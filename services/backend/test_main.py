import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import httpx
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import BigInteger, create_engine, delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.requests import Request

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ["OMNIKESTRAPIM_API_KEY"] = "test-api-key-for-tests-1234567890-abcd"
os.environ["OMNIKESTRAPIM_ADMIN_PASSWORD"] = "test-admin-password"
os.environ["OMNIKESTRAPIM_SESSION_SECRET"] = "test-session-secret-with-sufficient-length"
os.environ["OMNIKESTRAPIM_COOKIE_SECURE"] = "false"

from app import app
from backend_core.api.auth import is_valid_session
from backend_core.api.config import restore_saved_product_roots
from backend_core.api.dependencies import get_db, require_api_key
from backend_core import runtime
from backend_core.database import Base, engine
from backend_core.marketplace_worker import (
    _mark_reconciliation_required,
    _run_job,
    run_marketplace_worker,
)
from backend_core.json_logging import JsonFormatter
from backend_core.marketplaces import MarketplaceOutcomeUnknown, publish_product
from backend_core.models import (
    AppSettingRecord,
    AuthSessionRecord,
    LoginAttemptRecord,
    MarketplaceCredentialRecord,
    MarketplaceJobRecord,
    ProductEvent,
    ProductEventRecord,
    ProductRecord,
)
from backend_core.catalog import (
    _scan_products,
    _sync_product_catalog,
    _sync_product_catalog_for_roots,
    _sync_product_for_event,
    _product_directory_for_path,
    _write_manifest_atomic,
)


def _login(client):
    response = client.post(
        "/api/auth/login",
        json={"password": "test-admin-password"},
    )
    assert response.status_code == 200
    return client


def pytest_sessionfinish(session, exitstatus):
    for database_path in (Path("test.db"), Path("services/backend/test.db")):
        try:
            database_path.unlink()
        except FileNotFoundError:
            pass


@pytest.fixture(autouse=True)
def create_test_database():
    Base.metadata.create_all(engine)
    yield


def test_scan_products_detects_manifest_and_files(tmp_path):
    product_dir = tmp_path / "Dell Latitude 5420"
    product_dir.mkdir()
    (product_dir / "product.yaml").write_text("name: Dell Latitude 5420\n", encoding="utf-8")
    images_dir = product_dir / "images"
    images_dir.mkdir()
    (images_dir / "cover.jpg").write_bytes(b"img")

    products = _scan_products(tmp_path)

    assert len(products) == 1
    assert products[0].name == "Dell Latitude 5420"
    assert products[0].has_manifest is True
    assert any(file.name == "cover.jpg" for file in products[0].files)


def test_scan_products_recurses_groups_and_flags_ambiguous_product_folders(tmp_path):
    notebook = tmp_path / "Eletrônicos" / "Notebooks" / "ThinkPad"
    notebook.mkdir(parents=True)
    (notebook / "product.yaml").write_text(
        "schema_version: 1\n"
        "id: 0197f381-7c63-7a1d-a8b1-4e08b8472401\n"
        "title: ThinkPad\n"
        "category: Portáteis\n",
        encoding="utf-8",
    )
    reserved_images = notebook / "images" / "archive"
    reserved_images.mkdir(parents=True)
    (reserved_images / "product.yaml").write_text(
        "title: This must remain a file\n", encoding="utf-8"
    )
    tripod = tmp_path / "Acessórios" / "Tripé"
    (tripod / "details").mkdir(parents=True)
    (tripod / "cover.jpg").write_bytes(b"image")
    (tripod / "details" / "side.jpg").write_bytes(b"image")

    products = _scan_products(tmp_path)
    by_path = {Path(product.path).relative_to(tmp_path).as_posix(): product for product in products}

    assert set(by_path) == {
        "Eletrônicos/Notebooks/ThinkPad",
        "Acessórios/Tripé",
    }
    assert by_path["Eletrônicos/Notebooks/ThinkPad"].folder_group == "Eletrônicos/Notebooks"
    assert by_path["Eletrônicos/Notebooks/ThinkPad"].category == "Portáteis"
    assert by_path["Eletrônicos/Notebooks/ThinkPad"].category_suggestion == "Notebooks"
    assert by_path["Eletrônicos/Notebooks/ThinkPad"].category_mismatch is True
    assert by_path["Eletrônicos/Notebooks/ThinkPad"].ambiguous_structure is False
    assert by_path["Acessórios/Tripé"].folder_group == "Acessórios"
    assert by_path["Acessórios/Tripé"].ambiguous_structure is True
    assert any(file.name == "product.yaml" and "images/archive" in file.relative_path for file in by_path["Eletrônicos/Notebooks/ThinkPad"].files)


def test_product_patch_assigns_new_uuid_when_manifest_changed_to_duplicate(
    product_api,
):
    client, database, product_root, product_dir, product_id = product_api
    other_id = "0197f381-7c63-7a1d-a8b1-4e08b8472402"
    other_dir = product_root / "Other item"
    other_dir.mkdir()
    (other_dir / "product.yaml").write_text(
        f"id: {other_id}\ntitle: Other item\n", encoding="utf-8"
    )
    with Session(database) as db:
        _sync_product_catalog(db, product_root)

    manifest_path = product_dir / "product.yaml"
    manifest_path.write_text(
        f"id: {other_id}\ntitle: Test item\n", encoding="utf-8"
    )
    response = client.patch(
        f"/api/products/{product_id}", json={"description": "Updated"}
    )

    assert response.status_code == 200
    assert response.json()["id"] not in {product_id, other_id}
    assert yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["id"] == response.json()["id"]


def test_force_cleanup_scan_endpoint_removes_empty_catalog_records(product_api):
    client, _, _, product_dir, _ = product_api
    for path in product_dir.iterdir():
        path.unlink()
    product_dir.rmdir()

    response = client.post("/api/products/scan?force_cleanup=true")

    assert response.status_code == 200
    assert response.json()["force_cleanup"] is True
    assert response.json()["removed_records"] == 1


