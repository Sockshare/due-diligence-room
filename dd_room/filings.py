"""Helpers over the submissions index and individual filings: 8-K event
timeline, auditor-change / restatement detection, Form 4 parsing, peers via
EFTS + XBRL frames, best-effort 13F holder list."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import Optional

from .sec import SecClient

EIGHT_K_ITEMS = {
    "1.01": "Entry into a material definitive agreement", "1.02": "Termination of a material definitive agreement",
    "1.03": "Bankruptcy or receivership", "1.05": "Material cybersecurity incident",
    "2.01": "Completion of acquisition or disposition of assets", "2.02": "Results of operations and financial condition",
    "2.03": "Creation of a direct financial obligation", "2.04": "Triggering events that accelerate a financial obligation",
    "2.05": "Costs associated with exit or disposal activities", "2.06": "Material impairments",
    "3.01": "Notice of delisting or failure to satisfy a listing rule", "3.02": "Unregistered sales of equity securities",
    "3.03": "Material modification to rights of security holders",
    "4.01": "Changes in registrant's certifying accountant", "4.02": "Non-reliance on previously issued financial statements (restatement)",
    "5.01": "Changes in control of registrant", "5.02": "Departure/election of directors or officers; compensatory arrangements",
    "5.03": "Amendments to articles or bylaws; change in fiscal year", "5.07": "Submission of matters to a vote of security holders",
    "5.08": "Shareholder nominations", "7.01": "Regulation FD disclosure", "8.01": "Other events", "9.01": "Financial statements and exhibits",
}
MATERIAL_ITEMS = {"1.01", "1.02", "1.03", "1.05", "2.01", "2.03", "2.04", "2.05", "2.06", "3.01", "4.01", "4.02", "5.01", "5.02"}


def filings_list(subs: dict) -> list[dict]:
    """Flatten submissions['filings']['recent'] into a list of dicts."""
    rec = (subs or {}).get("filings", {}).get("recent", {})
    n = len(rec.get("accessionNumber", []))
    out = []
    for i in range(n):
        out.append({
            "accn": rec["accessionNumber"][i], "form": rec["form"][i], "filed": rec["filingDate"][i],
            "report_date": rec.get("reportDate", [""] * n)[i], "items": rec.get("items", [""] * n)[i],
            "doc": rec.get("primaryDocument", [""] * n)[i], "desc": rec.get("primaryDocDescription", [""] * n)[i],
            "size": rec.get("size", [0] * n)[i],
        })
    return out


def latest_filings(subs: dict, form: str, n: int = 2) -> list[dict]:
    return [f for f in filings_list(subs) if f["form"] == form][:n]


def eight_k_timeline(subs: dict, months: int = 24, today: Optional[date] = None) -> list[dict]:
    today = today or date.today()
    cutoff = (today - timedelta(days=30 * months)).isoformat()
    out = []
    for f in filings_list(subs):
        if f["form"] not in ("8-K", "8-K/A") or f["filed"] < cutoff:
            continue
        codes = [c.strip() for c in (f["items"] or "").split(",") if c.strip()]
        out.append({
            "filed": f["filed"], "accn": f["accn"], "items": codes,
            "labels": [EIGHT_K_ITEMS.get(c, c) for c in codes],
            "material": any(c in MATERIAL_ITEMS for c in codes), "doc": f["doc"],
        })
    return out


def auditor_and_restatement_events(subs: dict) -> dict:
    changes, restatements = [], []
    for f in filings_list(subs):
        if not f["form"].startswith("8-K"):
            continue
        codes = (f["items"] or "").split(",")
        if "4.01" in codes:
            changes.append({"filed": f["filed"], "accn": f["accn"], "doc": f["doc"]})
        if "4.02" in codes:
            restatements.append({"filed": f["filed"], "accn": f["accn"], "doc": f["doc"]})
    amendments = [f for f in filings_list(subs) if f["form"] in ("10-K/A", "10-Q/A")][:5]
    return {"auditor_changes": changes, "restatements": restatements, "amended_reports": amendments}


# ------------------------------------------------------------------------------ Form 4
def parse_form4(xml_text: str) -> Optional[dict]:
    """Extract reporting owner, relationship, and non-derivative transactions (code, shares, price, A/D)."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return None

    def t(path, node=root):
        el = node.find(path)
        return (el.text or "").strip() if el is not None and el.text else ""
    owner = root.find("reportingOwner")
    name = t("reportingOwnerId/rptOwnerName", owner) if owner is not None else ""
    rel = []
    if owner is not None:
        r = owner.find("reportingOwnerRelationship")
        if r is not None:
            if t("isDirector", r) in ("1", "true"):
                rel.append("Director")
            if t("isOfficer", r) in ("1", "true"):
                rel.append(t("officerTitle", r) or "Officer")
            if t("isTenPercentOwner", r) in ("1", "true"):
                rel.append("10% owner")
    txns = []
    for tr in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = t("transactionCoding/transactionCode", tr)
        shares = t("transactionAmounts/transactionShares/value", tr)
        price = t("transactionAmounts/transactionPricePerShare/value", tr)
        ad = t("transactionAmounts/transactionAcquiredDisposedCode/value", tr)
        d = t("transactionDate/value", tr)
        try:
            sh = float(shares) if shares else None
        except ValueError:
            sh = None
        try:
            pr = float(price) if price else None
        except ValueError:
            pr = None
        txns.append({"date": d, "code": code, "shares": sh, "price": pr, "acquired_disposed": ad})
    return {"owner": name, "relationship": ", ".join(rel), "transactions": txns, "period": t("periodOfReport")}


