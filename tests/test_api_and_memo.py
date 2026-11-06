"""API smoke tests, human approval gate test, and offline memo source-audit
verification for AAPL and SCKT."""
from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from dd_room import memo
from dd_room.agents import Orchestrator
from dd_room.server import create_app
from dd_room.store import Store


def test_offline_memo_zero_unsourced_numbers_aapl_and_sckt(tmp_path: Path):
    """Run AAPL and SCKT in offline mode and verify zero missing-source numbers."""
    store = Store(tmp_path / "test.db")
    orch = Orchestrator(on_finish=lambda r: store.save_run(r))

    for ticker in ("AAPL", "SCKT"):
        run = orch.start(ticker, depth="standard", provider="offline", auto_approve=True, blocking=True)
        assert run.status == "done", f"{ticker} failed: {run.error}"
        md_text = run.bb.meta.get("memo_md", "")
        assert len(md_text) > 5000
        unsourced = memo.audit_numbers(md_text)
        assert unsourced == [], f"{ticker} had unsourced figures: {unsourced[:10]}"
        stats = memo.memo_stats(md_text)
        assert stats["citations"] > 40
        assert len(run.bb.sources) > 15


def test_api_smoke_and_approval_gate(tmp_path: Path):
    store = Store(tmp_path / "api_test.db")
    orch = Orchestrator(on_finish=lambda r: store.save_run(r))
    app = create_app(store=store, orchestrator=orch)
    client = TestClient(app)

    # Pages
    r = client.get("/")
    assert r.status_code == 200
    assert "Due-Diligence Room" in r.text

    r = client.get("/methodology")
    assert r.status_code == 200
    assert "Beneish" in r.text

    # Health & config
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get("/api/config")
    assert r.status_code == 200
    assert len(r.json()["agents"]) == 5

    # Settings
    r = client.post("/api/settings", json={"provider": "offline", "model": "", "auto_approve": True})
    assert r.status_code == 200
    assert r.json()["settings"]["provider"] == "offline"

    # Estimate & search
    r = client.get("/api/estimate?depth=standard&provider=gemini")
    assert r.status_code == 200
    assert r.json()["calls"] == 4

    r = client.get("/api/search?q=AAPL")
    assert r.status_code == 200
    assert any(x["ticker"] == "AAPL" for x in r.json()["results"])

    # Start a gated run (auto_approve=False) -> waits at approval gate before Editor
    r = client.post("/api/runs", json={"ticker": "AAPL", "depth": "quick", "provider": "offline", "auto_approve": False})
    assert r.status_code == 200
    run_id = r.json()["id"]

    # Wait until it reaches awaiting_approval
    deadline = time.time() + 30.0
    status = ""
    while time.time() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        status = detail["status"]
        if status in ("awaiting_approval", "done", "failed"):
            break
        time.sleep(0.1)
    assert status == "awaiting_approval"

    # Approve via API
    r = client.post(f"/api/runs/{run_id}/approve", json={"approved": True, "note": "Looks solid"})
    assert r.status_code == 200
    assert r.json()["approved"] is True

    # Wait for Editor to finish
    deadline = time.time() + 15.0
    while time.time() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        status = detail["status"]
        if status in ("done", "failed"):
            break
        time.sleep(0.1)
    assert status == "done"

    # Verify memo.md, memo.html, memo.pdf, audit, events.json, latest
    assert client.get(f"/api/runs/{run_id}/memo.md").status_code == 200
    assert client.get(f"/api/runs/{run_id}/memo.html").status_code == 200
    pdf_resp = client.get(f"/api/runs/{run_id}/memo.pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.content.startswith(b"%PDF-1.4")

    aud_resp = client.get(f"/api/runs/{run_id}/audit")
    assert aud_resp.status_code == 200
    assert aud_resp.json()["unsourced"] == []

    ev_resp = client.get(f"/api/runs/{run_id}/events.json")
    assert ev_resp.status_code == 200
    assert len(ev_resp.json()["events"]) > 10

    lat_resp = client.get("/api/latest?ticker=AAPL")
    assert lat_resp.status_code == 200
    assert lat_resp.json()["run"]["id"] == run_id
