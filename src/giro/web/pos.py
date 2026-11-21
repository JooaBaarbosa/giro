"""Tela do PDV (/caixa): abrir caixa, carrinho, pagamentos, conclusão e ajuste de estoque.

Toda ação HTMX confirma no sucesso e responde 200 com o painel inteiro (`_pos_panel.html`).
Erros de domínio voltam dentro do painel, depois de um rollback, para que ele mostre o banco.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from giro.api.deps import NowDep, SessionDep
from giro.errors import DomainError, InsufficientStock, InvalidInput, Shortage
from giro.fmt import brl
from giro.inventory.service import find_product_by_code
from giro.models import CashMovementKind, PaymentMethod, Sale, SaleStatus, Unit
from giro.pos import service
from giro.pos.codes import parse_entry
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
from giro.web.templating import form_errors, parse_decimal, render

router = APIRouter()

MOVEMENT_LABELS = {CashMovementKind.WITHDRAWAL: "Sangria", CashMovementKind.SUPPLY: "Suprimento"}


def _money_input(value: Decimal) -> str:
    return f"{value:.2f}".replace(".", ",")


def _first_error(exc: ValidationError, default: str) -> str:
    errors = form_errors(exc)
    return next(iter(errors.values()), default)


def _context(
    s: Session,
    *,
    error: str | None = None,
    notice: str | None = None,
    done: str | None = None,
    shortages: list[Shortage] | None = None,
    weight_code: str | None = None,
    last_item_id: int | None = None,
) -> dict[str, Any]:
    cash_session = service.current_session(s)
    sale: Sale | None = service.current_sale(s, fresh=True) if cash_session else None
    items = list(sale.items) if sale else []
    payments = list(sale.payments) if sale else []
    total = sale.total if sale else Decimal(0)
    paid = sum((p.amount for p in payments), Decimal(0))
    cash_paid = sum((p.amount for p in payments if p.method is PaymentMethod.CASH), Decimal(0))
    excess = paid - total
    remaining = max(total - paid, Decimal(0))
    last_item = next((i for i in items if i.id == last_item_id), items[-1] if items else None)
    return dict(
        cash_session=cash_session,
        sale=sale,
        items=items,
        payments=payments,
        total=total,
        paid=paid,
        remaining=remaining,
        change=excess if 0 < excess <= cash_paid else Decimal(0),
        pay_default=_money_input(remaining),
        over_stock=service.over_stock,
        error=error,
        notice=notice,
        done=done,
        shortages=shortages or [],
        weight_code=weight_code,
        last_item_id=last_item_id,
        last_item=last_item,
        qty_step=_qty_step,
    )


def _qty_step(item: Any, delta: int) -> str | None:
    """Quantidade que os botões − e + enviam (un/cx); None quando ficaria abaixo de 1."""
    target = item.quantity + delta
    return None if target < 1 else str(target.to_integral_value())


def _panel(request: Request, s: Session, **kwargs: Any) -> HTMLResponse:
    return render(request, "_pos_panel.html", **_context(s, **kwargs))


def _act(
    request: Request,
    s: Session,
    action: Callable[[], Any],
    *,
    notice: Callable[[Any], str] | None = None,
) -> HTMLResponse:
    try:
        result = action()
        s.commit()
    except DomainError as exc:
        s.rollback()
        return _panel(request, s, error=exc.message)
    return _panel(request, s, notice=notice(result) if notice else None)


@router.get("/caixa", response_class=HTMLResponse)
def pos_page(request: Request, s: SessionDep) -> HTMLResponse:
    if service.current_session(s) is None:
        return render(request, "pos_open.html", error=None, nav="pos")
    return render(request, "pos.html", nav="pos", **_context(s))


@router.post("/caixa/abrir", response_class=HTMLResponse, response_model=None)
def pos_open(
    request: Request,
    s: SessionDep,
    now: NowDep,
    opening_amount: Annotated[str, Form()] = "0",
) -> Response:
    try:
        data = OpenSessionIn.model_validate({"opening_amount": parse_decimal(opening_amount)})
        service.open_session(s, data, now=now)
        s.commit()
    except ValidationError as exc:
        error = _first_error(exc, "Valor inválido.")
    except DomainError as exc:
        s.rollback()
        error = exc.message
    else:
        return RedirectResponse("/caixa", status_code=303)
    return render(request, "pos_open.html", error=error, nav="pos")


def _weight_needed(s: Session, code: str) -> bool:
    return find_product_by_code(s, code).unit is Unit.KG


@router.post("/caixa/itens", response_class=HTMLResponse)
def pos_add_item(
    request: Request,
    s: SessionDep,
    now: NowDep,
    entry: Annotated[str, Form()] = "",
    weight: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    try:
        parsed = parse_entry(entry)
    except InvalidInput as exc:
        return _panel(request, s, error=exc.message)
    weight_given = weight is not None and weight.strip() != ""
    quantity: Decimal | str | None = parsed.quantity
    if quantity is None and weight_given:
        assert weight is not None
        quantity = parse_decimal(weight)
    asked_weight = weight is not None
    try:
        service.require_session(s)
        if quantity is None and _weight_needed(s, parsed.code):
            return _panel(request, s, weight_code=parsed.code)
        data = AddItemIn.model_validate({"code": parsed.code, "quantity": quantity})
        item = service.add_item(s, data, now=now)
        s.commit()
    except ValidationError as exc:
        s.rollback()
        return _panel(
            request,
            s,
            error=_first_error(exc, "Quantidade inválida."),
            weight_code=parsed.code if asked_weight else None,
        )
    except DomainError as exc:
        s.rollback()
        return _panel(
            request, s, error=exc.message, weight_code=parsed.code if asked_weight else None
        )
    return _panel(request, s, last_item_id=item.id)


@router.post("/caixa/itens/{item_id}", response_class=HTMLResponse)
def pos_item_quantity(
    request: Request, item_id: int, s: SessionDep, quantity: Annotated[str, Form()] = ""
) -> HTMLResponse:
    try:
        data = ItemQtyIn.model_validate({"quantity": parse_decimal(quantity)})
    except ValidationError as exc:
        return _panel(request, s, error=_first_error(exc, "Quantidade inválida."))
    try:
        service.set_item_quantity(s, item_id, data)
        s.commit()
    except DomainError as exc:
        s.rollback()
        return _panel(request, s, error=exc.message)
    return _panel(request, s, last_item_id=item_id)


@router.post("/caixa/itens/{item_id}/remover", response_class=HTMLResponse)
def pos_item_remove(request: Request, item_id: int, s: SessionDep) -> HTMLResponse:
    return _act(request, s, lambda: service.remove_item(s, item_id))


@router.post("/caixa/descartar", response_class=HTMLResponse)
def pos_discard(request: Request, s: SessionDep) -> HTMLResponse:
    return _act(request, s, lambda: service.discard_sale(s))


@router.post("/caixa/pagar", response_class=HTMLResponse)
def pos_pay(
    request: Request,
    s: SessionDep,
    now: NowDep,
    method: Annotated[str, Form()] = "",
    amount: Annotated[str, Form()] = "",
    sale_id: Annotated[int | None, Form()] = None,
) -> HTMLResponse:
    """Lança o pagamento e, se ele fecha o total, conclui a venda na mesma ação."""
    try:
        data = PaymentIn.model_validate({"method": method, "amount": parse_decimal(amount)})
    except ValidationError as exc:
        return _panel(request, s, error=_first_error(exc, "Valor do pagamento inválido."))
    try:
        service.add_payment(s, data)
        s.commit()
    except DomainError as exc:
        s.rollback()
        return _panel(request, s, error=exc.message)
    sale = service.current_sale(s, fresh=True)
    if sale is None or sum((p.amount for p in sale.payments), Decimal(0)) < sale.total:
        return _panel(request, s)
    return _complete(request, s, now, sale_id)


@router.post("/caixa/pagamentos/{payment_id}/remover", response_class=HTMLResponse)
def pos_remove_payment(request: Request, payment_id: int, s: SessionDep) -> HTMLResponse:
    return _act(request, s, lambda: service.remove_payment(s, payment_id))


def _complete(request: Request, s: Session, now: datetime, sale_id: int | None) -> HTMLResponse:
    try:
        sale = service.complete_sale(s, now=now, sale_id=sale_id)
        s.commit()
    except InsufficientStock as exc:
        s.rollback()
        return _panel(request, s, error=exc.message, shortages=exc.shortages)
    except DomainError as exc:
        s.rollback()
        return _panel(request, s, error=exc.message)
    done = f"Venda #{sale.id} concluída"
    if sale.change_given > 0:
        done += f" · troco {brl(sale.change_given)}"
    return _panel(request, s, done=done)


@router.post("/caixa/concluir", response_class=HTMLResponse)
def pos_complete(
    request: Request, s: SessionDep, now: NowDep, sale_id: Annotated[int | None, Form()] = None
) -> HTMLResponse:
    return _complete(request, s, now, sale_id)


@router.post("/caixa/ajustar", response_class=HTMLResponse)
def pos_adjust(
    request: Request,
    s: SessionDep,
    now: NowDep,
    product_id: Annotated[int, Form()],
    reason: Annotated[str, Form()] = "",
    sale_id: Annotated[int | None, Form()] = None,
) -> HTMLResponse:
    try:
        data = AdjustShortageIn.model_validate({"product_id": product_id, "reason": reason})
    except ValidationError as exc:
        message = form_errors(exc).get("reason", "Informe o motivo do ajuste.")
        return _shortage_panel(request, s, message)
    try:
        service.adjust_for_shortage(s, data, now=now)
        s.commit()
    except DomainError as exc:
        s.rollback()
        return _panel(request, s, error=exc.message)
    return _complete(request, s, now, sale_id)


def _shortage_panel(request: Request, s: Session, message: str) -> HTMLResponse:
    """Recalcula as faltas da venda em aberto para reexibir os formulários de ajuste."""
    sale = service.current_sale(s, fresh=True)
    shortages: list[Shortage] = []
    if sale is not None:
        needed: dict[int, Decimal] = {}
        for item in sale.items:
            needed[item.product_id] = needed.get(item.product_id, Decimal(0)) + item.quantity
        for item in sale.items:
            if needed[item.product_id] > item.product.current_stock:
                shortages.append(
                    Shortage(
                        item.product_id,
                        item.product.name,
                        item.product.current_stock,
                        needed.pop(item.product_id),
                    )
                )
    return _panel(request, s, error=message, shortages=shortages)


@router.post("/caixa/movimentos", response_class=HTMLResponse)
def pos_cash_movement(
    request: Request,
    s: SessionDep,
    now: NowDep,
    kind: Annotated[str, Form()] = "",
    amount: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        data = CashMovementIn.model_validate(
            {"kind": kind, "amount": parse_decimal(amount), "note": note.strip() or None}
        )
    except ValidationError as exc:
        return _panel(request, s, error=_first_error(exc, "Movimento inválido."))
    label = MOVEMENT_LABELS[data.kind]
    suffix = "registrada" if data.kind is CashMovementKind.WITHDRAWAL else "registrado"
    return _act(
        request,
        s,
        lambda: service.add_cash_movement(s, data, now=now),
        notice=lambda _: f"{label} de {brl(data.amount)} {suffix}.",
    )


# --- Fechamento, turnos e detalhe da venda ---------------------------------------------------


def _close_page(
    request: Request, s: Session, session_id: int, error: str | None = None
) -> HTMLResponse:
    return render(
        request,
        "pos_close.html",
        nav="pos",
        cash_session=service.get_session(s, session_id),
        summary=service.session_summary(s, session_id),
        error=error,
    )


@router.get("/caixa/fechar", response_class=HTMLResponse, response_model=None)
def pos_close_page(request: Request, s: SessionDep) -> Response:
    cash_session = service.current_session(s)
    if cash_session is None:
        return RedirectResponse("/caixa", status_code=303)
    return _close_page(request, s, cash_session.id)


@router.post("/caixa/fechar", response_class=HTMLResponse, response_model=None)
def pos_close(
    request: Request,
    s: SessionDep,
    now: NowDep,
    counted_amount: Annotated[str, Form()] = "",
    note: Annotated[str, Form()] = "",
) -> Response:
    cash_session = service.current_session(s)
    if cash_session is None:
        return RedirectResponse("/caixa", status_code=303)
    try:
        data = CloseSessionIn.model_validate(
            {"counted_amount": parse_decimal(counted_amount), "note": note.strip() or None}
        )
        closed = service.close_session(s, data, now=now)
        s.commit()
    except ValidationError as exc:
        error = _first_error(exc, "Valor contado inválido.")
    except DomainError as exc:
        s.rollback()
        error = exc.message
    else:
        return RedirectResponse(f"/caixa/turnos/{closed.id}", status_code=303)
    return _close_page(request, s, cash_session.id, error)


@router.get("/caixa/turnos", response_class=HTMLResponse)
def pos_sessions(request: Request, s: SessionDep) -> HTMLResponse:
    rows = [(cs, service.session_summary(s, cs.id)) for cs in service.list_sessions(s)]
    return render(request, "pos_sessions.html", nav="pos", rows=rows)


@router.get("/caixa/turnos/{session_id}", response_class=HTMLResponse)
def pos_session_detail(request: Request, session_id: int, s: SessionDep) -> HTMLResponse:
    cash_session = service.get_session(s, session_id)
    return render(
        request,
        "pos_session_detail.html",
        nav="pos",
        cash_session=cash_session,
        summary=service.session_summary(s, session_id),
        sales=service.list_session_sales(s, session_id),
    )


def _sale_page(
    request: Request, s: Session, sale_id: int, error: str | None = None
) -> HTMLResponse:
    sale = service.get_sale(s, sale_id)
    return render(
        request,
        "pos_sale_detail.html",
        nav="pos",
        sale=sale,
        cancellable=sale.status is SaleStatus.COMPLETED and sale.session.closed_at is None,
        error=error,
    )


@router.get("/caixa/vendas/{sale_id}", response_class=HTMLResponse)
def pos_sale_detail(request: Request, sale_id: int, s: SessionDep) -> HTMLResponse:
    return _sale_page(request, s, sale_id)


@router.post("/caixa/vendas/{sale_id}/cancelar", response_class=HTMLResponse, response_model=None)
def pos_sale_cancel(
    request: Request,
    sale_id: int,
    s: SessionDep,
    now: NowDep,
    reason: Annotated[str, Form()] = "",
) -> Response:
    service.get_sale(s, sale_id)
    try:
        data = CancelSaleIn.model_validate({"reason": reason})
        service.cancel_sale(s, sale_id, data, now=now)
        s.commit()
    except ValidationError as exc:
        error = _first_error(exc, "Informe o motivo do cancelamento.")
    except DomainError as exc:
        s.rollback()
        error = exc.message
    else:
        return RedirectResponse(f"/caixa/vendas/{sale_id}", status_code=303)
    return _sale_page(request, s, sale_id, error)
