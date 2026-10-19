#!/usr/bin/env python3
"""Fail-closed explicit launcher for the project default humanize flow."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

HUMANIZE_WORKFLOW_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "humanize-workflow" / "scripts"
)
if str(HUMANIZE_WORKFLOW_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(HUMANIZE_WORKFLOW_SCRIPTS))

from safe_mapping import MappingError, validate_hmz_binary
from execution_context import ExecutionContextError, build_execution_context

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
    canonical_bytes,
    canonical_sha256,
    digest_bytes,
    resolve_launch_spec,
)
from live_catalog import CatalogError, normalize_cursor_catalog_id
from validate_model_roles import ValidationError as ModelRoleValidationError
from validate_model_roles import validate as validate_model_roles

from validate_project_flows import (
    FlowValidationError,
    install_approved_snapshot,
    read_versioned_slot,
    regular_bytes,
    validate_default_bytes,
    validate_slot_bytes,
)

SUPPORTED_BACKENDS = frozenset({"claude", "codex"})
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
CANONICAL_RECEIPT_SCHEMA = "workflow-native-launch-receipt/v1"
ACTIVATION_FIELDS = frozenset(
    {
        "activationId",
        "permission",
        "taskClass",
        "reasoningPosture",
        "semanticRole",
        "reviewMode",
        "writerRole",
        "receipt",
    }
)


def _document(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FlowValidationError(f"model role policy is not valid UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        raise FlowValidationError("model role policy root must be an object")
    return value


def _task_class(document: dict[str, Any], role_name: str) -> str:
    capability = document.get("capabilityPolicy")
    classes = capability.get("taskClassRoles") if isinstance(capability, dict) else None
    if not isinstance(classes, dict):
        raise FlowValidationError("canonical model policy has no taskClassRoles contract")
    matches = sorted(
        name
        for name, role_names in classes.items()
        if isinstance(role_names, list) and role_name in role_names
    )
    if len(matches) != 1:
        raise FlowValidationError(
            f"semantic role {role_name!r} must belong to exactly one explicit task class"
        )
    return matches[0]


def _canonical_default_posture(document: dict[str, Any]) -> tuple[str, set[str]]:
    """Read the only posture this launcher may use from canonical policy bytes."""
    reasoning = document.get("reasoningPolicy")
    posture = reasoning.get("defaultPosture") if isinstance(reasoning, dict) else None
    postures = reasoning.get("postures") if isinstance(reasoning, dict) else None
    if not isinstance(posture, str) or not posture:
        raise FlowValidationError("canonical model policy has no explicit defaultPosture")
    if not isinstance(postures, dict) or not isinstance(postures.get(posture), dict):
        raise FlowValidationError(
            f"canonical model policy defaultPosture {posture!r} is not declared"
        )
    allowed = postures[posture].get("allowedRoles")
    if not isinstance(allowed, list) or not all(
        isinstance(role, str) and role for role in allowed
    ):
        raise FlowValidationError(
            f"canonical policy defaultPosture {posture!r} has malformed allowedRoles"
        )
    return posture, set(allowed)


def _activation_plans(
    document: dict[str, Any],
    executor_name: str,
    reviewer_name: str,
    posture: str,
) -> list[dict[str, Any]]:
    """Construct the fixed two-activation contract from the canonical default."""
    canonical_posture, allowed = _canonical_default_posture(document)
    if posture != canonical_posture:
        raise FlowValidationError(
            "activation reasoning posture differs from canonical policy defaultPosture"
        )
    if executor_name not in allowed or reviewer_name not in allowed:
        denied = [
            role
            for role in (executor_name, reviewer_name)
            if role not in allowed
        ]
        raise FlowValidationError(
            f"canonical policy defaultPosture {posture!r} does not admit roles {denied!r}"
        )
    reviewer_task_class = _task_class(document, reviewer_name)
    if reviewer_task_class != "verification":
        raise FlowValidationError(
            f"default reviewer role {reviewer_name!r} must have verification task class"
        )
    return [
        {
            "activationId": "executor",
            "permission": "workspace-write",
            "taskClass": _task_class(document, executor_name),
            "reasoningPosture": posture,
            "semanticRole": executor_name,
            "reviewMode": "none",
            "writerRole": None,
        },
        {
            "activationId": "reviewer",
            "permission": "read-only",
            "taskClass": "verification",
            "reasoningPosture": posture,
            "semanticRole": reviewer_name,
            "reviewMode": "exact-opposite",
            "writerRole": executor_name,
        },
    ]


def _require_fresh_catalog_coverage(
    identity: object, policy: object
) -> dict[str, Any]:
    """Require the receipt's concrete catalog ID to match its fresh evidence."""
    if not isinstance(identity, dict) or not isinstance(policy, dict):
        raise FlowValidationError("canonical launch receipt has malformed identity or policy")
    backend = identity.get("backend")
    model = identity.get("model")
    effort = identity.get("effort")
    concrete = identity.get("catalogModelId")
    evidence_by_backend = policy.get("freshCatalogEvidence")
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
        raise FlowValidationError(
            "canonical launch receipt lacks a fresh concrete catalog model binding"
        )
    evidence = evidence_by_backend.get(backend)
    if not isinstance(evidence, dict) or evidence.get("backend") != backend:
        raise FlowValidationError(
            "canonical launch receipt fresh catalog evidence does not bind its backend"
        )
    coverage = evidence.get("coverage")
    if not isinstance(coverage, dict):
        raise FlowValidationError(
            "canonical launch receipt fresh catalog evidence has no coverage"
        )
    if backend in {"claude", "codex"}:
        efforts = coverage.get(model)
        if (
            concrete != model
            or not isinstance(efforts, list)
            or effort not in efforts
        ):
            raise FlowValidationError(
                "canonical launch receipt concrete catalog model is not covered by fresh evidence"
            )
    elif backend == "cursor":
        ids = coverage.get("__cursor_ids__")
        try:
            expected_concrete = normalize_cursor_catalog_id(model, effort)
        except CatalogError as error:
            raise FlowValidationError(
                "canonical launch receipt has no audited Cursor concrete model mapping"
            ) from error
        if (
            concrete != expected_concrete
            or not isinstance(ids, list)
            or concrete not in ids
        ):
            raise FlowValidationError(
                "canonical launch receipt concrete catalog model is not covered by fresh evidence"
            )
    else:
        raise FlowValidationError(
            f"canonical launch receipt backend {backend!r} has no audited catalog verifier"
        )
    return identity


