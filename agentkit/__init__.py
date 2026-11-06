"""agentkit — a small, dependency-free toolkit for multi-agent apps.

Pieces:
* :class:`Blackboard` — shared typed state (sections, sources, claims, gaps).
* :class:`Agent` — base class with a tool registry and a step log that streams
  to an :class:`EventBus`.
* :class:`EventBus` — thread-safe event stream with replay (for SSE).
* :class:`ApprovalGate` — a human-in-the-loop pause.
* :class:`LLMClient` — provider-agnostic chat completion over plain HTTP with a
  deterministic ``offline`` mode.
"""
from .blackboard import Blackboard, Section, Source, Claim
from .events import EventBus, Event
from .agent import Agent, ToolRegistry, tool
from .approval import ApprovalGate, ApprovalDecision
from .llm import LLMClient, LLMResult, estimate_tokens

__all__ = [
    "Blackboard", "Section", "Source", "Claim", "EventBus", "Event", "Agent", "ToolRegistry", "tool",
    "ApprovalGate", "ApprovalDecision", "LLMClient", "LLMResult", "estimate_tokens",
]
