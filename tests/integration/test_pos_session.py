from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from giro.errors import InvalidInput, NoOpenSession, SessionAlreadyOpen
from giro.models import (
    CashMovementKind,
    PaymentMethod,
    Sale,
    SalePayment,
    SaleStatus,
)
from giro.pos.schemas import CashMovementIn, OpenSessionIn, PaymentIn
from giro.pos.service import (
    add_cash_movement,
    current_session,
    expected_cash,
    open_session,
    require_session,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
D = Decimal
P = PaymentMethod


def _open(s: Session, amount: str = "100"):
    return open_session(s, OpenSessionIn(opening_amount=D(amount)), now=NOW)


def _move(s: Session, kind: CashMovementKind, amount: str):
    return add_cash_movement(s, CashMovementIn(kind=kind, amount=D(amount), note="x"), now=NOW)


def test_open_session(session: Session):
    assert current_session(session) is None
    cs = _open(session)
    assert cs.opened_at == NOW
    assert cs.opening_amount == D("100")
    assert current_session(session) is cs
    assert require_session(session, lock=True).id == cs.id


def test_open_twice(session: Session):
    _open(session)
    with pytest.raises(SessionAlreadyOpen) as exc:
        _open(session)
    assert exc.value.message == "Já existe um caixa aberto."


def test_open_integrity_error_becomes_already_open(session: Session, monkeypatch):
    _open(session)
    monkeypatch.setattr("giro.pos.service.current_session", lambda s: None)
    with pytest.raises(SessionAlreadyOpen):
        _open(session)
    assert session.is_active


def test_without_session(session: Session):
    with pytest.raises(NoOpenSession) as exc:
        require_session(session)
    assert exc.value.message == "O caixa está fechado. Abra o caixa para continuar."
    with pytest.raises(NoOpenSession):
        require_session(session, lock=True)
    with pytest.raises(NoOpenSession):
        _move(session, CashMovementKind.SUPPLY, "10")


def test_expected_cash_movements(session: Session):
    cs = _open(session)
    m = _move(session, CashMovementKind.SUPPLY, "50")
    assert m.occurred_at == NOW
    _move(session, CashMovementKind.WITHDRAWAL, "30")
    assert expected_cash(session, cs.id) == D("120.00")


def test_withdrawal_above_expected(session: Session):
    cs = _open(session)
    _move(session, CashMovementKind.SUPPLY, "50")
    _move(session, CashMovementKind.WITHDRAWAL, "30")
    with pytest.raises(InvalidInput) as exc:
        _move(session, CashMovementKind.WITHDRAWAL, "200")
    assert exc.value.message == "Sangria maior que o dinheiro esperado no caixa (R$ 120,00)."
    _move(session, CashMovementKind.WITHDRAWAL, "120")
    assert expected_cash(session, cs.id) == D("0.00")


def _sale(session, cs, status, total, payments, change="0"):
    sale = Sale(session_id=cs.id, status=status, total=D(total), change_given=D(change))
    sale.payments = [SalePayment(method=m, amount=D(a)) for m, a in payments]
    session.add(sale)
    session.flush()


def test_expected_cash_with_sales(session: Session):
    cs = _open(session)
    # concluida: 50 em dinheiro, troco 10 => +40
    _sale(session, cs, SaleStatus.COMPLETED, "40", [(P.CASH, "50")], change="10")
    # concluida mista: so o dinheiro conta => +10
    _sale(session, cs, SaleStatus.COMPLETED, "30", [(P.CASH, "10"), (P.PIX, "20")])
    # cancelada e em andamento nao contam
    _sale(session, cs, SaleStatus.CANCELLED, "99", [(P.CASH, "99")])
    _sale(session, cs, SaleStatus.OPEN, "5", [])
    assert expected_cash(session, cs.id) == D("150.00")


def test_expected_cash_is_per_session(session: Session):
    cs = _open(session)
    cs.closed_at = NOW
    session.flush()
    _sale(session, cs, SaleStatus.COMPLETED, "10", [(P.CASH, "10")])
    cs2 = _open(session, "5")
    assert expected_cash(session, cs2.id) == D("5.00")
    assert expected_cash(session, cs.id) == D("110.00")


def test_schemas_validation():
    with pytest.raises(ValidationError):
        OpenSessionIn(opening_amount=D("-1"))
    with pytest.raises(ValidationError):
        CashMovementIn(kind=CashMovementKind.SUPPLY, amount=D("0"))
    assert PaymentIn(method=P.CASH, amount=D("0")).amount == 0
