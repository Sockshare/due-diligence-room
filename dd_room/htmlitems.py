"""Tolerant HTML → text and 10-K item extraction.

EDGAR 10-K documents are inline-XBRL HTML of wildly varying quality. We convert
to text with a forgiving ``html.parser`` subclass, then locate items with a
*largest-span* heuristic: each item heading usually appears at least twice (in
the table of contents and at the start of the section) so we take the candidate
whose span to the next heading is longest.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional

BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "ul", "ol", "blockquote", "pre", "hr", "title"}
CELL_TAGS = {"td", "th"}
SKIP_TAGS = {"script", "style", "head", "noscript", "svg"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in SKIP_TAGS:
            self._skip += 1
        elif t in BLOCK_TAGS:
            self.parts.append("\n")
        elif t in CELL_TAGS:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif t in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(doc: str) -> str:
    if not doc:
        return ""
    # ix:header blocks hold hidden XBRL context — drop them wholesale
    doc = re.sub(r"<ix:header>.*?</ix:header>", " ", doc, flags=re.S | re.I)
    p = _TextExtractor()
    try:
        p.feed(doc)
        p.close()
    except Exception:  # pragma: no cover - html.parser is very tolerant; belt and braces
        pass
    text = "".join(p.parts)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Item boundaries: (item key, start pattern, list of possible next-item patterns)
_ITEM_HEAD = r"(?:^|\n)\s*(?:PART\s+[IV]+\W{{0,3}}\s*)?ITEM\s*{num}(?![A-Za-z0-9])\s*[\.:\-—–]?\s*(?:{title}[^\n]{{0,80}})?"

ITEM_SPECS = {
    "1": {"num": r"1", "title": r"BUSINESS", "next": [r"1A", r"1B", r"1C", r"2"]},
    "1A": {"num": r"1A", "title": r"RISK\s+FACTORS", "next": [r"1B", r"1C", r"2", r"3"]},
    "3": {"num": r"3", "title": r"LEGAL\s+PROCEEDINGS", "next": [r"4", r"5"]},
    "7": {"num": r"7", "title": r"MANAGEMENT['’]?S\s+DISCUSSION", "next": [r"7A", r"8"]},
}


def _start_regex(num: str, title: str) -> re.Pattern:
    return re.compile(_ITEM_HEAD.format(num=num, title=title), re.I)


def _end_regex(nexts: list[str]) -> re.Pattern:
    alts = "|".join(nexts)
    return re.compile(r"(?:^|\n)\s*(?:PART\s+[IV]+\W{0,3}\s*)?ITEM\s*(?:" + alts + r")\b\s*[\.:\-—–]?", re.I)


@dataclass
class ItemExtraction:
    item: str
    text: str
    start: int
    end: int
    candidates: int
    note: str = ""


def extract_item(text: str, item: str, min_chars: int = 400) -> Optional[ItemExtraction]:
    spec = ITEM_SPECS[item]
    start_re = _start_regex(spec["num"], spec["title"])
    end_re = _end_regex(spec["next"])
    best: Optional[ItemExtraction] = None
    starts = list(start_re.finditer(text))
    for m in starts:
        s = m.start()
        e_m = end_re.search(text, m.end())
        e = e_m.start() if e_m else len(text)
        body = text[m.end():e].strip()
        if best is None or len(body) > len(best.text):
            best = ItemExtraction(item=item, text=body, start=s, end=e, candidates=len(starts))
    if best is None:
        return None
    # heading wrapped onto two lines ("...Financial Condition\nand Results of Operations"): drop the fragment
    m = re.match(r"^(?:and|of|for)\s[^\n]{0,80}?(?:\n|  )", best.text)
    if m:
        best.text = best.text[m.end():].strip()
    if len(best.text) < min_chars:
        best.note = f"Item {item} section found but very short ({len(best.text)} chars) — may be incorporated by reference or omitted"
    return best


def extract_items(doc_html: str, items: tuple[str, ...] = ("1", "1A", "3", "7")) -> dict[str, Optional[ItemExtraction]]:
    text = html_to_text(doc_html)
    return {it: extract_item(text, it) for it in items}


# ----------------------------------------------------------------------------- risk factor analysis
RISK_CLUSTERS: dict[str, list[str]] = {
    "Macro & demand": ["economic conditions", "recession", "inflation", "consumer spending", "demand for our", "interest rates", "macroeconomic", "tariff", "trade polic"],
    "Competition & pricing": ["competit", "pricing pressure", "market share", "new entrants", "lower-cost", "price reductions"],
    "Supply chain & operations": ["supply chain", "supplier", "manufactur", "component", "shortage", "logistics", "single source", "sole source", "capacity"],
    "Regulation & legal": ["regulat", "legislation", "antitrust", "litigation", "legal proceedings", "government investigation", "compliance", "fines", "penalties", "export control", "sanction"],
    "Cybersecurity & data": ["cyber", "data breach", "security breach", "privacy", "personal data", "ransomware", "information technology systems"],
    "Financial & liquidity": ["indebtedness", "liquidity", "covenant", "credit facility", "going concern", "raise additional capital", "dilution", "impairment", "tax rate", "foreign currency", "exchange rate"],
    "People & key personnel": ["key personnel", "retain", "attract and retain", "employees", "labor", "workforce", "union"],
    "Technology & IP": ["intellectual property", "patent", "infring", "trade secret", "technological change", "new products", "innovation", "artificial intelligence"],
    "International & geopolitical": ["international operations", "geopolitical", "china", "war", "conflict", "political instability", "foreign operations"],
    "Stock & governance": ["stock price", "volatil", "dividend", "share repurchase", "anti-takeover", "controlling stockholder", "dual class", "concentrat"],
    "Health, safety & environment": ["climate", "environmental", "natural disaster", "pandemic", "public health", "product safety", "recall", "esg"],
}


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[\.\!\?])\s+(?=[A-Z\"“(])", text)
    return [p.strip() for p in parts if len(p.strip()) >= 40]


def cluster_risk_factors(text: str, max_examples: int = 2) -> list[dict]:
    sents = split_sentences(text)
    out = []
    for name, keys in RISK_CLUSTERS.items():
        hits = [s for s in sents if any(k in s.lower() for k in keys)]
        if not hits:
            continue
        # prefer sentences that look like risk headings (shorter, assertive)
        examples = sorted(hits, key=lambda s: (len(s) > 260, len(s)))[:max_examples]
        out.append({"cluster": name, "sentences": len(hits), "share": len(hits) / max(1, len(sents)), "examples": examples})
    out.sort(key=lambda c: -c["sentences"])
    return out


def _norm_sentence(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\d[\d,\.]*", "#", s)
    s = re.sub(r"[^a-z# ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def diff_risk_factors(current: str, prior: str, max_items: int = 8) -> dict:
    cur = {_norm_sentence(s): s for s in split_sentences(current)}
    old = {_norm_sentence(s): s for s in split_sentences(prior)}
    added = [cur[k] for k in cur if k not in old]
    removed = [old[k] for k in old if k not in cur]
    # Rank new sentences by distinct vocabulary vs prior text (crude novelty score)
    old_words = set(" ".join(old.keys()).split())

    def novelty(s: str) -> float:
        w = set(_norm_sentence(s).split())
        return len(w - old_words) / max(1, len(w))
    added.sort(key=novelty, reverse=True)
    removed.sort(key=len, reverse=True)
    return {
        "current_sentences": len(cur), "prior_sentences": len(old),
        "added_count": len(added), "removed_count": len(removed),
        "unchanged_count": len([k for k in cur if k in old]),
        "added_examples": added[:max_items], "removed_examples": removed[:max_items],
        "length_change_pct": (len(current) / len(prior) - 1.0) if prior else None,
    }


GOING_CONCERN_PATTERNS = [
    r"substantial doubt (?:about|regarding|as to) (?:the company'?s?|our|its) ability to continue as a going concern",
    r"ability to continue as a going concern",
    r"going[- ]concern",
]


def going_concern_mentions(text: str, max_snippets: int = 3) -> dict:
    low = text.lower()
    snippets: list[str] = []
    substantial = bool(re.search(GOING_CONCERN_PATTERNS[0], low))
    for m in re.finditer(GOING_CONCERN_PATTERNS[2], low):
        s = max(0, m.start() - 160)
        e = min(len(text), m.end() + 160)
        snip = re.sub(r"\s+", " ", text[s:e]).strip()
        # skip boilerplate "basis of presentation ... going concern basis" phrasing
        snippets.append(snip)
        if len(snippets) >= max_snippets:
            break
    return {"mentions": len(re.findall(GOING_CONCERN_PATTERNS[2], low)), "substantial_doubt": substantial, "snippets": snippets}


# ----------------------------------------------------------------------------- tables (for DEF 14A)
class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._row: Optional[list[str]] = None
        self._cell: Optional[list[str]] = None
        self.text_before: list[str] = []  # text preceding each table (for heading detection)
        self._recent: list[str] = []

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "table":
            self._stack.append([])
            self.text_before.append(" ".join(self._recent)[-600:])
        elif t == "tr" and self._stack:
            self._row = []
        elif t in CELL_TAGS and self._row is not None:
            self._cell = []
        elif t == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in CELL_TAGS and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif t == "tr" and self._stack and self._row is not None:
            if any(c for c in self._row):
                self._stack[-1].append(self._row)
            self._row = None
        elif t == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif not self._stack:
            d = data.strip()
            if d:
                self._recent.append(d)
                self._recent = self._recent[-40:]


def parse_tables(doc_html: str) -> list[tuple[str, list[list[str]]]]:
    p = _TableParser()
    try:
        p.feed(doc_html)
        p.close()
    except Exception:  # pragma: no cover
        pass
    return list(zip(p.text_before, p.tables))


_MONEY = re.compile(r"^\(?\$?\s*[\d,]+(?:\.\d+)?\)?$")


def _to_number(s: str) -> Optional[float]:
    s = s.strip().replace("$", "").replace(",", "").replace("—", "").replace("-", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _first_number(cell: str) -> Optional[float]:
    m = re.search(r"\(?\$?\s*(\d[\d,]*(?:\.\d+)?)\)?", cell)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def summary_compensation_table(doc_html: str, max_rows: int = 8) -> dict:
    """Best-effort extraction of the Summary Compensation Table from a DEF 14A.

    Returns {"rows": [{"name", "year", "total"}], "note": str}. Rows are
    name/year/total only — we deliberately do not try to parse every column.
    """
    best: Optional[list[list[str]]] = None
    for before, table in parse_tables(doc_html):
        if len(table) < 3:
            continue
        header_text = " ".join(" ".join(r) for r in table[:4]).lower()
        if "salary" in header_text and "total" in header_text and ("name" in header_text or "principal position" in header_text):
            if best is None or len(table) > len(best):
                best = table
    if not best:
        return {"rows": [], "note": "Summary Compensation Table not found or not parseable in DEF 14A"}
    # locate 'total' column from header rows
    total_col = None
    for r in best[:4]:
        cells = [c.lower() for c in r]
        for i, c in enumerate(cells):
            if c.startswith("total"):
                total_col = i
        if total_col is not None:
            break
    rows: list[dict] = []
    last_name = ""
    for r in best:
        cells = [c for c in r if c != ""]
        if not cells:
            continue
        if len(cells) < 3:
            if _to_number(cells[0]) is None and not re.fullmatch(r"(19|20)\d\d", cells[0].strip()) and len(cells[0]) < 80:
                last_name = re.split(r"\s{2,}|,|\(", cells[0])[0].strip()
            continue
        name_cell = cells[0]
        if not re.fullmatch(r"(19|20)\d\d", name_cell.strip()) and _to_number(name_cell) is None:
            last_name = re.split(r"\s{2,}|,|\(", name_cell)[0].strip()
        year = None
        for c in cells[:3]:
            m = re.match(r"\s*((?:19|20)\d\d)(?:\s+(?:19|20)\d\d)*\s*$", c)
            if m:
                year = int(m.group(1))  # stacked "2025 2024 2023" cells: first = most recent
                break
        if year is None:
            continue
        nums = [n for n in (_first_number(c) for c in cells[1:]) if n is not None and n > 1000]
        total = nums[-1] if nums else None  # Total is conventionally the last numeric column
        if last_name and total is not None:
            rows.append({"name": last_name, "year": year, "total": total})
        if len(rows) >= max_rows * 3:
            break
    # keep most recent year per name
    latest: dict[str, dict] = {}
    for row in rows:
        if row["name"] not in latest or row["year"] > latest[row["name"]]["year"]:
            latest[row["name"]] = row
    out = sorted(latest.values(), key=lambda r: -r["total"])[:max_rows]
    return {"rows": out, "note": "" if out else "Summary Compensation Table found but rows not parseable", "total_col": total_col}


# ----------------------------------------------------------------------------- segment tables (best effort)
_TOTAL_ROW = re.compile(r"^total\b", re.I)


def segment_revenue_tables(doc_html: str, max_tables: int = 2, max_rows: int = 8) -> list[dict]:
    """Best-effort: tables whose preceding text mentions segments/products/geography and that end in a
    'Total net sales/revenue' row. Returns [{'heading', 'rows': [{'label', 'value'}], 'total'}]. The first numeric
    column is assumed to be the most recent fiscal year, which is the overwhelming EDGAR convention."""
    out: list[dict] = []
    seen_signatures = set()
    for before, table in parse_tables(doc_html):
        b = before.lower()
        if not any(k in b for k in ("segment", "net sales by", "revenue by", "by category", "by product", "geographic", "disaggregat")):
            continue
        rows: list[dict] = []
        total: Optional[float] = None
        for r in table:
            cells = [c for c in r if c not in ("", "$", ")", "%")]
            if len(cells) < 2:
                continue
            label = cells[0]
            if _first_number(label) is not None and len(label) < 12:
                continue
            val = None
            for c in cells[1:]:
                v = _first_number(c)
                if v is not None and not re.fullmatch(r"\(?\d{1,3}\)?\s*%?", c.strip()):
                    val = -v if c.strip().startswith("(") else v
                    break
            if val is None:
                continue
            if _TOTAL_ROW.match(label):
                total = val
                break
            if len(label) <= 60 and len(rows) < max_rows:
                rows.append({"label": re.sub(r"\s*\(\d+\)\s*$", "", label), "value": val})
        if total and rows and len(rows) >= 2:
            s = sum(r["value"] for r in rows)
            if 0.9 <= s / total <= 1.1:  # components should roughly add to the total
                sig = tuple(r["label"] for r in rows)
                if sig in seen_signatures:
                    continue
                seen_signatures.add(sig)
                out.append({"heading": before[-160:].strip(), "rows": rows, "total": total})
        if len(out) >= max_tables:
            break
    return out
