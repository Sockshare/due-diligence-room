"""Human approval gate: a run pauses here until a person approves or rejects.

The orchestrator calls ``gate.request(...)`` from its worker thread, which
blocks (with timeout). The HTTP layer calls ``gate.decide(...)`` from a
request handler. ``auto_approve=True`` makes the gate transparent (used in
offline mode and tests).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ApprovalDecision:
    approved: bool
    by: str = "human"
    note: str = ""
    decided_at: float = field(default_factory=time.time)
    timed_out: bool = False


class ApprovalGate:
    def __init__(self, auto_approve: bool = False, timeout: float = 1800.0):
        self.auto_approve = auto_approve
        self.timeout = timeout
        self._event = threading.Event()
        self._decision: Optional[ApprovalDecision] = None
        self.pending: Optional[dict] = None
        self.requested_at: Optional[float] = None

    def request(self, payload: dict) -> ApprovalDecision:
        self.pending = payload
        self.requested_at = time.time()
        if self.auto_approve:
            self._decision = ApprovalDecision(approved=True, by="auto", note="auto-approved (offline mode or setting)")
            self.pending = None
            return self._decision
        self._event.clear()
        ok = self._event.wait(self.timeout)
        self.pending = None
        if not ok or self._decision is None:
            self._decision = ApprovalDecision(approved=False, by="timeout", note="no decision within timeout", timed_out=True)
        return self._decision

    def decide(self, approved: bool, by: str = "human", note: str = "") -> bool:
        if self.pending is None:
            return False
        self._decision = ApprovalDecision(approved=approved, by=by, note=note)
        self._event.set()
        return True

    @property
    def is_pending(self) -> bool:
        return self.pending is not None

    @property
    def decision(self) -> Optional[ApprovalDecision]:
        return self._decision
