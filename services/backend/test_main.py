import os
from pathlib import Path
from io import BytesIO
from unittest.mock import patch

import pytest
import yaml
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ["OMNIKESTRAPIM_API_KEY"] = "test-api-key-for-tests-1234567890-abcd"
os.environ["OMNIKESTRAPIM_ADMIN_PASSWORD"] = "test-admin-password"
os.environ["OMNIKESTRAPIM_SESSION_SECRET"] = "test-session-secret-with-sufficient-length"
os.environ["OMNIKESTRAPIM_COOKIE_SECURE"] = "false"

from app import app
from backend_core.api.dependencies import get_db
from backend_core.database import Base, engine
from backend_core.marketplace_worker import _mark_reconciliation_required
from backend_core.models import (
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

        manifest.unlink()
        product_dir.rmdir()
        _sync_product_catalog(session, tmp_path)
        assert session.scalar(select(ProductRecord)) is None

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
        headers={"X-API-Key": "test-api-key-for-tests-1234567890-abcd"},
    ).status_code == 401


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
    assert client.get("/api/products").status_code == 200
    assert client.get("/api/products?workspace_id=other").status_code == 403
    assert client.post(
        "/api/products/scan",
        headers={"Origin": "https://attacker.example"},
    ).status_code == 403
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/products").status_code == 401


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
