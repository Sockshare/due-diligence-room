"""XBRL companyfacts fact selection.

The SEC companyfacts JSON contains every value a company has ever tagged, across
every filing. The same fiscal-year number appears in several filings (the
original 10-K plus two or three later 10-Ks as a comparative), sometimes with
different values (restatements), sometimes under several units, and sometimes
with duplicate "frames". This module turns that into one clean annual series per
concept with an audit trail:

* only annual-report forms (10-K, 10-K/A, 10-KT, 20-F, 40-F) are used;
* duration concepts must span roughly one year (340–390 days) so quarterly and
  year-to-date values never leak in;
* periods are keyed by fiscal-year *end date*, labelled by the calendar year of
  that date (Apple's FY ending 2024-09-28 is "2024");
* when several filings report the same period, the **latest filed wins** and the
  superseded values are kept on the Fact so the memo can say "restated";
* each Fact records the concept, unit, accession and filing date it came from.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A"}
CURRENCY_LIKE = ("USD", "EUR", "GBP", "CAD", "JPY", "CHF", "AUD", "CNY", "HKD", "INR", "KRW", "SEK", "DKK", "NOK", "BRL", "MXN", "ILS", "SGD", "TWD")


@dataclass
class Fact:
    name: str                 # friendly series name, e.g. "revenue"
    concept: str              # "us-gaap:Revenues"
    value: float
    unit: str
    end: str                  # period end (YYYY-MM-DD)
    start: Optional[str]
    year: int                 # fiscal-year label
    accn: str
    form: str
    filed: str
    frame: Optional[str] = None
    restated: bool = False
    superseded: list[dict] = field(default_factory=list)  # earlier values for same period

    def as_dict(self) -> dict:
        return {
            "name": self.name, "concept": self.concept, "value": self.value, "unit": self.unit,
            "end": self.end, "start": self.start, "year": self.year, "accn": self.accn,
            "form": self.form, "filed": self.filed, "frame": self.frame, "restated": self.restated,
            "superseded": list(self.superseded),
        }


def _parse_date(s: str) -> Optional[date]:
    try:
        y, m, d = s.split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None


def fiscal_year_label(end: str) -> int:
    d = _parse_date(end)
    return d.year if d else 0


def choose_unit(units: dict, kind: str) -> Optional[str]:
    """kind: 'money' | 'shares' | 'per_share' | 'pure'."""
    if not units:
        return None
    if kind == "money":
        if "USD" in units:
            return "USD"
        for u in units:
            if u in CURRENCY_LIKE:
                return u
        return None
    if kind == "shares":
        return "shares" if "shares" in units else None
    if kind == "per_share":
        if "USD/shares" in units:
            return "USD/shares"
        for u in units:
            if u.endswith("/shares"):
                return u
        return None
    if kind == "pure":
        return "pure" if "pure" in units else next(iter(units))
    return None


def _is_annual_duration(entry: dict) -> bool:
    s, e = _parse_date(entry.get("start") or ""), _parse_date(entry.get("end") or "")
    if not s or not e:
        return False
    days = (e - s).days
    return 340 <= days <= 390


def annual_series(facts: dict, concept: str, *, name: str, kind: str = "money", instant: bool = False,
                  taxonomy: str = "us-gaap", forms: Iterable[str] = ANNUAL_FORMS) -> dict[int, Fact]:
    """Return {fiscal_year_label: Fact} for one concept."""
    tax = (facts or {}).get("facts", {}).get(taxonomy, {})
    node = tax.get(concept)
    if not node:
        return {}
    units = node.get("units", {})
    unit = choose_unit(units, kind)
    if unit is None:
        return {}
    entries = units.get(unit, [])
    forms = set(forms)
    by_end: dict[str, list[dict]] = {}
    for ent in entries:
        if ent.get("form") not in forms:
            continue
        if ent.get("val") is None:
            continue
        if instant:
            if ent.get("start"):
                continue
        else:
            if not _is_annual_duration(ent):
                continue
        by_end.setdefault(ent["end"], []).append(ent)

    per_year: dict[int, Fact] = {}
    for end, group in by_end.items():
        # latest filed wins; tie-break: entries carrying a canonical frame, then accession
        group.sort(key=lambda e: (e.get("filed", ""), 1 if e.get("frame") else 0, e.get("accn", "")), reverse=True)
        best = group[0]
        distinct_vals: list[dict] = []
        seen_vals = set()
        for e in group:
            v = float(e["val"])
            if v not in seen_vals:
                seen_vals.add(v)
                distinct_vals.append({"value": v, "accn": e.get("accn"), "filed": e.get("filed"), "form": e.get("form")})
        fact = Fact(
            name=name, concept=f"{taxonomy}:{concept}", value=float(best["val"]), unit=unit, end=end,
            start=best.get("start"), year=fiscal_year_label(end), accn=best.get("accn", ""),
            form=best.get("form", ""), filed=best.get("filed", ""), frame=best.get("frame"),
            restated=len(distinct_vals) > 1, superseded=distinct_vals[1:],
        )
        y = fact.year
        # two period-ends in one label year (transition periods): keep the later end date
        if y in per_year and per_year[y].end > fact.end:
            continue
        per_year[y] = fact
    return per_year


def merged_series(facts: dict, concepts: list[str], *, name: str, kind: str = "money", instant: bool = False,
                  taxonomy: str = "us-gaap") -> tuple[dict[int, Fact], list[str]]:
    """Merge a priority list of concepts into one series.

    The *primary* concept is the first in priority order that covers the most
    recent year reported by any candidate; remaining years are filled from the
    others. Returns (series, concepts_used_in_order).
    """
    candidates = [(c, annual_series(facts, c, name=name, kind=kind, instant=instant, taxonomy=taxonomy)) for c in concepts]
    candidates = [(c, s) for c, s in candidates if s]
    if not candidates:
        return {}, []
    latest_year = max(max(s) for _, s in candidates)
    ordered = sorted(candidates, key=lambda cs: (0 if latest_year in cs[1] else 1, -len(cs[1]), concepts.index(cs[0])))
    out: dict[int, Fact] = {}
    used: list[str] = []
    for concept, series in ordered:
        added = False
        for y, f in series.items():
            if y not in out:
                out[y] = f
                added = True
        if added:
            used.append(f"{taxonomy}:{concept}")
    return out, used


def latest_instant(facts: dict, concept: str, *, name: str, kind: str = "shares", taxonomy: str = "dei") -> Optional[Fact]:
    """Most recent instant value across *all* forms (used for dei shares outstanding)."""
    node = (facts or {}).get("facts", {}).get(taxonomy, {}).get(concept)
    if not node:
        return None
    unit = choose_unit(node.get("units", {}), kind)
    if not unit:
        return None
    entries = [e for e in node["units"][unit] if e.get("val") is not None and not e.get("start")]
    if not entries:
        return None
    entries.sort(key=lambda e: (e.get("end", ""), e.get("filed", "")), reverse=True)
    # Some filers tag several share classes for the same date; sum them.
    top_end = entries[0]["end"]
    same = [e for e in entries if e["end"] == top_end]
    total = sum(float(e["val"]) for e in {(e.get("accn"), e.get("val")): e for e in same}.values()) if len(same) > 1 else float(same[0]["val"])
    best = same[0]
    return Fact(name=name, concept=f"{taxonomy}:{concept}", value=total, unit=unit, end=top_end, start=None,
                year=fiscal_year_label(top_end), accn=best.get("accn", ""), form=best.get("form", ""),
                filed=best.get("filed", ""), frame=best.get("frame"))


# ----------------------------------------------------------------------------
# Standard concept map used by the financial model. Order = priority.
# ----------------------------------------------------------------------------
CONCEPTS: dict[str, dict] = {
    "revenue": {"concepts": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                             "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet",
                             "RevenuesNetOfInterestExpense", "InterestAndDividendIncomeOperating"]},
    "cogs": {"concepts": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices",
                          "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"]},
    "gross_profit": {"concepts": ["GrossProfit"]},
    "operating_income": {"concepts": ["OperatingIncomeLoss"]},
    "net_income": {"concepts": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]},
    "pretax_income": {"concepts": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                                   "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]},
    "income_tax": {"concepts": ["IncomeTaxExpenseBenefit"]},
    "interest_expense": {"concepts": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"]},
    "cfo": {"concepts": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]},
    "cfi": {"concepts": ["NetCashProvidedByUsedInInvestingActivities", "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"]},
    "capex": {"concepts": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
                           "PaymentsToAcquirePropertyPlantAndEquipmentAndIntangibleAssets"]},
    "da": {"concepts": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                        "DepreciationAmortizationAndAccretionNet", "Depreciation", "DepreciationAmortizationAndOther"]},
    "sga": {"concepts": ["SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense"]},
    "rnd": {"concepts": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"]},
    "sbc": {"concepts": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"]},
    "buybacks": {"concepts": ["PaymentsForRepurchaseOfCommonStock"]},
    "dividends": {"concepts": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]},
    "total_assets": {"concepts": ["Assets"], "instant": True},
    "current_assets": {"concepts": ["AssetsCurrent"], "instant": True},
    "current_liabilities": {"concepts": ["LiabilitiesCurrent"], "instant": True},
    "total_liabilities": {"concepts": ["Liabilities"], "instant": True},
    "equity": {"concepts": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], "instant": True},
    "retained_earnings": {"concepts": ["RetainedEarningsAccumulatedDeficit"], "instant": True},
    "receivables": {"concepts": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent", "AccountsReceivableNet"], "instant": True},
    "inventory": {"concepts": ["InventoryNet", "InventoryFinishedGoodsNetOfReserves"], "instant": True},
    "ppe": {"concepts": ["PropertyPlantAndEquipmentNet", "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"], "instant": True},
    "cash": {"concepts": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"], "instant": True},
    "short_investments": {"concepts": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"], "instant": True},
    "long_investments": {"concepts": ["LongTermInvestments", "MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"], "instant": True},
    "ltd": {"concepts": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt", "LongTermNotesPayable", "ConvertibleLongTermNotesPayable"], "instant": True},
    "std": {"concepts": ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings", "LongTermDebtAndCapitalLeaseObligationsCurrent", "CommercialPaper", "NotesPayableCurrent", "NotesPayableRelatedPartiesClassifiedCurrent", "ConvertibleNotesPayableCurrent"], "instant": True},
    "goodwill": {"concepts": ["Goodwill"], "instant": True},
    "shares_diluted": {"concepts": ["WeightedAverageNumberOfDilutedSharesOutstanding"], "kind": "shares"},
    "shares_basic": {"concepts": ["WeightedAverageNumberOfSharesOutstandingBasic"], "kind": "shares"},
    "eps_diluted": {"concepts": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"], "kind": "per_share"},
}

LABELS = {
    "revenue": "Revenue", "cogs": "Cost of revenue", "gross_profit": "Gross profit", "operating_income": "Operating income",
    "net_income": "Net income", "pretax_income": "Pre-tax income", "income_tax": "Income tax", "interest_expense": "Interest expense",
    "cfo": "Cash from operations", "cfi": "Cash from investing", "capex": "Capital expenditure", "da": "Depreciation & amortization",
    "sga": "SG&A", "rnd": "R&D", "sbc": "Stock-based compensation", "buybacks": "Share repurchases", "dividends": "Dividends paid",
    "total_assets": "Total assets", "current_assets": "Current assets", "current_liabilities": "Current liabilities",
    "total_liabilities": "Total liabilities", "equity": "Shareholders' equity", "retained_earnings": "Retained earnings",
    "receivables": "Accounts receivable", "inventory": "Inventory", "ppe": "PP&E (net)", "cash": "Cash & equivalents",
    "short_investments": "Short-term investments", "long_investments": "Long-term investments", "ltd": "Long-term debt",
    "std": "Short-term debt", "goodwill": "Goodwill", "shares_diluted": "Diluted shares (weighted)",
    "shares_basic": "Basic shares (weighted)", "eps_diluted": "Diluted EPS",
}


@dataclass
class FinancialTable:
    years: list[int]
    series: dict[str, dict[int, Fact]]
    concepts_used: dict[str, list[str]]
    gaps: list[str]
    shares_outstanding: Optional[Fact] = None
    currency: str = "USD"

    def get(self, name: str, year: int) -> Optional[Fact]:
        return self.series.get(name, {}).get(year)

    def value(self, name: str, year: int) -> Optional[float]:
        f = self.get(name, year)
        return f.value if f else None

    def has(self, name: str) -> bool:
        return bool(self.series.get(name))

    def as_dict(self) -> dict:
        return {
            "years": self.years,
            "series": {n: {str(y): f.as_dict() for y, f in s.items()} for n, s in self.series.items()},
            "concepts_used": self.concepts_used,
            "gaps": self.gaps,
            "shares_outstanding": self.shares_outstanding.as_dict() if self.shares_outstanding else None,
            "currency": self.currency,
        }


def build_table(facts: dict, years: int = 5) -> FinancialTable:
    series: dict[str, dict[int, Fact]] = {}
    used: dict[str, list[str]] = {}
    gaps: list[str] = []
    for name, spec in CONCEPTS.items():
        s, u = merged_series(facts, spec["concepts"], name=name, kind=spec.get("kind", "money"), instant=spec.get("instant", False))
        if s:
            series[name] = s
            used[name] = u
        else:
            gaps.append(name)
    # derive gross profit if missing
    if "gross_profit" not in series and "revenue" in series and "cogs" in series:
        gp: dict[int, Fact] = {}
        for y, rev in series["revenue"].items():
            c = series["cogs"].get(y)
            if c:
                gp[y] = Fact(name="gross_profit", concept=f"derived:{rev.concept}-{c.concept}", value=rev.value - c.value, unit=rev.unit,
                             end=rev.end, start=rev.start, year=y, accn=rev.accn, form=rev.form, filed=rev.filed)
        if gp:
            series["gross_profit"] = gp
            used["gross_profit"] = ["derived: revenue − cost of revenue"]
            gaps = [g for g in gaps if g != "gross_profit"]
    # derive total liabilities if missing
    if "total_liabilities" not in series and "total_assets" in series and "equity" in series:
        tl: dict[int, Fact] = {}
        for y, ta in series["total_assets"].items():
            eq = series["equity"].get(y)
            if eq:
                tl[y] = Fact(name="total_liabilities", concept=f"derived:{ta.concept}-{eq.concept}", value=ta.value - eq.value, unit=ta.unit,
                             end=ta.end, start=None, year=y, accn=ta.accn, form=ta.form, filed=ta.filed)
        if tl:
            series["total_liabilities"] = tl
            used["total_liabilities"] = ["derived: total assets − equity"]
            gaps = [g for g in gaps if g != "total_liabilities"]

    anchor = series.get("revenue") or series.get("net_income") or series.get("total_assets") or {}
    all_years = sorted(anchor.keys())
    ys = all_years[-years:] if all_years else []
    currency = "USD"
    if "revenue" in series and ys:
        currency = series["revenue"][ys[-1]].unit
    so = latest_instant(facts, "EntityCommonStockSharesOutstanding", name="shares_outstanding", kind="shares", taxonomy="dei")
    return FinancialTable(years=ys, series=series, concepts_used=used, gaps=gaps, shares_outstanding=so, currency=currency)