def test_invalid_saved_roots_show_setup_warning_instead_of_crashing(product_api):
    client, _, _, _, _ = product_api
    warning = "A raiz salva não está mais permitida."
    client.app.state.setup_warning = warning
    try:
        status = client.get("/api/setup/status")
        setup_page = client.get("/")
    finally:
        client.app.state.setup_warning = None

    assert status.json()["warning"] == warning
    assert status.json()["completed"] is False
    assert "Onde estão seus produtos?" in setup_page.text


def test_scan_products_descends_legacy_scanner_group_placeholder_without_rewriting_it(tmp_path):
    group = tmp_path / "Impressoras"
    product_dir = group / "Impressora 3D"
    product_dir.mkdir(parents=True)
    group_manifest = group / "product.yaml"
    group_manifest.write_text(
        "schema_version: 1\n"
        "id: 0197f381-7c63-7a1d-a8b1-4e08b8472410\n"
        "title: Impressoras\n",
        encoding="utf-8",
    )
    original_group_manifest = group_manifest.read_bytes()
    (product_dir / "product.yaml").write_text(
        "schema_version: 1\n"
        "id: 0197f381-7c63-7a1d-a8b1-4e08b8472411\n"
        "title: Impressora 3D\n"
        "sku: PRN-3D\n",
        encoding="utf-8",
    )
    (product_dir / "front.jpg").write_bytes(b"image")

    products = _scan_products(tmp_path)

    assert len(products) == 1
    assert products[0].path == str(product_dir)
    assert products[0].folder_group == "Impressoras"
    assert products[0].sku == "PRN-3D"
    assert group_manifest.read_bytes() == original_group_manifest


def test_scan_products_extracts_catalog_metadata_and_primary_image(tmp_path):
    product_dir = tmp_path / "Banco RC modelismo"
    product_dir.mkdir()
    (product_dir / "product.yaml").write_text(
        "name: Banco RC\nsku: BRC-42\ncategory: Modelismo\ndescription: Banco ajustável\n",
        encoding="utf-8",
    )
    (product_dir / "IMG_0448.HEIC").write_bytes(b"source")
    (product_dir / "IMG_0448-removebg-preview.png").write_bytes(b"preview")

    product = _scan_products(tmp_path)[0]

    assert product.name == "Banco RC"
    assert product.sku == "BRC-42"
    assert product.category == "Modelismo"
    assert product.description == "Banco ajustável"
    assert product.primary_image == "Banco RC modelismo/IMG_0448-removebg-preview.png"
    assert product.images[0] == product.primary_image
    assert product.manifest["sku"] == "BRC-42"


def test_scan_products_extracts_sku_from_filename_and_seeds_manifest(tmp_path):
    product_dir = tmp_path / "Suporte drone"
    product_dir.mkdir()
    (product_dir / "SKU-DRN_204.jpg").write_bytes(b"image")

    product = _scan_products(tmp_path)[0]

    assert product.has_manifest is True
    assert product.sku == "DRN_204"
    assert product.category is None
    assert product.primary_image == "Suporte drone/SKU-DRN_204.jpg"
    assert (product_dir / "product.yaml").exists()


def test_scan_products_seeds_stable_v1_manifest(tmp_path):
    product_dir = tmp_path / "Legacy product"
    product_dir.mkdir()
    (product_dir / "cover.jpg").write_bytes(b"image")

    with patch("backend_core.catalog._write_manifest_atomic", wraps=_write_manifest_atomic) as write_manifest:
        first_scan = _scan_products(tmp_path)[0]
        second_scan = _scan_products(tmp_path)[0]

    assert write_manifest.call_count == 1
    assert first_scan.id is not None
    assert first_scan.id == second_scan.id
    assert first_scan.title == "Legacy product"
    assert (product_dir / "product.yaml").exists()
    assert any(file.name == "product.yaml" for file in first_scan.files)


def test_scan_products_reports_invalid_manifest_fields(tmp_path):
    product_dir = tmp_path / "Invalid product"
    product_dir.mkdir()
    (product_dir / "product.yaml").write_text(
        "status: unknown\nprice: -1\n", encoding="utf-8"
    )

    product = _scan_products(tmp_path)[0]

    assert product.id is not None
    assert "status" in product.manifest_error
    assert "price" in product.manifest_error


def test_sync_product_catalog_persists_and_removes_products(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'catalog.db'}")
    Base.metadata.create_all(database)
    product_dir = tmp_path / "Dell Latitude 5420"
    product_dir.mkdir()
    manifest = product_dir / "product.yaml"
    manifest.write_text("name: Dell\n", encoding="utf-8")

    with Session(database) as session:
        products = _sync_product_catalog(session, tmp_path)
        stored = session.scalar(
            select(ProductRecord).where(ProductRecord.path == str(product_dir))
        )
        assert len(products) == 1
        assert stored is not None
        assert stored.has_manifest is True
        assert stored.files[0]["name"] == "product.yaml"

        other_product = tmp_path / "Other product"
        other_product.mkdir()
        (other_product / "product.yaml").write_text(
            "schema_version: 2\nid: 0197f381-7c63-7a1d-a8b1-4e08b8472482\n"
            "title: Other product\n",
            encoding="utf-8",
        )
        _sync_product_catalog(session, tmp_path)

        manifest.unlink()
        product_dir.rmdir()
        _sync_product_catalog(session, tmp_path)
        assert session.scalar(
            select(ProductRecord).where(ProductRecord.path == str(product_dir))
        ) is None
        assert session.scalar(
            select(ProductRecord).where(ProductRecord.path == str(other_product))
        ) is not None

    database.dispose()


