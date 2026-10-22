from decimal import Decimal

from giro.models import Unit
from giro.orders.message import (
    format_items_line,
    format_qty,
    owner_alert_params,
    render_supplier_message,
    supplier_template_params,
)


def test_format_qty():
    assert format_qty(Decimal("30.000"), Unit.UN) == "30"
    assert format_qty(Decimal("1.500"), Unit.KG) == "1,5"
    assert format_qty(Decimal("2.000"), Unit.KG) == "2"


def test_items_line():
    line = format_items_line(
        [("Arroz 5kg", Decimal(30), Unit.UN), ("Queijo", Decimal("1.500"), Unit.KG)]
    )
    assert line == "Arroz 5kg: 30 un · Queijo: 1,5 kg"


def test_params_sanitized():
    params = supplier_template_params(supplier_name="A\nB", store_name="L\t", items_line="x     y")
    assert params == ["A B", "L", "x y"]


def test_owner_alert_params():
    params = owner_alert_params(
        count=2, names=["Arroz", "Café"], supplier_name="Dist X", link="http://app/orders/7"
    )
    assert params == ["2", "Arroz, Café", "Dist X", "http://app/orders/7"]


def test_render_supplier_message():
    text = render_supplier_message(["Dist X", "Mercearia Demo", "Arroz 5kg: 30 un"])
    assert text == (
        "Olá Dist X, aqui é da Mercearia Demo. Gostaríamos de fazer o seguinte pedido: "
        "Arroz 5kg: 30 un. Por favor, confirme o recebimento. Obrigado!"
    )
