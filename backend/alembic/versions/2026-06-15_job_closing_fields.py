"""add job closing fields

Revision ID: 2026_06_15_job_closing_fields
Revises: 2026_06_08_jobs_dedup
Create Date: 2026-06-15 00:00:00.000000

Manual migration. Adds the columns the "Dispatch closing" pipeline writes
to when a closing/payment message from the WhatsApp group of that name
matches an existing Job. ``classification_status`` is a plain VARCHAR(20)
so the two new enum values (``closed`` / ``closing_unmatched``) do not
require any DDL change.

New columns on ``jobs``:

- ``closed_total``, ``closed_parts``, ``closed_tip``,
  ``closed_payment_method``, ``closed_notes`` — final actuals, kept
  separate from the estimates that live on the originating DispatchJob.
- ``closed_at`` — timestamp of the closing event. Indexed because the
  analytics queries filter on it. ``IS NOT NULL`` is the canonical
  "this Job has been closed" check.
- ``closed_from_dispatch_job_id`` — audit pointer back to the closing
  message's DispatchJob row, so the closing source is one join away.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_06_15_job_closing_fields"
down_revision: str | None = "2026_06_08_jobs_dedup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("closed_total", sa.String(length=50), nullable=True))
    op.add_column("jobs", sa.Column("closed_parts", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("closed_tip", sa.String(length=50), nullable=True))
    op.add_column("jobs", sa.Column("closed_payment_method", sa.String(length=20), nullable=True))
    op.add_column("jobs", sa.Column("closed_notes", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("closed_from_dispatch_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jobs_closed_from_dispatch_job_id",
        "jobs",
        "dispatch_jobs",
        ["closed_from_dispatch_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_jobs_closed_at", "jobs", ["closed_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_closed_at", table_name="jobs")
    op.drop_constraint("fk_jobs_closed_from_dispatch_job_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "closed_from_dispatch_job_id")
    op.drop_column("jobs", "closed_at")
    op.drop_column("jobs", "closed_notes")
    op.drop_column("jobs", "closed_payment_method")
    op.drop_column("jobs", "closed_tip")
    op.drop_column("jobs", "closed_parts")
    op.drop_column("jobs", "closed_total")
