from datetime import datetime
from decimal import Decimal
from typing import Any, List, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
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


class ProductManifest(BaseModel):
    model_config = {"extra": "allow"}

    schema_version: Literal[1] = 1
    id: UUID = Field(default_factory=uuid4)
    sku: str | None = None
    title: str = Field(min_length=1)
    category: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    condition: Literal["new", "used", "refurbished"] | None = None
    status: Literal["pending", "published", "sold", "archived"] = "pending"
    marketplaces: list[str] = Field(default_factory=list)
    description: str | None = None


class ProductManifestPatch(BaseModel):
    model_config = {"extra": "forbid"}

    sku: str | None = None
    title: str | None = Field(default=None, min_length=1)
    category: str | None = None
    price: Decimal | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    condition: Literal["new", "used", "refurbished"] | None = None
    status: Literal["pending", "published", "sold", "archived"] | None = None
    marketplaces: list[str] | None = None
    description: str | None = None


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
    condition: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    marketplaces: Mapped[list[str]] = mapped_column(JSON, default=list)
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
    condition: str | None = None
    status: str | None = None
    marketplaces: List[str] = Field(default_factory=list)
    images: List[str] = Field(default_factory=list)
    primary_image: str | None = None
    category_suggestion: str | None = None
    category_mismatch: bool = False
    ambiguous_structure: bool = False
    manifest: dict[str, Any] | None = None
    manifest_error: str | None = None
