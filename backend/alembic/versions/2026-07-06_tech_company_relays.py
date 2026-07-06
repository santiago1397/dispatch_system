"""tech-update denormalization + company-update relays

Revision ID: 2026_07_06_tech_company_relays
Revises: 2026_06_28_drop_outbound_drafts
Create Date: 2026-07-06 00:00:00.000000

Two schema changes behind the post-acceptance tech-update work:

1. Three denormalized columns on ``jobs`` so the /jobs list + detail can
   render tech-update timings without a per-row lifecycle-event query:
     - ``appt_at``          — appointment time (set on the ``appt_set`` transition)
     - ``follow_up_at``     — callback reminder time (set on ``needs_follow_up``)
     - ``last_tech_reason`` — last reason code (refused / dns / priceshopping / …)

2. A new ``company_updates`` table holding the status relay the operator
   should forward to the source company (original job message + the
   update). The system never sends it — the operator relays it natively;
   the alert engine reminds them via ``company_update_unsent`` if it isn't
   observed within the SLA.

No DDL is needed for the new *string-enum* values introduced alongside
this work, since they live in plain VARCHAR columns:
  - ``jobs.lifecycle_status``    gains ``accepted`` and ``rejected``
  - ``alerts.kind``              gains ``undispatched``, ``follow_up_due``,
                                 ``company_update_unsent``
  - ``job_lifecycle_events.source`` gains ``operator_reject``

Downgrade drops the table and the three columns; the enum-string values
simply stop being written.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "2026_07_06_tech_company_relays"
down_revision = "2026_06_28_drop_outbound_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Denormalized tech-update columns on jobs (nullable — backfill not
    #    needed; existing rows simply have no appt/follow-up/reason yet).
    op.add_column("jobs", sa.Column("appt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("follow_up_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("jobs", sa.Column("last_tech_reason", sa.String(length=30), nullable=True))

    # 2. company_updates — the pending operator→company relays.
    op.create_table(
        "company_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lifecycle_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("update_kind", sa.String(length=30), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("company_chat_jid", sa.String(length=100), nullable=True),
        sa.Column("company_phone", sa.String(length=50), nullable=True),
        sa.Column("message_text", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="company_updates_pkey"),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="company_updates_job_id_fkey", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["lifecycle_event_id"],
            ["job_lifecycle_events.id"],
            name="company_updates_lifecycle_event_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="company_updates_company_id_fkey",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "company_updates_job_id_idx", "company_updates", ["job_id"], unique=False
    )
    op.create_index(
        "ix_company_updates_sent_at_created_at",
        "company_updates",
        ["sent_at", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_company_updates_sent_at_created_at", table_name="company_updates")
    op.drop_index("company_updates_job_id_idx", table_name="company_updates")
    op.drop_table("company_updates")

    op.drop_column("jobs", "last_tech_reason")
    op.drop_column("jobs", "follow_up_at")
    op.drop_column("jobs", "appt_at")
