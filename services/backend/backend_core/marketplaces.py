import asyncio
import hashlib
import hmac
import io
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx
import yaml
from cryptography.fernet import Fernet, InvalidToken
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .catalog import _ensure_workspace, _write_manifest_atomic
from .models import (
    DEFAULT_WORKSPACE_ID,
    MarketplaceCredentialRecord,
    MarketplaceOAuthStateRecord,
    ProductRecord,
)


PROVIDER = "mercadolivre"
AUTHORIZATION_URL = "https://auth.mercadolivre.com.br/authorization"
API_URL = "https://api.mercadolibre.com"
OAUTH_STATE_TTL = timedelta(minutes=10)
_request_rate_lock = asyncio.Lock()
_next_marketplace_request = 0.0
register_heif_opener()


class MarketplaceConfigurationError(RuntimeError):
    pass


class MarketplaceRequestError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class MarketplaceOutcomeUnknown(RuntimeError):
    pass


class MarketplaceConnector(Protocol):
    provider: str

    def authorization_url(self, db: Session, workspace_id: str) -> str: ...

    async def authorize(
        self, db: Session, code: str, state: str
    ) -> MarketplaceCredentialRecord: ...

    async def publish(
        self, db: Session, product: ProductRecord, workspace_id: str
    ) -> dict[str, str]: ...


class MercadoLivreConnector:
    provider = PROVIDER

    def authorization_url(self, db: Session, workspace_id: str) -> str:
        return create_authorization_url(db, workspace_id)

    async def authorize(
        self, db: Session, code: str, state: str
    ) -> MarketplaceCredentialRecord:
        return await complete_authorization(db, code, state)

    async def publish(
        self, db: Session, product: ProductRecord, workspace_id: str
    ) -> dict[str, str]:
        return await publish_product(db, product, workspace_id)


CONNECTORS: dict[str, MarketplaceConnector] = {
    PROVIDER: MercadoLivreConnector(),
}


def get_marketplace_connector(provider: str) -> MarketplaceConnector:
    try:
        return CONNECTORS[provider]
    except KeyError as error:
        raise MarketplaceConfigurationError(
            f"Marketplace não suportado: {provider}."
        ) from error


def _read_setting(name: str) -> str:
    file_name = os.getenv(f"{name}_FILE")
    if file_name:
        try:
            value = Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise MarketplaceConfigurationError(
                f"Não foi possível ler o segredo configurado em {name}_FILE."
            ) from error
    else:
        value = os.getenv(name, "").strip()
    if not value:
        raise MarketplaceConfigurationError(f"A configuração {name} não está definida.")
    return value


def _fernet() -> Fernet:
    try:
        return Fernet(_read_setting("MARKETPLACE_TOKEN_ENCRYPTION_KEY").encode())
    except (ValueError, TypeError) as error:
        raise MarketplaceConfigurationError(
            "MARKETPLACE_TOKEN_ENCRYPTION_KEY deve ser uma chave Fernet válida."
        ) from error


