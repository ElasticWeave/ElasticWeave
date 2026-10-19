from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("sync_policy_transaction", HERE / "sync_policy.py")
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
import sys

sys.modules[SPEC.name] = POLICY
SPEC.loader.exec_module(POLICY)


class AggregateSyncTransactionTest(unittest.TestCase):
    def plan_for(self, root: Path, *writes: tuple[str, bytes]) -> object:
        plan = POLICY.SyncPlan("test")
        plan.add_directory_chain(root, mode=0o700)
        for name, content in writes:
            path = root / name
            plan.add_write(path, content, mode=0o600)
        return plan

    def test_second_instruction_write_failure_restores_first(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "AGENTS.md"
            second = root / "CLAUDE.md"
            first.write_bytes(b"old agents\n")
            second.write_bytes(b"old claude\n")
            os.chmod(first, 0o1640)
            plan = self.plan_for(
                root,
                ("AGENTS.md", b"new agents\n"),
                ("CLAUDE.md", b"new claude\n"),
            )
            real_publish = POLICY._publish_action

            def fail_second(action, staged):
                if action.path == second:
                    raise OSError("injected second instruction failure")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=fail_second):
                with self.assertRaisesRegex(POLICY.SyncTransactionError, "rolled back"):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"old agents\n", first.read_bytes())
            self.assertEqual(0o1640, first.stat().st_mode & 0o7777)
            self.assertEqual(b"old claude\n", second.read_bytes())

    def test_late_claude_failure_rolls_back_prior_project_and_user_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = self.plan_for(
                root,
                ("AGENTS.md", b"project\n"),
                ("codex.toml", b"codex\n"),
                ("kimi.toml", b"kimi\n"),
                ("claude-agent.md", b"claude\n"),
            )
            real_publish = POLICY._publish_action

            def fail_claude(action, staged):
                if action.path.name == "claude-agent.md":
                    raise OSError("injected Claude generation failure")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=fail_claude):
                with self.assertRaises(POLICY.SyncTransactionError):
                    POLICY.commit_plan(plan)

            self.assertEqual([], list(root.iterdir()))

    def test_codex_manifest_failure_restores_targets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "agent.toml"
            manifest = root / ".general-agent-sync.json"
            target.write_bytes(b"old target\n")
            manifest.write_bytes(b"old manifest\n")
            plan = self.plan_for(
                root,
                ("agent.toml", b"new target\n"),
                (".general-agent-sync.json", b"new manifest\n"),
            )
            real_publish = POLICY._publish_action

            def fail_manifest(action, staged):
                if action.path == manifest:
                    raise OSError("injected manifest failure")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=fail_manifest):
                with self.assertRaises(POLICY.SyncTransactionError):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"old target\n", target.read_bytes())
            self.assertEqual(b"old manifest\n", manifest.read_bytes())

    def test_kimi_late_failure_restores_project_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "AGENTS.md"
            kimi = root / "config.toml"
            project.write_bytes(b"old project\n")
            kimi.write_bytes(b"old kimi\n")
            plan = self.plan_for(
                root,
                ("AGENTS.md", b"new project\n"),
                ("config.toml", b"new kimi\n"),
            )
            real_publish = POLICY._publish_action

            def fail_kimi(action, staged):
                if action.path == kimi:
                    raise OSError("injected Kimi failure")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=fail_kimi):
                with self.assertRaises(POLICY.SyncTransactionError):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"old project\n", project.read_bytes())
            self.assertEqual(b"old kimi\n", kimi.read_bytes())

    def test_second_retired_hook_failure_restores_first(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first.ts"
            second = root / "second.ts"
            first.write_bytes(b"legacy one\n")
            second.write_bytes(b"legacy two\n")
            plan = POLICY.SyncPlan("retirement")
            plan.add_directory_chain(root, mode=0o700)
            plan.add_delete(first)
            plan.add_delete(second)
            real_publish = POLICY._publish_action

            def fail_second(action, staged):
                if action.path == second:
                    raise OSError("injected second retirement failure")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=fail_second):
                with self.assertRaises(POLICY.SyncTransactionError):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"legacy one\n", first.read_bytes())
            self.assertEqual(b"legacy two\n", second.read_bytes())

    def test_symlink_delete_rollback_restores_exact_link_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            link = root / "managed-link"
            later = root / "later"
            link.symlink_to("../exact-relative-target")
            later.write_bytes(b"old later\n")
            plan = POLICY.SyncPlan("symlink-delete")
            plan.add_directory_chain(root, mode=0o700)
            plan.add_delete(link)
            plan.add_write(later, b"new later\n", mode=0o600)
            real_publish = POLICY._publish_action

            def fail_later(action, staged):
                if action.path == later:
                    raise OSError("injected failure after link deletion")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=fail_later):
                with self.assertRaises(POLICY.SyncTransactionError):
                    POLICY.commit_plan(plan)

            self.assertTrue(link.is_symlink())
            self.assertEqual("../exact-relative-target", os.readlink(link))
            self.assertEqual(b"old later\n", later.read_bytes())

    def test_new_directories_disappear_after_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            nested = root / "new" / "agents"
            plan = POLICY.SyncPlan("directories")
            plan.add_directory_chain(nested, mode=0o700)
            plan.add_write(nested / "one", b"one\n", mode=0o600)
            plan.add_write(nested / "two", b"two\n", mode=0o600)
            real_publish = POLICY._publish_action

            def fail_second(action, staged):
                if action.path.name == "two":
                    raise OSError("injected late failure")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=fail_second):
                with self.assertRaises(POLICY.SyncTransactionError):
                    POLICY.commit_plan(plan)

            self.assertFalse(root.joinpath("new").exists())

    def test_directory_rollback_preserves_substitution_before_remove(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            nested = root / "new" / "agents"
            moved = root / "moved-created-directory"
            plan = POLICY.SyncPlan("directory-race")
            plan.add_directory_chain(nested, mode=0o700)
            plan.add_write(nested / "one", b"one\n", mode=0o600)
            plan.add_write(nested / "two", b"two\n", mode=0o600)
            real_publish = POLICY._publish_action
            real_noreplace = POLICY._rename_noreplace
            injected = False

            def fail_second(action, staged):
                if action.path.name == "two":
                    raise OSError("injected late failure")
                return real_publish(action, staged)

            def substitute_before_remove(parent, source, target):
                nonlocal injected
                if source == "agents" and ".ga-rmdir-" in target and not injected:
                    injected = True
                    nested.rename(moved)
                    nested.mkdir()
                return real_noreplace(parent, source, target)

            with (
                mock.patch.object(POLICY, "_publish_action", side_effect=fail_second),
                mock.patch.object(
                    POLICY, "_rename_noreplace", side_effect=substitute_before_remove
                ),
            ):
                with self.assertRaisesRegex(
                    POLICY.SyncTransactionError, "rollback incomplete"
                ):
                    POLICY.commit_plan(plan)

            self.assertTrue(nested.is_dir())
            self.assertEqual([], list(nested.iterdir()))
            self.assertTrue(moved.is_dir())

    def test_aggregate_duplicate_target_conflict_touches_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "shared"
            target.write_bytes(b"original\n")
            first = self.plan_for(root, ("shared", b"one\n"))
            second = self.plan_for(root, ("shared", b"two\n"))

            with self.assertRaisesRegex(RuntimeError, "duplicate transaction target"):
                first.extend(second)

            self.assertEqual(b"original\n", target.read_bytes())

    def test_target_change_between_plan_and_commit_aborts_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first"
            second = root / "second"
            first.write_bytes(b"old first\n")
            second.write_bytes(b"old second\n")
            plan = self.plan_for(root, ("first", b"new first\n"), ("second", b"new second\n"))
            second.write_bytes(b"concurrent second\n")

            with self.assertRaisesRegex(POLICY.SyncTransactionError, "before mutation"):
                POLICY.commit_plan(plan)

            self.assertEqual(b"old first\n", first.read_bytes())
            self.assertEqual(b"concurrent second\n", second.read_bytes())

    def test_all_outputs_are_staged_before_any_target_publication(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first"
            second = root / "second"
            first.write_bytes(b"old first\n")
            second.write_bytes(b"old second\n")
            plan = self.plan_for(
                root, ("first", b"new first\n"), ("second", b"new second\n")
            )
            real_stage = POLICY._stage_action

            def fail_second_stage(action, parent):
                if action.path == second:
                    raise OSError("injected second staging failure")
                return real_stage(action, parent)

            with mock.patch.object(POLICY, "_stage_action", side_effect=fail_second_stage):
                with self.assertRaises(POLICY.SyncTransactionError):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"old first\n", first.read_bytes())
            self.assertEqual(b"old second\n", second.read_bytes())
            self.assertEqual([], list(root.glob(".*.ga-stage-*")))

    def test_source_change_between_plan_and_commit_aborts_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "models.json"
            target = root / "generated"
            source.write_bytes(b"old model bytes\n")
            plan = self.plan_for(root, ("generated", b"generated\n"))
            plan.add_file_guard(source)
            source.write_bytes(b"new model bytes\n")

            with self.assertRaisesRegex(POLICY.SyncTransactionError, "before mutation"):
                POLICY.commit_plan(plan)

            self.assertFalse(target.exists())

    def test_direct_role_document_guard_binds_exact_model_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            models = root / "models.json"
            target = root / "generated"
            models.write_bytes(b'{"roles": {}}\n')
            plan = self.plan_for(root, ("generated", b"derived\n"))
            POLICY.add_role_document_guard(plan, models, {"roles": {}})
            models.write_bytes(b'{"roles": {}, "changed": true}\n')

            with self.assertRaisesRegex(
                POLICY.SyncTransactionError, "before mutation"
            ):
                POLICY.commit_plan(plan)

            self.assertFalse(target.exists())

    def test_canonical_model_agent_and_membership_guards_abort_before_output(self) -> None:
        for changed in ("models", "agent", "membership"):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                models = root / "models.json"
                agents = root / "agents"
                agent = agents / "worker.md"
                output = root / "generated"
                models.write_bytes(b'{"policy": "one"}\n')
                agents.mkdir()
                agent.write_bytes(b"agent one\n")
                model_state = POLICY.capture_path_state(models)
                directory_state = POLICY.capture_path_state(agents)
                agent_state = POLICY.capture_path_state(agent)
                inputs = POLICY.CanonicalInputs(
                    models,
                    model_state,
                    {},
                    agents,
                    directory_state,
                    (
                        POLICY.CanonicalAgentSource(
                            agent, agent_state, {}, "", None
                        ),
                    ),
                )
                plan = POLICY.SyncPlan("canonical-guards")
                POLICY.add_canonical_input_guards(plan, inputs)
                plan.add_directory_chain(root, mode=0o700)
                plan.add_write(output, b"derived\n", mode=0o600)

                if changed == "models":
                    models.write_bytes(b'{"policy": "two"}\n')
                elif changed == "agent":
                    agent.write_bytes(b"agent two\n")
                else:
                    (agents / "appeared.md").write_bytes(b"new member\n")

                with self.assertRaisesRegex(
                    POLICY.SyncTransactionError, "before mutation"
                ):
                    POLICY.commit_plan(plan)
                self.assertFalse(output.exists())

    def test_concurrent_edit_after_mutation_is_preserved_and_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first"
            second = root / "second"
            first.write_bytes(b"old first\n")
            second.write_bytes(b"old second\n")
            plan = self.plan_for(root, ("first", b"new first\n"), ("second", b"new second\n"))
            real_publish = POLICY._publish_action

            def edit_then_fail(action, staged):
                if action.path == second:
                    first.write_bytes(b"concurrent user edit\n")
                    raise OSError("injected failure after concurrent edit")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=edit_then_fail):
                with self.assertRaisesRegex(
                    POLICY.SyncTransactionError, "rollback incomplete"
                ):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"concurrent user edit\n", first.read_bytes())
            self.assertEqual(b"old second\n", second.read_bytes())

    def test_concurrent_replacement_of_deleted_target_retains_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            retired = root / "retired.ts"
            later = root / "later"
            retired.write_bytes(b"recoverable legacy bytes\n")
            later.write_bytes(b"old later\n")
            plan = POLICY.SyncPlan("delete-concurrency")
            plan.add_directory_chain(root, mode=0o700)
            plan.add_delete(retired)
            plan.add_write(later, b"new later\n", mode=0o600)
            real_publish = POLICY._publish_action

            def replace_then_fail(action, staged):
                if action.path == later:
                    retired.write_bytes(b"concurrent replacement\n")
                    raise OSError("injected failure after concurrent replacement")
                return real_publish(action, staged)

            with mock.patch.object(POLICY, "_publish_action", side_effect=replace_then_fail):
                with self.assertRaisesRegex(
                    POLICY.SyncTransactionError, "recoverable transaction preimage retained"
                ):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"concurrent replacement\n", retired.read_bytes())
            backups = list(root.glob(".retired.ts.ga-backup-*"))
            self.assertEqual(1, len(backups))
            self.assertEqual(b"recoverable legacy bytes\n", backups[0].read_bytes())
            self.assertEqual(b"old later\n", later.read_bytes())

    def test_move_aside_window_replacement_is_restored_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first"
            raced = root / "raced"
            first.write_bytes(b"old first\n")
            raced.write_bytes(b"old raced\n")
            plan = self.plan_for(
                root,
                ("first", b"new first\n"),
                ("raced", b"new raced\n"),
            )
            real_noreplace = POLICY._rename_noreplace
            injected = False

            def replace_before_move(parent, source, target):
                nonlocal injected
                if (
                    source == raced.name
                    and ".ga-backup-" in target
                    and not injected
                ):
                    injected = True
                    replacement = root / "replacement"
                    replacement.write_bytes(b"concurrent raced\n")
                    os.replace(replacement, raced)
                return real_noreplace(parent, source, target)

            with mock.patch.object(
                POLICY, "_rename_noreplace", side_effect=replace_before_move
            ):
                with self.assertRaisesRegex(
                    POLICY.SyncTransactionError, "rolled back"
                ):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"old first\n", first.read_bytes())
            self.assertEqual(b"concurrent raced\n", raced.read_bytes())
            self.assertEqual([], list(root.glob(".*.ga-stage-*")))

    def test_delete_window_replacement_is_restored_without_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first"
            retired = root / "retired"
            first.write_bytes(b"old first\n")
            retired.write_bytes(b"legacy\n")
            plan = self.plan_for(root, ("first", b"new first\n"))
            plan.add_delete(retired)
            real_noreplace = POLICY._rename_noreplace
            injected = False

            def replace_before_delete(parent, source, target):
                nonlocal injected
                if source == retired.name and not injected:
                    injected = True
                    replacement = root / "replacement"
                    replacement.write_bytes(b"concurrent retired\n")
                    os.replace(replacement, retired)
                return real_noreplace(parent, source, target)

            with mock.patch.object(
                POLICY, "_rename_noreplace", side_effect=replace_before_delete
            ):
                with self.assertRaisesRegex(
                    POLICY.SyncTransactionError, "rolled back"
                ):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"old first\n", first.read_bytes())
            self.assertEqual(b"concurrent retired\n", retired.read_bytes())
            self.assertEqual([], list(root.glob(".*.ga-backup-*")))

    def test_failure_between_move_aside_and_publish_restores_preimage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "target"
            target.write_bytes(b"old target\n")
            plan = self.plan_for(root, ("target", b"new target\n"))
            real_noreplace = POLICY._rename_noreplace

            def fail_staged_publish(parent, source, destination):
                if ".ga-stage-" in source and destination == target.name:
                    raise OSError("injected failure after recoverable move-aside")
                return real_noreplace(parent, source, destination)

            with mock.patch.object(
                POLICY, "_rename_noreplace", side_effect=fail_staged_publish
            ):
                with self.assertRaisesRegex(
                    POLICY.SyncTransactionError, "rolled back"
                ):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"old target\n", target.read_bytes())
            self.assertEqual([], list(root.glob(".*.ga-*")))

    def test_parent_replacement_cannot_redirect_descriptor_relative_publish(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            root = base / "managed"
            moved = base / "moved"
            root.mkdir()
            target = root / "target"
            target.write_bytes(b"old target\n")
            plan = self.plan_for(root, ("target", b"new target\n"))
            real_noreplace = POLICY._rename_noreplace
            injected = False

            def replace_parent_before_move(parent, source, target_name):
                nonlocal injected
                if source == target.name and not injected:
                    injected = True
                    root.rename(moved)
                    root.mkdir()
                    (root / "target").write_bytes(b"concurrent external target\n")
                return real_noreplace(parent, source, target_name)

            with mock.patch.object(
                POLICY, "_rename_noreplace", side_effect=replace_parent_before_move
            ):
                with self.assertRaises(POLICY.SyncTransactionError):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"concurrent external target\n", (root / "target").read_bytes())
            self.assertEqual(b"old target\n", (moved / "target").read_bytes())

    def test_rollback_move_window_preserves_concurrent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first"
            later = root / "later"
            first.write_bytes(b"old first\n")
            later.write_bytes(b"old later\n")
            plan = self.plan_for(
                root,
                ("first", b"new first\n"),
                ("later", b"new later\n"),
            )
            real_publish = POLICY._publish_action
            real_noreplace = POLICY._rename_noreplace
            first_moves = 0

            def fail_later(action, staged):
                if action.path == later:
                    raise OSError("injected later failure")
                return real_publish(action, staged)

            def replace_before_rollback_move(parent, source, target):
                nonlocal first_moves
                if source == first.name:
                    first_moves += 1
                    if first_moves == 2:
                        replacement = root / "replacement"
                        replacement.write_bytes(b"concurrent rollback edit\n")
                        os.replace(replacement, first)
                return real_noreplace(parent, source, target)

            with (
                mock.patch.object(POLICY, "_publish_action", side_effect=fail_later),
                mock.patch.object(
                    POLICY,
                    "_rename_noreplace",
                    side_effect=replace_before_rollback_move,
                ),
            ):
                with self.assertRaisesRegex(
                    POLICY.SyncTransactionError, "rollback incomplete"
                ):
                    POLICY.commit_plan(plan)

            self.assertEqual(b"concurrent rollback edit\n", first.read_bytes())
            self.assertEqual(b"old later\n", later.read_bytes())


if __name__ == "__main__":
    unittest.main()
