from decimal import Decimal


def br_number(value: Decimal) -> str:
    """Formata quantidade no padrão brasileiro sem zeros à direita: 2.000 → "2", 1.500 → "1,5"."""
    text = f"{value.normalize():f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")
