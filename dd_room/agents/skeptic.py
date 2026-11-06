"""Skeptic: mandatory bear case. Attacks every bullish claim on the blackboard,
states what evidence would change its mind, flags poor data quality, and lists
unresolved disagreements. Deterministic in offline mode; an LLM (if configured)
may sharpen the prose but the structure and numbers come from the blackboard."""
from __future__ import annotations

from .. import fmt
from .base import DDAgent

SYSTEM = ("You are the designated skeptic on an investment committee. Rewrite the bear-case bullets below into tighter prose. "
          "Keep every bracketed citation like [text][S12] exactly as written. Do not add numbers that are not in the input. Max 250 words.")

# claim tag -> (counter template, evidence that would change my mind, default severity)
COUNTERS: dict[str, tuple[str, str, str]] = {
    "revenue_growth": ("A multi-year CAGR hides the shape of the curve: check whether the latest year decelerated, how much came from price vs volume, and whether acquisitions inflated it.",
                       "Organic growth disclosure in MD&A that matches the headline, and a latest-year growth rate at or above the CAGR.", "medium"),
    "margin_expansion": ("Margin expansion can be cyclical (input-cost tailwinds, under-investment) or accounting-driven (capitalised costs, longer depreciation lives). It mean-reverts more often than it persists.",
                         "Stable or rising R&D and capex intensity alongside the margin gain, and no increase in the DEPI index.", "medium"),
    "fcf_strong": ("Free cash flow here is CFO minus capex only; it ignores stock-based compensation (a real cost settled in shares), acquisitions and lease principal. Adjusted for SBC the picture may be materially weaker.",
                   "FCF after SBC and after acquisitions still comfortably positive, and working-capital changes not the main driver of CFO.", "medium"),
    "net_cash": ("Net cash is computed from cash and short-term investments against *reported* debt; it omits leases, pensions, guarantees and any structured financing. Cash abroad may be tax-trapped.",
                 "A reconciliation of total obligations (including operating leases and other long-term liabilities) that still leaves a surplus.", "low"),
    "high_roic": ("Our ROIC uses book equity, which buybacks and write-offs shrink; a company that has repurchased heavily will show a flattering ratio regardless of underlying returns.",
                  "ROIC computed on gross invested capital (adding back accumulated buybacks and goodwill write-offs) that remains above the cost of capital.", "medium"),
    "diversified": ("Line-item diversity is not economic diversity if the lines share customers, suppliers or a single platform. Services attached to a product base rise and fall with it.",
                    "Segment-level disclosure showing uncorrelated demand drivers and independent margins.", "low"),
    "cheap_valuation": ("A low multiple is often correct: it can price in shrinking earnings, a cyclical peak or balance-sheet risk. Trailing P/E on peak earnings is a classic trap.",
                        "Forward estimates (not provided by this tool) that hold or grow earnings, and a normalised-earnings multiple that is still low.", "medium"),
    "beneish_clean": ("A clean M-Score has a meaningful false-negative rate; it was calibrated on 1980s–90s manipulators and says nothing about aggressive but GAAP-compliant choices (channel stuffing within limits, reserve releases).",
                      "Consistent CFO/NI above one over several years, stable DSO and no restatements.", "low"),
    "cash_backed": ("A single-year CFO/NI above one can come from working-capital timing (stretching payables, collecting receivables early) rather than durable cash earnings.",
                    "CFO/NI above one in each of the last three years with working-capital changes a small part of CFO.", "low"),
    "altman_safe": ("Altman Z is driven by market cap (X4) for large companies; a falling share price can move a 'safe' company into the grey zone without any operational change.",
                    "A safe-zone Z when X4 is recomputed with the stock price down by half.", "low"),
    "buyback_shrink": ("Shrinking share count only creates value if shares were bought below intrinsic value; buybacks at rich multiples transfer wealth from remaining holders to sellers (often insiders exercising options).",
                       "Buyback prices below the current price on average and insider selling not concentrated around buyback windows.", "low"),
    "no_going_concern": ("Absence of going-concern language is the baseline for any solvent large company and carries no positive information.", "Not applicable — this is a floor, not a merit.", "low"),
    "no_litigation": ("Brief Item 3 text often means proceedings are discussed in the notes to the financial statements instead; it does not mean there are none.",
                      "The commitments-and-contingencies note confirming no material accruals or reasonably-possible losses.", "low"),
    "insider_buying": ("Open-market insider buying can be token-sized relative to pay or part of a pre-arranged plan; weight it by size relative to the insider's holdings and compensation.",
                       "Purchases that are large relative to the buyer's annual compensation and not under a 10b5-1 plan.", "low"),
}

