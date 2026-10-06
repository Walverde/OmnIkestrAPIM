import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, List

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, JSON, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
import yaml

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class ProductEventRecord(Base):
    __tablename__ = "product_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(String(2048))
    is_directory: Mapped[bool] = mapped_column(Boolean, default=False)
    service: Mapped[str] = mapped_column(String(64), default="watcher")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProductRecord(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    path: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    files: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    has_manifest: Mapped[bool] = mapped_column(Boolean, default=False)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


PRODUCTS_ROOT = Path(os.getenv("PRODUCTS_ROOT", "/data/products")).resolve()
DASHBOARD_PAGE = Path(__file__).parent / "static" / "dashboard.html"
SETTINGS_PAGE = Path(__file__).parent / "static" / "settings.html"


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        _sync_product_catalog(db, PRODUCTS_ROOT)
    yield

app = FastAPI(
    title="OmnIkestrAPIM API",
    version="0.1.0",
    description="Core API for OmnIkestrAPIM Orchestrator",
    lifespan=lifespan,
)

class ProductEvent(BaseModel):
    event: str
    path: str
    is_directory: bool = False
    service: str = "watcher"


class ProductFile(BaseModel):
    name: str
    full_path: str
    relative_path: str
    extension: str | None = None


class ProductSummary(BaseModel):
    name: str
    path: str
    files: List[ProductFile]
    has_manifest: bool
    sku: str | None = None
    category: str | None = None
    description: str | None = None
    images: List[str] = Field(default_factory=list)
    primary_image: str | None = None
    manifest: dict[str, Any] | None = None
    manifest_error: str | None = None



def get_db():
    with SessionLocal() as session:
        yield session


IMAGE_EXTENSIONS = {"bmp", "gif", "heic", "jpeg", "jpg", "png", "tif", "tiff", "webp"}
SKU_PATTERN = re.compile(r"(?:^|[^a-z0-9])(?:sku|ref|referencia|codigo)[-_ ]*([a-z0-9][a-z0-9._-]*)", re.IGNORECASE)


def _manifest_metadata(files: List[ProductFile]) -> tuple[dict[str, Any] | None, str | None]:
    manifest_file = next(
        (file for file in files if file.name.lower() in {"product.yaml", "product.yml"}),
        None,
    )
    if manifest_file is None:
        return None, None

    try:
        with Path(manifest_file.full_path).open("r", encoding="utf-8") as manifest_stream:
            metadata = yaml.safe_load(manifest_stream)
        if metadata is None:
            return {}, None
        if not isinstance(metadata, dict):
            return None, "O manifesto precisa conter um objeto YAML."
        return metadata, None
    except (OSError, yaml.YAMLError) as error:
        return None, str(error)


def _metadata_value(metadata: dict[str, Any] | None, *keys: str) -> str | None:
    if not metadata:
        return None
    normalized = {str(key).casefold(): value for key, value in metadata.items()}
    for key in keys:
        value = normalized.get(key.casefold())
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return None


def _image_priority(file: ProductFile) -> tuple[int, str]:
    name = file.name.casefold()
    extension = (file.extension or "").casefold()
    if any(marker in name for marker in ("cover", "primary", "principal", "capa")):
        priority = 0
    elif "removebg-preview" in name:
        priority = 1
    elif any(marker in name for marker in ("front", "frente")):
        priority = 2
    else:
        priority = 3
    extension_priority = {"png": 0, "webp": 1, "jpg": 2, "jpeg": 2, "heic": 3}
    return priority * 10 + extension_priority.get(extension, 4), file.relative_path.casefold()


def _analyze_product(name: str, path: str, files: List[ProductFile]) -> ProductSummary:
    manifest, manifest_error = _manifest_metadata(files)
    images = sorted(
        (file for file in files if file.extension and file.extension.casefold() in IMAGE_EXTENSIONS),
        key=_image_priority,
    )
    sku = _metadata_value(manifest, "sku", "ref", "referencia", "codigo", "código")
    if sku is None:
        candidates = [(name, False), *((file.name, True) for file in files)]
        for candidate, is_filename in candidates:
            searchable_name = Path(candidate).stem if is_filename else candidate
            match = SKU_PATTERN.search(searchable_name)
            if match:
                sku = match.group(1).rstrip("._-")
                break

    return ProductSummary(
        name=_metadata_value(manifest, "name", "nome", "title", "titulo", "título") or name,
        path=path,
        files=files,
        has_manifest=manifest is not None or manifest_error is not None,
        sku=sku,
        category=_metadata_value(manifest, "category", "categoria"),
        description=_metadata_value(manifest, "description", "descricao", "descrição"),
        images=[file.relative_path for file in images],
        primary_image=images[0].relative_path if images else None,
        manifest=manifest,
        manifest_error=manifest_error,
    )


def _scan_products(root: Path) -> List[ProductSummary]:
    if not root.exists():
        return []

    products: List[ProductSummary] = []

    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue

        files: List[ProductFile] = []
        for file in sorted(child.rglob("*"), key=lambda p: str(p).lower()):
            if file.is_dir():
                continue

            relative_path = file.relative_to(root)
            extension = file.suffix.lower().lstrip(".") or None

            files.append(
                ProductFile(
                    name=file.name,
                    full_path=str(file),
                    relative_path=str(relative_path),
                    extension=extension,
                )
            )

        products.append(_analyze_product(child.name, str(child), files))

    return products


def _sync_product_catalog(db: Session, root: Path) -> List[ProductSummary]:
    products = _scan_products(root)
    existing = {
        record.path: record
        for record in db.scalars(select(ProductRecord)).all()
    }
    current_paths = set()

    for product in products:
        current_paths.add(product.path)
        record = existing.get(product.path)
        if record is None:
            record = ProductRecord(path=product.path)
            db.add(record)

        record.name = product.name
        record.files = [file.model_dump() for file in product.files]
        record.has_manifest = product.has_manifest

    for path, record in existing.items():
        if path not in current_paths:
            db.delete(record)

    db.commit()
    return products


def _serialize_product(record: ProductRecord) -> dict[str, object]:
    files = [ProductFile.model_validate(file) for file in record.files]
    return _analyze_product(record.name, record.path, files).model_dump()


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "OmnIkestrAPIM Core Backend",
        "version": "0.1.0",
    }


