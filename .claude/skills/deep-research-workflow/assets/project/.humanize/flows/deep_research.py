"""Trusted native Humanize2 reference for bounded falsification-first Deep Research.

This is executable source, not a narrow humanize adapter mapping and not the canonical
platform-neutral research design.  The controller permits no external side effect except its
explicit terminal JSON artifact; all research and review roles must start read-only.
"""

from __future__ import annotations

import asyncio
from typing import Literal, NamedTuple

from hmz.agents import AgentBase
from hmz.flows import flow
from pydantic import Field, model_validator

from _research_reference import (
    CallBudget,
    IterationConfig,
    StrictModel,
    TerminalReview,
    attach_iteration_result,
    iteration_prompt,
    json_prompt,
    require_read_only,
    resolve_iteration_policy,
    reviewed_terminal,
    safe_project_path,
    unsupported_diversity_terminal,
    verify_launch,
    write_failure_terminal,
    write_terminal,
)


class Agents(NamedTuple):
    """Semantic roles; launch-time policy resolves providers, models, and effort."""

    preflight: AgentBase
    collector: AgentBase
    counterevidence: AgentBase
    reducer: AgentBase
    reviewer: AgentBase


class Config(IterationConfig):
    """A deliberately bounded standard-width reference run."""

    branch_count: int = Field(default=3, ge=1, le=4)
    max_model_calls: int = Field(default=8, ge=5, le=9)
    terminal_artifact: str = Field(
        default=".humanize/artifacts/deep-research-terminal.json", min_length=1
    )
    cursor_diversity_required: bool = False

    @model_validator(mode="after")
    def _budget_covers_static_path(self) -> "Config":
        required = self.branch_count + 4
        if self.max_model_calls < required:
            raise ValueError(
                f"max_model_calls must cover {required} static calls for this branch count"
            )
        return self


class Assumption(StrictModel):
    assumption_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    uncertainty: int = Field(ge=1, le=5)
    downstream_invalidation: int = Field(ge=1, le=5)
    falsifier: str = Field(min_length=1)
    minimum_valid_check: str = Field(min_length=1)


class PreflightDecision(StrictModel):
    core_problem: str = Field(min_length=1)
    hard_constraints: list[str] = Field(min_length=1)
    kill_set: list[Assumption] = Field(min_length=1, max_length=6)
    verdict: Literal[
        "CONTINUE",
        "STOP_IDEA_REFUTED",
        "UNRESOLVED_PREFLIGHT_VALIDATION",
        "NEEDS_USER_DECISION",
    ]
    evidence: list[str] = Field(min_length=1)
    risk_triggers: list[
        Literal[
            "lease_expired",
            "human_unavailable",
            "contradiction",
            "ambiguity",
            "plateau_or_repeated_failure",
            "irreversible_or_high_cost",
            "safety_sensitive",
            "weak_proxy",
        ]
    ] = Field(default_factory=list)

    @model_validator(mode="after")
    def _kill_set_is_ranked_and_unique(self) -> "PreflightDecision":
        ids = [item.assumption_id for item in self.kill_set]
        if len(ids) != len(set(ids)):
            raise ValueError("kill_set assumption_id values must be unique")
        ranks = [
            (item.downstream_invalidation, item.uncertainty)
            for item in self.kill_set
        ]
        if ranks != sorted(ranks, reverse=True):
            raise ValueError("kill_set must rank invalidation, then uncertainty, descending")
        if len(self.risk_triggers) != len(set(self.risk_triggers)):
            raise ValueError("risk_triggers must be unique")
        return self


class BranchPlan(StrictModel):
    branch_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    source_classes: list[str] = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)
    counterevidence_target: str = Field(min_length=1)


class ResearchPlan(StrictModel):
    branches: list[BranchPlan] = Field(min_length=1, max_length=4)


class EvidenceRecord(StrictModel):
    evidence_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    kind: Literal["Fact", "Inference", "Extrapolation"]
    source: str = Field(min_length=1)
    supporting_location: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    contradiction_key: str | None = None


class BranchResult(StrictModel):
    branch_id: str = Field(min_length=1)
    records: list[EvidenceRecord] = Field(min_length=1)
    unanswered_claim_ids: list[str] = Field(default_factory=list)


class ResearchSynthesis(StrictModel):
    recommendation: str = Field(min_length=1)
    claim_coverage: dict[str, list[str]] = Field(min_length=1)
    contradictions: list[str] = Field(default_factory=list)
    open_gaps: list[str] = Field(default_factory=list)
    terminal: Literal["SUCCESS", "UNRESOLVED_WITH_GAPS"]


