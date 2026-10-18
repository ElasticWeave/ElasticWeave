from __future__ import annotations

import importlib.util
import json
import math
import re
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "validate_design", HERE / "validate_design.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def valid_machine_gated_design() -> dict[str, object]:
    """Small static DAG exercising the neutral machine-gated contract."""
    return {
        "schema": "workflow-design/v1",
        "id": "static-review-gate",
        "entry": "seed",
        "admission": {
            "reasons": ["multi-stage", "machine-gated-review"],
            "demotionCondition": "No workspace-writing path requires an in-graph gate.",
        },
        "governance": {
            "reviewPolicy": "machine-gated",
            "completionProof": "state-bound",
            "malformedDecision": "unresolved",
        },
        "goal": {"objective": "Produce a reviewed draft.", "acceptance": "review PASS"},
        "budgets": {
            "maxModelCalls": 2,
            "maxReviewCycles": 1,
            "maxWallTimeSeconds": 300,
        },
        "requiredCapabilities": [
            "agent-invocation",
            "fresh-sessions",
            "stateful-continuation",
            "conditional-routing",
            "structured-output",
            "observability",
            "machine-gated-review",
            "state-bound-proof",
        ],
        "state": [
            {"name": "topic", "type": "string"},
            {"name": "draft", "type": "string"},
        ],
        "nodes": [
            {"id": "seed", "kind": "script", "reads": [], "writes": ["topic"]},
            {
                "id": "write-draft",
                "kind": "agent",
                "role": "task",
                "session": "state-mediated",
                "workspaceAccess": "write",
                "reads": ["topic"],
                "writes": ["draft"],
                "timeoutSeconds": 60,
            },
            {
                "id": "review",
                "kind": "decision",
                "role": "advisor",
                "session": "fresh",
                "workspaceAccess": "read",
                "reads": ["draft"],
                "writes": [],
                "outcomes": ["PASS", "BLOCK"],
                "reviews": ["write-draft"],
                "onMissing": "unresolved",
                "timeoutSeconds": 60,
            },
            {
                "id": "success",
                "kind": "terminal",
                "outcome": "success",
                "reads": ["draft"],
                "writes": [],
            },
            {
                "id": "unresolved",
                "kind": "terminal",
                "outcome": "unresolved",
                "reads": [],
                "writes": [],
            },
        ],
        "transitions": [
            {"from": "seed", "to": "write-draft"},
            {"from": "write-draft", "to": "review"},
            {
                "from": "review",
                "to": "success",
                "when": {"field": "review.verdict", "equals": "PASS"},
            },
            {
                "from": "review",
                "to": "unresolved",
                "when": {"field": "review.verdict", "equals": "BLOCK"},
            },
        ],
        "terminals": {"success": "success", "unresolved": "unresolved"},
    }


def exclusive_branch_join_design(*, wait_for: bool) -> dict[str, object]:
    join = {
        "id": "join",
        "kind": "reducer",
        "reads": ["leftValue", "rightValue"],
        "writes": ["combined"],
    }
    if wait_for:
        join["waitFor"] = ["left", "right"]
    return {
        "schema": "workflow-design/v1",
        "id": "exclusive-branch-join",
        "entry": "seed",
        "admission": {
            "reasons": ["multi-stage"],
            "demotionCondition": "No branch routing remains.",
        },
        "governance": {
            "reviewPolicy": "independent",
            "completionProof": "none",
            "malformedDecision": "unresolved",
        },
        "goal": {"objective": "Exercise branch state.", "acceptance": "join"},
        "budgets": {"maxModelCalls": 1, "maxWallTimeSeconds": 300},
        "requiredCapabilities": [
            "agent-invocation",
            "fresh-sessions",
            "conditional-routing",
            "structured-output",
            "observability",
            "fanout-fanin",
        ],
        "state": [
            {"name": "mode", "type": "string"},
            {"name": "leftValue", "type": "string"},
            {"name": "rightValue", "type": "string"},
            {"name": "combined", "type": "string"},
        ],
        "nodes": [
            {"id": "seed", "kind": "script", "reads": [], "writes": ["mode"]},
            {
                "id": "route",
                "kind": "decision",
                "role": "smol",
                "session": "fresh",
                "workspaceAccess": "read",
                "reads": ["mode"],
                "writes": [],
                "outcomes": ["LEFT", "RIGHT"],
                "onMissing": "unresolved",
                "timeoutSeconds": 60,
            },
            {
                "id": "left",
                "kind": "script",
                "reads": ["mode"],
                "writes": ["leftValue"],
            },
            {
                "id": "right",
                "kind": "script",
                "reads": ["mode"],
                "writes": ["rightValue"],
            },
            join,
            {
                "id": "success",
                "kind": "terminal",
                "outcome": "success",
                "reads": ["combined"],
                "writes": [],
            },
        ],
        "transitions": [
            {"from": "seed", "to": "route"},
            {
                "from": "route",
                "to": "left",
                "when": {"field": "route.verdict", "equals": "LEFT"},
            },
            {
                "from": "route",
                "to": "right",
                "when": {"field": "route.verdict", "equals": "RIGHT"},
            },
            {"from": "left", "to": "join"},
            {"from": "right", "to": "join"},
            {"from": "join", "to": "success"},
        ],
        "terminals": {"success": "success"},
    }


ITERATION_RISK_TRIGGERS = [
    "lease_expired",
    "human_unavailable",
    "contradiction",
    "ambiguity",
    "plateau_or_repeated_failure",
    "irreversible_or_high_cost",
    "safety_sensitive",
    "weak_proxy",
]

