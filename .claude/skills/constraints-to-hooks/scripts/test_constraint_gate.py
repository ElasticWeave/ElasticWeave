#!/usr/bin/env python3
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("constraint_gate.py")
EXTRACTOR = Path(__file__).with_name("extract_constraints.py")
SPEC = importlib.util.spec_from_file_location("constraint_gate", SCRIPT)
constraint_gate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(constraint_gate)

EXTRACTOR_SPEC = importlib.util.spec_from_file_location(
    "extract_constraints_test", EXTRACTOR
)
extract_constraints = importlib.util.module_from_spec(EXTRACTOR_SPEC)
assert EXTRACTOR_SPEC.loader is not None
EXTRACTOR_SPEC.loader.exec_module(extract_constraints)


class ConstraintGateTests(unittest.TestCase):
    @staticmethod
    def _init_repo(root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Constraint Tests"], check=True)

    @staticmethod
    def _policy(path: Path, block: dict) -> None:
        path.write_text(
            "# Constraints\n<!-- constraints:deterministic -->\n```json\n"
            + json.dumps(block)
            + "\n```\n",
            encoding="utf-8",
        )

    def _framework_copy(self, root: Path, block: dict) -> tuple[Path, Path]:
        """Build a committed package copy so fallback provenance is exercised end to end."""
        framework = root / "framework"
        script = framework / ".claude/skills/constraints-to-hooks/scripts/constraint_gate.py"
        template = framework / ".claude/skills/default-mode/references/constraint.template.md"
        script.parent.mkdir(parents=True)
        template.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, script)
        self._policy(template, block)
        self._init_repo(framework)
        subprocess.run(["git", "-C", str(framework), "add", "."], check=True)
        subprocess.run(["git", "-C", str(framework), "commit", "-qm", "framework"], check=True)
        return script, template

    def test_protected_paths_are_only_the_project_declared_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp, "constraint.md")
            policy.write_text(
                """# Constraints
<!-- constraints:deterministic -->
```json
{"protected_paths": ["fixtures/**", ".pm/proofs/**"], "forbidden_diff_patterns": [], "pre_commit_checks": []}
```
""",
                encoding="utf-8",
            )
            block = constraint_gate.parse_block(policy)
            self.assertEqual(["fixtures/**", ".pm/proofs/**"], block["protected_paths"])
            self.assertEqual("fixtures/**", constraint_gate._path_matches("fixtures/golden.json", block["protected_paths"]))
            for removed in (".pm/accept.sh", ".pm/immutable", ".pm/verify_base"):
                self.assertIsNone(constraint_gate._path_matches(removed, block["protected_paths"]))

    def test_no_policy_has_no_implicit_gate_triad(self):
        self.assertEqual({}, constraint_gate.parse_block(None))
        for removed in (".pm/accept.sh", ".pm/immutable", ".pm/verify_base"):
            self.assertIsNone(constraint_gate._path_matches(removed, []))

    def test_globs_compile_to_path_prefixes_without_hidden_defaults(self):
        declared = ["tests/**", "fixtures/*.json", ".pm/proofs/"]
        prefixes = sorted({constraint_gate.glob_to_prefix(item) for item in declared})
        self.assertEqual([".pm/proofs/", "fixtures/", "tests/"], prefixes)
        self.assertNotIn(".pm/accept.sh", json.dumps(prefixes))

    def test_cross_project_channel_is_quarantined_from_constraint_and_section_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            home = Path(tmp, "home")
            root.mkdir()
            home.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            result = subprocess.run(
                ["python3", str(EXTRACTOR), "--repo", str(root), "--home", str(home)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("UNVERIFIED CROSS-PROJECT LEADS", result.stdout)
            self.assertIn("DO NOT distill these directly into constraint.md or deterministic §3", result.stdout)
            self.assertIn("self-improve transfer review", result.stdout)
            self.assertIn("this-project validation", result.stdout)

    def test_unicode_escaped_chinese_constraint_cues_remain_active(self):
        text = (
            "\u4e0d\u8981\u4fee\u6539\u56fa\u5b9a\u8bc4\u4f30\u6587\u4ef6"
            " fixtures/golden.json \u56e0\u4e3a\u8fd9\u662f\u5fc5\u987b\u4fdd\u7559\u7684\u7ea6\u675f\u3002"
        )
        self.assertIsNotNone(extract_constraints.KW.search(text))

    def test_raw_shell_check_is_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo").resolve()
            root.mkdir()
            self._init_repo(root)
            marker = root / "raw-shell-ran"
            policy = root / "policy.md"
            self._policy(policy, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [f"touch {marker}"],
            })
            subprocess.run(["git", "-C", str(root), "add", "policy.md"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "policy"], check=True)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "--constraint", str(policy), "check-commit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("pre_commit_checks", result.stderr)
            self.assertFalse(marker.exists(), "an invalid policy must never reach a shell")

    def test_structured_check_uses_literal_argv_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)
            marker = root / "should-not-exist"
            block = {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [["python3", "-c", "import sys; sys.exit(0)", ";", "touch", str(marker)]],
            }
            self.assertEqual([], constraint_gate.check_commit(root, block))
            self.assertFalse(marker.exists(), "argv data must not be interpreted by a shell")

    def test_space_containing_staged_path_is_not_split(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)
            locked = root / "locked dir" / "fixture.txt"
            locked.parent.mkdir()
            locked.write_text("ground truth\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "--", "locked dir/fixture.txt"], check=True)
            violations = constraint_gate.check_commit(root, {
                "protected_paths": ["locked dir/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            self.assertTrue(any("locked dir/fixture.txt" in item for item in violations), violations)

    def test_git_failure_is_a_policy_failure_not_a_clean_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = root / "policy.md"
            self._policy(policy, {
                "protected_paths": ["fixtures/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "--constraint", str(policy), "check-commit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("git", result.stderr.lower())

    def test_missing_repo_path_is_a_policy_failure_not_a_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp, "missing")
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(missing), "check-commit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("failing CLOSED", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_atomic_install_preserves_last_known_good_on_bad_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            target = root / ".omp/hooks/protected-paths.json"
            target.parent.mkdir(parents=True)
            target.write_text('["last-known-good/"]\n', encoding="utf-8")
            policy = root / "constraint.md"
            policy.write_text(
                "# Constraints\n<!-- constraints:deterministic -->\n```json\n{ malformed\n```\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "--constraint", str(policy),
                 "install-protected-paths", "--output", str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertEqual('["last-known-good/"]\n', target.read_text(encoding="utf-8"))

    def test_install_preserves_last_known_good_on_schema_incomplete_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            target = root / ".omp/hooks/protected-paths.json"
            target.parent.mkdir(parents=True)
            target.write_text('["last-known-good/"]\n', encoding="utf-8")
            policy = root / "constraint.md"
            self._policy(policy, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
            })

            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "--constraint", str(policy),
                 "install-protected-paths", "--output", str(target)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("missing field", result.stderr)
            self.assertEqual('["last-known-good/"]\n', target.read_text(encoding="utf-8"))

    def test_valid_project_policy_repairs_malformed_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            target = root / ".omp/hooks/protected-paths.json"
            target.parent.mkdir(parents=True)
            target.write_text("{ malformed", encoding="utf-8")
            policy = root / "constraint.md"
            self._policy(policy, {
                "protected_paths": ["fixtures/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })

            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root),
                 "install-protected-paths", "--output", str(target)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["fixtures/"], json.loads(target.read_text(encoding="utf-8")))

    def test_atomic_install_writes_valid_compiled_prefixes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            target = root / ".omp/hooks/protected-paths.json"
            policy = root / "constraint.md"
            self._policy(policy, {
                "protected_paths": ["locked dir/**", "fixtures/*.json"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "--constraint", str(policy),
                 "install-protected-paths", "--output", str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["fixtures/", "locked dir/"], json.loads(target.read_text(encoding="utf-8")))

    def test_install_rejects_external_explicit_policy_without_changing_target_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_repo = base / "target"
            external_repo = base / "external"
            target_repo.mkdir()
            external_repo.mkdir()
            self._init_repo(target_repo)
            self._init_repo(external_repo)

            target = target_repo / ".omp/hooks/protected-paths.json"
            target.parent.mkdir(parents=True)
            target.write_text('["keep/", "target-only/"]\n', encoding="utf-8")
            self._policy(target_repo / "constraint.md", {
                "protected_paths": ["keep/**", "target-only/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            external_policy = external_repo / "constraint.md"
            self._policy(external_policy, {
                "protected_paths": ["keep/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            prior_bytes = target.read_bytes()

            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(target_repo),
                 "--constraint", str(external_policy), "install-protected-paths",
                 "--output", str(target)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("canonical", result.stderr.lower())
            self.assertEqual(prior_bytes, target.read_bytes())

    def test_install_rejects_same_repo_noncanonical_policy_without_changing_target_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)

            target = root / ".omp/hooks/protected-paths.json"
            target.parent.mkdir(parents=True)
            target.write_text('["keep/", "target-only/"]\n', encoding="utf-8")
            self._policy(root / "constraint.md", {
                "protected_paths": ["keep/**", "target-only/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            alternate = root / "policy.md"
            self._policy(alternate, {
                "protected_paths": ["keep/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            prior_bytes = target.read_bytes()

            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root),
                 "--constraint", str(alternate), "install-protected-paths",
                 "--output", str(target)],
                capture_output=True,
                text=True,
            )

            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("canonical", result.stderr.lower())
            self.assertEqual(prior_bytes, target.read_bytes())

    def test_dirty_fallback_policy_is_watched_and_never_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_repo = base / "target"
            target_repo.mkdir()
            self._init_repo(target_repo)
            (target_repo / "README.md").write_text("target\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(target_repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(target_repo), "commit", "-qm", "target"], check=True)
            script, template = self._framework_copy(base, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            marker = target_repo / "dirty-fallback-ran"
            self._policy(template, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [[
                    "python3", "-c",
                    f"from pathlib import Path; Path({str(marker)!r}).touch()",
                ]],
            })

            result = subprocess.run(
                ["python3", str(script), "--repo", str(target_repo), "check-commit"],
                capture_output=True,
                text=True,
            )

            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("fallback", result.stderr.lower())
            self.assertIn("changed", result.stderr.lower())
            self.assertFalse(marker.exists(), "unreviewed fallback checks must never execute")

    def test_clean_empty_fallback_cannot_authorize_a_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_repo = base / "target"
            target_repo.mkdir()
            self._init_repo(target_repo)
            (target_repo / "README.md").write_text("target\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(target_repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(target_repo), "commit", "-qm", "target"], check=True)
            script, _ = self._framework_copy(base, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })

            result = subprocess.run(
                ["python3", str(script), "--repo", str(target_repo), "check-commit"],
                capture_output=True,
                text=True,
            )

            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("initialization", result.stderr.lower())
            self.assertIn("constraint.md", result.stderr)

    def test_explicit_fallback_path_remains_bootstrap_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_repo = base / "target"
            target_repo.mkdir()
            self._init_repo(target_repo)
            (target_repo / "README.md").write_text("target\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(target_repo), "add", "."], check=True)
            subprocess.run(["git", "-C", str(target_repo), "commit", "-qm", "target"], check=True)
            script, template = self._framework_copy(base, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })

            result = subprocess.run(
                ["python3", str(script), "--repo", str(target_repo),
                 "--constraint", str(template), "check-commit"],
                capture_output=True,
                text=True,
            )

            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("initialization", result.stderr.lower())

    def test_fallback_initializes_missing_config_but_cannot_replace_existing_protections(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target_repo = base / "target"
            target_repo.mkdir()
            script, template = self._framework_copy(base, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            target = target_repo / ".omp/hooks/protected-paths.json"

            initial = subprocess.run(
                ["python3", str(script), "--repo", str(target_repo), "install-protected-paths"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, initial.returncode, initial.stderr)
            self.assertEqual([], json.loads(target.read_text(encoding="utf-8")))

            target.write_text('["last-known-good/"]\n', encoding="utf-8")
            replacement = subprocess.run(
                ["python3", str(script), "--repo", str(target_repo), "install-protected-paths"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, replacement.returncode, replacement.stderr)
            self.assertIn("bootstrap", replacement.stderr.lower())
            self.assertEqual('["last-known-good/"]\n', target.read_text(encoding="utf-8"))

            explicit_replacement = subprocess.run(
                ["python3", str(script), "--repo", str(target_repo),
                 "--constraint", str(template), "install-protected-paths", "--allow-empty"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, explicit_replacement.returncode, explicit_replacement.stderr)
            self.assertIn("bootstrap", explicit_replacement.stderr.lower())
            self.assertEqual('["last-known-good/"]\n', target.read_text(encoding="utf-8"))

    def test_empty_project_policy_requires_explicit_erasure_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            target = root / ".omp/hooks/protected-paths.json"
            target.parent.mkdir(parents=True)
            target.write_text('["last-known-good/"]\n', encoding="utf-8")
            policy = root / "constraint.md"
            self._policy(policy, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })

            refused = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root),
                 "install-protected-paths", "--output", str(target)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, refused.returncode, refused.stderr)
            self.assertIn("--allow-empty", refused.stderr)
            self.assertEqual('["last-known-good/"]\n', target.read_text(encoding="utf-8"))

            confirmed = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root),
                 "install-protected-paths", "--output", str(target), "--allow-empty"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, confirmed.returncode, confirmed.stderr)
            self.assertEqual([], json.loads(target.read_text(encoding="utf-8")))

    def test_removing_policy_block_is_flagged_as_a_policy_source_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)
            policy = root / "constraint.md"
            self._policy(policy, {
                "protected_paths": ["fixtures/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            subprocess.run(["git", "-C", str(root), "add", "constraint.md"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "policy"], check=True)
            policy.write_text("# Constraints\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "constraint.md"], check=True)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "check-commit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(3, result.returncode, result.stderr)
            self.assertIn("POLICY SOURCE", result.stderr)

    def test_clean_policy_without_deterministic_fence_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)
            policy = root / "constraint.md"
            policy.write_text("# Constraints without policy\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "constraint.md"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "policy"], check=True)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "check-commit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, result.returncode, result.stderr)
            self.assertIn("fence is missing", result.stderr)

    def test_policy_source_change_is_flagged_without_running_new_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)
            policy = root / "constraint.md"
            self._policy(policy, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            subprocess.run(["git", "-C", str(root), "add", "constraint.md"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "policy"], check=True)
            marker = root / "new-policy-ran"
            self._policy(policy, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [["python3", "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]],
            })
            subprocess.run(["git", "-C", str(root), "add", "constraint.md"], check=True)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "check-commit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(3, result.returncode, result.stderr)
            self.assertIn("POLICY SOURCE", result.stderr)
            self.assertFalse(marker.exists(), "changed policy bytes must not execute before review")

    def test_ignored_untracked_policy_is_still_treated_as_unreviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)
            (root / ".gitignore").write_text("constraint.md\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", ".gitignore"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "ignore"], check=True)
            marker = root / "ignored-policy-ran"
            self._policy(root / "constraint.md", {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [["python3", "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]],
            })
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "check-commit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(3, result.returncode, result.stderr)
            self.assertIn("POLICY SOURCE", result.stderr)
            self.assertFalse(marker.exists())

    def test_external_diff_configuration_is_not_executed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)
            marker = root / "external-diff-ran"
            tracked = root / "ordinary.txt"
            tracked.write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "ordinary.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
            subprocess.run(
                ["git", "-C", str(root), "config", "diff.external", f"touch {marker}"],
                check=True,
            )
            tracked.write_text("after\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "ordinary.txt"], check=True)
            self.assertEqual([], constraint_gate.check_commit(root, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            }))
            self.assertFalse(marker.exists())

    def test_dirty_policy_still_enforces_reviewed_head_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "repo")
            root.mkdir()
            self._init_repo(root)
            policy = root / "constraint.md"
            self._policy(policy, {
                "protected_paths": ["locked dir/**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            locked = root / "locked dir/fixture.txt"
            locked.parent.mkdir()
            locked.write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "policy"], check=True)
            self._policy(policy, {
                "protected_paths": [],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })
            locked.write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(root), "check-commit"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(3, result.returncode, result.stderr)
            self.assertIn("POLICY SOURCE", result.stderr)
            self.assertIn("PROTECTED PATH staged: locked dir/fixture.txt", result.stderr)

    def test_root_wildcard_protection_is_rejected(self):
        with self.assertRaisesRegex(constraint_gate.PolicyError, "non-root path prefix"):
            constraint_gate.validate_block({
                "protected_paths": ["**"],
                "forbidden_diff_patterns": [],
                "pre_commit_checks": [],
            })


if __name__ == "__main__":
    unittest.main()