def _canonical_receipt_identity(
    receipt: object,
    *,
    raw_document: bytes,
    document: dict[str, Any],
    activation: dict[str, Any],
) -> dict[str, Any]:
    """Reject a resolver result that is not bound to this exact activation."""
    if not isinstance(receipt, dict) or receipt.get("schema") != CANONICAL_RECEIPT_SCHEMA:
        raise FlowValidationError(
            "canonical resolver did not return a workflow-native launch receipt"
        )
    unsigned = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    if receipt.get("receiptSha256") != canonical_sha256(unsigned):
        raise FlowValidationError("canonical launch receipt self-digest is invalid")

    expected_request = {
        "taskClass": activation["taskClass"],
        "reasoningPosture": activation["reasoningPosture"],
        "semanticRole": activation["semanticRole"],
        "writerRole": activation["writerRole"],
        "reviewMode": activation["reviewMode"],
    }
    request = receipt.get("request")
    if not isinstance(request, dict) or request != expected_request:
        raise FlowValidationError(
            "canonical launch receipt request differs from fixed activation"
        )

    resolution = document.get("catalogResolution")
    capability = document.get("capabilityPolicy")
    reasoning = document.get("reasoningPolicy")
    if (
        not isinstance(resolution, dict)
        or not isinstance(capability, dict)
        or not isinstance(reasoning, dict)
    ):
        raise FlowValidationError("canonical model policy has malformed receipt inputs")
    expected_policy = {
        "modelsSchema": document.get("schema"),
        "modelsDocumentSha256": digest_bytes(raw_document),
        "catalogResolutionSha256": canonical_sha256(resolution),
        "capabilityPolicySha256": canonical_sha256(capability),
        "reasoningPolicySha256": canonical_sha256(reasoning),
        "catalogStatus": resolution.get("status"),
        "catalogSource": "authenticated-native-cli",
        "catalogEvidenceReceipt": resolution.get("evidenceReceipt"),
        "liveCatalogAuthenticatedByResolver": True,
    }
    policy = receipt.get("policy")
    if (
        not isinstance(policy, dict)
        or set(policy) != set(expected_policy) | {"freshCatalogEvidence"}
        or any(policy.get(key) != value for key, value in expected_policy.items())
        or not isinstance(policy.get("freshCatalogEvidence"), dict)
    ):
        raise FlowValidationError(
            "canonical launch receipt does not bind current policy bytes and fresh catalog evidence"
        )

    roles = document.get("roles")
    configured = (
        roles.get(activation["semanticRole"]) if isinstance(roles, dict) else None
    )
    identity = receipt.get("resolved")
    if not isinstance(identity, dict) or not isinstance(configured, dict):
        raise FlowValidationError("canonical launch receipt has no resolved identity")
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
        expected = (
            activation["semanticRole"]
            if field == "semanticRole"
            else configured.get(field)
        )
        if identity.get(field) != expected:
            raise FlowValidationError(
                f"canonical launch receipt identity differs from current "
                f"{activation['semanticRole']}.{field}"
            )
    _require_fresh_catalog_coverage(identity, policy)

    writer_role = activation["writerRole"]
    writer = receipt.get("writer")
    if writer_role is None:
        if writer is not None:
            raise FlowValidationError(
                "canonical launch receipt unexpectedly binds a writer identity"
            )
    else:
        configured_writer = roles.get(writer_role) if isinstance(roles, dict) else None
        if not isinstance(writer, dict) or not isinstance(configured_writer, dict):
            raise FlowValidationError("canonical launch receipt has no writer identity")
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
            expected = writer_role if field == "semanticRole" else configured_writer.get(field)
            if writer.get(field) != expected:
                raise FlowValidationError(
                    f"canonical launch receipt writer differs from current {writer_role}.{field}"
                )
        _require_fresh_catalog_coverage(writer, policy)
    return identity


