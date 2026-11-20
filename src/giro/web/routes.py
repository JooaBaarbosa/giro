"""Telas do dashboard (HTMX + Jinja). Sem autenticação no MVP — ver README."""

from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from giro import reports
from giro.api.deps import NowDep, SessionDep, SettingsDep
from giro.pos.state import METHOD_LABELS
from giro.web import inventory, orders, pos
from giro.web.templating import chart_data, render

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def overview_page(
    request: Request, s: SessionDep, settings: SettingsDep, now: NowDep
) -> HTMLResponse:
    ov = reports.overview(s, now=now, tz=settings.store_tz)
    series = reports.daily_sales(s, now=now, tz=settings.store_tz)
    sales = reports.sales_kpis(s, now=now, tz=settings.store_tz)
    pay_values = [float(v) for v in sales.by_method_month.values()]
    payments = {"labels": [METHOD_LABELS[m] for m in sales.by_method_month], "values": pay_values}
    return render(
        request,
        "overview.html",
        ov=ov,
        today=reports.local_today(now, settings.store_tz),
        chart=chart_data(series),
        sales=sales,
        payments=payments,
        has_payments=any(pay_values),
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
        request, "_product_table.html", ov=ov, today=reports.local_today(now, settings.store_tz)
    )


router.include_router(inventory.router)
router.include_router(orders.router)
router.include_router(pos.router)
