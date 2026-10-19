"""One isolated workflow node with an explicit executor/reviewer skill policy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, NamedTuple

from hmz.flows import Agent, flow
from pydantic import BaseModel, ConfigDict, Field


SKILLS = frozenset({"analysis", "first-principles", "implementation", "research", "review"})


class Agents(NamedTuple):
    """The two roles a node may use; each receives its own session and skill set."""

    executor: Agent
    reviewer: Agent


class Config(BaseModel):
    """Runtime-only settings supplied by the parent flow for one node."""

    model_config = ConfigDict(extra="forbid")
    branch: str = Field(min_length=1, max_length=96)
    workspace: str = Field(min_length=1)
    executor_skills: tuple[str, ...] = ()
    reviewer_skills: tuple[str, ...] = ("review",)

    def checked(self) -> "Config":
        if any(skill not in SKILLS for skill in self.executor_skills + self.reviewer_skills):
            raise ValueError(f"node skills must be chosen from {sorted(SKILLS)}")
        return self


def _write_result(config: Config, executor_output: str, reviewer_output: str) -> None:
    """Write a bounded branch result inside its disposable workspace."""
    root = Path(config.workspace).resolve(strict=True)
    directory = root / "workflow-output/humanize/worker"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or directory.resolve() != root / "workflow-output/humanize/worker":
        raise RuntimeError("worker result directory escapes the branch workspace")
    destination = directory / f"{config.branch}.json"
    if destination.is_symlink():
        raise RuntimeError("worker result destination must not be a symlink")
    payload = {
        "schema": "elasticweave-worker-result/v1",
        "branch": config.branch,
        "status": "complete",
        "executor_skills": list(config.executor_skills),
        "reviewer_skills": list(config.reviewer_skills),
        "executor_output": executor_output[-16_384:],
        "reviewer_output": reviewer_output[-16_384:],
    }
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


@flow(selectable=False)
async def run(agents: Agents, task: str, config: Config | None = None) -> None:
    """Execute one node, loading only the skills assigned to each role."""
    if config is None:
        config = Config(branch="node", workspace=os.getcwd())
    config.checked()
    executor_session = agents.executor.new(config.workspace)
    executor_session.loads(config.executor_skills)
    executor_output = await executor_session.aturn(
        "Implement the assigned node in this isolated workspace. Keep the work bounded, "
        "record concrete evidence, and do not schedule another workflow.\n\n" + task
    )
    reviewer_session = agents.reviewer.new(config.workspace)
    reviewer_session.loads(config.reviewer_skills)
    reviewer_output = await reviewer_session.aturn(
        "Review the executor's actual changes and evidence in this isolated workspace. "
        "Check the task-specific reward-hacking hypotheses and report concrete support or "
        "disconfirmation. Do not edit files or dispatch another workflow.\n\n" + task
    )
    _write_result(config, str(executor_output or ""), str(reviewer_output or ""))
