"""openphone thread labels

Revision ID: 2026_07_12_openphone_labels
Revises: 2026_07_06_tech_company_relays
Create Date: 2026-07-12 00:00:00.000000

New ``openphone_thread_labels`` table backing the "associate a company or
label" affordance on the ``/openphone`` chat view. Deliberately separate
from ``company_phone_bindings`` (which feeds the tier-3 classifier) — this
table is display-only and never touches classification.

Revision id kept short (<=32 chars) — ``alembic_version.version_num`` is
``VARCHAR(32)`` and the obvious longer id doesn't fit.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "2026_07_12_openphone_labels"
down_revision = "2026_07_06_tech_company_relays"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "openphone_thread_labels",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("counterparty", sa.String(length=50), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="openphone_thread_labels_pkey"),
        sa.UniqueConstraint("counterparty", name="openphone_thread_labels_counterparty_key"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="openphone_thread_labels_company_id_fkey",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="openphone_thread_labels_created_by_user_id_fkey",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_openphone_thread_labels_counterparty",
        "openphone_thread_labels",
        ["counterparty"],
        unique=True,
    )
    op.create_index(
        "ix_openphone_thread_labels_company_id",
        "openphone_thread_labels",
        ["company_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_openphone_thread_labels_company_id", table_name="openphone_thread_labels")
    op.drop_index("ix_openphone_thread_labels_counterparty", table_name="openphone_thread_labels")
    op.drop_table("openphone_thread_labels")
