"""Unit tests for SEC rate limiter (TokenBucket), DiskCache TTL/stale fallback,
Form 4 XML parsing, and 8-K material event classification."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dd_room import filings, sec


def test_token_bucket_rate_limiter():
    now = [100.0]
    slept: list[float] = []

    def fake_clock() -> float:
        return now[0]

    def fake_sleep(dt: float) -> None:
        slept.append(dt)
        now[0] += dt

    bucket = sec.TokenBucket(rate=8.0, capacity=2, clock=fake_clock, sleeper=fake_sleep)
    # First 2 tokens burst immediately with 0 wait
    assert bucket.acquire() == pytest.approx(0.0)
    assert bucket.acquire() == pytest.approx(0.0)
    assert bucket.try_acquire() is False

    # 3rd token waits 1/8 = 0.125s
    waited = bucket.acquire()
    assert waited == pytest.approx(0.125)
    assert slept == [pytest.approx(0.125)]


def test_disk_cache_ttl_and_stale_fallback(tmp_path: Path):
    cache = sec.DiskCache(tmp_path / "cache")
    url = "https://data.sec.gov/submissions/CIK0000320193.json"
    payload = b'{"cik": 320193, "name": "Apple Inc."}'
    cache.put(url, payload)

    assert cache.get(url, max_age=60.0) == payload
    assert cache.get(url, max_age=-1.0) is None
    stale = cache.get_stale(url)
    assert stale is not None
    assert stale[0] == payload

    # Offline SecClient serves stale cache when TTL=0
    client = sec.SecClient(cache=cache, offline=True)
    res = client.fetch(url, ttl=-1.0)
    assert res.ok is True
    assert res.from_cache is True
    assert res.stale is True
    assert res.data == payload


def test_parse_and_summarise_form4():
    xml = """<?xml version="1.0"?>
    <ownershipDocument>
      <periodOfReport>2025-05-01</periodOfReport>
      <reportingOwner>
        <reportingOwnerId><rptOwnerName>SMITH ALEX</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship>
          <isDirector>1</isDirector>
          <isOfficer>1</isOfficer>
          <officerTitle>Chief Financial Officer</officerTitle>
        </reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <transactionDate><value>2025-05-01</value></transactionDate>
          <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>2000</value></transactionShares>
            <transactionPricePerShare><value>25.50</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
          <transactionDate><value>2025-05-02</value></transactionDate>
          <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
          <transactionAmounts>
            <transactionShares><value>500</value></transactionShares>
            <transactionPricePerShare><value>26.00</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
          </transactionAmounts>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>
    """
    p = filings.parse_form4(xml)
    assert p is not None
    assert p["owner"] == "SMITH ALEX"
    assert "Director" in p["relationship"]
    assert "Chief Financial Officer" in p["relationship"]
    assert len(p["transactions"]) == 2

    summ = filings.summarise_form4([p])
    assert summ["buy_shares"] == pytest.approx(2000.0)
    assert summ["buy_usd"] == pytest.approx(51_000.0)
    assert summ["sell_shares"] == pytest.approx(500.0)
    assert summ["sell_usd"] == pytest.approx(13_000.0)


def test_eight_k_and_auditor_events():
    subs = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001-25-0001", "0001-25-0002", "0001-25-0003"],
                "form": ["8-K", "8-K", "10-K/A"],
                "filingDate": ["2025-04-01", "2025-03-15", "2025-02-20"],
                "reportDate": ["2025-04-01", "2025-03-15", "2024-12-31"],
                "items": ["4.01,9.01", "2.02,7.01", ""],
                "primaryDocument": ["d1.htm", "d2.htm", "d3.htm"],
                "primaryDocDescription": ["", "", ""],
                "size": [1000, 1000, 5000],
            }
        }
    }
    tl = filings.eight_k_timeline(subs, months=24, today=date(2025, 6, 1))
    assert len(tl) == 2
    assert tl[0]["material"] is True
    assert tl[1]["material"] is False

    ev = filings.auditor_and_restatement_events(subs)
    assert len(ev["auditor_changes"]) == 1
    assert len(ev["restatements"]) == 0
    assert len(ev["amended_reports"]) == 1
