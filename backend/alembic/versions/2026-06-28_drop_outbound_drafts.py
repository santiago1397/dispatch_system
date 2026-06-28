"""drop outbound_drafts table — system no longer sends customer messages

Revision ID: 2026_06_28_drop_outbound_drafts
Revises: 2026_06_27_lifecycle_pipeline
Create Date: 2026-06-28 00:00:00.000000

The operator types every customer message natively in WhatsApp / OpenPhone;
our system only observes what they did. The ``outbound_drafts`` table,
``/outbound`` console, ``POST /outbound/{id}/send`` route, and the
``OpenPhoneService.send_message`` method have all been removed in the
same refactor.

This migration drops the orphaned ``outbound_drafts`` table and its
status index. The partial index ``ix_outbound_drafts_status_created_at_idx``
was created with ``WHERE status='pending'`` in the previous revision —
``op.drop_index`` handles it by name without needing the WHERE clause.

There is no data to preserve — drafts were transient (pending → sent /
discarded); no historical report reads them.

Reverse direction (downgrade) recreates the table with the same schema
as the prior migration so ``alembic downgrade -1`` lands cleanly.
"""

import sqlalchemy as sa

from alembic import op

revision = "2026_06_28_drop_outbound_drafts"
down_revision = "2026_06_27_lifecycle_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index first (FK to nothing, but drop_index is idempotent on name).
    op.drop_index(
        "ix_outbound_drafts_status_created_at_idx",
        table_name="outbound_drafts",
    )
    op.drop_table("outbound_drafts")


def downgrade() -> None:
    # Mirror the CREATE TABLE from 2026-06-27_lifecycle_pipeline.py so
    # downgrade lands cleanly. We intentionally don't backfill any rows —
    # the data was transient.
    op.create_table(
        "outbound_drafts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lifecycle_event_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recipient_kind", sa.String(length=20), nullable=False),
        sa.Column("recipient_phone_e164", sa.String(length=30), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_by_source", sa.String(length=30), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_message_id", sa.String(length=100), nullable=True),
        sa.Column(
            "raw_payload", sa.dialects.postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["lifecycle_event_id"], ["job_lifecycle_events.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_outbound_drafts_status_created_at_idx",
        "outbound_drafts",
        ["status", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