def summarise_form4(parsed: list[dict]) -> dict:
    """Open-market purchases (P) vs sales (S) in shares and $ (price × shares when price given)."""
    buys = sells = 0.0
    buy_usd = sell_usd = 0.0
    buyers, sellers = set(), set()
    other_codes: dict[str, int] = {}
    for p in parsed:
        for tx in p["transactions"]:
            c = tx["code"]
            sh = tx["shares"] or 0.0
            usd = sh * tx["price"] if tx["price"] else 0.0
            if c == "P":
                buys += sh; buy_usd += usd; buyers.add(p["owner"])
            elif c == "S":
                sells += sh; sell_usd += usd; sellers.add(p["owner"])
            else:
                other_codes[c] = other_codes.get(c, 0) + 1
    return {"forms_parsed": len(parsed), "buy_shares": buys, "sell_shares": sells, "buy_usd": buy_usd, "sell_usd": sell_usd,
            "buyers": sorted(buyers), "sellers": sorted(sellers), "other_codes": other_codes}


def form4_filings(subs: dict, months: int = 12, today: Optional[date] = None) -> list[dict]:
    today = today or date.today()
    cutoff = (today - timedelta(days=30 * months)).isoformat()
    return [f for f in filings_list(subs) if f["form"] in ("4", "4/A") and f["filed"] >= cutoff]


def form4_raw_doc(primary_doc: str) -> str:
    """Submissions index lists the rendered path 'xslF345X05/form4.xml'; the raw XML is the bare filename."""
    return primary_doc.split("/")[-1]


# ------------------------------------------------------------------------------ peers
REVENUE_FRAME_CONCEPTS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]


def peer_ciks_by_sic(client: SecClient, sic: str, exclude_cik: int, today: Optional[date] = None, limit: int = 40) -> list[dict]:
    today = today or date.today()
    start = (today - timedelta(days=456)).isoformat()
    res = client.efts_search('"annual report"', forms=["10-K"], sics=[str(sic)], startdt=start, enddt=today.isoformat())
    out: dict[str, dict] = {}
    for h in ((res or {}).get("hits", {}).get("hits", []) or []):
        src = h.get("_source", {})
        for cik in src.get("ciks", []):
            try:
                c = int(cik)
            except ValueError:
                continue
            if c == exclude_cik or c in out:
                continue
            name = (src.get("display_names") or [""])[0]
            name = re.sub(r"\s*\(CIK \d+\)\s*$", "", name)
            tick = None
            m = re.search(r"\(([A-Z\.\-]{1,6})\)\s*$", name)
            if m:
                tick = m.group(1)
                name = name[: m.start()].strip()
            out[c] = {"cik": c, "name": name, "ticker": tick, "period_ending": src.get("period_ending")}
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return list(out.values())


def peer_table(client: SecClient, peers: list[dict], cy: int) -> list[dict]:
    """Attach CY revenue and net income from XBRL frames; peers lacking both are dropped."""
    frame = f"CY{cy}"
    rev: dict[int, dict] = {}
    for concept in REVENUE_FRAME_CONCEPTS:
        data = client.frame(concept, frame)
        for row in ((data or {}).get("data") or []):
            if row["cik"] not in rev:
                rev[row["cik"]] = {"val": row["val"], "accn": row["accn"], "concept": f"us-gaap:{concept}", "end": row.get("end")}
    ni: dict[int, dict] = {}
    data = client.frame("NetIncomeLoss", frame)
    for row in ((data or {}).get("data") or []):
        ni[row["cik"]] = {"val": row["val"], "accn": row["accn"], "concept": "us-gaap:NetIncomeLoss", "end": row.get("end")}
    out = []
    for p in peers:
        r, n = rev.get(p["cik"]), ni.get(p["cik"])
        if not r and not n:
            continue
        out.append({**p, "revenue": r, "net_income": n, "net_margin": (n["val"] / r["val"]) if r and n and r["val"] else None, "frame": frame})
    out.sort(key=lambda x: -(x["revenue"]["val"] if x["revenue"] else 0))
    return out


# ------------------------------------------------------------------------------ 13F best effort
def notable_holders(client: SecClient, company_name: str, today: Optional[date] = None, limit: int = 12) -> dict:
    today = today or date.today()
    start = (today - timedelta(days=200)).isoformat()
    q = '"' + re.sub(r"[^A-Za-z0-9 &\.]", " ", company_name).strip() + '"'
    res = client.efts_search(q, forms=["13F-HR"], startdt=start, enddt=today.isoformat())
    holders: dict[str, dict] = {}
    total = 0
    if res:
        total = (res.get("hits", {}).get("total", {}) or {}).get("value", 0)
        for h in res.get("hits", {}).get("hits", []) or []:
            src = h.get("_source", {})
            name = re.sub(r"\s*\(CIK \d+\)\s*$", "", (src.get("display_names") or [""])[0])
            if name and name not in holders:
                holders[name] = {"name": name, "filed": src.get("file_date"), "period": src.get("period_ending"), "accn": src.get("adsh")}
            if len(holders) >= limit:
                break
    return {"query": q, "total_hits": total, "holders": list(holders.values()),
            "note": "Best-effort: full-text hits on the company name inside 13F-HR information tables; not a ranked or complete holder list and may include false positives."}
