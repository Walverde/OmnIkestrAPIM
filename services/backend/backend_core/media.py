import hashlib
import io
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from .models import ProductRecord


register_heif_opener()

THUMBNAIL_CACHE_ROOT = Path(
    os.getenv("THUMBNAIL_CACHE_ROOT", "/tmp/omnikestr-thumbnails")
)
THUMBNAIL_SIZE = 1280


def thumbnail_cache_path(product: ProductRecord, source_path: Path) -> Path:
    stat = source_path.stat()
    fingerprint = hashlib.sha256(
        f"{product.product_uuid}:{source_path}:{stat.st_size}:{stat.st_mtime_ns}:{THUMBNAIL_SIZE}".encode()
    ).hexdigest()
    return THUMBNAIL_CACHE_ROOT / f"{fingerprint}.webp"


def create_webp_thumbnail(source_path: Path, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as source_image:
        image = ImageOps.exif_transpose(source_image)
        preserve_alpha = "A" in image.getbands() or "transparency" in image.info
        image.thumbnail(
            (THUMBNAIL_SIZE, THUMBNAIL_SIZE),
            Image.Resampling.LANCZOS,
        )
        image = image.convert("RGBA" if preserve_alpha else "RGB")
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=84, method=6)

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=cache_path.parent,
            prefix=".thumbnail-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(output.getvalue())
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, cache_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
