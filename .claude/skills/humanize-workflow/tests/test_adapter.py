from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
COMMITTED_SMOKE_RECEIPT = HERE / "evidence/humanize2-smoke-receipt.json"
ROLE_TEMPLATE = (
    HERE.parents[1]
    / "default-mode/assets/project/config/workflows/models.json"
)
SPEC = importlib.util.spec_from_file_location("safe_mapping", SCRIPTS / "safe_mapping.py")
assert SPEC is not None and SPEC.loader is not None
SAFE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SAFE
SPEC.loader.exec_module(SAFE)

RUN_SPEC = importlib.util.spec_from_file_location("run_mapping", SCRIPTS / "run_mapping.py")
assert RUN_SPEC is not None and RUN_SPEC.loader is not None
RUN = importlib.util.module_from_spec(RUN_SPEC)
sys.modules[RUN_SPEC.name] = RUN
RUN_SPEC.loader.exec_module(RUN)
CANONICAL_RESOLVER = RUN.resolve_launch_spec

ENSURE_SPEC = importlib.util.spec_from_file_location(
    "ensure_humanize", SCRIPTS / "ensure_humanize.py"
)
assert ENSURE_SPEC is not None and ENSURE_SPEC.loader is not None
ENSURE = importlib.util.module_from_spec(ENSURE_SPEC)
sys.modules[ENSURE_SPEC.name] = ENSURE
ENSURE_SPEC.loader.exec_module(ENSURE)


def design() -> dict[str, object]:
    return {
        "schema": "workflow-design/v1",
        "id": "humanize-safe-smoke",
        "entry": "seed",
        "goal": {"objective": "Produce one bounded result.", "acceptance": "Result is present."},
        "admission": {"reasons": ["multi-stage"], "demotionCondition": "Use one native subagent."},
        "governance": {"reviewPolicy": "independent", "completionProof": "none", "malformedDecision": "unresolved"},
        "budgets": {"maxModelCalls": 2},
        "requiredCapabilities": ["agent-invocation", "fresh-sessions", "conditional-routing", "structured-output", "observability"],
        "state": [
            {"name": "task", "type": "string", "source": "input"},
            {"name": "answer", "type": "string", "source": "worker"},
            {"name": "reason", "type": "string", "source": "judge"},
        ],
        "nodes": [
            {"id": "seed", "kind": "script", "reads": [], "writes": ["task"]},
            {"id": "worker", "kind": "agent", "role": "task", "session": "fresh", "workspaceAccess": "write", "timeoutSeconds": 300, "reads": ["task"], "writes": ["answer"]},
            {"id": "judge", "kind": "decision", "role": "advisor", "session": "fresh", "workspaceAccess": "read", "timeoutSeconds": 300, "reads": ["answer"], "writes": ["reason"], "outcomes": ["PASS", "REVISE"], "onMissing": "unresolved"},
            {"id": "success", "kind": "terminal", "outcome": "success", "reads": ["answer"], "writes": []},
            {"id": "unresolved", "kind": "terminal", "outcome": "unresolved", "reads": ["reason"], "writes": []},
        ],
        "transitions": [
            {"from": "seed", "to": "worker"},
            {"from": "worker", "to": "judge"},
            {"from": "judge", "to": "success", "when": {"field": "judge.verdict", "equals": "PASS"}},
            {"from": "judge", "to": "unresolved", "when": {"field": "judge.verdict", "equals": "REVISE"}},
        ],
        "terminals": {"success": "success", "unresolved": "unresolved"},
        "sideEffects": [],
        "evidence": ["humanize cycle", "adapter result", "external terminal review"],
    }


def mapping_spec() -> dict[str, object]:
    return {
        "schema": "humanize-workflow-spec/v1",
        "inputPath": ".humanize/fixtures/humanize-safe-smoke.input.json",
        "initialState": {"answer": "", "reason": ""},
        "prompts": {"worker": "Do the bounded task.", "judge": "Judge the result."},
    }


