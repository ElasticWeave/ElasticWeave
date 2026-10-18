#!/usr/bin/env python3
"""Validate and execute an adapter-generated flow through the pinned hmz CLI."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

WORKFLOW_SCRIPTS = Path(__file__).resolve().parent
if str(WORKFLOW_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WORKFLOW_SCRIPTS))

from safe_mapping import (
    MappingError,
    REASONING_POSTURES,
    load_object,
    project_path,
    validate_full_mapping,
    validate_hmz_binary,
)
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

# This is the pinned Humanize2 runtime surface, not a claim about an upstream
# model family.  Cursor Agent is intentionally absent: it must not be relabelled
# as the Claude CLI merely because it serves an Anthropic model.
SUPPORTED_BACKENDS = {"codex", "claude"}
SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
CANONICAL_RECEIPT_SCHEMA = "workflow-native-launch-receipt/v1"


def canonical_roles_path(root: Path) -> Path:
    """Return the one project-owned model policy path accepted for dispatch."""
    return root / "config/workflows/models.json"


def _regular_bytes(path: Path, label: str) -> bytes:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise MappingError(f"{label} requires Linux no-follow descriptor support")
    expanded = path.expanduser()
    parts = list(expanded.parts)
    if expanded.is_absolute():
        start = expanded.anchor
        parts = parts[1:]
    else:
        start = "."
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise MappingError(f"{label} has an unsafe path")
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    leaf_flags = os.O_RDONLY | os.O_NOFOLLOW | close_on_exec
    directory_fd: int | None = None
    leaf_fd: int | None = None
    try:
        directory_fd = os.open(start, directory_flags)
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        leaf_fd = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
        if not stat.S_ISREG(os.fstat(leaf_fd).st_mode):
            raise MappingError(f"{label} must be a regular non-symlink file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(leaf_fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except MappingError:
        raise
    except OSError as error:
        raise MappingError(
            f"{label} must be a regular file with no linked path component: {error}"
        ) from error
    finally:
        if leaf_fd is not None:
            os.close(leaf_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _document(raw: bytes) -> dict[str, Any]:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MappingError(f"canonical model policy is not valid UTF-8 JSON: {error}") from error
    if not isinstance(document, dict):
        raise MappingError("canonical model policy root must be an object")
    return document


def _task_class(document: dict[str, Any], role_name: str) -> str:
    capability = document.get("capabilityPolicy")
    classes = capability.get("taskClassRoles") if isinstance(capability, dict) else None
    if not isinstance(classes, dict):
        raise MappingError("canonical model policy has no taskClassRoles contract")
    matches = sorted(
        name
        for name, role_names in classes.items()
        if isinstance(role_names, list) and role_name in role_names
    )
    if len(matches) != 1:
        raise MappingError(
            f"semantic role {role_name!r} must belong to exactly one explicit task class"
        )
    return matches[0]


def _require_fresh_catalog_coverage(
    identity: object, policy: object
) -> dict[str, Any]:
    """Require the resolved concrete ID to match its embedded fresh catalog."""
    if not isinstance(identity, dict):
        raise MappingError("canonical launch receipt has no resolved identity")
    if not isinstance(policy, dict):
        raise MappingError("canonical launch receipt has no policy binding")
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
        raise MappingError(
            "canonical launch receipt lacks a fresh concrete catalog model binding"
        )
    evidence = evidence_by_backend.get(backend)
    if not isinstance(evidence, dict) or evidence.get("backend") != backend:
        raise MappingError(
            "canonical launch receipt fresh catalog evidence does not bind its backend"
        )
    coverage = evidence.get("coverage")
    if not isinstance(coverage, dict):
        raise MappingError("canonical launch receipt fresh catalog evidence has no coverage")
    if backend in {"claude", "codex"}:
        efforts = coverage.get(model)
        if (
            concrete != model
            or not isinstance(efforts, list)
            or effort not in efforts
        ):
            raise MappingError(
                "canonical launch receipt concrete catalog model is not covered by fresh evidence"
            )
    elif backend == "cursor":
        ids = coverage.get("__cursor_ids__")
        try:
            expected_concrete = normalize_cursor_catalog_id(model, effort)
        except CatalogError as error:
            raise MappingError(
                "canonical launch receipt has no audited Cursor concrete model mapping"
            ) from error
        if (
            concrete != expected_concrete
            or not isinstance(ids, list)
            or concrete not in ids
        ):
            raise MappingError(
                "canonical launch receipt concrete catalog model is not covered by fresh evidence"
            )
    else:
        raise MappingError(
            f"canonical launch receipt backend {backend!r} has no audited catalog verifier"
        )
    return identity


def _canonical_receipt_identity(
    receipt: object,
    *,
    raw_document: bytes,
    document: dict[str, Any],
    semantic_role: str,
    task_class: str,
    reasoning_posture: str,
    review_mode: str,
    writer_role: str | None,
) -> dict[str, Any]:
    """Reject anything other than the current resolver's complete receipt."""
    if not isinstance(receipt, dict) or receipt.get("schema") != CANONICAL_RECEIPT_SCHEMA:
        raise MappingError("mapped activation has no canonical workflow-native launch receipt")
    unsigned = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    if receipt.get("receiptSha256") != canonical_sha256(unsigned):
        raise MappingError("canonical launch receipt self-digest is invalid")
    request = receipt.get("request")
    if not isinstance(request, dict) or request != {
        "taskClass": task_class,
        "reasoningPosture": reasoning_posture,
        "semanticRole": semantic_role,
        "writerRole": writer_role,
        "reviewMode": review_mode,
    }:
        raise MappingError("canonical launch receipt request differs from activation")
    policy = receipt.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("modelsDocumentSha256") != digest_bytes(raw_document)
        or policy.get("liveCatalogAuthenticatedByResolver") is not True
        or not isinstance(policy.get("freshCatalogEvidence"), dict)
    ):
        raise MappingError(
            "canonical launch receipt does not bind current policy bytes and fresh catalog evidence"
        )
    identity = receipt.get("resolved")
    configured = (
        document.get("roles", {}).get(semantic_role)
        if isinstance(document.get("roles"), dict)
        else None
    )
    if not isinstance(identity, dict) or not isinstance(configured, dict):
        raise MappingError("canonical launch receipt has no resolved identity")
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
            raise MappingError(
                f"canonical launch receipt identity differs from current {semantic_role}.{field}"
            )
    return _require_fresh_catalog_coverage(identity, policy)


