from pathlib import Path
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..catalog import _write_manifest_atomic
from ..marketplaces import (
    MarketplaceConfigurationError,
    MarketplaceRequestError,
    get_marketplace_connector,
    verify_manual_action_token,
)
from ..models import (
    DEFAULT_WORKSPACE_ID,
    DomainEventRecord,
    MarketplaceCredentialRecord,
    MarketplaceJobRecord,
    ProductRecord,
)
from .dependencies import get_db


router = APIRouter(prefix="/api/marketplaces", tags=["marketplaces"])


class MarketplaceJobReconciliation(BaseModel):
    item_id: str | None = Field(default=None, min_length=1, max_length=128)
    confirmed_not_published: bool = False

    @field_validator("item_id")
    @classmethod
    def strip_item_id(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def require_one_reconciliation_outcome(self):
        if bool(self.item_id) == self.confirmed_not_published:
            raise ValueError(
                "Informe o ID do anúncio encontrado ou confirme que não foi publicado."
            )
        return self


@router.get("/mercadolivre/authorize")
def authorize_mercadolivre(
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    try:
        connector = get_marketplace_connector("mercadolivre")
        authorization_url = connector.authorization_url(db, workspace_id)
    except MarketplaceConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"authorization_url": authorization_url}


@router.get("/mercadolivre/callback")
async def mercadolivre_callback(
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
    db: Session = Depends(get_db),
):
    try:
        connector = get_marketplace_connector("mercadolivre")
        credential = await connector.authorize(db, code, state)
    except MarketplaceConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except MarketplaceRequestError as error:
        response_status = 502 if error.retryable else 400
        raise HTTPException(status_code=response_status, detail=str(error)) from error
    db.add(
        DomainEventRecord(
            workspace_id=credential.workspace_id,
            event_type="marketplace.connected",
            aggregate_id=credential.account_id,
            payload={"provider": "mercadolivre", "account_id": credential.account_id},
        )
    )
    db.commit()
    return {
        "status": "connected",
        "provider": "mercadolivre",
        "account_id": credential.account_id,
    }


@router.get("/mercadolivre/status")
def mercadolivre_status(
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    credential = db.scalar(
        select(MarketplaceCredentialRecord).where(
            MarketplaceCredentialRecord.workspace_id == workspace_id,
            MarketplaceCredentialRecord.provider == "mercadolivre",
        )
    )
    return {
        "connected": credential is not None,
        "account_id": credential.account_id if credential else None,
        "updated_at": credential.updated_at.isoformat()
        if credential and credential.updated_at
        else None,
    }


@router.post(
    "/mercadolivre/products/{product_id}/publish",
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_mercadolivre_publish(
    product_id: UUID,
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    action_token: str | None = Header(default=None, alias="X-Marketplace-Action-Token"),
    db: Session = Depends(get_db),
):
    if not verify_manual_action_token(action_token):
        raise HTTPException(
            status_code=403,
            detail="Token de confirmação manual inválido ou não configurado.",
        )
    credential = db.scalar(
        select(MarketplaceCredentialRecord).where(
            MarketplaceCredentialRecord.workspace_id == workspace_id,
            MarketplaceCredentialRecord.provider == "mercadolivre",
        )
    )
    if credential is None:
        raise HTTPException(
            status_code=409, detail="Vincule uma conta do Mercado Livre antes de publicar."
        )
    product = db.scalar(
        select(ProductRecord).where(
            ProductRecord.workspace_id == workspace_id,
            ProductRecord.product_uuid == str(product_id),
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado.")
    if product.status in {"sold", "archived"}:
        raise HTTPException(
            status_code=409,
            detail="Produtos vendidos ou arquivados não podem ser publicados.",
        )
    active_jobs = db.scalars(
        select(MarketplaceJobRecord)
        .where(
            MarketplaceJobRecord.workspace_id == workspace_id,
            MarketplaceJobRecord.provider == "mercadolivre",
            MarketplaceJobRecord.status.in_(
                ["pending", "in_progress", "reconciliation_required"]
            ),
        )
    ).all()
    if any(
        isinstance(job.payload, dict)
        and job.payload.get("product_id") == str(product_id)
        for job in active_jobs
    ):
        raise HTTPException(
            status_code=409,
            detail="Existe uma publicação em andamento ou aguardando reconciliação.",
        )
    marketplace_config = (product.marketplaces or {}).get("mercadolivre", {})
    if not isinstance(marketplace_config, dict) or not marketplace_config.get("enabled"):
        raise HTTPException(
            status_code=409, detail="Habilite Mercado Livre nos canais do produto."
        )
    if product.price is None or product.price_floor is None:
        raise HTTPException(
            status_code=409,
            detail="Defina o preço e o piso mínimo antes de publicar.",
        )
    if product.price < product.price_floor:
        raise HTTPException(
            status_code=409,
            detail="O preço atual está abaixo do piso mínimo definido para este produto.",
        )
    job = MarketplaceJobRecord(
        workspace_id=workspace_id,
        provider="mercadolivre",
        job_type="publish_product",
        idempotency_key=f"mercadolivre:publish:{product_id}",
        payload={"product_id": str(product_id)},
    )
    try:
        db.add(job)
        db.flush()
        db.add(
            DomainEventRecord(
                workspace_id=workspace_id,
                event_type="marketplace.publish.requested",
                aggregate_id=str(product_id),
                payload={"provider": "mercadolivre", "job_id": job.id},
            )
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        constraint_name = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
        sqlite_unique_conflict = (
            "UNIQUE constraint failed: marketplace_jobs.workspace_id, "
            "marketplace_jobs.provider, marketplace_jobs.idempotency_key"
        ) in str(error.orig)
        if (
            constraint_name == "uq_marketplace_jobs_active_idempotency_key"
            or sqlite_unique_conflict
        ):
            raise HTTPException(
                status_code=409,
                detail="Já existe uma publicação deste produto ativa ou aguardando reconciliação.",
            ) from error
        raise
    return {"job_id": job.id, "status": job.status}


@router.get("/jobs/{job_id}")
def get_marketplace_job(
    job_id: int,
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(MarketplaceJobRecord).where(
            MarketplaceJobRecord.id == job_id,
            MarketplaceJobRecord.workspace_id == workspace_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return {
        "id": job.id,
        "provider": job.provider,
        "job_type": job.job_type,
        "status": job.status,
        "attempts": job.attempts,
        "result": job.result,
        "last_error": job.last_error,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.post("/jobs/{job_id}/reconcile")
def reconcile_marketplace_job(
    job_id: int,
    reconciliation: MarketplaceJobReconciliation,
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    job = db.scalar(
        select(MarketplaceJobRecord).where(
            MarketplaceJobRecord.id == job_id,
            MarketplaceJobRecord.workspace_id == workspace_id,
            MarketplaceJobRecord.status == "reconciliation_required",
        )
    )
    if job is None:
        raise HTTPException(
            status_code=409,
            detail="A tarefa não está aguardando reconciliação.",
        )
    product_id = job.payload.get("product_id") if isinstance(job.payload, dict) else None
    product = db.scalar(
        select(ProductRecord).where(
            ProductRecord.workspace_id == workspace_id,
            ProductRecord.product_uuid == product_id,
        )
    )
    if product is None:
        raise HTTPException(status_code=404, detail="Produto não encontrado.")

    if reconciliation.item_id:
        manifest_files = [
            item
            for item in product.files
            if str(item.get("name", "")).lower() in {"product.yaml", "product.yml"}
        ]
        manifest_path = (
            Path(str(manifest_files[0]["full_path"]))
            if manifest_files
            else Path(product.path) / "product.yaml"
        ).resolve()
        product_root = Path(product.path).resolve()
        try:
            manifest_path.relative_to(product_root)
        except ValueError as error:
            raise HTTPException(
                status_code=409, detail="Manifesto fora da pasta do produto."
            ) from error
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise HTTPException(
                status_code=409, detail="Não foi possível ler o manifesto do produto."
            ) from error
        if not isinstance(manifest, dict):
            raise HTTPException(
                status_code=409, detail="O manifesto precisa conter um objeto YAML."
            )
        marketplaces = manifest.setdefault("marketplaces", {})
        if not isinstance(marketplaces, dict):
            raise HTTPException(
                status_code=409, detail="A configuração de marketplaces é inválida."
            )
        market_config = marketplaces.setdefault(job.provider, {})
        if not isinstance(market_config, dict):
            raise HTTPException(
                status_code=409, detail="A configuração do marketplace é inválida."
            )
        market_config.update({"enabled": True, "item_id": reconciliation.item_id})
        if product.status not in {"sold", "archived"}:
            product.status = "published"
            manifest["status"] = "published"
        _write_manifest_atomic(manifest_path, manifest)
        product.marketplaces = marketplaces
        product.manifest = manifest
        job.status = "succeeded"
        job.result = {"item_id": reconciliation.item_id, "reconciled": True}
        job.last_error = None
        event_type = "marketplace.publish.reconciled"
        event_payload = {
            "provider": job.provider,
            "job_id": job.id,
            "item_id": reconciliation.item_id,
        }
    else:
        job.status = "failed"
        job.result = {"reconciled": True, "confirmed_not_published": True}
        event_type = "marketplace.publish.reconciled_not_published"
        event_payload = {"provider": job.provider, "job_id": job.id}

    db.add(
        DomainEventRecord(
            workspace_id=workspace_id,
            event_type=event_type,
            aggregate_id=product_id,
            payload=event_payload,
        )
    )
    db.commit()
    return {"job_id": job.id, "status": job.status, "result": job.result}


@router.get("/events")
def get_domain_events(
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    events = db.scalars(
        select(DomainEventRecord)
        .where(DomainEventRecord.workspace_id == workspace_id)
        .order_by(DomainEventRecord.created_at.desc(), DomainEventRecord.id.desc())
        .limit(limit)
    ).all()
    return {
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "aggregate_id": event.aggregate_id,
                "payload": event.payload,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ]
    }
