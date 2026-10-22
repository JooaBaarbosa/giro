"""Previsão de compra por média móvel de consumo. Função pura: não acessa o banco."""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from giro.models import Unit

WINDOW_DAYS = 30
LOW_HISTORY_DAYS = 7


@dataclass(frozen=True)
class Forecast:
    avg_daily: Decimal
    days_left: Decimal | None
    next_purchase: date | None
    suggested_qty: Decimal
    low_history: bool
    no_forecast: bool


def round_qty(raw: Decimal, unit: Unit) -> Decimal:
    """Arredonda para cima: inteiro para un/cx, uma casa decimal para kg; mínimo 1."""
    if unit is Unit.KG:
        rounded = raw.quantize(Decimal("0.1"), rounding=ROUND_CEILING)
    else:
        rounded = Decimal(math.ceil(raw))
    return max(rounded, Decimal(1))


def compute_forecast(
    *,
    current_stock: Decimal,
    min_stock: Decimal,
    cover_days: int,
    unit: Unit,
    sales_by_day: Mapping[date, Decimal],
    first_sale: date | None,
    today: date,
) -> Forecast:
    window = min(WINDOW_DAYS, (today - first_sale).days + 1) if first_sale else 1
    window = max(window, 1)
    start = today - timedelta(days=window - 1)
    total = sum((q for d, q in sales_by_day.items() if start <= d <= today), Decimal(0))

    no_forecast = total == 0
    avg_daily = Decimal(0) if no_forecast else total / window
    days_left: Decimal | None = None
    next_purchase: date | None = None
    if not no_forecast:
        days_left = (current_stock - min_stock) / avg_daily
        next_purchase = (
            today if days_left <= 0 else today + timedelta(days=math.floor(days_left))
        )

    raw = avg_daily * cover_days + min_stock - current_stock
    return Forecast(
        avg_daily=avg_daily,
        days_left=days_left,
        next_purchase=next_purchase,
        suggested_qty=round_qty(raw, unit),
        low_history=not no_forecast and window < LOW_HISTORY_DAYS,
        no_forecast=no_forecast,
    )
