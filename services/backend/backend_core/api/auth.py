import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ..database import SessionLocal
from ..models import AuthSessionRecord, LoginAttemptRecord

router = APIRouter(tags=["authentication"])
SESSION_COOKIE_NAME = "omnikestr_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5
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


def _session_signature(expiration: int, session_id: str) -> str:
    secret = _configured_value("OMNIKESTRAPIM_SESSION_SECRET")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticação não está configurada.",
        )
    payload = f"admin:{expiration}:{session_id}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _verified_session_id(request: Request, now: int) -> tuple[str, int] | None:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    try:
        raw_expiration, session_id, signature = token.split(".", maxsplit=2)
        expiration = int(raw_expiration)
    except (ValueError, TypeError):
        return None
    if expiration <= now:
        return None
    try:
        expected_signature = _session_signature(expiration, session_id)
    except (HTTPException, RuntimeError):
        return None
    if not hmac.compare_digest(
        signature.encode("utf-8"), expected_signature.encode("utf-8")
    ):
        return None
    return session_id, expiration


def is_valid_session(request: Request) -> bool:
    session = _verified_session_id(request, int(time.time()))
    if session is None:
        return False
    session_id, expiration = session
    with SessionLocal() as db:
        stored_session = db.get(AuthSessionRecord, session_id)
        return (
            stored_session is not None
            and stored_session.expires_at == expiration
            and stored_session.expires_at > int(time.time())
        )


def _login_client_key(request: Request) -> str:
    try:
        secret = _configured_value("OMNIKESTRAPIM_SESSION_SECRET")
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A configuração de autenticação não pôde ser carregada.",
        ) from error
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Autenticação não está configurada.",
        )
    client_host = request.client.host if request.client else "unknown"
    return hmac.new(
        secret.encode("utf-8"), client_host.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _check_login_attempts(client_key: str, password_is_valid: bool) -> None:
    now = int(time.time())
    cutoff = now - LOGIN_ATTEMPT_WINDOW_SECONDS
    with SessionLocal.begin() as db:
        db.execute(
            delete(LoginAttemptRecord).where(
                LoginAttemptRecord.attempted_at <= cutoff
            )
        )
        attempts = db.scalars(
            select(LoginAttemptRecord.attempted_at)
            .where(
                LoginAttemptRecord.client_key == client_key,
                LoginAttemptRecord.attempted_at > cutoff,
            )
            .order_by(LoginAttemptRecord.attempted_at)
        ).all()
        if len(attempts) >= LOGIN_MAX_ATTEMPTS:
            retry_after = max(
                1, attempts[0] + LOGIN_ATTEMPT_WINDOW_SECONDS - now
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Muitas tentativas de login. Tente novamente mais tarde.",
                headers={"Retry-After": str(retry_after)},
            )
        if password_is_valid:
            db.execute(
                delete(LoginAttemptRecord).where(
                    LoginAttemptRecord.client_key == client_key
                )
            )
        else:
            db.add(
                LoginAttemptRecord(client_key=client_key, attempted_at=now)
            )


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
def login(credentials: LoginRequest, request: Request, response: Response):
    try:
        expected = _configured_value("OMNIKESTRAPIM_ADMIN_PASSWORD")
    except RuntimeError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A configuração de autenticação não pôde ser carregada.",
        ) from error
    password_is_valid = bool(expected) and hmac.compare_digest(
        credentials.password.encode("utf-8"), expected.encode("utf-8")
    )
    client_key = _login_client_key(request)
    _check_login_attempts(client_key, password_is_valid)
    if not password_is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha inválida.",
        )
    expiration = int(time.time()) + SESSION_TTL_SECONDS
    session_id = secrets.token_urlsafe(32)
    try:
        signature = _session_signature(expiration, session_id)
    except (HTTPException, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A configuração de sessão não pôde ser carregada.",
        ) from error
    with SessionLocal.begin() as db:
        db.execute(
            delete(AuthSessionRecord).where(
                AuthSessionRecord.expires_at <= int(time.time())
            )
        )
        db.add(
            AuthSessionRecord(session_id=session_id, expires_at=expiration)
        )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=f"{expiration}.{session_id}.{signature}",
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
def logout(request: Request, response: Response):
    session = _verified_session_id(request, int(time.time()))
    if session is not None:
        session_id, _ = session
        with SessionLocal.begin() as db:
            db.execute(
                delete(AuthSessionRecord).where(
                    AuthSessionRecord.session_id == session_id
                )
            )
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
