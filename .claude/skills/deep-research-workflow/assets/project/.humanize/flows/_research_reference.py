"""Shared deterministic contracts for the trusted Deep Research reference flows.

The public flow files keep orchestration small.  This module owns only deterministic
validation, model-call accounting, and the one controller-side write: an atomic terminal
artifact.  Humanize ignores underscore-prefixed files during flow discovery.
"""

from __future__ import annotations

import json
import os
import fcntl
import hashlib
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base class for shaped agent answers used by the reference flows."""

    model_config = ConfigDict(extra="forbid")


ITERATION_MODES = ("autonomous", "human_guided")
HUMAN_UNAVAILABLE_FALLBACKS = ("pause", "autonomous_review")
ITERATION_RISK_TRIGGERS = (
    "lease_expired",
    "human_unavailable",
    "contradiction",
    "ambiguity",
    "plateau_or_repeated_failure",
    "irreversible_or_high_cost",
    "safety_sensitive",
    "weak_proxy",
)


def _utc_timestamp(value: str, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ValueError(f"{label} is not a valid UTC timestamp") from error
    return parsed


class IterationConfig(StrictModel):
    """Shared strict mode/lease fields bound by the native launch receipt."""

    iteration_mode: Literal["autonomous", "human_guided"] = "autonomous"
    human_checkin_lease_minutes: int | None = Field(default=None, ge=5, le=1440)
    human_checkin_lease_expires_at: str | None = None
    human_unavailable_fallback: Literal["pause", "autonomous_review"] = "pause"
    human_feedback: str | None = Field(default=None, min_length=1, max_length=8192)
    human_feedback_checkpoint_artifact: str | None = Field(
        default=None, min_length=1, max_length=1024
    )
    human_feedback_checkpoint_sha256: str | None = None

    @model_validator(mode="after")
    def _mode_has_consistent_lease(self) -> "IterationConfig":
        if self.iteration_mode == "autonomous":
            if (
                self.human_checkin_lease_minutes is not None
                or self.human_checkin_lease_expires_at is not None
            ):
                raise ValueError("autonomous mode cannot carry a human check-in lease")
            if self.human_feedback is not None:
                raise ValueError("autonomous mode cannot carry human checkpoint feedback")
            if (
                self.human_feedback_checkpoint_artifact is not None
                or self.human_feedback_checkpoint_sha256 is not None
            ):
                raise ValueError("autonomous mode cannot carry a human feedback checkpoint")
            return self
        if (
            self.human_checkin_lease_minutes is None
            or self.human_checkin_lease_expires_at is None
        ):
            raise ValueError("human_guided mode requires lease minutes and UTC expiry")
        _utc_timestamp(
            self.human_checkin_lease_expires_at,
            "human_checkin_lease_expires_at",
        )
        bindings = (
            self.human_feedback,
            self.human_feedback_checkpoint_artifact,
            self.human_feedback_checkpoint_sha256,
        )
        if any(value is not None for value in bindings) and not all(
            value is not None for value in bindings
        ):
            raise ValueError(
                "human feedback requires both checkpoint artifact and checkpoint SHA-256"
            )
        if self.human_feedback_checkpoint_artifact is not None:
            checkpoint_path = Path(self.human_feedback_checkpoint_artifact)
            if (
                checkpoint_path.is_absolute()
                or ".." in checkpoint_path.parts
                or checkpoint_path.parts[:2] != (".humanize", "artifacts")
            ):
                raise ValueError(
                    "human feedback checkpoint artifact must be under .humanize/artifacts"
                )
            if not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(self.human_feedback_checkpoint_sha256),
            ):
                raise ValueError("human feedback checkpoint SHA-256 is invalid")
        return self


def resolve_iteration_policy(
    setting: IterationConfig, *, now: datetime | None = None
) -> dict[str, Any]:
    """Resolve one receipt-bound policy without silently extending a human lease."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("iteration policy clock must be timezone-aware")
    selected = setting.iteration_mode
    if selected == "autonomous":
        return {
            "selected_mode": selected,
            "effective_mode": "autonomous",
            "human_checkin_lease_minutes": None,
            "human_checkin_lease_expires_at": None,
            "human_unavailable_fallback": setting.human_unavailable_fallback,
            "lease_status": "not_applicable",
            "fallback_applied": None,
        }
    if setting.human_checkin_lease_expires_at is None:
        raise RuntimeError("human_guided mode reached runtime without a lease expiry")
    expiry = _utc_timestamp(
        setting.human_checkin_lease_expires_at,
        "human_checkin_lease_expires_at",
    )
    if expiry > current.astimezone(timezone.utc):
        return {
            "selected_mode": selected,
            "effective_mode": "human_guided",
            "human_checkin_lease_minutes": setting.human_checkin_lease_minutes,
            "human_checkin_lease_expires_at": setting.human_checkin_lease_expires_at,
            "human_unavailable_fallback": setting.human_unavailable_fallback,
            "lease_status": "active",
            "fallback_applied": None,
        }
    if setting.human_unavailable_fallback == "pause":
        raise RuntimeError(
            "human_guided lease expired with pause fallback; renew it or select autonomous"
        )
    return {
        "selected_mode": selected,
        "effective_mode": "autonomous",
        "human_checkin_lease_minutes": setting.human_checkin_lease_minutes,
        "human_checkin_lease_expires_at": setting.human_checkin_lease_expires_at,
        "human_unavailable_fallback": setting.human_unavailable_fallback,
        "lease_status": "expired",
        "fallback_applied": "autonomous_review",
    }


