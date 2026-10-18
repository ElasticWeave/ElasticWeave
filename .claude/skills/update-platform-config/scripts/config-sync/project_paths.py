#!/usr/bin/env python3
"""Resolve the target project and installed skill bundle without fixed parent counts."""

from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT_ENV = "GENERAL_AGENT_PROJECT_ROOT"


def _project_ancestor(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists() or (candidate / "config" / "workflows" / "models.json").is_file():
            return candidate
    return None


def project_root(script: Path = Path(__file__)) -> Path:
    """Return the project whose generated state is being synchronized."""
    configured = os.environ.get(PROJECT_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve()

    from_cwd = _project_ancestor(Path.cwd().resolve())
    if from_cwd is not None:
        return from_cwd

    resolved = script.resolve()
    for candidate in resolved.parents:
        if candidate.name == ".claude":
            return candidate.parent
    raise RuntimeError(
        f"cannot resolve project root; run inside the project or set {PROJECT_ROOT_ENV}"
    )


def skills_root(script: Path = Path(__file__)) -> Path:
    """Return the sibling skill directory that owns package templates."""
    resolved = script.resolve()
    for candidate in resolved.parents:
        if candidate.name == "skills" and (candidate / "update-platform-config").is_dir():
            return candidate
    raise RuntimeError(f"cannot resolve installed skills root from {resolved}")
