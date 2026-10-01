import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, JSON, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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



def get_db():
    with SessionLocal() as session:
        yield session


def _scan_products(root: Path) -> List[ProductSummary]:
    if not root.exists():
        return []

    products: List[ProductSummary] = []

    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue

        files: List[ProductFile] = []
        has_manifest = False

        for file in sorted(child.rglob("*"), key=lambda p: str(p).lower()):
            if file.is_dir():
                continue

            relative_path = file.relative_to(root)
            extension = file.suffix.lower().lstrip(".") or None

            if file.name.lower() in {"product.yaml", "product.yml"}:
                has_manifest = True

            files.append(
                ProductFile(
                    name=file.name,
                    full_path=str(file),
                    relative_path=str(relative_path),
                    extension=extension,
                )
            )

        products.append(
            ProductSummary(
                name=child.name,
                path=str(child),
                files=files,
                has_manifest=has_manifest,
            )
        )

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
    return {
        "name": record.name,
        "path": record.path,
        "files": record.files,
        "has_manifest": record.has_manifest,
    }


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "OmnIkestrAPIM Core Backend",
        "version": "0.1.0",
    }


@app.get("/")
def read_root():
    return {"message": "Welcome to OmnIkestrAPIM API"}


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