def _validate_activations(
    raw_document: bytes, activations: list[dict[str, Any]]
) -> tuple[dict[str, Any], str]:
    """Validate every fixed activation against the exact policy and receipt."""
    document = _document(raw_document)
    try:
        validate_model_roles(document)
    except ModelRoleValidationError as error:
        raise FlowValidationError(str(error)) from error
    posture, _ = _canonical_default_posture(document)
    if not isinstance(activations, list) or len(activations) != 2:
        raise FlowValidationError("default launch must contain exactly two activations")
    by_id: dict[str, dict[str, Any]] = {}
    for activation in activations:
        if not isinstance(activation, dict):
            raise FlowValidationError("default launch activation is malformed")
        identifier = activation.get("activationId")
        if not isinstance(identifier, str) or identifier in by_id:
            raise FlowValidationError("default launch activation IDs are malformed")
        by_id[identifier] = activation
    if set(by_id) != {"executor", "reviewer"}:
        raise FlowValidationError("default launch activation IDs must be executor and reviewer")
    executor_name = by_id["executor"].get("semanticRole")
    reviewer_name = by_id["reviewer"].get("semanticRole")
    if not isinstance(executor_name, str) or not isinstance(reviewer_name, str):
        raise FlowValidationError("default launch activation has no semantic role")
    expected_plans = _activation_plans(
        document, executor_name, reviewer_name, posture
    )
    for expected in expected_plans:
        activation = by_id[expected["activationId"]]
        if set(activation) != ACTIVATION_FIELDS:
            raise FlowValidationError("default launch activation fields are invalid")
        if any(
            activation.get(field) != expected.get(field)
            for field in ACTIVATION_FIELDS - {"receipt"}
        ):
            raise FlowValidationError(
                "default launch activation differs from the canonical role contract"
            )
        _canonical_receipt_identity(
            activation.get("receipt"),
            raw_document=raw_document,
            document=document,
            activation=activation,
        )
    return document, posture


def resolve_activations(
    raw_document: bytes, executor_name: str, reviewer_name: str
) -> list[dict[str, Any]]:
    """Resolve the default contract through the canonical validator/receipt generator."""
    document = _document(raw_document)
    try:
        validate_model_roles(document)
    except ModelRoleValidationError as error:
        raise FlowValidationError(str(error)) from error
    posture, _ = _canonical_default_posture(document)
    activations = _activation_plans(
        document, executor_name, reviewer_name, posture
    )
    try:
        for activation in activations:
            activation["receipt"] = resolve_launch_spec(
                raw_document,
                task_class=activation["taskClass"],
                reasoning_posture=activation["reasoningPosture"],
                semantic_role=activation["semanticRole"],
                review_mode=activation["reviewMode"],
                writer_role=activation["writerRole"],
            )
    except LaunchSpecError as error:
        raise FlowValidationError(str(error)) from error
    _validate_activations(raw_document, activations)
    _require_supported_backends(activations)
    return activations