BEARISH_TEMPLATES: dict[str, str] = {
    "concentration": "Revenue concentration means a single product cycle or region can swing results.",
    "revenue_decline": "A shrinking top line makes every margin and valuation metric look worse than it appears.",
    "margin_contraction": "Margins are contracting; without a credible cost or mix story, earnings growth will lag revenue.",
    "losses": "Persistent losses mean the company depends on capital markets or cash reserves to operate.",
    "rich_valuation": "A rich multiple leaves no room for execution slips; de-rating risk alone can dominate returns.",
    "beneish_flag": "The Beneish screen is in the warning zone — earnings quality deserves a line-by-line look before trusting reported profits.",
    "altman_distress": "Altman Z in the distress zone — financing risk is a first-order concern.",
    "dso_red": "Receivables are growing faster than revenue — a classic early signal of channel stuffing or weakening customers.",
    "dio_red": "Inventory days jumped — risk of write-downs or weaker demand than reported revenue suggests.",
    "sloan_red": "High accruals: reported earnings are running ahead of cash; Sloan (1996) finds such firms underperform.",
    "sbc_red": "Stock-based compensation is a large share of revenue; GAAP earnings and 'adjusted' earnings diverge materially and dilution is structural.",
    "dilution_red": "The share count is rising quickly; per-share value needs the business to grow faster than the dilution.",
    "auditor_change": "An auditor change (8-K Item 4.01) is a governance yellow flag — read the filing for disagreements.",
    "restatement": "A non-reliance 8-K (Item 4.02) means past numbers were wrong; apply a discount to all historical ratios.",
    "going_concern": "Substantial-doubt going-concern language is the strongest solvency warning a filer can give.",
    "risk_rewrite": "A heavily rewritten risk-factor section signals that management sees the landscape differently from a year ago.",
    "litigation": "Active regulatory or class-action matters can produce step-change costs or business-model constraints.",
    "turnover": "Elevated officer/director turnover often precedes strategic or accounting changes.",
    "distress_8k": "Distress-type 8-K items (impairments, debt acceleration, delisting notices) are present.",
    "insider_selling": "Insiders have been net sellers — not necessarily informative (diversification, taxes) but the absence of buying is notable.",
}


