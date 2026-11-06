# Quality Gate Report — Due-Diligence Room

All nine quality gates (G1–G9) were executed and verified prior to final commit.

---

## Gate Summary Table

| Gate | Description | Status | Evidence / Result |
|---|---|---|---|
| **G1** | Unit tests for core logic (`forensics`, `xbrl`, `htmlitems`, `sec`, `filings`) | **PASS** | `pytest` — 17/17 tests passing (`tests/test_forensics.py`, `tests/test_xbrl.py`, `tests/test_htmlitems.py`, `tests/test_sec_and_filings.py`, `tests/test_api_and_memo.py`) |
| **G2** | API smoke tests & approval gate workflow | **PASS** | `tests/test_api_and_memo.py::test_api_smoke_and_approval_gate` + `scripts/selfcheck.py` verify `/`, `/methodology`, `/api/health`, `/api/config`, `/api/settings`, `/api/estimate`, `/api/search`, `/api/runs`, `/api/runs/{id}/approve`, `.md`, `.html`, `.pdf`, `/audit`, and `/events.json` |
| **G3** | Fresh-install test (`rm -rf .venv && ./run.sh`) | **PASS** | Deleted `.venv`, launched `./run.sh --port 8112`, verified `/api/health` returned `{"ok": true, "version": "0.1.0"}` |
| **G4** | Zero JS console / page errors across all screens | **PASS** | Verified with headless Chromium via Playwright across Memo, Live Room, Financials, Forensics, Risk & Legal, Sources, History, Settings, and `/methodology`: `Console errors: 0`, `Page errors: 0` |
| **G5** | Real screenshots of main screens with real EDGAR data | **PASS** | 6 high-DPI (`1440x900 @ 1.5x`) screenshots in `docs/screenshots/` (`live_room.png`, `memo.png`, `forensics.png`, `financials.png`, `risk_legal.png`, `sources.png`) captured from real `AAPL` and `SCKT` runs |
| **G6** | Zero-key offline mode works & produces zero unsourced figures | **PASS** | `test_offline_memo_zero_unsourced_numbers_aapl_and_sckt` verified `0` unsourced financial figures across full 15-section memos for both `AAPL` (349 citations, 250 sources) and `SCKT` |
| **G7** | Secret & internal-path scan of the repository tree | **PASS** | Clean scan across all tracked files: zero API keys (`AIza`, `sk-`, `ghp_`, `github_pat_`, `AQ.`, `xox`, `AKIA`), zero absolute home paths, zero internal references |
| **G8** | Independent / adversarial code review (P0/P1 hunt & fixes) | **PASS** | 1 P0 and 2 P1 issues identified and fixed (logged below) |
| **G9** | Documentation completeness (`README.md`, `METHODOLOGY.md`, `ARCHITECTURE.md`, `USER_GUIDE.md`) | **PASS** | Complete documentation with 3 lead screenshots, 3-command quickstart, configuration table, formula citations, limitations, and non-advice disclaimer |

---

## G8 Adversarial Review Log

1. **[P0 — Fixed] FastAPI + `from __future__ import annotations` on inner Pydantic models (`dd_room/server.py`)**
   - *Finding:* `SettingsIn`, `RunIn`, and `Decision` were originally defined inside the local scope of `create_app()` while `from __future__ import annotations` was active at the top of `dd_room/server.py`. Under Pydantic v2 + FastAPI, stringified forward annotations for function-local classes cannot be resolved from module globals, causing `POST /api/settings`, `POST /api/runs`, and `POST /api/runs/{id}/approve` to fail with `422 Unprocessable Entity`.
   - *Fix:* Moved `SettingsIn`, `RunIn`, and `Decision` to module level in `dd_room/server.py` and verified all three endpoints in `tests/test_api_and_memo.py`.

2. **[P1 — Fixed] Named Server-Sent Events (`event: <type>`) handling in browser `EventSource` (`dd_room/static/app.js`)**
   - *Finding:* `EventBus.to_sse()` emits named SSE frames (`event: agent.step`, `event: blackboard`, `event: approval`, etc.). Standard `EventSource.onmessage` only triggers on unnamed `message` events.
   - *Fix:* Registered explicit `es.addEventListener(type, ...)` handlers in `dd_room/static/app.js` for all emitted event types (`run.status`, `agent.status`, `agent.step`, `blackboard`, `approval`, `approval.decision`, `error`, `memo`, `run.closed`).

3. **[P1 — Fixed] Horizontal overflow and scroll retention across tab transitions (`dd_room/static/app.css`, `dd_room/static/app.js`)**
   - *Finding:* Long lines inside `.memo-paper` could cause horizontal clipping in `.memo-layout`, and `.main-area` retained its vertical scroll offset when switching between left-nav views.
   - *Fix:* Added `min-width: 0; overflow-wrap: break-word;` to `.main-area`, `.view-section`, and `.memo-paper`, and reset `.main-area.scrollTop = 0` inside `switchView()`.
