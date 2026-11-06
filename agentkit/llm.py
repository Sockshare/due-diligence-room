"""Provider-agnostic LLM client over plain HTTP (no SDKs).

Providers: ``gemini`` (GEMINI_API_KEY), ``anthropic`` (ANTHROPIC_API_KEY),
``openai`` (OPENAI_API_KEY), ``ollama`` (OLLAMA_HOST), ``offline``.

``offline`` never makes a network call: ``complete`` returns an empty result
with ``ok=False`` and callers use deterministic templates. Keys are read from
the environment at call time and never logged or echoed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

DEFAULT_MODELS = {
    "gemini": "gemini-2.0-flash",
    "anthropic": "claude-3-5-haiku-latest",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
}


def estimate_tokens(text: str) -> int:
    """Cheap, provider-neutral estimate (~4 chars per token for English prose)."""
    return max(1, len(text) // 4)


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    provider: str = "offline"
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    error: Optional[str] = None


class LLMClient:
    def __init__(self, provider: str = "offline", model: Optional[str] = None, timeout: float = 90.0):
        self.provider = (provider or "offline").lower()
        self.model = model or DEFAULT_MODELS.get(self.provider, "")
        self.timeout = timeout
        self.total_in = 0
        self.total_out = 0
        self.calls = 0

    # ------------------------------------------------------------ helpers
    def is_configured(self) -> bool:
        if self.provider == "offline":
            return True
        if self.provider == "gemini":
            return bool(os.environ.get("GEMINI_API_KEY"))
        if self.provider == "anthropic":
            return bool(os.environ.get("ANTHROPIC_API_KEY"))
        if self.provider == "openai":
            return bool(os.environ.get("OPENAI_API_KEY"))
        if self.provider == "ollama":
            return bool(os.environ.get("OLLAMA_HOST"))
        return False

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # --------------------------------------------------------------- main
    def complete(self, system: str, prompt: str, *, max_tokens: int = 700, temperature: float = 0.2) -> LLMResult:
        if self.provider == "offline":
            return LLMResult(ok=False, provider="offline", error="offline mode")
        if not self.is_configured():
            return LLMResult(ok=False, provider=self.provider, model=self.model, error=f"{self.provider} not configured (missing key/host)")
        tokens_in = estimate_tokens(system) + estimate_tokens(prompt)
        try:
            if self.provider == "gemini":
                text, usage = self._gemini(system, prompt, max_tokens, temperature)
            elif self.provider == "anthropic":
                text, usage = self._anthropic(system, prompt, max_tokens, temperature)
            elif self.provider == "openai":
                text, usage = self._openai(system, prompt, max_tokens, temperature)
            elif self.provider == "ollama":
                text, usage = self._ollama(system, prompt, max_tokens, temperature)
            else:
                return LLMResult(ok=False, provider=self.provider, error=f"unknown provider {self.provider}")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            return LLMResult(ok=False, provider=self.provider, model=self.model, error=f"HTTP {e.code} from {self.provider}: {detail}")
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            return LLMResult(ok=False, provider=self.provider, model=self.model, error=f"{type(e).__name__}: {e}")
        ti = usage.get("in") or tokens_in
        to = usage.get("out") or estimate_tokens(text)
        self.total_in += ti
        self.total_out += to
        self.calls += 1
        return LLMResult(ok=True, text=text, provider=self.provider, model=self.model, tokens_in=ti, tokens_out=to)

    # ---------------------------------------------------------- providers
    def _gemini(self, system, prompt, max_tokens, temperature):
        key = os.environ["GEMINI_API_KEY"]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        r = self._post(url, body, {"x-goog-api-key": key})
        text = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
        u = r.get("usageMetadata", {})
        return text, {"in": u.get("promptTokenCount"), "out": u.get("candidatesTokenCount")}

    def _anthropic(self, system, prompt, max_tokens, temperature):
        key = os.environ["ANTHROPIC_API_KEY"]
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature, "system": system,
                "messages": [{"role": "user", "content": prompt}]}
        r = self._post("https://api.anthropic.com/v1/messages", body, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        text = "".join(b.get("text", "") for b in r["content"] if b.get("type") == "text")
        u = r.get("usage", {})
        return text, {"in": u.get("input_tokens"), "out": u.get("output_tokens")}

    def _openai(self, system, prompt, max_tokens, temperature):
        key = os.environ["OPENAI_API_KEY"]
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        r = self._post(f"{base}/chat/completions", body, {"Authorization": f"Bearer {key}"})
        text = r["choices"][0]["message"]["content"]
        u = r.get("usage", {})
        return text, {"in": u.get("prompt_tokens"), "out": u.get("completion_tokens")}

    def _ollama(self, system, prompt, max_tokens, temperature):
        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        if not host.startswith("http"):
            host = "http://" + host
        body = {"model": self.model, "stream": False, "options": {"num_predict": max_tokens, "temperature": temperature},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        r = self._post(f"{host}/api/chat", body, {})
        text = r["message"]["content"]
        return text, {"in": r.get("prompt_eval_count"), "out": r.get("eval_count")}
