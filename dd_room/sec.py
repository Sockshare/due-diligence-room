"""SEC EDGAR client.

* Sends the descriptive User-Agent SEC asks for (configurable via SEC_USER_AGENT).
* Enforces SEC's fair-access limit (10 requests/second) with a token bucket,
  defaulting to 8/s to leave headroom.
* Caches JSON/HTML responses on disk with a TTL so repeated runs are fast and
  gentle on EDGAR, and so the app can show last-known data if EDGAR is down.
* Never raises on upstream failure from the high-level helpers: they return
  ``None`` and record the specific error in ``last_errors`` so the UI can show it.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{unit}/{frame}.json"
SEC_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/{doc}"


class TokenBucket:
    """Classic token bucket. ``rate`` tokens per second, burst up to ``capacity``.

    Thread-safe. ``acquire`` blocks until a token is available and returns the
    number of seconds it waited (useful for tests and the UI's rate display).
    """

    def __init__(self, rate: float = 8.0, capacity: int = 8, clock=time.monotonic, sleeper=time.sleep):
        self.rate = float(rate)
        self.capacity = int(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._sleep = sleeper
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def try_acquire(self) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def acquire(self) -> float:
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                need = (1.0 - self._tokens) / self.rate
            self._sleep(need)
            waited += need


class DiskCache:
    """Very small content cache: key → (bytes, stored_at). Stored gzip'd."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / h[:2] / (h + ".gz")

    def get(self, key: str, max_age: Optional[float]) -> Optional[bytes]:
        p = self._path(key)
        if not p.exists():
            return None
        age = time.time() - p.stat().st_mtime
        if max_age is not None and age > max_age:
            return None
        try:
            with gzip.open(p, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def get_stale(self, key: str) -> Optional[tuple[bytes, float]]:
        """Return (data, age_seconds) regardless of TTL — for 'last known' fallback."""
        p = self._path(key)
        if not p.exists():
            return None
        try:
            with gzip.open(p, "rb") as fh:
                return fh.read(), time.time() - p.stat().st_mtime
        except OSError:
            return None

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        with gzip.open(tmp, "wb") as fh:
            fh.write(data)
        tmp.replace(p)


@dataclass
class FetchResult:
    ok: bool
    data: Optional[bytes] = None
    status: Optional[int] = None
    error: Optional[str] = None
    from_cache: bool = False
    stale: bool = False
    url: str = ""


@dataclass
class SecClient:
    user_agent: str = field(default_factory=config.sec_user_agent)
    rate: float = 8.0
    timeout: float = 30.0
    cache: Optional[DiskCache] = None
    bucket: TokenBucket = field(default_factory=lambda: TokenBucket(8.0, 8))
    offline: bool = False  # tests: never touch the network
    last_errors: list[str] = field(default_factory=list)
    request_count: int = 0

    def __post_init__(self):
        if self.cache is None:
            self.cache = DiskCache(config.cache_dir())
        if self.bucket.rate != self.rate:
            self.bucket = TokenBucket(self.rate, int(self.rate))

    # ------------------------------------------------------------------ core
    def fetch(self, url: str, *, ttl: Optional[float] = 86400.0, headers: Optional[dict] = None,
              use_cache: bool = True) -> FetchResult:
        assert self.cache is not None
        if use_cache and ttl is not None:
            cached = self.cache.get(url, ttl)
            if cached is not None:
                return FetchResult(ok=True, data=cached, status=200, from_cache=True, url=url)
        if self.offline:
            stale = self.cache.get_stale(url)
            if stale:
                return FetchResult(ok=True, data=stale[0], status=200, from_cache=True, stale=True, url=url)
            return FetchResult(ok=False, error="offline mode: no cached copy", url=url)

        host = urllib.parse.urlparse(url).netloc
        hdrs = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "gzip",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        }
        if host.endswith("sec.gov"):
            hdrs["Host"] = host
        if headers:
            hdrs.update(headers)
        self.bucket.acquire()
        self.request_count += 1
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "") == "gzip":
                    try:
                        raw = gzip.decompress(raw)
                    except OSError:
                        pass
                status = resp.status
        except urllib.error.HTTPError as e:
            msg = f"HTTP {e.code} from {host} for {url}"
            self.last_errors.append(msg)
            stale = self.cache.get_stale(url)
            if stale:
                return FetchResult(ok=True, data=stale[0], status=e.code, error=msg, from_cache=True, stale=True, url=url)
            return FetchResult(ok=False, status=e.code, error=msg, url=url)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            msg = f"network error for {url}: {e}"
            self.last_errors.append(msg)
            stale = self.cache.get_stale(url)
            if stale:
                return FetchResult(ok=True, data=stale[0], error=msg, from_cache=True, stale=True, url=url)
            return FetchResult(ok=False, error=msg, url=url)
        if use_cache:
            self.cache.put(url, raw)
        return FetchResult(ok=True, data=raw, status=status, url=url)

    def fetch_json(self, url: str, *, ttl: Optional[float] = 86400.0, headers: Optional[dict] = None) -> Optional[Any]:
        r = self.fetch(url, ttl=ttl, headers=headers)
        if not r.ok or r.data is None:
            return None
        try:
            return json.loads(r.data.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            self.last_errors.append(f"bad JSON from {url}: {e}")
            return None

    def fetch_text(self, url: str, *, ttl: Optional[float] = 7 * 86400.0) -> Optional[str]:
        r = self.fetch(url, ttl=ttl)
        if not r.ok or r.data is None:
            return None
        return r.data.decode("utf-8", errors="replace")

    # --------------------------------------------------------------- helpers
    def ticker_map(self) -> dict[str, dict]:
        """ticker → {cik, name}. Cached for 7 days."""
        data = self.fetch_json(SEC_TICKERS_URL, ttl=7 * 86400.0)
        out: dict[str, dict] = {}
        if not isinstance(data, dict):
            return out
        for row in data.values():
            try:
                out[str(row["ticker"]).upper()] = {"cik": int(row["cik_str"]), "name": row["title"], "ticker": str(row["ticker"]).upper()}
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def resolve_ticker(self, query: str) -> Optional[dict]:
        q = query.strip().upper()
        if not q:
            return None
        m = self.ticker_map()
        if q in m:
            return m[q]
        # Allow "BRK.B" / "BRK-B" variants
        alt = q.replace(".", "-")
        if alt in m:
            return m[alt]
        if q.isdigit():
            cik = int(q)
            for row in m.values():
                if row["cik"] == cik:
                    return row
            return {"cik": cik, "name": f"CIK {cik}", "ticker": q}
        return None

    def search_companies(self, query: str, limit: int = 10) -> list[dict]:
        q = query.strip().upper()
        if not q:
            return []
        hits = []
        for t, row in self.ticker_map().items():
            if t.startswith(q) or q in row["name"].upper():
                hits.append(row)
                if len(hits) >= limit * 3:
                    break
        hits.sort(key=lambda r: (not r["ticker"].startswith(q), len(r["ticker"])))
        return hits[:limit]

    def submissions(self, cik: int) -> Optional[dict]:
        return self.fetch_json(SEC_SUBMISSIONS_URL.format(cik=cik), ttl=12 * 3600.0)

    def company_facts(self, cik: int) -> Optional[dict]:
        return self.fetch_json(SEC_FACTS_URL.format(cik=cik), ttl=12 * 3600.0)

    def frame(self, concept: str, frame: str, unit: str = "USD", taxonomy: str = "us-gaap") -> Optional[dict]:
        return self.fetch_json(SEC_FRAMES_URL.format(taxonomy=taxonomy, concept=concept, unit=unit, frame=frame), ttl=7 * 86400.0)

    def efts_search(self, q: str, *, forms: Optional[list[str]] = None, ciks: Optional[list[str]] = None,
                    sics: Optional[list[str]] = None, startdt: Optional[str] = None, enddt: Optional[str] = None,
                    page: int = 1) -> Optional[dict]:
        params: dict[str, str] = {"q": q}
        if forms:
            params["forms"] = ",".join(forms)
        if ciks:
            params["ciks"] = ",".join(ciks)
        if sics:
            params["sics"] = ",".join(sics)
        if startdt or enddt:
            params["dateRange"] = "custom"
            if startdt:
                params["startdt"] = startdt
            if enddt:
                params["enddt"] = enddt
        if page > 1:
            params["page"] = str(page)
            params["from"] = str((page - 1) * 100)
        url = SEC_EFTS_URL + "?" + urllib.parse.urlencode(params)
        return self.fetch_json(url, ttl=24 * 3600.0)

    @staticmethod
    def archive_url(cik: int, accession: str, doc: str) -> str:
        return SEC_ARCHIVES_URL.format(cik=int(cik), accn_nodash=accession.replace("-", ""), doc=doc)

    @staticmethod
    def filing_index_url(cik: int, accession: str) -> str:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"

    def filing_document(self, cik: int, accession: str, doc: str) -> Optional[str]:
        return self.fetch_text(self.archive_url(cik, accession, doc), ttl=30 * 86400.0)


_default_client: Optional[SecClient] = None
_client_lock = threading.Lock()


def get_client() -> SecClient:
    global _default_client
    with _client_lock:
        if _default_client is None:
            _default_client = SecClient()
        return _default_client
