"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

QTY = sa.Numeric(12, 3)


def _ts(name: str, *, default_now: bool = False) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=not default_now,
        server_default=sa.func.now() if default_now else None,
    )


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("whatsapp", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts("created_at", default_now=True),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sku", sa.String(40), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("min_stock", QTY, nullable=False),
        sa.Column("cover_days", sa.Integer, nullable=False, server_default="15"),
        sa.Column("current_stock", QTY, nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts("created_at", default_now=True),
        sa.CheckConstraint("current_stock >= 0", name="ck_products_stock_nonneg"),
        sa.CheckConstraint("unit IN ('un', 'kg', 'cx')", name="ck_products_unit"),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("supplier_id", sa.Integer, sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="suggested"),
        sa.Column("message_text", sa.String(4000)),
        sa.Column("dry_run", sa.Boolean),
        sa.Column("whatsapp_message_id", sa.String(200)),
        sa.Column("error", sa.String(2000)),
        _ts("created_at", default_now=True),
        _ts("approved_at"),
        _ts("claimed_at"),
        _ts("sent_at"),
        _ts("received_at"),
        sa.CheckConstraint(
            "status IN ('suggested','approved','sending','sent','received','failed','cancelled')",
            name="ck_orders_status",
        ),
    )
    op.create_index("ix_orders_status", "purchase_orders", ["status"])
    op.create_table(
        "purchase_order_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer,
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", QTY, nullable=False),
    )
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("quantity", QTY, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(500)),
        sa.Column("purchase_order_id", sa.Integer, sa.ForeignKey("purchase_orders.id")),
        _ts("created_at", default_now=True),
        sa.CheckConstraint("kind IN ('sale', 'purchase', 'adjustment')", name="ck_movements_kind"),
    )
    op.create_index("ix_movements_product_time", "stock_movements", ["product_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("stock_movements")
    op.drop_table("purchase_order_items")
    op.drop_table("purchase_orders")
    op.drop_table("products")
    op.drop_table("suppliers")
