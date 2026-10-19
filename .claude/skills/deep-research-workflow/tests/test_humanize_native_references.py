from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import types
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterator
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
FLOW_ROOT = SKILL_ROOT / "assets" / "project" / ".humanize" / "flows"
LAUNCHER_PATH = SKILL_ROOT / "scripts" / "run_humanize_research.py"
HELPER_PATH = FLOW_ROOT / "_research_reference.py"


def load_launcher() -> Any:
    specification = importlib.util.spec_from_file_location(
        "native_research_launcher_for_flow_tests", LAUNCHER_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


LAUNCHER = load_launcher()


def load_reference_helper() -> Any:
    specification = importlib.util.spec_from_file_location(
        "research_reference_helper_for_structural_tests", HELPER_PATH
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    prior_pydantic = sys.modules.get("pydantic")
    replaced_pydantic = False
    prior_bytecode = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        try:
            import pydantic as available_pydantic
        except ModuleNotFoundError:
            available_pydantic = None
        if available_pydantic is None or not all(
            hasattr(available_pydantic, name)
            for name in ("BaseModel", "ConfigDict", "Field", "model_validator")
        ):
            stub = types.ModuleType("pydantic")

            class BaseModel:
                pass

            missing = object()

            def field(
                default: object = missing,
                *,
                default_factory: Any | None = None,
                **_kwargs: object,
            ) -> object:
                if default is not missing:
                    return default
                return default_factory() if default_factory is not None else None

            def model_validator(**_kwargs: object) -> Any:
                return lambda function: function

            stub.BaseModel = BaseModel
            stub.ConfigDict = lambda **kwargs: kwargs
            stub.Field = field
            stub.model_validator = model_validator
            sys.modules["pydantic"] = stub
            replaced_pydantic = True
        specification.loader.exec_module(module)
    finally:
        if replaced_pydantic:
            if prior_pydantic is None:
                sys.modules.pop("pydantic", None)
            else:
                sys.modules["pydantic"] = prior_pydantic
        sys.dont_write_bytecode = prior_bytecode
    return module


REFERENCE_HELPER = load_reference_helper()


def load_flow(filename: str, module_name: str) -> Any:
    """Load a temp copy through Humanize; never write bytecode into bundle assets."""

    from hmz.flows import loaded

    with TemporaryDirectory() as directory:
        copied = Path(directory) / "flows"
        shutil.copytree(FLOW_ROOT, copied)
        before = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            namespace = loaded(copied / filename)
        finally:
            sys.dont_write_bytecode = before
            for name, module in list(sys.modules.items()):
                location = getattr(module, "__file__", "")
                if location and Path(location).is_relative_to(copied):
                    sys.modules.pop(name, None)
        return type(module_name, (), namespace)


class FakeConfig:
    def __init__(
        self,
        permission: str,
        provider: str,
        model: str,
        effort: str,
    ) -> None:
        self.permission = permission
        self.provider = provider
        self.model = model
        self.effort = effort


class FakeSession:
    def __init__(self, agent: "FakeAgent") -> None:
        self.agent = agent

    def __call__(self, prompt: str, *, schema: type[Any]) -> Any:
        return self.agent.answer(prompt, schema)

    async def aturn(self, prompt: str, *, schema: type[Any]) -> Any:
        return self.agent.answer(prompt, schema)


class FakeAgent:
    """Zero-token scripted stand-in matching the flow-facing AgentBase surface."""

    def __init__(
        self,
        backend: str,
        permission: str,
        answers: dict[str, list[dict[str, Any]]] | None = None,
        *,
        provider_account: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        self.backend = backend
        self.config = FakeConfig(
            permission,
            provider_account or f"{backend}-account",
            model or ("claude-opus-5" if backend == "claude" else "gpt-6-astra"),
            effort or "max",
        )
        self.effort = self.config.effort
        self.answers = {key: list(value) for key, value in (answers or {}).items()}
        self.calls: list[str] = []
        self.schema_calls: defaultdict[str, int] = defaultdict(int)

    def new(self) -> FakeSession:
        return FakeSession(self)

    def __call__(self, prompt: str, *, schema: type[Any]) -> Any:
        return self.answer(prompt, schema)

    async def aturn(self, prompt: str, *, schema: type[Any]) -> Any:
        return self.answer(prompt, schema)

    def answer(self, prompt: str, schema: type[Any]) -> Any:
        self.calls.append(prompt)
        self.schema_calls[schema.__name__] += 1
        queued = self.answers.get(schema.__name__, [])
        if not queued:
            raise AssertionError(f"no scripted {schema.__name__} answer")
        return schema.model_validate(queued.pop(0))


def complete_review(*criterion_ids: str) -> dict[str, Any]:
    return {
        "verdict": "COMPLETE",
        "criteria": [
            {"criterion_id": item, "passed": True, "evidence": f"checked {item}"}
            for item in criterion_ids
        ],
        "issues": [],
        "human_dependency": None,
    }


def project_files(root: Path) -> list[str]:
    return sorted(
        str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()
    )


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_sha256(value: object) -> str:
    return digest(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


OLD_LAUNCH_ROLE_FIELDS = (
    "semanticRole",
    "backend",
    "model",
    "modelFamily",
    "effort",
    "upstreamProviderFamily",
    "intelligenceClass",
    "providerAccount",
    "providerRecordSha256",
    "permission",
    "flowRole",
)


def resolve_complete_identity(
    root: Path, flow_name: str = "deep-research"
) -> tuple[dict[str, Any], Path]:
    from test_humanize_research_launcher import (
        canned_private_catalog_runner,
        write_fixture,
    )

    _policy, bindings, humanize_home = write_fixture(root)
    with mock.patch.object(
        LAUNCHER, "SUPPORTED_BACKENDS", {"claude", "codex", "cursor"}
    ):
        identity = LAUNCHER.resolve_launch_identity(
            root,
            flow_name,
            bindings,
            humanize_home,
            catalog_runner=canned_private_catalog_runner,
        )
    return identity, humanize_home


def complete_outer_receipt(
    root: Path,
    identity: dict[str, Any],
    flow_name: str,
    task: str,
    setting: dict[str, Any],
) -> dict[str, Any]:
    feedback_receipt = None
    if setting.get("human_feedback") is not None:
        feedback_receipt = {
            "schema": "humanize-human-feedback-receipt/v1",
            "checkpoint_artifact": setting["human_feedback_checkpoint_artifact"],
            "checkpoint_sha256": setting["human_feedback_checkpoint_sha256"],
            "checkpoint_id": digest(b"test-checkpoint"),
            "human_feedback": setting["human_feedback"],
            "received_at": "2030-01-01T00:00:00Z",
        }
        feedback_receipt["receipt_sha256"] = canonical_sha256(feedback_receipt)
    return {
        "schema": "humanize-research-launch/v1",
        "runId": "launcher-to-flow-test",
        "flow": flow_name,
        "projectRoot": str(root.resolve()),
        "taskSha256": digest(task.encode()),
        "config": setting,
        "iterationContext": {
            "schema": "humanize-research-iteration-context/v1",
            "source": "standalone",
            "pmIssuePath": None,
            "pmIssueSha256": None,
            "iterationModeSnapshotSha256": None,
            "legacyDefault": False,
        },
        "humanFeedbackReceipt": feedback_receipt,
        "rolePolicySha256": identity["rolePolicySha256"],
        "accountBindingsSha256": identity["accountBindingsSha256"],
        "referenceSources": {
            "schema": "humanize-research-reference-binding/v1",
            "approval": "launcher-owned-approved-sha256",
            "selectedFlow": flow_name,
            "files": {
                name: digest((FLOW_ROOT / name).read_bytes())
                for name in (
                    "_research_reference.py",
                    "auto_research.py",
                    "deep_research.py",
                )
            },
        },
        "roles": identity["roles"],
    }


def agents_from_roles(roles: list[dict[str, Any]]) -> dict[str, FakeAgent]:
    named: dict[str, FakeAgent] = {}
    for recorded in roles:
        named[str(recorded["flowRole"])] = FakeAgent(
            str(recorded["backend"]),
            str(recorded["permission"]),
            provider_account=str(recorded["providerAccount"]),
            model=str(recorded["model"]),
            effort=str(recorded["effort"]),
        )
    return named


@contextmanager
def sealed_verify_env(
    root: Path, receipt: dict[str, Any], humanize_home: Path
) -> Iterator[None]:
    descriptor, receipt_digest = LAUNCHER.seal_launch_receipt(receipt)
    previous = {
        "fd": os.environ.get("HUMANIZE_RESEARCH_LAUNCH_FD"),
        "sha": os.environ.get("HUMANIZE_RESEARCH_LAUNCH_SHA256"),
        "home": os.environ.get("HUMANIZE_HOME"),
    }
    os.environ["HUMANIZE_RESEARCH_LAUNCH_FD"] = str(descriptor)
    os.environ["HUMANIZE_RESEARCH_LAUNCH_SHA256"] = receipt_digest
    os.environ["HUMANIZE_HOME"] = str(humanize_home)
    try:
        yield
    finally:
        os.close(descriptor)
        for variable, value in (
            ("HUMANIZE_RESEARCH_LAUNCH_FD", previous["fd"]),
            ("HUMANIZE_RESEARCH_LAUNCH_SHA256", previous["sha"]),
            ("HUMANIZE_HOME", previous["home"]),
        ):
            if value is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = value


def role(
    backend: str,
    upstream: str,
    model: str,
    family: str,
    effort: str,
    intelligence: str,
) -> dict[str, object]:
    return {
        "backend": backend,
        "provider": backend,
        "upstreamProviderFamily": upstream,
        "model": model,
        "modelFamily": family,
        "effort": effort,
        "dispatchStatus": "active",
        "intelligenceClass": intelligence,
    }


def policy() -> dict[str, object]:
    roles = {
        "plan": role(
            "codex", "openai", "gpt-6-astra", "gpt-6-astra", "xhigh", "planning"
        ),
        "research_collect": role(
            "codex", "openai", "gpt-5.6-luna", "gpt-5.6-luna", "max", "collection"
        ),
        "frontier_execute": role(
            "codex", "openai", "gpt-6-astra", "gpt-6-astra", "max", "topIntelligence"
        ),
        "task": role(
            "codex", "openai", "gpt-6-astra", "gpt-6-astra", "max", "analysis"
        ),
        "advisor": role(
            "cursor", "anthropic", "claude-opus-5", "claude-opus-5", "max", "verification"
        ),
    }
    return {
        "schema": "workflow-model-roles/v3",
        "catalogResolution": {
            "schema": "workflow-model-catalog-resolution/v1",
            "status": "verified",
            "source": "authenticated-native-cli",
            "verifiedBackends": ["codex", "cursor"],
            "verifiedRoles": list(roles),
            "evidenceReceipt": None,
        },
        "capabilityPolicy": {
            "reviewPairings": [
                {
                    "writer": {
                        "upstreamProviderFamily": "openai",
                        "modelFamily": "gpt-6-astra",
                        "efforts": ["max", "xhigh"],
                    },
                    "reviewer": {
                        "role": "advisor",
                        "upstreamProviderFamily": "anthropic",
                        "modelFamily": "claude-opus-5",
                        "effort": "max",
                    },
                }
            ]
        },
        "reasoningPolicy": {
            "defaultPosture": "balanced",
            "postures": {
                "balanced": {"allowedRoles": list(roles)},
            },
            "backendEffortLevels": {
                "codex": ["max", "xhigh"],
                "cursor": ["max"],
            },
            "intelligenceClasses": {
                "planning": ["plan"],
                "collection": ["research_collect"],
                "topIntelligence": ["frontier_execute"],
                "analysis": ["task"],
                "verification": ["advisor"],
            },
        },
        "roles": roles,
    }


FLOW_ROLES = {
    "deep-research": {
        "preflight": "plan",
        "collector": "research_collect",
        "counterevidence": "research_collect",
        "reducer": "frontier_execute",
        "reviewer": "advisor",
    },
    "auto-research": {
        "knowledge": "frontier_execute",
        "applicability": "plan",
        "experimenter": "task",
        "reviewer": "advisor",
    },
}

FLOW_ROLE_REQUESTS = {
    "deep-research": {
        "preflight": ("planning", "none", None),
        "collector": ("collection", "none", None),
        "counterevidence": ("collection", "none", None),
        "reducer": ("topIntelligence", "none", None),
        "reviewer": ("verification", "exact-opposite", "frontier_execute"),
    },
    "auto-research": {
        "knowledge": ("topIntelligence", "none", None),
        "applicability": ("planning", "none", None),
        "experimenter": ("analysis", "none", None),
        "reviewer": ("verification", "exact-opposite", "frontier_execute"),
    },
}


def cursor_catalog_id(model: str, effort: str) -> str:
    if model == "claude-opus-5" or model.startswith("claude-opus-5-"):
        return f"claude-opus-5-thinking-{effort}"
    if model == "claude-sonnet-5" or model.startswith("claude-sonnet-5-"):
        return f"claude-sonnet-5-thinking-{effort}"
    if model == "gpt-5.6-sol" or model.startswith("gpt-5.6-sol-"):
        return f"gpt-5.6-sol-{effort}"
    if model == "gpt-5.6-luna" or model.startswith("gpt-5.6-luna-"):
        return f"gpt-5.6-luna-{effort}"
    raise AssertionError(f"unsupported cursor model {model!r}")


def catalog_model_id(configured: dict[str, object]) -> str:
    backend = str(configured["backend"])
    model = str(configured["model"])
    effort = str(configured["effort"])
    if backend == "codex":
        return model
    if backend == "cursor":
        return cursor_catalog_id(model, effort)
    raise AssertionError(f"unsupported test backend {backend!r}")


def resolved_identity(semantic_role: str, configured: dict[str, object]) -> dict[str, object]:
    return {
        "semanticRole": semantic_role,
        "backend": configured["backend"],
        "provider": configured["provider"],
        "upstreamProviderFamily": configured["upstreamProviderFamily"],
        "model": configured["model"],
        "modelFamily": configured["modelFamily"],
        "effort": configured["effort"],
        "intelligenceClass": configured["intelligenceClass"],
        "dispatchStatus": configured["dispatchStatus"],
        "catalogModelId": catalog_model_id(configured),
    }


def fresh_catalog_evidence(document: dict[str, object]) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    roles = document["roles"]
    assert isinstance(roles, dict)
    for configured in roles.values():
        assert isinstance(configured, dict)
        backend = str(configured["backend"])
        if backend == "codex":
            block = evidence.setdefault("codex", {"backend": "codex", "coverage": {}})
            coverage = block["coverage"]
            assert isinstance(coverage, dict)
            model = str(configured["model"])
            efforts = coverage.setdefault(model, [])
            assert isinstance(efforts, list)
            effort = str(configured["effort"])
            if effort not in efforts:
                efforts.append(effort)
        elif backend == "cursor":
            block = evidence.setdefault(
                "cursor", {"backend": "cursor", "coverage": {"__cursor_ids__": []}}
            )
            coverage = block["coverage"]
            assert isinstance(coverage, dict)
            ids = coverage["__cursor_ids__"]
            assert isinstance(ids, list)
            concrete = catalog_model_id(configured)
            if concrete not in ids:
                ids.append(concrete)
    return evidence


def nested_launch_receipt(
    document: dict[str, object],
    policy_bytes: bytes,
    *,
    semantic_role: str,
    task_class: str,
    posture: str,
    review_mode: str,
    writer_role: str | None,
    evidence: dict[str, dict[str, object]],
) -> dict[str, object]:
    roles = document["roles"]
    assert isinstance(roles, dict)
    configured = roles[semantic_role]
    assert isinstance(configured, dict)
    resolved = resolved_identity(semantic_role, configured)
    writer_identity = None
    used_evidence = {resolved["backend"]: evidence[str(resolved["backend"])]}
    if writer_role is not None:
        writer_configured = roles[writer_role]
        assert isinstance(writer_configured, dict)
        writer_identity = resolved_identity(writer_role, writer_configured)
        used_evidence[str(writer_identity["backend"])] = evidence[
            str(writer_identity["backend"])
        ]
    resolution = document["catalogResolution"]
    capability = document["capabilityPolicy"]
    reasoning = document["reasoningPolicy"]
    nested: dict[str, object] = {
        "schema": "workflow-native-launch-receipt/v1",
        "request": {
            "taskClass": task_class,
            "reasoningPosture": posture,
            "semanticRole": semantic_role,
            "writerRole": writer_role,
            "reviewMode": review_mode,
        },
        "resolved": resolved,
        "writer": writer_identity,
        "policy": {
            "modelsSchema": document["schema"],
            "modelsDocumentSha256": digest(policy_bytes),
            "catalogResolutionSha256": canonical_sha256(resolution),
            "capabilityPolicySha256": canonical_sha256(capability),
            "reasoningPolicySha256": canonical_sha256(reasoning),
            "catalogStatus": resolution.get("status") if isinstance(resolution, dict) else None,
            "catalogSource": "authenticated-native-cli",
            "catalogEvidenceReceipt": (
                resolution.get("evidenceReceipt") if isinstance(resolution, dict) else None
            ),
            "liveCatalogAuthenticatedByResolver": True,
            "freshCatalogEvidence": used_evidence,
        },
    }
    unsigned = {key: value for key, value in nested.items() if key != "receiptSha256"}
    nested["receiptSha256"] = canonical_sha256(unsigned)
    return nested


@contextmanager
def trusted_launch(
    root: Path,
    flow_name: str,
    agents: Any,
    task: str,
    setting: Any,
) -> Iterator[None]:
    document = policy()
    policy_bytes = (json.dumps(document, indent=2) + "\n").encode()
    policy_path = root / "config/workflows/models.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_bytes(policy_bytes)

    bindings: dict[str, Any] = {
        "schema": "humanize-research-account-bindings/v1",
        "flows": {flow_name: {}},
    }
    receipt_roles: list[dict[str, Any]] = []
    evidence = fresh_catalog_evidence(document)
    reasoning = document["reasoningPolicy"]
    assert isinstance(reasoning, dict)
    posture = str(reasoning["defaultPosture"])
    for flow_role, agent in agents._asdict().items():
        semantic_role = FLOW_ROLES[flow_name][flow_role]
        configured = document["roles"][semantic_role]
        assert isinstance(configured, dict)
        account = agent.config.provider
        provider_record = (
            json.dumps(
                {
                    "cli": configured["backend"],
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
        provider_digest = digest(provider_record)
        provider_path = (
            root
            / "humanize-home/providers"
            / configured["backend"]
            / account
            / "provider.json"
        )
        provider_path.parent.mkdir(parents=True, exist_ok=True)
        provider_path.write_bytes(provider_record)
        bindings["flows"][flow_name][flow_role] = {
            "providerAccount": account,
            "providerRecordSha256": provider_digest,
            "upstreamProviderFamily": configured["upstreamProviderFamily"],
        }
        task_class, review_mode, writer_role = FLOW_ROLE_REQUESTS[flow_name][flow_role]
        nested = nested_launch_receipt(
            document,
            policy_bytes,
            semantic_role=semantic_role,
            task_class=task_class,
            posture=posture,
            review_mode=review_mode,
            writer_role=writer_role,
            evidence=evidence,
        )
        resolved = nested["resolved"]
        assert isinstance(resolved, dict)
        concrete = str(resolved["catalogModelId"])
        receipt_roles.append(
            {
                "semanticRole": semantic_role,
                "backend": configured["backend"],
                "provider": configured["provider"],
                "upstreamProviderFamily": configured["upstreamProviderFamily"],
                "model": concrete,
                "modelFamily": configured["modelFamily"],
                "effort": configured["effort"],
                "intelligenceClass": configured["intelligenceClass"],
                "dispatchStatus": configured["dispatchStatus"],
                "catalogModelId": concrete,
                "canonicalModel": configured["model"],
                "providerAccount": account,
                "providerRecordSha256": provider_digest,
                "permission": agent.config.permission,
                "flowRole": flow_role,
                "launchReceipt": nested,
            }
        )
        agent.backend = str(configured["backend"])
        agent.config.model = concrete
        agent.config.effort = str(configured["effort"])
        agent.effort = str(configured["effort"])
    bindings_bytes = (json.dumps(bindings, indent=2) + "\n").encode()
    bindings_path = root / "config/workflows/humanize-research-accounts.json"
    bindings_path.write_bytes(bindings_bytes)
    feedback_receipt = None
    if setting.human_feedback is not None:
        feedback_receipt = {
            "schema": "humanize-human-feedback-receipt/v1",
            "checkpoint_artifact": setting.human_feedback_checkpoint_artifact,
            "checkpoint_sha256": setting.human_feedback_checkpoint_sha256,
            "checkpoint_id": digest(b"test-checkpoint"),
            "human_feedback": setting.human_feedback,
            "received_at": "2030-01-01T00:00:00Z",
        }
        feedback_receipt["receipt_sha256"] = canonical_sha256(feedback_receipt)
    receipt = {
        "schema": "humanize-research-launch/v1",
        "runId": "zero-token-test",
        "flow": flow_name,
        "projectRoot": str(root.resolve()),
        "taskSha256": digest(task.encode()),
        "config": setting.model_dump(mode="json"),
        "iterationContext": {
            "schema": "humanize-research-iteration-context/v1",
            "source": "standalone",
            "pmIssuePath": None,
            "pmIssueSha256": None,
            "iterationModeSnapshotSha256": None,
            "legacyDefault": False,
        },
        "humanFeedbackReceipt": feedback_receipt,
        "rolePolicySha256": digest(policy_bytes),
        "accountBindingsSha256": digest(bindings_bytes),
        "referenceSources": {
            "schema": "humanize-research-reference-binding/v1",
            "approval": "launcher-owned-approved-sha256",
            "selectedFlow": flow_name,
            "files": {
                name: digest((FLOW_ROOT / name).read_bytes())
                for name in (
                    "_research_reference.py",
                    "auto_research.py",
                    "deep_research.py",
                )
            },
        },
        "roles": receipt_roles,
    }
    descriptor, receipt_digest = LAUNCHER.seal_launch_receipt(receipt)
    previous = {
        "fd": os.environ.get("HUMANIZE_RESEARCH_LAUNCH_FD"),
        "sha": os.environ.get("HUMANIZE_RESEARCH_LAUNCH_SHA256"),
        "home": os.environ.get("HUMANIZE_HOME"),
    }
    os.environ["HUMANIZE_RESEARCH_LAUNCH_FD"] = str(descriptor)
    os.environ["HUMANIZE_RESEARCH_LAUNCH_SHA256"] = receipt_digest
    os.environ["HUMANIZE_HOME"] = str(root / "humanize-home")
    try:
        yield
    finally:
        os.close(descriptor)
        for variable, value in (
            ("HUMANIZE_RESEARCH_LAUNCH_FD", previous["fd"]),
            ("HUMANIZE_RESEARCH_LAUNCH_SHA256", previous["sha"]),
            ("HUMANIZE_HOME", previous["home"]),
        ):
            if value is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = value
        shutil.rmtree(root / "config")
        shutil.rmtree(root / "humanize-home")


@contextmanager
def project_dir() -> Iterator[Path]:
    old = Path.cwd()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        try:
            os.chdir(root)
            yield root
        finally:
            os.chdir(old)


class ResearchReferenceStructuralSecurityTest(unittest.TestCase):
    """Security contracts that do not require importing or executing Humanize."""

    def test_terminal_target_is_lexical_and_does_not_follow_existing_leaf(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            victim = root / "victim.json"
            victim.write_text("ORIGINAL\n", encoding="utf-8")
            linked = root / "terminal.json"
            linked.symlink_to(victim)
            setting = LAUNCHER.normalize_config(
                "deep-research", {"terminal_artifact": "terminal.json"}
            )
            self.assertEqual(LAUNCHER._terminal_target(root, setting), linked)

    def test_structural_loader_does_not_write_bytecode_into_bundle_assets(self) -> None:
        self.assertFalse((FLOW_ROOT / "__pycache__").exists())

    def test_reference_terminal_writer_refuses_a_linked_leaf(self) -> None:
        with project_dir() as root:
            victim = root / "victim.json"
            victim.write_text("ORIGINAL\n", encoding="utf-8")
            (root / "terminal.json").symlink_to(victim)
            with self.assertRaisesRegex(ValueError, "link|symlink"):
                REFERENCE_HELPER.write_terminal("terminal.json", {"replaced": True})
            self.assertEqual(victim.read_text(encoding="utf-8"), "ORIGINAL\n")

    def test_reference_terminal_writer_revalidates_leaf_before_replace(self) -> None:
        with project_dir() as root:
            victim = root / "victim.json"
            victim.write_text("ORIGINAL\n", encoding="utf-8")
            terminal = root / "terminal.json"
            compare_directories = REFERENCE_HELPER._same_output_directory
            swapped = False

            def swap_before_leaf_revalidation(left_fd: int, right_fd: int) -> bool:
                nonlocal swapped
                if not swapped:
                    terminal.symlink_to(victim)
                    swapped = True
                return compare_directories(left_fd, right_fd)

            with mock.patch.object(
                REFERENCE_HELPER,
                "_same_output_directory",
                side_effect=swap_before_leaf_revalidation,
            ), self.assertRaisesRegex(ValueError, "symlink"):
                REFERENCE_HELPER.write_terminal(
                    "terminal.json", {"replacement": True}
                )
            self.assertTrue(swapped)
            self.assertTrue(terminal.is_symlink())
            self.assertEqual(victim.read_text(encoding="utf-8"), "ORIGINAL\n")

    def test_terminal_artifact_refuses_absolute_and_parent_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "project-relative"):
            REFERENCE_HELPER.safe_project_path("/tmp/out.json")
        with self.assertRaisesRegex(ValueError, "non-traversing"):
            REFERENCE_HELPER.safe_project_path("../out.json")

    def test_model_call_budget_fails_before_overrun(self) -> None:
        budget = REFERENCE_HELPER.CallBudget(1)
        budget.reserve("first")
        with self.assertRaisesRegex(RuntimeError, "exhausted before second"):
            budget.reserve("second")
        self.assertEqual(budget.used, 1)

    def test_iteration_policy_is_strict_and_preserves_checkpoint_boundaries(self) -> None:
        if hasattr(REFERENCE_HELPER.IterationConfig, "model_validate"):
            with self.assertRaisesRegex(Exception, "autonomous mode cannot carry"):
                REFERENCE_HELPER.IterationConfig(human_feedback="stale direction")
            with self.assertRaisesRegex(Exception, "literal_error|Input should be"):
                REFERENCE_HELPER.IterationConfig(iteration_mode="Fast")
            with self.assertRaisesRegex(Exception, "requires lease"):
                REFERENCE_HELPER.IterationConfig(iteration_mode="human_guided")
            with self.assertRaisesRegex(Exception, "checkpoint"):
                REFERENCE_HELPER.IterationConfig(
                    iteration_mode="human_guided",
                    human_checkin_lease_minutes=60,
                    human_checkin_lease_expires_at="2030-01-01T01:00:00Z",
                    human_feedback="unbound feedback",
                )
            setting = REFERENCE_HELPER.IterationConfig(
                iteration_mode="human_guided",
                human_checkin_lease_minutes=60,
                human_checkin_lease_expires_at="2030-01-01T01:00:00Z",
                human_unavailable_fallback="autonomous_review",
                human_feedback="prioritize the falsifier before another broad search",
                human_feedback_checkpoint_artifact=(
                    ".humanize/artifacts/prior-terminal.json"
                ),
                human_feedback_checkpoint_sha256="sha256:" + "a" * 64,
            )
        else:
            setting = types.SimpleNamespace(
                iteration_mode="human_guided",
                human_checkin_lease_minutes=60,
                human_checkin_lease_expires_at="2030-01-01T01:00:00Z",
                human_unavailable_fallback="autonomous_review",
                human_feedback="prioritize the falsifier before another broad search",
                human_feedback_checkpoint_artifact=(
                    ".humanize/artifacts/prior-terminal.json"
                ),
                human_feedback_checkpoint_sha256="sha256:" + "a" * 64,
            )
        active = REFERENCE_HELPER.resolve_iteration_policy(
            setting,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        prompt = REFERENCE_HELPER.iteration_prompt(
            active,
            setting.human_feedback,
            setting.human_feedback_checkpoint_artifact,
            setting.human_feedback_checkpoint_sha256,
        )
        self.assertIn('"effective_mode": "human_guided"', prompt)
        self.assertIn("prioritize the falsifier", prompt)
        self.assertIn("prior-terminal.json", prompt)
        self.assertIn("sha256:" + "a" * 64, prompt)
        artifact = REFERENCE_HELPER.attach_iteration_result(
            {"domain_terminal": "SUCCESS"},
            setting,
            active,
            hypothesis="the intervention beats the control",
            actual_operation="ran one frozen comparison",
            raw_observation={"delta": 1.5},
            agent_interpretation="the observed delta supports the hypothesis",
            anomaly_or_alternate_interpretation=["measurement noise"],
            candidate_next_tasks=["replicate with a second seed"],
        )
        self.assertEqual(artifact["domain_terminal"], "HUMAN_OBSERVATION_REQUIRED")
        self.assertEqual(artifact["research_outcome"], "SUCCESS")
        self.assertEqual(artifact["idea_review_disposition"], "skipped_human_guided")
        self.assertEqual(
            artifact["consumed_human_feedback"], setting.human_feedback
        )
        self.assertEqual(
            artifact["consumed_human_feedback_checkpoint_artifact"],
            setting.human_feedback_checkpoint_artifact,
        )
        self.assertEqual(
            artifact["consumed_human_feedback_checkpoint_sha256"],
            setting.human_feedback_checkpoint_sha256,
        )
        self.assertIsNone(artifact["human_observation_checkpoint"]["human_feedback"])
        self.assertFalse(artifact["next_planning_allowed"])

        expired = types.SimpleNamespace(
            iteration_mode="human_guided",
            human_checkin_lease_minutes=60,
            human_checkin_lease_expires_at="2029-12-31T23:00:00Z",
            human_unavailable_fallback="autonomous_review",
            human_feedback=None,
            human_feedback_checkpoint_artifact=None,
            human_feedback_checkpoint_sha256=None,
        )
        fallback = REFERENCE_HELPER.resolve_iteration_policy(
            expired,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        fallback_artifact = REFERENCE_HELPER.attach_iteration_result(
            {"domain_terminal": "SUCCESS"},
            expired,
            fallback,
            hypothesis="candidate",
            actual_operation="reviewed before execution",
            raw_observation={"status": "reviewed"},
            agent_interpretation="continue under autonomous review",
            anomaly_or_alternate_interpretation=[],
            candidate_next_tasks=["continue the bounded cycle"],
        )
        self.assertEqual(fallback_artifact["idea_review_disposition"], "performed")
        self.assertEqual(fallback_artifact["iteration_risk_triggers"], ["lease_expired"])
        self.assertIsNone(fallback_artifact["human_observation_checkpoint"])

        paused = types.SimpleNamespace(
            **{
                **vars(expired),
                "human_unavailable_fallback": "pause",
            }
        )
        with self.assertRaisesRegex(RuntimeError, "lease expired"):
            REFERENCE_HELPER.resolve_iteration_policy(
                paused,
                now=datetime(2030, 1, 1, tzinfo=timezone.utc),
            )


class DeepResearchHumanizeReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.deep = load_flow("deep_research.py", "reference_deep_research")
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(f"pinned Humanize2 environment unavailable: {exc}")

    def test_preflight_stop_never_fans_out_and_writes_reviewed_terminal(self) -> None:
        preflight = FakeAgent(
            "planner-provider",
            "read-only",
            {
                "PreflightDecision": [
                    {
                        "core_problem": "decide whether the candidate is viable",
                        "hard_constraints": ["preserve user outcome"],
                        "kill_set": [
                            {
                                "assumption_id": "a1",
                                "statement": "the measurement is valid",
                                "uncertainty": 5,
                                "downstream_invalidation": 5,
                                "falsifier": "proxy diverges from target",
                                "minimum_valid_check": "compare proxy and target",
                            }
                        ],
                        "verdict": "STOP_IDEA_REFUTED",
                        "evidence": ["the minimum valid comparison failed"],
                    }
                ]
            },
        )
        collector = FakeAgent("cheap-a", "read-only")
        counter = FakeAgent("cheap-b", "read-only")
        reducer = FakeAgent("planner-provider", "read-only")
        reviewer = FakeAgent(
            "review-provider",
            "read-only",
            {"TerminalReview": [complete_review("preflight-stop")]},
        )
        agents = self.deep.Agents(preflight, collector, counter, reducer, reviewer)
        with project_dir() as root:
            setting = self.deep.Config(branch_count=3, max_model_calls=8)
            with trusted_launch(root, "deep-research", agents, "candidate task", setting):
                asyncio.run(
                    self.deep.run(
                        agents,
                        "candidate task",
                        setting,
                    )
                )
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
            files = project_files(root)
        self.assertEqual(artifact["terminal"], "STOP_IDEA_REFUTED")
        self.assertEqual(artifact["model_calls_used"], 2)
        self.assertEqual(collector.calls, [])
        self.assertEqual(counter.calls, [])
        self.assertEqual(reducer.calls, [])
        self.assertEqual(
            files, [".humanize/artifacts/deep-research-terminal.json"]
        )

    def test_bounded_fanout_builds_deterministic_claim_ledger(self) -> None:
        preflight = FakeAgent(
            "planner-provider",
            "read-only",
            {
                "PreflightDecision": [
                    {
                        "core_problem": "select the supported option",
                        "hard_constraints": ["cite sources"],
                        "kill_set": [
                            {
                                "assumption_id": "a1",
                                "statement": "mechanism can work",
                                "uncertainty": 5,
                                "downstream_invalidation": 5,
                                "falsifier": "counterexample",
                                "minimum_valid_check": "search counterexample",
                            }
                        ],
                        "verdict": "CONTINUE",
                        "evidence": ["minimum valid check passed"],
                    }
                ],
                "ResearchPlan": [
                    {
                        "branches": [
                            {
                                "branch_id": "negative",
                                "question": "what refutes it",
                                "source_classes": ["primary-paper"],
                                "claim_ids": ["c1"],
                                "counterevidence_target": "boundary failures",
                            },
                            {
                                "branch_id": "baseline",
                                "question": "what is baseline",
                                "source_classes": ["official-doc"],
                                "claim_ids": ["c2"],
                                "counterevidence_target": "stronger baseline",
                            },
                        ]
                    }
                ],
            },
        )
        counter = FakeAgent(
            "cheap-b",
            "read-only",
            {
                "BranchResult": [
                    {
                        "branch_id": "negative",
                        "records": [
                            {
                                "evidence_id": "e2",
                                "claim_id": "c1",
                                "kind": "Fact",
                                "source": "https://primary.invalid/two",
                                "supporting_location": "result table",
                                "finding": "bounded failure",
                                "contradiction_key": "k1",
                            }
                        ],
                        "unanswered_claim_ids": [],
                    }
                ]
            },
        )
        collector = FakeAgent(
            "cheap-a",
            "read-only",
            {
                "BranchResult": [
                    {
                        "branch_id": "baseline",
                        "records": [
                            {
                                "evidence_id": "e1",
                                "claim_id": "c2",
                                "kind": "Fact",
                                "source": "https://official.invalid/one",
                                "supporting_location": "section one",
                                "finding": "baseline result",
                                "contradiction_key": None,
                            }
                        ],
                        "unanswered_claim_ids": [],
                    }
                ]
            },
        )
        reducer = FakeAgent(
            "planner-provider",
            "read-only",
            {
                "ResearchSynthesis": [
                    {
                        "recommendation": "proceed only within the boundary",
                        "claim_coverage": {"c1": ["e2"], "c2": ["e1"]},
                        "contradictions": ["k1"],
                        "open_gaps": [],
                        "terminal": "SUCCESS",
                    }
                ]
            },
        )
        reviewer = FakeAgent(
            "review-provider",
            "read-only",
            {"TerminalReview": [complete_review("coverage", "contradictions")]},
        )
        agents = self.deep.Agents(preflight, collector, counter, reducer, reviewer)
        with project_dir() as root:
            setting = self.deep.Config(branch_count=2, max_model_calls=7)
            with trusted_launch(root, "deep-research", agents, "research task", setting):
                asyncio.run(
                    self.deep.run(
                        agents,
                        "research task",
                        setting,
                    )
                )
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
            files = project_files(root)
        self.assertEqual(artifact["terminal"], "SUCCESS")
        self.assertEqual(artifact["claim_ledger"], {"c1": ["e2"], "c2": ["e1"]})
        self.assertEqual(artifact["model_calls_used"], 6)
        self.assertEqual(artifact["iteration_policy"]["effective_mode"], "autonomous")
        self.assertEqual(artifact["idea_review_disposition"], "performed")
        self.assertTrue(artifact["next_planning_allowed"])
        self.assertIsNone(artifact["human_observation_checkpoint"])
        self.assertEqual(counter.schema_calls["BranchResult"], 1)
        self.assertEqual(collector.schema_calls["BranchResult"], 1)
        self.assertEqual(
            files, [".humanize/artifacts/deep-research-terminal.json"]
        )

    def test_direct_invocation_without_launcher_fails_closed_before_turn(self) -> None:
        agents = self.deep.Agents(
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("claude", "read-only"),
        )
        with project_dir() as root:
            asyncio.run(
                self.deep.run(
                    agents,
                    "untrusted task",
                    self.deep.Config(branch_count=1, max_model_calls=5),
                )
            )
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
        self.assertEqual(artifact["terminal"], "UNRESOLVED_INFRASTRUCTURE")
        self.assertEqual(artifact["failure_category"], "launch-contract-failure")
        self.assertIsNone(artifact["human_dependency"])
        self.assertEqual(sum(len(agent.calls) for agent in agents), 0)

    def test_agent_effort_change_after_receipt_fails_closed_before_turn(self) -> None:
        agents = self.deep.Agents(
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("claude", "read-only"),
        )
        with project_dir() as root:
            setting = self.deep.Config(branch_count=1, max_model_calls=5)
            with trusted_launch(root, "deep-research", agents, "bound task", setting):
                agents.reviewer.effort = "high"
                asyncio.run(self.deep.run(agents, "bound task", setting))
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
        self.assertEqual(artifact["terminal"], "UNRESOLVED_INFRASTRUCTURE")
        self.assertEqual(artifact["failure_category"], "launch-contract-failure")
        self.assertIn("effort changed after launch", artifact["reason"])
        self.assertEqual(sum(len(agent.calls) for agent in agents), 0)

    def test_cursor_diversity_routes_without_an_agent_turn(self) -> None:
        agents = self.deep.Agents(
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("claude", "read-only"),
        )
        with project_dir() as root:
            setting = self.deep.Config(
                cursor_diversity_required=True,
                branch_count=1,
                max_model_calls=5,
            )
            with trusted_launch(root, "deep-research", agents, "research task", setting):
                asyncio.run(
                    self.deep.run(
                        agents,
                        "research task",
                        setting,
                    )
                )
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
            files = project_files(root)
        self.assertEqual(artifact["terminal"], "UNSUPPORTED_HUMANIZE_CAPABILITY")
        self.assertEqual(artifact["route_to"], "subagent-scheduler")
        self.assertEqual(sum(len(agent.calls) for agent in agents), 0)
        self.assertEqual(
            files, [".humanize/artifacts/deep-research-terminal.json"]
        )

    def test_budget_failure_is_an_explicit_unresolved_terminal(self) -> None:
        agents = self.deep.Agents(
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("codex", "read-only"),
            FakeAgent("claude", "read-only"),
        )
        with project_dir() as root:
            setting = self.deep.Config(branch_count=1, max_model_calls=5)
            with trusted_launch(root, "deep-research", agents, "budget test", setting):
                with mock.patch.object(
                    self.deep.CallBudget,
                    "reserve",
                    side_effect=RuntimeError(
                        "model-call budget exhausted before preflight"
                    ),
                ):
                    asyncio.run(self.deep.run(agents, "budget test", setting))
            artifact = json.loads(
                (root / ".humanize/artifacts/deep-research-terminal.json").read_text()
            )
        self.assertEqual(artifact["terminal"], "UNRESOLVED_INFRASTRUCTURE")
        self.assertEqual(
            artifact["failure_category"], "model-call-budget-exhausted"
        )
        self.assertIsNone(artifact["human_dependency"])
        self.assertEqual(sum(len(agent.calls) for agent in agents), 0)


class AutoResearchHumanizeReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.auto = load_flow("auto_research.py", "reference_auto_research")
        except ModuleNotFoundError as exc:
            raise unittest.SkipTest(f"pinned Humanize2 environment unavailable: {exc}")

    @staticmethod
    def knowledge_answer() -> dict[str, Any]:
        return {
            "core_problem": "validate the mechanism",
            "claims": [
                {
                    "claim_id": "c1",
                    "conclusion": "the intervention may improve the metric",
                    "evidence_refs": ["e1"],
                    "material": True,
                    "experimental_candidate": True,
                    "observational_boundary": "update if the primary study is refuted",
                }
            ],
            "contradictions": [],
            "open_gaps": [],
        }

    @staticmethod
    def applicability_answer() -> dict[str, Any]:
        return {
            "verdict": "APPLICABLE",
            "dispositions": [
                {
                    "claim_id": "c1",
                    "disposition": "EXPERIMENTAL",
                    "reason": "a controlled intervention is possible",
                    "hypothesis_candidate": "intervention beats control",
                    "observational_boundary": None,
                }
            ],
            "evidence": "the intervention and control are defined",
        }

    def test_human_guided_not_ready_returns_checkpoint_without_experimenter(self) -> None:
        knowledge = FakeAgent(
            "writer-provider",
            "read-only",
            {
                "KnowledgeSynthesis": [self.knowledge_answer()],
                "ValidationPlan": [
                    {
                        "status": "NOT_EXECUTED_NOT_EXPERIMENTALLY_VALIDATED",
                        "missing_resources": ["resources declaration"],
                        "main_experiment": "compare intervention to control",
                        "baselines_and_controls": ["current baseline", "negative control"],
                        "ablations": ["remove component"],
                        "statistics": "fixed seeds, repeats, intervals, multiplicity rule",
                        "correctness_and_safety_gates": ["schema and leakage gate"],
                        "resource_estimate": "compute, data, time, storage, cost",
                        "execution_order": ["instrument", "baseline", "critical trial"],
                        "stopping_rules": ["budget", "invalid", "crash"],
                        "outcome_to_decision": {
                            "support": "strengthen",
                            "refute": "revise",
                            "inconclusive": "narrow",
                        },
                        "reproducibility_artifacts": ["contract", "seeds", "raw logs"],
                    }
                ],
            },
        )
        applicability = FakeAgent(
            "analyst-provider",
            "read-only",
            {"ApplicabilityDecision": [self.applicability_answer()]},
        )
        experimenter = FakeAgent("trial-provider", "workspace-write")
        reviewer = FakeAgent(
            "review-provider",
            "read-only",
            {"TerminalReview": [complete_review("not-ready-gate")]},
        )
        agents = self.auto.Agents(knowledge, applicability, experimenter, reviewer)
        with project_dir() as root:
            setting = self.auto.Config(
                iteration_mode="human_guided",
                human_checkin_lease_minutes=60,
                human_checkin_lease_expires_at="2099-01-01T00:00:00Z",
                human_unavailable_fallback="pause",
                human_feedback="test the strongest falsifier first",
                human_feedback_checkpoint_artifact=(
                    ".humanize/artifacts/prior-terminal.json"
                ),
                human_feedback_checkpoint_sha256="sha256:" + "b" * 64,
                max_trials=1,
                max_model_calls=5,
            )
            with trusted_launch(root, "auto-research", agents, "auto research task", setting):
                asyncio.run(
                    self.auto.run(
                        agents,
                        "auto research task",
                        setting,
                    )
                )
            artifact = json.loads(
                (root / ".humanize/artifacts/auto-research-terminal.json").read_text()
            )
            files = project_files(root)
        self.assertEqual(artifact["resource_verdict"], "NOT_READY")
        self.assertEqual(artifact["terminal"], "HUMAN_OBSERVATION_REQUIRED")
        self.assertEqual(
            artifact["research_outcome"], "SUCCESS_WITH_EXPERIMENT_PLAN"
        )
        self.assertEqual(artifact["idea_review_disposition"], "skipped_human_guided")
        self.assertFalse(artifact["next_planning_allowed"])
        self.assertEqual(
            artifact["consumed_human_feedback"],
            "test the strongest falsifier first",
        )
        checkpoint = artifact["human_observation_checkpoint"]
        self.assertIsNone(checkpoint["human_feedback"])
        self.assertFalse(checkpoint["next_planning_allowed"])
        self.assertEqual(
            artifact["validation_plan"]["status"],
            "NOT_EXECUTED_NOT_EXPERIMENTALLY_VALIDATED",
        )
        for agent in (knowledge, applicability, reviewer):
            self.assertTrue(
                any("test the strongest falsifier first" in prompt for prompt in agent.calls)
            )
        self.assertTrue(
            any(
                "minimum-validity and risk-admission" in prompt
                and "without broad novelty or interestingness debate" in prompt
                and "perform the targeted idea or method challenge" in prompt
                for prompt in applicability.calls
            )
        )
        self.assertEqual(experimenter.calls, [])
        self.assertEqual(
            files, [".humanize/artifacts/auto-research-terminal.json"]
        )

    def test_ready_executes_only_bounded_trial_contract(self) -> None:
        knowledge = FakeAgent(
            "writer-provider",
            "read-only",
            {
                "KnowledgeSynthesis": [self.knowledge_answer()],
                "ExperimentContract": [
                    {
                        "trials": [
                            {
                                "trial_id": "t1",
                                "claim_id": "c1",
                                "hypothesis": "intervention beats control",
                                "uncertainty": 5,
                                "downstream_invalidation": 5,
                                "intervention": "enable candidate",
                                "control": "unchanged baseline",
                                "metric": "predeclared score",
                                "falsifier": "score fails to exceed noise floor",
                                "budget": "one bounded run",
                                "stop_rule": "stop after the run or invalid gate",
                            }
                        ],
                        "global_stop_rule": "stop at one trial or budget exhaustion",
                        "correctness_gate": "schema, leakage, and baseline checks pass",
                        "isolation_rule": "project-local isolated harness only",
                    }
                ],
            },
        )
        applicability = FakeAgent(
            "analyst-provider",
            "read-only",
            {"ApplicabilityDecision": [self.applicability_answer()]},
        )
        experimenter = FakeAgent(
            "trial-provider",
            "workspace-write",
            {
                "TrialResult": [
                    {
                        "trial_id": "t1",
                        "status": "REFUTED",
                        "intervention_result": "no improvement",
                        "control_result": "stable baseline",
                        "correctness_gate_passed": True,
                        "falsifier_triggered": True,
                        "root_cause": "mechanism did not move the target",
                        "evidence_refs": ["local://trial/t1"],
                        "budget_used": "one bounded run",
                    }
                ]
            },
        )
        reviewer = FakeAgent(
            "review-provider",
            "read-only",
            {"TerminalReview": [complete_review("trial-contract", "terminal")]},
        )
        resources = self.auto.ResourceDeclaration(
            compute_available=True,
            data_available=True,
            harness_available=True,
            budget_available=True,
            correctness_gate_available=True,
            isolation_available=True,
            authorized=True,
            resource_refs=["local://resources/manifest"],
        )
        agents = self.auto.Agents(knowledge, applicability, experimenter, reviewer)
        with project_dir() as root:
            setting = self.auto.Config(
                max_trials=1,
                max_model_calls=5,
                resources=resources,
            )
            with trusted_launch(root, "auto-research", agents, "auto research task", setting):
                asyncio.run(
                    self.auto.run(
                        agents,
                        "auto research task",
                        setting,
                    )
                )
            artifact = json.loads(
                (root / ".humanize/artifacts/auto-research-terminal.json").read_text()
            )
            files = project_files(root)
        self.assertEqual(artifact["resource_verdict"], "READY")
        self.assertEqual(artifact["terminal"], "SUCCESS_EXPERIMENTALLY_CHECKED")
        self.assertEqual(len(artifact["trial_results"]), 1)
        prompt = experimenter.calls[0]
        for required in ("control", "falsifier", "budget", "stop_rule"):
            self.assertIn(required, prompt)
        self.assertEqual(
            files, [".humanize/artifacts/auto-research-terminal.json"]
        )

    def test_resource_gate_is_deterministic_and_ignores_ambient_state(self) -> None:
        self.assertEqual(self.auto.resource_gate(None)[0], "NOT_READY")
        invalid = self.auto.ResourceDeclaration(
            integrity="INVALID", invalid_reason="contradictory manifest"
        )
        self.assertEqual(self.auto.resource_gate(invalid)[0], "INVALID")
        partial = self.auto.ResourceDeclaration(compute_available=True)
        verdict, missing = self.auto.resource_gate(partial)
        self.assertEqual(verdict, "NOT_READY")
        self.assertIn("authorization", missing)

    def test_malformed_structured_answer_writes_unresolved_terminal(self) -> None:
        knowledge = FakeAgent(
            "codex",
            "read-only",
            {"KnowledgeSynthesis": [{"not": "the requested shape"}]},
        )
        applicability = FakeAgent("codex", "read-only")
        experimenter = FakeAgent("codex", "workspace-write")
        reviewer = FakeAgent("claude", "read-only")
        agents = self.auto.Agents(knowledge, applicability, experimenter, reviewer)
        with project_dir() as root:
            setting = self.auto.Config(max_trials=1, max_model_calls=5)
            with trusted_launch(root, "auto-research", agents, "bad answer", setting):
                asyncio.run(self.auto.run(agents, "bad answer", setting))
            artifact = json.loads(
                (root / ".humanize/artifacts/auto-research-terminal.json").read_text()
            )
        self.assertEqual(artifact["terminal"], "UNRESOLVED_INFRASTRUCTURE")
        self.assertEqual(artifact["failure_category"], "structured-contract-failure")
        self.assertIsNone(artifact["human_dependency"])
        self.assertEqual(artifact["model_calls_used"], 1)


class LauncherToFlowLaunchReceiptContractTest(unittest.TestCase):
    """Launcher-sealed complete roles must be the only authorization path."""

    def test_flow_helper_does_not_import_or_exec_catalog_tools(self) -> None:
        source = HELPER_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "import resolve_launch_spec",
            "import live_catalog",
            "import validate_model_roles",
            "import subprocess",
            "import importlib",
            "__import__",
            "eval(",
            "exec(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_nominal_complete_launcher_to_flow_role_shape(self) -> None:
        flow_name = "deep-research"
        task = "bounded launcher-to-flow task"
        setting = LAUNCHER.normalize_config(flow_name, {})
        with project_dir() as root:
            identity, humanize_home = resolve_complete_identity(root, flow_name)
            roles = identity["roles"]
            self.assertGreaterEqual(len(roles), 5)
            for recorded in roles:
                self.assertIn("canonicalModel", recorded)
                self.assertIn("catalogModelId", recorded)
                self.assertIn("launchReceipt", recorded)
                nested = recorded["launchReceipt"]
                self.assertEqual(nested["schema"], "workflow-native-launch-receipt/v1")
                self.assertEqual(
                    recorded["catalogModelId"],
                    nested["resolved"]["catalogModelId"],
                )
                self.assertEqual(recorded["model"], recorded["catalogModelId"])
                self.assertEqual(
                    recorded["canonicalModel"], nested["resolved"]["model"]
                )
            named = agents_from_roles(roles)
            receipt = complete_outer_receipt(
                root, identity, flow_name, task, setting
            )
            with sealed_verify_env(root, receipt, humanize_home):
                launch = REFERENCE_HELPER.verify_launch(
                    flow_name, named, FLOW_ROLES[flow_name], task, setting
                )
            self.assertEqual(launch["schema"], "humanize-research-launch/v1")
            self.assertEqual(len(launch["roles"]), len(roles))
            for public, recorded in zip(launch["roles"], roles, strict=True):
                self.assertEqual(public["catalogModelId"], recorded["catalogModelId"])
                self.assertEqual(public["canonicalModel"], recorded["canonicalModel"])
            self.assertEqual(sum(len(agent.calls) for agent in named.values()), 0)

    def test_forged_persisted_verified_state_does_not_authorize(self) -> None:
        flow_name = "deep-research"
        task = "forged catalogResolution must not authorize"
        setting = LAUNCHER.normalize_config(flow_name, {})
        with project_dir() as root:
            identity, humanize_home = resolve_complete_identity(root, flow_name)
            policy_path = root / "config/workflows/models.json"
            document = json.loads(policy_path.read_text(encoding="utf-8"))
            catalog = document["catalogResolution"]
            self.assertEqual(catalog["status"], "verified")
            self.assertEqual(catalog["source"], "authenticated-native-cli")
            self.assertTrue(catalog["verifiedRoles"])
            old_roles = []
            for recorded in identity["roles"]:
                old = {field: recorded[field] for field in OLD_LAUNCH_ROLE_FIELDS}
                old["model"] = recorded["canonicalModel"]
                old_roles.append(old)
            identity = {**identity, "roles": old_roles}
            named = agents_from_roles(old_roles)
            receipt = complete_outer_receipt(
                root, identity, flow_name, task, setting
            )
            with sealed_verify_env(root, receipt, humanize_home):
                with self.assertRaisesRegex(
                    REFERENCE_HELPER.LaunchContractError,
                    "launchReceipt|canonical launch receipt|fields do not match",
                ):
                    REFERENCE_HELPER.verify_launch(
                        flow_name, named, FLOW_ROLES[flow_name], task, setting
                    )
            self.assertEqual(sum(len(agent.calls) for agent in named.values()), 0)

    def test_stripped_nested_receipt_is_rejected_before_model_call(self) -> None:
        flow_name = "deep-research"
        task = "stripped nested receipt"
        setting = LAUNCHER.normalize_config(flow_name, {})
        with project_dir() as root:
            identity, humanize_home = resolve_complete_identity(root, flow_name)
            stripped = json.loads(json.dumps(identity["roles"]))
            stripped[0].pop("launchReceipt")
            identity = {**identity, "roles": stripped}
            named = agents_from_roles(stripped)
            receipt = complete_outer_receipt(
                root, identity, flow_name, task, setting
            )
            with sealed_verify_env(root, receipt, humanize_home):
                with self.assertRaisesRegex(
                    REFERENCE_HELPER.LaunchContractError,
                    "launchReceipt|fields do not match|canonical launch receipt",
                ):
                    REFERENCE_HELPER.verify_launch(
                        flow_name, named, FLOW_ROLES[flow_name], task, setting
                    )
            self.assertEqual(sum(len(agent.calls) for agent in named.values()), 0)

    def test_altered_nested_receipt_is_rejected_before_model_call(self) -> None:
        flow_name = "deep-research"
        task = "altered nested receipt"
        setting = LAUNCHER.normalize_config(flow_name, {})
        with project_dir() as root:
            identity, humanize_home = resolve_complete_identity(root, flow_name)
            altered = json.loads(json.dumps(identity["roles"]))
            nested = altered[0]["launchReceipt"]
            nested["request"]["semanticRole"] = "task"
            unsigned = {
                key: value
                for key, value in nested.items()
                if key != "receiptSha256"
            }
            nested["receiptSha256"] = canonical_sha256(unsigned)
            identity = {**identity, "roles": altered}
            named = agents_from_roles(altered)
            receipt = complete_outer_receipt(
                root, identity, flow_name, task, setting
            )
            with sealed_verify_env(root, receipt, humanize_home):
                with self.assertRaisesRegex(
                    REFERENCE_HELPER.LaunchContractError,
                    "request differs|canonical launch receipt|sealed launch role",
                ):
                    REFERENCE_HELPER.verify_launch(
                        flow_name, named, FLOW_ROLES[flow_name], task, setting
                    )
            self.assertEqual(sum(len(agent.calls) for agent in named.values()), 0)

    def test_concrete_id_drift_is_rejected_before_model_call(self) -> None:
        flow_name = "deep-research"
        task = "concrete catalog id drift"
        setting = LAUNCHER.normalize_config(flow_name, {})
        with project_dir() as root:
            identity, humanize_home = resolve_complete_identity(root, flow_name)
            drifted = json.loads(json.dumps(identity["roles"]))
            original = drifted[0]["catalogModelId"]
            replacement = (
                "gpt-5.6-luna" if original == "gpt-5.6-sol" else "gpt-5.6-sol"
            )
            drifted[0]["catalogModelId"] = replacement
            drifted[0]["model"] = replacement
            identity = {**identity, "roles": drifted}
            named = agents_from_roles(drifted)
            receipt = complete_outer_receipt(
                root, identity, flow_name, task, setting
            )
            with sealed_verify_env(root, receipt, humanize_home):
                with self.assertRaisesRegex(
                    REFERENCE_HELPER.LaunchContractError,
                    "catalogModelId|concrete catalog|sealed launch role|fresh evidence",
                ):
                    REFERENCE_HELPER.verify_launch(
                        flow_name, named, FLOW_ROLES[flow_name], task, setting
                    )
            self.assertEqual(sum(len(agent.calls) for agent in named.values()), 0)


if __name__ == "__main__":
    unittest.main()
