"""Legal: risk factors + YoY diff, legal proceedings, 8-K timeline, insider Form 4, 13F holders, exec comp."""
from __future__ import annotations

from .. import filings, fmt, htmlitems
from .base import DDAgent

SYSTEM_RISK = ("You are a securities lawyer summarising risk-factor disclosures for an investment committee. Use ONLY the provided sentences. "
               "Write one paragraph (max 120 words) on what is new or emphasised this year. No numbers unless they appear verbatim in the input.")


class Legal(DDAgent):
    name = "Legal"
    role = "Risk factors & YoY diff, legal proceedings, 8-K events, insider activity, executive pay"
    color = "#a78bfa"

    def run(self) -> None:
        self.step("risk factors & diff", self.risk_factors)
        self.step("legal proceedings", self.legal_proceedings)
        self.step("8-K material events", self.material_events)
        self.step("insider Form 4 & holders", self.insiders)
        self.step("executive compensation (DEF 14A)", self.compensation)

    # ---- risk factors
    def risk_factors(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("risk_factors", status="in_progress")
        if not rc.tenk:
            self.gap("risk_factors", "No 10-K on file")
            bb.update_section("risk_factors", status="done", narrative="_No 10-K found in the submissions index._", completeness=0.0)
            return
        k = rc.tenk[0]
        cur = rc.tenk_items.get(k["accn"], {}).get("1A")
        if not cur or not cur.text:
            self.gap("risk_factors", "Item 1A not extracted", "document unavailable or non-standard headings")
            bb.update_section("risk_factors", status="done", narrative="_Item 1A could not be extracted from the latest 10-K._", completeness=0.0)
            return
        sid = self.src_filing(k["accn"], k["form"], k["filed"], k["doc"], "Item 1A Risk Factors")
        clusters = htmlitems.cluster_risk_factors(cur.text)
        total_sents = len(htmlitems.split_sentences(cur.text))
        parts = [f"Item 1A of the {k['form']} filed {k['filed']} {self.c('(source)', sid)} runs to about {len(cur.text):,} characters / {total_sents} sentences. "
                 "Sentences were bucketed by keyword into themes (a sentence can hit several themes):"]
        rows = [f"| {c['cluster']} | {c['sentences']} | {self.c(fmt.pct(c['share']), sid)} | {self.q(c['examples'][0][:220].replace('|', '/'), sid)}{'…' if len(c['examples'][0]) > 220 else ''} |" for c in clusters]
        parts.append("| Theme | Sentences | Share | Example sentence |\n|---|---|---|---|\n" + "\n".join(rows))
        diff = None
        prior = rc.tenk[1] if len(rc.tenk) > 1 else None
        pcur = rc.tenk_items.get(prior["accn"], {}).get("1A") if prior else None
        if pcur and pcur.text:
            psid = self.src_filing(prior["accn"], prior["form"], prior["filed"], prior["doc"], "Item 1A Risk Factors (prior year)")
            diff = htmlitems.diff_risk_factors(cur.text, pcur.text)
            parts.append(f"**What changed vs the prior 10-K** ({self.c(prior['filed'], psid)}): {diff['added_count']} sentence(s) are new, {diff['removed_count']} were dropped, "
                         f"{diff['unchanged_count']} unchanged (after normalising numbers/punctuation); section length changed {self.c(fmt.pct(diff['length_change_pct'], signed=True), sid)}.")
            if diff["added_examples"]:
                parts.append("New or substantially rewritten sentences (ranked by novelty of vocabulary):\n\n" + "\n".join(f"- {self.q(s[:400].replace(chr(10), ' '), sid)}{'…' if len(s) > 400 else ''}" for s in diff["added_examples"][:6]))
            if diff["removed_examples"]:
                parts.append("Dropped from last year:\n\n" + "\n".join(f"- {self.q(s[:300], psid)}{'…' if len(s) > 300 else ''}" for s in diff["removed_examples"][:4]))
            if diff["added_count"] > 0.25 * max(1, diff["current_sentences"]):
                bb.add_claim(self.name, "risk_factors", f"Risk-factor section was substantially rewritten ({diff['added_count']} new sentences)", kind="bearish", support=[sid, psid], tag="risk_rewrite")
            prompt = "New sentences this year:\n" + "\n".join(diff["added_examples"][:10])
            text, mode = self.write("risk-factor change summary", SYSTEM_RISK, prompt, fallback="")
            if text and mode != "offline":
                parts.append(f"_Summary drafted by {mode} from the new sentences above:_ {text}")
        elif rc.cfg["prior_10k"]:
            self.gap("risk_factors", "Prior-year Item 1A unavailable — no diff", "only one 10-K parsed")
            parts.append("_Prior-year comparison unavailable (prior 10-K not parsed)._")
        else:
            parts.append("_Prior-year diff skipped at quick depth._")
        if clusters:
            top = clusters[0]
            bb.add_claim(self.name, "risk_factors", f"Largest risk theme is “{top['cluster']}” ({top['sentences']} sentences)", kind="neutral", support=[sid], tag="risk_theme")
        bb.update_section("risk_factors", status="done", narrative="\n\n".join(parts), completeness=1.0 if diff else 0.6,
                          data={"clusters": clusters, "diff": {k_: v for k_, v in (diff or {}).items()}, "chars": len(cur.text)})

    # ---- legal proceedings
    def legal_proceedings(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("legal_proceedings", status="in_progress")
        k = rc.tenk[0] if rc.tenk else None
        it = rc.tenk_items.get(k["accn"], {}).get("3") if k else None
        if not it:
            self.gap("legal_proceedings", "Item 3 not extracted")
            bb.update_section("legal_proceedings", status="done", narrative="_Item 3 (Legal Proceedings) could not be extracted._", completeness=0.0)
            return
        sid = self.src_filing(k["accn"], k["form"], k["filed"], k["doc"], "Item 3 Legal Proceedings")
        sents = htmlitems.split_sentences(it.text)
        text = it.text.strip()
        parts = [f"Item 3 of the latest 10-K {self.c('(source)', sid)} ({len(text):,} characters)."]
        if len(text) < 400:
            parts.append(f"> {self.q(text[:400], sid)}")
            parts.append("The section is brief — either no material proceedings or the discussion is incorporated by reference to the financial-statement notes (commitments & contingencies).")
            bb.add_claim(self.name, "legal_proceedings", "No material legal proceedings disclosed in Item 3", support=[sid], tag="no_litigation")
        else:
            keywords = ["antitrust", "class action", "securities", "patent", "department of justice", "european commission", "ftc", "sec ", "investigation", "settlement", "fine", "damages", "injunction"]
            flagged = [s for s in sents if any(kw in s.lower() for kw in keywords)]
            parts.append("Opening of the section:\n\n> " + self.q(" ".join(sents[:4])[:900], sid))
            if flagged:
                parts.append(f"Sentences mentioning regulators, class actions, patents or settlements ({len(flagged)} of {len(sents)}):\n\n" + "\n".join(f"- {self.q(s[:350], sid)}{'…' if len(s) > 350 else ''}" for s in flagged[:6]))
                bb.add_claim(self.name, "legal_proceedings", f"Active legal/regulatory matters disclosed ({len(flagged)} sentences reference regulators, class actions or settlements)", kind="bearish", support=[sid], tag="litigation")
        if rc.cfg["efts"]:
            url = f"https://efts.sec.gov/LATEST/search-index?q=%22class%20action%22&forms=10-K,10-Q,8-K&ciks={rc.cik:010d}"
            res = rc.client.efts_search('"class action"', forms=["10-K", "10-Q", "8-K"], ciks=["%010d" % rc.cik],
                                        startdt=rc.today.replace(year=rc.today.year - 2).isoformat(), enddt=rc.today.isoformat())
            total = ((res or {}).get("hits", {}).get("total", {}) or {}).get("value")
            if total is not None:
                esid = self.src_efts(url, "EDGAR full-text search: “class action” in this filer's 10-K/10-Q/8-K, 2 years")
                parts.append(f"EDGAR full-text search for “class action” in this filer's periodic and current reports over two years: {self.c(str(total) + ' document hit(s)', esid)} (counts documents/exhibits, not cases).")
        bb.update_section("legal_proceedings", status="done", narrative="\n\n".join(parts), completeness=1.0, data={"chars": len(text), "note": it.note})

    # ---- 8-K
    def material_events(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("material_events", status="in_progress")
        tl = filings.eight_k_timeline(rc.subs or {}, months=24, today=rc.today)
        idx = self.src_index()
        if not tl:
            bb.update_section("material_events", status="done", narrative=f"_No 8-K filings in the last 24 months per the submissions index {self.c('(index)', idx)}._", completeness=0.5, data={"timeline": []})
            return
        material = [e for e in tl if e["material"]]
        parts = [f"{len(tl)} Form 8-K filings in the last 24 months {self.c('(index)', idx)}, of which {len(material)} carry items this tool treats as material "
                 "(agreements, M&A, debt, impairments, delisting notices, auditor changes, restatements, control changes, officer departures). Routine earnings releases (Item 2.02) and Reg FD (7.01) are listed but not flagged."]
        rows = []
        for e in tl[:40]:
            sid = self.src_filing(e["accn"], "8-K", e["filed"], e["doc"], "Items " + ", ".join(e["items"]))
            rows.append(f"| {self.c(e['filed'], sid)} | {', '.join('`' + i + '`' for i in e['items']) or '—'} | {'; '.join(e['labels'])[:140] or 'no items listed'} | {'⚠' if e['material'] else ''} |")
        parts.append("| Filed | Items | Description | Flag |\n|---|---|---|---|\n" + "\n".join(rows))
        from collections import Counter
        cnt = Counter(c for e in tl for c in e["items"])
        if cnt:
            parts.append("Item frequency: " + ", ".join(f"`{c}` ×{n}" for c, n in cnt.most_common(8)) + ".")
        off = cnt.get("5.02", 0)
        if off >= 4:
            bb.add_claim(self.name, "material_events", f"Elevated officer/director turnover: {off} Item 5.02 filings in 24 months", kind="bearish", support=[idx], tag="turnover")
        if cnt.get("2.06") or cnt.get("2.04") or cnt.get("3.01") or cnt.get("1.03"):
            bb.add_claim(self.name, "material_events", "Distress-type 8-K items present (impairment, acceleration, delisting or bankruptcy)", kind="bearish", support=[idx], tag="distress_8k")
        bb.update_section("material_events", status="done", narrative="\n\n".join(parts), completeness=1.0, data={"timeline": tl, "counts": dict(cnt)})

    # ---- insiders
    def insiders(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("insiders", status="in_progress")
        idx = self.src_index()
        f4 = filings.form4_filings(rc.subs or {}, months=12, today=rc.today)
        parts = [f"{len(f4)} Form 4 filings (insider transactions) in the last 12 months are listed in the submissions index {self.c('(index)', idx)}."]
        n = rc.cfg["form4"]
        parsed = []
        if n and f4:
            for f in f4[:n]:
                xml = rc.client.filing_document(rc.cik, f["accn"], filings.form4_raw_doc(f["doc"]))
                p = filings.parse_form4(xml or "")
                if p:
                    p["accn"] = f["accn"]; p["filed"] = f["filed"]; p["doc"] = filings.form4_raw_doc(f["doc"])
                    parsed.append(p)
            s = filings.summarise_form4(parsed)
            sids = [self.src_filing(p["accn"], "4", p["filed"], p["doc"], f"Form 4 — {p['owner']}") for p in parsed[:n]]
            agg = self.src_derived(f"Form 4 aggregate over {len(parsed)} most recent filings", sids, "insiders")
            parts.append(f"Parsed the {len(parsed)} most recent Form 4s. Open-market **purchases (code P): {self.c(fmt.count(s['buy_shares']) + ' shares', agg)}** "
                         f"(≈ {self.c(fmt.money(s['buy_usd']), agg)} where a price was reported) by {len(s['buyers'])} insider(s); "
                         f"**sales (code S): {self.c(fmt.count(s['sell_shares']) + ' shares', agg)}** (≈ {self.c(fmt.money(s['sell_usd']), agg)}) by {len(s['sellers'])} insider(s). "
                         f"Other transaction codes seen: {', '.join(f'{k} ×{v}' for k, v in s['other_codes'].items()) or 'none'} (A = grant/award, M = option exercise, F = tax withholding, G = gift).")
            if parsed:
                rows = []
                for p in parsed[:12]:
                    sid = self.src_filing(p["accn"], "4", p["filed"], p["doc"], f"Form 4 — {p['owner']}")
                    tx = p["transactions"][:3]
                    desc = "; ".join(f"{t['code']} {fmt.count(t['shares']) if t['shares'] is not None else ''}{' @ ' + fmt.num(t['price'], 2) if t['price'] else ''}" for t in tx) or "no non-derivative transactions"
                    rows.append(f"| {self.c(p['filed'], sid)} | {p['owner']} | {p['relationship'] or '—'} | {self.q(desc, sid)} |")
                parts.append("| Filed | Insider | Role | Transactions (code, shares @ price) |\n|---|---|---|---|\n" + "\n".join(rows))
            if s["sell_usd"] > 0 and s["buy_usd"] == 0:
                bb.add_claim(self.name, "insiders", f"Insiders were net sellers in the sampled Form 4s ({fmt.money(s['sell_usd'])} sold, no open-market buys)", kind="bearish", support=[agg], tag="insider_selling")
            elif s["buy_usd"] > s["sell_usd"]:
                bb.add_claim(self.name, "insiders", f"Insiders were net open-market buyers ({fmt.money(s['buy_usd'])} bought)", support=[agg], tag="insider_buying")
            completeness = 1.0
        elif not f4:
            parts.append("No Form 4 filings in the index window — common for foreign private issuers or very small filers.")
            completeness = 0.5
        else:
            parts.append("_Form 4 parsing skipped at quick depth._")
            completeness = 0.3
        # holders
        if rc.cfg["holders"]:
            h = filings.notable_holders(rc.client, rc.name, today=rc.today)
            if h["holders"]:
                sid = self.src_efts(f"https://efts.sec.gov/LATEST/search-index?q={h['query']}&forms=13F-HR", f"EDGAR full-text search {h['query']} in 13F-HR")
                parts.append(f"**Notable 13F holders (best-effort):** full-text search for the company name inside recent 13F-HR information tables returned {self.c(str(h['total_hits']) + ' filings', sid)}. "
                             "Sample filers: " + ", ".join(x["name"] for x in h["holders"]) + f". _{h['note']}_")
            else:
                parts.append("**Notable 13F holders:** no full-text hits for the company name in recent 13F-HR filings (search is best-effort and name-based).")
        else:
            parts.append("_13F holder search skipped at quick depth._")
        bb.update_section("insiders", status="done", narrative="\n\n".join(parts), completeness=completeness, data={"form4_count": len(f4), "parsed": parsed[:40]})

    # ---- compensation
    def compensation(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("compensation", status="in_progress")
        d14 = filings.latest_filings(rc.subs or {}, "DEF 14A", 1)
        if not d14:
            self.gap("compensation", "No DEF 14A proxy on file", "foreign private issuers and some small filers do not file DEF 14A")
            bb.update_section("compensation", status="done", narrative="_No DEF 14A proxy statement in the submissions index._", completeness=0.0)
            return
        k = d14[0]
        html = rc.client.filing_document(rc.cik, k["accn"], k["doc"])
        sid = self.src_filing(k["accn"], "DEF 14A", k["filed"], k["doc"], "Summary Compensation Table")
        if not html:
            self.gap("compensation", "DEF 14A document fetch failed")
            bb.update_section("compensation", status="done", narrative=f"_DEF 14A {self.c(k['accn'], sid)} could not be fetched._", completeness=0.2)
            return
        sct = htmlitems.summary_compensation_table(html)
        if not sct["rows"]:
            self.gap("compensation", "Summary Compensation Table not parseable", sct["note"])
            bb.update_section("compensation", status="done", narrative=f"_Summary Compensation Table could not be parsed from the DEF 14A filed {self.c(k['filed'], sid)}; open the filing to read it._", completeness=0.3, data={"rows": []})
            return
        rows = "\n".join(f"| {r['name'][:60]} | {r['year']} | {self.c(fmt.money(r['total']), sid)} |" for r in sct["rows"])
        parts = [f"From the Summary Compensation Table in the DEF 14A filed {self.c(k['filed'], sid)} (best-effort parse: name, most recent year, *Total* column):",
                 "| Named executive | Year | Total compensation |\n|---|---|---|\n" + rows]
        top = sct["rows"][0]
        t = rc.table
        if t and t.years:
            ni = t.value("net_income", t.years[-1])
            rev = t.value("revenue", t.years[-1])
            if rev:
                parts.append(f"Top-paid executive total equals {self.derived(fmt.pct(top['total'] / rev, 3), 'CEO pay / revenue', [sid] + self.fact_sources(['revenue'], t.years[-1]), 'compensation')} of latest-year revenue" +
                             (f" and {self.derived(fmt.pct(top['total'] / ni, 2), 'CEO pay / net income', [sid] + self.fact_sources(['net_income'], t.years[-1]), 'compensation')} of net income." if ni and ni > 0 else "."))
        bb.update_section("compensation", status="done", narrative="\n\n".join(parts), completeness=1.0, data={"rows": sct["rows"], "accn": k["accn"]})
