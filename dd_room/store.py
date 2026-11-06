"""SQLite persistence for memo history and user settings."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, ticker TEXT, cik INTEGER, name TEXT, depth TEXT, provider TEXT, status TEXT,
  started REAL, finished REAL, error TEXT, memo_md TEXT, memo_html TEXT, blackboard TEXT, events TEXT, stats TEXT
);
CREATE INDEX IF NOT EXISTS runs_ticker ON runs(ticker, started);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    # ---------------------------------------------------------------- runs
    def save_run(self, run) -> None:
        bb = run.bb
        snap = bb.snapshot()
        meta = dict(snap["meta"])
        memo_md = meta.pop("memo_md", "")
        memo_html = meta.pop("memo_html", "")
        snap["meta"] = meta
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO runs (id,ticker,cik,name,depth,provider,status,started,finished,error,memo_md,memo_html,blackboard,events,stats) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run.id, run.ticker, run.cik, run.name, run.depth, run.provider, run.status, run.started, run.finished, run.error,
                 memo_md, memo_html, json.dumps(snap, default=str), json.dumps(run.bus.history(), default=str)[:4_000_000],
                 json.dumps(meta.get("stats") or {})),
            )

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT id,ticker,cik,name,depth,provider,status,started,finished,error,stats FROM runs ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["stats"] = json.loads(d["stats"] or "{}")
            except json.JSONDecodeError:
                d["stats"] = {}
            out.append(d)
        return out

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for k in ("blackboard", "events", "stats"):
            try:
                d[k] = json.loads(d[k] or "null")
            except json.JSONDecodeError:
                d[k] = None
        return d

    def latest_run(self, ticker: Optional[str] = None) -> Optional[dict]:
        with self._conn() as c:
            if ticker:
                r = c.execute("SELECT id FROM runs WHERE ticker=? AND status='done' ORDER BY started DESC LIMIT 1", (ticker.upper(),)).fetchone()
            else:
                r = c.execute("SELECT id FROM runs WHERE status='done' ORDER BY started DESC LIMIT 1").fetchone()
        return self.get_run(r["id"]) if r else None

    def delete_run(self, run_id: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM runs WHERE id=?", (run_id,))
            return cur.rowcount > 0

    # ------------------------------------------------------------ settings
    def get_settings(self) -> dict[str, Any]:
        with self._conn() as c:
            rows = c.execute("SELECT key,value FROM settings").fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except json.JSONDecodeError:
                out[r["key"]] = r["value"]
        return out

    def set_settings(self, values: dict[str, Any]) -> None:
        with self._lock, self._conn() as c:
            for k, v in values.items():
                c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (k, json.dumps(v)))

    def housekeeping(self, keep: int = 200) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM runs WHERE id NOT IN (SELECT id FROM runs ORDER BY started DESC LIMIT ?)", (keep,))
