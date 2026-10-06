import json
import hashlib
import io
import os
import re
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from pydantic import BaseModel, Field, ValidationError
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    JSON,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
import yaml

register_heif_opener()

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AppSettingRecord(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String(2048))


class ProductEventRecord(Base):
    __tablename__ = "product_events"
    __table_args__ = (
        Index("ix_product_events_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default="default", index=True
    )
    event: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(String(2048))
    destination_path: Mapped[str | None] = mapped_column(String(2048))
    is_directory: Mapped[bool] = mapped_column(Boolean, default=False)
    service: Mapped[str] = mapped_column(String(64), default="watcher")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProductManifest(BaseModel):
    model_config = {"extra": "allow"}

    schema_version: Literal[1] = 1
    id: UUID = Field(default_factory=uuid4)
    sku: str | None = None
    title: str = Field(min_length=1)
    category: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    condition: Literal["new", "used", "refurbished"] | None = None
    status: Literal["pending", "published", "sold", "archived"] = "pending"
    marketplaces: list[str] = Field(default_factory=list)
    description: str | None = None


class ProductManifestPatch(BaseModel):
    model_config = {"extra": "forbid"}

    sku: str | None = None
    title: str | None = Field(default=None, min_length=1)
    category: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    condition: Literal["new", "used", "refurbished"] | None = None
    status: Literal["pending", "published", "sold", "archived"] | None = None
    marketplaces: list[str] | None = None
    description: str | None = None


class ProductRecord(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("workspace_id", "product_uuid", name="uq_products_workspace_product_uuid"),
        UniqueConstraint("workspace_id", "path", name="uq_products_workspace_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default="default", index=True
    )
    product_uuid: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512))
    folder_group: Mapped[str] = mapped_column(String(1024), default="", index=True)
    sku: Mapped[str | None] = mapped_column(String(256), index=True)
    category: Mapped[str | None] = mapped_column(String(256), index=True)
    description: Mapped[str | None] = mapped_column(String(4096))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    condition: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    marketplaces: Mapped[list[str]] = mapped_column(JSON, default=list)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest_error: Mapped[str | None] = mapped_column(String(2048))
    ambiguous_structure: Mapped[bool] = mapped_column(Boolean, default=False)
    path: Mapped[str] = mapped_column(String(2048), index=True)
    files: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    has_manifest: Mapped[bool] = mapped_column(Boolean, default=False)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


PRODUCTS_ROOT = Path(os.getenv("PRODUCTS_ROOT", "/data/products")).resolve()
DEFAULT_WORKSPACE_ID = "default"
MAX_PRODUCT_EVENTS = 10000
PRODUCT_EVENT_RETENTION_DAYS = 90
PICKER_TOKEN = os.getenv("PICKER_TOKEN", "")
THUMBNAIL_CACHE_ROOT = Path(os.getenv("THUMBNAIL_CACHE_ROOT", "/tmp/omnikestr-thumbnails"))
THUMBNAIL_SIZE = 1280
DASHBOARD_PAGE = Path(__file__).parent / "static" / "dashboard.html"
SETTINGS_PAGE = Path(__file__).parent / "static" / "settings.html"


