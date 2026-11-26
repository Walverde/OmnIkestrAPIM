from datetime import datetime
from decimal import Decimal
from typing import Any, List, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import (
    Boolean,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


DEFAULT_WORKSPACE_ID = "default"


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AppSettingRecord(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String(2048))


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)


class LoginAttemptRecord(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_client_attempted", "client_key", "attempted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_key: Mapped[str] = mapped_column(String(64))
    attempted_at: Mapped[int] = mapped_column(BigInteger)


class ProductEventRecord(Base):
    __tablename__ = "product_events"
    __table_args__ = (
        Index("ix_product_events_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, index=True
    )
    event: Mapped[str] = mapped_column(String(32))
    path: Mapped[str] = mapped_column(String(2048))
    destination_path: Mapped[str | None] = mapped_column(String(2048))
    is_directory: Mapped[bool] = mapped_column(Boolean, default=False)
    service: Mapped[str] = mapped_column(String(64), default="watcher")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PriceAuditRecord(Base):
    __tablename__ = "price_audits"
    __table_args__ = (
        Index("ix_price_audits_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, index=True
    )
    product_uuid: Mapped[str] = mapped_column(String(36), index=True)
    old_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    new_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_floor: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MarketplaceCredentialRecord(Base):
    __tablename__ = "marketplace_credentials"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "provider", name="uq_marketplace_credentials_workspace_provider"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(String(128))
    access_token_encrypted: Mapped[str] = mapped_column(String(4096))
    refresh_token_encrypted: Mapped[str] = mapped_column(String(4096))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MarketplaceOAuthStateRecord(Base):
    __tablename__ = "marketplace_oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MarketplaceJobRecord(Base):
    __tablename__ = "marketplace_jobs"
    __table_args__ = (
        Index("ix_marketplace_jobs_status_available", "status", "available_at"),
        Index(
            "uq_marketplace_jobs_active_idempotency_key",
            "workspace_id",
            "provider",
            "idempotency_key",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'in_progress', 'reconciliation_required')"
            ),
            sqlite_where=text(
                "status IN ('pending', 'in_progress', 'reconciliation_required')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    job_type: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_error: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DomainEventRecord(Base):
    __tablename__ = "domain_events"
    __table_args__ = (
        Index("ix_domain_events_workspace_created_at", "workspace_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    aggregate_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MarketplaceConfig(BaseModel):
    model_config = {"extra": "allow"}

    enabled: bool = False


class ProductManifest(BaseModel):
    model_config = {"extra": "allow"}

    schema_version: Literal[2] = 2
    id: UUID = Field(default_factory=uuid4)
    sku: str | None = None
    title: str = Field(min_length=1)
    category: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    price_floor: Decimal | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    condition: Literal["new", "used", "refurbished"] | None = None
    status: Literal["pending", "published", "sold", "archived"] = "pending"
    marketplaces: dict[str, MarketplaceConfig] = Field(default_factory=dict)
    description: str | None = None

    @model_validator(mode="after")
    def price_respects_floor(self):
        if (
            self.price is not None
            and self.price_floor is not None
            and self.price < self.price_floor
        ):
            raise ValueError(f"O preço não pode ser menor que o piso de {self.price_floor}.")
        return self


class ProductManifestPatch(BaseModel):
    model_config = {"extra": "forbid"}

    sku: str | None = None
    title: str | None = Field(default=None, min_length=1)
    category: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    price_floor: Decimal | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    condition: Literal["new", "used", "refurbished"] | None = None
    status: Literal["pending", "published", "sold", "archived"] | None = None
    marketplaces: dict[str, MarketplaceConfig] | None = None
    description: str | None = None

    @field_validator("marketplaces", mode="before")
    @classmethod
    def accept_legacy_marketplace_names(cls, value: Any) -> Any:
        if isinstance(value, list) and all(isinstance(name, str) for name in value):
            return {name: {"enabled": True} for name in value}
        return value


class ProductRecord(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "product_uuid", name="uq_products_workspace_product_uuid"
        ),
        UniqueConstraint("workspace_id", "path", name="uq_products_workspace_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id"), default=DEFAULT_WORKSPACE_ID, index=True
    )
    product_uuid: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512))
    folder_group: Mapped[str] = mapped_column(String(1024), default="", index=True)
    sku: Mapped[str | None] = mapped_column(String(256), index=True)
    category: Mapped[str | None] = mapped_column(String(256), index=True)
    description: Mapped[str | None] = mapped_column(String(4096))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    price_floor: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    condition: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    marketplaces: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    manifest_error: Mapped[str | None] = mapped_column(String(2048))
    ambiguous_structure: Mapped[bool] = mapped_column(Boolean, default=False)
    path: Mapped[str] = mapped_column(String(2048), index=True)
    files: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    has_manifest: Mapped[bool] = mapped_column(Boolean, default=False)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProductEvent(BaseModel):
    event: Literal["created", "modified", "deleted", "moved"]
    path: str
    destination_path: str | None = None
    is_directory: bool = False
    service: str = "watcher"
    workspace_id: str = Field(
        default=DEFAULT_WORKSPACE_ID, min_length=1, max_length=64
    )


class ProductFile(BaseModel):
    name: str
    full_path: str
    relative_path: str
    extension: str | None = None


class ProductRootConfig(BaseModel):
    roots: List[str] = Field(default_factory=list)


class CreateProductFolderRequest(BaseModel):
    parent: str = Field(min_length=1, max_length=2048)
    name: str = Field(min_length=1, max_length=128)


class ProductSummary(BaseModel):
    id: str | None = None
    name: str
    path: str
    folder_group: str = ""
    files: List[ProductFile]
    has_manifest: bool
    sku: str | None = None
    title: str | None = None
    category: str | None = None
    description: str | None = None
    price: Decimal | None = None
    cost: Decimal | None = None
    price_floor: Decimal | None = None
    condition: str | None = None
    status: str | None = None
    marketplaces: dict[str, MarketplaceConfig] = Field(default_factory=dict)
    images: List[str] = Field(default_factory=list)
    primary_image: str | None = None
    category_suggestion: str | None = None
    category_mismatch: bool = False
    ambiguous_structure: bool = False
    manifest: dict[str, Any] | None = None
    manifest_error: str | None = None
