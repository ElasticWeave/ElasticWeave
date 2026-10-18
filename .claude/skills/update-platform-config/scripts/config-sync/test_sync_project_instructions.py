from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "sync_project_instructions", HERE / "sync_project_instructions.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

START = "<!-- general-agent:project-session-contract:start -->"
END = "<!-- general-agent:project-session-contract:end -->"
MODE_START = "<!-- general-agent:startup-mode:start -->"
MODE_END = "<!-- general-agent:startup-mode:end -->"


def contract(body: str = "canonical policy") -> str:
    return f"{START}\n{body}\n{END}\n"


def mode_contract(mode: str, *, shared: str = "shared policy") -> str:
    return contract(
        f"{shared}\n"
        f"{MODE_START}\n"
        f"### Startup operating mode: `{mode}`\n"
        f"{mode} startup policy\n"
        f"{MODE_END}\n"
        "tail policy"
    )


class ProjectInstructionSyncTest(unittest.TestCase):
    def test_creates_both_surfaces_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(contract(), encoding="utf-8")

            changed = MODULE.sync_project_instructions(root, source)
            self.assertEqual([Path("AGENTS.md"), Path("CLAUDE.md")], changed)
            first = {
                name: (root / name).read_bytes() for name in ("AGENTS.md", "CLAUDE.md")
            }
            self.assertEqual(contract().encode(), first["AGENTS.md"])
            self.assertEqual(first["AGENTS.md"], first["CLAUDE.md"])

            self.assertEqual([], MODULE.sync_project_instructions(root, source))
            self.assertEqual(
                first,
                {name: (root / name).read_bytes() for name in first},
            )

    def test_replaces_only_the_managed_block_and_preserves_user_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(contract("new policy"), encoding="utf-8")
            before = f"user prefix\n\n{START}\nold policy\n{END}\n\nuser suffix\n"
            for name in ("AGENTS.md", "CLAUDE.md"):
                (root / name).write_text(before, encoding="utf-8")

            MODULE.sync_project_instructions(root, source)

            expected = before.replace("old policy", "new policy")
            for name in ("AGENTS.md", "CLAUDE.md"):
                self.assertEqual(expected, (root / name).read_text(encoding="utf-8"))

    def test_replacement_preserves_crlf_suffix_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(contract("new policy"), encoding="utf-8")
            before = (
                b"user prefix\r\n"
                + START.encode()
                + b"\r\nold policy\r\n"
                + END.encode()
                + b"\r\nuser suffix\r\n"
            )
            target = root / "AGENTS.md"
            target.write_bytes(before)

            MODULE.sync_project_instructions(root, source, (Path("AGENTS.md"),))

            expected = (
                b"user prefix\r\n"
                + contract("new policy").encode().rstrip(b"\r\n")
                + b"\r\nuser suffix\r\n"
            )
            self.assertEqual(expected, target.read_bytes())

    def test_appends_without_rewriting_existing_user_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(contract(), encoding="utf-8")
            user = b"# Existing instructions\nuser-owned bytes\n"
            (root / "AGENTS.md").write_bytes(user)

            MODULE.sync_project_instructions(root, source)

            rendered = (root / "AGENTS.md").read_bytes()
            self.assertTrue(rendered.startswith(user))
            self.assertEqual(1, rendered.count(START.encode()))
            self.assertEqual(1, rendered.count(END.encode()))

    def test_preflights_both_targets_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(contract(), encoding="utf-8")
            agents = root / "AGENTS.md"
            agents.write_text("keep agents\n", encoding="utf-8")
            sentinel = root / "sentinel"
            sentinel.write_text("keep sentinel\n", encoding="utf-8")
            (root / "CLAUDE.md").symlink_to(sentinel)

            with self.assertRaisesRegex(RuntimeError, "CLAUDE.md.*regular file"):
                MODULE.sync_project_instructions(root, source)

            self.assertEqual("keep agents\n", agents.read_text(encoding="utf-8"))
            self.assertEqual("keep sentinel\n", sentinel.read_text(encoding="utf-8"))

    def test_second_publication_failure_restores_both_instruction_preimages(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(contract("new policy"), encoding="utf-8")
            agents = root / "AGENTS.md"
            claude = root / "CLAUDE.md"
            agents.write_bytes(b"old agents\n")
            claude.write_bytes(b"old claude\n")
            agents.chmod(0o640)
            plan = MODULE.build_project_instruction_plan(root, source)
            policy_globals = MODULE.commit_plan.__globals__
            real_publish = policy_globals["_publish_action"]

            def fail_claude(action, staged):
                if action.path == claude:
                    raise OSError("injected second instruction failure")
                return real_publish(action, staged)

            with mock.patch.dict(
                policy_globals, {"_publish_action": fail_claude}
            ):
                with self.assertRaisesRegex(RuntimeError, "rolled back"):
                    MODULE.commit_plan(plan)

            self.assertEqual(b"old agents\n", agents.read_bytes())
            self.assertEqual(0o640, agents.stat().st_mode & 0o777)
            self.assertEqual(b"old claude\n", claude.read_bytes())

    def test_rejects_duplicate_or_unbalanced_markers(self) -> None:
        candidates = (
            f"{START}\none\n{START}\ntwo\n{END}\n",
            f"{START}\nmissing end\n",
            f"{END}\nwrong order\n{START}\n",
        )
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    source = root / "canonical.md"
                    source.write_text(contract(), encoding="utf-8")
                    target = root / "AGENTS.md"
                    target.write_text(candidate, encoding="utf-8")
                    with self.assertRaisesRegex(RuntimeError, "managed markers"):
                        MODULE.sync_project_instructions(root, source)
                    self.assertEqual(candidate, target.read_text(encoding="utf-8"))

    def test_sync_preserves_an_existing_fast_startup_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(mode_contract("default"), encoding="utf-8")
            existing = (
                "user-owned prefix\n\n"
                + mode_contract("fast").replace("shared policy", "old shared policy")
                + "\nuser-owned suffix\n"
            )
            for name in ("AGENTS.md", "CLAUDE.md"):
                (root / name).write_text(existing, encoding="utf-8")

            MODULE.sync_project_instructions(root, source)

            for name in ("AGENTS.md", "CLAUDE.md"):
                rendered = (root / name).read_text(encoding="utf-8")
                self.assertIn("Startup operating mode: `fast`", rendered)
                self.assertNotIn("Startup operating mode: `default`", rendered)
                self.assertIn("shared policy", rendered)
                self.assertIn("user-owned prefix", rendered)
                self.assertIn("user-owned suffix", rendered)

            plan = MODULE.build_project_instruction_plan(root, source)
            self.assertEqual("fast", plan.metadata["project_instructions_mode"])

    def test_conflicting_existing_startup_modes_fail_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(mode_contract("default"), encoding="utf-8")
            agents = root / "AGENTS.md"
            claude = root / "CLAUDE.md"
            agents.write_text(mode_contract("default"), encoding="utf-8")
            claude.write_text(mode_contract("fast"), encoding="utf-8")
            before = {path: path.read_bytes() for path in (agents, claude)}

            with self.assertRaisesRegex(RuntimeError, "conflicting startup modes"):
                MODULE.sync_project_instructions(root, source)

            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_user_owned_markers_cannot_select_the_startup_mode(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(mode_contract("default"), encoding="utf-8")
            fake_user_text = (
                f"user prefix\n{MODE_START}\n"
                "### Startup operating mode: `fast`\n"
                f"{MODE_END}\n"
                f"{START}\nlegacy\n{END}\nuser suffix\n"
            )
            for name in ("AGENTS.md", "CLAUDE.md"):
                (root / name).write_text(fake_user_text, encoding="utf-8")

            MODULE.sync_project_instructions(root, source)

            for name in ("AGENTS.md", "CLAUDE.md"):
                rendered = (root / name).read_text(encoding="utf-8")
                managed = rendered.split(START, 1)[1].split(END, 1)[0]
                self.assertIn("Startup operating mode: `default`", managed)
                self.assertNotIn("Startup operating mode: `fast`", managed)
                self.assertIn("user prefix", rendered)
                self.assertIn("user suffix", rendered)

    def test_canonical_source_must_be_only_one_complete_managed_block(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "canonical.md"
            source.write_text(f"outside\n{contract()}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "canonical contract"):
                MODULE.sync_project_instructions(root, source)
            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / "CLAUDE.md").exists())

    def test_empty_or_symlink_canonical_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            empty = root / "empty.md"
            empty.write_bytes(b"")
            with self.assertRaisesRegex(RuntimeError, "canonical contract"):
                MODULE.sync_project_instructions(root, empty)

            actual = root / "actual.md"
            actual.write_text(contract(), encoding="utf-8")
            source = root / "canonical.md"
            source.symlink_to(actual)
            with self.assertRaisesRegex(RuntimeError, "regular file"):
                MODULE.sync_project_instructions(root, source)

            self.assertFalse((root / "AGENTS.md").exists())
            self.assertFalse((root / "CLAUDE.md").exists())


if __name__ == "__main__":
    unittest.main()
