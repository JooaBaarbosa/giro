"""Tela de pedidos: aprovação, edição de itens, reenvio e recebimento.

Toda ação responde com o card do pedido (HTMX troca o card inteiro). Erros de domínio
voltam como 200 com a mensagem no card, porque o HTMX não troca conteúdo em respostas 4xx.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from giro.api.deps import NowDep, SessionDep, SettingsDep
from giro.config import Settings
from giro.errors import DomainError
from giro.models import OrderStatus, PurchaseOrder
from giro.notifier import notify_order_approved
from giro.orders import service
from giro.orders.schemas import ItemQtyIn
from giro.web.templating import form_errors, parse_decimal, render

router = APIRouter()

TABS: list[tuple[str, str]] = [
    ("", "Todos"),
    (OrderStatus.SUGGESTED.value, "Sugeridos"),
    (OrderStatus.APPROVED.value, "Aprovados"),
    (OrderStatus.SENT.value, "Enviados"),
    (OrderStatus.FAILED.value, "Com falha"),
    (OrderStatus.RECEIVED.value, "Recebidos"),
    (OrderStatus.CANCELLED.value, "Cancelados"),
]


def _card(
    request: Request,
    s: Session,
    settings: Settings,
    order_id: int,
    *,
    error: str | None = None,
    item_errors: dict[int, str] | None = None,
    notify_failed: bool = False,
) -> HTMLResponse:
    order = service.get_order(s, order_id)
    return render(
        request,
        "_order_card.html",
        o=service.to_out(order, settings=settings),
        error=error,
        item_errors=item_errors or {},
        notify_failed=notify_failed,
    )


def _act(
    request: Request,
    s: Session,
    settings: Settings,
    order_id: int,
    action: Callable[[], PurchaseOrder],
    *,
    notify: bool = False,
) -> HTMLResponse:
    try:
        action()
        s.commit()
    except DomainError as exc:
        s.rollback()
        return _card(request, s, settings, order_id, error=exc.message)
    notify_failed = notify and not notify_order_approved(order_id, settings)
    return _card(request, s, settings, order_id, notify_failed=notify_failed)


@router.get("/orders", response_class=HTMLResponse)
def orders_page(
    request: Request,
    s: SessionDep,
    settings: SettingsDep,
    status: Annotated[str, Query()] = "",
) -> HTMLResponse:
    selected = OrderStatus(status) if status in OrderStatus._value2member_map_ else None
    orders = [service.to_out(o, settings=settings) for o in service.list_orders(s, selected)]
    return render(
        request,
        "orders.html",
        orders=orders,
        tabs=TABS,
        current=selected.value if selected else "",
        nav="orders",
    )


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail(
    request: Request, order_id: int, s: SessionDep, settings: SettingsDep
) -> HTMLResponse:
    order = service.get_order(s, order_id)
    context: dict[str, Any] = {
        "o": service.to_out(order, settings=settings),
        "error": None,
        "item_errors": {},
        "notify_failed": False,
        "nav": "orders",
    }
    return render(request, "order_detail.html", **context)


@router.post("/orders/{order_id}/approve", response_class=HTMLResponse)
def order_approve(
    request: Request, order_id: int, s: SessionDep, settings: SettingsDep, now: NowDep
) -> HTMLResponse:
    return _act(
        request, s, settings, order_id, lambda: service.approve(s, order_id, now=now), notify=True
    )


@router.post("/orders/{order_id}/retry", response_class=HTMLResponse)
def order_retry(
    request: Request, order_id: int, s: SessionDep, settings: SettingsDep, now: NowDep
) -> HTMLResponse:
    return _act(
        request, s, settings, order_id, lambda: service.retry(s, order_id, now=now), notify=True
    )


@router.post("/orders/{order_id}/cancel", response_class=HTMLResponse)
def order_cancel(
    request: Request, order_id: int, s: SessionDep, settings: SettingsDep
) -> HTMLResponse:
    return _act(request, s, settings, order_id, lambda: service.cancel(s, order_id))


@router.post("/orders/{order_id}/receive", response_class=HTMLResponse)
def order_receive(
    request: Request, order_id: int, s: SessionDep, settings: SettingsDep, now: NowDep
) -> HTMLResponse:
    return _act(request, s, settings, order_id, lambda: service.receive(s, order_id, now=now))


@router.post("/orders/{order_id}/items/{item_id}", response_class=HTMLResponse)
def order_item_update(
    request: Request,
    order_id: int,
    item_id: int,
    s: SessionDep,
    settings: SettingsDep,
    quantity: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        data = ItemQtyIn.model_validate({"quantity": parse_decimal(quantity)})
    except ValidationError as exc:
        message = form_errors(exc).get("quantity", "Quantidade inválida.")
        return _card(request, s, settings, order_id, item_errors={item_id: message})
    return _act(
        request,
        s,
        settings,
        order_id,
        lambda: service.set_item_quantity(s, order_id, item_id, data.quantity),
    )


@router.post("/orders/{order_id}/items/{item_id}/delete", response_class=HTMLResponse)
def order_item_delete(
    request: Request, order_id: int, item_id: int, s: SessionDep, settings: SettingsDep
) -> HTMLResponse:
    return _act(
        request, s, settings, order_id, lambda: service.remove_item(s, order_id, item_id)
    )
