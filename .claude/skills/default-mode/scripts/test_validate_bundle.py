from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("validate_bundle", HERE / "validate_bundle.py")
assert SPEC is not None and SPEC.loader is not None
validate_bundle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validate_bundle
SPEC.loader.exec_module(validate_bundle)


class RemovedHarnessHygieneTests(unittest.TestCase):
    def test_root_humanize_must_match_its_canonical_owner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = root / ".claude/skills"
            canonical = (
                skills
                / "write-humanize-flow/assets/project/.humanize/flows/scheduler/__init__.py"
            )
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(b"reviewed scheduler\n")
            materialized = root / ".humanize/flows/scheduler/__init__.py"
            materialized.parent.mkdir(parents=True)
            materialized.write_bytes(canonical.read_bytes())

            errors: list[str] = []
            validate_bundle.validate_root_humanize(root, skills, errors)
            self.assertEqual([], errors)

            materialized.write_bytes(b"changed scheduler\n")
            errors = []
            validate_bundle.validate_root_humanize(root, skills, errors)
            self.assertTrue(any("bytes differ" in error for error in errors), errors)

    def test_root_humanize_allows_only_matching_content_addressed_slot_link(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = root / ".claude/skills"
            canonical_flows = (
                skills / "write-humanize-flow/assets/project/.humanize/flows"
            )
            canonical_versions = canonical_flows / "versions"
            canonical_versions.mkdir(parents=True)
            payload = b"reviewed slot\n"
            digest = __import__("hashlib").sha256(payload).hexdigest()
            target = f"versions/slot-{digest}.py"
            canonical_version = canonical_flows / target
            canonical_version.write_bytes(payload)
            canonical_version.chmod(0o644)
            (canonical_flows / "slot.py").symlink_to(target)

            materialized_flows = root / ".humanize/flows"
            materialized_versions = materialized_flows / "versions"
            materialized_versions.mkdir(parents=True)
            materialized_version = materialized_flows / target
            materialized_version.write_bytes(payload)
            materialized_version.chmod(0o644)
            materialized_slot = materialized_flows / "slot.py"
            materialized_slot.symlink_to(target)

            errors: list[str] = []
            validate_bundle.validate_root_humanize(root, skills, errors)
            self.assertEqual([], errors)

            next_payload = b"next reviewed slot\n"
            next_digest = __import__("hashlib").sha256(next_payload).hexdigest()
            next_target = f"versions/slot-{next_digest}.py"
            next_version = materialized_flows / next_target
            next_version.write_bytes(next_payload)
            next_version.chmod(0o444)
            materialized_slot.unlink()
            materialized_slot.symlink_to(next_target)
            errors = []
            validate_bundle.validate_root_humanize(root, skills, errors)
            self.assertEqual([], errors)

            materialized_version.chmod(0o664)
            errors = []
            validate_bundle.validate_root_humanize(root, skills, errors)
            self.assertEqual([], errors)

            materialized_version.write_bytes(payload + b"tampered\n")
            errors = []
            validate_bundle.validate_root_humanize(root, skills, errors)
            self.assertTrue(any("digest" in error for error in errors), errors)
            materialized_version.write_bytes(payload)
            materialized_version.chmod(0o644)

            materialized_slot.unlink()
            materialized_slot.symlink_to("../outside.py")
            errors = []
            validate_bundle.validate_root_humanize(root, skills, errors)
            self.assertTrue(any("slot link target" in error for error in errors), errors)

    def test_root_humanize_rejects_special_and_linked_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            canonical = (
                root
                / ".claude/skills/write-humanize-flow/assets/project/.humanize/flows/scheduler/__init__.py"
            )
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(b"reviewed scheduler\n")
            materialized = root / ".humanize/flows/scheduler/__init__.py"
            materialized.parent.mkdir(parents=True)
            materialized.write_bytes(canonical.read_bytes())

            fifo = root / ".humanize/flows/runtime.pipe"
            os.mkfifo(fifo)
            errors: list[str] = []
            validate_bundle.validate_root_humanize(
                root, root / ".claude/skills", errors
            )
            self.assertTrue(any("non-regular node" in error for error in errors), errors)
            fifo.unlink()

            materialized.unlink()
            os.link(canonical, materialized)
            errors = []
            validate_bundle.validate_root_humanize(
                root, root / ".claude/skills", errors
            )
            self.assertTrue(any("single-link" in error for error in errors), errors)

    def test_any_skill_asset_bytecode_fails_closed(self) -> None:
        cases = (
            Path(".claude/skills/example-skill/assets/project/pkg/__pycache__/module.py"),
            Path(".claude/skills/example-skill/assets/project/pkg/__PYCACHE__/module.py"),
            Path(".claude/skills/example-skill/assets/project/pkg/__PyCache__/module.py"),
            Path(".claude/skills/example-skill/assets/project/pkg/module.pyc"),
            Path(".claude/skills/example-skill/assets/project/pkg/module.PYC"),
            Path(".claude/skills/example-skill/assets/project/pkg/module.pyo"),
            Path(".claude/skills/example-skill/assets/project/pkg/module.PYO"),
        )
        for relative in cases:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                skills = root / ".claude" / "skills"
                skills.mkdir(parents=True)
                for platform in (".agents", ".codex", ".kimi-code"):
                    link = root / platform / "skills"
                    link.parent.mkdir()
                    link.symlink_to("../.claude/skills")
                target = root / relative
                target.parent.mkdir(parents=True)
                target.write_bytes(b"fixture")

                errors = validate_bundle.validate(root)
                self.assertTrue(
                    any("runtime bytecode shipped in skill assets" in error for error in errors),
                    errors,
                )

    def test_retired_skill_and_artifact_markers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "SKILL.md"
            for marker in (
                "omh-workflow",
                "flowjanus-workflow",
                "example.omhflow",
                ".flowjanus",
                ".flowjanus/flows/example.py",
            ):
                with self.subTest(marker=marker):
                    self.assertTrue(validate_bundle.has_removed_harness_reference(path, marker))

    def test_unrelated_active_harness_text_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "SKILL.md"
            self.assertFalse(
                validate_bundle.has_removed_harness_reference(
                    path, "subagent-scheduler and humanize2 are active adapters"
                )
            )

    def test_opaque_authenticated_historical_evidence_is_exempt(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = (
                Path(raw)
                / "tests/evidence/historical-workflow-evidence/candidate/archive.txt"
            )
            self.assertFalse(
                validate_bundle.has_removed_harness_reference(path, "example.omhflow")
            )

    def test_retired_skill_directories_fail_bundle_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = root / ".claude" / "skills"
            skills.mkdir(parents=True)
            for platform in (".agents", ".codex", ".kimi-code"):
                link = root / platform / "skills"
                link.parent.mkdir()
                link.symlink_to("../.claude/skills")
            retired = skills / "omh-workflow"
            retired.mkdir()
            (retired / "SKILL.md").write_text(
                "---\nname: retired\ndescription: retired\n---\n", encoding="utf-8"
            )
            errors = validate_bundle.validate(root)
            self.assertTrue(
                any("removed harness skill still shipped" in error for error in errors), errors
            )

    def test_retired_standalone_skill_directories_fail_bundle_validation(self) -> None:
        for name in validate_bundle.RETIRED_STANDALONE_SKILL_DIRS:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                skills = root / ".claude" / "skills"
                skills.mkdir(parents=True)
                for platform in (".agents", ".codex", ".kimi-code"):
                    link = root / platform / "skills"
                    link.parent.mkdir()
                    link.symlink_to("../.claude/skills")
                retired = skills / name
                retired.mkdir()
                (retired / "SKILL.md").write_text(
                    "---\nname: retired\ndescription: retired\n---\n",
                    encoding="utf-8",
                )

                errors = validate_bundle.validate(root)

                self.assertTrue(
                    any(
                        f"retired standalone skill still shipped: .claude/skills/{name}"
                        in error
                        for error in errors
                    ),
                    errors,
                )

    def test_retired_startup_skill_and_paths_fail_bundle_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = root / ".claude" / "skills"
            skills.mkdir(parents=True)
            for platform in (".agents", ".codex", ".kimi-code"):
                link = root / platform / "skills"
                link.parent.mkdir()
                link.symlink_to("../.claude/skills")
            retired = "in" + "it"
            retired_skill = skills / retired
            retired_skill.mkdir()
            (retired_skill / "SKILL.md").write_text(
                "---\nname: retired\ndescription: retired\n---\n", encoding="utf-8"
            )
            active = skills / "active" / "SKILL.md"
            active.parent.mkdir()
            active.write_text(
                "---\nname: active\ndescription: active\n---\n"
                + f"Run .claude/skills/{retired}/scripts/materialize.py.\n"
                + f'BROKEN = SKILLS / "{retired}" / "assets"\n',
                encoding="utf-8",
            )

            errors = validate_bundle.validate(root)

            self.assertTrue(
                any("retired startup skill still shipped" in error for error in errors), errors
            )
            self.assertTrue(
                any("retired startup path reference" in error for error in errors), errors
            )


if __name__ == "__main__":
    unittest.main()
