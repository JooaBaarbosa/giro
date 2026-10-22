"""Ciclo de vida do pedido de compra. Nenhuma função faz commit: quem chama decide."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from giro.config import Settings
from giro.errors import EmptyOrder, InvalidInput, InvalidTransition, NotFound
from giro.inventory.schemas import MovementIn
from giro.inventory.service import ensure_quantity_fits_unit, record_movement
from giro.models import (
    MovementKind,
    OrderStatus,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)
from giro.orders.message import (
    format_items_line,
    owner_alert_params,
    render_supplier_message,
    supplier_template_params,
)
from giro.orders.schemas import (
    FailedIn,
    OrderItemOut,
    OrderOut,
    SentIn,
    SuggestedOrderOut,
    SupplierRef,
)
from giro.orders.state import OPEN_STATUSES, ensure_transition
from giro.reports import forecast_for

log = logging.getLogger(__name__)

SENDING_TIMEOUT = timedelta(minutes=10)
TIMEOUT_ERROR = "envio não confirmado; verifique antes de reenviar"
SUGGEST_LOCK_KEY = 74_201  # pg_advisory_xact_lock: serializa sugestões simultâneas
MAX_ERROR_LEN = 2000


def get_order(s: Session, order_id: int) -> PurchaseOrder:
    order = s.get(PurchaseOrder, order_id)
    if order is None:
        raise NotFound("Pedido não encontrado.")
    return order


def list_orders(s: Session, status: OrderStatus | None = None) -> list[PurchaseOrder]:
    stmt = select(PurchaseOrder).order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
    if status is not None:
        stmt = stmt.where(PurchaseOrder.status == status)
    return list(s.scalars(stmt))


def _fail_stuck_sending(s: Session, *, now: datetime) -> None:
    stuck = s.scalars(
        update(PurchaseOrder)
        .where(
            PurchaseOrder.status == OrderStatus.SENDING,
            PurchaseOrder.claimed_at < now - SENDING_TIMEOUT,
        )
        .values(status=OrderStatus.FAILED, error=TIMEOUT_ERROR)
        .returning(PurchaseOrder.id)
    ).all()
    for order_id in stuck:
        log.warning("pedido preso em envio marcado como falhou", extra={"order_id": order_id})


def suggest_orders(s: Session, *, now: datetime, tz: str) -> list[PurchaseOrder]:
    s.execute(select(func.pg_advisory_xact_lock(SUGGEST_LOCK_KEY)))
    _fail_stuck_sending(s, now=now)

    in_open_order = (
        select(PurchaseOrderItem.product_id)
        .join(PurchaseOrder)
        .where(PurchaseOrder.status.in_(OPEN_STATUSES))
    )
    candidates = s.scalars(
        select(Product)
        .join(Supplier)
        .where(
            Product.active,
            Supplier.active,
            Product.current_stock < Product.min_stock,
            Product.id.not_in(in_open_order),
        )
        .order_by(Product.supplier_id, Product.name)
    ).all()

    by_supplier: dict[int, list[Product]] = defaultdict(list)
    for product in candidates:
        by_supplier[product.supplier_id].append(product)

    created: list[PurchaseOrder] = []
    for supplier_id, products in by_supplier.items():
        order = PurchaseOrder(supplier_id=supplier_id, status=OrderStatus.SUGGESTED)
        order.items = [
            PurchaseOrderItem(product=p, quantity=forecast_for(s, p, now=now, tz=tz).suggested_qty)
            for p in products
        ]
        s.add(order)
        created.append(order)
    s.flush()
    for order in created:
        log.info("pedido sugerido", extra={"order_id": order.id})
    return created


def _get_item(order: PurchaseOrder, item_id: int) -> PurchaseOrderItem:
    for item in order.items:
        if item.id == item_id:
            return item
    raise NotFound("Item não encontrado neste pedido.")


def _ensure_editable(order: PurchaseOrder) -> None:
    if order.status is not OrderStatus.SUGGESTED:
        raise InvalidTransition("Itens só podem ser alterados em pedidos sugeridos.")


def set_item_quantity(s: Session, order_id: int, item_id: int, quantity: Decimal) -> PurchaseOrder:
    order = get_order(s, order_id)
    _ensure_editable(order)
    item = _get_item(order, item_id)
    if quantity <= 0:
        raise InvalidInput("Quantidade deve ser maior que zero.")
    ensure_quantity_fits_unit(quantity, item.product.unit)
    item.quantity = quantity
    s.flush()
    return order


def remove_item(s: Session, order_id: int, item_id: int) -> PurchaseOrder:
    order = get_order(s, order_id)
    _ensure_editable(order)
    order.items.remove(_get_item(order, item_id))
    s.flush()
    return order


def approve(s: Session, order_id: int, *, now: datetime) -> PurchaseOrder:
    order = get_order(s, order_id)
    ensure_transition(order.status, OrderStatus.APPROVED)
    if not order.items:
        raise EmptyOrder("Pedido sem itens não pode ser aprovado.")
    order.status = OrderStatus.APPROVED
    order.approved_at = now
    s.flush()
    log.info("pedido aprovado", extra={"order_id": order.id})
    return order


def cancel(s: Session, order_id: int) -> PurchaseOrder:
    order = get_order(s, order_id)
    ensure_transition(order.status, OrderStatus.CANCELLED)
    order.status = OrderStatus.CANCELLED
    s.flush()
    log.info("pedido cancelado", extra={"order_id": order.id})
    return order


def retry(s: Session, order_id: int, *, now: datetime) -> PurchaseOrder:
    order = get_order(s, order_id)
    if order.status is not OrderStatus.FAILED:
        raise InvalidTransition("Só pedidos que falharam podem ser reenviados.")
    order.status = OrderStatus.APPROVED
    order.error = None
    order.claimed_at = None
    order.approved_at = now
    s.flush()
    log.info("pedido reenviado", extra={"order_id": order.id})
    return order


def claim(s: Session, order_id: int, *, now: datetime) -> PurchaseOrder:
    """Transição atômica approved → sending: só um disparo concorrente vence."""
    claimed = s.scalar(
        update(PurchaseOrder)
        .where(PurchaseOrder.id == order_id, PurchaseOrder.status == OrderStatus.APPROVED)
        .values(status=OrderStatus.SENDING, claimed_at=now)
        .returning(PurchaseOrder.id)
    )
    if claimed is None:
        order = get_order(s, order_id)
        ensure_transition(order.status, OrderStatus.SENDING)
        raise InvalidTransition("Pedido já está sendo enviado.")
    order = s.execute(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == order_id)
        .execution_options(populate_existing=True)
    ).scalar_one()
    log.info("pedido em envio", extra={"order_id": order.id})
    return order


def mark_sent(s: Session, order_id: int, data: SentIn, *, now: datetime) -> PurchaseOrder:
    order = get_order(s, order_id)
    ensure_transition(order.status, OrderStatus.SENT)
    order.status = OrderStatus.SENT
    order.message_text = data.message_text
    order.dry_run = data.dry_run
    order.whatsapp_message_id = data.whatsapp_message_id
    order.error = None
    order.sent_at = now
    s.flush()
    log.info("pedido enviado", extra={"order_id": order.id, "dry_run": data.dry_run})
    return order


def mark_failed(s: Session, order_id: int, data: FailedIn) -> PurchaseOrder:
    order = get_order(s, order_id)
    ensure_transition(order.status, OrderStatus.FAILED)
    order.status = OrderStatus.FAILED
    order.error = data.error[:MAX_ERROR_LEN]
    s.flush()
    log.warning("falha no envio do pedido", extra={"order_id": order.id, "error": order.error})
    return order


def receive(s: Session, order_id: int, *, now: datetime) -> PurchaseOrder:
    order = get_order(s, order_id)
    ensure_transition(order.status, OrderStatus.RECEIVED)
    for item in order.items:
        record_movement(
            s,
            MovementIn(
                product_id=item.product_id,
                kind=MovementKind.PURCHASE,
                quantity=item.quantity,
                occurred_at=now,
                note=f"Pedido #{order.id}",
            ),
            purchase_order_id=order.id,
        )
    order.status = OrderStatus.RECEIVED
    order.received_at = now
    s.flush()
    log.info("pedido recebido", extra={"order_id": order.id})
    return order


def _items_line(order: PurchaseOrder) -> str:
    return format_items_line([(i.product.name, i.quantity, i.product.unit) for i in order.items])


def to_out(order: PurchaseOrder, *, settings: Settings) -> OrderOut:
    params = supplier_template_params(
        supplier_name=order.supplier.name,
        store_name=settings.store_name,
        items_line=_items_line(order),
    )
    return OrderOut(
        id=order.id,
        status=order.status,
        supplier=SupplierRef(
            id=order.supplier.id,
            name=order.supplier.name,
            whatsapp=order.supplier.whatsapp,
            whatsapp_digits=order.supplier.whatsapp.lstrip("+"),
        ),
        items=[
            OrderItemOut(
                id=i.id,
                product_id=i.product_id,
                name=i.product.name,
                unit=i.product.unit,
                quantity=i.quantity,
            )
            for i in order.items
        ],
        message_preview=render_supplier_message(params),
        template_params=params,
        dry_run=order.dry_run,
        message_text=order.message_text,
        whatsapp_message_id=order.whatsapp_message_id,
        error=order.error,
        created_at=order.created_at,
        approved_at=order.approved_at,
        claimed_at=order.claimed_at,
        sent_at=order.sent_at,
        received_at=order.received_at,
        link=f"{settings.app_base_url.rstrip('/')}/orders/{order.id}",
    )


def to_suggested_out(order: PurchaseOrder, *, settings: Settings) -> SuggestedOrderOut:
    out = to_out(order, settings=settings)
    alert = owner_alert_params(
        count=len(order.items),
        names=[i.product.name for i in order.items],
        supplier_name=order.supplier.name,
        link=out.link,
    )
    return SuggestedOrderOut(**out.model_dump(), alert_params=alert)
