import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0004_marketplace_events"
down_revision = "0003_price_floor_audit"
branch_labels = None
depends_on = None


def _create_index_if_missing(name, table, columns):
    indexes = {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "marketplace_credentials" not in tables:
        op.create_table(
            "marketplace_credentials",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=64),
                sa.ForeignKey("workspaces.id"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("account_id", sa.String(length=128), nullable=False),
            sa.Column("access_token_encrypted", sa.String(length=4096), nullable=False),
            sa.Column("refresh_token_encrypted", sa.String(length=4096), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "workspace_id",
                "provider",
                name="uq_marketplace_credentials_workspace_provider",
            ),
        )
    _create_index_if_missing(
        "ix_marketplace_credentials_workspace_id",
        "marketplace_credentials",
        ["workspace_id"],
    )

    tables = set(inspect(bind).get_table_names())
    if "marketplace_oauth_states" not in tables:
        op.create_table(
            "marketplace_oauth_states",
            sa.Column("state", sa.String(length=128), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=64),
                sa.ForeignKey("workspaces.id"),
                nullable=False,
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
    _create_index_if_missing(
        "ix_marketplace_oauth_states_workspace_id",
        "marketplace_oauth_states",
        ["workspace_id"],
    )

    tables = set(inspect(bind).get_table_names())
    if "marketplace_jobs" not in tables:
        op.create_table(
            "marketplace_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=64),
                sa.ForeignKey("workspaces.id"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=64), nullable=False),
            sa.Column("job_type", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("last_error", sa.String(length=2048), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    _create_index_if_missing(
        "ix_marketplace_jobs_workspace_id", "marketplace_jobs", ["workspace_id"]
    )
    _create_index_if_missing(
        "ix_marketplace_jobs_status",
        "marketplace_jobs",
        ["status"],
    )
    _create_index_if_missing(
        "ix_marketplace_jobs_status_available",
        "marketplace_jobs",
        ["status", "available_at"],
    )

    tables = set(inspect(bind).get_table_names())
    if "domain_events" not in tables:
        op.create_table(
            "domain_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "workspace_id",
                sa.String(length=64),
                sa.ForeignKey("workspaces.id"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(length=128), nullable=False),
            sa.Column("aggregate_id", sa.String(length=128), nullable=True),
            sa.Column(
                "payload",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    _create_index_if_missing(
        "ix_domain_events_workspace_id", "domain_events", ["workspace_id"]
    )
    _create_index_if_missing(
        "ix_domain_events_event_type", "domain_events", ["event_type"]
    )
    _create_index_if_missing(
        "ix_domain_events_workspace_created_at",
        "domain_events",
        ["workspace_id", "created_at"],
    )


def downgrade():
    tables = set(inspect(op.get_bind()).get_table_names())
    for table in (
        "domain_events",
        "marketplace_jobs",
        "marketplace_oauth_states",
        "marketplace_credentials",
    ):
        if table in tables:
            op.drop_table(table)
