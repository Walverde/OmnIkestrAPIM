import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import runtime
from ..catalog import _sync_product_catalog_for_roots
from ..models import (
    AppSettingRecord,
    CreateProductFolderRequest,
    DEFAULT_WORKSPACE_ID,
    ProductRecord,
    ProductRootConfig,
)
from .dependencies import get_db


router = APIRouter()
PRODUCT_ROOTS_SETTING = "product_roots"
SETUP_COMPLETE_SETTING = "setup_completed"


def _validated_root(raw_root: str | Path) -> Path:
    try:
        return runtime.resolve_allowed_product_root(raw_root)
    except (OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def restore_saved_product_roots(db: Session) -> None:
    saved_roots = db.get(AppSettingRecord, PRODUCT_ROOTS_SETTING)
    if saved_roots is None:
        return
    try:
        configured_roots = json.loads(saved_roots.value)
        if not isinstance(configured_roots, list) or not configured_roots:
            raise ValueError("A lista de pastas está vazia ou inválida.")
        roots = tuple(_validated_root(root) for root in configured_roots)
    except (HTTPException, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError(
            "Não foi possível restaurar as pastas de produtos salvas. "
            "Verifique se o volume está montado e acessível."
        ) from error
    runtime.apply_product_roots(tuple(dict.fromkeys(roots)))


@router.get("/api/config/product-roots")
def get_product_roots(db: Session = Depends(get_db)):
    saved_roots = db.get(AppSettingRecord, PRODUCT_ROOTS_SETTING)
    roots = runtime.PRODUCT_ROOTS
    if saved_roots:
        try:
            configured_roots = json.loads(saved_roots.value)
            if not isinstance(configured_roots, list) or not configured_roots:
                raise ValueError("A lista de pastas está vazia ou inválida.")
            if not all(isinstance(root, str) for root in configured_roots):
                raise ValueError("A lista de pastas contém valores inválidos.")
            roots = tuple(Path(root) for root in configured_roots)
        except (json.JSONDecodeError, TypeError, ValueError):
            raise HTTPException(
                status_code=500, detail="A configuração salva de pastas está inválida."
            )
    return {
        "roots": [root.as_posix() for root in roots],
        "default_root": roots[0].as_posix(),
        "source": "database"
        if saved_roots
        else "environment"
        if os.getenv("PRODUCT_ROOTS") or os.getenv("PRODUCTS_ROOT")
        else "runtime",
    }


@router.post("/api/config/product-roots")
def set_product_roots(config: ProductRootConfig, db: Session = Depends(get_db)):
    if not config.roots or any(not root.strip() for root in config.roots):
        raise HTTPException(
            status_code=400, detail="Informe pastas de produtos válidas."
        )

    updated_roots = tuple(dict.fromkeys(_validated_root(root) for root in config.roots))
    _sync_product_catalog_for_roots(db, updated_roots, DEFAULT_WORKSPACE_ID)

    roots_setting = db.get(AppSettingRecord, PRODUCT_ROOTS_SETTING)
    if roots_setting is None:
        roots_setting = AppSettingRecord(key=PRODUCT_ROOTS_SETTING, value="")
        db.add(roots_setting)
    roots_setting.value = json.dumps([root.as_posix() for root in updated_roots])

    complete_setting = db.get(AppSettingRecord, SETUP_COMPLETE_SETTING)
    if complete_setting is None:
        db.add(AppSettingRecord(key=SETUP_COMPLETE_SETTING, value="true"))
    else:
        complete_setting.value = "true"
    db.commit()
    runtime.apply_product_roots(updated_roots)
    return {
        "roots": [root.as_posix() for root in updated_roots],
        "default_root": updated_roots[0].as_posix(),
    }


@router.get("/api/setup/status")
def get_setup_status(db: Session = Depends(get_db)):
    completion = db.get(AppSettingRecord, SETUP_COMPLETE_SETTING)
    has_products = db.scalar(select(ProductRecord.id).limit(1)) is not None
    return {
        "completed": bool(completion and completion.value == "true") or has_products,
        "selected_root": runtime.PRODUCTS_ROOT.as_posix(),
        "available_roots": [
            root.as_posix()
            for root in runtime.ALLOWED_PRODUCT_ROOTS
            if root.is_dir()
        ],
    }


@router.get("/api/setup/browse")
def browse_product_directories(path: str | None = Query(default=None)):
    if path is None:
        roots = [
            _validated_root(root)
            for root in runtime.ALLOWED_PRODUCT_ROOTS
            if root.is_dir()
        ]
        return {
            "current": None,
            "parent": None,
            "directories": [
                {"name": root.name or root.as_posix(), "path": root.as_posix()}
                for root in roots
            ],
        }

    current = _validated_root(path)
    containing_roots = []
    for root in runtime.ALLOWED_PRODUCT_ROOTS:
        try:
            resolved_root = root.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if current == resolved_root or resolved_root in current.parents:
            containing_roots.append(resolved_root)
    if not containing_roots:
        raise HTTPException(
            status_code=400, detail="A pasta não está dentro de um volume permitido."
        )
    base = max(containing_roots, key=lambda root: len(root.parts))
    directories = []
    for child in sorted(current.iterdir(), key=lambda entry: entry.name.casefold()):
        if child.name.startswith((".", "_")) or not child.is_dir():
            continue
        try:
            safe_child = _validated_root(child)
        except HTTPException:
            continue
        directories.append({"name": child.name, "path": safe_child.as_posix()})

    return {
        "current": current.as_posix(),
        "parent": current.parent.as_posix() if current != base else None,
        "directories": directories,
    }


@router.post("/api/setup/folders", status_code=201)
def create_product_directory(request: CreateProductFolderRequest):
    name = request.name.strip()
    if (
        not name
        or name != request.name
        or name in {".", ".."}
        or name.startswith((".", "_"))
        or "/" in name
        or "\\" in name
    ):
        raise HTTPException(
            status_code=400, detail="Informe um nome simples para a pasta."
        )

    parent = _validated_root(request.parent)
    new_directory = parent / name
    try:
        new_directory.mkdir()
    except FileExistsError as error:
        raise HTTPException(status_code=409, detail="Essa pasta já existe.") from error
    return {"name": name, "path": new_directory.resolve().as_posix()}