def activation_manifest(
    node_agents: list[dict[str, str]] | None = None,
    *,
    posture: str = "balanced",
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "reasoningPosture": posture,
        "nodeAgents": node_agents
        or [
            {"node": "worker", "role": "task", "workspaceAccess": "write"},
            {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
        ],
    }
    payload.update(extra)
    return payload


def verified_role_policy() -> dict[str, object]:
    document = json.loads(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    roles = document["roles"]
    active = sorted(
        name for name, role in roles.items() if role["dispatchStatus"] == "active"
    )
    identity_fields = (
        "backend", "provider", "upstreamProviderFamily", "model", "modelFamily",
        "effort", "intelligenceClass", "dispatchStatus",
    )
    identities = [
        {
            "semanticRole": name,
            **{field: roles[name].get(field) for field in identity_fields},
        }
        for name in active
    ]
    encoded = json.dumps(
        identities, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    backends = sorted({roles[name]["backend"] for name in active})
    document["catalogResolution"].update(
        status="verified",
        verifiedRoles=active,
        verifiedBackends=backends,
        evidenceReceipt={
            "schema": "workflow-model-catalog-evidence/v1",
            "assurance": "caller-supplied-structural-evidence-not-live-authentication",
            "backendCatalogSha256": {
                backend: "sha256:" + hashlib.sha256(backend.encode()).hexdigest()
                for backend in backends
            },
            "verifiedRoleIdentitiesSha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
        },
    )
    return document


def rebind_verified_role_identities(document: dict[str, object]) -> None:
    roles = document["roles"]
    active = document["catalogResolution"]["verifiedRoles"]
    identity_fields = (
        "backend", "provider", "upstreamProviderFamily", "model", "modelFamily",
        "effort", "intelligenceClass", "dispatchStatus",
    )
    identities = [
        {
            "semanticRole": name,
            **{field: roles[name].get(field) for field in identity_fields},
        }
        for name in sorted(active)
    ]
    encoded = json.dumps(
        identities, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    document["catalogResolution"]["evidenceReceipt"][
        "verifiedRoleIdentitiesSha256"
    ] = "sha256:" + hashlib.sha256(encoded).hexdigest()


def write_role_policy(
    root: Path, document: dict[str, object] | None = None
) -> Path:
    path = root / "config/workflows/models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document or verified_role_policy(), sort_keys=True),
        encoding="utf-8",
    )
    return path


def write_mapping_project(
    root: Path,
    *,
    document: dict[str, object] | None = None,
    include_validator: bool = False,
) -> Path:
    design_document = document or design()
    spec_document = mapping_spec()
    design_path = root / ".workflow/designs/humanize-safe-smoke.workflow.json"
    spec_path = root / ".humanize/specs/humanize-safe-smoke.json"
    input_path = root / ".humanize/fixtures/humanize-safe-smoke.input.json"
    artifact = root / ".humanize/generated-flows/humanize-safe-smoke.py"
    for path in (design_path, spec_path, input_path, artifact):
        path.parent.mkdir(parents=True, exist_ok=True)
    design_path.write_text(SAFE.canonical_json(design_document), encoding="utf-8")
    spec_path.write_text(SAFE.canonical_json(spec_document), encoding="utf-8")
    input_path.write_text(SAFE.canonical_json({"task": "bounded task"}), encoding="utf-8")
    artifact.write_text(
        SAFE.generate_source(design_document, spec_document), encoding="utf-8"
    )
    write_role_policy(root)
    manifest = SAFE.build_manifest(root, design_path, spec_path, artifact)
    manifest_path = root / ".humanize/mappings/humanize-safe-smoke.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(SAFE.canonical_json(manifest), encoding="utf-8")
    if include_validator:
        validator = root / ".claude/skills/build-workflow/scripts/validate_design.py"
        validator.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            HERE.parents[1] / "build-workflow/scripts/validate_design.py", validator
        )
    return manifest_path


def write_fake_hmz(root: Path, marker: Path) -> Path:
    binary = root / ".venv/bin/hmz"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(
        f"""#!/usr/bin/env python3
from pathlib import Path
import sys

Path({str(marker)!r}).write_text("invoked", encoding="utf-8")
if sys.argv[1:] == ["--version"]:
    print("hmz 0.1.0")
else:
    raise SystemExit(91)
""",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


class FakeExecutionContext:
    """Launcher-level double; execution_context has its own adversarial suite."""

    def __init__(self, *, pinned, sources, **_):
        self.pinned = pinned
        self.sources = sources
        self.environment = {}
        self.pass_fds = ()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def argv_prefix(self, **_):
        return list(self.pinned["argvPrefix"])

    def source_path(self, label):
        return f"/proc/self/fd/{100 + sorted(self.sources).index(label)}"

    def revalidate(self):
        return None


CURSOR_CATALOG_TIP = (
    "Tip: use --model <id> (or /model <id> in interactive mode) to switch. "
    "Parameterized models also accept quoted overrides, e.g. --model "
    "'claude-opus-4-8[context=1m,effort=high,fast=false]'."
)


def canned_private_catalog_runner(
    argv: tuple[str, ...],
) -> tuple[int, bytes, bytes]:
    if tuple(argv) == (
        "claude", "-p", "--input-format", "stream-json",
        "--output-format", "stream-json", "--verbose",
    ):
        return (
            0,
            (json.dumps({
                "type": "control_response",
                "response": {
                    "subtype": "success", "request_id": "models",
                    "response": {"models": [{
                        "resolvedModel": "claude-opus-5",
                        "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
                    }]},
                },
            }).encode("utf-8") + b"\n"),
            b"",
        )
    if tuple(argv) == ("codex", "debug", "models"):
        return (
            0,
            (
                json.dumps(
                    {
                        "models": [
                            {
                                "slug": "gpt-5.6-luna",
                                "default_reasoning_level": "low",
                                "supported_reasoning_levels": [
                                    {"effort": effort}
                                    for effort in (
                                        "low",
                                        "medium",
                                        "high",
                                        "xhigh",
                                        "max",
                                    )
                                ],
                            },
                            {
                                "slug": "gpt-5.6-sol",
                                "default_reasoning_level": "low",
                                "supported_reasoning_levels": [
                                    {"effort": effort}
                                    for effort in (
                                        "low",
                                        "medium",
                                        "high",
                                        "xhigh",
                                        "max",
                                        "ultra",
                                    )
                                ],
                            },
                        ]
                    }
                ).encode("utf-8")
                + b"\n"
            ),
            b"",
        )
    if tuple(argv) == ("cursor-agent", "models"):
        return (
            0,
            (
                "Available models\n\n"
                "claude-opus-5-thinking-max - Claude Opus 5 Max\n"
                "claude-opus-5-thinking-xhigh - Claude Opus 5 X-High\n"
                "claude-sonnet-5-thinking-high - Claude Sonnet 5 High\n"
                + CURSOR_CATALOG_TIP
                + "\n"
            ).encode("utf-8"),
            b"",
        )
    raise AssertionError(f"unexpected catalog argv: {argv!r}")


class CannedCatalogResolverTest(unittest.TestCase):
    """Keep adapter tests off live catalog and model paths."""

    def setUp(self) -> None:
        super().setUp()
        # Process-lifetime Git identity must not leak from a prior test into the next one.
        SAFE._TRUSTED_GIT = None
        self.addCleanup(setattr, SAFE, "_TRUSTED_GIT", None)
        resolver = RUN.resolve_launch_spec

        def resolve_with_canned_catalog(
            *args: object, **kwargs: object
        ) -> dict[str, object]:
            supplied_runner = kwargs.pop("catalog_runner", None)
            if supplied_runner is not None:
                raise AssertionError("tests must use their private catalog runner")
            return resolver(
                *args,
                **kwargs,
                catalog_runner=canned_private_catalog_runner,
            )

        patcher = mock.patch.object(
            RUN, "resolve_launch_spec", side_effect=resolve_with_canned_catalog
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class HumanizeAdapterTest(CannedCatalogResolverTest):
    def test_generated_manifest_seals_canonical_reasoning_posture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_mapping_project(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("balanced", manifest["reasoningPosture"])

    def test_manifest_rejects_missing_and_forged_reasoning_posture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_mapping_project(root)
            baseline = json.loads(manifest_path.read_text(encoding="utf-8"))
            baseline["reasoningPosture"] = "balanced"
            for name, mutate in (
                ("missing", lambda value: value.pop("reasoningPosture")),
                ("forged", lambda value: value.update(reasoningPosture="economy")),
            ):
                candidate = dict(baseline)
                mutate(candidate)
                manifest_path.write_text(
                    SAFE.canonical_json(candidate), encoding="utf-8"
                )
                with self.subTest(name=name), self.assertRaisesRegex(
                    SAFE.MappingError, "reasoningPosture"
                ):
                    SAFE.validate_manifest(root, manifest_path)

    def test_runtime_rejects_missing_or_mismatched_manifest_posture(self) -> None:
        policy = verified_role_policy()
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(SAFE.MappingError, "reasoningPosture"):
                RUN._validated_roles(
                    roles,
                    {"nodeAgents": activation_manifest()["nodeAgents"]},
                    design(),
                )

            document = design()
            document["nodes"][1]["role"] = "research_collect"
            manifest = activation_manifest(
                [
                    {"node": "worker", "role": "research_collect", "workspaceAccess": "write"},
                    {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
                ],
                posture="economy",
            )
            with self.assertRaisesRegex(
                SAFE.MappingError, r"reasoningPosture.*defaultPosture"
            ):
                RUN._validated_roles(roles, manifest, document)

    def test_receipt_verification_rejects_self_consistent_forged_posture(self) -> None:
        policy = verified_role_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roles = root / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            routes = RUN._validated_roles(
                roles,
                {
                    "reasoningPosture": "balanced",
                    "nodeAgents": [
                        {"node": "worker", "role": "task", "workspaceAccess": "write"},
                    ],
                },
                design(),
            )
            evidence = RUN.create_launch_evidence(
                root, "humanize-safe-smoke", "fixed-run", routes
            )
            binding = evidence["activations"][0]
            receipt_path = root / binding["receiptPath"]
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["request"]["reasoningPosture"] = "economy"
            unsigned_receipt = {
                key: value for key, value in receipt.items() if key != "receiptSha256"
            }
            receipt["receiptSha256"] = RUN.canonical_sha256(unsigned_receipt)
            rendered = RUN.canonical_bytes(receipt) + b"\n"
            receipt_path.write_bytes(rendered)
            binding["receiptSha256"] = receipt["receiptSha256"]
            binding["receiptFileSha256"] = RUN.digest_bytes(rendered)
            unsigned_evidence = {
                key: value for key, value in evidence.items() if key != "evidenceSha256"
            }
            evidence["evidenceSha256"] = RUN.canonical_sha256(unsigned_evidence)
            evidence_path = root / evidence["evidencePath"]
            evidence_path.write_bytes(RUN.canonical_bytes(evidence) + b"\n")
            with self.assertRaisesRegex(SAFE.MappingError, "reasoningPosture"):
                RUN.verify_launch_evidence(root, evidence, "balanced")

    def test_receipt_with_unsupported_concrete_catalog_id_is_rejected(self) -> None:
        policy = verified_role_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roles = root / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            routes = RUN._validated_roles(
                roles,
                {
                    "reasoningPosture": "balanced",
                    "nodeAgents": [
                        {
                            "node": "worker",
                            "role": "task",
                            "workspaceAccess": "write",
                        }
                    ],
                },
                design(),
            )
            evidence = RUN.create_launch_evidence(
                root, "humanize-safe-smoke", "fixed-run", routes
            )
            binding = evidence["activations"][0]
            receipt_path = root / binding["receiptPath"]
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["resolved"]["catalogModelId"] = "gpt-5.6-sol-altered"
            unsigned_receipt = {
                key: value for key, value in receipt.items() if key != "receiptSha256"
            }
            receipt["receiptSha256"] = RUN.canonical_sha256(unsigned_receipt)
            rendered = RUN.canonical_bytes(receipt) + b"\n"
            receipt_path.write_bytes(rendered)
            binding["receiptSha256"] = receipt["receiptSha256"]
            binding["receiptFileSha256"] = RUN.digest_bytes(rendered)
            unsigned_evidence = {
                key: value for key, value in evidence.items() if key != "evidenceSha256"
            }
            evidence["evidenceSha256"] = RUN.canonical_sha256(unsigned_evidence)
            evidence_path = root / evidence["evidencePath"]
            evidence_path.write_bytes(RUN.canonical_bytes(evidence) + b"\n")
            with self.assertRaisesRegex(
                SAFE.MappingError, "concrete catalog model is not covered by fresh evidence"
            ):
                RUN.verify_launch_evidence(
                    root,
                    evidence,
                    "balanced",
                    expected_policy_sha256=RUN.digest_bytes(roles.read_bytes()),
                    expected_routes=routes,
                )

    def test_canonical_roles_path_is_fixed_under_explicit_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            caller = Path(directory) / "caller"
            root.mkdir()
            caller.mkdir()
            roles = root / "config/workflows/models.json"
            roles.parent.mkdir(parents=True)
            roles.write_text(json.dumps(verified_role_policy()), encoding="utf-8")
            prior = Path.cwd()
            try:
                os.chdir(caller)
                resolved = RUN.canonical_roles_path(root)
            finally:
                os.chdir(prior)
            self.assertEqual(resolved, roles)

    def test_complete_validator_rejects_forged_catalog_evidence(self) -> None:
        policy = verified_role_policy()
        policy["catalogResolution"]["evidenceReceipt"][
            "verifiedRoleIdentitiesSha256"
        ] = "sha256:" + "f" * 64
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(
                SAFE.MappingError, "does not bind the verified role identities"
            ):
                RUN._validated_roles(roles, {"reasoningPosture": "balanced", "nodeAgents": [
                    {"node": "worker", "role": "task", "workspaceAccess": "write"},
                    {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
                ]}, design())

    def test_complete_validator_rejects_partial_catalog_coverage(self) -> None:
        policy = verified_role_policy()
        policy["catalogResolution"]["verifiedRoles"].remove("smol")
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(
                SAFE.MappingError, "exactly cover every active role"
            ):
                RUN._validated_roles(roles, {"reasoningPosture": "balanced", "nodeAgents": [
                    {"node": "worker", "role": "task", "workspaceAccess": "write"},
                    {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
                ]}, design())

    def test_resolved_routes_carry_canonical_launch_receipts(self) -> None:
        policy = verified_role_policy()
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            routes = RUN._validated_roles(roles, {"reasoningPosture": "balanced", "nodeAgents": [
                {"node": "worker", "role": "task", "workspaceAccess": "write"},
            ]}, design())
        self.assertEqual(["worker"], [route["node"] for route in routes])
        self.assertEqual(
            ["none"],
            [route["receipt"]["request"]["reviewMode"] for route in routes],
        )
        self.assertEqual("gpt-5.6-sol", routes[0]["catalogModelId"])
        self.assertEqual(
            routes[0]["catalogModelId"],
            routes[0]["receipt"]["resolved"]["catalogModelId"],
        )

    def test_advisor_uses_the_current_native_claude_route(self) -> None:
        policy = verified_role_policy()
        raw = json.dumps(policy, sort_keys=True).encode("utf-8")
        receipt = CANONICAL_RESOLVER(
            raw,
            task_class="verification",
            reasoning_posture="balanced",
            semantic_role="advisor",
            review_mode="exact-opposite",
            writer_role="task",
            catalog_runner=canned_private_catalog_runner,
        )
        self.assertEqual("claude", receipt["resolved"]["backend"])
        self.assertEqual(
            "claude-opus-5",
            receipt["resolved"]["catalogModelId"],
        )
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_bytes(raw)
            routes = RUN._validated_roles(
                roles,
                {
                    "reasoningPosture": "balanced",
                    "nodeAgents": [
                        {
                            "node": "worker",
                            "role": "task",
                            "workspaceAccess": "write",
                        },
                        {
                            "node": "judge",
                            "role": "advisor",
                            "workspaceAccess": "read",
                        },
                    ],
                },
                design(),
            )
        self.assertEqual("claude", routes[1]["backend"])

    def test_launch_evidence_refuses_overwrite_and_symlink_escape(self) -> None:
        policy = verified_role_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            roles = root / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            routes = RUN._validated_roles(roles, {"reasoningPosture": "balanced", "nodeAgents": [
                {"node": "worker", "role": "task", "workspaceAccess": "write"},
            ]}, design())
            evidence = RUN.create_launch_evidence(
                root, "humanize-safe-smoke", "fixed-run", routes
            )
            self.assertEqual(1, len(evidence["activations"]))
            with self.assertRaisesRegex(SAFE.MappingError, "exists|overwrite"):
                RUN.create_launch_evidence(
                    root, "humanize-safe-smoke", "fixed-run", routes
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "workflow-output").mkdir()
            (root / "workflow-output/humanize").symlink_to(
                outside, target_is_directory=True
            )
            with self.assertRaisesRegex(SAFE.MappingError, "link|directory|namespace"):
                RUN.create_launch_evidence(
                    root, "humanize-safe-smoke", "fixed-run", routes
                )

    def test_launch_evidence_detects_missing_modified_and_swapped_receipts(self) -> None:
        policy = verified_role_policy()

        def prepared(root: Path) -> tuple[dict[str, object], list[Path]]:
            roles = root / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            routes = RUN._validated_roles(roles, {"reasoningPosture": "balanced", "nodeAgents": [
                {"node": "worker", "role": "task", "workspaceAccess": "write"},
            ]}, design())
            second = RUN._validated_roles(roles, {"reasoningPosture": "balanced", "nodeAgents": [
                {"node": "worker", "role": "research_collect", "workspaceAccess": "read"},
            ]}, design())[0]
            second["node"] = "collector"
            routes.append(second)
            evidence = RUN.create_launch_evidence(
                root, "humanize-safe-smoke", "fixed-run", routes
            )
            paths = [root / item["receiptPath"] for item in evidence["activations"]]
            RUN.verify_launch_evidence(root, evidence, "balanced")
            return evidence, paths

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, paths = prepared(root)
            paths[0].unlink()
            with self.assertRaisesRegex(SAFE.MappingError, "regular file|receipt"):
                RUN.verify_launch_evidence(root, evidence, "balanced")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, paths = prepared(root)
            paths[0].write_bytes(paths[0].read_bytes() + b" ")
            with self.assertRaisesRegex(SAFE.MappingError, "replaced or modified"):
                RUN.verify_launch_evidence(root, evidence, "balanced")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence, paths = prepared(root)
            first, second = paths[0].read_bytes(), paths[1].read_bytes()
            paths[0].write_bytes(second)
            paths[1].write_bytes(first)
            with self.assertRaisesRegex(SAFE.MappingError, "replaced or modified"):
                RUN.verify_launch_evidence(root, evidence, "balanced")

    def test_main_binds_receipts_before_hmz_and_fails_on_during_run_tamper(self) -> None:
        for tamper in (False, True):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                input_path = root / "input.json"
                design_path = root / "design.json"
                artifact_path = root / "generated.py"
                models_path = root / "config/workflows/models.json"
                models_path.parent.mkdir(parents=True)
                input_path.write_text(json.dumps({"task": "bounded"}), encoding="utf-8")
                design_path.write_text(json.dumps(design()), encoding="utf-8")
                artifact_path.write_text("# admitted by mocked validator\n", encoding="utf-8")
                models_path.write_text(
                    json.dumps(verified_role_policy(), sort_keys=True), encoding="utf-8"
                )
                manifest = {
                    "inputPath": "input.json",
                    "designPath": "design.json",
                    "artifact": "generated.py",
                    "reasoningPosture": "balanced",
                    "nodeAgents": [
                        {"node": "worker", "role": "task", "workspaceAccess": "write"},
                    ],
                }
                saw_prelaunch_receipts = False

                def hmz_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                    nonlocal saw_prelaunch_receipts
                    payload = json.loads(argv[-1])
                    evidence = payload["humanizeAdapterLaunchEvidence"]
                    RUN.verify_launch_evidence(root, evidence, "balanced")
                    saw_prelaunch_receipts = True
                    if tamper:
                        receipt_path = root / evidence["activations"][0]["receiptPath"]
                        receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
                    result_path = root / payload["humanizeAdapterResultPath"]
                    result_path.write_text(
                        json.dumps({
                            "terminal": "COMPLETE",
                            "evidence": ["cycle.json"],
                        }),
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(argv, 0, "", "")

                stderr = io.StringIO()
                stdout = io.StringIO()
                with (
                    mock.patch.object(RUN, "validate_full_mapping", return_value=manifest),
                    mock.patch.object(
                        RUN,
                        "validate_hmz_binary",
                        return_value={"argvPrefix": ["/trusted/python", "-c", "launcher"]},
                    ),
                    mock.patch.object(
                        RUN,
                        "build_execution_context",
                        side_effect=lambda **kwargs: FakeExecutionContext(**kwargs),
                    ),
                    mock.patch.object(RUN.subprocess, "run", side_effect=hmz_run),
                    contextlib.redirect_stderr(stderr),
                    contextlib.redirect_stdout(stdout),
                ):
                    status = RUN.main([
                        str(root / "mapping.json"),
                        "--hmz",
                        str(root / "hmz"),
                        "--root",
                        str(root),
                    ])
                self.assertTrue(saw_prelaunch_receipts)
                if tamper:
                    self.assertEqual(2, status)
                    self.assertIn("replaced or modified", stderr.getvalue())
                else:
                    self.assertEqual(0, status, stderr.getvalue())
                    result = json.loads(stdout.getvalue())
                    self.assertEqual(
                        result["result"]["launchEvidenceSha256"],
                        result["terminalEvidence"]["launchEvidenceSha256"],
                    )
                    self.assertEqual(
                        result["result"]["adapterInputSha256"],
                        result["terminalEvidence"]["adapterInputSha256"],
                    )

    def test_run_cli_rejects_alternate_roles_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_mapping_project(root)
            alternate = root / "alternate-models.json"
            alternate.write_text(json.dumps(verified_role_policy()), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_mapping.py"),
                    str(manifest_path),
                    "--hmz",
                    str(write_fake_hmz(root / "humanize2", root / "hmz-invoked")),
                    "--root",
                    str(root),
                    "--roles",
                    str(alternate),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, completed.returncode)
            self.assertIn("unrecognized arguments: --roles", completed.stderr)

    def test_invalid_canonical_policy_is_rejected_before_hmz_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "input.json").write_text(
                json.dumps({"task": "bounded"}), encoding="utf-8"
            )
            (root / "design.json").write_text(json.dumps(design()), encoding="utf-8")
            models = root / "config/workflows/models.json"
            models.parent.mkdir(parents=True)
            policy = verified_role_policy()
            policy["catalogResolution"]["evidenceReceipt"][
                "verifiedRoleIdentitiesSha256"
            ] = "sha256:" + "f" * 64
            models.write_text(json.dumps(policy), encoding="utf-8")
            manifest = {
                "inputPath": "input.json",
                "designPath": "design.json",
                "artifact": "generated.py",
                "reasoningPosture": "balanced",
                "nodeAgents": [
                    {"node": "worker", "role": "task", "workspaceAccess": "write"},
                    {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
                ],
            }
            hmz_validator = mock.Mock(
                return_value={"argvPrefix": ["/trusted/python", "-c", "launcher"]}
            )
            with (
                mock.patch.object(RUN, "validate_full_mapping", return_value=manifest),
                mock.patch.object(RUN, "validate_hmz_binary", hmz_validator),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = RUN.main([
                    str(root / "mapping.json"),
                    "--hmz",
                    str(root / "hmz"),
                    "--root",
                    str(root),
                ])
            self.assertEqual(2, status)
            hmz_validator.assert_not_called()

    def test_review_decision_with_multiple_model_predecessors_fails_closed(self) -> None:
        document = design()
        document["nodes"].insert(
            2,
            {
                "id": "worker-two", "kind": "agent", "role": "research_collect",
                "session": "fresh", "workspaceAccess": "read", "timeoutSeconds": 300,
                "reads": ["task"], "writes": [],
            },
        )
        document["transitions"].insert(2, {"from": "worker-two", "to": "judge"})
        bindings = [
            {"node": node["id"], "role": node["role"], "workspaceAccess": node["workspaceAccess"]}
            for node in document["nodes"]
            if node["kind"] in {"agent", "decision"}
        ]
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(verified_role_policy()), encoding="utf-8")
            with self.assertRaisesRegex(SAFE.MappingError, "exactly one model predecessor"):
                RUN._validated_roles(
                    roles,
                    {"reasoningPosture": "balanced", "nodeAgents": bindings},
                    document,
                )

    def test_non_exact_writer_uses_provider_default_native_claude_verification(self) -> None:
        policy = verified_role_policy()
        manifest = {
            "reasoningPosture": "balanced",
            "nodeAgents": [
                {"node": "worker", "role": "research_collect", "workspaceAccess": "write"},
                {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
            ]
        }
        document = design()
        document["nodes"][1]["role"] = "research_collect"
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy), encoding="utf-8")
            routes = RUN._validated_roles(roles, manifest, document)
        advisor = next(route for route in routes if route["role"] == "advisor")
        self.assertEqual("claude", advisor["backend"])
        self.assertEqual("claude-opus-5", advisor["catalogModelId"])
        self.assertEqual("max", advisor["effort"])

    def test_non_exact_writer_rejects_non_default_decision_role(self) -> None:
        policy = verified_role_policy()
        manifest = {
            "reasoningPosture": "balanced",
            "nodeAgents": [
                {"node": "worker", "role": "research_collect", "workspaceAccess": "write"},
                {"node": "judge", "role": "anthropic_task", "workspaceAccess": "read"},
            ]
        }
        document = design()
        document["nodes"][1]["role"] = "research_collect"
        document["nodes"][2]["role"] = "anthropic_task"
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(
                SAFE.MappingError, "not qualified for taskClass 'verification'"
            ):
                RUN._validated_roles(roles, manifest, document)

    def test_role_effort_change_without_catalog_rebinding_fails_closed(self) -> None:
        policy = verified_role_policy()
        policy["roles"]["task"]["effort"] = "high"
        manifest = {
            "reasoningPosture": "balanced",
            "nodeAgents": [
                {"node": "worker", "role": "task", "workspaceAccess": "write"},
                {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(
                SAFE.MappingError, "does not bind the verified role identities"
            ):
                RUN._validated_roles(roles, manifest, design())

    def test_decision_role_must_be_verification_class_at_max(self) -> None:
        policy = verified_role_policy()
        policy["roles"]["advisor"]["intelligenceClass"] = "analysis"
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy), encoding="utf-8")
            manifest = {
                "reasoningPosture": "balanced",
                "nodeAgents": [
                    {"node": "worker", "role": "task", "workspaceAccess": "write"},
                    {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
                ]
            }
            with self.assertRaisesRegex(SAFE.MappingError, "class mapping is inconsistent"):
                RUN._validated_roles(roles, manifest, design())

    def test_decision_reviewer_max_floor_is_not_self_redefinable(self) -> None:
        policy = verified_role_policy()
        policy["roles"]["advisor"]["effort"] = "low"
        policy["capabilityPolicy"]["reviewPairings"][0]["reviewer"]["effort"] = "low"
        rebind_verified_role_identities(policy)
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy), encoding="utf-8")
            manifest = {
                "reasoningPosture": "balanced",
                "nodeAgents": [
                    {"node": "worker", "role": "task", "workspaceAccess": "write"},
                    {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
                ]
            }
            with self.assertRaisesRegex(SAFE.MappingError, "(?:Max|advisor)"):
                RUN._validated_roles(roles, manifest, design())

    def test_reserved_native_flow_names_are_rejected_before_mapping(self) -> None:
        for reserved in ("default", "slot"):
            document = design()
            document["id"] = reserved
            with self.subTest(reserved=reserved), self.assertRaisesRegex(
                SAFE.MappingError, "reserved"
            ):
                SAFE.generate_source(document, mapping_spec())

    def test_manifest_rejects_legacy_native_flow_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design_path = root / ".workflow/designs/humanize-safe-smoke.workflow.json"
            spec_path = root / ".humanize/specs/humanize-safe-smoke.json"
            artifact = root / ".humanize/flows/humanize-safe-smoke.py"
            for path in (design_path, spec_path, artifact):
                path.parent.mkdir(parents=True, exist_ok=True)
            design_path.write_text(SAFE.canonical_json(design()), encoding="utf-8")
            spec_path.write_text(SAFE.canonical_json(mapping_spec()), encoding="utf-8")
            artifact.write_text(SAFE.generate_source(design(), mapping_spec()), encoding="utf-8")
            write_role_policy(root)
            manifest = SAFE.build_manifest(root, design_path, spec_path, artifact)
            manifest_path = root / ".humanize/mappings/humanize-safe-smoke.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(SAFE.canonical_json(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SAFE.MappingError, "generated-flows namespace"):
                SAFE.validate_manifest(root, manifest_path)

    def test_hmz_validator_rejects_same_version_fake_console_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "humanize2"
            marker = source / "wrapper-ran"
            binary = write_fake_hmz(source, marker)
            responses = iter(
                [
                    subprocess.CompletedProcess([], 0, f"{source}\n", ""),
                    subprocess.CompletedProcess([], 0, f"{SAFE.UPSTREAM_REVISION}\n", ""),
                    subprocess.CompletedProcess([], 0, f"{SAFE.UPSTREAM_REPOSITORY}\n", ""),
                    subprocess.CompletedProcess([], 0, f"{SAFE.HMZ_VERSION}\n", ""),
                ]
            )
            with mock.patch.object(SAFE.subprocess, "run", side_effect=responses):
                with self.assertRaisesRegex(
                    SAFE.MappingError,
                    "provenance|interpreter|console|installed",
                ):
                    SAFE.validate_hmz_binary(binary)
            self.assertFalse(marker.exists(), "fake same-version wrapper was executed")

    def test_hmz_validator_accepts_current_uv_console_wrapper_shape(self) -> None:
        interpreter = Path("/trusted/python")
        modern = SAFE._uv_console_script(interpreter)
        self.assertIn(modern, SAFE._console_scripts(interpreter))
        self.assertNotIn(b"import re", modern)

    def test_hmz_validator_rejects_wrong_origin_even_with_right_head_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "humanize2"
            binary = source / ".venv/bin/hmz"
            binary.parent.mkdir(parents=True)
            interpreter = binary.parent / "python"
            interpreter.symlink_to(Path(sys.executable).resolve())
            binary.write_bytes(SAFE._console_script(interpreter))
            binary.chmod(0o755)
            responses = iter(
                [
                    subprocess.CompletedProcess([], 0, f"{source}\n", ""),
                    subprocess.CompletedProcess([], 0, f"{SAFE.UPSTREAM_REVISION}\n", ""),
                    subprocess.CompletedProcess([], 0, "https://example.invalid/fork.git\n", ""),
                    subprocess.CompletedProcess([], 0, "", ""),
                ]
            )
            with mock.patch.object(SAFE.subprocess, "run", side_effect=responses):
                with self.assertRaisesRegex(SAFE.MappingError, "repository"):
                    SAFE.validate_hmz_binary(binary)

    def test_hmz_source_git_ignores_caller_path_and_alternate_object_views(self) -> None:
        """Pinned-source Git must not honor PATH, GIT_*, or replacement objects."""

        def init_repo(path: Path, origin: str, message: str) -> None:
            subprocess.run(["git", "init", "-q", str(path)], check=True)
            subprocess.run(
                ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(path), "config", "user.name", "Adapter Test"],
                check=True,
            )
            (path / "README").write_text(f"{message}\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(path), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(path), "commit", "-qm", message], check=True)
            subprocess.run(
                ["git", "-C", str(path), "remote", "add", "origin", origin],
                check=True,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "humanize2"
            binary = source / ".venv/bin/hmz"
            binary.parent.mkdir(parents=True)
            interpreter = binary.parent / "python"
            interpreter.symlink_to(Path(sys.executable).resolve())
            binary.write_bytes(SAFE._console_script(interpreter))
            binary.chmod(0o755)
            init_repo(source, "https://example.invalid/fork.git", "unpinned checkout")

            decoy = root / "decoy"
            init_repo(decoy, SAFE.UPSTREAM_REPOSITORY, "alternate object view")
            (decoy / ".git" / "HEAD").write_text(
                f"{SAFE.UPSTREAM_REVISION}\n", encoding="utf-8"
            )

            shim_dir = root / "git-shim"
            shim_dir.mkdir()
            marker = shim_dir / "git-was-run"
            shim = shim_dir / "git"
            shim.write_text(
                "#!/usr/bin/env bash\n"
                f"printf 'ran' > '{marker}'\n"
                "args=\"$*\"\n"
                "if [[ \"$args\" == *'--show-toplevel'* ]]; then\n"
                f"  printf '%s\\n' '{source}'\n"
                "elif [[ \"$args\" == *'get-url'* ]]; then\n"
                f"  printf '%s\\n' '{SAFE.UPSTREAM_REPOSITORY}'\n"
                "elif [[ \"$args\" == *'rev-parse'* ]]; then\n"
                f"  printf '%s\\n' '{SAFE.UPSTREAM_REVISION}'\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            shim.chmod(0o555)

            injected = {
                "PATH": os.pathsep.join((str(shim_dir), os.environ.get("PATH", ""))),
                "GIT_DIR": str(decoy / ".git"),
                "GIT_WORK_TREE": str(source),
                "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(decoy / ".git" / "objects"),
                "GIT_REPLACE_REF_BASE": str(source / ".git" / "refs" / "replace"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "remote.origin.url",
                "GIT_CONFIG_VALUE_0": SAFE.UPSTREAM_REPOSITORY,
            }
            captured: list[dict[str, object]] = []
            real_run = SAFE.subprocess.run

            def wrapped(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                argv = args[0] if args else kwargs.get("args")
                if (
                    isinstance(argv, (list, tuple))
                    and argv
                    and Path(str(argv[0])).name == "git"
                ):
                    captured.append({"argv": [str(part) for part in argv], "env": kwargs.get("env")})
                return real_run(*args, **kwargs)

            with mock.patch.dict(os.environ, injected):
                with mock.patch.object(SAFE.subprocess, "run", side_effect=wrapped):
                    with self.assertRaisesRegex(
                        SAFE.MappingError, "audited revision and repository"
                    ):
                        SAFE.validate_hmz_binary(binary)

            self.assertFalse(marker.exists(), "caller PATH git interpreted the checkout")
            self.assertTrue(captured, "pinned-source validation must invoke Git")
            bound, digest = SAFE._bind_trusted_git()
            for call in captured:
                argv = call["argv"]
                env = call["env"]
                self.assertEqual(Path(str(argv[0])), bound)
                self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
                self.assertIn("--no-replace-objects", argv)
                self.assertIn(f"--git-dir={source / '.git'}", argv)
                self.assertIn(f"--work-tree={source}", argv)
                self.assertIsInstance(env, dict)
                for name in injected:
                    if name.startswith("GIT_"):
                        self.assertNotIn(name, env)
                self.assertEqual("1", env["GIT_NO_REPLACE_OBJECTS"])
                self.assertEqual("1", env["GIT_CONFIG_NOSYSTEM"])
                self.assertEqual(os.devnull, env["GIT_CONFIG_GLOBAL"])
                self.assertEqual(os.devnull, env["GIT_CONFIG_SYSTEM"])

    def test_pinned_source_git_fails_closed_on_executable_identity_drift(self) -> None:
        """Pinned Git identity is the absolute regular file plus SHA-256, rechecked after each call."""

        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory)
            stub = install / "git"
            original = b"#!/usr/bin/env bash\nexit 0\n"
            stub.write_bytes(original)
            stub.chmod(0o555)
            source = install / "repo"
            source.mkdir()
            (source / ".git").mkdir()

            real_run = SAFE.subprocess.run

            def wrapped(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                completed = real_run(*args, **kwargs)
                stub.chmod(0o755)
                stub.write_bytes(b"#!/usr/bin/env bash\nprintf 'swapped'\nexit 0\n")
                stub.chmod(0o555)
                return completed

            with mock.patch.object(SAFE, "_SYSTEM_GIT_DIRS", (install,)):
                bound, digest = SAFE._bind_trusted_git()
                self.assertEqual(stub.resolve(), bound)
                self.assertFalse(bound.is_symlink())
                self.assertTrue(stat.S_ISREG(bound.stat().st_mode))
                self.assertEqual(
                    digest, "sha256:" + hashlib.sha256(original).hexdigest()
                )
                with mock.patch.object(SAFE.subprocess, "run", side_effect=wrapped):
                    with self.assertRaisesRegex(
                        SAFE.MappingError, "trusted git executable drift"
                    ):
                        SAFE._run_pinned_source_git(
                            source, ("rev-parse", "--show-toplevel")
                        )

    def test_v3_persisted_catalog_snapshot_is_audit_only(self) -> None:
        document = verified_role_policy()
        document["catalogResolution"].update(
            status="bootstrap-unverified", verifiedRoles=[], verifiedBackends=[]
        )
        del document["catalogResolution"]["evidenceReceipt"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            manifest = {
                "reasoningPosture": "balanced",
                "nodeAgents": [
                    {"node": "worker", "role": "task", "workspaceAccess": "write"}
                ],
            }
            routes = RUN._validated_roles(path, manifest, design())
        self.assertEqual("gpt-5.6-sol", routes[0]["catalogModelId"])

    def test_v3_role_resolution_rejects_cursor_candidate_without_substitution(self) -> None:
        document = verified_role_policy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            manifest = {
                "reasoningPosture": "balanced",
                "nodeAgents": [
                    {"node": "worker", "role": "diversity_grok", "workspaceAccess": "read"}
                ],
            }
            with self.assertRaisesRegex(
                SAFE.MappingError, "does not admit semantic role"
            ):
                RUN._validated_roles(path, manifest, design())

    def test_v1_and_v2_role_documents_are_rejected_by_canonical_validator(self) -> None:
        for schema in ("workflow-model-roles/v1", "workflow-model-roles/v2"):
            document = verified_role_policy()
            document["schema"] = schema
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "models.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                manifest = {
                    "reasoningPosture": "balanced",
                    "nodeAgents": [
                        {"node": "worker", "role": "task", "workspaceAccess": "write"}
                    ]
                }
                with self.assertRaisesRegex(SAFE.MappingError, "unsupported role schema"):
                    RUN._validated_roles(path, manifest, design())

    def test_validate_cli_rejects_mapping_without_neutral_validator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_mapping_project(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_mapping.py"),
                    str(manifest_path),
                    "--root",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("neutral design validator", completed.stderr)

    def test_run_rejects_missing_validator_before_hmz_exec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_mapping_project(root)
            marker = root / "hmz-invoked"
            binary = write_fake_hmz(root / "humanize2", marker)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_mapping.py"),
                    str(manifest_path),
                    "--hmz",
                    str(binary),
                    "--root",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("neutral design validator", completed.stderr)
            self.assertFalse(marker.exists(), "hmz exec ran before full mapping validation")

    def test_run_rejects_neutral_invalid_design_before_hmz_exec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            neutral_invalid = design()
            neutral_invalid["admission"] = {
                "reasons": [],
                "demotionCondition": "Use one native subagent.",
            }
            manifest_path = write_mapping_project(
                root, document=neutral_invalid, include_validator=True
            )
            marker = root / "hmz-invoked"
            binary = write_fake_hmz(root / "humanize2", marker)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_mapping.py"),
                    str(manifest_path),
                    "--hmz",
                    str(binary),
                    "--root",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("neutral design validation failed", completed.stderr)
            self.assertIn("admission.reasons", completed.stderr)
            self.assertFalse(marker.exists(), "hmz exec ran after neutral admission failed")

    def test_validate_cli_rejects_malformed_validator_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = write_mapping_project(root)
            validator = root / ".claude/skills/build-workflow/scripts/validate_design.py"
            validator.parent.mkdir(parents=True, exist_ok=True)
            validator.write_text(
                'print("looks plausible but is not the validator contract")\n',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_mapping.py"),
                    str(manifest_path),
                    "--root",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("malformed result", completed.stderr)

    def test_smoke_integration_requires_configured_hmz(self) -> None:
        environment = os.environ.copy()
        environment.pop("HUMANIZE2_SMOKE_HMZ", None)
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "smoke_humanize.py"), "--integration"],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("HUMANIZE2_SMOKE_HMZ", completed.stderr)

    def test_smoke_integration_rejects_wrong_checkout_pin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "humanize2"
            marker = source / "hmz-invoked"
            binary = write_fake_hmz(source, marker)
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "test@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Adapter Test"],
                check=True,
            )
            (source / "README").write_text("wrong revision\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "wrong pin"], check=True)
            environment = os.environ.copy()
            environment["HUMANIZE2_SMOKE_HMZ"] = str(binary)
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "smoke_humanize.py"), "--integration"],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertRegex(completed.stderr, "audited revision|console script")
            self.assertFalse(marker.exists(), "wrong-pin hmz reached exec")

    def test_installer_requests_editable_source_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "install"
            commands: list[list[str]] = []

            def record(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                mock.patch.object(ENSURE.shutil, "which", return_value="/trusted/tool"),
                mock.patch.object(ENSURE.subprocess, "run", side_effect=record),
            ):
                ENSURE.install(root, "python3.12")
            self.assertIn(
                [str(root / ".venv/bin/python"), "-m", "pip", "install", "--editable", "."],
                commands,
            )

    def test_generates_deterministic_restricted_source(self) -> None:
        first = SAFE.generate_source(design(), mapping_spec())
        second = SAFE.generate_source(design(), mapping_spec())
        self.assertEqual(first, second)
        SAFE.audit_generated_ast(first)
        self.assertIn("TERMINAL: {terminal}", first)
        self.assertIn("from hmz.flows import Agent, flow", first)
        self.assertNotIn("from hmz.agents import AgentBase", first)
        self.assertIn("@flow\nasync def run", first)
        self.assertNotIn("subprocess", first)
        self.assertNotIn("runpy", first)

    def test_rejects_machine_gated_and_state_bound(self) -> None:
        document = design()
        document["governance"] = {"reviewPolicy": "machine-gated", "completionProof": "state-bound"}
        with self.assertRaisesRegex(SAFE.MappingError, "reviewPolicy independent"):
            SAFE.generate_source(document, mapping_spec())

    def test_rejects_unenforced_bounds_and_invalid_initial_types(self) -> None:
        no_deadline = design()
        del no_deadline["nodes"][1]["timeoutSeconds"]
        with self.assertRaisesRegex(SAFE.MappingError, "timeoutSeconds"):
            SAFE.generate_source(no_deadline, mapping_spec())

        wall_time = design()
        wall_time["budgets"]["maxWallTimeSeconds"] = 600
        with self.assertRaisesRegex(SAFE.MappingError, "maxWallTimeSeconds"):
            SAFE.generate_source(wall_time, mapping_spec())

        invalid_initial = mapping_spec()
        invalid_initial["initialState"]["answer"] = 7
        with self.assertRaisesRegex(SAFE.MappingError, "declared type"):
            SAFE.generate_source(design(), invalid_initial)

    def test_rejects_cycles_fanout_skills_and_persistent_sessions(self) -> None:
        cases: list[tuple[str, object]] = []
        cyclic = design()
        cyclic["transitions"] = [*cyclic["transitions"], {"from": "success", "to": "worker"}]
        cases.append(("terminal", cyclic))
        fanout = design()
        fanout["nodes"][1]["kind"] = "reducer"
        cases.append(("does not support", fanout))
        skilled = design()
        skilled["nodes"][1]["skills"] = {"required": ["grind"], "onDemand": []}
        cases.append(("skill loading", skilled))
        persistent = design()
        persistent["nodes"][1]["session"] = "persistent"
        cases.append(("session fresh", persistent))
        for expected, document in cases:
            with self.subTest(expected=expected), self.assertRaisesRegex(SAFE.MappingError, expected):
                SAFE.generate_source(document, mapping_spec())

    def test_ast_rejects_untrusted_import_and_calls(self) -> None:
        with self.assertRaisesRegex(SAFE.MappingError, "import"):
            SAFE.audit_generated_ast("import os\nos.system('id')\n")
        with self.assertRaisesRegex(SAFE.MappingError, "non-whitelisted call"):
            SAFE.audit_generated_ast("import json\neval('1')\n")

    def test_manifest_validation_rejects_source_tampering_without_import(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design_path = root / ".workflow/designs/humanize-safe-smoke.workflow.json"
            spec_path = root / ".humanize/specs/humanize-safe-smoke.json"
            artifact = root / ".humanize/generated-flows/humanize-safe-smoke.py"
            for path in (design_path, spec_path, artifact):
                path.parent.mkdir(parents=True, exist_ok=True)
            design_path.write_text(SAFE.canonical_json(design()))
            spec_path.write_text(SAFE.canonical_json(mapping_spec()))
            artifact.write_text(SAFE.generate_source(design(), mapping_spec()))
            write_role_policy(root)
            manifest = SAFE.build_manifest(root, design_path, spec_path, artifact)
            manifest_path = root / ".humanize/mappings/humanize-safe-smoke.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(SAFE.canonical_json(manifest))
            SAFE.validate_manifest(root, manifest_path)
            artifact.write_text(artifact.read_text() + "\nimport os\n")
            with self.assertRaisesRegex(SAFE.MappingError, "digest mismatch"):
                SAFE.validate_manifest(root, manifest_path)

    def test_manifest_rejects_malicious_source_even_with_recomputed_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design_path = root / ".workflow/designs/humanize-safe-smoke.workflow.json"
            spec_path = root / ".humanize/specs/humanize-safe-smoke.json"
            artifact = root / ".humanize/generated-flows/humanize-safe-smoke.py"
            for path in (design_path, spec_path, artifact):
                path.parent.mkdir(parents=True, exist_ok=True)
            design_path.write_text(SAFE.canonical_json(design()), encoding="utf-8")
            spec_path.write_text(SAFE.canonical_json(mapping_spec()), encoding="utf-8")
            artifact.write_text(SAFE.generate_source(design(), mapping_spec()), encoding="utf-8")
            write_role_policy(root)
            manifest = SAFE.build_manifest(root, design_path, spec_path, artifact)
            marker = root / "imported-marker"
            artifact.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            manifest["artifactSha256"] = SAFE.sha256_file(artifact)
            manifest_path = root / ".humanize/mappings/humanize-safe-smoke.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(SAFE.canonical_json(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SAFE.MappingError, "deterministic adapter generation"):
                SAFE.validate_manifest(root, manifest_path)
            self.assertFalse(marker.exists(), "validation imported or executed the flow")

    def test_committed_real_hmz_zero_agent_receipt_verifies(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "smoke_humanize.py"),
                "--verify-receipt",
                str(COMMITTED_SMOKE_RECEIPT),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema"], "humanize2-smoke-receipt/v3")
        self.assertTrue(result["integration"])
        self.assertEqual(result["modelCalls"], 0)
        self.assertEqual(result["terminal"], "COMPLETE")
        self.assertEqual(result["revision"], SAFE.UPSTREAM_REVISION)
        self.assertEqual(result["version"], "hmz 0.1.0")
        self.assertEqual(result["bundledDefault"]["result"]["versions"], [
            "approved-v1-start",
            "approved-v1-end",
            "approved-v2",
        ])
        self.assertEqual(result["bundledDefault"]["turns"], 0)
        self.assertEqual(result["bundledDefault"]["cycleCount"], 2)
        self.assertEqual(len(result["commitments"]["bundledDefaultSha256"]), 64)
        self.assertEqual(len(result["commitments"]["bundledSeedSlotSha256"]), 64)
        self.assertEqual(len(result["commitments"]["approvedSlotOneSha256"]), 64)
        self.assertEqual(len(result["commitments"]["approvedSlotTwoSha256"]), 64)
        self.assertEqual(len(result["commitments"]["bundledProbeSha256"]), 64)
        self.assertEqual(len(result["commitments"]["flowValidatorSha256"]), 64)

    def test_receipt_verifier_rejects_missing_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "smoke_humanize.py"),
                    "--verify-receipt",
                    str(Path(directory) / "missing.json"),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("receipt", completed.stderr)

    def test_receipt_verifier_rejects_tampered_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = json.loads(COMMITTED_SMOKE_RECEIPT.read_text(encoding="utf-8"))
            receipt["terminal"] = "REVISE"
            candidate = Path(directory) / "tampered.json"
            candidate.write_text(json.dumps(receipt), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "smoke_humanize.py"),
                    "--verify-receipt",
                    str(candidate),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("does not match", completed.stderr)

    def test_receipt_verifier_rejects_stale_contract_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            receipt = json.loads(COMMITTED_SMOKE_RECEIPT.read_text(encoding="utf-8"))
            receipt["commitments"]["contractSha256"] = "0" * 64
            candidate = Path(directory) / "stale.json"
            candidate.write_text(json.dumps(receipt), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "smoke_humanize.py"),
                    "--verify-receipt",
                    str(candidate),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("stale", completed.stderr)


class HumanizeReasoningPostureTests(CannedCatalogResolverTest):
    def _write_generated_mapping(
        self,
        root: Path,
        *,
        policy: dict[str, object] | None = None,
        document: dict[str, object] | None = None,
    ) -> tuple[Path, dict[str, object]]:
        design_document = document or design()
        spec_document = mapping_spec()
        design_path = root / ".workflow/designs/humanize-safe-smoke.workflow.json"
        spec_path = root / ".humanize/specs/humanize-safe-smoke.json"
        artifact = root / ".humanize/generated-flows/humanize-safe-smoke.py"
        for path in (design_path, spec_path, artifact):
            path.parent.mkdir(parents=True, exist_ok=True)
        design_path.write_text(SAFE.canonical_json(design_document), encoding="utf-8")
        spec_path.write_text(SAFE.canonical_json(spec_document), encoding="utf-8")
        artifact.write_text(
            SAFE.generate_source(design_document, spec_document), encoding="utf-8"
        )
        write_role_policy(root, policy)
        manifest = SAFE.build_manifest(root, design_path, spec_path, artifact)
        manifest_path = root / ".humanize/mappings/humanize-safe-smoke.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(SAFE.canonical_json(manifest), encoding="utf-8")
        return manifest_path, manifest

    def test_producer_emits_pm_compatible_bound_posture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _path, manifest = self._write_generated_mapping(Path(directory))
        self.assertEqual(set(manifest), set(SAFE.MAPPING_KEYS))
        self.assertEqual(manifest["reasoningPosture"], "balanced")
        self.assertEqual(
            list(SAFE.MAPPING_KEYS),
            [
                "schema",
                "adapterVersion",
                "upstream",
                "designPath",
                "designSha256",
                "specPath",
                "specSha256",
                "artifact",
                "artifactSha256",
                "inputPath",
                "reasoningPosture",
                "nodeAgents",
                "terminalProtocol",
            ],
        )

    def test_producer_derives_posture_from_canonical_policy_default(self) -> None:
        policy = verified_role_policy()
        policy["reasoningPolicy"]["defaultPosture"] = "economy"
        economy_design = design()
        economy_design["nodes"][1]["role"] = "research_collect"
        with tempfile.TemporaryDirectory() as directory:
            _path, manifest = self._write_generated_mapping(
                Path(directory), policy=policy, document=economy_design
            )
        self.assertEqual(manifest["reasoningPosture"], "economy")

    def test_spec_cannot_choose_a_posture_different_from_policy_default(self) -> None:
        policy = verified_role_policy()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = design()
            spec = mapping_spec()
            spec["reasoningPosture"] = "economy"
            design_path = root / ".workflow/designs/humanize-safe-smoke.workflow.json"
            spec_path = root / ".humanize/specs/humanize-safe-smoke.json"
            artifact = root / ".humanize/generated-flows/humanize-safe-smoke.py"
            for path in (design_path, spec_path, artifact):
                path.parent.mkdir(parents=True, exist_ok=True)
            design_path.write_text(SAFE.canonical_json(document), encoding="utf-8")
            spec_path.write_text(SAFE.canonical_json(spec), encoding="utf-8")
            artifact.write_text(
                SAFE.generate_source(document, spec), encoding="utf-8"
            )
            write_role_policy(root, policy)
            with self.assertRaisesRegex(
                SAFE.MappingError, "spec reasoningPosture.*defaultPosture"
            ):
                SAFE.build_manifest(root, design_path, spec_path, artifact)

    def test_validate_full_mapping_rejects_absent_unknown_and_invalid_posture(self) -> None:
        cases = {
            "missing": (
                lambda manifest: manifest.pop("reasoningPosture", None),
                r"missing required keys \['reasoningPosture'\]",
            ),
            "blank": (
                lambda manifest: manifest.update(reasoningPosture="   "),
                r"reasoningPosture must be a non-empty string",
            ),
            "non-string": (
                lambda manifest: manifest.update(reasoningPosture=None),
                r"reasoningPosture must be a non-empty string",
            ),
            "unknown": (
                lambda manifest: manifest.update(reasoningPosture="turbo"),
                r"not an admitted posture",
            ),
            "policy-mismatch": (
                lambda manifest: manifest.update(reasoningPosture="economy"),
                r"differs from canonical policy defaultPosture",
            ),
            "extra-key": (
                lambda manifest: manifest.update(unexpected=True),
                r"unsupported keys \['unexpected'\]",
            ),
        }
        for label, (mutate, pattern) in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest_path, manifest = self._write_generated_mapping(root)
                validator = root / ".claude/skills/build-workflow/scripts/validate_design.py"
                validator.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(
                    HERE.parents[1] / "build-workflow/scripts/validate_design.py",
                    validator,
                )
                mutate(manifest)
                manifest_path.write_text(SAFE.canonical_json(manifest), encoding="utf-8")
                with self.assertRaisesRegex(SAFE.MappingError, pattern):
                    SAFE.validate_full_mapping(root, manifest_path)

    def test_runtime_rejects_manifest_posture_that_differs_from_default(self) -> None:
        policy = verified_role_policy()
        self.assertEqual(policy["reasoningPolicy"]["defaultPosture"], "balanced")
        document = design()
        document["nodes"][1]["role"] = "research_collect"
        manifest = activation_manifest(
            [
                {"node": "worker", "role": "research_collect", "workspaceAccess": "write"},
                {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
            ],
            posture="economy",
        )
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(
                SAFE.MappingError, r"reasoningPosture.*defaultPosture"
            ):
                RUN._validated_roles(roles, manifest, document)

    def test_runtime_does_not_substitute_default_when_role_exceeds_bound_posture(self) -> None:
        policy = verified_role_policy()
        policy["reasoningPolicy"]["defaultPosture"] = "economy"
        self.assertIn("task", policy["reasoningPolicy"]["postures"]["balanced"]["allowedRoles"])
        self.assertNotIn("task", policy["reasoningPolicy"]["postures"]["economy"]["allowedRoles"])
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(SAFE.MappingError, r"reasoningPosture|exceeds reasoning posture"):
                RUN._validated_roles(roles, activation_manifest(posture="economy"), design())

    def test_runtime_fails_closed_when_policy_drops_the_bound_posture_allowance(self) -> None:
        policy = verified_role_policy()
        policy["reasoningPolicy"]["defaultPosture"] = "economy"
        policy["reasoningPolicy"]["postures"]["economy"]["allowedRoles"] = [
            role
            for role in policy["reasoningPolicy"]["postures"]["economy"]["allowedRoles"]
            if role != "research_collect"
        ]
        self.assertIn(
            "research_collect",
            policy["reasoningPolicy"]["postures"]["balanced"]["allowedRoles"],
        )
        document = design()
        document["nodes"][1]["role"] = "research_collect"
        manifest = activation_manifest(
            [
                {"node": "worker", "role": "research_collect", "workspaceAccess": "write"},
                {"node": "judge", "role": "advisor", "workspaceAccess": "read"},
            ],
            posture="economy",
        )
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(
                SAFE.MappingError,
                r"reasoningPosture|exceeds reasoning posture|not admitted by the current policy",
            ):
                RUN._validated_roles(roles, manifest, document)

    def test_runtime_rejects_missing_manifest_posture_instead_of_defaulting(self) -> None:
        policy = verified_role_policy()
        with tempfile.TemporaryDirectory() as directory:
            roles = Path(directory) / "models.json"
            roles.write_text(json.dumps(policy, sort_keys=True), encoding="utf-8")
            with self.assertRaisesRegex(
                SAFE.MappingError, r"reasoningPosture must be a non-empty string"
            ):
                RUN._validated_roles(
                    roles,
                    {"nodeAgents": activation_manifest()["nodeAgents"]},
                    design(),
                )


if __name__ == "__main__":
    unittest.main()
