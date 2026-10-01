import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

from main import Base, ProductRecord, _scan_products, _sync_product_catalog


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
