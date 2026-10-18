#!/usr/bin/env python3
"""Resolve one explicit native launch specification from the canonical model-role policy.

For every production resolve call this module freshly invokes the fixed,
allowlisted authenticated native catalog command for the actual backend used
by the selected role (and, for ``exact-opposite`` review, the writer role as
well), verifies the configured model and provider-local effort against that
fresh catalog, and emits a deterministic receipt binding the exact input
bytes, the freshly authenticated catalog evidence, and the selected native
identity. Persisted ``catalogResolution`` digests and receipts are audit
snapshots only; they never authorize or deny dispatch. Only the structural
schema of ``catalogResolution`` is checked; its ``status``, ``source``,
``verifiedRoles``, and digests are recorded into the receipt for audit, but a
caller flipping any of them cannot authorize or deny an otherwise valid fresh
dispatch. The fresh live-catalog verification is the sole authorization gate.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Callable

from project_paths import project_root


REPO = project_root()
DEFAULT_PATH = REPO / "config" / "workflows" / "models.json"
REVIEW_MODES = frozenset({"none", "exact-opposite"})
IDENTITY_FIELDS = (
    "backend", "provider", "upstreamProviderFamily", "model", "modelFamily", "effort",
    "intelligenceClass", "dispatchStatus",
)


class LaunchSpecError(ValueError):
    """The requested launch cannot be proven from the supplied policy snapshot."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_sha256(value: Any) -> str:
    return digest_bytes(canonical_bytes(value))


def resolved_role_identities(roles: dict[str, Any], role_names: list[str]) -> list[dict[str, Any]]:
    return [
        {"semanticRole": name, **{field: roles[name].get(field) for field in IDENTITY_FIELDS}}
        for name in sorted(role_names)
    ]


