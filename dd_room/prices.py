"""Optional price history from the Yahoo Finance chart endpoint.

Yahoo is not an official API; this is best-effort and the app degrades to
"no market data" when it fails. Nothing here is required for a memo.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .sec import SecClient

YAHOO_URLS = [
    "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1d",
    "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range=5y&interval=1d",
]
# Yahoo throttles some full browser UA strings harder than a plain compatible one.
BROWSER_UA = "Mozilla/5.0 (compatible; DueDiligenceRoom/1.0)"


def fetch_prices(client: SecClient, ticker: str) -> dict:
    last: dict = {"ok": False, "error": "no attempt", "points": [], "latest": None}
    for tmpl in YAHOO_URLS:
        url = tmpl.format(ticker=ticker.upper().replace(".", "-"))
        data = client.fetch_json(url, ttl=6 * 3600.0, headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})
        last = parse_chart(data, url)
        if last["ok"]:
            return last
    return last


def parse_chart(data: Optional[dict], url: str = "") -> dict:
    out = {"ok": False, "source": url, "error": None, "points": [], "latest": None, "latest_date": None}
    try:
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        out["error"] = "Yahoo Finance chart endpoint returned no usable data (endpoint unavailable, rate-limited, or ticker not quoted)"
        if isinstance(data, dict) and data.get("chart", {}).get("error"):
            out["error"] = f"Yahoo Finance: {data['chart']['error']}"
        return out
    pts = [(datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(), float(c)) for t, c in zip(ts, closes) if c is not None]
    if not pts:
        out["error"] = "Yahoo Finance returned an empty series"
        return out
    out["ok"] = True
    out["latest"] = pts[-1][1]
    out["latest_date"] = pts[-1][0]
    out["currency"] = res.get("meta", {}).get("currency")
    # weekly downsample for storage / chart
    weekly = pts[::5]
    if weekly[-1] != pts[-1]:
        weekly.append(pts[-1])
    out["points"] = weekly
    last = pts[-1][1]
    year_ago = [p for p in pts if p[0] <= _shift(pts[-1][0], 365)]
    out["return_1y"] = (last / year_ago[-1][1] - 1.0) if year_ago else None
    out["return_5y"] = (last / pts[0][1] - 1.0) if len(pts) > 250 else None
    yr = [p[1] for p in pts if p[0] > _shift(pts[-1][0], 365)]
    out["high_52w"] = max(yr) if yr else None
    out["low_52w"] = min(yr) if yr else None
    peak, mdd = pts[0][1], 0.0
    for _, c in pts:
        peak = max(peak, c)
        mdd = min(mdd, c / peak - 1.0)
    out["max_drawdown_5y"] = mdd
    return out


def _shift(iso: str, days: int) -> str:
    from datetime import date, timedelta
    y, m, d = (int(x) for x in iso.split("-"))
    return (date(y, m, d) - timedelta(days=days)).isoformat()
