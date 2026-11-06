from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import runtime
from ..catalog import IMAGE_EXTENSIONS
from ..media import create_webp_thumbnail, thumbnail_cache_path
from ..models import DEFAULT_WORKSPACE_ID, ProductRecord
from .dependencies import get_db


router = APIRouter()


@router.get("/api/products/{product_id}/media/{relative_path:path}")
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
    product_base = next(
        (
            root
            for root in runtime.PRODUCT_ROOTS
            if product_root.is_relative_to(root.resolve())
        ),
        None,
    )
    if product_base is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado para este produto.")
    source_path = (product_base / relative_path).resolve()
    try:
        source_path.relative_to(product_root)
    except ValueError as error:
        raise HTTPException(
            status_code=404, detail="Arquivo não encontrado para este produto."
        ) from error
    if (
        not source_path.is_file()
        or source_path.suffix.lower().lstrip(".") not in IMAGE_EXTENSIONS
    ):
        raise HTTPException(status_code=404, detail="Imagem não encontrada.")

    extension = source_path.suffix.lower()
    if not thumbnail and extension not in {".heic", ".heif"}:
        return FileResponse(source_path, headers={"Cache-Control": "no-cache"})

    cache_path = thumbnail_cache_path(product, source_path)
    if not cache_path.exists():
        try:
            create_webp_thumbnail(source_path, cache_path)
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=415, detail="Não foi possível processar esta imagem."
            ) from error
    return FileResponse(
        cache_path,
        media_type="image/webp",
        headers={"Cache-Control": "no-cache"},
    )