class Skeptic(DDAgent):
    name = "Skeptic"
    role = "Bear case, counters to every bullish claim, data-quality flags, unresolved disagreements"
    color = "#f26d6d"

    def run(self) -> None:
        self.step("attack bullish claims", self.attack_claims)
        self.step("write bear case", self.bear_case)
        self.step("data quality review", self.data_quality)
        self.step("list unresolved disagreements", self.disagreements)

    def _severity(self, tag: str) -> str:
        bb = self.bb
        fin = bb.sections.get("financials").data if "financials" in bb.sections else {}
        summ = (fin or {}).get("summary", {})
        forensic = {m["key"]: m for m in (bb.sections.get("forensics").data.get("metrics", []) if "forensics" in bb.sections else [])}
        if tag == "revenue_growth" and summ.get("revenue_yoy") is not None and summ.get("revenue_cagr") is not None:
            return "high" if summ["revenue_yoy"] < summ["revenue_cagr"] / 2 else "low"
        if tag == "fcf_strong":
            sbc = forensic.get("sbc")
            return "high" if sbc and sbc.get("value") and sbc["value"] > 0.08 else "medium"
        if tag == "cash_backed":
            sl = forensic.get("sloan")
            return "high" if sl and sl.get("light") in ("red", "amber") else "low"
        if tag == "margin_expansion":
            b = forensic.get("beneish")
            depi = (b or {}).get("components", {}).get("DEPI")
            return "high" if depi and depi > 1.1 else "medium"
        if tag == "high_roic":
            rows = (fin or {}).get("rows", [])
            if rows and rows[-1].get("buybacks") and rows[-1].get("revenue") and rows[-1]["buybacks"] > 0.1 * rows[-1]["revenue"]:
                return "high"
            return "medium"
        return COUNTERS.get(tag, ("", "", "medium"))[2]

    def attack_claims(self) -> str:
        bb = self.bb
        n = 0
        for c in bb.claims:
            if c.kind != "bullish":
                continue
            tpl = COUNTERS.get(c.tag)
            if tpl:
                counter, evidence, _ = tpl
            else:
                counter = "This claim rests on a single metric from one filing year; it needs corroboration from a second, independent indicator before it should move a decision."
                evidence = "A second metric pointing the same way for at least two consecutive years."
            bb.add_counter(c.id, counter, evidence, severity=self._severity(c.tag))
            n += 1
        self.say(f"countered {n} bullish claim(s)")
        return f"{n} claims countered"

    def bear_case(self) -> None:
        bb = self.bb
        bb.update_section("bear_case", status="in_progress")
        bearish = [c for c in bb.claims if c.kind == "bearish"]
        bullets = []
        for c in bearish:
            why = BEARISH_TEMPLATES.get(c.tag, "")
            cite = ""
            if c.support:
                cite = " " + " ".join(self.c("(source)", s) for s in c.support[:2])
            bullets.append(f"- **{self.q(c.text, c.support[0] if c.support else None)}** — {why}{cite} _(raised by {c.agent})_")
        if not bullets:
            bullets.append("- No bearish claims were raised by the other agents. That itself is a warning: the data set is limited to what EDGAR and a price feed can show, and the absence of red flags is not evidence of safety.")
        # structural bear points that always apply
        val = bb.sections.get("valuation").data if "valuation" in bb.sections else {}
        if val and val.get("pe") is not None and val["pe"] > 20:
            pe_src = next((sid for sid, src in bb.sources.items() if src.kind == "derived" and src.label == "P/E (trailing FY)"), None)
            bullets.append(f"- **Multiple compression risk.** At a trailing P/E of {self.c(fmt.ratio(val['pe'], 1), pe_src)}, a reversion to a market-average multiple would offset several years of earnings growth. _(Skeptic, from the valuation section's sourced figures)_")
        gaps = [g for g in bb.gaps]
        if gaps:
            bullets.append(f"- **Blind spots.** {len(gaps)} data gaps were logged (see Data Quality). Anything this tool cannot see — off-balance-sheet commitments, customer concentration, management quality — is unpriced here.")
        counters_md = []
        for ctr in bb.counters:
            claim = next((c for c in bb.claims if c.id == ctr["claim_id"]), None)
            if not claim:
                continue
            counters_md.append(f"| {claim.agent}: {self.q(claim.text, claim.support[0] if claim.support else None)} | {ctr['counter']} | {ctr['evidence_to_change_mind']} | {ctr['severity']} |")
        parts = ["The Skeptic's job is to make the strongest honest case against the investment. Every bullish statement another agent put on the blackboard is answered below, together with the evidence that would change the Skeptic's mind.",
                 "### Bear thesis\n\n" + "\n".join(bullets)]
        fallback = "\n".join(bullets)
        text, mode = self.write("bear case prose", SYSTEM, fallback, fallback="")
        if text and mode != "offline":
            parts.append(f"### Bear thesis — narrative ({mode})\n\n{text}")
        if counters_md:
            parts.append("### Counters to bullish claims\n\n| Bullish claim | Skeptic's counter | What would change my mind | Severity |\n|---|---|---|---|\n" + "\n".join(counters_md))
        else:
            parts.append("### Counters to bullish claims\n\n_No bullish claims were made._")
        bb.update_section("bear_case", status="done", narrative="\n\n".join(parts), completeness=1.0,
                          data={"bearish_claims": [c.id for c in bearish], "countered": len(bb.counters)})

    def data_quality(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("data_quality", status="in_progress")
        flags: list[str] = []
        t = rc.table
        if t:
            restated = [(n, y) for n, s in t.series.items() for y, f in s.items() if f.restated and y in t.years]
            if restated:
                flags.append(f"**{len(restated)} restated XBRL value(s)** in the analysed years (latest filed value used): " + ", ".join(f"{n} FY{y}" for n, y in restated[:8]) + ".")
            if t.currency != "USD":
                flags.append(f"Reporting currency is **{t.currency}**; no FX conversion is applied and market multiples mix currencies if the quote is in USD.")
            if len(t.years) < rc.cfg["years"]:
                flags.append(f"Only **{len(t.years)} fiscal year(s)** of annual XBRL data available (requested {rc.cfg['years']}). Trends and CAGRs are correspondingly weaker.")
            missing_core = [g for g in t.gaps if g in ("revenue", "net_income", "cfo", "total_assets", "shares_diluted", "cogs", "sga", "receivables")]
            if missing_core:
                flags.append("Core concepts missing from companyfacts: **" + ", ".join(missing_core) + "** — the filer may use custom extension tags, which this tool does not read.")
            mixed = {n: u for n, u in t.concepts_used.items() if len(u) > 1}
            if mixed:
                flags.append(f"**{len(mixed)} series stitched from more than one XBRL concept** across years (e.g. {next(iter(mixed))}: {', '.join(mixed[next(iter(mixed))])}); definitions can differ subtly between concepts.")
        p = rc.prices
        if not p.get("ok"):
            flags.append("**No market price** — valuation multiples, market cap and Altman X4 are absent, not estimated.")
        else:
            flags.append(f"Market price comes from an **unofficial Yahoo Finance endpoint** (last close {p.get('latest_date')}); treat multiples as approximate.")
        fz = bb.sections.get("forensics").data if "forensics" in bb.sections else {}
        greys = [m["label"] for m in fz.get("metrics", []) if m.get("light") == "grey"]
        if greys:
            flags.append("Forensic metrics not computable: **" + ", ".join(greys) + "** (see notes in the Forensic section).")
        if rc.client.last_errors:
            flags.append(f"**{len(rc.client.last_errors)} upstream fetch error(s)** during this run; e.g. `{rc.client.last_errors[-1][:160]}`.")
        gaps_md = "\n".join(f"- [{g['section']}] {g['what']}" + (f" — {g['why']}" if g.get("why") else "") for g in bb.gaps) or "- none logged"
        parts = ["Everything above is only as good as the inputs. The flags below were raised automatically; an analyst should resolve each before relying on the affected section.",
                 "### Quality flags\n\n" + ("\n".join(f"- {f}" for f in flags) if flags else "- none"),
                 "### Logged data gaps\n\n" + gaps_md,
                 "### Known limitations of this tool\n\n- Reads only non-dimensional XBRL facts (no segment data from the API).\n- Does not read custom extension tags, footnotes or non-GAAP reconciliations.\n- Risk-factor and legal analyses are keyword/sentence based, not legal review.\n- Peer sets are SIC-code based.\n- 13F holder list is a name search, not a holdings ranking."]
        bb.update_section("data_quality", status="done", narrative="\n\n".join(parts), completeness=1.0, data={"flags": flags, "gaps": bb.gaps})

    def disagreements(self) -> None:
        bb = self.bb
        bb.update_section("disagreements", status="in_progress")
        rows = []
        for ctr in bb.counters:
            claim = next((c for c in bb.claims if c.id == ctr["claim_id"]), None)
            if not claim:
                continue
            resolved = ctr["severity"] == "low"
            bb.add_disagreement(claim.tag or claim.section, f"{claim.agent}: {claim.text}", f"Skeptic: {ctr['counter'][:160]}…", (claim.agent, self.name), resolved=resolved)
            if not resolved:
                rows.append(f"| {claim.section} | {claim.agent}: {self.q(claim.text, claim.support[0] if claim.support else None)} | {ctr['counter'][:200]} | {ctr['severity']} |")
        # cross-agent conflicts: e.g. Analyst bullish on growth while Forensic flags red
        fz = bb.sections.get("forensics").data if "forensics" in bb.sections else {}
        reds = set(fz.get("reds", []))
        if reds and any(c.kind == "bullish" and c.agent == "Analyst" for c in bb.claims):
            bb.add_disagreement("earnings quality", "Analyst: fundamentals look attractive", f"Forensic Accountant: red flags on {', '.join(sorted(reds))}", ("Analyst", "Forensic Accountant"))
            rows.append(f"| forensics | Analyst: fundamentals look attractive | Forensic Accountant: red flags on {', '.join(sorted(reds))} | high |")
        unresolved = [d for d in bb.disagreements if not d["resolved"]]
        md = ("Disagreements the agents could not settle from EDGAR data alone. Each one lowers the confidence of the section it touches and is a question for management, the auditor's report or primary research.\n\n"
              + ("| Section | Position | Counter-position | Severity |\n|---|---|---|---|\n" + "\n".join(rows) if rows else "_All counters were rated low severity; no unresolved disagreements._")
              + f"\n\n**{len(unresolved)} unresolved** of {len(bb.disagreements)} total disagreements.")
        bb.update_section("disagreements", status="done", narrative=md, completeness=1.0, data={"unresolved": len(unresolved), "total": len(bb.disagreements)})
