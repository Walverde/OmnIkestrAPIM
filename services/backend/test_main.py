import os

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from main import Base, ProductRecord, _scan_products, _sync_product_catalog, app


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


def test_scan_products_extracts_sku_from_filename_without_manifest(tmp_path):
    product_dir = tmp_path / "Suporte drone"
    product_dir.mkdir()
    (product_dir / "SKU-DRN_204.jpg").write_bytes(b"image")

    product = _scan_products(tmp_path)[0]

    assert product.has_manifest is False
    assert product.sku == "DRN_204"
    assert product.category is None
    assert product.primary_image == "Suporte drone/SKU-DRN_204.jpg"


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
        _sync_product_catalog(session, tmp_path)
        assert session.scalar(select(ProductRecord)) is None

    database.dispose()


def test_dashboard_routes_return_html():
    client = TestClient(app)

    root = client.get("/")
    dashboard = client.get("/dashboard")
    home = client.get("/home")

    assert root.status_code == 200
    assert dashboard.status_code == 200
    assert home.status_code == 200
    assert "OmnIkestrAPIM" in root.text
    assert "Painel geral" in dashboard.text
