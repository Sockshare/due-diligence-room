# Build plan — Due-Diligence Room

Goal: type a ticker → five agents (Analyst, Forensic Accountant, Legal, Skeptic,
Editor) work a shared blackboard over SEC EDGAR data and produce a sourced
investment memo with a mandatory bear case and per-section confidence.

## Order of work

1. **Data layer** (`dd_room/sec.py`, `xbrl.py`, `htmlitems.py`, `prices.py`)
   - SEC client: configurable User-Agent, token-bucket rate limiter (10 req/s,
     we default to 8), on-disk JSON cache with TTL, tolerant error handling.
   - XBRL fact selection: annual series per concept, unit selection,
     duplicate-frame dedupe, "latest filed wins" for restated values, with the
     chosen accession recorded per data point.
   - 10-K item extraction (Item 1, 1A, 3, 7) from inline-XBRL HTML using a
     largest-span heuristic that skips the table of contents.
   - Yahoo chart endpoint for price history (optional, degrades silently).
2. **Analytics** (`financials.py`, `forensics.py`, `peers.py`)
   - Trends, margins, FCF, ROIC, net debt, valuation multiples.
   - Beneish M-Score, Sloan accruals, Altman Z, DSO/DIO, CFO/NI, dilution, SBC.
   - Peers via EFTS `sics` filter + XBRL frames.
3. **agentkit** (`agentkit/`): Blackboard (typed sections + sources registry),
   Agent base with tools + step log, EventBus (SSE), ApprovalGate, LLM client
   (gemini / anthropic / openai / ollama / offline).
4. **Agents** (`dd_room/agents/`): the five specialists + orchestrator.
5. **Editor / memo** (`memo.py`, `pdf.py`): Markdown assembly, source
   enforcement, confidence computation, HTML render, pure-Python PDF.
6. **Server + UI** (`server.py`, `static/`): FastAPI, SSE, SQLite history,
   vanilla JS single page app with vendored Chart.js.
7. **Tests, docs, gates** G1–G9, QUALITY.md, single commit.

## Depth levels

| depth    | years | 10-K docs parsed | Form 4 parsed | peers | 13F holders |
|----------|-------|------------------|---------------|-------|-------------|
| quick    | 3     | current only     | 0             | no    | no          |
| standard | 5     | current + prior  | 20            | yes   | yes         |
| deep     | 10    | current + prior  | 40            | yes   | yes         |
