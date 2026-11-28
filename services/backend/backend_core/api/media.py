from pathlib import Path
from uuid import UUID
from PIL import Image

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import runtime
from ..catalog import IMAGE_EXTENSIONS, _is_ignored_product_path
from ..media import create_webp_thumbnail, thumbnail_cache_path
from ..models import DEFAULT_WORKSPACE_ID, ProductRecord
from .dependencies import get_db


router = APIRouter()
MAX_IMAGE_PIXELS = 100_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


@router.get("/api/products/{product_id}/media/{relative_path:path}")
def get_product_media(
    product_id: UUID,
    relative_path: str,
    thumbnail: bool = Query(True),
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
    relative_media_path = Path(relative_path)
    if _is_ignored_product_path(relative_media_path):
        raise HTTPException(status_code=404, detail="Imagem não encontrada.")
    source_path = (product_base / relative_media_path).resolve()
    try:
        product_relative_path = source_path.relative_to(product_root)
    except ValueError as error:
        raise HTTPException(
            status_code=404, detail="Arquivo não encontrado para este produto."
        ) from error
    if _is_ignored_product_path(product_relative_path):
        raise HTTPException(status_code=404, detail="Imagem não encontrada.")
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
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            if isinstance(error, Image.DecompressionBombError):
                raise HTTPException(
                    status_code=413, detail="Imagem excede o limite permitido."
                ) from error
            raise HTTPException(
                status_code=415, detail="Não foi possível processar esta imagem."
            ) from error
    return FileResponse(
        cache_path,
        media_type="image/webp",
        headers={"Cache-Control": "no-cache"},
    )
