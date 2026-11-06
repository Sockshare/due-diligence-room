"""Shared blackboard: typed sections, a source registry, claims and gaps.

Design notes
* Every number that reaches the memo must be attached to a :class:`Source`.
  ``Blackboard.cite()`` is the only sanctioned way to put a figure in prose; it
  returns a Markdown reference link ``[text][S12]`` that the Editor later
  resolves to the filing URL.
* Sources are de-duplicated on (kind, concept, accn, period) so the same fact
  cited ten times gets one footnote.
* Claims are statements an agent believes; the Skeptic must counter each one
  with kind == "bullish".
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional


@dataclass
class Source:
    id: str
    kind: str                     # xbrl | filing | efts | market | derived | index
    url: str
    label: str
    concept: Optional[str] = None
    accn: Optional[str] = None
    form: Optional[str] = None
    filed: Optional[str] = None
    period: Optional[str] = None
    inputs: list[str] = field(default_factory=list)   # for derived: source ids of inputs
    note: str = ""


@dataclass
class Claim:
    id: str
    agent: str
    section: str
    text: str
    kind: str = "bullish"          # bullish | bearish | neutral
    support: list[str] = field(default_factory=list)
    tag: str = ""                  # machine-readable topic, e.g. "revenue_growth"


@dataclass
class Section:
    key: str
    title: str
    owner: str
    order: int
    status: str = "empty"          # empty | in_progress | done | failed
    data: dict = field(default_factory=dict)
    narrative: str = ""            # Markdown
    confidence: Optional[float] = None
    confidence_notes: list[str] = field(default_factory=list)
    completeness: Optional[float] = None
    updated_at: float = field(default_factory=time.time)


class Blackboard:
    def __init__(self, meta: Optional[dict] = None, on_change: Optional[Callable[[str, dict], None]] = None):
        self.meta: dict = meta or {}
        self.sections: dict[str, Section] = {}
        self.sources: dict[str, Source] = {}
        self._source_index: dict[tuple, str] = {}
        self.claims: list[Claim] = []
        self.counters: list[dict] = []
        self.gaps: list[dict] = []
        self.disagreements: list[dict] = []
        self.log: list[dict] = []
        self._lock = threading.RLock()
        self._on_change = on_change

    # ------------------------------------------------------------ sections
    def define_section(self, key: str, title: str, owner: str, order: int) -> Section:
        with self._lock:
            if key not in self.sections:
                self.sections[key] = Section(key=key, title=title, owner=owner, order=order)
            return self.sections[key]

    def section(self, key: str) -> Section:
        return self.sections[key]

    def update_section(self, key: str, *, status: Optional[str] = None, data: Optional[dict] = None,
                       narrative: Optional[str] = None, confidence: Optional[float] = None,
                       confidence_notes: Optional[list[str]] = None, completeness: Optional[float] = None) -> Section:
        with self._lock:
            s = self.sections[key]
            if status is not None:
                s.status = status
            if data is not None:
                s.data.update(data)
            if narrative is not None:
                s.narrative = narrative
            if confidence is not None:
                s.confidence = confidence
            if confidence_notes is not None:
                s.confidence_notes = confidence_notes
            if completeness is not None:
                s.completeness = completeness
            s.updated_at = time.time()
        self._changed("section", {"key": key, "status": s.status, "title": s.title, "owner": s.owner,
                                  "confidence": s.confidence, "has_narrative": bool(s.narrative)})
        return s

    # ------------------------------------------------------------- sources
    def add_source(self, kind: str, url: str, label: str, *, concept: Optional[str] = None, accn: Optional[str] = None,
                   form: Optional[str] = None, filed: Optional[str] = None, period: Optional[str] = None,
                   inputs: Optional[list[str]] = None, note: str = "") -> str:
        key = (kind, concept, accn, period, url if kind in ("efts", "market", "derived", "index") else None, label if kind == "derived" else None)
        with self._lock:
            if key in self._source_index:
                return self._source_index[key]
            sid = f"S{len(self.sources) + 1}"
            self.sources[sid] = Source(id=sid, kind=kind, url=url, label=label, concept=concept, accn=accn, form=form,
                                       filed=filed, period=period, inputs=list(inputs or []), note=note)
            self._source_index[key] = sid
        return sid

    def cite(self, text: str, source_id: str) -> str:
        """Markdown reference-style link. Editor resolves ``[Sn]`` to the URL."""
        if source_id not in self.sources:
            raise KeyError(f"unknown source {source_id}")
        return f"[{text}][{source_id}]"

    # -------------------------------------------------------- claims & gaps
    def add_claim(self, agent: str, section: str, text: str, kind: str = "bullish", support: Optional[list[str]] = None, tag: str = "") -> Claim:
        with self._lock:
            c = Claim(id=f"C{len(self.claims) + 1}", agent=agent, section=section, text=text, kind=kind, support=list(support or []), tag=tag)
            self.claims.append(c)
        self._changed("claim", asdict(c))
        return c

    def add_counter(self, claim_id: str, counter: str, evidence_to_change_mind: str, severity: str = "medium") -> dict:
        d = {"claim_id": claim_id, "counter": counter, "evidence_to_change_mind": evidence_to_change_mind, "severity": severity}
        with self._lock:
            self.counters.append(d)
        self._changed("counter", d)
        return d

    def add_gap(self, section: str, what: str, why: str = "") -> dict:
        g = {"section": section, "what": what, "why": why}
        with self._lock:
            if g not in self.gaps:
                self.gaps.append(g)
        self._changed("gap", g)
        return g

    def add_disagreement(self, topic: str, position_a: str, position_b: str, agents: tuple[str, str], resolved: bool = False) -> dict:
        d = {"topic": topic, "position_a": position_a, "position_b": position_b, "agents": list(agents), "resolved": resolved}
        with self._lock:
            self.disagreements.append(d)
        self._changed("disagreement", d)
        return d

    # ---------------------------------------------------------------- misc
    def _changed(self, what: str, payload: dict) -> None:
        if self._on_change:
            try:
                self._on_change(what, payload)
            except Exception:
                pass

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "meta": self.meta,
                "sections": {k: {**asdict(s)} for k, s in sorted(self.sections.items(), key=lambda kv: kv[1].order)},
                "sources": {k: asdict(v) for k, v in self.sources.items()},
                "claims": [asdict(c) for c in self.claims],
                "counters": list(self.counters),
                "gaps": list(self.gaps),
                "disagreements": list(self.disagreements),
            }

    def to_json(self) -> str:
        return json.dumps(self.snapshot(), default=_json_default)

    @classmethod
    def from_snapshot(cls, snap: dict) -> "Blackboard":
        bb = cls(meta=snap.get("meta", {}))
        for k, s in snap.get("sections", {}).items():
            bb.sections[k] = Section(**{kk: vv for kk, vv in s.items() if kk in Section.__dataclass_fields__})
        for k, v in snap.get("sources", {}).items():
            bb.sources[k] = Source(**{kk: vv for kk, vv in v.items() if kk in Source.__dataclass_fields__})
        bb.claims = [Claim(**{kk: vv for kk, vv in c.items() if kk in Claim.__dataclass_fields__}) for c in snap.get("claims", [])]
        bb.counters = list(snap.get("counters", []))
        bb.gaps = list(snap.get("gaps", []))
        bb.disagreements = list(snap.get("disagreements", []))
        return bb


def _json_default(o: Any):
    if hasattr(o, "as_dict"):
        return o.as_dict()
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    if isinstance(o, set):
        return sorted(o)
    return str(o)
