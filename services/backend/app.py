import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend_core.api.config import restore_saved_product_roots, router as config_router
from backend_core.api.events import router as events_router
from backend_core.api.local import router as local_router
from backend_core.api.marketplaces import router as marketplaces_router
from backend_core.api.media import router as media_router
from backend_core.api.observability import HTTPMetricsMiddleware, router as observability_router
from backend_core.api.products import router as products_router
from backend_core.api.system import router as system_router
from backend_core.catalog import _sync_product_catalog_for_roots
from backend_core.database import SessionLocal, run_migrations
from backend_core.marketplace_worker import run_marketplace_worker
from backend_core import runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    with SessionLocal() as db:
        try:
            restore_saved_product_roots(db)
        except RuntimeError:
            pass

    stop_event = asyncio.Event()

    def reconcile_catalog() -> None:
        with SessionLocal() as db:
            _sync_product_catalog_for_roots(db, runtime.PRODUCT_ROOTS)

    catalog_task = asyncio.create_task(asyncio.to_thread(reconcile_catalog))
    worker_task = asyncio.create_task(run_marketplace_worker(stop_event))
    app.state.marketplace_worker_stop = stop_event
    try:
        yield
    finally:
        stop_event.set()
        if not catalog_task.done():
            catalog_task.cancel()
        with suppress(asyncio.CancelledError):
            await catalog_task
        with suppress(asyncio.CancelledError):
            await worker_task


app = FastAPI(
    title="OmnIkestrAPIM API",
    version="0.2.0",
    description="Core API for OmnIkestrAPIM filesystem-first commerce platform",
    lifespan=lifespan,
)

app.include_router(system_router)
app.include_router(config_router)
app.include_router(events_router)
app.include_router(products_router)
app.include_router(media_router)
app.include_router(local_router)
app.include_router(marketplaces_router)
app.include_router(observability_router)
app.add_middleware(HTTPMetricsMiddleware)
STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
