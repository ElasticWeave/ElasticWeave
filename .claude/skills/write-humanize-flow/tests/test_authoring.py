from __future__ import annotations

import asyncio
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
ASSET = SKILL / "assets/project/.humanize"
PROJECT_ROOT = SKILL.parents[2]
RESEARCH_FLOW_ASSET = (
    SKILL.parent / "deep-research-workflow/assets/project/.humanize/flows"
)
SCRIPTS = SKILL / "scripts"
ROLE_TEMPLATE = (
    SKILL.parent
    / "default-mode/assets/project/config/workflows/models.json"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_project_flows", SCRIPTS / "validate_project_flows.py"
)
assert SPEC is not None and SPEC.loader is not None
VALIDATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VALIDATE
SPEC.loader.exec_module(VALIDATE)

RUN_SPEC = importlib.util.spec_from_file_location(
    "run_default", SCRIPTS / "run_default.py"
)
assert RUN_SPEC is not None and RUN_SPEC.loader is not None
RUN_DEFAULT = importlib.util.module_from_spec(RUN_SPEC)
sys.modules[RUN_SPEC.name] = RUN_DEFAULT
RUN_SPEC.loader.exec_module(RUN_DEFAULT)


def project(root: Path) -> None:
    shutil.copytree(ASSET, root / ".humanize", symlinks=True)


def project_with_research_references(root: Path) -> None:
    project(root)
    for name in ("_research_reference.py", "auto_research.py", "deep_research.py"):
        shutil.copyfile(
            RESEARCH_FLOW_ASSET / name,
            root / ".humanize/flows" / name,
        )


def scheduler_namespace(path: Path | None = None) -> dict[str, object]:
    """Load the Scheduler source without replacing its protocol logic."""
    agents_module = types.ModuleType("hmz.agents")
    agents_module.Unrecoverable = FakeUnrecoverable
    flows_module = types.ModuleType("hmz.flows")
    flows_module.Agent = object
    flows_module.AgentDefaults = lambda **values: values
    flows_module.Unrecoverable = FakeUnrecoverable
    flows_module.flow = lambda function: function
    flows_module.load = lambda _name: None
    pydantic_module = types.ModuleType("pydantic")

    class BaseModel:
        def __init__(self, **values: object) -> None:
            for name, value in values.items():
                setattr(self, name, value)

    pydantic_module.BaseModel = BaseModel
    pydantic_module.ConfigDict = lambda **values: values
    pydantic_module.Field = lambda *, default, **_bounds: default
    with mock.patch.dict(
        sys.modules,
        {
            "hmz.agents": agents_module,
            "hmz.flows": flows_module,
            "pydantic": pydantic_module,
        },
    ):
        return runpy.run_path(
            str(path or (ASSET / "flows/scheduler/__init__.py"))
        )


def fake_hmz(root: Path, capture: Path) -> Path:
    binary = root / "hmz"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        f"Path({str(capture)!r}).write_text(json.dumps({{'argv': sys.argv[1:], 'digest': os.environ.get('HUMANIZE_SLOT_SHA256')}}))\n",
        encoding="utf-8",
    )
    binary.chmod(0o755)
    return binary


class FakeExecutionContext:
    """Launcher-level double; execution_context has its own adversarial suite."""

    def __init__(self, *, pinned, sources, launcher_environment=None, source_environment=None, **_):
        self.pinned = pinned
        self.sources = sources
        self.environment = dict(launcher_environment or {})
        self.environment.update({name: "101" for name in (source_environment or {})})
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


class FakeUnrecoverable(subprocess.CalledProcessError):
    """Test-only stand-in for Humanize's typed no-retry failure."""


def marker_slot(marker: Path, value: str) -> bytes:
    return (
        '"""A deterministic test slot."""\n'
        "from __future__ import annotations\n"
        "from pathlib import Path\n"
        "from typing import NamedTuple\n"
        "from hmz.flows import Agent\n"
        "from hmz.flows import flow\n"
        "class Agents(NamedTuple):\n"
        " executor: Agent\n"
        " reviewer: Agent\n"
        "@flow\n"
        "def run(agents: Agents, task: str) -> None:\n"
        f" Path({str(marker)!r}).write_text({value!r}, encoding='utf-8')\n"
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def cycle_decision_payload(
    disposition: str = "performed",
    risks: list[str] | None = None,
    *,
    reward_hacking_risks: list[str] | None = None,
    expected_outcome: str = "the bounded child returns evidence",
    expected_measurement: str = "the declared acceptance measurement is recorded",
    strategy: str = "explore",
) -> bytes:
    """Build the current strict scheduler decision fixture."""
    return (
        json.dumps(
            {
                "schema": "humanize-iteration-decision/v3",
                "idea_review_disposition": disposition,
                "risk_triggers": risks or [],
                "reward_hacking_risks": reward_hacking_risks or [],
                "expected_outcome": expected_outcome,
                "expected_measurement": expected_measurement,
                "strategy": strategy,
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def write_pm_issue_snapshot(
    root: Path,
    *,
    expiry: str = "2099-01-01T00:00:00Z",
    fallback: str = "pause",
) -> Path:
    snapshot = {
        "iteration_mode_selected": "human_guided",
        "iteration_mode_effective": "human_guided",
        "human_checkin_lease_minutes": "60",
        "human_unavailable_fallback": fallback,
        "human_checkin_lease_expires_at": expiry,
        "human_checkin_lease_status": "active",
        "iteration_mode_decision_at": "2030-01-01T00:00:00Z",
    }
    snapshot["iteration_mode_snapshot_sha256"] = _canonical_sha256(snapshot)
    issue = root / ".pm/issues/m1/i1.md"
    issue.parent.mkdir(parents=True)
    issue.write_text(
        "---\n"
        + "\n".join(f"{key}: {value}" for key, value in snapshot.items())
        + "\n---\n\n## Acceptance Criteria\n\nBound context.\n",
        encoding="utf-8",
    )
    return issue


CURSOR_CATALOG_TIP = (
    "Tip: use --model <id> (or /model <id> in interactive mode) to switch. "
    "Parameterized models also accept quoted overrides, e.g. --model "
    "'claude-opus-4-8[context=1m,effort=high,fast=false]'."
)


def canned_private_catalog_runner(
    argv: tuple[str, ...],
) -> tuple[int, bytes, bytes]:
    """Private in-process catalog fixture; production has no injection surface."""
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
                                    for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
                                ],
                            }
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


def verified_role_document() -> dict[str, object]:
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
            "verifiedRoleIdentitiesSha256": _canonical_sha256(identities),
        },
    )
    return document


