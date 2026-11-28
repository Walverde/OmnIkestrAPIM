import sqlalchemy as sa
from alembic import op


revision = "0008_marketplaces_server_default"
down_revision = "0007_auth_epoch_biginteger"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "products",
        "marketplaces",
        existing_type=sa.JSON(),
        server_default=sa.text("'{}'"),
    )


def downgrade():
    op.alter_column(
        "products",
        "marketplaces",
        existing_type=sa.JSON(),
        server_default=sa.text("'[]'"),
    )
