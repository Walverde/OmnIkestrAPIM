import asyncio
import hmac
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend_core.api.auth import (
    SESSION_COOKIE_NAME,
    configured_api_key,
    is_valid_session,
    router as auth_router,
    validate_security_configuration,
)
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
from backend_core.json_logging import configure_json_logging
from backend_core.marketplace_worker import run_marketplace_worker
from backend_core import runtime
from backend_core.models import DEFAULT_WORKSPACE_ID


configure_json_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_security_configuration()
    run_migrations()
    with SessionLocal() as db:
        app.state.setup_warning = restore_saved_product_roots(db)
    if app.state.setup_warning:
        logger.warning(
            app.state.setup_warning,
            extra={"event": "setup.saved_product_roots_invalid"},
        )

    stop_event = asyncio.Event()

    def reconcile_catalog() -> None:
        with SessionLocal() as db:
            _sync_product_catalog_for_roots(db, runtime.PRODUCT_ROOTS)

    catalog_task = (
        None
        if app.state.setup_warning
        else asyncio.create_task(asyncio.to_thread(reconcile_catalog))
    )
    worker_task = asyncio.create_task(run_marketplace_worker(stop_event))
    app.state.marketplace_worker_stop = stop_event
    try:
        yield
    finally:
        stop_event.set()
        if catalog_task is not None and not catalog_task.done():
            catalog_task.cancel()
        if catalog_task is not None:
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

app.include_router(auth_router)
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


@app.middleware("http")
async def require_authenticated_session(request, call_next):
    path = request.url.path
    public_api_paths = {
        "/api/health",
        "/api/ready",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/session",
        "/api/marketplaces/mercadolivre/callback",
    }
    is_api_request = path.startswith("/api/")
    is_authenticated = (
        await asyncio.to_thread(is_valid_session, request)
        if request.cookies.get(SESSION_COOKIE_NAME)
        else False
    )
    request.state.user_authenticated = is_authenticated

    if is_api_request and path not in public_api_paths:
        service_key_allowed = (
            path in {"/api/products/watch", "/api/products/scan"}
            and request.method == "POST"
        )
        if not is_authenticated and service_key_allowed:
            try:
                expected_key = configured_api_key()
            except RuntimeError:
                expected_key = ""
            supplied_key = request.headers.get("X-API-Key", "")
            if expected_key and supplied_key:
                if hmac.compare_digest(
                    supplied_key.encode("utf-8"), expected_key.encode("utf-8")
                ):
                    request.state.service_authenticated = True
                    is_authenticated = True
        if not is_authenticated:
            return JSONResponse(
                {"detail": "Autenticação necessária."},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        if any(
            workspace_id != DEFAULT_WORKSPACE_ID
            for workspace_id in request.query_params.getlist("workspace_id")
        ):
            return JSONResponse(
                {"detail": "A sessão não tem acesso a esse workspace."},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if (
            request.state.user_authenticated
            and request.method not in {"GET", "HEAD", "OPTIONS"}
        ):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc.casefold() != request.url.netloc.casefold():
                return JSONResponse(
                    {"detail": "Origem da requisição inválida."},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )

    if path in {"/docs", "/redoc", "/openapi.json"}:
        if not is_authenticated:
            return RedirectResponse("/login", status_code=303)
    elif (
        not is_api_request
        and path != "/login"
        and path != "/metrics"
        and (
            not path.startswith("/static/")
            or path.endswith(".html")
        )
    ):
        if not is_authenticated:
            return RedirectResponse("/login", status_code=303)

    response = await call_next(request)
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response
