import os
from pathlib import Path
from io import BytesIO
from unittest.mock import patch

import pytest
import yaml
from PIL import Image
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from main import (
    Base,
    ProductEvent,
    ProductEventRecord,
    ProductRecord,
    _scan_products,
    _sync_product_catalog,
    _sync_product_for_event,
    _product_directory_for_path,
    _write_manifest_atomic,
    app,
    get_db,
)


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

    with patch("main._write_manifest_atomic", wraps=_write_manifest_atomic) as write_manifest:
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
    client = TestClient(app)

    root = client.get("/")
    dashboard = client.get("/dashboard")
    home = client.get("/home")
    catalog = client.get("/products")
    product_detail = client.get("/products/0197f381-7c63-7a1d-a8b1-4e08b8472401")

    assert root.status_code == 200
    assert dashboard.status_code == 200
    assert home.status_code == 200
    assert catalog.status_code == 200
    assert product_detail.status_code == 200
    assert "OmnIkestrAPIM" in root.text
    assert "Painel geral" in dashboard.text
    assert "Produtos" in catalog.text
    assert "Dados do produto" in product_detail.text


def test_local_picker_token_is_not_cacheable(monkeypatch):
    monkeypatch.setattr("main.PICKER_TOKEN", "test-local-picker-token")
    response = TestClient(app).get("/api/local/picker-token")

    assert response.status_code == 200
    assert response.json() == {"token": "test-local-picker-token"}
    assert response.headers["cache-control"] == "no-store"


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
    monkeypatch.setattr("main.PRODUCTS_ROOT", product_root)
    with Session(database) as session:
        _sync_product_catalog(session, product_root)

    client = TestClient(app)
    yield client, database, product_root, product_dir, product_id
    client.close()
    app.dependency_overrides.clear()
    database.dispose()


def test_product_patch_preserves_unknown_yaml_and_prevents_write_loop(product_api):
    client, database, product_root, product_dir, product_id = product_api
    with patch("main._write_manifest_atomic", wraps=_write_manifest_atomic) as atomic_write:
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