PREFLIGHT_PROMPT = """Act as the preflight and first-principles role for bounded Deep Research.
Lock the user's invariant outcome and hard constraints. Produce a complete kill set of unresolved
blockers and load-bearing assumptions, ranked by downstream invalidation then uncertainty. For
each give a falsifier and minimum valid check. Seek disconfirming evidence first. Return CONTINUE
only if every kill-set item has valid evidence and no blocker is refuted or untestable. Task:
"""

HUMAN_GUIDED_PREFLIGHT_PROMPT = """Act as the minimum-validity and risk-admission role for one
human-guided research cycle. Do not run broad novelty or interestingness debate for a low-risk
candidate. Freeze the user-aligned hypothesis, falsifier, measurement/comparator, reversibility,
authority, safety, and resource feasibility. Populate risk_triggers from the canonical vocabulary.
If any trigger applies, perform the targeted idea/method challenge needed for that trigger inside
this bounded turn before returning CONTINUE. Seek disconfirming evidence; never weaken validity.
Task:
"""

PLAN_PROMPT = """Plan bounded independent evidence branches for the task below. Branches must
have non-overlapping questions, explicit claim IDs, primary/official source classes where
available, and a counterevidence target. Return exactly the requested branch count. Do not write
files, change the workspace, publish, or ask for permission. Task and validated preflight:
"""

BRANCH_PROMPT = """Collect evidence for exactly this branch. Work read-only. Return auditable
records with canonical source, supporting location, claim ID, evidence kind, and contradiction
key. Preserve negative and conflicting evidence. Do not synthesize other branches, write files,
publish, or perform any external mutation. Contract:
"""

REDUCE_PROMPT = """Synthesize only the supplied structured evidence. Treat the ordering as
irrelevant; build a claim-to-evidence ledger deterministically from record IDs, retain explicit
contradictions and gaps, and do not invent sources. SUCCESS requires decision-relevant claim
coverage; otherwise return UNRESOLVED_WITH_GAPS. Evidence:
"""

REVIEW_PROMPT = """You are a fresh, read-only, different-provider terminal reviewer. Review the
entire current Deep Research artifact against these criteria: invariant contract retained; kill
set complete and uncertainty-first; every branch is represented; claim IDs link to auditable
evidence; contradictions and gaps are preserved; no external side effect was authorized; the
domain terminal matches the evidence. Return COMPLETE, REVISE, or HUMAN_BLOCKED. HUMAN_BLOCKED is
valid only for one exact human-only dependency after agent-side alternatives are exhausted.
Current artifact:
"""


ROLE_POLICY = {
    "preflight": "plan",
    "collector": "research_collect",
    "counterevidence": "research_collect",
    "reducer": "frontier_execute",
    "reviewer": "advisor",
}


def validate_startup(
    agents: Agents, task: str, setting: Config
) -> dict[str, object]:
    """Verify launcher-bound roles, identities, effort and permissions before any turn."""

    require_read_only(agents._asdict())
    return verify_launch(
        "deep-research", agents._asdict(), ROLE_POLICY, task, setting
    )


def _stop_payload(preflight: PreflightDecision) -> dict[str, object]:
    return {
        "schema": "research-terminal/v1",
        "flow": "deep-research",
        "domain_terminal": preflight.verdict,
        "core_problem": preflight.core_problem,
        "hard_constraints": preflight.hard_constraints,
        "preflight": preflight,
        "branch_plan": None,
        "branch_results": [],
        "claim_ledger": {},
        "synthesis": None,
    }


async def review_and_write(
    agents: Agents,
    budget: CallBudget,
    setting: Config,
    current: dict[str, object],
    launch: dict[str, object],
) -> None:
    review = await budget.aask(
        agents.reviewer,
        REVIEW_PROMPT + json_prompt(current),
        TerminalReview,
        "terminal-review",
    )
    write_terminal(
        setting.terminal_artifact,
        {
            **current,
            "terminal": reviewed_terminal(str(current["domain_terminal"]), review),
            "model_calls_used": budget.used,
            "terminal_review": review,
            "launch": launch,
        },
    )


