"""Money as integer cents (never floats)."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def parse_cents(text: str) -> int:
    cleaned = str(text).strip().replace("$", "").replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"not an amount: {text!r}") from None
    if not value.is_finite():
        raise ValueError(f"not an amount: {text!r}")
    if abs(value) >= Decimal(2) ** 63 / 100:
        raise ValueError(f"amount is too large: {text!r}")
    if value != value.quantize(Decimal("0.01")):
        raise ValueError(f"amount has more than 2 decimals: {text!r}")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"
