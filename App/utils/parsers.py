# utils/parsers.py
import re
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = ["₵", "$", "¢", "€", "£", "ghc", "gh₵", "usd", "eur", "gbp"]

def parse_price(value: str):
    """
    Extracts numeric part of a price string like "₵5.00", "$2.50", "2.5 GHC", etc.
    Returns a Decimal value or raises ValueError if invalid.
    """
    if not value:
        raise ValueError("Empty price value")

    # Normalize the string (lowercase and strip spaces)
    value = value.strip().lower()

    # Remove any known currency symbols or codes
    for symbol in CURRENCY_SYMBOLS:
        value = value.replace(symbol.lower(), "")

    # Remove non-numeric and non-dot characters (like commas, spaces)
    value = re.sub(r"[^0-9.]", "", value)

    # Handle cases like "2.500.0"
    if value.count('.') > 1:
        raise ValueError(f"Invalid number format: {value}")

    try:
        return Decimal(value)
    except InvalidOperation:
        raise ValueError(f"Invalid price value: {value}")
