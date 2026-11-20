import threading
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from giro.errors import (
    InsufficientStock,
    InvalidInput,
    InvalidTransition,
    NotFound,
    PaymentMismatch,
    Shortage,
)
from giro.models import MovementKind, PaymentMethod, SaleStatus, StockMovement
from giro.pos.schemas import AddItemIn, AdjustShortageIn, OpenSessionIn, PaymentIn
from giro.pos.service import (
    add_item,
    add_payment,
    adjust_for_shortage,
    complete_sale,
    current_sale,
    expected_cash,
    open_session,
    remove_item,
    remove_payment,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
D = Decimal
CASH, PIX = PaymentMethod.CASH, PaymentMethod.PIX


@pytest.fixture
def cash(session: Session):
    return open_session(session, OpenSessionIn(opening_amount=D("100")), now=NOW)


def _add(s: Session, code: str, qty: str | None = None):
    return add_item(s, AddItemIn(code=code, quantity=D(qty) if qty else None), now=NOW)


def _pay(s: Session, method: PaymentMethod, amount: str):
    return add_payment(s, PaymentIn(method=method, amount=D(amount)))


def _sale_movements(s: Session) -> list[StockMovement]:
    return list(
        s.scalars(
            select(StockMovement)
            .where(StockMovement.kind == MovementKind.SALE)
            .order_by(StockMovement.id)
        )
    )


def test_complete_deducts_stock_with_sale_id(session, cash, make_product):
    p = make_product(sku="A1", sale_price=D("5.00"), current_stock=D("10"))
    _add(session, "A1", "2")
    _pay(session, CASH, "10")
    sale = complete_sale(session, now=NOW)
    assert sale.status is SaleStatus.COMPLETED
    assert sale.completed_at == NOW
    assert sale.change_given == D("0.00")
    assert p.current_stock == D("8")
    movements = _sale_movements(session)
    assert len(movements) == 1
    assert movements[0].sale_id == sale.id
    assert movements[0].quantity == D("2")
    assert movements[0].product_id == p.id
    assert movements[0].occurred_at == NOW
    assert current_sale(session) is None


def test_complete_split_payment_with_change(session, cash, make_product):
    make_product(sku="A1", sale_price=D("30.00"), current_stock=D("5"))
    _add(session, "A1")
    _pay(session, PIX, "10")
    _pay(session, CASH, "50")
    sale = complete_sale(session, now=NOW)
    assert sale.change_given == D("30.00")
    assert sale.total == D("30.00")


def test_complete_with_insufficient_payment_refused(session, cash, make_product):
    make_product(sku="A1", sale_price=D("30.00"), current_stock=D("5"))
    _add(session, "A1")
    _pay(session, CASH, "10")
    with pytest.raises(PaymentMismatch):
        complete_sale(session, now=NOW)
    assert _sale_movements(session) == []


def test_complete_without_open_sale_refused(session, cash):
    with pytest.raises(InvalidInput, match="Não há venda em andamento."):
        complete_sale(session, now=NOW)


def test_complete_empty_refused(session, cash, make_product):
    make_product(sku="A1", sale_price=D("5.00"), current_stock=D("5"))
    item = _add(session, "A1")
    remove_item(session, item.id)
    assert current_sale(session) is not None
    with pytest.raises(InvalidInput, match="Adicione ao menos um item antes de concluir."):
        complete_sale(session, now=NOW)


def test_complete_shortage_rolls_back(session, cash, make_product):
    ok = make_product(sku="OK", sale_price=D("5.00"), current_stock=D("10"))
    short = make_product(sku="SH", name="Sem estoque", sale_price=D("2.00"), current_stock=D("1"))
    _add(session, "OK", "2")
    _add(session, "SH", "3")
    _pay(session, CASH, "100")
    session.commit()
    with pytest.raises(
        InsufficientStock, match="Estoque insuficiente para concluir a venda."
    ) as ei:
        complete_sale(session, now=NOW)
    assert ei.value.shortages == [Shortage(short.id, "Sem estoque", D("1"), D("3"))]
    session.rollback()
    assert _sale_movements(session) == []
    sale = current_sale(session)
    assert sale is not None
    assert sale.status is SaleStatus.OPEN
    assert len(sale.items) == 2
    session.refresh(ok)
    assert ok.current_stock == D("10")


def test_adjust_then_complete(session, cash, make_product):
    p = make_product(sku="A1", sale_price=D("5.00"), current_stock=D("1"))
    _add(session, "A1", "3")
    _pay(session, CASH, "15")
    data = AdjustShortageIn(product_id=p.id, reason="Contagem errada")
    movement = adjust_for_shortage(session, data, now=NOW)
    assert movement.kind is MovementKind.ADJUSTMENT
    assert movement.quantity == D("2")
    assert movement.note == "Ajuste no caixa: Contagem errada"
    assert p.current_stock == D("3")
    sale = complete_sale(session, now=NOW)
    assert sale.status is SaleStatus.COMPLETED
    assert p.current_stock == D("0")


def test_adjust_not_short_refused(session, cash, make_product):
    p = make_product(sku="A1", sale_price=D("5.00"), current_stock=D("5"))
    _add(session, "A1", "3")
    with pytest.raises(InvalidInput, match="Este item não está mais em falta."):
        adjust_for_shortage(session, AdjustShortageIn(product_id=p.id, reason="abc"), now=NOW)


def test_payment_validation_at_entry(session, cash, make_product):
    make_product(sku="A1", sale_price=D("30.00"), current_stock=D("5"))
    _add(session, "A1")
    _pay(session, PIX, "10")
    with pytest.raises(PaymentMismatch):
        _pay(session, PIX, "25")
    with pytest.raises(PaymentMismatch):
        _pay(session, CASH, "0")
    assert _pay(session, CASH, "50").amount == D("50")


def test_payment_needs_open_sale(session, cash):
    with pytest.raises(InvalidInput, match="Não há venda em andamento."):
        _pay(session, CASH, "10")


def test_remove_payment(session, cash, make_product):
    make_product(sku="A1", sale_price=D("30.00"), current_stock=D("5"))
    _add(session, "A1")
    payment = _pay(session, PIX, "10")
    remove_payment(session, payment.id)
    assert current_sale(session, fresh=True).payments == []
    with pytest.raises(NotFound, match="Pagamento não encontrado."):
        remove_payment(session, payment.id)


def test_expected_cash_with_change_not_double_counted(session, cash, make_product):
    make_product(sku="A1", sale_price=D("50.00"), current_stock=D("5"))
    _add(session, "A1")
    _pay(session, PIX, "10")
    _pay(session, CASH, "30")
    _pay(session, CASH, "30")
    sale = complete_sale(session, now=NOW)
    assert sale.change_given == D("20.00")
    assert expected_cash(session, cash.id) == D("140.00")


def test_concurrent_complete_deducts_once(engine, session, cash, make_product):
    p1 = make_product(sku="A1", sale_price=D("5.00"), current_stock=D("10"))
    p2 = make_product(sku="A2", sale_price=D("3.00"), current_stock=D("10"))
    _add(session, "A1", "2")
    _add(session, "A2", "1")
    _pay(session, CASH, "13")
    sale_id = current_sale(session).id
    session.commit()
    barrier = threading.Barrier(2)
    results: list[str] = []

    def worker() -> None:
        with Session(engine) as s:
            barrier.wait()
            try:
                complete_sale(s, now=NOW, sale_id=sale_id)
                s.commit()
                results.append("ok")
            except InvalidTransition:
                s.rollback()
                results.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "ok"]
    session.rollback()
    movements = _sale_movements(session)
    assert sorted(m.product_id for m in movements) == sorted([p1.id, p2.id])
    session.refresh(p1)
    session.refresh(p2)
    assert (p1.current_stock, p2.current_stock) == (D("8"), D("9"))
