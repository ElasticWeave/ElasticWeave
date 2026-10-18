"""Trusted native Humanize2 reference for resource-gated Auto Research.

The flow first synthesizes knowledge, classifies experimental applicability, and computes a
deterministic READY | NOT_READY | INVALID resource verdict.  Only READY can reach the bounded
trial role.  NOT_READY emits an explicitly unexecuted plan.  The canonical research intent
remains the platform-neutral Deep Research contract, not this native source file.
"""

from __future__ import annotations

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
    require_workspace_writer,
    resolve_iteration_policy,
    reviewed_terminal,
    unsupported_diversity_terminal,
    verify_launch,
    write_failure_terminal,
    write_terminal,
)


class Agents(NamedTuple):
    """Semantic roles; launch-time policy resolves providers, models, and effort."""

    knowledge: AgentBase
    applicability: AgentBase
    experimenter: AgentBase
    reviewer: AgentBase


class ResourceDeclaration(StrictModel):
    """Explicit resources only; ambient hardware or credentials never count."""

    integrity: Literal["VALID", "INVALID"] = "VALID"
    invalid_reason: str | None = None
    compute_available: bool = False
    data_available: bool = False
    harness_available: bool = False
    budget_available: bool = False
    correctness_gate_available: bool = False
    isolation_available: bool = False
    authorized: bool = False
    resource_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _integrity_has_reason(self) -> "ResourceDeclaration":
        if self.integrity == "INVALID" and not self.invalid_reason:
            raise ValueError("an INVALID resource declaration requires invalid_reason")
        if self.integrity == "VALID" and self.invalid_reason:
            raise ValueError("invalid_reason is valid only when integrity is INVALID")
        return self


class Config(IterationConfig):
    max_trials: int = Field(default=2, ge=1, le=3)
    max_model_calls: int = Field(default=7, ge=3, le=8)
    terminal_artifact: str = Field(
        default=".humanize/artifacts/auto-research-terminal.json", min_length=1
    )
    cursor_diversity_required: bool = False
    resources: ResourceDeclaration | None = None

    @model_validator(mode="after")
    def _budget_covers_ready_path(self) -> "Config":
        required = self.max_trials + 4
        if self.max_model_calls < required:
            raise ValueError(
                f"max_model_calls must cover {required} static calls for this trial cap"
            )
        return self


class KnowledgeClaim(StrictModel):
    claim_id: str = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    material: bool
    experimental_candidate: bool
    observational_boundary: str = Field(min_length=1)


class KnowledgeSynthesis(StrictModel):
    core_problem: str = Field(min_length=1)
    claims: list[KnowledgeClaim] = Field(min_length=1)
    contradictions: list[str] = Field(default_factory=list)
    open_gaps: list[str] = Field(default_factory=list)


class ClaimDisposition(StrictModel):
    claim_id: str = Field(min_length=1)
    disposition: Literal["EXPERIMENTAL", "OBSERVATIONAL"]
    reason: str = Field(min_length=1)
    hypothesis_candidate: str | None = None
    observational_boundary: str | None = None

    @model_validator(mode="after")
    def _matching_payload(self) -> "ClaimDisposition":
        if self.disposition == "EXPERIMENTAL":
            if not self.hypothesis_candidate or self.observational_boundary:
                raise ValueError(
                    "EXPERIMENTAL requires a hypothesis candidate and no observational boundary"
                )
        elif not self.observational_boundary or self.hypothesis_candidate:
            raise ValueError(
                "OBSERVATIONAL requires a boundary and no hypothesis candidate"
            )
        return self


class ApplicabilityDecision(StrictModel):
    verdict: Literal["APPLICABLE", "NOT_APPLICABLE", "INVALID"]
    dispositions: list[ClaimDisposition] = Field(min_length=1)
    evidence: str = Field(min_length=1)
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
    def _verdict_matches_dispositions(self) -> "ApplicabilityDecision":
        experimental = any(
            item.disposition == "EXPERIMENTAL" for item in self.dispositions
        )
        if self.verdict == "APPLICABLE" and not experimental:
            raise ValueError("APPLICABLE requires at least one EXPERIMENTAL claim")
        if self.verdict == "NOT_APPLICABLE" and experimental:
            raise ValueError("NOT_APPLICABLE requires every claim to be OBSERVATIONAL")
        if len(self.risk_triggers) != len(set(self.risk_triggers)):
            raise ValueError("risk_triggers must be unique")
        return self