def _encrypt_token(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _decrypt_token(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise MarketplaceConfigurationError(
            "Não foi possível descriptografar as credenciais. Verifique a chave secreta."
        ) from error


def _oauth_config() -> tuple[str, str, str]:
    return (
        _read_setting("MELI_CLIENT_ID"),
        _read_setting("MELI_CLIENT_SECRET"),
        _read_setting("MELI_REDIRECT_URI"),
    )


async def _rate_limited_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs
) -> httpx.Response:
    global _next_marketplace_request
    try:
        interval = float(os.getenv("MELI_MIN_REQUEST_INTERVAL_SECONDS", "1"))
    except ValueError as error:
        raise MarketplaceConfigurationError(
            "MELI_MIN_REQUEST_INTERVAL_SECONDS precisa ser um número positivo."
        ) from error
    if interval <= 0:
        raise MarketplaceConfigurationError(
            "MELI_MIN_REQUEST_INTERVAL_SECONDS precisa ser um número positivo."
        )
    async with _request_rate_lock:
        delay = _next_marketplace_request - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        _next_marketplace_request = time.monotonic() + interval
    return await client.request(method, url, **kwargs)


def _marketplace_image_upload(path: Path) -> tuple[str, bytes, str]:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        preserve_alpha = "A" in image.getbands() or "transparency" in image.info
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        if preserve_alpha:
            image.convert("RGBA").save(output, format="PNG", optimize=True)
            return f"{path.stem}.png", output.getvalue(), "image/png"
        image.convert("RGB").save(output, format="JPEG", quality=90, optimize=True)
        return f"{path.stem}.jpg", output.getvalue(), "image/jpeg"


def verify_manual_action_token(candidate: str | None) -> bool:
    try:
        expected = _read_setting("MARKETPLACE_ACTION_TOKEN")
    except MarketplaceConfigurationError:
        return False
    return bool(candidate) and hmac.compare_digest(candidate, expected)


def create_authorization_url(
    db: Session, workspace_id: str = DEFAULT_WORKSPACE_ID
) -> str:
    client_id, _, redirect_uri = _oauth_config()
    _ensure_workspace(db, workspace_id)
    state = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    db.execute(
        delete(MarketplaceOAuthStateRecord).where(
            MarketplaceOAuthStateRecord.expires_at < now
        )
    )
    db.add(
        MarketplaceOAuthStateRecord(
            state=hashlib.sha256(state.encode()).hexdigest(),
            workspace_id=workspace_id,
            expires_at=now + OAUTH_STATE_TTL,
        )
    )
    db.commit()
    return f"{AUTHORIZATION_URL}?{urlencode({'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect_uri, 'state': state})}"


async def complete_authorization(
    db: Session, code: str, state: str
) -> MarketplaceCredentialRecord:
    client_id, client_secret, redirect_uri = _oauth_config()
    hashed_state = hashlib.sha256(state.encode()).hexdigest()
    state_record = db.scalar(
        select(MarketplaceOAuthStateRecord).where(
            MarketplaceOAuthStateRecord.state == hashed_state
        ).with_for_update()
    )
    if state_record is None:
        raise MarketplaceRequestError("Estado OAuth inválido ou já utilizado.")
    expires_at = state_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        db.delete(state_record)
        db.commit()
        raise MarketplaceRequestError("A autorização expirou. Inicie o vínculo novamente.")

    workspace_id = state_record.workspace_id
    db.delete(state_record)
    db.commit()

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await _rate_limited_request(
                client,
                "POST",
                f"{API_URL}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
    except httpx.RequestError as error:
        raise MarketplaceRequestError(
            "Não foi possível conectar ao serviço OAuth do Mercado Livre."
        ) from error
    if response.is_error:
        raise MarketplaceRequestError(
            f"O Mercado Livre recusou a autorização (HTTP {response.status_code}).",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    try:
        token_data = response.json()
    except ValueError as error:
        raise MarketplaceRequestError(
            "O serviço OAuth do Mercado Livre retornou uma resposta inválida."
        ) from error
    return _store_tokens(db, workspace_id, token_data)


def _store_tokens(
    db: Session, workspace_id: str, token_data: dict
) -> MarketplaceCredentialRecord:
    try:
        account_id = str(token_data["user_id"])
        access_token = str(token_data["access_token"])
        refresh_token = str(token_data["refresh_token"])
        expires_in = int(token_data["expires_in"])
    except (KeyError, TypeError, ValueError) as error:
        raise MarketplaceRequestError(
            "A resposta OAuth do Mercado Livre não contém os campos esperados."
        ) from error
    record = db.scalar(
        select(MarketplaceCredentialRecord).where(
            MarketplaceCredentialRecord.workspace_id == workspace_id,
            MarketplaceCredentialRecord.provider == PROVIDER,
        )
    )
    if record is None:
        record = MarketplaceCredentialRecord(
            workspace_id=workspace_id,
            provider=PROVIDER,
            account_id=account_id,
            access_token_encrypted="",
            refresh_token_encrypted="",
            expires_at=datetime.now(timezone.utc),
        )
        db.add(record)
    record.account_id = account_id
    record.access_token_encrypted = _encrypt_token(access_token)
    record.refresh_token_encrypted = _encrypt_token(refresh_token)
    record.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    db.commit()
    db.refresh(record)
    return record


async def _access_token(db: Session, workspace_id: str) -> str:
    record = db.scalar(
        select(MarketplaceCredentialRecord).where(
            MarketplaceCredentialRecord.workspace_id == workspace_id,
            MarketplaceCredentialRecord.provider == PROVIDER,
        )
    )
    if record is None:
        raise MarketplaceConfigurationError("A conta do Mercado Livre não está vinculada.")
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
        return _decrypt_token(record.access_token_encrypted)

    client_id, client_secret, _ = _oauth_config()
    refresh_token = _decrypt_token(record.refresh_token_encrypted)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await _rate_limited_request(
                client,
                "POST",
                f"{API_URL}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
    except httpx.RequestError as error:
        raise MarketplaceRequestError(
            "Não foi possível conectar para renovar o token do Mercado Livre.",
            retryable=True,
        ) from error
    if response.is_error:
        raise MarketplaceRequestError(
            f"Falha ao renovar o token do Mercado Livre (HTTP {response.status_code}).",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    try:
        refreshed = response.json()
    except ValueError as error:
        raise MarketplaceRequestError(
            "O Mercado Livre retornou uma resposta inválida ao renovar o token."
        ) from error
    _store_tokens(db, workspace_id, refreshed)
    db.refresh(record)
    return _decrypt_token(record.access_token_encrypted)


async def publish_product(
    db: Session, product: ProductRecord, workspace_id: str
) -> dict[str, str]:
    if product.status in {"sold", "archived"}:
        raise MarketplaceConfigurationError(
            "Produtos vendidos ou arquivados não podem ser publicados."
        )
    config = (product.marketplaces or {}).get(PROVIDER, {})
    if not isinstance(config, dict) or not config.get("enabled"):
        raise MarketplaceConfigurationError(
            "Habilite Mercado Livre nos canais do produto antes de publicar."
        )
    if product.price is None:
        raise MarketplaceConfigurationError("Defina o preço do produto antes de publicar.")
    if product.price_floor is None:
        raise MarketplaceConfigurationError(
            "Defina o piso mínimo de preço antes de publicar."
        )
    if product.price < product.price_floor:
        raise MarketplaceConfigurationError(
            "O preço atual está abaixo do piso mínimo definido para este produto."
        )
    category_id = str(config.get("category_id") or os.getenv("MELI_CATEGORY_ID", ""))
    listing_type_id = os.getenv("MELI_LISTING_TYPE_ID", "").strip()
    site_id = os.getenv("MELI_SITE_ID", "MLB").strip()
    currency_id = os.getenv("MELI_CURRENCY_ID", "BRL").strip()
    try:
        quantity = int(os.getenv("MELI_AVAILABLE_QUANTITY", "1"))
    except ValueError as error:
        raise MarketplaceConfigurationError(
            "MELI_AVAILABLE_QUANTITY precisa ser um inteiro positivo."
        ) from error
    if not category_id or not listing_type_id or quantity < 1:
        raise MarketplaceConfigurationError(
            "Configure a categoria do produto e MELI_LISTING_TYPE_ID; "
            "MELI_AVAILABLE_QUANTITY deve ser positivo."
        )

    token = await _access_token(db, workspace_id)
    product_path = Path(product.path).resolve()
    pictures: list[dict[str, str]] = []
    image_extensions = {"bmp", "gif", "heic", "jpeg", "jpg", "png", "tif", "tiff", "webp"}
    for item in product.files:
        relative = Path(str(item.get("relative_path", "")))
        if any(part.startswith("_") for part in relative.parts):
            continue
        if relative.suffix.lower().lstrip(".") not in image_extensions:
            continue
        full_path = Path(str(item.get("full_path", ""))).resolve()
        try:
            full_path.relative_to(product_path)
        except ValueError:
            continue
        if not full_path.is_file():
            continue
        upload_name, upload_content, content_type = _marketplace_image_upload(full_path)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await _rate_limited_request(
                client,
                "POST",
                f"{API_URL}/pictures/items/upload",
                headers={"Authorization": f"Bearer {token}"},
                files={"file": (upload_name, upload_content, content_type)},
            )
        if response.is_error:
            raise MarketplaceRequestError(
                f"Falha ao enviar imagem ao Mercado Livre (HTTP {response.status_code}).",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        picture_result = response.json()
        if not isinstance(picture_result, dict):
            raise MarketplaceRequestError(
                "O Mercado Livre retornou uma resposta inválida ao receber a imagem."
            )
        picture_id = picture_result.get("id")
        if picture_id:
            pictures.append({"id": str(picture_id)})
    if not pictures:
        raise MarketplaceConfigurationError(
            "Adicione ao menos uma imagem válida fora das pastas ignoradas."
        )

    condition = "new" if product.condition == "new" else "used"
    payload = {
        "site_id": site_id,
        "title": product.title,
        "category_id": category_id,
        "price": float(product.price),
        "currency_id": currency_id,
        "available_quantity": quantity,
        "buying_mode": "buy_it_now",
        "listing_type_id": listing_type_id,
        "condition": condition,
        "pictures": pictures,
    }
    update_payload = {
        key: payload[key]
        for key in ("title", "price", "available_quantity", "pictures")
    }
    headers = {"Authorization": f"Bearer {token}"}
    existing_item_id = str(config.get("item_id") or "")
    manifest_path = product_path / "product.yaml"
    if not manifest_path.is_file():
        manifest_path = product_path / "product.yml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise MarketplaceConfigurationError(
            "Não foi possível ler o manifesto do produto."
        ) from error
    if not isinstance(manifest, dict):
        raise MarketplaceConfigurationError("O manifesto do produto não é um objeto YAML.")
    manifest_marketplaces = manifest.setdefault("marketplaces", {})
    if not isinstance(manifest_marketplaces, dict):
        raise MarketplaceConfigurationError("O campo marketplaces do manifesto é inválido.")
    current_config = manifest_marketplaces.setdefault(PROVIDER, {})
    if not isinstance(current_config, dict):
        raise MarketplaceConfigurationError("A configuração Mercado Livre do manifesto é inválida.")

    async with httpx.AsyncClient(timeout=30) as client:
        if existing_item_id:
            response = await _rate_limited_request(
                client,
                "PUT",
                f"{API_URL}/items/{existing_item_id}",
                json=update_payload,
                headers=headers,
            )
        else:
            try:
                response = await _rate_limited_request(
                    client, "POST", f"{API_URL}/items", json=payload, headers=headers
                )
            except httpx.RequestError as error:
                raise MarketplaceOutcomeUnknown(
                    "A conexão foi interrompida durante a criação do anúncio; "
                    "confirme no Mercado Livre antes de tentar novamente."
                ) from error
    if response.is_error:
        if not existing_item_id and response.status_code >= 500:
            raise MarketplaceOutcomeUnknown(
                "O Mercado Livre retornou um erro após receber a solicitação de criação; "
                "confirme no Mercado Livre antes de tentar novamente."
            )
        raise MarketplaceRequestError(
            f"O Mercado Livre recusou o anúncio (HTTP {response.status_code}).",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    try:
        item_data = response.json()
    except ValueError as error:
        if not existing_item_id:
            raise MarketplaceOutcomeUnknown(
                "A resposta de criação do anúncio era inválida; "
                "confirme no Mercado Livre antes de tentar novamente."
            ) from error
        raise MarketplaceRequestError(
            "O Mercado Livre retornou uma resposta inválida ao processar o anúncio."
        ) from error
    if not isinstance(item_data, dict):
        if not existing_item_id:
            raise MarketplaceOutcomeUnknown(
                "A resposta de criação não identificou o anúncio; "
                "confirme no Mercado Livre antes de tentar novamente."
            )
        raise MarketplaceRequestError(
            "O Mercado Livre retornou uma resposta inválida ao processar o anúncio."
        )
    item_id = str(item_data.get("id") or existing_item_id)
    if not item_id:
        if not existing_item_id:
            raise MarketplaceOutcomeUnknown(
                "A resposta de criação não incluiu o ID do anúncio; "
                "confirme no Mercado Livre antes de tentar novamente."
            )
        raise MarketplaceRequestError("A resposta do Mercado Livre não incluiu o ID do anúncio.")

    current_config["item_id"] = item_id
    current_config["enabled"] = True
    permalink = str(item_data.get("permalink", config.get("permalink", "")))
    if permalink:
        current_config["permalink"] = permalink
    manifest["status"] = "published"
    try:
        _write_manifest_atomic(manifest_path, manifest)
        product.marketplaces = manifest_marketplaces
        product.manifest = manifest
        product.status = "published"
        db.commit()
    except (OSError, SQLAlchemyError) as error:
        if not existing_item_id:
            raise MarketplaceOutcomeUnknown(
                "O anúncio pode ter sido criado, mas o estado local não foi salvo; "
                "confirme no Mercado Livre antes de tentar novamente."
            ) from error
        raise

    if product.description:
        async with httpx.AsyncClient(timeout=20) as client:
            description_response = await _rate_limited_request(
                client,
                "PUT" if existing_item_id else "POST",
                f"{API_URL}/items/{item_id}/description",
                json={"plain_text": product.description},
                headers=headers,
            )
        if description_response.is_error:
            raise MarketplaceRequestError(
                "O anúncio foi criado, mas a descrição não pôde ser sincronizada "
                f"(HTTP {description_response.status_code}); repita a publicação para atualizar.",
                retryable=description_response.status_code == 429
                or description_response.status_code >= 500,
            )
    return {"item_id": item_id, "permalink": permalink}