CHECKPOINT_FIELDS = [
    "checkpoint_id",
    "hypothesis",
    "actual_operation",
    "raw_observation",
    "agent_interpretation",
    "anomaly_or_alternate_interpretation",
    "candidate_next_tasks",
    "human_feedback",
]

DISPOSITION_VALUES = [
    "performed",
    "skipped_human_guided",
    "triggered_by_risk",
]


def iterative_design(mode: str) -> dict[str, object]:
    """Small neutral design exercising one complete iteration policy."""
    state = [
        {"name": "topic", "type": "string", "source": "input"},
        {
            "name": "idea_review_disposition",
            "type": "string",
            "allowedValues": DISPOSITION_VALUES,
        },
        {"name": "idea_review_evidence", "type": "object"},
        {"name": "idea_review_risk_triggers", "type": "array"},
        {
            "name": "observation_checkpoint",
            "type": "object",
            "requiredFields": CHECKPOINT_FIELDS,
        },
        {
            "name": "human_feedback",
            "type": "object",
            "requiredFields": ["checkpoint_id", "human_feedback", "received_at"],
            "source": "input",
        },
        {"name": "experiment_result", "type": "object"},
    ]
    policy = {
        "mode": mode,
        "ideaReviewDispositionState": "idea_review_disposition",
        "ideaReviewEvidenceState": "idea_review_evidence",
        "riskTriggerState": "idea_review_risk_triggers",
        "checkpointState": "observation_checkpoint",
        "resumeEvidenceState": "human_feedback",
        "reducerNode": "reduce-cycle",
        "riskTriggers": list(ITERATION_RISK_TRIGGERS),
    }
    seed_writes = ["topic", "idea_review_risk_triggers", "human_feedback"]

    if mode == "autonomous":
        policy["ideaReviewNode"] = "idea-review"
        nodes = [
            {"id": "seed", "kind": "script", "reads": [], "writes": seed_writes},
            {
                "id": "idea-review",
                "kind": "agent",
                "role": "advisor",
                "session": "fresh",
                "workspaceAccess": "read",
                "reads": ["topic", "idea_review_risk_triggers"],
                "writes": ["idea_review_disposition", "idea_review_evidence"],
                "timeoutSeconds": 60,
            },
            {
                "id": "experiment",
                "kind": "agent",
                "role": "task",
                "session": "fresh",
                "workspaceAccess": "read",
                "reads": ["topic", "idea_review_evidence"],
                "writes": ["experiment_result"],
                "timeoutSeconds": 60,
            },
            {
                "id": "reduce-cycle",
                "kind": "reducer",
                "reads": [
                    "experiment_result",
                    "idea_review_disposition",
                    "idea_review_evidence",
                    "idea_review_risk_triggers",
                ],
                "writes": ["observation_checkpoint"],
            },
            {
                "id": "success",
                "kind": "terminal",
                "outcome": "success",
                "reads": ["observation_checkpoint"],
                "writes": [],
            },
        ]
        transitions = [
            {"from": "seed", "to": "idea-review"},
            {"from": "idea-review", "to": "experiment"},
            {"from": "experiment", "to": "reduce-cycle"},
            {"from": "reduce-cycle", "to": "success"},
        ]
        terminals = {"success": "success"}
    else:
        state.extend(
            [
                {"name": "minimum_validity_evidence", "type": "object"},
                {
                    "name": "human_checkin_lease_expires_at",
                    "type": "string",
                    "source": "input",
                },
                {
                    "name": "human_checkin_lease_status",
                    "type": "string",
                    "allowedValues": ["live", "expired", "absent"],
                    "initial": "live",
                    "source": "input",
                },
                {
                    "name": "iteration_mode_transition",
                    "type": "object",
                    "requiredFields": [
                        "from_mode",
                        "to_mode",
                        "reason",
                        "recorded_at",
                    ],
                },
            ]
        )
        seed_writes.extend(
            ["human_checkin_lease_expires_at", "human_checkin_lease_status"]
        )
        policy.update(
            {
                "minimumValidityGateNode": "minimum-validity-gate",
                "minimumValidityEvidenceState": "minimum_validity_evidence",
                "riskReviewNode": "risk-review",
                "humanCheckinLeaseMinutes": 60,
                "leaseExpiryState": "human_checkin_lease_expires_at",
                "leaseStatusState": "human_checkin_lease_status",
                "humanUnavailableFallback": "pause",
                "fallbackRecordState": "iteration_mode_transition",
                "checkpointTerminal": "observation-checkpoint",
            }
        )
        nodes = [
            {"id": "seed", "kind": "script", "reads": [], "writes": seed_writes},
            {
                "id": "minimum-validity-gate",
                "kind": "decision",
                "role": "advisor",
                "session": "fresh",
                "workspaceAccess": "read",
                "reads": [
                    "topic",
                    "idea_review_risk_triggers",
                    "human_checkin_lease_expires_at",
                    "human_checkin_lease_status",
                ],
                "writes": [
                    "minimum_validity_evidence",
                    "idea_review_disposition",
                    "idea_review_evidence",
                    "iteration_mode_transition",
                ],
                "outcomes": ["PROCEED", "REVIEW"],
                "onMissing": "unresolved",
                "timeoutSeconds": 60,
            },
            {
                "id": "risk-review",
                "kind": "agent",
                "role": "advisor",
                "session": "fresh",
                "workspaceAccess": "read",
                "reads": [
                    "topic",
                    "minimum_validity_evidence",
                    "idea_review_risk_triggers",
                ],
                "writes": ["idea_review_disposition", "idea_review_evidence"],
                "timeoutSeconds": 60,
            },
            {
                "id": "experiment",
                "kind": "agent",
                "role": "task",
                "session": "fresh",
                "workspaceAccess": "read",
                "reads": [
                    "topic",
                    "minimum_validity_evidence",
                    "idea_review_evidence",
                ],
                "writes": ["experiment_result"],
                "timeoutSeconds": 60,
            },
            {
                "id": "reduce-cycle",
                "kind": "reducer",
                "reads": [
                    "experiment_result",
                    "minimum_validity_evidence",
                    "idea_review_disposition",
                    "idea_review_evidence",
                    "idea_review_risk_triggers",
                ],
                "writes": ["observation_checkpoint"],
            },
            {
                "id": "observation-checkpoint",
                "kind": "terminal",
                "outcome": "unresolved",
                "reads": ["observation_checkpoint"],
                "writes": [],
            },
        ]
        transitions = [
            {"from": "seed", "to": "minimum-validity-gate"},
            {
                "from": "minimum-validity-gate",
                "to": "experiment",
                "when": {
                    "field": "minimum-validity-gate.verdict",
                    "equals": "PROCEED",
                },
            },
            {
                "from": "minimum-validity-gate",
                "to": "risk-review",
                "when": {
                    "field": "minimum-validity-gate.verdict",
                    "equals": "REVIEW",
                },
            },
            {"from": "risk-review", "to": "experiment"},
            {"from": "experiment", "to": "reduce-cycle"},
            {"from": "reduce-cycle", "to": "observation-checkpoint"},
        ]
        terminals = {"unresolved": "observation-checkpoint"}

    return {
        "schema": "workflow-design/v1",
        "id": f"{mode.replace('_', '-')}-iteration",
        "entry": "seed",
        "admission": {
            "reasons": ["multi-stage", "lifecycle-boundary"],
            "demotionCondition": "No iterative lifecycle boundary remains.",
        },
        "governance": {
            "reviewPolicy": "independent",
            "completionProof": "none",
            "malformedDecision": "unresolved",
        },
        "goal": {"objective": "Run one evidence-bearing cycle.", "acceptance": "cycle reduced"},
        "budgets": {"maxModelCalls": 4},
        "requiredCapabilities": [
            "agent-invocation",
            "fresh-sessions",
            "structured-output",
            "observability",
            "fanout-fanin",
        ]
        + (["conditional-routing"] if mode == "human_guided" else []),
        "iterationPolicy": policy,
        "state": state,
        "nodes": nodes,
        "transitions": transitions,
        "terminals": terminals,
    }