def _run_migrations() -> None:
    config = AlembicConfig(str(Path(__file__).parent / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")


@asynccontextmanager
async def lifespan(_: FastAPI):
    _run_migrations()
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
    event: Literal["created", "modified", "deleted", "moved"]
    path: str
    destination_path: str | None = None
    is_directory: bool = False
    service: str = "watcher"
    workspace_id: str = Field(default=DEFAULT_WORKSPACE_ID, min_length=1, max_length=64)


class ProductFile(BaseModel):
    name: str
    full_path: str
    relative_path: str
    extension: str | None = None


class ProductSummary(BaseModel):
    id: str | None = None
    name: str
    path: str
    folder_group: str = ""
    files: List[ProductFile]
    has_manifest: bool
    sku: str | None = None
    title: str | None = None
    category: str | None = None
    description: str | None = None
    price: Decimal | None = None
    cost: Decimal | None = None
    condition: str | None = None
    status: str | None = None
    marketplaces: List[str] = Field(default_factory=list)
    images: List[str] = Field(default_factory=list)
    primary_image: str | None = None
    category_suggestion: str | None = None
    category_mismatch: bool = False
    ambiguous_structure: bool = False
    manifest: dict[str, Any] | None = None
    manifest_error: str | None = None



def get_db():
    with SessionLocal() as session:
        yield session


def _ensure_workspace(db: Session, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
    if db.get(WorkspaceRecord, workspace_id) is None:
        db.add(WorkspaceRecord(id=workspace_id, name=workspace_id))
        db.flush()


IMAGE_EXTENSIONS = {"bmp", "gif", "heic", "jpeg", "jpg", "png", "tif", "tiff", "webp"}
DIRECT_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | {
    "avi", "m4a", "m4v", "mkv", "mov", "mp3", "mp4", "mpeg", "mpg", "wav", "webm"
}
LEGACY_GROUP_MANIFEST_KEYS = {"schema_version", "id", "title"}
SKU_PATTERN = re.compile(r"(?:^|[^a-z0-9])(?:sku|ref|referencia|codigo)[-_ ]*([a-z0-9][a-z0-9._-]*)", re.IGNORECASE)


def _write_manifest_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            yaml.safe_dump(data, temporary_file, allow_unicode=True, sort_keys=False)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _manifest_metadata(
    files: List[ProductFile], product_path: str, preferred_id: str | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    manifest_file = next(
        (
            file
            for file in files
            if file.name.lower() in {"product.yaml", "product.yml"}
            and Path(file.full_path).parent.resolve() == Path(product_path).resolve()
        ),
        None,
    )
    manifest_path = (
        Path(manifest_file.full_path)
        if manifest_file is not None
        else Path(product_path) / "product.yaml"
    )

    try:
        manifest_exists = manifest_path.exists()
        if manifest_exists:
            with manifest_path.open("r", encoding="utf-8") as manifest_stream:
                raw_metadata = yaml.safe_load(manifest_stream)
        else:
            raw_metadata = {}
        if raw_metadata is None:
            raw_metadata = {}
        if not isinstance(raw_metadata, dict):
            return None, "O manifesto precisa conter um objeto YAML."
    except (OSError, yaml.YAMLError) as error:
        return None, str(error)

    needs_write = not manifest_exists
    if "schema_version" not in raw_metadata:
        raw_metadata["schema_version"] = 1
        needs_write = True
    if "id" not in raw_metadata:
        raw_metadata["id"] = preferred_id or str(uuid4())
        needs_write = True
    if "title" not in raw_metadata:
        raw_metadata["title"] = raw_metadata.get("name") or Path(product_path).name
        needs_write = True

    try:
        manifest = ProductManifest.model_validate(raw_metadata)
        if needs_write:
            _write_manifest_atomic(manifest_path, raw_metadata)
    except (OSError, ValidationError) as error:
        if isinstance(error, ValidationError):
            details = "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors()
            )
            if any(item["loc"] == ("id",) for item in error.errors()):
                raw_metadata["id"] = str(preferred_id or uuid4())
                try:
                    _write_manifest_atomic(manifest_path, raw_metadata)
                except OSError:
                    pass
            try:
                return json.loads(json.dumps(raw_metadata, default=str)), details
            except (TypeError, ValueError):
                return None, f"{details}; o manifesto contém dados que não podem ser indexados."
        return raw_metadata, str(error)

    return manifest.model_dump(mode="json"), None


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


def _analyze_product(
    name: str,
    path: str,
    files: List[ProductFile],
    preferred_id: str | None = None,
    folder_group: str = "",
    ambiguous_structure: bool = False,
) -> ProductSummary:
    manifest, manifest_error = _manifest_metadata(files, path, preferred_id)
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

    category = _metadata_value(manifest, "category", "categoria")
    category_suggestion = folder_group.rsplit("/", 1)[-1] if folder_group else None

    return ProductSummary(
        id=str(manifest["id"]) if manifest and manifest.get("id") else None,
        name=_metadata_value(manifest, "title", "name", "nome", "titulo", "título") or name,
        path=path,
        folder_group=folder_group,
        files=files,
        has_manifest=manifest is not None or manifest_error is not None,
        sku=sku,
        title=_metadata_value(manifest, "title", "name", "nome", "titulo", "título") or name,
        category=category,
        description=_metadata_value(manifest, "description", "descricao", "descrição"),
        price=manifest.get("price") if manifest and not manifest_error else None,
        cost=manifest.get("cost") if manifest and not manifest_error else None,
        condition=manifest.get("condition") if manifest and not manifest_error else None,
        status=manifest.get("status") if manifest and not manifest_error else None,
        marketplaces=(
            manifest.get("marketplaces", [])
            if manifest and not manifest_error and isinstance(manifest.get("marketplaces", []), list)
            else []
        ),
        images=[file.relative_path for file in images],
        primary_image=images[0].relative_path if images else None,
        category_suggestion=category_suggestion,
        category_mismatch=bool(
            category
            and category_suggestion
            and category.strip().casefold() != category_suggestion.strip().casefold()
        ),
        ambiguous_structure=ambiguous_structure,
        manifest=manifest,
        manifest_error=manifest_error,
    )


def _scan_product(
    root: Path,
    product_path: Path,
    preferred_id: str | None = None,
    folder_group: str | None = None,
    ambiguous_structure: bool = False,
) -> ProductSummary:
    if folder_group is None:
        try:
            parent_group = product_path.resolve().parent.relative_to(root.resolve())
            folder_group = "" if str(parent_group) == "." else parent_group.as_posix()
        except ValueError:
            folder_group = ""
    files: List[ProductFile] = []
    for file in sorted(product_path.rglob("*"), key=lambda item: str(item).lower()):
        if file.is_dir():
            continue
        files.append(
            ProductFile(
                name=file.name,
                full_path=str(file),
                relative_path=str(file.relative_to(root)),
                extension=file.suffix.lower().lstrip(".") or None,
            )
        )

    product = _analyze_product(
        product_path.name,
        str(product_path),
        files,
        preferred_id=preferred_id,
        folder_group=folder_group,
        ambiguous_structure=ambiguous_structure,
    )
    has_root_manifest = any(
        file.name.lower() in {"product.yaml", "product.yml"}
        and Path(file.full_path).parent.resolve() == product_path.resolve()
        for file in files
    )
    if product.has_manifest and not has_root_manifest:
        manifest_path = product_path / "product.yaml"
        files.append(
            ProductFile(
                name=manifest_path.name,
                full_path=str(manifest_path),
                relative_path=str(manifest_path.relative_to(root)),
                extension="yaml",
            )
        )
        product.files = files
    return product


def _scan_products(
    root: Path, preferred_ids: dict[str, str] | None = None
) -> List[ProductSummary]:
    if not root.exists():
        return []
    preferred_ids = preferred_ids or {}
    products: List[ProductSummary] = []

    def walk(group_path: Path, group_parts: list[str]) -> None:
        for folder in sorted(group_path.iterdir(), key=lambda path: path.name.casefold()):
            if not folder.is_dir():
                continue

            direct_files = [entry for entry in folder.iterdir() if entry.is_file()]
            child_folders = [entry for entry in folder.iterdir() if entry.is_dir()]
            has_manifest = any(
                entry.name.casefold() in {"product.yaml", "product.yml"}
                for entry in direct_files
            )
            manifest_path = next(
                (
                    entry
                    for entry in direct_files
                    if entry.name.casefold() in {"product.yaml", "product.yml"}
                ),
                None,
            )
            legacy_group_manifest = bool(
                manifest_path
                and child_folders
                and _is_legacy_group_manifest(manifest_path)
            )
            has_direct_media = any(
                entry.suffix.lower().lstrip(".") in DIRECT_MEDIA_EXTENSIONS
                for entry in direct_files
            )

            if (has_manifest and not (legacy_group_manifest and not has_direct_media)) or has_direct_media:
                group = "/".join(group_parts)
                products.append(
                    _scan_product(
                        root,
                        folder,
                        preferred_ids.get(str(folder)),
                        folder_group=group,
                        ambiguous_structure=(
                            (not has_manifest or legacy_group_manifest)
                            and has_direct_media
                            and bool(child_folders)
                        ),
                    )
                )
                continue

            walk(folder, [*group_parts, folder.name])

    walk(root, [])
    return products


def _is_legacy_group_manifest(manifest_path: Path) -> bool:
    try:
        metadata = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    return isinstance(metadata, dict) and set(metadata) == LEGACY_GROUP_MANIFEST_KEYS


def _upsert_product_record(
    db: Session, product: ProductSummary, workspace_id: str
) -> ProductRecord:
    record = db.scalar(
        select(ProductRecord).where(
            ProductRecord.workspace_id == workspace_id,
            ProductRecord.product_uuid == product.id,
        )
    )
    if record is None:
        record = db.scalar(
            select(ProductRecord).where(
                ProductRecord.workspace_id == workspace_id,
                ProductRecord.path == product.path,
            )
        )
    if record is None:
        record = ProductRecord(
            workspace_id=workspace_id,
            product_uuid=product.id or str(uuid4()),
            name=product.name,
            title=product.title or product.name,
            path=product.path,
        )
        db.add(record)

    record.product_uuid = product.id or record.product_uuid
    record.name = product.name
    record.title = product.title or product.name
    record.folder_group = product.folder_group
    record.folder_group = product.folder_group
    record.path = product.path
    record.sku = product.sku
    record.category = product.category
    record.description = product.description
    record.price = product.price
    record.cost = product.cost
    record.condition = product.condition
    record.status = product.status or "pending"
    record.marketplaces = product.marketplaces
    record.manifest = product.manifest or {}
    record.manifest_error = product.manifest_error
    record.ambiguous_structure = product.ambiguous_structure
    record.files = [file.model_dump() for file in product.files]
    record.has_manifest = product.has_manifest
    return record


def _sync_product_catalog(
    db: Session, root: Path, workspace_id: str = DEFAULT_WORKSPACE_ID
) -> List[ProductSummary]:
    _ensure_workspace(db, workspace_id)
    existing_records = db.scalars(
        select(ProductRecord).where(ProductRecord.workspace_id == workspace_id)
    ).all()
    preferred_ids = {record.path: record.product_uuid for record in existing_records}
    products = _scan_products(root, preferred_ids)
    current_paths = {product.path for product in products}
    current_uuids = {product.id for product in products if product.id}

    for product in products:
        _upsert_product_record(db, product, workspace_id)

    for record in existing_records:
        if record.path not in current_paths and record.product_uuid not in current_uuids:
            db.delete(record)

    db.commit()
    return products


def _is_product_directory(path: Path) -> bool:
    try:
        direct_entries = list(path.iterdir())
    except OSError:
        return False
    has_manifest = any(
        entry.is_file() and entry.name.casefold() in {"product.yaml", "product.yml"}
        for entry in direct_entries
    )
    has_direct_media = any(
        entry.is_file() and entry.suffix.lower().lstrip(".") in DIRECT_MEDIA_EXTENSIONS
        for entry in direct_entries
    )
    manifest_path = next(
        (
            entry
            for entry in direct_entries
            if entry.is_file() and entry.name.casefold() in {"product.yaml", "product.yml"}
        ),
        None,
    )
    has_children = any(entry.is_dir() for entry in direct_entries)
    if (
        has_manifest
        and has_children
        and not has_direct_media
        and manifest_path is not None
        and _is_legacy_group_manifest(manifest_path)
    ):
        return False
    return has_manifest or has_direct_media


def _product_directory_for_path(
    root: Path,
    path: str,
    db: Session,
    workspace_id: str,
    is_directory: bool = False,
) -> Path | None:
    try:
        target = Path(path).resolve()
        relative_path = target.relative_to(root.resolve())
    except ValueError:
        return None
    if not relative_path.parts:
        return None

    candidate = target if is_directory else target.parent
    while candidate != root.resolve() and root.resolve() in candidate.parents:
        indexed = db.scalar(
            select(ProductRecord.path).where(
                ProductRecord.workspace_id == workspace_id,
                ProductRecord.path == str(candidate),
            )
        )
        if indexed or (candidate.is_dir() and _is_product_directory(candidate)):
            return candidate
        candidate = candidate.parent
    return None


def _sync_product_for_event(
    db: Session, root: Path, event: ProductEvent
) -> list[ProductSummary]:
    _ensure_workspace(db, event.workspace_id)
    source_dir = _product_directory_for_path(
        root, event.path, db, event.workspace_id, event.is_directory
    )
    destination_dir = (
        _product_directory_for_path(
            root, event.destination_path, db, event.workspace_id, event.is_directory
        )
        if event.destination_path
        else source_dir
    )
    affected_dirs = list(dict.fromkeys(path for path in (source_dir, destination_dir) if path))
    if not affected_dirs:
        return []

    source_record = None
    if (
        event.event == "moved"
        and event.is_directory
        and source_dir is not None
        and source_dir != destination_dir
    ):
        source_record = db.scalar(
            select(ProductRecord).where(
                ProductRecord.workspace_id == event.workspace_id,
                ProductRecord.path == str(source_dir),
            )
        )

    indexed: list[ProductSummary] = []
    for product_dir in affected_dirs:
        if product_dir.is_dir():
            preferred_id = (
                source_record.product_uuid
                if source_record is not None and product_dir == destination_dir
                else None
            )
            product = _scan_product(root.resolve(), product_dir, preferred_id)
            _upsert_product_record(db, product, event.workspace_id)
            indexed.append(product)
        else:
            record = db.scalar(
                select(ProductRecord).where(
                    ProductRecord.workspace_id == event.workspace_id,
                    ProductRecord.path == str(product_dir),
                )
            )
            if record is not None:
                db.delete(record)
    db.commit()
    return indexed


def _serialize_product(record: ProductRecord) -> dict[str, object]:
    files = [ProductFile.model_validate(file) for file in record.files]
    images = sorted(
        (file for file in files if file.extension and file.extension.casefold() in IMAGE_EXTENSIONS),
        key=_image_priority,
    )
    return ProductSummary(
        id=record.product_uuid,
        name=record.name,
        title=record.title,
        path=record.path,
        folder_group=record.folder_group or "",
        files=files,
        has_manifest=record.has_manifest,
        sku=record.sku,
        category=record.category,
        description=record.description,
        price=record.price,
        cost=record.cost,
        condition=record.condition,
        status=record.status,
        marketplaces=record.marketplaces or [],
        images=[file.relative_path for file in images],
        primary_image=images[0].relative_path if images else None,
        category_suggestion=record.folder_group.rsplit("/", 1)[-1] if record.folder_group else None,
        category_mismatch=bool(
            record.category
            and record.folder_group
            and record.category.strip().casefold()
            != record.folder_group.rsplit("/", 1)[-1].strip().casefold()
        ),
        ambiguous_structure=record.ambiguous_structure,
        manifest=record.manifest,
        manifest_error=record.manifest_error,
    ).model_dump()


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


@app.get("/products", response_class=FileResponse)
def products_page():
    return FileResponse(Path(__file__).parent / "static" / "products.html")


@app.get("/products/{product_id}", response_class=FileResponse)
def product_detail_page(product_id: UUID):
    return FileResponse(Path(__file__).parent / "static" / "product.html")


@app.get("/api/products")
def list_products(
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    folder_group: str | None = Query(default=None, max_length=1024),
    category: str | None = Query(default=None, max_length=256),
    status: str | None = Query(default=None, max_length=32),
    condition: str | None = Query(default=None, max_length=32),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    filters = [ProductRecord.workspace_id == workspace_id]
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(
            ProductRecord.title.ilike(pattern)
            | ProductRecord.sku.ilike(pattern)
            | ProductRecord.category.ilike(pattern)
            | ProductRecord.folder_group.ilike(pattern)
            | ProductRecord.description.ilike(pattern)
        )
    if folder_group:
        filters.append(ProductRecord.folder_group == folder_group)
    if category:
        filters.append(ProductRecord.category == category)
    if status:
        filters.append(ProductRecord.status == status)
    if condition:
        filters.append(ProductRecord.condition == condition)

    total = db.scalar(select(func.count()).select_from(ProductRecord).where(*filters)) or 0
    products = db.scalars(
        select(ProductRecord)
        .where(*filters)
        .order_by(ProductRecord.title, ProductRecord.product_uuid)
        .offset(offset)
        .limit(limit)
    ).all()
    categories = db.scalars(
        select(ProductRecord.category)
        .where(
            ProductRecord.workspace_id == workspace_id,
            ProductRecord.category.is_not(None),
        )
        .distinct()
        .order_by(ProductRecord.category)
    ).all()
    folder_groups = db.scalars(
        select(ProductRecord.folder_group)
        .where(
            ProductRecord.workspace_id == workspace_id,
            ProductRecord.folder_group != "",
        )
        .distinct()
        .order_by(ProductRecord.folder_group)
    ).all()
    return {
        "products": [_serialize_product(product) for product in products],
        "total": total,
        "limit": limit,
        "offset": offset,
        "categories": categories,
        "folder_groups": folder_groups,
        "root": str(PRODUCTS_ROOT),
    }


@app.post("/api/products/scan")
def scan_products(
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    products = _sync_product_catalog(db, PRODUCTS_ROOT, workspace_id)
    return {"products": [product.model_dump() for product in products]}


def _prune_product_events(db: Session, workspace_id: str) -> None:
    retention_cutoff = datetime.now(timezone.utc) - timedelta(
        days=PRODUCT_EVENT_RETENTION_DAYS
    )
    db.execute(
        delete(ProductEventRecord).where(
            ProductEventRecord.workspace_id == workspace_id,
            ProductEventRecord.created_at < retention_cutoff,
        )
    )
    overflow_ids = (
        select(ProductEventRecord.id)
        .where(ProductEventRecord.workspace_id == workspace_id)
        .order_by(ProductEventRecord.created_at.desc(), ProductEventRecord.id.desc())
        .offset(MAX_PRODUCT_EVENTS)
        .subquery()
    )
    db.execute(
        delete(ProductEventRecord).where(ProductEventRecord.id.in_(select(overflow_ids.c.id)))
    )
    db.commit()


@app.get("/api/products/events")
def get_product_events(
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    events = list(
        db.scalars(
            select(ProductEventRecord)
            .where(ProductEventRecord.workspace_id == workspace_id)
            .order_by(ProductEventRecord.created_at.desc(), ProductEventRecord.id.desc())
            .limit(limit)
        ).all()
    )
    events.reverse()
    return {
        "events": [
            {
                "id": event.id,
                "workspace_id": event.workspace_id,
                "event": event.event,
                "path": event.path,
                "destination_path": event.destination_path,
                "is_directory": event.is_directory,
                "service": event.service,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ]
    }


@app.post("/api/products/watch")
def receive_product_event(event: ProductEvent, db: Session = Depends(get_db)):
    _ensure_workspace(db, event.workspace_id)
    record = ProductEventRecord(**event.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    indexed_products = _sync_product_for_event(db, PRODUCTS_ROOT, event)
    _prune_product_events(db, event.workspace_id)
    return {
        "status": "accepted",
        "event": event.model_dump(),
        "indexed_products": len(indexed_products),
        "id": record.id,
    }


def _manifest_path_for_record(record: ProductRecord) -> Path:
    manifest_file = next(
        (
            file
            for file in record.files
            if str(file.get("name", "")).lower() in {"product.yaml", "product.yml"}
        ),
        None,
    )
    product_root = Path(record.path).resolve()
    manifest_path = (
        Path(str(manifest_file["full_path"]))
        if manifest_file
        else product_root / "product.yaml"
    ).resolve()
    try:
        manifest_path.relative_to(product_root)
    except ValueError as error:
        raise HTTPException(status_code=409, detail="Manifesto fora da pasta do produto.") from error
    return manifest_path


def _validation_details(error: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
        }
        for item in error.errors()
    ]


@app.get("/api/products/{product_id}")
def get_product_detail(
    product_id: UUID,
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    product = db.scalar(
        select(ProductRecord).where(
            ProductRecord.workspace_id == workspace_id,
            ProductRecord.product_uuid == str(product_id),
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado.")
    return _serialize_product(product)


@app.patch("/api/products/{product_id}")
def patch_product_detail(
    product_id: UUID,
    changes: ProductManifestPatch,
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    product = db.scalar(
        select(ProductRecord).where(
            ProductRecord.workspace_id == workspace_id,
            ProductRecord.product_uuid == str(product_id),
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado.")

    manifest_path = _manifest_path_for_record(product)
    try:
        with manifest_path.open("r", encoding="utf-8") as manifest_stream:
            manifest_data = yaml.safe_load(manifest_stream)
    except (OSError, yaml.YAMLError) as error:
        raise HTTPException(status_code=409, detail=f"Manifesto inválido: {error}") from error
    if not isinstance(manifest_data, dict):
        raise HTTPException(status_code=409, detail="O manifesto precisa conter um objeto YAML.")

    updated_manifest = {
        **manifest_data,
        **changes.model_dump(exclude_unset=True, mode="json"),
    }
    try:
        validated_manifest = ProductManifest.model_validate(updated_manifest)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=_validation_details(error)) from error

    try:
        _write_manifest_atomic(manifest_path, validated_manifest.model_dump(mode="json"))
    except OSError as error:
        raise HTTPException(status_code=500, detail="Não foi possível gravar o manifesto.") from error

    scanned_product = _scan_product(
        PRODUCTS_ROOT.resolve(), Path(product.path), preferred_id=str(product.product_uuid)
    )
    _upsert_product_record(db, scanned_product, workspace_id)
    db.commit()
    db.refresh(product)
    return _serialize_product(product)


@app.get("/api/local/picker-token")
def local_picker_token():
    if not PICKER_TOKEN:
        raise HTTPException(status_code=503, detail="Seletor local não configurado.")
    return JSONResponse(
        content={"token": PICKER_TOKEN},
        headers={"Cache-Control": "no-store"},
    )


def _thumbnail_cache_path(product: ProductRecord, source_path: Path) -> Path:
    stat = source_path.stat()
    fingerprint = hashlib.sha256(
        f"{product.product_uuid}:{source_path}:{stat.st_size}:{stat.st_mtime_ns}:{THUMBNAIL_SIZE}".encode()
    ).hexdigest()
    return THUMBNAIL_CACHE_ROOT / f"{fingerprint}.webp"


def _create_webp_thumbnail(source_path: Path, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as source_image:
        image = ImageOps.exif_transpose(source_image)
        preserve_alpha = "A" in image.getbands() or "transparency" in image.info
        image.thumbnail(
            (THUMBNAIL_SIZE, THUMBNAIL_SIZE),
            Image.Resampling.LANCZOS,
        )
        image = image.convert("RGBA" if preserve_alpha else "RGB")
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=84, method=6)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=cache_path.parent, prefix=".thumbnail-", suffix=".tmp", delete=False
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(output.getvalue())
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, cache_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


@app.get("/api/products/{product_id}/media/{relative_path:path}")
def get_product_media(
    product_id: UUID,
    relative_path: str,
    thumbnail: bool = Query(True),
    db: Session = Depends(get_db),
):
    product = db.scalar(
        select(ProductRecord).where(
            ProductRecord.workspace_id == DEFAULT_WORKSPACE_ID,
            ProductRecord.product_uuid == str(product_id),
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado.")

    product_root = Path(product.path).resolve()
    source_path = (PRODUCTS_ROOT / relative_path).resolve()
    try:
        source_path.relative_to(product_root)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado para este produto.") from error
    if not source_path.is_file() or source_path.suffix.lower().lstrip(".") not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Imagem não encontrada.")

    extension = source_path.suffix.lower()
    if not thumbnail and extension not in {".heic", ".heif"}:
        return FileResponse(source_path, headers={"Cache-Control": "no-cache"})

    cache_path = _thumbnail_cache_path(product, source_path)
    if not cache_path.exists():
        try:
            _create_webp_thumbnail(source_path, cache_path)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=415, detail="Não foi possível processar esta imagem.") from error
    return FileResponse(
        cache_path,
        media_type="image/webp",
        headers={"Cache-Control": "no-cache"},
    )