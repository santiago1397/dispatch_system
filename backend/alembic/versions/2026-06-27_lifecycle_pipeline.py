"""add lifecycle pipeline tables and columns

Revision ID: 2026_06_27_lifecycle_pipeline
Revises: 2026_06_15_job_closing_fields
Create Date: 2026-06-27 00:00:00.000000

Adds the lifecycle pipeline foundation. One revision for everything in
Phase 1 — five new tables (technicians, job_lifecycle_events,
outbound_drafts, alerts, daily_stats_snapshots), four new columns on
existing tables (jobs, whatsapp_tracked_chats, incoming_messages), and
the lifecycle_status backfill on existing Job rows.

Backfill order matters: the column is added NULL-able, populated, then
``NOT NULL`` is enforced. This avoids a constraint violation when the
column is created with a server default on a table that already has
rows.

Note on ``Base.metadata.create_all`` baseline: this project's schema
was bootstrapped with ``create_all`` (see ``CLAUDE.md``) but two
manually-written revisions (``2026_06_08_jobs_dedup``,
``2026_06_15_job_closing_fields``) already ship the closing-pipeline
additions. Continue that pattern: explicit ``op.add_column`` /
``op.create_table`` calls, NOT ``alembic revision --autogenerate``,
because autogenerate against a create_all-built schema sees the entire
current schema as a brand-new baseline and produces garbage.

New columns on ``jobs``:

- ``lifecycle_status`` — denormalized latest transition's ``to_status``.
  Backfilled: ``closed`` for jobs with ``closed_at IS NOT NULL``,
  ``pending`` otherwise. NOT NULL after backfill.
- ``lifecycle_status_changed_at`` — timestamp of the latest transition;
  anchor for the alert engine's SLA checks.
- ``original_inbound_from_number`` — frozen at Job creation so the
  outbound-draft pipeline always reaches the same contact.
- ``original_inbound_channel`` — ``openphone`` or ``whatsapp``; tells
  the draft-sender which path to use.

New columns on ``whatsapp_tracked_chats``:

- ``chat_role`` — ``customer_source`` | ``tech_dispatch`` | ``closing``
  | ``other``. Default ``other`` so existing chats route nowhere until
  an operator retags them from ``/dispatch/chat-roles``.

New columns on ``incoming_messages``:

- ``lifecycle_event_id`` — optional FK to ``job_lifecycle_events.id``,
  NULL for messages that didn't trigger a lifecycle transition.

New tables (all include ``created_at`` / ``updated_at`` via
``TimestampMixin``):

- ``technicians`` — dispatch targets (one row per tech).
- ``job_lifecycle_events`` — append-only audit log; one row per
  state transition.
- ``outbound_drafts`` — the manual gateway for all customer comms;
  the operator sends from ``/outbound``.
- ``alerts`` — pipeline health (stuck jobs, missing closings,
  unattributed replies, no-match dispatches).
- ``daily_stats_snapshots`` — per-day rollups for the ``/stats`` page
  and CSV / JSON export.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2026_06_27_lifecycle_pipeline"
down_revision: str | None = "2026_06_15_job_closing_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === 1. jobs: add lifecycle_status (nullable) + 3 other columns ===
    op.add_column(
        "jobs",
        sa.Column("lifecycle_status", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "lifecycle_status_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("original_inbound_from_number", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("original_inbound_channel", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_jobs_lifecycle_status_idx",
        "jobs",
        ["lifecycle_status"],
    )

    # === 2. Backfill lifecycle_status on existing rows ===
    # Jobs already closed stay closed; everything else is pending until
    # the lifecycle pipeline picks it up.
    op.execute(
        "UPDATE jobs SET lifecycle_status = 'closed' "
        "WHERE closed_at IS NOT NULL AND lifecycle_status IS NULL"
    )
    op.execute("UPDATE jobs SET lifecycle_status = 'pending' WHERE lifecycle_status IS NULL")

    # === 3. jobs.lifecycle_status → NOT NULL with default 'pending' ===
    op.alter_column(
        "jobs",
        "lifecycle_status",
        nullable=False,
        server_default="pending",
    )

    # === 4. whatsapp_tracked_chats: add chat_role ===
    op.add_column(
        "whatsapp_tracked_chats",
        sa.Column(
            "chat_role",
            sa.String(length=20),
            nullable=False,
            server_default="other",
        ),
    )
    op.create_index(
        "ix_whatsapp_tracked_chats_chat_role_idx",
        "whatsapp_tracked_chats",
        ["chat_role"],
    )

    # === 5. technicians table ===
    op.create_table(
        "technicians",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone_e164", sa.String(length=15), nullable=True),
        sa.Column("whatsapp_chat_jid", sa.String(length=100), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("whatsapp_chat_jid", name="technicians_whatsapp_chat_jid_key"),
    )
    op.create_index(
        "ix_technicians_whatsapp_chat_jid_idx",
        "technicians",
        ["whatsapp_chat_jid"],
    )

    # === 6. job_lifecycle_events table ===
    op.create_table(
        "job_lifecycle_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=False),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
            nullable=True,
        ),
    )
    op.create_index(
        "ix_job_lifecycle_events_job_id_created_at_idx",
        "job_lifecycle_events",
        ["job_id", "created_at"],
    )

    # === 7. incoming_messages: add lifecycle_event_id + FK ===
    op.add_column(
        "incoming_messages",
        sa.Column(
            "lifecycle_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_incoming_messages_lifecycle_event_id",
        "incoming_messages",
        "job_lifecycle_events",
        ["lifecycle_event_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # === 8. outbound_drafts table ===
    op.create_table(
        "outbound_drafts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lifecycle_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_lifecycle_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient_kind", sa.String(length=20), nullable=False),
        sa.Column("recipient_phone_e164", sa.String(length=30), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_by_source", sa.String(length=30), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_message_id", sa.String(length=100), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_outbound_drafts_status_created_at_idx",
        "outbound_drafts",
        ["status", "created_at"],
    )

    # === 9. alerts table ===
    op.create_table(
        "alerts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("chat_jid", sa.String(length=100), nullable=True),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("threshold_minutes", sa.Integer(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_alerts_kind_resolved_at_idx",
        "alerts",
        ["kind", "resolved_at"],
    )

    # === 10. daily_stats_snapshots table ===
    op.create_table(
        "daily_stats_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_daily_stats_snapshots_date_scope_idx",
        "daily_stats_snapshots",
        ["snapshot_date", "scope"],
    )


def downgrade() -> None:
    # Reverse order. Tables first, then the FKs + new columns.

    op.drop_index(
        "ix_daily_stats_snapshots_date_scope_idx",
        table_name="daily_stats_snapshots",
    )
    op.drop_table("daily_stats_snapshots")

    op.drop_index("ix_alerts_kind_resolved_at_idx", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_outbound_drafts_status_created_at_idx", table_name="outbound_drafts")
    op.drop_table("outbound_drafts")

    op.drop_constraint(
        "fk_incoming_messages_lifecycle_event_id",
        "incoming_messages",
        type_="foreignkey",
    )
    op.drop_column("incoming_messages", "lifecycle_event_id")

    op.drop_index(
        "ix_job_lifecycle_events_job_id_created_at_idx",
        table_name="job_lifecycle_events",
    )
    op.drop_table("job_lifecycle_events")

    op.drop_index("ix_technicians_whatsapp_chat_jid_idx", table_name="technicians")
    op.drop_table("technicians")

    op.drop_index(
        "ix_whatsapp_tracked_chats_chat_role_idx",
        table_name="whatsapp_tracked_chats",
    )
    op.drop_column("whatsapp_tracked_chats", "chat_role")

    op.drop_index("ix_jobs_lifecycle_status_idx", table_name="jobs")
    op.drop_column("jobs", "original_inbound_channel")
    op.drop_column("jobs", "original_inbound_from_number")
    op.drop_column("jobs", "lifecycle_status_changed_at")
    op.drop_column("jobs", "lifecycle_status")