def _require_supported_backends(activations: list[dict[str, Any]]) -> None:
    """Reject an unsupported route even when a caller supplied an activation list."""
    for activation in activations:
        receipt = activation.get("receipt")
        identity = receipt.get("resolved") if isinstance(receipt, dict) else None
        if not isinstance(identity, dict):
            raise FlowValidationError("default launch activation has no resolved identity")
        backend = identity.get("backend")
        if backend not in SUPPORTED_BACKENDS:
            role = activation.get("semanticRole")
            concrete = identity.get("catalogModelId")
            if backend == "cursor":
                raise FlowValidationError(
                    f"canonical role {role!r} resolves to backend {backend!r} model "
                    f"{concrete!r}, but the audited default launcher has no Cursor execution "
                    "route; execution is refused before hmz and Cursor must not be relabelled "
                    "as Claude or substituted with another backend"
                )
            raise FlowValidationError(
                f"canonical role {role!r} backend {backend!r} has no pinned Humanize2 "
                "runtime mapping; execution is refused before hmz"
            )


def _execution_context_routes(
    activations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Adapt validated receipt identities to the execution-context route contract."""
    _require_supported_backends(activations)
    routes: list[dict[str, Any]] = []
    for activation in activations:
        receipt = activation.get("receipt")
        identity = receipt.get("resolved") if isinstance(receipt, dict) else None
        backend = identity.get("backend") if isinstance(identity, dict) else None
        if backend not in SUPPORTED_BACKENDS:
            raise FlowValidationError(
                "validated default activation has no supported native receipt backend"
            )
        routes.append(
            {
                **activation,
                "backend": backend,
                "launchReceipt": receipt,
            }
        )
    return routes


def _route(activation: dict[str, Any]) -> dict[str, str]:
    identity = activation["receipt"]["resolved"]
    return {
        "name": str(identity["semanticRole"]),
        "backend": str(identity["backend"]),
        "model": str(identity["catalogModelId"]),
        "canonicalModel": str(identity["model"]),
        "effort": str(identity["effort"]),
        "upstreamProviderFamily": str(identity["upstreamProviderFamily"]),
        "modelFamily": str(identity["modelFamily"]),
    }


def resolve_pair(
    document: dict[str, Any], executor_name: str, reviewer_name: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Compatibility surface for callers that already hold a decoded policy."""
    activations = resolve_activations(
        canonical_bytes(document), executor_name, reviewer_name
    )
    return _route(activations[0]), _route(activations[1])


def _open_child_directory(
    parent_fd: int, name: str, *, create: bool, exclusive: bool = False
) -> int:
    if not SAFE_COMPONENT.fullmatch(name):
        raise FlowValidationError(f"unsafe launch evidence namespace component: {name!r}")
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            if exclusive:
                raise FlowValidationError(
                    f"launch evidence namespace already exists; overwrite is forbidden: {name}"
                ) from None
    descriptor = os.open(
        name,
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent_fd,
    )
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise FlowValidationError("launch evidence namespace component is not a directory")
    return descriptor


def _write_exclusive(directory_fd: int, name: str, payload: bytes) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FlowValidationError("launch receipt write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise FlowValidationError("launch receipt destination is not a regular file")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def create_launch_evidence(
    root: Path,
    adapter_id: str,
    run_id: str,
    activations: list[dict[str, Any]],
    *,
    raw_document: bytes,
) -> dict[str, Any]:
    """Validate then exclusively seal canonical receipts before any dispatch."""
    _validate_activations(raw_document, activations)
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise FlowValidationError("launch evidence requires Linux no-follow descriptor support")
    descriptors: list[int] = []
    try:
        root_fd = os.open(
            str(root),
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
        )
        descriptors.append(root_fd)
        current = root_fd
        for component in ("workflow-output", "humanize", adapter_id):
            current = _open_child_directory(current, component, create=True)
            descriptors.append(current)
        current = _open_child_directory(
            current, run_id, create=True, exclusive=True
        )
        descriptors.append(current)
        evidence_fd = _open_child_directory(current, "evidence", create=True)
        descriptors.append(evidence_fd)
        receipt_fd = _open_child_directory(
            evidence_fd, "model-launches", create=True
        )
        descriptors.append(receipt_fd)

        bindings: list[dict[str, str]] = []
        base = Path("workflow-output") / "humanize" / adapter_id / run_id / "evidence"
        for index, activation in enumerate(activations):
            receipt = activation.get("receipt")
            if not isinstance(receipt, dict):
                raise FlowValidationError("activation has no canonical launch receipt")
            rendered = canonical_bytes(receipt) + b"\n"
            filename = f"{index:03d}.json"
            _write_exclusive(receipt_fd, filename, rendered)
            bindings.append(
                {
                    "activationId": str(activation["activationId"]),
                    "semanticRole": str(receipt["request"]["semanticRole"]),
                    "receiptPath": (base / "model-launches" / filename).as_posix(),
                    "receiptSha256": str(receipt["receiptSha256"]),
                    "requestSha256": canonical_sha256(receipt["request"]),
                    "policySha256": canonical_sha256(receipt["policy"]),
                    "receiptFileSha256": digest_bytes(rendered),
                }
            )
        evidence: dict[str, Any] = {
            "schema": "humanize-native-launch-evidence/v1",
            "adapterId": adapter_id,
            "runId": run_id,
            "evidencePath": (base / "launch-evidence.json").as_posix(),
            "activations": bindings,
        }
        evidence["evidenceSha256"] = canonical_sha256(evidence)
        _write_exclusive(
            evidence_fd,
            "launch-evidence.json",
            canonical_bytes(evidence) + b"\n",
        )
        os.fsync(receipt_fd)
        os.fsync(evidence_fd)
        return evidence
    except FlowValidationError:
        raise
    except OSError as error:
        raise FlowValidationError(
            f"launch evidence namespace is not a real no-follow directory: {error}"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def verify_launch_evidence(
    root: Path,
    evidence: dict[str, Any],
    *,
    raw_document: bytes,
    expected_activations: list[dict[str, Any]],
) -> None:
    """Verify sealed receipt bytes still equal the freshly resolved activations."""
    document, _ = _validate_activations(raw_document, expected_activations)
    claimed = evidence.get("evidenceSha256")
    unsigned = {key: value for key, value in evidence.items() if key != "evidenceSha256"}
    if claimed != canonical_sha256(unsigned):
        raise FlowValidationError("launch evidence digest is invalid")
    stored = regular_bytes(root / str(evidence.get("evidencePath", "")), "launch evidence")
    if stored != canonical_bytes(evidence) + b"\n":
        raise FlowValidationError("stored launch evidence was replaced or modified")
    bindings = evidence.get("activations")
    if not isinstance(bindings, list) or not bindings:
        raise FlowValidationError("launch evidence has no activations")
    expected_by_id = {
        str(activation["activationId"]): activation
        for activation in expected_activations
    }
    if len(bindings) != len(expected_by_id):
        raise FlowValidationError(
            "launch evidence does not contain one receipt for every activation"
        )
    seen_activation_ids: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {
            "activationId",
            "semanticRole",
            "receiptPath",
            "receiptSha256",
            "requestSha256",
            "policySha256",
            "receiptFileSha256",
        }:
            raise FlowValidationError("launch evidence activation is malformed")
        activation_id = binding.get("activationId")
        if (
            not isinstance(activation_id, str)
            or activation_id not in expected_by_id
            or activation_id in seen_activation_ids
        ):
            raise FlowValidationError(
                "launch evidence must bind every activation exactly once"
            )
        seen_activation_ids.add(activation_id)
        activation = expected_by_id[activation_id]
        raw = regular_bytes(
            root / str(binding.get("receiptPath", "")), "native launch receipt"
        )
        if digest_bytes(raw) != binding.get("receiptFileSha256"):
            raise FlowValidationError("native launch receipt was replaced or modified")
        try:
            receipt = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FlowValidationError(f"native launch receipt is invalid JSON: {error}") from error
        if not isinstance(receipt, dict) or raw != canonical_bytes(receipt) + b"\n":
            raise FlowValidationError("native launch receipt bytes are not canonical")
        if (
            receipt != activation.get("receipt")
            or receipt.get("receiptSha256") != binding.get("receiptSha256")
            or not isinstance(receipt.get("request"), dict)
            or canonical_sha256(receipt["request"]) != binding.get("requestSha256")
            or not isinstance(receipt.get("policy"), dict)
            or canonical_sha256(receipt["policy"]) != binding.get("policySha256")
            or receipt["request"].get("semanticRole") != binding.get("semanticRole")
        ):
            raise FlowValidationError("native launch receipt binding is invalid")
        _canonical_receipt_identity(
            receipt,
            raw_document=raw_document,
            document=document,
            activation=activation,
        )
    if seen_activation_ids != set(expected_by_id):
        raise FlowValidationError("launch evidence must bind every activation exactly once")


def build_command(
    argv_prefix: list[str],
    default: Path | str,
    executor: dict[str, str],
    reviewer: dict[str, str],
    task: str,
) -> list[str]:
    """Build hmz argv; account-level provider routing is absent unless separately bound."""
    command = [*argv_prefix, "exec", "-f", str(default)]
    for route, permission in ((executor, "workspace-write"), (reviewer, "read-only")):
        specification = (
            f"cli={route['backend']},model={route['model']},"
            f"effort={route['effort']},permission={permission}"
        )
        command.extend(["-a", specification])
    command.extend(["--", task])
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--hmz", required=True, type=Path)
    parser.add_argument("--executor-role", default="frontier_execute")
    parser.add_argument("--reviewer-role", default="advisor")
    parser.add_argument("task")
    args = parser.parse_args(argv)
    try:
        root = args.root.expanduser().resolve(strict=True)
        default = root / ".humanize/flows/default.py"
        default_bytes = regular_bytes(default, "default flow")
        validate_default_bytes(default_bytes)
        _, slot_bytes, slot_digest = read_versioned_slot(root)
        if validate_slot_bytes(slot_bytes) != slot_digest:
            raise FlowValidationError("versioned slot failed its source validator")
        roles_path = root / "config/workflows/models.json"
        raw_roles = regular_bytes(roles_path, "canonical model role policy")
        document = _document(raw_roles)
        try:
            validate_model_roles(document)
        except ModelRoleValidationError as error:
            raise FlowValidationError(str(error)) from error
        _canonical_default_posture(document)
        pinned = validate_hmz_binary(args.hmz)
        activations = resolve_activations(
            raw_roles, args.executor_role, args.reviewer_role
        )
        _require_supported_backends(activations)
        executor, reviewer = _route(activations[0]), _route(activations[1])
        install_approved_snapshot(root, slot_bytes, slot_digest)
        run_id = uuid.uuid4().hex
        launch_evidence = create_launch_evidence(
            root, "default", run_id, activations, raw_document=raw_roles
        )
        verify_launch_evidence(
            root,
            launch_evidence,
            raw_document=raw_roles,
            expected_activations=activations,
        )
        if regular_bytes(roles_path, "canonical model role policy") != raw_roles:
            raise FlowValidationError("canonical model role policy changed during launch preparation")
        context_routes = _execution_context_routes(activations)
        with build_execution_context(
            pinned=pinned,
            routes=context_routes,
            sources={"default-flow": default_bytes, "slot-flow": slot_bytes},
            account_mode="ambient",
            launcher_environment={
                "HUMANIZE_SLOT_SHA256": slot_digest,
                "HUMANIZE_NATIVE_LAUNCH_EVIDENCE_PATH": str(launch_evidence["evidencePath"]),
                "HUMANIZE_NATIVE_LAUNCH_EVIDENCE_SHA256": str(launch_evidence["evidenceSha256"]),
            },
            source_environment={"HUMANIZE_SLOT_FD": "slot-flow"},
        ) as context:
            command = build_command(
                context.argv_prefix(),
                context.source_path("default-flow"),
                executor,
                reviewer,
                args.task,
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
            verify_launch_evidence(
                root,
                launch_evidence,
                raw_document=raw_roles,
                expected_activations=activations,
            )
            if regular_bytes(roles_path, "canonical model role policy") != raw_roles:
                raise FlowValidationError("canonical model role policy changed during hmz dispatch")
            return completed.returncode
    except (OSError, FlowValidationError, MappingError, ExecutionContextError) as error:
        print(f"humanize-default: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
