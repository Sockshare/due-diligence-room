"""Editor: scores confidence, enforces source links, writes the executive summary last, assembles the memo."""
from __future__ import annotations

from datetime import datetime, timezone

from .. import fmt, memo
from .base import SECTION_ORDER, DDAgent

SYSTEM = ("You are the editor of an investment committee memo. Write a single-paragraph executive summary (max 170 words) that is balanced: "
          "what the company is, the two or three most important sourced facts, the key forensic/legal flags, and the Skeptic's strongest objection. "
          "Keep every bracketed citation like [text][S12] exactly as written. Do not introduce any number that is not already cited in the input.")


class Editor(DDAgent):
    name = "Editor"
    role = "Confidence scoring, source enforcement, executive summary, memo assembly"
    color = "#34d399"

    def run(self) -> None:
        self.step("score section confidence", self.score_confidence)
        self.step("executive summary", self.executive_summary)
        self.step("assemble memo & audit sources", self.assemble, required=True)

    def score_confidence(self) -> str:
        bb = self.bb
        for key, _, _ in SECTION_ORDER:
            if key in bb.sections and key != "executive_summary":
                c, notes = memo.section_confidence(bb, key)
                bb.update_section(key, confidence=c, confidence_notes=notes)
        vals = [s.confidence for k, s in bb.sections.items() if s.confidence is not None]
        return f"mean confidence {sum(vals) / len(vals):.0%}" if vals else "no sections"

    def executive_summary(self) -> None:
        bb, rc = self.bb, self.rc
        bb.update_section("executive_summary", status="in_progress")
        fin = bb.sections["financials"].data if "financials" in bb.sections else {}
        rows = fin.get("rows", [])
        summ = fin.get("summary", {})
        val = bb.sections["valuation"].data if "valuation" in bb.sections else {}
        fz = bb.sections["forensics"].data if "forensics" in bb.sections else {}
        mets = {m["key"]: m for m in fz.get("metrics", [])}
        bits = []
        bits.append(f"**{rc.name}** ({rc.ticker}) — {rc.sic_desc or 'SIC ' + str(rc.sic)}.")
        if rows:
            last = rows[-1]
            y = last["year"]
            bits.append(f"FY{y} revenue {self.cf('revenue', y)} and net income {self.cf('net_income', y)}" +
                        (f"; revenue CAGR {self.derived(fmt.pct(summ['revenue_cagr']), 'Revenue CAGR', self.fact_sources(['revenue'], rows[0]['year']) + self.fact_sources(['revenue'], y), 'cagr')} over {summ.get('years', 0)} years" if summ.get("revenue_cagr") is not None else "") +
                        (f"; FCF {self.derived(fmt.money(last['fcf'], rc.currency), 'FCF', self.fact_sources(['cfo', 'capex'], y), 'fcf')}" if last.get("fcf") is not None else "") + ".")
        if val.get("pe") is not None:
            ms = self.src_market()
            inputs = [ms] if ms else []
            bits.append(f"Trailing P/E {self.derived(fmt.ratio(val['pe'], 1), 'P/E', inputs + self.fact_sources(['net_income'], val['year']), 'valuation')}" +
                        (f", EV/EBITDA {self.derived(fmt.ratio(val['ev_ebitda'], 1), 'EV/EBITDA', inputs + self.fact_sources(['operating_income', 'da'], val['year']), 'valuation')}" if val.get("ev_ebitda") else "") + ".")
        elif val:
            bits.append("Market multiples unavailable (no price feed).")
        lights = {k: m["light"] for k, m in mets.items()}
        reds = [mets[k]["label"] for k, l in lights.items() if l == "red"]
        ambers = [mets[k]["label"] for k, l in lights.items() if l == "amber"]
        greys = [mets[k]["label"] for k, l in lights.items() if l == "grey"]
        if mets:
            bits.append("Forensic screens: " + (f"red on {', '.join(reds)}; " if reds else "no red flags; ") + (f"amber on {', '.join(ambers)}; " if ambers else "") +
                        (f"not computable: {', '.join(greys)}." if greys else "all computable."))
        ae = bb.sections["audit_events"].data if "audit_events" in bb.sections else {}
        if ae:
            flags = []
            if ae.get("auditor_changes"):
                flags.append("auditor change")
            if ae.get("restatements"):
                flags.append("restatement 8-K")
            if (ae.get("going_concern") or {}).get("substantial_doubt"):
                flags.append("going-concern doubt")
            bits.append(("Governance flags: " + ", ".join(flags) + ".") if flags else "No auditor-change, restatement or going-concern flags in the index.")
        bear = [c for c in bb.claims if c.kind == "bearish"]
        if bear:
            bits.append(f"The Skeptic's strongest objection: {self.q(bear[0].text.rstrip('.'), bear[0].support[0] if bear[0].support else None)}.")
        unresolved = bb.sections["disagreements"].data.get("unresolved", 0) if "disagreements" in bb.sections else 0
        bits.append(f"{unresolved} disagreement(s) between agents remain unresolved; {len(bb.gaps)} data gap(s) were logged. Section confidence bars reflect both.")
        fallback = " ".join(bits)
        text, mode = self.write("executive summary", SYSTEM, "Sourced facts and flags:\n" + fallback + "\n\nBear case:\n" + (bb.sections["bear_case"].narrative[:3000] if "bear_case" in bb.sections else ""), fallback=fallback, max_tokens=400)
        if mode != "offline":
            unsourced = memo.audit_numbers(text)
            if unsourced:
                self.say(f"LLM summary introduced {len(unsourced)} uncited figure(s); reverting to deterministic summary", status="warn")
                text, mode = fallback, "offline"
            else:
                text += f"\n\n_Drafted by {mode}; all figures carry citations from the blackboard._"
        comps = [s.confidence for k, s in bb.sections.items() if s.confidence is not None and k != "executive_summary"]
        conf = sum(comps) / len(comps) if comps else 0.0
        bb.update_section("executive_summary", status="done", narrative=text, confidence=conf, confidence_notes=["mean of all section confidences"], completeness=1.0, data={"mode": mode})

    def assemble(self) -> str:
        bb = self.bb
        md = memo.assemble_markdown(bb, SECTION_ORDER, generated_at=datetime.now(timezone.utc))
        unsourced = memo.audit_numbers(md)
        if unsourced:
            self.say(f"source audit: {len(unsourced)} figure(s) without a citation — listed in meta.unsourced", status="warn")
        else:
            self.say("source audit: every financial figure carries a citation")
        html = memo.render_html(md)
        stats = memo.memo_stats(md)
        bb.meta["unsourced"] = unsourced[:50]
        bb.meta["stats"] = stats
        bb.meta["memo_md"] = md
        bb.meta["memo_html"] = html
        self.bus.emit("memo", "memo assembled", agent=self.name, data={"stats": stats, "unsourced": len(unsourced)})
        return f"{stats['words']} words, {stats['citations']} citations, ~{stats['pages_est']} pages, {len(unsourced)} unsourced figure(s)"
