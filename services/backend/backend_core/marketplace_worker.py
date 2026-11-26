import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import database
from .api.observability import QUEUE_JOBS
from .marketplaces import (
    MarketplaceConfigurationError,
    MarketplaceOutcomeUnknown,
    MarketplaceRequestError,
    get_marketplace_connector,
)
from .models import DomainEventRecord, MarketplaceJobRecord, ProductRecord


logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
POLL_SECONDS = 2
RECOVERY_INTERVAL_SECONDS = 60


def _recover_interrupted_jobs() -> None:
    stale_before = datetime.now(timezone.utc) - timedelta(minutes=10)
    try:
        with Session(database.engine) as db:
            jobs = db.scalars(
                select(MarketplaceJobRecord).where(
                    MarketplaceJobRecord.status == "in_progress",
                    MarketplaceJobRecord.updated_at < stale_before,
                )
            ).all()
            if not jobs:
                return
            for job in jobs:
                product_id = job.payload.get("product_id") if isinstance(job.payload, dict) else None
                product = db.scalar(
                    select(ProductRecord).where(
                        ProductRecord.workspace_id == job.workspace_id,
                        ProductRecord.product_uuid == product_id,
                    )
                )
                marketplace_config = (
                    (product.marketplaces or {}).get(job.provider, {})
                    if product is not None
                    else {}
                )
                if isinstance(marketplace_config, dict) and marketplace_config.get("item_id"):
                    job.status = "pending"
                    job.available_at = datetime.now(timezone.utc)
                else:
                    job.status = "reconciliation_required"
                    job.last_error = (
                        "A tarefa foi interrompida durante a criação. Confirme no marketplace "
                        "se o anúncio foi criado antes de tentar novamente."
                    )
            db.commit()
            logger.warning(
                "Recovered interrupted marketplace jobs",
                extra={"event": "marketplace.jobs.recovered", "attempt": len(jobs)},
            )
    except SQLAlchemyError:
        logger.exception(
            "Failed to recover interrupted marketplace jobs",
            extra={"event": "marketplace.jobs.recovery_failed"},
        )


