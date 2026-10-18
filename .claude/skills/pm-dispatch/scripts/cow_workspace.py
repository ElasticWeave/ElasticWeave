#!/usr/bin/env python3
"""Create and account for disposable, context-filtered workflow workspaces."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

RUNTIME_SKILLS = {"subagent-workflow", "update-platform-config"}
PRUNED_ROOT_FILES = {"AGENTS.md", "CLAUDE.md"}
PRUNED_PROJECT_CONFIG = {".claude/settings.json", ".claude/settings.local.json"}
SKIP_DIRS = {".git", "workflow-output"}


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    if relative == Path(".") or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe workspace path: {relative}")
    return relative.as_posix()


def tree_manifest(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRS)
        for name in sorted(files):
            path = current_path / name
            relative = _relative(path, root)
            if path.is_symlink():
                result[relative] = {"kind": "symlink", "target": os.readlink(path)}
            elif path.is_file():
                result[relative] = {
                    "kind": "file", "sha256": _digest(path.read_bytes()),
                    "mode": path.stat().st_mode & 0o777,
                }
    return result


def _declared_skills(source: Path, mapping_relative: str) -> set[str]:
    mapping = json.loads((source / mapping_relative).read_text(encoding="utf-8"))
    design = json.loads((source / mapping["designPath"]).read_text(encoding="utf-8"))
    names = set(RUNTIME_SKILLS)
    for node in design.get("nodes", []):
        skills = node.get("skills") or {}
        names.update(skills.get("required", []))
        names.update(skills.get("onDemand", []))
    return names


def _copy_tree(source: Path, destination: Path) -> str:
    try:
        subprocess.run(
            ["cp", "-a", "--reflink=auto", f"{source}/.", str(destination)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        return "reflink-or-copy"
    except (OSError, subprocess.CalledProcessError) as error:
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=True)
        return f"copy-fallback:{type(error).__name__}"


def create(source: Path, mapping_relative: str, parent: Path, node_id: str) -> tuple[Path, dict[str, Any]]:
    source = source.resolve()
    if not source.is_dir() or source.is_symlink():
        raise ValueError(f"workflow source is not a plain directory: {source}")
    parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=f"elasticweave-{node_id}-", dir=parent))
    method = _copy_tree(source, workspace)
    allowed = _declared_skills(source, mapping_relative)
    skill_root = workspace / ".claude" / "skills"
    if skill_root.is_dir():
        for child in skill_root.iterdir():
            if child.name in allowed:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    for relative in PRUNED_ROOT_FILES | PRUNED_PROJECT_CONFIG:
        candidate = workspace / relative
        if candidate.exists() or candidate.is_symlink():
            candidate.unlink()
    baseline = tree_manifest(workspace)
    # Keep small text snapshots so a source repository edit made while the
    # disposable worker is running cannot change the reported "before" side
    # of a diff. Hashes remain the authoritative whole-tree commitment.
    baseline_text: dict[str, str] = {}
    for relative, entry in baseline.items():
        if entry.get("kind") != "file":
            continue
        candidate = workspace / relative
        try:
            raw = candidate.read_bytes()
        except OSError:
            continue
        if len(raw) <= 256 * 1024 and b"\x00" not in raw:
            try:
                baseline_text[relative] = raw.decode("utf-8")
            except UnicodeDecodeError:
                pass
    canonical = json.dumps(baseline, sort_keys=True, separators=(",", ":")).encode()
    return workspace, {
        "schema": "workflow-cow-workspace/v1",
        "source": str(source), "workspace": str(workspace), "node_id": node_id,
        "mapping": mapping_relative, "copy_method": method,
        "declared_skills": sorted(allowed - RUNTIME_SKILLS),
        "runtime_skills": sorted(RUNTIME_SKILLS), "baseline": baseline,
        "baseline_text": baseline_text,
        "baseline_tree_sha256": _digest(canonical),
    }


def finalize(workspace: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    current = tree_manifest(workspace)
    baseline = manifest["baseline"]
    changed = sorted(path for path in set(baseline) | set(current) if baseline.get(path) != current.get(path))
    diffs: dict[str, str] = {}
    baseline_text = manifest.get("baseline_text", {})
    for relative in changed:
        after = workspace / relative
        before_text = baseline_text.get(relative, "")
        after_text = after.read_text(encoding="utf-8", errors="replace") if after.is_file() else ""
        if "\x00" not in before_text + after_text:
            diffs[relative] = "".join(difflib.unified_diff(
                before_text.splitlines(keepends=True), after_text.splitlines(keepends=True),
                fromfile=f"source/{relative}", tofile=f"workspace/{relative}"))
    canonical = json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
    return {
        "schema": "workflow-cow-result/v1", "workspace": str(workspace),
        "changed_paths": changed, "current_tree_sha256": _digest(canonical),
        "diffs": diffs, "current": current,
    }


def remove(workspace: Path) -> None:
    workspace = Path(os.path.abspath(workspace))
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError(f"refusing to remove non-directory COW workspace: {workspace}")
    if not workspace.name.startswith("elasticweave-") or workspace.parent.name != "cow":
        raise ValueError(f"refusing to remove non-COW workspace: {workspace}")
    shutil.rmtree(workspace)
