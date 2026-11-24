import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0005_marketplace_job_idempotency"
down_revision = "0004_marketplace_events"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("marketplace_jobs")}
    if "idempotency_key" not in columns:
        op.add_column(
            "marketplace_jobs",
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        )

    bind.execute(
        sa.text(
            """
            WITH active_jobs AS (
                SELECT
                    id,
                    workspace_id,
                    provider,
                    payload ->> 'product_id' AS product_id,
                    count(*) OVER (
                        PARTITION BY workspace_id, provider, payload ->> 'product_id'
                    ) AS duplicate_count
                FROM marketplace_jobs
                WHERE status IN ('pending', 'in_progress', 'reconciliation_required')
                  AND payload ->> 'product_id' IS NOT NULL
            )
            UPDATE marketplace_jobs AS job
            SET idempotency_key = active_jobs.provider || ':publish:' || active_jobs.product_id
            FROM active_jobs
            WHERE job.id = active_jobs.id
              AND active_jobs.duplicate_count = 1
              AND job.idempotency_key IS NULL
            """
        )
    )

    indexes = {index["name"] for index in inspect(bind).get_indexes("marketplace_jobs")}
    if "uq_marketplace_jobs_active_idempotency_key" not in indexes:
        op.create_index(
            "uq_marketplace_jobs_active_idempotency_key",
            "marketplace_jobs",
            ["workspace_id", "provider", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text(
                "status IN ('pending', 'in_progress', 'reconciliation_required')"
            ),
            sqlite_where=sa.text(
                "status IN ('pending', 'in_progress', 'reconciliation_required')"
            ),
        )


def downgrade():
    bind = op.get_bind()
    indexes = {index["name"] for index in inspect(bind).get_indexes("marketplace_jobs")}
    if "uq_marketplace_jobs_active_idempotency_key" in indexes:
        op.drop_index(
            "uq_marketplace_jobs_active_idempotency_key",
            table_name="marketplace_jobs",
        )
    columns = {column["name"] for column in inspect(bind).get_columns("marketplace_jobs")}
    if "idempotency_key" in columns:
        op.drop_column("marketplace_jobs", "idempotency_key")
