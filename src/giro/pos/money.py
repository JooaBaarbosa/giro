from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from giro.errors import PaymentMismatch
from giro.fmt import brl
from giro.models import PaymentMethod

_CENT = Decimal("0.01")


def line_total(quantity: Decimal, unit_price: Decimal) -> Decimal:
    """Total da linha arredondado para centavos (meio para cima)."""
    return (quantity * unit_price).quantize(_CENT, rounding=ROUND_HALF_UP)


def compute_change(total: Decimal, payments: Sequence[tuple[PaymentMethod, Decimal]]) -> Decimal:
    """Troco da venda. Só dinheiro gera troco; falta ou excesso fora do dinheiro é recusado."""
    paid = sum((amount for _, amount in payments), Decimal(0))
    cash = sum((amount for method, amount in payments if method is PaymentMethod.CASH), Decimal(0))
    if paid < total:
        raise PaymentMismatch(f"Faltam {brl(total - paid)} para fechar o total.")
    change = paid - total
    if change > cash:
        raise PaymentMismatch(_NON_CASH_OVERPAY)
    return change


_NON_CASH_OVERPAY = "Pix e cartão não podem passar do valor da venda; troco só em dinheiro."


def validate_new_payment(
    total: Decimal,
    paid: Sequence[tuple[PaymentMethod, Decimal]],
    method: PaymentMethod,
    amount: Decimal,
) -> None:
    """Valida um pagamento a adicionar às parcelas já lançadas."""
    if amount <= 0:
        raise PaymentMismatch("Valor do pagamento deve ser maior que zero.")
    if method is not PaymentMethod.CASH:
        remaining = total - sum((value for _, value in paid), Decimal(0))
        if amount > remaining:
            raise PaymentMismatch(_NON_CASH_OVERPAY)
