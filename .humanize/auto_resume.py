#!/usr/bin/env python3
"""Bounded retry wrapper for resumable Humanize flows after an OOM or killed process."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


OOM_MARKERS = ("out of memory", "oom", "killed", "sigkill", "cannot allocate memory")


def _retryable(returncode: int, output: str) -> bool:
    if returncode == 0:
        return False
    lowered = output.lower()
    return returncode < 0 or any(marker in lowered for marker in OOM_MARKERS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flow", required=True, help="the same flow name/path used for every attempt")
    parser.add_argument("--agent", action="append", required=True, dest="agents")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--task", required=True)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--delay-seconds", type=float, default=10.0)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--resume-epic", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 0 <= args.max_retries <= 5:
        parser.error("--max-retries must be between 0 and 5")
    if args.delay_seconds < 0 or args.delay_seconds > 3600:
        parser.error("--delay-seconds must be between 0 and 3600")

    command = [sys.executable, __file__, "--once", "--flow", args.flow, "--task", args.task,
               "--workspace", str(args.workspace)]
    for agent in args.agents:
        command.extend(("--agent", agent))
    if args.config is not None:
        command.extend(("--config", str(args.config)))
    if args.once:
        from hmz.sdk import Hmz

        os.chdir(args.workspace)
        hmz = Hmz(args.workspace)
        argv = ["-f", args.flow]
        for agent in args.agents:
            argv.extend(("-a", agent))
        if args.config is not None:
            argv.extend(("-c", str(args.config)))
        argv.append(args.task)
        flow, agents, task, config, _ = hmz.read(argv)
        hmz.runner(flow, agents, config, resume=args.resume_epic).run(task)
        return 0

    environment = dict(os.environ)
    resume_epic: Path | None = args.resume_epic
    for attempt in range(args.max_retries + 1):
        current = list(command)
        if resume_epic is not None:
            current.extend(("--resume-epic", str(resume_epic)))
        completed = subprocess.run(current, text=True, capture_output=True, check=False, env=environment)
        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        sys.stdout.write(completed.stdout or "")
        sys.stderr.write(completed.stderr or "")
        if completed.returncode == 0:
            return 0
        if not _retryable(completed.returncode, output) or attempt >= args.max_retries:
            return completed.returncode if completed.returncode > 0 else 128 - completed.returncode
        # Resolve the newest resumable epic after the killed child exits. Humanize's next
        # Runner(resume=...) invocation is the programmatic equivalent of `/resume`.
        try:
            from hmz.sdk import Hmz

            resume_epic = Hmz(args.workspace).epics.resumed(args.flow)
        except Exception as error:  # noqa: BLE001 - report and let the bounded retry stop
            print(f"auto-resume could not locate a resumable epic: {error}", file=sys.stderr)
            return 1
        if resume_epic is None:
            print("auto-resume found no persisted state; refusing a fresh rerun", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "event": "auto-resume",
                    "attempt": attempt + 1,
                    "reason": "killed-or-oom",
                    "flow": args.flow,
                    "epic": str(resume_epic),
                    "next": "Runner(..., resume=epic)",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        time.sleep(args.delay_seconds)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
