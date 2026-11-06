"""Memo assembly, source enforcement, confidence scoring and HTML rendering."""
from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone
from typing import Optional

import markdown as _md

from agentkit import Blackboard

# ----------------------------------------------------------------------------- number audit
# Tokens we consider "financial figures" that must carry a citation:
#   $1,234 / 12.5% / 3.4x / 391.0B / 1,234,567 / 0.85 / −12.3%
_FIGURE = re.compile(
    r"(?<![\w\[\(/#\-])"                      # not inside a word, link, path or anchor
    r"(?:[−\-+]?\s?[$€£¥]\s?\d[\d,]*(?:\.\d+)?\s?(?:[TBMK]|bn|mm)?"   # currency amounts
    r"|[−\-+]?\d[\d,]*\.\d+\s?(?:%|x|[TBMK]\b)?"                          # decimals (with optional unit)
    r"|[−\-+]?\d[\d,]*\s?(?:%|x\b)"                                        # integers with % or x
    r"|\d{1,3}(?:,\d{3})+\b"                                                 # thousands-separated integers
    r"|\d[\d,]*(?:\.\d+)?\s?(?:[TBMK]\b|bn\b|mm\b)"                        # magnitude suffix
    r")"
)
_LINK_TEXT = re.compile(r"\[([^\]]*)\]\[S\d+\]")           # cited: [text][S12]
_INLINE_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")        # ordinary links
_CODE = re.compile(r"`[^`]*`")
_REFDEF = re.compile(r"^\[S\d+\]:.*$", re.M)
_YEAR = re.compile(r"^(19|20)\d\d$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME = re.compile(r"\d{1,2}:\d{2}")


_CONFBAR = re.compile(r'<div class="confbar".*?</div>', re.S)
_TAG = re.compile(r"<[^>]+>")


def cite_all_figures(text: str, source_id: Optional[str]) -> str:
    """Attach ``[tok][Sn]`` to every figure token in plain text (used for claim text and quoted filing excerpts)."""
    if not source_id:
        return text
    def rep(m):
        return f"[{m.group(0)}][{source_id}]"
    # protect existing citations
    parts = re.split(r"(\[[^\]]*\]\[S\d+\])", text)
    return "".join(p if re.fullmatch(r"\[[^\]]*\]\[S\d+\]", p) else _FIGURE.sub(rep, p) for p in parts)


def strip_cited(md: str) -> str:
    """Remove everything that is already cited or is not prose, leaving the text that must be clean."""
    s = _REFDEF.sub("", md)
    s = _CONFBAR.sub(" ", s)      # confidence meta line (scores, not financial figures)
    s = re.sub(r"<!-- sources:start -->.*?<!-- sources:end -->", " ", s, flags=re.S)  # the sources table *is* the citations
    s = _TAG.sub(" ", s)
    s = _CODE.sub(" ", s)
    s = _LINK_TEXT.sub(" ", s)
    s = _INLINE_LINK.sub(" ", s)
    s = _DATE.sub(" ", s)
    s = _TIME.sub(" ", s)
    s = re.sub(r"\bFY\s?\d{4}\b|\bCY\s?\d{4}\b|\bQ[1-4]\s?\d{4}\b", " ", s)
    s = re.sub(r"\bItem\s+\d+[A-C]?\b|\b\d+-K(?:/A)?\b|\b\d+-Q(?:/A)?\b|\b13F(?:-HR)?\b|\bDEF\s+14A\b|\b10b5-1\b|\b8-K\b", " ", s)
    s = re.sub(r"\bS\d+\b|\bC\d+\b|\bX[1-5]\b", " ", s)   # source / claim / component ids
    s = re.sub(r"\b10%\s+owner", " ", s)                 # Form 4 relationship label
    s = re.sub(r"\(\d{4}\)", " ", s)                      # paper years
    s = re.sub(r"\b\d{4}[–\-]\d{2,4}\b", " ", s)          # year ranges
    s = re.sub(r"\b\d{4}\b", " ", s)                      # bare years
    s = re.sub(r"\b\d+-year\b|\b\d+\s?(?:years?|months?|days?|weeks?|times?|sentences?|filings?|hits?|pages?|characters?|rows?|peers?|insiders?|claims?)\b", " ", s)
    return s


def audit_numbers(md: str) -> list[dict]:
    """Return figure tokens that appear in prose without a citation, with context."""
    body = strip_cited(md)
    out = []
    for m in _FIGURE.finditer(body):
        tok = m.group(0).strip()
        if _YEAR.match(tok):
            continue
        # skip formula constants like "0.920·DSRI" and thresholds inside "*Formula:*"/"*Thresholds:*" lines
        line_start = body.rfind("\n", 0, m.start()) + 1
        line_end = body.find("\n", m.end())
        line = body[line_start: line_end if line_end != -1 else None]
        if line.lstrip().startswith(("*Formula:*", "*Thresholds:*", "*Reference:*", "M = ", "Z = ", "> ")):
            continue
        if any(kw in line for kw in ("Thresholds", "Formula", "cut-off", "threshold")):
            continue
        ctx = body[max(0, m.start() - 50): m.end() + 50].replace("\n", " ")
        out.append({"token": tok, "context": ctx})
    return out


# ----------------------------------------------------------------------------- confidence
def section_confidence(bb: Blackboard, key: str) -> tuple[float, list[str]]:
    s = bb.sections[key]
    notes: list[str] = []
    comp = s.completeness if s.completeness is not None else (0.5 if s.narrative else 0.0)
    notes.append(f"data completeness {comp:.0%}")
    score = comp
    unresolved = [d for d in bb.disagreements if not d["resolved"] and any(k in d["topic"] for k in (key,))]
    # claims attached to this section with non-low counters
    claim_ids = {c.id for c in bb.claims if c.section == key and c.kind == "bullish"}
    hard = [c for c in bb.counters if c["claim_id"] in claim_ids and c["severity"] in ("high", "medium")]
    if hard:
        pen = min(0.3, 0.08 * len([c for c in hard if c["severity"] == "high"]) + 0.04 * len([c for c in hard if c["severity"] == "medium"]))
        score -= pen
        notes.append(f"−{pen:.0%} for {len(hard)} unresolved counter(s) from the Skeptic")
    if unresolved:
        score -= 0.05 * len(unresolved)
        notes.append(f"−{5 * len(unresolved)}% for cross-agent disagreement")
    gaps = [g for g in bb.gaps if g["section"] == key]
    if gaps:
        pen = min(0.25, 0.05 * len(gaps))
        score -= pen
        notes.append(f"−{pen:.0%} for {len(gaps)} logged gap(s)")
    if s.status != "done":
        score = min(score, 0.2)
        notes.append("section did not complete")
    score = max(0.0, min(1.0, score))
    return score, notes


def confidence_label(c: Optional[float]) -> str:
    if c is None:
        return "unknown"
    return "high" if c >= 0.75 else "medium" if c >= 0.45 else "low"


# ----------------------------------------------------------------------------- assembly
DISCLAIMER = ("**Research aid, not investment advice.** This memo was generated automatically from public SEC EDGAR filings and an unofficial price feed. "
              "It may contain extraction errors, it does not know anything that is not in those sources, and it does not consider your circumstances. "
              "Nothing here is a recommendation to buy, sell or hold any security. Verify every figure against the linked filing before relying on it.")


def assemble_markdown(bb: Blackboard, order: list[tuple[str, str, str]], *, generated_at: Optional[datetime] = None, page_breaks: bool = True) -> str:
    meta = bb.meta
    generated_at = generated_at or datetime.now(timezone.utc)
    lines = [f"# {meta.get('name', '')} ({meta.get('ticker', '')}) — Investment Memo",
             f"_Generated {generated_at.strftime('%Y-%m-%d %H:%M UTC')} by Due-Diligence Room · depth: {meta.get('depth', '')} · mode: {meta.get('mode', 'offline')} · CIK {meta.get('cik', '')}_",
             "", DISCLAIMER, ""]
    # table of contents
    toc = []
    n = 0
    for key, title, owner in order:
        if key in bb.sections:
            n += 1
            s = bb.sections[key]
            toc.append(f"{n}. {title} — _{owner}_ · confidence {confidence_label(s.confidence)}")
    lines.append("## Contents\n\n" + "\n".join(toc) + "\n")
    n = 0
    for key, title, owner in order:
        if key not in bb.sections:
            continue
        n += 1
        s = bb.sections[key]
        if page_breaks:
            lines.append('<div class="page-break"></div>')
        lines.append(f"## {n}. {title}")
        conf = s.confidence
        bar = "█" * int(round((conf or 0) * 10)) + "░" * (10 - int(round((conf or 0) * 10)))
        lines.append(f"<div class=\"confbar\" data-conf=\"{(conf or 0):.2f}\"><span class=\"owner\">{owner}</span> · confidence <b>{confidence_label(conf)}</b> <code>{bar}</code>" +
                     (f" <small>({'; '.join(s.confidence_notes)})</small>" if s.confidence_notes else "") + "</div>\n")
        lines.append(s.narrative or "_(empty)_")
        lines.append("")
    # sources
    lines.append('<div class="page-break"></div>')
    lines.append(f"## {n + 1}. Sources")
    lines.append("Every cited figure links to one of the entries below. XBRL entries point at the filing index page; the concept name tells an expert exactly which tag was read. "
                 "Derived entries point at the methodology section and list the input sources they were computed from.\n")
    lines.append("<!-- sources:start -->")
    lines.append("| Id | Kind | Reference | Concept / period |\n|---|---|---|---|")
    for sid, src in bb.sources.items():
        ref = f"[{_html.escape(src.label[:140])}]({src.url})"
        extra = " ".join(x for x in [src.concept or "", src.period or ""] if x)
        if src.inputs:
            extra += " ← " + ", ".join(src.inputs[:8]) + ("…" if len(src.inputs) > 8 else "")
        lines.append(f"| {sid} | {src.kind} | {ref} | {extra} |")
    lines.append("<!-- sources:end -->")
    lines.append("")
    lines.append(f"## {n + 2}. Methodology & Disclaimer")
    lines.append("Formulas, thresholds and their citations are documented in the project's METHODOLOGY document (served at `/methodology`). "
                 "Annual values are taken from the SEC XBRL companyfacts API using only annual-report forms, one-year durations, and the latest-filed value per period. "
                 "Fiscal years are labelled by the calendar year of the period end date. Counts and parameters (e.g. number of filings, months in a window) are not footnoted; financial figures are.\n")
    lines.append(DISCLAIMER)
    lines.append("")
    # reference definitions
    for sid, src in bb.sources.items():
        title = src.label.replace('"', "'")[:200]
        lines.append(f"[{sid}]: {src.url} \"{title}\"")
    return "\n".join(lines)


def render_html(md_text: str, *, title: str = "Investment memo", standalone: bool = False) -> str:
    body = _md.markdown(md_text, extensions=["tables", "fenced_code", "toc"], output_format="html5")
    # mark citation anchors so CSS can style them subtly and the audit can count them
    body = re.sub(r'<a href="([^"]+)" title="([^"]*)">', r'<a class="cite" target="_blank" rel="noopener" href="\1" title="\2">', body)
    if not standalone:
        return body
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{_html.escape(title)}</title>
<link rel="stylesheet" href="/static/memo.css"></head><body class="memo-standalone"><article class="memo">{body}</article></body></html>"""


def memo_stats(md_text: str) -> dict:
    words = len(re.findall(r"\w+", md_text))
    cites = len(_LINK_TEXT.findall(md_text))
    return {"words": words, "citations": cites, "pages_est": round(words / 450, 1)}
