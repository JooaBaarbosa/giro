from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from giro.errors import InvalidTransition, NotFound
from giro.models import (
    CashMovementKind,
    MovementKind,
    PaymentMethod,
    SaleStatus,
    StockMovement,
)
from giro.pos.schemas import (
    AddItemIn,
    CancelSaleIn,
    CashMovementIn,
    CloseSessionIn,
    OpenSessionIn,
    PaymentIn,
)
from giro.pos.service import (
    add_cash_movement,
    add_item,
    add_payment,
    cancel_sale,
    close_session,
    complete_sale,
    current_sale,
    current_session,
    get_sale,
    get_session,
    list_session_sales,
    list_sessions,
    open_session,
    session_summary,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
D = Decimal
CASH, PIX = PaymentMethod.CASH, PaymentMethod.PIX
CREDIT, DEBIT = PaymentMethod.CREDIT, PaymentMethod.DEBIT


@pytest.fixture
def cash(session: Session):
    return open_session(session, OpenSessionIn(opening_amount=D("100")), now=NOW)


def _sell(s: Session, code: str, qty: str, payments: list[tuple[PaymentMethod, str]]):
    add_item(s, AddItemIn(code=code, quantity=D(qty)), now=NOW)
    for method, amount in payments:
        add_payment(s, PaymentIn(method=method, amount=D(amount)))
    return complete_sale(s, now=NOW)


def _supply(s: Session, kind: CashMovementKind, amount: str):
    return add_cash_movement(s, CashMovementIn(kind=kind, amount=D(amount)), now=NOW)


def test_cancel_returns_stock_and_records_movement(session, cash, make_product):
    p = make_product(sku="A1", sale_price=D("5.00"), current_stock=D("10"))
    sale = _sell(session, "A1", "3", [(CASH, "15.00")])
    assert p.current_stock == D("7")
    cancelled = cancel_sale(session, sale.id, CancelSaleIn(reason="Cliente desistiu"), now=NOW)
    assert cancelled.status is SaleStatus.CANCELLED
    assert cancelled.cancelled_at == NOW
    assert cancelled.cancel_reason == "Cliente desistiu"
    session.refresh(p)
    assert p.current_stock == D("10")
    moves = list(
        session.scalars(select(StockMovement).where(StockMovement.kind == MovementKind.SALE_CANCEL))
    )
    assert len(moves) == 1
    assert moves[0].sale_id == sale.id
    assert moves[0].product_id == p.id
    assert moves[0].quantity == D("3")


def test_cancel_twice_is_rejected(session, cash, make_product):
    make_product(sku="A1", sale_price=D("5.00"), current_stock=D("10"))
    sale = _sell(session, "A1", "1", [(CASH, "5.00")])
    cancel_sale(session, sale.id, CancelSaleIn(reason="Erro no valor"), now=NOW)
    with pytest.raises(InvalidTransition):
        cancel_sale(session, sale.id, CancelSaleIn(reason="Erro no valor"), now=NOW)


def test_cancel_open_sale_is_rejected(session, cash, make_product):
    make_product(sku="A1", sale_price=D("5.00"), current_stock=D("10"))
    add_item(session, AddItemIn(code="A1"), now=NOW)
    sale = current_sale(session)
    assert sale is not None
    with pytest.raises(InvalidTransition):
        cancel_sale(session, sale.id, CancelSaleIn(reason="Teste aberto"), now=NOW)


def test_cancel_unknown_sale_not_found(session, cash):
    with pytest.raises(NotFound):
        cancel_sale(session, 999999, CancelSaleIn(reason="Não existe"), now=NOW)


def test_cancel_sale_of_closed_session_is_rejected(session, cash, make_product):
    make_product(sku="A1", sale_price=D("5.00"), current_stock=D("10"))
    sale = _sell(session, "A1", "1", [(CASH, "5.00")])
    close_session(session, CloseSessionIn(counted_amount=D("105")), now=NOW)
    with pytest.raises(InvalidTransition, match="caixa já fechado"):
        cancel_sale(session, sale.id, CancelSaleIn(reason="Tarde demais"), now=NOW)
    open_session(session, OpenSessionIn(opening_amount=D("0")), now=NOW)
    with pytest.raises(InvalidTransition, match="caixa já fechado"):
        cancel_sale(session, sale.id, CancelSaleIn(reason="Tarde demais"), now=NOW)


def test_summary_matches_hand_computation(session, cash, make_product):
    make_product(sku="A1", sale_price=D("10.00"), current_stock=D("50"))
    make_product(sku="B1", sale_price=D("2.50"), current_stock=D("50"))
    # venda 1: 3 x 10 = 30,00 -> 20 pix + 20 dinheiro (troco 10)
    _sell(session, "A1", "3", [(PIX, "20.00"), (CASH, "20.00")])
    # venda 2 (cancelada): 4 x 2,50 = 10,00 em dinheiro
    s2 = _sell(session, "B1", "4", [(CASH, "10.00")])
    cancel_sale(session, s2.id, CancelSaleIn(reason="Cliente desistiu"), now=NOW)
    _supply(session, CashMovementKind.SUPPLY, "50")
    _supply(session, CashMovementKind.WITHDRAWAL, "30")
    summary = session_summary(session, cash.id)
    assert summary.by_method == {
        CASH: D("10.00"),
        PIX: D("20.00"),
        CREDIT: D("0.00"),
        DEBIT: D("0.00"),
    }
    assert summary.sales_count == 1
    assert summary.sales_total == D("30.00")
    assert summary.cancelled_count == 1
    assert summary.supplies == D("50.00")
    assert summary.withdrawals == D("30.00")
    # 100 + 50 - 30 + (20 - 10)
    assert summary.expected_cash == D("130.00")


def test_summary_empty_session(session, cash):
    summary = session_summary(session, cash.id)
    assert len(summary.by_method) == 4
    assert all(v == D("0.00") for v in summary.by_method.values())
    assert summary.sales_count == 0
    assert summary.sales_total == D("0.00")
    assert summary.expected_cash == D("100.00")


def test_close_with_open_sale_is_rejected(session, cash, make_product):
    make_product(sku="A1", sale_price=D("5.00"), current_stock=D("10"))
    add_item(session, AddItemIn(code="A1"), now=NOW)
    with pytest.raises(InvalidTransition, match="Conclua ou descarte"):
        close_session(session, CloseSessionIn(counted_amount=D("100")), now=NOW)
    assert current_session(session) is not None


def test_close_records_difference_and_allows_new_session(session, cash):
    closed = close_session(
        session, CloseSessionIn(counted_amount=D("95"), note="Faltou troco"), now=NOW
    )
    assert closed.expected_amount == D("100.00")
    assert closed.counted_amount == D("95.00")
    assert closed.difference == D("-5.00")
    assert closed.close_note == "Faltou troco"
    assert closed.closed_at == NOW
    assert current_session(session) is None
    new = open_session(session, OpenSessionIn(opening_amount=D("0")), now=NOW)
    assert new.id != closed.id


def test_getters_and_lists(session, cash, make_product):
    make_product(sku="A1", sale_price=D("5.00"), current_stock=D("10"))
    s1 = _sell(session, "A1", "1", [(CASH, "5.00")])
    s2 = _sell(session, "A1", "1", [(CASH, "5.00")])
    cancel_sale(session, s1.id, CancelSaleIn(reason="Erro no valor"), now=NOW)
    assert get_sale(session, s1.id).id == s1.id
    assert get_session(session, cash.id).id == cash.id
    with pytest.raises(NotFound):
        get_sale(session, 999999)
    with pytest.raises(NotFound):
        get_session(session, 999999)
    assert [x.id for x in list_session_sales(session, cash.id)] == [s2.id, s1.id]
    close_session(session, CloseSessionIn(counted_amount=D("105")), now=NOW)
    newer = open_session(session, OpenSessionIn(opening_amount=D("0")), now=NOW)
    assert [x.id for x in list_sessions(session)] == [newer.id, cash.id]
    assert [x.id for x in list_sessions(session, limit=1)] == [newer.id]
