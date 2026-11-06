"""Number formatting used by every agent so the memo reads consistently."""
from __future__ import annotations

from typing import Optional

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "CN¥", "CAD": "C$", "AUD": "A$", "CHF": "CHF ", "HKD": "HK$", "INR": "₹"}


def money(v: Optional[float], currency: str = "USD", digits: int = 1) -> str:
    if v is None:
        return "n/a"
    sym = CURRENCY_SYMBOLS.get(currency, currency + " ")
    neg = v < 0
    a = abs(v)
    if a >= 1e12:
        s = f"{a / 1e12:.{digits}f}T"
    elif a >= 1e9:
        s = f"{a / 1e9:.{digits}f}B"
    elif a >= 1e6:
        s = f"{a / 1e6:.{digits}f}M"
    elif a >= 1e3:
        s = f"{a / 1e3:.{digits}f}K"
    else:
        s = f"{a:.{digits}f}"
    return f"{'−' if neg else ''}{sym}{s}"


def pct(v: Optional[float], digits: int = 1, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    s = f"{v * 100:{'+' if signed else ''}.{digits}f}%"
    return s.replace("-", "−")


def ratio(v: Optional[float], digits: int = 2, suffix: str = "x") -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}{suffix}".replace("-", "−")


def num(v: Optional[float], digits: int = 2) -> str:
    if v is None:
        return "n/a"
    return f"{v:,.{digits}f}".replace("-", "−")


def count(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.2f}B"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:,.0f}"


def days(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.0f} days"


def light_emoji(light: str) -> str:
    return {"green": "🟢", "amber": "🟡", "red": "🔴"}.get(light, "⚪")
