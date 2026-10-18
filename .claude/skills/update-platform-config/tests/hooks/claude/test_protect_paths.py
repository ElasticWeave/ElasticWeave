import importlib.util
import json
import os
import shutil
import sys
import unittest
import tempfile
from pathlib import Path
from unittest import mock

HOOK_SOURCE = Path(__file__).resolve().parents[3] / "assets" / "project" / ".claude" / "hooks" / "protect_paths.py"
_IMPORT_TMP = tempfile.TemporaryDirectory()
HOOK = Path(_IMPORT_TMP.name, "protect_paths.py")
shutil.copyfile(HOOK_SOURCE, HOOK)
SPEC = importlib.util.spec_from_file_location("protect_paths", HOOK)
assert SPEC is not None and SPEC.loader is not None
protect_paths = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = protect_paths
SPEC.loader.exec_module(protect_paths)


class PathBoundaryTests(unittest.TestCase):
    def test_absent_project_config_keeps_cold_start_optional(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                protect_paths.FRAMEWORK_PROTECTED,
                protect_paths.protected_list(tmp),
            )

    def test_malformed_project_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, ".omp/hooks/protected-paths.json")
            config.parent.mkdir(parents=True)
            config.write_text("{ malformed", encoding="utf-8")
            payload = {
                "tool_use_id": "bad-project-policy",
                "session_id": "bad-project-policy",
                "cwd": tmp,
                "tool_name": "Write",
                "tool_input": {"path": "ordinary.txt"},
            }
            with self.assertRaises(SystemExit) as caught:
                protect_paths.pre(payload)
            self.assertEqual(2, caught.exception.code)

            repair = dict(payload)
            repair["tool_name"] = "Bash"
            repair["tool_input"] = {
                "command": ("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 python3 "
                            ".claude/skills/constraints-to-hooks/scripts/constraint_gate.py install-protected-paths")
            }
            protect_paths.pre(repair)
            repair["tool_input"] = {"command": "GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 touch ordinary.txt"}
            with self.assertRaises(SystemExit):
                protect_paths.pre(repair)
            repair["tool_input"] = {
                "command": ("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun "
                            ".claude/skills/subagent-workflow/scripts/controller/run.ts start "
                            ".subagent/flows/example.json")
            }
            with self.assertRaises(SystemExit):
                protect_paths.pre(repair)

    def test_non_string_project_entry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, ".omp/hooks/protected-paths.json")
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps(["fixtures/", 7]), encoding="utf-8")
            with self.assertRaises(protect_paths.ProjectProtectionConfigError):
                protect_paths.protected_list(tmp)

    def test_broken_symlink_project_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, ".omp/hooks/protected-paths.json")
            config.parent.mkdir(parents=True)
            config.symlink_to(Path(tmp, "missing-policy.json"))
            with self.assertRaises(protect_paths.ProjectProtectionConfigError):
                protect_paths.protected_list(tmp)

    def test_subagent_runner_can_read_a_protected_mapping(self):
        legal = [
            "bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate .subagent/flows/example.json",
            ("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts "
             "start .subagent/flows/example.json"),
            ("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun ./.claude/skills/subagent-workflow/scripts/controller/run.ts start ./.subagent/flows/example.json "
             "--input .subagent/fixtures/example.input.json"),
            ("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--resume workflow-output/subagent/example/subagent-123"),
            ("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--resume workflow-output/subagent/example/subagent-123 "
             "--input .subagent/fixtures/example.input.json"),
            ("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts "
             "finalize-only .subagent/flows/example.json --resume "
             "workflow-output/subagent/example/subagent-123"),
        ]
        for command in legal:
            with self.subTest(command=command):
                self.assertTrue(protect_paths.subagent_runner_shape(command))
        self.assertIsNone(protect_paths.bash_hit(legal[0], protect_paths.FRAMEWORK_PROTECTED))

    def test_subagent_runner_rejects_protected_output_and_resume_overrides(self):
        illegal = [
            "bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json",
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts finalize-only .subagent/flows/example.json "
             "--resume workflow-output/subagent/example/run-1"),
            ("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=true bun .claude/skills/subagent-workflow/scripts/controller/run.ts "
             "start .subagent/flows/example.json"),
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--output-root .pm/reviews"),
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--output-root .workflow/designs"),
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--resume .pm/reviews/forged"),
        ]
        for command in illegal:
            with self.subTest(command=command):
                self.assertFalse(protect_paths.subagent_runner_shape(command))
                self.assertIsNotNone(
                    protect_paths.bash_hit(command, protect_paths.FRAMEWORK_PROTECTED))

    def test_subagent_runner_rejects_unknown_duplicate_or_malformed_arguments(self):
        illegal = [
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--unknown value"),
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--input one.json --input two.json"),
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--resume workflow-output/subagent/example/run-1 "
             "--resume workflow-output/subagent/example/run-2"),
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate .subagent/flows/example.json "
             "--input example.json"),
            "bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json extra",
            "bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/../example.json",
            ("bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json "
             "--input ../outside.json"),
        ]
        for command in illegal:
            with self.subTest(command=command):
                self.assertFalse(protect_paths.subagent_runner_shape(command))
                self.assertIsNotNone(
                    protect_paths.bash_hit(command, protect_paths.FRAMEWORK_PROTECTED))

    def test_normal_pm_cli_execution_is_allowlisted(self):
        command = "python3 .claude/skills/pm-dispatch/scripts/pm.py list issues"
        self.assertIsNone(protect_paths.bash_hit(command, protect_paths.FRAMEWORK_PROTECTED))

    def test_old_gate_and_reproducible_evidence_paths_are_not_protected(self):
        writable = (
            ".pm/accept.sh",
            ".pm/immutable/case.json",
            ".pm/verify_base",
            ".pm/evidence/i1.md",
            ".pm/review-briefs/i1.md",
        )
        for path in writable:
            with self.subTest(path=path):
                self.assertIsNone(
                    protect_paths.hits_list(path, protect_paths.FRAMEWORK_PROTECTED)
                )

    def test_authoritative_terminal_artifacts_remain_protected(self):
        for directory in (".pm/reviews/", ".pm/verdicts/", ".pm/proofs/"):
            self.assertIn(directory, protect_paths.FRAMEWORK_PROTECTED)
            self.assertEqual(
                directory,
                protect_paths.hits_list(
                    f"{directory}nested/result.md", protect_paths.FRAMEWORK_PROTECTED
                ),
            )

    def test_control_plane_files_require_acknowledgement_on_both_ports(self):
        expected = {
            ".claude/skills/run-workflow/scripts/select_harness.py",
            ".workflow/harnesses.json",
            "config/workflows/models.json",
            ".claude/skills/subagent-workflow/scripts/native/native-map.json",
            ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py",
            ".claude/skills/subagent-workflow/scripts/native/run.ts",
            ".claude/skills/subagent-workflow/scripts/controller/run.ts",
            ".claude/skills/subagent-workflow/scripts/controller/state_bound_proof.ts",
            ".claude/skills/pm-dispatch/scripts/pm.py",
            "constraint.md",
        }
        self.assertTrue(expected.issubset(set(protect_paths.FRAMEWORK_PROTECTED)))
        ts = (
            Path(__file__).resolve().parents[3]
            / "assets/project/.omp/hooks/pre/protect-paths.ts"
        ).read_text()
        for path in expected:
            self.assertIn(f'"{path}"', ts)

    def test_workflow_review_proof_is_protected_and_audited_on_both_ports(self):
        """.pm/workflow-reviews/ is pm.py's machine-gated workflow proof (disjoint from
        .pm/reviews/) — it must be write-blocked AND covered by the post-bash proof audit, on
        both the Python and the TypeScript port."""
        self.assertIn(".pm/workflow-reviews/", protect_paths.FRAMEWORK_PROTECTED)
        self.assertIn(".pm/workflow-reviews", protect_paths.PROOF_DIRS)
        self.assertEqual(
            ".pm/workflow-reviews/",
            protect_paths.hits_list(
                ".pm/workflow-reviews/nested/i1.md", protect_paths.FRAMEWORK_PROTECTED
            ),
        )
        ts = (
            Path(__file__).resolve().parents[3]
            / "assets/project/.omp/hooks/pre/protect-paths.ts"
        ).read_text()
        self.assertIn('".pm/workflow-reviews/"', ts)
        self.assertIn('".pm/workflow-reviews"', ts)

    def test_node_audit_reverts_workflow_review_proof_forgery(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof = Path(tmp, ".pm/workflow-reviews/i1.md")
            proof.parent.mkdir(parents=True)
            proof.write_text("TERMINAL: REVISE\n")
            payload = {
                "tool_use_id": "node-workflow-review-test",
                "session_id": "native-node",
                "cwd": tmp,
                "tool_name": "Bash",
                "tool_input": {"command": ""},
            }
            protect_paths.write_private_state(
                payload,
                {"proof": protect_paths.snapshot_proof(tmp), "cwd": tmp},
            )
            proof.write_text("TERMINAL: COMPLETE\n")
            with self.assertRaises(SystemExit):
                protect_paths.post(payload)
            self.assertEqual("TERMINAL: REVISE\n", proof.read_text())

    def test_neutral_and_subagent_flow_directories_are_protected(self):
        for directory in (
            ".workflow/designs/", ".subagent/flows/", ".claude/skills/subagent-workflow/scripts/controller/actions/"
        ):
            self.assertIn(directory, protect_paths.FRAMEWORK_PROTECTED)
            self.assertEqual(
                directory,
                protect_paths.hits_list(f"{directory}example.json", protect_paths.FRAMEWORK_PROTECTED),
            )

    def test_directory_matching_respects_path_boundaries(self):
        self.assertIsNone(protect_paths.hits_list(".omp/hooks-extra/file.ts", protect_paths.FRAMEWORK_PROTECTED))
        self.assertEqual(
            ".omp/hooks/",
            protect_paths.hits_list(".omp/hooks/file.ts", protect_paths.FRAMEWORK_PROTECTED),
        )
        self.assertIsNone(protect_paths.hits_list("docs/constraint.md", protect_paths.FRAMEWORK_PROTECTED))
        self.assertIsNone(protect_paths.bash_hit("touch docs/constraint.md", protect_paths.FRAMEWORK_PROTECTED, "/repo"))
        self.assertEqual(
            "constraint.md",
            protect_paths.bash_hit("touch constraint.md", protect_paths.FRAMEWORK_PROTECTED, "/repo"),
        )
        self.assertEqual(
            "constraint.md",
            protect_paths.bash_hit("touch /repo/constraint.md", protect_paths.FRAMEWORK_PROTECTED, "/repo"),
        )

    def test_node_audit_does_not_restore_general_control_plane_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp, ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py")
            helper.parent.mkdir(parents=True)
            helper.write_text("original\n")
            payload = {
                "tool_use_id": "node-integrity-test",
                "session_id": "native-node",
                "cwd": tmp,
                "tool_name": "Bash",
                "tool_input": {"command": ""},
            }
            protect_paths.write_private_state(
                payload,
                {"proof": protect_paths.snapshot_proof(tmp), "cwd": tmp},
            )
            helper.write_text("tampered\n")
            protect_paths.post(payload)
            self.assertEqual("tampered\n", helper.read_text())

    def test_node_audit_does_not_restore_old_gate_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_gate = Path(tmp, ".pm/verify_base")
            old_gate.parent.mkdir(parents=True)
            old_gate.write_text("original\n")
            payload = {
                "tool_use_id": "node-old-gate-test",
                "session_id": "native-node",
                "cwd": tmp,
                "tool_name": "Bash",
                "tool_input": {"command": ""},
            }
            protect_paths.write_private_state(
                payload,
                {"proof": protect_paths.snapshot_proof(tmp), "cwd": tmp},
            )
            old_gate.write_text("changed\n")
            protect_paths.post(payload)
            self.assertEqual("changed\n", old_gate.read_text())

    def test_node_audit_still_reverts_proof_forgery(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof = Path(tmp, ".pm/verdicts/i1.md")
            proof.parent.mkdir(parents=True)
            proof.write_text("VERDICT: FAIL\n")
            payload = {
                "tool_use_id": "node-proof-test",
                "session_id": "native-node",
                "cwd": tmp,
                "tool_name": "Bash",
                "tool_input": {"command": ""},
            }
            protect_paths.write_private_state(
                payload,
                {"proof": protect_paths.snapshot_proof(tmp), "cwd": tmp},
            )
            proof.write_text("VERDICT: PASS\n")
            with self.assertRaises(SystemExit):
                protect_paths.post(payload)
            self.assertEqual("VERDICT: FAIL\n", proof.read_text())

    def test_node_audit_recursively_reverts_workflow_proof_forgery(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof = Path(tmp, ".pm/proofs/workflow/run-1/result.json")
            proof.parent.mkdir(parents=True)
            proof.write_text('{"terminal":"REVISE"}\n')
            payload = {
                "tool_use_id": "node-nested-proof-test",
                "session_id": "native-node",
                "cwd": tmp,
                "tool_name": "Bash",
                "tool_input": {"command": ""},
            }
            protect_paths.write_private_state(
                payload,
                {"proof": protect_paths.snapshot_proof(tmp), "cwd": tmp},
            )
            proof.write_text('{"terminal":"COMPLETE"}\n')
            with self.assertRaises(SystemExit):
                protect_paths.post(payload)
            self.assertEqual('{"terminal":"REVISE"}\n', proof.read_text())

    def test_node_audit_replaces_proof_symlink_without_writing_through_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof = Path(tmp, ".pm/verdicts/i1.md")
            sentinel = Path(tmp, "outside.txt")
            proof.parent.mkdir(parents=True)
            proof.write_text("VERDICT: REVISE\n")
            sentinel.write_text("outside\n")
            payload = {
                "tool_use_id": "node-proof-symlink-test",
                "session_id": "native-node",
                "cwd": tmp,
                "tool_name": "Bash",
                "tool_input": {"command": "python3 -c pass"},
            }
            protect_paths.write_private_state(
                payload,
                {"proof": protect_paths.snapshot_proof(tmp), "cwd": tmp},
            )
            proof.unlink()
            proof.symlink_to(sentinel)

            with self.assertRaises(SystemExit) as caught:
                protect_paths.post(payload)

            self.assertEqual(2, caught.exception.code)
            self.assertEqual("outside\n", sentinel.read_text())
            self.assertFalse(proof.is_symlink())
            self.assertEqual("VERDICT: REVISE\n", proof.read_text())

    def test_node_audit_rejects_symlinked_proof_parent_without_writing_through_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp, ".pm/proofs/workflow/run-1")
            proof = run_dir / "result.json"
            outside = Path(tmp, "outside")
            sentinel = outside / "result.json"
            run_dir.mkdir(parents=True)
            outside.mkdir()
            proof.write_text('{"terminal":"REVISE"}\n')
            sentinel.write_text("outside\n")
            payload = {
                "tool_use_id": "node-proof-parent-symlink-test",
                "session_id": "native-node",
                "cwd": tmp,
                "tool_name": "Bash",
                "tool_input": {"command": "python3 -c pass"},
            }
            protect_paths.write_private_state(
                payload,
                {"proof": protect_paths.snapshot_proof(tmp), "cwd": tmp},
            )
            proof.unlink()
            run_dir.rmdir()
            run_dir.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(SystemExit) as caught:
                protect_paths.post(payload)

            self.assertEqual(2, caught.exception.code)
            self.assertEqual("outside\n", sentinel.read_text())
            self.assertTrue(run_dir.is_symlink())


class GateStatePrivateStorageTests(unittest.TestCase):
    """Finding: the pre/post hand-off snapshot must live in a fixed, private, account-scoped
    runtime location — never the shared/world-writable system temp directory — with exclusive
    randomized creation, strict regular-file ownership/mode/link checks on read, and safe
    cleanup. A co-resident local account (or a stale same-account artifact) must not be able to
    redirect a write outside the gate, nor feed forged "pre-image" content back into post()'s
    proof audit."""

    def setUp(self):
        self._env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.runtime_dir = tempfile.mkdtemp(prefix="fake-xdg-runtime-")
        self.addCleanup(shutil.rmtree, self.runtime_dir, True)
        os.environ["XDG_RUNTIME_DIR"] = self.runtime_dir

    def payload(self, key="gate-state-test-key", cwd="/tmp"):
        return {"tool_use_id": key, "session_id": key, "cwd": cwd}

    def test_gate_state_never_touches_the_shared_system_temp_directory(self):
        payload = self.payload()
        shared_temp = Path(tempfile.gettempdir())
        legacy_state = shared_temp / "ga-gate-gate-state-test-key.json"
        legacy_temporary = shared_temp / "ga-gate-gate-state-test-key.json.tmp"

        protect_paths.write_private_state(payload, {"proof": {}, "cwd": "/tmp"})

        self.assertFalse(legacy_state.exists(), "gate state must not use its legacy shared-temp path")
        self.assertFalse(legacy_temporary.exists(), "gate state must not create a legacy shared-temp file")
        self.assertTrue(
            (Path(self.runtime_dir) / "general-agent-gate" / "gate-gate-state-test-key.json").is_file(),
            "gate state must instead use the private XDG runtime directory",
        )

    def test_gate_state_directory_is_private_and_account_scoped(self):
        protect_paths.write_private_state(self.payload(), {"proof": {}, "cwd": "/tmp"})
        gate_dir = Path(self.runtime_dir, "general-agent-gate")
        self.assertTrue(gate_dir.is_dir())
        self.assertFalse(gate_dir.is_symlink())
        mode = gate_dir.stat().st_mode & 0o777
        self.assertEqual(0o700, mode, f"gate directory must be private (0700), got {oct(mode)}")
        self.assertEqual(os.geteuid(), gate_dir.stat().st_uid)

    def test_gate_state_round_trip_reads_back_and_clears(self):
        payload = self.payload()
        protect_paths.write_private_state(payload, {"proof": {"x": "y"}, "cwd": "/tmp/repo"})
        state = protect_paths.read_and_clear_private_state(payload)
        self.assertEqual({"proof": {"x": "y"}, "cwd": "/tmp/repo"}, state)
        # A second read must be benign (the state was consumed and removed).
        self.assertIsNone(protect_paths.read_and_clear_private_state(payload))

    def test_gate_state_read_without_prior_write_is_benign_none(self):
        self.assertIsNone(protect_paths.read_and_clear_private_state(self.payload("never-written")))

    def test_gate_state_write_leaves_no_stray_temporary_files(self):
        payload = self.payload("repeat-key")
        protect_paths.write_private_state(payload, {"proof": {"n": 1}, "cwd": "/tmp"})
        protect_paths.write_private_state(payload, {"proof": {"n": 2}, "cwd": "/tmp"})
        gate_dir = Path(self.runtime_dir, "general-agent-gate")
        names = sorted(p.name for p in gate_dir.iterdir())
        self.assertEqual(["gate-repeat-key.json"], names)
        state = protect_paths.read_and_clear_private_state(payload)
        self.assertEqual({"proof": {"n": 2}, "cwd": "/tmp"}, state)

    def test_gate_state_write_does_not_follow_a_preplanted_symlink_to_an_outside_sentinel(self):
        outside = Path(tempfile.mkdtemp(prefix="outside-sentinel-"))
        self.addCleanup(shutil.rmtree, outside, True)
        sentinel = outside / "victim-owned-elsewhere.txt"
        sentinel.write_text("PRISTINE\n")

        payload = self.payload("precreated-symlink-key")
        # Establish the (legitimately private) directory once, then plant the attack exactly at
        # the deterministic final name an attacker could predict from tool_use_id/session_id.
        protect_paths.write_private_state(payload, {"proof": {}, "cwd": "/tmp"})
        protect_paths.read_and_clear_private_state(payload)  # consume + remove the honest file
        gate_dir = Path(self.runtime_dir, "general-agent-gate")
        planted = gate_dir / "gate-precreated-symlink-key.json"
        planted.symlink_to(sentinel)

        protect_paths.write_private_state(payload, {"proof": {"forged": "PWNED"}}, )

        self.assertEqual("PRISTINE\n", sentinel.read_text(), "outside sentinel must be untouched")
        self.assertFalse(planted.is_symlink(), "the final gate file must be replaced, not written through")
        self.assertEqual({"proof": {"forged": "PWNED"}}, json.loads(planted.read_text()))

    def test_gate_state_read_rejects_a_preplanted_symlink_without_trusting_or_leaking_it(self):
        outside = Path(tempfile.mkdtemp(prefix="outside-lie-"))
        self.addCleanup(shutil.rmtree, outside, True)
        lie = outside / "attacker-owned-lie.json"
        lie.write_text(json.dumps({"proof": {".pm/verdicts/i1.md": "FORGED"}, "cwd": "/tmp"}))

        payload = self.payload("precreated-read-symlink-key")
        gate_dir_fd_owner = protect_paths._open_private_gate_dir(create=True)
        os.close(gate_dir_fd_owner)
        gate_dir = Path(self.runtime_dir, "general-agent-gate")
        planted = gate_dir / "gate-precreated-read-symlink-key.json"
        planted.symlink_to(lie)

        with self.assertRaises(protect_paths.UnsafeGateStateError):
            protect_paths.read_and_clear_private_state(payload)

        self.assertEqual(
            json.dumps({"proof": {".pm/verdicts/i1.md": "FORGED"}, "cwd": "/tmp"}),
            lie.read_text(),
            "the attacker's own file must not be mutated by the failed read",
        )
        self.assertTrue(planted.is_symlink(), "a detected forgery is left in place, not silently cleaned up")

    def test_gate_state_read_rejects_a_group_or_world_readable_file(self):
        payload = self.payload("loose-mode-key")
        protect_paths.write_private_state(payload, {"proof": {}, "cwd": "/tmp"})
        gate_dir = Path(self.runtime_dir, "general-agent-gate")
        target = gate_dir / "gate-loose-mode-key.json"
        os.chmod(target, 0o644)

        with self.assertRaises(protect_paths.UnsafeGateStateError):
            protect_paths.read_and_clear_private_state(payload)

    def test_gate_state_falls_back_to_a_private_home_directory_without_xdg_runtime_dir(self):
        del os.environ["XDG_RUNTIME_DIR"]
        fake_home = tempfile.mkdtemp(prefix="fake-home-")
        self.addCleanup(shutil.rmtree, fake_home, True)
        os.environ["HOME"] = fake_home

        payload = self.payload("home-fallback-key")
        protect_paths.write_private_state(payload, {"proof": {"via": "home"}, "cwd": "/tmp"})
        gate_file = Path(fake_home, ".cache", "general-agent", "gate", "gate-home-fallback-key.json")
        self.assertTrue(gate_file.is_file())
        self.assertFalse(gate_file.is_symlink())
        self.assertEqual(0o700, Path(fake_home, ".cache", "general-agent", "gate").stat().st_mode & 0o777)
        state = protect_paths.read_and_clear_private_state(payload)
        self.assertEqual({"proof": {"via": "home"}, "cwd": "/tmp"}, state)

    def test_gate_state_fails_closed_when_runtime_root_is_group_or_world_writable(self):
        os.chmod(self.runtime_dir, 0o777)
        with self.assertRaises(protect_paths.UnsafeGateStateError):
            protect_paths.write_private_state(self.payload(), {"proof": {}, "cwd": "/tmp"})

    def test_gate_state_fails_closed_on_a_preplanted_symlinked_home_component(self):
        del os.environ["XDG_RUNTIME_DIR"]
        fake_home = tempfile.mkdtemp(prefix="fake-home-symlink-")
        self.addCleanup(shutil.rmtree, fake_home, True)
        os.environ["HOME"] = fake_home
        elsewhere = Path(tempfile.mkdtemp(prefix="elsewhere-"))
        self.addCleanup(shutil.rmtree, elsewhere, True)

        Path(fake_home, ".cache").mkdir(mode=0o700)
        Path(fake_home, ".cache", "general-agent").symlink_to(elsewhere, target_is_directory=True)

        with self.assertRaises(protect_paths.UnsafeGateStateError):
            protect_paths.write_private_state(self.payload("home-symlink-key"), {"proof": {}, "cwd": "/tmp"})

        self.assertEqual([], list(elsewhere.iterdir()), "must never write through the planted symlink")

    def test_pre_and_post_end_to_end_use_the_private_gate_and_reject_a_hijacked_state_file(self):
        with tempfile.TemporaryDirectory() as repo:
            verdict = Path(repo, ".pm/verdicts/i1.md")
            verdict.parent.mkdir(parents=True)
            verdict.write_text("VERDICT: REVISE\n")
            payload = {
                "tool_use_id": "e2e-hijack-key",
                "session_id": "e2e-hijack-key",
                "cwd": repo,
                "tool_name": "Bash",
                "tool_input": {"command": "python3 -c pass"},
            }

            protect_paths.pre(payload)
            gate_dir = Path(self.runtime_dir, "general-agent-gate")
            self.assertEqual(["gate-e2e-hijack-key.json"], [p.name for p in gate_dir.iterdir()])

            outside = Path(tempfile.mkdtemp(prefix="e2e-outside-"))
            self.addCleanup(shutil.rmtree, outside, True)
            lie = outside / "lie.json"
            lie.write_text(json.dumps({"proof": {".pm/verdicts/i1.md": "FORGED VIA HIJACK"}, "cwd": repo}))
            real_gate_file = gate_dir / "gate-e2e-hijack-key.json"
            real_gate_file.unlink()
            real_gate_file.symlink_to(lie)

            with self.assertRaises(SystemExit):
                protect_paths.post(payload)

            self.assertEqual(
                "VERDICT: REVISE\n", verdict.read_text(),
                "a hijacked hand-off file must never be able to plant forged content into protected proof",
            )


if __name__ == "__main__":
    unittest.main()