class ValidateDesignTest(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_root = HERE.parents[3] / ".claude" / "skills"
        self.reference_design_root = HERE.parent / "references" / "designs"
        self.design_root = (
            self.skill_root
            / "subagent-workflow"
            / "assets"
            / "project"
            / ".workflow"
            / "designs"
        )
        self.document = valid_machine_gated_design()

    def test_static_machine_gated_design_is_valid_without_bounded_loops(self) -> None:
        self.assertNotIn("bounded-loops", self.document["requiredCapabilities"])
        self.assertEqual(MODULE.validate(self.document), [])

    def test_independent_review_is_the_non_graph_terminal_review_policy(self) -> None:
        document = exclusive_branch_join_design(wait_for=False)
        self.assertEqual(document["governance"]["reviewPolicy"], "independent")
        errors = MODULE.validate(document)
        self.assertFalse(any("reviewPolicy" in error for error in errors), errors)

    def test_legacy_none_review_policy_has_an_actionable_migration_error(self) -> None:
        self.document["governance"]["reviewPolicy"] = "none"
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "reviewPolicy 'none' is retired" in error
                and "top session" in error
                and "terminal reviewer" in error
                for error in errors
            ),
            errors,
        )

    def test_subagent_scheduler_smoke_design_is_valid(self) -> None:
        path = self.design_root / "subagent-scheduler-smoke.workflow.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(MODULE.validate(document, self.skill_root), [])

    def test_standard_round_design_is_valid(self) -> None:
        path = self.design_root / "standard-round.workflow.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(MODULE.validate(document, self.skill_root), [])

    def test_iteration_policy_is_optional_for_existing_noniterative_designs(self) -> None:
        self.assertNotIn("iterationPolicy", self.document)
        self.assertEqual(MODULE.validate(self.document), [])

    def test_stateful_or_cyclic_design_requires_iteration_policy(self) -> None:
        admitted = valid_machine_gated_design()
        admitted["admission"]["reasons"].append("stateful-loop")
        errors = MODULE.validate(admitted)
        self.assertTrue(any("must declare iterationPolicy" in error for error in errors), errors)

        cyclic = valid_machine_gated_design()
        cyclic["transitions"][3]["to"] = "write-draft"
        errors = MODULE.validate(cyclic)
        self.assertTrue(any("must declare iterationPolicy" in error for error in errors), errors)

    def test_valid_autonomous_and_human_guided_iteration_policies(self) -> None:
        for mode in ("autonomous", "human_guided"):
            with self.subTest(mode=mode):
                self.assertEqual(MODULE.validate(iterative_design(mode)), [])

    def test_iteration_mode_is_strictly_canonical(self) -> None:
        for mode in (
            "Fast",
            "fast",
            "human-guided",
            "HUMAN_GUIDED",
            "agent_loop",
            ["human_guided"],
        ):
            document = iterative_design("autonomous")
            document["iterationPolicy"]["mode"] = mode
            errors = MODULE.validate(document)
            self.assertTrue(any("iterationPolicy.mode" in error for error in errors), errors)

    def test_malformed_iteration_pointers_remain_validation_errors(self) -> None:
        for key in (
            "ideaReviewDispositionState",
            "reducerNode",
            "leaseStatusState",
            "humanUnavailableFallback",
        ):
            document = iterative_design("human_guided")
            document["iterationPolicy"][key] = ["malformed"]
            errors = MODULE.validate(document)
            self.assertTrue(any(key in error for error in errors), (key, errors))

    def test_iteration_policy_rejects_unknown_and_mode_inapplicable_keys(self) -> None:
        document = iterative_design("autonomous")
        document["iterationPolicy"]["humanCheckinLeaseMinutes"] = 60
        document["iterationPolicy"]["customHarness"] = "anything"
        errors = MODULE.validate(document)
        self.assertTrue(any("unsupported keys" in error for error in errors), errors)
        self.assertTrue(any("autonomous" in error and "lease" in error for error in errors), errors)

    def test_human_guided_policy_requires_a_bounded_lease_and_fallback(self) -> None:
        for key in (
            "humanCheckinLeaseMinutes",
            "leaseExpiryState",
            "leaseStatusState",
            "humanUnavailableFallback",
            "fallbackRecordState",
        ):
            document = iterative_design("human_guided")
            del document["iterationPolicy"][key]
            errors = MODULE.validate(document)
            self.assertTrue(any(key in error for error in errors), (key, errors))

        for value in (None, True, 0, 4, 1441, "60"):
            document = iterative_design("human_guided")
            document["iterationPolicy"]["humanCheckinLeaseMinutes"] = value
            errors = MODULE.validate(document)
            self.assertTrue(any("5 through 1440" in error for error in errors), (value, errors))

        document = iterative_design("human_guided")
        document["iterationPolicy"]["humanUnavailableFallback"] = "continue"
        errors = MODULE.validate(document)
        self.assertTrue(any("pause or autonomous_review" in error for error in errors), errors)

    def test_human_guided_policy_rejects_statically_expired_or_absent_lease(self) -> None:
        for status in ("expired", "absent"):
            document = iterative_design("human_guided")
            lease_status = next(
                field
                for field in document["state"]
                if field["name"] == "human_checkin_lease_status"
            )
            lease_status["initial"] = status
            errors = MODULE.validate(document)
            self.assertTrue(any("live lease" in error for error in errors), (status, errors))

    def test_iteration_policy_requires_complete_canonical_risk_triggers(self) -> None:
        for mutation in (
            lambda values: values.pop(),
            lambda values: values.append(values[0]),
            lambda values: values.append("Weak Proxy"),
        ):
            document = iterative_design("human_guided")
            mutation(document["iterationPolicy"]["riskTriggers"])
            errors = MODULE.validate(document)
            self.assertTrue(any("riskTriggers" in error for error in errors), errors)

        extended = iterative_design("human_guided")
        extended["iterationPolicy"]["riskTriggers"].append("regulatory_boundary")
        self.assertEqual(MODULE.validate(extended), [])

    def test_iteration_policy_requires_typed_disposition_checkpoint_and_resume_state(self) -> None:
        mutations = (
            ("idea_review_disposition", "allowedValues", ["performed"]),
            ("idea_review_risk_triggers", "type", "string"),
            ("observation_checkpoint", "requiredFields", CHECKPOINT_FIELDS[:-1]),
            ("human_feedback", "requiredFields", ["human_feedback"]),
        )
        for field_name, key, value in mutations:
            document = iterative_design("human_guided")
            field = next(item for item in document["state"] if item["name"] == field_name)
            field[key] = value
            errors = MODULE.validate(document)
            self.assertTrue(
                any(field_name in error or key in error for error in errors),
                (field_name, errors),
            )

    def test_iteration_state_pointers_have_distinct_semantics(self) -> None:
        document = iterative_design("autonomous")
        document["iterationPolicy"]["resumeEvidenceState"] = document[
            "iterationPolicy"
        ]["checkpointState"]
        errors = MODULE.validate(document)
        self.assertTrue(any("state pointers must be distinct" in error for error in errors), errors)

    def test_autonomous_reducer_cannot_bypass_idea_review(self) -> None:
        document = iterative_design("autonomous")
        document["transitions"].append({"from": "seed", "to": "experiment"})
        errors = MODULE.validate(document)
        self.assertTrue(any("bypass idea review" in error for error in errors), errors)

    def test_human_guided_reducer_cannot_bypass_minimum_validity_gate(self) -> None:
        document = iterative_design("human_guided")
        document["transitions"].append({"from": "seed", "to": "experiment"})
        errors = MODULE.validate(document)
        self.assertTrue(any("bypass minimum validity gate" in error for error in errors), errors)

    def test_human_guided_reduce_must_stop_before_another_model_turn(self) -> None:
        document = iterative_design("human_guided")
        document["transitions"][-1]["to"] = "risk-review"
        errors = MODULE.validate(document)
        self.assertTrue(
            any("after reducer" in error and "model" in error for error in errors),
            errors,
        )

    def test_human_guided_checkpoint_is_an_unresolved_terminal_after_reduction(self) -> None:
        document = iterative_design("human_guided")
        checkpoint = next(
            node for node in document["nodes"] if node["id"] == "observation-checkpoint"
        )
        checkpoint["outcome"] = "success"
        document["terminals"] = {"success": "observation-checkpoint"}
        errors = MODULE.validate(document)
        self.assertTrue(
            any(
                "checkpointTerminal" in error and "unresolved" in error
                for error in errors
            ),
            errors,
        )

    def test_human_guided_checkpoint_cannot_bypass_deterministic_reduction(self) -> None:
        document = iterative_design("human_guided")
        seed = next(node for node in document["nodes"] if node["id"] == "seed")
        seed["writes"].append("observation_checkpoint")
        document["transitions"].append(
            {"from": "seed", "to": "observation-checkpoint"}
        )
        errors = MODULE.validate(document)
        self.assertTrue(
            any("checkpoint cannot bypass deterministic reduction" in error for error in errors),
            errors,
        )

    def test_iteration_reducer_cannot_be_the_workflow_entry(self) -> None:
        for mode in ("autonomous", "human_guided"):
            document = iterative_design(mode)
            document["entry"] = document["iterationPolicy"]["reducerNode"]
            errors = MODULE.validate(document)
            self.assertTrue(
                any("reducerNode cannot be the workflow entry" in error for error in errors),
                (mode, errors),
            )

    def test_headless_iteration_policy_rejects_a_human_role_node(self) -> None:
        document = iterative_design("human_guided")
        next(node for node in document["nodes"] if node["id"] == "risk-review")["role"] = "human"
        errors = MODULE.validate(document)
        self.assertTrue(any("human node" in error for error in errors), errors)

    def test_iteration_authoring_surfaces_share_the_canonical_contract(self) -> None:
        skill_root = HERE.parent
        documents = {
            "build": (skill_root / "SKILL.md").read_text(encoding="utf-8"),
            "template": (
                skill_root / "templates/workflow-design-contract.md"
            ).read_text(encoding="utf-8"),
            "idea-board": (
                skill_root / "references/designs/idea-board-optimization.md"
            ).read_text(encoding="utf-8"),
            "writing-plans": (
                skill_root.parent / "writing-plans/SKILL.md"
            ).read_text(encoding="utf-8"),
            "rethink": (
                skill_root.parent / "rethink-workflow-design/SKILL.md"
            ).read_text(encoding="utf-8"),
        }
        for name, text in documents.items():
            with self.subTest(document=name):
                self.assertIn("`autonomous`", text)
                self.assertIn("`human_guided`", text)
        self.assertIn("**Iteration mode:**", documents["writing-plans"])
        self.assertIn("checkpointTerminal", documents["build"])
        self.assertIn("triggered_by_risk", documents["idea-board"])

    def test_skill_markdown_local_references_exist(self) -> None:
        skill_root = HERE.parent
        skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        declared = sorted(
            set(re.findall(r"`((?:references|templates)/[^`\s]+)`", skill_text))
        )
        missing = [relative for relative in declared if not (skill_root / relative).exists()]
        self.assertEqual([], missing, "SKILL.md declares missing local references")

    def test_mapping_report_docs_use_canonical_adapter_suffixes(self) -> None:
        skill_root = HERE.parent
        contract = (skill_root / "references/harness-contract.md").read_text(encoding="utf-8")
        documents = [
            contract,
            (skill_root.parent / "run-workflow/SKILL.md").read_text(encoding="utf-8"),
        ]
        for document in documents:
            self.assertNotIn("<design>.<harness>.md", document)
            self.assertIn("<design>.<adapter-suffix>.md", document)
        self.assertIn("| `subagent-scheduler` | `subagent` |", contract)
        self.assertIn("| `humanize` | `humanize` |", contract)

    def test_skill_contract_rejects_overlap_and_wrong_node_kind(self) -> None:
        self.document["nodes"][0]["skills"] = {
            "required": ["grind"],
            "onDemand": ["grind"],
        }
        errors = MODULE.validate(self.document, self.skill_root)
        self.assertTrue(any("only valid" in error for error in errors))
        self.assertTrue(any("same skills" in error for error in errors))

    def test_skill_contract_requires_existing_skill(self) -> None:
        self.document["nodes"][1]["skills"] = {
            "required": ["missing-skill"],
            "onDemand": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            errors = MODULE.validate(self.document, Path(directory))
        self.assertTrue(
            any("missing-skill" in error and "missing" in error for error in errors)
        )

    def test_skill_contract_rejects_invalid_name_and_unknown_key(self) -> None:
        self.document["nodes"][1]["skills"] = {
            "required": ["../escape"],
            "onDemand": [],
            "autoload": ["grind"],
        }
        errors = MODULE.validate(self.document, self.skill_root)
        self.assertTrue(any("hyphen-case" in error for error in errors))
        self.assertTrue(any("unsupported keys" in error for error in errors))

    def test_skill_contract_rejects_symlink_escape(self) -> None:
        self.document["nodes"][1]["skills"] = {
            "required": ["linked-skill"],
            "onDemand": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.md"
            outside.write_text("outside", encoding="utf-8")
            skill_root = root / "skills"
            skill = skill_root / "linked-skill"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").symlink_to(outside)
            errors = MODULE.validate(self.document, skill_root)
        self.assertTrue(any("resolves outside" in error for error in errors))

    def test_decision_requires_all_routes(self) -> None:
        self.document["transitions"] = [
            edge
            for edge in self.document["transitions"]
            if edge.get("when", {}).get("equals") != "BLOCK"
        ]
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any("BLOCK" in error and "exact verdict route" in error for error in errors)
        )

    def test_harness_specific_model_is_rejected(self) -> None:
        self.document["nodes"][1]["model"] = "remembered-latest"
        errors = MODULE.validate(self.document)
        self.assertTrue(any("model-specific" in error for error in errors))

    def test_reachable_cycle_must_have_terminal_path(self) -> None:
        for edge in self.document["transitions"]:
            if edge["from"] == "review":
                edge["to"] = "write-draft"
        errors = MODULE.validate(self.document)
        self.assertTrue(any("cannot reach a terminal" in error for error in errors))

    def test_required_capabilities_reject_duplicates_and_aliases(self) -> None:
        self.document["requiredCapabilities"].extend(
            ["observability", "persistent-sessions", "state-bound-completion"]
        )
        errors = MODULE.validate(self.document)
        self.assertTrue(any("contains duplicates" in error for error in errors))
        self.assertTrue(any("non-canonical" in error for error in errors))

    def test_graph_structure_requires_derived_capabilities(self) -> None:
        path = self.design_root / "subagent-scheduler-smoke.workflow.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        for capability in ("fanout-fanin", "conditional-routing"):
            with self.subTest(capability=capability):
                mutated = json.loads(json.dumps(document))
                mutated["requiredCapabilities"].remove(capability)
                errors = MODULE.validate(mutated, self.skill_root)
                self.assertTrue(
                    any(
                        "derived capabilities" in error and capability in error
                        for error in errors
                    ),
                    errors,
                )

    def test_structure_bound_capability_cannot_be_self_declared_without_structure(self) -> None:
        self.document["requiredCapabilities"].append("fanout-fanin")
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "contradictory structure-bound capabilities" in error
                and "fanout-fanin" in error
                for error in errors
            ),
            errors,
        )

    def test_wait_for_reducer_and_nondecision_fanout_derive_fanout_fanin(self) -> None:
        path = self.design_root / "subagent-scheduler-smoke.workflow.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        derived = MODULE.derive_required_capabilities(document)
        self.assertIn("fanout-fanin", derived)
        self.assertIn("conditional-routing", derived)
        self.assertIn("agent-invocation", derived)
        self.assertIn("fresh-sessions", derived)
        self.assertIn("structured-output", derived)

    def test_single_wait_for_dependency_requires_fanout_fanin(self) -> None:
        self.document["nodes"][2]["waitFor"] = ["write-draft"]
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "derived capabilities" in error and "fanout-fanin" in error
                for error in errors
            ),
            errors,
        )

    def test_cycle_requires_bounded_loop_capability(self) -> None:
        document = valid_machine_gated_design()
        document["transitions"][3]["to"] = "write-draft"
        errors = MODULE.validate(document)
        self.assertTrue(
            any(
                "derived capabilities" in error and "bounded-loops" in error
                for error in errors
            ),
            errors,
        )

    def test_node_skill_declaration_requires_loading_capability(self) -> None:
        self.document["nodes"][1]["skills"] = {
            "required": ["grind"],
            "onDemand": [],
        }
        errors = MODULE.validate(self.document, self.skill_root)
        self.assertTrue(
            any(
                "derived capabilities" in error and "node-skill-loading" in error
                for error in errors
            ),
            errors,
        )

    def test_state_mediated_session_requires_continuation_capability(self) -> None:
        self.document["requiredCapabilities"].remove("stateful-continuation")
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "derived capabilities" in error
                and "stateful-continuation" in error
                for error in errors
            ),
            errors,
        )

    def test_fresh_only_graph_rejects_stateful_continuation_claim(self) -> None:
        self.document["nodes"][1]["session"] = "fresh"
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "contradictory structure-bound capabilities" in error
                and "stateful-continuation" in error
                for error in errors
            ),
            errors,
        )

    def test_state_bound_governance_and_capability_must_match(self) -> None:
        for mutate in (
            lambda document: document["requiredCapabilities"].remove(
                "state-bound-proof"
            ),
            lambda document: document["governance"].update({"completionProof": "none"}),
        ):
            document = valid_machine_gated_design()
            mutate(document)
            errors = MODULE.validate(document)
            self.assertTrue(
                any(
                    "state-bound-proof must be declared together" in error
                    for error in errors
                )
            )

    def test_machine_gate_governance_and_capability_must_match(self) -> None:
        self.document["requiredCapabilities"].remove("machine-gated-review")
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "machine-gated-review must be declared together" in error
                for error in errors
            )
        )

    def test_state_bound_proof_is_not_valid_with_external_independent_review(self) -> None:
        self.document["governance"]["reviewPolicy"] = "independent"
        self.document["requiredCapabilities"].remove("machine-gated-review")
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "state-bound requires reviewPolicy machine-gated" in error
                and "outside the graph" in error
                for error in errors
            ),
            errors,
        )

    def test_wall_time_budget_is_optional_but_finite_and_positive_when_declared(self) -> None:
        document = valid_machine_gated_design()
        del document["budgets"]["maxWallTimeSeconds"]
        self.assertFalse(any("maxWallTimeSeconds" in error for error in MODULE.validate(document)))
        invalid_values = (None, 0, -1, "300", True, math.inf, math.nan)
        for value in invalid_values:
            document = valid_machine_gated_design()
            document["budgets"]["maxWallTimeSeconds"] = value
            errors = MODULE.validate(document)
            self.assertTrue(
                any(
                    "budgets.maxWallTimeSeconds must be finite and positive when declared" in error
                    for error in errors
                ),
                value,
            )

    def test_workspace_access_and_session_use_neutral_values(self) -> None:
        writer = self.document["nodes"][1]
        writer["workspaceAccess"] = "edit"
        writer["session"] = "persistent"
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any("workspaceAccess must be read or write" in error for error in errors)
        )
        self.assertTrue(
            any("session must be fresh or state-mediated" in error for error in errors)
        )

    def test_model_node_requires_explicit_neutral_session(self) -> None:
        del self.document["nodes"][1]["session"]
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "must declare a fresh or state-mediated session" in error
                for error in errors
            )
        )

    def test_model_nodes_require_positive_explicit_timeout(self) -> None:
        for timeout in (None, 0, -1, True, math.inf):
            document = valid_machine_gated_design()
            writer = document["nodes"][1]
            if timeout is None:
                del writer["timeoutSeconds"]
            else:
                writer["timeoutSeconds"] = timeout
            errors = MODULE.validate(document)
            self.assertTrue(
                any(
                    "positive timeoutSeconds" in error
                    or "timeoutSeconds must be positive" in error
                    for error in errors
                ),
                timeout,
            )

    def test_deterministic_pure_node_may_omit_timeout(self) -> None:
        seed = self.document["nodes"][0]
        self.assertNotIn("timeoutSeconds", seed)
        self.assertEqual(MODULE.validate(self.document), [])

    def test_declared_timeout_is_positive_even_on_script_node(self) -> None:
        self.document["nodes"][0]["timeoutSeconds"] = 0
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any("seed.timeoutSeconds must be positive" in error for error in errors)
        )

    def test_workspace_command_node_requires_explicit_timeout(self) -> None:
        seed = self.document["nodes"][0]
        seed["workspaceAccess"] = "read"
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "seed must declare a positive timeoutSeconds" in error
                for error in errors
            )
        )

    def test_review_coverage_rejects_duplicates_and_invalid_node_ids(self) -> None:
        for reviews in (["write-draft", "write-draft"], ["../write-draft"]):
            document = valid_machine_gated_design()
            document["nodes"][2]["reviews"] = reviews
            errors = MODULE.validate(document)
            self.assertTrue(
                any(
                    "reviews" in error
                    and ("duplicates" in error or "node-id list" in error)
                    for error in errors
                )
            )

    def test_review_coverage_references_only_workspace_writers(self) -> None:
        self.document["nodes"][2]["reviews"] = ["seed"]
        errors = MODULE.validate(self.document)
        self.assertTrue(any("references non-writer seed" in error for error in errors))

    def test_review_coverage_writer_must_be_an_ancestor(self) -> None:
        self.document["state"].append({"name": "lateDraft", "type": "string"})
        self.document["nodes"].insert(
            3,
            {
                "id": "late-writer",
                "kind": "agent",
                "role": "task",
                "session": "fresh",
                "workspaceAccess": "write",
                "reads": ["draft"],
                "writes": ["lateDraft"],
                "timeoutSeconds": 60,
            },
        )
        self.document["nodes"][2]["reviews"] = ["late-writer"]
        for edge in self.document["transitions"]:
            if (
                edge["from"] == "review"
                and edge.get("when", {}).get("equals") == "BLOCK"
            ):
                edge["to"] = "late-writer"
        self.document["transitions"].append({"from": "late-writer", "to": "unresolved"})
        errors = MODULE.validate(self.document)
        self.assertTrue(any("not an ancestor" in error for error in errors))

    def test_approval_decision_is_read_only_and_declares_coverage(self) -> None:
        review = self.document["nodes"][2]
        review["workspaceAccess"] = "write"
        review["reviews"] = []
        errors = MODULE.validate(self.document)
        self.assertTrue(any("must be read-only" in error for error in errors))
        self.assertTrue(
            any("must declare review coverage" in error for error in errors)
        )

    def test_nonapproval_decision_cannot_claim_review_coverage(self) -> None:
        review = self.document["nodes"][2]
        review["outcomes"] = ["BLOCK", "REVISE"]
        for edge, outcome in zip(
            [edge for edge in self.document["transitions"] if edge["from"] == "review"],
            review["outcomes"],
        ):
            edge["when"]["equals"] = outcome
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any("cannot declare review coverage" in error for error in errors)
        )

    def test_machine_gate_requires_exactly_one_exact_exit_per_outcome(self) -> None:
        variants = []

        missing = valid_machine_gated_design()
        missing["transitions"] = [
            edge
            for edge in missing["transitions"]
            if edge.get("when", {}).get("equals") != "BLOCK"
        ]
        variants.append(missing)

        duplicate = valid_machine_gated_design()
        duplicate["transitions"].append(
            {
                "from": "review",
                "to": "unresolved",
                "when": {"field": "review.verdict", "equals": "PASS"},
            }
        )
        variants.append(duplicate)

        unconditional = valid_machine_gated_design()
        unconditional["transitions"].append({"from": "review", "to": "success"})
        variants.append(unconditional)

        extra_predicate = valid_machine_gated_design()
        extra_predicate["transitions"][2]["when"]["extra"] = True
        variants.append(extra_predicate)

        unknown = valid_machine_gated_design()
        unknown["transitions"].append(
            {
                "from": "review",
                "to": "unresolved",
                "when": {"field": "review.verdict", "equals": "MAYBE"},
            }
        )
        variants.append(unknown)

        for document in variants:
            errors = MODULE.validate(document)
            self.assertTrue(
                any(
                    "exact verdict route" in error or "malformed" in error
                    for error in errors
                ),
                errors,
            )

    def test_malformed_outcomes_fail_as_validation_errors(self) -> None:
        self.document["nodes"][2]["outcomes"] = [["PASS"]]
        errors = MODULE.validate(self.document)
        self.assertTrue(any("must declare outcomes" in error for error in errors))

    def test_malformed_state_reads_remain_validation_errors(self) -> None:
        self.document["nodes"][1]["reads"] = None
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any("write-draft.reads must be a string list" in error for error in errors)
        )

    def test_node_ids_use_canonical_hyphen_case(self) -> None:
        self.document["nodes"][0]["id"] = "bad_node"
        errors = MODULE.validate(self.document)
        self.assertTrue(any("node id 'bad_node'" in error for error in errors))

    def test_entry_cannot_read_a_field_it_only_writes_after_start(self) -> None:
        self.document["nodes"][0]["reads"] = ["topic"]
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "node seed.reads field topic is not definitely assigned" in error
                for error in errors
            )
        )

    def test_node_write_does_not_initialize_its_own_read(self) -> None:
        self.document["nodes"][1]["reads"].append("draft")
        errors = MODULE.validate(self.document)
        self.assertTrue(
            any(
                "node write-draft.reads field draft is not definitely assigned" in error
                for error in errors
            )
        )

    def test_wait_for_fanin_combines_definite_writes_from_all_branches(self) -> None:
        path = self.design_root / "subagent-scheduler-smoke.workflow.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(MODULE.validate(document, self.skill_root), [])

    def test_join_without_wait_for_intersects_possible_predecessor_states(self) -> None:
        path = self.design_root / "subagent-scheduler-smoke.workflow.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        next(node for node in document["nodes"] if node["id"] == "merge").pop("waitFor")
        errors = MODULE.validate(document, self.skill_root)
        self.assertTrue(
            any(
                "merge.reads field summaryA is not definitely assigned" in error
                for error in errors
            )
        )
        self.assertTrue(
            any(
                "merge.reads field summaryB is not definitely assigned" in error
                for error in errors
            )
        )

    def test_mutually_exclusive_decision_branches_do_not_form_a_wait_barrier(
        self,
    ) -> None:
        errors = MODULE.validate(exclusive_branch_join_design(wait_for=True))
        self.assertTrue(
            any(
                "node join cannot become runnable" in error and "incompatible" in error
                for error in errors
            )
        )

    def test_mutually_exclusive_join_does_not_union_branch_writes(self) -> None:
        errors = MODULE.validate(exclusive_branch_join_design(wait_for=False))
        self.assertTrue(
            any(
                "join.reads field leftValue is not definitely assigned" in error
                for error in errors
            )
        )
        self.assertTrue(
            any(
                "join.reads field rightValue is not definitely assigned" in error
                for error in errors
            )
        )

    def test_waiting_activation_must_arrive_after_all_dependencies(self) -> None:
        path = self.design_root / "subagent-scheduler-smoke.workflow.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["transitions"] = [
            edge
            for edge in document["transitions"]
            if edge
            not in (
                {"from": "seed", "to": "scout-b"},
                {"from": "scout-b", "to": "merge"},
            )
        ]
        document["transitions"].extend(
            [
                {"from": "scout-a", "to": "scout-b"},
                {"from": "scout-b", "to": "unresolved"},
            ]
        )
        errors = MODULE.validate(document, self.skill_root)
        self.assertTrue(
            any("node merge cannot become runnable" in error for error in errors),
            errors,
        )

    def test_wait_for_rejects_unknown_duplicate_and_self_dependencies(self) -> None:
        for wait_for, expected in (
            (["missing"], "references unknown node missing"),
            (["seed", "seed"], "contains duplicates"),
            (["review"], "cannot reference itself"),
        ):
            document = valid_machine_gated_design()
            document["nodes"][2]["waitFor"] = wait_for
            errors = MODULE.validate(document)
            self.assertTrue(any(expected in error for error in errors), errors)

    def test_all_active_designs_pass_neutral_validation(self) -> None:
        for name in (
            "review-gated-loop.workflow.json",
            "standard-round.workflow.json",
            "subagent-long-node-probe.workflow.json",
            "subagent-scheduler-smoke.workflow.json",
        ):
            with self.subTest(name=name):
                document = json.loads((self.design_root / name).read_text(encoding="utf-8"))
                self.assertEqual(MODULE.validate(document, self.skill_root), [])

    def test_simplify_verify_reference_is_neutral_and_bounded(self) -> None:
        path = self.reference_design_root / "simplify-verify-loop.workflow.json"
        document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(MODULE.validate(document, self.skill_root), [])
        self.assertEqual(document["budgets"]["maxModelCalls"], 15)
        roles = {
            node["role"]
            for node in document["nodes"]
            if node.get("kind") in {"agent", "decision"}
        }
        self.assertEqual(
            roles,
            {
                "simplify-planner",
                "simplify-writer",
                "verify-correctness",
                "verify-maintainability",
                "verify-diversity",
            },
        )
        for round_number in range(1, 4):
            reducer = next(
                node
                for node in document["nodes"]
                if node["id"] == f"reduce-r{round_number}"
            )
            self.assertEqual(
                reducer["waitFor"],
                [
                    f"verify-correctness-r{round_number}",
                    f"verify-maintainability-r{round_number}",
                    f"verify-diversity-r{round_number}",
                ],
            )
        third_round_revise = [
            edge
            for edge in document["transitions"]
            if edge["from"] == "reduce-r3"
            and edge.get("when", {}).get("equals") == "REVISE"
        ]
        self.assertEqual(third_round_revise, [{
            "from": "reduce-r3",
            "to": "exhaust-rounds",
            "when": {"field": "roundStatus", "equals": "REVISE"},
        }])


if __name__ == "__main__":
    unittest.main()
