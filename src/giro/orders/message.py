"""Textos e parâmetros dos templates de WhatsApp (pedido ao fornecedor e alerta ao dono)."""

import re
from collections.abc import Sequence
from decimal import Decimal

from giro.fmt import br_number
from giro.models import Unit

SUPPLIER_TEMPLATE = (
    "Olá {0}, aqui é da {1}. Gostaríamos de fazer o seguinte pedido: {2}. "
    "Por favor, confirme o recebimento. Obrigado!"
)


def sanitize_param(text: str) -> str:
    """A Meta rejeita parâmetros com quebra de linha, tab ou 4+ espaços seguidos."""
    return re.sub(r"\s+", " ", text).strip()


def format_qty(quantity: Decimal, unit: Unit) -> str:
    return br_number(quantity)


def format_items_line(items: Sequence[tuple[str, Decimal, Unit]]) -> str:
    return " · ".join(f"{name}: {format_qty(qty, unit)} {unit.value}" for name, qty, unit in items)


def supplier_template_params(*, supplier_name: str, store_name: str, items_line: str) -> list[str]:
    return [sanitize_param(p) for p in (supplier_name, store_name, items_line)]


def owner_alert_params(
    *, count: int, names: Sequence[str], supplier_name: str, link: str
) -> list[str]:
    return [sanitize_param(p) for p in (str(count), ", ".join(names), supplier_name, link)]


def render_supplier_message(params: list[str]) -> str:
    return SUPPLIER_TEMPLATE.format(*params)
