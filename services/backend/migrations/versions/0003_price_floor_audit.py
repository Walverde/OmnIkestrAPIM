import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0003_price_floor_audit"
down_revision = "0002_product_folder_group"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    product_columns = {
        column["name"] for column in inspect(bind).get_columns("products")
    }
    if "price_floor" not in product_columns:
        op.add_column(
            "products",
            sa.Column("price_floor", sa.Numeric(12, 2), nullable=True),
        )

    tables = set(inspect(bind).get_table_names())
    if "price_audits" not in tables:
        op.create_table(
            "price_audits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=64),
                sa.ForeignKey("workspaces.id"),
                nullable=False,
            ),
            sa.Column("product_uuid", sa.String(length=36), nullable=False),
            sa.Column("old_price", sa.Numeric(12, 2), nullable=True),
            sa.Column("new_price", sa.Numeric(12, 2), nullable=True),
            sa.Column("price_floor", sa.Numeric(12, 2), nullable=True),
            sa.Column(
                "source", sa.String(length=32), nullable=False, server_default="manual"
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_price_audits_workspace_created_at",
            "price_audits",
            ["workspace_id", "created_at"],
        )
        op.create_index(
            "ix_price_audits_workspace_id",
            "price_audits",
            ["workspace_id"],
        )
        op.create_index(
            "ix_price_audits_product_uuid",
            "price_audits",
            ["product_uuid"],
        )


def downgrade():
    tables = set(inspect(op.get_bind()).get_table_names())
    if "price_audits" in tables:
        op.drop_table("price_audits")
    product_columns = {
        column["name"] for column in inspect(op.get_bind()).get_columns("products")
    }
    if "price_floor" in product_columns:
        op.drop_column("products", "price_floor")
