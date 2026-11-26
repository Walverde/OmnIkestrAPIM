from collections.abc import Iterator
import hmac

from fastapi import Header, HTTPException, Request, status

from sqlalchemy.orm import Session

from .auth import configured_api_key
from ..database import SessionLocal


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    if getattr(request.state, "user_authenticated", False):
        return
    try:
        expected = configured_api_key()
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A chave da API não pôde ser carregada.",
        ) from error
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A chave da API não está configurada.",
        )
    if not x_api_key or not hmac.compare_digest(
        x_api_key.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key inválida.",
        )
