from pathlib import Path
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import runtime
from ..catalog import (
    _scan_product,
    _serialize_product,
    _sync_product_catalog_for_roots,
    _upsert_product_record,
    _write_manifest_atomic,
)
from ..models import (
    DEFAULT_WORKSPACE_ID,
    ProductManifest,
    ProductManifestPatch,
    ProductRecord,
)
from .dependencies import get_db


router = APIRouter()


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
        raise HTTPException(
            status_code=409, detail="Manifesto fora da pasta do produto."
        ) from error
    return manifest_path


def _validation_details(error: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
        }
        for item in error.errors()
    ]


@router.get("/api/products")
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
        "root": runtime.PRODUCTS_ROOT.as_posix(),
        "roots": [root.as_posix() for root in runtime.PRODUCT_ROOTS],
    }


@router.post("/api/products/scan")
def scan_products(
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    products = _sync_product_catalog_for_roots(
        db, runtime.PRODUCT_ROOTS, workspace_id
    )
    return {
        "products": [product.model_dump() for product in products],
        "roots": [root.as_posix() for root in runtime.PRODUCT_ROOTS],
    }


@router.get("/api/products/{product_id}")
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


@router.patch("/api/products/{product_id}")
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
        raise HTTPException(
            status_code=409, detail="O manifesto precisa conter um objeto YAML."
        )

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
        raise HTTPException(
            status_code=500, detail="Não foi possível gravar o manifesto."
        ) from error

    product_root = next(
        (
            root
            for root in runtime.PRODUCT_ROOTS
            if Path(product.path).resolve().is_relative_to(root.resolve())
        ),
        runtime.PRODUCTS_ROOT,
    )
    scanned_product = _scan_product(
        product_root.resolve(), Path(product.path), preferred_id=str(product.product_uuid)
    )
    _upsert_product_record(db, scanned_product, workspace_id)
    db.commit()
    db.refresh(product)
    return _serialize_product(product)
