import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0007_auth_epoch_biginteger"
down_revision = "0006_auth_session_security"
branch_labels = None
depends_on = None


def _column_type(table_name: str, column_name: str) -> str | None:
    for column in inspect(op.get_bind()).get_columns(table_name):
        if column["name"] == column_name:
            return str(column["type"])
    return None


def upgrade():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "auth_sessions" in tables:
        current = _column_type("auth_sessions", "expires_at")
        if current and "BIGINT" not in current.upper():
            with op.batch_alter_table("auth_sessions") as batch:
                batch.alter_column(
                    "expires_at",
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False,
                )
    if "login_attempts" in tables:
        current = _column_type("login_attempts", "attempted_at")
        if current and "BIGINT" not in current.upper():
            with op.batch_alter_table("login_attempts") as batch:
                batch.alter_column(
                    "attempted_at",
                    existing_type=sa.Integer(),
                    type_=sa.BigInteger(),
                    existing_nullable=False,
                )


def downgrade():
    with op.batch_alter_table("auth_sessions") as batch:
        batch.alter_column(
            "expires_at",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
    with op.batch_alter_table("login_attempts") as batch:
        batch.alter_column(
            "attempted_at",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
