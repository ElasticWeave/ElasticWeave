#!/usr/bin/env python3
"""Read-only local-first discovery for reusable skills, agents, and workflows."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run(args, timeout=45):
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return CommandResult(cp.returncode, cp.stdout, cp.stderr)
    except subprocess.TimeoutExpired as exc:
        return CommandResult(124, exc.stdout or "", exc.stderr or "command timed out")
    except OSError as exc:
        return CommandResult(127, "", str(exc))


def query_regex(query):
    terms = [term for term in re.split(r"\s+", query.strip()) if len(term) > 2]
    return re.compile("|".join(re.escape(term) for term in terms) or re.escape(query), re.I)


def _text(path, limit=64_000):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _candidate(kind, path, root, rx):
    haystack = path.name + " " + _text(path)
    if not rx.search(haystack):
        return None
    return {"kind": kind, "path": str(path.resolve()), "project_path": path.relative_to(root).as_posix()}


def scan_project(root, rx):
    """Scan only known read-only project resource surfaces, in deterministic priority order."""
    root = Path(root).resolve(strict=True)
    found = []
    skills = root / ".claude" / "skills"
    if skills.is_dir():
        for skill_md in sorted(skills.glob("*/SKILL.md")):
            item = _candidate("skill", skill_md, root, rx)
            if item:
                found.append(item)
        # Search skill-owned examples without treating every implementation file as a candidate.
        for pattern, kind in (("*/assets/**/*.workflow.json", "skill-workflow-design"),
                              ("*/assets/**/agents/*.md", "skill-agent"),
                              ("*/assets/**/flows/*", "skill-flow")):
            for path in sorted(skills.glob(pattern)):
                item = _candidate(kind, path, root, rx)
                if item:
                    found.append(item)
    for directory, pattern, kind in (
        (root / ".omp" / "agents", "*.md", "agent"),
        (root / ".workflow" / "designs", "*.json", "workflow-design"),
        (root / ".subagent" / "flows", "*.json", "subagent-flow"),
        (root / ".humanize" / "flows", "*.py", "humanize-flow"),
    ):
        if directory.is_dir():
            for path in sorted(directory.glob(pattern)):
                item = _candidate(kind, path, root, rx)
                if item:
                    found.append(item)
    return found


def github_status():
    if not shutil.which("gh"):
        return {"state": "unavailable"}
    version = run(["gh", "--version"])
    if version.returncode:
        return {"state": "command-failed", "returncode": version.returncode}
    auth = run(["gh", "auth", "status"])
    if auth.returncode:
        detail = (auth.stderr + " " + auth.stdout).lower()
        if any(term in detail for term in ("not logged", "unauth", "no accounts", "token")):
            return {"state": "unauthenticated", "returncode": auth.returncode}
        return {"state": "command-failed", "returncode": auth.returncode}
    return {"state": "authenticated"}


def _print_local(root, query):
    print("== 1. PROJECT-LOCAL candidates (read-only, reuse before external search) ==")
    try:
        found = scan_project(root, query_regex(query))
    except (OSError, RuntimeError) as exc:
        print(f"  local-scan-failed: {exc}")
        return []
    if not found:
        print("  (nothing project-local matches)")
    for item in found:
        print(f"  {item['kind']}: {item['project_path']}")
    return found


def _github_search(query):
    status = github_status()
    print("\n== 2. GITHUB candidates (discovery only; adoption is a separate authorization) ==")
    if status["state"] != "authenticated":
        suffix = f" rc={status.get('returncode')}" if status.get("returncode") is not None else ""
        print(f"  github-state: {status['state']}{suffix}")
        return 2 if status["state"] == "command-failed" else 0
    seen = set()
    for variant in (f"{query} claude code", f"{query} subagent", f"{query} skill", "awesome claude code"):
        result = run(["gh", "search", "repos", "--sort=stars", "--limit=5",
                      "--json", "fullName,stargazersCount,description,pushedAt", "--", variant])
        if result.returncode:
            print(f"  github-state: search-failed rc={result.returncode}")
            return 3
        try:
            rows = json.loads(result.stdout)
        except json.JSONDecodeError:
            print("  github-state: search-invalid-json")
            return 3
        for row in rows:
            name = row.get("fullName")
            if not name or name in seen:
                continue
            seen.add(name)
            print(f"  {name}  ★{row.get('stargazersCount', 0)}  {(row.get('pushedAt') or '')[:10]}  "
                  f"{(row.get('description') or '')[:58]}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="exact project root to scan first")
    ap.add_argument("query", nargs="+", help="domain keywords")
    args = ap.parse_args(argv)
    query = " ".join(args.query).strip()
    _print_local(args.root, query)
    rc = _github_search(query)
    print("\n== 3. INSPECT, THEN REQUEST SEPARATE ADOPTION AUTHORIZATION ==")
    print("  Discovery is read-only. Do not clone, install, copy, enable, or activate from this command.")
    print("  Verify candidate source, maintainer, license, permissions, hooks, scripts, and network calls.")
    print("  After selection, obtain authorization for the exact candidate and destination before adoption.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
