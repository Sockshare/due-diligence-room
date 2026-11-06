"""Shared run context and citation helpers for the five Due-Diligence Room agents."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from agentkit import Agent, ApprovalGate, Blackboard, EventBus, LLMClient

from .. import fmt
from ..sec import SecClient
from ..xbrl import Fact, FinancialTable

DEPTHS = {
    "quick": {"years": 3, "prior_10k": False, "form4": 0, "peers": False, "holders": False, "efts": False},
    "standard": {"years": 5, "prior_10k": True, "form4": 20, "peers": True, "holders": True, "efts": True},
    "deep": {"years": 10, "prior_10k": True, "form4": 40, "peers": True, "holders": True, "efts": True},
}

SECTION_ORDER = [
    ("executive_summary", "Executive Summary", "Editor"),
    ("business", "Business Overview", "Analyst"),
    ("financials", "Financial Trends", "Analyst"),
    ("valuation", "Valuation Snapshot", "Analyst"),
    ("peers", "Peer Comparison", "Analyst"),
    ("forensics", "Forensic Accounting", "Forensic Accountant"),
    ("audit_events", "Auditor Changes, Restatements & Going Concern", "Forensic Accountant"),
    ("risk_factors", "Risk Factors (Item 1A) & Year-over-Year Changes", "Legal"),
    ("legal_proceedings", "Legal Proceedings (Item 3)", "Legal"),
    ("material_events", "Material Events — 8-K Timeline", "Legal"),
    ("insiders", "Insider Activity & Notable Holders", "Legal"),
    ("compensation", "Executive Compensation", "Legal"),
    ("bear_case", "Bear Case", "Skeptic"),
    ("disagreements", "Unresolved Disagreements", "Skeptic"),
    ("data_quality", "Data Quality & Gaps", "Skeptic"),
]


@dataclass
class RunContext:
    ticker: str
    cik: int
    name: str
    depth: str
    client: SecClient
    bus: EventBus
    bb: Blackboard
    llm: LLMClient
    gate: ApprovalGate
    today: date = field(default_factory=date.today)
    subs: Optional[dict] = None
    facts: Optional[dict] = None
    table: Optional[FinancialTable] = None
    prices: dict = field(default_factory=dict)
    sic: Optional[str] = None
    sic_desc: str = ""
    tenk: list[dict] = field(default_factory=list)          # latest 10-K filings (index rows)
    tenk_items: dict = field(default_factory=dict)          # accn -> {item: ItemExtraction}
    tenk_text: dict = field(default_factory=dict)           # accn -> full text
    tenk_html: dict = field(default_factory=dict)           # accn -> raw html
    errors: list[str] = field(default_factory=list)

    @property
    def cfg(self) -> dict:
        return DEPTHS.get(self.depth, DEPTHS["standard"])

    @property
    def currency(self) -> str:
        return self.table.currency if self.table else "USD"


class DDAgent(Agent):
    """Agent with citation helpers bound to a RunContext."""

    def __init__(self, rc: RunContext):
        super().__init__(rc.bb, rc.bus, rc.llm, ctx={"ticker": rc.ticker})
        self.rc = rc

    # ------------------------------------------------------------ sources
    def src_fact(self, f: Fact) -> str:
        url = self.rc.client.filing_index_url(self.rc.cik, f.accn) if f.accn else "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json" % self.rc.cik
        label = f"{f.concept} FY{f.year} (period end {f.end}) — {f.form} {f.accn} filed {f.filed}"
        if f.restated:
            label += f"; restated: earlier value(s) {', '.join(fmt.money(s['value'], f.unit) if f.unit in fmt.CURRENCY_SYMBOLS else fmt.count(s['value']) for s in f.superseded)}"
        return self.bb.add_source("xbrl", url, label, concept=f.concept, accn=f.accn, form=f.form, filed=f.filed, period=f.end)

    def src_filing(self, accn: str, form: str, filed: str, doc: str = "", what: str = "") -> str:
        url = self.rc.client.archive_url(self.rc.cik, accn, doc) if doc else self.rc.client.filing_index_url(self.rc.cik, accn)
        label = f"{form} {accn} filed {filed}" + (f" — {what}" if what else "")
        return self.bb.add_source("filing", url, label, accn=accn, form=form, filed=filed, period=what or None)

    def src_index(self) -> str:
        url = "https://data.sec.gov/submissions/CIK%010d.json" % self.rc.cik
        return self.bb.add_source("index", url, f"EDGAR submissions index for CIK {self.rc.cik}")

    def src_facts_api(self) -> str:
        url = "https://data.sec.gov/api/xbrl/companyfacts/CIK%010d.json" % self.rc.cik
        return self.bb.add_source("index", url, f"XBRL companyfacts API for CIK {self.rc.cik}")

    def src_market(self) -> Optional[str]:
        p = self.rc.prices
        if not p or not p.get("ok"):
            return None
        return self.bb.add_source("market", p.get("source", ""), f"Yahoo Finance chart endpoint, last close {p.get('latest_date')} (unofficial, best-effort)",
                                  period=p.get("latest_date"))

    def src_efts(self, url_hint: str, label: str) -> str:
        return self.bb.add_source("efts", url_hint, label)

    def src_derived(self, label: str, inputs: list[str], anchor: str) -> str:
        return self.bb.add_source("derived", f"/methodology#{anchor}", label, inputs=inputs, note="derived from the listed inputs; see methodology")

    # ------------------------------------------------------------ citations
    def c(self, text: str, sid: Optional[str]) -> str:
        return self.bb.cite(text, sid) if sid else text

    def cf(self, name: str, year: int, formatter=None) -> str:
        """Cite a table fact by series name/year with a formatter (defaults to money)."""
        t = self.rc.table
        f = t.get(name, year) if t else None
        if f is None:
            return "n/a"
        formatter = formatter or (lambda v: fmt.money(v, f.unit))
        return self.bb.cite(formatter(f.value), self.src_fact(f))

    def fact_sources(self, names: list[str], year: int) -> list[str]:
        out = []
        t = self.rc.table
        for n in names:
            f = t.get(n, year) if t else None
            if f:
                out.append(self.src_fact(f))
        return out

    def derived(self, text: str, label: str, inputs: list[str], anchor: str) -> str:
        return self.bb.cite(text, self.src_derived(label, inputs, anchor))

    def q(self, text: str, sid: Optional[str]) -> str:
        """Quote filing text / claim text with every figure auto-linked to ``sid``."""
        from ..memo import cite_all_figures
        return cite_all_figures(text, sid)

    # -------------------------------------------------------------- misc
    def gap(self, section: str, what: str, why: str = "") -> None:
        self.bb.add_gap(section, what, why)
        self.say(f"gap: {what}" + (f" ({why})" if why else ""), status="warn")
