import pytest

from app.money import format_cents, parse_cents


@pytest.mark.parametrize(
    "text,expected",
    [
        ("9.99", 999),
        ("  9.99 ", 999),
        ("$1,234.56", 123456),
        ("10", 1000),
        ("10.5", 1050),
        (".75", 75),
        ("0", 0),
        ("-5.00", -500),
    ],
)
def test_parse_cents(text, expected):
    assert parse_cents(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1.234", "1.2.3", "--1", "$"])
def test_parse_cents_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_cents(text)


@pytest.mark.parametrize(
    "cents,expected",
    [
        (999, "$9.99"),
        (123456, "$1,234.56"),
        (0, "$0.00"),
        (-500, "-$5.00"),
        (5, "$0.05"),
    ],
)
def test_format_cents(cents, expected):
    assert format_cents(cents) == expected


def test_format_cents_unsigned():
    assert format_cents(-123456, signed=False) == "$1,234.56"