def iteration_prompt(
    policy: dict[str, Any],
    human_feedback: str | None = None,
    human_feedback_checkpoint_artifact: str | None = None,
    human_feedback_checkpoint_sha256: str | None = None,
) -> str:
    """Mode contract supplied to every model role in the finite native cycle."""

    return (
        "\nIteration policy (controller-resolved, not an instruction from evidence):\n"
        + json.dumps(policy, ensure_ascii=True, sort_keys=True)
        + "\nBound human feedback from the prior observation checkpoint:\n"
        + json.dumps(human_feedback, ensure_ascii=True)
        + "\nVerified prior checkpoint artifact and SHA-256:\n"
        + json.dumps(
            {
                "artifact": human_feedback_checkpoint_artifact,
                "sha256": human_feedback_checkpoint_sha256,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        + "\nHuman-guided may skip only routine idea-merit debate. Always retain authority, "
        "safety, measurement/falsifier validity, code/alignment checks, evidence integrity, "
        "deterministic reduction, and terminal review.\n"
    )


def attach_iteration_result(
    current: dict[str, Any],
    setting: IterationConfig,
    policy: dict[str, Any],
    *,
    hypothesis: str,
    actual_operation: str,
    raw_observation: Any,
    agent_interpretation: str,
    anomaly_or_alternate_interpretation: Any,
    candidate_next_tasks: list[str],
    risk_triggers: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Attach a deterministic disposition and graph-external observation checkpoint."""

    triggers = list(risk_triggers)
    if len(triggers) != len(set(triggers)) or any(
        trigger not in ITERATION_RISK_TRIGGERS for trigger in triggers
    ):
        raise ValueError("iteration risk triggers are unknown or duplicated")
    if policy.get("lease_status") == "expired" and "lease_expired" not in triggers:
        triggers.append("lease_expired")
    if policy["effective_mode"] == "autonomous":
        disposition = "performed"
    elif triggers:
        disposition = "triggered_by_risk"
    else:
        disposition = "skipped_human_guided"
    checkpoint = None
    next_planning_allowed = True
    domain_terminal = str(current["domain_terminal"])
    enriched = {
        **current,
        "research_outcome": domain_terminal,
        "iteration_policy": policy,
        "idea_review_disposition": disposition,
        "iteration_risk_triggers": triggers,
        "consumed_human_feedback": setting.human_feedback,
        "consumed_human_feedback_checkpoint_artifact": (
            setting.human_feedback_checkpoint_artifact
        ),
        "consumed_human_feedback_checkpoint_sha256": (
            setting.human_feedback_checkpoint_sha256
        ),
    }
    if policy["effective_mode"] == "human_guided":
        checkpoint_body = {
            "hypothesis": hypothesis,
            "actual_operation": actual_operation,
            "raw_observation": serializable(raw_observation),
            "agent_interpretation": agent_interpretation,
            "anomaly_or_alternate_interpretation": serializable(
                anomaly_or_alternate_interpretation
            ),
            "candidate_next_tasks": candidate_next_tasks,
            "human_feedback": None,
        }
        checkpoint = {
            "checkpoint_id": _canonical_sha256(checkpoint_body),
            **checkpoint_body,
            "questions_for_human": [
                "What do you notice in the raw observation before reading the proposed next tasks?",
                "Which next task or new idea should the following planning round prioritize?",
            ],
            "next_planning_allowed": False,
        }
        next_planning_allowed = False
        enriched["domain_terminal"] = "HUMAN_OBSERVATION_REQUIRED"
    return {
        **enriched,
        "human_observation_checkpoint": checkpoint,
        "next_planning_allowed": next_planning_allowed,
    }


LAUNCH_FD_ENV = "HUMANIZE_RESEARCH_LAUNCH_FD"
LAUNCH_SHA_ENV = "HUMANIZE_RESEARCH_LAUNCH_SHA256"
LAUNCH_SCHEMA = "humanize-research-launch/v1"
CANONICAL_RECEIPT_SCHEMA = "workflow-native-launch-receipt/v1"
REFERENCE_BINDING_SCHEMA = "humanize-research-reference-binding/v1"
ITERATION_CONTEXT_SCHEMA = "humanize-research-iteration-context/v1"
REFERENCE_SOURCE_FILES = {
    "_research_reference.py",
    "auto_research.py",
    "deep_research.py",
}
MAX_LAUNCH_RECEIPT_BYTES = 128 * 1024
PROVIDER_ACCOUNT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
CANONICAL_RECEIPT_FIELDS = {
    "schema",
    "request",
    "resolved",
    "writer",
    "policy",
    "receiptSha256",
}
CANONICAL_REQUEST_FIELDS = {
    "taskClass",
    "reasoningPosture",
    "semanticRole",
    "writerRole",
    "reviewMode",
}
CANONICAL_POLICY_FIELDS = {
    "modelsSchema",
    "modelsDocumentSha256",
    "catalogResolutionSha256",
    "capabilityPolicySha256",
    "reasoningPolicySha256",
    "catalogStatus",
    "catalogSource",
    "catalogEvidenceReceipt",
    "liveCatalogAuthenticatedByResolver",
    "freshCatalogEvidence",
}
IDENTITY_FIELD_NAMES = (
    "semanticRole",
    "backend",
    "provider",
    "upstreamProviderFamily",
    "model",
    "modelFamily",
    "effort",
    "intelligenceClass",
    "dispatchStatus",
    "catalogModelId",
)
LAUNCH_ROLE_FIELDS = {
    "semanticRole",
    "backend",
    "provider",
    "upstreamProviderFamily",
    "model",
    "modelFamily",
    "effort",
    "intelligenceClass",
    "dispatchStatus",
    "catalogModelId",
    "canonicalModel",
    "providerAccount",
    "providerRecordSha256",
    "permission",
    "flowRole",
    "launchReceipt",
}
FLOW_ROLE_CONTRACT = {
    "deep-research": {
        "preflight": {
            "semanticRole": "plan",
            "taskClass": "planning",
            "reviewMode": "none",
            "writerRole": None,
            "permission": "read-only",
        },
        "collector": {
            "semanticRole": "research_collect",
            "taskClass": "collection",
            "reviewMode": "none",
            "writerRole": None,
            "permission": "read-only",
        },
        "counterevidence": {
            "semanticRole": "research_collect",
            "taskClass": "collection",
            "reviewMode": "none",
            "writerRole": None,
            "permission": "read-only",
        },
        "reducer": {
            "semanticRole": "frontier_execute",
            "taskClass": "topIntelligence",
            "reviewMode": "none",
            "writerRole": None,
            "permission": "read-only",
        },
        "reviewer": {
            "semanticRole": "advisor",
            "taskClass": "verification",
            "reviewMode": "exact-opposite",
            "writerRole": "frontier_execute",
            "permission": "read-only",
        },
    },
    "auto-research": {
        "knowledge": {
            "semanticRole": "frontier_execute",
            "taskClass": "topIntelligence",
            "reviewMode": "none",
            "writerRole": None,
            "permission": "read-only",
        },
        "applicability": {
            "semanticRole": "plan",
            "taskClass": "planning",
            "reviewMode": "none",
            "writerRole": None,
            "permission": "read-only",
        },
        "experimenter": {
            "semanticRole": "task",
            "taskClass": "analysis",
            "reviewMode": "none",
            "writerRole": None,
            "permission": "workspace-write",
        },
        "reviewer": {
            "semanticRole": "advisor",
            "taskClass": "verification",
            "reviewMode": "exact-opposite",
            "writerRole": "frontier_execute",
            "permission": "read-only",
        },
    },
}
_CURSOR_THINKING_PREFIXES = ("claude-opus-5", "claude-sonnet-5")
_CURSOR_PLAIN_PREFIXES = ("gpt-5.6-sol", "gpt-5.6-luna")
_CURSOR_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


class LaunchContractError(ValueError):
    """The flow was not started through its fail-closed policy launcher."""


class ReviewCriterion(StrictModel):
    criterion_id: str = Field(min_length=1)
    passed: bool
    evidence: str = Field(min_length=1)


class TerminalReview(StrictModel):
    """Fresh terminal review over the current artifact, never a self-approval."""

    verdict: Literal["COMPLETE", "REVISE", "HUMAN_BLOCKED"]
    criteria: list[ReviewCriterion] = Field(min_length=1)
    issues: list[str] = Field(default_factory=list)
    human_dependency: str | None = None

    @model_validator(mode="after")
    def _verdict_is_evidenced(self) -> "TerminalReview":
        if self.verdict == "COMPLETE" and (
            self.issues or any(not item.passed for item in self.criteria)
        ):
            raise ValueError("COMPLETE requires every criterion to pass and no open issue")
        if self.verdict == "HUMAN_BLOCKED" and not self.human_dependency:
            raise ValueError("HUMAN_BLOCKED requires one exact human-only dependency")
        if self.verdict != "HUMAN_BLOCKED" and self.human_dependency:
            raise ValueError("human_dependency is valid only for HUMAN_BLOCKED")
        return self


class CallBudget:
    """Fail closed before a model call would exceed the configured cumulative cap."""

    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0

    def reserve(self, label: str) -> None:
        if self.used >= self.maximum:
            raise RuntimeError(f"model-call budget exhausted before {label}")
        self.used += 1

    def ask(self, agent: Any, prompt: str, schema: type[StrictModel], label: str) -> Any:
        self.reserve(label)
        answer = agent(prompt, schema=schema)
        return _validate_answer(answer, schema, label)

    async def aask(
        self, agent: Any, prompt: str, schema: type[StrictModel], label: str
    ) -> Any:
        self.reserve(label)
        answer = await agent.aturn(prompt, schema=schema)
        return _validate_answer(answer, schema, label)


def _validate_answer(answer: Any, schema: type[StrictModel], label: str) -> Any:
    if answer is None:
        raise ValueError(f"{label} returned no structured answer")
    if isinstance(answer, schema):
        return answer
    return schema.model_validate(answer)


def serializable(value: Any) -> Any:
    """Convert nested Pydantic/reference values into deterministic JSON data."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    return value


def json_prompt(value: Any) -> str:
    return json.dumps(serializable(value), ensure_ascii=False, sort_keys=True, indent=2)


def safe_project_path(value: str) -> Path:
    """Return one lexical project-relative output without following any path component."""

    given = Path(value)
    if (
        given.is_absolute()
        or not given.parts
        or any(part in {"", ".", ".."} for part in given.parts)
    ):
        raise ValueError("terminal_artifact must be a non-traversing project-relative path")
    project = Path.cwd().resolve()
    return project.joinpath(*given.parts)


def _output_parts(value: str) -> tuple[str, ...]:
    target = safe_project_path(value)
    relative = target.relative_to(Path.cwd().resolve())
    if not relative.parts:
        raise ValueError("terminal_artifact must not name the project root")
    return relative.parts


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("terminal_artifact requires Linux no-follow file-descriptor support")
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_output_parent(
    root_fd: int, parts: tuple[str, ...], *, create: bool
) -> int:
    current = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            try:
                next_fd = os.open(component, _directory_flags(), dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise ValueError("terminal_artifact parent directory is missing")
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
                next_fd = os.open(component, _directory_flags(), dir_fd=current)
            except OSError as error:
                raise ValueError(
                    f"terminal_artifact parent must be a real non-symlink directory: {error}"
                ) from error
            if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                raise ValueError(
                    "terminal_artifact parent must be a real non-symlink directory"
                )
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        try:
            os.close(current)
        except OSError:
            pass
        raise


def _output_leaf_identity(parent_fd: int, name: str) -> tuple[int, int] | None:
    try:
        status = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"terminal_artifact leaf cannot be inspected safely: {error}") from error
    if stat.S_ISLNK(status.st_mode):
        raise ValueError("terminal_artifact leaf must not be a symlink")
    if not stat.S_ISREG(status.st_mode):
        raise ValueError("terminal_artifact leaf must be a regular file")
    return status.st_dev, status.st_ino


def _same_output_directory(left_fd: int, right_fd: int) -> bool:
    left = os.fstat(left_fd)
    right = os.fstat(right_fd)
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _atomic_project_write(project: Path, parts: tuple[str, ...], rendered: bytes) -> None:
    root_fd: int | None = None
    parent_fd: int | None = None
    temporary_fd: int | None = None
    temporary_name = f".{parts[-1]}.{uuid.uuid4().hex}.tmp"
    try:
        root_fd = os.open(project, _directory_flags())
        parent_fd = _open_output_parent(root_fd, parts, create=True)
        initial_leaf = _output_leaf_identity(parent_fd, parts[-1])
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        view = memoryview(rendered)
        while view:
            written = os.write(temporary_fd, view)
            if written <= 0:
                raise ValueError("terminal_artifact write made no progress")
            view = view[written:]
        os.fsync(temporary_fd)
        status = os.fstat(temporary_fd)
        temporary_identity = status.st_dev, status.st_ino
        os.close(temporary_fd)
        temporary_fd = None

        current_parent = _open_output_parent(root_fd, parts, create=False)
        try:
            if not _same_output_directory(parent_fd, current_parent):
                raise ValueError("terminal_artifact parent changed during atomic write")
        finally:
            os.close(current_parent)
        if _output_leaf_identity(parent_fd, parts[-1]) != initial_leaf:
            raise ValueError("terminal_artifact leaf changed during atomic write")

        os.replace(
            temporary_name,
            parts[-1],
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
        if _output_leaf_identity(parent_fd, parts[-1]) != temporary_identity:
            raise ValueError("terminal_artifact atomic replacement was not retained")
        current_parent = _open_output_parent(root_fd, parts, create=False)
        try:
            if not _same_output_directory(parent_fd, current_parent) or (
                _output_leaf_identity(current_parent, parts[-1]) != temporary_identity
            ):
                raise ValueError("terminal_artifact path changed after atomic replacement")
        finally:
            os.close(current_parent)
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"terminal_artifact could not be written safely: {error}") from error
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        if parent_fd is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(parent_fd)
        if root_fd is not None:
            os.close(root_fd)


def write_terminal(path: str, payload: dict[str, Any]) -> Path:
    """Atomically write the only controller-owned side effect of a reference flow."""

    target = safe_project_path(path)
    rendered = (
        json.dumps(
            serializable(payload), ensure_ascii=False, sort_keys=True, indent=2
        )
        + "\n"
    ).encode("utf-8")
    _atomic_project_write(Path.cwd().resolve(), _output_parts(path), rendered)
    print(f"TERMINAL_ARTIFACT={target}")
    return target


def permission_of(agent: Any) -> str:
    config = getattr(agent, "config", None)
    return str(getattr(config, "permission", "")).strip()


def require_read_only(named_agents: dict[str, Any]) -> None:
    invalid = [
        name
        for name, agent in named_agents.items()
        if permission_of(agent) != "read-only"
    ]
    if invalid:
        raise ValueError(
            "read-only permission is required for: " + ", ".join(sorted(invalid))
        )


def require_workspace_writer(name: str, agent: Any) -> None:
    if permission_of(agent) != "workspace-write":
        raise ValueError(f"{name} must use exactly workspace-write permission")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return _sha256(_canonical_json_bytes(value))


def _cursor_catalog_id(model: str, effort: str) -> str:
    """Map a configured Cursor base model + effort without importing catalog tools."""

    if effort not in _CURSOR_EFFORTS:
        raise LaunchContractError(
            "canonical launch receipt has no audited Cursor concrete model mapping"
        )
    for prefix in _CURSOR_THINKING_PREFIXES:
        if model == prefix or model.startswith(prefix + "-"):
            return f"{prefix}-thinking-{effort}"
    for prefix in _CURSOR_PLAIN_PREFIXES:
        if model == prefix or model.startswith(prefix + "-"):
            return f"{prefix}-{effort}"
    raise LaunchContractError(
        "canonical launch receipt has no audited Cursor concrete model mapping"
    )


def _require_fresh_catalog_coverage(identity: dict[str, Any], policy_block: dict[str, Any]) -> None:
    backend = identity.get("backend")
    model = identity.get("model")
    effort = identity.get("effort")
    concrete = identity.get("catalogModelId")
    evidence_by_backend = policy_block.get("freshCatalogEvidence")
    if (
        not isinstance(backend, str)
        or not backend
        or not isinstance(model, str)
        or not model
        or not isinstance(effort, str)
        or not effort
        or not isinstance(concrete, str)
        or not concrete
        or not isinstance(evidence_by_backend, dict)
    ):
        raise LaunchContractError(
            "canonical launch receipt lacks a fresh concrete catalog model binding"
        )
    if backend not in evidence_by_backend:
        raise LaunchContractError(
            "canonical launch receipt lacks fresh catalog evidence for its resolved backend"
        )
    evidence = evidence_by_backend[backend]
    if not isinstance(evidence, dict) or evidence.get("backend") != backend:
        raise LaunchContractError(
            "canonical launch receipt fresh catalog evidence does not bind its backend"
        )
    coverage = evidence.get("coverage")
    if not isinstance(coverage, dict):
        raise LaunchContractError(
            "canonical launch receipt fresh catalog evidence has no coverage"
        )
    if backend in {"claude", "codex"}:
        efforts = coverage.get(model)
        if concrete != model or not isinstance(efforts, list) or effort not in efforts:
            raise LaunchContractError(
                "canonical launch receipt concrete catalog model is not covered by fresh evidence"
            )
        return
    if backend == "cursor":
        ids = coverage.get("__cursor_ids__")
        expected_concrete = _cursor_catalog_id(model, effort)
        if (
            concrete != expected_concrete
            or not isinstance(ids, list)
            or concrete not in ids
        ):
            raise LaunchContractError(
                "canonical launch receipt concrete catalog model is not covered by fresh evidence"
            )
        return
    raise LaunchContractError(
        f"canonical launch receipt backend {backend!r} has no audited catalog verifier"
    )


def _validate_identity_fields(
    identity: object,
    configured: dict[str, Any],
    semantic_role: str,
    label: str,
) -> dict[str, Any]:
    if not isinstance(identity, dict):
        raise LaunchContractError(f"{label} has no resolved identity")
    _strict_fields(identity, set(IDENTITY_FIELD_NAMES), label)
    for field in IDENTITY_FIELD_NAMES:
        if field == "catalogModelId":
            continue
        expected = semantic_role if field == "semanticRole" else configured.get(field)
        if identity.get(field) != expected:
            raise LaunchContractError(
                f"{label} differs from current {semantic_role}.{field}"
            )
    if not isinstance(identity.get("catalogModelId"), str) or not identity["catalogModelId"]:
        raise LaunchContractError(f"{label} has no concrete catalog model ID")
    if identity.get("dispatchStatus") != "active":
        raise LaunchContractError(f"{label} is not an active dispatch identity")
    return identity


def _validate_canonical_launch_receipt(
    nested: object,
    *,
    policy: dict[str, Any],
    policy_raw: bytes,
    semantic_role: str,
    task_class: str,
    posture: str,
    review_mode: str,
    writer_role: str | None,
) -> dict[str, Any]:
    if not isinstance(nested, dict) or nested.get("schema") != CANONICAL_RECEIPT_SCHEMA:
        raise LaunchContractError(
            "canonical launch receipt must use workflow-native-launch-receipt/v1"
        )
    _strict_fields(nested, CANONICAL_RECEIPT_FIELDS, "canonical launch receipt")
    unsigned = {key: value for key, value in nested.items() if key != "receiptSha256"}
    if nested.get("receiptSha256") != _canonical_sha256(unsigned):
        raise LaunchContractError("canonical launch receipt self-digest is invalid")
    request = nested.get("request")
    if not isinstance(request, dict):
        raise LaunchContractError("canonical launch receipt request is missing")
    _strict_fields(request, CANONICAL_REQUEST_FIELDS, "canonical launch receipt request")
    expected_request = {
        "taskClass": task_class,
        "reasoningPosture": posture,
        "semanticRole": semantic_role,
        "writerRole": writer_role,
        "reviewMode": review_mode,
    }
    if request != expected_request:
        raise LaunchContractError(
            "canonical launch receipt request differs from the fixed flow role"
        )

    resolution = policy.get("catalogResolution")
    capability = policy.get("capabilityPolicy")
    reasoning = policy.get("reasoningPolicy")
    if (
        not isinstance(resolution, dict)
        or not isinstance(capability, dict)
        or not isinstance(reasoning, dict)
    ):
        raise LaunchContractError("canonical model policy has malformed receipt inputs")
    expected_policy = {
        "modelsSchema": policy.get("schema"),
        "modelsDocumentSha256": _sha256(policy_raw),
        "catalogResolutionSha256": _canonical_sha256(resolution),
        "capabilityPolicySha256": _canonical_sha256(capability),
        "reasoningPolicySha256": _canonical_sha256(reasoning),
        "catalogStatus": resolution.get("status"),
        "catalogSource": "authenticated-native-cli",
        "catalogEvidenceReceipt": resolution.get("evidenceReceipt"),
        "liveCatalogAuthenticatedByResolver": True,
    }
    policy_block = nested.get("policy")
    if (
        not isinstance(policy_block, dict)
        or set(policy_block) != CANONICAL_POLICY_FIELDS
        or any(policy_block.get(key) != value for key, value in expected_policy.items())
        or policy_block.get("liveCatalogAuthenticatedByResolver") is not True
        or not isinstance(policy_block.get("freshCatalogEvidence"), dict)
    ):
        raise LaunchContractError(
            "canonical launch receipt does not bind current policy bytes and fresh catalog evidence"
        )

    roles = policy.get("roles")
    configured = roles.get(semantic_role) if isinstance(roles, dict) else None
    if not isinstance(configured, dict):
        raise LaunchContractError(f"semantic role {semantic_role} is absent")
    identity = _validate_identity_fields(
        nested.get("resolved"),
        configured,
        semantic_role,
        "canonical launch receipt resolved identity",
    )
    _require_fresh_catalog_coverage(identity, policy_block)

    writer = nested.get("writer")
    if writer_role is None:
        if writer is not None:
            raise LaunchContractError(
                "canonical launch receipt unexpectedly binds a writer identity"
            )
    else:
        configured_writer = roles.get(writer_role) if isinstance(roles, dict) else None
        if not isinstance(configured_writer, dict):
            raise LaunchContractError("canonical launch receipt has no writer identity")
        writer_identity = _validate_identity_fields(
            writer,
            configured_writer,
            writer_role,
            "canonical launch receipt writer identity",
        )
        _require_fresh_catalog_coverage(writer_identity, policy_block)
    return nested


def _expected_sealed_role(
    nested: dict[str, Any],
    *,
    flow_role: str,
    permission: str,
    provider_account: str,
    provider_record_sha256: str,
) -> dict[str, Any]:
    resolved = nested["resolved"]
    expected = {
        field: resolved[field]
        for field in IDENTITY_FIELD_NAMES
        if field != "model"
    }
    expected.update(
        {
            "model": resolved["catalogModelId"],
            "canonicalModel": resolved["model"],
            "providerAccount": provider_account,
            "providerRecordSha256": provider_record_sha256,
            "permission": permission,
            "flowRole": flow_role,
            "launchReceipt": nested,
        }
    )
    return expected


def _receipt_bytes(descriptor: int) -> bytes:
    try:
        status = os.fstat(descriptor)
    except OSError as error:
        raise LaunchContractError(f"launch receipt descriptor is unavailable: {error}") from error
    if status.st_size <= 0 or status.st_size > MAX_LAUNCH_RECEIPT_BYTES:
        raise LaunchContractError("launch receipt size is invalid")
    required = (
        fcntl.F_SEAL_SEAL
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_WRITE
    )
    try:
        seals = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
    except OSError as error:
        raise LaunchContractError("launch receipt is not a sealable anonymous file") from error
    if seals & required != required:
        raise LaunchContractError("launch receipt is not immutable and fully sealed")
    try:
        return os.pread(descriptor, status.st_size, 0)
    except OSError as error:
        raise LaunchContractError(f"launch receipt cannot be read: {error}") from error


def _strict_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise LaunchContractError(f"{label} fields do not match the launch schema")


def _regular_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        status = path.stat(follow_symlinks=False)
    except OSError as error:
        raise LaunchContractError(f"{label} is unavailable: {error}") from error
    if not path.is_file() or path.is_symlink() or not status.st_size:
        raise LaunchContractError(f"{label} must be a non-empty regular non-symlink file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise LaunchContractError(f"{label} cannot be read as JSON: {error}") from error
    if not isinstance(value, dict):
        raise LaunchContractError(f"{label} root must be an object")
    return value, raw


def _verify_policy_and_accounts(
    flow_name: str,
    receipt: dict[str, Any],
    recorded_roles: list[dict[str, Any]],
) -> None:
    root = Path.cwd().resolve()
    policy, policy_raw = _regular_json(
        root / "config/workflows/models.json", "canonical model role policy"
    )
    bindings, bindings_raw = _regular_json(
        root / "config/workflows/humanize-research-accounts.json",
        "canonical Humanize account bindings",
    )
    if receipt.get("rolePolicySha256") != _sha256(policy_raw):
        raise LaunchContractError("launch receipt does not bind the canonical model role policy")
    if receipt.get("accountBindingsSha256") != _sha256(bindings_raw):
        raise LaunchContractError("launch receipt does not bind the canonical account bindings")
    if policy.get("schema") != "workflow-model-roles/v3":
        raise LaunchContractError("canonical model policy is not workflow-model-roles/v3")
    catalog = policy.get("catalogResolution")
    if not isinstance(catalog, dict) or (
        catalog.get("schema") != "workflow-model-catalog-resolution/v1"
    ):
        raise LaunchContractError("canonical model policy lacks a catalog resolution snapshot")
    roles = policy.get("roles")
    reasoning = policy.get("reasoningPolicy")
    classes = reasoning.get("intelligenceClasses") if isinstance(reasoning, dict) else None
    efforts = reasoning.get("backendEffortLevels") if isinstance(reasoning, dict) else None
    posture = reasoning.get("defaultPosture") if isinstance(reasoning, dict) else None
    if not all(
        isinstance(value, expected)
        for value, expected in (
            (roles, dict),
            (classes, dict),
            (efforts, dict),
            (posture, str),
        )
    ) or not posture:
        raise LaunchContractError("canonical model policy catalogs are malformed")
    allowed_roles = None
    postures = reasoning.get("postures") if isinstance(reasoning, dict) else None
    if isinstance(postures, dict) and isinstance(postures.get(posture), dict):
        allowed_roles = postures[posture].get("allowedRoles")
    if not isinstance(allowed_roles, list):
        raise LaunchContractError("canonical model policy defaultPosture has malformed allowedRoles")
    if bindings.get("schema") != "humanize-research-account-bindings/v1":
        raise LaunchContractError("canonical Humanize account binding schema is unsupported")
    flow_bindings = bindings.get("flows")
    if not isinstance(flow_bindings, dict) or not isinstance(
        flow_bindings.get(flow_name), dict
    ):
        raise LaunchContractError("canonical account bindings omit this research flow")
    declared_bindings = flow_bindings[flow_name]
    if set(declared_bindings) != {item.get("flowRole") for item in recorded_roles}:
        raise LaunchContractError("canonical account bindings do not exactly cover flow roles")
    contract = FLOW_ROLE_CONTRACT.get(flow_name)
    if not isinstance(contract, dict):
        raise LaunchContractError("unsupported native research flow")

    humanize_home = Path(
        os.environ.get("HUMANIZE_HOME", Path.home() / ".humanize")
    ).resolve()
    for recorded in recorded_roles:
        flow_role = recorded["flowRole"]
        role_contract = contract.get(flow_role)
        if not isinstance(role_contract, dict):
            raise LaunchContractError(f"flow role {flow_role} is not in the fixed contract")
        semantic_role = recorded["semanticRole"]
        if semantic_role != role_contract["semanticRole"]:
            raise LaunchContractError(f"launch receipt changes the fixed role for {flow_role}")
        configured = roles.get(semantic_role)
        if not isinstance(configured, dict) or configured.get("dispatchStatus") != "active":
            raise LaunchContractError(f"semantic role {semantic_role} is absent or inactive")
        if semantic_role not in allowed_roles:
            raise LaunchContractError(
                f"canonical policy defaultPosture {posture!r} does not admit "
                f"semantic role {semantic_role!r}"
            )
        class_members = classes.get(recorded["intelligenceClass"])
        offered = efforts.get(recorded["backend"])
        if not isinstance(class_members, list) or semantic_role not in class_members:
            raise LaunchContractError(f"semantic role {semantic_role} class membership is invalid")
        if not isinstance(offered, list) or recorded["effort"] not in offered:
            raise LaunchContractError(f"semantic role {semantic_role} effort is not verified")

        binding = declared_bindings.get(flow_role)
        if not isinstance(binding, dict) or set(binding) != {
            "providerAccount",
            "providerRecordSha256",
            "upstreamProviderFamily",
        }:
            raise LaunchContractError(f"canonical account binding for {flow_role} is malformed")
        for field in (
            "providerAccount",
            "providerRecordSha256",
            "upstreamProviderFamily",
        ):
            if binding.get(field) != recorded.get(field):
                raise LaunchContractError(f"launch account identity for {flow_role} is not canonical")
        account = recorded["providerAccount"]
        if PROVIDER_ACCOUNT_NAME.fullmatch(account) is None:
            raise LaunchContractError(f"provider account name for {flow_role} is invalid")
        record, record_raw = _regular_json(
            humanize_home
            / "providers"
            / recorded["backend"]
            / account
            / "provider.json",
            f"Humanize provider record for {flow_role}",
        )
        if (
            record.get("cli") != recorded["backend"]
            or record.get("name") != recorded["providerAccount"]
            or _sha256(record_raw) != recorded["providerRecordSha256"]
        ):
            raise LaunchContractError(f"Humanize provider record for {flow_role} changed")

        nested = _validate_canonical_launch_receipt(
            recorded.get("launchReceipt"),
            policy=policy,
            policy_raw=policy_raw,
            semantic_role=semantic_role,
            task_class=str(role_contract["taskClass"]),
            posture=str(posture),
            review_mode=str(role_contract["reviewMode"]),
            writer_role=role_contract["writerRole"],
        )
        expected = _expected_sealed_role(
            nested,
            flow_role=flow_role,
            permission=str(role_contract["permission"]),
            provider_account=str(binding["providerAccount"]),
            provider_record_sha256=str(binding["providerRecordSha256"]),
        )
        if recorded != expected:
            raise LaunchContractError(
                f"sealed launch role {flow_role} does not equal its nested canonical receipt"
            )

    reviewer = next(item for item in recorded_roles if item["flowRole"] == "reviewer")
    writer_name = "reducer" if flow_name == "deep-research" else "knowledge"
    writer = next(item for item in recorded_roles if item["flowRole"] == writer_name)
    if reviewer["effort"] != "max":
        raise LaunchContractError("terminal reviewer must use Max effort")
    capability = policy.get("capabilityPolicy")
    pairings = capability.get("reviewPairings") if isinstance(capability, dict) else None
    if not isinstance(pairings, list):
        raise LaunchContractError("canonical model policy omits exact review pairings")
    matching = []
    for pairing in pairings:
        writer_policy = pairing.get("writer") if isinstance(pairing, dict) else None
        if isinstance(writer_policy, dict) and (
            writer_policy.get("upstreamProviderFamily") == writer["upstreamProviderFamily"]
            and writer_policy.get("modelFamily") == writer["modelFamily"]
            and isinstance(writer_policy.get("efforts"), list)
            and writer["effort"] in writer_policy["efforts"]
        ):
            matching.append(pairing)
    if len(matching) != 1:
        raise LaunchContractError("load-bearing writer has no unique exact review pairing")
    expected_reviewer = matching[0].get("reviewer")
    if not isinstance(expected_reviewer, dict) or any(
        expected_reviewer.get(field) != reviewer[key]
        for field, key in (
            ("role", "semanticRole"),
            ("upstreamProviderFamily", "upstreamProviderFamily"),
            ("modelFamily", "modelFamily"),
            ("effort", "effort"),
        )
    ):
        raise LaunchContractError("terminal reviewer violates the exact Max review pairing")
    writer_nested = writer.get("launchReceipt")
    reviewer_nested = reviewer.get("launchReceipt")
    if (
        not isinstance(writer_nested, dict)
        or not isinstance(reviewer_nested, dict)
        or reviewer_nested.get("writer") != writer_nested.get("resolved")
    ):
        raise LaunchContractError(
            "terminal reviewer is not linked to the sealed writer canonical receipt"
        )


def _agent_identity(agent: Any) -> dict[str, str]:
    config = getattr(agent, "config", None)
    return {
        "backend": str(getattr(agent, "backend", "")).strip(),
        "model": str(getattr(config, "model", "")).strip(),
        "effort": str(getattr(config, "effort", "")).strip(),
        "currentEffort": str(getattr(agent, "effort", "")).strip(),
        "providerAccount": str(getattr(config, "provider", "")).strip(),
        "permission": str(getattr(config, "permission", "")).strip(),
    }


def verify_launch(
    flow_name: str,
    named_agents: dict[str, Any],
    expected_roles: dict[str, str],
    task: str,
    setting: BaseModel,
) -> dict[str, Any]:
    """Bind this exact call to launcher-owned model policy and account identities."""

    descriptor_text = os.environ.get(LAUNCH_FD_ENV, "")
    expected_digest = os.environ.get(LAUNCH_SHA_ENV, "")
    if not descriptor_text.isdecimal() or int(descriptor_text) <= 2:
        raise LaunchContractError("trusted Humanize research launcher receipt is required")
    payload = _receipt_bytes(int(descriptor_text))
    if expected_digest != _sha256(payload):
        raise LaunchContractError("launch receipt digest does not match its sealed bytes")
    try:
        receipt = json.loads(payload)
    except json.JSONDecodeError as error:
        raise LaunchContractError("launch receipt is not valid JSON") from error
    if not isinstance(receipt, dict):
        raise LaunchContractError("launch receipt root must be an object")
    _strict_fields(
        receipt,
        {
            "schema",
            "runId",
            "flow",
            "projectRoot",
            "taskSha256",
            "config",
            "iterationContext",
            "humanFeedbackReceipt",
            "rolePolicySha256",
            "accountBindingsSha256",
            "referenceSources",
            "roles",
        },
        "launch receipt",
    )
    if receipt.get("schema") != LAUNCH_SCHEMA or receipt.get("flow") != flow_name:
        raise LaunchContractError("launch receipt names the wrong schema or flow")
    if receipt.get("projectRoot") != str(Path.cwd().resolve()):
        raise LaunchContractError("launch receipt is bound to a different project root")
    if receipt.get("taskSha256") != _sha256(task.encode("utf-8")):
        raise LaunchContractError("launch receipt is bound to a different task")
    if receipt.get("config") != serializable(setting):
        raise LaunchContractError("launch receipt is bound to a different flow config")
    iteration_context = receipt.get("iterationContext")
    if not isinstance(iteration_context, dict):
        raise LaunchContractError("launch receipt lacks its iteration context")
    _strict_fields(
        iteration_context,
        {
            "schema",
            "source",
            "pmIssuePath",
            "pmIssueSha256",
            "iterationModeSnapshotSha256",
            "legacyDefault",
        },
        "iteration context",
    )
    source = iteration_context.get("source")
    if iteration_context.get("schema") != ITERATION_CONTEXT_SCHEMA:
        raise LaunchContractError("launch receipt iteration context schema is unsupported")
    if source == "standalone":
        if any(
            iteration_context.get(field) is not None
            for field in (
                "pmIssuePath",
                "pmIssueSha256",
                "iterationModeSnapshotSha256",
            )
        ):
            raise LaunchContractError("standalone iteration context carries PM issue claims")
        if iteration_context.get("legacyDefault") is not False:
            raise LaunchContractError("standalone iteration context has a contradictory legacy marker")
    elif source == "pm_issue":
        issue_path = iteration_context.get("pmIssuePath")
        path = Path(str(issue_path))
        if (
            not isinstance(issue_path, str)
            or path.is_absolute()
            or len(path.parts) != 4
            or path.parts[:2] != (".pm", "issues")
            or path.suffix != ".md"
            or any(
                not isinstance(iteration_context.get(field), str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", iteration_context[field]) is None
                for field in ("pmIssueSha256", "iterationModeSnapshotSha256")
            )
            or not isinstance(iteration_context.get("legacyDefault"), bool)
        ):
            raise LaunchContractError("PM-bound iteration context is malformed")
    else:
        raise LaunchContractError("launch receipt iteration context source is unsupported")
    feedback_receipt = receipt.get("humanFeedbackReceipt")
    configured_feedback = getattr(setting, "human_feedback", None)
    if configured_feedback is None:
        if feedback_receipt is not None:
            raise LaunchContractError("launch receipt invents human feedback")
    else:
        if not isinstance(feedback_receipt, dict):
            raise LaunchContractError("launch receipt omits bound human feedback")
        _strict_fields(
            feedback_receipt,
            {
                "schema",
                "checkpoint_artifact",
                "checkpoint_sha256",
                "checkpoint_id",
                "human_feedback",
                "received_at",
                "receipt_sha256",
            },
            "human feedback receipt",
        )
        try:
            _utc_timestamp(
                feedback_receipt.get("received_at"),
                "human feedback received_at",
            )
        except ValueError as error:
            raise LaunchContractError(str(error)) from error
        unsigned_feedback = dict(feedback_receipt)
        feedback_digest = unsigned_feedback.pop("receipt_sha256", None)
        if (
            feedback_receipt.get("schema") != "humanize-human-feedback-receipt/v1"
            or feedback_receipt.get("human_feedback") != configured_feedback
            or feedback_receipt.get("checkpoint_artifact")
            != getattr(setting, "human_feedback_checkpoint_artifact", None)
            or feedback_receipt.get("checkpoint_sha256")
            != getattr(setting, "human_feedback_checkpoint_sha256", None)
            or not isinstance(feedback_receipt.get("checkpoint_id"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", feedback_receipt["checkpoint_id"])
            is None
            or _canonical_sha256(unsigned_feedback) != feedback_digest
        ):
            raise LaunchContractError("human feedback receipt is malformed or unbound")
    for field in ("runId", "rolePolicySha256", "accountBindingsSha256"):
        if not isinstance(receipt.get(field), str) or not receipt[field]:
            raise LaunchContractError(f"launch receipt {field} is missing")
    reference_sources = receipt.get("referenceSources")
    if not isinstance(reference_sources, dict):
        raise LaunchContractError("launch receipt lacks its approved reference binding")
    _strict_fields(
        reference_sources,
        {"schema", "approval", "selectedFlow", "files"},
        "reference source binding",
    )
    source_digests = reference_sources.get("files")
    if (
        reference_sources.get("schema") != REFERENCE_BINDING_SCHEMA
        or reference_sources.get("approval") != "launcher-owned-approved-sha256"
        or reference_sources.get("selectedFlow") != flow_name
        or not isinstance(source_digests, dict)
        or set(source_digests) != REFERENCE_SOURCE_FILES
        or not all(
            isinstance(value, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
            for value in source_digests.values()
        )
    ):
        raise LaunchContractError("launch receipt reference binding is malformed")
    roles = receipt.get("roles")
    if not isinstance(roles, list) or len(roles) != len(named_agents):
        raise LaunchContractError("launch receipt role count does not match the flow")
    if len({id(agent) for agent in named_agents.values()}) != len(named_agents):
        raise LaunchContractError("every Humanize flow role requires a distinct agent instance")

    role_fields = LAUNCH_ROLE_FIELDS
    upstream_by_role: dict[str, str] = {}
    public_roles: list[dict[str, str]] = []
    for (flow_role, agent), recorded in zip(named_agents.items(), roles, strict=True):
        if not isinstance(recorded, dict):
            raise LaunchContractError("launch receipt roles must be objects")
        _strict_fields(recorded, role_fields, f"launch role {flow_role}")
        if recorded.get("flowRole") != flow_role:
            raise LaunchContractError("launch receipt role order does not match the flow")
        if recorded.get("semanticRole") != expected_roles.get(flow_role):
            raise LaunchContractError(f"launch receipt changes the fixed role for {flow_role}")
        actual = _agent_identity(agent)
        for receipt_field, actual_field in (
            ("backend", "backend"),
            ("model", "model"),
            ("effort", "effort"),
            ("providerAccount", "providerAccount"),
            ("permission", "permission"),
        ):
            if recorded.get(receipt_field) != actual[actual_field]:
                raise LaunchContractError(
                    f"actual {flow_role} {receipt_field} differs from the launch receipt"
                )
        if actual["currentEffort"] != recorded.get("effort"):
            raise LaunchContractError(f"{flow_role} effort changed after launch")
        if recorded.get("model") != recorded.get("catalogModelId"):
            raise LaunchContractError(
                f"launch role {flow_role} concrete catalog ID drifted from the sealed model"
            )
        if not all(
            isinstance(recorded.get(field), str) and recorded[field]
            for field in (
                "modelFamily",
                "upstreamProviderFamily",
                "intelligenceClass",
                "providerAccount",
                "providerRecordSha256",
                "canonicalModel",
                "catalogModelId",
                "provider",
                "dispatchStatus",
            )
        ):
            raise LaunchContractError(f"launch identity for {flow_role} is incomplete")
        if not isinstance(recorded.get("launchReceipt"), dict):
            raise LaunchContractError(f"launch role {flow_role} lacks a nested canonical receipt")
        upstream_by_role[flow_role] = recorded["upstreamProviderFamily"]
        public_roles.append(
            {
                key: str(recorded[key])
                for key in (
                    "flowRole",
                    "semanticRole",
                    "backend",
                    "model",
                    "canonicalModel",
                    "catalogModelId",
                    "modelFamily",
                    "effort",
                    "upstreamProviderFamily",
                    "providerAccount",
                    "providerRecordSha256",
                    "permission",
                )
            }
        )
    reviewer_upstream = upstream_by_role.get("reviewer")
    if not reviewer_upstream or any(
        upstream == reviewer_upstream
        for role, upstream in upstream_by_role.items()
        if role != "reviewer"
    ):
        raise LaunchContractError(
            "terminal reviewer must differ from every writing role by upstream provider"
        )
    _verify_policy_and_accounts(flow_name, receipt, roles)
    return {
        "schema": LAUNCH_SCHEMA,
        "runId": receipt["runId"],
        "receiptSha256": expected_digest,
        "taskSha256": receipt["taskSha256"],
        "rolePolicySha256": receipt["rolePolicySha256"],
        "accountBindingsSha256": receipt["accountBindingsSha256"],
        "iterationContext": iteration_context,
        "humanFeedbackReceipt": feedback_receipt,
        "referenceSources": reference_sources,
        "roles": public_roles,
    }


def failure_category(error: Exception) -> str:
    if isinstance(error, LaunchContractError):
        return "launch-contract-failure"
    if isinstance(error, RuntimeError) and "budget exhausted" in str(error):
        return "model-call-budget-exhausted"
    if isinstance(error, (ValueError, TypeError)):
        return "structured-contract-failure"
    return "flow-infrastructure-failure"


def write_failure_terminal(
    flow_name: str,
    terminal_artifact: str,
    error: Exception,
    model_calls_used: int,
    launch: dict[str, Any] | None,
) -> None:
    """Atomically preserve every controlled failure as unresolved, never human-blocked."""

    write_terminal(
        terminal_artifact,
        {
            "schema": "research-terminal/v1",
            "flow": flow_name,
            "terminal": "UNRESOLVED_INFRASTRUCTURE",
            "domain_terminal": "UNRESOLVED_INFRASTRUCTURE",
            "failure_category": failure_category(error),
            "reason": str(error)[:2000] or type(error).__name__,
            "human_dependency": None,
            "model_calls_used": model_calls_used,
            "terminal_review": None,
            "launch": launch,
        },
    )


def unsupported_diversity_terminal(
    flow_name: str,
    terminal_artifact: str,
    launch: dict[str, Any],
) -> None:
    """Fail closed when a requested diversity backend is absent from Humanize."""

    write_terminal(
        terminal_artifact,
        {
            "schema": "research-terminal/v1",
            "flow": flow_name,
            "terminal": "UNSUPPORTED_HUMANIZE_CAPABILITY",
            "domain_terminal": "UNSUPPORTED_HUMANIZE_CAPABILITY",
            "route_to": "subagent-scheduler",
            "reason": (
                "cursor-agent diversity was required, but this Humanize runtime does not "
                "provide that backend; no substitute was selected"
            ),
            "model_calls_used": 0,
            "terminal_review": None,
            "launch": launch,
        },
    )


def reviewed_terminal(domain_terminal: str, review: TerminalReview) -> str:
    """Keep domain meaning when approved; otherwise expose the reviewer's stop route."""

    return domain_terminal if review.verdict == "COMPLETE" else review.verdict
