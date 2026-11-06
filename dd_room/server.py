"""FastAPI server for Due-Diligence Room. Binds to 127.0.0.1 by default."""
from __future__ import annotations

import asyncio
import json
import queue
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, memo
from .agents import DEPTHS, Orchestrator, agent_catalog, token_budget_estimate
from .pdf import markdown_to_pdf
from .sec import get_client
from .store import Store

STATIC = Path(__file__).parent / "static"
DOCS = config.PROJECT_ROOT / "docs"
VERSION = "0.1.0"


class SettingsIn(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    auto_approve: Optional[bool] = None


class RunIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    depth: str = "standard"
    provider: Optional[str] = None
    model: Optional[str] = None
    auto_approve: Optional[bool] = None


class Decision(BaseModel):
    approved: bool = True
    note: str = ""


def create_app(store: Optional[Store] = None, orchestrator: Optional[Orchestrator] = None) -> FastAPI:
    app = FastAPI(title="Due-Diligence Room", version=VERSION, docs_url="/api/docs", redoc_url=None)
    st = store or Store(config.db_path())
    orch = orchestrator or Orchestrator(client=get_client(), on_finish=lambda run: st.save_run(run))
    if orchestrator is not None and orchestrator.on_finish is None:
        orchestrator.on_finish = lambda run: st.save_run(run)
    app.state.store = st
    app.state.orch = orch
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    # ---------------------------------------------------------------- pages
    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/methodology", response_class=HTMLResponse)
    def methodology():
        md_text = (DOCS / "METHODOLOGY.md").read_text(encoding="utf-8") if (DOCS / "METHODOLOGY.md").exists() else "# Methodology\n\n_missing_"
        return memo.render_html(md_text, title="Methodology", standalone=True)

    # ------------------------------------------------------------------ api
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": VERSION, "time": time.time(), "providers": config.available_providers(),
                "sec_user_agent_set": config.sec_user_agent() != config.DEFAULT_SEC_USER_AGENT, "active_runs": len(orch.active())}

    @app.get("/api/config")
    def get_config():
        s = st.get_settings()
        return {"providers": config.available_providers(), "default_provider": s.get("provider", config.llm_provider()),
                "model": s.get("model", config.llm_model()), "auto_approve": s.get("auto_approve"),
                "sec_user_agent": config.sec_user_agent(), "sec_user_agent_is_default": config.sec_user_agent() == config.DEFAULT_SEC_USER_AGENT,
                "depths": DEPTHS, "agents": agent_catalog(), "data_dir": str(config.data_dir().name), "settings": s}

    @app.post("/api/settings")
    def set_settings(body: SettingsIn):
        vals = {k: v for k, v in body.model_dump().items() if v is not None}
        if "provider" in vals and vals["provider"] not in ("offline", "gemini", "anthropic", "openai", "ollama"):
            raise HTTPException(400, "unknown provider")
        st.set_settings(vals)
        return {"ok": True, "settings": st.get_settings()}

    @app.get("/api/search")
    def search(q: str = ""):
        return {"results": get_client().search_companies(q, limit=12)}

    @app.get("/api/estimate")
    def estimate(depth: str = "standard", provider: str = "offline"):
        return token_budget_estimate(depth, provider)

    @app.post("/api/runs")
    def start_run(body: RunIn):
        s = st.get_settings()
        provider = (body.provider or s.get("provider") or config.llm_provider()).lower()
        model = body.model or s.get("model") or (config.llm_model() or None)
        auto = body.auto_approve if body.auto_approve is not None else s.get("auto_approve")
        if len(orch.active()) >= 2:
            raise HTTPException(429, "Two runs are already in progress; wait for one to finish.")
        try:
            run = orch.start(body.ticker, body.depth, provider, model=model, auto_approve=auto)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return run.summary()

    @app.get("/api/runs")
    def list_runs():
        live = {r.id: r.summary() for r in orch.runs.values()}
        hist = st.list_runs()
        for h in hist:
            h.setdefault("live", False)
        return {"active": list(live.values()), "history": hist}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        run = orch.get(run_id)
        if run:
            snap = run.bb.snapshot()
            meta = dict(snap["meta"])
            meta.pop("memo_md", None); meta.pop("memo_html", None)
            snap["meta"] = meta
            return {**run.summary(), "blackboard": snap, "live": True}
        row = st.get_run(run_id)
        if not row:
            raise HTTPException(404, "run not found")
        return {"id": row["id"], "ticker": row["ticker"], "cik": row["cik"], "name": row["name"], "depth": row["depth"], "provider": row["provider"],
                "status": row["status"], "started": row["started"], "finished": row["finished"], "error": row["error"], "stats": row["stats"],
                "blackboard": row["blackboard"], "live": False}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, since: int = 0):
        run = orch.get(run_id)
        if not run:
            row = st.get_run(run_id)
            if not row:
                raise HTTPException(404, "run not found")

            async def replay():
                for ev in (row["events"] or []):
                    if ev["seq"] > since:
                        yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                yield "event: run.closed\ndata: {}\n\n"
            return StreamingResponse(replay(), media_type="text/event-stream")
        q = run.bus.subscribe(since=since)

        async def gen():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = q.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.15)
                        if run.bus.closed and q.empty():
                            break
                        continue
                    yield ev.to_sse()
                    if ev.type == "run.closed":
                        break
            finally:
                run.bus.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/events.json")
    def events_json(run_id: str, since: int = 0):
        run = orch.get(run_id)
        if run:
            return {"events": run.bus.history(since)}
        row = st.get_run(run_id)
        if not row:
            raise HTTPException(404, "run not found")
        return {"events": [e for e in (row["events"] or []) if e["seq"] > since]}

    @app.post("/api/runs/{run_id}/approve")
    def approve(run_id: str, body: Decision):
        run = orch.get(run_id)
        if not run:
            raise HTTPException(404, "run not found or no longer live")
        if not run.gate.is_pending:
            raise HTTPException(409, "no approval pending for this run")
        run.gate.decide(body.approved, by="human", note=body.note)
        return {"ok": True, "approved": body.approved}

    def _memo_md(run_id: str) -> tuple[str, dict]:
        run = orch.get(run_id)
        if run and run.bb.meta.get("memo_md"):
            return run.bb.meta["memo_md"], {"ticker": run.ticker, "name": run.name}
        row = st.get_run(run_id)
        if not row or not row.get("memo_md"):
            raise HTTPException(404, "memo not available for this run")
        return row["memo_md"], {"ticker": row["ticker"], "name": row["name"]}

    @app.get("/api/runs/{run_id}/memo.md", response_class=PlainTextResponse)
    def memo_md(run_id: str):
        md_text, meta = _memo_md(run_id)
        return PlainTextResponse(md_text, headers={"Content-Disposition": f'attachment; filename="{meta["ticker"]}_memo.md"'})

    @app.get("/api/runs/{run_id}/memo.html")
    def memo_html(run_id: str, standalone: int = 0):
        md_text, meta = _memo_md(run_id)
        html = memo.render_html(md_text, title=f"{meta['name']} memo", standalone=bool(standalone))
        return HTMLResponse(html)

    @app.get("/api/runs/{run_id}/memo.pdf")
    def memo_pdf(run_id: str):
        md_text, meta = _memo_md(run_id)
        data = markdown_to_pdf(md_text)
        return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{meta["ticker"]}_memo.pdf"'})

    @app.get("/api/runs/{run_id}/audit")
    def memo_audit(run_id: str):
        md_text, _ = _memo_md(run_id)
        return {"unsourced": memo.audit_numbers(md_text), "stats": memo.memo_stats(md_text)}

    @app.delete("/api/runs/{run_id}")
    def delete_run(run_id: str):
        if orch.get(run_id) and orch.get(run_id).status in ("running", "awaiting_approval"):
            raise HTTPException(409, "run is still in progress")
        orch.runs.pop(run_id, None)
        return {"ok": st.delete_run(run_id)}

    @app.get("/api/latest")
    def latest(ticker: Optional[str] = None):
        row = st.latest_run(ticker)
        if not row:
            return JSONResponse({"run": None})
        return {"run": {"id": row["id"], "ticker": row["ticker"], "name": row["name"], "status": row["status"], "started": row["started"], "stats": row["stats"]}}

    return app


app = create_app()