class TrialPlan(StrictModel):
    trial_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    uncertainty: int = Field(ge=1, le=5)
    downstream_invalidation: int = Field(ge=1, le=5)
    intervention: str = Field(min_length=1)
    control: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    falsifier: str = Field(min_length=1)
    budget: str = Field(min_length=1)
    stop_rule: str = Field(min_length=1)


class ExperimentContract(StrictModel):
    trials: list[TrialPlan] = Field(min_length=1, max_length=3)
    global_stop_rule: str = Field(min_length=1)
    correctness_gate: str = Field(min_length=1)
    isolation_rule: str = Field(min_length=1)

    @model_validator(mode="after")
    def _trials_are_ranked_and_unique(self) -> "ExperimentContract":
        ids = [item.trial_id for item in self.trials]
        if len(ids) != len(set(ids)):
            raise ValueError("trial_id values must be unique")
        ranks = [
            (item.downstream_invalidation, item.uncertainty) for item in self.trials
        ]
        if ranks != sorted(ranks, reverse=True):
            raise ValueError(
                "trials must rank downstream invalidation, then uncertainty, descending"
            )
        return self


class TrialResult(StrictModel):
    trial_id: str = Field(min_length=1)
    status: Literal[
        "SUPPORTED", "REFUTED", "INCONCLUSIVE", "INVALID", "STOP_BUDGET"
    ]
    intervention_result: str = Field(min_length=1)
    control_result: str = Field(min_length=1)
    correctness_gate_passed: bool
    falsifier_triggered: bool
    root_cause: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    budget_used: str = Field(min_length=1)


class ValidationPlan(StrictModel):
    status: Literal["NOT_EXECUTED_NOT_EXPERIMENTALLY_VALIDATED"]
    missing_resources: list[str] = Field(min_length=1)
    main_experiment: str = Field(min_length=1)
    baselines_and_controls: list[str] = Field(min_length=1)
    ablations: list[str] = Field(min_length=1)
    statistics: str = Field(min_length=1)
    correctness_and_safety_gates: list[str] = Field(min_length=1)
    resource_estimate: str = Field(min_length=1)
    execution_order: list[str] = Field(min_length=1)
    stopping_rules: list[str] = Field(min_length=1)
    outcome_to_decision: dict[str, str] = Field(min_length=1)
    reproducibility_artifacts: list[str] = Field(min_length=1)


KNOWLEDGE_PROMPT = """Produce an auditable knowledge synthesis for the task. Work read-only.
Every material claim needs evidence references, an observational falsifiability/update boundary,
and an explicit indication of whether a controlled intervention could reasonably test it.
Preserve contradictions and open gaps. Do not run an experiment, write files, or publish. Task:
"""

APPLICABILITY_PROMPT = """Classify every supplied claim exactly once as EXPERIMENTAL or
OBSERVATIONAL. EXPERIMENTAL requires a concrete hypothesis candidate; OBSERVATIONAL requires a
falsifiability/update boundary. Overall APPLICABLE means at least one experimental claim;
NOT_APPLICABLE means all are observational; INVALID means the supplied knowledge cannot support a
safe, coherent classification. Do not run or plan trials. Knowledge:
"""

HUMAN_GUIDED_APPLICABILITY_PROMPT = """Act as the minimum-validity and risk-admission role for one
human-guided Auto Research cycle. Classify every supplied claim exactly once as EXPERIMENTAL or
OBSERVATIONAL without broad novelty or interestingness debate. Before APPLICABLE, freeze a
user-aligned hypothesis, falsifier, measurement and comparator, reversibility, authority, safety,
and resource feasibility. Populate risk_triggers from the canonical vocabulary. If any trigger
applies, perform the targeted idea or method challenge needed for that trigger in this bounded
turn and record its evidence. NOT_APPLICABLE requires every claim to be observational; INVALID
means the supplied knowledge cannot support a safe, coherent classification. Do not run or plan
trials. Knowledge:
"""

