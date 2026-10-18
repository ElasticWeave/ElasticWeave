from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
SPEC = importlib.util.spec_from_file_location("sync_all", HERE / "sync_all.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AggregatePlanningTest(unittest.TestCase):
    def empty_plan(self, name: str, **metadata: object) -> object:
        return MODULE.SyncPlan(name, metadata)

    def test_default_builds_all_project_plans_without_user_surfaces(self) -> None:
        inputs = SimpleNamespace(role_document={})
        events: list[str] = []

        def planned(name: str, **metadata: object):
            def build(*args, **kwargs):
                events.append(name)
                return self.empty_plan(name, **metadata)

            return build

        with (
            mock.patch.object(
                MODULE, "load_canonical_inputs", return_value=inputs
            ) as load_inputs,
            mock.patch.object(MODULE, "preflight_retired_hooks", return_value=object()),
            mock.patch.object(
                MODULE,
                "build_retired_hooks_plan",
                side_effect=planned("retirement", retired_hooks=()),
            ),
            mock.patch.object(
                MODULE,
                "build_project_instruction_plan",
                side_effect=planned("instructions", project_instructions_changed=()),
            ),
            mock.patch.object(
                MODULE,
                "build_claude_agent_plan",
                side_effect=planned(
                    "claude",
                    claude_agent_count=0,
                    claude_agent_names=(),
                    claude_agent_skipped=(),
                ),
            ),
            mock.patch.object(MODULE, "build_codex_plan") as codex,
            mock.patch.object(MODULE, "build_kimi_plan") as kimi,
        ):
            plan = MODULE.build_aggregate_plan(
                Path("/project"),
                Path("/skills"),
                include_user_config=False,
            )

        self.assertEqual(["retirement", "instructions", "claude"], events)
        load_inputs.assert_called_once_with(
            Path("/project/config/workflows/models.json"),
            Path("/project/.omp/agents"),
        )
        codex.assert_not_called()
        kimi.assert_not_called()
        self.assertFalse(plan.metadata["user_config_enabled"])

    def test_opt_in_preflights_every_builder_before_returning_plan(self) -> None:
        inputs = SimpleNamespace(role_document={})
        events: list[str] = []

        def build(name: str, **metadata: object):
            def callback(*args, **kwargs):
                events.append(name)
                return self.empty_plan(name, **metadata)

            return callback

        with (
            mock.patch.object(
                MODULE, "load_canonical_inputs", return_value=inputs
            ) as load_inputs,
            mock.patch.object(MODULE, "preflight_retired_hooks", return_value=object()),
            mock.patch.object(
                MODULE,
                "build_retired_hooks_plan",
                side_effect=build("retirement", retired_hooks=()),
            ),
            mock.patch.object(
                MODULE,
                "build_project_instruction_plan",
                side_effect=build("instructions", project_instructions_changed=()),
            ),
            mock.patch.object(
                MODULE,
                "build_codex_plan",
                side_effect=build(
                    "codex",
                    codex_skills=(),
                    codex_agents=(),
                    codex_rules=(),
                    codex_skipped=(),
                ),
            ),
            mock.patch.object(
                MODULE,
                "build_kimi_plan",
                side_effect=build("kimi", kimi_config="/kimi/config.toml"),
            ),
            mock.patch.object(
                MODULE,
                "build_claude_agent_plan",
                side_effect=build(
                    "claude",
                    claude_agent_count=0,
                    claude_agent_names=(),
                    claude_agent_skipped=(),
                ),
            ),
        ):
            plan = MODULE.build_aggregate_plan(
                Path("/project"),
                Path("/skills"),
                include_user_config=True,
            )

        self.assertEqual(
            ["retirement", "instructions", "claude", "codex", "kimi"],
            events,
        )
        load_inputs.assert_called_once()
        self.assertTrue(plan.metadata["user_config_enabled"])

    def test_late_claude_publication_failure_rolls_back_enabled_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "AGENTS.md"
            codex = root / "codex.toml"
            kimi = root / "kimi.toml"
            claude = root / "claude.md"
            hook = root / "legacy.ts"
            for path, content in (
                (project, b"old project\n"),
                (codex, b"old codex\n"),
                (kimi, b"old kimi\n"),
                (claude, b"old claude\n"),
                (hook, b"legacy hook\n"),
            ):
                path.write_bytes(content)

            def write_plan(name: str, path: Path, content: bytes, **metadata: object):
                plan = MODULE.SyncPlan(name, metadata)
                plan.add_directory_chain(root, mode=0o700)
                plan.add_write(path, content, mode=0o600)
                return plan

            retirement = MODULE.SyncPlan("retirement", {"retired_hooks": ("legacy.ts",)})
            retirement.add_directory_chain(root, mode=0o700)
            retirement.add_delete(hook)
            inputs = SimpleNamespace(role_document={})
            with (
                mock.patch.object(MODULE, "load_canonical_inputs", return_value=inputs),
                mock.patch.object(MODULE, "preflight_retired_hooks", return_value=object()),
                mock.patch.object(MODULE, "build_retired_hooks_plan", return_value=retirement),
                mock.patch.object(
                    MODULE,
                    "build_project_instruction_plan",
                    return_value=write_plan(
                        "instructions",
                        project,
                        b"new project\n",
                        project_instructions_changed=("AGENTS.md",),
                    ),
                ),
                mock.patch.object(
                    MODULE,
                    "build_codex_plan",
                    return_value=write_plan(
                        "codex",
                        codex,
                        b"new codex\n",
                        codex_skills=(),
                        codex_agents=(),
                        codex_rules=(),
                        codex_skipped=(),
                    ),
                ),
                mock.patch.object(
                    MODULE,
                    "build_kimi_plan",
                    return_value=write_plan(
                        "kimi", kimi, b"new kimi\n", kimi_config=str(kimi)
                    ),
                ),
                mock.patch.object(
                    MODULE,
                    "build_claude_agent_plan",
                    return_value=write_plan(
                        "claude",
                        claude,
                        b"new claude\n",
                        claude_agent_count=1,
                        claude_agent_names=("claude",),
                        claude_agent_skipped=(),
                    ),
                ),
            ):
                plan = MODULE.build_aggregate_plan(
                    root, root, include_user_config=True
                )

            policy_globals = MODULE.commit_plan.__globals__
            real_publish = policy_globals["_publish_action"]

            def fail_claude(action, staged):
                if action.path == claude:
                    raise OSError("injected Claude generation failure")
                return real_publish(action, staged)

            with mock.patch.dict(
                policy_globals, {"_publish_action": fail_claude}
            ):
                with self.assertRaisesRegex(
                    MODULE.SyncTransactionError, "rolled back"
                ):
                    MODULE.commit_plan(plan)

            self.assertEqual(b"old project\n", project.read_bytes())
            self.assertEqual(b"old codex\n", codex.read_bytes())
            self.assertEqual(b"old kimi\n", kimi.read_bytes())
            self.assertEqual(b"old claude\n", claude.read_bytes())
            self.assertEqual(b"legacy hook\n", hook.read_bytes())

    def test_kimi_publication_failure_rolls_back_prior_project_action(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = root / "skills"
            template = (
                skills
                / "update-platform-config"
                / "assets"
                / "project"
                / "config"
                / "kimi"
                / "hooks.toml"
            )
            template.parent.mkdir(parents=True)
            template.write_text(
                'command = "GENERAL_AGENT_REPO/hook.py"\n', encoding="utf-8"
            )
            project = root / "AGENTS.md"
            project.write_bytes(b"old project\n")
            kimi_home = root / ".kimi-code"
            kimi_home.mkdir()
            config = kimi_home / "config.toml"
            config.write_bytes(b"old kimi\n")

            plan = MODULE.SyncPlan("project-and-kimi")
            plan.add_directory_chain(root, mode=0o700)
            plan.add_write(project, b"new project\n", mode=0o600)
            plan.extend(
                MODULE.build_kimi_plan(
                    repo=root,
                    skills=skills,
                    kimi_home=kimi_home,
                    config=config,
                )
            )
            policy_globals = MODULE.commit_plan.__globals__
            real_publish = policy_globals["_publish_action"]

            def fail_kimi(action, staged):
                if action.path == config:
                    raise OSError("injected Kimi publication failure")
                return real_publish(action, staged)

            with mock.patch.dict(policy_globals, {"_publish_action": fail_kimi}):
                with self.assertRaises(MODULE.SyncTransactionError):
                    MODULE.commit_plan(plan)

            self.assertEqual(b"old project\n", project.read_bytes())
            self.assertEqual(b"old kimi\n", config.read_bytes())


class MainBoundaryTest(unittest.TestCase):
    def run_main(
        self,
        *,
        opted_in: bool,
        build_side_effect: BaseException | None = None,
        commit_side_effect: BaseException | None = None,
    ) -> tuple[int, mock.Mock, mock.Mock]:
        plan = MODULE.SyncPlan("aggregate")
        builder = mock.Mock(return_value=plan, side_effect=build_side_effect)
        commit = mock.Mock(side_effect=commit_side_effect)
        with (
            mock.patch.object(MODULE, "project_root", return_value=Path("/project")),
            mock.patch.object(MODULE, "skills_root", return_value=Path("/skills")),
            mock.patch.object(MODULE, "user_config_write_enabled", return_value=opted_in),
            mock.patch.object(MODULE, "build_aggregate_plan", builder),
            mock.patch.object(MODULE, "commit_plan", commit),
        ):
            result = MODULE.main()
        return result, builder, commit

    def test_main_commits_exactly_one_aggregate_plan(self) -> None:
        result, builder, commit = self.run_main(opted_in=True)
        self.assertEqual(0, result)
        builder.assert_called_once_with(
            Path("/project"), Path("/skills"), include_user_config=True
        )
        commit.assert_called_once_with(builder.return_value)

    def test_aggregate_preflight_failure_never_calls_commit(self) -> None:
        result, _, commit = self.run_main(
            opted_in=False,
            build_side_effect=RuntimeError("aggregate conflict"),
        )
        self.assertEqual(2, result)
        commit.assert_not_called()

    def test_complete_rollback_and_incomplete_rollback_have_distinct_status(self) -> None:
        complete = MODULE.SyncTransactionError("rolled back")
        incomplete = MODULE.SyncTransactionError(
            "rollback incomplete", rollback_incomplete=True
        )
        result, _, _ = self.run_main(opted_in=True, commit_side_effect=complete)
        self.assertEqual(2, result)
        result, _, _ = self.run_main(opted_in=True, commit_side_effect=incomplete)
        self.assertEqual(3, result)


if __name__ == "__main__":
    unittest.main()
