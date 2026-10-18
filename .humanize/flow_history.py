#!/usr/bin/env python3
"""Show Humanize flow records in a compact flow-centric history or status view."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from typing import Any, Iterator


def _epic_root(workspace: Path) -> Path:
    humanize_home = Path(os.environ.get("HUMANIZE_HOME", Path.home() / ".humanize"))
    key = re.sub(r"[^A-Za-z0-9]", "-", str(workspace.resolve()))
    return humanize_home / "epics" / key


def _events(path: Path) -> Iterator[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _runs(workspace: Path) -> list[Path]:
    root = _epic_root(workspace)
    try:
        return sorted((path for path in root.iterdir() if (path / "epic.jsonl").is_file()), reverse=True)
    except OSError:
        return []


def _summary(epic: Path) -> dict[str, Any]:
    events = list(_events(epic / "epic.jsonl"))
    began = next((event for event in events if event.get("event") == "began"), {})
    ended = next((event for event in reversed(events) if event.get("event") == "ended"), {})
    records = [epic / "epic.jsonl"] + sorted(epic.glob("epic.*.jsonl"))
    calls: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    for record in records:
        for event in _events(record):
            if event.get("event") in {"called", "returned"}:
                calls.append({key: event.get(key) for key in ("event", "flow", "epic", "at", "how")})
            if event.get("event") == "opened":
                sessions.append({key: event.get(key) for key in ("agent", "backend", "session", "flow", "at")})
    return {
        "epic": str(epic),
        "flow": began.get("flow", ""),
        "task": began.get("task", ""),
        "began": began.get("at", ""),
        "ended": ended.get("at", ""),
        "how": ended.get("how", "running"),
        "resumable": bool(began.get("resumable")),
        "agents": began.get("agents", []),
        "calls": calls,
        "sessions": sessions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--status", action="store_true", help="show the latest run and active flow branches")
    parser.add_argument("--flow", help="filter records by flow name")
    args = parser.parse_args()
    summaries = [_summary(epic) for epic in _runs(args.workspace)]
    if args.flow:
        summaries = [summary for summary in summaries if summary["flow"] == args.flow]
    if args.as_json:
        print(json.dumps(summaries[0] if args.status and summaries else summaries, ensure_ascii=False, indent=2))
        return 0
    if not summaries:
        print("No Humanize flow records found.")
        return 0
    shown = summaries[:1] if args.status else summaries
    for summary in shown:
        print(f"{summary['flow']}  {summary['how']}  {summary['began']}  {summary['epic']}")
        print(f"  agents: {', '.join(str(agent.get('agent', '')) for agent in summary['agents'] if isinstance(agent, dict))}")
        for session in summary["sessions"]:
            print(f"  action: {session.get('at', '')} {session.get('agent', '')} opened {session.get('session', '')}")
        for call in summary["calls"]:
            print(f"  flow: {call.get('at', '')} {call.get('event', '')} {call.get('flow', '')} {call.get('epic', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
