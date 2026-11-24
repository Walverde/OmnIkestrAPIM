import hashlib
import hmac
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


router = APIRouter(tags=["authentication"])
SESSION_COOKIE_NAME = "omnikestr_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_PAGE = Path(__file__).resolve().parents[2] / "static" / "login.html"


def _configured_value(name: str) -> str:
    file_name = os.getenv(f"{name}_FILE")
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise RuntimeError(f"{name}_FILE could not be read.") from error
    return os.getenv(name, "").strip()


def configured_api_key() -> str:
    return _configured_value("OMNIKESTRAPIM_API_KEY")


def validate_security_configuration() -> None:
    try:
        api_key = configured_api_key()
        admin_password = _configured_value("OMNIKESTRAPIM_ADMIN_PASSWORD")
        session_secret = _configured_value("OMNIKESTRAPIM_SESSION_SECRET")
    except RuntimeError as error:
        raise RuntimeError("Unable to load required security configuration.") from error

    missing = [
        name
        for name, value in (
            ("OMNIKESTRAPIM_API_KEY", api_key),
            ("OMNIKESTRAPIM_ADMIN_PASSWORD", admin_password),
            ("OMNIKESTRAPIM_SESSION_SECRET", session_secret),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Required security configuration is missing: " + ", ".join(missing)
        )
    if len(admin_password) < 12:
        raise RuntimeError("OMNIKESTRAPIM_ADMIN_PASSWORD must be at least 12 characters.")
    if len(api_key) < 32:
        raise RuntimeError("OMNIKESTRAPIM_API_KEY must be at least 32 characters.")
    if len(session_secret) < 32:
        raise RuntimeError("OMNIKESTRAPIM_SESSION_SECRET must be at least 32 characters.")


def _session_signature(expiration: int) -> str:
    secret = _configured_value("OMNIKESTRAPIM_SESSION_SECRET")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticação não está configurada.",
        )
    payload = f"admin:{expiration}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def is_valid_session(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    try:
        raw_expiration, signature = token.split(".", maxsplit=1)
        expiration = int(raw_expiration)
    except (ValueError, TypeError):
        return False
    if expiration <= int(time.time()):
        return False
    try:
        expected_signature = _session_signature(expiration)
    except (HTTPException, RuntimeError):
        return False
    return hmac.compare_digest(signature, expected_signature)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@router.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(LOGIN_PAGE, media_type="text/html")


@router.get("/api/auth/session")
def get_session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": is_valid_session(request)}


@router.post("/api/auth/login")
def login(credentials: LoginRequest, response: Response):
    try:
        expected = _configured_value("OMNIKESTRAPIM_ADMIN_PASSWORD")
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A configuração de autenticação não pôde ser carregada.",
        ) from error
    if not expected or not hmac.compare_digest(credentials.password, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha inválida.",
        )
    expiration = int(time.time()) + SESSION_TTL_SECONDS
    try:
        signature = _session_signature(expiration)
    except (HTTPException, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A configuração de sessão não pôde ser carregada.",
        ) from error
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=f"{expiration}.{signature}",
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=os.getenv("OMNIKESTRAPIM_COOKIE_SECURE", "true").lower()
        in {"1", "true", "yes", "on"},
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": True}


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=os.getenv("OMNIKESTRAPIM_COOKIE_SECURE", "true").lower()
        in {"1", "true", "yes", "on"},
        httponly=True,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"authenticated": False}
