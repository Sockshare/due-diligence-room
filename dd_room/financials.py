"""Financial trend + valuation math over a :class:`FinancialTable`.

All functions are deterministic and return plain dicts with explicit ``None``
for anything not computable, plus a ``notes`` list describing the gap.
"""
from __future__ import annotations

from typing import Optional

from .xbrl import FinancialTable


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _sub(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return a - b


def _add(*xs: Optional[float], allow_missing: bool = True) -> Optional[float]:
    vals = [x for x in xs if x is not None]
    if not vals:
        return None
    if not allow_missing and len(vals) != len(xs):
        return None
    return float(sum(vals))


def effective_tax_rate(t: FinancialTable, y: int) -> Optional[float]:
    r = _div(t.value("income_tax", y), t.value("pretax_income", y))
    if r is None:
        return None
    return min(max(r, 0.0), 0.5)


def total_debt(t: FinancialTable, y: int) -> Optional[float]:
    return _add(t.value("ltd", y), t.value("std", y))


def cash_and_investments(t: FinancialTable, y: int) -> Optional[float]:
    return _add(t.value("cash", y), t.value("short_investments", y))


def year_metrics(t: FinancialTable, y: int) -> dict:
    rev = t.value("revenue", y)
    gp = t.value("gross_profit", y)
    op = t.value("operating_income", y)
    ni = t.value("net_income", y)
    cfo = t.value("cfo", y)
    capex = t.value("capex", y)
    fcf = _sub(cfo, abs(capex) if capex is not None else None)
    debt = total_debt(t, y)
    cash = cash_and_investments(t, y)
    net_debt = _sub(debt, cash) if debt is not None or cash is not None else None
    if debt is None and cash is not None:
        net_debt = -cash
    eq = t.value("equity", y)
    tax = effective_tax_rate(t, y)
    nopat = op * (1 - (tax if tax is not None else 0.21)) if op is not None else None
    invested = None
    if eq is not None:
        invested = eq + (debt or 0.0) - (cash or 0.0)
    roic = _div(nopat, invested) if invested and invested > 0 else None
    return {
        "year": y,
        "revenue": rev, "gross_profit": gp, "operating_income": op, "net_income": ni,
        "gross_margin": _div(gp, rev), "operating_margin": _div(op, rev), "net_margin": _div(ni, rev),
        "cfo": cfo, "capex": capex, "fcf": fcf, "fcf_margin": _div(fcf, rev),
        "total_debt": debt, "cash_and_investments": cash, "net_debt": net_debt,
        "equity": eq, "effective_tax_rate": tax, "nopat": nopat, "invested_capital": invested, "roic": roic,
        "shares_diluted": t.value("shares_diluted", y), "eps_diluted": t.value("eps_diluted", y),
        "sbc": t.value("sbc", y), "buybacks": t.value("buybacks", y), "dividends": t.value("dividends", y),
        "rnd": t.value("rnd", y), "total_assets": t.value("total_assets", y),
    }


def trend_table(t: FinancialTable) -> list[dict]:
    return [year_metrics(t, y) for y in t.years]


def cagr(first: Optional[float], last: Optional[float], years: int) -> Optional[float]:
    if first is None or last is None or years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def valuation_snapshot(t: FinancialTable, price: Optional[float], shares_outstanding: Optional[float]) -> dict:
    notes: list[str] = []
    if not t.years:
        return {"notes": ["No annual financial data"], "market_cap": None}
    y = t.years[-1]
    m = year_metrics(t, y)
    if price is None:
        notes.append("No market price available (Yahoo Finance endpoint unreachable or ticker not quoted) — market-based multiples omitted")
    if shares_outstanding is None:
        shares_outstanding = m["shares_diluted"]
        if shares_outstanding is not None:
            notes.append("dei:EntityCommonStockSharesOutstanding missing — used diluted weighted-average shares instead")
        else:
            notes.append("No share count available — market cap not computable")
    mcap = price * shares_outstanding if price is not None and shares_outstanding else None
    debt = m["total_debt"]
    cash = m["cash_and_investments"]
    ev = (mcap + (debt or 0.0) - (cash or 0.0)) if mcap is not None else None
    da = t.value("da", y)
    ebitda = (m["operating_income"] + da) if m["operating_income"] is not None and da is not None else None
    if ebitda is None and m["operating_income"] is not None:
        notes.append("D&A not tagged — EV/EBITDA omitted (EV/EBIT shown instead)")
    pe = _div(mcap, m["net_income"]) if m["net_income"] and m["net_income"] > 0 else None
    if mcap is not None and (m["net_income"] is None or m["net_income"] <= 0):
        notes.append("P/E not meaningful: net income is zero or negative")
    return {
        "year": y, "price": price, "shares_outstanding": shares_outstanding, "market_cap": mcap,
        "enterprise_value": ev, "pe": pe, "ps": _div(mcap, m["revenue"]),
        "ev_ebitda": _div(ev, ebitda) if ebitda and ebitda > 0 else None,
        "ev_ebit": _div(ev, m["operating_income"]) if m["operating_income"] and m["operating_income"] > 0 else None,
        "fcf_yield": _div(m["fcf"], mcap), "ebitda": ebitda, "net_debt": m["net_debt"], "notes": notes,
    }


def summarise_trends(rows: list[dict]) -> dict:
    """Headline growth / margin direction for the memo text."""
    if not rows:
        return {}
    first, last = rows[0], rows[-1]
    n = last["year"] - first["year"]
    out = {
        "years": n,
        "revenue_cagr": cagr(first["revenue"], last["revenue"], n) if n > 0 else None,
        "net_income_cagr": cagr(first["net_income"], last["net_income"], n) if n > 0 else None,
        "fcf_cagr": cagr(first["fcf"], last["fcf"], n) if n > 0 else None,
        "gross_margin_change": _sub(last["gross_margin"], first["gross_margin"]),
        "operating_margin_change": _sub(last["operating_margin"], first["operating_margin"]),
        "net_margin_change": _sub(last["net_margin"], first["net_margin"]),
        "loss_years": [r["year"] for r in rows if r["net_income"] is not None and r["net_income"] < 0],
        "negative_fcf_years": [r["year"] for r in rows if r["fcf"] is not None and r["fcf"] < 0],
    }
    if n > 0 and len(rows) >= 2:
        prev = rows[-2]
        out["revenue_yoy"] = _div(_sub(last["revenue"], prev["revenue"]), abs(prev["revenue"]) if prev["revenue"] else None)
        out["net_income_yoy"] = _div(_sub(last["net_income"], prev["net_income"]), abs(prev["net_income"]) if prev["net_income"] else None)
    return out
