#!/usr/bin/env python3
"""Check or explicitly install the pinned humanize2 source in an isolated venv."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from safe_mapping import MappingError, validate_hmz_binary

REPOSITORY = "https://github.com/humanfia/humanize2.git"
REVISION = "8fbb2b718d74b7ef1d3d0650a9768415ec489f26"


def default_root() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / "humanize2" / "source" / REVISION


def revision(root: Path) -> str:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def status(root: Path) -> dict[str, str] | None:
    binary = root / ".venv" / "bin" / "hmz"
    if not binary.is_file() or revision(root) != REVISION:
        return None
    try:
        found = validate_hmz_binary(binary)
    except (OSError, MappingError):
        return None
    return {
        "root": str(root),
        "binary": str(binary),
        "repository": REPOSITORY,
        "revision": REVISION,
        "version": str(found["version"]),
    }


def install(root: Path, python: str) -> None:
    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty install target: {root}")
    if shutil.which("git") is None or shutil.which(python) is None:
        raise RuntimeError("git and Python 3.12 are required")
    root.parent.mkdir(parents=True, exist_ok=True)
    commands: list[tuple[list[str], Path | None]] = [
        (["git", "init", str(root)], None),
        (["git", "-C", str(root), "remote", "add", "origin", REPOSITORY], None),
        (["git", "-C", str(root), "fetch", "--depth", "1", "origin", REVISION], None),
        (["git", "-C", str(root), "checkout", "--detach", "FETCH_HEAD"], None),
        ([python, "-m", "venv", str(root / ".venv")], None),
        ([str(root / ".venv/bin/python"), "-m", "pip", "install", "--editable", "."], root),
    ]
    for command, cwd in commands:
        subprocess.run(command, cwd=cwd, check=True, stdout=sys.stderr, stderr=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--install", action="store_true")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--python", default="python3.12")
    args = parser.parse_args()
    configured = os.environ.get("HUMANIZE2_HOME")
    root = (args.target or (Path(configured) if configured else default_root())).expanduser().resolve()
    try:
        found = status(root)
        if found is None and args.check:
            raise RuntimeError(f"pinned humanize2 is not installed at {root}")
        if found is None:
            install(root, args.python)
            found = status(root)
        if found is None:
            raise RuntimeError("install finished but the pinned hmz CLI failed validation")
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"humanize-install: {error}", file=sys.stderr)
        return 1
    print(json.dumps(found, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
