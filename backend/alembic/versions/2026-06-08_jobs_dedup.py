"""add jobs table and dedup columns

Revision ID: 2026_06_08_jobs_dedup
Revises:
Create Date: 2026-06-08 00:00:00.000000

Manual migration. The project's schema was bootstrapped with
``Base.metadata.create_all`` and ``alembic/versions/`` was empty before
this file — there is no baseline revision. This migration contains only
the *new* schema additions, not the existing tables.

New schema:

- ``jobs`` parent table for cross-message dedup
- ``incoming_messages.source`` enum column (default 'openphone')
- ``incoming_messages`` OpenPhone-specific columns made nullable so
  WhatsApp-sourced rows can leave them empty
- ``dispatch_jobs.job_id`` FK to ``jobs.id`` (nullable, ON DELETE SET NULL)
- ``dispatch_jobs`` gains the 4 new extraction columns: ``customer_name``,
  ``customer_phone``, ``scheduled_at``, ``job_description``
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_06_08_jobs_dedup"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === New jobs table ===
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("address_street_number", sa.String(length=20), nullable=True),
        sa.Column("address_street_name", sa.String(length=100), nullable=True),
        sa.Column("address_city", sa.String(length=100), nullable=True),
        sa.Column("address_state", sa.String(length=2), nullable=True),
        sa.Column("address_zip", sa.String(length=10), nullable=True),
        sa.Column("job_type", sa.String(length=100), nullable=True),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duplicate_of", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["duplicate_of"], ["jobs.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_jobs_company_id", "jobs", ["company_id"])
    op.create_index("ix_jobs_first_message_at", "jobs", ["first_message_at"])
    op.create_index("ix_jobs_address_street_name", "jobs", ["address_street_name"])
    op.create_index("ix_jobs_job_type", "jobs", ["job_type"])
    op.create_index("ix_jobs_is_duplicate", "jobs", ["is_duplicate"])

    # === incoming_messages: add source + relax OpenPhone-specific columns ===
    op.add_column(
        "incoming_messages",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="openphone",
        ),
    )
    op.create_index("ix_incoming_messages_source", "incoming_messages", ["source"])

    op.alter_column(
        "incoming_messages", "openphone_id", existing_type=sa.String(length=100), nullable=True
    )
    op.alter_column(
        "incoming_messages", "direction", existing_type=sa.String(length=20), nullable=True
    )
    op.alter_column(
        "incoming_messages", "from_number", existing_type=sa.String(length=50), nullable=True
    )
    op.alter_column(
        "incoming_messages", "event_type", existing_type=sa.String(length=30), nullable=True
    )
    op.alter_column(
        "incoming_messages", "to_numbers", existing_type=postgresql.JSONB, nullable=True
    )
    op.alter_column(
        "incoming_messages", "raw_payload", existing_type=postgresql.JSONB, nullable=True
    )

    # === dispatch_jobs: add job_id + 4 new extraction columns ===
    op.add_column(
        "dispatch_jobs",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_dispatch_jobs_job_id",
        "dispatch_jobs",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_dispatch_jobs_job_id", "dispatch_jobs", ["job_id"])

    op.add_column(
        "dispatch_jobs",
        sa.Column("customer_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "dispatch_jobs",
        sa.Column("customer_phone", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "dispatch_jobs",
        sa.Column("scheduled_at", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "dispatch_jobs",
        sa.Column("job_description", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dispatch_jobs", "job_description")
    op.drop_column("dispatch_jobs", "scheduled_at")
    op.drop_column("dispatch_jobs", "customer_phone")
    op.drop_column("dispatch_jobs", "customer_name")
    op.drop_index("ix_dispatch_jobs_job_id", table_name="dispatch_jobs")
    op.drop_constraint("fk_dispatch_jobs_job_id", "dispatch_jobs", type_="foreignkey")
    op.drop_column("dispatch_jobs", "job_id")

    op.alter_column(
        "incoming_messages", "raw_payload", existing_type=postgresql.JSONB, nullable=False
    )
    op.alter_column(
        "incoming_messages", "to_numbers", existing_type=postgresql.JSONB, nullable=False
    )
    op.alter_column(
        "incoming_messages", "event_type", existing_type=sa.String(length=30), nullable=False
    )
    op.alter_column(
        "incoming_messages", "from_number", existing_type=sa.String(length=50), nullable=False
    )
    op.alter_column(
        "incoming_messages", "direction", existing_type=sa.String(length=20), nullable=False
    )
    op.alter_column(
        "incoming_messages", "openphone_id", existing_type=sa.String(length=100), nullable=False
    )
    op.drop_index("ix_incoming_messages_source", table_name="incoming_messages")
    op.drop_column("incoming_messages", "source")

    op.drop_index("ix_jobs_is_duplicate", table_name="jobs")
    op.drop_index("ix_jobs_job_type", table_name="jobs")
    op.drop_index("ix_jobs_address_street_name", table_name="jobs")
    op.drop_index("ix_jobs_first_message_at", table_name="jobs")
    op.drop_index("ix_jobs_company_id", table_name="jobs")
    op.drop_table("jobs")
