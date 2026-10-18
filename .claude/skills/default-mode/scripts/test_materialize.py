from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("materialize", HERE / "materialize.py")
assert SPEC is not None and SPEC.loader is not None
materialize = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = materialize
SPEC.loader.exec_module(materialize)


class MaterializerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skills = materialize.skills_root(HERE / "materialize.py")

    def test_multimodel_consultation_reference_is_packaged(self):
        reference = (
            self.skills
            / "default-mode"
            / "references"
            / "multimodel-consultation.md"
        )
        self.assertTrue(reference.is_file())
        text = reference.read_text(encoding="utf-8")
        self.assertIn("highest-available", text)
        self.assertIn("result analysis", text)

    def test_collect_templates_skips_python_bytecode_case_insensitively(self):
        with tempfile.TemporaryDirectory() as raw:
            skills = Path(raw) / "skills"
            for skill, relative_source in materialize.PACKAGE_SOURCES:
                source = skills / skill / relative_source
                source.mkdir(parents=True)
                (source / f"{skill}.txt").write_text("fixture\n", encoding="utf-8")
            bytecode_root = skills / "default-mode" / "assets" / "project" / "pkg"
            for name in ("module.pyc", "module.PYC", "module.pyo", "module.PYO"):
                (bytecode_root / name).parent.mkdir(parents=True, exist_ok=True)
                (bytecode_root / name).write_bytes(b"compiled fixture")
            for dirname in ("__pycache__", "__PYCACHE__", "__PyCache__"):
                cached_source = bytecode_root / dirname / "module.py"
                cached_source.parent.mkdir(parents=True, exist_ok=True)
                cached_source.write_text("cached source fixture\n", encoding="utf-8")

            templates = materialize.collect_templates(skills)
            copied = {template.relative.as_posix() for template in templates}
            self.assertTrue(any(path.endswith(".txt") for path in copied))
            self.assertFalse(
                any(Path(path).suffix.lower() in {".pyc", ".pyo"} for path in copied),
                copied,
            )
            self.assertFalse(
                any(
                    any(part.casefold() == "__pycache__" for part in Path(path).parts)
                    for path in copied
                ),
                copied,
            )

    def test_collect_templates_rejects_slot_version_digest_mismatch(self):
        with tempfile.TemporaryDirectory() as raw:
            skills = Path(raw) / "skills"
            for skill, relative_source in materialize.PACKAGE_SOURCES:
                source = skills / skill / relative_source
                source.mkdir(parents=True)
                (source / f"{skill}.txt").write_text("fixture\n", encoding="utf-8")
            flows = (
                skills
                / "write-humanize-flow/assets/project/.humanize/flows"
            )
            versions = flows / "versions"
            versions.mkdir(parents=True)
            target = "versions/slot-" + "0" * 64 + ".py"
            (flows / target).write_bytes(b"digest mismatch\n")
            (flows / "slot.py").symlink_to(target)

            with self.assertRaisesRegex(RuntimeError, "unsafe Humanize slot link target"):
                materialize.collect_templates(skills)

    def test_empty_project_is_idempotent_and_omits_unselected_external_harnesses(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            created, unchanged = materialize.materialize(project, self.skills)
            self.assertTrue(created)
            self.assertFalse(unchanged)
            first = {path: (project / path).read_bytes() for path in created}

            second_created, second_unchanged = materialize.materialize(project, self.skills)
            self.assertEqual([], second_created)
            self.assertEqual(set(first), set(second_unchanged))
            self.assertEqual(first, {path: (project / path).read_bytes() for path in first})
            self.assertFalse((project / ".external-harness").exists())
            self.assertFalse((project / "config" / "external-harness").exists())
            self.assertFalse((project / ".pm" / "accept.sh").exists())
            self.assertTrue((project / ".memory" / "MAIN.md").is_file())
            self.assertTrue((project / ".memory" / "INDEX.md").is_file())
            humanize_flows = {
                path.relative_to(project).as_posix()
                for path in (project / ".humanize" / "flows").iterdir()
                if path.is_file()
            }
            self.assertEqual(
                humanize_flows,
                {
                    ".humanize/flows/_research_reference.py",
                    ".humanize/flows/auto_research.py",
                    ".humanize/flows/deep_research.py",
                    ".humanize/flows/default.py",
                    ".humanize/flows/slot.py",
                },
            )
            slot = project / ".humanize/flows/slot.py"
            self.assertTrue(slot.is_symlink())
            self.assertRegex(
                slot.readlink().as_posix(),
                r"^versions/slot-[0-9a-f]{64}\.py$",
            )
            self.assertTrue((slot.parent / slot.readlink()).is_file())
            self.assertTrue(
                (project / ".humanize/flows/scheduler/__init__.py").is_file()
            )
            self.assertTrue((project / ".humanize/README.md").is_file())
            self.assertTrue((project / ".humanize/run_scheduler.py").is_file())
            self.assertTrue((project / ".humanize/scheduler/README.md").is_file())
            self.assertTrue((project / ".humanize/scheduler/.gitignore").is_file())
            self.assertFalse((project / ".humanize/scheduler/dispatch.txt").exists())
            self.assertFalse((project / ".humanize/scheduler/history.jsonl").exists())
            for owner in ("write-humanize-flow", "deep-research-workflow"):
                source_root = self.skills / owner / "assets" / "project"
                for source in source_root.rglob("*"):
                    if (
                        source.is_file()
                        and not materialize.has_python_cache_component(source)
                        and source.suffix.lower()
                        not in materialize.PYTHON_BYTECODE_SUFFIXES
                    ):
                        destination = project / source.relative_to(source_root)
                        self.assertEqual(source.read_bytes(), destination.read_bytes())
                        self.assertEqual(
                            source.stat().st_mode & 0o777,
                            destination.stat().st_mode & 0o777,
                        )
            self.assertFalse(
                any(
                    path.suffix.lower() in materialize.PYTHON_BYTECODE_SUFFIXES
                    for path in project.rglob("*")
                )
            )
            self.assertFalse(
                any(materialize.has_python_cache_component(path) for path in project.rglob("*"))
            )
            constraint_template = (
                self.skills / "default-mode" / "references" / "constraint.template.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Cross-project transcripts are unverified leads only", constraint_template)
            self.assertIn("self-improve", constraint_template)
            self.assertIn("de-identification", constraint_template)
            self.assertIn("applicability boundary", constraint_template)
            self.assertIn("falsifier", constraint_template)

    def test_minimum_bootstrap_only_merges_contract_and_ignores_optional_conflicts(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            optional = project / ".humanize/flows/default.py"
            optional.parent.mkdir(parents=True)
            optional.write_text("# project-owned optional flow\n", encoding="utf-8")

            with mock.patch.object(materialize, "collect_templates") as collect:
                created, unchanged = materialize.materialize(
                    project,
                    self.skills,
                    mode="default",
                    minimum_bootstrap=True,
                )
            collect.assert_not_called()

            self.assertEqual({Path("AGENTS.md"), Path("CLAUDE.md")}, set(created))
            self.assertEqual([], unchanged)
            self.assertEqual(
                "# project-owned optional flow\n",
                optional.read_text(encoding="utf-8"),
            )
            for deferred in (".memory", ".pm", ".workflow", ".subagent", ".omp", "config"):
                self.assertFalse((project / deferred).exists(), deferred)
            extra_files = {
                path.relative_to(project)
                for path in project.rglob("*")
                if path.is_file()
            } - {Path("AGENTS.md"), Path("CLAUDE.md")}
            self.assertEqual(
                {Path(".humanize/flows/default.py")},
                extra_files,
            )

            second_created, second_unchanged = materialize.materialize(
                project,
                self.skills,
                mode="default",
                minimum_bootstrap=True,
            )
            self.assertEqual([], second_created)
            self.assertEqual({Path("AGENTS.md"), Path("CLAUDE.md")}, set(second_unchanged))
            constraint_template = (
                self.skills / "default-mode" / "references" / "constraint.template.md"
            ).read_text(encoding="utf-8")
            self.assertIn("owner review", constraint_template)
            self.assertIn("revalidated in this project", constraint_template)
            for instructions in ("AGENTS.md", "CLAUDE.md"):
                text = (project / instructions).read_text(encoding="utf-8")
                self.assertEqual(1, text.count("general-agent:project-session-contract:start"))
                self.assertIn("A top session is a coordinator, not an implementer", text)
                self.assertIn("`TERMINAL: COMPLETE`", text)
                self.assertIn("`TERMINAL: HUMAN_BLOCKED`", text)
                self.assertIn("`TERMINAL: REVISE`", text)
                self.assertIn("HUMAN-REQUEST-KIND: authorization|decision|credential|external-action", text)
                self.assertIn("EVIDENCE-COMMITMENT: sha256:<64hex>", text)
                self.assertIn("Select the iteration mode before dispatch", text)
                self.assertIn("`autonomous` or `human_guided`", text)
                self.assertIn("external human observation checkpoint before replanning", text)
                self.assertIn("active agent instruction/brief is the marked block", text)
                self.assertIn("SIMPLIFY_VERIFY_DECISION: required|not_required", text)
                self.assertIn("SIMPLIFY_VERIFY_REASON: <one concrete sentence>", text)
                self.assertIn("Bounded multi-model consultation", text)
                self.assertIn("planning proposal", text)
                self.assertIn("result analysis", text)
                self.assertIn("independent proposals", text)
                self.assertIn("does not replace terminal review", text)
            self.assertFalse(
                any(
                    path.suffix.lower() in materialize.PYTHON_BYTECODE_SUFFIXES
                    for path in project.rglob("*")
                )
            )
            self.assertFalse(
                any(materialize.has_python_cache_component(path) for path in project.rglob("*"))
            )

    def test_startup_mode_selects_only_the_requested_instruction_contract(self):
        expected = {
            "default": (
                "Startup operating mode: `default`",
                "implementation and workflow execution. Do not",
                "reward-hacking and jailbreak\nchecks",
                "expected outcome and measurement",
                "decide whether Simplify -> Verify is required",
                "SIMPLIFY_VERIFY_DECISION: required|not_required",
                "SIMPLIFY_VERIFY_REASON: <one concrete sentence>",
                "proposal or result-analysis consultation",
                "every native agent CLI turn 3600 seconds",
                "Startup readiness does not require terminal review",
                "minimum-bootstrap worker",
            ),
            "fast": (
                "Startup operating mode: `fast`",
                "selected PM iteration policy remains `human_guided`",
                "external human observation checkpoint",
                "decide whether Simplify -> Verify is required",
                "SIMPLIFY_VERIFY_DECISION: required|not_required",
                "SIMPLIFY_VERIFY_REASON: <one concrete sentence>",
                "one bounded proposal or result-analysis consultation",
                "every native agent CLI turn 3600 seconds",
                "Startup readiness does not require terminal review",
                "minimum-bootstrap worker",
            ),
        }
        for mode, required in expected.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
                project = Path(raw)
                materialize.materialize(project, self.skills, mode=mode)
                text = (project / "AGENTS.md").read_text(encoding="utf-8")
                normalized = " ".join(text.split())
                for phrase in required:
                    self.assertIn(" ".join(phrase.split()), normalized)
                for other in set(expected) - {mode}:
                    self.assertNotIn(
                        f"Startup operating mode: `{other}`", text,
                        f"selected contract leaked another startup mode: {other}",
                    )

    def test_mode_metadata_keeps_context_description_small(self):
        for mode in ("default-mode", "fast-mode"):
            metadata = (self.skills / mode / "SKILL.md").read_text(encoding="utf-8")
            description = metadata.split("---", 2)[1]
            body = metadata.split("---", 2)[2]
            self.assertIn("description:", description)
            self.assertLessEqual(len(description), 320)
            self.assertIn("SIMPLIFY_VERIFY_DECISION", body)
            self.assertIn("SIMPLIFY_VERIFY_REASON", body)
        naive = (self.skills / "naive-mode" / "SKILL.md").read_text(encoding="utf-8")
        description = naive.split("---", 2)[1]
        body = naive.split("---", 2)[2]
        self.assertIn("description:", description)
        self.assertLessEqual(len(description), 320)
        self.assertIn("SIMPLIFY_VERIFY_DECISION", body)
        self.assertIn("SIMPLIFY_VERIFY_REASON", body)
        self.assertIn("source project's agent instructions", body)

    def test_startup_mode_switch_replaces_only_managed_block(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            prefix = b"# user-owned instructions\n"
            suffix = b"\n# user-owned tail\n"
            old_block = (
                prefix
                + materialize.MANAGED_START
                + b"\nold mode\n"
                + materialize.MANAGED_END
                + suffix
            )
            (project / "AGENTS.md").write_bytes(old_block)
            materialize.materialize(project, self.skills, mode="default")
            materialize.materialize(project, self.skills, mode="fast")
            merged = (project / "AGENTS.md").read_bytes()
            self.assertTrue(merged.startswith(prefix))
            self.assertTrue(merged.endswith(suffix))
            self.assertIn(b"Startup operating mode: `fast`", merged)
            self.assertNotIn(b"Startup operating mode: `default`", merged)
            self.assertEqual(1, merged.count(materialize.MANAGED_START))
            self.assertEqual(1, merged.count(materialize.MANAGED_END))

    def test_live_mode_switch_preserves_shared_lifecycle_in_both_instruction_files(self):
        canonical = (
            self.skills / "default-mode" / "references" / "project-session-contract.md"
        ).read_bytes()
        lifecycle = canonical.split(b"### Managed session lifecycle\n", 1)[1].split(
            b"### Startup operating mode\n", 1
        )[0]
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            user_text = b"# Existing project instructions\n"
            for target in ("AGENTS.md", "CLAUDE.md"):
                (project / target).write_bytes(user_text)
            for mode in ("default", "fast", "default"):
                with self.subTest(mode=mode):
                    materialize.materialize(project, self.skills, mode=mode, minimum_bootstrap=True)
                    for target in ("AGENTS.md", "CLAUDE.md"):
                        output = (project / target).read_bytes()
                        self.assertTrue(output.startswith(user_text))
                        self.assertEqual(1, output.count(lifecycle))
                        self.assertIn(f"Startup operating mode: `{mode}`".encode(), output)
                        self.assertEqual(1, output.count(materialize.MODE_SECTION_START))

    def test_mode_switch_contract_updates_in_place(self):
        contract = (self.skills / "default-mode" / "references" / "project-session-contract.md").read_text(encoding="utf-8")
        default_skill = (self.skills / "default-mode" / "SKILL.md").read_text(encoding="utf-8")
        fast_skill = (self.skills / "fast-mode" / "SKILL.md").read_text(encoding="utf-8")
        contract_text = " ".join(contract.split())
        self.assertIn("atomically materialize", contract_text)
        self.assertIn("both `AGENTS.md` and `CLAUDE.md`", contract_text)
        self.assertIn("same top session", contract_text)
        for skill in (default_skill, fast_skill):
            normalized = " ".join(skill.split())
            self.assertIn("canonical materializer", normalized)
            self.assertIn("both root `AGENTS.md` and `CLAUDE.md`", normalized)
            self.assertIn("same top session", normalized)

    def test_invalid_startup_mode_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            with self.assertRaisesRegex(RuntimeError, "startup mode must be one of"):
                materialize.materialize(project, self.skills, mode="unknown")
            self.assertEqual([], list(project.iterdir()))

    def test_naive_mode_isolation_rejects_materialization_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            with self.assertRaisesRegex(RuntimeError, "naive mode is isolated"):
                materialize.materialize(project, self.skills, mode="naive")
            self.assertEqual([], list(project.iterdir()))

    def test_naive_mode_contract_requires_isolated_codex_invocation(self):
        skill = (self.skills / "naive-mode" / "SKILL.md").read_text(encoding="utf-8")
        for flag in ("--cd", "--ignore-user-config", "--ignore-rules", "--ephemeral"):
            self.assertIn(flag, skill)
        for handoff in (
            "NAIVE_EXIT_STATUS=",
            "--- FINAL MESSAGE ---",
            "--- STDOUT ---",
            "--- STDERR ---",
            'exit "$naive_status"',
            'naive_final_message="$(head -c 65536 "$naive_dir/return.txt"',
            'naive_stdout="$(head -c 65536 "$naive_dir/stdout.txt"',
            'naive_stderr="$(head -c 65536 "$naive_dir/stderr.txt"',
            "before its `EXIT`\n   trap removes the temporary directory",
        ):
            self.assertIn(handoff, skill)
        self.assertIn("Refuse the run if\n   `CODEX_HOME` resolves inside the source project", skill)
        self.assertIn("default-mode/scripts/materialize.py --mode naive", skill)
        self.assertIn("intentionally rejected", skill)

        shared_contract = (
            self.skills / "default-mode" / "references" / "project-session-contract.md"
        ).read_text(encoding="utf-8")
        self.assertIn("it never\nmaterializes project state or replaces this block", shared_contract)
        self.assertNotIn("default", materialize.STARTUP_MODE_INSTRUCTIONS)
        self.assertNotIn("naive", materialize.STARTUP_MODE_INSTRUCTIONS)

    def test_default_mode_uses_minimum_bootstrap_before_real_work(self):
        skill = (self.skills / "default-mode" / "SKILL.md").read_text(encoding="utf-8")
        headings = re.findall(r"^\d+\. \*\*(.+?)\*\*", skill, flags=re.MULTILINE)
        self.assertEqual(
            [
                "Adopt the repository and request.",
                "Materialize only when needed.",
                "Pin the first useful outcome.",
                "Resolve the first dispatch only.",
                "Start actual task work.",
            ],
            headings,
        )
        required = (
            "--mode default",
            "--minimum-bootstrap",
            "one measurable next step",
            "first dispatch",
            "opening cycle",
            "## Lazy reconciliation",
            "full model catalog",
            "marketplace search",
            "migration/*.draft.md",
            "staged evidence",
            "never pinned truth",
            "reproduced current-task failure",
            "one bounded repair",
            "resume the task",
            "## Integrity boundaries",
            "## Ready to dispatch when",
            "expected outcome and measurement",
            "Do not\nimplement detailed code, schedule individual workflow nodes",
            "actual task or milestone",
        )
        normalized = " ".join(skill.split())
        for phrase in required:
            self.assertIn(" ".join(phrase.split()), normalized)
        self.assertNotIn(".pm/cold_start_done", skill)
        self.assertNotIn("terminal artifact for this bounded\n   cold-start milestone", skill)

        retired_alias = "in" + "it"
        self.assertFalse((self.skills / retired_alias).exists())

        ui = (self.skills / "default-mode" / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn('display_name: "Default Mode"', ui)
        self.assertIn("$default-mode", ui)

    def test_fast_mode_starts_a_short_cycle_before_lazy_reconciliation(self):
        skill = (self.skills / "fast-mode" / "SKILL.md").read_text(encoding="utf-8")
        headings = re.findall(r"^\d+\. \*\*(.+?)\*\*", skill, flags=re.MULTILINE)
        self.assertEqual(
            [
                "Adopt and materialize only as needed.",
                "Bind the human-guided checkpoint.",
                "Pin the first experiment.",
                "Resolve the first dispatch only.",
                "Run the short cycle.",
            ],
            headings,
        )
        normalized = " ".join(skill.split())
        for required in (
            "--mode fast",
            "--minimum-bootstrap",
            "human_guided",
            "5..1440",
            "pause|autonomous_review",
            "one measurable next step",
            "opening cycle",
            "## Lazy reconciliation",
            "reproduced current-task failure",
            "one bounded repair",
            "resume the experiment",
            "actual task or milestone",
        ):
            self.assertIn(" ".join(required.split()), normalized)
        self.assertNotIn("complete fresh-project startup", skill)
        self.assertNotIn("eleven steps", skill)
        self.assertNotIn("cold-start completion contract", skill)

    def test_plateau_research_follows_evidence_instead_of_bootstrap(self):
        default_skill = (
            self.skills / "default-mode" / "SKILL.md"
        ).read_text(encoding="utf-8")
        fast_skill = (
            self.skills / "fast-mode" / "SKILL.md"
        ).read_text(encoding="utf-8")
        managed = (
            self.skills / "default-mode" / "references" / "project-session-contract.md"
        ).read_text(encoding="utf-8")
        research = (
            self.skills / "deep-research-workflow" / "SKILL.md"
        ).read_text(encoding="utf-8")

        for text in (default_skill, managed, research):
            normalized = " ".join(text.split())
            for required in (
                "task-scoped",
                "session",
                "primary papers",
                "official project",
                "reward hacking",
                "harness",
                "discriminating",
            ):
                self.assertIn(required, normalized)
        for text in (default_skill, managed):
            self.assertIn("startup hardening", " ".join(text.split()))

        default_startup = default_skill.split("## Minimum viable bootstrap", 1)[1].split(
            "## Lazy reconciliation", 1
        )[0]
        self.assertNotIn("search the live web", default_startup)

        normalized_fast = " ".join(fast_skill.split())
        self.assertIn("human checkpoint", normalized_fast)
        self.assertIn("feedback still leaves no discriminating direction", normalized_fast)
        self.assertIn("current primary papers", normalized_fast)
        self.assertIn("perfect infrastructure preemptively", normalized_fast)

    def test_generate_insight_is_a_visible_native_owner_lane(self):
        paths = (
            self.skills / "default-mode" / "SKILL.md",
            self.skills / "fast-mode" / "SKILL.md",
            self.skills / "pm-dispatch" / "SKILL.md",
            self.skills / "deep-research-workflow" / "SKILL.md",
            self.skills / "build-workflow" / "SKILL.md",
            self.skills / "run-workflow" / "SKILL.md",
            self.skills
            / "default-mode"
            / "references"
            / "project-session-contract.md",
        )
        for path in paths:
            with self.subTest(path=path):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("generate-insight", normalized)
                self.assertIn("native", normalized)
                self.assertIn("workflow", normalized)

        for path in (
            self.skills / "default-mode" / "agents" / "openai.yaml",
            self.skills / "fast-mode" / "agents" / "openai.yaml",
            self.skills / "deep-research-workflow" / "agents" / "openai.yaml",
        ):
            with self.subTest(agent_surface=path):
                self.assertIn(
                    "$generate-insight",
                    path.read_text(encoding="utf-8"),
                )

        insight = (
            self.skills / "generate-insight" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("`human_guided` runs at most one new verifying experiment", insight)
        self.assertIn("return the record in `RETHINK`", insight)
        self.assertIn("absence of feedback never relabels the\n  checkpoint as `FAILED`", insight)

        for mode in ("default", "fast"):
            with self.subTest(materialized_mode=mode), tempfile.TemporaryDirectory() as raw:
                project = Path(raw)
                materialize.materialize(
                    project,
                    self.skills,
                    mode=mode,
                    minimum_bootstrap=True,
                )
                managed = " ".join(
                    (project / "AGENTS.md").read_text(encoding="utf-8").split()
                )
                self.assertIn("Mechanistic insight lane", managed)
                self.assertIn("$generate-insight", managed)
                self.assertIn("Do not widen this into Deep Research", managed)

    def test_default_result_gate_is_explicit_and_not_materialized_into_fast(self):
        required = (
            "GENERATE_INSIGHT_DECISION=dispatch|deep_research|project_repair|not_applicable",
            "GENERATE_INSIGHT_REASON=",
            "GENERATE_INSIGHT_EVIDENCE=",
            "INSIGHT_RESULT_STATUS=SOLIDATED|FAILED|RETHINK|NOT_APPLICABLE",
            "INSIGHT_RESULT_REFS=",
            "SELF_EVOLVE_DISPOSITION=none|project_memory|repaired_surface|candidate_cross_task",
            "SELF_EVOLVE_REASON=",
            "SELF_EVOLVE_ARTIFACT_REFS=",
            "pm.py task-workspace",
            "Board or memory updates alone never count as self-evolution",
            "$self-improve",
            "before ordinary replanning",
        )
        skill = (self.skills / "default-mode" / "SKILL.md").read_text(encoding="utf-8")
        contract = (
            self.skills
            / "default-mode"
            / "references"
            / "project-session-contract.md"
        ).read_text(encoding="utf-8")
        ui = (self.skills / "default-mode" / "agents" / "openai.yaml").read_text(
            encoding="utf-8"
        )
        for text in (skill, contract):
            normalized = " ".join(text.split())
            for token in required:
                self.assertIn(" ".join(token.split()), normalized)
        normalized_ui = " ".join(ui.split())
        for token in (
            "GENERATE_INSIGHT_DECISION",
            "typed insight result status",
            "SELF_EVOLVE_DISPOSITION",
            "$self-improve",
            "before ordinary replanning",
        ):
            self.assertIn(" ".join(token.split()), normalized_ui)

        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            materialize.materialize(
                project,
                self.skills,
                mode="default",
                minimum_bootstrap=True,
            )
            default_managed = " ".join(
                (project / "AGENTS.md").read_text(encoding="utf-8").split()
            )
            for token in required:
                self.assertIn(" ".join(token.split()), default_managed)

            fast_project = project / "fast"
            fast_project.mkdir()
            materialize.materialize(
                fast_project,
                self.skills,
                mode="fast",
                minimum_bootstrap=True,
            )
            fast_managed = " ".join(
                (fast_project / "AGENTS.md").read_text(encoding="utf-8").split()
            )
            self.assertNotIn(
                "GENERATE_INSIGHT_DECISION=dispatch|deep_research|project_repair|not_applicable",
                fast_managed,
            )
            self.assertNotIn(
                "SELF_EVOLVE_DISPOSITION=none|project_memory|repaired_surface|candidate_cross_task",
                fast_managed,
            )

    def test_simplify_verify_admission_is_evidence_and_risk_based(self):
        paths = (
            self.skills / "default-mode" / "SKILL.md",
            self.skills / "fast-mode" / "SKILL.md",
            self.skills / "default-mode" / "references" / "project-session-contract.md",
            self.skills
            / "default-mode"
            / "references"
            / "review-and-simplify-verify.md",
            self.skills
            / "default-mode"
            / "references"
            / "native-cli-execution.md",
        )
        for path in paths:
            with self.subTest(path=path):
                text = " ".join(path.read_text(encoding="utf-8").split())
                for required in (
                    "Task type alone",
                    "ordinary code",
                    "speculative fallback",
                    "repeated failed revisions",
                    "high-impact",
                    "weakly measurable",
                    "explicit user",
                    "after candidate evidence",
                    "Focused tests",
                    "alignment review",
                    "terminal review",
                ):
                    self.assertIn(required, text)
                self.assertNotIn("normally needed for code", text)
                self.assertNotIn("Use `required` for code", text)

    def test_empty_project_publishes_slot_version_before_symlink(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            copied: list[Path] = []
            original = materialize.atomic_copy

            def record(source: Path, destination: Path) -> None:
                copied.append(destination.relative_to(project))
                original(source, destination)

            with mock.patch.object(materialize, "atomic_copy", side_effect=record):
                materialize.materialize(project, self.skills)

            slot = Path(".humanize/flows/slot.py")
            target = slot.parent / (project / slot).readlink()
            self.assertLess(copied.index(target), copied.index(slot))

    def test_existing_humanize_flow_collision_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            destination = project / ".humanize" / "flows" / "default.py"
            destination.parent.mkdir(parents=True)
            destination.write_text("# project-owned flow\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "existing bytes differ"):
                materialize.materialize(project, self.skills)

            self.assertEqual(
                "# project-owned flow\n", destination.read_text(encoding="utf-8")
            )
            self.assertFalse((project / ".memory").exists())
            self.assertFalse((project / "CLAUDE.md").exists())

    def test_valid_versioned_slot_retarget_survives_rematerialization(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            materialize.materialize(project, self.skills)
            slot = project / ".humanize/flows/slot.py"
            payload = slot.read_bytes().replace(
                b"Replaceable project slot", b"Versioned project slot"
            )
            digest = hashlib.sha256(payload).hexdigest()
            target = f"versions/slot-{digest}.py"
            version = slot.parent / target
            version.write_bytes(payload)
            version.chmod(0o644)
            slot.unlink()
            slot.symlink_to(target)

            created, unchanged = materialize.materialize(project, self.skills)

            self.assertEqual([], created)
            self.assertIn(Path(".humanize/flows/slot.py"), unchanged)
            self.assertEqual(target, slot.readlink().as_posix())
            self.assertEqual(payload, slot.read_bytes())

    def test_duplicate_humanize_asset_owners_fail_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            skills = Path(raw) / "skills"
            for skill, relative_source in materialize.PACKAGE_SOURCES:
                source = skills / skill / relative_source
                source.mkdir(parents=True)
                (source / f"{skill}.txt").write_text("fixture\n", encoding="utf-8")
            for owner in ("write-humanize-flow", "deep-research-workflow"):
                collision = (
                    skills
                    / owner
                    / "assets/project/.humanize/flows/collision.py"
                )
                collision.parent.mkdir(parents=True, exist_ok=True)
                collision.write_text(f"# {owner}\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "duplicate materializer destination"):
                materialize.materialize(project, skills)
            self.assertEqual([], list(project.iterdir()))

    def test_documented_default_launcher_matches_current_cli(self):
        skill = self.skills / "write-humanize-flow"
        launcher = skill / "scripts" / "run_default.py"
        completed = subprocess.run(
            [sys.executable, str(launcher), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        for flag in ("--executor-role", "--reviewer-role"):
            self.assertIn(flag, completed.stdout)
        self.assertNotIn("--roles", completed.stdout)
        self.assertNotIn("-a AGENT", completed.stdout)

        rejected = subprocess.run(
            [
                sys.executable,
                str(launcher),
                "--roles",
                "config/workflows/models.json",
                "--hmz",
                "/missing",
                "task",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn("unrecognized arguments: --roles", rejected.stderr)

        documented = (
            self.skills / "default-mode" / "references" / "installed-project-layout.md"
        ).read_text(encoding="utf-8")
        self.assertIn("`config/workflows/models.json` only", documented)
        self.assertIn("rejects `--roles`", documented)

    def test_unselected_external_harness_assets_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "project"
            project.mkdir()
            skills = Path(raw) / "skills"
            for skill, relative_source in materialize.PACKAGE_SOURCES:
                source = skills / skill / relative_source
                source.mkdir(parents=True)
                (source / f"{skill}.txt").write_text("fixture\n", encoding="utf-8")
            contract = skills / materialize.SESSION_CONTRACT_SOURCE
            contract.parent.mkdir(parents=True, exist_ok=True)
            contract.write_bytes(
                materialize.MANAGED_START + b"\nfixture\n" + materialize.MANAGED_END + b"\n"
            )
            external = (
                skills
                / "default-mode"
                / "assets"
                / "project"
                / ".external-harness"
                / "flow.py"
            )
            external.parent.mkdir(parents=True)
            external.write_text("fixture\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "unselected external harness asset"):
                materialize.materialize(project, skills)
            self.assertEqual([], list(project.iterdir()))

    def test_existing_root_instructions_are_preserved_around_managed_block(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            agents_prefix = b"# User AGENTS\n\nKeep this exactly.\n"
            claude_prefix = b"# User CLAUDE\r\n\r\nKeep CRLF prefix.\r\n"
            (project / "AGENTS.md").write_bytes(agents_prefix)
            (project / "CLAUDE.md").write_bytes(claude_prefix)
            (project / "AGENTS.md").chmod(0o600)

            materialize.materialize(project, self.skills)

            agents = (project / "AGENTS.md").read_bytes()
            claude = (project / "CLAUDE.md").read_bytes()
            self.assertTrue(agents.startswith(agents_prefix))
            self.assertTrue(claude.startswith(claude_prefix))
            self.assertEqual(1, agents.count(materialize.MANAGED_START))
            self.assertEqual(1, claude.count(materialize.MANAGED_START))
            self.assertEqual(0o600, (project / "AGENTS.md").stat().st_mode & 0o777)
            first = {name: (project / name).read_bytes() for name in ("AGENTS.md", "CLAUDE.md")}
            materialize.materialize(project, self.skills)
            self.assertEqual(first, {name: (project / name).read_bytes() for name in first})

    def test_existing_managed_block_is_replaced_without_touching_surrounding_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            old = (
                b"user-prefix\n"
                + materialize.MANAGED_START
                + b"\nold framework policy\n"
                + materialize.MANAGED_END
                + b"\nuser-suffix-without-final-newline"
            )
            (project / "AGENTS.md").write_bytes(old)

            materialize.materialize(project, self.skills)

            merged = (project / "AGENTS.md").read_bytes()
            self.assertTrue(merged.startswith(b"user-prefix\n" + materialize.MANAGED_START))
            self.assertTrue(merged.endswith(b"\nuser-suffix-without-final-newline"))
            self.assertNotIn(b"old framework policy", merged)
            self.assertIn(b"Universal terminal review", merged)

    def test_malformed_managed_markers_fail_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            (project / "AGENTS.md").write_bytes(b"user\n" + materialize.MANAGED_START + b"\n")

            with self.assertRaisesRegex(RuntimeError, "incomplete or duplicated"):
                materialize.materialize(project, self.skills)

            self.assertEqual(
                b"user\n" + materialize.MANAGED_START + b"\n",
                (project / "AGENTS.md").read_bytes(),
            )
            self.assertFalse((project / "CLAUDE.md").exists())
            self.assertFalse((project / ".workflow").exists())

    def test_conflict_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            sentinel = project / "config" / "workflows" / "models.json"
            sentinel.parent.mkdir(parents=True)
            sentinel.write_text("user-owned\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "existing bytes differ"):
                materialize.materialize(project, self.skills)
            self.assertEqual("user-owned\n", sentinel.read_text(encoding="utf-8"))
            self.assertFalse((project / ".workflow").exists())

    def test_symlink_destination_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            target = project / "sentinel"
            target.write_text("keep\n", encoding="utf-8")
            destination = project / "config" / "workflows" / "models.json"
            destination.parent.mkdir(parents=True)
            destination.symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                materialize.materialize(project, self.skills)
            self.assertEqual("keep\n", target.read_text(encoding="utf-8"))

    def test_root_instruction_symlink_fails_before_any_write(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            target = project / "sentinel"
            target.write_text("keep\n", encoding="utf-8")
            (project / "AGENTS.md").symlink_to(target)
            with self.assertRaisesRegex(RuntimeError, "not a regular file"):
                materialize.materialize(project, self.skills)
            self.assertEqual("keep\n", target.read_text(encoding="utf-8"))
            self.assertFalse((project / "CLAUDE.md").exists())
            self.assertFalse((project / ".workflow").exists())


if __name__ == "__main__":
    unittest.main()
