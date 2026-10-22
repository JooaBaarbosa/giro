"""Telas do dashboard (HTMX + Jinja). Sem autenticação no MVP — ver README."""

import math
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from giro import reports
from giro.api.deps import NowDep, SessionDep, SettingsDep
from giro.forecast import Forecast
from giro.models import Unit
from giro.orders.message import format_qty
from giro.reports import StockStatus

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

STATUS_LABELS = {
    StockStatus.RED: "🔴 Abaixo do mínimo",
    StockStatus.YELLOW: "🟡 Comprar em breve",
    StockStatus.GREEN: "🟢 OK",
}


def _qty(value: Decimal | None, unit: Unit = Unit.KG) -> str:
    return "—" if value is None else format_qty(value, unit)


def _qty1(value: Decimal) -> str:
    return format_qty(value.quantize(Decimal("0.1")), Unit.KG)


def _brdate(value: date | None) -> str:
    return "—" if value is None else value.strftime("%d/%m")


def _days(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return "0" if value <= 0 else str(math.floor(value))


templates.env.filters["qty"] = _qty
templates.env.filters["qty1"] = _qty1
templates.env.filters["brdate"] = _brdate
templates.env.filters["days"] = _days
templates.env.globals["STATUS_LABELS"] = STATUS_LABELS

router = APIRouter(include_in_schema=False)


def render(request: Request, name: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


def next_purchase_label(forecast: Forecast, today: date) -> str:
    if forecast.no_forecast or forecast.next_purchase is None:
        return "sem previsão"
    if forecast.next_purchase <= today:
        return "agora"
    return _brdate(forecast.next_purchase)


templates.env.globals["next_purchase_label"] = next_purchase_label


def _chart(series: list[tuple[date, Decimal]]) -> dict[str, list[Any]]:
    return {
        "labels": [_brdate(d) for d, _ in series],
        "values": [float(q) for _, q in series],
    }


@router.get("/", response_class=HTMLResponse)
def overview_page(
    request: Request, s: SessionDep, settings: SettingsDep, now: NowDep
) -> HTMLResponse:
    ov = reports.overview(s, now=now, tz=settings.store_tz)
    series = reports.daily_sales(s, now=now, tz=settings.store_tz)
    return render(
        request,
        "overview.html",
        ov=ov,
        today=reports.local_today(now, settings.store_tz),
        chart=_chart(series),
        store_name=settings.store_name,
        nav="overview",
    )


@router.get("/products/table", response_class=HTMLResponse)
def product_table(
    request: Request,
    s: SessionDep,
    settings: SettingsDep,
    now: NowDep,
    q: Annotated[str | None, Query()] = None,
) -> HTMLResponse:
    ov = reports.overview(s, now=now, tz=settings.store_tz, search=q)
    return render(
        request,
        "_product_table.html",
        ov=ov,
        today=reports.local_today(now, settings.store_tz),
    )
