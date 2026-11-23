from collections.abc import Iterator
import hmac
import os
from pathlib import Path

from fastapi import Header, HTTPException, Request, status
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from ..database import SessionLocal


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    file_name = os.getenv("OMNIKESTRAPIM_API_KEY_FILE")
    expected = ""
    if file_name:
        try:
            expected = Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise HTTPException(
                status_code=503,
                detail="A chave da API não pôde ser carregada.",
            ) from error
    else:
        expected = os.getenv("OMNIKESTRAPIM_API_KEY", "").strip()

    if not expected:
        return
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlparse(origin).netloc
        if origin_host and origin_host == request.url.netloc:
            return
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key inválida.",
        )
