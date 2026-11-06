"""Forensic accounting metrics — pure functions over plain numbers.

Every function returns a :class:`Metric` carrying the formula, the inputs used,
the thresholds applied (with the paper they come from) and a traffic light.
Missing inputs never raise: the metric comes back grey with a ``note`` that says
exactly which input was unavailable, so the memo can print the gap.

References (full citations in docs/METHODOLOGY.md):
* Beneish, M. D. (1999). "The Detection of Earnings Manipulation."
  Financial Analysts Journal 55(5): 24–36.
* Beneish, Lee & Nichols (2013). "Earnings Manipulation and Expected Returns."
  Financial Analysts Journal 69(2): 57–82 (−1.78 cut-off).
* Sloan, R. G. (1996). "Do Stock Prices Fully Reflect Information in Accruals
  and Cash Flows about Future Earnings?" The Accounting Review 71(3): 289–315.
* Altman, E. I. (1968). "Financial Ratios, Discriminant Analysis and the
  Prediction of Corporate Bankruptcy." Journal of Finance 23(4): 589–609.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

GREEN, AMBER, RED, GREY = "green", "amber", "red", "grey"


@dataclass
class Metric:
    key: str
    label: str
    value: Optional[float]
    unit: str                      # "score" | "ratio" | "pct" | "days" | "x"
    light: str
    formula: str
    inputs: dict[str, Optional[float]]
    thresholds: str
    citation: str
    note: str = ""
    components: dict = field(default_factory=dict)
    year: Optional[int] = None
    prior_year: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "value": self.value, "unit": self.unit, "light": self.light,
            "formula": self.formula, "inputs": self.inputs, "thresholds": self.thresholds, "citation": self.citation,
            "note": self.note, "components": self.components, "year": self.year, "prior_year": self.prior_year,
        }


def _missing(inputs: dict[str, Optional[float]]) -> list[str]:
    return [k for k, v in inputs.items() if v is None]


def _div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


# --------------------------------------------------------------------------- Beneish
BENEISH_COEFFS = {"DSRI": 0.920, "GMI": 0.528, "AQI": 0.404, "SGI": 0.892, "DEPI": 0.115, "SGAI": -0.172, "TATA": 4.679, "LVGI": -0.327}
BENEISH_INTERCEPT = -4.84
BENEISH_CITATION = "Beneish (1999) FAJ 55(5); cut-off −1.78 per Beneish, Lee & Nichols (2013) FAJ 69(2)"


def beneish_m_score(cur: dict, prev: dict, *, year: Optional[int] = None, prior_year: Optional[int] = None) -> Metric:
    """Eight-variable Beneish M-Score.

    ``cur``/``prev`` keys: revenue, cogs, receivables, current_assets, ppe,
    securities (optional, long-term investments), total_assets, da, sga,
    ltd, current_liabilities, net_income, cfo.
    """
    req = ["revenue", "cogs", "receivables", "current_assets", "ppe", "total_assets", "da", "sga", "ltd", "current_liabilities", "net_income", "cfo"]
    inputs: dict[str, Optional[float]] = {}
    for k in req:
        inputs[f"{k}_t"] = cur.get(k)
        inputs[f"{k}_t-1"] = prev.get(k)
    inputs["securities_t"] = cur.get("securities") or 0.0
    inputs["securities_t-1"] = prev.get("securities") or 0.0
    formula = ("M = −4.84 + 0.920·DSRI + 0.528·GMI + 0.404·AQI + 0.892·SGI + 0.115·DEPI − 0.172·SGAI + 4.679·TATA − 0.327·LVGI")
    thresholds = "M > −1.78: red (likely manipulator zone); −2.22 < M ≤ −1.78: amber; M ≤ −2.22: green"

    def ratio(k, d):
        return _div(d.get(k), d.get("revenue"))

    comps: dict[str, Optional[float]] = {}
    comps["DSRI"] = _div(ratio("receivables", cur), ratio("receivables", prev))
    gm_t = _div((cur.get("revenue") or 0) - (cur.get("cogs") or 0), cur.get("revenue")) if cur.get("cogs") is not None else None
    gm_p = _div((prev.get("revenue") or 0) - (prev.get("cogs") or 0), prev.get("revenue")) if prev.get("cogs") is not None else None
    comps["GMI"] = _div(gm_p, gm_t)

    def aq(d):
        ta = d.get("total_assets")
        if ta in (None, 0) or d.get("current_assets") is None or d.get("ppe") is None:
            return None
        return 1.0 - (d["current_assets"] + d["ppe"] + (d.get("securities") or 0.0)) / ta
    comps["AQI"] = _div(aq(cur), aq(prev))
    comps["SGI"] = _div(cur.get("revenue"), prev.get("revenue"))

    def dep_rate(d):
        if d.get("da") is None or d.get("ppe") is None:
            return None
        return _div(d["da"], d["da"] + d["ppe"])
    comps["DEPI"] = _div(dep_rate(prev), dep_rate(cur))
    comps["SGAI"] = _div(ratio("sga", cur), ratio("sga", prev))

    def lev(d):
        if d.get("ltd") is None or d.get("current_liabilities") is None:
            return None
        return _div(d["ltd"] + d["current_liabilities"], d.get("total_assets"))
    comps["LVGI"] = _div(lev(cur), lev(prev))
    if cur.get("net_income") is not None and cur.get("cfo") is not None:
        comps["TATA"] = _div(cur["net_income"] - cur["cfo"], cur.get("total_assets"))
    else:
        comps["TATA"] = None

    missing_comps = [k for k, v in comps.items() if v is None]
    if missing_comps:
        return Metric("beneish", "Beneish M-Score", None, "score", GREY, formula, inputs, thresholds, BENEISH_CITATION,
                      note=f"Not computable: missing index/inputs {', '.join(missing_comps)} (inputs unavailable: {', '.join(_missing(inputs)) or 'ratio undefined'})",
                      components=comps, year=year, prior_year=prior_year)
    m = BENEISH_INTERCEPT + sum(BENEISH_COEFFS[k] * comps[k] for k in BENEISH_COEFFS)  # type: ignore[operator]
    light = RED if m > -1.78 else AMBER if m > -2.22 else GREEN
    return Metric("beneish", "Beneish M-Score", m, "score", light, formula, inputs, thresholds, BENEISH_CITATION,
                  components=comps, year=year, prior_year=prior_year)


# --------------------------------------------------------------------------- Sloan
SLOAN_CITATION = "Sloan (1996) The Accounting Review 71(3); ±10% screen is a common practitioner convention, not from the paper"


def sloan_accrual_ratio(net_income: Optional[float], cfo: Optional[float], total_assets: Optional[float],
                        total_assets_prev: Optional[float], *, year: Optional[int] = None) -> Metric:
    inputs = {"net_income": net_income, "cfo": cfo, "total_assets_t": total_assets, "total_assets_t-1": total_assets_prev}
    formula = "Accrual ratio = (Net income − Cash from operations) / Average total assets"
    thresholds = "> +10%: red (earnings far ahead of cash); 5–10%: amber; otherwise green (very negative values also worth a look)"
    miss = _missing(inputs)
    if miss:
        return Metric("sloan", "Sloan accrual ratio", None, "pct", GREY, formula, inputs, thresholds, SLOAN_CITATION,
                      note=f"Not computable: missing {', '.join(miss)}", year=year)
    avg_ta = (total_assets + total_assets_prev) / 2.0  # type: ignore[operator]
    v = _div(net_income - cfo, avg_ta)  # type: ignore[operator]
    if v is None:
        return Metric("sloan", "Sloan accrual ratio", None, "pct", GREY, formula, inputs, thresholds, SLOAN_CITATION, note="Average total assets is zero", year=year)
    light = RED if v > 0.10 else AMBER if v > 0.05 else GREEN
    return Metric("sloan", "Sloan accrual ratio", v, "pct", light, formula, inputs, thresholds, SLOAN_CITATION, year=year)


# --------------------------------------------------------------------------- Altman
ALTMAN_CITATION = "Altman (1968) Journal of Finance 23(4) — original public-manufacturer model; zones: >2.99 safe, 1.81–2.99 grey, <1.81 distress"


def altman_z(working_capital: Optional[float], retained_earnings: Optional[float], ebit: Optional[float],
             market_cap: Optional[float], total_liabilities: Optional[float], sales: Optional[float],
             total_assets: Optional[float], *, year: Optional[int] = None, applicable: bool = True, why_not: str = "") -> Metric:
    inputs = {"working_capital": working_capital, "retained_earnings": retained_earnings, "ebit": ebit, "market_cap": market_cap,
              "total_liabilities": total_liabilities, "sales": sales, "total_assets": total_assets}
    formula = "Z = 1.2·(WC/TA) + 1.4·(RE/TA) + 3.3·(EBIT/TA) + 0.6·(Market cap/Total liabilities) + 1.0·(Sales/TA)"
    thresholds = "Z < 1.81: red (distress zone); 1.81–2.99: amber (grey zone); Z > 2.99: green (safe zone)"
    if not applicable:
        return Metric("altman", "Altman Z-Score", None, "score", GREY, formula, inputs, thresholds, ALTMAN_CITATION,
                      note=why_not or "Not applicable to this industry (model built for non-financial companies)", year=year)
    miss = _missing(inputs)
    if miss or not total_assets:
        return Metric("altman", "Altman Z-Score", None, "score", GREY, formula, inputs, thresholds, ALTMAN_CITATION,
                      note=f"Not computable: missing {', '.join(miss) or 'total_assets=0'}", year=year)
    x1 = working_capital / total_assets  # type: ignore[operator]
    x2 = retained_earnings / total_assets  # type: ignore[operator]
    x3 = ebit / total_assets  # type: ignore[operator]
    x4 = _div(market_cap, total_liabilities)
    x5 = sales / total_assets  # type: ignore[operator]
    if x4 is None:
        return Metric("altman", "Altman Z-Score", None, "score", GREY, formula, inputs, thresholds, ALTMAN_CITATION, note="Total liabilities is zero", year=year)
    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    light = RED if z < 1.81 else AMBER if z < 2.99 else GREEN
    return Metric("altman", "Altman Z-Score", z, "score", light, formula, inputs, thresholds, ALTMAN_CITATION,
                  components={"X1 WC/TA": x1, "X2 RE/TA": x2, "X3 EBIT/TA": x3, "X4 MVE/TL": x4, "X5 Sales/TA": x5}, year=year)


# --------------------------------------------------------------------------- working-capital days
def days_sales_outstanding(receivables: Optional[float], revenue: Optional[float], prev_dso: Optional[float] = None, *, year: Optional[int] = None) -> Metric:
    inputs = {"receivables": receivables, "revenue": revenue, "prior_dso": prev_dso}
    formula = "DSO = Accounts receivable / Revenue × 365"
    thresholds = "Year-over-year DSO increase > 20%: red; > 10%: amber; else green (level itself is industry-specific)"
    cite = "Standard ratio; trend thresholds are this tool's convention"
    v = _div(receivables, revenue)
    if v is None:
        return Metric("dso", "Days sales outstanding", None, "days", GREY, formula, inputs, thresholds, cite,
                      note="Receivables or revenue not reported" + (" — common for companies without trade receivables" if receivables is None else ""), year=year)
    dso = v * 365.0
    if prev_dso is None or prev_dso == 0:
        light = GREEN
        note = "No prior-year DSO to compare; light reflects availability only"
    else:
        chg = dso / prev_dso - 1.0
        light = RED if chg > 0.20 else AMBER if chg > 0.10 else GREEN
        note = f"Change vs prior year: {chg:+.1%}"
    return Metric("dso", "Days sales outstanding", dso, "days", light, formula, inputs, thresholds, cite, note=note, year=year)


def days_inventory_outstanding(inventory: Optional[float], cogs: Optional[float], prev_dio: Optional[float] = None, *, year: Optional[int] = None) -> Metric:
    inputs = {"inventory": inventory, "cogs": cogs, "prior_dio": prev_dio}
    formula = "DIO = Inventory / Cost of revenue × 365"
    thresholds = "Year-over-year DIO increase > 20%: red; > 10%: amber; else green"
    cite = "Standard ratio; trend thresholds are this tool's convention"
    v = _div(inventory, cogs)
    if v is None:
        return Metric("dio", "Days inventory outstanding", None, "days", GREY, formula, inputs, thresholds, cite,
                      note="Inventory not reported — typical for service/software/financial companies" if inventory is None else "Cost of revenue not reported", year=year)
    dio = v * 365.0
    if prev_dio is None or prev_dio == 0:
        light, note = GREEN, "No prior-year DIO to compare"
    else:
        chg = dio / prev_dio - 1.0
        light = RED if chg > 0.20 else AMBER if chg > 0.10 else GREEN
        note = f"Change vs prior year: {chg:+.1%}"
    return Metric("dio", "Days inventory outstanding", dio, "days", light, formula, inputs, thresholds, cite, note=note, year=year)


def cash_conversion(cfo: Optional[float], net_income: Optional[float], *, year: Optional[int] = None) -> Metric:
    inputs = {"cfo": cfo, "net_income": net_income}
    formula = "Cash conversion = Cash from operations / Net income"
    thresholds = "< 0.8: red; 0.8–1.0: amber; ≥ 1.0: green (only meaningful when net income > 0)"
    cite = "Practitioner convention"
    if net_income is None or cfo is None:
        return Metric("cfo_ni", "Cash conversion (CFO / NI)", None, "x", GREY, formula, inputs, thresholds, cite, note="CFO or net income not reported", year=year)
    if net_income <= 0:
        return Metric("cfo_ni", "Cash conversion (CFO / NI)", None, "x", GREY, formula, inputs, thresholds, cite,
                      note="Net income is zero or negative; the ratio is not meaningful (see the trends table for CFO and net income)", year=year)
    v = cfo / net_income
    light = RED if v < 0.8 else AMBER if v < 1.0 else GREEN
    return Metric("cfo_ni", "Cash conversion (CFO / NI)", v, "x", light, formula, inputs, thresholds, cite, year=year)


def share_dilution(shares_now: Optional[float], shares_prev: Optional[float], *, year: Optional[int] = None, years_apart: int = 1) -> Metric:
    inputs = {"diluted_shares_t": shares_now, "diluted_shares_t-n": shares_prev, "years_apart": float(years_apart)}
    formula = "Dilution = (Diluted weighted shares_t / Diluted weighted shares_t−n)^(1/n) − 1 (annualised)"
    thresholds = "> +5%/yr: red; +2–5%/yr: amber; ≤ +2%/yr (or shrinking): green"
    cite = "Practitioner convention"
    if not shares_now or not shares_prev or years_apart <= 0:
        return Metric("dilution", "Share-count dilution (annualised)", None, "pct", GREY, formula, inputs, thresholds, cite, note="Diluted share counts not available for both years", year=year)
    v = (shares_now / shares_prev) ** (1.0 / years_apart) - 1.0
    light = RED if v > 0.05 else AMBER if v > 0.02 else GREEN
    return Metric("dilution", "Share-count dilution (annualised)", v, "pct", light, formula, inputs, thresholds, cite, year=year)


def sbc_ratio(sbc: Optional[float], revenue: Optional[float], *, year: Optional[int] = None) -> Metric:
    inputs = {"sbc": sbc, "revenue": revenue}
    formula = "SBC % = Stock-based compensation / Revenue"
    thresholds = "> 10%: red; 5–10%: amber; < 5%: green"
    cite = "Practitioner convention"
    v = _div(sbc, revenue)
    if v is None:
        return Metric("sbc", "Stock-based compensation / revenue", None, "pct", GREY, formula, inputs, thresholds, cite,
                      note="Stock-based compensation not tagged in the cash-flow statement" if sbc is None else "Revenue not reported", year=year)
    light = RED if v > 0.10 else AMBER if v > 0.05 else GREEN
    return Metric("sbc", "Stock-based compensation / revenue", v, "pct", light, formula, inputs, thresholds, cite, year=year)


def is_financial_sic(sic: Optional[str]) -> bool:
    try:
        n = int(str(sic)[:4])
    except (TypeError, ValueError):
        return False
    return 6000 <= n <= 6799