def role_policy(root: Path, *, verified: bool = True, same_provider: bool = False) -> Path:
    path = root / "config/workflows/models.json"
    path.parent.mkdir(parents=True)
    document = verified_role_document() if verified else json.loads(
        ROLE_TEMPLATE.read_text(encoding="utf-8")
    )
    if same_provider:
        document["roles"]["advisor"]["upstreamProviderFamily"] = "openai"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class AuthoringTest(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._canonical_resolver = RUN_DEFAULT.resolve_launch_spec

        def resolve_with_private_catalog(
            *args: object, **kwargs: object
        ) -> dict[str, object]:
            supplied_runner = kwargs.pop("catalog_runner", None)
            if supplied_runner is not None:
                raise AssertionError("tests must use their private catalog runner")
            return self._canonical_resolver(
                *args,
                **kwargs,
                catalog_runner=canned_private_catalog_runner,
            )

        patcher = mock.patch.object(
            RUN_DEFAULT,
            "resolve_launch_spec",
            side_effect=resolve_with_private_catalog,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fresh_default_activations(
        self, raw_document: bytes
    ) -> list[dict[str, object]]:
        document = RUN_DEFAULT._document(raw_document)
        posture = document["reasoningPolicy"]["defaultPosture"]
        plans = RUN_DEFAULT._activation_plans(
            document, "frontier_execute", "advisor", posture
        )
        for plan in plans:
            plan["receipt"] = self._canonical_resolver(
                raw_document,
                task_class=plan["taskClass"],
                reasoning_posture=posture,
                semantic_role=plan["semanticRole"],
                review_mode=plan["reviewMode"],
                writer_role=plan["writerRole"],
                catalog_runner=canned_private_catalog_runner,
            )
        return plans

    def test_skill_discovery_summary_exposes_scheduler_protocol(self) -> None:
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        description = skill.split("---", 2)[1]
        for expected in (
            "humanize2",
            "scheduler or flow source changes",
        ):
            self.assertIn(expected, description)

    def test_scheduler_asset_is_one_directory_flow_entry_point(self) -> None:
        entry = ASSET / "flows/scheduler/__init__.py"
        self.assertTrue(entry.is_file())
        self.assertFalse((ASSET / "flows/scheduler.py").exists())
        source = entry.read_text(encoding="utf-8")
        self.assertIn(
            "Do not invoke .humanize/run_scheduler.py, hmz, or hmz -f local/scheduler",
            source,
        )

    def test_scheduler_launcher_replays_repository_defaults_in_role_order(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        expected = (
            ("scheduler", "codex/gpt-6-astra:max"),
            ("executor", "claude/claude-sonnet-5:max"),
            ("reviewer", "codex/gpt-6-astra:max"),
            ("planner", "claude/claude-opus-5:max"),
            ("preflight", "claude/claude-opus-5:xhigh"),
            ("collector", "codex/gpt-5.6-luna:max"),
            ("counterevidence", "codex/gpt-6-astra:max"),
            ("reducer", "codex/gpt-6-astra:max"),
            ("knowledge", "codex/gpt-5.6-terra:max"),
            ("applicability", "claude/claude-opus-5:xhigh"),
            ("experimenter", "codex/gpt-6-astra:xhigh"),
        )
        self.assertEqual(expected, launcher["DEFAULT_AGENTS"])

        self.assertEqual("local/scheduler", launcher["FLOW"])
        self.assertEqual(
            Path(".humanize/flows/scheduler/__init__.py"), launcher["FLOW_ENTRY"]
        )
        argv = ["hmz", "-f", "local/scheduler"]
        for _, agent in expected:
            argv.extend(("-a", agent))
        self.assertEqual(tuple(argv), launcher["scheduler_argv"]())

    def test_scheduler_launcher_check_reports_automatic_defaults_without_exec(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        pinned = Path("/audited/humanize/.venv/bin/hmz")
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(launcher["os"], "execve") as execute,
            mock.patch.object(launcher["os"], "chdir") as change_directory,
            mock.patch.object(launcher["os"], "close") as close,
            mock.patch("builtins.print") as emit,
            mock.patch.dict(
                launcher["main"].__globals__,
                {"resolve_hmz": mock.Mock(return_value=pinned)},
            ),
        ):
            launcher["main"](["--check"])

        report = json.loads(emit.call_args.args[0])
        self.assertEqual("humanize-scheduler-launch-check/v1", report["schema"])
        self.assertEqual("local/scheduler", report["flow"])
        self.assertEqual(0, report["modelCalls"])
        self.assertEqual("off", report["telemetry"])
        self.assertEqual("off", report["nativeGoals"])
        self.assertEqual("off", report["wholeRunContainer"])
        self.assertEqual("not-offered", report["callbackTools"])
        self.assertEqual("disabled", report["sourceBytecode"])
        self.assertEqual(
            ".humanize/scheduler/runtime", report["runtimeHome"]
        )
        self.assertEqual("project-isolated", report["persistedBehavior"])
        self.assertEqual(
            [role for role, _ in launcher["DEFAULT_AGENTS"]],
            [agent["role"] for agent in report["agents"]],
        )
        self.assertEqual(
            [spec for _, spec in launcher["DEFAULT_AGENTS"]],
            [agent["spec"] for agent in report["agents"]],
        )
        self.assertEqual(list(launcher["scheduler_argv"](pinned)), report["argv"])
        execute.assert_not_called()
        change_directory.assert_not_called()
        close.assert_not_called()

    def test_scheduler_launcher_main_wires_guards_context_and_child_environment(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        pinned = Path("/audited/humanize/.venv/bin/hmz")
        project_root = ASSET.parent
        child_environment = {"SEALED": "yes", launcher["SENTRY_ENV"]: "off"}
        reject = mock.Mock()
        acquire = mock.Mock(return_value=41)
        runtime_home = project_root / ".humanize/scheduler/runtime"
        prepare_runtime = mock.Mock(return_value=runtime_home)
        build = mock.Mock(return_value={"schema": "test-context/v1"})
        seal = mock.Mock(return_value=(42, "sha256:" + "a" * 64))
        launch_environment = mock.Mock(return_value=child_environment)
        with (
            mock.patch.dict(os.environ, {"PATH": "/bin"}, clear=True),
            mock.patch.object(launcher["os"], "execve") as execute,
            mock.patch.object(launcher["os"], "chdir") as change_directory,
            mock.patch.object(launcher["os"], "close") as close,
            mock.patch.dict(
                launcher["main"].__globals__,
                {
                    "resolve_hmz": mock.Mock(return_value=pinned),
                    "_reject_recursive_launch": reject,
                    "_acquire_owner_lock": acquire,
                    "_prepare_runtime_home": prepare_runtime,
                    "build_invocation_context": build,
                    "_sealed_context": seal,
                    "_launch_environment": launch_environment,
                },
            ),
        ):
            launcher["main"]([])

        reject.assert_called_once_with(os.environ)
        acquire.assert_called_once_with(project_root)
        prepare_runtime.assert_called_once_with(project_root)
        build.assert_called_once_with(
            project_root,
            pm_issue=None,
            human_feedback=None,
        )
        seal.assert_called_once_with({"schema": "test-context/v1"})
        launch_environment.assert_called_once_with(
            os.environ,
            42,
            "sha256:" + "a" * 64,
            runtime_home,
        )
        change_directory.assert_called_once_with(project_root)
        execute.assert_called_once_with(
            pinned,
            list(launcher["scheduler_argv"](pinned)),
            child_environment,
        )
        self.assertEqual([mock.call(42), mock.call(41)], close.call_args_list)

    def test_scheduler_launcher_disables_telemetry_only_for_its_process(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        original = {"PATH": "/bin", launcher["SENTRY_ENV"]: "on"}
        runtime_home = Path("/project/.humanize/scheduler/runtime")
        launched = launcher["_launch_environment"](
            original, 17, "sha256:" + "b" * 64, runtime_home
        )
        self.assertEqual("on", original[launcher["SENTRY_ENV"]])
        self.assertEqual("off", launched[launcher["SENTRY_ENV"]])
        self.assertEqual("17", launched[launcher["CONTEXT_FD_ENV"]])
        self.assertEqual("sha256:" + "b" * 64, launched[launcher["CONTEXT_SHA_ENV"]])
        self.assertEqual(
            str(runtime_home), launched[launcher["HUMANIZE_HOME_ENV"]]
        )

    def test_scheduler_launcher_isolates_persisted_humanize_behavior_per_project(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".humanize/scheduler").mkdir(parents=True)
            runtime_home = launcher["_prepare_runtime_home"](root)

            self.assertEqual(root / launcher["RUNTIME_HOME"], runtime_home)
            self.assertTrue(runtime_home.is_dir())
            self.assertFalse(runtime_home.is_symlink())
            self.assertEqual(0o700, runtime_home.stat().st_mode & 0o777)

            global_home = root / "global-humanize-home"
            original = {
                launcher["HUMANIZE_HOME_ENV"]: str(global_home),
                launcher["PYTHONDONTWRITEBYTECODE_ENV"]: "0",
                "PATH": "/bin",
            }
            launched = launcher["_launch_environment"](
                original,
                17,
                "sha256:" + "b" * 64,
                runtime_home,
            )
            self.assertEqual(str(global_home), original[launcher["HUMANIZE_HOME_ENV"]])
            self.assertEqual(
                str(runtime_home), launched[launcher["HUMANIZE_HOME_ENV"]]
            )
            self.assertEqual(
                "0", original[launcher["PYTHONDONTWRITEBYTECODE_ENV"]]
            )
            self.assertEqual(
                "1", launched[launcher["PYTHONDONTWRITEBYTECODE_ENV"]]
            )

            runtime_home.rmdir()
            runtime_home.symlink_to(global_home, target_is_directory=True)
            with self.assertRaisesRegex(SystemExit, "runtime home"):
                launcher["_prepare_runtime_home"](root)

    def test_scheduler_launcher_rejects_recursive_sealed_context(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        reject = launcher["_reject_recursive_launch"]
        reject({})
        for environment in (
            {launcher["CONTEXT_FD_ENV"]: "17"},
            {launcher["CONTEXT_SHA_ENV"]: "sha256:" + "a" * 64},
        ):
            with self.subTest(environment=environment), self.assertRaisesRegex(
                SystemExit, "recursive Humanize scheduler launch"
            ):
                reject(environment)

    def test_scheduler_launcher_holds_one_nonblocking_project_owner_lock(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".humanize/scheduler").mkdir(parents=True)
            first = launcher["_acquire_owner_lock"](root)
            try:
                self.assertTrue(os.get_inheritable(first))
                with self.assertRaisesRegex(SystemExit, "already active"):
                    launcher["_acquire_owner_lock"](root)
            finally:
                os.close(first)
            second = launcher["_acquire_owner_lock"](root)
            os.close(second)
            lock = root / launcher["OWNER_LOCK"]
            self.assertTrue(lock.is_file())
            self.assertEqual(0, lock.stat().st_mode & 0o077)

    def test_scheduler_launcher_is_directly_executable(self) -> None:
        self.assertTrue((ASSET / "run_scheduler.py").stat().st_mode & 0o100)
        self.assertTrue(
            (PROJECT_ROOT / ".humanize/run_scheduler.py").stat().st_mode & 0o100
        )

    def test_scheduler_launcher_requires_strong_source_install_validation(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pinned = root / ".venv/bin/hmz"
            pinned.parent.mkdir(parents=True)
            pinned.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            pinned.chmod(0o755)
            with self.assertRaisesRegex(SystemExit, "failed audited source validation"):
                launcher["resolve_hmz"](
                    {"HUMANIZE2_HOME": str(root), "PATH": ""}
                )

            path_binary = root / "pipx/venvs/hmz/bin/hmz"
            path_binary.parent.mkdir(parents=True)
            path_binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path_binary.chmod(0o755)
            with self.assertRaisesRegex(SystemExit, "failed audited source validation"):
                launcher["resolve_hmz"](
                    {
                        "HUMANIZE2_HOME": str(root / "missing"),
                        "PATH": str(path_binary.parent),
                    }
                )

    def test_scheduler_pm_issue_snapshot_and_feedback_are_runtime_bound(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        scheduler = scheduler_namespace()
        now = scheduler["_parse_utc_instant"]("2030-01-01T00:05:00Z")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issue = write_pm_issue_snapshot(root)
            (root / ".pm/config.json").write_text(
                json.dumps(
                    {
                        "iteration_mode": "autonomous",
                        "human_checkin_lease_minutes": None,
                        "human_checkin_lease_expires_at": None,
                        "human_unavailable_fallback": "pause",
                    }
                ),
                encoding="utf-8",
            )
            context = launcher["build_invocation_context"](
                root, pm_issue=issue.relative_to(root), human_feedback=None
            )
            policy = scheduler["_resolve_iteration_policy"](
                root, now=now, invocation_context=context
            )
            self.assertEqual("pm_issue", policy.policy_source)
            self.assertEqual("human_guided", policy.effective_mode)

            scheduler["_write_observation_checkpoint"](
                root,
                task="bound task",
                status="awaiting_human_feedback",
                round_number=1,
                target=".humanize/flows/slot.py",
                outcome="completed",
                policy=policy,
                decision=scheduler["CycleDecision"]("skipped_human_guided", ()),
                invocation_context=context,
            )
            with self.assertRaisesRegex(RuntimeError, "human feedback"):
                scheduler["_validated_human_feedback"](
                    root, "bound task", policy, context
                )

            resumed = launcher["build_invocation_context"](
                root,
                pm_issue=issue.relative_to(root),
                human_feedback="replicate the measurement before widening",
            )
            second_issue = root / ".pm/issues/m1/i2.md"
            second_issue.write_bytes(issue.read_bytes())
            cross_issue = launcher["build_invocation_context"](
                root,
                pm_issue=second_issue.relative_to(root),
                human_feedback="replicate the measurement before widening",
            )
            claim = scheduler["_validated_human_feedback"](
                root, "bound task", policy, resumed
            )
            self.assertIsNotNone(claim)
            assert claim is not None
            self.assertEqual(
                "replicate the measurement before widening",
                claim.human_feedback,
            )
            self.assertEqual(
                claim,
                scheduler["_validated_human_feedback"](
                    root, "bound task", policy, resumed, claim
                ),
            )
            marker = root / claim.consumption_path
            alias = marker.with_suffix(".hardlink")
            os.link(marker, alias)
            with self.assertRaisesRegex(RuntimeError, "single-link"):
                scheduler["_validated_human_feedback"](
                    root, "bound task", policy, resumed, claim
                )
            alias.unlink()

            marker_parent = marker.parent
            saved_parent = marker_parent.with_name("feedback-consumed-saved")
            outside_parent = root / "outside-feedback-consumed"
            outside_parent.mkdir()
            marker_parent.rename(saved_parent)
            marker_parent.symlink_to(outside_parent, target_is_directory=True)
            try:
                with self.assertRaisesRegex(RuntimeError, "directory"):
                    scheduler["_validated_human_feedback"](
                        root, "bound task", policy, resumed, claim
                    )
            finally:
                marker_parent.unlink()
                saved_parent.rename(marker_parent)
            with self.assertRaisesRegex(RuntimeError, "checkpoint ID"):
                scheduler["_feedback_consumption_path"]("sha256:../escape")

            with self.assertRaisesRegex(RuntimeError, "already consumed"):
                scheduler["_validated_human_feedback"](
                    root, "bound task", policy, resumed
                )
            replayed = launcher["build_invocation_context"](
                root,
                pm_issue=issue.relative_to(root),
                human_feedback="replicate the measurement before widening",
            )
            with self.assertRaisesRegex(RuntimeError, "already consumed"):
                scheduler["_validated_human_feedback"](
                    root, "bound task", policy, replayed
                )
            with self.assertRaisesRegex(RuntimeError, "planning context"):
                scheduler["_validated_human_feedback"](
                    root,
                    "bound task",
                    scheduler["_resolve_iteration_policy"](
                        root, now=now, invocation_context=cross_issue
                    ),
                    cross_issue,
                )
            with self.assertRaisesRegex(RuntimeError, "planning context"):
                scheduler["_validated_human_feedback"](
                    root, "different task", policy, resumed, claim
                )
            checkpoint = (
                root
                / "workflow-output/humanize/scheduler/observation-checkpoint.json"
            )
            checkpoint.write_bytes(checkpoint.read_bytes() + b" ")
            with self.assertRaisesRegex(RuntimeError, "checkpoint"):
                scheduler["_validated_human_feedback"](
                    root, "bound task", policy, resumed, claim
                )

            issue.write_bytes(issue.read_bytes() + b"\n")
            with self.assertRaisesRegex(RuntimeError, "PM issue"):
                scheduler["_resolve_iteration_policy"](
                    root, now=now, invocation_context=resumed
                )

    def test_scheduler_sealed_context_and_legacy_issue_default_are_consumed(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        scheduler = scheduler_namespace()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issue = root / ".pm/issues/m1/legacy.md"
            issue.parent.mkdir(parents=True)
            issue.write_text(
                "---\nid: legacy\nstatus: todo\n---\n\nLegacy issue.\n",
                encoding="utf-8",
            )
            context = launcher["build_invocation_context"](
                root, pm_issue=issue.relative_to(root), human_feedback=None
            )
            self.assertTrue(context["iteration_policy_legacy_default"])
            descriptor, digest_value = launcher["_sealed_context"](context)
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        launcher["CONTEXT_FD_ENV"]: str(descriptor),
                        launcher["CONTEXT_SHA_ENV"]: digest_value,
                    },
                    clear=False,
                ):
                    loaded = scheduler["_load_invocation_context"](root)
                policy = scheduler["_resolve_iteration_policy"](
                    root,
                    now=scheduler["_parse_utc_instant"]("2030-01-01T00:00:00Z"),
                    invocation_context=loaded,
                )
            finally:
                os.close(descriptor)
            self.assertEqual("autonomous", policy.effective_mode)
            self.assertTrue(policy.legacy_default)
            self.assertEqual("pm_issue", policy.policy_source)

    def test_slot_validator_rejects_nested_import_time_execution_without_running_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "ran"
            prefix = (
                "from __future__ import annotations\n"
                "from pathlib import Path\n"
                "from typing import NamedTuple\n"
                "from hmz.flows import Agent\n"
                "from hmz.flows import flow\n"
                "class Agents(NamedTuple):\n executor: Agent\n reviewer: Agent\n"
            )
            run = "@flow\ndef run(agents: Agents, task: str) -> None:\n pass\n"
            cases = {
                "container-assignment": (
                    prefix + f"value = [Path({str(marker)!r}).write_text('ran')]\n" + run
                ),
                "top-level-if": (
                    prefix + f"if True:\n Path({str(marker)!r}).write_text('ran')\n" + run
                ),
                "function-default": (
                    prefix
                    + f"def helper(value=Path({str(marker)!r}).write_text('ran')):\n return value\n"
                    + run
                ),
                "function-kw-default": (
                    prefix
                    + f"def helper(*, value=Path({str(marker)!r}).write_text('ran')):\n return value\n"
                    + run
                ),
                "function-decorator": (
                    prefix
                    + f"@Path({str(marker)!r}).write_text('ran')\ndef helper():\n pass\n"
                    + run
                ),
                "function-annotation": (
                    prefix
                    + f"def helper(value: Path({str(marker)!r}).write_text('ran')):\n return value\n"
                    + run
                ),
                "class-body": (
                    prefix
                    + f"class Unsafe:\n value = Path({str(marker)!r}).write_text('ran')\n"
                    + run
                ),
                "class-decorator": (
                    prefix
                    + f"@Path({str(marker)!r}).write_text('ran')\nclass Unsafe:\n pass\n"
                    + run
                ),
                "comprehension": (
                    prefix
                    + f"value = [Path({str(marker)!r}).write_text('ran') for _ in [0]]\n"
                    + run
                ),
            }
            for name, source in cases.items():
                with self.subTest(name=name):
                    with self.assertRaises(VALIDATE.FlowValidationError):
                        VALIDATE.validate_slot_bytes(source.encode("utf-8"))
                    self.assertFalse(marker.exists(), "validator executed candidate source")

    def test_slot_roles_are_an_ordered_subset_of_the_fixed_scheduler_pool(self) -> None:
        def source(fields: str) -> bytes:
            return (
                "from __future__ import annotations\n"
                "from typing import NamedTuple\n"
                "from hmz.flows import Agent\n"
                "from hmz.flows import flow\n"
                "class Agents(NamedTuple):\n"
                f"{fields}"
                "@flow\n"
                "def run(agents: Agents, task: str) -> None:\n"
                "    pass\n"
            ).encode("utf-8")

        admitted = source(
            "    planner: Agent\n"
            "    collector: Agent\n"
            "    experimenter: Agent\n"
            "    reviewer: Agent\n"
        )
        VALIDATE.validate_slot_bytes(admitted)
        self.assertEqual(
            ("planner", "collector", "experimenter", "reviewer"),
            VALIDATE.slot_agent_roles(admitted),
        )
        for fields, message in (
            ("    executor: Agent\n", "reviewer"),
            ("    writer: Agent\n    reviewer: Agent\n", "unsupported"),
            ("    executor: object\n    reviewer: Agent\n", "Agent"),
        ):
            with self.subTest(fields=fields), self.assertRaisesRegex(
                VALIDATE.FlowValidationError, message
            ):
                VALIDATE.validate_slot_bytes(source(fields))

    def test_scheduler_and_validator_use_the_same_child_role_pool(self) -> None:
        tree = ast.parse((ASSET / "flows/scheduler/__init__.py").read_text(encoding="utf-8"))
        assignments = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_CHILD_AGENT_ROLES"
                for target in node.targets
            )
        ]
        self.assertEqual(1, len(assignments))
        self.assertEqual(
            VALIDATE.SLOT_AGENT_ROLE_POOL,
            ast.literal_eval(assignments[0].value),
        )

    def test_default_validator_rejects_import_time_execution_without_running_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "default-ran"
            source = (ASSET / "flows/default.py").read_text(encoding="utf-8")
            source = source.replace(
                "from typing import NamedTuple\n",
                "from typing import NamedTuple\n"
                f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
            )
            with self.assertRaises(VALIDATE.FlowValidationError):
                VALIDATE.validate_default_bytes(source.encode("utf-8"))
            self.assertFalse(marker.exists(), "validator executed malicious default source")

    def test_scheduler_asset_is_materialized_at_the_repository_root(self) -> None:
        for relative in (
            Path("README.md"),
            Path("run_scheduler.py"),
            Path("flows/default.py"),
            Path("flows/slot.py"),
            Path("flows/versions/slot-89f57a85f264e13096be7fdaee2fa74945a410eeb1aff47f3674b5440e55b03d.py"),
            Path("flows/versions/slot-e3bcbc9a3bad71d157aab519da780403e48729da13b80b9be5deafcc9f88aacf.py"),
            Path("flows/scheduler/__init__.py"),
            Path("scheduler/.gitignore"),
            Path("scheduler/README.md"),
        ):
            with self.subTest(relative=relative.as_posix()):
                self.assertEqual(
                    (ASSET / relative).read_bytes(),
                    (PROJECT_ROOT / ".humanize" / relative).read_bytes(),
                )
        for root in (ASSET, PROJECT_ROOT / ".humanize"):
            slot = root / "flows/slot.py"
            self.assertTrue(slot.is_symlink())
            target = os.readlink(slot)
            self.assertRegex(target, r"^versions/slot-[0-9a-f]{64}\.py$")
            version = slot.parent / target
            self.assertEqual(
                target,
                f"versions/slot-{hashlib.sha256(version.read_bytes()).hexdigest()}.py",
            )

    def test_scheduler_decision_parser_is_strict_and_slot_only(self) -> None:
        agents_module = types.ModuleType("hmz.agents")
        agents_module.Unrecoverable = FakeUnrecoverable
        flows_module = types.ModuleType("hmz.flows")
        flows_module.Agent = object
        flows_module.AgentDefaults = lambda **values: values
        flows_module.Unrecoverable = FakeUnrecoverable
        flows_module.flow = lambda function: function
        flows_module.load = lambda _name: None
        runner_module = types.ModuleType("hmz.runner")
        pydantic_module = types.ModuleType("pydantic")
        pydantic_module.BaseModel = type("BaseModel", (), {})
        pydantic_module.ConfigDict = lambda **values: values
        pydantic_module.Field = lambda *, default, **_bounds: default
        with mock.patch.dict(
            sys.modules,
            {
                "hmz.agents": agents_module,
                "hmz.flows": flows_module,
                "hmz.runner": runner_module,
                "pydantic": pydantic_module,
            },
        ):
            scheduler = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))

        self.assertEqual(scheduler["_parse_decision"](b"false\n"), (False, None))
        self.assertEqual(
            scheduler["_parse_decision"](b"true\n.humanize/flows/slot.py\n"),
            (True, ".humanize/flows/slot.py"),
        )
        rejected = (
            b"pending\n",
            b"False\n",
            b"false\nextra\n",
            b"true\n",
            b"true\n/absolute.py\n",
            b"true\n../outside.py\n",
            b"true\n.humanize/flows/scheduler.py\n",
            b"true\n.humanize/flows/default.py\n",
            b"true\n.humanize/flows/other.py\n",
            b"true\n.humanize/flows/slot.py\nextra\n",
        )
        for payload in rejected:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                scheduler["_parse_decision"](payload)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            validator = (
                root
                / ".claude/skills/write-humanize-flow/scripts/validate_project_flows.py"
            )
            validator.parent.mkdir(parents=True)
            shutil.copyfile(SCRIPTS / "validate_project_flows.py", validator)
            sentinel = root / "sentinel"
            sentinel.write_text("false\n", encoding="utf-8")
            dispatch = root / ".humanize/scheduler/dispatch.txt"
            dispatch.symlink_to(sentinel)
            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                scheduler["_read_state"](root, "dispatch.txt", 4096)
            dispatch.unlink()
            os.mkfifo(dispatch)
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                scheduler["_read_state"](root, "dispatch.txt", 4096)
            dispatch.unlink()

            slot = root / ".humanize/flows/slot.py"
            slot.unlink()
            slot.symlink_to(sentinel)
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                scheduler["_read_relative"](
                    root, ".humanize/flows/slot.py", "selected slot", 4096
                )
            slot.unlink()
            slot.symlink_to(os.readlink(ASSET / "flows/slot.py"))

            held_validator, held_roles = scheduler["_load_slot_validator"](root)
            validator.write_text(
                "import hashlib\n"
                "def validate_slot_bytes(payload):\n"
                "    return hashlib.sha256(payload).hexdigest()\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "slot validator rejected"):
                scheduler["_validate_slot"](
                    root, b"not an admitted flow\n", held_validator, held_roles
                )
            shutil.copyfile(SCRIPTS / "validate_project_flows.py", validator)

            class SilentScheduler:
                backend = "codex"

                def __call__(self, _prompt: str) -> str:
                    raise AssertionError("async scheduler flow must use aturn")

                async def aturn(self, _prompt: str) -> str:
                    return "returned without a decision"

            class WrongScheduler(SilentScheduler):
                backend = "claude"

            prior = Path.cwd()
            try:
                os.chdir(root)
                options = scheduler["Config"]()
                with self.assertRaisesRegex(RuntimeError, "exact lowercase true or false"):
                    asyncio.run(
                        scheduler["run"](
                            scheduler["Agents"](
                                SilentScheduler(), *[object() for _ in range(10)]
                            ),
                            "bounded task",
                            options,
                        )
                    )
                with self.assertRaisesRegex(RuntimeError, "native codex backend"):
                    asyncio.run(
                        scheduler["run"](
                            scheduler["Agents"](
                                WrongScheduler(), *[object() for _ in range(10)]
                            ),
                            "bounded task",
                            options,
                        )
                    )
                self.assertEqual(50, options.max_rounds)
                self.assertEqual(4, options.scheduler_retry_rounds)
                self.assertFalse(hasattr(options, "scheduler_retry_backoff_seconds"))
            finally:
                os.chdir(prior)

    def test_scheduler_resolves_pm_iteration_policy_no_follow_and_fail_closed(self) -> None:
        scheduler = scheduler_namespace()
        resolve = scheduler["_resolve_iteration_policy"]
        now = scheduler["_parse_utc_instant"]("2030-01-01T00:00:00Z")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".pm").mkdir()

            legacy = resolve(root, now=now)
            self.assertEqual("autonomous", legacy.requested_mode)
            self.assertEqual("autonomous", legacy.effective_mode)
            self.assertEqual("not_applicable", legacy.lease_status)
            self.assertEqual("none", legacy.fallback_applied)
            self.assertFalse(legacy.pause_before_dispatch)

            config = root / ".pm/config.json"
            config.write_text(
                json.dumps({"debug": False, "ui_mode": "file"}), encoding="utf-8"
            )
            legacy_document = resolve(root, now=now)
            self.assertEqual("autonomous", legacy_document.effective_mode)
            self.assertEqual("not_applicable", legacy_document.lease_status)

            config.write_text(
                json.dumps(
                    {
                        "debug": False,
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2030-01-01T00:30:00Z",
                        "human_unavailable_fallback": "pause",
                    }
                ),
                encoding="utf-8",
            )
            active = resolve(root, now=now)
            self.assertEqual("human_guided", active.effective_mode)
            self.assertEqual("active", active.lease_status)
            self.assertFalse(active.pause_before_dispatch)

            config.write_text(
                json.dumps(
                    {
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2029-12-31T23:59:59Z",
                        "human_unavailable_fallback": "pause",
                    }
                ),
                encoding="utf-8",
            )
            paused = resolve(root, now=now)
            self.assertIsNone(paused.effective_mode)
            self.assertEqual("expired", paused.lease_status)
            self.assertEqual("pause", paused.fallback_applied)
            self.assertTrue(paused.pause_before_dispatch)

            config.write_text(
                json.dumps(
                    {
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2029-12-31T23:59:59Z",
                        "human_unavailable_fallback": "autonomous_review",
                    }
                ),
                encoding="utf-8",
            )
            fallback = resolve(root, now=now)
            self.assertEqual("human_guided", fallback.requested_mode)
            self.assertEqual("autonomous", fallback.effective_mode)
            self.assertEqual("autonomous_review", fallback.fallback_applied)
            self.assertFalse(fallback.pause_before_dispatch)

            malformed = (
                {"iteration_mode": "Fast"},
                {
                    "iteration_mode": "human_guided",
                    "human_checkin_lease_minutes": 4,
                    "human_checkin_lease_expires_at": "2030-01-01T00:30:00Z",
                    "human_unavailable_fallback": "pause",
                },
                {
                    "iteration_mode": "human_guided",
                    "human_checkin_lease_minutes": 60,
                    "human_checkin_lease_expires_at": "2030-01-01T00:30:00+00:00",
                    "human_unavailable_fallback": "pause",
                },
                {
                    "iteration_mode": "autonomous",
                    "human_checkin_lease_minutes": 60,
                    "human_checkin_lease_expires_at": "2030-01-01T00:30:00Z",
                    "human_unavailable_fallback": "pause",
                },
            )
            for document in malformed:
                config.write_text(json.dumps(document), encoding="utf-8")
                with self.subTest(document=document), self.assertRaises(RuntimeError):
                    resolve(root, now=now)

            sentinel = root / "outside-config.json"
            sentinel.write_text('{"iteration_mode": "autonomous"}', encoding="utf-8")
            config.unlink()
            config.symlink_to(sentinel)
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                resolve(root, now=now)
            config.unlink()
            os.link(sentinel, config)
            with self.assertRaisesRegex(RuntimeError, "single-link regular file"):
                resolve(root, now=now)
            config.unlink()
            (root / ".pm").rmdir()
            outside_pm = root / "outside-pm"
            outside_pm.mkdir()
            (root / ".pm").symlink_to(outside_pm, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "parent must be a real directory"):
                resolve(root, now=now)

    def test_scheduler_mode_prompts_bind_retained_gates_and_review_disposition(self) -> None:
        scheduler = scheduler_namespace()
        now = scheduler["_parse_utc_instant"]("2030-01-01T00:00:00Z")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".pm").mkdir()
            config = root / ".pm/config.json"
            config.write_text(
                json.dumps(
                    {
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2030-01-01T00:30:00Z",
                        "human_unavailable_fallback": "pause",
                    }
                ),
                encoding="utf-8",
            )
            guided = scheduler["_resolve_iteration_policy"](root, now=now)
            guided_prompt = scheduler["_scheduler_prompt"](
                "task", 1, 50, "none", "none", guided
            )
            guided_child = scheduler["_child_task"](
                "task",
                guided,
                scheduler["CycleDecision"]("skipped_human_guided", ()),
            )
            for expected in (
                "iteration_mode: human_guided",
                "minimum alignment/safety/authority/resource/measurement/falsifier validity gate",
                "code review",
                "alignment review",
                "evidence integrity",
                "terminal review",
                "idea_review_disposition",
                "anomaly_or_alternate_interpretation",
            ):
                self.assertIn(expected, guided_prompt)
                self.assertIn(expected, guided_child)

            config.write_text(
                json.dumps(
                    {
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2029-12-31T23:59:59Z",
                        "human_unavailable_fallback": "autonomous_review",
                    }
                ),
                encoding="utf-8",
            )
            fallback = scheduler["_resolve_iteration_policy"](root, now=now)
            fallback_prompt = scheduler["_scheduler_prompt"](
                "task", 1, 50, "none", "none", fallback
            )
            self.assertIn("effective iteration_mode: autonomous", fallback_prompt)
            self.assertIn("expired", fallback_prompt)
            self.assertIn("autonomous_review", fallback_prompt)
            self.assertIn("review-heavy", fallback_prompt)
            self.assertIn("idea and counterevidence review before child execution", fallback_prompt)
            self.assertNotIn("may skip idea-level logic review", fallback_prompt)

    def test_scheduler_pause_fallback_starts_no_model_or_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            (root / ".pm").mkdir()
            (root / ".pm/config.json").write_text(
                json.dumps(
                    {
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2000-01-01T00:00:00Z",
                        "human_unavailable_fallback": "pause",
                    }
                ),
                encoding="utf-8",
            )

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> None:
                    raise AssertionError("pause fallback started a model turn")

            namespace = scheduler_namespace(root / ".humanize/flows/scheduler/__init__.py")
            namespace["run"].__globals__["_load_slot_validator"] = lambda _root: (_ for _ in ()).throw(
                AssertionError("pause fallback loaded a child validator")
            )
            previous = Path.cwd()
            try:
                os.chdir(root)
                asyncio.run(
                    namespace["run"](
                        namespace["Agents"](Scheduler(), *[object() for _ in range(10)]),
                        "bounded task",
                        namespace["Config"](),
                    )
                )
            finally:
                os.chdir(previous)

            checkpoint = json.loads(
                (root / "workflow-output/humanize/scheduler/observation-checkpoint.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual("paused_human_unavailable", checkpoint["status"])
            self.assertFalse(checkpoint["next_planning_allowed"])
            self.assertIsNone(checkpoint["effective_iteration_mode"])
            self.assertEqual("pause", checkpoint["fallback_applied"])
            self.assertEqual(
                "false\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(encoding="utf-8"),
            )

    def test_scheduler_cycle_decision_matches_the_effective_mode(self) -> None:
        scheduler = scheduler_namespace()
        self.assertEqual(
            (
                "lease_expired",
                "human_unavailable",
                "contradiction",
                "ambiguity",
                "plateau_or_repeated_failure",
                "irreversible_or_high_cost",
                "safety_sensitive",
                "weak_proxy",
            ),
            scheduler["_RISK_TRIGGERS"],
        )
        now = scheduler["_parse_utc_instant"]("2030-01-01T00:00:00Z")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".pm").mkdir()
            config = root / ".pm/config.json"
            config.write_text(
                json.dumps(
                    {
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2030-01-01T00:30:00Z",
                        "human_unavailable_fallback": "pause",
                    }
                ),
                encoding="utf-8",
            )
            guided = scheduler["_resolve_iteration_policy"](root, now=now)
            skipped = scheduler["_parse_cycle_decision"](
                cycle_decision_payload("skipped_human_guided"),
                guided,
            )
            self.assertEqual("skipped_human_guided", skipped.idea_review_disposition)
            triggered = scheduler["_parse_cycle_decision"](
                cycle_decision_payload("triggered_by_risk", ["contradiction"]),
                guided,
            )
            self.assertEqual(("contradiction",), triggered.risk_triggers)
            with self.assertRaises(RuntimeError):
                scheduler["_parse_cycle_decision"](
                    cycle_decision_payload("skipped_human_guided", ["contradiction"]),
                    guided,
                )

            config.write_text(
                json.dumps(
                    {
                        "iteration_mode": "autonomous",
                        "human_checkin_lease_minutes": None,
                        "human_checkin_lease_expires_at": None,
                        "human_unavailable_fallback": "pause",
                    }
                ),
                encoding="utf-8",
            )
            autonomous = scheduler["_resolve_iteration_policy"](root, now=now)
            performed = scheduler["_parse_cycle_decision"](
                cycle_decision_payload("performed"),
                autonomous,
            )
            self.assertEqual("performed", performed.idea_review_disposition)
            with self.assertRaises(RuntimeError):
                scheduler["_parse_cycle_decision"](
                    cycle_decision_payload("skipped_human_guided"),
                    autonomous,
                )
            with self.assertRaisesRegex(RuntimeError, "did not write a cycle decision"):
                scheduler["_parse_cycle_decision"](scheduler["_PENDING"], autonomous)

    def test_scheduler_cycle_decision_requires_typed_expectation_and_strategy(self) -> None:
        scheduler = scheduler_namespace()
        policy = scheduler["_legacy_autonomous_policy"]()
        parsed = scheduler["_parse_cycle_decision"](
            cycle_decision_payload(
                expected_outcome="a reproducible child result",
                expected_measurement="the acceptance check passes",
                strategy="verify",
            ),
            policy,
        )
        self.assertEqual("a reproducible child result", parsed.expected_outcome)
        self.assertEqual("the acceptance check passes", parsed.expected_measurement)
        self.assertEqual("verify", parsed.strategy)
        valid = json.loads(cycle_decision_payload().decode("utf-8"))
        for field in ("expected_outcome", "expected_measurement", "strategy"):
            malformed = dict(valid)
            malformed.pop(field)
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                scheduler["_parse_cycle_decision"](
                    (json.dumps(malformed, separators=(",", ":")) + "\n").encode(),
                    policy,
                )
        malformed = dict(valid)
        malformed["strategy"] = "retry"
        with self.assertRaisesRegex(RuntimeError, "unknown strategy"):
            scheduler["_parse_cycle_decision"](
                (json.dumps(malformed, separators=(",", ":")) + "\n").encode(),
                policy,
            )

    def test_scheduler_cycle_ledger_escalates_repeated_deviation(self) -> None:
        scheduler = scheduler_namespace()
        decision = scheduler["CycleDecision"](
            "performed", (), "expected result", "acceptance measurement", "explore"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            first = scheduler["_record_history"](
                root,
                1,
                ".humanize/flows/slot.py",
                "failed: timeout while collecting evidence",
                decision=decision,
                task="ledger task",
            )
            self.assertIsNotNone(first)
            assert first is not None
            self.assertEqual("accessibility", first.deviation_class)
            self.assertEqual("substitute", first.next_strategy)
            self.assertFalse(first.structural_review_required)

            second = scheduler["_record_history"](
                root,
                2,
                ".humanize/flows/slot.py",
                "failed: timeout while collecting evidence",
                decision=decision,
                task="ledger task",
            )
            self.assertIsNotNone(second)
            assert second is not None
            self.assertEqual("repeated_failure", second.deviation_class)
            self.assertEqual("pivot", second.next_strategy)
            self.assertTrue(second.structural_review_required)
            self.assertEqual(2, second.repeated_deviation_count)
            self.assertNotIn(".humanize/scheduler/history.jsonl", second.evidence_lineage)
            self.assertNotIn("workflow-output/humanize/", second.evidence_lineage)
            self.assertTrue(
                any(
                    reference.startswith("observed:sha256:")
                    for reference in second.evidence_lineage
                )
            )

            with self.assertRaisesRegex(RuntimeError, "structural review"):
                scheduler["_enforce_structural_review"](
                    root,
                    scheduler["CycleDecision"](
                        "performed", (), "same result", "same measurement", "exploit"
                    ),
                )
            with self.assertRaisesRegex(RuntimeError, "not only its label"):
                scheduler["_enforce_structural_review"](
                    root,
                    scheduler["CycleDecision"](
                        "performed",
                        (),
                        "expected result",
                        "acceptance measurement",
                        "pivot",
                    ),
                )
            scheduler["_enforce_structural_review"](
                root,
                scheduler["CycleDecision"](
                    "performed", (), "new result", "new measurement", "pivot"
                ),
            )
            records = [
                json.loads(line)
                for line in (root / ".humanize/scheduler/history.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual("humanize-cycle-ledger/v1", records[-1]["cycle_schema"])
            self.assertTrue(records[-1]["structural_review_required"])

    def test_scheduler_cycle_ledger_isolated_between_sealed_pm_issues(self) -> None:
        scheduler = scheduler_namespace()
        decision = scheduler["CycleDecision"](
            "performed", (), "expected result", "acceptance measurement", "explore"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            scope = scheduler["_HISTORY_SCOPE"]
            first_token = scope.set("pm_issue:.pm/issues/m1/i1.md:sha256:" + "1" * 64)
            try:
                scheduler["_record_history"](
                    root,
                    1,
                    ".humanize/flows/slot.py",
                    "reward hacking marker: proxy gaming",
                    decision=decision,
                    task="issue one",
                )
                self.assertIsNotNone(scheduler["_latest_cycle_document"](root))
            finally:
                scope.reset(first_token)

            second_token = scope.set("pm_issue:.pm/issues/m1/i2.md:sha256:" + "2" * 64)
            try:
                self.assertIsNone(scheduler["_latest_cycle_document"](root))
                isolated = scheduler["_record_history"](
                    root,
                    1,
                    ".humanize/flows/slot.py",
                    "reward hacking marker: proxy gaming",
                    decision=decision,
                    task="issue two",
                )
                self.assertIsNotNone(isolated)
                assert isolated is not None
                self.assertEqual(1, isolated.repeated_deviation_count)
                records = [
                    json.loads(line)
                    for line in (root / ".humanize/scheduler/history.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                self.assertEqual(
                    "pm_issue:.pm/issues/m1/i1.md:sha256:" + "1" * 64,
                    records[0]["history_scope"],
                )
                self.assertEqual(
                    "pm_issue:.pm/issues/m1/i2.md:sha256:" + "2" * 64,
                    records[1]["history_scope"],
                )
            finally:
                scope.reset(second_token)

    def test_scheduler_child_assessment_cannot_hide_reward_hacking_evidence(self) -> None:
        scheduler = scheduler_namespace()
        decision = scheduler["CycleDecision"](
            "performed",
            (),
            "the measured result satisfies the task",
            "the task acceptance check passes",
            "verify",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "workflow-output/humanize/child/result.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "finding": "contradictory evidence shows proxy gaming and reward hacking",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            reference = (
                "workflow-output/humanize/child/result.json#sha256:"
                + hashlib.sha256(evidence.read_bytes()).hexdigest()
            )
            assessment = scheduler["ChildAssessment"](
                observed_outcome="completed",
                expected_outcome_status="met",
                expected_measurement_status="met",
                deviation_class="none",
                evidence_lineage=[reference],
                reward_hacking_signals=[],
            )
            observed = scheduler["_validated_child_assessment"](
                root, assessment
            )
            ledger = scheduler["_cycle_ledger"](
                b"",
                round_number=1,
                target=".humanize/flows/slot.py",
                outcome=observed,
                decision=decision,
                task="bounded task",
            )
            self.assertEqual("reward_hacking", ledger.deviation_class)
            self.assertTrue(ledger.structural_review_required)
            self.assertIn("reward hacking", ledger.reward_hacking_signals)
            self.assertIn(reference, ledger.evidence_lineage)

            benign = root / "workflow-output/humanize/child/measurement.json"
            benign.write_text('{"measurement":"inconclusive"}\n', encoding="utf-8")
            benign_reference = (
                "workflow-output/humanize/child/measurement.json#sha256:"
                + hashlib.sha256(benign.read_bytes()).hexdigest()
            )
            missed = scheduler["ChildAssessment"](
                observed_outcome="the expected measurement was inconclusive",
                expected_outcome_status="met",
                expected_measurement_status="inconclusive",
                deviation_class="none",
                evidence_lineage=[benign_reference],
                reward_hacking_signals=[],
            )
            missed_observation = scheduler["_validated_child_assessment"](
                root, missed
            )
            self.assertEqual("unexpected", missed_observation.deviation_class)

            wrong_digest = reference[:-1] + ("0" if reference[-1] != "0" else "1")
            mismatched = scheduler["ChildAssessment"](
                observed_outcome="completed",
                expected_outcome_status="met",
                expected_measurement_status="met",
                deviation_class="none",
                evidence_lineage=[wrong_digest],
                reward_hacking_signals=[],
            )
            with self.assertRaisesRegex(RuntimeError, "digest"):
                scheduler["_validated_child_assessment"](root, mismatched)

            scheduler_owned = (
                root
                / "workflow-output/humanize/scheduler/observation-checkpoint.json"
            )
            scheduler_owned.parent.mkdir(parents=True, exist_ok=True)
            scheduler_owned.write_text('{"stale":true}\n', encoding="utf-8")
            scheduler_reference = (
                "workflow-output/humanize/scheduler/observation-checkpoint.json#sha256:"
                + hashlib.sha256(scheduler_owned.read_bytes()).hexdigest()
            )
            stale = scheduler["ChildAssessment"](
                observed_outcome="completed",
                expected_outcome_status="met",
                expected_measurement_status="met",
                deviation_class="none",
                evidence_lineage=[scheduler_reference],
                reward_hacking_signals=[],
            )
            with self.assertRaisesRegex(RuntimeError, "non-scheduler"):
                scheduler["_validated_child_assessment"](root, stale)

    def test_scheduler_repeat_count_requires_consecutive_comparable_deviations(self) -> None:
        scheduler = scheduler_namespace()
        first_decision = scheduler["CycleDecision"](
            "performed", (), "first expectation", "first measurement", "verify"
        )
        other_decision = scheduler["CycleDecision"](
            "performed", (), "other expectation", "other measurement", "verify"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            first = scheduler["_record_history"](
                root,
                1,
                ".humanize/flows/slot.py",
                "failed: timeout while collecting evidence",
                decision=first_decision,
                task="ledger task",
            )
            middle = scheduler["_record_history"](
                root,
                2,
                ".humanize/flows/slot.py",
                "failed: timeout while collecting evidence",
                decision=other_decision,
                task="ledger task",
            )
            last = scheduler["_record_history"](
                root,
                3,
                ".humanize/flows/slot.py",
                "failed: timeout while collecting evidence",
                decision=first_decision,
                task="ledger task",
            )
            self.assertEqual(1, first.repeated_deviation_count)
            self.assertEqual(1, middle.repeated_deviation_count)
            self.assertEqual(1, last.repeated_deviation_count)
            self.assertFalse(last.structural_review_required)

    def test_scheduler_unassessed_status_cannot_be_success_or_fake_lineage(self) -> None:
        scheduler = scheduler_namespace()
        decision = scheduler["CycleDecision"](
            "performed", (), "expected result", "acceptance measurement", "verify"
        )
        ledger = scheduler["_cycle_ledger"](
            b"",
            round_number=1,
            target=".humanize/flows/slot.py",
            outcome="completed",
            decision=decision,
            task="bounded task",
        )
        self.assertEqual("inconclusive", ledger.expected_outcome_status)
        self.assertEqual("inconclusive", ledger.expected_measurement_status)
        self.assertEqual("unexpected", ledger.base_deviation_class)
        self.assertEqual("unexpected", ledger.deviation_class)
        self.assertEqual("explore", ledger.next_strategy)
        self.assertNotIn("workflow-output/humanize/", ledger.evidence_lineage)
        self.assertNotIn(
            ".humanize/scheduler/history.jsonl", ledger.evidence_lineage
        )
        self.assertTrue(
            any(
                reference.startswith("observed:sha256:")
                for reference in ledger.evidence_lineage
            )
        )

    def test_scheduler_child_assessment_retries_without_rerunning_child(self) -> None:
        scheduler = scheduler_namespace()
        decision = scheduler["CycleDecision"](
            "performed", (), "expected result", "acceptance measurement", "verify"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            evidence = root / "workflow-output/humanize/child/result.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text('{"status":"complete"}\n', encoding="utf-8")
            reference = (
                "workflow-output/humanize/child/result.json#sha256:"
                + hashlib.sha256(evidence.read_bytes()).hexdigest()
            )
            attempts = 0
            slept: list[float] = []

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str, *, schema=None):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise subprocess.CalledProcessError(
                            1,
                            ["codex", "app-server", "--stdio"],
                            stderr="rate limit exceeded",
                        )
                    return schema(
                        observed_outcome="expected result and measurement met",
                        expected_outcome_status="met",
                        expected_measurement_status="met",
                        deviation_class="none",
                        evidence_lineage=[reference],
                        reward_hacking_signals=[],
                    )

            async def fake_sleep(delay: float) -> None:
                slept.append(delay)

            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(scheduler["asyncio"], "sleep", fake_sleep):
                    observed = asyncio.run(
                        scheduler["_assess_completed_child"](
                            scheduler["Agents"](
                                Scheduler(), *[object() for _ in range(10)]
                            ),
                            root,
                            "bounded task",
                            ".humanize/flows/slot.py",
                            1,
                            scheduler["_legacy_autonomous_policy"](),
                            decision,
                            scheduler["Config"](scheduler_retry_rounds=1),
                        )
                    )
            finally:
                os.chdir(previous)
            self.assertEqual("completed", observed.status)
            self.assertEqual(2, attempts)
            self.assertEqual([300.0], slept)
            self.assertEqual(
                "pending\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(
                    encoding="utf-8"
                ),
            )
            history = (root / ".humanize/scheduler/history.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("post-child assessment attempt 1/2 retrying", history)

    def test_scheduler_child_assessment_retries_bounded_malformed_output(self) -> None:
        scheduler = scheduler_namespace()
        decision = scheduler["CycleDecision"](
            "performed", (), "expected result", "acceptance measurement", "verify"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            evidence = root / "workflow-output/humanize/child/result.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text('{"status":"complete"}\n', encoding="utf-8")
            reference = (
                "workflow-output/humanize/child/result.json#sha256:"
                + hashlib.sha256(evidence.read_bytes()).hexdigest()
            )
            attempts = 0
            slept: list[float] = []

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str, *, schema=None):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        return schema(
                            observed_outcome="",
                            expected_outcome_status="inconclusive",
                            expected_measurement_status="inconclusive",
                            deviation_class="unexpected",
                            evidence_lineage=[reference],
                            reward_hacking_signals=[],
                        )
                    return schema(
                        observed_outcome="evidence confirms the expected result",
                        expected_outcome_status="met",
                        expected_measurement_status="met",
                        deviation_class="none",
                        evidence_lineage=[reference],
                        reward_hacking_signals=[],
                    )

            async def fake_sleep(delay: float) -> None:
                slept.append(delay)

            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(scheduler["asyncio"], "sleep", fake_sleep):
                    observed = asyncio.run(
                        scheduler["_assess_completed_child"](
                            scheduler["Agents"](
                                Scheduler(), *[object() for _ in range(10)]
                            ),
                            root,
                            "bounded task",
                            ".humanize/flows/slot.py",
                            1,
                            scheduler["_legacy_autonomous_policy"](),
                            decision,
                            scheduler["Config"](scheduler_retry_rounds=1),
                        )
                    )
            finally:
                os.chdir(previous)
            self.assertEqual("completed", observed.status)
            self.assertEqual(2, attempts)
            self.assertEqual([300.0], slept)

    def test_scheduler_prompt_exposes_delegation_and_reward_hacking_guard(self) -> None:
        scheduler = scheduler_namespace()
        prompt = scheduler["_scheduler_prompt"](
            "prompt task",
            1,
            50,
            "none",
            "none",
            scheduler["_legacy_autonomous_policy"](),
            ledger_context='{"structural_review_required":true}',
        )
        for expected in (
            "team leader and monitoring",
            "Delegate every implementation",
            "approved Slot is the only workflow execution boundary",
            "schedule individual workflow nodes",
            "multi-model consultation",
            "independent proposal branches",
            "result-analysis branches",
            "not majority voting",
            "does not replace the child reviewer or terminal review",
            "expected_outcome",
            "expected_measurement",
            ".humanize/scheduler/.cycle-decision.tmp",
            "humanize-iteration-decision/v3",
            "pivot or explore",
            "reward-hacking/jailbreak",
            "not proof of deterministic security",
        ):
            self.assertIn(expected, prompt)

    def test_scheduler_human_guided_child_exits_to_external_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            (root / ".pm").mkdir()
            (root / ".pm/config.json").write_text(
                json.dumps(
                    {
                        "iteration_mode": "human_guided",
                        "human_checkin_lease_minutes": 60,
                        "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                        "human_unavailable_fallback": "pause",
                    }
                ),
                encoding="utf-8",
            )
            slot = root / ".humanize/flows/slot.py"
            payload = (slot.parent / os.readlink(slot)).read_bytes()
            slot_sha = hashlib.sha256(payload).hexdigest()
            approved = root / ".humanize/flows/.approved"
            approved.mkdir()
            snapshot = approved / f"slot-{slot_sha}.py"
            snapshot.write_bytes(payload)
            snapshot.chmod(0o400)
            approved.chmod(0o500)

            prompts: list[str] = []
            child_tasks: list[str] = []

            class Scheduler:
                backend = "codex"

                async def aturn(self, prompt: str, *, schema=None):
                    prompts.append(prompt)
                    if schema is not None:
                        evidence = (
                            root / "workflow-output/humanize/child/result.json"
                        )
                        reference = (
                            "workflow-output/humanize/child/result.json#sha256:"
                            + hashlib.sha256(evidence.read_bytes()).hexdigest()
                        )
                        return schema(
                            observed_outcome="expected result and measurement met",
                            expected_outcome_status="met",
                            expected_measurement_status="met",
                            deviation_class="none",
                            evidence_lineage=[reference],
                            reward_hacking_signals=[],
                        )
                    state = root / ".humanize/scheduler"
                    decision = state / "dispatch.txt"
                    iteration = state / ".cycle-decision.tmp"
                    decision_tmp = state / ".dispatch.test"
                    iteration_tmp = state / ".cycle.test"
                    decision_tmp.write_text(
                        "true\n.humanize/flows/slot.py\n", encoding="utf-8"
                    )
                    iteration_tmp.write_text(
                        cycle_decision_payload("skipped_human_guided").decode("utf-8"),
                        encoding="utf-8",
                    )
                    os.replace(decision_tmp, decision)
                    os.replace(iteration_tmp, iteration)

            namespace = scheduler_namespace(root / ".humanize/flows/scheduler/__init__.py")
            namespace["run"].__globals__["_load_slot_validator"] = lambda _root: (
                lambda candidate: hashlib.sha256(candidate).hexdigest(),
                lambda _candidate: ("executor", "reviewer"),
            )

            def load(_name: str):
                def child(_agents: tuple[object, ...], task: str) -> None:
                    child_tasks.append(task)
                    evidence = root / "workflow-output/humanize/child/result.json"
                    evidence.parent.mkdir(parents=True)
                    evidence.write_text(
                        '{"status":"complete"}\n', encoding="utf-8"
                    )

                return child

            namespace["run"].__globals__["load"] = load
            previous = Path.cwd()
            try:
                os.chdir(root)
                asyncio.run(
                    namespace["run"](
                        namespace["Agents"](Scheduler(), *[object() for _ in range(10)]),
                        "bounded task",
                        namespace["Config"](),
                    )
                )
            finally:
                os.chdir(previous)
                approved.chmod(0o700)

            planning_prompts = [
                prompt for prompt in prompts if "Before this session ends" in prompt
            ]
            assessment_prompts = [
                prompt for prompt in prompts if "monitoring coordinator after" in prompt
            ]
            self.assertEqual(1, len(planning_prompts))
            self.assertEqual(1, len(assessment_prompts))
            self.assertEqual(1, len(child_tasks))
            self.assertIn("iteration_mode: human_guided", child_tasks[0])
            checkpoint = json.loads(
                (root / "workflow-output/humanize/scheduler/observation-checkpoint.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                "humanize-external-observation-checkpoint/v2", checkpoint["schema"]
            )
            self.assertRegex(checkpoint["checkpoint_id"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual("awaiting_human_feedback", checkpoint["status"])
            self.assertEqual("human_guided", checkpoint["iteration_mode"])
            self.assertEqual(
                "skipped_human_guided", checkpoint["idea_review_disposition"]
            )
            self.assertFalse(checkpoint["next_planning_allowed"])
            self.assertIsNone(checkpoint["human_feedback"])
            for field in (
                "hypothesis",
                "actual_operation",
                "raw_observation",
                "agent_interpretation",
                "anomaly_or_alternate_interpretation",
                "candidate_next_tasks",
            ):
                self.assertIn(field, checkpoint)
            self.assertEqual(
                "false\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(encoding="utf-8"),
            )

    def test_scheduler_retry_rechecks_consumed_feedback_before_another_model(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            issue = write_pm_issue_snapshot(root)
            namespace = scheduler_namespace(root / ".humanize/flows/scheduler/__init__.py")
            context = launcher["build_invocation_context"](
                root, pm_issue=issue.relative_to(root), human_feedback=None
            )
            policy = namespace["_resolve_iteration_policy"](
                root,
                now=namespace["_parse_utc_instant"]("2030-01-01T00:05:00Z"),
                invocation_context=context,
            )
            namespace["_write_observation_checkpoint"](
                root,
                task="bound retry task",
                status="awaiting_human_feedback",
                round_number=1,
                target=".humanize/flows/slot.py",
                outcome="completed",
                policy=policy,
                decision=namespace["CycleDecision"]("skipped_human_guided", ()),
                invocation_context=context,
            )
            resumed = launcher["build_invocation_context"](
                root,
                pm_issue=issue.relative_to(root),
                human_feedback="check the retry evidence",
            )
            claim = namespace["_validated_human_feedback"](
                root, "bound retry task", policy, resumed
            )
            self.assertIsNotNone(claim)
            attempts = 0

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> None:
                    nonlocal attempts
                    attempts += 1
                    raise subprocess.CalledProcessError(
                        1,
                        ["codex", "app-server", "--stdio"],
                        stderr="rate limit exceeded",
                    )

            async def tamper_during_wait(_delay: float) -> None:
                checkpoint = (
                    root
                    / "workflow-output/humanize/scheduler/observation-checkpoint.json"
                )
                checkpoint.write_bytes(checkpoint.read_bytes() + b" ")

            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(namespace["asyncio"], "sleep", tamper_during_wait):
                    with self.assertRaisesRegex(RuntimeError, "checkpoint"):
                        asyncio.run(
                            namespace["_run_scheduler_turn"](
                                namespace["Agents"](
                                    Scheduler(), *[object() for _ in range(10)]
                                ),
                                root,
                                "bound retry task",
                                1,
                                namespace["Config"](scheduler_retry_rounds=1),
                                "none",
                                "none",
                                policy,
                                resumed,
                                claim,
                            )
                        )
            finally:
                os.chdir(previous)
            self.assertEqual(1, attempts)

    def test_scheduler_retry_with_consumed_feedback_pauses_when_lease_expires(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            issue = write_pm_issue_snapshot(
                root, expiry="2030-01-01T00:10:00Z", fallback="pause"
            )
            namespace = scheduler_namespace(root / ".humanize/flows/scheduler/__init__.py")
            real_datetime = namespace["datetime"]

            class Clock(real_datetime):
                current = real_datetime.fromisoformat("2030-01-01T00:05:00+00:00")

                @classmethod
                def now(cls, _zone: object = None):
                    return cls.current

            namespace["_resolve_iteration_policy"].__globals__["datetime"] = Clock
            initial = launcher["build_invocation_context"](
                root, pm_issue=issue.relative_to(root), human_feedback=None
            )
            policy = namespace["_resolve_iteration_policy"](
                root, invocation_context=initial
            )
            namespace["_write_observation_checkpoint"](
                root,
                task="expiring feedback task",
                status="awaiting_human_feedback",
                round_number=1,
                target=".humanize/flows/slot.py",
                outcome="completed",
                policy=policy,
                decision=namespace["CycleDecision"]("skipped_human_guided", ()),
                invocation_context=initial,
            )
            resumed = launcher["build_invocation_context"](
                root,
                pm_issue=issue.relative_to(root),
                human_feedback="pause if I am no longer present",
            )
            claim = namespace["_validated_human_feedback"](
                root, "expiring feedback task", policy, resumed
            )
            attempts = 0

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> None:
                    nonlocal attempts
                    attempts += 1
                    raise subprocess.CalledProcessError(
                        1,
                        ["codex", "app-server", "--stdio"],
                        stderr="rate limit exceeded",
                    )

            async def expire_during_wait(_delay: float) -> None:
                Clock.current = real_datetime.fromisoformat(
                    "2030-01-01T00:15:00+00:00"
                )

            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(namespace["asyncio"], "sleep", expire_during_wait):
                    result = asyncio.run(
                        namespace["_run_scheduler_turn"](
                            namespace["Agents"](
                                Scheduler(), *[object() for _ in range(10)]
                            ),
                            root,
                            "expiring feedback task",
                            1,
                            namespace["Config"](scheduler_retry_rounds=1),
                            "none",
                            "none",
                            policy,
                            resumed,
                            claim,
                        )
                    )
            finally:
                os.chdir(previous)
                namespace["_resolve_iteration_policy"].__globals__["datetime"] = (
                    real_datetime
                )
            self.assertIsNone(result)
            self.assertEqual(1, attempts)
            checkpoint = json.loads(
                (
                    root
                    / "workflow-output/humanize/scheduler/observation-checkpoint.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("paused_human_unavailable", checkpoint["status"])

    def test_scheduler_outer_round_keeps_feedback_claim_after_child_failure(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            issue = write_pm_issue_snapshot(root)
            namespace = scheduler_namespace(root / ".humanize/flows/scheduler/__init__.py")
            initial = launcher["build_invocation_context"](
                root, pm_issue=issue.relative_to(root), human_feedback=None
            )
            policy = namespace["_resolve_iteration_policy"](
                root, invocation_context=initial
            )
            namespace["_write_observation_checkpoint"](
                root,
                task="outer round task",
                status="awaiting_human_feedback",
                round_number=1,
                target=".humanize/flows/slot.py",
                outcome="completed",
                policy=policy,
                decision=namespace["CycleDecision"]("skipped_human_guided", ()),
                invocation_context=initial,
            )
            resumed = launcher["build_invocation_context"](
                root,
                pm_issue=issue.relative_to(root),
                human_feedback="retain this observation across rounds",
            )
            scheduler_turns = 0

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> None:
                    nonlocal scheduler_turns
                    scheduler_turns += 1
                    state = root / ".humanize/scheduler"
                    (state / "dispatch.txt").write_text(
                        "true\n.humanize/flows/slot.py\n", encoding="utf-8"
                    )
                    (state / ".cycle-decision.tmp").write_text(
                        cycle_decision_payload("skipped_human_guided").decode("utf-8"),
                        encoding="utf-8",
                    )

            async def failed_child(*_args: object, **_kwargs: object) -> str:
                markers = list(
                    (
                        root
                        / "workflow-output/humanize/scheduler/feedback-consumed"
                    ).glob("*.json")
                )
                self.assertEqual(1, len(markers))
                markers[0].unlink()
                return "failed"

            run_globals = namespace["run"].__globals__
            run_globals["_load_invocation_context"] = lambda _root: resumed
            run_globals["_load_slot_validator"] = lambda _root: (
                lambda candidate: hashlib.sha256(candidate).hexdigest(),
                lambda _candidate: ("executor", "reviewer"),
            )
            run_globals["_run_child"] = failed_child
            previous = Path.cwd()
            try:
                os.chdir(root)
                with self.assertRaisesRegex(RuntimeError, "consumption record"):
                    asyncio.run(
                        namespace["run"](
                            namespace["Agents"](
                                Scheduler(), *[object() for _ in range(10)]
                            ),
                            "outer round task",
                            namespace["Config"](max_rounds=2),
                        )
                    )
            finally:
                os.chdir(previous)
            self.assertEqual(1, scheduler_turns)

    def test_scheduler_outer_round_keeps_claim_during_autonomous_review_fallback(self) -> None:
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            issue = write_pm_issue_snapshot(
                root,
                expiry="2030-01-01T00:10:00Z",
                fallback="autonomous_review",
            )
            namespace = scheduler_namespace(root / ".humanize/flows/scheduler/__init__.py")
            real_datetime = namespace["datetime"]

            class Clock(real_datetime):
                current = real_datetime.fromisoformat("2030-01-01T00:05:00+00:00")

                @classmethod
                def now(cls, _zone: object = None):
                    return cls.current

            namespace["_resolve_iteration_policy"].__globals__["datetime"] = Clock
            initial = launcher["build_invocation_context"](
                root, pm_issue=issue.relative_to(root), human_feedback=None
            )
            policy = namespace["_resolve_iteration_policy"](
                root, invocation_context=initial
            )
            namespace["_write_observation_checkpoint"](
                root,
                task="fallback outer task",
                status="awaiting_human_feedback",
                round_number=1,
                target=".humanize/flows/slot.py",
                outcome="completed",
                policy=policy,
                decision=namespace["CycleDecision"]("skipped_human_guided", ()),
                invocation_context=initial,
            )
            resumed = launcher["build_invocation_context"](
                root,
                pm_issue=issue.relative_to(root),
                human_feedback="continue with restored review if I leave",
            )
            prompts: list[str] = []

            class Scheduler:
                backend = "codex"

                async def aturn(self, prompt: str) -> None:
                    prompts.append(prompt)
                    state = root / ".humanize/scheduler"
                    if len(prompts) == 1:
                        (state / "dispatch.txt").write_text(
                            "true\n.humanize/flows/slot.py\n", encoding="utf-8"
                        )
                        (state / ".cycle-decision.tmp").write_text(
                            cycle_decision_payload("skipped_human_guided").decode("utf-8"),
                            encoding="utf-8",
                        )
                    else:
                        (state / "dispatch.txt").write_text(
                            "false\n", encoding="utf-8"
                        )

            async def failed_child(*_args: object, **_kwargs: object) -> str:
                Clock.current = real_datetime.fromisoformat(
                    "2030-01-01T00:15:00+00:00"
                )
                return "failed"

            run_globals = namespace["run"].__globals__
            run_globals["_load_invocation_context"] = lambda _root: resumed
            run_globals["_load_slot_validator"] = lambda _root: (
                lambda candidate: hashlib.sha256(candidate).hexdigest(),
                lambda _candidate: ("executor", "reviewer"),
            )
            run_globals["_run_child"] = failed_child
            previous = Path.cwd()
            try:
                os.chdir(root)
                asyncio.run(
                    namespace["run"](
                        namespace["Agents"](
                            Scheduler(), *[object() for _ in range(10)]
                        ),
                        "fallback outer task",
                        namespace["Config"](max_rounds=2),
                    )
                )
            finally:
                os.chdir(previous)
                namespace["_resolve_iteration_policy"].__globals__["datetime"] = (
                    real_datetime
                )
            self.assertEqual(2, len(prompts))
            self.assertIn("effective iteration_mode: autonomous", prompts[1])
            self.assertIn("Fallback applied now: autonomous_review", prompts[1])
            self.assertIn("continue with restored review if I leave", prompts[1])

    def test_scheduler_retry_rechecks_lease_and_pauses_before_another_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            (root / ".pm").mkdir()
            config = root / ".pm/config.json"
            document = {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
                "human_unavailable_fallback": "pause",
            }
            config.write_text(json.dumps(document), encoding="utf-8")
            attempts = 0

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> None:
                    nonlocal attempts
                    attempts += 1
                    raise subprocess.CalledProcessError(
                        1,
                        ["codex", "app-server", "--stdio"],
                        stderr="rate limit exceeded",
                    )

            namespace = scheduler_namespace(root / ".humanize/flows/scheduler/__init__.py")
            policy = namespace["_resolve_iteration_policy"](root)
            slept: list[float] = []

            async def expire_during_wait(delay: float) -> None:
                slept.append(delay)
                document["human_checkin_lease_expires_at"] = "2000-01-01T00:00:00Z"
                config.write_text(json.dumps(document), encoding="utf-8")

            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(
                    namespace["asyncio"], "sleep", expire_during_wait
                ):
                    result = asyncio.run(
                        namespace["_run_scheduler_turn"](
                            namespace["Agents"](
                                Scheduler(), *[object() for _ in range(10)]
                            ),
                            root,
                            "bounded task",
                            1,
                            namespace["Config"](),
                            "none",
                            "none",
                            policy,
                        )
                    )
            finally:
                os.chdir(previous)

            self.assertIsNone(result)
            self.assertEqual(1, attempts)
            self.assertEqual([300.0], slept)
            checkpoint = json.loads(
                (
                    root
                    / "workflow-output/humanize/scheduler/observation-checkpoint.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual("paused_human_unavailable", checkpoint["status"])
            self.assertEqual("pause", checkpoint["fallback_applied"])

    def test_scheduler_retries_transient_turn_and_resets_pending_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            validator = (
                root
                / ".claude/skills/write-humanize-flow/scripts/validate_project_flows.py"
            )
            validator.parent.mkdir(parents=True)
            shutil.copyfile(SCRIPTS / "validate_project_flows.py", validator)

            attempts: list[str] = []
            slept: list[float] = []

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> str:
                    attempts.append("turn")
                    dispatch = root / ".humanize/scheduler/dispatch.txt"
                    if len(attempts) == 1:
                        dispatch.write_text(
                            "true\n.humanize/flows/slot.py\n", encoding="utf-8"
                        )
                        raise subprocess.CalledProcessError(
                            1,
                            ["codex", "app-server", "--stdio"],
                            '{"code":"rate_limit_exceeded","api_key":"secret-value","message":"try again"}',
                            "",
                        )
                    dispatch.write_text("false\n", encoding="utf-8")
                    return "done"

            agents_module = types.ModuleType("hmz.agents")
            agents_module.Unrecoverable = FakeUnrecoverable
            flows_module = types.ModuleType("hmz.flows")
            flows_module.Agent = object
            flows_module.AgentDefaults = lambda **values: values
            flows_module.Unrecoverable = FakeUnrecoverable
            flows_module.flow = lambda function: function
            flows_module.load = lambda _path: None
            runner_module = types.ModuleType("hmz.runner")
            pydantic_module = types.ModuleType("pydantic")

            class BaseModel:
                def __init__(self, **values: object) -> None:
                    for name, value in values.items():
                        setattr(self, name, value)

            pydantic_module.BaseModel = BaseModel
            pydantic_module.ConfigDict = lambda **values: values
            pydantic_module.Field = lambda *, default, **_bounds: default
            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.dict(
                    sys.modules,
                    {
                        "hmz.agents": agents_module,
                        "hmz.flows": flows_module,
                        "hmz.runner": runner_module,
                        "pydantic": pydantic_module,
                    },
                ):
                    scheduler = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
                    stopped_server = subprocess.CalledProcessError(
                        1,
                        ["codex", "app-server", "--stdio"],
                        "",
                        "app server stopped mid-turn",
                    )
                    self.assertFalse(
                        scheduler["_retryable_scheduler_error"](stopped_server)
                    )
                    usage_limit = subprocess.CalledProcessError(
                        1,
                        ["codex", "app-server", "--stdio"],
                        "",
                        '{"code":"usage_limit_reached","message":"quota resets later"}',
                    )
                    self.assertTrue(
                        scheduler["_retryable_scheduler_error"](usage_limit)
                    )
                    quota_window = subprocess.CalledProcessError(
                        1,
                        ["codex", "app-server", "--stdio"],
                        "",
                        '{"code":"quota_exceeded","message":"reset pending"}',
                    )
                    self.assertTrue(
                        scheduler["_retryable_scheduler_error"](quota_window)
                    )
                    unrecoverable = FakeUnrecoverable(
                        1,
                        ["codex", "app-server", "--stdio"],
                        "",
                        '{"code":"rate_limit_exceeded","message":"reset pending"}',
                    )
                    self.assertFalse(
                        scheduler["_retryable_scheduler_error"](unrecoverable)
                    )

                    async def fake_sleep(delay: float) -> None:
                        slept.append(delay)
                        self.assertEqual(
                            "pending\n",
                            (
                                root / ".humanize/scheduler/dispatch.txt"
                            ).read_text(encoding="utf-8"),
                        )

                    with mock.patch.object(scheduler["asyncio"], "sleep", fake_sleep):
                        asyncio.run(
                            scheduler["run"](
                                scheduler["Agents"](
                                    Scheduler(), *[object() for _ in range(10)]
                                ),
                                "bounded task",
                                scheduler["Config"](
                                    max_rounds=50,
                                    scheduler_retry_rounds=4,
                                ),
                            )
                        )
            finally:
                os.chdir(previous)

            self.assertEqual(["turn", "turn"], attempts)
            self.assertEqual([300.0], slept)
            self.assertEqual(
                "false\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(
                    encoding="utf-8"
                ),
            )
            history = (root / ".humanize/scheduler/history.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("rate_limit_exceeded", history)
            self.assertIn("<redacted>", history)
            self.assertNotIn("secret-value", history)
            self.assertIn("retrying", history)

    def test_scheduler_exhausts_the_four_fixed_retry_delays_then_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            attempts = 0
            slept: list[float] = []

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> str:
                    nonlocal attempts
                    attempts += 1
                    raise subprocess.CalledProcessError(
                        1,
                        ["codex", "app-server", "--stdio"],
                        "",
                        '{"code":"rate_limit_exceeded","message":"try again"}',
                    )

            agents_module = types.ModuleType("hmz.agents")
            agents_module.Unrecoverable = FakeUnrecoverable
            flows_module = types.ModuleType("hmz.flows")
            flows_module.Agent = object
            flows_module.AgentDefaults = lambda **values: values
            flows_module.Unrecoverable = FakeUnrecoverable
            flows_module.flow = lambda function: function
            flows_module.load = lambda _path: None
            runner_module = types.ModuleType("hmz.runner")
            pydantic_module = types.ModuleType("pydantic")

            class BaseModel:
                def __init__(self, **values: object) -> None:
                    for name, value in values.items():
                        setattr(self, name, value)

            pydantic_module.BaseModel = BaseModel
            pydantic_module.ConfigDict = lambda **values: values
            pydantic_module.Field = lambda *, default, **_bounds: default
            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.dict(
                    sys.modules,
                    {
                        "hmz.agents": agents_module,
                        "hmz.flows": flows_module,
                        "hmz.runner": runner_module,
                        "pydantic": pydantic_module,
                    },
                ):
                    scheduler = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))

                    async def fake_sleep(delay: float) -> None:
                        slept.append(delay)
                        self.assertEqual(
                            "pending\n",
                            (root / ".humanize/scheduler/dispatch.txt").read_text(
                                encoding="utf-8"
                            ),
                        )

                    with mock.patch.object(scheduler["asyncio"], "sleep", fake_sleep):
                        with self.assertRaisesRegex(RuntimeError, "attempt 5/5 stopping"):
                            asyncio.run(
                                scheduler["_run_scheduler_turn"](
                                    scheduler["Agents"](
                                        Scheduler(), *[object() for _ in range(10)]
                                    ),
                                    root,
                                    "bounded task",
                                    1,
                                    scheduler["Config"](),
                                    "none",
                                    "none",
                                    scheduler["_legacy_autonomous_policy"](),
                                )
                            )
            finally:
                os.chdir(previous)

            self.assertEqual(5, attempts)
            self.assertEqual([300.0, 600.0, 1800.0, 3600.0], slept)
            self.assertEqual(
                "pending\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(
                    encoding="utf-8"
                ),
            )
            history = (root / ".humanize/scheduler/history.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertEqual(5, len(history.splitlines()))
            self.assertIn("attempt 5/5 stopping", history)

    def test_scheduler_stops_on_hard_quota_and_keeps_pending_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            validator = (
                root
                / ".claude/skills/write-humanize-flow/scripts/validate_project_flows.py"
            )
            validator.parent.mkdir(parents=True)
            shutil.copyfile(SCRIPTS / "validate_project_flows.py", validator)
            attempts = 0

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> str:
                    nonlocal attempts
                    attempts += 1
                    raise subprocess.CalledProcessError(
                        1,
                        ["codex", "app-server", "--stdio"],
                        "",
                        '{"code":"insufficient_quota","message":"quota exhausted"}',
                    )

            agents_module = types.ModuleType("hmz.agents")
            agents_module.Unrecoverable = FakeUnrecoverable
            flows_module = types.ModuleType("hmz.flows")
            flows_module.Agent = object
            flows_module.AgentDefaults = lambda **values: values
            flows_module.Unrecoverable = FakeUnrecoverable
            flows_module.flow = lambda function: function
            flows_module.load = lambda _path: None
            runner_module = types.ModuleType("hmz.runner")
            pydantic_module = types.ModuleType("pydantic")

            class BaseModel:
                def __init__(self, **values: object) -> None:
                    for name, value in values.items():
                        setattr(self, name, value)

            pydantic_module.BaseModel = BaseModel
            pydantic_module.ConfigDict = lambda **values: values
            pydantic_module.Field = lambda *, default, **_bounds: default
            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.dict(
                    sys.modules,
                    {
                        "hmz.agents": agents_module,
                        "hmz.flows": flows_module,
                        "hmz.runner": runner_module,
                        "pydantic": pydantic_module,
                    },
                ):
                    scheduler = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
                    with self.assertRaisesRegex(RuntimeError, "insufficient_quota"):
                        asyncio.run(
                            scheduler["run"](
                                scheduler["Agents"](
                                    Scheduler(), *[object() for _ in range(10)]
                                ),
                                "bounded task",
                                scheduler["Config"](
                                    max_rounds=50,
                                    scheduler_retry_rounds=4,
                                ),
                            )
                        )
            finally:
                os.chdir(previous)

            self.assertEqual(1, attempts)
            self.assertEqual(
                "pending\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(
                    encoding="utf-8"
                ),
            )
            history = (root / ".humanize/scheduler/history.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("insufficient_quota", history)
            self.assertIn("stopping", history)

    def test_scheduler_resets_pending_on_non_backend_turn_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> str:
                    (root / ".humanize/scheduler/dispatch.txt").write_text(
                        "true\n.humanize/flows/slot.py\n", encoding="utf-8"
                    )
                    raise RuntimeError("scheduler protocol broke")

            agents_module = types.ModuleType("hmz.agents")
            agents_module.Unrecoverable = FakeUnrecoverable
            flows_module = types.ModuleType("hmz.flows")
            flows_module.Agent = object
            flows_module.AgentDefaults = lambda **values: values
            flows_module.Unrecoverable = FakeUnrecoverable
            flows_module.flow = lambda function: function
            flows_module.load = lambda _path: None
            runner_module = types.ModuleType("hmz.runner")
            pydantic_module = types.ModuleType("pydantic")

            class BaseModel:
                def __init__(self, **values: object) -> None:
                    for name, value in values.items():
                        setattr(self, name, value)

            pydantic_module.BaseModel = BaseModel
            pydantic_module.ConfigDict = lambda **values: values
            pydantic_module.Field = lambda *, default, **_bounds: default
            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.dict(
                    sys.modules,
                    {
                        "hmz.agents": agents_module,
                        "hmz.flows": flows_module,
                        "hmz.runner": runner_module,
                        "pydantic": pydantic_module,
                    },
                ):
                    scheduler = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
                    with self.assertRaisesRegex(RuntimeError, "scheduler protocol broke"):
                        asyncio.run(
                            scheduler["_run_scheduler_turn"](
                                scheduler["Agents"](
                                    Scheduler(), *[object() for _ in range(10)]
                                ),
                                root,
                                "bounded task",
                                1,
                                scheduler["Config"](
                                    max_rounds=50,
                                    scheduler_retry_rounds=4,
                                ),
                                "none",
                                "none",
                                scheduler["_legacy_autonomous_policy"](),
                            )
                        )
            finally:
                os.chdir(previous)

            self.assertEqual(
                "pending\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(
                    encoding="utf-8"
                ),
            )
            history = (root / ".humanize/scheduler/history.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("scheduler protocol broke", history)
            self.assertIn("stopping", history)

    def test_scheduler_retries_truncated_dispatch_without_relaxing_parser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            scheduler = scheduler_namespace(root / ".humanize/flows/scheduler/__init__.py")
            attempts = 0
            slept: list[float] = []

            class Scheduler:
                backend = "codex"

                async def aturn(self, _prompt: str) -> None:
                    nonlocal attempts
                    attempts += 1
                    payload = (
                        "true\n"
                        if attempts == 1
                        else "true\n.humanize/flows/slot.py\n"
                    )
                    (root / ".humanize/scheduler/dispatch.txt").write_text(
                        payload, encoding="utf-8"
                    )

            async def fake_sleep(delay: float) -> None:
                slept.append(delay)
                self.assertEqual(
                    "pending\n",
                    (root / ".humanize/scheduler/dispatch.txt").read_text(
                        encoding="utf-8"
                    ),
                )

            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.object(scheduler["asyncio"], "sleep", fake_sleep):
                    observed = asyncio.run(
                        scheduler["_run_scheduler_turn"](
                            scheduler["Agents"](
                                Scheduler(), *[object() for _ in range(10)]
                            ),
                            root,
                            "bounded task",
                            1,
                            scheduler["Config"](scheduler_retry_rounds=1),
                            "none",
                            "none",
                            scheduler["_legacy_autonomous_policy"](),
                        )
                    )
            finally:
                os.chdir(previous)

            self.assertIsNotNone(observed)
            self.assertEqual(2, attempts)
            self.assertEqual([300.0], slept)
            self.assertEqual(
                "true\n.humanize/flows/slot.py\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(
                    encoding="utf-8"
                ),
            )
            history = (root / ".humanize/scheduler/history.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn("true requires exactly one newline-terminated workflow path", history)
            self.assertIn("attempt 1/2 retrying", history)

    def test_scheduler_runs_fresh_codex_turns_and_sealed_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            scripts = root / ".claude/skills/write-humanize-flow/scripts"
            scripts.mkdir(parents=True)
            shutil.copyfile(
                SCRIPTS / "validate_project_flows.py",
                scripts / "validate_project_flows.py",
            )
            controller_asset = (
                root
                / ".claude/skills/write-humanize-flow/assets/project/.humanize/flows"
            )
            controller_asset.mkdir(parents=True)
            shutil.copytree(
                ASSET / "flows/scheduler",
                controller_asset / "scheduler",
            )
            slot = root / ".humanize/flows/slot.py"
            candidate = slot.parent / os.readlink(slot)
            approved = subprocess.run(
                [
                    sys.executable,
                    str(scripts / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                    "--candidate",
                    str(candidate),
                    "--install-slot",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, approved.returncode, approved.stderr)

            calls_seen: list[tuple[str, bytes]] = []
            children_seen: list[tuple[tuple[object, object], str]] = []
            prompts: list[str] = []
            planning_turns = 0

            class Scheduler:
                backend = "codex"

                def __call__(self, _prompt: str) -> str:
                    raise AssertionError("async scheduler flow must use aturn")

                async def aturn(self, prompt: str, *, schema=None):
                    nonlocal planning_turns
                    prompts.append(prompt)
                    if schema is not None:
                        evidence = root / "workflow-output/humanize/child/result.json"
                        reference = (
                            "workflow-output/humanize/child/result.json#sha256:"
                            + hashlib.sha256(evidence.read_bytes()).hexdigest()
                        )
                        return schema(
                            observed_outcome="expected result and measurement met",
                            expected_outcome_status="met",
                            expected_measurement_status="met",
                            deviation_class="none",
                            evidence_lineage=[reference],
                            reward_hacking_signals=[],
                        )
                    planning_turns += 1
                    decision = (
                        "true\n.humanize/flows/slot.py\n"
                        if planning_turns == 1
                        else "false\n"
                    )
                    dispatch = root / ".humanize/scheduler/dispatch.txt"
                    temporary = dispatch.with_suffix(".test-tmp")
                    temporary.write_text(decision, encoding="utf-8")
                    os.replace(temporary, dispatch)
                    if planning_turns == 1:
                        cycle = root / ".humanize/scheduler/.cycle-decision.tmp"
                        cycle_temporary = cycle.with_suffix(".test-tmp")
                        cycle_temporary.write_bytes(cycle_decision_payload("performed"))
                        os.replace(cycle_temporary, cycle)
                    return "scheduler turn complete"

            scheduler_agent = Scheduler()
            executor = object()
            reviewer = object()
            other_agents = tuple(object() for _ in range(8))

            agents_module = types.ModuleType("hmz.agents")
            agents_module.Unrecoverable = FakeUnrecoverable
            flows_module = types.ModuleType("hmz.flows")
            flows_module.Agent = object
            flows_module.AgentDefaults = lambda **values: values
            flows_module.Unrecoverable = FakeUnrecoverable
            flows_module.flow = lambda function: function
            runner_module = types.ModuleType("hmz.runner")

            def load(name: str):
                approved_snapshot = next(
                    (root / ".humanize/flows/.approved").glob("slot-*.py")
                )
                approved_snapshot.chmod(0o644)
                approved_snapshot.write_bytes(b"replaced after scheduler admission\n")
                target = Path(name)
                payload = target.read_bytes()
                calls_seen.append((name, payload))

                def child(agents: tuple[object, ...], task: str) -> None:
                    children_seen.append((agents, task))
                    evidence = root / "workflow-output/humanize/child/result.json"
                    evidence.parent.mkdir(parents=True)
                    evidence.write_text(
                        '{"status":"complete"}\n', encoding="utf-8"
                    )

                return child

            flows_module.load = load
            pydantic_module = types.ModuleType("pydantic")

            class BaseModel:
                def __init__(self, **values: object) -> None:
                    for name, value in values.items():
                        setattr(self, name, value)

            pydantic_module.BaseModel = BaseModel
            pydantic_module.ConfigDict = lambda **values: values
            pydantic_module.Field = lambda *, default, **_bounds: default
            previous = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch.dict(
                    sys.modules,
                    {
                        "hmz.agents": agents_module,
                        "hmz.flows": flows_module,
                        "hmz.runner": runner_module,
                        "pydantic": pydantic_module,
                    },
                ):
                    namespace = runpy.run_path(str(root / ".humanize/flows/scheduler/__init__.py"))
                    config = namespace["Config"](max_rounds=2)
                    asyncio.run(
                        namespace["run"](
                            namespace["Agents"](
                                scheduler_agent, executor, reviewer, *other_agents
                            ),
                            "bounded task",
                            config,
                        )
                    )
            finally:
                os.chdir(previous)
                approved_directory = root / ".humanize/flows/.approved"
                if approved_directory.exists():
                    approved_directory.chmod(0o700)

            planning_prompts = [
                prompt for prompt in prompts if "Before this session ends" in prompt
            ]
            assessment_prompts = [
                prompt for prompt in prompts if "monitoring coordinator after" in prompt
            ]
            self.assertEqual(2, len(planning_prompts))
            self.assertEqual(1, len(assessment_prompts))
            self.assertIn(".humanize/scheduler/dispatch.txt", planning_prompts[0])
            self.assertIn("true\n.humanize/flows/slot.py", planning_prompts[0])
            self.assertIn(
                "--candidate .humanize/scheduler/candidate.py --install-slot",
                planning_prompts[0],
            )
            self.assertIn("Previous child target: none", planning_prompts[0])
            self.assertIn(
                "Previous child target: .humanize/flows/slot.py", planning_prompts[1]
            )
            self.assertIn(
                "Previous child outcome: expected result and measurement met",
                planning_prompts[1],
            )
            self.assertIn("planner, preflight, collector", planning_prompts[0])
            self.assertIn("cursor-agent", planning_prompts[0])
            self.assertIn("Do not execute the task directly", planning_prompts[0])
            self.assertIn("Do not launch native Codex subagents", planning_prompts[0])
            self.assertNotIn("Work on the task directly", planning_prompts[0])
            self.assertEqual(1, len(calls_seen))
            staged_path = Path(calls_seen[0][0])
            self.assertEqual("slot.py", staged_path.name)
            self.assertTrue(staged_path.parent.name.startswith(".slot-load-"))
            self.assertFalse(staged_path.exists())
            self.assertEqual(slot.read_bytes(), calls_seen[0][1])
            self.assertEqual((executor, reviewer), children_seen[0][0])
            self.assertIn("Original project task:\n\nbounded task", children_seen[0][1])
            self.assertIn("idea_review_disposition: performed", children_seen[0][1])
            history = (root / ".humanize/scheduler/history.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertIn('"outcome": "expected result and measurement met"', history)
            self.assertEqual(
                "false\n",
                (root / ".humanize/scheduler/dispatch.txt").read_text(encoding="utf-8"),
            )

    def test_scheduler_cleans_staging_directory_when_open_fails_after_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            agents_module = types.ModuleType("hmz.agents")
            agents_module.Unrecoverable = FakeUnrecoverable
            flows_module = types.ModuleType("hmz.flows")
            flows_module.Agent = object
            flows_module.AgentDefaults = lambda **values: values
            flows_module.Unrecoverable = FakeUnrecoverable
            flows_module.flow = lambda function: function
            flows_module.load = lambda _path: None
            runner_module = types.ModuleType("hmz.runner")
            pydantic_module = types.ModuleType("pydantic")
            pydantic_module.BaseModel = object
            pydantic_module.ConfigDict = lambda **values: values
            pydantic_module.Field = lambda *, default, **_bounds: default
            with mock.patch.dict(
                sys.modules,
                {
                    "hmz.agents": agents_module,
                    "hmz.flows": flows_module,
                    "hmz.runner": runner_module,
                    "pydantic": pydantic_module,
                },
            ):
                scheduler = runpy.run_path(str(root / ".humanize/flows/scheduler/__init__.py"))

            payload = (root / ".humanize/flows/slot.py").read_bytes()
            descriptor = scheduler["_sealed_memfd"]("staging-cleanup-test", payload)
            real_open = os.open

            def fail_staging_directory_open(
                path: os.PathLike[str] | str,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                if (
                    isinstance(path, str)
                    and path.startswith(".slot-load-")
                    and dir_fd is not None
                ):
                    raise PermissionError("injected staging directory open failure")
                keyword = {} if dir_fd is None else {"dir_fd": dir_fd}
                return real_open(path, flags, mode, **keyword)

            try:
                with mock.patch.object(os, "open", side_effect=fail_staging_directory_open):
                    with self.assertRaisesRegex(PermissionError, "injected staging"):
                        with scheduler["_stage_slot_for_humanize"](root, descriptor):
                            self.fail("staging must fail before yielding")
            finally:
                os.close(descriptor)
            self.assertEqual(
                [],
                list((root / ".humanize/scheduler").glob(".slot-load-*")),
            )

    def test_default_executes_approved_snapshot_after_mutable_slot_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            marker = root / "result.txt"
            candidate = root / "candidate.py"
            candidate.write_bytes(marker_slot(marker, "approved"))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                    "--candidate",
                    str(candidate),
                    "--install-slot",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            slot_digest = json.loads(completed.stdout)["slotSha256"]
            mutable = root / ".humanize/flows/slot.py"
            replacement = marker_slot(marker, "replacement")
            swap_triggered = False

            agents_module = types.ModuleType("hmz.agents")
            agents_module.Unrecoverable = FakeUnrecoverable
            flows_module = types.ModuleType("hmz.flows")
            flows_module.Agent = object
            flows_module.Unrecoverable = FakeUnrecoverable
            flows_module.flow = lambda function: function
            runner_module = types.ModuleType("hmz.runner")

            def load(name: str):
                nonlocal swap_triggered
                temporary = mutable.with_suffix(".replacement")
                temporary.write_bytes(replacement)
                os.replace(temporary, mutable)
                swap_triggered = True
                target = Path(name)
                if not target.is_absolute():
                    target = (
                        root / target
                        if "/" in name or name.endswith(".py")
                        else root / ".humanize/flows" / f"{name}.py"
                    )
                return runpy.run_path(str(target))["run"]

            flows_module.load = load
            prior_cwd = Path.cwd()
            prior_digest = os.environ.get("HUMANIZE_SLOT_SHA256")
            prior_slot_fd = os.environ.get("HUMANIZE_SLOT_FD")
            slot_fd = os.memfd_create(
                "authoring-approved-slot",
                flags=getattr(os, "MFD_ALLOW_SEALING", 0),
            )
            try:
                os.write(slot_fd, candidate.read_bytes())
                os.lseek(slot_fd, 0, os.SEEK_SET)
                os.chdir(root)
                os.environ["HUMANIZE_SLOT_SHA256"] = slot_digest
                os.environ["HUMANIZE_SLOT_FD"] = str(slot_fd)
                with mock.patch.dict(
                    sys.modules,
                    {
                        "hmz.agents": agents_module,
                        "hmz.flows": flows_module,
                        "hmz.runner": runner_module,
                    },
                ):
                    namespace = runpy.run_path(str(root / ".humanize/flows/default.py"))
                    namespace["run"]((None, None), "bounded task")
            finally:
                os.chdir(prior_cwd)
                if prior_digest is None:
                    os.environ.pop("HUMANIZE_SLOT_SHA256", None)
                else:
                    os.environ["HUMANIZE_SLOT_SHA256"] = prior_digest
                if prior_slot_fd is None:
                    os.environ.pop("HUMANIZE_SLOT_FD", None)
                else:
                    os.environ["HUMANIZE_SLOT_FD"] = prior_slot_fd
                os.close(slot_fd)
                approved = root / ".humanize/flows/.approved"
                if approved.exists():
                    approved.chmod(0o700)
            self.assertTrue(swap_triggered)
            self.assertEqual(marker.read_text(encoding="utf-8"), "approved")

    def test_existing_digest_snapshot_with_different_bytes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            candidate = root / "candidate.py"
            payload = marker_slot(root / "result.txt", "approved")
            candidate.write_bytes(payload)
            slot_digest = VALIDATE.digest(payload)
            approved = root / ".humanize/flows/.approved"
            approved.mkdir()
            collision = approved / f"slot-{slot_digest}.py"
            collision.write_bytes(b"different")
            collision.chmod(0o444)
            approved.chmod(0o555)
            before = (root / ".humanize/flows/slot.py").read_bytes()
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                    "--candidate",
                    str(candidate),
                    "--install-slot",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertIn("different bytes", completed.stderr)
            self.assertEqual((root / ".humanize/flows/slot.py").read_bytes(), before)
            approved.chmod(0o700)

    def test_launcher_command_preserves_supported_routes_without_fake_account_provider(self) -> None:
        executor = {
            "name": "frontier_execute",
            "backend": "codex",
            "model": "gpt-6-astra",
            "effort": "max",
            "upstreamProviderFamily": "openai",
            "modelFamily": "gpt-6-astra",
        }
        reviewer = {
            "name": "supported-review-fixture",
            "backend": "claude",
            "model": "claude-opus-5-thinking-max",
            "effort": "max",
            "upstreamProviderFamily": "anthropic",
            "modelFamily": "claude-opus-5",
        }
        command = RUN_DEFAULT.build_command(
            ["/pinned/.venv/bin/python", "-I", "-c", "trusted-launch", "/pinned/src"],
            Path("/project/.humanize/flows/default.py"),
            executor,
            reviewer,
            "bounded task",
        )
        self.assertEqual(command[:5], ["/pinned/.venv/bin/python", "-I", "-c", "trusted-launch", "/pinned/src"])
        self.assertIn(
            "cli=codex,model=gpt-6-astra,effort=max,permission=workspace-write",
            command,
        )
        self.assertIn(
            "cli=claude,model=claude-opus-5-thinking-max,effort=max,permission=read-only",
            command,
        )
        self.assertFalse(any("provider=" in argument for argument in command))

    def test_cursor_advisor_fails_closed_without_a_backend_relabel(self) -> None:
        raw = json.dumps(verified_role_document(), sort_keys=True).encode("utf-8")
        receipt = self._canonical_resolver(
            raw,
            task_class="verification",
            reasoning_posture="balanced",
            semantic_role="advisor",
            review_mode="exact-opposite",
            writer_role="frontier_execute",
            catalog_runner=canned_private_catalog_runner,
        )
        self.assertEqual("claude", receipt["resolved"]["backend"])
        self.assertEqual(
            "claude-opus-5",
            receipt["resolved"]["catalogModelId"],
        )
        with self.assertRaisesRegex(
            VALIDATE.FlowValidationError,
            r"cursor.*default launcher has no Cursor execution route.*must not be relabelled.*Claude",
        ):
            RUN_DEFAULT._require_supported_backends([
                {
                    "semanticRole": "advisor",
                    "receipt": {
                        "resolved": {
                            "backend": "cursor",
                            "catalogModelId": "claude-opus-5-thinking-max",
                        }
                    },
                }
            ])

    def test_launcher_rejects_self_consistent_non_max_reviewer_pairing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            document = json.loads(
                role_policy(Path(directory)).read_text(encoding="utf-8")
            )
        document["roles"]["advisor"]["effort"] = "low"
        document["capabilityPolicy"]["reviewPairings"][0]["reviewer"]["effort"] = "low"

        with self.assertRaisesRegex(VALIDATE.FlowValidationError, "(?:Max|advisor)"):
            RUN_DEFAULT.resolve_pair(document, "frontier_execute", "advisor")

    def test_launcher_reuses_complete_canonical_model_validator(self) -> None:
        document = verified_role_document()
        document["reasoningPolicy"]["crossFamilyEffortComparable"] = True
        with self.assertRaisesRegex(
            VALIDATE.FlowValidationError, "cross-family effort"
        ):
            RUN_DEFAULT.resolve_pair(document, "frontier_execute", "advisor")

    def test_launcher_rejects_drifted_resolver_receipt_fields(self) -> None:
        raw = json.dumps(verified_role_document(), sort_keys=True).encode("utf-8")
        resolver = self._canonical_resolver
        cases = (
            (
                "self-digest",
                lambda receipt: receipt.__setitem__("receiptSha256", "sha256:" + "0" * 64),
                False,
                "self-digest",
            ),
            (
                "request",
                lambda receipt: receipt["request"].__setitem__("reasoningPosture", "economy"),
                True,
                "request differs",
            ),
            (
                "policy",
                lambda receipt: receipt["policy"].__setitem__(
                    "modelsDocumentSha256", "sha256:" + "0" * 64
                ),
                True,
                "current policy bytes",
            ),
        )
        for name, mutate, reseal, expected in cases:
            with self.subTest(name=name):
                def forged_receipt(
                    *args: object, **kwargs: object
                ) -> dict[str, object]:
                    receipt = json.loads(
                        json.dumps(
                            resolver(
                                *args,
                                **kwargs,
                                catalog_runner=canned_private_catalog_runner,
                            )
                        )
                    )
                    if kwargs["semantic_role"] == "advisor":
                        mutate(receipt)
                        if reseal:
                            receipt["receiptSha256"] = _canonical_sha256(
                                {
                                    key: value
                                    for key, value in receipt.items()
                                    if key != "receiptSha256"
                                }
                            )
                    return receipt

                with mock.patch.object(
                    RUN_DEFAULT, "resolve_launch_spec", side_effect=forged_receipt
                ), self.assertRaisesRegex(VALIDATE.FlowValidationError, expected):
                    RUN_DEFAULT.resolve_activations(raw, "frontier_execute", "advisor")

    def test_launcher_seals_exact_fresh_receipt_per_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.dumps(verified_role_document(), sort_keys=True).encode("utf-8")
            activations = self._fresh_default_activations(raw)
            self.assertEqual(
                ["executor", "reviewer"],
                [activation["activationId"] for activation in activations],
            )
            self.assertEqual(
                ["none", "exact-opposite"],
                [activation["receipt"]["request"]["reviewMode"] for activation in activations],
            )
            self.assertEqual(
                "topIntelligence",
                activations[0]["receipt"]["request"]["taskClass"],
            )
            self.assertEqual(
                "verification",
                activations[1]["receipt"]["request"]["taskClass"],
            )
            evidence = RUN_DEFAULT.create_launch_evidence(
                root, "default", "fixed-run", activations, raw_document=raw
            )
            self.assertEqual("humanize-native-launch-evidence/v1", evidence["schema"])
            for binding in evidence["activations"]:
                receipt_path = root / binding["receiptPath"]
                self.assertTrue(receipt_path.is_file())
                self.assertEqual(
                    binding["receiptSha256"],
                    json.loads(receipt_path.read_text(encoding="utf-8"))["receiptSha256"],
                )
                self.assertEqual(
                    binding["requestSha256"],
                    _canonical_sha256(
                        json.loads(receipt_path.read_text(encoding="utf-8"))["request"]
                    ),
                )
                self.assertEqual(
                    binding["policySha256"],
                    _canonical_sha256(
                        json.loads(receipt_path.read_text(encoding="utf-8"))["policy"]
                    ),
                )
            RUN_DEFAULT.verify_launch_evidence(
                root,
                evidence,
                raw_document=raw,
                expected_activations=activations,
            )
            with self.assertRaisesRegex(
                VALIDATE.FlowValidationError, "exists|overwrite"
            ):
                RUN_DEFAULT.create_launch_evidence(
                    root, "default", "fixed-run", activations, raw_document=raw
                )

    def test_launch_evidence_requires_every_activation_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.dumps(verified_role_document(), sort_keys=True).encode("utf-8")
            activations = self._fresh_default_activations(raw)
            evidence = RUN_DEFAULT.create_launch_evidence(
                root, "default", "fixed-run", activations, raw_document=raw
            )
            duplicate = json.loads(json.dumps(evidence))
            duplicate["activations"][1] = dict(duplicate["activations"][0])
            unsigned = {
                key: value for key, value in duplicate.items() if key != "evidenceSha256"
            }
            duplicate["evidenceSha256"] = _canonical_sha256(unsigned)
            evidence_path = root / duplicate["evidencePath"]
            evidence_path.write_bytes(RUN_DEFAULT.canonical_bytes(duplicate) + b"\n")
            with self.assertRaisesRegex(
                VALIDATE.FlowValidationError, "every activation exactly once"
            ):
                RUN_DEFAULT.verify_launch_evidence(
                    root,
                    duplicate,
                    raw_document=raw,
                    expected_activations=activations,
                )

    def test_launch_evidence_rejects_symlink_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            outside = Path(directory) / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "workflow-output").mkdir()
            (root / "workflow-output/humanize").symlink_to(
                outside, target_is_directory=True
            )
            raw = json.dumps(verified_role_document(), sort_keys=True).encode("utf-8")
            activations = self._fresh_default_activations(raw)
            with self.assertRaisesRegex(
                VALIDATE.FlowValidationError, "link|directory|namespace"
            ):
                RUN_DEFAULT.create_launch_evidence(
                    root, "default", "fixed-run", activations, raw_document=raw
                )

    def test_launcher_cli_rejects_alternate_roles_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            canonical = role_policy(root)
            alternate = root / "alternate.json"
            alternate.write_bytes(canonical.read_bytes())
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_default.py"),
                    "--root",
                    str(root),
                    "--hmz",
                    str(fake_hmz(root, root / "capture.json")),
                    "--roles",
                    str(alternate),
                    "bounded task",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, completed.returncode)
            self.assertIn("unrecognized arguments: --roles", completed.stderr)

    def test_launcher_cli_rejects_reasoning_posture_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_default.py"),
                    "--root",
                    str(root),
                    "--hmz",
                    str(root / "hmz"),
                    "--reasoning-posture",
                    "economy",
                    "bounded task",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, completed.returncode)
            self.assertIn(
                "unrecognized arguments: --reasoning-posture", completed.stderr
            )

    def test_activation_plan_refuses_noncanonical_posture(self) -> None:
        document = verified_role_document()
        with self.assertRaisesRegex(
            VALIDATE.FlowValidationError, "defaultPosture"
        ):
            RUN_DEFAULT._activation_plans(
                document, "frontier_execute", "advisor", "economy"
            )

    def test_launcher_refuses_cursor_advisor_before_hmz_execution(self) -> None:
        with self.assertRaisesRegex(
            VALIDATE.FlowValidationError,
            r"cursor.*default launcher has no Cursor execution route",
        ):
            RUN_DEFAULT._require_supported_backends([
                {
                    "semanticRole": "advisor",
                    "receipt": {
                        "resolved": {
                            "backend": "cursor",
                            "catalogModelId": "claude-opus-5-thinking-max",
                        }
                    },
                }
            ])

    def test_validated_native_routes_reach_real_sealed_context_preparation(self) -> None:
        """A validated Claude/Codex pair must carry its receipt backend into staging."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_roles = json.dumps(verified_role_document(), sort_keys=True).encode()
            activations = self._fresh_default_activations(raw_roles)
            native_sources: dict[str, dict[str, Path]] = {}
            commands: dict[str, dict[str, object]] = {}
            for backend in ("claude", "codex"):
                native = root / "native" / backend
                native.mkdir(parents=True)
                executable = native / backend
                payload = f"#!/bin/sh\nexit 0 # {backend}\n".encode()
                executable.write_bytes(payload)
                executable.chmod(0o500)
                source_files = {backend: executable}
                if backend == "codex":
                    companion = native / "codex-code-mode-host"
                    companion.write_bytes(b"test codex companion\n")
                    companion.chmod(0o500)
                    source_files["codex-code-mode-host"] = companion
                executable_digest = RUN_DEFAULT.digest_bytes(payload)
                closure = [{
                    "role": "entrypoint",
                    "path": str(executable),
                    "sha256": executable_digest,
                }]
                commands[backend] = {
                    "argv": [backend, "catalog"],
                    "executable": str(executable),
                    "executableSha256": executable_digest,
                    "invokedArgv": [str(executable), "catalog"],
                    "executionClosure": closure,
                    "executionClosureSha256": RUN_DEFAULT.digest_bytes(
                        "\n".join(
                            f"{entry['role']}\t{entry['path']}\t{entry['sha256']}"
                            for entry in closure
                        ).encode()
                    ),
                }
                native_sources[backend] = source_files

            for activation in activations:
                receipt = activation["receipt"]
                evidence = receipt["policy"]["freshCatalogEvidence"]
                for backend, record in evidence.items():
                    record["command"] = commands[backend]
                receipt["receiptSha256"] = RUN_DEFAULT.canonical_sha256(
                    {key: value for key, value in receipt.items() if key != "receiptSha256"}
                )
            RUN_DEFAULT._validate_activations(raw_roles, activations)

            humanize_root = root / "humanize"
            source_root = humanize_root / "src"
            package_root = humanize_root / "packages"
            (source_root / "hmz").mkdir(parents=True)
            package_root.mkdir(parents=True)
            (source_root / "hmz/__init__.py").write_text("# source\n", encoding="utf-8")
            (package_root / "dependency.py").write_text("# package\n", encoding="utf-8")
            interpreter = humanize_root / "python"
            wrapper = humanize_root / "hmz"
            interpreter.write_bytes(b"test interpreter\n")
            wrapper.write_bytes(b"test wrapper\n")
            interpreter.chmod(0o500)
            wrapper.chmod(0o500)
            pinned = {
                "argvPrefix": [
                    str(interpreter), "-I", "-S", "-B", "-c", "test launcher",
                    str(source_root), str(package_root),
                ],
                "binary": str(wrapper),
                "interpreter": str(interpreter),
                "root": str(humanize_root),
                "revision": "test-revision",
                "repository": "test-repository",
                "version": "hmz test",
            }

            routes = RUN_DEFAULT._execution_context_routes(activations)
            with RUN_DEFAULT.build_execution_context(
                pinned=pinned,
                routes=routes,
                sources={"default-flow": b"print('sealed')\n"},
                account_mode="ambient",
                native_source_override=native_sources,
            ) as context:
                self.assertEqual({"claude", "codex"}, {
                    item.backend for item in context.native_files
                })
                self.assertTrue(all(context.is_sealed(fd) for fd in context.pass_fds))

    def test_launcher_seals_receipts_before_isolated_dispatch_and_rejects_tamper(self) -> None:
        for tamper in (False, True):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project(root)
                roles_path = role_policy(root)
                raw_roles = roles_path.read_bytes()
                activations = self._fresh_default_activations(raw_roles)
                saw_prelaunch_receipts = False

                def hmz_run(
                    command: list[str], **kwargs: object
                ) -> subprocess.CompletedProcess[str]:
                    nonlocal saw_prelaunch_receipts
                    environment = kwargs["env"]
                    evidence_path = root / environment[
                        "HUMANIZE_NATIVE_LAUNCH_EVIDENCE_PATH"
                    ]
                    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                    self.assertEqual(
                        environment["HUMANIZE_NATIVE_LAUNCH_EVIDENCE_SHA256"],
                        evidence["evidenceSha256"],
                    )
                    self.assertEqual(2, len(evidence["activations"]))
                    RUN_DEFAULT.verify_launch_evidence(
                        root,
                        evidence,
                        raw_document=raw_roles,
                        expected_activations=activations,
                    )
                    saw_prelaunch_receipts = True
                    if tamper:
                        receipt = root / evidence["activations"][0]["receiptPath"]
                        receipt.write_bytes(receipt.read_bytes() + b" ")
                    return subprocess.CompletedProcess(command, 0, "", "")

                stderr = io.StringIO()
                with (
                    mock.patch.object(
                        RUN_DEFAULT,
                        "validate_hmz_binary",
                        return_value={"argvPrefix": ["/trusted/python", "-c", "launcher"]},
                    ),
                    mock.patch.object(
                        RUN_DEFAULT,
                        "resolve_activations",
                        return_value=activations,
                    ),
                    mock.patch.object(
                        RUN_DEFAULT,
                        "build_execution_context",
                        side_effect=lambda **kwargs: FakeExecutionContext(**kwargs),
                    ),
                    mock.patch.object(
                        RUN_DEFAULT.subprocess, "run", side_effect=hmz_run
                    ),
                    contextlib.redirect_stderr(stderr),
                ):
                    status = RUN_DEFAULT.main([
                        "--root",
                        str(root),
                        "--hmz",
                        str(root / "hmz"),
                        "bounded task",
                    ])
                self.assertTrue(saw_prelaunch_receipts)
                if tamper:
                    self.assertEqual(2, status)
                    self.assertIn("replaced or modified", stderr.getvalue())
                else:
                    self.assertEqual(0, status, stderr.getvalue())
                approved = root / ".humanize/flows/.approved"
                if approved.exists():
                    approved.chmod(0o700)

    def test_seed_validates_without_importing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(len(result["defaultSha256"]), 64)
            self.assertEqual(len(result["slotSha256"]), 64)
            self.assertEqual(
                result["slotTarget"],
                f"versions/slot-{result['slotSha256']}.py",
            )

    def test_validator_requires_an_explicit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_project_flows.py"), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertIn("--trusted-seed --all-admitted is required", completed.stderr)

    def test_all_admitted_mode_rejects_unknown_native_python(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            (root / ".humanize/flows/unknown.py").write_text(
                "from hmz.flows import flow\n@flow\ndef run(agents, task):\n    pass\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--all-admitted",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertIn("not covered by a supported validation contract", completed.stderr)

    def test_all_admitted_rejects_unknown_executable_flow_directory_without_importing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            marker = root / "imported"
            unknown = root / ".humanize/flows/unknown"
            unknown.mkdir()
            (unknown / "__init__.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            reported = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(reported.returncode, 0, reported.stderr)
            self.assertEqual(
                ["unknown/__init__.py"],
                json.loads(reported.stdout)["unvalidatedFlows"],
            )
            self.assertFalse(marker.exists())
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--all-admitted",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertIn("unknown/__init__.py", completed.stderr)
            self.assertFalse(marker.exists())

    def test_all_admitted_reports_extensionless_symlink_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            project(root)
            marker = root / "imported"
            external = Path(outside) / "linked"
            external.mkdir()
            (external / "__init__.py").write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            (root / ".humanize/flows/linked").symlink_to(
                external, target_is_directory=True
            )

            reported = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, reported.returncode, reported.stderr)
            self.assertEqual(
                ["linked/__init__.py"],
                json.loads(reported.stdout)["unvalidatedFlows"],
            )
            self.assertFalse(marker.exists())

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--all-admitted",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, completed.returncode, completed.stdout)
            self.assertIn("linked/__init__.py", completed.stderr)
            self.assertFalse(marker.exists())

    def test_all_admitted_accepts_exact_shipped_research_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project_with_research_references(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--all-admitted",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["unvalidatedFlows"], [])
            self.assertEqual(
                result["admittedSchedulerFlow"], ["scheduler/__init__.py"]
            )
            self.assertEqual(
                sorted(result["admittedResearchReferences"]),
                ["_research_reference.py", "auto_research.py", "deep_research.py"],
            )

    def test_all_admitted_rejects_replaced_research_reference_bytes(self) -> None:
        for name in ("_research_reference.py", "auto_research.py", "deep_research.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project_with_research_references(root)
                target = root / ".humanize/flows" / name
                target.write_bytes(target.read_bytes() + b"\n# replaced bytes\n")
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "validate_project_flows.py"),
                        "--root",
                        str(root),
                        "--all-admitted",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertIn(name, completed.stderr)
                self.assertIn("controller-approved bytes", completed.stderr)

    def test_all_admitted_rejects_replaced_scheduler_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            scheduler = root / ".humanize/flows/scheduler/__init__.py"
            scheduler.write_bytes(scheduler.read_bytes() + b"\n# replaced bytes\n")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--all-admitted",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertIn("scheduler/__init__.py", completed.stderr)
            self.assertIn("controller-approved bytes", completed.stderr)

    def test_all_admitted_binds_every_flow_owned_scheduler_skill_file(self) -> None:
        attacks = ("changed", "extra", "link")
        for attack in attacks:
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project(root)
                skill = (
                    root
                    / ".humanize/flows/scheduler/skills/plateau-diagnosis/SKILL.md"
                )
                if attack == "changed":
                    skill.write_bytes(skill.read_bytes() + b"\nUnreviewed prompt.\n")
                elif attack == "extra":
                    extra = skill.parent / "references/unreviewed.md"
                    extra.parent.mkdir()
                    extra.write_text("unreviewed prompt\n", encoding="utf-8")
                else:
                    metadata = skill.parent / "agents/openai.yaml"
                    metadata.unlink()
                    metadata.symlink_to("../SKILL.md")

                completed = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "validate_project_flows.py"),
                        "--root",
                        str(root),
                        "--all-admitted",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(2, completed.returncode, completed.stdout)
                self.assertIn("scheduler", completed.stderr)
                self.assertTrue(
                    "controller-approved bytes" in completed.stderr
                    or "unvalidated mounted content" in completed.stderr
                    or "must not be a link" in completed.stderr,
                    completed.stderr,
                )

    def test_trusted_seed_mode_reports_ignored_non_seed_flows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            (root / ".humanize/flows/unknown.py").write_text(
                "raise RuntimeError('must never execute')\n", encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["scope"], "trusted-seed")
            self.assertEqual(result["unvalidatedFlows"], ["unknown.py"])

    def test_all_admitted_ignores_nested_non_discovery_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            nested = root / ".humanize/flows/nested"
            nested.mkdir()
            (nested / "not-discovered.py").write_text(
                "raise RuntimeError('nested file must never execute')\n", encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--all-admitted",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["unvalidatedFlows"], [])

    def test_all_admitted_rejects_unknown_underscore_helper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            (root / ".humanize/flows/_helper.py").write_text(
                "raise RuntimeError('helper must never execute')\n", encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--all-admitted",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertIn("unknown executable helper", completed.stderr)

    def test_all_admitted_failure_does_not_install_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            slot = root / ".humanize/flows/slot.py"
            before = slot.read_bytes()
            candidate = root / "candidate.py"
            candidate.write_bytes(
                before.replace(b"Replaceable project slot", b"Candidate project slot")
            )
            (root / ".humanize/flows/unknown.py").write_text(
                "raise RuntimeError('must never execute')\n", encoding="utf-8"
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--all-admitted",
                    "--candidate",
                    str(candidate),
                    "--install-slot",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertEqual(slot.read_bytes(), before)
            self.assertFalse((root / ".humanize/flows/.approved").exists())

    def test_validation_never_executes_candidate_top_level(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            marker = root / "ran"
            candidate = root / "candidate.py"
            candidate.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('ran')\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                    "--candidate",
                    str(candidate),
                    "--install-slot",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(marker.exists())

    def test_atomic_install_accepts_valid_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            candidate = root / "candidate.py"
            original = (root / ".humanize/flows/slot.py").read_bytes()
            candidate.write_bytes(original.replace(b"Replaceable project slot", b"Replaceable trusted slot"))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                    "--candidate",
                    str(candidate),
                    "--install-slot",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            slot = root / ".humanize/flows/slot.py"
            self.assertTrue(slot.is_symlink())
            expected_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            self.assertEqual(
                os.readlink(slot), f"versions/slot-{expected_digest}.py"
            )
            version = slot.parent / os.readlink(slot)
            self.assertEqual(version.read_bytes(), candidate.read_bytes())
            self.assertEqual(version.stat().st_mode & 0o222, 0)
            self.assertEqual(version.stat().st_nlink, 1)

    def test_version_snapshot_write_failure_never_publishes_digest_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            payload = (root / ".humanize/flows/slot.py").read_bytes().replace(
                b"Replaceable project slot", b"Interrupted project slot"
            )
            slot_sha = VALIDATE.digest(payload)
            published = root / f".humanize/flows/versions/slot-{slot_sha}.py"

            with mock.patch.object(os, "write", side_effect=OSError("injected write failure")):
                with self.assertRaisesRegex(
                    VALIDATE.FlowValidationError, "could not be installed safely"
                ):
                    VALIDATE.install_version_snapshot(root, payload, slot_sha)

            self.assertFalse(published.exists())
            self.assertEqual(
                [],
                list((root / ".humanize/flows/versions").glob(f".slot-{slot_sha}.*.tmp")),
            )

    def test_versioned_slot_reader_rejects_unsafe_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            slot = root / ".humanize/flows/slot.py"
            original_target = os.readlink(slot)
            original_version = slot.parent / original_target
            payload = original_version.read_bytes()

            for target in (
                "/absolute/slot.py",
                "../slot.py",
                "versions/slot-not-a-digest.py",
                "versions/slot-" + "0" * 64 + ".py",
            ):
                with self.subTest(target=target):
                    slot.unlink()
                    slot.symlink_to(target)
                    with self.assertRaises(VALIDATE.FlowValidationError):
                        VALIDATE.read_versioned_slot(root)

            slot.unlink()
            slot.symlink_to(original_target)
            original_version.chmod(0o644)
            self.assertEqual(
                VALIDATE.read_versioned_slot(root)[2],
                VALIDATE.digest(payload),
            )
            original_version.chmod(0o664)
            original_version.write_bytes(payload + b"\n# digest mismatch\n")
            with self.assertRaisesRegex(
                VALIDATE.FlowValidationError, "filename digest does not match"
            ):
                VALIDATE.read_versioned_slot(root)
            self.assertNotEqual(original_version.stat().st_mode & 0o022, 0)

            original_version.write_bytes(payload)
            self.assertEqual(
                VALIDATE.read_versioned_slot(root)[2],
                VALIDATE.digest(payload),
            )
            self.assertEqual(original_version.stat().st_mode & 0o022, 0)

            external = root / "external-version.py"
            external.write_bytes(payload)
            external.chmod(0o444)
            original_version.unlink()
            os.link(external, original_version)
            with self.assertRaisesRegex(VALIDATE.FlowValidationError, "single-link"):
                VALIDATE.read_versioned_slot(root)

            for kind in ("linked-parent", "linked-target", "special-target"):
                with self.subTest(kind=kind), tempfile.TemporaryDirectory() as nested:
                    hostile = Path(nested)
                    project(hostile)
                    hostile_slot = hostile / ".humanize/flows/slot.py"
                    hostile_target = hostile_slot.parent / os.readlink(hostile_slot)
                    if kind == "linked-parent":
                        versions = hostile_slot.parent / "versions"
                        versions.rename(hostile_slot.parent / "versions-real")
                        versions.symlink_to("versions-real", target_is_directory=True)
                    elif kind == "linked-target":
                        outside_target = hostile / "outside-version.py"
                        outside_target.write_bytes(hostile_target.read_bytes())
                        outside_target.chmod(0o444)
                        hostile_target.unlink()
                        hostile_target.symlink_to("../../../outside-version.py")
                    else:
                        hostile_target.unlink()
                        os.mkfifo(hostile_target)
                    with self.assertRaises(VALIDATE.FlowValidationError):
                        VALIDATE.read_versioned_slot(hostile)

    def test_versioned_slot_readers_harden_umask_0002_git_checkouts(self) -> None:
        """A Git checkout cannot preserve the installed snapshot's read-only mode."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project(source)
            subprocess.run(["git", "init", "--quiet", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "add", "--all"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(source),
                    "-c",
                    "user.name=Humanize test",
                    "-c",
                    "user.email=humanize-test@example.invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "seed",
                ],
                check=True,
            )

            def checkout(name: str) -> Path:
                destination = root / name
                subprocess.run(
                    ["git", "clone", "--quiet", "--no-local", str(source), str(destination)],
                    check=True,
                    preexec_fn=lambda: os.umask(0o002),
                )
                return destination

            validator_checkout = checkout("validator-checkout")
            validator_version = (
                validator_checkout
                / ".humanize/flows"
                / os.readlink(validator_checkout / ".humanize/flows/slot.py")
            )
            self.assertNotEqual(validator_version.stat().st_mode & 0o022, 0)
            self.assertEqual(
                VALIDATE.read_versioned_slot(validator_checkout)[2],
                hashlib.sha256(validator_version.read_bytes()).hexdigest(),
            )
            self.assertEqual(validator_version.stat().st_mode & 0o022, 0)

            scheduler_checkout = checkout("scheduler-checkout")
            scheduler_version = (
                scheduler_checkout
                / ".humanize/flows"
                / os.readlink(scheduler_checkout / ".humanize/flows/slot.py")
            )
            self.assertNotEqual(scheduler_version.stat().st_mode & 0o022, 0)
            with mock.patch.object(sys, "dont_write_bytecode", True):
                scheduler = scheduler_namespace(
                    scheduler_checkout / ".humanize/flows/scheduler/__init__.py"
                )
            self.assertEqual(
                scheduler["_read_versioned_slot"](scheduler_checkout)[2],
                hashlib.sha256(scheduler_version.read_bytes()).hexdigest(),
            )
            self.assertEqual(scheduler_version.stat().st_mode & 0o022, 0)

    def test_atomic_install_rejects_candidate_symlink_without_changing_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            slot = root / ".humanize/flows/slot.py"
            before = slot.read_bytes()
            regular_candidate = root / "regular-candidate.py"
            regular_candidate.write_bytes(before)
            candidate_link = root / "candidate.py"
            candidate_link.symlink_to(regular_candidate)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                    "--candidate",
                    str(candidate_link),
                    "--install-slot",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertIn("not a link", completed.stderr)
            self.assertEqual(slot.read_bytes(), before)

    def test_atomic_install_rejects_candidate_through_symlink_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            project(root)
            slot = root / ".humanize/flows/slot.py"
            before = slot.read_bytes()
            outside = Path(directory) / "outside"
            outside.mkdir()
            (outside / "candidate.py").write_bytes(before)
            (root / "linked").symlink_to(outside, target_is_directory=True)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "validate_project_flows.py"),
                    "--root",
                    str(root),
                    "--trusted-seed",
                    "--candidate",
                    str(root / "linked/candidate.py"),
                    "--install-slot",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            self.assertEqual(slot.read_bytes(), before)

    def test_open_leaf_fd_remains_bound_when_path_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            slot = root / ".humanize/flows/slot.py"
            source = slot.read_bytes()
            intended = source.replace(b"Replaceable project slot", b"Intended project slot")
            replacement = source.replace(b"Replaceable project slot", b"Replacement project slot")
            candidate = root / "candidate.py"
            target = root / "replacement.py"
            candidate.write_bytes(intended)
            target.write_bytes(replacement)
            original_open = os.open
            swap_triggered = False

            def replace_leaf() -> None:
                candidate.unlink()
                candidate.symlink_to(target)

            def swap_after_leaf_open(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swap_triggered
                if dir_fd is None:
                    file_descriptor = original_open(path, flags, mode)
                else:
                    file_descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
                if path == candidate.name and not flags & os.O_DIRECTORY:
                    replace_leaf()
                    swap_triggered = True
                return file_descriptor

            with mock.patch.object(os, "open", new=swap_after_leaf_open):
                payload = VALIDATE.regular_bytes(candidate, "slot candidate")
            self.assertTrue(swap_triggered, "test seam did not replace the opened leaf name")
            self.assertTrue(candidate.is_symlink())
            self.assertEqual(candidate.resolve(), target.resolve())
            VALIDATE.validate_slot_bytes(payload)
            payload_digest = VALIDATE.validate_slot_bytes(payload)
            VALIDATE.install_versioned_slot(root, payload, payload_digest)
            self.assertEqual(slot.read_bytes(), intended)
            self.assertNotEqual(slot.read_bytes(), replacement)

    def test_rejects_forbidden_slot_import_and_call(self) -> None:
        for payload in (
            b"import subprocess\n",
            b"import os\nfrom hmz.flows import flow\nclass Agents(tuple): pass\n@flow\ndef run(agents: Agents, task: str):\n os.system('id')\n",
        ):
            with self.subTest(payload=payload), self.assertRaises(VALIDATE.FlowValidationError):
                VALIDATE.validate_slot_bytes(payload)

    def test_launcher_rejects_unpinned_executable_before_exec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            role_policy(root)
            capture = root / "capture.json"
            binary = fake_hmz(root, capture)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_default.py"),
                    "--root",
                    str(root),
                    "--hmz",
                    str(binary),
                    "bounded task",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            self.assertIn("canonical", completed.stderr)
            self.assertFalse(capture.exists(), "unpinned hmz reached subprocess dispatch")

    def test_launcher_rejects_unverified_catalog_before_exec(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project(root)
            role_policy(root, verified=False)
            capture = root / "capture.json"
            binary = fake_hmz(root, capture)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "run_default.py"),
                    "--root",
                    str(root),
                    "--hmz",
                    str(binary),
                    "bounded task",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(capture.exists())


if __name__ == "__main__":
    unittest.main()