def test_incremental_move_preserves_product_identity(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'move.db'}")
    Base.metadata.create_all(database)
    product_dir = tmp_path / "Chair"
    product_dir.mkdir()
    (product_dir / "product.yaml").write_text(
        "schema_version: 1\nid: 0197f381-7c63-7a1d-a8b1-4e08b84724af\ntitle: Chair\n",
        encoding="utf-8",
    )

    with Session(database) as session:
        _sync_product_catalog(session, tmp_path)
        record = session.scalar(select(ProductRecord))
        stable_id = record.product_uuid
        database_id = record.id

        image = product_dir / "new.jpg"
        image.write_bytes(b"image")
        _sync_product_for_event(
            session, tmp_path, ProductEvent(event="modified", path=str(image))
        )
        assert any(item["name"] == "new.jpg" for item in record.files)

        renamed_dir = tmp_path / "Renamed chair"
        product_dir.rename(renamed_dir)
        _sync_product_for_event(
            session,
            tmp_path,
            ProductEvent(
                event="moved",
                path=str(product_dir),
                destination_path=str(renamed_dir),
                is_directory=True,
            ),
        )
        products = session.scalars(select(ProductRecord)).all()
        assert len(products) == 1
        assert products[0].id == database_id
        assert products[0].product_uuid == stable_id
        assert products[0].path == str(renamed_dir)

    database.dispose()


def test_event_on_legacy_group_placeholder_resolves_to_nested_product(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'legacy-group-event.db'}")
    Base.metadata.create_all(database)
    group = tmp_path / "Printers"
    product_dir = group / "Laser"
    product_dir.mkdir(parents=True)
    (group / "product.yaml").write_text(
        "schema_version: 1\nid: 0197f381-7c63-7a1d-a8b1-4e08b8472410\ntitle: Printers\n",
        encoding="utf-8",
    )
    (product_dir / "product.yaml").write_text(
        "schema_version: 1\nid: 0197f381-7c63-7a1d-a8b1-4e08b8472411\ntitle: Laser\n",
        encoding="utf-8",
    )

    with Session(database) as session:
        _sync_product_catalog(session, tmp_path)
        assert _product_directory_for_path(tmp_path, str(group), session, "default", True) is None
        indexed = session.scalars(select(ProductRecord)).all()
        assert len(indexed) == 1
        assert indexed[0].path == str(product_dir)

    database.dispose()


def test_moving_file_between_products_preserves_both_identities(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'file-move.db'}")
    Base.metadata.create_all(database)
    first_dir = tmp_path / "First"
    second_dir = tmp_path / "Second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "product.yaml").write_text(
        "id: 0197f381-7c63-7a1d-a8b1-4e08b8472401\ntitle: First\n", encoding="utf-8"
    )
    (second_dir / "product.yaml").write_text(
        "id: 0197f381-7c63-7a1d-a8b1-4e08b8472402\ntitle: Second\n", encoding="utf-8"
    )
    source_file = first_dir / "photo.jpg"
    source_file.write_bytes(b"image")

    with Session(database) as session:
        _sync_product_catalog(session, tmp_path)
        original_ids = {
            record.path: record.product_uuid
            for record in session.scalars(select(ProductRecord)).all()
        }
        destination_file = second_dir / source_file.name
        source_file.rename(destination_file)
        _sync_product_for_event(
            session,
            tmp_path,
            ProductEvent(
                event="moved",
                path=str(source_file),
                destination_path=str(destination_file),
                is_directory=False,
            ),
        )

        records = {
            record.path: record
            for record in session.scalars(select(ProductRecord)).all()
        }
        assert records[first_dir.as_posix()].product_uuid == original_ids[first_dir.as_posix()]
        assert records[second_dir.as_posix()].product_uuid == original_ids[second_dir.as_posix()]
        assert not any(file["name"] == "photo.jpg" for file in records[first_dir.as_posix()].files)
        assert any(file["name"] == "photo.jpg" for file in records[second_dir.as_posix()].files)

    database.dispose()


def test_incremental_event_assigns_new_uuid_to_duplicate_manifest(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'event-duplicate.db'}")
    Base.metadata.create_all(database)
    root = tmp_path / "Products"
    original = root / "Original"
    duplicate = root / "Duplicate"
    original.mkdir(parents=True)
    shared_id = "0197f381-7c63-7a1d-a8b1-4e08b8472499"
    (original / "product.yaml").write_text(
        f"id: {shared_id}\ntitle: Original\n", encoding="utf-8"
    )

    with Session(database) as db:
        _sync_product_catalog(db, root)
        original_record = db.scalar(
            select(ProductRecord).where(ProductRecord.path == str(original))
        )
        original_id = original_record.product_uuid
        duplicate.mkdir()
        (duplicate / "product.yaml").write_text(
            f"id: {shared_id}\ntitle: Duplicate\n", encoding="utf-8"
        )
        _sync_product_for_event(
            db,
            root,
            ProductEvent(event="created", path=str(duplicate), is_directory=True),
        )
        duplicate_record = db.scalar(
            select(ProductRecord).where(ProductRecord.path == str(duplicate))
        )
        assert original_record.product_uuid == original_id
        assert duplicate_record.product_uuid != original_id
        assert yaml.safe_load(
            (duplicate / "product.yaml").read_text(encoding="utf-8")
        )["id"] == duplicate_record.product_uuid
    database.dispose()


def test_dashboard_routes_return_html():
    client = _login(TestClient(app))

    root = client.get("/")
    dashboard = client.get("/dashboard")
    home = client.get("/home")
    catalog = client.get("/products")
    product_detail = client.get("/products/0197f381-7c63-7a1d-a8b1-4e08b8472401")
    settings = client.get("/settings")

    assert root.status_code == 200
    assert dashboard.status_code == 200
    assert home.status_code == 200
    assert catalog.status_code == 200
    assert product_detail.status_code == 200
    assert settings.status_code == 200
    assert "OmnIkestrAPIM" in root.text
    assert "Painel geral" in dashboard.text
    assert "Produtos" in catalog.text
    assert "Dados do produto" in product_detail.text
    assert "Primeiros passos" in settings.text
    assert "Pasta no Windows" not in settings.text


def test_metrics_are_available_without_login():
    response = TestClient(app).get("/metrics")

    assert response.status_code == 200
    assert "omnikestr_http_requests_total" in response.text


