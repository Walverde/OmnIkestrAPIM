import os

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..catalog import _sync_product_catalog_for_roots
from ..models import DEFAULT_WORKSPACE_ID, ProductRootConfig
from .. import runtime
from .dependencies import get_db


router = APIRouter()


@router.get("/api/config/product-roots")
def get_product_roots():
    return {
        "roots": [root.as_posix() for root in runtime.PRODUCT_ROOTS],
        "default_root": runtime.PRODUCTS_ROOT.as_posix(),
        "source": "environment"
        if os.getenv("PRODUCT_ROOTS") or os.getenv("PRODUCTS_ROOT")
        else "runtime",
    }


@router.post("/api/config/product-roots")
def set_product_roots(config: ProductRootConfig, db: Session = Depends(get_db)):
    if not config.roots:
        raise HTTPException(
            status_code=400, detail="Informe pelo menos uma pasta de produtos."
        )

    updated_roots = runtime.apply_product_roots(config.roots)
    _sync_product_catalog_for_roots(db, updated_roots, DEFAULT_WORKSPACE_ID)
    db.commit()
    return {
        "roots": [root.as_posix() for root in updated_roots],
        "default_root": runtime.PRODUCTS_ROOT.as_posix(),
    }
