#!/usr/bin/env python3
"""Real pinned-Humanize integration for scheduler discovery and child loading."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SKILL = PROJECT_ROOT / ".claude/skills/write-humanize-flow"
ASSET = SKILL / "assets/project/.humanize"
VALIDATOR = SKILL / "scripts/validate_project_flows.py"
PIN_CHECK = (
    PROJECT_ROOT
    / ".claude/skills/humanize-workflow/scripts/ensure_humanize.py"
)
CHILD_ROLES = (
    "executor",
    "reviewer",
    "planner",
    "preflight",
    "collector",
    "counterevidence",
    "reducer",
    "knowledge",
    "applicability",
    "experimenter",
)
OUTER_ROLES = ("scheduler", *CHILD_ROLES)
DEFAULT_AGENT_SPECS = (
    "codex/gpt-6-astra:max",
    "claude/claude-sonnet-5:max",
    "codex/gpt-6-astra:max",
    "claude/claude-opus-5:max",
    "claude/claude-opus-5:xhigh",
    "codex/gpt-5.6-luna:max",
    "codex/gpt-6-astra:max",
    "codex/gpt-6-astra:max",
    "codex/gpt-5.6-terra:max",
    "claude/claude-opus-5:xhigh",
    "codex/gpt-6-astra:xhigh",
)


def canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class RealHumanizeSchedulerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source_root = Path(sys.prefix).parent
        expected_binary = Path(sys.prefix) / "bin/hmz"
        checked = subprocess.run(
            [
                sys.executable,
                str(PIN_CHECK),
                "--check",
                "--target",
                str(source_root),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if checked.returncode != 0:
            raise RuntimeError(
                "run this probe with the canonical pinned Humanize .venv Python: "
                + checked.stderr.strip()
            )
        evidence = json.loads(checked.stdout)
        if Path(evidence["binary"]) != expected_binary:
            raise RuntimeError("pinned Humanize check did not bind this interpreter's hmz")
        sys.dont_write_bytecode = True

    def test_real_scheduler_config_uses_bounded_round_and_retry_defaults(self) -> None:
        namespace = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
        options = namespace["Config"]()
        self.assertEqual(50, options.max_rounds)
        self.assertEqual(4, options.scheduler_retry_rounds)
        self.assertEqual(
            (300.0, 600.0, 1800.0, 3600.0),
            namespace["_SCHEDULER_RETRY_DELAYS_SECONDS"],
        )

    def test_real_scheduler_mounts_only_the_admitted_plateau_skill(self) -> None:
        from hmz.flows.skills import brought

        mounted = brought(ASSET / "flows/scheduler")
        self.assertEqual(["plateau-diagnosis"], [skill.name for skill in mounted])
        text = (mounted[0].at / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Do not delay the first useful dispatch", text)
        self.assertIn("cheapest discriminating next experiment", text)

    def test_real_scheduler_requires_typed_cycle_plan_and_escalates_deviation(self) -> None:
        namespace = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
        policy = namespace["_legacy_autonomous_policy"]()
        payload = (
            json.dumps(
                {
                    "schema": "humanize-iteration-decision/v3",
                    "idea_review_disposition": "performed",
                    "risk_triggers": [],
                    "reward_hacking_risks": ["hardcoded expected output"],
                    "expected_outcome": "a bounded evidence-backed result",
                    "expected_measurement": "the acceptance measurement passes",
                    "strategy": "verify",
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        decision = namespace["_parse_cycle_decision"](payload, policy)
        self.assertEqual("verify", decision.strategy)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".humanize/scheduler").mkdir(parents=True)
            first = namespace["_record_history"](
                root,
                1,
                ".humanize/flows/slot.py",
                "failed: timeout while collecting evidence",
                decision=decision,
                task="real ledger probe",
            )
            second = namespace["_record_history"](
                root,
                2,
                ".humanize/flows/slot.py",
                "failed: timeout while collecting evidence",
                decision=decision,
                task="real ledger probe",
            )
            self.assertEqual("accessibility", first.deviation_class)
            self.assertTrue(second.structural_review_required)
            self.assertEqual("pivot", second.next_strategy)

    def test_real_scheduler_binds_child_evidence_before_recording_success(self) -> None:
        namespace = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
        decision = namespace["CycleDecision"](
            "performed",
            (),
            "the expected result is supported",
            "the acceptance measurement passes",
            "verify",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "workflow-output/humanize/probe/result.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text(
                '{"finding":"contradictory proxy gaming and reward hacking"}\n',
                encoding="utf-8",
            )
            reference = (
                "workflow-output/humanize/probe/result.json#sha256:"
                + hashlib.sha256(evidence.read_bytes()).hexdigest()
            )
            assessment = namespace["ChildAssessment"](
                observed_outcome="completed",
                expected_outcome_status="met",
                expected_measurement_status="met",
                deviation_class="none",
                evidence_lineage=[reference],
                reward_hacking_signals=[],
            )
            observation = namespace["_validated_child_assessment"](
                root, assessment
            )
            ledger = namespace["_cycle_ledger"](
                b"",
                round_number=1,
                target=".humanize/flows/slot.py",
                outcome=observation,
                decision=decision,
                task="real evidence probe",
            )
            self.assertEqual("reward_hacking", ledger.deviation_class)
            self.assertTrue(ledger.structural_review_required)
            self.assertEqual("met", ledger.expected_outcome_status)
            self.assertEqual("met", ledger.expected_measurement_status)
            self.assertIn(reference, ledger.evidence_lineage)

    def test_real_scheduler_resolves_pm_mode_and_expiry_fallback(self) -> None:
        namespace = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
        now = namespace["_parse_utc_instant"]("2030-01-01T00:00:00Z")
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
                        "human_unavailable_fallback": "autonomous_review",
                    }
                ),
                encoding="utf-8",
            )
            active = namespace["_resolve_iteration_policy"](root, now=now)
            self.assertEqual("human_guided", active.effective_mode)
            self.assertEqual("active", active.lease_status)

            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "2030-01-01T00:30:00Z", "2029-12-31T23:59:59Z"
                ),
                encoding="utf-8",
            )
            expired = namespace["_resolve_iteration_policy"](root, now=now)
            self.assertEqual("autonomous", expired.effective_mode)
            self.assertEqual("autonomous_review", expired.fallback_applied)
            prompt = namespace["_scheduler_prompt"](
                "real mode probe", 1, 50, "none", "none", expired
            )
            self.assertIn("review-heavy", prompt)
            self.assertIn("idea and counterevidence review before child execution", prompt)

    def test_real_scheduler_consumes_the_launchers_sealed_pm_issue_context(self) -> None:
        namespace = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        snapshot = {
            "iteration_mode_selected": "human_guided",
            "iteration_mode_effective": "human_guided",
            "human_checkin_lease_minutes": "60",
            "human_unavailable_fallback": "pause",
            "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
            "human_checkin_lease_status": "active",
            "iteration_mode_decision_at": "2030-01-01T00:00:00Z",
        }
        snapshot["iteration_mode_snapshot_sha256"] = canonical_sha256(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            issue = root / ".pm/issues/m1/i1.md"
            issue.parent.mkdir(parents=True)
            issue.write_text(
                "---\n"
                + "\n".join(f"{key}: {value}" for key, value in snapshot.items())
                + "\n---\n\nBound issue.\n",
                encoding="utf-8",
            )
            context = launcher["build_invocation_context"](
                root, pm_issue=issue.relative_to(root), human_feedback=None
            )
            descriptor, digest = launcher["_sealed_context"](context)
            prior_fd = os.environ.get(launcher["CONTEXT_FD_ENV"])
            prior_sha = os.environ.get(launcher["CONTEXT_SHA_ENV"])
            try:
                os.environ[launcher["CONTEXT_FD_ENV"]] = str(descriptor)
                os.environ[launcher["CONTEXT_SHA_ENV"]] = digest
                loaded = namespace["_load_invocation_context"](root)
                policy = namespace["_resolve_iteration_policy"](
                    root,
                    now=namespace["_parse_utc_instant"]("2030-01-01T00:05:00Z"),
                    invocation_context=loaded,
                )
            finally:
                os.close(descriptor)
                if prior_fd is None:
                    os.environ.pop(launcher["CONTEXT_FD_ENV"], None)
                else:
                    os.environ[launcher["CONTEXT_FD_ENV"]] = prior_fd
                if prior_sha is None:
                    os.environ.pop(launcher["CONTEXT_SHA_ENV"], None)
                else:
                    os.environ[launcher["CONTEXT_SHA_ENV"]] = prior_sha
            self.assertEqual("pm_issue", policy.policy_source)
            self.assertEqual("human_guided", policy.effective_mode)
            self.assertFalse(policy.legacy_default)

    def test_real_failed_turns_keep_the_scheduler_retry_contract(self) -> None:
        from hmz.agents import Failed, Unrecoverable

        namespace = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
        command = ["codex", "app-server", "--stdio"]
        transient = Failed(1, command, stderr="rate limit exceeded")
        hard = Failed(1, command, stderr="insufficient credits")
        unrecoverable = Unrecoverable(1, command, stderr="rate limit exceeded")

        self.assertIsInstance(transient, subprocess.CalledProcessError)
        self.assertTrue(namespace["_retryable_scheduler_error"](transient))
        self.assertFalse(namespace["_retryable_scheduler_error"](hard))
        self.assertFalse(namespace["_retryable_scheduler_error"](unrecoverable))

    def test_unclassified_native_exit_is_bounded_retryable(self) -> None:
        from hmz.agents import Failed

        namespace = runpy.run_path(str(ASSET / "flows/scheduler/__init__.py"))
        failed = Failed(1, ["codex", "app-server", "--stdio"], stderr="")
        self.assertTrue(namespace["_retryable_scheduler_error"](failed))

        invalid = Failed(
            1,
            ["codex", "app-server", "--stdio"],
            stderr="invalid request for unsupported model",
        )
        self.assertFalse(namespace["_retryable_scheduler_error"](invalid))

    def test_real_local_scheduler_offer_resolves_to_all_roles(self) -> None:
        from hmz.flows import found, wanted

        previous = Path.cwd()
        try:
            os.chdir(PROJECT_ROOT)
            offers = [
                offer
                for offer in found()
                if offer.whose == "local"
                and offer.name == "local/scheduler"
            ]
            self.assertEqual(1, len(offers))
            places = wanted(offers[0].name)
        finally:
            os.chdir(previous)

        self.assertEqual(OUTER_ROLES, tuple(place.name for place in places))
        self.assertTrue(places)
        self.assertTrue(all(place.goals_default is False for place in places))

    def test_real_single_file_offer_resolves_without_its_py_suffix(self) -> None:
        from hmz.flows import find, found, wanted

        source = """from hmz.flows import Agent, flow

