#!/usr/bin/env python3
"""Validate the platform-neutral workflow-design/v1 JSON contract."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ID = re.compile(r"^[a-z][a-z0-9-]*$")
SKILL_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
NODE_KINDS = {"script", "agent", "decision", "reducer", "terminal"}
TERMINAL_OUTCOMES = {"success", "unresolved", "failure"}
REVIEW_POLICIES = {"independent", "machine-gated"}
COMPLETION_PROOFS = {"none", "state-bound"}
PROHIBITED_KEYS = {"model", "modelId", "harness", "harnessId", "harnessCommand"}
SESSION_MODES = {"fresh", "state-mediated"}
WORKSPACE_ACCESS_MODES = {"read", "write"}
APPROVAL_OUTCOMES = {"PASS", "APPROVE"}
ITERATION_MODES = {"autonomous", "human_guided"}
ITERATION_RISK_TRIGGER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
ITERATION_DISPOSITIONS = {
    "performed",
    "skipped_human_guided",
    "triggered_by_risk",
}
ITERATION_RISK_TRIGGERS = {
    "lease_expired",
    "human_unavailable",
    "contradiction",
    "ambiguity",
    "plateau_or_repeated_failure",
    "irreversible_or_high_cost",
    "safety_sensitive",
    "weak_proxy",
}
CHECKPOINT_REQUIRED_FIELDS = {
    "checkpoint_id",
    "hypothesis",
    "actual_operation",
    "raw_observation",
    "agent_interpretation",
    "anomaly_or_alternate_interpretation",
    "candidate_next_tasks",
    "human_feedback",
}
RESUME_EVIDENCE_REQUIRED_FIELDS = {
    "checkpoint_id",
    "human_feedback",
    "received_at",
}
FALLBACK_RECORD_REQUIRED_FIELDS = {
    "from_mode",
    "to_mode",
    "reason",
    "recorded_at",
}
ITERATION_POLICY_COMMON_KEYS = {
    "mode",
    "ideaReviewDispositionState",
    "ideaReviewEvidenceState",
    "riskTriggerState",
    "checkpointState",
    "resumeEvidenceState",
    "reducerNode",
    "riskTriggers",
}
ITERATION_POLICY_AUTONOMOUS_KEYS = ITERATION_POLICY_COMMON_KEYS | {
    "ideaReviewNode",
}
ITERATION_POLICY_HUMAN_GUIDED_KEYS = ITERATION_POLICY_COMMON_KEYS | {
    "minimumValidityGateNode",
    "minimumValidityEvidenceState",
    "riskReviewNode",
    "humanCheckinLeaseMinutes",
    "leaseExpiryState",
    "leaseStatusState",
    "humanUnavailableFallback",
    "fallbackRecordState",
    "checkpointTerminal",
}
CANONICAL_CAPABILITIES = {
    "agent-invocation",
    "bounded-loops",
    "conditional-routing",
    "durable-checkpoints",
    "fanout-fanin",
    "fresh-sessions",
    "interrupted-session-resume",
    "machine-gated-review",
    "node-skill-loading",
    "observability",
    "persistent-sessions",
    "state-bound-proof",
    "stateful-continuation",
    "structured-output",
}

STRUCTURE_BOUND_CAPABILITIES = {
    "agent-invocation",
    "bounded-loops",
    "conditional-routing",
    "fanout-fanin",
    "fresh-sessions",
    "machine-gated-review",
    "node-skill-loading",
    "state-bound-proof",
    "stateful-continuation",
    "structured-output",
}


def _is_nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def derive_required_capabilities(document: object) -> set[str]:
    """Derive requirements whose need is visible in a neutral design.

    A design may add requirements whose semantics live outside the graph (for
    example durable checkpoints or interrupted-session recovery), but it may not
    omit or falsely claim requirements that the graph and governance determine.
    The selector imports this function so validation and negotiation share one
    executable definition.
    """
    if not isinstance(document, dict):
        return set()

    derived = {"observability"}
    nodes = [node for node in document.get("nodes", []) if isinstance(node, dict)]
    transitions = [
        edge for edge in document.get("transitions", []) if isinstance(edge, dict)
    ]

    if any(node.get("kind") in {"agent", "decision"} for node in nodes):
        derived.update({"agent-invocation", "structured-output"})
    if any(node.get("session") == "fresh" for node in nodes):
        derived.add("fresh-sessions")
    if any(node.get("session") == "state-mediated" for node in nodes):
        derived.add("stateful-continuation")
    if any(isinstance(node.get("skills"), dict) for node in nodes):
        derived.add("node-skill-loading")
    if any(node.get("kind") == "decision" for node in nodes) or any(
        "when" in edge for edge in transitions
    ):
        derived.add("conditional-routing")

    outgoing_counts: dict[str, int] = defaultdict(int)
    unconditional_outgoing_counts: dict[str, int] = defaultdict(int)
    incoming_counts: dict[str, int] = defaultdict(int)
    adjacency: dict[str, set[str]] = defaultdict(set)
    node_ids = {
        str(node["id"])
        for node in nodes
        if isinstance(node.get("id"), str)
    }
    for edge in transitions:
        source, target = edge.get("from"), edge.get("to")
        if isinstance(source, str) and isinstance(target, str):
            outgoing_counts[source] += 1
            if "when" not in edge:
                unconditional_outgoing_counts[source] += 1
            incoming_counts[target] += 1
            if source in node_ids and target in node_ids:
                adjacency[source].add(target)

    has_fanout_fanin = any(node.get("kind") == "reducer" for node in nodes)
    has_fanout_fanin = has_fanout_fanin or any(
        isinstance(node.get("waitFor"), list) and bool(node["waitFor"])
        for node in nodes
    )
    has_fanout_fanin = has_fanout_fanin or any(
        node.get("kind") != "decision"
        and unconditional_outgoing_counts.get(str(node.get("id")), 0) >= 2
        for node in nodes
    )
    has_fanout_fanin = has_fanout_fanin or any(
        node.get("kind") == "reducer"
        and incoming_counts.get(str(node.get("id")), 0) >= 2
        for node in nodes
    )
    if has_fanout_fanin:
        derived.add("fanout-fanin")

    indegree = {node_id: 0 for node_id in node_ids}
    for successors in adjacency.values():
        for successor in successors:
            indegree[successor] += 1
    frontier = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited_count = 0
    while frontier:
        node_id = frontier.popleft()
        visited_count += 1
        for successor in sorted(adjacency[node_id]):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                frontier.append(successor)
    if visited_count != len(node_ids):
        derived.add("bounded-loops")

    governance = document.get("governance")
    if isinstance(governance, dict):
        if governance.get("reviewPolicy") == "machine-gated":
            derived.add("machine-gated-review")
        if governance.get("completionProof") == "state-bound":
            derived.add("state-bound-proof")
    return derived


def validate(document: object, skill_root: Path | None = None) -> list[str]:
    """Return validation errors for a decoded workflow design."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["root must be an object"]
    if document.get("schema") != "workflow-design/v1":
        errors.append("schema must equal workflow-design/v1")
    design_id = document.get("id")
    if not _is_nonempty(design_id) or not ID.fullmatch(str(design_id)):
        errors.append("id must be lower-case hyphen-case")
    if not _is_nonempty(document.get("entry")):
        errors.append("entry must name the first node")

    admission = document.get("admission")
    if not isinstance(admission, dict):
        errors.append("admission must be an object")
    else:
        reasons = admission.get("reasons")
        if (
            not isinstance(reasons, list)
            or not reasons
            or not all(_is_nonempty(reason) for reason in reasons)
        ):
            errors.append("admission.reasons must be a non-empty string list")
        if not _is_nonempty(admission.get("demotionCondition")):
            errors.append("admission.demotionCondition must be non-empty")

    governance = document.get("governance")
    if not isinstance(governance, dict):
        errors.append("governance must be an object")
    else:
        review_policy = governance.get("reviewPolicy")
        if review_policy == "none":
            errors.append(
                "governance.reviewPolicy 'none' is retired; migrate to 'independent' "
                "and require the top session to dispatch a terminal reviewer before stopping"
            )
        elif review_policy not in REVIEW_POLICIES:
            errors.append("governance.reviewPolicy is unsupported")
        if governance.get("completionProof") not in COMPLETION_PROOFS:
            errors.append("governance.completionProof is unsupported")
        if governance.get("malformedDecision") not in {"unresolved", "failure"}:
            errors.append("governance.malformedDecision must fail closed")

    goal = document.get("goal")
    if not isinstance(goal, dict) or not _is_nonempty(goal.get("objective")):
        errors.append("goal.objective must be non-empty")
    if not isinstance(goal, dict) or not _is_nonempty(goal.get("acceptance")):
        errors.append("goal.acceptance must be non-empty")

    capabilities = document.get("requiredCapabilities")
    capability_names: set[str] = set()
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(_is_nonempty(item) for item in capabilities)
    ):
        errors.append("requiredCapabilities must be a non-empty string list")
    else:
        capability_names = set(capabilities)
        if len(capability_names) != len(capabilities):
            errors.append("requiredCapabilities contains duplicates")
        unsupported_capabilities = sorted(capability_names - CANONICAL_CAPABILITIES)
        if unsupported_capabilities:
            errors.append(
                "requiredCapabilities contains non-canonical names "
                f"{unsupported_capabilities}"
            )

        derived_capabilities = derive_required_capabilities(document)
        missing_derived = sorted(derived_capabilities - capability_names)
        if missing_derived:
            errors.append(
                "requiredCapabilities omits derived capabilities "
                f"{missing_derived}"
            )
        contradictory = sorted(
            (capability_names & STRUCTURE_BOUND_CAPABILITIES)
            - derived_capabilities
        )
        if contradictory:
            errors.append(
                "requiredCapabilities declares contradictory structure-bound "
                f"capabilities {contradictory}"
            )

    if isinstance(governance, dict):
        state_bound = governance.get("completionProof") == "state-bound"
        declares_state_bound = "state-bound-proof" in capability_names
        if state_bound != declares_state_bound:
            errors.append(
                "governance.completionProof state-bound and required capability "
                "state-bound-proof must be declared together"
            )
        machine_gated = governance.get("reviewPolicy") == "machine-gated"
        declares_machine_gate = "machine-gated-review" in capability_names
        if machine_gated != declares_machine_gate:
            errors.append(
                "governance.reviewPolicy machine-gated and required capability "
                "machine-gated-review must be declared together"
            )
        if state_bound and not machine_gated:
            errors.append(
                "governance.completionProof state-bound requires reviewPolicy machine-gated; "
                "independent review completes outside the graph"
            )

    admission_reasons = (
        set(admission.get("reasons", []))
        if isinstance(admission, dict)
        and isinstance(admission.get("reasons"), list)
        and all(isinstance(reason, str) for reason in admission["reasons"])
        else set()
    )
    visibly_iterative = (
        "stateful-loop" in admission_reasons
        or "bounded-loops" in derive_required_capabilities(document)
    )
    if visibly_iterative and "iterationPolicy" not in document:
        errors.append(
            "iterative designs (stateful-loop admission or a graph cycle) must "
            "declare iterationPolicy explicitly"
        )

    budgets = document.get("budgets")
    if not isinstance(budgets, dict) or not isinstance(
        budgets.get("maxModelCalls"), int
    ):
        errors.append("budgets.maxModelCalls must be an integer")
    elif budgets["maxModelCalls"] < 1:
        errors.append("budgets.maxModelCalls must be positive")
    if isinstance(budgets, dict) and "maxWallTimeSeconds" in budgets:
        max_wall_time = budgets["maxWallTimeSeconds"]
        if (
            isinstance(max_wall_time, bool)
            or not isinstance(max_wall_time, (int, float))
            or not math.isfinite(max_wall_time)
            or max_wall_time <= 0
        ):
            errors.append("budgets.maxWallTimeSeconds must be finite and positive when declared")
    if (
        isinstance(governance, dict)
        and governance.get("reviewPolicy") == "machine-gated"
    ):
        review_cycles = (
            budgets.get("maxReviewCycles") if isinstance(budgets, dict) else None
        )
        if not isinstance(review_cycles, int) or not 1 <= review_cycles <= 3:
            errors.append(
                "machine-gated designs require maxReviewCycles from 1 through 3"
            )
        if governance.get("completionProof") != "state-bound":
            errors.append("machine-gated designs require state-bound completion proof")

    def find_prohibited(value: object, path: str = "root") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in PROHIBITED_KEYS:
                    errors.append(f"{path}.{key} is harness/model-specific")
                find_prohibited(nested, f"{path}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                find_prohibited(nested, f"{path}[{index}]")

    find_prohibited(document)

    state_names: set[str] = set()
    state_by_name: dict[str, dict[str, Any]] = {}
    state = document.get("state")
    if not isinstance(state, list):
        errors.append("state must be a list")
    else:
        for index, field in enumerate(state):
            if not isinstance(field, dict) or not _is_nonempty(field.get("name")):
                errors.append(f"state[{index}] must have a non-empty name")
                continue
            name = str(field["name"])
            if name in state_names:
                errors.append(f"duplicate state field: {name}")
            state_names.add(name)
            state_by_name[name] = field
            if not _is_nonempty(field.get("type")):
                errors.append(f"state field {name} must declare type")

    nodes = document.get("nodes")
    node_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
        nodes = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or not _is_nonempty(node.get("id")):
            errors.append(f"nodes[{index}] must have a non-empty id")
            continue
        node_id = str(node["id"])
        if not ID.fullmatch(node_id):
            errors.append(f"node id {node_id!r} must be lower-case hyphen-case")
        if node_id in node_by_id:
            errors.append(f"duplicate node id: {node_id}")
        node_by_id[node_id] = node
        if node.get("kind") not in NODE_KINDS:
            errors.append(f"node {node_id} has unsupported kind {node.get('kind')!r}")
        workspace_access = node.get("workspaceAccess")
        if "workspaceAccess" in node and workspace_access not in WORKSPACE_ACCESS_MODES:
            errors.append(f"node {node_id}.workspaceAccess must be read or write")
        session = node.get("session")
        if "session" in node and session not in SESSION_MODES:
            errors.append(f"node {node_id}.session must be fresh or state-mediated")
        if node.get("kind") in {"agent", "decision"} and session is None:
            errors.append(
                f"node {node_id} must declare a fresh or state-mediated session"
            )
        timeout = node.get("timeoutSeconds")
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            errors.append(f"node {node_id}.timeoutSeconds must be positive")
        if (
            node.get("kind") in {"agent", "decision"}
            or workspace_access in WORKSPACE_ACCESS_MODES
        ) and timeout is None:
            errors.append(f"node {node_id} must declare a positive timeoutSeconds")
        for key in ("reads", "writes"):
            fields = node.get(key, [])
            if not isinstance(fields, list) or not all(
                _is_nonempty(item) for item in fields
            ):
                errors.append(f"node {node_id}.{key} must be a string list")
                continue
            for field in fields:
                if field not in state_names:
                    errors.append(
                        f"node {node_id}.{key} references undeclared state {field}"
                    )
        wait_for = node.get("waitFor", [])
        if not isinstance(wait_for, list) or not all(
            isinstance(item, str) and ID.fullmatch(item) for item in wait_for
        ):
            errors.append(f"node {node_id}.waitFor must be a node-id list")
        elif len(wait_for) != len(set(wait_for)):
            errors.append(f"node {node_id}.waitFor contains duplicates")
        if node.get("kind") in {"agent", "decision"} and not _is_nonempty(
            node.get("role")
        ):
            errors.append(f"node {node_id} must declare role")
        skills = node.get("skills")
        if skills is not None:
            if node.get("kind") not in {"agent", "decision"}:
                errors.append(
                    f"node {node_id}.skills is only valid for agent or decision nodes"
                )
            if not isinstance(skills, dict):
                errors.append(f"node {node_id}.skills must be an object")
            else:
                unknown = sorted(set(skills) - {"required", "onDemand"})
                if unknown:
                    errors.append(
                        f"node {node_id}.skills has unsupported keys {unknown}"
                    )
                declared: dict[str, list[str]] = {}
                for mode in ("required", "onDemand"):
                    values = skills.get(mode, [])
                    if not isinstance(values, list) or not all(
                        isinstance(value, str) and SKILL_NAME.fullmatch(value)
                        for value in values
                    ):
                        errors.append(
                            f"node {node_id}.skills.{mode} must be a hyphen-case string list"
                        )
                        continue
                    declared[mode] = values
                    if len(values) != len(set(values)):
                        errors.append(
                            f"node {node_id}.skills.{mode} contains duplicates"
                        )
                    if skill_root is not None:
                        for name in values:
                            source = skill_root / name / "SKILL.md"
                            if not source.is_file():
                                errors.append(
                                    f"node {node_id}.skills.{mode} references missing {source}"
                                )
                            elif not source.resolve().is_relative_to(
                                skill_root.resolve()
                            ):
                                errors.append(
                                    f"node {node_id}.skills.{mode} resolves outside {skill_root}: {name}"
                                )
                if not any(declared.values()):
                    errors.append(
                        f"node {node_id}.skills must declare at least one skill"
                    )
                overlap = sorted(
                    set(declared.get("required", []))
                    & set(declared.get("onDemand", []))
                )
                if overlap:
                    errors.append(
                        f"node {node_id}.skills cannot require and defer the same skills {overlap}"
                    )
        if node.get("kind") == "decision":
            outcomes = node.get("outcomes")
            if (
                not isinstance(outcomes, list)
                or not outcomes
                or not all(_is_nonempty(item) for item in outcomes)
            ):
                errors.append(f"decision node {node_id} must declare outcomes")
            elif len(outcomes) != len(set(outcomes)):
                errors.append(f"decision node {node_id}.outcomes contains duplicates")
            if node.get("onMissing") not in {"unresolved", "failure"}:
                errors.append(f"decision node {node_id} must fail closed via onMissing")
        reviews = node.get("reviews")
        if reviews is not None:
            if node.get("kind") != "decision":
                errors.append(
                    f"node {node_id}.reviews is only valid for decision nodes"
                )
            if not isinstance(reviews, list) or not all(
                isinstance(item, str) and ID.fullmatch(item) for item in reviews
            ):
                errors.append(f"decision node {node_id}.reviews must be a node-id list")
            elif len(reviews) != len(set(reviews)):
                errors.append(f"decision node {node_id}.reviews contains duplicates")
        if (
            node.get("kind") == "terminal"
            and node.get("outcome") not in TERMINAL_OUTCOMES
        ):
            errors.append(f"terminal node {node_id} must declare a supported outcome")

    entry = document.get("entry")
    if _is_nonempty(entry) and entry not in node_by_id:
        errors.append(f"entry references unknown node {entry}")

    transitions = document.get("transitions")
    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not isinstance(transitions, list):
        errors.append("transitions must be a list")
        transitions = []
    for index, edge in enumerate(transitions):
        if not isinstance(edge, dict):
            errors.append(f"transitions[{index}] must be an object")
            continue
        source, target = edge.get("from"), edge.get("to")
        if source not in node_by_id:
            errors.append(f"transition {index} has unknown source {source!r}")
        if target not in node_by_id:
            errors.append(f"transition {index} has unknown target {target!r}")
        if source in node_by_id and target in node_by_id:
            outgoing[str(source)].append(edge)
            incoming[str(target)].append(edge)

    for node_id, node in node_by_id.items():
        wait_for = node.get("waitFor", [])
        if not isinstance(wait_for, list):
            continue
        for dependency in wait_for:
            if not isinstance(dependency, str):
                continue
            if dependency not in node_by_id:
                errors.append(
                    f"node {node_id}.waitFor references unknown node {dependency}"
                )
            elif dependency == node_id:
                errors.append(f"node {node_id}.waitFor cannot reference itself")

    def exact_decision_outcome(edge: dict[str, Any], node_id: str) -> str | None:
        condition = edge.get("when")
        if not isinstance(condition, dict) or set(condition) != {"field", "equals"}:
            return None
        if condition.get("field") != f"{node_id}.verdict":
            return None
        outcome = condition.get("equals")
        return outcome if isinstance(outcome, str) else None

    def reaches(source: str, target: str) -> bool:
        seen: set[str] = set()
        queue = deque([source])
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            for edge in outgoing[current]:
                successor = str(edge["to"])
                if successor == target:
                    return True
                if successor not in seen:
                    queue.append(successor)
        return False

    def path_exists_avoiding(source: str, target: str, avoided: str) -> bool:
        if source == avoided:
            return False
        seen = {avoided}
        queue = deque([source])
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            if current == target:
                return True
            seen.add(current)
            for edge in outgoing[current]:
                successor = str(edge["to"])
                if successor not in seen:
                    queue.append(successor)
        return False

    def descendants(source: str) -> set[str]:
        found: set[str] = set()
        queue = deque(str(edge["to"]) for edge in outgoing[source])
        while queue:
            current = queue.popleft()
            if current in found:
                continue
            found.add(current)
            queue.extend(str(edge["to"]) for edge in outgoing[current])
        return found

    def validate_iteration_policy() -> None:
        policy = document.get("iterationPolicy")
        if policy is None:
            return
        if not isinstance(policy, dict):
            errors.append("iterationPolicy must be an object")
            return

        raw_mode = policy.get("mode")
        mode = raw_mode if isinstance(raw_mode, str) else None
        if mode not in ITERATION_MODES:
            errors.append(
                "iterationPolicy.mode must be exactly autonomous or human_guided"
            )

        all_known_keys = (
            ITERATION_POLICY_AUTONOMOUS_KEYS
            | ITERATION_POLICY_HUMAN_GUIDED_KEYS
        )
        unknown_keys = sorted(set(policy) - all_known_keys)
        if unknown_keys:
            errors.append(
                f"iterationPolicy has unsupported keys {unknown_keys}"
            )

        applicable_keys = (
            ITERATION_POLICY_AUTONOMOUS_KEYS
            if mode == "autonomous"
            else ITERATION_POLICY_HUMAN_GUIDED_KEYS
            if mode == "human_guided"
            else ITERATION_POLICY_COMMON_KEYS
        )
        inapplicable_keys = sorted((set(policy) & all_known_keys) - applicable_keys)
        if inapplicable_keys:
            label = str(mode) if mode in ITERATION_MODES else "unknown mode"
            errors.append(
                f"iterationPolicy {label} has inapplicable keys {inapplicable_keys}; "
                "autonomous mode has no human lease"
            )
        missing_keys = sorted(applicable_keys - set(policy))
        for key in missing_keys:
            errors.append(f"iterationPolicy.{key} is required for {mode}")

        triggers = policy.get("riskTriggers")
        if (
            not isinstance(triggers, list)
            or not all(_is_nonempty(trigger) for trigger in triggers)
            or len(triggers) != len(set(triggers))
            or not all(ITERATION_RISK_TRIGGER_NAME.fullmatch(trigger) for trigger in triggers)
            or not ITERATION_RISK_TRIGGERS.issubset(set(triggers))
        ):
            errors.append(
                "iterationPolicy.riskTriggers must contain every canonical risk "
                "trigger, use lower-case snake-case names, and contain no duplicates"
            )

        def state_pointer(
            key: str,
            *,
            expected_type: str,
            required_fields: set[str] | None = None,
            allowed_values: set[str] | None = None,
        ) -> dict[str, Any] | None:
            name = policy.get(key)
            if not _is_nonempty(name):
                if key not in missing_keys:
                    errors.append(f"iterationPolicy.{key} must name a state field")
                return None
            field = state_by_name.get(str(name))
            if field is None:
                errors.append(
                    f"iterationPolicy.{key} references undeclared state {name}"
                )
                return None
            if field.get("type") != expected_type:
                errors.append(
                    f"iterationPolicy.{key} state {name} must have type {expected_type}"
                )
            if required_fields is not None:
                declared = field.get("requiredFields")
                if (
                    not isinstance(declared, list)
                    or not all(_is_nonempty(item) for item in declared)
                    or len(declared) != len(set(declared))
                    or not required_fields.issubset(set(declared))
                ):
                    errors.append(
                        f"iterationPolicy.{key} state {name}.requiredFields must "
                        f"include {sorted(required_fields)}"
                    )
            if allowed_values is not None:
                declared = field.get("allowedValues")
                if (
                    not isinstance(declared, list)
                    or not all(_is_nonempty(item) for item in declared)
                    or len(declared) != len(set(declared))
                    or set(declared) != allowed_values
                ):
                    errors.append(
                        f"iterationPolicy.{key} state {name}.allowedValues must be "
                        f"exactly {sorted(allowed_values)}"
                    )
            return field

        disposition_field = state_pointer(
            "ideaReviewDispositionState",
            expected_type="string",
            allowed_values=ITERATION_DISPOSITIONS,
        )
        del disposition_field
        state_pointer("ideaReviewEvidenceState", expected_type="object")
        state_pointer("riskTriggerState", expected_type="array")
        state_pointer(
            "checkpointState",
            expected_type="object",
            required_fields=CHECKPOINT_REQUIRED_FIELDS,
        )
        state_pointer(
            "resumeEvidenceState",
            expected_type="object",
            required_fields=RESUME_EVIDENCE_REQUIRED_FIELDS,
        )

        def node_pointer(
            key: str, allowed_kinds: set[str]
        ) -> tuple[str, dict[str, Any]] | None:
            node_id = policy.get(key)
            if not _is_nonempty(node_id):
                if key not in missing_keys:
                    errors.append(f"iterationPolicy.{key} must name a node")
                return None
            node = node_by_id.get(str(node_id))
            if node is None:
                errors.append(
                    f"iterationPolicy.{key} references unknown node {node_id}"
                )
                return None
            if node.get("kind") not in allowed_kinds:
                errors.append(
                    f"iterationPolicy.{key} node {node_id} must have kind in "
                    f"{sorted(allowed_kinds)}"
                )
            return str(node_id), node

        reducer_ref = node_pointer("reducerNode", {"reducer", "script"})
        def pointer_name(key: str) -> str | None:
            value = policy.get(key)
            return str(value) if _is_nonempty(value) else None

        def declared_names(node: dict[str, Any], key: str) -> set[str]:
            value = node.get(key, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                return set()
            return set(value)

        disposition_name = pointer_name("ideaReviewDispositionState")
        evidence_name = pointer_name("ideaReviewEvidenceState")
        risk_name = pointer_name("riskTriggerState")
        checkpoint_name = pointer_name("checkpointState")
        common_pointer_names = [
            disposition_name,
            evidence_name,
            risk_name,
            checkpoint_name,
            pointer_name("resumeEvidenceState"),
        ]
        declared_common_pointers = [
            name for name in common_pointer_names if name is not None
        ]
        if len(declared_common_pointers) != len(set(declared_common_pointers)):
            errors.append(
                "iterationPolicy state pointers must be distinct; disposition, "
                "evidence, risks, checkpoint, and resume evidence have different semantics"
            )
        common_reducer_reads = {disposition_name, evidence_name, risk_name}
        if reducer_ref is not None:
            _, reducer = reducer_ref
            reducer_reads = declared_names(reducer, "reads")
            if not common_reducer_reads.issubset(reducer_reads):
                errors.append(
                    "iterationPolicy.reducerNode must read idea-review disposition, "
                    "evidence, and risk-trigger state"
                )
            if checkpoint_name not in declared_names(reducer, "writes"):
                errors.append(
                    "iterationPolicy.reducerNode must write checkpointState after "
                    "deterministic reduction"
                )

        for node_id, node in node_by_id.items():
            if str(node.get("role", "")).strip().lower() in {"human", "user"}:
                errors.append(
                    f"iterationPolicy headless graph cannot contain human node {node_id}"
                )

        entry_id = str(document.get("entry", ""))
        reducer_id = reducer_ref[0] if reducer_ref is not None else ""
        if reducer_ref is not None and entry_id == reducer_id:
            errors.append(
                "iterationPolicy.reducerNode cannot be the workflow entry; "
                "admission and review state must be established first"
            )
        if mode == "autonomous":
            review_ref = node_pointer("ideaReviewNode", {"agent", "decision"})
            if review_ref is not None:
                review_id, review = review_ref
                review_writes = declared_names(review, "writes")
                required_writes = {disposition_name, evidence_name}
                if not required_writes.issubset(review_writes):
                    errors.append(
                        "iterationPolicy.ideaReviewNode must write idea-review "
                        "disposition and evidence"
                    )
                if reducer_ref is not None and not reaches(review_id, reducer_id):
                    errors.append(
                        "iterationPolicy autonomous idea review must precede reducer"
                    )
                if (
                    reducer_ref is not None
                    and entry_id in node_by_id
                    and path_exists_avoiding(entry_id, reducer_id, review_id)
                ):
                    errors.append(
                        "iterationPolicy autonomous route cannot bypass idea review"
                    )

        if mode != "human_guided":
            return

        lease_minutes = policy.get("humanCheckinLeaseMinutes")
        if (
            isinstance(lease_minutes, bool)
            or not isinstance(lease_minutes, int)
            or not 5 <= lease_minutes <= 1440
        ):
            errors.append(
                "iterationPolicy.humanCheckinLeaseMinutes must be an integer from "
                "5 through 1440"
            )
        fallback = policy.get("humanUnavailableFallback")
        if not isinstance(fallback, str) or fallback not in {
            "pause",
            "autonomous_review",
        }:
            errors.append(
                "iterationPolicy.humanUnavailableFallback must be pause or "
                "autonomous_review"
            )

        state_pointer("leaseExpiryState", expected_type="string")
        lease_status = state_pointer(
            "leaseStatusState",
            expected_type="string",
            allowed_values={"live", "expired", "absent"},
        )
        initial_lease_status = (
            lease_status.get("initial") if lease_status is not None else None
        )
        if isinstance(initial_lease_status, str) and initial_lease_status in {
            "expired",
            "absent",
        }:
            errors.append(
                "iterationPolicy human_guided requires a live lease at entry; "
                "expired or absent state must take the configured fallback"
            )
        state_pointer(
            "fallbackRecordState",
            expected_type="object",
            required_fields=FALLBACK_RECORD_REQUIRED_FIELDS,
        )
        state_pointer("minimumValidityEvidenceState", expected_type="object")

        gate_ref = node_pointer("minimumValidityGateNode", {"decision", "script"})
        risk_review_ref = node_pointer("riskReviewNode", {"agent", "decision"})
        checkpoint_ref = node_pointer("checkpointTerminal", {"terminal"})
        minimum_evidence_name = pointer_name("minimumValidityEvidenceState")
        lease_expiry_name = pointer_name("leaseExpiryState")
        lease_status_name = pointer_name("leaseStatusState")
        fallback_record_name = pointer_name("fallbackRecordState")

        if gate_ref is not None:
            gate_id, gate = gate_ref
            required_reads = {risk_name, lease_expiry_name, lease_status_name}
            required_writes = {
                minimum_evidence_name,
                disposition_name,
                evidence_name,
                fallback_record_name,
            }
            if not required_reads.issubset(declared_names(gate, "reads")):
                errors.append(
                    "iterationPolicy.minimumValidityGateNode must read risk and "
                    "lease state"
                )
            if not required_writes.issubset(declared_names(gate, "writes")):
                errors.append(
                    "iterationPolicy.minimumValidityGateNode must write validity, "
                    "disposition, evidence, and fallback state"
                )
            if (
                reducer_ref is not None
                and entry_id in node_by_id
                and path_exists_avoiding(entry_id, reducer_id, gate_id)
            ):
                errors.append(
                    "iterationPolicy human_guided route cannot bypass minimum "
                    "validity gate"
                )

        if risk_review_ref is not None:
            risk_review_id, risk_review = risk_review_ref
            if not {disposition_name, evidence_name}.issubset(
                declared_names(risk_review, "writes")
            ):
                errors.append(
                    "iterationPolicy.riskReviewNode must write triggered review "
                    "disposition and evidence"
                )
            if not {risk_name, minimum_evidence_name}.issubset(
                declared_names(risk_review, "reads")
            ):
                errors.append(
                    "iterationPolicy.riskReviewNode must read risk triggers and "
                    "minimum validity evidence"
                )
            if gate_ref is not None and not reaches(gate_ref[0], risk_review_id):
                errors.append(
                    "iterationPolicy risk review must be reachable from the "
                    "minimum validity gate"
                )
            if reducer_ref is not None and not reaches(risk_review_id, reducer_id):
                errors.append(
                    "iterationPolicy risk review must precede deterministic reduction"
                )

        if reducer_ref is not None and minimum_evidence_name not in declared_names(
            reducer_ref[1], "reads"
        ):
            errors.append(
                "iterationPolicy human_guided reducer must read minimum validity evidence"
            )

        if checkpoint_ref is not None:
            checkpoint_id, checkpoint = checkpoint_ref
            if checkpoint.get("outcome") != "unresolved":
                errors.append(
                    "iterationPolicy.checkpointTerminal must be an unresolved "
                    "workflow terminal, not assignment completion"
                )
            if checkpoint_name not in declared_names(checkpoint, "reads"):
                errors.append(
                    "iterationPolicy.checkpointTerminal must read checkpointState"
                )
            if outgoing[checkpoint_id]:
                errors.append(
                    "iterationPolicy.checkpointTerminal cannot resume inside the "
                    "headless workflow"
                )
            if reducer_ref is not None and not reaches(reducer_id, checkpoint_id):
                errors.append(
                    "iterationPolicy human_guided reducer must reach checkpointTerminal"
                )
            if (
                reducer_ref is not None
                and entry_id in node_by_id
                and path_exists_avoiding(entry_id, checkpoint_id, reducer_id)
            ):
                errors.append(
                    "iterationPolicy human_guided checkpoint cannot bypass deterministic reduction"
                )

        if reducer_ref is not None:
            after_reducer = descendants(reducer_id)
            model_nodes = sorted(
                node_id
                for node_id in after_reducer
                if node_by_id.get(node_id, {}).get("kind") in {"agent", "decision"}
            )
            if model_nodes:
                errors.append(
                    "iterationPolicy human_guided route cannot run a model after reducer; "
                    f"stop at checkpoint first (found {model_nodes})"
                )
            alternate_terminals = sorted(
                node_id
                for node_id in after_reducer
                if node_by_id.get(node_id, {}).get("kind") == "terminal"
                and node_id != policy.get("checkpointTerminal")
                and node_by_id[node_id].get("outcome") != "failure"
            )
            if alternate_terminals:
                errors.append(
                    "iterationPolicy human_guided reduction has non-checkpoint "
                    f"terminals {alternate_terminals}"
                )

    validate_iteration_policy()

    def topological_order(
        dependency_edges: set[tuple[str, str]],
    ) -> list[str] | None:
        successors: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}
        indegree = {node_id: 0 for node_id in node_by_id}
        for source, target in dependency_edges:
            if source not in node_by_id or target not in node_by_id:
                continue
            if target in successors[source]:
                continue
            successors[source].add(target)
            indegree[target] += 1
        ready = deque(
            sorted(node_id for node_id, degree in indegree.items() if degree == 0)
        )
        order: list[str] = []
        while ready:
            node_id = ready.popleft()
            order.append(node_id)
            for successor in sorted(successors[node_id]):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
        return order if len(order) == len(node_by_id) else None

    Cube = tuple[tuple[str, str], ...]

    def merge_cubes(left: Cube, right: Cube) -> Cube | None:
        merged = dict(left)
        for decision_id, outcome in right:
            existing = merged.get(decision_id)
            if existing is not None and existing != outcome:
                return None
            merged[decision_id] = outcome
        return tuple(sorted(merged.items()))

    def minimize_cubes(cubes: list[Cube]) -> list[Cube]:
        unique = sorted(set(cubes), key=lambda cube: (len(cube), cube))
        minimal: list[Cube] = []
        for cube in unique:
            values = dict(cube)
            if any(
                all(values.get(key) == value for key, value in candidate)
                for candidate in minimal
            ):
                continue
            minimal.append(cube)
            if len(minimal) > 100_000:
                raise ValueError(
                    "definite-assignment decision analysis exceeded 100000 states"
                )
        return minimal

    def combine_cube_sets(base: list[Cube], additions: list[Cube]) -> list[Cube]:
        combined: list[Cube] = []
        for left in base:
            for right in additions:
                merged = merge_cubes(left, right)
                if merged is not None:
                    combined.append(merged)
        return minimize_cubes(combined)

    def edge_activation_cubes(
        source: str, edge: dict[str, Any], source_cubes: list[Cube]
    ) -> list[Cube]:
        node = node_by_id[source]
        if node.get("kind") != "decision":
            return source_cubes
        outcome = exact_decision_outcome(edge, source)
        if outcome is None:
            # A malformed or non-verdict decision edge is validated elsewhere.
            # Treating it as unconstrained is conservative for assignment checks.
            return source_cubes
        branch = ((source, outcome),)
        activated: list[Cube] = []
        for cube in source_cubes:
            merged = merge_cubes(cube, branch)
            if merged is not None:
                activated.append(merged)
        return minimize_cubes(activated)

    def validate_definite_assignment() -> None:
        """Check monotone state initialization under static frontier semantics.

        Ordinary predecessor joins use intersection: the node may run after any
        activating predecessor. ``waitFor`` is different: every named node must
        have completed before the node can run, so its definitely-written state
        is added. Decision-outcome cubes prevent mutually exclusive branches from
        being mistaken for a fan-out barrier.
        """

        transition_dependencies = {
            (str(edge["from"]), str(edge["to"]))
            for edges in outgoing.values()
            for edge in edges
        }
        transition_order = topological_order(transition_dependencies)
        if transition_order is None:
            # Cyclic workflows use a separate bounded-loop contract. This pass is
            # intentionally limited to static acyclic designs.
            return

        execution_dependencies = set(transition_dependencies)
        valid_wait_for: dict[str, list[str]] = {}
        for node_id, node in node_by_id.items():
            raw = node.get("waitFor", [])
            dependencies = (
                [
                    dependency
                    for dependency in raw
                    if isinstance(dependency, str)
                    and dependency in node_by_id
                    and dependency != node_id
                ]
                if isinstance(raw, list)
                else []
            )
            valid_wait_for[node_id] = dependencies
            execution_dependencies.update(
                (dependency, node_id) for dependency in dependencies
            )
        execution_order = topological_order(execution_dependencies)
        if execution_order is None:
            errors.append(
                "static design waitFor dependencies create an execution cycle"
            )
            return

        execution_successors: dict[str, set[str]] = {
            node_id: set() for node_id in node_by_id
        }
        for source, target in execution_dependencies:
            if source in node_by_id and target in node_by_id:
                execution_successors[source].add(target)

        def execution_reaches(source: str, target: str) -> bool:
            seen: set[str] = set()
            stack = list(execution_successors[source])
            while stack:
                current = stack.pop()
                if current == target:
                    return True
                if current in seen:
                    continue
                seen.add(current)
                stack.extend(execution_successors[current])
            return False

        entry_id = str(document.get("entry", ""))
        reachable_cubes: dict[str, list[Cube]] = {node_id: [] for node_id in node_by_id}
        definitely_out: dict[str, set[str]] = {node_id: set() for node_id in node_by_id}

        def declared_fields(node: dict[str, Any], key: str) -> set[str]:
            value = node.get(key, [])
            if not isinstance(value, list) or not all(
                isinstance(field, str) for field in value
            ):
                return set()
            return set(value)

        for node_id in execution_order:
            node = node_by_id[node_id]
            dependencies = valid_wait_for[node_id]
            definitely_in: set[str] = set()

            if node_id == entry_id:
                if dependencies:
                    errors.append(
                        f"entry node {node_id} cannot wait for predecessor nodes"
                    )
                else:
                    reachable_cubes[node_id] = [tuple()]
            else:
                activation_records: list[tuple[str, Cube]] = []
                for edge in incoming[node_id]:
                    source = str(edge["from"])
                    for cube in edge_activation_cubes(
                        source, edge, reachable_cubes[source]
                    ):
                        activation_records.append((source, cube))

                feasible_records: list[tuple[str, Cube]] = []
                for source, activation_cube in activation_records:
                    # The controller drops an activation that arrives before a
                    # waitFor dependency. It can run only if some incoming source
                    # may complete after every dependency; a source that must
                    # precede a dependency cannot provide that final activation.
                    if any(
                        source != dependency and execution_reaches(source, dependency)
                        for dependency in dependencies
                    ):
                        continue
                    contexts = [activation_cube]
                    for dependency in dependencies:
                        contexts = combine_cube_sets(
                            contexts, reachable_cubes[dependency]
                        )
                        if not contexts:
                            break
                    feasible_records.extend((source, context) for context in contexts)

                reachable_cubes[node_id] = minimize_cubes(
                    [cube for _, cube in feasible_records]
                )
                if activation_records and not feasible_records:
                    errors.append(
                        f"node {node_id} cannot become runnable: its waitFor "
                        "dependencies are incompatible with every reachable "
                        "activation path"
                    )

                feasible_sources = sorted({source for source, _ in feasible_records})
                if feasible_sources:
                    definitely_in = set(definitely_out[feasible_sources[0]])
                    for source in feasible_sources[1:]:
                        definitely_in.intersection_update(definitely_out[source])
                    for dependency in dependencies:
                        definitely_in.update(definitely_out[dependency])

            if node_id == entry_id or reachable_cubes[node_id]:
                for field in sorted(declared_fields(node, "reads") - definitely_in):
                    errors.append(
                        f"node {node_id}.reads field {field} is not definitely "
                        "assigned on every reachable predecessor execution state"
                    )
            definitely_out[node_id] = definitely_in | declared_fields(node, "writes")

    try:
        validate_definite_assignment()
    except ValueError as error:
        errors.append(str(error))

    machine_gated = (
        isinstance(governance, dict)
        and governance.get("reviewPolicy") == "machine-gated"
    )

    for node_id, node in node_by_id.items():
        if node.get("kind") != "terminal" and not outgoing[node_id]:
            errors.append(f"non-terminal node {node_id} has no outgoing transition")
        if (
            node.get("kind") == "decision"
            and isinstance(node.get("outcomes"), list)
            and all(_is_nonempty(outcome) for outcome in node["outcomes"])
        ):
            outcomes = node["outcomes"]
            if machine_gated:
                exact_outcomes = [
                    exact_decision_outcome(edge, node_id) for edge in outgoing[node_id]
                ]
                for outcome in outcomes:
                    count = exact_outcomes.count(outcome)
                    if count != 1:
                        errors.append(
                            f"machine-gated decision node {node_id} outcome "
                            f"{outcome} must have exactly one exact verdict route"
                        )
                if len(outgoing[node_id]) != len(outcomes) or any(
                    outcome not in outcomes for outcome in exact_outcomes
                ):
                    errors.append(
                        f"machine-gated decision node {node_id} has an "
                        "unconditional, malformed, duplicate, or undeclared exit"
                    )

                has_approval = any(outcome in APPROVAL_OUTCOMES for outcome in outcomes)
                reviews = node.get("reviews")
                valid_reviews = (
                    reviews
                    if isinstance(reviews, list)
                    and all(isinstance(item, str) for item in reviews)
                    else []
                )
                if has_approval:
                    if node.get("workspaceAccess") != "read":
                        errors.append(
                            f"machine-gated approval decision node {node_id} "
                            "must be read-only"
                        )
                    if not valid_reviews:
                        errors.append(
                            f"machine-gated approval decision node {node_id} "
                            "must declare review coverage"
                        )
                elif valid_reviews:
                    errors.append(
                        f"machine-gated non-approval decision node {node_id} "
                        "cannot declare review coverage"
                    )

                for writer_id in valid_reviews:
                    writer = node_by_id.get(writer_id)
                    if writer is None or writer.get("workspaceAccess") != "write":
                        errors.append(
                            f"machine-gated decision node {node_id}.reviews "
                            f"references non-writer {writer_id}"
                        )
                    elif not reaches(writer_id, node_id):
                        errors.append(
                            f"machine-gated decision node {node_id}.reviews "
                            f"references writer {writer_id}, which is not an ancestor"
                        )
            else:
                covered = {
                    edge.get("when", {}).get("equals")
                    for edge in outgoing[node_id]
                    if isinstance(edge.get("when"), dict)
                    and edge["when"].get("field") == f"{node_id}.verdict"
                }
                missing = set(outcomes) - covered
                if missing:
                    errors.append(
                        f"decision node {node_id} has no explicit route for outcomes {sorted(missing)}"
                    )

    terminals = document.get("terminals")
    if not isinstance(terminals, dict) or not terminals:
        errors.append("terminals must map named outcomes to terminal nodes")
    else:
        for outcome, node_id in terminals.items():
            if outcome not in TERMINAL_OUTCOMES:
                errors.append(f"terminals has unsupported outcome {outcome}")
            node = node_by_id.get(str(node_id))
            if node is None or node.get("kind") != "terminal":
                errors.append(
                    f"terminal mapping {outcome} references non-terminal {node_id}"
                )
            elif node.get("outcome") != outcome:
                errors.append(
                    f"terminal mapping {outcome} disagrees with node {node_id}"
                )

    if entry in node_by_id:
        seen = {str(entry)}
        queue = deque([str(entry)])
        while queue:
            for edge in outgoing[queue.popleft()]:
                target = str(edge["to"])
                if target not in seen:
                    seen.add(target)
                    queue.append(target)
        unreachable = sorted(set(node_by_id) - seen)
        if unreachable:
            errors.append(f"unreachable nodes: {unreachable}")
        reverse: dict[str, set[str]] = defaultdict(set)
        for source, edges in outgoing.items():
            for edge in edges:
                reverse[str(edge["to"])].add(source)
        terminal_ids = {
            node_id
            for node_id, node in node_by_id.items()
            if node.get("kind") == "terminal"
        }
        can_terminate = set(terminal_ids)
        queue = deque(sorted(terminal_ids))
        while queue:
            for source in reverse[queue.popleft()]:
                if source not in can_terminate:
                    can_terminate.add(source)
                    queue.append(source)
        trapped = sorted(seen - can_terminate)
        if trapped:
            errors.append(f"reachable nodes cannot reach a terminal: {trapped}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("design", type=Path)
    args = parser.parse_args()
    try:
        document = json.loads(args.design.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"DESIGN: invalid JSON: {error}", file=sys.stderr)
        return 2
    skill_root: Path | None = None
    for parent in (args.design.resolve().parent, *args.design.resolve().parents):
        candidate = parent / ".claude" / "skills"
        if candidate.is_dir():
            skill_root = candidate
            break
    errors = validate(document, skill_root)
    if errors:
        for error in errors:
            print(f"DESIGN: {error}", file=sys.stderr)
        return 1
    print("DESIGN: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
