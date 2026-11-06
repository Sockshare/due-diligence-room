"""Thread-safe event bus with replay — the backbone of the Live Room SSE feed."""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Event:
    seq: int
    ts: float
    type: str                      # run.status | agent.status | agent.step | blackboard | approval | error | memo
    agent: Optional[str] = None
    message: str = ""
    data: dict = field(default_factory=dict)

    def to_sse(self) -> str:
        return f"id: {self.seq}\nevent: {self.type}\ndata: {json.dumps(asdict(self), default=str)}\n\n"


class EventBus:
    def __init__(self, max_history: int = 5000):
        self._history: list[Event] = []
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._seq = 0
        self.max_history = max_history
        self.closed = False

    def emit(self, type: str, message: str = "", *, agent: Optional[str] = None, data: Optional[dict] = None) -> Event:
        with self._lock:
            self._seq += 1
            ev = Event(seq=self._seq, ts=time.time(), type=type, agent=agent, message=message, data=data or {})
            self._history.append(ev)
            if len(self._history) > self.max_history:
                self._history = self._history[-self.max_history:]
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass
        return ev

    def subscribe(self, since: int = 0) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=10000)
        with self._lock:
            for ev in self._history:
                if ev.seq > since:
                    q.put_nowait(ev)
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def history(self, since: int = 0) -> list[dict]:
        with self._lock:
            return [asdict(e) for e in self._history if e.seq > since]

    def close(self) -> None:
        self.closed = True
        self.emit("run.closed", "stream closed")
