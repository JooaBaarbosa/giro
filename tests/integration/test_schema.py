from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from giro.models import CashSession, MovementKind, Sale, StockMovement


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


def test_pos_tables_exist(engine):
    names = set(sa.inspect(engine).get_table_names())
    assert {"cash_sessions", "cash_movements", "sales", "sale_items", "sale_payments"} <= names


def test_only_one_open_cash_session(session):
    session.add_all(
        [CashSession(opening_amount=Decimal(0)), CashSession(opening_amount=Decimal(0))]
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_only_one_open_sale(session):
    cs = CashSession(opening_amount=Decimal(0))
    session.add(cs)
    session.flush()
    session.add_all([Sale(session_id=cs.id), Sale(session_id=cs.id)])
    with pytest.raises(IntegrityError):
        session.flush()


def test_barcode_unique_when_present(session, make_product):
    make_product(barcode="7891000100103")
    make_product(barcode=None)
    make_product(barcode=None)
    with pytest.raises(IntegrityError):
        make_product(barcode="7891000100103")


def test_sale_cancel_movement_kind_accepted(session, make_product):
    p = make_product(current_stock=Decimal(1))
    session.add(
        StockMovement(
            product_id=p.id,
            kind=MovementKind.SALE_CANCEL,
            quantity=Decimal(1),
            occurred_at=datetime.now(UTC),
        )
    )
    session.flush()
