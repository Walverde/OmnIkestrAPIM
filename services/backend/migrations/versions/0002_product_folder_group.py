import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "0002_product_folder_group"
down_revision = "0001_workspace_product_catalog"
branch_labels = None
depends_on = None


def _columns():
    return {column["name"] for column in inspect(op.get_bind()).get_columns("products")}


def upgrade():
    columns = _columns()
    if "folder_group" not in columns:
        op.add_column("products", sa.Column("folder_group", sa.String(1024), nullable=True))
    if "ambiguous_structure" not in columns:
        op.add_column("products", sa.Column("ambiguous_structure", sa.Boolean(), nullable=True))

    bind = op.get_bind()
    bind.execute(text("UPDATE products SET folder_group = '' WHERE folder_group IS NULL"))
    bind.execute(text("UPDATE products SET ambiguous_structure = FALSE WHERE ambiguous_structure IS NULL"))
    op.alter_column("products", "folder_group", existing_type=sa.String(1024), nullable=False)
    op.alter_column("products", "ambiguous_structure", existing_type=sa.Boolean(), nullable=False)

    indexes = {index["name"] for index in inspect(bind).get_indexes("products")}
    if "ix_products_folder_group" not in indexes:
        op.create_index("ix_products_folder_group", "products", ["folder_group"])


def downgrade():
    indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes("products")}
    if "ix_products_folder_group" in indexes:
        op.drop_index("ix_products_folder_group", table_name="products")
    columns = _columns()
    if "ambiguous_structure" in columns:
        op.drop_column("products", "ambiguous_structure")
    if "folder_group" in columns:
        op.drop_column("products", "folder_group")