from decimal import Decimal

import pytest

from giro.errors import InvalidInput
from giro.pos.codes import CodeEntry, is_valid_gtin, parse_entry


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("7891000100103", CodeEntry(None, "7891000100103")),
        ("  ARR5 ", CodeEntry(None, "ARR5")),
        ("3*ARR5", CodeEntry(Decimal(3), "ARR5")),
        ("0,350*QMU", CodeEntry(Decimal("0.350"), "QMU")),
        ("2 * ARR5", CodeEntry(Decimal(2), "ARR5")),
    ],
)
def test_parse_entry(raw, expected):
    assert parse_entry(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", "*ARR5", "x*ARR5", "0*ARR5", "-1*ARR5", "0,3456*QMU", "3*"]
)
def test_parse_entry_invalid(raw):
    with pytest.raises(InvalidInput):
        parse_entry(raw)


@pytest.mark.parametrize(
    "code,ok",
    [
        ("7891000100103", True),
        ("7891000100104", False),
        ("96385074", True),
        ("036000291452", True),
        ("12345", False),
        ("78910001001AB", False),
    ],
)
def test_gtin(code, ok):
    assert is_valid_gtin(code) is ok
