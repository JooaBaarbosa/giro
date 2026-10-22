"""Jinja: filtros pt-BR, rótulos e helpers de formulário compartilhados pelas telas."""

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from giro.config import get_settings
from giro.forecast import Forecast
from giro.models import MovementKind, Unit
from giro.orders.message import format_qty
from giro.orders.state import STATUS_LABELS as ORDER_STATUS_LABELS
from giro.reports import StockStatus

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

STATUS_LABELS = {
    StockStatus.RED: "🔴 Abaixo do mínimo",
    StockStatus.YELLOW: "🟡 Comprar em breve",
    StockStatus.GREEN: "🟢 OK",
}

KIND_LABELS = {
    MovementKind.SALE: "Venda",
    MovementKind.PURCHASE: "Entrada",
    MovementKind.ADJUSTMENT: "Ajuste",
}


def qty(value: Decimal | None, unit: Unit = Unit.KG) -> str:
    return "—" if value is None else format_qty(value, unit)


def qty1(value: Decimal) -> str:
    return format_qty(value.quantize(Decimal("0.1")), Unit.KG)


def brdate(value: date | None) -> str:
    return "—" if value is None else value.strftime("%d/%m")


def localdt(value: datetime | None, fmt: str = "%d/%m %H:%M") -> str:
    """Converte timestamptz (vem do banco em UTC) para o fuso da loja antes de formatar."""
    if value is None:
        return "—"
    return value.astimezone(ZoneInfo(get_settings().store_tz)).strftime(fmt)


def days(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return "0" if value <= 0 else str(math.floor(value))


def next_purchase_label(forecast: Forecast, today: date) -> str:
    if forecast.no_forecast or forecast.next_purchase is None:
        return "sem previsão"
    if forecast.next_purchase <= today:
        return "agora"
    return brdate(forecast.next_purchase)


templates.env.filters.update(qty=qty, qty1=qty1, brdate=brdate, days=days, localdt=localdt)
templates.env.globals.update(
    STATUS_LABELS=STATUS_LABELS,
    KIND_LABELS=KIND_LABELS,
    ORDER_STATUS_LABELS=ORDER_STATUS_LABELS,
    next_purchase_label=next_purchase_label,
)


def render(request: Request, name: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def chart_data(series: list[tuple[date, Decimal]]) -> dict[str, list[Any]]:
    return {"labels": [brdate(d) for d, _ in series], "values": [float(q) for _, q in series]}


def parse_decimal(raw: str) -> Decimal | str:
    """Aceita vírgula decimal ("1,5"). Devolve o texto original se não for número,
    para o Pydantic gerar a mensagem de validação."""
    try:
        return Decimal(raw.strip().replace(",", "."))
    except InvalidOperation:
        return raw


def form_errors(exc: ValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for err in exc.errors():
        field = str(err["loc"][0]) if err["loc"] else "__all__"
        message = err["msg"].removeprefix("Value error, ")
        errors.setdefault(field, message)
    return errors