def _validator() -> Any:
    path = Path(__file__).with_name("validate_model_roles.py")
    spec = importlib.util.spec_from_file_location("_launch_spec_model_validator", path)
    if spec is None or spec.loader is None:
        raise LaunchSpecError(f"cannot load model-role validator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _live_catalog_module() -> Any:
    path = Path(__file__).with_name("live_catalog.py")
    spec = importlib.util.spec_from_file_location("_launch_spec_live_catalog", path)
    if spec is None or spec.loader is None:
        raise LaunchSpecError(f"cannot load live catalog producer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _identity(role_name: str, role: dict[str, Any], *, catalog_model_id: str | None = None) -> dict[str, Any]:
    identity = {"semanticRole": role_name, **{field: role.get(field) for field in IDENTITY_FIELDS}}
    if catalog_model_id is not None:
        identity["catalogModelId"] = catalog_model_id
    return identity


def _active_verified_role(document: dict[str, Any], role_name: str) -> dict[str, Any]:
    roles = document["roles"]
    role = roles.get(role_name)
    if not isinstance(role, dict):
        future = document["capabilityPolicy"].get("futureCandidates", [])
        excluded = document["capabilityPolicy"].get("excludedFamilies", [])
        if "deepseek" in role_name and any(item.get("modelFamily") == "deepseek" for item in future):
            raise LaunchSpecError(f"semantic role {role_name!r} is a future candidate, not dispatchable")
        if "composer" in role_name and any(item.get("modelFamily") == "composer-2.5" for item in excluded):
            raise LaunchSpecError(f"semantic role {role_name!r} belongs to an excluded model family")
        raise LaunchSpecError(f"unknown semantic role {role_name!r}; parent-model inheritance is forbidden")
    if role.get("dispatchStatus") != "active":
        raise LaunchSpecError(
            f"semantic role {role_name!r} is {role.get('dispatchStatus')!r}, not dispatchable"
        )
    effort = role.get("effort")
    if not isinstance(effort, str) or not effort:
        raise LaunchSpecError(f"semantic role {role_name!r} has no explicit provider-local effort")
    return role


def _exact_reviewer_role(document: dict[str, Any], writer: dict[str, Any]) -> str:
    matches: list[str] = []
    for pair in document["capabilityPolicy"]["reviewPairings"]:
        expected_writer = pair["writer"]
        if (
            expected_writer["upstreamProviderFamily"] == writer["upstreamProviderFamily"]
            and expected_writer["modelFamily"] == writer["modelFamily"]
            and writer["effort"] in expected_writer["efforts"]
        ):
            matches.append(pair["reviewer"]["role"])
    if len(matches) > 1:
        raise LaunchSpecError("writerRole has multiple exact opposite-provider review pairings")
    if len(matches) == 1:
        return matches[0]
    default = document["capabilityPolicy"]["providerReviewerDefaults"].get(
        writer["upstreamProviderFamily"]
    )
    if not isinstance(default, dict):
        raise LaunchSpecError("writerRole has no exact opposite-provider reviewer default")
    reviewer_role = default.get("reviewerRole")
    reviewer = document["roles"].get(reviewer_role)
    if (
        not isinstance(reviewer_role, str)
        or not isinstance(reviewer, dict)
        or reviewer.get("upstreamProviderFamily") == writer.get("upstreamProviderFamily")
        or reviewer.get("effort") != default.get("requiredEffort")
    ):
        raise LaunchSpecError("writerRole has no valid exact opposite-provider reviewer default")
    return reviewer_role


def _verify_role_fresh(
    live_catalog: Any,
    role: dict[str, Any],
    *,
    runner: Callable | None = None,
) -> tuple[Any, str]:
    """Freshly produce the catalog for the role's backend and verify model+effort."""
    backend = role["backend"]
    try:
        evidence = live_catalog.produce(backend, runner=runner)
    except live_catalog.CatalogError as error:
        raise LaunchSpecError(f"fresh catalog for backend {backend!r} unavailable: {error}") from error
    effort_policy = role.get("effortPolicy")
    if effort_policy is not None:
        if effort_policy != "highest-available":
            raise LaunchSpecError(
                f"role {role.get('model')!r} has unsupported effortPolicy {effort_policy!r}"
            )
        try:
            highest = live_catalog.highest_available_effort(
                backend, role["model"], evidence
            )
        except live_catalog.CatalogError as error:
            raise LaunchSpecError(
                f"role {role.get('model')!r} highest available effort cannot be resolved "
                f"from fresh {backend} catalog: {error}"
            ) from error
        if role["effort"] != highest:
            raise LaunchSpecError(
                f"role {role.get('model')!r} pins effort {role.get('effort')!r}, "
                f"but highest available effort is {highest!r}"
            )
    try:
        catalog_model_id = live_catalog.verify_role(backend, role["model"], role["effort"], evidence)
    except live_catalog.CatalogError as error:
        raise LaunchSpecError(
            f"role {role.get('model')!r} effort {role.get('effort')!r} not covered by fresh "
            f"{backend} catalog: {error}"
        ) from error
    return evidence, catalog_model_id


def resolve_launch_spec(
    raw_document: bytes,
    *,
    task_class: str,
    reasoning_posture: str,
    semantic_role: str,
    review_mode: str,
    writer_role: str | None = None,
    catalog_runner: Callable | None = None,
) -> dict[str, Any]:
    """Resolve one launch spec against the canonical policy and fresh catalogs.

    ``catalog_runner`` is test-only and is forwarded to ``live_catalog.produce``
    so tests never invoke a real CLI. Production callers must leave it ``None``.
    """
    if not isinstance(raw_document, bytes) or not raw_document:
        raise LaunchSpecError("models document bytes are required")
    try:
        document = json.loads(raw_document.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LaunchSpecError(f"models document is not valid UTF-8 JSON: {error}") from error
    if not isinstance(document, dict):
        raise LaunchSpecError("models document must be a JSON object")

    validator = _validator()
    try:
        validator.validate(document)
    except validator.ValidationError as error:
        raise LaunchSpecError(str(error)) from error

    resolution = document.get("catalogResolution")
    if not isinstance(resolution, dict):
        raise LaunchSpecError("catalogResolution must be an object")
    # Persisted catalogResolution is an audit snapshot only; it never authorizes
    # or denies dispatch. Only its structural schema is checked here; the
    # status, source, verifiedRoles, and digests are recorded into the receipt
    # for audit but a caller flipping any of them cannot authorize or deny an
    # otherwise valid fresh dispatch. The fresh live-catalog verification below
    # is the sole authorization gate.
    if resolution.get("schema") != "workflow-model-catalog-resolution/v1":
        raise LaunchSpecError("unsupported catalog resolution schema")

    live_catalog = _live_catalog_module()
    role = _active_verified_role(document, semantic_role)
    task_classes = document["capabilityPolicy"]["taskClassRoles"]
    qualified_roles = task_classes.get(task_class)
    if not isinstance(qualified_roles, list):
        raise LaunchSpecError(f"unknown taskClass {task_class!r}")
    if semantic_role not in qualified_roles:
        raise LaunchSpecError(f"semantic role {semantic_role!r} is not qualified for taskClass {task_class!r}")
    postures = document["reasoningPolicy"]["postures"]
    posture = postures.get(reasoning_posture)
    if not isinstance(posture, dict):
        raise LaunchSpecError(f"unknown reasoning posture {reasoning_posture!r}")
    if semantic_role not in posture["allowedRoles"]:
        raise LaunchSpecError(
            f"semantic role {semantic_role!r} exceeds reasoning posture {reasoning_posture!r}"
        )
    if review_mode not in REVIEW_MODES:
        raise LaunchSpecError(f"unsupported reviewMode {review_mode!r}")

    writer_identity: dict[str, Any] | None = None
    catalog_evidence_by_backend: dict[str, Any] = {}
    if review_mode == "exact-opposite":
        if not writer_role:
            raise LaunchSpecError("reviewMode exact-opposite requires writerRole")
        if task_class != "verification" or role.get("intelligenceClass") != "verification":
            raise LaunchSpecError("reviewMode exact-opposite requires a verification task and role")
        writer = _active_verified_role(document, writer_role)
        expected_reviewer = _exact_reviewer_role(document, writer)
        if semantic_role != expected_reviewer:
            raise LaunchSpecError(
                f"exact reviewer for writerRole {writer_role!r} is {expected_reviewer!r}, "
                f"not {semantic_role!r}"
            )
        writer_evidence, writer_catalog_model_id = _verify_role_fresh(live_catalog, writer, runner=catalog_runner)
        catalog_evidence_by_backend[writer["backend"]] = writer_evidence.record()
        writer_identity = _identity(writer_role, writer, catalog_model_id=writer_catalog_model_id)
    elif writer_role is not None:
        raise LaunchSpecError("writerRole is allowed only with reviewMode exact-opposite")
    elif role.get("intelligenceClass") == "verification":
        raise LaunchSpecError("verification roles require reviewMode exact-opposite and writerRole")

    role_evidence, role_catalog_model_id = _verify_role_fresh(live_catalog, role, runner=catalog_runner)
    if role["backend"] not in catalog_evidence_by_backend:
        catalog_evidence_by_backend[role["backend"]] = role_evidence.record()

    request = {
        "taskClass": task_class,
        "reasoningPosture": reasoning_posture,
        "semanticRole": semantic_role,
        "writerRole": writer_role,
        "reviewMode": review_mode,
    }
    policy = {
        "modelsSchema": document["schema"],
        "modelsDocumentSha256": digest_bytes(raw_document),
        "catalogResolutionSha256": canonical_sha256(resolution),
        "capabilityPolicySha256": canonical_sha256(document["capabilityPolicy"]),
        "reasoningPolicySha256": canonical_sha256(document["reasoningPolicy"]),
        # catalogStatus is recorded from the persisted snapshot for audit; the
        # schema accepts both bootstrap-unverified and verified, so a flipped
        # status cannot deny. It never authorizes: the fresh live-catalog check
        # below is the sole authorization gate.
        "catalogStatus": resolution.get("status"),
        # catalogSource is a fixed constant: this resolver IS the authenticated
        # native CLI path, regardless of what a persisted snapshot claims. A
        # caller flipping the persisted source therefore cannot change the
        # receipt's bound source or break the receipt schema.
        "catalogSource": "authenticated-native-cli",
        "catalogEvidenceReceipt": resolution.get("evidenceReceipt"),
        "liveCatalogAuthenticatedByResolver": True,
        "freshCatalogEvidence": catalog_evidence_by_backend,
    }
    receipt: dict[str, Any] = {
        "schema": "workflow-native-launch-receipt/v1",
        "request": request,
        "resolved": _identity(semantic_role, role, catalog_model_id=role_catalog_model_id),
        "writer": writer_identity,
        "policy": policy,
    }
    receipt["receiptSha256"] = canonical_sha256(receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--task-class", required=True)
    parser.add_argument("--reasoning-posture", required=True)
    parser.add_argument("--semantic-role", required=True)
    parser.add_argument("--review-mode", choices=sorted(REVIEW_MODES), required=True)
    parser.add_argument("--writer-role")
    parser.add_argument("--output", type=Path, help="write the receipt atomically instead of stdout")
    return parser


def main(argv: list[str] | None = None, *, catalog_runner: Callable | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw = args.models.read_bytes()
        receipt = resolve_launch_spec(
            raw,
            task_class=args.task_class,
            reasoning_posture=args.reasoning_posture,
            semantic_role=args.semantic_role,
            review_mode=args.review_mode,
            writer_role=args.writer_role,
            catalog_runner=catalog_runner,
        )
        rendered = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.output is None:
            print(rendered, end="")
        else:
            target = args.output.resolve()
            if not target.parent.is_dir():
                raise LaunchSpecError(
                    f"receipt output parent does not exist: {target.parent}; create authorized PM evidence storage first"
                )
            if target.exists() or target.is_symlink():
                raise LaunchSpecError(f"receipt output already exists: {target}; overwrite is forbidden")
            temporary = target.with_name(target.name + ".tmp")
            if temporary.exists() or temporary.is_symlink():
                raise LaunchSpecError(f"temporary receipt path already exists: {temporary}")
            try:
                temporary.write_text(rendered, encoding="utf-8")
                temporary.replace(target)
            finally:
                if temporary.is_file() and not temporary.is_symlink():
                    temporary.unlink()
    except (OSError, LaunchSpecError) as error:
        print(f"[resolve-launch-spec] {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
