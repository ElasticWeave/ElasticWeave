#!/usr/bin/env python3
"""Fail-closed launcher for the trusted native Humanize research flows."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


HUMANIZE_WORKFLOW_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "humanize-workflow" / "scripts"
)
if str(HUMANIZE_WORKFLOW_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(HUMANIZE_WORKFLOW_SCRIPTS))

from execution_context import (
    ExecutionContextError,
    build_execution_context,
    current_account,
)


CONFIG_SYNC_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "update-platform-config"
    / "scripts"
    / "config-sync"
)
if str(CONFIG_SYNC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CONFIG_SYNC_SCRIPTS))

from resolve_launch_spec import (
    LaunchSpecError,
    canonical_sha256,
    digest_bytes,
    resolve_launch_spec,
)
from live_catalog import CatalogError, normalize_cursor_catalog_id
from validate_model_roles import ValidationError as ModelRoleValidationError
from validate_model_roles import validate as validate_model_roles


# The upstream pin exposes Cursor, but this launcher's audited execution context does not stage
# it. An Anthropic model reached through Cursor is not interchangeable with the Claude CLI.
SUPPORTED_BACKENDS = {"claude", "codex"}
CANONICAL_RECEIPT_SCHEMA = "workflow-native-launch-receipt/v1"
PROVIDER_ACCOUNT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
RECEIPT_FD_ENV = "HUMANIZE_RESEARCH_LAUNCH_FD"
RECEIPT_SHA_ENV = "HUMANIZE_RESEARCH_LAUNCH_SHA256"
FLOW_FILES = {
    "deep-research": "deep_research.py",
    "auto-research": "auto_research.py",
}
REFERENCE_BINDING_SCHEMA = "humanize-research-reference-binding/v1"
REFERENCE_SOURCE_FILES = (
    "_research_reference.py",
    "auto_research.py",
    "deep_research.py",
)
ITERATION_SNAPSHOT_KEYS = (
    "iteration_mode_selected",
    "iteration_mode_effective",
    "human_checkin_lease_minutes",
    "human_unavailable_fallback",
    "human_checkin_lease_expires_at",
    "human_checkin_lease_status",
    "iteration_mode_decision_at",
)
ITERATION_CONFIG_KEYS = {
    "iteration_mode",
    "human_checkin_lease_minutes",
    "human_unavailable_fallback",
    "human_checkin_lease_expires_at",
}
APPROVED_REFERENCE_SHA256 = {
    "_research_reference.py": "sha256:76954cc31d5f7c77eff772efab35e0470c9c8b2813b8314e26e212432c9b4881",
    "auto_research.py": "sha256:0c85975d126f2161d2e3115142bef575901434f99e57bca515d8e0884634468e",
    "deep_research.py": "sha256:5650567a61609f27209f9545d5b75add89ecd9452f4ae2fb8208c543009d7e35",
}
FLOW_ROLE_POLICY = {
    "deep-research": (
        ("preflight", "plan", "read-only"),
        ("collector", "research_collect", "read-only"),
        ("counterevidence", "research_collect", "read-only"),
        ("reducer", "frontier_execute", "read-only"),
        ("reviewer", "advisor", "read-only"),
    ),
    "auto-research": (
        ("knowledge", "frontier_execute", "read-only"),
        ("applicability", "plan", "read-only"),
        ("experimenter", "task", "workspace-write"),
        ("reviewer", "advisor", "read-only"),
    ),
}
DEFAULT_CONFIGS: dict[str, dict[str, Any]] = {
    "deep-research": {
        "iteration_mode": "autonomous",
        "human_checkin_lease_minutes": None,
        "human_checkin_lease_expires_at": None,
        "human_unavailable_fallback": "pause",
        "human_feedback": None,
        "human_feedback_checkpoint_artifact": None,
        "human_feedback_checkpoint_sha256": None,
        "branch_count": 3,
        "max_model_calls": 8,
        "terminal_artifact": ".humanize/artifacts/deep-research-terminal.json",
        "cursor_diversity_required": False,
    },
    "auto-research": {
        "iteration_mode": "autonomous",
        "human_checkin_lease_minutes": None,
        "human_checkin_lease_expires_at": None,
        "human_unavailable_fallback": "pause",
        "human_feedback": None,
        "human_feedback_checkpoint_artifact": None,
        "human_feedback_checkpoint_sha256": None,
        "max_trials": 2,
        "max_model_calls": 7,
        "terminal_artifact": ".humanize/artifacts/auto-research-terminal.json",
        "cursor_diversity_required": False,
        "resources": None,
    },
}
RESOURCE_DEFAULTS: dict[str, Any] = {
    "integrity": "VALID",
    "invalid_reason": None,
    "compute_available": False,
    "data_available": False,
    "harness_available": False,
    "budget_available": False,
    "correctness_gate_available": False,
    "isolation_available": False,
    "authorized": False,
    "resource_refs": [],
}


class LaunchError(ValueError):
    """A requested native research launch is not fully bound and cannot run."""


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise LaunchError(f"{label} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise LaunchError(f"{label} is not a valid UTC timestamp") from error


def _regular_bytes(path: Path, label: str, *, single_link: bool = False) -> bytes:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise LaunchError(f"{label} requires Linux no-follow file-descriptor support")
    expanded = path.expanduser()
    parts = list(expanded.parts)
    if expanded.is_absolute():
        start = expanded.anchor
        parts = parts[1:]
    else:
        start = "."
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise LaunchError(f"{label} has an unsafe path")
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    leaf_flags = os.O_RDONLY | os.O_NOFOLLOW | close_on_exec
    directory_fd: int | None = None
    leaf_fd: int | None = None
    try:
        directory_fd = os.open(start, directory_flags)
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            previous_fd = directory_fd
            directory_fd = next_fd
            os.close(previous_fd)
        leaf_fd = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
        status = os.fstat(leaf_fd)
        if not stat.S_ISREG(status.st_mode) or (single_link and status.st_nlink != 1):
            raise LaunchError(f"{label} must be a regular non-symlink file")
        before = os.fstat(leaf_fd)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(leaf_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(leaf_fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise LaunchError(f"{label} changed while it was read")
        return b"".join(chunks)
    except LaunchError:
        raise
    except OSError as error:
        raise LaunchError(
            f"{label} must be a regular file with no linked path component: {error}"
        ) from error
    finally:
        try:
            if leaf_fd is not None:
                os.close(leaf_fd)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)


def _object(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _regular_bytes(path, label)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LaunchError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise LaunchError(f"{label} root must be an object")
    return value, raw


def _strict_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise LaunchError(f"unknown {label} fields: {', '.join(unknown)}")


def _legacy_issue_snapshot() -> dict[str, str]:
    snapshot = {
        "iteration_mode_selected": "autonomous",
        "iteration_mode_effective": "autonomous",
        "human_checkin_lease_minutes": "none",
        "human_unavailable_fallback": "pause",
        "human_checkin_lease_expires_at": "none",
        "human_checkin_lease_status": "not_applicable",
        "iteration_mode_decision_at": "1970-01-01T00:00:00Z",
    }
    return {**snapshot, "iteration_mode_snapshot_sha256": canonical_sha256(snapshot)}


def _issue_iteration_snapshot(raw: bytes) -> tuple[dict[str, str], bool]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise LaunchError("PM issue must be UTF-8") from error
    if not lines or lines[0] != "---":
        raise LaunchError("PM issue must start with frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise LaunchError("PM issue frontmatter is unterminated") from error
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or not key or key in metadata:
            raise LaunchError("PM issue frontmatter is malformed or has duplicate keys")
        metadata[key] = value.strip()
    required = set(ITERATION_SNAPSHOT_KEYS) | {"iteration_mode_snapshot_sha256"}
    present = required.intersection(metadata)
    if not present:
        return _legacy_issue_snapshot(), True
    if present != required:
        raise LaunchError("PM issue has a missing or partial iteration-mode snapshot")
    snapshot = {key: metadata[key] for key in ITERATION_SNAPSHOT_KEYS}
    if metadata["iteration_mode_snapshot_sha256"] != canonical_sha256(snapshot):
        raise LaunchError("PM issue iteration-mode snapshot digest is invalid")
    selected = snapshot["iteration_mode_selected"]
    effective = snapshot["iteration_mode_effective"]
    fallback = snapshot["human_unavailable_fallback"]
    status = snapshot["human_checkin_lease_status"]
    decision = _parse_utc(snapshot["iteration_mode_decision_at"], "iteration_mode_decision_at")
    lease = snapshot["human_checkin_lease_minutes"]
    expiry = snapshot["human_checkin_lease_expires_at"]
    valid = False
    if selected == "autonomous":
        valid = (
            effective == "autonomous"
            and status == "not_applicable"
            and lease == "none"
            and expiry == "none"
            and fallback in {"pause", "autonomous_review"}
        )
    elif selected == "human_guided" and fallback in {"pause", "autonomous_review"}:
        try:
            lease_value = int(lease)
        except ValueError:
            lease_value = 0
        if 5 <= lease_value <= 1440 and expiry != "none":
            expiry_time = _parse_utc(expiry, "human_checkin_lease_expires_at")
            valid = (
                status == "active"
                and effective == "human_guided"
                and expiry_time > decision
            ) or (
                status == "expired"
                and effective == "autonomous"
                and fallback == "autonomous_review"
                and expiry_time <= decision
            )
    if not valid:
        raise LaunchError("PM issue iteration-mode snapshot is contradictory")
    return (
        {
            **snapshot,
            "iteration_mode_snapshot_sha256": metadata["iteration_mode_snapshot_sha256"],
        },
        False,
    )


def _pm_issue_path(root: Path, value: Path) -> tuple[Path, str]:
    if (
        value.is_absolute()
        or len(value.parts) != 4
        or value.parts[:2] != (".pm", "issues")
        or value.suffix != ".md"
        or any(part in {"", ".", ".."} for part in value.parts)
    ):
        raise LaunchError("--pm-issue must name .pm/issues/<milestone>/<issue>.md")
    return root / value, value.as_posix()


def resolve_iteration_context(
    root: Path,
    flow_name: str,
    supplied: dict[str, Any],
    *,
    pm_issue: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind a standalone config or derive iteration fields from one PM issue snapshot."""
    if pm_issue is None:
        return normalize_config(flow_name, supplied), {
            "schema": "humanize-research-iteration-context/v1",
            "source": "standalone",
            "pmIssuePath": None,
            "pmIssueSha256": None,
            "iterationModeSnapshotSha256": None,
            "legacyDefault": False,
        }
    conflicting = sorted(ITERATION_CONFIG_KEYS.intersection(supplied))
    if conflicting:
        raise LaunchError(
            "PM issue is the only iteration source; remove config fields: "
            + ", ".join(conflicting)
        )
    issue_path, relative = _pm_issue_path(root, pm_issue)
    raw = _regular_bytes(issue_path, "PM issue", single_link=True)
    snapshot, legacy_default = _issue_iteration_snapshot(raw)
    lease = snapshot["human_checkin_lease_minutes"]
    derived = {
        "iteration_mode": snapshot["iteration_mode_selected"],
        "human_checkin_lease_minutes": None if lease == "none" else int(lease),
        "human_unavailable_fallback": snapshot["human_unavailable_fallback"],
        "human_checkin_lease_expires_at": (
            None
            if snapshot["human_checkin_lease_expires_at"] == "none"
            else snapshot["human_checkin_lease_expires_at"]
        ),
    }
    setting = normalize_config(flow_name, {**supplied, **derived})
    return setting, {
        "schema": "humanize-research-iteration-context/v1",
        "source": "pm_issue",
        "pmIssuePath": relative,
        "pmIssueSha256": _sha256(raw),
        "iterationModeSnapshotSha256": snapshot["iteration_mode_snapshot_sha256"],
        "legacyDefault": legacy_default,
    }