@app.get("/")
def read_root():
    return FileResponse(DASHBOARD_PAGE, media_type="text/html")


@app.get("/home", response_class=FileResponse)
def home_page():
    return FileResponse(DASHBOARD_PAGE, media_type="text/html")


@app.get("/dashboard", response_class=FileResponse)
def dashboard_page():
    return FileResponse(DASHBOARD_PAGE, media_type="text/html")


@app.get("/settings", response_class=FileResponse)
def settings_page():
    return FileResponse(SETTINGS_PAGE, media_type="text/html")


@app.get("/api/products")
def list_products(db: Session = Depends(get_db)):
    products = db.scalars(select(ProductRecord).order_by(ProductRecord.name)).all()
    return {
        "products": [_serialize_product(product) for product in products],
        "root": str(PRODUCTS_ROOT),
    }


@app.post("/api/products/scan")
def scan_products(db: Session = Depends(get_db)):
    products = _sync_product_catalog(db, PRODUCTS_ROOT)
    return {"products": [product.model_dump() for product in products]}


@app.get("/api/products/events")
def get_product_events(db: Session = Depends(get_db)):
    events = db.scalars(
        select(ProductEventRecord).order_by(ProductEventRecord.id)
    ).all()
    return {
        "events": [
            {
                "id": event.id,
                "event": event.event,
                "path": event.path,
                "is_directory": event.is_directory,
                "service": event.service,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ]
    }


@app.post("/api/products/watch")
def receive_product_event(event: ProductEvent, db: Session = Depends(get_db)):
    record = ProductEventRecord(**event.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    _sync_product_catalog(db, PRODUCTS_ROOT)
    return {
        "status": "accepted",
        "event": event.model_dump(),
        "id": record.id,
    }