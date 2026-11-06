#!/usr/bin/env python3
"""CLI and server entry point for Due-Diligence Room."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from dd_room import config
from dd_room.agents import DEPTHS, Orchestrator
from dd_room.store import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dd-room",
        description="Due-Diligence Room — multi-agent SEC EDGAR investment memo workstation.",
    )
    parser.add_argument("--host", default=config.host(), help="Bind host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=config.port(), help="Bind port (default: %(default)s)")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn auto-reload for development")
    parser.add_argument("--ticker", help="Run a single ticker in CLI mode and exit instead of starting the server")
    parser.add_argument("--depth", choices=list(DEPTHS.keys()), default="standard", help="Depth for --ticker mode")
    parser.add_argument("--provider", default=config.llm_provider(), help="LLM provider for --ticker mode (default: %(default)s)")
    parser.add_argument("--out", help="Write the generated Markdown memo to this path in --ticker mode")
    args = parser.parse_args(argv)

    if args.ticker:
        store = Store(config.db_path())
        orch = Orchestrator(on_finish=lambda r: store.save_run(r))
        run = orch.start(args.ticker, depth=args.depth, provider=args.provider, auto_approve=True, blocking=True)
        if run.status != "done":
            print(f"[dd-room] run failed ({run.status}): {run.error}", file=sys.stderr)
            return 1
        md = run.bb.meta.get("memo_md", "")
        stats = run.bb.meta.get("stats", {})
        uns = run.bb.meta.get("unsourced", [])
        if args.out:
            Path(args.out).write_text(md, encoding="utf-8")
            print(f"[dd-room] wrote {args.out} ({stats.get('words', 0)} words, {stats.get('citations', 0)} citations, {len(uns)} unsourced)")
        else:
            sys.stdout.write(md + "\n")
        return 0

    print(f"Due-Diligence Room listening on http://{args.host}:{args.port}")
    uvicorn.run("dd_room.server:app", host=args.host, port=args.port, reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