def _revalidate_iteration_context(root: Path, binding: dict[str, Any]) -> None:
    if binding["source"] == "standalone":
        return
    issue_path, _ = _pm_issue_path(root, Path(binding["pmIssuePath"]))
    raw = _regular_bytes(issue_path, "PM issue", single_link=True)
    snapshot, legacy_default = _issue_iteration_snapshot(raw)
    if (
        _sha256(raw) != binding["pmIssueSha256"]
        or snapshot["iteration_mode_snapshot_sha256"]
        != binding["iterationModeSnapshotSha256"]
        or legacy_default is not binding["legacyDefault"]
    ):
        raise LaunchError("PM issue changed during native research dispatch")


def _integer(value: object, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise LaunchError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise LaunchError(f"{label} must be boolean")
    return value


def _normalize_iteration_config(setting: dict[str, Any]) -> None:
    mode = setting["iteration_mode"]
    if mode not in {"autonomous", "human_guided"}:
        raise LaunchError("iteration_mode must be autonomous or human_guided")
    fallback = setting["human_unavailable_fallback"]
    if fallback not in {"pause", "autonomous_review"}:
        raise LaunchError(
            "human_unavailable_fallback must be pause or autonomous_review"
        )
    feedback = setting["human_feedback"]
    checkpoint_artifact = setting["human_feedback_checkpoint_artifact"]
    checkpoint_sha256 = setting["human_feedback_checkpoint_sha256"]
    if feedback is not None and (
        not isinstance(feedback, str)
        or not feedback.strip()
        or len(feedback) > 8192
    ):
        raise LaunchError(
            "human_feedback must be null or a non-blank string of at most 8192 characters"
        )
    lease = setting["human_checkin_lease_minutes"]
    expiry = setting["human_checkin_lease_expires_at"]
    if mode == "autonomous":
        if lease is not None or expiry is not None:
            raise LaunchError("autonomous mode cannot carry a human check-in lease")
        if feedback is not None:
            raise LaunchError("autonomous mode cannot carry human checkpoint feedback")
        if checkpoint_artifact is not None or checkpoint_sha256 is not None:
            raise LaunchError("autonomous mode cannot carry a human feedback checkpoint")
        return
    if lease is None or expiry is None:
        raise LaunchError("human_guided mode requires lease minutes and UTC expiry")
    _integer(lease, 5, 1440, "human_checkin_lease_minutes")
    if not isinstance(expiry, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", expiry
    ):
        raise LaunchError(
            "human_checkin_lease_expires_at must be an ISO-8601 UTC timestamp ending in Z"
        )
    try:
        expires_at = datetime.fromisoformat(expiry[:-1] + "+00:00")
    except ValueError as error:
        raise LaunchError("human_checkin_lease_expires_at is invalid") from error
    if expires_at <= datetime.now(timezone.utc) and fallback == "pause":
        raise LaunchError(
            "human_guided lease expired with pause fallback; renew it or select autonomous"
        )
    bindings = (feedback, checkpoint_artifact, checkpoint_sha256)
    if any(value is not None for value in bindings) and not all(
        value is not None for value in bindings
    ):
        raise LaunchError(
            "human feedback requires both checkpoint artifact and checkpoint SHA-256"
        )
    if checkpoint_artifact is not None:
        checkpoint_path = Path(checkpoint_artifact)
        if (
            checkpoint_path.is_absolute()
            or ".." in checkpoint_path.parts
            or checkpoint_path.parts[:2] != (".humanize", "artifacts")
        ):
            raise LaunchError(
                "human feedback checkpoint artifact must be under .humanize/artifacts"
            )
        if not isinstance(checkpoint_sha256, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", checkpoint_sha256
        ):
            raise LaunchError("human feedback checkpoint SHA-256 is invalid")


def _terminal_path(value: object) -> str:
    if not _text(value):
        raise LaunchError("terminal_artifact must be a non-empty project-relative path")
    path = Path(str(value))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise LaunchError("terminal_artifact must be a non-traversing project-relative path")
    return str(path)


def normalize_config(flow_name: str, supplied: dict[str, Any]) -> dict[str, Any]:
    """Return one complete strict config; role/model overrides are never accepted."""

    if flow_name not in DEFAULT_CONFIGS:
        raise LaunchError(f"unsupported native research flow: {flow_name}")
    if not isinstance(supplied, dict):
        raise LaunchError("flow config must be a JSON object")
    allowed = set(DEFAULT_CONFIGS[flow_name])
    _strict_keys(supplied, allowed, "config")
    setting = {**DEFAULT_CONFIGS[flow_name], **supplied}
    setting["terminal_artifact"] = _terminal_path(setting["terminal_artifact"])
    setting["cursor_diversity_required"] = _boolean(
        setting["cursor_diversity_required"], "cursor_diversity_required"
    )
    _normalize_iteration_config(setting)
    if flow_name == "deep-research":
        branches = _integer(setting["branch_count"], 1, 4, "branch_count")
        maximum = _integer(setting["max_model_calls"], 5, 9, "max_model_calls")
        if maximum < branches + 4:
            raise LaunchError(
                f"max_model_calls must cover {branches + 4} static calls for branch_count"
            )
        return setting

    trials = _integer(setting["max_trials"], 1, 3, "max_trials")
    maximum = _integer(setting["max_model_calls"], 3, 8, "max_model_calls")
    if maximum < trials + 4:
        raise LaunchError(
            f"max_model_calls must cover {trials + 4} static calls for max_trials"
        )
    resources = setting["resources"]
    if resources is not None:
        if not isinstance(resources, dict):
            raise LaunchError("resources must be null or an object")
        _strict_keys(resources, set(RESOURCE_DEFAULTS), "resources")
        resources = {**RESOURCE_DEFAULTS, **resources}
        if resources["integrity"] not in {"VALID", "INVALID"}:
            raise LaunchError("resources.integrity must be VALID or INVALID")
        if resources["integrity"] == "INVALID":
            if not _text(resources["invalid_reason"]):
                raise LaunchError("INVALID resources require invalid_reason")
        elif resources["invalid_reason"] is not None:
            raise LaunchError("invalid_reason is valid only for INVALID resources")
        for name in (
            "compute_available",
            "data_available",
            "harness_available",
            "budget_available",
            "correctness_gate_available",
            "isolation_available",
            "authorized",
        ):
            resources[name] = _boolean(resources[name], f"resources.{name}")
        if not isinstance(resources["resource_refs"], list) or not all(
            _text(item) for item in resources["resource_refs"]
        ):
            raise LaunchError("resources.resource_refs must be a string list")
    setting["resources"] = resources
    return setting


def _validate_human_feedback_checkpoint(
    root: Path,
    setting: dict[str, Any],
    iteration_context: dict[str, Any],
    task_sha256: str,
    *,
    received_at: str | None = None,
) -> dict[str, Any] | None:
    """Bind resumed feedback to one exact, reviewed prior observation artifact."""

    feedback = setting["human_feedback"]
    if feedback is None:
        return None
    relative = str(setting["human_feedback_checkpoint_artifact"])
    if relative == setting["terminal_artifact"]:
        raise LaunchError(
            "human feedback checkpoint artifact must differ from this run's terminal artifact"
        )
    previous, raw = _object(
        root / relative, "human feedback checkpoint artifact"
    )
    if _sha256(raw) != setting["human_feedback_checkpoint_sha256"]:
        raise LaunchError("human feedback checkpoint artifact SHA-256 does not match")
    review = previous.get("terminal_review")
    checkpoint = previous.get("human_observation_checkpoint")
    launch = previous.get("launch")
    expected_review_keys = {"verdict", "criteria", "issues", "human_dependency"}
    expected_checkpoint_keys = {
        "checkpoint_id",
        "hypothesis",
        "actual_operation",
        "raw_observation",
        "agent_interpretation",
        "anomaly_or_alternate_interpretation",
        "candidate_next_tasks",
        "human_feedback",
        "questions_for_human",
        "next_planning_allowed",
    }
    if (
        previous.get("schema") != "research-terminal/v1"
        or previous.get("terminal") != "HUMAN_OBSERVATION_REQUIRED"
        or previous.get("domain_terminal") != "HUMAN_OBSERVATION_REQUIRED"
        or previous.get("next_planning_allowed") is not False
        or not isinstance(review, dict)
        or set(review) != expected_review_keys
        or review.get("verdict") != "COMPLETE"
        or not isinstance(review.get("criteria"), list)
        or not review["criteria"]
        or any(
            not isinstance(criterion, dict)
            or criterion.get("passed") is not True
            or not _text(criterion.get("criterion_id"))
            or not _text(criterion.get("evidence"))
            for criterion in review["criteria"]
        )
        or review.get("issues") != []
        or review.get("human_dependency") is not None
        or not isinstance(checkpoint, dict)
        or set(checkpoint) != expected_checkpoint_keys
    ):
        raise LaunchError(
            "human feedback checkpoint artifact is not a reviewed observation boundary"
        )
    if not isinstance(launch, dict):
        raise LaunchError("human feedback checkpoint artifact lacks launch provenance")
    if launch.get("taskSha256") != task_sha256:
        raise LaunchError("human feedback checkpoint artifact is bound to a different task")
    if launch.get("iterationContext") != iteration_context:
        raise LaunchError(
            "human feedback checkpoint artifact is bound to a different iteration context"
        )
    body_keys = (
        "hypothesis",
        "actual_operation",
        "raw_observation",
        "agent_interpretation",
        "anomaly_or_alternate_interpretation",
        "candidate_next_tasks",
        "human_feedback",
    )
    if (
        checkpoint.get("next_planning_allowed") is not False
        or checkpoint.get("human_feedback") is not None
        or not isinstance(checkpoint.get("questions_for_human"), list)
        or not checkpoint["questions_for_human"]
        or not all(_text(question) for question in checkpoint["questions_for_human"])
        or checkpoint.get("checkpoint_id")
        != canonical_sha256({key: checkpoint.get(key) for key in body_keys})
    ):
        raise LaunchError(
            "human feedback checkpoint artifact has an invalid checkpoint commitment"
        )
    timestamp = (
        received_at
        if received_at is not None
        else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    _parse_utc(timestamp, "human feedback received_at")
    receipt = {
        "schema": "humanize-human-feedback-receipt/v1",
        "checkpoint_artifact": relative,
        "checkpoint_sha256": setting["human_feedback_checkpoint_sha256"],
        "checkpoint_id": checkpoint["checkpoint_id"],
        "human_feedback": feedback,
        "received_at": timestamp,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _revalidate_human_feedback_checkpoint(
    root: Path,
    setting: dict[str, Any],
    iteration_context: dict[str, Any],
    task_sha256: str,
    receipt: dict[str, Any] | None,
) -> None:
    if receipt is None:
        if setting["human_feedback"] is not None:
            raise LaunchError("human feedback receipt disappeared during dispatch")
        return
    current = _validate_human_feedback_checkpoint(
        root,
        setting,
        iteration_context,
        task_sha256,
        received_at=str(receipt.get("received_at", "")),
    )
    if current != receipt:
        raise LaunchError("human feedback checkpoint changed during native research dispatch")


def _validate_policy(document: dict[str, Any]) -> None:
    """Delegate the complete models-v3 contract to its canonical owner."""

    try:
        validate_model_roles(document)
    except ModelRoleValidationError as error:
        raise LaunchError(str(error)) from error


def _required_class(flow_role: str) -> str:
    return {
        "preflight": "planning",
        "collector": "collection",
        "counterevidence": "collection",
        "reducer": "topIntelligence",
        "knowledge": "topIntelligence",
        "applicability": "planning",
        "experimenter": "analysis",
        "reviewer": "verification",
    }[flow_role]


def _account_binding(
    humanize_home: Path,
    role: dict[str, str],
    binding: object,
) -> dict[str, str]:
    if not isinstance(binding, dict):
        raise LaunchError(f"account binding for {role['semanticRole']} must be an object")
    if set(binding) != {
        "providerAccount",
        "providerRecordSha256",
        "upstreamProviderFamily",
    }:
        raise LaunchError(f"account binding for {role['semanticRole']} has invalid fields")
    account = binding.get("providerAccount")
    digest = binding.get("providerRecordSha256")
    upstream = binding.get("upstreamProviderFamily")
    if not all(_text(item) for item in (account, digest, upstream)):
        raise LaunchError(f"account binding for {role['semanticRole']} is incomplete")
    if not isinstance(account, str) or PROVIDER_ACCOUNT_NAME.fullmatch(account) is None:
        raise LaunchError(
            f"account binding for {role['semanticRole']} has an invalid provider account name"
        )
    if upstream != role["upstreamProviderFamily"]:
        raise LaunchError(
            f"account binding for {role['semanticRole']} disagrees with the role's upstream family"
        )
    record_path = humanize_home / "providers" / role["backend"] / str(account) / "provider.json"
    record, raw = _object(record_path, f"provider record {role['backend']}/{account}")
    if record.get("cli") != role["backend"] or record.get("name") != account:
        raise LaunchError(f"provider record identity mismatch for {role['semanticRole']}")
    if digest != _sha256(raw):
        raise LaunchError(f"provider record digest mismatch for {role['semanticRole']}")
    return {
        **role,
        "providerAccount": str(account),
        "providerRecordSha256": str(digest),
        "permission": "",
    }


def _canonical_launch_identity(
    receipt: object,
    *,
    policy_raw: bytes,
    document: dict[str, Any],
    semantic_role: str,
    task_class: str,
    reasoning_posture: str,
    review_mode: str,
    writer_role: str | None,
) -> dict[str, Any]:
    """Validate the complete receipt emitted by the shared fresh resolver."""
    if not isinstance(receipt, dict) or receipt.get("schema") != CANONICAL_RECEIPT_SCHEMA:
        raise LaunchError("canonical resolver did not return a workflow-native launch receipt")
    unsigned = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    if receipt.get("receiptSha256") != canonical_sha256(unsigned):
        raise LaunchError("canonical launch receipt self-digest is invalid")
    request = receipt.get("request")
    if not isinstance(request, dict) or request != {
        "taskClass": task_class,
        "reasoningPosture": reasoning_posture,
        "semanticRole": semantic_role,
        "writerRole": writer_role,
        "reviewMode": review_mode,
    }:
        raise LaunchError("canonical launch receipt request differs from the fixed flow role")
    policy = receipt.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("modelsDocumentSha256") != digest_bytes(policy_raw)
        or policy.get("liveCatalogAuthenticatedByResolver") is not True
        or not isinstance(policy.get("freshCatalogEvidence"), dict)
    ):
        raise LaunchError(
            "canonical launch receipt does not bind current policy bytes and fresh catalog evidence"
        )
    identity = receipt.get("resolved")
    configured = (
        document.get("roles", {}).get(semantic_role)
        if isinstance(document.get("roles"), dict)
        else None
    )
    if not isinstance(identity, dict) or not isinstance(configured, dict):
        raise LaunchError("canonical launch receipt has no resolved identity")
    for field in (
        "semanticRole",
        "backend",
        "provider",
        "upstreamProviderFamily",
        "model",
        "modelFamily",
        "effort",
        "intelligenceClass",
        "dispatchStatus",
    ):
        expected = semantic_role if field == "semanticRole" else configured.get(field)
        if identity.get(field) != expected:
            raise LaunchError(
                f"canonical launch receipt identity differs from current {semantic_role}.{field}"
            )
    concrete = identity.get("catalogModelId")
    if not isinstance(concrete, str) or not concrete:
        raise LaunchError("canonical launch receipt has no concrete catalog model ID")
    if identity.get("backend") not in policy["freshCatalogEvidence"]:
        raise LaunchError(
            "canonical launch receipt lacks fresh catalog evidence for its resolved backend"
        )
    evidence = policy["freshCatalogEvidence"][identity["backend"]]
    if not isinstance(evidence, dict) or evidence.get("backend") != identity["backend"]:
        raise LaunchError(
            "canonical launch receipt fresh catalog evidence does not bind its backend"
        )
    coverage = evidence.get("coverage")
    if not isinstance(coverage, dict):
        raise LaunchError("canonical launch receipt fresh catalog evidence has no coverage")
    backend = identity["backend"]
    model = configured.get("model")
    effort = configured.get("effort")
    if backend in {"claude", "codex"}:
        efforts = coverage.get(model)
        if (
            concrete != model
            or not isinstance(efforts, list)
            or effort not in efforts
        ):
            raise LaunchError(
                "canonical launch receipt concrete catalog model is not covered by fresh evidence"
            )
    elif backend == "cursor":
        ids = coverage.get("__cursor_ids__")
        try:
            expected_concrete = normalize_cursor_catalog_id(str(model), str(effort))
        except CatalogError as error:
            raise LaunchError(
                "canonical launch receipt has no audited Cursor concrete model mapping"
            ) from error
        if (
            concrete != expected_concrete
            or not isinstance(ids, list)
            or concrete not in ids
        ):
            raise LaunchError(
                "canonical launch receipt concrete catalog model is not covered by fresh evidence"
            )
    else:
        raise LaunchError(
            f"canonical launch receipt backend {backend!r} has no audited catalog verifier"
        )
    return identity


def resolve_launch_identity(
    root: Path,
    flow_name: str,
    account_bindings_path: Path,
    humanize_home: Path,
    *,
    catalog_runner: Callable | None = None,
) -> dict[str, Any]:
    """Resolve the fixed multi-role flow policy and explicit credential-account bindings."""

    if flow_name not in FLOW_ROLE_POLICY:
        raise LaunchError(f"unsupported native research flow: {flow_name}")
    role_policy_path = root / "config/workflows/models.json"
    document, policy_raw = _object(role_policy_path, "model role policy")
    _validate_policy(document)
    reasoning = document.get("reasoningPolicy")
    posture = reasoning.get("defaultPosture") if isinstance(reasoning, dict) else None
    if not isinstance(posture, str) or not posture:
        raise LaunchError("canonical model policy has no explicit defaultPosture")
    postures = reasoning.get("postures") if isinstance(reasoning, dict) else None
    if not isinstance(postures, dict) or not isinstance(postures.get(posture), dict):
        raise LaunchError("canonical model policy defaultPosture is not declared")
    allowed_roles = postures[posture].get("allowedRoles")
    if not isinstance(allowed_roles, list):
        raise LaunchError("canonical model policy defaultPosture has malformed allowedRoles")
    bindings, bindings_raw = _object(account_bindings_path, "Humanize account bindings")
    if bindings.get("schema") != "humanize-research-account-bindings/v1":
        raise LaunchError("unsupported Humanize research account binding schema")
    flow_bindings = bindings.get("flows")
    if not isinstance(flow_bindings, dict) or not isinstance(flow_bindings.get(flow_name), dict):
        raise LaunchError(f"account bindings do not define {flow_name}")
    declared = flow_bindings[flow_name]
    expected_flow_roles = [item[0] for item in FLOW_ROLE_POLICY[flow_name]]
    if set(declared) != set(expected_flow_roles):
        raise LaunchError(f"account bindings for {flow_name} must exactly cover its flow roles")
    writer_semantic_role = next(
        semantic_role
        for flow_role, semantic_role, _permission in FLOW_ROLE_POLICY[flow_name]
        if flow_role in {"reducer", "knowledge"}
    )
    resolved: list[dict[str, Any]] = []
    for flow_role, semantic_role, permission in FLOW_ROLE_POLICY[flow_name]:
        if semantic_role not in allowed_roles:
            raise LaunchError(
                f"canonical policy defaultPosture {posture!r} does not admit "
                f"semantic role {semantic_role!r}"
            )
        review_mode = "exact-opposite" if flow_role == "reviewer" else "none"
        task_class = _required_class(flow_role)
        writer_role = writer_semantic_role if flow_role == "reviewer" else None
        try:
            launch_spec = resolve_launch_spec(
                policy_raw,
                task_class=task_class,
                reasoning_posture=posture,
                semantic_role=semantic_role,
                review_mode=review_mode,
                writer_role=writer_role,
                catalog_runner=catalog_runner,
            )
        except LaunchSpecError as error:
            raise LaunchError(str(error)) from error
        identity = _canonical_launch_identity(
            launch_spec,
            policy_raw=policy_raw,
            document=document,
            semantic_role=semantic_role,
            task_class=task_class,
            reasoning_posture=posture,
            review_mode=review_mode,
            writer_role=writer_role,
        )
        backend = identity.get("backend")
        if backend not in SUPPORTED_BACKENDS:
            raise LaunchError(
                f"semantic role {semantic_role} backend {backend!r} has no verified "
                f"Humanize runtime mapping for concrete model {identity['catalogModelId']!r}; "
                "Cursor must not be relabelled as Claude"
            )
        routed = _account_binding(humanize_home, identity, declared[flow_role])
        routed["flowRole"] = flow_role
        routed["permission"] = permission
        routed["canonicalModel"] = str(identity["model"])
        routed["catalogModelId"] = str(identity["catalogModelId"])
        routed["model"] = str(identity["catalogModelId"])
        routed["launchReceipt"] = launch_spec
        resolved.append(routed)
    reviewer = next(item for item in resolved if item["flowRole"] == "reviewer")
    if any(
        item["upstreamProviderFamily"] == reviewer["upstreamProviderFamily"]
        for item in resolved
        if item["flowRole"] != "reviewer"
    ):
        raise LaunchError(
            "every writing role and terminal reviewer must use different upstream providers"
        )
    return {
        "schema": "humanize-research-resolved-identity/v1",
        "projectRoot": str(root.resolve()),
        "flow": flow_name,
        "rolePolicySha256": _sha256(policy_raw),
        "accountBindingsSha256": _sha256(bindings_raw),
        "roles": resolved,
    }


def seal_launch_receipt(receipt: dict[str, Any]) -> tuple[int, str]:
    """Write a receipt to a sealed anonymous Linux file descriptor."""

    if not hasattr(os, "memfd_create"):
        raise LaunchError("native research launch requires Linux memfd sealing")
    flags = getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0)
    descriptor = os.memfd_create("humanize-research-launch", flags=flags)
    payload = _canonical_bytes(receipt)
    try:
        os.write(descriptor, payload)
        os.lseek(descriptor, 0, os.SEEK_SET)
        seals = (
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE
        )
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        os.set_inheritable(descriptor, True)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, _sha256(payload)


def is_sealed_launch_descriptor(descriptor: int) -> bool:
    required = (
        fcntl.F_SEAL_SEAL
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_WRITE
    )
    try:
        return fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & required == required
    except OSError:
        return False


def build_command(
    argv_prefix: list[str],
    flow_path: Path | str,
    config_path: Path | str,
    roles: list[dict[str, str]],
    task: str,
) -> list[str]:
    command = [
        *argv_prefix,
        "exec",
        "-f",
        str(flow_path),
        "-c",
        str(config_path),
    ]
    for role in roles:
        specification = (
            f"cli={role['backend']},model={role['model']},effort={role['effort']},"
            f"provider={role['providerAccount']},permission={role['permission']}"
        )
        command.extend(["-a", specification])
    command.extend(["--", task])
    return command


def bind_reference_sources(root: Path, flow_name: str) -> tuple[dict[str, Any], Path]:
    """Bind mutable project copies to launcher-owned approved reference bytes."""

    if flow_name not in FLOW_FILES:
        raise LaunchError(f"unsupported native research flow: {flow_name}")
    approved_root = (
        Path(__file__).resolve().parents[1]
        / "assets/project/.humanize/flows"
    )
    materialized_root = root / ".humanize/flows"
    bound: dict[str, str] = {}
    for name in REFERENCE_SOURCE_FILES:
        approved = _regular_bytes(
            approved_root / name, f"controller-approved research source {name}"
        )
        approved_digest = _sha256(approved)
        if approved_digest != APPROVED_REFERENCE_SHA256[name]:
            raise LaunchError(
                f"controller-approved research source {name} disagrees with its launcher-owned digest"
            )
        materialized = _regular_bytes(
            materialized_root / name, f"materialized research source {name}"
        )
        if materialized != approved:
            raise LaunchError(
                f"materialized research source {name} differs from the controller-approved bytes"
            )
        bound[name] = approved_digest
    return (
        {
            "schema": REFERENCE_BINDING_SCHEMA,
            "approval": "launcher-owned-approved-sha256",
            "selectedFlow": flow_name,
            "files": bound,
        },
        approved_root / FLOW_FILES[flow_name],
    )


def _terminal_target(root: Path, setting: dict[str, Any]) -> Path:
    relative = Path(setting["terminal_artifact"])
    parts = relative.parts
    if (
        relative.is_absolute()
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise LaunchError("terminal_artifact must name a file below the project root")
    return root.joinpath(*parts)


def _terminal_parts(setting: dict[str, Any]) -> tuple[str, ...]:
    target = _terminal_target(Path("/"), setting)
    parts = tuple(part for part in target.parts if part != target.anchor)
    if not parts:
        raise LaunchError("terminal_artifact must not name the project root")
    return parts


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise LaunchError("terminal writes require Linux no-follow file-descriptor support")
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_project_root(root: Path) -> int:
    try:
        descriptor = os.open(root, _directory_flags())
    except OSError as error:
        raise LaunchError(f"project root is not a stable real directory: {error}") from error
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise LaunchError("project root is not a real directory")
    return descriptor


def _open_terminal_parent(
    root_fd: int,
    parts: tuple[str, ...],
    *,
    create: bool,
    allow_missing: bool = False,
) -> int | None:
    current = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            try:
                next_fd = os.open(component, _directory_flags(), dir_fd=current)
            except FileNotFoundError:
                if not create:
                    if allow_missing:
                        os.close(current)
                        return None
                    raise LaunchError("terminal_artifact parent directory is missing")
                try:
                    os.mkdir(component, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
                next_fd = os.open(component, _directory_flags(), dir_fd=current)
            except OSError as error:
                raise LaunchError(
                    f"terminal_artifact parent must be a real non-symlink directory: {error}"
                ) from error
            if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                raise LaunchError(
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


def _leaf_identity(parent_fd: int, name: str) -> tuple[int, int] | None:
    try:
        status = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LaunchError(f"terminal_artifact leaf cannot be inspected safely: {error}") from error
    if stat.S_ISLNK(status.st_mode):
        raise LaunchError("terminal_artifact leaf must not be a symlink")
    if not stat.S_ISREG(status.st_mode):
        raise LaunchError("terminal_artifact leaf must be a regular file")
    return status.st_dev, status.st_ino


def _same_open_directory(left_fd: int, right_fd: int) -> bool:
    left = os.fstat(left_fd)
    right = os.fstat(right_fd)
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _validate_terminal_destination(root: Path, setting: dict[str, Any]) -> None:
    parts = _terminal_parts(setting)
    root_fd = _open_project_root(root)
    parent_fd: int | None = None
    try:
        parent_fd = _open_terminal_parent(
            root_fd, parts, create=False, allow_missing=True
        )
        if parent_fd is not None:
            _leaf_identity(parent_fd, parts[-1])
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        os.close(root_fd)


def _terminal_exists(root: Path, setting: dict[str, Any]) -> bool:
    parts = _terminal_parts(setting)
    root_fd = _open_project_root(root)
    parent_fd: int | None = None
    try:
        parent_fd = _open_terminal_parent(
            root_fd, parts, create=False, allow_missing=True
        )
        return parent_fd is not None and _leaf_identity(parent_fd, parts[-1]) is not None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        os.close(root_fd)


def _atomic_terminal(
    root: Path, setting: dict[str, Any], payload: dict[str, Any]
) -> None:
    parts = _terminal_parts(setting)
    rendered = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    root_fd = _open_project_root(root)
    parent_fd: int | None = None
    temporary_fd: int | None = None
    temporary_name = f".{parts[-1]}.{uuid.uuid4().hex}.tmp"
    try:
        parent_fd = _open_terminal_parent(root_fd, parts, create=True)
        assert parent_fd is not None
        initial_leaf = _leaf_identity(parent_fd, parts[-1])
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
                raise LaunchError("terminal_artifact write made no progress")
            view = view[written:]
        os.fsync(temporary_fd)
        temporary_identity = (
            os.fstat(temporary_fd).st_dev,
            os.fstat(temporary_fd).st_ino,
        )
        os.close(temporary_fd)
        temporary_fd = None

        current_parent = _open_terminal_parent(root_fd, parts, create=False)
        assert current_parent is not None
        try:
            if not _same_open_directory(parent_fd, current_parent):
                raise LaunchError("terminal_artifact parent changed during atomic write")
        finally:
            os.close(current_parent)
        if _leaf_identity(parent_fd, parts[-1]) != initial_leaf:
            raise LaunchError("terminal_artifact leaf changed during atomic write")

        os.replace(
            temporary_name,
            parts[-1],
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
        if _leaf_identity(parent_fd, parts[-1]) != temporary_identity:
            raise LaunchError("terminal_artifact atomic replacement was not retained")
        current_parent = _open_terminal_parent(root_fd, parts, create=False)
        assert current_parent is not None
        try:
            if not _same_open_directory(parent_fd, current_parent) or (
                _leaf_identity(current_parent, parts[-1]) != temporary_identity
            ):
                raise LaunchError("terminal_artifact path changed after atomic replacement")
        finally:
            os.close(current_parent)
    except LaunchError:
        raise
    except OSError as error:
        raise LaunchError(f"terminal_artifact could not be written safely: {error}") from error
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
        os.close(root_fd)


def _failure_terminal(
    root: Path,
    flow_name: str,
    setting: dict[str, Any],
    category: str,
    reason: str,
    run_id: str,
    receipt_sha256: str | None,
) -> None:
    _atomic_terminal(
        root,
        setting,
        {
            "schema": "research-terminal/v1",
            "flow": flow_name,
            "terminal": "UNRESOLVED_INFRASTRUCTURE",
            "domain_terminal": "UNRESOLVED_INFRASTRUCTURE",
            "failure_category": category,
            "reason": reason,
            "human_dependency": None,
            "model_calls_used": None,
            "terminal_review": None,
            "launch": {
                "runId": run_id,
                "receiptSha256": receipt_sha256,
            },
        },
    )


def _validate_current_terminal(
    root: Path,
    setting: dict[str, Any],
    flow_name: str,
    run_id: str,
    receipt_sha256: str,
) -> dict[str, Any]:
    parts = _terminal_parts(setting)
    root_fd = _open_project_root(root)
    parent_fd: int | None = None
    leaf_fd: int | None = None
    try:
        parent_fd = _open_terminal_parent(root_fd, parts, create=False)
        assert parent_fd is not None
        leaf_fd = os.open(
            parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        status = os.fstat(leaf_fd)
        if not stat.S_ISREG(status.st_mode):
            raise LaunchError("native research terminal artifact is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(leaf_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        current_parent = _open_terminal_parent(root_fd, parts, create=False)
        assert current_parent is not None
        try:
            if not _same_open_directory(parent_fd, current_parent) or (
                _leaf_identity(current_parent, parts[-1])
                != (status.st_dev, status.st_ino)
            ):
                raise LaunchError("native research terminal artifact changed while read")
        finally:
            os.close(current_parent)
        try:
            artifact = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LaunchError(
                f"native research terminal artifact is not valid JSON: {error}"
            ) from error
        if not isinstance(artifact, dict):
            raise LaunchError("native research terminal artifact root must be an object")
    except LaunchError:
        raise
    except OSError as error:
        raise LaunchError(
            f"native research terminal artifact cannot be read safely: {error}"
        ) from error
    finally:
        if leaf_fd is not None:
            os.close(leaf_fd)
        if parent_fd is not None:
            os.close(parent_fd)
        os.close(root_fd)
    if artifact.get("schema") != "research-terminal/v1":
        raise LaunchError("native research terminal artifact has the wrong schema")
    if artifact.get("flow") != flow_name:
        raise LaunchError("native research terminal artifact names the wrong flow")
    if not _text(artifact.get("terminal")) or not _text(
        artifact.get("domain_terminal")
    ):
        raise LaunchError("native research terminal artifact has no explicit terminal")
    launch = artifact.get("launch")
    if not isinstance(launch, dict) or (
        launch.get("runId") != run_id
        or launch.get("receiptSha256") != receipt_sha256
    ):
        raise LaunchError("native research terminal artifact is stale or unbound")
    terminal_review = artifact.get("terminal_review")
    if terminal_review is None:
        if artifact["terminal"] not in {
            "UNRESOLVED_INFRASTRUCTURE",
            "UNSUPPORTED_HUMANIZE_CAPABILITY",
        }:
            raise LaunchError("native research success has no terminal review")
    elif not isinstance(terminal_review, dict):
        raise LaunchError("native research terminal review is malformed")
    else:
        _validate_iteration_terminal(artifact, setting)
    return artifact


def _validate_iteration_terminal(
    artifact: dict[str, Any], setting: dict[str, Any]
) -> None:
    policy = artifact.get("iteration_policy")
    expected_policy_keys = {
        "selected_mode",
        "effective_mode",
        "human_checkin_lease_minutes",
        "human_checkin_lease_expires_at",
        "human_unavailable_fallback",
        "lease_status",
        "fallback_applied",
    }
    if not isinstance(policy, dict) or set(policy) != expected_policy_keys:
        raise LaunchError("native research terminal has no strict iteration policy")
    for terminal_key, config_key in (
        ("selected_mode", "iteration_mode"),
        ("human_checkin_lease_minutes", "human_checkin_lease_minutes"),
        ("human_checkin_lease_expires_at", "human_checkin_lease_expires_at"),
        ("human_unavailable_fallback", "human_unavailable_fallback"),
    ):
        if policy[terminal_key] != setting[config_key]:
            raise LaunchError("native research terminal iteration policy drifted from config")
    triggers = artifact.get("iteration_risk_triggers")
    canonical_triggers = {
        "lease_expired",
        "human_unavailable",
        "contradiction",
        "ambiguity",
        "plateau_or_repeated_failure",
        "irreversible_or_high_cost",
        "safety_sensitive",
        "weak_proxy",
    }
    if (
        not isinstance(triggers, list)
        or len(triggers) != len(set(triggers))
        or any(trigger not in canonical_triggers for trigger in triggers)
    ):
        raise LaunchError("native research terminal has invalid iteration risk triggers")
    disposition = artifact.get("idea_review_disposition")
    if disposition not in {
        "performed",
        "skipped_human_guided",
        "triggered_by_risk",
    }:
        raise LaunchError("native research terminal has invalid idea review disposition")
    if not _text(artifact.get("research_outcome")):
        raise LaunchError("native research terminal omits the reduced research outcome")
    if artifact.get("consumed_human_feedback") != setting["human_feedback"]:
        raise LaunchError("native research terminal lost the bound human feedback")
    if (
        artifact.get("consumed_human_feedback_checkpoint_artifact")
        != setting["human_feedback_checkpoint_artifact"]
        or artifact.get("consumed_human_feedback_checkpoint_sha256")
        != setting["human_feedback_checkpoint_sha256"]
    ):
        raise LaunchError("native research terminal lost the bound feedback checkpoint")
    checkpoint = artifact.get("human_observation_checkpoint")
    effective = policy.get("effective_mode")
    if effective == "autonomous":
        if checkpoint is not None or artifact.get("next_planning_allowed") is not True:
            raise LaunchError("autonomous terminal has a contradictory human checkpoint")
        if disposition != "performed":
            raise LaunchError("autonomous terminal must record performed idea review")
        if policy["selected_mode"] == "autonomous":
            if (
                policy["lease_status"] != "not_applicable"
                or policy["fallback_applied"] is not None
                or artifact["research_outcome"] != artifact["domain_terminal"]
            ):
                raise LaunchError("autonomous terminal has contradictory mode evidence")
        elif (
            policy["selected_mode"] != "human_guided"
            or policy["lease_status"] != "expired"
            or policy["fallback_applied"] != "autonomous_review"
            or "lease_expired" not in triggers
        ):
            raise LaunchError("expired human-guided terminal lacks fallback evidence")
        return
    if effective != "human_guided" or policy.get("lease_status") != "active":
        raise LaunchError("native research terminal has an invalid effective iteration mode")
    required_checkpoint = {
        "checkpoint_id",
        "hypothesis",
        "actual_operation",
        "raw_observation",
        "agent_interpretation",
        "anomaly_or_alternate_interpretation",
        "candidate_next_tasks",
        "human_feedback",
        "questions_for_human",
        "next_planning_allowed",
    }
    if not isinstance(checkpoint, dict) or set(checkpoint) != required_checkpoint:
        raise LaunchError("human-guided terminal has no complete observation checkpoint")
    if (
        artifact.get("domain_terminal") != "HUMAN_OBSERVATION_REQUIRED"
        or artifact.get("next_planning_allowed") is not False
        or checkpoint.get("next_planning_allowed") is not False
        or checkpoint.get("human_feedback") is not None
    ):
        raise LaunchError("human-guided terminal permits replanning before bound feedback")
    checkpoint_body = {
        key: checkpoint[key]
        for key in (
            "hypothesis",
            "actual_operation",
            "raw_observation",
            "agent_interpretation",
            "anomaly_or_alternate_interpretation",
            "candidate_next_tasks",
            "human_feedback",
        )
    }
    if checkpoint["checkpoint_id"] != canonical_sha256(checkpoint_body):
        raise LaunchError("human-guided checkpoint digest is invalid")
    if not all(
        _text(checkpoint[key])
        for key in ("hypothesis", "actual_operation", "agent_interpretation")
    ):
        raise LaunchError("human-guided checkpoint has empty required text")
    if not (
        isinstance(checkpoint["candidate_next_tasks"], list)
        and checkpoint["candidate_next_tasks"]
        and all(_text(item) for item in checkpoint["candidate_next_tasks"])
        and isinstance(checkpoint["questions_for_human"], list)
        and checkpoint["questions_for_human"]
        and all(_text(item) for item in checkpoint["questions_for_human"])
    ):
        raise LaunchError("human-guided checkpoint has invalid task or question lists")
    expected_disposition = "triggered_by_risk" if triggers else "skipped_human_guided"
    if disposition != expected_disposition:
        raise LaunchError("human-guided terminal disposition disagrees with risk evidence")


def _relative(root: Path, value: Path) -> Path:
    candidate = value if value.is_absolute() else root / value
    resolved = candidate.expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise LaunchError("configured path escapes the project root")
    return resolved


def _failure_root(value: Path) -> Path | None:
    """Return only an existing directory safe enough for a launch-failure artifact."""

    try:
        candidate = value.expanduser().resolve(strict=True)
    except OSError:
        return None
    return candidate if candidate.is_dir() else None


def _revalidate_launch_identity_inputs(
    root: Path, accounts_path: Path, identity: dict[str, Any]
) -> None:
    """Require the policy and named-account bytes to remain receipt-bound."""

    expected_policy = identity.get("rolePolicySha256")
    expected_accounts = identity.get("accountBindingsSha256")
    if not isinstance(expected_policy, str) or not isinstance(expected_accounts, str):
        raise LaunchError("resolved launch identity has incomplete policy bindings")
    policy = _regular_bytes(
        root / "config/workflows/models.json", "canonical model role policy"
    )
    accounts = _regular_bytes(accounts_path, "canonical Humanize account bindings")
    if _sha256(policy) != expected_policy:
        raise LaunchError("canonical model role policy changed during Humanize dispatch")
    if _sha256(accounts) != expected_accounts:
        raise LaunchError("canonical Humanize account bindings changed during Humanize dispatch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--hmz", required=True, type=Path)
    parser.add_argument("--flow", choices=sorted(FLOW_FILES), required=True)
    parser.add_argument(
        "--accounts",
        type=Path,
        default=Path("config/workflows/humanize-research-accounts.json"),
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--pm-issue", type=Path)
    parser.add_argument("task")
    args = parser.parse_args(argv)
    flow_name = str(args.flow)
    setting = normalize_config(flow_name, {})
    run_id = uuid.uuid4().hex
    receipt_sha256: str | None = None
    reference_sources_admitted = False
    try:
        root = args.root.expanduser().resolve(strict=True)
        accounts_path = _relative(root, args.accounts)
        config_raw: dict[str, Any] = {}
        if args.config is not None:
            config_path = _relative(root, args.config)
            config_raw, _ = _object(config_path, "native research config")
        setting, iteration_context = resolve_iteration_context(
            root,
            flow_name,
            config_raw,
            pm_issue=args.pm_issue,
        )
        # Source admission is deliberately first: an altered materialized
        # reference must not trigger catalog access, Humanize, or even a
        # failure-artifact write.
        reference_sources, approved_flow_path = bind_reference_sources(root, flow_name)
        reference_sources_admitted = True
        task_sha256 = _sha256(args.task.encode("utf-8"))
        human_feedback_receipt = _validate_human_feedback_checkpoint(
            root, setting, iteration_context, task_sha256
        )
        _revalidate_iteration_context(root, iteration_context)
        _revalidate_human_feedback_checkpoint(
            root,
            setting,
            iteration_context,
            task_sha256,
            human_feedback_receipt,
        )
        _validate_terminal_destination(root, setting)
        identity = resolve_launch_identity(
            root,
            flow_name,
            accounts_path,
            Path(current_account().humanize_home),
        )
        _revalidate_launch_identity_inputs(root, accounts_path, identity)

        # Import the pinned validator only after source admission and role
        # resolution. It owns executable provenance; this launcher owns the
        # multi-role policy and source-admission ordering.
        scripts = Path(__file__).resolve().parents[2] / "humanize-workflow" / "scripts"
        sys.path.insert(0, str(scripts))
        try:
            from safe_mapping import MappingError, validate_hmz_binary
        finally:
            sys.path.remove(str(scripts))
        try:
            pinned = validate_hmz_binary(args.hmz)
        except (OSError, MappingError) as error:
            raise LaunchError(str(error)) from error

        receipt = {
            "schema": "humanize-research-launch/v1",
            "runId": run_id,
            "flow": flow_name,
            "projectRoot": str(root),
            "taskSha256": task_sha256,
            "config": setting,
            "iterationContext": iteration_context,
            "humanFeedbackReceipt": human_feedback_receipt,
            "rolePolicySha256": identity["rolePolicySha256"],
            "accountBindingsSha256": identity["accountBindingsSha256"],
            "referenceSources": reference_sources,
            "roles": identity["roles"],
        }
        receipt_payload = _canonical_bytes(receipt)
        receipt_sha256 = _sha256(receipt_payload)
        provider_bindings = [
            {
                "backend": route["backend"],
                "providerAccount": route["providerAccount"],
                "providerRecordSha256": route["providerRecordSha256"],
                "catalogModelId": route["catalogModelId"],
                "effort": route["effort"],
            }
            for route in identity["roles"]
        ]
        with build_execution_context(
            pinned=pinned,
            routes=identity["roles"],
            sources={
                "research-flow": _regular_bytes(approved_flow_path, "approved research flow"),
                "research-helper": _regular_bytes(
                    approved_flow_path.parent / "_research_reference.py",
                    "approved research helper",
                ),
                "research-config": _canonical_bytes(setting),
                "research-launch-receipt": receipt_payload,
            },
            account_mode="named",
            provider_bindings=provider_bindings,
            launcher_environment={RECEIPT_SHA_ENV: receipt_sha256},
            source_environment={
                RECEIPT_FD_ENV: "research-launch-receipt",
                "HUMANIZE_RESEARCH_HELPER_FD": "research-helper",
            },
        ) as context:
            command = build_command(
                context.argv_prefix(helper_label="research-helper"),
                context.source_path("research-flow"),
                context.source_path("research-config"),
                identity["roles"],
                args.task,
            )
            _revalidate_launch_identity_inputs(root, accounts_path, identity)
            _revalidate_iteration_context(root, iteration_context)
            _revalidate_human_feedback_checkpoint(
                root,
                setting,
                iteration_context,
                task_sha256,
                human_feedback_receipt,
            )
            context.revalidate()
            try:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=context.environment,
                    pass_fds=context.pass_fds,
                    check=False,
                )
            finally:
                context.revalidate()
                _revalidate_launch_identity_inputs(root, accounts_path, identity)
                _revalidate_iteration_context(root, iteration_context)
                _revalidate_human_feedback_checkpoint(
                    root,
                    setting,
                    iteration_context,
                    task_sha256,
                    human_feedback_receipt,
                )
            if completed.returncode != 0:
                _failure_terminal(
                    root,
                    flow_name,
                    setting,
                    "humanize-process-failure",
                    f"pinned hmz exited with status {completed.returncode}",
                    run_id,
                    receipt_sha256,
                )
                return completed.returncode or 2
            if not _terminal_exists(root, setting):
                _failure_terminal(
                    root,
                    flow_name,
                    setting,
                    "missing-terminal-artifact",
                    "pinned hmz returned success without the required terminal artifact",
                    run_id,
                    receipt_sha256,
                )
                return 2
            try:
                artifact = _validate_current_terminal(
                    root, setting, flow_name, run_id, receipt_sha256
                )
            except LaunchError as error:
                _failure_terminal(
                    root,
                    flow_name,
                    setting,
                    "invalid-terminal-artifact",
                    str(error),
                    run_id,
                    receipt_sha256,
                )
                return 2
            if artifact["terminal"] == "UNRESOLVED_INFRASTRUCTURE":
                return 2
            return 0
    except Exception as error:
        category = (
            "launch-validation-failure"
            if isinstance(error, (OSError, LaunchError, ExecutionContextError, subprocess.SubprocessError))
            else "launcher-infrastructure-failure"
        )
        try:
            failure_root = _failure_root(args.root)
            if reference_sources_admitted and failure_root is not None:
                _failure_terminal(
                    failure_root,
                    flow_name,
                    setting,
                    category,
                    str(error),
                    run_id,
                    receipt_sha256,
                )
        except (OSError, LaunchError):
            pass
        print(f"humanize-research: {error}", file=sys.stderr)
        return 2
if __name__ == "__main__":
    raise SystemExit(main())
