import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parents[2] / "scripts" / "native" / "reward_hacking_evidence.py"


class RewardHackingEvidenceTests(unittest.TestCase):
    def run_cmd(self, *args, cwd=None, check=True):
        result = subprocess.run(
            [str(arg) for arg in args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            self.fail(f"command failed ({result.returncode}): {args}\nstdout={result.stdout}\nstderr={result.stderr}")
        return result

    def git(self, root, *args):
        return self.run_cmd("git", *args, cwd=root)

    def write(self, root, rel, data, mode="w"):
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, mode) as handle:
            handle.write(data)
        return path

    def init_repo(self, root):
        Path(root).mkdir(parents=True, exist_ok=True)
        self.git(root, "init", "-q")
        self.git(root, "config", "user.name", "Evidence Test")
        self.git(root, "config", "user.email", "evidence@example.invalid")

    def helper_json(self, root, *args):
        result = self.run_cmd("python3", HELPER, *args, cwd=root)
        return json.loads(result.stdout)

    def evidence(self, root, baseline, acceptance_argv=None):
        if acceptance_argv is None:
            acceptance_argv = ["/usr/bin/true"]
        return self.helper_json(
            root,
            "evidence",
            "--root",
            root,
            "--baseline",
            baseline["baselinePath"],
            "--expected-baseline-digest",
            baseline["baselineDigest"],
            "--acceptance-argv-json",
            json.dumps(acceptance_argv),
        )

    def make_fixture(self, root):
        self.init_repo(root)
        self.write(root, "committed.txt", "before\n")
        self.write(root, "staged.txt", "stage-before\n")
        self.write(root, "binary.bin", b"\x00before\xff", mode="wb")
        self.write(root, "delete.txt", "delete-me\n")
        self.write(root, "rename.txt", "rename-me\n")
        mode_path = self.write(root, "mode.sh", "#!/bin/sh\nexit 0\n")
        os.chmod(mode_path, 0o644)
        os.symlink("staged.txt", Path(root, "link"))
        self.git(root, "add", "-A")
        self.git(root, "commit", "-qm", "baseline")

        workspace = Path(root, "workspace")
        self.init_repo(workspace)
        self.write(workspace, "nested.txt", "nested-before\n")
        self.git(workspace, "add", "nested.txt")
        self.git(workspace, "commit", "-qm", "nested baseline")

    def test_evidence_covers_root_and_workspace_byte_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            baseline = self.helper_json(root, "baseline", "--root", root)

            self.write(root, "committed.txt", "after commit\n")
            self.git(root, "add", "committed.txt")
            self.git(root, "commit", "-qm", "head moved")
            self.write(root, "staged.txt", "stage-after\n")
            self.git(root, "add", "staged.txt")
            self.write(root, "binary.bin", b"\x00after\xfe", mode="wb")
            Path(root, "delete.txt").unlink()
            Path(root, "rename.txt").rename(Path(root, "renamed.txt"))
            os.chmod(Path(root, "mode.sh"), 0o755)
            Path(root, "link").unlink()
            os.symlink("committed.txt", Path(root, "link"))
            self.write(root, "untracked.txt", "new\n")
            self.write(root, "workspace/nested.txt", "nested-after\n")

            result = self.evidence(root, baseline, ["/usr/bin/test", "-f", "renamed.txt"])
            evidence = result["evidence"]
            self.assertEqual(0, evidence["acceptance"]["exitCode"])
            by_label = {repo["label"]: repo for repo in evidence["repositories"]}
            self.assertIn(".", by_label)
            self.assertIn("workspace", by_label)

            root_evidence = by_label["."]
            self.assertTrue(root_evidence["head"]["changed"])
            index_paths = {change["path"] for change in root_evidence["index"]["changes"]}
            self.assertIn("staged.txt", index_paths)

            changes = {change["path"]: change for change in root_evidence["worktree"]["changes"]}
            self.assertEqual("deleted", changes["delete.txt"]["change"])
            self.assertEqual("mode-change", changes["mode.sh"]["change"])
            self.assertEqual("symlink-target-change", changes["link"]["change"])
            self.assertEqual("added", changes["untracked.txt"]["change"])
            self.assertTrue(changes["binary.bin"]["after"]["binary"])
            self.assertIn(
                {"from": "rename.txt", "to": "renamed.txt"},
                root_evidence["worktree"]["renames"],
            )
            nested_paths = {
                change["path"] for change in by_label["workspace"]["worktree"]["changes"]
            }
            self.assertIn("nested.txt", nested_paths)

    def test_acceptance_result_is_mechanical_and_output_is_digest_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            baseline = self.helper_json(root, "baseline", "--root", root)
            result = self.evidence(
                root,
                baseline,
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.write('secret-output'); "
                    "sys.stderr.write('error-output'); raise SystemExit(7)",
                ],
            )
            acceptance = result["evidence"]["acceptance"]
            self.assertEqual(7, acceptance["exitCode"])
            self.assertEqual(len("secret-output"), acceptance["stdoutBytes"])
            self.assertEqual(len("error-output"), acceptance["stderrBytes"])
            rendered = json.dumps(result)
            self.assertNotIn("stdout", acceptance.keys() - {"stdoutBytes", "stdoutSha256"})
            self.assertNotIn("stderr", acceptance.keys() - {"stderrBytes", "stderrSha256"})
            self.assertEqual(1, rendered.count("secret-output"))
            self.assertEqual(1, rendered.count("error-output"))

    def test_acceptance_does_not_receive_general_agent_control_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            baseline = self.helper_json(root, "baseline", "--root", root)
            previous_helper = os.environ.get("GENERAL_AGENT_REWARD_HELPER")
            previous_digest = os.environ.get("GENERAL_AGENT_REWARD_HELPER_SHA256")
            os.environ["GENERAL_AGENT_REWARD_HELPER"] = "/tmp/secret-helper"
            os.environ["GENERAL_AGENT_REWARD_HELPER_SHA256"] = "f" * 64
            try:
                result = self.evidence(
                    root,
                    baseline,
                    [
                        sys.executable,
                        "-c",
                        "import os; raise SystemExit("
                        "'GENERAL_AGENT_REWARD_HELPER' in os.environ or "
                        "'GENERAL_AGENT_REWARD_HELPER_SHA256' in os.environ)",
                    ],
                )
            finally:
                if previous_helper is None:
                    os.environ.pop("GENERAL_AGENT_REWARD_HELPER", None)
                else:
                    os.environ["GENERAL_AGENT_REWARD_HELPER"] = previous_helper
                if previous_digest is None:
                    os.environ.pop("GENERAL_AGENT_REWARD_HELPER_SHA256", None)
                else:
                    os.environ["GENERAL_AGENT_REWARD_HELPER_SHA256"] = previous_digest
            self.assertEqual(0, result["evidence"]["acceptance"]["exitCode"])

    def test_legacy_helper_proof_cannot_satisfy_canonical_controller_verifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            baseline = self.helper_json(root, "baseline", "--root", root)
            evidence = self.evidence(root, baseline)
            item_id = "M1-I1"
            self.run_cmd("python3", HELPER, "clear-candidate", "--root", root, "--item-id", item_id)
            self.run_cmd(
                "python3",
                HELPER,
                "candidate",
                "--root",
                root,
                "--item-id",
                item_id,
                "--baseline-digest",
                baseline["baselineDigest"],
                "--evidence-digest",
                evidence["evidenceDigest"],
                "--evidence-path",
                evidence["evidencePath"],
                "--binding-json",
                json.dumps(evidence["stateBinding"], sort_keys=True),
            )
            finalized = self.helper_json(root, "finalize-candidate", "--root", root, "--item-id", item_id)
            proof = Path(finalized["proofPath"])
            self.assertEqual(proof, root / ".pm" / "workflow-reviews" / f"{item_id}.md")
            proof_text = proof.read_text()
            self.assertIn("TERMINAL: COMPLETE", proof_text)
            self.assertRegex(proof_text, r"(?m)^EVIDENCE-COMMITMENT: sha256:[0-9a-f]{64}$")
            self.assertRegex(proof_text, r"(?m)^CANDIDATE_DIGEST: [0-9a-f]{64}$")
            self.assertNotIn("WORKFLOW: PASS", proof_text)
            self.assertIn("REVIEW_POLICY: machine-gated", proof_text)
            self.assertIn("COMPLETION_PROOF: state-bound", proof_text)
            rejected = self.run_cmd(
                "python3", HELPER, "verify-proof", "--root", root, "--item-id", item_id, "--proof", proof,
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertRegex(rejected.stderr, r"ITEM_KIND|SUCCESS_TERMINAL|frozen inputs digest")

    def test_proof_rejects_legacy_or_duplicate_terminal_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            baseline = self.helper_json(root, "baseline", "--root", root)
            evidence = self.evidence(root, baseline)
            item_id = "M1-I4"
            self.helper_json(
                root,
                "candidate",
                "--root",
                root,
                "--item-id",
                item_id,
                "--baseline-digest",
                baseline["baselineDigest"],
                "--evidence-digest",
                evidence["evidenceDigest"],
                "--evidence-path",
                evidence["evidencePath"],
                "--binding-json",
                json.dumps(evidence["stateBinding"], sort_keys=True),
            )
            proof = Path(self.helper_json(root, "finalize-candidate", "--root", root, "--item-id", item_id)["proofPath"])

            legacy = proof.read_text().replace("TERMINAL: COMPLETE\n", "WORKFLOW: PASS\n")
            proof.write_text(legacy)
            rejected = self.run_cmd(
                "python3", HELPER, "verify-proof", "--root", root, "--item-id", item_id, "--proof", proof,
                cwd=root, check=False,
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("TERMINAL: COMPLETE", rejected.stderr)

            proof.write_text(legacy.replace("WORKFLOW: PASS\n", "TERMINAL: COMPLETE\nTERMINAL: REVISE\n"))
            duplicate = self.run_cmd(
                "python3", HELPER, "verify-proof", "--root", root, "--item-id", item_id, "--proof", proof,
                cwd=root, check=False,
            )
            self.assertNotEqual(0, duplicate.returncode)
            self.assertIn("duplicate TERMINAL", duplicate.stderr)

            clean = legacy.replace("WORKFLOW: PASS\n", "TERMINAL: COMPLETE\n")
            proof.write_text(clean.replace("EVIDENCE-COMMITMENT: sha256:", "EVIDENCE-COMMITMENT: sha256:" + "0" * 64 + "\nIGNORED: ", 1))
            mismatch = self.run_cmd(
                "python3", HELPER, "verify-proof", "--root", root, "--item-id", item_id, "--proof", proof,
                cwd=root, check=False,
            )
            self.assertNotEqual(0, mismatch.returncode)
            self.assertIn("commitment", mismatch.stderr)

    def test_verify_proof_rejects_field_complete_handwritten_proof_without_controller_audit(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as fake_tmp:
            root = Path(tmp)
            self.make_fixture(root)
            digest = "a" * 64
            binding = self.helper_json(root, "current-state", "--root", root)["stateBinding"]
            proof = self.write(
                root,
                ".pm/workflow-reviews/M1-WEAK.md",
                "\n".join(
                    [
                        "TERMINAL: COMPLETE",
                        f"EVIDENCE-COMMITMENT: sha256:{digest}",
                        "REVIEW_POLICY: machine-gated",
                        "COMPLETION_PROOF: state-bound",
                        "ITEM_ID: M1-WEAK",
                        "ITEM_KIND: none",
                        "PROOF_SCOPE: no-pm",
                        f"PROOF_BINDING_DIGEST: {digest}",
                        f"ROLE_MODEL_POLICY_DIGEST: {digest}",
                        "PROOF_BINDING_JSON: " + json.dumps({
                            "schema": "subagent-proof-binding/v1",
                            "scope": "no-pm",
                            "itemKind": "none",
                            "itemId": "M1-WEAK",
                            "milestoneId": "",
                            "roleModelPolicyDigest": digest,
                            "reason": "handwritten weak proof",
                            "artifacts": [],
                            "digest": digest,
                        }, sort_keys=True, separators=(",", ":")),
                        "DESIGN_ID: fake-design",
                        "RUN_ID: fake-run",
                        "SUCCESS_TERMINAL: success",
                        "MODEL_CALLS: 1",
                        "MAX_MODEL_CALLS: 1",
                        f"FROZEN_INPUTS_DIGEST: {digest}",
                        f"MACHINE_REVIEW_DIGEST: {digest}",
                        "MACHINE_REVIEW_CALL: 1",
                        f"MACHINE_REVIEW_PROMPT_SHA256: {digest}",
                        f"MACHINE_REVIEWED_STATE_DIGEST: {binding['finalStateDigest']}",
                        f"MACHINE_REVIEWED_INPUTS_DIGEST: {digest}",
                        f"MACHINE_REVIEW_EVIDENCE_DIGEST: {digest}",
                        f"NATIVE_TRACE_SHA256: {digest}",
                        "NATIVE_TRACE_CALLS: 1",
                        f"MACHINE_REVIEW_RESPONSE_SHA256: {digest}",
                        f"MACHINE_REVIEW_RECEIPT_SHA256: {digest}",
                        f"MACHINE_REVIEW_PROMPT_BYTES_SHA256: {digest}",
                        f"BASELINE_DIGEST: {digest}",
                        f"EVIDENCE_DIGEST: {digest}",
                        "EVIDENCE_PRODUCER_NODE: evidence",
                        "EVIDENCE_PRODUCER_ACTIVATION: 1",
                        f"EVIDENCE_PRODUCER_DIGEST: {digest}",
                        f"EVIDENCE_PRODUCER_RECEIPT_SHA256: {digest}",
                        f"FINAL_STATE_DIGEST: {binding['finalStateDigest']}",
                        "STATE_BINDING_JSON: " + json.dumps(binding, sort_keys=True, separators=(",", ":")),
                        f"CANDIDATE_DIGEST: {digest}",
                        "AUTHORIZATION_MODE: controller-tier1-env",
                        "AUTHORIZATION_SOURCE: GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1",
                        "THREAT_BOUNDARY: Practical no-follow byte binding; not an OS sandbox against a concurrent same-UID pathname attacker.",
                        "",
                    ]
                ),
            )
            fake_bun = self.write(
                fake_tmp,
                "bun",
                "#!/bin/sh\nprintf '%s\\n' '{\"terminal\":\"success\",\"reviewerTerminal\":\"COMPLETE\"}'\n",
            )
            os.chmod(fake_bun, 0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake_bun.parent}:{env.get('PATH', '')}"
            result = subprocess.run(
                [
                    "python3", str(HELPER), "verify-proof", "--root", str(root),
                    "--item-id", "M1-WEAK", "--proof", str(proof),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr, r"proof binding digest|canonical mapping|controller proof audit")

    def test_verify_proof_rejects_fake_runtime_override_without_frozen_closure(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as fake_tmp:
            root = Path(tmp)
            self.make_fixture(root)
            digest = "b" * 64
            binding = self.helper_json(root, "current-state", "--root", root)["stateBinding"]
            controller = self.write(
                root,
                ".claude/skills/subagent-workflow/scripts/controller/run.ts",
                "// controller fixture\n",
            )
            mapping = self.write(root, ".subagent/flows/audit-design.json", "{}\n")
            run_dir = Path(root, "workflow-output/subagent/audit-design/audit-run")
            run_dir.mkdir(parents=True)
            proof = self.write(
                root,
                ".pm/workflow-reviews/M1-AUDIT.md",
                "\n".join(
                    [
                        "TERMINAL: COMPLETE",
                        f"EVIDENCE-COMMITMENT: sha256:{digest}",
                        "REVIEW_POLICY: machine-gated",
                        "COMPLETION_PROOF: state-bound",
                        "ITEM_ID: M1-AUDIT",
                        "ITEM_KIND: none",
                        "PROOF_SCOPE: no-pm",
                        f"PROOF_BINDING_DIGEST: {digest}",
                        f"ROLE_MODEL_POLICY_DIGEST: {digest}",
                        "PROOF_BINDING_JSON: " + json.dumps({
                            "schema": "subagent-proof-binding/v1",
                            "scope": "no-pm",
                            "itemKind": "none",
                            "itemId": "M1-AUDIT",
                            "milestoneId": "",
                            "roleModelPolicyDigest": digest,
                            "reason": "fake runtime proof",
                            "artifacts": [],
                            "digest": digest,
                        }, sort_keys=True, separators=(",", ":")),
                        "DESIGN_ID: audit-design",
                        "RUN_ID: audit-run",
                        "SUCCESS_TERMINAL: success",
                        "MODEL_CALLS: 1",
                        "MAX_MODEL_CALLS: 1",
                        f"FROZEN_INPUTS_DIGEST: {digest}",
                        f"MACHINE_REVIEW_DIGEST: {digest}",
                        "MACHINE_REVIEW_CALL: 1",
                        f"MACHINE_REVIEW_PROMPT_SHA256: {digest}",
                        f"MACHINE_REVIEWED_STATE_DIGEST: {binding['finalStateDigest']}",
                        f"MACHINE_REVIEWED_INPUTS_DIGEST: {digest}",
                        f"MACHINE_REVIEW_EVIDENCE_DIGEST: {digest}",
                        f"NATIVE_TRACE_SHA256: {digest}",
                        "NATIVE_TRACE_CALLS: 1",
                        f"MACHINE_REVIEW_RESPONSE_SHA256: {digest}",
                        f"MACHINE_REVIEW_RECEIPT_SHA256: {digest}",
                        f"MACHINE_REVIEW_PROMPT_BYTES_SHA256: {digest}",
                        f"BASELINE_DIGEST: {digest}",
                        f"EVIDENCE_DIGEST: {digest}",
                        "EVIDENCE_PRODUCER_NODE: evidence",
                        "EVIDENCE_PRODUCER_ACTIVATION: 1",
                        f"EVIDENCE_PRODUCER_DIGEST: {digest}",
                        f"EVIDENCE_PRODUCER_RECEIPT_SHA256: {digest}",
                        f"FINAL_STATE_DIGEST: {binding['finalStateDigest']}",
                        "STATE_BINDING_JSON: " + json.dumps(binding, sort_keys=True, separators=(",", ":")),
                        f"CANDIDATE_DIGEST: {digest}",
                        "AUTHORIZATION_MODE: controller-tier1-env",
                        "AUTHORIZATION_SOURCE: GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1",
                        "THREAT_BOUNDARY: Practical no-follow byte binding; not an OS sandbox against a concurrent same-UID pathname attacker.",
                        "",
                    ]
                ),
            )
            invocation = Path(fake_tmp, "invocation.txt")
            fake_bun = self.write(
                fake_tmp,
                "fake-runtime",
                "#!/bin/sh\n"
                f"printf invoked > {invocation}\n"
                "printf '%s\\n' '{\"terminal\":\"success\",\"reviewerTerminal\":\"COMPLETE\"}'\n",
            )
            os.chmod(fake_bun, 0o755)
            env = os.environ.copy()
            env["GENERAL_AGENT_CONTROLLER_RUNTIME"] = str(fake_bun)
            result = subprocess.run(
                [
                    "python3", str(HELPER), "verify-proof", "--root", str(root),
                    "--item-id", "M1-AUDIT", "--proof", str(proof),
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr, r"proof binding digest|frozen executable closure")
            self.assertFalse(invocation.exists(), "untrusted runtime override was invoked")

    def test_evidence_rejects_replaced_baseline_even_when_file_is_internally_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            original = self.helper_json(root, "baseline", "--root", root)
            self.write(root, "committed.txt", "new clean baseline\n")
            self.git(root, "add", "committed.txt")
            self.git(root, "commit", "-qm", "new clean baseline")
            replacement = self.helper_json(root, "baseline", "--root", root)

            result = self.run_cmd(
                "python3",
                HELPER,
                "evidence",
                "--root",
                root,
                "--baseline",
                replacement["baselinePath"],
                "--expected-baseline-digest",
                original["baselineDigest"],
                "--acceptance-argv-json",
                '["/usr/bin/true"]',
                cwd=root,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("expected baseline digest", result.stderr)

    def test_evidence_rejects_tampered_baseline_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            baseline = self.helper_json(root, "baseline", "--root", root)
            baseline_path = Path(baseline["baselinePath"])
            payload = json.loads(baseline_path.read_text())
            payload["summary"]["stateDigest"] = "f" * 64
            baseline_path.write_text(json.dumps(payload))

            result = self.run_cmd(
                "python3",
                HELPER,
                "evidence",
                "--root",
                root,
                "--baseline",
                baseline_path,
                "--expected-baseline-digest",
                baseline["baselineDigest"],
                "--acceptance-argv-json",
                '["/usr/bin/true"]',
                cwd=root,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("baseline summary", result.stderr)

    def test_candidate_rejects_evidence_from_another_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            baseline = self.helper_json(root, "baseline", "--root", root)
            evidence = self.evidence(root, baseline)
            result = self.run_cmd(
                "python3",
                HELPER,
                "candidate",
                "--root",
                root,
                "--item-id",
                "M1-I2",
                "--baseline-digest",
                "f" * 64,
                "--evidence-digest",
                evidence["evidenceDigest"],
                "--evidence-path",
                evidence["evidencePath"],
                "--binding-json",
                json.dumps(evidence["stateBinding"], sort_keys=True),
                cwd=root,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("baseline digest", result.stderr)

    def test_finalize_revalidates_evidence_baseline_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            baseline = self.helper_json(root, "baseline", "--root", root)
            evidence = self.evidence(root, baseline)
            candidate = self.helper_json(
                root,
                "candidate",
                "--root",
                root,
                "--item-id",
                "M1-I3",
                "--baseline-digest",
                baseline["baselineDigest"],
                "--evidence-digest",
                evidence["evidenceDigest"],
                "--evidence-path",
                evidence["evidencePath"],
                "--binding-json",
                json.dumps(evidence["stateBinding"], sort_keys=True),
            )

            evidence_path = Path(evidence["evidencePath"])
            evidence_payload = json.loads(evidence_path.read_text())
            evidence_payload["evidence"]["baselineDigest"] = "f" * 64
            encoded = json.dumps(
                evidence_payload["evidence"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
            replacement_digest = hashlib.sha256(encoded).hexdigest()
            evidence_payload["evidenceDigest"] = replacement_digest
            evidence_path.write_text(json.dumps(evidence_payload))

            candidate_path = Path(candidate["candidatePath"])
            candidate_payload = json.loads(candidate_path.read_text())
            candidate_payload["evidenceDigest"] = replacement_digest
            candidate_path.write_text(json.dumps(candidate_payload))

            result = self.run_cmd(
                "python3",
                HELPER,
                "finalize-candidate",
                "--root",
                root,
                "--item-id",
                "M1-I3",
                cwd=root,
                check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("candidate digest", result.stderr)

    def test_baseline_rejects_dirty_covered_paths_but_lists_ignored_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            self.write(root, ".gitignore", "ignored.log\n")
            self.git(root, "add", ".gitignore")
            self.git(root, "commit", "-qm", "ignore policy")
            self.write(root, "ignored.log", "cache\n")
            self.write(root, "node_modules/pkg/a.js", "ignored dependency\n")
            with open(Path(root, ".gitignore"), "a") as handle:
                handle.write("node_modules/\n")
            self.git(root, "add", ".gitignore")
            self.git(root, "commit", "-qm", "ignore dependency tree")
            self.write(root, ".pm/round_input.json", "{}\n")

            clean = self.helper_json(root, "baseline", "--root", root)
            exclusions = clean["baselineSummary"]["exclusions"]
            root_exclusions = next(item for item in exclusions if item["label"] == ".")
            self.assertIn("ignored.log", root_exclusions["ignoredPaths"])
            self.assertIn("node_modules/", root_exclusions["ignoredPaths"])
            self.assertNotIn("node_modules/pkg/a.js", root_exclusions["ignoredPaths"])
            self.assertEqual("excluded", root_exclusions["ignoredUntrackedPolicy"])
            self.assertIn(".pm/round_input.json", root_exclusions["explicitExcludedPaths"])

            self.write(root, "dirty.txt", "covered untracked\n")
            dirty = self.run_cmd(
                "python3", HELPER, "baseline", "--root", root, cwd=root, check=False
            )
            self.assertNotEqual(0, dirty.returncode)
            self.assertIn("dirty baseline", dirty.stderr)

    def test_baseline_binds_real_integrity_evidence_and_excludes_runtime_pm_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            self.write(root, ".pm/goal.md", "# Goal\n\ncriteria: export is deterministic\n")
            self.write(root, ".pm/eval/cases.json", "{}\n")
            self.write(root, ".pm/evidence/m1-i1.md", "check: pytest\nexit: 0\n")
            self.write(root, ".pm/review-briefs/m1-i1.md", "fresh evidence brief\n")
            self.write(root, ".pm/reviews/m1.md", "TERMINAL: COMPLETE\n")
            self.write(root, ".pm/verdicts/m1-i1.md", "TERMINAL: COMPLETE\n")
            self.write(root, ".pm/proofs/m1-i1.json", "{}\n")
            self.write(root, "constraint.md", "Do not modify fixtures.\n")
            self.write(root, ".pm/milestones/m1.md", "status: in_progress\n")
            self.write(root, ".pm/issues/m1/i1.md", "status: todo\n")
            self.write(root, ".pm/cold_start_done", "")
            self.write(root, ".pm/ask/pending.md", "runtime question\n")
            self.git(
                root,
                "add",
                ".pm/goal.md",
                ".pm/eval/cases.json",
                ".pm/evidence/m1-i1.md",
                ".pm/review-briefs/m1-i1.md",
                ".pm/reviews/m1.md",
                ".pm/verdicts/m1-i1.md",
                ".pm/proofs/m1-i1.json",
                "constraint.md",
            )
            self.git(root, "commit", "-qm", "record project integrity evidence")

            baseline = self.helper_json(root, "baseline", "--root", root)
            state_root = next(repo for repo in json.loads(Path(baseline["baselinePath"]).read_text())["state"]["repositories"] if repo["label"] == ".")
            for bound in (
                ".pm/goal.md",
                ".pm/eval/cases.json",
                ".pm/evidence/m1-i1.md",
                ".pm/review-briefs/m1-i1.md",
                ".pm/reviews/m1.md",
                ".pm/verdicts/m1-i1.md",
                ".pm/proofs/m1-i1.json",
                "constraint.md",
            ):
                self.assertIn(bound, state_root["worktreeEntries"])
            root_exclusions = next(
                entry for entry in baseline["baselineSummary"]["exclusions"] if entry["label"] == "."
            )
            self.assertIn(".pm/milestones/m1.md", root_exclusions["explicitExcludedPaths"])
            self.assertIn(".pm/issues/m1/i1.md", root_exclusions["explicitExcludedPaths"])
            self.assertIn(".pm/cold_start_done", root_exclusions["explicitExcludedPaths"])
            self.assertIn(".pm/ask/pending.md", root_exclusions["explicitExcludedPaths"])

    def test_machine_workflow_proofs_are_excluded_while_independent_reviews_remain_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            self.write(root, ".pm/reviews/workflow-x.md", "TERMINAL: REVISE\nindependent milestone\n")
            self.write(root, ".pm/workflow-reviews/x.md", "TERMINAL: COMPLETE\nmachine proof\n")
            self.write(root, ".pm/verdicts/x.md", "TERMINAL: COMPLETE\nindependent issue\n")
            self.git(
                root,
                "add",
                ".pm/reviews/workflow-x.md",
                ".pm/workflow-reviews/x.md",
                ".pm/verdicts/x.md",
            )
            self.git(root, "commit", "-qm", "disjoint review namespaces")
            baseline = self.helper_json(root, "baseline", "--root", root)
            state_root = next(
                repo for repo in json.loads(Path(baseline["baselinePath"]).read_text())["state"]["repositories"]
                if repo["label"] == "."
            )
            self.assertIn(".pm/reviews/workflow-x.md", state_root["worktreeEntries"])
            self.assertIn(".pm/verdicts/x.md", state_root["worktreeEntries"])
            self.assertNotIn(".pm/workflow-reviews/x.md", state_root["worktreeEntries"])
            root_exclusions = next(
                entry for entry in baseline["baselineSummary"]["exclusions"] if entry["label"] == "."
            )
            self.assertIn(".pm/workflow-reviews/x.md", root_exclusions["explicitExcludedPaths"])
            self.assertNotIn(".pm/reviews/workflow-x.md", root_exclusions["explicitExcludedPaths"])

    def test_verify_proof_refuses_legacy_colliding_path_without_following_parent_symlink(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            root = Path(tmp)
            self.make_fixture(root)
            body = "TERMINAL: COMPLETE\nITEM_ID: x\n"
            legacy = self.write(root, ".pm/reviews/workflow-x.md", body)
            outside = Path(outside_raw, "workflow-reviews")
            outside.mkdir()
            (outside / "x.md").write_text("TERMINAL: COMPLETE\noutside\n", encoding="utf-8")
            Path(root, ".pm", "workflow-reviews").symlink_to(outside)
            legacy_result = self.run_cmd(
                "python3", HELPER, "verify-proof", "--root", root, "--item-id", "x", "--proof", legacy,
                cwd=root, check=False,
            )
            self.assertNotEqual(0, legacy_result.returncode)
            self.assertRegex(legacy_result.stderr.lower(), r"legacy|ambiguous|workflow-reviews|rerun")
            parent_result = self.run_cmd(
                "python3",
                HELPER,
                "verify-proof",
                "--root",
                root,
                "--item-id",
                "x",
                "--proof",
                root / ".pm" / "workflow-reviews" / "x.md",
                cwd=root,
                check=False,
            )
            self.assertNotEqual(0, parent_result.returncode)
            self.assertRegex(parent_result.stderr.lower(), r"symlink|follow|canonical|plain|legacy|ambiguous")
            self.assertEqual("TERMINAL: COMPLETE\noutside\n", (outside / "x.md").read_text(encoding="utf-8"))
            self.assertEqual(body, legacy.read_text(encoding="utf-8"))

    def test_removed_gate_triad_has_no_special_binding_or_shape_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make_fixture(root)
            for rel, content in (
                (".pm/accept.sh", "exit 0\n"),
                (".pm/immutable", "tests/**\n"),
                (".pm/verify_base", "not-a-commit\n"),
            ):
                self.write(root, rel, content)

            baseline = self.helper_json(root, "baseline", "--root", root)
            state_root = next(
                repo for repo in json.loads(Path(baseline["baselinePath"]).read_text())["state"]["repositories"]
                if repo["label"] == "."
            )
            root_exclusions = next(
                entry for entry in baseline["baselineSummary"]["exclusions"] if entry["label"] == "."
            )
            for rel in (".pm/accept.sh", ".pm/immutable", ".pm/verify_base"):
                self.assertNotIn(rel, state_root["worktreeEntries"])
                self.assertIn(rel, root_exclusions["explicitExcludedPaths"])

    def test_workspace_repository_discovery_has_no_arbitrary_depth_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.init_repo(root)
            self.write(root, "root.txt", "root\n")
            self.git(root, "add", "root.txt")
            self.git(root, "commit", "-qm", "root")
            deep = root / "workspace/a/b/c/d/e/f/repo"
            self.init_repo(deep)
            self.write(deep, "deep.txt", "deep\n")
            self.git(deep, "add", "deep.txt")
            self.git(deep, "commit", "-qm", "deep")

            baseline = self.helper_json(root, "baseline", "--root", root)
            labels = {repo["label"] for repo in baseline["baselineSummary"]["repositories"]}
            self.assertIn("workspace/a/b/c/d/e/f/repo", labels)


if __name__ == "__main__":
    unittest.main()
