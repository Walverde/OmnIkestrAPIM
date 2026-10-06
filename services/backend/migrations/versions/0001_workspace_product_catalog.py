from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "0001_workspace_product_catalog"
down_revision = None
branch_labels = None
depends_on = None


def _has_table(name):
    return name in inspect(op.get_bind()).get_table_names()


def _columns(table_name):
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def _add_column_if_missing(table_name, column):
    if column.name not in _columns(table_name):
        op.add_column(table_name, column)


def _ensure_workspace_table():
    if not _has_table("workspaces"):
        op.create_table(
            "workspaces",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    bind = op.get_bind()
    if bind.execute(text("SELECT 1 FROM workspaces WHERE id = 'default'")).first() is None:
        bind.execute(text("INSERT INTO workspaces (id, name) VALUES ('default', 'Default')"))


def _ensure_app_settings_table():
    if not _has_table("app_settings"):
        op.create_table(
            "app_settings",
            sa.Column("key", sa.String(128), primary_key=True),
            sa.Column("value", sa.String(2048), nullable=False),
        )


def _create_products_table():
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("product_uuid", sa.String(36), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("sku", sa.String(256)),
        sa.Column("category", sa.String(256)),
        sa.Column("description", sa.String(4096)),
        sa.Column("price", sa.Numeric(12, 2)),
        sa.Column("cost", sa.Numeric(12, 2)),
        sa.Column("condition", sa.String(32)),
        sa.Column("status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("marketplaces", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("manifest", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("manifest_error", sa.String(2048)),
        sa.Column("path", sa.String(2048), nullable=False),
        sa.Column("files", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("has_manifest", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "product_uuid", name="uq_products_workspace_product_uuid"),
        sa.UniqueConstraint("workspace_id", "path", name="uq_products_workspace_path"),
    )


def _upgrade_existing_products():
    _add_column_if_missing("products", sa.Column("workspace_id", sa.String(64), nullable=True))
    _add_column_if_missing("products", sa.Column("product_uuid", sa.String(36), nullable=True))
    _add_column_if_missing("products", sa.Column("title", sa.String(512), nullable=True))
    _add_column_if_missing("products", sa.Column("sku", sa.String(256)))
    _add_column_if_missing("products", sa.Column("category", sa.String(256)))
    _add_column_if_missing("products", sa.Column("description", sa.String(4096)))
    _add_column_if_missing("products", sa.Column("price", sa.Numeric(12, 2)))
    _add_column_if_missing("products", sa.Column("cost", sa.Numeric(12, 2)))
    _add_column_if_missing("products", sa.Column("condition", sa.String(32)))
    _add_column_if_missing("products", sa.Column("status", sa.String(32), server_default="pending"))
    _add_column_if_missing("products", sa.Column("marketplaces", sa.JSON(), server_default=sa.text("'[]'")))
    _add_column_if_missing("products", sa.Column("manifest", sa.JSON(), server_default=sa.text("'{}'")))
    _add_column_if_missing("products", sa.Column("manifest_error", sa.String(2048)))

    bind = op.get_bind()
    bind.execute(text("UPDATE products SET workspace_id = 'default' WHERE workspace_id IS NULL"))
    bind.execute(text("UPDATE products SET title = name WHERE title IS NULL"))
    rows = bind.execute(text("SELECT id FROM products WHERE product_uuid IS NULL")).all()
    for row in rows:
        bind.execute(
            text("UPDATE products SET product_uuid = :product_uuid WHERE id = :id"),
            {"product_uuid": str(uuid4()), "id": row.id},
        )

    op.alter_column("products", "workspace_id", existing_type=sa.String(64), nullable=False)
    op.alter_column("products", "product_uuid", existing_type=sa.String(36), nullable=False)
    op.alter_column("products", "title", existing_type=sa.String(512), nullable=False)
    op.alter_column("products", "status", existing_type=sa.String(32), nullable=False)
    op.alter_column("products", "marketplaces", existing_type=sa.JSON(), nullable=False)
    op.alter_column("products", "manifest", existing_type=sa.JSON(), nullable=False)
    op.create_foreign_key("fk_products_workspace_id", "products", "workspaces", ["workspace_id"], ["id"])


def _create_events_table():
    op.create_table(
        "product_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("path", sa.String(2048), nullable=False),
        sa.Column("destination_path", sa.String(2048)),
        sa.Column("is_directory", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("service", sa.String(64), server_default="watcher", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def _upgrade_existing_events():
    _add_column_if_missing("product_events", sa.Column("workspace_id", sa.String(64), nullable=True))
    _add_column_if_missing("product_events", sa.Column("destination_path", sa.String(2048)))
    bind = op.get_bind()
    bind.execute(text("UPDATE product_events SET workspace_id = 'default' WHERE workspace_id IS NULL"))
    op.alter_column("product_events", "workspace_id", existing_type=sa.String(64), nullable=False)
    op.create_foreign_key(
        "fk_product_events_workspace_id", "product_events", "workspaces", ["workspace_id"], ["id"]
    )


def _ensure_indexes_and_constraints():
    bind = op.get_bind()
    inspector = inspect(bind)
    unique_constraints = inspector.get_unique_constraints("products")
    for constraint in unique_constraints:
        if constraint.get("column_names") == ["path"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "products", type_="unique")

    inspector = inspect(bind)
    for index in inspector.get_indexes("products"):
        if (
            index.get("unique")
            and index.get("column_names") == ["path"]
            and index["name"] not in {item.get("name") for item in unique_constraints}
        ):
            op.drop_index(index["name"], table_name="products")

    inspector = inspect(bind)
    unique_constraints = inspector.get_unique_constraints("products")
    unique_names = {constraint.get("name") for constraint in unique_constraints}
    if "uq_products_workspace_product_uuid" not in unique_names:
        op.create_unique_constraint(
            "uq_products_workspace_product_uuid", "products", ["workspace_id", "product_uuid"]
        )
    if "uq_products_workspace_path" not in unique_names:
        op.create_unique_constraint(
            "uq_products_workspace_path", "products", ["workspace_id", "path"]
        )

    indexes = {index["name"] for index in inspect(bind).get_indexes("products")}
    for name, columns in (
        ("ix_products_workspace_id", ["workspace_id"]),
        ("ix_products_path", ["path"]),
        ("ix_products_sku", ["sku"]),
        ("ix_products_category", ["category"]),
        ("ix_products_status", ["status"]),
    ):
        if name not in indexes:
            op.create_index(name, "products", columns)

    event_indexes = {index["name"] for index in inspect(bind).get_indexes("product_events")}
    if "ix_product_events_workspace_id" not in event_indexes:
        op.create_index("ix_product_events_workspace_id", "product_events", ["workspace_id"])
        event_indexes.add("ix_product_events_workspace_id")
    if "ix_product_events_workspace_created_at" not in event_indexes:
        op.create_index(
            "ix_product_events_workspace_created_at",
            "product_events",
            ["workspace_id", "created_at"],
        )


def upgrade():
    _ensure_app_settings_table()
    _ensure_workspace_table()
    if _has_table("products"):
        _upgrade_existing_products()
    else:
        _create_products_table()
    if _has_table("product_events"):
        _upgrade_existing_events()
    else:
        _create_events_table()
    _ensure_indexes_and_constraints()


def downgrade():
    raise RuntimeError("This data-preserving baseline migration cannot be downgraded safely.")