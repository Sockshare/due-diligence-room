"""Minimal pure-Python PDF writer: Markdown → plain text pages with Helvetica.

Not a typesetting engine. Headings are larger/bold, tables become aligned
monospace blocks, links are stripped to their text (the Sources section keeps
URLs). For a nicer PDF use the browser's print dialog on the memo page, which
has a print stylesheet.
"""
from __future__ import annotations

import re
import textwrap
import zlib
from typing import Iterable

PAGE_W, PAGE_H = 612, 792   # US Letter, points
MARGIN = 54
LINE_H = 13
BODY_PT = 9.5
MONO_PT = 7.5
H1_PT, H2_PT, H3_PT = 18, 14, 11.5
CHARS_PER_LINE = 100
MONO_CHARS = 118

_WINANSI_MAP = {"−": "-", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "·": "-", "→": "->", "←": "<-", "≈": "~", "≤": "<=", "≥": ">=",
                "█": "#", "░": ".", "🟢": "[G]", "🟡": "[A]", "🔴": "[R]", "⚪": "[-]", "⚠": "!", "✓": "v", "✗": "x", "▶": ">", "×": "x", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "\xa0": " "}


def _ascii(s: str) -> str:
    for k, v in _WINANSI_MAP.items():
        s = s.replace(k, v)
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _md_to_lines(md: str) -> Iterable[tuple[str, str]]:
    """Yield (style, text). style in h1,h2,h3,body,mono,blank."""
    in_table = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if re.match(r"^\[S\d+\]:", line):
            # reference definitions: keep URL for the sources appendix
            m = re.match(r"^\[(S\d+)\]:\s*(\S+)", line)
            if m:
                yield "mono", f"{m.group(1)}: {m.group(2)}"
            continue
        if line.startswith("<div") or line.startswith("</div"):
            m = re.search(r"confidence <b>(\w+)</b>", line)
            if m:
                yield "body", f"Confidence: {m.group(1)}"
            continue
        line = re.sub(r"\[([^\]]*)\]\[S\d+\]", r"\1", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"<[^>]+>", "", line)
        line = line.replace("**", "").replace("`", "")
        line = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", line)
        if not line.strip():
            if in_table:
                in_table = False
            yield "blank", ""
            continue
        if line.startswith("|"):
            if re.match(r"^\|\s*-{2,}", line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            in_table = True
            yield "mono", "  ".join(c[:28].ljust(min(28, max(6, len(c)))) for c in cells)
            continue
        if line.startswith("# "):
            yield "h1", line[2:]
        elif line.startswith("## "):
            yield "h2", line[3:]
        elif line.startswith("### ") or line.startswith("#### "):
            yield "h3", line.lstrip("# ")
        elif line.startswith("> "):
            yield "body", "    " + line[2:]
        else:
            yield "body", line


def markdown_to_pdf(md: str) -> bytes:
    pages: list[list[tuple[str, str]]] = [[]]
    y_used = 0.0

    def push(style: str, text: str, h: float):
        nonlocal y_used
        if y_used + h > PAGE_H - 2 * MARGIN:
            pages.append([])
            y_used = 0.0
        pages[-1].append((style, text))
        y_used += h

    for style, text in _md_to_lines(md):
        text = _ascii(text)
        if style == "blank":
            push("blank", "", LINE_H * 0.5)
        elif style == "h1":
            push("h1", text[:70], LINE_H * 2.2)
        elif style == "h2":
            push("blank", "", LINE_H * 0.6)
            push("h2", text[:90], LINE_H * 1.8)
        elif style == "h3":
            push("h3", text[:110], LINE_H * 1.4)
        elif style == "mono":
            for chunk in textwrap.wrap(text, MONO_CHARS) or [""]:
                push("mono", chunk, LINE_H * 0.85)
        else:
            for chunk in textwrap.wrap(text, CHARS_PER_LINE) or [""]:
                push("body", chunk, LINE_H)

    objs: list[bytes] = []

    def add(obj: bytes) -> int:
        objs.append(obj)
        return len(objs)

    font_reg = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    font_bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    font_mono = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>")
    page_ids: list[int] = []
    pages_obj_index = len(objs) + 1 + 2 * len(pages)  # placeholder id for /Pages computed below
    content_ids = []
    for pno, page in enumerate(pages):
        y = PAGE_H - MARGIN
        ops = []
        for style, text in page:
            if style == "blank":
                y -= LINE_H * 0.5
                continue
            font, size, dy = {"h1": ("/F2", H1_PT, LINE_H * 2.2), "h2": ("/F2", H2_PT, LINE_H * 1.8), "h3": ("/F2", H3_PT, LINE_H * 1.4),
                              "mono": ("/F3", MONO_PT, LINE_H * 0.85), "body": ("/F1", BODY_PT, LINE_H)}[style]
            y -= dy
            ops.append(f"BT {font} {size} Tf {MARGIN} {y:.1f} Td ({_pdf_escape(text)}) Tj ET")
        ops.append(f"BT /F1 7 Tf {MARGIN} {MARGIN / 2:.1f} Td (Due-Diligence Room - research aid, not investment advice - page {pno + 1}/{len(pages)}) Tj ET")
        stream = zlib.compress("\n".join(ops).encode("latin-1", errors="replace"))
        cid = add(b"<< /Length " + str(len(stream)).encode() + b" /Filter /FlateDecode >>\nstream\n" + stream + b"\nendstream")
        content_ids.append(cid)
    pages_id = len(objs) + len(pages) + 1
    for cid in content_ids:
        pid = add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Contents {cid} 0 R /Resources << /Font << /F1 {font_reg} 0 R /F2 {font_bold} 0 R /F3 {font_mono} 0 R >> >> >>".encode())
        page_ids.append(pid)
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    real_pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())
    assert real_pages_id == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
