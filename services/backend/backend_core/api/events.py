from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import runtime
from ..catalog import (
    _ensure_workspace,
    _root_for_path,
    _sync_product_for_event,
)
from ..models import (
    DEFAULT_WORKSPACE_ID,
    ProductEvent,
    ProductEventRecord,
)
from .dependencies import get_db


router = APIRouter()


def _prune_product_events(db: Session, workspace_id: str) -> None:
    retention_cutoff = datetime.now(timezone.utc) - timedelta(
        days=runtime.PRODUCT_EVENT_RETENTION_DAYS
    )
    db.execute(
        delete(ProductEventRecord).where(
            ProductEventRecord.workspace_id == workspace_id,
            ProductEventRecord.created_at < retention_cutoff,
        )
    )
    overflow_ids = (
        select(ProductEventRecord.id)
        .where(ProductEventRecord.workspace_id == workspace_id)
        .order_by(ProductEventRecord.created_at.desc(), ProductEventRecord.id.desc())
        .offset(runtime.MAX_PRODUCT_EVENTS)
        .subquery()
    )
    db.execute(
        delete(ProductEventRecord).where(
            ProductEventRecord.id.in_(select(overflow_ids.c.id))
        )
    )
    db.commit()


@router.get("/api/products/events")
def get_product_events(
    workspace_id: str = Query(DEFAULT_WORKSPACE_ID, min_length=1, max_length=64),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    events = list(
        db.scalars(
            select(ProductEventRecord)
            .where(ProductEventRecord.workspace_id == workspace_id)
            .order_by(ProductEventRecord.created_at.desc(), ProductEventRecord.id.desc())
            .limit(limit)
        ).all()
    )
    events.reverse()
    return {
        "events": [
            {
                "id": event.id,
                "workspace_id": event.workspace_id,
                "event": event.event,
                "path": event.path,
                "destination_path": event.destination_path,
                "is_directory": event.is_directory,
                "service": event.service,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in events
        ]
    }


@router.post("/api/products/watch")
def receive_product_event(event: ProductEvent, db: Session = Depends(get_db)):
    _ensure_workspace(db, event.workspace_id)
    record = ProductEventRecord(**event.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    root = (
        _root_for_path(event.path, runtime.PRODUCT_ROOTS)
        or _root_for_path(event.destination_path, runtime.PRODUCT_ROOTS)
        or runtime.PRODUCTS_ROOT
    )
    indexed_products = _sync_product_for_event(db, root, event)
    _prune_product_events(db, event.workspace_id)
    return {
        "status": "accepted",
        "event": event.model_dump(),
        "indexed_products": len(indexed_products),
        "id": record.id,
    }
