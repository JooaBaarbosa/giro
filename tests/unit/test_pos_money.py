from decimal import Decimal

import pytest

from giro.errors import PaymentMismatch
from giro.fmt import brl
from giro.models import PaymentMethod
from giro.pos.money import compute_change, line_total, validate_new_payment

CASH = PaymentMethod.CASH
PIX = PaymentMethod.PIX
CREDIT = PaymentMethod.CREDIT


def test_line_total_rounds_half_up():
    assert line_total(Decimal("0.355"), Decimal("49.90")) == Decimal("17.71")  # 17.7145
    assert line_total(Decimal("3"), Decimal("4.99")) == Decimal("14.97")


def test_change_only_from_cash():
    assert compute_change(Decimal("30.00"), [(CASH, Decimal("50.00"))]) == Decimal("20.00")
    assert (
        compute_change(Decimal("30.00"), [(CASH, Decimal("20.00")), (PIX, Decimal("10.00"))]) == 0
    )
    with pytest.raises(PaymentMismatch):
        compute_change(Decimal("30.00"), [(PIX, Decimal("40.00"))])


def test_underpaid_refused():
    with pytest.raises(PaymentMismatch, match="Faltam R\$ 5,00"):
        compute_change(Decimal("30.00"), [(PIX, Decimal("25.00"))])


def test_non_cash_cannot_exceed_remaining():
    with pytest.raises(PaymentMismatch):
        validate_new_payment(Decimal("30"), [(CASH, Decimal("20"))], CREDIT, Decimal("15"))


def test_cash_may_exceed_remaining():
    validate_new_payment(Decimal("30"), [(CASH, Decimal("20"))], CASH, Decimal("15"))


@pytest.mark.parametrize("amount", [Decimal(0), Decimal("-1")])
def test_payment_must_be_positive(amount):
    with pytest.raises(PaymentMismatch):
        validate_new_payment(Decimal("30"), [], CASH, amount)


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("1234.56"), "R$ 1.234,56"),
        (Decimal("5"), "R$ 5,00"),
        (Decimal("0.005"), "R$ 0,01"),
        (Decimal("1234567.891"), "R$ 1.234.567,89"),
        (Decimal("-5"), "-R$ 5,00"),
        (Decimal("0"), "R$ 0,00"),
    ],
)
def test_brl(value, expected):
    assert brl(value) == expected
