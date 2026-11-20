"""Turno de caixa e carrinho (venda em aberto). Sem commit aqui."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from giro.errors import (
    InsufficientStock,
    InvalidInput,
    InvalidTransition,
    NoOpenSession,
    NotFound,
    SessionAlreadyOpen,
    Shortage,
)
from giro.fmt import brl
from giro.inventory.schemas import MovementIn
from giro.inventory.service import (
    _lock_product,
    ensure_quantity_fits_unit,
    find_product_by_code,
    record_movement,
)
from giro.models import (
    CashMovement,
    CashMovementKind,
    CashSession,
    MovementKind,
    PaymentMethod,
    Sale,
    SaleItem,
    SalePayment,
    SaleStatus,
    StockMovement,
    Unit,
)
from giro.pos.money import compute_change, line_total, validate_new_payment
from giro.pos.schemas import (
    AddItemIn,
    AdjustShortageIn,
    CancelSaleIn,
    CashMovementIn,
    CloseSessionIn,
    ItemQtyIn,
    OpenSessionIn,
    PaymentIn,
)
from giro.pos.state import ensure_transition

ALREADY_OPEN = "Já existe um caixa aberto."
NOT_SHORT = "Este item não está mais em falta."
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


NO_OPEN_SALE = "Não há venda em andamento."


def _open_sale_locked(s: Session) -> Sale:
    """Venda em aberto relida; o chamador já travou o turno (serializa o carrinho)."""
    sale = current_sale(s, fresh=True)
    if sale is None:
        raise InvalidInput(NO_OPEN_SALE)
    s.expire(sale, ["payments"])
    return sale


def add_payment(s: Session, data: PaymentIn) -> SalePayment:
    require_session(s, lock=True)
    sale = _open_sale_locked(s)
    paid = [(p.method, p.amount) for p in sale.payments]
    validate_new_payment(sale.total, paid, data.method, data.amount)
    payment = SalePayment(sale=sale, method=data.method, amount=data.amount)
    s.add(payment)
    s.flush()
    return payment


def remove_payment(s: Session, payment_id: int) -> None:
    require_session(s, lock=True)
    sale = _open_sale_locked(s)
    payment = next((p for p in sale.payments if p.id == payment_id), None)
    if payment is None:
        raise NotFound("Pagamento não encontrado.")
    sale.payments.remove(payment)
    s.flush()


def _lock_sale(s: Session, sale_id: int | None) -> Sale:
    if sale_id is None:
        current = current_sale(s)
        if current is None:
            raise InvalidInput(NO_OPEN_SALE)
        sale_id = current.id
    sale = s.scalar(
        select(Sale)
        .where(Sale.id == sale_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if sale is None:
        raise NotFound("Venda não encontrada.")
    s.expire(sale, ["items", "payments"])
    return sale


def _needed_by_product(sale: Sale) -> dict[int, Decimal]:
    needed: dict[int, Decimal] = {}
    for item in sale.items:
        needed[item.product_id] = needed.get(item.product_id, Decimal(0)) + item.quantity
    return needed


def complete_sale(s: Session, *, now: datetime, sale_id: int | None = None) -> Sale:
    """Conclui a venda e baixa o estoque. Em falta, levanta InsufficientStock sem gravar nada."""
    require_session(s, lock=True)
    sale = _lock_sale(s, sale_id)
    ensure_transition(sale.status, SaleStatus.COMPLETED)
    if not sale.items:
        raise InvalidInput("Adicione ao menos um item antes de concluir.")
    change = compute_change(sale.total, [(p.method, p.amount) for p in sale.payments])

    needed = _needed_by_product(sale)
    shortages: list[Shortage] = []
    for product_id in sorted(needed):
        product = _lock_product(s, product_id)
        if needed[product_id] > product.current_stock:
            shortages.append(
                Shortage(product_id, product.name, product.current_stock, needed[product_id])
            )
    if shortages:
        raise InsufficientStock("Estoque insuficiente para concluir a venda.", shortages)

    for product_id in sorted(needed):
        record_movement(
            s,
            MovementIn(
                product_id=product_id,
                kind=MovementKind.SALE,
                quantity=needed[product_id],
                occurred_at=now,
            ),
            sale_id=sale.id,
        )
    sale.change_given = change
    sale.status = SaleStatus.COMPLETED
    sale.completed_at = now
    s.flush()
    return sale


def adjust_for_shortage(s: Session, data: AdjustShortageIn, *, now: datetime) -> StockMovement:
    require_session(s, lock=True)
    sale = _open_sale_locked(s)
    needed = _needed_by_product(sale).get(data.product_id)
    if needed is None:
        raise InvalidInput(NOT_SHORT)
    product = _lock_product(s, data.product_id)
    missing = needed - product.current_stock
    if missing <= 0:
        raise InvalidInput(NOT_SHORT)
    return record_movement(
        s,
        MovementIn(
            product_id=product.id,
            kind=MovementKind.ADJUSTMENT,
            quantity=missing,
            occurred_at=now,
            note=f"Ajuste no caixa: {data.reason}",
        ),
    )


CLOSED_SESSION_SALE = "Não é possível cancelar venda de um caixa já fechado."
OPEN_SALE_BLOCKS_CLOSE = "Conclua ou descarte a venda em andamento antes de fechar o caixa."


def cancel_sale(s: Session, sale_id: int, data: CancelSaleIn, *, now: datetime) -> Sale:
    """Cancela venda concluída do turno aberto e devolve o estoque."""
    try:
        session = require_session(s, lock=True)
    except NoOpenSession:
        session = None
    sale = _lock_sale(s, sale_id)
    if session is None or sale.session_id != session.id:
        raise InvalidTransition(CLOSED_SESSION_SALE)
    ensure_transition(sale.status, SaleStatus.CANCELLED)
    for product_id, quantity in sorted(_needed_by_product(sale).items()):
        record_movement(
            s,
            MovementIn(
                product_id=product_id,
                kind=MovementKind.SALE_CANCEL,
                quantity=quantity,
                occurred_at=now,
                note=f"Cancelamento de venda: {data.reason}",
            ),
            sale_id=sale.id,
        )
    sale.status = SaleStatus.CANCELLED
    sale.cancelled_at = now
    sale.cancel_reason = data.reason
    s.flush()
    return sale


@dataclass(frozen=True)
class SessionSummary:
    by_method: dict[PaymentMethod, Decimal]
    sales_count: int
    sales_total: Decimal
    cancelled_count: int
    supplies: Decimal
    withdrawals: Decimal
    expected_cash: Decimal


def _money(value: Decimal | int) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def session_summary(s: Session, session_id: int) -> SessionSummary:
    expected = expected_cash(s, session_id)  # também valida que o turno existe
    by_method = {method: Decimal(0) for method in PaymentMethod}
    rows = s.execute(
        select(SalePayment.method, func.sum(SalePayment.amount))
        .join(Sale, Sale.id == SalePayment.sale_id)
        .where(Sale.session_id == session_id, Sale.status == SaleStatus.COMPLETED)
        .group_by(SalePayment.method)
    )
    for method, total in rows:
        by_method[method] = Decimal(total)
    change = s.scalar(
        select(func.coalesce(func.sum(Sale.change_given), 0)).where(
            Sale.session_id == session_id, Sale.status == SaleStatus.COMPLETED
        )
    ) or Decimal(0)
    by_method[PaymentMethod.CASH] -= Decimal(change)
    sales_count, sales_total = s.execute(
        select(func.count(Sale.id), func.coalesce(func.sum(Sale.total), 0)).where(
            Sale.session_id == session_id, Sale.status == SaleStatus.COMPLETED
        )
    ).one()
    cancelled_count = s.scalar(
        select(func.count(Sale.id)).where(
            Sale.session_id == session_id, Sale.status == SaleStatus.CANCELLED
        )
    )
    return SessionSummary(
        by_method={m: _money(v) for m, v in by_method.items()},
        sales_count=sales_count,
        sales_total=_money(sales_total),
        cancelled_count=cancelled_count or 0,
        supplies=_money(_movements_total(s, session_id, CashMovementKind.SUPPLY)),
        withdrawals=_money(_movements_total(s, session_id, CashMovementKind.WITHDRAWAL)),
        expected_cash=expected,
    )


def close_session(s: Session, data: CloseSessionIn, *, now: datetime) -> CashSession:
    session = require_session(s, lock=True)
    if current_sale(s) is not None:
        raise InvalidTransition(OPEN_SALE_BLOCKS_CLOSE)
    expected = expected_cash(s, session.id)
    session.expected_amount = expected
    session.counted_amount = data.counted_amount
    session.difference = (data.counted_amount - expected).quantize(Decimal("0.01"))
    session.close_note = data.note
    session.closed_at = now
    s.flush()
    return session


def get_sale(s: Session, sale_id: int) -> Sale:
    sale = s.get(Sale, sale_id)
    if sale is None:
        raise NotFound("Venda não encontrada.")
    return sale


def get_session(s: Session, session_id: int) -> CashSession:
    session = s.get(CashSession, session_id)
    if session is None:
        raise NotFound("Turno de caixa não encontrado.")
    return session


def list_sessions(s: Session, *, limit: int = 50) -> list[CashSession]:
    stmt = select(CashSession).order_by(CashSession.opened_at.desc(), CashSession.id.desc())
    return list(s.scalars(stmt.limit(limit)))


def list_session_sales(s: Session, session_id: int) -> list[Sale]:
    stmt = (
        select(Sale)
        .where(
            Sale.session_id == session_id,
            Sale.status.in_([SaleStatus.COMPLETED, SaleStatus.CANCELLED]),
        )
        .order_by(Sale.created_at.desc(), Sale.id.desc())
    )
    return list(s.scalars(stmt))
