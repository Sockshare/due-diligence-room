"""Agent base class + tool registry.

An agent owns some blackboard sections, has a set of callable tools, and
narrates every step through the event bus so a UI can show it working. Tools
are plain Python callables registered with :func:`tool`; each call is logged
with its duration and a short result summary, and exceptions are captured as
failed steps rather than crashing the run.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .blackboard import Blackboard
from .events import EventBus
from .llm import LLMClient


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    description: str = ""


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}

    def register(self, name: str, fn: Callable[..., Any], description: str = "") -> None:
        self._tools[name] = ToolSpec(name=name, fn=fn, description=description)

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict]:
        return [{"name": t.name, "description": t.description} for t in self._tools.values()]


def tool(description: str = ""):
    """Decorator marking an Agent method as a tool (auto-registered in __init__)."""
    def deco(fn):
        fn._tool_description = description
        return fn
    return deco


@dataclass
class StepRecord:
    agent: str
    step: str
    status: str
    started: float
    duration: float = 0.0
    detail: str = ""
    data: dict = field(default_factory=dict)


class Agent:
    name: str = "agent"
    role: str = ""
    color: str = "#888"

    def __init__(self, bb: Blackboard, bus: EventBus, llm: Optional[LLMClient] = None, ctx: Optional[dict] = None):
        self.bb = bb
        self.bus = bus
        self.llm = llm or LLMClient(provider="offline")
        self.ctx = ctx or {}
        self.tools = ToolRegistry()
        self.steps: list[StepRecord] = []
        self.status = "idle"
        for attr in dir(self):
            fn = getattr(self, attr, None)
            if callable(fn) and getattr(fn, "_tool_description", None) is not None:
                self.tools.register(attr, fn, fn._tool_description)

    # ------------------------------------------------------------ logging
    def set_status(self, status: str, message: str = "") -> None:
        self.status = status
        self.bus.emit("agent.status", message or status, agent=self.name, data={"status": status, "role": self.role, "color": self.color})

    def say(self, message: str, **data: Any) -> None:
        self.bus.emit("agent.step", message, agent=self.name, data={"status": "info", **data})

    def step(self, name: str, fn: Callable[[], Any], *, summary: Optional[Callable[[Any], str]] = None, required: bool = False) -> Any:
        """Run one tool/step with timing, logging and error capture."""
        rec = StepRecord(agent=self.name, step=name, status="running", started=time.time())
        self.steps.append(rec)
        self.bus.emit("agent.step", f"▶ {name}", agent=self.name, data={"status": "running", "step": name})
        try:
            result = fn()
            rec.duration = time.time() - rec.started
            rec.status = "done"
            rec.detail = summary(result) if summary else ""
            self.bus.emit("agent.step", f"✓ {name}" + (f" — {rec.detail}" if rec.detail else ""), agent=self.name,
                          data={"status": "done", "step": name, "duration": round(rec.duration, 2)})
            return result
        except Exception as e:  # noqa: BLE001 — the whole point is to capture
            rec.duration = time.time() - rec.started
            rec.status = "failed"
            rec.detail = f"{type(e).__name__}: {e}"
            self.bus.emit("agent.step", f"✗ {name} failed — {rec.detail}", agent=self.name,
                          data={"status": "failed", "step": name, "error": rec.detail, "trace": traceback.format_exc()[-1500:]})
            if required:
                raise
            return None

    def call_tool(self, name: str, *args, **kwargs) -> Any:
        spec = self.tools.get(name)
        return self.step(name, lambda: spec.fn(*args, **kwargs))

    # ------------------------------------------------------------- LLM use
    def write(self, purpose: str, system: str, prompt: str, *, fallback: str, max_tokens: int = 700) -> tuple[str, str]:
        """Ask the LLM for prose; fall back to deterministic text. Returns (text, mode)."""
        if self.llm.provider == "offline":
            return fallback, "offline"
        res = self.step(f"draft: {purpose}", lambda: self.llm.complete(system, prompt, max_tokens=max_tokens),
                        summary=lambda r: f"{r.tokens_in}+{r.tokens_out} tokens via {r.provider}" if r and r.ok else "fallback")
        if res and res.ok and res.text.strip():
            return res.text.strip(), res.provider
        if res and res.error:
            self.say(f"LLM unavailable ({res.error}); using deterministic text", status="warn")
        return fallback, "offline"

    # ------------------------------------------------------------- lifecycle
    def run(self) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def execute(self) -> bool:
        self.set_status("working", f"{self.name} starts")
        try:
            self.run()
            self.set_status("done", f"{self.name} finished")
            return True
        except Exception as e:  # noqa: BLE001
            self.bus.emit("error", f"{self.name} crashed: {type(e).__name__}: {e}", agent=self.name,
                          data={"trace": traceback.format_exc()[-2000:]})
            self.set_status("failed", f"{self.name} failed")
            return False

    def step_log(self) -> list[dict]:
        return [{"step": s.step, "status": s.status, "duration": round(s.duration, 2), "detail": s.detail} for s in self.steps]
