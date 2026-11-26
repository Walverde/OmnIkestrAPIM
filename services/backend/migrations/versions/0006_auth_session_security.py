import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0006_auth_session_security"
down_revision = "0005_marketplace_job_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(inspect(op.get_bind()).get_table_names())
    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("session_id", sa.String(length=64), primary_key=True),
            sa.Column("expires_at", sa.BigInteger(), nullable=False),
        )
    indexes = {
        index["name"] for index in inspect(op.get_bind()).get_indexes("auth_sessions")
    }
    if "ix_auth_sessions_expires_at" not in indexes:
        op.create_index(
            "ix_auth_sessions_expires_at",
            "auth_sessions",
            ["expires_at"],
        )

    tables = set(inspect(op.get_bind()).get_table_names())
    if "login_attempts" not in tables:
        op.create_table(
            "login_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_key", sa.String(length=64), nullable=False),
            sa.Column("attempted_at", sa.BigInteger(), nullable=False),
        )
    indexes = {
        index["name"] for index in inspect(op.get_bind()).get_indexes("login_attempts")
    }
    if "ix_login_attempts_client_attempted" not in indexes:
        op.create_index(
            "ix_login_attempts_client_attempted",
            "login_attempts",
            ["client_key", "attempted_at"],
        )


def downgrade():
    tables = set(inspect(op.get_bind()).get_table_names())
    if "login_attempts" in tables:
        op.drop_table("login_attempts")
    if "auth_sessions" in tables:
        op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
        op.drop_table("auth_sessions")