def _claim_job() -> tuple[int, str, str, dict] | None:
    now = datetime.now(timezone.utc)
    try:
        with Session(database.engine) as db:
            job = db.scalar(
                select(MarketplaceJobRecord)
                .where(
                    MarketplaceJobRecord.status == "pending",
                    MarketplaceJobRecord.available_at <= now,
                )
                .order_by(MarketplaceJobRecord.created_at, MarketplaceJobRecord.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status = "in_progress"
            job.attempts += 1
            db.commit()
            return job.id, job.workspace_id, job.provider, job.payload
    except SQLAlchemyError:
        logger.exception("Failed to claim marketplace job", extra={"event": "marketplace.job.claim_failed"})
        return None


async def _run_job(job_id: int, workspace_id: str, provider: str, payload: dict) -> None:
    try:
        connector = get_marketplace_connector(provider)
        with Session(database.engine) as db:
            product = db.scalar(
                select(ProductRecord).where(
                    ProductRecord.workspace_id == workspace_id,
                    ProductRecord.product_uuid == payload["product_id"],
                )
            )
            if product is None:
                raise MarketplaceConfigurationError("O produto da tarefa não existe mais.")
            result = await connector.publish(db, product, workspace_id)
        with Session(database.engine) as db:
            job = db.get(MarketplaceJobRecord, job_id)
            if job is None:
                return
            job.status = "succeeded"
            job.result = result
            job.last_error = None
            db.add(
                DomainEventRecord(
                    workspace_id=workspace_id,
                    event_type="marketplace.publish.succeeded",
                    aggregate_id=payload["product_id"],
                    payload={"provider": provider, "job_id": job_id, **result},
                )
            )
            db.commit()
        QUEUE_JOBS.labels(provider=provider, status="succeeded").inc()
        logger.info(
            "Marketplace job completed",
            extra={"event": "marketplace.job.succeeded", "job_id": job_id, "provider": provider},
        )
    except MarketplaceOutcomeUnknown as error:
        _mark_reconciliation_required(
            job_id, workspace_id, provider, payload, error
        )
    except MarketplaceConfigurationError as error:
        _finish_job(job_id, workspace_id, provider, payload, error, retry=False)
    except MarketplaceRequestError as error:
        _finish_job(
            job_id, workspace_id, provider, payload, error, retry=error.retryable
        )
    except (KeyError, TypeError, ValueError, RuntimeError, yaml.YAMLError) as error:
        _finish_job(job_id, workspace_id, provider, payload, error, retry=False)
    except (httpx.HTTPError, OSError, SQLAlchemyError) as error:
        _finish_job(job_id, workspace_id, provider, payload, error, retry=True)


def _finish_job(
    job_id: int,
    workspace_id: str,
    provider: str,
    payload: dict,
    error: Exception,
    retry: bool,
) -> None:
    product_id = payload.get("product_id") if isinstance(payload, dict) else None
    with Session(database.engine) as db:
        job = db.get(MarketplaceJobRecord, job_id)
        if job is None:
            return
        attempts = job.attempts
        should_retry = retry and job.attempts < MAX_ATTEMPTS
        job.last_error = str(error)[:2048]
        job.status = "pending" if should_retry else "failed"
        if should_retry:
            job.available_at = datetime.now(timezone.utc) + timedelta(
                seconds=min(60, 2 ** job.attempts)
            )
        else:
            db.add(
                DomainEventRecord(
                    workspace_id=workspace_id,
                    event_type="marketplace.publish.failed",
                    aggregate_id=product_id,
                    payload={
                        "provider": provider,
                        "job_id": job_id,
                        "error": str(error)[:500],
                    },
                )
            )
        db.commit()
    QUEUE_JOBS.labels(provider=provider, status="retry" if should_retry else "failed").inc()
    logger.warning(
        "Marketplace job failed",
        extra={
            "event": "marketplace.job.retry" if should_retry else "marketplace.job.failed",
            "job_id": job_id,
            "provider": provider,
            "attempt": attempts,
            "error": str(error),
        },
    )


def _mark_reconciliation_required(
    job_id: int,
    workspace_id: str,
    provider: str,
    payload: dict,
    error: Exception,
) -> None:
    product_id = payload.get("product_id") if isinstance(payload, dict) else None
    with Session(database.engine) as db:
        job = db.get(MarketplaceJobRecord, job_id)
        if job is None:
            return
        job.status = "reconciliation_required"
        job.last_error = str(error)[:2048]
        db.add(
            DomainEventRecord(
                workspace_id=workspace_id,
                event_type="marketplace.publish.reconciliation_required",
                aggregate_id=product_id,
                payload={
                    "provider": provider,
                    "job_id": job_id,
                    "error": str(error)[:500],
                },
            )
        )
        db.commit()
    QUEUE_JOBS.labels(provider=provider, status="reconciliation_required").inc()
    logger.error(
        "Marketplace job requires manual reconciliation",
        extra={
            "event": "marketplace.job.reconciliation_required",
            "job_id": job_id,
            "provider": provider,
            "error": str(error),
        },
    )


async def _run_recovery() -> None:
    try:
        await asyncio.to_thread(_recover_interrupted_jobs)
    except Exception:
        logger.exception(
            "Unexpected error during marketplace job recovery",
            extra={"event": "marketplace.jobs.recovery_unexpected"},
        )


async def run_marketplace_worker(stop_event: asyncio.Event) -> None:
    await _run_recovery()
    last_recovery = time.monotonic()
    while not stop_event.is_set():
        try:
            if time.monotonic() - last_recovery >= RECOVERY_INTERVAL_SECONDS:
                await _run_recovery()
                last_recovery = time.monotonic()
            if stop_event.is_set():
                break

            job = await asyncio.to_thread(_claim_job)
            if job is None:
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
                except TimeoutError:
                    continue
                break
            try:
                await _run_job(*job)
            except Exception:
                logger.exception(
                    "Unexpected error while processing marketplace job",
                    extra={
                        "event": "marketplace.job.unexpected",
                        "job_id": job[0],
                        "provider": job[2],
                    },
                )
        except Exception:
            logger.exception(
                "Unexpected error in marketplace worker loop",
                extra={"event": "marketplace.worker.unexpected"},
            )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
            except TimeoutError:
                continue
            break
