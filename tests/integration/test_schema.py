from decimal import Decimal

import pytest
import sqlalchemy as sa


def test_tables_exist(engine):
    names = set(sa.inspect(engine).get_table_names())
    assert {
        "suppliers",
        "products",
        "stock_movements",
        "purchase_orders",
        "purchase_order_items",
    } <= names


def test_current_stock_cannot_be_negative(session, make_product):
    p = make_product()
    p.current_stock = Decimal("-1")
    with pytest.raises(sa.exc.IntegrityError):
        session.flush()


def test_sku_unique(session, make_product):
    make_product(sku="A1")
    with pytest.raises(sa.exc.IntegrityError):
        make_product(sku="A1")
