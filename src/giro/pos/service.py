"""Turno de caixa: abertura, sangria, suprimento e dinheiro esperado. Sem commit aqui."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from giro.errors import InvalidInput, NoOpenSession, SessionAlreadyOpen
from giro.fmt import brl
from giro.models import (
    CashMovement,
    CashMovementKind,
    CashSession,
    PaymentMethod,
    Sale,
    SalePayment,
    SaleStatus,
)
from giro.pos.schemas import CashMovementIn, OpenSessionIn

ALREADY_OPEN = "Já existe um caixa aberto."
CLOSED = "O caixa está fechado. Abra o caixa para continuar."


def current_session(s: Session) -> CashSession | None:
    return s.scalar(select(CashSession).where(CashSession.closed_at.is_(None)))


def open_session(s: Session, data: OpenSessionIn, *, now: datetime) -> CashSession:
    if current_session(s) is not None:
        raise SessionAlreadyOpen(ALREADY_OPEN)
    session = CashSession(opened_at=now, opening_amount=data.opening_amount)
    try:
        with s.begin_nested():
            s.add(session)
            s.flush()
    except IntegrityError as exc:
        raise SessionAlreadyOpen(ALREADY_OPEN) from exc
    return session


def require_session(s: Session, *, lock: bool = False) -> CashSession:
    stmt = select(CashSession).where(CashSession.closed_at.is_(None))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    session = s.scalar(stmt)
    if session is None:
        raise NoOpenSession(CLOSED)
    return session


def _movements_total(s: Session, session_id: int, kind: CashMovementKind) -> Decimal:
    return s.scalar(
        select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
            CashMovement.session_id == session_id, CashMovement.kind == kind
        )
    ) or Decimal(0)


def expected_cash(s: Session, session_id: int) -> Decimal:
    """Abertura + suprimentos - sangrias + (dinheiro recebido - troco) das vendas concluídas."""
    opening = s.scalar(select(CashSession.opening_amount).where(CashSession.id == session_id))
    if opening is None:
        raise InvalidInput("Turno de caixa não encontrado.")
    supplies = _movements_total(s, session_id, CashMovementKind.SUPPLY)
    withdrawals = _movements_total(s, session_id, CashMovementKind.WITHDRAWAL)
    cash_in = s.scalar(
        select(func.coalesce(func.sum(SalePayment.amount), 0))
        .join(Sale, Sale.id == SalePayment.sale_id)
        .where(
            Sale.session_id == session_id,
            Sale.status == SaleStatus.COMPLETED,
            SalePayment.method == PaymentMethod.CASH,
        )
    ) or Decimal(0)
    change = s.scalar(
        select(func.coalesce(func.sum(Sale.change_given), 0)).where(
            Sale.session_id == session_id, Sale.status == SaleStatus.COMPLETED
        )
    ) or Decimal(0)
    return Decimal(opening + supplies - withdrawals + cash_in - change).quantize(Decimal("0.01"))


def add_cash_movement(s: Session, data: CashMovementIn, *, now: datetime) -> CashMovement:
    session = require_session(s, lock=True)
    if data.kind is CashMovementKind.WITHDRAWAL:
        expected = expected_cash(s, session.id)
        if data.amount > expected:
            raise InvalidInput(f"Sangria maior que o dinheiro esperado no caixa ({brl(expected)}).")
    movement = CashMovement(
        session_id=session.id, kind=data.kind, amount=data.amount, note=data.note, occurred_at=now
    )
    s.add(movement)
    s.flush()
    return movement
