from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import Session

from backend_core import runtime
from backend_core.api.config import (
    restore_saved_product_roots,
    router as config_router,
)
from backend_core.api.dependencies import get_db
from backend_core.api.events import router as events_router
from backend_core.api.local import router as local_router
from backend_core.api.media import router as media_router
from backend_core.api.products import router as products_router
from backend_core.api.system import router as system_router
from backend_core.catalog import (
    DIRECT_MEDIA_EXTENSIONS,
    IMAGE_EXTENSIONS,
    _analyze_product,
    _catalog_path,
    _display_path,
    _ensure_workspace,
    _image_priority,
    _is_legacy_group_manifest,
    _is_product_directory,
    _manifest_metadata,
    _metadata_value,
    _product_directory_for_path,
    _root_for_path as _catalog_root_for_path,
    _scan_product,
    _scan_products,
    _serialize_product,
    _storage_path,
    _sync_product_catalog,
    _sync_product_catalog_for_roots as _catalog_sync_product_catalog_for_roots,
    _sync_product_for_event,
    _upsert_product_record,
    _write_manifest_atomic,
)
from backend_core.database import (
    DATABASE_URL,
    Base,
    SessionLocal,
    engine,
    run_migrations,
)
from backend_core.media import (
    THUMBNAIL_CACHE_ROOT,
    THUMBNAIL_SIZE,
    create_webp_thumbnail as _create_webp_thumbnail,
    thumbnail_cache_path as _thumbnail_cache_path,
)
from backend_core.models import (
    DEFAULT_WORKSPACE_ID,
    AppSettingRecord,
    ProductEvent,
    ProductEventRecord,
    ProductFile,
    ProductManifest,
    ProductManifestPatch,
    ProductRecord,
    ProductRootConfig,
    ProductSummary,
    WorkspaceRecord,
)


MAX_PRODUCT_EVENTS = runtime.MAX_PRODUCT_EVENTS
PRODUCT_EVENT_RETENTION_DAYS = runtime.PRODUCT_EVENT_RETENTION_DAYS


def _apply_product_roots(roots: list[str | Path] | tuple[str | Path, ...]) -> tuple[Path, ...]:
    return runtime.apply_product_roots(roots)


def _root_for_path(
    path: str | Path | None, roots: tuple[Path, ...] | None = None
) -> Path | None:
    return _catalog_root_for_path(path, roots or runtime.PRODUCT_ROOTS)


def _sync_product_catalog_for_roots(
    db: Session,
    roots: tuple[Path, ...] | list[Path] | None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[ProductSummary]:
    return _catalog_sync_product_catalog_for_roots(
        db, roots or runtime.PRODUCT_ROOTS, workspace_id
    )


def _run_migrations() -> None:
    run_migrations()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _run_migrations()
    with SessionLocal() as db:
        restore_saved_product_roots(db)
        _sync_product_catalog_for_roots(db, runtime.PRODUCT_ROOTS)
    yield


app = FastAPI(
    title="OmnIkestrAPIM API",
    version="0.1.0",
    description="Core API for OmnIkestrAPIM Orchestrator",
    lifespan=lifespan,
)

app.include_router(system_router)
app.include_router(config_router)
app.include_router(events_router)
app.include_router(products_router)
app.include_router(local_router)
app.include_router(media_router)


def __getattr__(name: str):
    if name in {
        "DASHBOARD_PAGE",
        "MAX_PRODUCT_EVENTS",
        "PICKER_TOKEN",
        "PRODUCT_EVENT_RETENTION_DAYS",
        "PRODUCT_ROOTS",
        "PRODUCTS_ROOT",
        "SETTINGS_PAGE",
    }:
        return getattr(runtime, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
