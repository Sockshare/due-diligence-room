"""Runtime configuration. Everything comes from environment variables (optionally
loaded from a gitignored .env file) and has a safe default so the app works with
zero configuration and zero API keys."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no dependency). Does not override already-set vars."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(PROJECT_ROOT / ".env")

DEFAULT_SEC_USER_AGENT = "DueDiligenceRoom contact@example.com"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def data_dir() -> Path:
    d = Path(env("DD_DATA_DIR", str(PROJECT_ROOT / "data")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir() -> Path:
    d = data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "dd_room.db"


def sec_user_agent() -> str:
    ua = env("SEC_USER_AGENT", "").strip()
    return ua or DEFAULT_SEC_USER_AGENT


def host() -> str:
    return env("DD_HOST", "127.0.0.1")


def port() -> int:
    try:
        return int(env("DD_PORT", "8112"))
    except ValueError:
        return 8112


def llm_provider() -> str:
    return env("DD_LLM_PROVIDER", "offline").strip().lower() or "offline"


def llm_model() -> str:
    return env("DD_LLM_MODEL", "").strip()


def available_providers() -> dict[str, bool]:
    """Which providers *could* work given the environment. Never returns key values."""
    return {
        "offline": True,
        "gemini": bool(env("GEMINI_API_KEY")),
        "anthropic": bool(env("ANTHROPIC_API_KEY")),
        "openai": bool(env("OPENAI_API_KEY")),
        "ollama": bool(env("OLLAMA_HOST")),
    }
