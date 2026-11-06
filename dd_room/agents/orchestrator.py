"""Run orchestrator: wires the five agents, the blackboard, the event stream and the approval gate."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from agentkit import ApprovalGate, Blackboard, EventBus, LLMClient, estimate_tokens

from ..sec import SecClient, get_client
from .analyst import Analyst
from .base import DEPTHS, SECTION_ORDER, RunContext
from .editor import Editor
from .forensic import ForensicAccountant
from .legal import Legal
from .skeptic import Skeptic

AGENT_ORDER = [Analyst, ForensicAccountant, Legal, Skeptic, Editor]


def agent_catalog() -> list[dict]:
    return [{"name": a.name, "role": a.role, "color": a.color} for a in AGENT_ORDER]


def token_budget_estimate(depth: str, provider: str) -> dict:
    """Rough token budget for keyed mode, by depth. Offline mode uses zero."""
    if provider == "offline":
        return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "note": "offline mode: no LLM calls"}
    base_calls = {"quick": 3, "standard": 4, "deep": 4}.get(depth, 4)
    tokens_in = {"quick": 4500, "standard": 7000, "deep": 9000}.get(depth, 7000)
    tokens_out = 1600
    return {"calls": base_calls, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "note": "estimate: ~4 chars/token; the LLM only drafts prose (business overview, risk-change summary, bear-case narrative, executive summary); all numbers are computed locally"}


@dataclass
class Run:
    id: str
    ticker: str
    cik: int
    name: str
    depth: str
    provider: str
    bus: EventBus
    bb: Blackboard
    gate: ApprovalGate
    status: str = "queued"
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    error: Optional[str] = None
    agents: dict = field(default_factory=dict)
    thread: Optional[threading.Thread] = None

    def summary(self) -> dict:
        return {"id": self.id, "ticker": self.ticker, "cik": self.cik, "name": self.name, "depth": self.depth, "provider": self.provider,
                "status": self.status, "started": self.started, "finished": self.finished, "error": self.error,
                "approval_pending": self.gate.is_pending, "agents": self.agents, "stats": self.bb.meta.get("stats"),
                "unsourced": len(self.bb.meta.get("unsourced", []))}


class Orchestrator:
    def __init__(self, client: Optional[SecClient] = None, on_finish: Optional[Callable[[Run], None]] = None):
        self.client = client or get_client()
        self.runs: dict[str, Run] = {}
        self.on_finish = on_finish
        self._lock = threading.Lock()

    def start(self, ticker: str, depth: str = "standard", provider: str = "offline", model: Optional[str] = None,
              auto_approve: Optional[bool] = None, today: Optional[date] = None, blocking: bool = False) -> Run:
        info = self.client.resolve_ticker(ticker)
        if not info:
            raise ValueError(f"Unknown ticker '{ticker}'. Try the exact symbol as listed on EDGAR (e.g. BRK-B) or a CIK number.")
        depth = depth if depth in DEPTHS else "standard"
        llm = LLMClient(provider=provider, model=model)
        if provider != "offline" and not llm.is_configured():
            raise ValueError(f"Provider '{provider}' is not configured (no key/host in environment). Use offline mode or set the key.")
        if auto_approve is None:
            auto_approve = provider == "offline"
        bus = EventBus()
        run_id = uuid.uuid4().hex[:12]
        bb = Blackboard(meta={"ticker": info["ticker"], "cik": info["cik"], "name": info["name"], "depth": depth, "mode": provider, "run_id": run_id},
                        on_change=lambda what, payload: bus.emit("blackboard", what, data=payload))
        for i, (key, title, owner) in enumerate(SECTION_ORDER):
            bb.define_section(key, title, owner, i)
        gate = ApprovalGate(auto_approve=auto_approve)
        run = Run(id=run_id, ticker=info["ticker"], cik=info["cik"], name=info["name"], depth=depth, provider=provider, bus=bus, bb=bb, gate=gate)
        run.agents = {a.name: "idle" for a in AGENT_ORDER}
        with self._lock:
            self.runs[run_id] = run
        rc = RunContext(ticker=info["ticker"], cik=info["cik"], name=info["name"], depth=depth, client=self.client, bus=bus, bb=bb, llm=llm, gate=gate, today=today or date.today())
        if blocking:
            self._execute(run, rc)
        else:
            run.thread = threading.Thread(target=self._execute, args=(run, rc), daemon=True, name=f"run-{run_id}")
            run.thread.start()
        return run

    def _execute(self, run: Run, rc: RunContext) -> None:
        bus, bb = run.bus, run.bb
        run.status = "running"
        bus.emit("run.status", f"run {run.id} started for {run.ticker} ({run.depth}, {run.provider})", data=run.summary())
        try:
            for cls in AGENT_ORDER:
                if cls is Editor:
                    payload = {"ticker": run.ticker, "sections_done": sum(1 for s in bb.sections.values() if s.status == "done"),
                               "claims": len(bb.claims), "counters": len(bb.counters), "gaps": len(bb.gaps),
                               "llm_tokens": {"in": rc.llm.total_in, "out": rc.llm.total_out, "calls": rc.llm.calls}}
                    run.status = "awaiting_approval" if not run.gate.auto_approve else run.status
                    bus.emit("approval", "Editor is ready to finalise the memo — approve to continue" if not run.gate.auto_approve else "approval gate auto-approved", data={**payload, "auto": run.gate.auto_approve})
                    decision = run.gate.request(payload)
                    bus.emit("approval.decision", f"{'approved' if decision.approved else 'rejected'} by {decision.by}" + (f": {decision.note}" if decision.note else ""),
                             data={"approved": decision.approved, "by": decision.by, "note": decision.note, "timed_out": decision.timed_out})
                    if not decision.approved:
                        run.status = "rejected"
                        run.error = f"finalisation rejected by {decision.by}"
                        bus.emit("run.status", "run stopped before the Editor (rejected)", data=run.summary())
                        return
                    run.status = "running"
                agent = cls(rc)
                run.agents[agent.name] = "working"
                ok = agent.execute()
                run.agents[agent.name] = "done" if ok else "failed"
                if not ok and cls is Editor:
                    raise RuntimeError("Editor failed; memo not assembled")
            run.status = "done"
            run.finished = time.time()
            bus.emit("run.status", "run complete", data=run.summary())
        except Exception as e:  # noqa: BLE001
            run.status = "failed"
            run.error = f"{type(e).__name__}: {e}"
            run.finished = time.time()
            bus.emit("error", run.error, data={"trace": traceback.format_exc()[-2000:]})
            bus.emit("run.status", "run failed", data=run.summary())
        finally:
            if self.on_finish:
                try:
                    self.on_finish(run)
                except Exception as e:  # noqa: BLE001
                    bus.emit("error", f"persist failed: {e}")
            bus.close()

    def get(self, run_id: str) -> Optional[Run]:
        return self.runs.get(run_id)

    def active(self) -> list[Run]:
        return [r for r in self.runs.values() if r.status in ("queued", "running", "awaiting_approval")]
