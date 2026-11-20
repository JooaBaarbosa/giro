from decimal import ROUND_HALF_UP, Decimal


def br_number(value: Decimal) -> str:
    """Formata quantidade no padrão brasileiro sem zeros à direita: 2.000 → "2", 1.500 → "1,5"."""
    text = f"{value.normalize():f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def brl(value: Decimal) -> str:
    """Formata valor em reais: 1234.5 → "R$ 1.234,50"; negativos como "-R$ 5,00"."""
    cents = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if cents < 0 else ""
    text = f"{abs(cents):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{sign}R$ {text}"
