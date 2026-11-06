"""Analyst: business overview, financial trends, valuation, peers."""
from __future__ import annotations

from datetime import date

from agentkit import tool

from .. import filings, financials, fmt, htmlitems, prices
from ..xbrl import build_table
from .base import DDAgent

SYSTEM = ("You are a sell-side style equity analyst writing a neutral business overview. Use ONLY facts in the provided excerpt. "
          "Do not state any numbers that are not in the excerpt. Write 2 short paragraphs of plain prose, no bullet points, no headings.")


class Analyst(DDAgent):
    name = "Analyst"
    role = "Business overview, 5-year financial trends, valuation snapshot, peers"
    color = "#4f9cf9"

    # ----------------------------------------------------------- tools
    @tool("Fetch EDGAR submissions index and XBRL companyfacts")
    def load_core_data(self) -> str:
        rc = self.rc
        rc.subs = rc.client.submissions(rc.cik)
        if not rc.subs:
            raise RuntimeError("EDGAR submissions index unavailable: " + (rc.client.last_errors[-1] if rc.client.last_errors else "unknown error"))
        rc.sic = str(rc.subs.get("sic") or "") or None
        rc.sic_desc = rc.subs.get("sicDescription") or ""
        rc.facts = rc.client.company_facts(rc.cik)
        if not rc.facts:
            self.say("XBRL companyfacts unavailable for this CIK — financial sections will be sparse", status="warn")
            rc.facts = {}
        rc.table = build_table(rc.facts, years=rc.cfg["years"])
        rc.tenk = filings.latest_filings(rc.subs, "10-K", 2) or filings.latest_filings(rc.subs, "20-F", 2)
        return f"{len(rc.table.years)} fiscal years, {len(rc.table.series)} series, {len(rc.table.gaps)} missing concepts; SIC {rc.sic} {rc.sic_desc}"

    @tool("Fetch 10-K primary documents and extract Items 1, 1A, 3, 7")
    def load_10k_items(self) -> str:
        rc = self.rc
        n = 2 if rc.cfg["prior_10k"] else 1
        got = []
        for k in rc.tenk[:n]:
            html = rc.client.filing_document(rc.cik, k["accn"], k["doc"])
            if not html:
                self.say(f"could not fetch 10-K {k['accn']} ({k['doc']})", status="warn")
                continue
            rc.tenk_html[k["accn"]] = html
            text = htmlitems.html_to_text(html)
            rc.tenk_text[k["accn"]] = text
            rc.tenk_items[k["accn"]] = {it: htmlitems.extract_item(text, it) for it in ("1", "1A", "3", "7")}
            got.append(k["accn"])
        return f"{len(got)} filing(s) parsed: " + ", ".join(f"{a} items " + "/".join(i for i, v in rc.tenk_items[a].items() if v) for a in got)

    @tool("Fetch 5-year price history (Yahoo Finance, optional)")
    def load_prices(self) -> str:
        self.rc.prices = prices.fetch_prices(self.rc.client, self.rc.ticker)
        p = self.rc.prices
        return f"last close {p['latest']} on {p['latest_date']}" if p.get("ok") else f"unavailable — {p.get('error')}"

    # ----------------------------------------------------------- sections
    def run(self) -> None:
        bb, rc = self.bb, self.rc
        self.step("load core data", self.load_core_data, summary=str, required=True)
        self.step("load 10-K items", self.load_10k_items, summary=str)
        self.step("load prices", self.load_prices, summary=str)
        self.step("business overview", self.business_overview)
        self.step("financial trends", self.financial_trends)
        self.step("valuation snapshot", self.valuation)
        if rc.cfg["peers"]:
            self.step("peer comparison", self.peers)
        else:
            bb.update_section("peers", status="done", narrative="_Peer comparison is skipped at **quick** depth. Re-run at standard or deep depth._",
                              completeness=0.0, data={"skipped": True})

    # ---- business
    def business_overview(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("business", status="in_progress")
        k = rc.tenk[0] if rc.tenk else None
        items = rc.tenk_items.get(k["accn"], {}) if k else {}
        item1 = items.get("1")
        parts: list[str] = []
        completeness = 0.0
        data: dict = {"sic": rc.sic, "sic_desc": rc.sic_desc, "category": (rc.subs or {}).get("category"),
                      "state": (rc.subs or {}).get("stateOfIncorporation"), "fye": (rc.subs or {}).get("fiscalYearEnd"),
                      "exchanges": (rc.subs or {}).get("exchanges")}
        idx = self.src_index()
        parts.append(f"**{rc.name}** ({rc.ticker}, CIK {rc.cik}) is classified under SIC {rc.sic or 'n/a'} — "
                     f"{rc.sic_desc or 'no description'} {self.c('per EDGAR', idx)}. Filer category: {data['category'] or 'n/a'}; "
                     f"state of incorporation: {data['state'] or 'n/a'}; fiscal year end: {data['fye'] or 'n/a'}.")
        if item1 and item1.text:
            sid = self.src_filing(k["accn"], k["form"], k["filed"], k["doc"], "Item 1 Business")
            excerpt = htmlitems.split_sentences(item1.text)[:6]
            fallback = " ".join(excerpt)
            fallback = f"From Item 1 of the latest 10-K {self.c('(source)', sid)}: {self.q(fallback, sid)}"
            prompt = f"Company: {rc.name}. Excerpt from Item 1 (Business) of the latest 10-K:\n\n{item1.text[:6000]}"
            text, mode = self.write("business overview", SYSTEM, prompt, fallback=fallback)
            if mode != "offline":
                text = text + f"\n\n_Narrative drafted by {mode} from Item 1 {self.c('(source)', sid)}; numbers in LLM prose are not individually footnoted — see the data tables below for sourced figures._"
            parts.append(text)
            data["item1_chars"] = len(item1.text)
            completeness += 0.5
        else:
            self.gap("business", "Item 1 (Business) text not extracted", "10-K document unavailable or non-standard layout")
        # segments
        segs = []
        if k and rc.tenk_html.get(k["accn"]):
            segs = htmlitems.segment_revenue_tables(rc.tenk_html[k["accn"]])
        if segs:
            sid = self.src_filing(k["accn"], k["form"], k["filed"], k["doc"], "segment / category revenue table")
            for s in segs:
                lines = [f"| {r['label']} | {self.c(fmt.pct(r['value'] / s['total']), sid)} |" for r in s["rows"] if s["total"]]
                parts.append("**Revenue mix (best-effort from 10-K table; most recent fiscal year column; shares of the table's total)**\n\n"
                             "| Line | Share of total |\n|---|---|\n" + "\n".join(lines))
            data["segments"] = segs
            completeness += 0.3
            top = max(segs[0]["rows"], key=lambda r: r["value"])
            share = top["value"] / segs[0]["total"] if segs[0]["total"] else None
            if share and share > 0.4:
                bb.add_claim(self.name, "business", f"Revenue is concentrated: {top['label']} is {fmt.pct(share)} of the total in the first segment table", kind="bearish", support=[sid], tag="concentration")
            else:
                bb.add_claim(self.name, "business", "Revenue mix is reasonably diversified across reported lines", kind="bullish", support=[sid], tag="diversified")
        else:
            self.gap("business", "Segment / product revenue table not found", "companyfacts has no dimensional data and no table matched in the 10-K")
            parts.append("_Segment revenue: not available — the XBRL companyfacts API carries no dimensional (segment) data and no segment table could be parsed from the 10-K._")
        if rc.tenk:
            completeness += 0.2
        bb.update_section("business", status="done", narrative="\n\n".join(parts), data=data, completeness=min(1.0, completeness))

    # ---- financials
    def financial_trends(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("financials", status="in_progress")
        t = rc.table
        if not t or not t.years:
            self.gap("financials", "No annual XBRL data", "companyfacts empty or no 10-K/20-F facts")
            bb.update_section("financials", status="done", narrative="_No annual XBRL financial data available for this filer._", completeness=0.0)
            return
        rows = financials.trend_table(t)
        summ = financials.summarise_trends(rows)
        cur = t.currency
        years = t.years
        hdr = "| Metric | " + " | ".join(f"FY{y}" for y in years) + " |\n|---|" + "---|" * len(years)

        def line(label, name, formatter=None):
            return f"| {label} | " + " | ".join(self.cf(name, y, formatter) for y in years) + " |"

        def dline(label, key, formatter, inputs_names, anchor):
            cells = []
            for r in rows:
                v = r[key]
                if v is None:
                    cells.append("n/a")
                else:
                    cells.append(self.derived(formatter(v), f"{label} FY{r['year']}", self.fact_sources(inputs_names, r["year"]), anchor))
            return f"| {label} | " + " | ".join(cells) + " |"

        table_md = "\n".join([
            hdr,
            line("Revenue", "revenue"), line("Gross profit", "gross_profit"), line("Operating income", "operating_income"),
            line("Net income", "net_income"), line("Cash from operations", "cfo"), line("Capital expenditure", "capex"),
            dline("Free cash flow", "fcf", lambda v: fmt.money(v, cur), ["cfo", "capex"], "fcf"),
            dline("Gross margin", "gross_margin", fmt.pct, ["gross_profit", "revenue"], "margins"),
            dline("Operating margin", "operating_margin", fmt.pct, ["operating_income", "revenue"], "margins"),
            dline("Net margin", "net_margin", fmt.pct, ["net_income", "revenue"], "margins"),
            dline("ROIC (approx.)", "roic", fmt.pct, ["operating_income", "income_tax", "pretax_income", "equity", "ltd", "std", "cash", "short_investments"], "roic"),
            dline("Net debt (cash negative)", "net_debt", lambda v: fmt.money(v, cur), ["ltd", "std", "cash", "short_investments"], "net-debt"),
            line("Diluted shares (wtd)", "shares_diluted", fmt.count), line("Diluted EPS", "eps_diluted", lambda v: fmt.num(v, 2)),
            line("Stock-based comp", "sbc"), line("Buybacks", "buybacks"), line("Dividends paid", "dividends"),
        ])
        parts = [f"Fiscal years {years[0]}–{years[-1]}; currency {cur}; values as tagged in XBRL (latest filed value wins when restated). "
                 f"Click any figure to open the filing it came from.", table_md]
        # narrative
        n = summ.get("years", 0)
        last = rows[-1]
        first = rows[0]
        bits = []
        if summ.get("revenue_cagr") is not None:
            sid = self.src_derived(f"Revenue CAGR FY{first['year']}–FY{last['year']}", self.fact_sources(["revenue"], first["year"]) + self.fact_sources(["revenue"], last["year"]), "cagr")
            bits.append(f"Revenue compounded at {self.c(fmt.pct(summ['revenue_cagr']), sid)} a year over {n} years, from {self.cf('revenue', first['year'])} to {self.cf('revenue', last['year'])}.")
            if summ["revenue_cagr"] > 0.05:
                bb.add_claim(self.name, "financials", f"Revenue growth is healthy ({fmt.pct(summ['revenue_cagr'])} CAGR over {n} years)", support=[sid], tag="revenue_growth")
            elif summ["revenue_cagr"] < 0:
                bb.add_claim(self.name, "financials", f"Revenue is shrinking ({fmt.pct(summ['revenue_cagr'])} CAGR)", kind="bearish", support=[sid], tag="revenue_decline")
        if summ.get("revenue_yoy") is not None:
            sid = self.src_derived(f"Revenue YoY FY{last['year']}", self.fact_sources(["revenue"], last["year"]) + self.fact_sources(["revenue"], rows[-2]["year"]), "cagr")
            bits.append(f"Latest year revenue change: {self.c(fmt.pct(summ['revenue_yoy'], signed=True), sid)}.")
        if last["operating_margin"] is not None and first["operating_margin"] is not None:
            sid = self.src_derived("Operating margin change", self.fact_sources(["operating_income", "revenue"], first["year"]) + self.fact_sources(["operating_income", "revenue"], last["year"]), "margins")
            chg = summ["operating_margin_change"]
            om_now = self.derived(fmt.pct(last["operating_margin"]), "Operating margin FY%d" % last["year"], self.fact_sources(["operating_income", "revenue"], last["year"]), "margins")
            bits.append(f"Operating margin moved {self.c(fmt.pct(chg, signed=True), sid)} (percentage points) over the period to {om_now}.")
            if chg > 0.01:
                bb.add_claim(self.name, "financials", f"Operating margin expanded by {fmt.pct(chg, signed=True)} points over {n} years", support=[sid], tag="margin_expansion")
            elif chg < -0.01:
                bb.add_claim(self.name, "financials", f"Operating margin contracted by {fmt.pct(chg, signed=True)} points", kind="bearish", support=[sid], tag="margin_contraction")
        if last["fcf"] is not None:
            sid = self.src_derived(f"FCF FY{last['year']}", self.fact_sources(["cfo", "capex"], last["year"]), "fcf")
            bits.append(f"Free cash flow in FY{last['year']} was {self.c(fmt.money(last['fcf'], cur), sid)}" + (f" ({self.derived(fmt.pct(last['fcf_margin']), 'FCF margin', self.fact_sources(['cfo', 'capex', 'revenue'], last['year']), 'fcf')} of revenue)." if last["fcf_margin"] is not None else "."))
            if last["fcf"] > 0 and last["revenue"] and last["fcf_margin"] and last["fcf_margin"] > 0.10:
                bb.add_claim(self.name, "financials", f"Strong free-cash-flow generation ({fmt.pct(last['fcf_margin'])} FCF margin)", support=[sid], tag="fcf_strong")
        if summ.get("loss_years"):
            bits.append(f"Net losses were reported in: {', '.join(f'FY{y}' for y in summ['loss_years'])}.")
            bb.add_claim(self.name, "financials", f"Loss-making in {len(summ['loss_years'])} of {len(rows)} years", kind="bearish", support=self.fact_sources(["net_income"], summ["loss_years"][-1]), tag="losses")
        if summ.get("negative_fcf_years"):
            bits.append(f"Free cash flow was negative in: {', '.join(f'FY{y}' for y in summ['negative_fcf_years'])}.")
        if last["net_debt"] is not None:
            sid = self.src_derived(f"Net debt FY{last['year']}", self.fact_sources(["ltd", "std", "cash", "short_investments"], last["year"]), "net-debt")
            if last["net_debt"] < 0:
                bits.append(f"The balance sheet carries net cash of {self.c(fmt.money(-last['net_debt'], cur), sid)} (cash + short-term investments exceed reported debt).")
                bb.add_claim(self.name, "financials", "Net cash balance sheet", support=[sid], tag="net_cash")
            else:
                bits.append(f"Net debt stands at {self.c(fmt.money(last['net_debt'], cur), sid)}.")
        if last["roic"] is not None:
            sid = self.src_derived(f"ROIC FY{last['year']}", self.fact_sources(["operating_income", "equity"], last["year"]), "roic")
            bits.append(f"Approximate ROIC (NOPAT / (equity + debt − cash)) is {self.c(fmt.pct(last['roic']), sid)}; treat as indicative — buybacks that shrink book equity inflate it.")
            if last["roic"] > 0.15:
                bb.add_claim(self.name, "financials", f"High return on invested capital ({fmt.pct(last['roic'])})", support=[sid], tag="high_roic")
        parts.append(" ".join(bits))
        restated = [(n_, y) for n_, s in t.series.items() for y, f in s.items() if f.restated and y in years]
        if restated:
            parts.append(f"**Restatements noticed:** {len(restated)} figure(s) in this table were reported with a different value in an earlier filing; the latest-filed value is shown and the earlier value is listed in the source note. Examples: " +
                         ", ".join(f"{t.series[n_][y].concept} FY{y}" for n_, y in restated[:4]) + ".")
        if t.gaps:
            parts.append("**Concepts not reported by this filer:** " + ", ".join(sorted(t.gaps)) + ". Rows that depend on them show n/a.")
        for g in t.gaps:
            if g in ("inventory", "receivables", "sbc", "da", "capex"):
                self.gap("financials", f"{g} not reported", "no matching us-gaap concept in companyfacts (may be a service/financial company or custom tag)")
        parts.append("**XBRL concepts used:** " + "; ".join(f"{k}: {', '.join(v)}" for k, v in t.concepts_used.items() if k in ("revenue", "cogs", "net_income", "cfo", "capex", "ltd", "std", "cash", "receivables", "inventory", "shares_diluted")))
        core = ["revenue", "net_income", "cfo", "total_assets", "equity", "operating_income", "capex", "shares_diluted"]
        completeness = sum(1 for n_ in core if t.has(n_)) / len(core) * min(1.0, len(years) / max(1, rc.cfg["years"]))
        bb.update_section("financials", status="done", narrative="\n\n".join(parts), completeness=completeness,
                          data={"rows": rows, "summary": summ, "currency": cur, "years": years, "restated": restated, "gaps": t.gaps, "concepts_used": t.concepts_used})

    # ---- valuation
    def valuation(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("valuation", status="in_progress")
        t = rc.table
        if not t or not t.years:
            bb.update_section("valuation", status="done", narrative="_No financial data — valuation not computed._", completeness=0.0)
            return
        p = rc.prices
        price = p.get("latest") if p.get("ok") else None
        so = t.shares_outstanding
        v = financials.valuation_snapshot(t, price, so.value if so else None)
        y = v["year"]
        msrc = self.src_market()
        parts = []
        inputs = []
        if so:
            inputs.append(self.src_fact(so))
        if msrc:
            inputs.append(msrc)
        if price is not None and msrc:
            parts.append(f"Last close {self.c(fmt.num(price, 2), msrc)} ({p.get('currency') or 'USD'}) on {p.get('latest_date')} (Yahoo Finance, unofficial). "
                         f"Shares outstanding {self.c(fmt.count(so.value), self.src_fact(so)) if so else 'n/a'} → market cap {self.derived(fmt.money(v['market_cap'], rc.currency), 'Market cap', inputs, 'valuation')}.")
            rows = []
            if v["pe"] is not None:
                rows.append(("P/E (trailing FY)", fmt.ratio(v["pe"], 1), ["net_income"]))
            if v["ps"] is not None:
                rows.append(("P/S (trailing FY)", fmt.ratio(v["ps"], 2), ["revenue"]))
            if v["ev_ebitda"] is not None:
                rows.append(("EV / EBITDA", fmt.ratio(v["ev_ebitda"], 1), ["operating_income", "da", "ltd", "std", "cash", "short_investments"]))
            elif v["ev_ebit"] is not None:
                rows.append(("EV / EBIT", fmt.ratio(v["ev_ebit"], 1), ["operating_income", "ltd", "std", "cash", "short_investments"]))
            if v["fcf_yield"] is not None:
                rows.append(("FCF yield", fmt.pct(v["fcf_yield"]), ["cfo", "capex"]))
            if v["enterprise_value"] is not None:
                rows.append(("Enterprise value", fmt.money(v["enterprise_value"], rc.currency), ["ltd", "std", "cash", "short_investments"]))
            md = "| Multiple | Value |\n|---|---|\n" + "\n".join(f"| {lbl} | {self.derived(val, lbl, inputs + self.fact_sources(names, y), 'valuation')} |" for lbl, val, names in rows)
            parts.append(md)
            if v["pe"] is not None and v["pe"] > 30:
                bb.add_claim(self.name, "valuation", f"Valuation is rich: trailing P/E {fmt.ratio(v['pe'], 1)}", kind="bearish", support=inputs, tag="rich_valuation")
            elif v["pe"] is not None and v["pe"] < 15:
                bb.add_claim(self.name, "valuation", f"Shares look inexpensive on trailing P/E {fmt.ratio(v['pe'], 1)}", support=inputs, tag="cheap_valuation")
            if p.get("return_1y") is not None:
                parts.append(f"Price performance: {self.c(fmt.pct(p['return_1y'], signed=True), msrc)} over one year" +
                             (f", {self.c(fmt.pct(p['return_5y'], signed=True), msrc)} over five years" if p.get("return_5y") is not None else "") +
                             f"; 52-week range {self.c(fmt.num(p['low_52w'], 2), msrc)}–{self.c(fmt.num(p['high_52w'], 2), msrc)}; maximum 5-year drawdown {self.c(fmt.pct(p['max_drawdown_5y']), msrc)}.")
            completeness = 1.0 if v["pe"] is not None or v["ps"] is not None else 0.6
        else:
            parts.append(f"**Market data unavailable** — {p.get('error') or 'price feed not attempted'}. Market multiples (P/E, EV/EBITDA, P/S) require a price and are omitted rather than estimated.")
            self.gap("valuation", "No market price", p.get("error") or "price feed unavailable")
            completeness = 0.2
        if v["ebitda"] is not None:
            parts.append(f"Trailing EBITDA (operating income + D&A) {self.derived(fmt.money(v['ebitda'], rc.currency), 'EBITDA', self.fact_sources(['operating_income', 'da'], y), 'valuation')}; "
                         f"net debt {self.derived(fmt.money(v['net_debt'], rc.currency), 'Net debt', self.fact_sources(['ltd', 'std', 'cash', 'short_investments'], y), 'net-debt') if v['net_debt'] is not None else 'n/a'}.")
        for note in v["notes"]:
            parts.append(f"_Note: {note}_")
        bb.update_section("valuation", status="done", narrative="\n\n".join(parts), completeness=completeness,
                          data={**{k: val for k, val in v.items() if k != 'notes'}, "notes": v["notes"], "price_points": p.get("points", []) if p.get("ok") else []})

    # ---- peers
    def peers(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("peers", status="in_progress")
        if not rc.sic:
            bb.update_section("peers", status="done", narrative="_No SIC code on file — peer set not built._", completeness=0.0)
            return
        plist = filings.peer_ciks_by_sic(rc.client, rc.sic, rc.cik, today=rc.today)
        cy = rc.today.year - 1
        tbl = filings.peer_table(rc.client, plist, cy)
        if not tbl and plist:
            cy -= 1
            tbl = filings.peer_table(rc.client, plist, cy)
        if not tbl:
            self.gap("peers", "No peers with XBRL frame data", f"EFTS returned {len(plist)} same-SIC filers but frames CY{cy} had none of them")
            bb.update_section("peers", status="done", narrative=f"_No peer data: EFTS found {len(plist)} filers with SIC {rc.sic}, none present in XBRL frames for CY{cy}._", completeness=0.1)
            return
        efts = self.src_efts(f"https://efts.sec.gov/LATEST/search-index?q=%22annual%20report%22&forms=10-K&sics={rc.sic}", f"EDGAR full-text search, 10-K filers with SIC {rc.sic}")
        lines = []
        own_rev = rc.table.value("revenue", rc.table.years[-1]) if rc.table and rc.table.years else None
        for p_ in tbl[:12]:
            cells = [f"{p_['name']}" + (f" ({p_['ticker']})" if p_.get("ticker") else "")]
            if p_["revenue"]:
                sid = bb.add_source("xbrl", f"https://www.sec.gov/Archives/edgar/data/{p_['cik']}/{p_['revenue']['accn'].replace('-', '')}/{p_['revenue']['accn']}-index.htm",
                                    f"{p_['revenue']['concept']} CY{cy} frame — {p_['name']} {p_['revenue']['accn']}", concept=p_["revenue"]["concept"], accn=p_["revenue"]["accn"], period=f"CY{cy}")
                cells.append(self.c(fmt.money(p_["revenue"]["val"]), sid))
            else:
                cells.append("n/a")
            if p_["net_income"]:
                sid = bb.add_source("xbrl", f"https://www.sec.gov/Archives/edgar/data/{p_['cik']}/{p_['net_income']['accn'].replace('-', '')}/{p_['net_income']['accn']}-index.htm",
                                    f"us-gaap:NetIncomeLoss CY{cy} frame — {p_['name']} {p_['net_income']['accn']}", concept="us-gaap:NetIncomeLoss", accn=p_["net_income"]["accn"], period=f"CY{cy}")
                cells.append(self.c(fmt.money(p_["net_income"]["val"]), sid))
                cells.append(self.c(fmt.pct(p_["net_margin"]), sid) if p_["net_margin"] is not None else "n/a")
            else:
                cells += ["n/a", "n/a"]
            lines.append("| " + " | ".join(cells) + " |")
        md = (f"Peers = other 10-K filers with the same SIC code ({rc.sic} {rc.sic_desc}) found via {self.c('EDGAR full-text search', efts)}, "
              f"with calendar-{cy} revenue and net income from the XBRL *frames* API. Frames align to calendar years, so fiscal-year companies are approximated. "
              f"SIC peers are a crude set — expect some non-comparables.\n\n| Company | Revenue CY{cy} | Net income CY{cy} | Net margin |\n|---|---|---|---|\n" + "\n".join(lines))
        if own_rev is not None:
            bigger = sum(1 for p_ in tbl if p_["revenue"] and p_["revenue"]["val"] > own_rev)
            md += f"\n\n{rc.name} is larger by revenue than {len(tbl) - bigger} of the {len(tbl)} peers with data."
        bb.update_section("peers", status="done", narrative=md, completeness=min(1.0, len(tbl) / 5), data={"peers": tbl, "cy": cy, "sic": rc.sic})
