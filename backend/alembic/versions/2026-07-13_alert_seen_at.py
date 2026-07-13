"""alert seen_at

Revision ID: 2026_07_13_alert_seen_at
Revises: 2026_07_12_openphone_labels
Create Date: 2026-07-13 00:00:00.000000

Adds ``alerts.seen_at`` so the navbar badge can show unseen alerts
separately from the dashboard's unsolved (unresolved) count. Nullable,
additive — existing rows stay unseen until the operator next opens the
Alerts page, which is expected (there's no way to know retroactively
whether an already-open alert was looked at).
"""

import sqlalchemy as sa

from alembic import op

revision = "2026_07_13_alert_seen_at"
down_revision = "2026_07_12_openphone_labels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alerts", sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_alerts_resolved_at_seen_at_idx",
        "alerts",
        ["resolved_at", "seen_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_resolved_at_seen_at_idx", table_name="alerts")
    op.drop_column("alerts", "seen_at")
