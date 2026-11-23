"""Money helpers for Stage 3a.

All monetary values use decimal.Decimal. Floats are rejected at the boundary
so that binary floating-point error never reaches stored amounts.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MONEY_QUANTUM = Decimal("0.01")
HUNDRED = Decimal("100")


def to_decimal(value):
    """Convert value to a finite Decimal without quantizing it.

    Accepts Decimal, int and numeric str. Rejects bool, float and any other type.

    Raises:
        TypeError: value is a bool, a float, or an unsupported type.
        ValueError: value is an invalid string, NaN, or +/-Infinity.
    """
    if isinstance(value, bool):
        raise TypeError("Booleans are not valid monetary amounts.")
    if isinstance(value, float):
        raise TypeError("Floats are not allowed for monetary amounts; use Decimal or str.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"Invalid monetary amount: {value!r}.") from exc
    else:
        raise TypeError(f"Unsupported type for monetary amount: {type(value).__name__}.")

    if not result.is_finite():
        raise ValueError("Monetary amounts must be finite (no NaN or Infinity).")
    return result


def quantize_money(value):
    """Return to_decimal(value) rounded to two decimal places, ROUND_HALF_UP."""
    return to_decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def percent_of(amount, rate):
    """Return amount * rate / 100, rounded to two decimal places, ROUND_HALF_UP.

    `rate` is a percentage, so percent_of("1000", "18") returns Decimal("180.00").
    """
    base = to_decimal(amount)
    percentage = to_decimal(rate)
    return (base * percentage / HUNDRED).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def to_money_str(value):
    """Return the quantized amount as a string with two decimal places."""
    return str(quantize_money(value))
