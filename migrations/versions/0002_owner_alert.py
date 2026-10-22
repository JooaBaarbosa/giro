"""registro do alerta ao dono no pedido

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_orders", sa.Column("owner_alerted_at", sa.DateTime(timezone=True)))
    op.add_column("purchase_orders", sa.Column("owner_alert_dry_run", sa.Boolean))
    op.add_column("purchase_orders", sa.Column("owner_alert_error", sa.String(2000)))


def downgrade() -> None:
    op.drop_column("purchase_orders", "owner_alert_error")
    op.drop_column("purchase_orders", "owner_alert_dry_run")
    op.drop_column("purchase_orders", "owner_alerted_at")
