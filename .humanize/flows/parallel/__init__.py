"""Resumable concurrent meta-flow for isolated workflow branches."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any, Literal

from hmz.flows import Agent, flow, load
from pydantic import BaseModel, ConfigDict, Field, field_validator


SKILLS = ("analysis", "first-principles", "implementation", "research", "review")
_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
_WORKER_FLOW = Path(__file__).resolve().parent.parent / "parallel_worker"


class Branch(BaseModel):
    """One independent flow invocation and its per-role skill allowlists."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=96)
    task: str = Field(min_length=1, max_length=65_536)
    executor_skills: tuple[str, ...] = ()
    reviewer_skills: tuple[str, ...] = ("review",)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        import re

        if re.fullmatch(_NAME, value) is None:
            raise ValueError("branch name contains unsafe characters")
        return value

    @field_validator("task")
    @classmethod
    def non_blank_task(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("branch task must not be blank")
        return value

    @field_validator("executor_skills", "reviewer_skills")
    @classmethod
    def valid_skills(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > len(SKILLS) or len(set(values)) != len(values):
            raise ValueError("node skill lists must be bounded and duplicate-free")
        if any(value not in SKILLS for value in values):
            raise ValueError(f"node skills must be chosen from {list(SKILLS)}")
        return values


class Config(BaseModel):
    """Bounded fan-out settings accepted by the meta-flow."""

    model_config = ConfigDict(extra="forbid")
    branches: tuple[Branch, ...] = Field(min_length=1, max_length=20)
    max_concurrency: int = Field(default=8, ge=1, le=20)
    keep_workspaces: bool = False


def _worker_path() -> Path:
    return _WORKER_FLOW


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(root: Path) -> dict[str, str]:
    """Hash a branch tree without following symlinks or recording transient outputs."""
    result: dict[str, str] = {}
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            name for name in directories if name not in {".git", "workflow-output"}
        )
        for name in sorted(files):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                result[relative] = "link:" + os.readlink(path)
            elif path.is_file():
                result[relative] = _digest_file(path)
    return result


def _copy_tree(source: Path, destination: Path) -> str:
    """Make a reflink-friendly disposable snapshot, falling back to a regular copy."""
    try:
        subprocess.run(
            ["cp", "-a", "--reflink=auto", f"{source}/.", str(destination)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        return "reflink-or-copy"
    except (OSError, subprocess.CalledProcessError):
        shutil.rmtree(destination)
        shutil.copytree(source, destination, symlinks=True)
        return "copy-fallback"


def _clean_context(branch_root: Path) -> None:
    """Remove project-wide instruction and skill surfaces from a node snapshot."""
    for relative in (
        ".git",
        ".humanize",
        ".codex",
        ".agent",
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        ".claude/settings.json",
        ".claude/settings.local.json",
    ):
        candidate = branch_root / relative
        if candidate.is_dir() and not candidate.is_symlink():
            shutil.rmtree(candidate)
        elif candidate.exists() or candidate.is_symlink():
            candidate.unlink()


def _stage(source: Path, run_id: str, branch: Branch) -> tuple[Path, dict[str, str], str]:
    parent = Path(tempfile.gettempdir()) / "elasticweave" / "cow" / run_id
    parent.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix=f"elasticweave-{branch.name}-", dir=parent))
    method = _copy_tree(source, destination)
    _clean_context(destination)
    return destination, _manifest(destination), method


def _copy_result(branch_root: Path, branch: Branch, output_root: Path) -> dict[str, Any]:
    source = branch_root / "workflow-output/humanize/worker" / f"{branch.name}.json"
    if not source.is_file() or source.is_symlink():
        raise RuntimeError(f"worker did not produce a regular result for {branch.name}")
    result = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("schema") != "elasticweave-worker-result/v1":
        raise RuntimeError(f"worker result for {branch.name} has an invalid schema")
    destination = output_root / f"{branch.name}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


async def _run_branch(
    agents: tuple[Agent, Agent],
    source: Path,
    output_root: Path,
    run_id: str,
    branch: Branch,
    entry: dict[str, Any],
    keep_workspaces: bool,
) -> None:
    entry.update({"status": "running", "started_at": time.time()})
    workspace, before, copy_method = _stage(source, run_id, branch)
    entry.update({"workspace_mode": "copy_on_write", "copy_method": copy_method})
    try:
        executor = agents[0].clone(name=f"{branch.name}:executor")
        reviewer = agents[1].clone(name=f"{branch.name}:reviewer")
        child = load(_worker_path())
        await child(
            (executor, reviewer),
            branch.task,
            {
                "branch": branch.name,
                "workspace": str(workspace),
                "executor_skills": branch.executor_skills,
                "reviewer_skills": branch.reviewer_skills,
            },
        )
        result = _copy_result(workspace, branch, output_root)
        after = _manifest(workspace)
        entry.update(
            {
                "status": "complete",
                "result": result,
                "changed_paths": sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path)),
                "finished_at": time.time(),
            }
        )
    except asyncio.CancelledError:
        entry.update({"status": "interrupted", "finished_at": time.time()})
        raise
    except Exception as error:  # noqa: BLE001 - branch failure is recorded for resume
        entry.update({"status": "failed", "error": f"{type(error).__name__}: {error}"[:2_000], "finished_at": time.time()})
    finally:
        if not keep_workspaces and workspace.exists():
            shutil.rmtree(workspace)


@flow(resumable=True)
async def run(
    agents: tuple[Agent, Agent],
    task: str,
    config: Config | None = None,
    state: dict[str, Any] | None = None,
) -> None:
    """Fan out independent flow calls, then resume only incomplete branches."""
    if config is None:
        config = Config(branches=(Branch(name="main", task=task),))
    held = state if state is not None else {}
    if held.get("schema") not in (None, "elasticweave-parallel-state/v1"):
        raise RuntimeError("parallel flow state has an unknown schema")
    held.update({"schema": "elasticweave-parallel-state/v1", "task": task})
    run_id = str(held.setdefault("run_id", uuid.uuid4().hex))
    source = Path.cwd().resolve(strict=True)
    output_root = source / "workflow-output/humanize/parallel" / run_id
    output_root.mkdir(parents=True, exist_ok=True)
    entries = held.setdefault("branches", {})
    if not isinstance(entries, dict):
        raise RuntimeError("parallel flow state branches must be an object")
    semaphore = asyncio.Semaphore(config.max_concurrency)

    async def bounded(branch: Branch) -> None:
        entry = entries.setdefault(branch.name, {"status": "pending"})
        if not isinstance(entry, dict):
            raise RuntimeError(f"parallel state for {branch.name} is not an object")
        if entry.get("status") == "complete":
            return
        async with semaphore:
            await _run_branch(agents, source, output_root, run_id, branch, entry, config.keep_workspaces)

    await asyncio.gather(*(bounded(branch) for branch in config.branches))
    held["finished_at"] = time.time()
