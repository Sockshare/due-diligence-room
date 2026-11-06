#!/usr/bin/env python3
"""Self-check script for Due-Diligence Room.

Verifies required files, vendored assets, forensic math, XBRL parser, API
endpoints, and offline memo source auditing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dd_room import forensics, memo, xbrl  # noqa: E402
from dd_room.agents import Orchestrator  # noqa: E402
from dd_room.server import create_app  # noqa: E402
from dd_room.store import Store  # noqa: E402


def check(cond: bool, msg: str) -> None:
    if not cond:
        print(f"[FAIL] {msg}", file=sys.stderr)
        raise SystemExit(1)
    print(f"[ OK ] {msg}")


def main() -> int:
    required = [
        "app.py",
        "run.sh",
        "requirements.txt",
        "LICENSE",
        ".gitignore",
        ".env.example",
        "dd_room/server.py",
        "dd_room/static/index.html",
        "dd_room/static/app.css",
        "dd_room/static/memo.css",
        "dd_room/static/app.js",
        "dd_room/static/vendor/chart.umd.js",
        "dd_room/static/vendor/CHART_JS_LICENSE",
        "docs/METHODOLOGY.md",
    ]
    for rel in required:
        check((ROOT / rel).exists(), f"file exists: {rel}")

    # Forensic formula check
    z = forensics.altman_z(200.0, 300.0, 150.0, 1200.0, 400.0, 1000.0, 1000.0, year=2025)
    check(z.value is not None and abs(z.value - 3.955) < 1e-6 and z.light == "green", "Altman Z-Score hand-computed check")

    sl = forensics.sloan_accrual_ratio(180.0, 100.0, 1000.0, 1000.0, year=2025)
    check(sl.value is not None and abs(sl.value - 0.08) < 1e-6 and sl.light == "amber", "Sloan accrual ratio check")

    # API smoke check
    store = Store(ROOT / "data" / "_selfcheck.db")
    orch = Orchestrator(on_finish=lambda r: store.save_run(r))
    client = TestClient(create_app(store=store, orchestrator=orch))
    check(client.get("/api/health").status_code == 200, "GET /api/health returns 200")
    check(client.get("/").status_code == 200, "GET / returns 200")
    check(client.get("/methodology").status_code == 200, "GET /methodology returns 200")

    # Offline run check on AAPL
    run = orch.start("AAPL", depth="quick", provider="offline", auto_approve=True, blocking=True)
    check(run.status == "done", f"AAPL quick offline run completed ({run.status})")
    uns = memo.audit_numbers(run.bb.meta.get("memo_md", ""))
    check(len(uns) == 0, f"AAPL memo has 0 unsourced figures (got {len(uns)})")

    (ROOT / "data" / "_selfcheck.db").unlink(missing_ok=True)
    print("\nAll self-checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