@flow
async def run(agents: Agents, task: str, config: Config | None = None) -> None:
    """Run the trusted, bounded native Humanize2 Deep Research reference."""

    setting = config or Config()
    budget = CallBudget(setting.max_model_calls)
    launch: dict[str, object] | None = None
    try:
        launch = validate_startup(agents, task, setting)
        iteration = resolve_iteration_policy(setting)
        mode_prompt = iteration_prompt(
            iteration,
            setting.human_feedback,
            setting.human_feedback_checkpoint_artifact,
            setting.human_feedback_checkpoint_sha256,
        )
        if setting.cursor_diversity_required:
            unsupported_diversity_terminal(
                "deep-research", setting.terminal_artifact, launch
            )
            return

        preflight = await budget.aask(
            agents.preflight,
            (
                HUMAN_GUIDED_PREFLIGHT_PROMPT
                if iteration["effective_mode"] == "human_guided"
                else PREFLIGHT_PROMPT
            )
            + task
            + mode_prompt,
            PreflightDecision,
            "preflight",
        )
        if preflight.verdict != "CONTINUE":
            stopped = attach_iteration_result(
                _stop_payload(preflight),
                setting,
                iteration,
                hypothesis=preflight.kill_set[0].statement,
                actual_operation="ran the bounded preflight validity and falsification gate",
                raw_observation={
                    "verdict": preflight.verdict,
                    "evidence": preflight.evidence,
                },
                agent_interpretation=(
                    "the current candidate cannot continue under the recorded preflight evidence"
                ),
                anomaly_or_alternate_interpretation=preflight.evidence,
                candidate_next_tasks=[
                    "inspect the failed gate before revising or abandoning the candidate"
                ],
                risk_triggers=preflight.risk_triggers,
            )
            await review_and_write(
                agents,
                budget,
                setting,
                stopped,
                launch,
            )
            return

        plan = await budget.aask(
            agents.preflight,
            PLAN_PROMPT
            + json_prompt({"task": task, "preflight": preflight})
            + f"\nReturn exactly {setting.branch_count} branches."
            + mode_prompt,
            ResearchPlan,
            "branch-plan",
        )
        if len(plan.branches) != setting.branch_count:
            raise ValueError("branch plan did not return the configured number of branches")
        branch_ids = [branch.branch_id for branch in plan.branches]
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("branch_id values must be unique")

        turns = []
        for index, branch in enumerate(plan.branches):
            role = agents.counterevidence if index == 0 else agents.collector
            turns.append(
                budget.aask(
                    role,
                    BRANCH_PROMPT + json_prompt(branch) + mode_prompt,
                    BranchResult,
                    f"research-branch:{branch.branch_id}",
                )
            )
        results = list(await asyncio.gather(*turns))
        if [item.branch_id for item in results] != branch_ids:
            raise ValueError("branch results must preserve the planned branch IDs and order")

        records = sorted(
            [record for result in results for record in result.records],
            key=lambda record: (record.claim_id, record.source, record.evidence_id),
        )
        evidence_ids = [record.evidence_id for record in records]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be globally unique")
        claim_ledger: dict[str, list[str]] = {}
        for record in records:
            claim_ledger.setdefault(record.claim_id, []).append(record.evidence_id)

        synthesis = await budget.aask(
            agents.reducer,
            REDUCE_PROMPT
            + json_prompt(
                {
                    "task": task,
                    "preflight": preflight,
                    "branch_plan": plan,
                    "records_sorted_by_claim_source_id": records,
                    "unanswered": {
                        result.branch_id: result.unanswered_claim_ids for result in results
                    },
                }
            )
            + mode_prompt,
            ResearchSynthesis,
            "synthesis",
        )
        if synthesis.claim_coverage != claim_ledger:
            raise ValueError("synthesis claim_coverage must equal the controller claim ledger")

        current = attach_iteration_result(
            {
            "schema": "research-terminal/v1",
            "flow": "deep-research",
            "domain_terminal": synthesis.terminal,
            "core_problem": preflight.core_problem,
            "hard_constraints": preflight.hard_constraints,
            "preflight": preflight,
            "branch_plan": plan,
            "branch_results": results,
            "claim_ledger": claim_ledger,
            "synthesis": synthesis,
            },
            setting,
            iteration,
            hypothesis=preflight.kill_set[0].statement,
            actual_operation=(
                f"collected {len(results)} bounded evidence branches and reduced "
                f"{len(records)} evidence records into the claim ledger"
            ),
            raw_observation={
                "branch_results": results,
                "claim_ledger": claim_ledger,
            },
            agent_interpretation=synthesis.recommendation,
            anomaly_or_alternate_interpretation=(
                synthesis.contradictions or synthesis.open_gaps or ["none recorded"]
            ),
            candidate_next_tasks=(
                synthesis.open_gaps[:4]
                or ["inspect the reduced evidence and choose the next bounded subtask"]
            ),
            risk_triggers=preflight.risk_triggers,
        )
        await review_and_write(agents, budget, setting, current, launch)
    except Exception as error:
        write_failure_terminal(
            "deep-research",
            setting.terminal_artifact,
            error,
            budget.used,
            launch,
        )
