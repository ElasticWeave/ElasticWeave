from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import retire_legacy_hooks


FLOW_START = Path(".omp/hooks/pre/flow-start-lint.ts")
WORKFLOW_DISPATCH = Path(".omp/hooks/pre/workflow-dispatch.ts")
SCHEDULER = Path(".humanize/flows/scheduler.py")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class RetireLegacyHooksTest(unittest.TestCase):
    legacy_one = b"legacy flow start hook\n"
    legacy_two = b"legacy workflow dispatch hook\n"
    legacy_hashes = {
        FLOW_START: digest(legacy_one),
        WORKFLOW_DISPATCH: digest(legacy_two),
    }

    def write_hook(self, root: Path, relative: Path, content: bytes) -> Path:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def test_pins_the_two_exact_shipped_legacy_hashes(self) -> None:
        self.assertEqual(
            {
                FLOW_START: "156dc87ee37cc3e7ab480a10bea3f3fc21f7a45361d334671559decce6b3f021",
                WORKFLOW_DISPATCH: (
                    "34ed1aaac3c1bd2a0ed79f65813a07d8b260b0b64c50deb2d5ccf03d918b6b37"
                ),
            },
            retire_legacy_hooks.LEGACY_HOOK_SHA256,
        )

    def test_pins_the_exact_superseded_scheduler_hash(self) -> None:
        self.assertEqual(
            {
                **retire_legacy_hooks.LEGACY_HOOK_SHA256,
                SCHEDULER: (
                    "c122fbf891b175e90ddd6a02daf43ab17345d3468cc57b0fd77edc9ab30334b5"
                ),
            },
            retire_legacy_hooks.LEGACY_PROJECT_ARTIFACT_SHA256,
        )

    def test_exact_scheduler_file_is_removed_without_removing_directory_flow(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            old_scheduler = b"legacy scheduler\n"
            target = self.write_hook(root, SCHEDULER, old_scheduler)
            directory_entry = root / ".humanize/flows/scheduler/__init__.py"
            directory_entry.parent.mkdir()
            directory_entry.write_bytes(b"new directory scheduler\n")

            plan = retire_legacy_hooks.preflight_retired_hooks(
                root,
                {SCHEDULER: digest(old_scheduler)},
            )
            removed = retire_legacy_hooks.apply_retired_hooks(plan)

            self.assertEqual([SCHEDULER], removed)
            self.assertFalse(target.exists())
            self.assertEqual(b"new directory scheduler\n", directory_entry.read_bytes())

    def test_modified_scheduler_blocks_default_retirement_and_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = self.write_hook(root, SCHEDULER, b"project-owned scheduler\n")

            with self.assertRaisesRegex(RuntimeError, "bytes do not match"):
                retire_legacy_hooks.preflight_retired_hooks(root)

            self.assertEqual(b"project-owned scheduler\n", target.read_bytes())

    def test_exact_legacy_regular_files_are_removed_together(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self.write_hook(root, FLOW_START, self.legacy_one)
            second = self.write_hook(root, WORKFLOW_DISPATCH, self.legacy_two)

            plan = retire_legacy_hooks.preflight_retired_hooks(root, self.legacy_hashes)
            removed = retire_legacy_hooks.apply_retired_hooks(plan)

            self.assertEqual([FLOW_START, WORKFLOW_DISPATCH], removed)
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertTrue((root / ".omp/hooks/pre").is_dir())

    def test_missing_targets_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = retire_legacy_hooks.preflight_retired_hooks(root, self.legacy_hashes)
            self.assertEqual([], retire_legacy_hooks.apply_retired_hooks(plan))
            self.assertFalse((root / ".omp").exists())

    def test_modified_file_rejects_plan_and_preserves_exact_peer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self.write_hook(root, FLOW_START, self.legacy_one)
            second = self.write_hook(root, WORKFLOW_DISPATCH, b"project-owned change\n")

            with self.assertRaisesRegex(RuntimeError, "bytes do not match"):
                retire_legacy_hooks.preflight_retired_hooks(root, self.legacy_hashes)

            self.assertEqual(self.legacy_one, first.read_bytes())
            self.assertEqual(b"project-owned change\n", second.read_bytes())

    def test_symlink_rejects_plan_without_following_or_deleting_peer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sentinel = root / "sentinel"
            sentinel.write_bytes(b"keep\n")
            first = self.write_hook(root, FLOW_START, self.legacy_one)
            second = root / WORKFLOW_DISPATCH
            second.symlink_to(sentinel)

            with self.assertRaisesRegex(RuntimeError, "symbolic link"):
                retire_legacy_hooks.preflight_retired_hooks(root, self.legacy_hashes)

            self.assertEqual(self.legacy_one, first.read_bytes())
            self.assertTrue(second.is_symlink())
            self.assertEqual(b"keep\n", sentinel.read_bytes())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires os.mkfifo")
    def test_special_file_rejects_plan_and_preserves_exact_peer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self.write_hook(root, FLOW_START, self.legacy_one)
            second = root / WORKFLOW_DISPATCH
            os.mkfifo(second)

            with self.assertRaisesRegex(RuntimeError, "special file"):
                retire_legacy_hooks.preflight_retired_hooks(root, self.legacy_hashes)

            self.assertEqual(self.legacy_one, first.read_bytes())
            self.assertTrue(second.exists())

    def test_symlinked_parent_is_rejected_without_touching_external_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as external_raw:
            root = Path(raw)
            external = Path(external_raw)
            self.write_hook(external, Path("pre/flow-start-lint.ts"), self.legacy_one)
            self.write_hook(external, Path("pre/workflow-dispatch.ts"), self.legacy_two)
            (root / ".omp").mkdir()
            (root / ".omp/hooks").symlink_to(external)

            with self.assertRaisesRegex(RuntimeError, "parent.*symbolic link"):
                retire_legacy_hooks.preflight_retired_hooks(root, self.legacy_hashes)

            self.assertEqual(self.legacy_one, (external / "pre/flow-start-lint.ts").read_bytes())
            self.assertEqual(
                self.legacy_two,
                (external / "pre/workflow-dispatch.ts").read_bytes(),
            )

    def test_change_after_preflight_is_detected_before_any_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self.write_hook(root, FLOW_START, self.legacy_one)
            second = self.write_hook(root, WORKFLOW_DISPATCH, self.legacy_two)
            plan = retire_legacy_hooks.preflight_retired_hooks(root, self.legacy_hashes)
            second.write_bytes(b"appeared project edit\n")

            with self.assertRaisesRegex(RuntimeError, "changed after preflight"):
                retire_legacy_hooks.apply_retired_hooks(plan)

            self.assertEqual(self.legacy_one, first.read_bytes())
            self.assertEqual(b"appeared project edit\n", second.read_bytes())

    def test_second_unlink_failure_restores_first_exact_hook(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self.write_hook(root, FLOW_START, self.legacy_one)
            second = self.write_hook(root, WORKFLOW_DISPATCH, self.legacy_two)
            first_mode = first.stat().st_mode & 0o777
            plan = retire_legacy_hooks.preflight_retired_hooks(root, self.legacy_hashes)
            policy_globals = retire_legacy_hooks.commit_plan.__globals__
            real_publish = policy_globals["_publish_action"]

            def fail_second(action, staged):
                if action.path == second:
                    raise OSError("injected second retired-hook failure")
                return real_publish(action, staged)

            with mock.patch.dict(
                policy_globals, {"_publish_action": fail_second}
            ):
                with self.assertRaisesRegex(RuntimeError, "rolled back"):
                    retire_legacy_hooks.apply_retired_hooks(plan)

            self.assertEqual(self.legacy_one, first.read_bytes())
            self.assertEqual(first_mode, first.stat().st_mode & 0o777)
            self.assertEqual(self.legacy_two, second.read_bytes())


if __name__ == "__main__":
    unittest.main()