@flow
def run(agents: tuple[Agent], task: str) -> None:
    pass
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flows = root / ".humanize/flows"
            flows.mkdir(parents=True)
            single = flows / "single.py"
            single.write_text(source, encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(root)
                offers = [offer for offer in found() if offer.whose == "local"]
                self.assertEqual(["local/single"], [offer.name for offer in offers])
                self.assertEqual(single.resolve(), Path(find(offers[0].name)))
                self.assertEqual(1, len(wanted(offers[0].name)))
            finally:
                os.chdir(previous)

    def test_real_tui_and_runner_accept_every_default_agent_in_scheduler_order(self) -> None:
        from hmz.cli import _line
        from hmz.flows import drives, wanted
        from hmz.runner import flow_and_agents, read_agent
        from hmz.tui import Humanize
        from hmz.tui.pick import Runs

        launcher = runpy.run_path(str(ASSET / "run_scheduler.py"))
        argv = launcher["scheduler_argv"]()
        self.assertEqual("hmz", argv[0])
        self.assertEqual(DEFAULT_AGENT_SPECS, tuple(argv[4::2]))

        tui_args = _line().parse_args(argv[1:])
        self.assertEqual("local/scheduler", tui_args.flow)
        self.assertEqual(list(DEFAULT_AGENT_SPECS), tui_args.agents)

        previous = Path.cwd()
        try:
            os.chdir(PROJECT_ROOT)
            places = wanted(tui_args.flow)
            app = Humanize(
                flow=tui_args.flow,
                agents=[
                    Runs(spec, goals=places[at].goals_default)
                    for at, spec in enumerate(tui_args.agents)
                ],
            )
            self.assertEqual(tui_args.flow, app._flow_named)
            self.assertEqual(DEFAULT_AGENT_SPECS, tuple(run.spec for run in app._models))
            self.assertTrue(all(run.goals is False for run in app._models))
            self.assertEqual(OUTER_ROLES, tuple(place.name for place in app._wanted))
            path, agents, task, config, container = flow_and_agents(
                [*argv[1:], "real-runner-default-agent-probe"]
            )
        finally:
            os.chdir(previous)

        self.assertEqual("local/scheduler", path)
        self.assertEqual(
            OUTER_ROLES,
            drives(PROJECT_ROOT / ".humanize/flows/scheduler"),
        )
        self.assertEqual("real-runner-default-agent-probe", task)
        self.assertIsNone(config)
        self.assertEqual("", container)
        self.assertEqual(len(OUTER_ROLES), len(agents))
        parsed = [read_agent(spec) for spec in DEFAULT_AGENT_SPECS]
        for role, agent, (
            profile,
            model,
            effort,
            service_tier,
            provider,
            permission,
            web_search,
            overrides,
        ) in zip(
            OUTER_ROLES, agents, parsed, strict=True
        ):
            with self.subTest(role=role):
                self.assertEqual(profile.name, agent.backend)
                self.assertEqual(model, agent.config.model)
                self.assertEqual(effort, agent.config.effort)
                self.assertEqual("default", service_tier)
                self.assertEqual("", provider)
                self.assertIsNone(permission)
                self.assertIsNone(web_search)
                self.assertEqual((), overrides)
                self.assertEqual("default", agent.config.service_tier)
                self.assertEqual("", agent.config.provider)
                self.assertEqual("bypass", agent.config.permission)
                self.assertTrue(agent.config.web_search)
                self.assertFalse(agent.config.goals)

    def test_real_cycle_monitor_exposes_turns_handovers_and_working_shape(self) -> None:
        from collections import Counter

        from hmz.tui.monitor import Monitor

        monitor = Monitor()
        monitor.begins("scheduler", "gpt-6-astra")
        monitor.ends("scheduler")
        monitor.begins("planner", "claude-opus-5")
        monitor.ends("planner")
        monitor.begins("scheduler", "gpt-6-astra")
        monitor.ends("scheduler")
        monitor.begins("reviewer", "gpt-6-astra")
        monitor.begins("executor", "claude-sonnet-5")

        self.assertEqual(
            Counter({"scheduler": 2, "planner": 1, "reviewer": 1, "executor": 1}),
            monitor.turns,
        )
        self.assertEqual(
            Counter(
                {
                    ("scheduler", "planner"): 1,
                    ("planner", "scheduler"): 1,
                    ("scheduler", "reviewer"): 1,
                    ("scheduler", "executor"): 1,
                }
            ),
            monitor.handovers,
        )
        self.assertEqual(["executor", "reviewer"], monitor.now_working())
        shape = monitor.shape()
        self.assertEqual(dict(monitor.turns), shape.turns)
        self.assertEqual(dict(monitor.handovers), shape.handovers)
        self.assertEqual(frozenset({"executor", "reviewer"}), shape.working)

    def test_real_load_opens_private_staged_slot_and_discovers_full_pool(self) -> None:
        from hmz.flows import drives, load

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ASSET, root / ".humanize", symlinks=True)
            installed_validator = (
                root
                / ".claude/skills/write-humanize-flow/scripts/validate_project_flows.py"
            )
            installed_validator.parent.mkdir(parents=True)
            shutil.copyfile(VALIDATOR, installed_validator)
            shutil.copytree(
                ASSET,
                root
                / ".claude/skills/write-humanize-flow/assets/project/.humanize",
                symlinks=True,
            )

            slot = root / ".humanize/flows/slot.py"
            seed = slot.parent / os.readlink(slot)

            def approve(candidate: Path) -> None:
                approved = subprocess.run(
                    [
                        sys.executable,
                        str(installed_validator),
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

            approve(seed)

            previous = Path.cwd()
            try:
                os.chdir(root)
                scheduler_path = root / ".humanize/flows/scheduler/__init__.py"
                self.assertEqual(OUTER_ROLES, drives(scheduler_path))
                scheduler = runpy.run_path(str(scheduler_path))
                validate, roles_for = scheduler["_load_slot_validator"](root)

                def assert_real_slot(expected: tuple[str, ...]) -> None:
                    with scheduler["_approved_slot"](
                        root,
                        ".humanize/flows/slot.py",
                        validate,
                        roles_for,
                    ) as admitted:
                        descriptor, selected_roles = admitted
                        self.assertEqual(expected, selected_roles)
                        with scheduler["_stage_slot_for_humanize"](
                            root, descriptor
                        ) as staged:
                            staged_path = Path(staged)
                            self.assertTrue(staged_path.is_file())
                            self.assertEqual(expected, drives(staged))
                            self.assertTrue(callable(load(staged)))
                        self.assertFalse(staged_path.exists())

                assert_real_slot(("executor", "reviewer"))
                richer = root / ".humanize/scheduler/candidate.py"
                source = seed.read_text(encoding="utf-8")
                old_roles = (
                    "class Agents(NamedTuple):\n"
                    "    \"\"\"The two-role seed subset selected from the scheduler's configured pool.\"\"\"\n\n"
                    "    executor: Agent\n"
                    "    reviewer: Agent\n"
                )
                new_roles = (
                    "class Agents(NamedTuple):\n"
                    "    \"\"\"A richer non-default-order subset of the configured pool.\"\"\"\n\n"
                    "    executor: Agent\n"
                    "    planner: Agent\n"
                    "    collector: Agent\n"
                    "    reviewer: Agent\n"
                )
                self.assertIn(old_roles, source)
                richer.write_text(source.replace(old_roles, new_roles, 1), encoding="utf-8")
                approve(richer)
                assert_real_slot(("executor", "planner", "collector", "reviewer"))
            finally:
                os.chdir(previous)
                approved_directory = root / ".humanize/flows/.approved"
                with contextlib.suppress(OSError):
                    approved_directory.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