CONTRACT_PROMPT = """Create a frozen bounded experiment contract only for EXPERIMENTAL claims.
Return no more than the stated trial cap. Order trials by downstream invalidation then uncertainty,
before easy confirmatory work. Every atomic trial needs intervention, real control, decision metric,
predeclared falsifier, local budget, and stop rule. Include global correctness and isolation gates.
Do not execute anything. Input:
"""

TRIAL_PROMPT = """Execute exactly this already-authorized atomic trial inside the declared local
resource and isolation envelope. Do not widen scope, acquire resources, use ambient credentials,
publish, or mutate external systems. Apply the correctness gate before interpreting a metric.
Compare the declared control, test the falsifier, record durable local evidence references, and
root-cause the observed movement. Stop at the trial budget or stop rule. Contract:
"""

PLAN_PROMPT = """Resources are NOT_READY. Run nothing: no baseline, smoke experiment, data
preprocessing, benchmark, or external mutation. Produce a complete plan explicitly marked
NOT_EXECUTED_NOT_EXPERIMENTALLY_VALIDATED, including main experiment, controls, ablations,
statistics, correctness/safety gates, resource estimate, dependency order, stop rules,
outcome-to-decision map, reproducibility artifacts, and every missing resource. Input:
"""

REVIEW_PROMPT = """You are a fresh, read-only, different-provider terminal reviewer. Review the
current Auto Research artifact. Verify: knowledge precedes applicability; every claim has exactly
one disposition; resource verdict is deterministic; only READY ran trials; NOT_READY is explicitly
unexecuted and complete; each executed trial has falsifier, control, budget, stop, correctness,
root cause, and evidence; no unauthorized external effect occurred; terminal meaning matches the
evidence. Return COMPLETE, REVISE, or HUMAN_BLOCKED. HUMAN_BLOCKED requires one exact human-only
dependency after alternatives are exhausted. Artifact:
"""


ROLE_POLICY = {
    "knowledge": "frontier_execute",
    "applicability": "plan",
    "experimenter": "task",
    "reviewer": "advisor",
}


def validate_startup(
    agents: Agents, task: str, setting: Config
) -> dict[str, object]:
    require_read_only(
        {
            "knowledge": agents.knowledge,
            "applicability": agents.applicability,
            "reviewer": agents.reviewer,
        }
    )
    require_workspace_writer("experimenter", agents.experimenter)
    return verify_launch(
        "auto-research", agents._asdict(), ROLE_POLICY, task, setting
    )


def resource_gate(
    declaration: ResourceDeclaration | None,
) -> tuple[Literal["READY", "NOT_READY", "INVALID"], list[str]]:
    """Compute readiness without a model and without consulting ambient resources."""

    if declaration is None:
        return "NOT_READY", ["resources declaration"]
    if declaration.integrity == "INVALID":
        return "INVALID", [declaration.invalid_reason or "invalid declaration"]
    checks = {
        "compute": declaration.compute_available,
        "data": declaration.data_available,
        "harness": declaration.harness_available,
        "budget": declaration.budget_available,
        "correctness gate": declaration.correctness_gate_available,
        "isolation": declaration.isolation_available,
        "authorization": declaration.authorized,
        "resource refs": bool(declaration.resource_refs),
    }
    missing = [name for name, ready in checks.items() if not ready]
    return ("NOT_READY", missing) if missing else ("READY", [])


def validate_applicability(
    knowledge: KnowledgeSynthesis, decision: ApplicabilityDecision
) -> None:
    expected = [claim.claim_id for claim in knowledge.claims]
    actual = [item.claim_id for item in decision.dispositions]
    if len(expected) != len(set(expected)):
        raise ValueError("knowledge claim_id values must be unique")
    if len(actual) != len(set(actual)) or set(actual) != set(expected):
        raise ValueError("applicability must classify every knowledge claim exactly once")


