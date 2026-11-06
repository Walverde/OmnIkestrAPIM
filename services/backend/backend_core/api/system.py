from pathlib import Path
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .. import runtime


router = APIRouter()
STATIC_ROOT = Path(__file__).resolve().parents[2] / "static"


@router.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "OmnIkestrAPIM Core Backend",
        "version": "0.1.0",
    }


@router.get("/")
def read_root():
    return FileResponse(runtime.DASHBOARD_PAGE, media_type="text/html")


@router.get("/home", response_class=FileResponse)
def home_page():
    return FileResponse(runtime.DASHBOARD_PAGE, media_type="text/html")


@router.get("/dashboard", response_class=FileResponse)
def dashboard_page():
    return FileResponse(runtime.DASHBOARD_PAGE, media_type="text/html")


@router.get("/settings", response_class=FileResponse)
def settings_page():
    return FileResponse(runtime.SETTINGS_PAGE, media_type="text/html")


@router.get("/products", response_class=FileResponse)
def products_page():
    return FileResponse(STATIC_ROOT / "products.html")


@router.get("/products/{product_id}", response_class=FileResponse)
def product_detail_page(product_id: UUID):
    return FileResponse(STATIC_ROOT / "product.html")