def test_proxy_server_uses_configurable_forwarded_ip_trust(monkeypatch):
    from backend_core import server

    calls = []
    monkeypatch.setattr(server.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setenv("UVICORN_PROXY_HEADERS", "false")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.2,10.0.0.3")

    server.main()

    assert calls[0][1]["proxy_headers"] is False
    assert calls[0][1]["forwarded_allow_ips"] == "10.0.0.2,10.0.0.3"


def test_epoch_auth_columns_use_bigint():
    assert isinstance(AuthSessionRecord.__table__.c.expires_at.type, BigInteger)
    assert isinstance(LoginAttemptRecord.__table__.c.attempted_at.type, BigInteger)


def test_json_formatter_includes_structured_fields():
    record = logging.LogRecord("backend", logging.WARNING, __file__, 1, "warning", (), None)
    record.event = "setup.warning"

    entry = json.loads(JsonFormatter().format(record))

    assert entry["level"] == "WARNING"
    assert entry["message"] == "warning"
    assert entry["event"] == "setup.warning"


def test_product_ui_includes_price_floor_and_publish_action():
    client = _login(TestClient(app))
    response = client.get("/products/0197f381-7c63-7a1d-a8b1-4e08b8472401")

    assert response.status_code == 200
    assert "Piso de preço" in response.text
    assert "Publicar no Mercado Livre" in response.text


def test_dashboard_uses_catalog_total_and_valid_manifest_count():
    response = _login(TestClient(app)).get("/dashboard")

    assert response.status_code == 200
    assert "productsResponse.total" in response.text
    assert "manifest_error" in response.text


def test_legacy_main_entrypoint_resolves_modular_app():
    import main
    import app as modular_app

    assert main.app is modular_app.app


def test_metrics_and_static_footer_are_served():
    client = _login(TestClient(app))

    client.get("/api/health")
    metrics = client.get("/metrics")
    footer = client.get("/static/site-footer.js")

    assert metrics.status_code == 200
    assert "omnikestr_http_requests_total" in metrics.text
    assert footer.status_code == 200
    assert "app-catalog-info" in footer.text


def test_product_events_route_is_not_matched_as_product_uuid():
    response = _login(TestClient(app)).get("/api/products/events")

    assert response.status_code == 200
    assert "events" in response.json()


def test_marketplaces_with_enabled_channel_are_json_serializable(product_api):
    client, _, _, product_dir, _ = product_api
    manifest_path = product_dir / "product.yaml"
    manifest_path.write_text(
        "schema_version: 2\n"
        "id: 0197f381-7c63-7a1d-a8b1-4e08b8472401\n"
        "title: Test item\n"
        "marketplaces:\n"
        "  mercadolivre:\n"
        "    enabled: true\n"
        "    category_id: MLB123\n",
        encoding="utf-8",
    )

    scan_response = client.post("/api/products/scan")
    assert scan_response.status_code == 200

    detail_response = client.get(
        "/api/products/0197f381-7c63-7a1d-a8b1-4e08b8472401"
    )
    assert detail_response.status_code == 200
    marketplaces = detail_response.json()["marketplaces"]
    assert marketplaces["mercadolivre"]["enabled"] is True
    assert marketplaces["mercadolivre"]["category_id"] == "MLB123"


def test_product_patch_merges_marketplace_metadata(product_api):
    client, _, _, product_dir, product_id = product_api
    manifest_path = product_dir / "product.yaml"
    manifest_path.write_text(
        "schema_version: 2\n"
        f"id: {product_id}\n"
        "title: Test item\n"
        "marketplaces:\n"
        "  mercadolivre:\n"
        "    enabled: true\n"
        "    item_id: MLB-123\n"
        "    permalink: https://example.test/item\n",
        encoding="utf-8",
    )
    client.post("/api/products/scan")

    response = client.patch(
        f"/api/products/{product_id}",
        json={"marketplaces": {"mercadolivre": {"enabled": False}}},
    )

    assert response.status_code == 200
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["marketplaces"]["mercadolivre"] == {
        "enabled": False,
        "item_id": "MLB-123",
        "permalink": "https://example.test/item",
    }


def test_republishing_existing_marketplace_item_does_not_reset_stock(
    product_api, monkeypatch
):
    _, database, _, product_dir, product_id = product_api
    monkeypatch.setenv("MELI_AVAILABLE_QUANTITY", "1")
    monkeypatch.setenv("MELI_CATEGORY_ID", "MLB123")
    monkeypatch.setenv("MELI_LISTING_TYPE_ID", "gold_special")
    requests = []

    async def fake_access_token(db, workspace_id):
        return "test-access-token"

    async def fake_rate_limited_request(client, method, url, **kwargs):
        requests.append((method, url, kwargs))
        body = (
            {"id": "uploaded-picture"}
            if url.endswith("/pictures/items/upload")
            else {"id": "MLB-123", "permalink": "https://example.test/item"}
        )
        return SimpleNamespace(
            is_error=False,
            status_code=200,
            json=lambda: body,
        )

    monkeypatch.setattr("backend_core.marketplaces._access_token", fake_access_token)
    monkeypatch.setattr(
        "backend_core.marketplaces._rate_limited_request", fake_rate_limited_request
    )

    with Session(database) as db:
        db.add(
            MarketplaceCredentialRecord(
                workspace_id="default",
                provider="mercadolivre",
                account_id="seller-123",
                access_token_encrypted="unused",
                refresh_token_encrypted="unused",
                expires_at=datetime.now(timezone.utc),
            )
        )
        product = db.scalar(
            select(ProductRecord).where(ProductRecord.product_uuid == product_id)
        )
        product.price = Decimal("25.00")
        product.price_floor = Decimal("10.00")
        product.condition = "new"
        product.marketplaces = {
            "mercadolivre": {
                "enabled": True,
                "category_id": "MLB123",
                "item_id": "MLB-123",
            }
        }
        db.commit()

        asyncio.run(publish_product(db, product, "default"))

    update_request = next(
        request
        for request in requests
        if request[0] == "PUT" and request[1].endswith("/items/MLB-123")
    )
    assert "available_quantity" not in update_request[2]["json"]
    assert (product_dir / "product.yaml").is_file()


def test_publish_connection_error_auto_reconciles_exact_product_uuid(
    product_api, monkeypatch
):
    _, database, _, _, product_id = product_api
    monkeypatch.setenv("MELI_CATEGORY_ID", "MLB123")
    monkeypatch.setenv("MELI_LISTING_TYPE_ID", "gold_special")
    requests = []

    async def fake_access_token(db, workspace_id):
        return "test-access-token"

    async def fake_rate_limited_request(client, method, url, **kwargs):
        requests.append((method, url, kwargs))
        if url.endswith("/pictures/items/upload"):
            body = {"id": "uploaded-picture"}
        elif method == "POST" and url.endswith("/items"):
            raise httpx.ConnectError("connection dropped")
        elif "/users/seller-123/items/search" in url:
            body = {"results": [{"id": "MLB-123"}]}
        elif url.endswith("/items/MLB-123"):
            body = {
                "id": "MLB-123",
                "seller_custom_field": product_id,
                "permalink": "https://example.test/MLB-123",
            }
        else:
            raise AssertionError(f"Unexpected request: {method} {url}")
        return SimpleNamespace(is_error=False, status_code=200, json=lambda: body)

    monkeypatch.setattr("backend_core.marketplaces._access_token", fake_access_token)
    monkeypatch.setattr(
        "backend_core.marketplaces._rate_limited_request", fake_rate_limited_request
    )

    with Session(database) as db:
        db.add(
            MarketplaceCredentialRecord(
                workspace_id="default",
                provider="mercadolivre",
                account_id="seller-123",
                access_token_encrypted="unused",
                refresh_token_encrypted="unused",
                expires_at=datetime.now(timezone.utc),
            )
        )
        product = db.scalar(
            select(ProductRecord).where(ProductRecord.product_uuid == product_id)
        )
        product.price = Decimal("25.00")
        product.price_floor = Decimal("10.00")
        product.condition = "new"
        product.marketplaces = {
            "mercadolivre": {"enabled": True, "category_id": "MLB123"}
        }
        db.commit()

        result = asyncio.run(publish_product(db, product, "default"))

    assert result["item_id"] == "MLB-123"
    create_request = next(
        request
        for request in requests
        if request[0] == "POST" and request[1].endswith("/items")
    )
    assert create_request[2]["json"]["seller_custom_field"] == product_id


def test_publish_connection_error_without_exact_match_stays_manual(
    product_api, monkeypatch
):
    _, database, _, _, product_id = product_api
    monkeypatch.setenv("MELI_CATEGORY_ID", "MLB123")
    monkeypatch.setenv("MELI_LISTING_TYPE_ID", "gold_special")

    async def fake_access_token(db, workspace_id):
        return "test-access-token"

    async def fake_rate_limited_request(client, method, url, **kwargs):
        if url.endswith("/pictures/items/upload"):
            body = {"id": "uploaded-picture"}
        elif method == "POST" and url.endswith("/items"):
            raise httpx.ConnectError("connection dropped")
        else:
            body = {"results": []}
        return SimpleNamespace(is_error=False, status_code=200, json=lambda: body)

    monkeypatch.setattr("backend_core.marketplaces._access_token", fake_access_token)
    monkeypatch.setattr(
        "backend_core.marketplaces._rate_limited_request", fake_rate_limited_request
    )

    with Session(database) as db:
        db.add(
            MarketplaceCredentialRecord(
                workspace_id="default",
                provider="mercadolivre",
                account_id="seller-123",
                access_token_encrypted="unused",
                refresh_token_encrypted="unused",
                expires_at=datetime.now(timezone.utc),
            )
        )
        product = db.scalar(
            select(ProductRecord).where(ProductRecord.product_uuid == product_id)
        )
        product.price = Decimal("25.00")
        product.price_floor = Decimal("10.00")
        product.condition = "new"
        product.marketplaces = {
            "mercadolivre": {"enabled": True, "category_id": "MLB123"}
        }
        db.commit()

        with pytest.raises(MarketplaceOutcomeUnknown):
            asyncio.run(publish_product(db, product, "default"))


def test_local_picker_token_is_not_cacheable(monkeypatch):
    monkeypatch.setattr("backend_core.runtime.PICKER_TOKEN", "test-local-picker-token")
    response = _login(TestClient(app)).get("/api/local/picker-token")

    assert response.status_code == 200
    assert response.json() == {"token": "test-local-picker-token"}
    assert response.headers["cache-control"] == "no-store"


def test_mutating_route_requires_api_key(monkeypatch, product_api):
    client, _, _, _, _ = product_api
    client.cookies.clear()
    response = client.post(
        "/api/products/watch",
        json={"event": "modified", "path": "/tmp/not-a-product"},
        headers={"Origin": "http://testserver"},
    )

    assert response.status_code == 401

    authorized = client.post(
        "/api/products/watch",
        json={"event": "modified", "path": "/tmp/not-a-product"},
        headers={"X-API-Key": "test-api-key-for-tests-1234567890-abcd"},
    )
    assert authorized.status_code == 200
    assert client.post(
        "/api/products/scan",
    ).status_code == 401
    assert client.post(
        "/api/products/scan",
        headers={"X-API-Key": "test-api-key-for-tests-1234567890-abcd"},
    ).status_code == 200


def test_non_ascii_auth_values_are_rejected_without_type_error():
    client = TestClient(app)
    assert client.post(
        "/api/auth/login",
        json={"password": "senha-inválida"},
    ).status_code == 401
    assert client.post(
        "/api/products/watch",
        headers=[(b"X-API-Key", b"chave-\xff")],
        json={"event": "modified", "path": "/tmp/not-a-product"},
    ).status_code == 401
    assert client.get(
        "/api/products",
        headers=[(b"Cookie", b"omnikestr_session=9999999999.assinatura-\xff")],
    ).status_code == 401

    request = Request({
        "type": "http",
        "headers": [
            (
                b"cookie",
                "omnikestr_session=9999999999.assinatura-é".encode("latin-1"),
            )
        ],
    })
    assert not is_valid_session(request)

    with pytest.raises(HTTPException) as error:
        require_api_key(Request({"type": "http", "headers": []}), "chave-inválida")
    assert error.value.status_code == 401


def test_watcher_cannot_choose_another_workspace(product_api):
    client, _, _, product_dir, _ = product_api

    response = client.post(
        "/api/products/watch",
        json={
            "event": "modified",
            "path": str(product_dir / "product.yaml"),
            "workspace_id": "another-workspace",
        },
    )

    assert response.status_code == 403


def test_duplicate_manifest_uuid_creates_unique_copy(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'duplicate.db'}")
    Base.metadata.create_all(database)
    root = tmp_path / "Products"
    first = root / "First"
    second = root / "Second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    shared_id = "0197f381-7c63-7a1d-a8b1-4e08b8472499"
    for folder in (first, second):
        (folder / "product.yaml").write_text(
            f"schema_version: 2\nid: {shared_id}\ntitle: {folder.name}\n",
            encoding="utf-8",
        )

    with Session(database) as db:
        products = _sync_product_catalog(db, root)

    assert len(products) == 2
    ids = {str(product.id) for product in products}
    assert len(ids) == 2
    assert shared_id in ids
    for folder in (first, second):
        manifest = yaml.safe_load(
            (folder / "product.yaml").read_text(encoding="utf-8")
        )
        assert manifest["id"] in ids
    database.dispose()


def test_duplicate_uuid_keeps_database_identity_and_unlinks_copy(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'duplicate-owner.db'}")
    Base.metadata.create_all(database)
    root = tmp_path / "Products"
    original = root / "Z-Original"
    copy = root / "A-Copy"
    original.mkdir(parents=True)
    shared_id = "0197f381-7c63-7a1d-a8b1-4e08b8472499"
    (original / "product.yaml").write_text(
        f"schema_version: 2\nid: {shared_id}\ntitle: Original\n",
        encoding="utf-8",
    )

    with Session(database) as db:
        _sync_product_catalog(db, root)
        copy.mkdir()
        (copy / "product.yaml").write_text(
            f"schema_version: 2\nid: {shared_id}\ntitle: Copy\n"
            "status: published\n"
            "marketplaces:\n"
            "  mercadolivre:\n"
            "    enabled: true\n"
            "    item_id: MLB-ORIGINAL\n"
            "    permalink: https://example.test/original\n",
            encoding="utf-8",
        )

        products = _sync_product_catalog(db, root)

        original_product = next(item for item in products if item.path == str(original))
        copy_product = next(item for item in products if item.path == str(copy))
        copy_manifest = yaml.safe_load(
            (copy / "product.yaml").read_text(encoding="utf-8")
        )
        assert original_product.id == shared_id
        assert copy_product.id != shared_id
        assert copy_manifest["id"] == copy_product.id
        assert copy_manifest["status"] == "pending"
        assert copy_manifest["marketplaces"]["mercadolivre"] == {"enabled": True}
    database.dispose()


def test_missing_root_does_not_delete_existing_catalog(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'missing-root.db'}")
    Base.metadata.create_all(database)
    root = tmp_path / "Products"
    root.mkdir()
    unavailable = tmp_path / "Unmounted"

    with Session(database) as db:
        _sync_product_catalog_for_roots(
            db,
            (root, unavailable),
            "default",
        )
        db.add(
            ProductRecord(
                workspace_id="default",
                product_uuid="0197f381-7c63-7a1d-a8b1-4e08b8472488",
                name="Preserved",
                title="Preserved",
                folder_group="",
                path=str(unavailable / "Preserved"),
                files=[],
                manifest={},
                has_manifest=True,
            )
        )
        db.commit()
        _sync_product_catalog_for_roots(db, (root, unavailable), "default")
        remaining = db.scalar(
            select(ProductRecord).where(
                ProductRecord.product_uuid
                == "0197f381-7c63-7a1d-a8b1-4e08b8472488"
            )
        )
        assert remaining is not None
    database.dispose()


def test_empty_root_does_not_delete_existing_catalog(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'empty-root.db'}")
    Base.metadata.create_all(database)
    root = tmp_path / "Products"
    product_dir = root / "Product"
    product_dir.mkdir(parents=True)
    product_id = "0197f381-7c63-7a1d-a8b1-4e08b8472488"
    manifest_path = product_dir / "product.yaml"
    manifest_path.write_text(
        f"schema_version: 2\nid: {product_id}\ntitle: Product\n",
        encoding="utf-8",
    )

    with Session(database) as db:
        _sync_product_catalog(db, root)
        manifest_path.unlink()
        product_dir.rmdir()

        assert _sync_product_catalog(db, root) == []
        assert db.scalar(
            select(ProductRecord).where(ProductRecord.product_uuid == product_id)
        ) is not None
    database.dispose()


def test_force_cleanup_removes_catalog_rows_from_empty_root(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'force-cleanup.db'}")
    Base.metadata.create_all(database)
    root = tmp_path / "Products"
    product_dir = root / "Product"
    product_dir.mkdir(parents=True)
    product_id = "0197f381-7c63-7a1d-a8b1-4e08b8472488"
    (product_dir / "product.yaml").write_text(
        f"schema_version: 2\nid: {product_id}\ntitle: Product\n",
        encoding="utf-8",
    )

    with Session(database) as db:
        _sync_product_catalog(db, root)
        (product_dir / "product.yaml").unlink()
        product_dir.rmdir()
        _sync_product_catalog(db, root)
        assert db.scalar(select(ProductRecord)) is not None

        assert _sync_product_catalog_for_roots(
            db, (root,), force_cleanup=True
        ) == []
        assert db.scalar(select(ProductRecord)) is None
    database.dispose()


def test_saved_missing_root_is_restored_and_allowlist_is_enforced(
    tmp_path, monkeypatch
):
    database = create_engine(f"sqlite:///{tmp_path / 'saved-roots.db'}")
    Base.metadata.create_all(database)
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    saved_root = allowed_root / "dataset" / "Products"
    monkeypatch.setattr(runtime, "ALLOWED_PRODUCT_ROOTS", (allowed_root,))
    monkeypatch.setattr(runtime, "PRODUCT_ROOTS", (allowed_root,))
    monkeypatch.setattr(runtime, "PRODUCTS_ROOT", allowed_root)

    with Session(database) as db:
        db.add(
            AppSettingRecord(
                key="product_roots",
                value=json.dumps([str(saved_root)]),
            )
        )
        db.commit()

        assert restore_saved_product_roots(db) is None
        assert runtime.PRODUCT_ROOTS == (saved_root.resolve(),)
        assert not saved_root.exists()

        db.get(AppSettingRecord, "product_roots").value = json.dumps(
            [str(tmp_path / "outside" / "Products")]
        )
        db.commit()
        warning = restore_saved_product_roots(db)
        assert warning and "Selecione novamente" in warning
        assert runtime.PRODUCT_ROOTS == (saved_root.resolve(),)
    database.dispose()


@pytest.fixture
def product_api(tmp_path, monkeypatch):
    database = create_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(database)
    product_root = tmp_path / "Products"
    product_dir = product_root / "Test item"
    product_dir.mkdir(parents=True)
    product_id = "0197f381-7c63-7a1d-a8b1-4e08b8472401"
    manifest_path = product_dir / "product.yaml"
    manifest_path.write_text(
        "schema_version: 1\n"
        f"id: {product_id}\n"
        "title: Test item\n"
        "category: Furniture\n"
        "status: pending\n"
        "custom_note: keep this value\n",
        encoding="utf-8",
    )
    image_path = product_dir / "cover.png"
    Image.new("RGB", (32, 20), color=(30, 90, 70)).save(image_path)
    Image.new("RGBA", (12, 12), color=(30, 90, 70, 0)).save(product_dir / "removebg-preview.png")

    def override_get_db():
        with Session(database) as session:
            yield session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("backend_core.runtime.PRODUCTS_ROOT", product_root)
    monkeypatch.setattr("backend_core.runtime.PRODUCT_ROOTS", (product_root,))
    with Session(database) as session:
        _sync_product_catalog(session, product_root)

    client = _login(TestClient(app))
    yield client, database, product_root, product_dir, product_id
    client.close()
    app.dependency_overrides.clear()
    database.dispose()


def test_login_is_required_and_session_is_invalidated_on_logout():
    client = TestClient(app)

    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/api/products").status_code == 401
    assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401

    _login(client)
    stolen_cookie = client.cookies.get("omnikestr_session")
    assert stolen_cookie
    assert client.get("/api/products").status_code == 200
    assert client.get("/api/products?workspace_id=other").status_code == 403
    assert client.post(
        "/api/products/scan",
        headers={"Origin": "https://attacker.example"},
    ).status_code == 403
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/products").status_code == 401
    client.cookies.set("omnikestr_session", stolen_cookie)
    assert client.get("/api/products").status_code == 401


def test_login_rate_limit_rejects_repeated_failures():
    with Session(engine) as db:
        db.execute(delete(LoginAttemptRecord))
        db.commit()

    client = TestClient(app)
    for _ in range(5):
        assert (
            client.post("/api/auth/login", json={"password": "wrong"}).status_code
            == 401
        )

    limited = client.post("/api/auth/login", json={"password": "wrong"})
    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0

    with Session(engine) as db:
        db.execute(delete(LoginAttemptRecord))
        db.execute(delete(AuthSessionRecord))
        db.commit()


def test_security_configuration_fails_closed_when_api_key_is_missing(monkeypatch):
    monkeypatch.delenv("OMNIKESTRAPIM_API_KEY")

    with pytest.raises(RuntimeError, match="OMNIKESTRAPIM_API_KEY"):
        with TestClient(app):
            pass


def test_interrupted_publish_requires_reconciliation(product_api, monkeypatch):
    client, database, _, product_dir, product_id = product_api
    monkeypatch.setattr("backend_core.marketplace_worker.database.engine", database)
    with Session(database) as session:
        job = MarketplaceJobRecord(
            workspace_id="default",
            provider="mercadolivre",
            job_type="publish_product",
            payload={"product_id": product_id},
            status="in_progress",
        )
        session.add(job)
        session.commit()
        job_id = job.id

    _mark_reconciliation_required(
        job_id,
        "default",
        "mercadolivre",
        {"product_id": product_id},
        RuntimeError("The remote result is unknown"),
    )
    with Session(database) as session:
        job = session.get(MarketplaceJobRecord, job_id)
        assert job.status == "reconciliation_required"

    response = client.post(
        f"/api/marketplaces/jobs/{job_id}/reconcile",
        json={"item_id": "MLB123456"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    manifest = yaml.safe_load((product_dir / "product.yaml").read_text(encoding="utf-8"))
    assert manifest["marketplaces"]["mercadolivre"]["item_id"] == "MLB123456"


@pytest.mark.parametrize("payload", [{}, []])
def test_invalid_marketplace_job_payload_fails_without_retry(
    payload, tmp_path, monkeypatch
):
    database = create_engine(f"sqlite:///{tmp_path / 'invalid-job.db'}")
    Base.metadata.create_all(database)
    monkeypatch.setattr("backend_core.marketplace_worker.database.engine", database)
    with Session(database) as db:
        job = MarketplaceJobRecord(
            workspace_id="default",
            provider="mercadolivre",
            job_type="publish_product",
            payload=payload,
            status="in_progress",
            attempts=1,
        )
        db.add(job)
        db.commit()
        job_id = job.id

    asyncio.run(_run_job(job_id, "default", "mercadolivre", payload))

    with Session(database) as db:
        job = db.get(MarketplaceJobRecord, job_id)
        assert job.status == "failed"
        assert job.attempts == 1
    database.dispose()


def test_worker_continues_after_unexpected_job_exception(monkeypatch):
    async def run_worker():
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        claims = 0

        def claim_job():
            nonlocal claims
            claims += 1
            if claims == 1:
                return 1, "default", "mercadolivre", {}
            loop.call_soon_threadsafe(stop_event.set)
            return None

        def fail_finishing_job(*args, **kwargs):
            raise SQLAlchemyError("database unavailable")

        monkeypatch.setattr(
            "backend_core.marketplace_worker._recover_interrupted_jobs", lambda: None
        )
        monkeypatch.setattr("backend_core.marketplace_worker._claim_job", claim_job)
        monkeypatch.setattr(
            "backend_core.marketplace_worker._finish_job",
            fail_finishing_job,
        )
        monkeypatch.setattr(
            "backend_core.marketplace_worker.RECOVERY_INTERVAL_SECONDS", 3600
        )
        await run_marketplace_worker(stop_event)
        return claims

    assert asyncio.run(run_worker()) == 2


def test_worker_recovers_interrupted_jobs_periodically(monkeypatch):
    async def run_worker():
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        recoveries = 0

        def recover():
            nonlocal recoveries
            recoveries += 1
            if recoveries >= 2:
                loop.call_soon_threadsafe(stop_event.set)

        monkeypatch.setattr(
            "backend_core.marketplace_worker._recover_interrupted_jobs", recover
        )
        monkeypatch.setattr(
            "backend_core.marketplace_worker._claim_job", lambda: None
        )
        monkeypatch.setattr(
            "backend_core.marketplace_worker.RECOVERY_INTERVAL_SECONDS", 0.01
        )
        monkeypatch.setattr("backend_core.marketplace_worker.POLL_SECONDS", 0.002)
        await run_marketplace_worker(stop_event)
        return recoveries

    assert asyncio.run(run_worker()) >= 2


def test_active_marketplace_idempotency_key_is_unique(tmp_path):
    database = create_engine(f"sqlite:///{tmp_path / 'idempotency.db'}")
    Base.metadata.create_all(database)
    with Session(database) as session:
        session.add(
            MarketplaceJobRecord(
                workspace_id="default",
                provider="mercadolivre",
                job_type="publish_product",
                idempotency_key="mercadolivre:publish:item-1",
                payload={"product_id": "item-1"},
                status="failed",
            )
        )
        session.add(
            MarketplaceJobRecord(
                workspace_id="default",
                provider="mercadolivre",
                job_type="publish_product",
                idempotency_key="mercadolivre:publish:item-1",
                payload={"product_id": "item-1"},
                status="pending",
            )
        )
        session.commit()
        session.add(
            MarketplaceJobRecord(
                workspace_id="default",
                provider="mercadolivre",
                job_type="publish_product",
                idempotency_key="mercadolivre:publish:item-1",
                payload={"product_id": "item-1"},
                status="in_progress",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
    database.dispose()


def test_product_patch_preserves_unknown_yaml_and_prevents_write_loop(product_api):
    client, database, product_root, product_dir, product_id = product_api
    with patch("backend_core.api.products._write_manifest_atomic", wraps=_write_manifest_atomic) as atomic_write:
        response = client.patch(
            f"/api/products/{product_id}",
            json={"title": "Updated title", "status": "published", "sku": "TEST-1"},
        )
        assert response.status_code == 200
        assert response.json()["title"] == "Updated title"
        assert response.json()["status"] == "published"
        assert atomic_write.call_count == 1

        manifest = yaml.safe_load((product_dir / "product.yaml").read_text(encoding="utf-8"))
        assert manifest["custom_note"] == "keep this value"
        assert manifest["id"] == product_id

        event_response = client.post(
            "/api/products/watch",
            json={"event": "modified", "path": str(product_dir / "product.yaml")},
        )
        assert event_response.status_code == 200
        assert event_response.json()["indexed_products"] == 1
        assert atomic_write.call_count == 1

    with Session(database) as session:
        assert session.scalar(select(ProductEventRecord)) is not None


def test_product_patch_reports_invalid_fields(product_api):
    client, _, _, _, product_id = product_api

    response = client.patch(f"/api/products/{product_id}", json={"price": -1})

    assert response.status_code == 422
    assert any("price" in ".".join(str(part) for part in issue["loc"]) for issue in response.json()["detail"])


def test_product_filters_and_pagination(product_api):
    client, _, _, _, product_id = product_api

    response = client.get("/api/products?q=Test&category=Furniture&status=pending&limit=1&offset=0")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["limit"] == 1
    assert response.json()["categories"] == ["Furniture"]
    assert response.json()["products"][0]["id"] == product_id


def test_folder_group_filter_suggests_parent_category_without_overriding_yaml(product_api):
    client, database, product_root, product_dir, product_id = product_api
    nested_dir = product_root / "Electronics" / "Laptops" / product_dir.name
    nested_dir.parent.mkdir(parents=True)
    product_dir.rename(nested_dir)
    with Session(database) as session:
        _sync_product_catalog(session, product_root)

    response = client.get("/api/products?folder_group=Electronics%2FLaptops")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    product = response.json()["products"][0]
    assert product["id"] == product_id
    assert product["folder_group"] == "Electronics/Laptops"
    assert product["category"] == "Furniture"
    assert product["category_suggestion"] == "Laptops"
    assert product["category_mismatch"] is True
    assert response.json()["folder_groups"] == ["Electronics/Laptops"]


def test_product_media_converts_to_webp_and_rejects_other_products(product_api):
    client, _, _, _, product_id = product_api

    response = client.get(
        f"/api/products/{product_id}/media/Test%20item/cover.png?thumbnail=true"
    )
    escaped = client.get(
        f"/api/products/{product_id}/media/Test%20item/../Outside/secret.png"
    )
    transparent = client.get(
        f"/api/products/{product_id}/media/Test%20item/removebg-preview.png?thumbnail=true"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.content[:4] == b"RIFF"
    assert response.content[8:12] == b"WEBP"
    assert Image.open(BytesIO(transparent.content)).mode == "RGBA"
    assert escaped.status_code == 404