def iteration_artifact(
    setting: Config,
    iteration: dict[str, object],
    current: dict[str, object],
    knowledge: KnowledgeSynthesis,
    applicability: ApplicabilityDecision,
    *,
    actual_operation: str,
    raw_observation: object,
    agent_interpretation: str,
    candidate_next_tasks: list[str] | None = None,
) -> dict[str, object]:
    experimental = [
        item.hypothesis_candidate
        for item in applicability.dispositions
        if item.hypothesis_candidate
    ]
    hypothesis = experimental[0] if experimental else knowledge.claims[0].conclusion
    candidates = candidate_next_tasks or knowledge.open_gaps[:4] or [
        "inspect the reduced evidence and choose the next bounded subtask"
    ]
    return attach_iteration_result(
        current,
        setting,
        iteration,
        hypothesis=hypothesis,
        actual_operation=actual_operation,
        raw_observation=raw_observation,
        agent_interpretation=agent_interpretation,
        anomaly_or_alternate_interpretation=(
            knowledge.contradictions or knowledge.open_gaps or ["none recorded"]
        ),
        candidate_next_tasks=candidates,
        risk_triggers=applicability.risk_triggers,
    )


async def terminal_review(
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
    """Run the trusted, resource-gated native Humanize2 Auto Research reference."""

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
                "auto-research", setting.terminal_artifact, launch
            )
            return

        knowledge = await budget.aask(
            agents.knowledge,
            KNOWLEDGE_PROMPT + task + mode_prompt,
            KnowledgeSynthesis,
            "knowledge-synthesis",
        )
        applicability = await budget.aask(
            agents.applicability,
            (
                HUMAN_GUIDED_APPLICABILITY_PROMPT
                if iteration["effective_mode"] == "human_guided"
                else APPLICABILITY_PROMPT
            )
            + json_prompt(knowledge)
            + mode_prompt,
            ApplicabilityDecision,
            "applicability",
        )
        validate_applicability(knowledge, applicability)

        base: dict[str, object] = {
            "schema": "research-terminal/v1",
            "flow": "auto-research",
            "knowledge": knowledge,
            "applicability": applicability,
            "resource_verdict": None,
            "missing_or_invalid_resources": [],
            "experiment_contract": None,
            "trial_results": [],
            "validation_plan": None,
        }
        if applicability.verdict == "INVALID":
            current = iteration_artifact(
                setting,
                iteration,
                {**base, "domain_terminal": "UNRESOLVED_EXPERIMENTAL_VALIDATION"},
                knowledge,
                applicability,
                actual_operation="synthesized knowledge and rejected invalid applicability evidence",
                raw_observation={"knowledge": knowledge, "applicability": applicability},
                agent_interpretation=applicability.evidence,
            )
            await terminal_review(
                agents,
                budget,
                setting,
                current,
                launch,
            )
            return
        if applicability.verdict == "NOT_APPLICABLE":
            current = iteration_artifact(
                setting,
                iteration,
                {**base, "domain_terminal": "SUCCESS"},
                knowledge,
                applicability,
                actual_operation="synthesized knowledge and classified every claim as observational",
                raw_observation={"knowledge": knowledge, "applicability": applicability},
                agent_interpretation=applicability.evidence,
            )
            await terminal_review(
                agents,
                budget,
                setting,
                current,
                launch,
            )
            return

        resource_verdict, resource_gaps = resource_gate(setting.resources)
        gated = {
            **base,
            "resource_verdict": resource_verdict,
            "missing_or_invalid_resources": resource_gaps,
        }
        if resource_verdict == "INVALID":
            current = iteration_artifact(
                setting,
                iteration,
                {**gated, "domain_terminal": "UNRESOLVED_EXPERIMENTAL_VALIDATION"},
                knowledge,
                applicability,
                actual_operation="validated applicability and rejected an invalid resource declaration",
                raw_observation={
                    "resource_verdict": resource_verdict,
                    "resource_gaps": resource_gaps,
                },
                agent_interpretation="the declared experiment cannot run safely or validly",
            )
            await terminal_review(
                agents,
                budget,
                setting,
                current,
                launch,
            )
            return
        if resource_verdict == "NOT_READY":
            validation_plan = await budget.aask(
                agents.knowledge,
                PLAN_PROMPT
                + json_prompt(
                    {
                        "knowledge": knowledge,
                        "applicability": applicability,
                        "missing_resources": resource_gaps,
                    }
                )
                + mode_prompt,
                ValidationPlan,
                "unexecuted-validation-plan",
            )
            current = iteration_artifact(
                setting,
                iteration,
                {
                    **gated,
                    "domain_terminal": "SUCCESS_WITH_EXPERIMENT_PLAN",
                    "validation_plan": validation_plan,
                },
                knowledge,
                applicability,
                actual_operation="reduced evidence into an explicitly unexecuted validation plan",
                raw_observation={
                    "missing_resources": resource_gaps,
                    "validation_plan": validation_plan,
                },
                agent_interpretation=(
                    "the experiment is applicable but cannot run until the recorded resources exist"
                ),
                candidate_next_tasks=validation_plan.execution_order[:4],
            )
            await terminal_review(
                agents,
                budget,
                setting,
                current,
                launch,
            )
            return

        experiment_contract = await budget.aask(
            agents.knowledge,
            CONTRACT_PROMPT
            + json_prompt(
                {
                    "knowledge": knowledge,
                    "applicability": applicability,
                    "resource_refs": setting.resources.resource_refs
                    if setting.resources
                    else [],
                    "max_trials": setting.max_trials,
                }
            )
            + mode_prompt,
            ExperimentContract,
            "experiment-contract",
        )
        if len(experiment_contract.trials) > setting.max_trials:
            raise ValueError("experiment contract exceeded max_trials")
        experimental_claims = {
            item.claim_id
            for item in applicability.dispositions
            if item.disposition == "EXPERIMENTAL"
        }
        if any(
            trial.claim_id not in experimental_claims for trial in experiment_contract.trials
        ):
            raise ValueError("trial contract may target only EXPERIMENTAL claims")

        results: list[TrialResult] = []
        for trial in experiment_contract.trials:
            result = await budget.aask(
                agents.experimenter,
                TRIAL_PROMPT
                + json_prompt(
                    {
                        "trial": trial,
                        "global_stop_rule": experiment_contract.global_stop_rule,
                        "correctness_gate": experiment_contract.correctness_gate,
                        "isolation_rule": experiment_contract.isolation_rule,
                        "resource_refs": setting.resources.resource_refs
                        if setting.resources
                        else [],
                    }
                )
                + mode_prompt,
                TrialResult,
                f"trial:{trial.trial_id}",
            )
            if result.trial_id != trial.trial_id:
                raise ValueError("trial result must match the active trial_id")
            results.append(result)
            if result.status in {"INVALID", "STOP_BUDGET"}:
                break

        valid_terminal_results = bool(results) and all(
            result.correctness_gate_passed
            and result.status in {"SUPPORTED", "REFUTED"}
            for result in results
        )
        all_planned_ran = len(results) == len(experiment_contract.trials)
        domain_terminal = (
            "SUCCESS_EXPERIMENTALLY_CHECKED"
            if valid_terminal_results and all_planned_ran
            else "UNRESOLVED_EXPERIMENTAL_VALIDATION"
        )
        current = iteration_artifact(
            setting,
            iteration,
            {
                **gated,
                "domain_terminal": domain_terminal,
                "experiment_contract": experiment_contract,
                "trial_results": results,
            },
            knowledge,
            applicability,
            actual_operation=(
                f"executed {len(results)} of {len(experiment_contract.trials)} bounded trials "
                "under the frozen contract"
            ),
            raw_observation={
                "experiment_contract": experiment_contract,
                "trial_results": results,
            },
            agent_interpretation=(
                "all planned trials passed their correctness gates"
                if domain_terminal == "SUCCESS_EXPERIMENTALLY_CHECKED"
                else "the experiment remains unresolved under the frozen validity gates"
            ),
            candidate_next_tasks=[
                f"inspect trial {result.trial_id}: {result.status}" for result in results[:4]
            ]
            or ["inspect why no valid trial completed"],
        )
        await terminal_review(
            agents,
            budget,
            setting,
            current,
            launch,
        )
    except Exception as error:
        write_failure_terminal(
            "auto-research",
            setting.terminal_artifact,
            error,
            budget.used,
            launch,
        )
