"""Turno de caixa e carrinho (venda em aberto). Sem commit aqui."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from giro.errors import (
    InvalidInput,
    InvalidTransition,
    NoOpenSession,
    NotFound,
    SessionAlreadyOpen,
)
from giro.fmt import brl
from giro.inventory.service import ensure_quantity_fits_unit, find_product_by_code
from giro.models import (
    CashMovement,
    CashMovementKind,
    CashSession,
    PaymentMethod,
    Sale,
    SaleItem,
    SalePayment,
    SaleStatus,
    Unit,
)
from giro.pos.money import line_total
from giro.pos.schemas import AddItemIn, CashMovementIn, ItemQtyIn, OpenSessionIn

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


def current_sale(s: Session, *, fresh: bool = False) -> Sale | None:
    stmt = select(Sale).where(Sale.status == SaleStatus.OPEN)
    if fresh:
        stmt = stmt.execution_options(populate_existing=True)
    sale = s.scalar(stmt)
    if sale is not None and fresh:
        s.expire(sale, ["items"])
    return sale


def _recalc_total(sale: Sale) -> None:
    sale.total = sum((item.line_total for item in sale.items), Decimal(0))


def _new_sale(s: Session, session_id: int, now: datetime) -> Sale:
    sale = Sale(session_id=session_id, created_at=now)
    try:
        with s.begin_nested():
            s.add(sale)
            s.flush()
    except IntegrityError:
        existing = current_sale(s, fresh=True)
        if existing is None:
            raise
        return existing
    return sale


def add_item(s: Session, data: AddItemIn, *, now: datetime) -> SaleItem:
    session = require_session(s, lock=True)
    product = find_product_by_code(s, data.code)
    if product.sale_price is None:
        raise InvalidInput("Produto sem preço de venda: cadastre o preço antes de vender.")
    quantity = data.quantity
    if quantity is None:
        if product.unit is Unit.KG:
            raise InvalidInput(f"Informe o peso para {product.name}.")
        quantity = Decimal(1)
    ensure_quantity_fits_unit(quantity, product.unit)
    sale = current_sale(s, fresh=True)
    if sale is None:
        sale = _new_sale(s, session.id, now)
    item = next((i for i in sale.items if i.product_id == product.id), None)
    if item is None:
        item = SaleItem(
            sale=sale,
            product=product,
            quantity=quantity,
            unit_price=product.sale_price,
            unit_cost=product.cost_price,
            line_total=line_total(quantity, product.sale_price),
        )
        s.add(item)
    else:
        merged = item.quantity + quantity
        ensure_quantity_fits_unit(merged, product.unit)
        item.quantity = merged
        item.line_total = line_total(merged, item.unit_price)
    s.flush()
    _recalc_total(sale)
    s.flush()
    return item


def _open_sale_item(s: Session, item_id: int) -> tuple[Sale, SaleItem]:
    require_session(s, lock=True)
    item = s.scalar(
        select(SaleItem).where(SaleItem.id == item_id).execution_options(populate_existing=True)
    )
    if item is None:
        raise NotFound("Item não encontrado.")
    sale = item.sale
    s.refresh(sale)
    s.expire(sale, ["items"])
    if sale.status is not SaleStatus.OPEN:
        raise InvalidTransition("A venda não está em aberto.")
    return sale, item


def set_item_quantity(s: Session, item_id: int, data: ItemQtyIn) -> SaleItem:
    sale, item = _open_sale_item(s, item_id)
    ensure_quantity_fits_unit(data.quantity, item.product.unit)
    item.quantity = data.quantity
    item.line_total = line_total(data.quantity, item.unit_price)
    _recalc_total(sale)
    s.flush()
    return item


def remove_item(s: Session, item_id: int) -> None:
    sale, item = _open_sale_item(s, item_id)
    sale.items.remove(item)
    s.flush()
    _recalc_total(sale)
    s.flush()


def discard_sale(s: Session) -> None:
    require_session(s, lock=True)
    sale = current_sale(s, fresh=True)
    if sale is not None:
        s.delete(sale)
        s.flush()


def over_stock(item: SaleItem) -> bool:
    return item.quantity > item.product.current_stock
