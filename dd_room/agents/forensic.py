"""Forensic Accountant: earnings-quality and solvency screens with full audit trail."""
from __future__ import annotations

from .. import filings, forensics, fmt, htmlitems
from .base import DDAgent

METRIC_ANCHORS = {"beneish": "beneish-m-score", "sloan": "sloan-accrual-ratio", "altman": "altman-z-score", "dso": "dso-dio",
                  "dio": "dso-dio", "cfo_ni": "cash-conversion", "dilution": "dilution", "sbc": "sbc"}

BENEISH_INPUT_SERIES = ["revenue", "cogs", "receivables", "current_assets", "ppe", "long_investments", "total_assets", "da", "sga", "ltd", "current_liabilities", "net_income", "cfo"]


class ForensicAccountant(DDAgent):
    name = "Forensic Accountant"
    role = "Beneish, Sloan, Altman, working-capital days, cash conversion, dilution, SBC, auditor events"
    color = "#f5a524"

    def run(self) -> None:
        self.step("forensic metrics", self.metrics)
        self.step("auditor changes, restatements, going concern", self.audit_events)

    def _inputs(self, y: int) -> dict:
        t = self.rc.table
        d = {k: t.value(k, y) for k in BENEISH_INPUT_SERIES}
        d["securities"] = d.pop("long_investments")
        # A filer with no long-term-debt concept at all almost always has none; assume 0 for LVGI and say so.
        if d["ltd"] is None and d["current_liabilities"] is not None:
            d["ltd"] = 0.0
            self._assumed_zero_ltd = True
        return d

    def metrics(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("forensics", status="in_progress")
        t = rc.table
        if not t or len(t.years) < 1:
            bb.update_section("forensics", status="done", narrative="_No annual XBRL data — forensic screens not computed._", completeness=0.0)
            return
        y = t.years[-1]
        py = t.years[-2] if len(t.years) >= 2 else None
        self._assumed_zero_ltd = False
        cur, prev = self._inputs(y), (self._inputs(py) if py else {})
        mets: list[forensics.Metric] = []
        mets.append(forensics.beneish_m_score(cur, prev, year=y, prior_year=py) if py else
                    forensics.Metric("beneish", "Beneish M-Score", None, "score", "grey", "see methodology", {}, "", forensics.BENEISH_CITATION, note="Needs two fiscal years", year=y))
        if self._assumed_zero_ltd and mets[-1].value is not None:
            mets[-1].note = (mets[-1].note + " " if mets[-1].note else "") + "No long-term-debt value tagged for one or both years; LTD assumed 0 in LVGI for the missing year(s)."
        mets.append(forensics.sloan_accrual_ratio(cur["net_income"], cur["cfo"], cur["total_assets"], prev.get("total_assets"), year=y))
        # Altman
        fin = forensics.is_financial_sic(rc.sic)
        mcap = bb.sections["valuation"].data.get("market_cap") if "valuation" in bb.sections else None
        wc = (cur["current_assets"] - cur["current_liabilities"]) if cur["current_assets"] is not None and cur["current_liabilities"] is not None else None
        ebit = t.value("operating_income", y)
        why = "SIC code is in the finance/insurance/real-estate range (6000–6799); Altman's 1968 model was built for industrial companies" if fin else ("Market cap unavailable (no price) — X4 cannot be computed" if mcap is None else "")
        mets.append(forensics.altman_z(wc, t.value("retained_earnings", y), ebit, mcap, t.value("total_liabilities", y), cur["revenue"], cur["total_assets"],
                                       year=y, applicable=(not fin and mcap is not None), why_not=why))
        prev_dso = (prev["receivables"] / prev["revenue"] * 365) if prev.get("receivables") and prev.get("revenue") else None
        mets.append(forensics.days_sales_outstanding(cur["receivables"], cur["revenue"], prev_dso, year=y))
        inv, pinv = t.value("inventory", y), (t.value("inventory", py) if py else None)
        prev_dio = (pinv / prev["cogs"] * 365) if pinv and prev.get("cogs") else None
        mets.append(forensics.days_inventory_outstanding(inv, cur["cogs"], prev_dio, year=y))
        mets.append(forensics.cash_conversion(cur["cfo"], cur["net_income"], year=y))
        sh_now, sh_first = t.value("shares_diluted", y), t.value("shares_diluted", t.years[0])
        mets.append(forensics.share_dilution(sh_now, sh_first, year=y, years_apart=max(1, y - t.years[0])))
        mets.append(forensics.sbc_ratio(t.value("sbc", y), cur["revenue"], year=y))

        # ---- narrative
        parts = [f"Screens computed on FY{y}" + (f" vs FY{py}" if py else "") + ". Each block lists the formula, the thresholds (with their source), the inputs and the XBRL facts behind them. "
                 "A red light is a prompt to investigate, not a verdict; these models have known false-positive rates."]
        summary_rows = []
        for m in mets:
            inputs_src = self._metric_inputs_sources(m, y, py)
            anchor = METRIC_ANCHORS[m.key]
            if m.value is None:
                val_txt = "n/a"
            else:
                val_txt = self.derived(self._fmt(m), f"{m.label} FY{y}", inputs_src, anchor)
            note_txt = self.q((m.note or '—').replace('|', '/'), self.src_derived(f"{m.label} FY{y}", inputs_src, anchor) if m.note else None)
            summary_rows.append(f"| {fmt.light_emoji(m.light)} | {m.label} | {val_txt} | {note_txt} |")
        parts.append("| Light | Metric | Value | Note |\n|---|---|---|---|\n" + "\n".join(summary_rows))
        for m in mets:
            parts.append(self._metric_block(m, y, py))
            self._claims_for(m, self.src_derived(f"{m.label} FY{y}", self._metric_inputs_sources(m, y, py), METRIC_ANCHORS[m.key]) if m.value is not None else None)
        reds = [m for m in mets if m.light == "red"]
        greys = [m for m in mets if m.light == "grey"]
        computed = [m for m in mets if m.value is not None]
        for g in greys:
            self.gap("forensics", f"{g.label} not computed", g.note)
        bb.update_section("forensics", status="done", narrative="\n\n".join(parts), completeness=len(computed) / len(mets),
                          data={"metrics": [m.as_dict() for m in mets], "year": y, "prior_year": py, "reds": [m.key for m in reds]})

    def _fmt(self, m: forensics.Metric) -> str:
        if m.unit == "pct":
            return fmt.pct(m.value)
        if m.unit == "days":
            return fmt.days(m.value)
        if m.unit == "x":
            return fmt.ratio(m.value, 2)
        return fmt.num(m.value, 2)

    def _metric_inputs_sources(self, m: forensics.Metric, y: int, py) -> list[str]:
        names = {
            "beneish": BENEISH_INPUT_SERIES, "sloan": ["net_income", "cfo", "total_assets"],
            "altman": ["current_assets", "current_liabilities", "retained_earnings", "operating_income", "total_liabilities", "revenue", "total_assets"],
            "dso": ["receivables", "revenue"], "dio": ["inventory", "cogs"], "cfo_ni": ["cfo", "net_income"],
            "dilution": ["shares_diluted"], "sbc": ["sbc", "revenue"],
        }[m.key]
        src = self.fact_sources(names, y)
        if py and m.key in ("beneish", "sloan", "dso", "dio"):
            src += self.fact_sources(names, py)
        if m.key == "dilution":
            src += self.fact_sources(names, self.rc.table.years[0])
        if m.key == "altman":
            ms = self.src_market()
            if ms:
                src.append(ms)
        return src

    def _metric_block(self, m: forensics.Metric, y: int, py) -> str:
        t = self.rc.table
        lines = [f"#### {fmt.light_emoji(m.light)} {m.label}", f"*Formula:* {m.formula}", f"*Thresholds:* {m.thresholds}", f"*Reference:* {m.citation}"]
        if m.note:
            lines.append(f"*Note:* {self.q(m.note, self.src_derived(f'{m.label} FY{y}', self._metric_inputs_sources(m, y, py), METRIC_ANCHORS[m.key]))}")
        if m.components:
            comps = ", ".join(f"{k} = {self.derived(fmt.num(v, 3), f'{m.label} {k}', self._metric_inputs_sources(m, y, py), METRIC_ANCHORS[m.key]) if v is not None else k + ' = n/a'}" for k, v in m.components.items())
            lines.append(f"*Components:* {comps}")
        # inputs table with sources
        rows = []
        first_year = t.years[0]
        alias = {"securities": "long_investments", "diluted_shares": "shares_diluted", "sales": "revenue", "ebit": "operating_income"}
        for k, v in m.inputs.items():
            if k.endswith("_t-1"):
                base, yr = k[:-4], py
            elif k.endswith("_t-n"):
                base, yr = k[:-4], first_year
            elif k.endswith("_t"):
                base, yr = k[:-2], y
            else:
                base, yr = k, y
            series_name = alias.get(base, base)
            f = t.get(series_name, yr) if (series_name in t.series and yr) else None
            if f is not None:
                shown = fmt.count(f.value) if f.unit == "shares" else fmt.money(f.value, f.unit)
                rows.append(f"| {k} | {self.c(shown, self.src_fact(f))} | `{f.concept}` FY{f.year} |")
            elif v is None:
                rows.append(f"| {k} | **missing** | not reported |")
            elif k == "years_apart":
                rows.append(f"| {k} | {int(v)} | parameter |")
            elif k == "market_cap":
                ms = self.src_market()
                rows.append(f"| {k} | {self.c(fmt.money(v, self.rc.currency), ms) if ms else fmt.money(v, self.rc.currency)} | market price × shares outstanding |")
            elif k in ("prior_dso", "prior_dio"):
                rows.append(f"| {k} | {self.derived(fmt.days(v), k, self._metric_inputs_sources(m, y, py), METRIC_ANCHORS[m.key])} | derived FY{py} |")
            else:
                rows.append(f"| {k} | {self.derived(fmt.money(v, self.rc.currency), k, self._metric_inputs_sources(m, y, py), METRIC_ANCHORS[m.key])} | derived |")
        if rows:
            lines.append("| Input | Value | XBRL concept / origin |\n|---|---|---|\n" + "\n".join(rows))
        return "\n\n".join(lines)

    def _claims_for(self, m: forensics.Metric, sid) -> None:
        bb = self.bb
        if m.value is None or sid is None:
            return
        src = [sid]
        if m.key == "beneish":
            if m.light == "green":
                bb.add_claim(self.name, "forensics", f"Beneish M-Score {fmt.num(m.value, 2)} is below the manipulation threshold", support=src, tag="beneish_clean")
            else:
                bb.add_claim(self.name, "forensics", f"Beneish M-Score {fmt.num(m.value, 2)} is in the {m.light} zone", kind="bearish", support=src, tag="beneish_flag")
        elif m.key == "cfo_ni" and m.light == "green":
            bb.add_claim(self.name, "forensics", f"Earnings are cash-backed (CFO/NI {fmt.ratio(m.value, 2)})", support=src, tag="cash_backed")
        elif m.key == "altman" and m.light == "green":
            bb.add_claim(self.name, "forensics", f"Altman Z {fmt.num(m.value, 2)} is in the safe zone", support=src, tag="altman_safe")
        elif m.key == "altman" and m.light == "red":
            bb.add_claim(self.name, "forensics", f"Altman Z {fmt.num(m.value, 2)} is in the distress zone", kind="bearish", support=src, tag="altman_distress")
        elif m.key == "dilution" and m.light == "green" and m.value < 0:
            bb.add_claim(self.name, "forensics", f"Share count is shrinking ({fmt.pct(m.value, signed=True)}/yr)", support=src, tag="buyback_shrink")
        elif m.key in ("dso", "dio", "sloan", "sbc", "dilution") and m.light == "red":
            bb.add_claim(self.name, "forensics", f"{m.label} is flagged red ({self._fmt(m)})", kind="bearish", support=src, tag=f"{m.key}_red")

    # ---- audit events
    def audit_events(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("audit_events", status="in_progress")
        ev = filings.auditor_and_restatement_events(rc.subs or {})
        idx = self.src_index()
        parts = []
        if ev["auditor_changes"]:
            items = "; ".join(self.c(f"8-K filed {e['filed']}", self.src_filing(e['accn'], '8-K', e['filed'], e['doc'], 'Item 4.01 change in certifying accountant')) for e in ev["auditor_changes"][:6])
            parts.append(f"**Auditor changes (8-K Item 4.01):** {len(ev['auditor_changes'])} filing(s) — {items}.")
            bb.add_claim(self.name, "audit_events", f"{len(ev['auditor_changes'])} auditor-change 8-K(s) on record", kind="bearish", support=[idx], tag="auditor_change")
        else:
            parts.append(f"**Auditor changes (8-K Item 4.01):** none in the EDGAR submissions index {self.c('(index)', idx)} (the index covers the most recent ~1,000 filings).")
        if ev["restatements"]:
            items = "; ".join(self.c(f"8-K filed {e['filed']}", self.src_filing(e['accn'], '8-K', e['filed'], e['doc'], 'Item 4.02 non-reliance on prior financials')) for e in ev["restatements"][:6])
            parts.append(f"**Restatements / non-reliance (8-K Item 4.02):** {len(ev['restatements'])} filing(s) — {items}.")
            bb.add_claim(self.name, "audit_events", f"{len(ev['restatements'])} non-reliance (restatement) 8-K(s) on record", kind="bearish", support=[idx], tag="restatement")
        else:
            parts.append(f"**Restatements / non-reliance (8-K Item 4.02):** none in the index {self.c('(index)', idx)}.")
        if ev["amended_reports"]:
            parts.append("**Amended periodic reports:** " + "; ".join(self.c(f"{a['form']} filed {a['filed']}", self.src_filing(a['accn'], a['form'], a['filed'], a['doc'])) for a in ev["amended_reports"]) + ".")
        # going concern
        gc = None
        if rc.tenk and rc.tenk_text.get(rc.tenk[0]["accn"]):
            gc = htmlitems.going_concern_mentions(rc.tenk_text[rc.tenk[0]["accn"]])
            k = rc.tenk[0]
            sid = self.src_filing(k["accn"], k["form"], k["filed"], k["doc"], "full-text going-concern search")
            if gc["substantial_doubt"]:
                parts.append(f"**Going concern:** the latest 10-K contains *substantial doubt* going-concern language {self.c('(source)', sid)}. Snippet: “{self.q(gc['snippets'][0][:300], sid)}…”")
                bb.add_claim(self.name, "audit_events", "Substantial-doubt going-concern language present in the latest 10-K", kind="bearish", support=[sid], tag="going_concern")
            elif gc["mentions"]:
                parts.append(f"**Going concern:** the phrase appears {gc['mentions']} time(s) in the latest 10-K {self.c('(source)', sid)} without substantial-doubt wording — usually boilerplate about the basis of presentation or an auditor's responsibilities. Snippet: “{self.q(gc['snippets'][0][:240], sid)}…”")
            else:
                parts.append(f"**Going concern:** no going-concern language in the latest 10-K text {self.c('(source)', sid)}.")
                bb.add_claim(self.name, "audit_events", "No going-concern language in the latest 10-K", support=[sid], tag="no_going_concern")
        else:
            self.gap("audit_events", "Going-concern search skipped", "10-K text unavailable")
        if rc.cfg["efts"]:
            res = rc.client.efts_search('"substantial doubt" "going concern"', forms=["10-K", "10-Q"], ciks=["%010d" % rc.cik],
                                        startdt=(rc.today.replace(year=rc.today.year - 3)).isoformat(), enddt=rc.today.isoformat())
            total = ((res or {}).get("hits", {}).get("total", {}) or {}).get("value")
            if total is not None:
                url = f"https://efts.sec.gov/LATEST/search-index?q=%22substantial%20doubt%22%20%22going%20concern%22&forms=10-K,10-Q&ciks={rc.cik:010d}"
                sid = self.src_efts(url, "EDGAR full-text search: “substantial doubt” “going concern”, this filer, 3 years")
                parts.append(f"EDGAR full-text search for “substantial doubt” + “going concern” across this filer's 10-K/10-Q documents in the last three years: {self.c(str(total) + ' hit(s)', sid)}.")
        completeness = 0.6 + (0.4 if gc is not None else 0.0)
        bb.update_section("audit_events", status="done", narrative="\n\n".join(parts), completeness=completeness, data={**ev, "going_concern": gc})
