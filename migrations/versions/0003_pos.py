"""modelo de dados do PDV (caixa)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(12, 2)
QTY = sa.Numeric(12, 3)


def upgrade() -> None:
    op.add_column("products", sa.Column("barcode", sa.String(14)))
    op.add_column("products", sa.Column("sale_price", MONEY))
    op.add_column("products", sa.Column("cost_price", MONEY))
    op.create_check_constraint("ck_products_sale_price", "products", "sale_price > 0")
    op.create_check_constraint("ck_products_cost_price", "products", "cost_price >= 0")
    op.create_index(
        "ux_products_barcode",
        "products",
        ["barcode"],
        unique=True,
        postgresql_where=sa.text("barcode IS NOT NULL"),
    )

    op.create_table(
        "cash_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("opening_amount", MONEY, nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("expected_amount", MONEY),
        sa.Column("counted_amount", MONEY),
        sa.Column("difference", MONEY),
        sa.Column("close_note", sa.String(500)),
        sa.Column("is_demo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.CheckConstraint("opening_amount >= 0", name="ck_cash_sessions_opening"),
        sa.CheckConstraint(
            "counted_amount IS NULL OR counted_amount >= 0", name="ck_cash_sessions_counted"
        ),
    )
    op.create_index(
        "ux_cash_sessions_open",
        "cash_sessions",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_table(
        "cash_movements",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("cash_sessions.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.Column("note", sa.String(500)),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('supply', 'withdrawal')", name="ck_cash_movements_kind"),
        sa.CheckConstraint("amount > 0", name="ck_cash_movements_amount"),
    )
    op.create_table(
        "sales",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("session_id", sa.Integer, sa.ForeignKey("cash_sessions.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("total", MONEY, nullable=False, server_default="0"),
        sa.Column("change_given", MONEY, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_reason", sa.String(500)),
        sa.CheckConstraint("status IN ('open', 'completed', 'cancelled')", name="ck_sales_status"),
        sa.CheckConstraint("total >= 0", name="ck_sales_total"),
        sa.CheckConstraint("change_given >= 0", name="ck_sales_change"),
    )
    op.create_index(
        "ux_sales_open",
        "sales",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_table(
        "sale_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "sale_id", sa.Integer, sa.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
        sa.Column("quantity", QTY, nullable=False),
        sa.Column("unit_price", MONEY, nullable=False),
        sa.Column("unit_cost", MONEY),
        sa.Column("line_total", MONEY, nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_sale_items_quantity"),
    )
    op.create_table(
        "sale_payments",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "sale_id", sa.Integer, sa.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("method", sa.String(20), nullable=False),
        sa.Column("amount", MONEY, nullable=False),
        sa.CheckConstraint(
            "method IN ('cash', 'pix', 'credit', 'debit')", name="ck_sale_payments_method"
        ),
        sa.CheckConstraint("amount > 0", name="ck_sale_payments_amount"),
    )

    op.add_column("stock_movements", sa.Column("sale_id", sa.Integer, sa.ForeignKey("sales.id")))
    op.drop_constraint("ck_movements_kind", "stock_movements", type_="check")
    op.create_check_constraint(
        "ck_movements_kind",
        "stock_movements",
        "kind IN ('sale', 'purchase', 'adjustment', 'sale_cancel')",
    )


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT 1 FROM stock_movements WHERE kind = 'sale_cancel'")).first():
        raise RuntimeError("Há cancelamentos de venda no estoque; o downgrade apagaria histórico.")
    op.drop_constraint("ck_movements_kind", "stock_movements", type_="check")
    op.create_check_constraint(
        "ck_movements_kind", "stock_movements", "kind IN ('sale', 'purchase', 'adjustment')"
    )
    op.drop_column("stock_movements", "sale_id")
    op.drop_table("sale_payments")
    op.drop_table("sale_items")
    op.drop_index("ux_sales_open", table_name="sales")
    op.drop_table("sales")
    op.drop_table("cash_movements")
    op.drop_index("ux_cash_sessions_open", table_name="cash_sessions")
    op.drop_table("cash_sessions")
    op.drop_index("ux_products_barcode", table_name="products")
    op.drop_constraint("ck_products_cost_price", "products", type_="check")
    op.drop_constraint("ck_products_sale_price", "products", type_="check")
    op.drop_column("products", "cost_price")
    op.drop_column("products", "sale_price")
    op.drop_column("products", "barcode")
