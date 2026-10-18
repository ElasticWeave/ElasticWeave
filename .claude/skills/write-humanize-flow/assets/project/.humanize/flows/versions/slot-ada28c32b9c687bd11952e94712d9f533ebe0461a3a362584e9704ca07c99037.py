"""Replaceable project slot with an executor pass and a fresh structured review."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, NamedTuple

from hmz.flows import Agent, flow
from pydantic import BaseModel, ConfigDict


class Agents(NamedTuple):
    """The two-role seed subset selected from the scheduler's configured pool."""

    executor: Agent
    reviewer: Agent


class Review(BaseModel):
    """The terminal review decision."""

    model_config = ConfigDict(extra="forbid")
    verdict: Literal["COMPLETE", "HUMAN_BLOCKED", "REVISE"]
    reason: str
    human_request_kind: Literal["", "authorization", "decision", "credential", "external-action"] = ""
    human_request: str = ""

    def checked(self) -> "Review":
        if self.verdict == "HUMAN_BLOCKED":
            if not self.human_request_kind or not self.human_request.strip():
                raise RuntimeError("HUMAN_BLOCKED requires an exact human request kind and request")
        elif self.human_request_kind or self.human_request:
            raise RuntimeError("only HUMAN_BLOCKED may carry a human request")
        return self


def _write_result(review: Review) -> None:
    root = Path.cwd().resolve()
    directory = Path("workflow-output/humanize/default")
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or directory.resolve() != root / "workflow-output/humanize/default":
        raise RuntimeError("result directory escapes the project")
    destination = directory / "result.json"
    if destination.is_symlink():
        raise RuntimeError("result destination must not be a symlink")
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    result = {
        "terminal": review.verdict,
        "reason": review.reason,
        "humanRequestKind": review.human_request_kind,
        "humanRequest": review.human_request,
    }
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


@flow
def run(agents: Agents, task: str) -> None:
    """Execute the task, then require an independent structured terminal review."""
    agents.executor(task)
    review = agents.reviewer(
        "Review the actual workspace and task. Before deciding, make one bounded best-effort "
        "attempt to ask an installed cursor-agent for a read-only second opinion. If command "
        "execution is available, use only a non-mutating plan/read-only mode and explicitly forbid "
        "Cursor from editing files. Independently verify every Cursor finding. If cursor-agent, "
        "authentication, a model, quota, or command execution is unavailable, say so briefly in "
        "the reason and continue your own review; Cursor absence alone is not HUMAN_BLOCKED. "
        "If the task prompt contains Candidate reward_hacking_risks, treat each item as an "
        "explicit hypothesis: inspect the actual diff and evidence for that mechanism, report "
        "concrete supporting or disconfirming facts, and never treat an empty list as proof of safety. "
        "Return COMPLETE only if the task is achieved; HUMAN_BLOCKED only for a concrete "
        "human-only dependency; otherwise REVISE.\n\n" + task,
        schema=Review,
    )
    if review is None:
        raise RuntimeError("reviewer did not return the required schema")
    review = review.checked()
    _write_result(review)
    print(f"TERMINAL: {review.verdict}")
    if review.verdict == "HUMAN_BLOCKED":
        print(f"HUMAN-REQUEST-KIND: {review.human_request_kind}")
        print(f"HUMAN-REQUEST: {review.human_request}")
