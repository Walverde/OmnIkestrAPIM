import os
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import runtime
from ..api.config import SETUP_COMPLETE_SETTING
from ..models import AppSettingRecord, ProductRecord
from .dependencies import get_db


router = APIRouter()
STATIC_ROOT = Path(__file__).resolve().parents[2] / "static"


@router.get("/api/health")
def health_check(response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    catalog_updated_at = db.scalar(select(func.max(ProductRecord.scanned_at)))
    return {
        "status": "healthy",
        "service": "OmnIkestrAPIM Core Backend",
        "version": os.getenv("BUILD_VERSION") or "development",
        "commit": os.getenv("BUILD_COMMIT", "unknown"),
        "built_at": os.getenv("BUILD_DATE", "unknown"),
        "catalog_updated_at": catalog_updated_at.isoformat() if catalog_updated_at else None,
    }


@router.get("/")
def read_root(db: Session = Depends(get_db)):
    setup_complete = db.get(AppSettingRecord, SETUP_COMPLETE_SETTING)
    has_products = db.scalar(select(ProductRecord.id).limit(1)) is not None
    if not has_products and not (setup_complete and setup_complete.value == "true"):
        return FileResponse(runtime.SETUP_PAGE, media_type="text/html")
    return FileResponse(runtime.DASHBOARD_PAGE, media_type="text/html")


@router.get("/setup", response_class=FileResponse)
def setup_page():
    return FileResponse(runtime.SETUP_PAGE, media_type="text/html")


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
