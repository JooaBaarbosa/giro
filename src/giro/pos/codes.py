import re
from dataclasses import dataclass
from decimal import Decimal

from giro.errors import InvalidInput

_QUANTITY = re.compile(r"\d+(?:[.,]\d{1,3})?")


@dataclass(frozen=True)
class CodeEntry:
    quantity: Decimal | None
    code: str


def parse_entry(raw: str) -> CodeEntry:
    """Lê a entrada do caixa: "CODIGO" ou "QTD*CODIGO" (vírgula ou ponto decimal)."""
    text = raw.strip()
    if not text:
        raise InvalidInput("Informe um código.")
    if "*" not in text:
        return CodeEntry(None, text)
    qty_text, code = (part.strip() for part in text.split("*", 1))
    if not code:
        raise InvalidInput("Informe o código depois do *.")
    if not _QUANTITY.fullmatch(qty_text):
        raise InvalidInput("Quantidade inválida. Use até 3 casas decimais, ex.: 0,350*CODIGO.")
    quantity = Decimal(qty_text.replace(",", "."))
    if quantity <= 0:
        raise InvalidInput("A quantidade deve ser maior que zero.")
    return CodeEntry(quantity, code)


def is_valid_gtin(code: str) -> bool:
    """GTIN-8/12/13 com dígito verificador GS1 (só dígitos ASCII)."""
    if len(code) not in (8, 12, 13) or not (code.isascii() and code.isdigit()):
        return False
    digits = [int(c) for c in code]
    body, check = digits[:-1], digits[-1]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check
