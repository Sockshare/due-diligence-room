#!/usr/bin/env bash
# One command to run Due-Diligence Room: creates .venv, installs pinned deps, launches the server.
set -euo pipefail
cd "$(dirname "$0")"

UV="${UV:-$HOME/.local/bin/uv}"
if [ ! -x "$UV" ] && command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"; fi

if [ ! -x .venv/bin/python ]; then
  if [ -x "$UV" ]; then
    "$UV" venv .venv
  else
    python3 -m venv .venv
  fi
fi

if [ ! -f .venv/.deps-ok ] || [ requirements.txt -nt .venv/.deps-ok ]; then
  if [ -x "$UV" ]; then
    "$UV" pip install -q -p .venv/bin/python -r requirements.txt
  else
    .venv/bin/python -m pip install -q -r requirements.txt
  fi
  touch .venv/.deps-ok
fi

exec .venv/bin/python app.py "$@"
