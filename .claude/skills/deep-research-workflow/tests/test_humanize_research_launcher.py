from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = SKILL_ROOT / "scripts" / "run_humanize_research.py"
FLOW_ASSET_ROOT = SKILL_ROOT / "assets/project/.humanize/flows"
NATIVE_REFERENCE_TEST = SKILL_ROOT / "tests/test_humanize_native_references.py"
MODEL_TEMPLATE = (
    SKILL_ROOT.parent / "default-mode/assets/project/config/workflows/models.json"
)
CANONICAL_VALIDATOR_PATH = (
    SKILL_ROOT.parent
    / "update-platform-config/scripts/config-sync/validate_model_roles.py"
)


def load_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    parent = str(path.parent)
    inserted = parent not in sys.path
    if inserted:
        sys.path.insert(0, parent)
    try:
        specification.loader.exec_module(module)
    finally:
        if inserted:
            sys.path.remove(parent)
    return module


def load_launcher() -> Any:
    return load_module("run_humanize_research", LAUNCHER_PATH)


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


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
                            {
                                "slug": "gpt-6-astra",
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


def refresh_policy_receipts(document: dict[str, object]) -> dict[str, object]:
    roles = document["roles"]
    assert isinstance(roles, dict)
    active = sorted(
        name
        for name, identity in roles.items()
        if isinstance(identity, dict) and identity.get("dispatchStatus") == "active"
    )
    identity_fields = (
        "backend",
        "provider",
        "upstreamProviderFamily",
        "model",
        "modelFamily",
        "effort",
        "intelligenceClass",
        "dispatchStatus",
    )
    resolved = [
        {
            "semanticRole": name,
            **{field: roles[name].get(field) for field in identity_fields},
        }
        for name in active
    ]
    audited: dict[tuple[object, object], dict[str, object]] = {}
    for identity in roles.values():
        assert isinstance(identity, dict)
        key = (identity["backend"], identity["model"])
        binding = {
            field: identity[field]
            for field in (
                "backend",
                "provider",
                "upstreamProviderFamily",
                "model",
                "modelFamily",
            )
        }
        prior = audited.setdefault(key, binding)
        assert prior == binding
    document["auditedModelIdentities"] = {
        "schema": "workflow-model-identity-map/v1",
        "assurance": "static-family-binding-not-live-availability-or-authentication",
        "bindings": list(audited.values()),
    }
    resolution = document["catalogResolution"]
    assert isinstance(resolution, dict)
    backends = sorted({str(roles[name]["backend"]) for name in active})
    resolution.update(
        {
            "status": "verified",
            "verifiedBackends": backends,
            "verifiedRoles": active,
            "evidenceReceipt": {
                "schema": "workflow-model-catalog-evidence/v1",
                "assurance": (
                    "caller-supplied-structural-evidence-not-live-authentication"
                ),
                "backendCatalogSha256": {
                    backend: digest(f"{backend}-catalog".encode())
                    for backend in backends
                },
                "verifiedRoleIdentitiesSha256": digest(
                    json.dumps(
                        resolved,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ),
            },
        }
    )
    return document


def verified_policy() -> dict[str, object]:
    return refresh_policy_receipts(
        json.loads(MODEL_TEMPLATE.read_text(encoding="utf-8"))
    )


def provider_record(backend: str, account: str) -> bytes:
    return (
        json.dumps(
            {
                "cli": backend,
                "name": account,
                "way": "login",
                "env": {},
                "args": [],
                "made": "",
            },
            indent=2,
        )
        + "\n"
    ).encode()


FLOW_ROLES = {
    "deep-research": {
        "preflight": ("plan", "codex-main", "openai"),
        "collector": ("research_collect", "codex-main", "openai"),
        "counterevidence": ("research_collect", "codex-main", "openai"),
        "reducer": ("frontier_execute", "codex-main", "openai"),
        "reviewer": ("advisor", "claude-review", "anthropic"),
    },
    "auto-research": {
        "knowledge": ("frontier_execute", "codex-main", "openai"),
        "applicability": ("plan", "codex-main", "openai"),
        "experimenter": ("task", "codex-main", "openai"),
        "reviewer": ("advisor", "claude-review", "anthropic"),
    },
}


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    policy_path = root / "config/workflows/models.json"
    binding_path = root / "config/workflows/humanize-research-accounts.json"
    humanize_home = root / "humanize-home"
    policy_path.parent.mkdir(parents=True)
    policy = verified_policy()
    policy_path.write_text(json.dumps(policy, indent=2) + "\n")

    flows: dict[str, dict[str, object]] = {}
    records: dict[tuple[str, str], bytes] = {}
    for flow_name, bindings in FLOW_ROLES.items():
        flows[flow_name] = {}
        for flow_role, (semantic_role, account, upstream) in bindings.items():
            backend = str(policy["roles"][semantic_role]["backend"])
            record = records.setdefault(
                (backend, account), provider_record(backend, account)
            )
            flows[flow_name][flow_role] = {
                "providerAccount": account,
                "providerRecordSha256": digest(record),
                "upstreamProviderFamily": upstream,
            }
    binding_path.write_text(
        json.dumps(
            {
                "schema": "humanize-research-account-bindings/v1",
                "flows": flows,
            },
            indent=2,
        )
        + "\n"
    )
    for (backend, account), record in records.items():
        target = humanize_home / "providers" / backend / account / "provider.json"
        target.parent.mkdir(parents=True)
        target.write_bytes(record)
    return policy_path, binding_path, humanize_home


def install_reference_flows(root: Path, flow_name: str) -> Path:
    flow_directory = root / ".humanize/flows"
    flow_directory.mkdir(parents=True)
    for name in ("deep_research.py", "auto_research.py", "_research_reference.py"):
        shutil.copyfile(FLOW_ASSET_ROOT / name, flow_directory / name)
    return flow_directory


def write_pm_issue_snapshot(root: Path) -> Path:
    snapshot = {
        "iteration_mode_selected": "human_guided",
        "iteration_mode_effective": "human_guided",
        "human_checkin_lease_minutes": "60",
        "human_unavailable_fallback": "pause",
        "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
        "human_checkin_lease_status": "active",
        "iteration_mode_decision_at": "2030-01-01T00:00:00Z",
    }
    snapshot["iteration_mode_snapshot_sha256"] = digest(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    issue = root / ".pm/issues/m1/i1.md"
    issue.parent.mkdir(parents=True)
    issue.write_text(
        "---\n"
        + "\n".join(f"{key}: {value}" for key, value in snapshot.items())
        + "\n---\n\n## Acceptance Criteria\n\nBound context.\n",
        encoding="utf-8",
    )
    return issue


def downstream_test_identity(launcher: Any, root: Path, flow_name: str) -> dict[str, Any]:
    """Isolate post-resolution process/terminal tests from routing admission."""
    roles: list[dict[str, Any]] = []
    for flow_role, semantic_role, permission in launcher.FLOW_ROLE_POLICY[flow_name]:
        roles.append(
            {
                "flowRole": flow_role,
                "semanticRole": semantic_role,
                "backend": "codex",
                "model": "gpt-6-astra",
                "canonicalModel": "gpt-6-astra",
                "catalogModelId": "gpt-6-astra",
                "modelFamily": "gpt-6-astra",
                "effort": "max",
                "upstreamProviderFamily": (
                    "anthropic" if flow_role == "reviewer" else "openai"
                ),
                "intelligenceClass": "verification",
                "providerAccount": "test-account",
                "providerRecordSha256": digest(b"test-account"),
                "permission": permission,
                "launchReceipt": {
                    "schema": "workflow-native-launch-receipt/v1",
                    "receiptSha256": digest(b"test-receipt"),
                },
            }
        )
    policy_path = root / "config/workflows/models.json"
    bindings_path = root / "config/workflows/humanize-research-accounts.json"
    return {
        "schema": "humanize-research-resolved-identity/v1",
        "projectRoot": str(root.resolve()),
        "flow": flow_name,
        "rolePolicySha256": digest(
            policy_path.read_bytes() if policy_path.is_file() else b"test-policy"
        ),
        "accountBindingsSha256": digest(
            bindings_path.read_bytes() if bindings_path.is_file() else b"test-bindings"
        ),
        "roles": roles,
    }


class FakeExecutionContext:
    """Launcher-level double; execution_context owns the closure tests."""

    def __init__(self, *, pinned, sources, launcher_environment=None, source_environment=None, **_):
        self.pinned = pinned
        self.environment = dict(launcher_environment or {})
        self._fds: dict[str, int] = {}
        for label, payload in sources.items():
            descriptor = os.memfd_create(f"research-launcher-test-{label}")
            os.write(descriptor, payload)
            os.lseek(descriptor, 0, os.SEEK_SET)
            self._fds[label] = descriptor
        for name, label in (source_environment or {}).items():
            self.environment[name] = str(self._fds[label])
        self.pass_fds = tuple(self._fds.values())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        for descriptor in self._fds.values():
            os.close(descriptor)
        return None

    def argv_prefix(self, **_):
        return list(self.pinned["argvPrefix"])

    def source_path(self, label):
        return f"/proc/self/fd/{self._fds[label]}"

    def revalidate(self):
        return None


class HumanizeResearchLauncherContractTest(unittest.TestCase):
    def test_plain_python_entrypoint_runs_structural_security_tests(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-E", "-S", str(NATIVE_REFERENCE_TEST), "-v"],
            cwd=SKILL_ROOT.parents[2],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn(
            "test_reference_terminal_writer_refuses_a_linked_leaf",
            completed.stderr,
        )
        self.assertIn("skipped=2", completed.stderr)

    @classmethod
    def setUpClass(cls) -> None:
        cls.launcher = load_launcher()
        cls.canonical_validator = load_module(
            "canonical_model_role_validator_for_research_tests",
            CANONICAL_VALIDATOR_PATH,
        )

    def setUp(self) -> None:
        super().setUp()
        resolver = self.launcher.resolve_launch_spec

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
            self.launcher,
            "resolve_launch_spec",
            side_effect=resolve_with_canned_catalog,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        context_patcher = mock.patch.object(
            self.launcher,
            "build_execution_context",
            side_effect=lambda **kwargs: FakeExecutionContext(**kwargs),
        )
        context_patcher.start()
        self.addCleanup(context_patcher.stop)

    def test_help_does_not_offer_a_model_policy_override(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(LAUNCHER_PATH), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("--roles", completed.stdout)

    def test_invocation_rejects_a_model_policy_override(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            alternate = root / "alternate-models.json"
            alternate.write_text("{}\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(LAUNCHER_PATH),
                    "--root",
                    str(root),
                    "--hmz",
                    str(root / "hmz"),
                    "--flow",
                    "deep-research",
                    "--roles",
                    str(alternate),
                    "bounded task",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("unrecognized arguments: --roles", completed.stderr)

    def test_policy_validation_uses_the_complete_canonical_contract(self) -> None:
        document = verified_policy()
        reasoning = document["reasoningPolicy"]
        assert isinstance(reasoning, dict)
        reasoning["crossFamilyEffortComparable"] = True
        with self.assertRaisesRegex(
            self.canonical_validator.ValidationError, "cross-family effort"
        ):
            self.canonical_validator.validate(document)
        with self.assertRaisesRegex(self.launcher.LaunchError, "cross-family effort"):
            self.launcher._validate_policy(document)

    def test_advisor_uses_native_claude_after_fresh_concrete_resolution(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, bindings, humanize_home = write_fixture(root)
            with mock.patch.object(
                self.launcher,
                "resolve_launch_spec",
                wraps=self.launcher.resolve_launch_spec,
            ) as canonical_resolver:
                identity = self.launcher.resolve_launch_identity(
                    root,
                    "deep-research",
                    bindings,
                    humanize_home,
                )
        advisor = next(role for role in identity["roles"] if role["flowRole"] == "reviewer")
        self.assertEqual("claude", advisor["backend"])
        self.assertEqual("claude-opus-5", advisor["catalogModelId"])
        self.assertEqual(
            [
                (
                    call.kwargs["semantic_role"],
                    call.kwargs["task_class"],
                    call.kwargs["reasoning_posture"],
                    call.kwargs["review_mode"],
                    call.kwargs["writer_role"],
                )
                for call in canonical_resolver.call_args_list
            ],
            [
                ("plan", "planning", "balanced", "none", None),
                ("research_collect", "collection", "balanced", "none", None),
                ("research_collect", "collection", "balanced", "none", None),
                ("frontier_execute", "topIntelligence", "balanced", "none", None),
                (
                    "advisor",
                    "verification",
                    "balanced",
                    "exact-opposite",
                    "frontier_execute",
                ),
            ],
        )

    def test_resolver_receipt_rejects_unsupported_concrete_catalog_id(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, bindings, humanize_home = write_fixture(root)
            resolver = self.launcher.resolve_launch_spec

            def forged_receipt(*args: object, **kwargs: object) -> dict[str, object]:
                receipt = json.loads(json.dumps(resolver(*args, **kwargs)))
                if kwargs["semantic_role"] == "plan":
                    receipt["resolved"]["catalogModelId"] = "gpt-5.6-not-in-catalog"
                    unsigned = {
                        key: value
                        for key, value in receipt.items()
                        if key != "receiptSha256"
                    }
                    receipt["receiptSha256"] = self.launcher.canonical_sha256(unsigned)
                return receipt

            with mock.patch.object(
                self.launcher, "resolve_launch_spec", side_effect=forged_receipt
            ), self.assertRaisesRegex(
                self.launcher.LaunchError,
                "concrete catalog model is not covered by fresh evidence",
            ):
                self.launcher.resolve_launch_identity(
                    root, "deep-research", bindings, humanize_home
                )

    def test_resolver_receipt_drift_to_another_catalog_model_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path, bindings, humanize_home = write_fixture(root)
            policy = json.loads(policy_path.read_text())
            configured = policy["roles"]["plan"]["model"]
            replacement = (
                "gpt-5.6-sol"
                if configured == "gpt-5.6-luna"
                else "gpt-5.6-luna"
            )
            resolver = self.launcher.resolve_launch_spec

            def drifted_receipt(*args: object, **kwargs: object) -> dict[str, object]:
                receipt = json.loads(json.dumps(resolver(*args, **kwargs)))
                if kwargs["semantic_role"] == "plan":
                    receipt["resolved"]["catalogModelId"] = replacement
                    unsigned = {
                        key: value
                        for key, value in receipt.items()
                        if key != "receiptSha256"
                    }
                    receipt["receiptSha256"] = self.launcher.canonical_sha256(unsigned)
                return receipt

            with mock.patch.object(
                self.launcher, "resolve_launch_spec", side_effect=drifted_receipt
            ), self.assertRaisesRegex(
                self.launcher.LaunchError,
                "concrete catalog model is not covered by fresh evidence",
            ):
                self.launcher.resolve_launch_identity(
                    root, "deep-research", bindings, humanize_home
                )

    def test_different_aliases_do_not_fake_different_upstream_families(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path, bindings, humanize_home = write_fixture(root)
            document = json.loads(policy_path.read_text())
            document["roles"]["advisor"].update(
                {
                    "backend": "codex",
                    "provider": "codex",
                    "upstreamProviderFamily": "openai",
                    "model": "gpt-5.6-sol",
                    "modelFamily": "gpt-5.6-sol",
                }
            )
            document["catalogResolution"]["verifiedBackends"] = ["codex"]
            document["capabilityPolicy"]["reviewPairings"][0]["reviewer"].update(
                {
                    "upstreamProviderFamily": "openai",
                    "modelFamily": "gpt-5.6-sol",
                }
            )
            refresh_policy_receipts(document)
            policy_path.write_text(json.dumps(document, indent=2) + "\n")
            binding = json.loads(bindings.read_text())
            for flow_name in ("deep-research", "auto-research"):
                reviewer = binding["flows"][flow_name]["reviewer"]
                reviewer["providerAccount"] = "codex-review-alias"
                reviewer["upstreamProviderFamily"] = "openai"
                record = provider_record("codex", "codex-review-alias")
                reviewer["providerRecordSha256"] = digest(record)
                target = (
                    humanize_home
                    / "providers/codex/codex-review-alias/provider.json"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(record)
            bindings.write_text(json.dumps(binding, indent=2) + "\n")
            with self.assertRaisesRegex(
                self.launcher.LaunchError, "advisor must be native Claude"
            ):
                self.launcher.resolve_launch_identity(
                    root,
                    "deep-research",
                    bindings,
                    humanize_home,
                )

    def test_reviewer_effort_must_be_max_even_if_policy_pairing_is_weakened(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy_path, bindings, humanize_home = write_fixture(root)
            document = json.loads(policy_path.read_text())
            document["roles"]["advisor"]["effort"] = "high"
            document["capabilityPolicy"]["reviewPairings"][0]["reviewer"][
                "effort"
            ] = "high"
            document["capabilityPolicy"]["providerReviewerDefaults"]["openai"][
                "requiredEffort"
            ] = "high"
            refresh_policy_receipts(document)
            policy_path.write_text(json.dumps(document, indent=2) + "\n")
            with self.assertRaisesRegex(self.launcher.LaunchError, "(?:Max|advisor)"):
                self.launcher.resolve_launch_identity(
                    root,
                    "deep-research",
                    bindings,
                    humanize_home,
                )

    def test_provider_record_digest_and_backend_are_bound(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, bindings, humanize_home = write_fixture(root)
            record = humanize_home / "providers/codex/codex-main/provider.json"
            record.write_text(record.read_text().replace('"way": "login"', '"way": "gateway"'))
            with self.assertRaisesRegex(self.launcher.LaunchError, "provider record digest"):
                self.launcher.resolve_launch_identity(
                    root, "deep-research", bindings, humanize_home
                )

    def test_provider_account_name_cannot_escape_humanize_store(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, bindings, humanize_home = write_fixture(root)
            document = json.loads(bindings.read_text())
            document["flows"]["deep-research"]["preflight"][
                "providerAccount"
            ] = "../codex-main"
            bindings.write_text(json.dumps(document, indent=2) + "\n")
            with self.assertRaisesRegex(
                self.launcher.LaunchError, "invalid provider account name"
            ):
                self.launcher.resolve_launch_identity(
                    root, "deep-research", bindings, humanize_home
                )

    def test_config_normalization_is_strict_and_explicit(self) -> None:
        normalized = self.launcher.normalize_config(
            "auto-research",
            {
                "max_trials": 1,
                "max_model_calls": 5,
                "resources": {"authorized": True},
            },
        )
        self.assertEqual(normalized["max_trials"], 1)
        self.assertEqual(normalized["resources"]["integrity"], "VALID")
        self.assertFalse(normalized["resources"]["compute_available"])
        self.assertEqual(normalized["iteration_mode"], "autonomous")
        self.assertIsNone(normalized["human_feedback"])
        self.assertIsNone(normalized["human_feedback_checkpoint_sha256"])
        with self.assertRaisesRegex(self.launcher.LaunchError, "unknown config"):
            self.launcher.normalize_config("deep-research", {"effort": "low"})
        with self.assertRaisesRegex(self.launcher.LaunchError, "iteration_mode"):
            self.launcher.normalize_config(
                "deep-research", {"iteration_mode": "Fast"}
            )
        with self.assertRaisesRegex(self.launcher.LaunchError, "requires lease"):
            self.launcher.normalize_config(
                "deep-research", {"iteration_mode": "human_guided"}
            )
        with self.assertRaisesRegex(self.launcher.LaunchError, "cannot carry"):
            self.launcher.normalize_config(
                "deep-research", {"human_feedback": "stale feedback"}
            )
        with self.assertRaisesRegex(self.launcher.LaunchError, "checkpoint"):
            self.launcher.normalize_config(
                "deep-research",
                {
                    "iteration_mode": "human_guided",
                    "human_checkin_lease_minutes": 60,
                    "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                    "human_unavailable_fallback": "pause",
                    "human_feedback": "unbound feedback",
                },
            )
        with self.assertRaisesRegex(self.launcher.LaunchError, "feedback"):
            self.launcher.normalize_config(
                "deep-research",
                {
                    "iteration_mode": "human_guided",
                    "human_checkin_lease_minutes": 60,
                    "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                    "human_unavailable_fallback": "pause",
                    "human_feedback_checkpoint_artifact": (
                        ".humanize/artifacts/prior-terminal.json"
                    ),
                    "human_feedback_checkpoint_sha256": "sha256:" + "a" * 64,
                },
            )
        active = self.launcher.normalize_config(
            "deep-research",
            {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                "human_unavailable_fallback": "pause",
                "human_feedback": "inspect the raw result before choosing a task",
                "human_feedback_checkpoint_artifact": (
                    ".humanize/artifacts/prior-terminal.json"
                ),
                "human_feedback_checkpoint_sha256": "sha256:" + "a" * 64,
            },
        )
        self.assertEqual(active["iteration_mode"], "human_guided")
        with self.assertRaisesRegex(self.launcher.LaunchError, "lease expired"):
            self.launcher.normalize_config(
                "deep-research",
                {
                    "iteration_mode": "human_guided",
                    "human_checkin_lease_minutes": 60,
                    "human_checkin_lease_expires_at": "2000-01-01T00:00:00Z",
                    "human_unavailable_fallback": "pause",
                },
            )
        fallback = self.launcher.normalize_config(
            "deep-research",
            {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_checkin_lease_expires_at": "2000-01-01T00:00:00Z",
                "human_unavailable_fallback": "autonomous_review",
            },
        )
        self.assertEqual(fallback["human_unavailable_fallback"], "autonomous_review")

    def test_pm_issue_snapshot_is_the_only_iteration_source_for_managed_launches(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            issue = write_pm_issue_snapshot(root)
            setting, binding = self.launcher.resolve_iteration_context(
                root,
                "deep-research",
                {"branch_count": 1, "max_model_calls": 5},
                pm_issue=issue.relative_to(root),
            )
            self.assertEqual("human_guided", setting["iteration_mode"])
            self.assertEqual("pm_issue", binding["source"])
            self.assertEqual(
                "sha256:" + hashlib.sha256(issue.read_bytes()).hexdigest(),
                binding["pmIssueSha256"],
            )
            self.assertRegex(
                binding["iterationModeSnapshotSha256"], r"^sha256:[0-9a-f]{64}$"
            )
            self.assertFalse(binding["legacyDefault"])
            original_issue = issue.read_bytes()
            issue.write_bytes(original_issue + b"\n")
            with self.assertRaisesRegex(
                self.launcher.LaunchError, "PM issue changed during"
            ):
                self.launcher._revalidate_iteration_context(root, binding)
            issue.write_bytes(original_issue)
            with self.assertRaisesRegex(
                self.launcher.LaunchError, "PM issue.*iteration"
            ):
                self.launcher.resolve_iteration_context(
                    root,
                    "deep-research",
                    {
                        "iteration_mode": "autonomous",
                        "branch_count": 1,
                        "max_model_calls": 5,
                    },
                    pm_issue=issue.relative_to(root),
                )

            legacy = root / ".pm/issues/m1/legacy.md"
            legacy.write_text(
                "---\nid: legacy\nstatus: todo\n---\n\nLegacy issue.\n",
                encoding="utf-8",
            )
            legacy_setting, legacy_binding = self.launcher.resolve_iteration_context(
                root,
                "deep-research",
                {"branch_count": 1, "max_model_calls": 5},
                pm_issue=legacy.relative_to(root),
            )
            self.assertEqual("autonomous", legacy_setting["iteration_mode"])
            self.assertTrue(legacy_binding["legacyDefault"])

            raw = issue.read_text(encoding="utf-8")
            issue.write_text(
                raw.replace(
                    "iteration_mode_snapshot_sha256: sha256:",
                    "iteration_mode_snapshot_sha256: sha256:0",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(self.launcher.LaunchError, "snapshot"):
                self.launcher.resolve_iteration_context(
                    root,
                    "deep-research",
                    {"branch_count": 1, "max_model_calls": 5},
                    pm_issue=issue.relative_to(root),
                )

    def test_resumed_feedback_requires_the_exact_reviewed_checkpoint_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_path = root / ".humanize/artifacts/prior-terminal.json"
            artifact_path.parent.mkdir(parents=True)
            checkpoint_body = {
                "hypothesis": "candidate beats baseline",
                "actual_operation": "ran one frozen comparison",
                "raw_observation": {"delta": 1.5},
                "agent_interpretation": "the candidate improved the metric",
                "anomaly_or_alternate_interpretation": ["measurement noise"],
                "candidate_next_tasks": ["replicate with another seed"],
                "human_feedback": None,
            }
            iteration_context = {
                "schema": "humanize-research-iteration-context/v1",
                "source": "standalone",
                "pmIssuePath": None,
                "pmIssueSha256": None,
                "iterationModeSnapshotSha256": None,
                "legacyDefault": False,
            }
            task_sha256 = self.launcher._sha256(b"bound research task")
            previous = {
                "schema": "research-terminal/v1",
                "terminal": "HUMAN_OBSERVATION_REQUIRED",
                "domain_terminal": "HUMAN_OBSERVATION_REQUIRED",
                "next_planning_allowed": False,
                "launch": {
                    "taskSha256": task_sha256,
                    "iterationContext": iteration_context,
                },
                "terminal_review": {
                    "verdict": "COMPLETE",
                    "criteria": [
                        {
                            "criterion_id": "reduced-current-evidence",
                            "passed": True,
                            "evidence": "the reviewer checked the reduced artifact",
                        }
                    ],
                    "issues": [],
                    "human_dependency": None,
                },
                "human_observation_checkpoint": {
                    "checkpoint_id": self.launcher.canonical_sha256(checkpoint_body),
                    **checkpoint_body,
                    "questions_for_human": [
                        "What do you notice before selecting the next task?"
                    ],
                    "next_planning_allowed": False,
                },
            }
            raw = self.launcher._canonical_bytes(previous)
            artifact_path.write_bytes(raw)
            setting = self.launcher.normalize_config(
                "deep-research",
                {
                    "iteration_mode": "human_guided",
                    "human_checkin_lease_minutes": 60,
                    "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                    "human_unavailable_fallback": "pause",
                    "human_feedback": "replicate before expanding scope",
                    "human_feedback_checkpoint_artifact": (
                        ".humanize/artifacts/prior-terminal.json"
                    ),
                    "human_feedback_checkpoint_sha256": self.launcher._sha256(raw),
                    "terminal_artifact": ".humanize/artifacts/next-terminal.json",
                },
            )
            receipt = self.launcher._validate_human_feedback_checkpoint(
                root, setting, iteration_context, task_sha256
            )
            self.assertIsNotNone(receipt)
            assert receipt is not None
            self.assertEqual(previous["human_observation_checkpoint"]["checkpoint_id"], receipt["checkpoint_id"])
            self.assertEqual(setting["human_feedback"], receipt["human_feedback"])
            self.assertRegex(receipt["receipt_sha256"], r"^sha256:[0-9a-f]{64}$")

            pm_context = {
                "schema": "humanize-research-iteration-context/v1",
                "source": "pm_issue",
                "pmIssuePath": ".pm/issues/m1/i1.md",
                "pmIssueSha256": "sha256:" + "a" * 64,
                "iterationModeSnapshotSha256": "sha256:" + "b" * 64,
                "legacyDefault": False,
            }
            with self.assertRaisesRegex(
                self.launcher.LaunchError, "different iteration context"
            ):
                self.launcher._validate_human_feedback_checkpoint(
                    root, setting, pm_context, task_sha256
                )
            with self.assertRaisesRegex(self.launcher.LaunchError, "different task"):
                self.launcher._validate_human_feedback_checkpoint(
                    root,
                    setting,
                    iteration_context,
                    self.launcher._sha256(b"different task"),
                )

            artifact_path.write_bytes(raw + b" ")
            with self.assertRaisesRegex(self.launcher.LaunchError, "SHA-256"):
                self.launcher._validate_human_feedback_checkpoint(
                    root, setting, iteration_context, task_sha256
                )

    def test_main_revalidates_feedback_checkpoint_after_humanize_returns(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, _bindings, humanize_home = write_fixture(root)
            install_reference_flows(root, "deep-research")
            task = "bound resumed task"
            task_sha256 = self.launcher._sha256(task.encode("utf-8"))
            iteration_context = {
                "schema": "humanize-research-iteration-context/v1",
                "source": "standalone",
                "pmIssuePath": None,
                "pmIssueSha256": None,
                "iterationModeSnapshotSha256": None,
                "legacyDefault": False,
            }
            checkpoint_body = {
                "hypothesis": "candidate beats baseline",
                "actual_operation": "ran one frozen comparison",
                "raw_observation": {"delta": 1.5},
                "agent_interpretation": "the candidate improved the metric",
                "anomaly_or_alternate_interpretation": ["measurement noise"],
                "candidate_next_tasks": ["replicate with another seed"],
                "human_feedback": None,
            }
            prior = {
                "schema": "research-terminal/v1",
                "terminal": "HUMAN_OBSERVATION_REQUIRED",
                "domain_terminal": "HUMAN_OBSERVATION_REQUIRED",
                "next_planning_allowed": False,
                "launch": {
                    "taskSha256": task_sha256,
                    "iterationContext": iteration_context,
                },
                "terminal_review": {
                    "verdict": "COMPLETE",
                    "criteria": [
                        {
                            "criterion_id": "reduced-current-evidence",
                            "passed": True,
                            "evidence": "reviewed",
                        }
                    ],
                    "issues": [],
                    "human_dependency": None,
                },
                "human_observation_checkpoint": {
                    "checkpoint_id": self.launcher.canonical_sha256(checkpoint_body),
                    **checkpoint_body,
                    "questions_for_human": ["What do you notice?"],
                    "next_planning_allowed": False,
                },
            }
            prior_path = root / ".humanize/artifacts/prior-terminal.json"
            prior_path.parent.mkdir(parents=True)
            prior_raw = self.launcher._canonical_bytes(prior)
            prior_path.write_bytes(prior_raw)
            config = root / "research-config.json"
            config.write_text(
                json.dumps(
                    {
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                        "human_unavailable_fallback": "pause",
                        "human_feedback": "replicate before expanding scope",
                        "human_feedback_checkpoint_artifact": (
                            ".humanize/artifacts/prior-terminal.json"
                        ),
                        "human_feedback_checkpoint_sha256": self.launcher._sha256(
                            prior_raw
                        ),
                        "terminal_artifact": ".humanize/artifacts/next-terminal.json",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            def mutate_checkpoint(
                command: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess:
                prior_path.write_bytes(prior_raw + b" ")
                return subprocess.CompletedProcess(command, 1)

            fake_safe = type(
                "safe_mapping",
                (),
                {
                    "MappingError": ValueError,
                    "validate_hmz_binary": staticmethod(
                        lambda _path: {"argvPrefix": ["trusted-hmz"]}
                    ),
                },
            )
            old = sys.modules.get("safe_mapping")
            sys.modules["safe_mapping"] = fake_safe
            try:
                with mock.patch(
                    "subprocess.run", side_effect=mutate_checkpoint
                ), mock.patch.object(
                    self.launcher,
                    "resolve_launch_identity",
                    return_value=downstream_test_identity(
                        self.launcher, root, "deep-research"
                    ),
                ), mock.patch.dict(
                    os.environ, {"HUMANIZE_HOME": str(humanize_home)}, clear=False
                ):
                    result = self.launcher.main(
                        [
                            "--root",
                            str(root),
                            "--hmz",
                            str(root / "hmz"),
                            "--flow",
                            "deep-research",
                            "--config",
                            str(config),
                            task,
                        ]
                    )
            finally:
                if old is None:
                    sys.modules.pop("safe_mapping", None)
                else:
                    sys.modules["safe_mapping"] = old
            self.assertEqual(2, result)
            failure = json.loads(
                (root / ".humanize/artifacts/next-terminal.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("launch-validation-failure", failure["failure_category"])
            self.assertIn("SHA-256", failure["reason"])

    def test_iteration_terminal_binds_feedback_checkpoint_and_expiry_evidence(self) -> None:
        setting = self.launcher.normalize_config(
            "deep-research",
            {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                "human_unavailable_fallback": "pause",
                "human_feedback": "inspect the raw result before choosing a task",
                "human_feedback_checkpoint_artifact": (
                    ".humanize/artifacts/prior-terminal.json"
                ),
                "human_feedback_checkpoint_sha256": "sha256:" + "a" * 64,
            },
        )
        checkpoint_body = {
            "hypothesis": "candidate beats baseline",
            "actual_operation": "ran one frozen comparison",
            "raw_observation": {"delta": 1.5},
            "agent_interpretation": "the candidate improved the metric",
            "anomaly_or_alternate_interpretation": ["measurement noise"],
            "candidate_next_tasks": ["replicate with another seed"],
            "human_feedback": None,
        }
        checkpoint = {
            "checkpoint_id": self.launcher.canonical_sha256(checkpoint_body),
            **checkpoint_body,
            "questions_for_human": ["What do you notice in the raw observation?"],
            "next_planning_allowed": False,
        }
        artifact = {
            "domain_terminal": "HUMAN_OBSERVATION_REQUIRED",
            "research_outcome": "SUCCESS",
            "iteration_policy": {
                "selected_mode": "human_guided",
                "effective_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                "human_unavailable_fallback": "pause",
                "lease_status": "active",
                "fallback_applied": None,
            },
            "idea_review_disposition": "skipped_human_guided",
            "iteration_risk_triggers": [],
            "consumed_human_feedback": setting["human_feedback"],
            "consumed_human_feedback_checkpoint_artifact": setting[
                "human_feedback_checkpoint_artifact"
            ],
            "consumed_human_feedback_checkpoint_sha256": setting[
                "human_feedback_checkpoint_sha256"
            ],
            "human_observation_checkpoint": checkpoint,
            "next_planning_allowed": False,
        }
        self.launcher._validate_iteration_terminal(artifact, setting)
        checkpoint["agent_interpretation"] = "tampered"
        with self.assertRaisesRegex(self.launcher.LaunchError, "digest"):
            self.launcher._validate_iteration_terminal(artifact, setting)

        fallback_setting = self.launcher.normalize_config(
            "deep-research",
            {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_checkin_lease_expires_at": "2000-01-01T00:00:00Z",
                "human_unavailable_fallback": "autonomous_review",
            },
        )
        fallback_artifact = {
            "domain_terminal": "SUCCESS",
            "research_outcome": "SUCCESS",
            "iteration_policy": {
                "selected_mode": "human_guided",
                "effective_mode": "autonomous",
                "human_checkin_lease_minutes": 60,
                "human_checkin_lease_expires_at": "2000-01-01T00:00:00Z",
                "human_unavailable_fallback": "autonomous_review",
                "lease_status": "expired",
                "fallback_applied": "autonomous_review",
            },
            "idea_review_disposition": "performed",
            "iteration_risk_triggers": ["lease_expired"],
            "consumed_human_feedback": None,
            "consumed_human_feedback_checkpoint_artifact": None,
            "consumed_human_feedback_checkpoint_sha256": None,
            "human_observation_checkpoint": None,
            "next_planning_allowed": True,
        }
        self.launcher._validate_iteration_terminal(
            fallback_artifact, fallback_setting
        )
        fallback_artifact["iteration_risk_triggers"] = []
        with self.assertRaisesRegex(self.launcher.LaunchError, "fallback evidence"):
            self.launcher._validate_iteration_terminal(
                fallback_artifact, fallback_setting
            )

    def test_sealed_receipt_cannot_be_replaced_by_plain_config_metadata(self) -> None:
        receipt = {
            "schema": "humanize-research-launch/v1",
            "runId": "test-run",
            "flow": "deep-research",
            "projectRoot": "/tmp/project",
            "taskSha256": digest(b"task"),
            "config": self.launcher.normalize_config("deep-research", {}),
            "rolePolicySha256": digest(b"policy"),
            "accountBindingsSha256": digest(b"bindings"),
            "referenceSources": {
                "schema": "humanize-research-reference-binding/v1",
                "approval": "launcher-owned-approved-sha256",
                "selectedFlow": "deep-research",
                "files": {
                    name: digest((FLOW_ASSET_ROOT / name).read_bytes())
                    for name in (
                        "_research_reference.py",
                        "auto_research.py",
                        "deep_research.py",
                    )
                },
            },
            "roles": [],
        }
        descriptor, receipt_digest = self.launcher.seal_launch_receipt(receipt)
        try:
            self.assertGreater(descriptor, 2)
            self.assertRegex(receipt_digest, r"^sha256:[0-9a-f]{64}$")
            self.assertTrue(self.launcher.is_sealed_launch_descriptor(descriptor))
        finally:
            os.close(descriptor)

    def test_build_command_pins_every_account_model_effort_and_permission(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            resolved = downstream_test_identity(
                self.launcher, root, "auto-research"
            )
        command = self.launcher.build_command(
            ["trusted-python", "-c", "trusted-hmz"],
            Path(".humanize/flows/auto_research.py"),
            Path(".humanize/.auto-research.test-config.json"),
            resolved["roles"],
            "task",
        )
        config_flag = max(index for index, value in enumerate(command) if value == "-c")
        self.assertEqual(
            command[config_flag + 1], ".humanize/.auto-research.test-config.json"
        )
        specifications = [
            command[index + 1]
            for index, value in enumerate(command[:-1])
            if value == "-a"
        ]
        self.assertEqual(len(specifications), 4)
        for role, specification in zip(
            resolved["roles"], specifications, strict=True
        ):
            self.assertIn(f"model={role['model']}", specification)
            self.assertIn(f"effort={role['effort']}", specification)
            self.assertIn(f"provider={role['providerAccount']}", specification)
            self.assertIn(f"permission={role['permission']}", specification)

    def test_process_failure_atomically_writes_unresolved_terminal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy, bindings, humanize_home = write_fixture(root)
            install_reference_flows(root, "deep-research")
            with mock.patch.object(
                self.launcher,
                "resolve_launch_identity",
                return_value=downstream_test_identity(
                    self.launcher, root, "deep-research"
                ),
            ), mock.patch.object(
                self.launcher,
                "seal_launch_receipt",
                wraps=self.launcher.seal_launch_receipt,
            ), mock.patch(
                "subprocess.run", return_value=subprocess.CompletedProcess([], 19)
            ), mock.patch.dict(
                os.environ, {"HUMANIZE_HOME": str(humanize_home)}, clear=False
            ):
                scripts = SKILL_ROOT.parents[0] / "humanize-workflow/scripts"
                sys.path.insert(0, str(scripts))
                fake_safe = type(
                    "safe_mapping",
                    (),
                    {
                        "MappingError": ValueError,
                        "validate_hmz_binary": staticmethod(
                            lambda _path: {"argvPrefix": ["trusted-hmz"]}
                        ),
                    },
                )
                prior = sys.modules.get("safe_mapping")
                sys.modules["safe_mapping"] = fake_safe
                try:
                    result = self.launcher.main(
                        [
                            "--root",
                            str(root),
                            "--hmz",
                            str(root / "hmz"),
                            "--flow",
                            "deep-research",
                            "task",
                        ]
                    )
                finally:
                    sys.path.remove(str(scripts))
                    if prior is None:
                        sys.modules.pop("safe_mapping", None)
                    else:
                        sys.modules["safe_mapping"] = prior
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
        self.assertEqual(result, 19)
        self.assertEqual(artifact["terminal"], "UNRESOLVED_INFRASTRUCTURE")
        self.assertEqual(artifact["failure_category"], "humanize-process-failure")
        self.assertIsNone(artifact["human_dependency"])

    def test_modified_reference_source_is_rejected_before_humanize_dispatch(self) -> None:
        cases = (
            ("deep-research", "deep_research.py"),
            ("auto-research", "auto_research.py"),
            ("deep-research", "_research_reference.py"),
        )
        for flow_name, changed_name in cases:
            with self.subTest(flow=flow_name, changed=changed_name), TemporaryDirectory() as directory:
                root = Path(directory)
                _policy, _bindings, humanize_home = write_fixture(root)
                flow_directory = install_reference_flows(root, flow_name)
                changed = flow_directory / changed_name
                changed.write_bytes(changed.read_bytes() + b"\n# replaced after materialization\n")
                fake_safe = type(
                    "safe_mapping",
                    (),
                    {
                        "MappingError": ValueError,
                        "validate_hmz_binary": staticmethod(
                            lambda _path: {"argvPrefix": ["trusted-hmz"]}
                        ),
                    },
                )
                prior = sys.modules.get("safe_mapping")
                sys.modules["safe_mapping"] = fake_safe
                dispatch = mock.Mock(
                    return_value=subprocess.CompletedProcess([], 19)
                )
                try:
                    with mock.patch("subprocess.run", dispatch), mock.patch.dict(
                        os.environ,
                        {"HUMANIZE_HOME": str(humanize_home)},
                        clear=False,
                    ):
                        result = self.launcher.main(
                            [
                                "--root",
                                str(root),
                                "--hmz",
                                str(root / "hmz"),
                                "--flow",
                                flow_name,
                                "task",
                            ]
                        )
                finally:
                    if prior is None:
                        sys.modules.pop("safe_mapping", None)
                    else:
                        sys.modules["safe_mapping"] = prior
                self.assertEqual(result, 2)
                dispatch.assert_not_called()
                self.assertFalse(
                    (root / ".humanize/artifacts/deep-research-terminal.json").exists()
                )

    def test_terminal_artifact_rejects_linked_leaf_and_parent_without_overwrite(self) -> None:
        for linked_parent in (False, True):
            with self.subTest(linked_parent=linked_parent), TemporaryDirectory() as directory:
                root = Path(directory)
                _policy, _bindings, humanize_home = write_fixture(root)
                install_reference_flows(root, "deep-research")
                victim_directory = root / "real-artifacts"
                victim_directory.mkdir()
                victim = victim_directory / "terminal.json"
                victim.write_text("ORIGINAL\n", encoding="utf-8")
                if linked_parent:
                    (root / "linked-artifacts").symlink_to(
                        victim_directory, target_is_directory=True
                    )
                    terminal_artifact = "linked-artifacts/terminal.json"
                else:
                    terminal = root / "terminal.json"
                    terminal.symlink_to(victim)
                    terminal_artifact = "terminal.json"
                requested = root / "research-config.json"
                requested.write_text(
                    json.dumps(
                        {
                            "branch_count": 1,
                            "max_model_calls": 5,
                            "terminal_artifact": terminal_artifact,
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                fake_safe = type(
                    "safe_mapping",
                    (),
                    {
                        "MappingError": ValueError,
                        "validate_hmz_binary": staticmethod(
                            lambda _path: {"argvPrefix": ["trusted-hmz"]}
                        ),
                    },
                )
                prior = sys.modules.get("safe_mapping")
                sys.modules["safe_mapping"] = fake_safe
                dispatch = mock.Mock(
                    return_value=subprocess.CompletedProcess([], 19)
                )
                try:
                    with mock.patch("subprocess.run", dispatch), mock.patch.dict(
                        os.environ,
                        {"HUMANIZE_HOME": str(humanize_home)},
                        clear=False,
                    ):
                        result = self.launcher.main(
                            [
                                "--root",
                                str(root),
                                "--hmz",
                                str(root / "hmz"),
                                "--flow",
                                "deep-research",
                                "--config",
                                str(requested),
                                "task",
                            ]
                        )
                finally:
                    if prior is None:
                        sys.modules.pop("safe_mapping", None)
                    else:
                        sys.modules["safe_mapping"] = prior
                self.assertEqual(result, 2)
                dispatch.assert_not_called()
                self.assertEqual(victim.read_text(encoding="utf-8"), "ORIGINAL\n")

    def test_atomic_terminal_revalidates_leaf_before_replace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            victim = root / "victim.json"
            victim.write_text("ORIGINAL\n", encoding="utf-8")
            terminal = root / "terminal.json"
            setting = self.launcher.normalize_config(
                "deep-research", {"terminal_artifact": "terminal.json"}
            )
            compare_directories = self.launcher._same_open_directory
            swapped = False

            def swap_before_leaf_revalidation(left_fd: int, right_fd: int) -> bool:
                nonlocal swapped
                if not swapped:
                    terminal.symlink_to(victim)
                    swapped = True
                return compare_directories(left_fd, right_fd)

            with mock.patch.object(
                self.launcher,
                "_same_open_directory",
                side_effect=swap_before_leaf_revalidation,
            ), self.assertRaisesRegex(self.launcher.LaunchError, "symlink"):
                self.launcher._atomic_terminal(root, setting, {"replacement": True})
            self.assertTrue(swapped)
            self.assertTrue(terminal.is_symlink())
            self.assertEqual(victim.read_text(encoding="utf-8"), "ORIGINAL\n")

    def test_launch_validation_failure_writes_unresolved_terminal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, _bindings, _humanize_home = write_fixture(root)
            install_reference_flows(root, "deep-research")
            with mock.patch.object(
                self.launcher,
                "resolve_launch_identity",
                return_value=downstream_test_identity(
                    self.launcher, root, "deep-research"
                ),
            ):
                result = self.launcher.main(
                    [
                        "--root",
                        str(root),
                        "--hmz",
                        str(root / "missing-hmz"),
                        "--flow",
                        "deep-research",
                        "task",
                    ]
                )
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
        self.assertEqual(result, 2)
        self.assertEqual(artifact["terminal"], "UNRESOLVED_INFRASTRUCTURE")
        self.assertEqual(artifact["failure_category"], "launch-validation-failure")
        self.assertIsNone(artifact["human_dependency"])
        self.assertIsNone(artifact["terminal_review"])

    def test_success_without_terminal_is_converted_to_unresolved(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, _bindings, humanize_home = write_fixture(root)
            install_reference_flows(root, "deep-research")
            fake_safe = type(
                "safe_mapping",
                (),
                {
                    "MappingError": ValueError,
                    "validate_hmz_binary": staticmethod(
                        lambda _path: {"argvPrefix": ["trusted-hmz"]}
                    ),
                },
            )
            prior = sys.modules.get("safe_mapping")
            sys.modules["safe_mapping"] = fake_safe
            try:
                with mock.patch(
                    "subprocess.run", return_value=subprocess.CompletedProcess([], 0)
                ), mock.patch.object(
                    self.launcher,
                    "resolve_launch_identity",
                    return_value=downstream_test_identity(
                        self.launcher, root, "deep-research"
                    ),
                ), mock.patch.dict(
                    os.environ, {"HUMANIZE_HOME": str(humanize_home)}, clear=False
                ):
                    result = self.launcher.main(
                        [
                            "--root",
                            str(root),
                            "--hmz",
                            str(root / "hmz"),
                            "--flow",
                            "deep-research",
                            "task",
                        ]
                    )
            finally:
                if prior is None:
                    sys.modules.pop("safe_mapping", None)
                else:
                    sys.modules["safe_mapping"] = prior
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
        self.assertEqual(result, 2)
        self.assertEqual(artifact["terminal"], "UNRESOLVED_INFRASTRUCTURE")
        self.assertEqual(artifact["failure_category"], "missing-terminal-artifact")

    def test_stale_terminal_is_replaced_by_current_unresolved_artifact(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, _bindings, humanize_home = write_fixture(root)
            install_reference_flows(root, "deep-research")
            terminal = root / ".humanize/artifacts/deep-research-terminal.json"
            terminal.parent.mkdir(parents=True)
            terminal.write_text(
                json.dumps(
                    {
                        "schema": "research-terminal/v1",
                        "flow": "deep-research",
                        "terminal": "SUCCESS",
                        "domain_terminal": "SUCCESS",
                        "launch": {
                            "runId": "prior-run",
                            "receiptSha256": digest(b"prior-receipt"),
                        },
                    }
                )
                + "\n"
            )
            fake_safe = type(
                "safe_mapping",
                (),
                {
                    "MappingError": ValueError,
                    "validate_hmz_binary": staticmethod(
                        lambda _path: {"argvPrefix": ["trusted-hmz"]}
                    ),
                },
            )
            prior = sys.modules.get("safe_mapping")
            sys.modules["safe_mapping"] = fake_safe
            try:
                with mock.patch(
                    "subprocess.run", return_value=subprocess.CompletedProcess([], 0)
                ), mock.patch.object(
                    self.launcher,
                    "resolve_launch_identity",
                    return_value=downstream_test_identity(
                        self.launcher, root, "deep-research"
                    ),
                ), mock.patch.dict(
                    os.environ, {"HUMANIZE_HOME": str(humanize_home)}, clear=False
                ):
                    result = self.launcher.main(
                        [
                            "--root",
                            str(root),
                            "--hmz",
                            str(root / "hmz"),
                            "--flow",
                            "deep-research",
                            "task",
                        ]
                    )
            finally:
                if prior is None:
                    sys.modules.pop("safe_mapping", None)
                else:
                    sys.modules["safe_mapping"] = prior
            artifact = json.loads(terminal.read_text())
        self.assertEqual(result, 2)
        self.assertEqual(artifact["terminal"], "UNRESOLVED_INFRASTRUCTURE")
        self.assertEqual(artifact["failure_category"], "invalid-terminal-artifact")
        self.assertNotEqual(artifact["launch"]["runId"], "prior-run")

    def test_main_passes_normalized_config_and_accepts_only_current_terminal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _policy, _bindings, humanize_home = write_fixture(root)
            install_reference_flows(root, "deep-research")
            requested = root / "research-config.json"
            requested.write_text(
                json.dumps(
                    {
                        "branch_count": 1,
                        "max_model_calls": 5,
                        "terminal_artifact": ".humanize/artifacts/custom-terminal.json",
                    }
                )
                + "\n"
            )
            observed: dict[str, object] = {}

            def completed(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
                flow_flag = command.index("-f")
                flow_descriptor = int(str(command[flow_flag + 1]).rsplit("/", 1)[1])
                observed["flow"] = os.pread(
                    flow_descriptor, os.fstat(flow_descriptor).st_size, 0
                )
                config_flag = max(
                    index for index, value in enumerate(command) if value == "-c"
                )
                config_descriptor = int(str(command[config_flag + 1]).rsplit("/", 1)[1])
                observed["config"] = json.loads(
                    os.pread(
                        config_descriptor, os.fstat(config_descriptor).st_size, 0
                    )
                )
                environment = kwargs["env"]
                assert isinstance(environment, dict)
                receipt_fd = kwargs["pass_fds"]
                assert isinstance(receipt_fd, tuple) and len(receipt_fd) == 4
                descriptor = int(environment["HUMANIZE_RESEARCH_LAUNCH_FD"])
                receipt = json.loads(
                    os.pread(descriptor, os.fstat(descriptor).st_size, 0)
                )
                observed["referenceSources"] = receipt["referenceSources"]
                observed["iterationContext"] = receipt["iterationContext"]
                observed["humanFeedbackReceipt"] = receipt["humanFeedbackReceipt"]
                normalized = observed["config"]
                assert isinstance(normalized, dict)
                target = root / normalized["terminal_artifact"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(
                        {
                            "schema": "research-terminal/v1",
                            "flow": "deep-research",
                            "terminal": "SUCCESS",
                            "domain_terminal": "SUCCESS",
                            "research_outcome": "SUCCESS",
                            "iteration_policy": {
                                "selected_mode": "autonomous",
                                "effective_mode": "autonomous",
                                "human_checkin_lease_minutes": None,
                                "human_checkin_lease_expires_at": None,
                                "human_unavailable_fallback": "pause",
                                "lease_status": "not_applicable",
                                "fallback_applied": None,
                            },
                            "idea_review_disposition": "performed",
                            "iteration_risk_triggers": [],
                            "consumed_human_feedback": None,
                            "consumed_human_feedback_checkpoint_artifact": None,
                            "consumed_human_feedback_checkpoint_sha256": None,
                            "human_observation_checkpoint": None,
                            "next_planning_allowed": True,
                            "terminal_review": {
                                "verdict": "COMPLETE",
                                "criteria": [
                                    {
                                        "criterion_id": "current-artifact",
                                        "passed": True,
                                        "evidence": "checked",
                                    }
                                ],
                                "issues": [],
                                "human_dependency": None,
                            },
                            "launch": {
                                "runId": receipt["runId"],
                                "receiptSha256": environment[
                                    "HUMANIZE_RESEARCH_LAUNCH_SHA256"
                                ],
                            },
                        }
                    )
                    + "\n"
                )
                return subprocess.CompletedProcess(command, 0)

            fake_safe = type(
                "safe_mapping",
                (),
                {
                    "MappingError": ValueError,
                    "validate_hmz_binary": staticmethod(
                        lambda _path: {"argvPrefix": ["trusted-hmz"]}
                    ),
                },
            )
            prior = sys.modules.get("safe_mapping")
            sys.modules["safe_mapping"] = fake_safe
            try:
                with mock.patch("subprocess.run", side_effect=completed), mock.patch.object(
                    self.launcher,
                    "resolve_launch_identity",
                    return_value=downstream_test_identity(
                        self.launcher, root, "deep-research"
                    ),
                ), mock.patch.dict(
                    os.environ, {"HUMANIZE_HOME": str(humanize_home)}, clear=False
                ):
                    result = self.launcher.main(
                        [
                            "--root",
                            str(root),
                            "--hmz",
                            str(root / "hmz"),
                            "--flow",
                            "deep-research",
                            "--config",
                            str(requested),
                            "task",
                        ]
                    )
            finally:
                if prior is None:
                    sys.modules.pop("safe_mapping", None)
                else:
                    sys.modules["safe_mapping"] = prior
        self.assertEqual(result, 0)
        self.assertEqual(observed["config"], self.launcher.normalize_config(
            "deep-research",
            {
                "branch_count": 1,
                "max_model_calls": 5,
                "terminal_artifact": ".humanize/artifacts/custom-terminal.json",
            },
        ))
        self.assertEqual(
            observed["flow"], (FLOW_ASSET_ROOT / "deep_research.py").read_bytes()
        )
        self.assertEqual("standalone", observed["iterationContext"]["source"])
        self.assertFalse(observed["iterationContext"]["legacyDefault"])
        self.assertIsNone(observed["humanFeedbackReceipt"])
        reference_sources = observed["referenceSources"]
        assert isinstance(reference_sources, dict)
        self.assertEqual(
            reference_sources["files"],
            {
                name: digest((FLOW_ASSET_ROOT / name).read_bytes())
                for name in (
                    "_research_reference.py",
                    "auto_research.py",
                    "deep_research.py",
                )
            },
        )


if __name__ == "__main__":
    unittest.main()