def _validated_roles(
    roles_path: Path,
    manifest: dict[str, object],
    design: dict[str, object],
    *,
    catalog_runner: Callable | None = None,
) -> list[dict[str, Any]]:
    """Resolve every mapped activation through the canonical launch-spec contract."""
    raw_document = _regular_bytes(roles_path, "canonical model role policy")
    document = _document(raw_document)
    try:
        validate_model_roles(document)
    except ModelRoleValidationError as error:
        raise MappingError(str(error)) from error
    posture = manifest.get("reasoningPosture")
    if not isinstance(posture, str) or not posture.strip():
        raise MappingError("mapping reasoningPosture must be a non-empty string")
    if posture not in REASONING_POSTURES:
        raise MappingError(f"mapping reasoningPosture {posture!r} is not an admitted posture")
    reasoning = document.get("reasoningPolicy")
    postures = reasoning.get("postures") if isinstance(reasoning, dict) else None
    if not isinstance(postures, dict) or posture not in postures:
        raise MappingError(
            f"mapping reasoningPosture {posture!r} is not admitted by the current policy"
        )
    default_posture = (
        reasoning.get("defaultPosture") if isinstance(reasoning, dict) else None
    )
    if posture != default_posture:
        raise MappingError(
            "mapping reasoningPosture differs from canonical policy defaultPosture"
        )
    allowed = (
        postures[posture].get("allowedRoles")
        if isinstance(postures[posture], dict)
        else None
    )
    if not isinstance(allowed, list):
        raise MappingError(
            f"canonical policy posture {posture!r} has malformed allowedRoles"
        )
    bindings = manifest.get("nodeAgents")
    if not isinstance(bindings, list):
        raise MappingError("mapping nodeAgents is malformed")
    by_node_role: dict[str, str] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            raise MappingError("mapping contains a malformed node-agent binding")
        node = binding.get("node")
        role = binding.get("role")
        if not isinstance(node, str) or not node or not isinstance(role, str) or not role:
            raise MappingError("mapping contains a malformed node-agent binding")
        if node in by_node_role:
            raise MappingError(f"mapping repeats node-agent binding for {node}")
        by_node_role[node] = role
        access = binding.get("workspaceAccess", "read")
        if access not in {"read", "write"}:
            raise MappingError(f"node {node} has unsupported workspaceAccess")
        if role not in allowed:
            raise MappingError(
                f"mapping reasoningPosture {posture!r} does not admit semantic role {role!r}"
            )
    nodes = design.get("nodes")
    transitions = design.get("transitions")
    if not isinstance(nodes, list) or not isinstance(transitions, list):
        raise MappingError("design nodes/transitions are malformed")
    node_kind = {
        str(node.get("id")): node.get("kind")
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    resolved: list[dict[str, Any]] = []
    for binding in bindings:
        node = str(binding["node"])
        role_name = str(binding["role"])
        kind = node_kind.get(node)
        review_mode = "none"
        writer_role: str | None = None
        task_class = _task_class(document, role_name)
        if kind == "decision":
            predecessors = [
                str(edge.get("from"))
                for edge in transitions
                if isinstance(edge, dict)
                and edge.get("to") == node
                and str(edge.get("from")) in by_node_role
            ]
            if len(predecessors) != 1:
                raise MappingError(
                    f"decision node {node} requires exactly one model predecessor for independent review"
                )
            task_class = "verification"
            review_mode = "exact-opposite"
            writer_role = by_node_role[predecessors[0]]
        elif kind != "agent":
            raise MappingError(f"mapped activation {node} is not an admitted agent/decision node")
        try:
            receipt = resolve_launch_spec(
                raw_document,
                task_class=task_class,
                reasoning_posture=posture,
                semantic_role=role_name,
                review_mode=review_mode,
                writer_role=writer_role,
                catalog_runner=catalog_runner,
            )
        except LaunchSpecError as error:
            raise MappingError(str(error)) from error
        identity = _canonical_receipt_identity(
            receipt,
            raw_document=raw_document,
            document=document,
            semantic_role=role_name,
            task_class=task_class,
            reasoning_posture=posture,
            review_mode=review_mode,
            writer_role=writer_role,
        )
        backend = identity["backend"]
        if backend not in SUPPORTED_BACKENDS:
            raise MappingError(
                f"role {role_name} backend {backend!r} has no verified Humanize runtime "
                "mapping; Cursor must not be relabelled as Claude"
            )
        resolved.append(
            {
                "node": node,
                "role": role_name,
                "backend": str(backend),
                "model": str(identity["catalogModelId"]),
                "canonicalModel": str(identity["model"]),
                "catalogModelId": str(identity["catalogModelId"]),
                "effort": str(identity["effort"]),
                "provider": str(identity["provider"]),
                "upstreamProviderFamily": str(identity["upstreamProviderFamily"]),
                "modelFamily": str(identity["modelFamily"]),
                "intelligenceClass": str(identity["intelligenceClass"]),
                "permission": "read-only"
                if binding.get("workspaceAccess", "read") == "read"
                else "workspace-write",
                "receipt": receipt,
            }
        )
    return resolved


def _open_child_directory(
    parent_fd: int, name: str, *, create: bool, exclusive: bool = False
) -> int:
    if not SAFE_COMPONENT.fullmatch(name):
        raise MappingError(f"unsafe launch evidence namespace component: {name!r}")
    if create:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            if exclusive:
                raise MappingError(
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
        raise MappingError("launch evidence namespace component is not a directory")
    return descriptor


def _write_exclusive(directory_fd: int, name: str, payload: bytes) -> tuple[int, int]:
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
                raise MappingError("launch evidence write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise MappingError("launch evidence destination is not a regular file")
        return status.st_dev, status.st_ino
    finally:
        if descriptor is not None:
            os.close(descriptor)


def create_launch_evidence(
    root: Path,
    adapter_id: str,
    run_id: str,
    routes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Exclusively seal every mapped activation receipt before dispatch."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise MappingError("launch evidence requires Linux no-follow descriptor support")
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
        for index, route in enumerate(routes):
            receipt = route.get("receipt")
            if not isinstance(receipt, dict):
                raise MappingError("mapped activation has no canonical launch receipt")
            rendered = canonical_bytes(receipt) + b"\n"
            filename = f"{index:03d}.json"
            _write_exclusive(receipt_fd, filename, rendered)
            bindings.append(
                {
                    "activationId": str(route["node"]),
                    "semanticRole": str(receipt["request"]["semanticRole"]),
                    "receiptPath": (base / "model-launches" / filename).as_posix(),
                    "receiptSha256": str(receipt["receiptSha256"]),
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
    except MappingError:
        raise
    except OSError as error:
        raise MappingError(
            f"launch evidence namespace is not a real no-follow directory: {error}"
        ) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def verify_launch_evidence(
    root: Path,
    evidence: dict[str, Any],
    expected_posture: str,
    *,
    expected_policy_sha256: str | None = None,
    expected_routes: list[dict[str, Any]] | None = None,
) -> None:
    if not isinstance(expected_posture, str) or not expected_posture.strip():
        raise MappingError("mapping reasoningPosture must be a non-empty string")
    claimed = evidence.get("evidenceSha256")
    unsigned = {key: value for key, value in evidence.items() if key != "evidenceSha256"}
    if claimed != canonical_sha256(unsigned):
        raise MappingError("launch evidence digest is invalid")
    stored = _regular_bytes(
        root / str(evidence.get("evidencePath", "")), "launch evidence"
    )
    if stored != canonical_bytes(evidence) + b"\n":
        raise MappingError("stored launch evidence was replaced or modified")
    bindings = evidence.get("activations")
    if not isinstance(bindings, list):
        raise MappingError("launch evidence activations are malformed")
    route_by_node = (
        {str(route.get("node")): route for route in expected_routes}
        if expected_routes is not None
        else {}
    )
    if expected_routes is not None and len(route_by_node) != len(expected_routes):
        raise MappingError("expected launch routes are malformed")
    for binding in bindings:
        if not isinstance(binding, dict):
            raise MappingError("launch evidence activation is malformed")
        raw = _regular_bytes(
            root / str(binding.get("receiptPath", "")), "native launch receipt"
        )
        if digest_bytes(raw) != binding.get("receiptFileSha256"):
            raise MappingError("native launch receipt was replaced or modified")
        try:
            receipt = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MappingError(f"native launch receipt is invalid JSON: {error}") from error
        if not isinstance(receipt, dict) or raw != canonical_bytes(receipt) + b"\n":
            raise MappingError("native launch receipt bytes are not canonical")
        unsigned_receipt = {
            key: value for key, value in receipt.items() if key != "receiptSha256"
        }
        request = receipt.get("request")
        policy = receipt.get("policy")
        resolved = receipt.get("resolved")
        activation = binding.get("activationId")
        if (
            receipt.get("schema") != CANONICAL_RECEIPT_SCHEMA
            or not isinstance(request, dict)
            or not isinstance(policy, dict)
            or not isinstance(resolved, dict)
        ):
            raise MappingError("native launch receipt binding is invalid")
        if request.get("reasoningPosture") != expected_posture:
            raise MappingError(
                "native launch receipt reasoningPosture does not match the bound mapping posture"
            )
        if (
            not isinstance(resolved.get("catalogModelId"), str)
            or not resolved["catalogModelId"]
            or policy.get("liveCatalogAuthenticatedByResolver") is not True
            or not isinstance(policy.get("freshCatalogEvidence"), dict)
        ):
            raise MappingError(
                "native launch receipt lacks a fresh concrete catalog model binding"
            )
        _require_fresh_catalog_coverage(resolved, policy)
        if (
            expected_policy_sha256 is not None
            and policy.get("modelsDocumentSha256") != expected_policy_sha256
        ):
            raise MappingError(
                "native launch receipt does not bind the current canonical policy bytes"
            )
        if (
            receipt.get("receiptSha256") != canonical_sha256(unsigned_receipt)
            or receipt.get("receiptSha256") != binding.get("receiptSha256")
            or request.get("semanticRole") != binding.get("semanticRole")
        ):
            raise MappingError("native launch receipt binding is invalid")
        if expected_routes is not None:
            route = route_by_node.get(str(activation))
            if route is None or receipt != route.get("receipt"):
                raise MappingError(
                    "native launch receipt differs from the canonical resolver result"
                )


def _open_relative_directory(root: Path, parts: tuple[str, ...]) -> int:
    current = os.open(
        str(root),
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        for component in parts:
            next_fd = _open_child_directory(current, component, create=False)
            os.close(current)
            current = next_fd
        return current
    except Exception:
        os.close(current)
        raise


def _replace_existing_result(root: Path, result_path: Path, payload: bytes) -> None:
    try:
        relative = result_path.relative_to(root)
    except ValueError as error:
        raise MappingError("adapter result path escapes the project root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise MappingError("adapter result path is unsafe")
    parent_fd: int | None = None
    initial_fd: int | None = None
    temporary = f".{relative.name}.{uuid.uuid4().hex}.tmp"
    try:
        parent_fd = _open_relative_directory(root, tuple(relative.parts[:-1]))
        initial_fd = os.open(
            relative.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        initial_status = os.fstat(initial_fd)
        if not stat.S_ISREG(initial_status.st_mode):
            raise MappingError("adapter result is not a regular file")
        os.close(initial_fd)
        initial_fd = None
        temporary_identity = _write_exclusive(parent_fd, temporary, payload)
        current = os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (
            initial_status.st_dev,
            initial_status.st_ino,
        ):
            raise MappingError("adapter result changed during evidence binding")
        os.replace(
            temporary,
            relative.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
        retained = os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False)
        if (retained.st_dev, retained.st_ino) != temporary_identity:
            raise MappingError("bound adapter result replacement was not retained")
    except MappingError:
        raise
    except OSError as error:
        raise MappingError(f"adapter result cannot be bound safely: {error}") from error
    finally:
        if initial_fd is not None:
            os.close(initial_fd)
        if parent_fd is not None:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(parent_fd)


def bind_adapter_result(
    root: Path,
    result_path: Path,
    result: dict[str, Any],
    adapter_input_sha256: str,
    launch_evidence: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    reserved = {"adapterInputSha256", "launchEvidence", "launchEvidenceSha256"}
    if reserved & set(result):
        raise MappingError("adapter result forged reserved launch evidence fields")
    bound = dict(result)
    bound["adapterInputSha256"] = adapter_input_sha256
    bound["launchEvidence"] = launch_evidence
    bound["launchEvidenceSha256"] = launch_evidence["evidenceSha256"]
    evidence_paths = bound.get("evidence")
    if not isinstance(evidence_paths, list) or not all(
        isinstance(item, str) for item in evidence_paths
    ):
        raise MappingError("adapter result evidence list is malformed")
    bound["evidence"] = [
        *evidence_paths,
        str(launch_evidence["evidencePath"]),
        *[
            str(item["receiptPath"])
            for item in launch_evidence["activations"]
        ],
    ]
    rendered = canonical_bytes(bound) + b"\n"
    _replace_existing_result(root, result_path, rendered)
    if _regular_bytes(result_path, "bound adapter result") != rendered:
        raise MappingError("bound adapter result was replaced or modified")
    return bound, digest_bytes(rendered)


def write_terminal_evidence(
    root: Path,
    result_path: Path,
    design_id: str,
    run_id: str,
    result: dict[str, Any],
    result_sha256: str,
    adapter_input_sha256: str,
    launch_evidence: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    relative_result = result_path.relative_to(root)
    terminal_path = result_path.with_name("terminal-evidence.json")
    terminal: dict[str, Any] = {
        "schema": "humanize-adapter-terminal-evidence/v1",
        "designId": design_id,
        "runId": run_id,
        "terminal": result["terminal"],
        "adapterInputSha256": adapter_input_sha256,
        "launchEvidencePath": launch_evidence["evidencePath"],
        "launchEvidenceSha256": launch_evidence["evidenceSha256"],
        "receiptSha256": [
            binding["receiptSha256"]
            for binding in launch_evidence["activations"]
        ],
        "resultPath": relative_result.as_posix(),
        "resultSha256": result_sha256,
    }
    terminal["terminalEvidenceSha256"] = canonical_sha256(terminal)
    rendered = canonical_bytes(terminal) + b"\n"
    parent_fd = _open_relative_directory(root, tuple(relative_result.parts[:-1]))
    try:
        _write_exclusive(parent_fd, terminal_path.name, rendered)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    if _regular_bytes(terminal_path, "adapter terminal evidence") != rendered:
        raise MappingError("adapter terminal evidence was replaced or modified")
    return terminal_path, terminal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--hmz", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        root = args.root.expanduser().resolve(strict=True)
        manifest_path = args.manifest.expanduser()
        manifest = validate_full_mapping(root, manifest_path)
        bound_posture = manifest.get("reasoningPosture")
        if not isinstance(bound_posture, str) or not bound_posture.strip():
            raise MappingError("mapping reasoningPosture must be a non-empty string")
        if bound_posture not in REASONING_POSTURES:
            raise MappingError(
                f"mapping reasoningPosture {bound_posture!r} is not an admitted posture"
            )
        input_path = project_path(root, manifest["inputPath"], "inputPath")
        payload = load_object(input_path)
        reserved_input = {
            "humanizeAdapterResultPath",
            "humanizeAdapterLaunchEvidence",
            "humanizeAdapterLaunchEvidenceSha256",
        }
        if reserved_input & set(payload):
            raise MappingError("input uses reserved humanize adapter launch fields")
        run_id = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        design = load_object(root / manifest["designPath"])
        design_id = design["id"]
        if not isinstance(design_id, str) or not SAFE_COMPONENT.fullmatch(design_id):
            raise MappingError("mapped design id is unsafe for an evidence namespace")
        result_path = root / "workflow-output" / "humanize" / design_id / run_id / "result.json"
        node_agents = manifest["nodeAgents"]
        routes: list[dict[str, Any]] = []
        roles_path: Path | None = None
        raw_roles: bytes | None = None
        roles_sha256: str | None = None
        if node_agents:
            roles_path = canonical_roles_path(root)
            raw_roles = _regular_bytes(roles_path, "canonical model role policy")
            roles_sha256 = digest_bytes(raw_roles)
            routes = _validated_roles(roles_path, manifest, design)
            if any(
                route["receipt"]["policy"]["modelsDocumentSha256"]
                != roles_sha256
                for route in routes
            ):
                raise MappingError("canonical model role policy changed during role resolution")
        hmz = validate_hmz_binary(args.hmz)
        launch_evidence = create_launch_evidence(root, design_id, run_id, routes)
        verify_launch_evidence(
            root,
            launch_evidence,
            bound_posture,
            expected_policy_sha256=roles_sha256,
            expected_routes=routes,
        )
        payload["humanizeAdapterResultPath"] = str(result_path.relative_to(root))
        payload["humanizeAdapterLaunchEvidence"] = launch_evidence
        payload["humanizeAdapterLaunchEvidenceSha256"] = launch_evidence[
            "evidenceSha256"
        ]
        adapter_input = canonical_bytes(payload)
        adapter_input_sha256 = digest_bytes(adapter_input)
        flow_bytes = _regular_bytes(root / str(manifest["artifact"]), "generated Humanize flow")
        context_routes = [
            {**route, "launchReceipt": route["receipt"]}
            for route in routes
        ]
        with build_execution_context(
            pinned=hmz,
            routes=context_routes,
            sources={"generated-flow": flow_bytes},
            account_mode="ambient",
        ) as context:
            argv = [*context.argv_prefix(), "exec", "-f", context.source_path("generated-flow")]
            for route in routes:
                argv.extend([
                    "-a",
                    f"cli={route['backend']},model={route['model']},"
                    f"effort={route['effort']},permission={route['permission']}",
                ])
            argv.append(adapter_input.decode("utf-8"))
            # Re-run the complete admission path immediately before executable flow
            # dispatch so mapping, receipts, policy, and the sealed execution
            # closure are all current at the same boundary.
            if validate_full_mapping(root, manifest_path) != manifest:
                raise MappingError("mapping changed during run preparation")
            verify_launch_evidence(
                root,
                launch_evidence,
                bound_posture,
                expected_policy_sha256=roles_sha256,
                expected_routes=routes,
            )
            if roles_path is not None and _regular_bytes(
                roles_path, "canonical model role policy"
            ) != raw_roles:
                raise MappingError("canonical model role policy changed before hmz dispatch")
            context.revalidate()
            try:
                completed = subprocess.run(
                    argv,
                    cwd=root,
                    env=context.environment,
                    pass_fds=context.pass_fds,
                    check=False,
                    stdout=sys.stderr,
                    stderr=sys.stderr,
                )
            finally:
                context.revalidate()
            verify_launch_evidence(
                root,
                launch_evidence,
                bound_posture,
                expected_policy_sha256=roles_sha256,
                expected_routes=routes,
            )
            if roles_path is not None and _regular_bytes(
                roles_path, "canonical model role policy"
            ) != raw_roles:
                raise MappingError("canonical model role policy changed during hmz dispatch")
        if completed.returncode != 0:
            raise MappingError(f"hmz exited {completed.returncode} without a valid adapter result")
        try:
            result = json.loads(_regular_bytes(result_path, "adapter result"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MappingError(f"adapter result is invalid JSON: {error}") from error
        if not isinstance(result, dict):
            raise MappingError("adapter result root must be an object")
        if result.get("terminal") not in {"COMPLETE", "HUMAN_BLOCKED", "REVISE"}:
            raise MappingError("adapter result has an invalid terminal")
        result, result_sha256 = bind_adapter_result(
            root,
            result_path,
            result,
            adapter_input_sha256,
            launch_evidence,
        )
        terminal_path, terminal_evidence = write_terminal_evidence(
            root,
            result_path,
            design_id,
            run_id,
            result,
            result_sha256,
            adapter_input_sha256,
            launch_evidence,
        )
        verify_launch_evidence(
            root,
            launch_evidence,
            bound_posture,
            expected_policy_sha256=roles_sha256,
            expected_routes=routes,
        )
        if _regular_bytes(result_path, "bound adapter result") != canonical_bytes(result) + b"\n":
            raise MappingError("bound adapter result changed before terminal emission")
    except (OSError, ValueError, json.JSONDecodeError, ExecutionContextError) as error:
        print(f"humanize-run: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "resultPath": str(result_path),
                "result": result,
                "terminalEvidencePath": str(terminal_path),
                "terminalEvidence": terminal_evidence,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
