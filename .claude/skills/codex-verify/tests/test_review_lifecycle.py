from __future__ import annotations

from pathlib import Path
import contextlib
import io
import json
import hashlib
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import importlib.util


SKILLS = Path(__file__).resolve().parents[2]
COMPLETE_MILESTONE = SKILLS / "complete-milestone" / "SKILL.md"
MILESTONE_REVIEW = SKILLS / "milestone-review" / "SKILL.md"
CODEX_VERIFY = SKILLS / "codex-verify" / "SKILL.md"
PM_DISPATCH = SKILLS / "pm-dispatch" / "SKILL.md"
MILESTONE_CLI = SKILLS / "milestone-review" / "scripts" / "milestone_review.py"
PREFLIGHT = SKILLS / "codex-verify" / "scripts" / "review_lifecycle" / "pr_preflight.py"
REMOTE = SKILLS / "codex-verify" / "scripts" / "review_lifecycle" / "pr_merge.py"
MODEL_POLICY_TEMPLATE = SKILLS / "default-mode/assets/project/config/workflows/models.json"
CONFIG_SYNC_SCRIPTS = SKILLS / "update-platform-config" / "scripts" / "config-sync"
sys.path.insert(0, str(CONFIG_SYNC_SCRIPTS))
import live_catalog  # noqa: E402
from resolve_launch_spec import resolve_launch_spec  # noqa: E402


CODEX_CATALOG = (
    json.dumps(
        {
            "models": [
                {
                    "slug": model,
                    "supported_reasoning_levels": [
                        {"effort": effort}
                        for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
                    ],
                }
                for model in ("gpt-5.6-luna", "gpt-5.6-sol", "gpt-6-astra")
            ]
        }
    ).encode()
    + b"\n"
)
CURSOR_CATALOG = (
    "Available models\n\n"
    "claude-opus-5-thinking-max - Opus Max\n"
    "claude-opus-5-thinking-xhigh - Opus XHigh\n"
    "claude-sonnet-5-thinking-high - Sonnet High\n"
    f"{live_catalog.CURSOR_TIP_TRAILER}\n"
).encode()
CLAUDE_CATALOG_ARGV = (
    "claude",
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
)
CLAUDE_CATALOG = (
    json.dumps(
        {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "models",
                "response": {
                    "models": [
                        {
                            "value": model,
                            "resolvedModel": model + "[1m]",
                            "supportedEffortLevels": [
                                "low",
                                "medium",
                                "high",
                                "xhigh",
                                "max",
                            ],
                        }
                        for model in ("claude-opus-5", "claude-sonnet-5")
                    ]
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    + b"\n"
)


def static_catalog_runner(argv: tuple[str, ...]) -> tuple[int, bytes, bytes]:
    if tuple(argv) == ("codex", "debug", "models"):
        return 0, CODEX_CATALOG, b""
    if tuple(argv) == ("cursor-agent", "models"):
        return 0, CURSOR_CATALOG, b""
    if tuple(argv) == CLAUDE_CATALOG_ARGV:
        return 0, CLAUDE_CATALOG, b""
    raise AssertionError(f"unexpected catalog argv: {argv!r}")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREFLIGHT_MODULE = load_module("review_pr_preflight", PREFLIGHT)
REMOTE_MODULE = load_module("review_pr_merge", REMOTE)


class ResolvePrLifecycleContractTests(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    @staticmethod
    def documented_commands(document: str) -> list[list[str]]:
        commands: list[list[str]] = []
        for block in re.findall(r"```bash\n(.*?)```", document, flags=re.DOTALL):
            lines = block.splitlines()
            index = 0
            while index < len(lines):
                stripped = lines[index].strip()
                if not stripped.startswith(("python3 ", "gh ")):
                    index += 1
                    continue
                parts = [stripped]
                while parts[-1].endswith("\\") and index + 1 < len(lines):
                    parts[-1] = parts[-1][:-1].rstrip()
                    index += 1
                    parts.append(lines[index].strip())
                commands.append(shlex.split(" ".join(parts)))
                index += 1
        return commands

    def test_pr_backed_issue_merges_before_done_transition(self) -> None:
        dispatch = self.read(PM_DISPATCH)
        complete_route = dispatch.split("`TERMINAL: COMPLETE` →", 1)[1].split(
            "`TERMINAL: HUMAN_BLOCKED`", 1
        )[0]
        self.assertIn("review-owned PR-merge", complete_route)
        self.assertLess(
            complete_route.index("review-owned PR-merge"),
            complete_route.index("pm.py set issue <id> done"),
        )

    def test_pr_backed_milestone_merges_before_done_transition(self) -> None:
        completion = self.read(COMPLETE_MILESTONE)
        final_step = completion.split(
            "7. **Revalidate, resolve any PR, and mark done as the final mutation.**", 1
        )[1].split("## Boundaries", 1)[0]
        self.assertIn("review-owned PR-merge", final_step)
        self.assertLess(
            final_step.index("review-owned PR-merge"),
            final_step.index("pm.py set milestone <id> done"),
        )

        dispatch = self.read(PM_DISPATCH)
        milestone_route = dispatch.split("6. **Milestone done", 1)[1].split(
            "## Deciding under uncertainty", 1
        )[0]
        self.assertLess(
            milestone_route.index("review-owned PR-merge"),
            milestone_route.index("pm.py set milestone done"),
        )

    def test_review_owned_pr_merge_requires_pre_transition_artifact_without_status_exception(self) -> None:
        resolve = self.read(CODEX_VERIFY)
        self.assertIn("corresponding `set ... done` transition", resolve)
        self.assertIn("already `done`", resolve)
        self.assertIn("status-only", resolve)
        self.assertNotIn("--allow-status-drift", resolve)
        self.assertNotIn("--allow-terminal-transition", resolve)

    def test_remote_merge_is_isolated_and_locally_revalidated(self) -> None:
        resolve = self.read(CODEX_VERIFY)
        self.assertIn("temporary working directory outside the reviewed checkout", resolve)
        self.assertIn("--repo github.com/<OWNER/REPO>", resolve)
        self.assertIn("git -C <reviewed-checkout> fetch --prune", resolve)
        self.assertIn("fixed verified system Git", resolve)
        self.assertIn("GIT_*", resolve)
        self.assertIn("rerun the exact local preflight", resolve)
        self.assertIn("must not touch the reviewed checkout", resolve)

    def test_reviewer_contracts_keep_full_criteria_drift_fail_closed(self) -> None:
        milestone = self.read(MILESTONE_REVIEW)
        issue = self.read(CODEX_VERIFY)
        for contract in (milestone, issue):
            self.assertIn("status-only", contract)
            self.assertIn("fresh review", contract)
            self.assertNotIn("--allow-status-drift", contract)

    def test_lifecycle_docs_use_current_review_and_merge_receipt_clis(self) -> None:
        completion = self.read(COMPLETE_MILESTONE)
        resolve = self.read(CODEX_VERIFY)
        for document in (completion, resolve):
            for stale in (
                "--model-roles",
                "--builder-provider",
                "--reviewer-provider",
            ):
                self.assertNotIn(stale, document)
        self.assertNotIn("--finalize --output", completion)
        self.assertNotIn(".pm/reviewer-replies/<id>.txt", completion)

        review_commands = [
            command
            for command in self.documented_commands(completion)
            if any("milestone-review/scripts/milestone_review.py" in token for token in command)
            and "--verify-artifact" not in command
        ]
        self.assertEqual(2, len(review_commands))
        prepare, finalize = review_commands
        required_review_flags = {
            "--base",
            "--evidence",
            "--builder-role",
            "--reviewer-role",
            "--reviewer-launch-receipt",
        }
        for command in review_commands:
            self.assertTrue(required_review_flags.issubset(command), command)
        self.assertNotIn("--finalize", prepare)
        self.assertIn("--finalize", finalize)

        merge_commands = [
            command
            for document in (completion, resolve)
            for command in self.documented_commands(document)
            if any("codex-verify/scripts/review_lifecycle/pr_merge.py" in token for token in command)
        ]
        self.assertEqual(2, len(merge_commands))
        required_merge_flags = {
            "--checkout",
            "--repository",
            "--pr",
            "--approval-review-id",
            "--kind",
            "--id",
            "--approved-head",
            "--strategy",
            "--authorization-receipt",
            "--receipt",
        }
        for command in merge_commands:
            self.assertTrue(required_merge_flags.issubset(command), command)

        self.assertIn("verifier-owned", completion)
        self.assertIn("verify_merged_github", completion)


class ResolvePrPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.fixture_tmp = tempfile.TemporaryDirectory()
        fixture_dir = Path(self.fixture_tmp.name)
        self.reviewer_payload = fixture_dir / "reviewer-fixture-payload.py"
        self.reviewer_fixture = fixture_dir / "reviewer-fixture"
        self.reviewer_fixture.write_text(
            "#!/usr/bin/env bash\n"
            f'exec "{sys.executable}" "{self.reviewer_payload}" "$@"\n',
            encoding="utf-8",
        )
        self.reviewer_fixture.chmod(0o755)
        self.reviewer_payload.write_text(
            "#!/usr/bin/env python3\n"
            "import re, sys\n"
            "prompt = sys.stdin.read()\n"
            "def field(name):\n"
            "    match = re.search(rf'(?m)^{name}: (\\S+)$', prompt)\n"
            "    if not match:\n"
            "        raise SystemExit(f'missing {name}')\n"
            "    return match.group(1)\n"
            "print('TERMINAL: COMPLETE')\n"
            "print(f'EVIDENCE-COMMITMENT: {field(\"EVIDENCE-COMMITMENT\")}')\n"
            "print(f'REVIEWER-ROLE: {field(\"REVIEWER-ROLE\")}')\n"
            "print(f'REVIEWER-IDENTITY-SHA256: {field(\"REVIEWER-IDENTITY-SHA256\")}')\n"
            "print('REASONS: fixture reviewer checked the criteria, diff, and fresh evidence.')\n",
            encoding="utf-8",
        )
        self.reviewer_payload.chmod(0o644)
        self.cli_wrapper = fixture_dir / "review-cli-wrapper.py"
        self.cli_wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            f"CODEX_CATALOG = {CODEX_CATALOG!r}\n"
            f"CURSOR_CATALOG = {CURSOR_CATALOG!r}\n"
            f"CLAUDE_CATALOG = {CLAUDE_CATALOG!r}\n"
            f"CLAUDE_CATALOG_ARGV = {CLAUDE_CATALOG_ARGV!r}\n"
            "script, fixture = Path(sys.argv[1]), Path(sys.argv[2])\n"
            "sys.path.insert(0, str(script.parent))\n"
            "spec = importlib.util.spec_from_file_location('review_pr_test_target', script)\n"
            "if spec is None or spec.loader is None:\n"
            "    raise SystemExit('cannot import test target')\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules[spec.name] = module\n"
            "spec.loader.exec_module(module)\n"
            "def catalog_runner(argv):\n"
            "    if tuple(argv) == ('codex', 'debug', 'models'):\n"
            "        return 0, CODEX_CATALOG, b''\n"
            "    if tuple(argv) == ('cursor-agent', 'models'):\n"
            "        return 0, CURSOR_CATALOG, b''\n"
            "    if tuple(argv) == CLAUDE_CATALOG_ARGV:\n"
            "        return 0, CLAUDE_CATALOG, b''\n"
            "    raise AssertionError(f'unexpected catalog argv: {argv!r}')\n"
            "raise SystemExit(module.main(sys.argv[3:], "
            "_test_reviewer_executable=fixture, _test_catalog_runner=catalog_runner))\n",
            encoding="utf-8",
        )
        self.cli_wrapper.chmod(0o755)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "preflight@example.test")
        self.git("config", "user.name", "Preflight Test")
        self.write("README.md", "baseline\n")
        self.write_verified_roles()
        self.git("add", "README.md", "config/workflows/models.json")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.write(".pm/goal.md", "# Goal\n## Acceptance\n- exact behavior\n")
        self.write(
            ".pm/milestones/m1.md",
            "---\nid: m1\nstatus: review\n---\n## Acceptance\n- feature complete\n",
        )
        self.write("feature.txt", "implemented\n")
        self.git("add", "feature.txt")
        self.git("commit", "-qm", "feature")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()
        self.write(".pm/self_evolve/m1.sealed", "sealed final state\n")
        time.sleep(0.002)
        self.write(".pm/evidence/m1.md", "COMMAND: checks\nEXIT: 0\nSUMMARY: passed\n")
        self.write(".pm/reports/m1.md", "# report\nfinal milestone result\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()
        self.fixture_tmp.cleanup()

    def write(self, relative: str, value: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, check=False
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    @staticmethod
    def role_document() -> dict:
        return json.loads(MODEL_POLICY_TEMPLATE.read_text(encoding="utf-8"))

    def write_verified_roles(self) -> Path:
        return self.write(
            "config/workflows/models.json",
            json.dumps(self.role_document(), indent=2) + "\n",
        )

    def write_launch_receipt(self) -> Path:
        target = self.repo / ".pm/receipts/launch/task-advisor.json"
        if target.is_file() and not target.is_symlink():
            return target
        document_path = self.repo / "config/workflows/models.json"
        receipt = resolve_launch_spec(
            document_path.read_bytes(),
            task_class="verification",
            reasoning_posture="balanced",
            semantic_role="advisor",
            writer_role="task",
            review_mode="exact-opposite",
            catalog_runner=static_catalog_runner,
        )
        return self.write(
            target.relative_to(self.repo).as_posix(),
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        )

    def milestone(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(self.cli_wrapper),
                str(MILESTONE_CLI),
                str(self.reviewer_fixture),
                "m1",
                "--base",
                self.base,
                "--builder-role",
                "task",
                "--reviewer-role",
                "advisor",
                "--reviewer-launch-receipt",
                str(self.write_launch_receipt()),
                *extra,
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )

    def finalize_complete(self) -> None:
        prepared = self.milestone()
        self.assertEqual(0, prepared.returncode, prepared.stderr)
        match = re.search(r"commitment: (sha256:[0-9a-f]{64})", prepared.stdout)
        self.assertIsNotNone(match, prepared.stdout)
        finalized = self.milestone("--finalize")
        self.assertEqual(0, finalized.returncode, finalized.stderr)

    def preflight(self, approved_head: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(self.cli_wrapper),
                str(PREFLIGHT),
                str(self.reviewer_fixture),
                "--kind",
                "milestone",
                "--id",
                "m1",
                "--approved-head",
                approved_head,
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_preflight_accepts_current_complete_artifact_and_exact_head(self) -> None:
        self.finalize_complete()
        result = self.preflight(self.head)
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(self.head, receipt["approvedHead"])
        self.assertEqual("COMPLETE", receipt["terminal"])
        self.assertEqual("review", receipt["status"])

    def test_preflight_rejects_approved_head_mismatch(self) -> None:
        self.finalize_complete()
        result = self.preflight(self.base)
        self.assertEqual(2, result.returncode)
        self.assertIn("approved HEAD mismatch", result.stderr)

    def test_preflight_rejects_status_only_done_transition(self) -> None:
        self.finalize_complete()
        milestone = self.repo / ".pm/milestones/m1.md"
        milestone.write_text(
            milestone.read_text(encoding="utf-8").replace("status: review", "status: done", 1),
            encoding="utf-8",
        )
        result = self.preflight(self.head)
        self.assertEqual(2, result.returncode)
        self.assertIn("already done", result.stderr)

    def test_preflight_caller_path_home_and_git_env_cannot_spoof_head(self) -> None:
        self.finalize_complete()
        frozen = Path(self.fixture_tmp.name) / "frozen-head"
        cloned = subprocess.run(
            ["git", "clone", "--", str(self.repo), str(frozen)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, cloned.returncode, cloned.stderr)
        attacker_root = Path(self.fixture_tmp.name) / "attacker-root"
        attacker_root.mkdir()
        poison_home = Path(self.fixture_tmp.name) / "poison-home"
        poison_home.mkdir()
        (poison_home / ".gitconfig").write_text(
            "[alias]\n"
            "    rev-parse = !printf 'aliased-head'\n"
            "[core]\n"
            f"    hooksPath = {poison_home / 'hooks'}\n"
            "[url \"git@attacker.example:\"]\n"
            "    insteadOf = git@github.com:\n",
            encoding="utf-8",
        )
        fake_bin = Path(self.fixture_tmp.name) / "poison-bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"attacker = {str(attacker_root)!r}\n"
            "argv = sys.argv[1:]\n"
            "if argv[:2] == ['rev-parse', '--show-toplevel']:\n"
            "    print(attacker)\n"
            "    raise SystemExit(0)\n"
            "if argv[:2] == ['rev-parse', '--verify']:\n"
            "    print('ab' * 20)\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(99)\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "HOME": str(poison_home),
                "GIT_DIR": str(frozen / ".git"),
                "GIT_WORK_TREE": str(frozen),
                "GIT_COMMON_DIR": str(frozen / ".git"),
                "GIT_OBJECT_DIRECTORY": str(frozen / ".git" / "objects"),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(frozen / ".git" / "objects"),
                "GIT_INDEX_FILE": str(frozen / ".git" / "index"),
                "GIT_CONFIG_GLOBAL": str(poison_home / ".gitconfig"),
                "GIT_CONFIG_SYSTEM": str(poison_home / ".gitconfig"),
                "GIT_CONFIG_NOSYSTEM": "0",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": str(poison_home),
                "GIT_REPLACE_REF_BASE": "refs/replace",
                "GIT_ASKPASS": str(poison_home / "askpass"),
                "GIT_SSH_COMMAND": "true",
            }
        )
        result = subprocess.run(
            [
                "python3",
                str(self.cli_wrapper),
                str(PREFLIGHT),
                str(self.reviewer_fixture),
                "--kind",
                "milestone",
                "--id",
                "m1",
                "--approved-head",
                self.head,
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(self.head, receipt["approvedHead"])
        self.assertEqual("COMPLETE", receipt["terminal"])

    def test_machine_gated_issue_reaches_real_verifier_at_canonical_proof_path(self) -> None:
        issue = self.write(
            ".pm/issues/m1/w1.md",
            "---\nid: w1\nstatus: review\ndispatch_lane: workflow\n"
            "review_policy: machine-gated\n---\n## Acceptance\n- graph complete\n",
        )
        proof = self.write(
            ".pm/workflow-reviews/w1.md",
            "TERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:" + "a" * 64 + "\n",
        )
        result = subprocess.run(
            [
                "python3", str(PREFLIGHT), "--kind", "issue", "--id", "w1",
                "--approved-head", self.head,
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("machine-gated proof is not current", result.stderr)
        self.assertIn("candidate digest", result.stderr)
        self.assertNotIn("canonical machine-gated proof is unavailable", result.stderr)
        self.assertTrue(proof.is_file())

    def test_machine_gated_issue_refuses_legacy_proof_alias(self) -> None:
        self.write(
            ".pm/issues/m1/w2.md",
            "---\nid: w2\nstatus: review\ndispatch_lane: workflow\n"
            "review_policy: machine-gated\n---\n## Acceptance\n- graph complete\n",
        )
        self.write(
            ".pm/reviews/workflow-w2.md",
            "TERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:" + "a" * 64 + "\n",
        )
        result = subprocess.run(
            [
                "python3", str(PREFLIGHT), "--kind", "issue", "--id", "w2",
                "--approved-head", self.head,
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("canonical machine-gated proof is unavailable", result.stderr)
        self.assertIn(".pm/workflow-reviews/w2.md", result.stderr)

    def test_machine_gated_helper_env_override_is_ignored(self) -> None:
        self.write(
            ".pm/workflow-reviews/w1.md",
            "TERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:" + "a" * 64 + "\n",
        )
        malicious = self.write("malicious-helper", "#!/bin/sh\ntouch PWNED\n")
        malicious.chmod(0o755)
        expected = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "valid": True,
                    "stateBinding": {"rootHead": self.head},
                    "designId": "flow",
                    "runId": "run",
                    "evidenceCommitment": "sha256:" + "a" * 64,
                    "proof": {"path": ".pm/workflow-reviews/w1.md", "sha256": "b" * 64},
                }
            ),
            "",
        )
        with mock.patch.dict(os.environ, {"REVIEW_PR_WORKFLOW_PROOF_HELPER": str(malicious)}), mock.patch.object(
            PREFLIGHT_MODULE.subprocess, "run", return_value=expected
        ) as invoked:
            PREFLIGHT_MODULE._run_machine_gated(self.repo, "w1", self.head)
        self.assertEqual(
            str(PREFLIGHT_MODULE.WORKFLOW_PROOF_HELPER.resolve()), invoked.call_args.args[0][1]
        )
        self.assertFalse((self.repo / "PWNED").exists())

    def test_unknown_policy_and_nonworkflow_machine_policy_refuse(self) -> None:
        for kind, fields, expected in (
            ("issue", "review_policy: none\ndispatch_lane: workflow\n", "unsupported"),
            ("issue", "review_policy: machine-gated\ndispatch_lane: native-subagent\n", "workflow-lane"),
            ("milestone", "review_policy: machine-gated\n", "workflow-lane"),
        ):
            with self.subTest(kind=kind, fields=fields):
                item = (
                    self.repo / ".pm/issues/m1/x.md"
                    if kind == "issue"
                    else self.repo / ".pm/milestones/x.md"
                )
                item.parent.mkdir(parents=True, exist_ok=True)
                item.write_text(
                    f"---\nid: x\nstatus: review\n{fields}---\n## Acceptance\n- done\n",
                    encoding="utf-8",
                )
                result = subprocess.run(
                    [
                        "python3", str(PREFLIGHT), "--kind", kind, "--id", "x",
                        "--approved-head", self.head,
                    ],
                    cwd=self.repo,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(2, result.returncode)
                self.assertIn(expected, result.stderr)


class ResolvePrRemoteStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name, "checkout")
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "feature"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "merge@example.test"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Merge Test"], cwd=self.repo, check=True)
        (self.repo / "tracked.txt").write_text("reviewed\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "reviewed"], cwd=self.repo, check=True)
        self.head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:owner/repo.git"],
            cwd=self.repo,
            check=True,
        )
        self.fake_bin = Path(self.tmp.name, "bin")
        self.fake_bin.mkdir()
        self.calls = Path(self.tmp.name, "gh-calls.jsonl")
        self.state = Path(self.tmp.name, "state.json")
        self.write_state()
        self.gh = self.fake_bin / "gh"
        self.gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "state_path=Path(os.environ['FAKE_GH_STATE']); calls=Path(os.environ['FAKE_GH_CALLS'])\n"
            "state=json.loads(state_path.read_text()); argv=sys.argv[1:]\n"
            "drift=os.environ.get('FAKE_GH_DRIFT')\n"
            "if drift: Path(drift).write_text(Path(drift).read_text()+'\\n# drift\\n')\n"
            "visible={key:os.environ.get(key) for key in ('HOME','GH_CONFIG_DIR','XDG_CONFIG_HOME','GH_HOST','HTTPS_PROXY','PATH','GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN')}\n"
            "with calls.open('a') as f: f.write(json.dumps({'argv':argv,'cwd':os.getcwd(),'env':visible})+'\\n')\n"
            "if argv[:1]==['api']:\n"
            " print(json.dumps(state['approval'])); raise SystemExit(0)\n"
            "if argv[:2]==['pr','view']:\n"
            " print(json.dumps(state['view'])); raise SystemExit(0)\n"
            "if argv[:2]==['pr','checks']:\n"
            " print(state.get('checksOutput',''),end=''); raise SystemExit(state.get('checksExit',0))\n"
            "if argv[:2]==['pr','merge']:\n"
            " if state.get('mergeExit',0): raise SystemExit(state['mergeExit'])\n"
            " state['view']['state']='MERGED'; state['view']['mergeCommit']={'oid':state['mergeOid']}\n"
            " state_path.write_text(json.dumps(state)); raise SystemExit(0)\n"
            "raise SystemExit(99)\n",
            encoding="utf-8",
        )
        self.gh.chmod(0o755)
        self.preflight = self.fake_bin / "preflight"
        self.preflight.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os\n"
            f"print(json.dumps({{'artifact':'.pm/verdicts/i1.md','approvedHead':'{self.head}',"
            "'commitment':'sha256:'+'a'*64,'itemId':'i1','itemKind':'issue',"
            "'reviewPolicy':'independent','status':'review','terminal':'COMPLETE'}))\n",
            encoding="utf-8",
        )
        self.preflight.chmod(0o755)
        artifact = self.repo / ".pm/verdicts/i1.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            "TERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:" + "a" * 64 + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_state(self, **updates) -> None:
        state = {
            "approval": {"id": 77, "state": "APPROVED", "commit_id": getattr(self, "head", "h")},
            "view": {
                "state": "OPEN", "isDraft": False, "headRefOid": getattr(self, "head", "h"),
                "baseRefName": "main", "baseRefOid": "b" * 40,
                "mergeStateStatus": "CLEAN", "mergeCommit": None, "reviewDecision": "APPROVED",
            },
            "checksOutput": "tests\tpass\t1m\thttps://github.com/owner/repo/actions/runs/1\n",
            "checksExit": 0,
            "mergeExit": 0,
            "mergeOid": "c" * 40,
        }
        state.update(updates)
        self.state.write_text(json.dumps(state), encoding="utf-8")

    def authorization_document(
        self, *, repository="owner/repo", delete_branch=True, updates=None
    ) -> dict:
        authorization = {
            "schema": "resolve-pr-authorization/v1",
            "transactionId": "merge-i1-12",
            "authorizationKind": "user",
            "authorizationRef": "terminal-session:user-approved",
            "itemKind": "issue",
            "itemId": "i1",
            "repository": repository,
            "pr": 12,
            "approvedHead": self.head,
            "baseRefName": "main",
            "baseRefOid": "b" * 40,
            "strategy": "squash",
            "deleteBranch": delete_branch,
        }
        authorization.update(updates or {})
        return authorization

    def invoke_merge(
        self,
        authorization_path: Path,
        receipt_path: Path,
        *,
        repository="owner/repo",
        approval="77",
        caller_environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.update(caller_environment or {})
        fixture = REMOTE_MODULE._TestRemoteGh(
            self.gh,
            {"FAKE_GH_STATE": str(self.state), "FAKE_GH_CALLS": str(self.calls)},
        )
        argv = [
            "merge.py", "--checkout", str(self.repo), "--repository", repository,
            "--pr", "12", "--approval-review-id", approval, "--kind", "issue",
            "--id", "i1", "--approved-head", self.head,
            "--authorization-receipt", str(authorization_path),
            "--receipt", str(receipt_path),
        ]
        stdout, stderr = [], []

        class Capture:
            def __init__(self, target): self.target = target
            def write(self, value): self.target.append(value)

        original_run = REMOTE_MODULE.run
        self.process_calls = []
        self.git_calls = []

        def is_git_fetch(command) -> bool:
            parts = [str(part) for part in command]
            return "fetch" in parts and "--prune" in parts

        def run_without_network(command, *, cwd, env=None, pass_fds=()):
            self.process_calls.append(list(command))
            if is_git_fetch(command):
                return subprocess.CompletedProcess(command, 0, "", "")
            return original_run(command, cwd=cwd, env=env, pass_fds=pass_fds)

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", argv))
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            stack.enter_context(mock.patch.object(REMOTE_MODULE, "_TEST_REMOTE_GH", fixture))
            local = stack.enter_context(mock.patch.object(REMOTE_MODULE, "local_preflight"))
            stack.enter_context(mock.patch.object(REMOTE_MODULE, "run", side_effect=run_without_network))
            stack.enter_context(mock.patch.object(REMOTE_MODULE.sys, "stdout", Capture(stdout)))
            stack.enter_context(mock.patch.object(REMOTE_MODULE.sys, "stderr", Capture(stderr)))
            if hasattr(REMOTE_MODULE, "run_git"):
                original_run_git = REMOTE_MODULE.run_git

                def git_without_network(command, *, cwd):
                    self.git_calls.append(list(command))
                    if is_git_fetch(command) or is_git_fetch(["git", *command]):
                        return subprocess.CompletedProcess(["git", *list(command)], 0, "", "")
                    return original_run_git(command, cwd=cwd)

                stack.enter_context(
                    mock.patch.object(REMOTE_MODULE, "run_git", side_effect=git_without_network)
                )
            if hasattr(REMOTE_MODULE, "_trusted_git"):
                original_trusted_run = REMOTE_MODULE._trusted_git.git_run

                def trusted_git_without_network(
                    git, command, *, cwd, env=None, extra_globals=()
                ):
                    if is_git_fetch(command) or is_git_fetch(["git", *command]):
                        self.git_calls.append(list(command))
                        return subprocess.CompletedProcess(
                            ["git", *list(command)], 0, "", ""
                        )
                    return original_trusted_run(
                        git, command, cwd=cwd, env=env, extra_globals=extra_globals
                    )

                stack.enter_context(
                    mock.patch.object(
                        REMOTE_MODULE._trusted_git,
                        "git_run",
                        side_effect=trusted_git_without_network,
                    )
                )
            local.return_value = {
                "artifact": ".pm/verdicts/i1.md", "approvedHead": self.head,
                "commitment": "sha256:" + "a" * 64, "itemId": "i1",
                "itemKind": "issue", "reviewPolicy": "independent",
                "status": "review", "terminal": "COMPLETE",
            }
            code = REMOTE_MODULE.main()
            self.local_preflight_calls = local.call_count
        return subprocess.CompletedProcess(argv, code, "".join(stdout), "".join(stderr))

    def run_merge(
        self, *, repository="owner/repo", approval="77", delete_branch=True,
        authorization_updates=None, keep_receipt=False, caller_environment=None,
    ) -> subprocess.CompletedProcess[str]:
        authorization = self.authorization_document(
            repository=repository,
            delete_branch=delete_branch,
            updates=authorization_updates,
        )
        authorization_path = self.repo / ".pm/receipts/merge/issue-i1-authorization.json"
        authorization_path.parent.mkdir(parents=True, exist_ok=True)
        authorization_path.write_text(json.dumps(authorization) + "\n", encoding="utf-8")
        receipt_path = self.repo / ".pm/receipts/merge/issue-i1.json"
        if not keep_receipt:
            receipt_path.unlink(missing_ok=True)
        return self.invoke_merge(
            authorization_path,
            receipt_path,
            repository=repository,
            approval=approval,
            caller_environment=caller_environment,
        )

    def calls_json(self) -> list[dict]:
        if not self.calls.exists():
            return []
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]

    def assert_receipt_attack_refused_before_mutation(
        self, result: subprocess.CompletedProcess[str], sentinel: Path, original: bytes
    ) -> None:
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual(original, sentinel.read_bytes())
        self.assertEqual([], self.calls_json())
        self.assertEqual(0, self.local_preflight_calls)
        recorded = [*self.process_calls, *getattr(self, "git_calls", [])]
        self.assertFalse(
            any("fetch" in list(command) and "--prune" in list(command) for command in recorded)
        )
        self.assertEqual("OPEN", json.loads(self.state.read_text(encoding="utf-8"))["view"]["state"])

    def test_ambient_path_home_and_config_cannot_select_remote_authority(self) -> None:
        fake_home = Path(self.tmp.name, "caller-home")
        fake_config = Path(self.tmp.name, "caller-gh-config")
        poisoned = dict(os.environ)
        poisoned.update(
            {
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "HOME": str(fake_home),
                "GH_CONFIG_DIR": str(fake_config),
                "XDG_CONFIG_HOME": str(fake_config),
                "GH_HOST": "attacker.example",
                "HTTPS_PROXY": "http://attacker.example:8080",
                "ALL_PROXY": "socks5://attacker.example:1080",
                "SSL_CERT_FILE": str(fake_config / "attacker-ca.pem"),
                "BASH_ENV": str(fake_config / "shell-hook"),
                "LD_PRELOAD": str(fake_config / "loader-hook.so"),
                "GIT_ASKPASS": str(fake_config / "askpass"),
                "GH_TOKEN": "fixture-token",
                "GITHUB_TOKEN": "fixture-github-token",
                "GH_ENTERPRISE_TOKEN": "enterprise-token",
            }
        )

        with mock.patch.dict(os.environ, poisoned, clear=True):
            gh = REMOTE_MODULE.require_gh()
            remote_env = REMOTE_MODULE.scrubbed_remote_env()

        try:
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
            self.assertNotEqual(self.gh.resolve(), gh.path)
            self.assertIn(gh.path.parent, REMOTE_MODULE.TRUSTED_GH_INSTALL_ROOTS)
            metadata = gh.path.stat(follow_symlinks=False)
            self.assertEqual(REMOTE_MODULE.TRUSTED_GH_OWNER_UID, metadata.st_uid)
            self.assertEqual(REMOTE_MODULE.TRUSTED_GH_OWNER_UID, metadata.st_gid)
            self.assertEqual(1, metadata.st_nlink)
            self.assertEqual(0, metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
            self.assertEqual(
                "sha256:" + hashlib.sha256(gh.path.read_bytes()).hexdigest(), gh.digest
            )
            self.assertEqual(str(home), remote_env["HOME"])
            self.assertEqual(str(home / ".config" / "gh"), remote_env["GH_CONFIG_DIR"])
            self.assertEqual(str(home / ".config"), remote_env["XDG_CONFIG_HOME"])
            self.assertEqual("fixture-token", remote_env["GH_TOKEN"])
            self.assertEqual("fixture-github-token", remote_env["GITHUB_TOKEN"])
            self.assertEqual(REMOTE_MODULE.REMOTE_RUNTIME_PATH, remote_env["PATH"])
            for unsafe in (
                "GH_HOST",
                "GH_ENTERPRISE_TOKEN",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "SSL_CERT_FILE",
                "BASH_ENV",
                "LD_PRELOAD",
                "GIT_ASKPASS",
            ):
                self.assertNotIn(unsafe, remote_env)
        finally:
            gh.close()

    def test_merge_caller_path_home_and_git_env_cannot_spoof_origin(self) -> None:
        subprocess.run(
            ["git", "remote", "set-url", "origin", "git@github.com:other/repo.git"],
            cwd=self.repo,
            check=True,
        )
        poison_home = Path(self.tmp.name, "git-poison-home")
        poison_home.mkdir()
        (poison_home / ".gitconfig").write_text(
            "[url \"git@github.com:owner/\"]\n    insteadOf = git@github.com:other/\n",
            encoding="utf-8",
        )
        fake_bin = Path(self.tmp.name, "git-poison-bin")
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"repo = {str(self.repo)!r}\n"
            f"head = {self.head!r}\n"
            "argv = sys.argv[1:]\n"
            "if argv[:2] == ['rev-parse', '--show-toplevel']:\n"
            "    print(repo)\n"
            "    raise SystemExit(0)\n"
            "if argv[:2] == ['rev-parse', '--verify']:\n"
            "    print(head)\n"
            "    raise SystemExit(0)\n"
            "if argv[:3] == ['remote', 'get-url', 'origin']:\n"
            "    print('git@github.com:owner/repo.git')\n"
            "    raise SystemExit(0)\n"
            "raise SystemExit(99)\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        result = self.run_merge(
            caller_environment={
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "HOME": str(poison_home),
                "GIT_DIR": str(Path(self.tmp.name, "missing.git")),
                "GIT_WORK_TREE": str(Path(self.tmp.name)),
                "GIT_CONFIG_GLOBAL": str(poison_home / ".gitconfig"),
                "GIT_CONFIG_SYSTEM": str(poison_home / ".gitconfig"),
                "GIT_ASKPASS": str(poison_home / "askpass"),
                "GIT_SSH_COMMAND": "true",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "url.git@github.com:owner/.insteadof",
                "GIT_CONFIG_VALUE_0": "git@github.com:other/",
            }
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("remote", result.stderr.lower())
        self.assertEqual([], self.calls_json())
        self.assertEqual(0, self.local_preflight_calls)

    def test_local_git_binding_ignores_path_home_and_git_variables(self) -> None:
        self.assertTrue(hasattr(REMOTE_MODULE, "require_git"))
        self.assertTrue(hasattr(REMOTE_MODULE, "scrubbed_git_env"))
        self.assertTrue(hasattr(PREFLIGHT_MODULE, "run_git"))
        self.assertTrue(hasattr(REMOTE_MODULE, "run_git"))
        fake_home = Path(self.tmp.name, "caller-git-home")
        fake_home.mkdir()
        poisoned = dict(os.environ)
        poisoned.update(
            {
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "HOME": str(fake_home),
                "GIT_DIR": str(self.repo / "absent.git"),
                "GIT_WORK_TREE": str(self.repo.parent),
                "GIT_INDEX_FILE": str(self.repo / "absent.index"),
                "GIT_CONFIG_GLOBAL": str(fake_home / ".gitconfig"),
                "GIT_CONFIG_SYSTEM": str(fake_home / ".gitconfig"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": str(fake_home),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.repo.parent),
                "GIT_ASKPASS": str(fake_home / "askpass"),
                "GIT_SSH_COMMAND": "true",
                "GIT_EXTERNAL_DIFF": str(fake_home / "diff"),
            }
        )
        with mock.patch.dict(os.environ, poisoned, clear=True):
            git = REMOTE_MODULE.require_git()
            git_env = REMOTE_MODULE.scrubbed_git_env()
            result = REMOTE_MODULE.run_git(["rev-parse", "--show-toplevel"], cwd=self.repo)
            status = REMOTE_MODULE.run_git(["status", "--porcelain=v1"], cwd=self.repo)
            ancestry = REMOTE_MODULE.run_git(["merge-base", "HEAD", "HEAD"], cwd=self.repo)
        try:
            home = Path(pwd.getpwuid(os.getuid()).pw_dir)
            self.assertNotEqual(self.fake_bin / "git", git.path)
            self.assertIn(git.path.parent, REMOTE_MODULE.TRUSTED_GIT_INSTALL_ROOTS)
            metadata = git.path.stat(follow_symlinks=False)
            self.assertEqual(REMOTE_MODULE.TRUSTED_GIT_OWNER_UID, metadata.st_uid)
            self.assertEqual(1, metadata.st_nlink)
            self.assertEqual(0, metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
            self.assertEqual(
                "sha256:" + hashlib.sha256(git.path.read_bytes()).hexdigest(), git.digest
            )
            self.assertEqual(str(home), git_env["HOME"])
            self.assertNotEqual(str(fake_home), git_env["HOME"])
            self.assertEqual(REMOTE_MODULE.GIT_RUNTIME_PATH, git_env["PATH"])
            self.assertEqual("1", git_env["GIT_CONFIG_NOSYSTEM"])
            self.assertEqual(os.devnull, git_env["GIT_CONFIG_GLOBAL"])
            self.assertEqual("1", git_env["GIT_NO_REPLACE_OBJECTS"])
            self.assertEqual("1", git_env["GIT_LITERAL_PATHSPECS"])
            for unsafe in (
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_CONFIG_COUNT",
                "GIT_CONFIG_KEY_0",
                "GIT_CONFIG_VALUE_0",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                "GIT_ASKPASS",
                "GIT_SSH_COMMAND",
                "GIT_EXTERNAL_DIFF",
            ):
                self.assertNotIn(unsafe, git_env)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(str(self.repo), Path(result.stdout.strip()).resolve().as_posix())
            self.assertEqual(0, status.returncode, status.stderr)
            self.assertEqual(0, ancestry.returncode, ancestry.stderr)
            self.assertEqual(self.head, ancestry.stdout.strip())
        finally:
            git.close()

    def test_private_git_fixture_refuses_symlink_and_post_operation_executable_drift(self) -> None:
        symlink = Path(self.tmp.name, "git-symlink")
        symlink.symlink_to("/usr/bin/git")
        fixture = REMOTE_MODULE._TestLocalGit(symlink, {})
        with mock.patch.object(REMOTE_MODULE, "_TEST_LOCAL_GIT", fixture):
            with self.assertRaises(REMOTE_MODULE.GitBindingError):
                REMOTE_MODULE.require_git()

        drifting = self.fake_bin / "drifting-git"
        drifting.write_text("#!/usr/bin/env python3\nimport pathlib, os, sys\n"
                            "target=pathlib.Path(os.environ['FAKE_GIT_DRIFT'])\n"
                            "target.write_text(target.read_text()+'\\n# drift\\n')\n"
                            "print('ok')\n", encoding="utf-8")
        drifting.chmod(0o755)
        fixture = REMOTE_MODULE._TestLocalGit(
            drifting, {"FAKE_GIT_DRIFT": str(drifting)}
        )
        with mock.patch.object(REMOTE_MODULE, "_TEST_LOCAL_GIT", fixture):
            with REMOTE_MODULE.require_git() as git:
                with self.assertRaises(REMOTE_MODULE.GitBindingError):
                    REMOTE_MODULE.git_run(git, ["rev-parse", "--show-toplevel"], cwd=self.repo)

    def test_run_git_disables_replacement_objects_and_repository_hooks(self) -> None:
        other_tree = self.repo / "replaced.txt"
        other_tree.write_text("replacement\n", encoding="utf-8")
        subprocess.run(["git", "add", "replaced.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "replacement"], cwd=self.repo, check=True)
        replacement = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()
        subprocess.run(["git", "replace", self.head, replacement], cwd=self.repo, check=True)
        followed = subprocess.check_output(
            ["git", "log", "-1", "--format=%s", self.head], cwd=self.repo, text=True
        ).strip()
        self.assertEqual("replacement", followed)

        isolated = REMOTE_MODULE.run_git(
            ["log", "-1", "--format=%s", self.head], cwd=self.repo
        )
        self.assertEqual(0, isolated.returncode, isolated.stderr)
        self.assertEqual("reviewed", isolated.stdout.strip())

        hook_dir = Path(self.tmp.name, "repo-hooks")
        hook_dir.mkdir()
        hooked = Path(self.tmp.name, "HOOKED")
        for name in ("reference-transaction", "pre-auto-gc", "post-checkout"):
            hook = hook_dir / name
            hook.write_text(f"#!/bin/sh\nprintf ran >> '{hooked}'\n", encoding="utf-8")
            hook.chmod(0o755)
        subprocess.run(
            ["git", "config", "core.hooksPath", str(hook_dir)], cwd=self.repo, check=True
        )
        source = Path(self.tmp.name, "local-source")
        subprocess.run(["git", "clone", "--bare", "-q", str(self.repo), str(source)], check=True)
        subprocess.run(
            ["git", "remote", "add", "local-source", str(source)], cwd=self.repo, check=True
        )
        fetched = REMOTE_MODULE.run_git(
            ["fetch", "--prune", "local-source"], cwd=self.repo
        )
        self.assertEqual(0, fetched.returncode, fetched.stderr)
        self.assertFalse(hooked.exists())

    def test_local_core_worktree_cannot_redirect_reviewed_checkout(self) -> None:
        outside = Path(self.tmp.name, "outside-worktree")
        outside.mkdir()
        (outside / "tracked.txt").write_text("forged outside worktree\n", encoding="utf-8")
        subprocess.run(
            ["git", "config", "core.worktree", str(outside)], cwd=self.repo, check=True
        )

        toplevel = REMOTE_MODULE.run_git(["rev-parse", "--show-toplevel"], cwd=self.repo)
        self.assertEqual(0, toplevel.returncode, toplevel.stderr)
        self.assertEqual(str(self.repo), Path(toplevel.stdout.strip()).resolve().as_posix())
        status = REMOTE_MODULE.run_git(["status", "--porcelain=v1"], cwd=self.repo)
        self.assertEqual(0, status.returncode, status.stderr)
        self.assertNotIn("tracked.txt", status.stdout)
        self.assertNotIn("forged outside worktree", status.stdout)
        self.assertEqual(
            self.repo, REMOTE_MODULE.canonical_checkout(str(self.repo))
        )

        result = self.run_merge()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            self.head,
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
            ).strip(),
        )

    def test_local_include_url_rewrite_refuses_before_remote(self) -> None:
        included = Path(self.tmp.name, "rewrite.gitconfig")
        included.write_text(
            "[remote \"origin\"]\n"
            "    url = git@attacker.example:other/repo.git\n"
            "[url \"git@attacker.example:\"]\n"
            "    insteadOf = git@github.com:\n"
            "    pushInsteadOf = git@github.com:\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "config", "include.path", str(included)], cwd=self.repo, check=True
        )
        result = self.run_merge()
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertRegex(
            result.stderr.lower(), r"include|insteadof|rewrite|redirect"
        )
        self.assertEqual([], self.calls_json())
        self.assertEqual(0, self.local_preflight_calls)
        recorded = [*self.process_calls, *getattr(self, "git_calls", [])]
        self.assertFalse(
            any("fetch" in list(command) and "--prune" in list(command) for command in recorded)
        )

    def test_production_cli_rejects_binary_and_environment_override_flags(self) -> None:
        required = [
            "merge.py",
            "--checkout",
            str(self.repo),
            "--repository",
            "owner/repo",
            "--pr",
            "12",
            "--approval-review-id",
            "77",
            "--kind",
            "issue",
            "--id",
            "i1",
            "--approved-head",
            self.head,
            "--authorization-receipt",
            str(self.repo / ".pm/receipts/merge/issue-i1-authorization.json"),
            "--receipt",
            str(self.repo / ".pm/receipts/merge/issue-i1.json"),
        ]
        for override in ("--gh", "--remote-env"):
            with self.subTest(override=override), mock.patch.object(
                sys, "argv", [*required, override, str(self.gh)]
            ), mock.patch.object(sys, "stderr", io.StringIO()), self.assertRaises(
                SystemExit
            ) as raised:
                REMOTE_MODULE.parse_args()
            self.assertEqual(2, raised.exception.code)

    def test_private_fixture_refuses_symlink_and_post_operation_executable_drift(self) -> None:
        symlink = Path(self.tmp.name, "gh-symlink")
        symlink.symlink_to(self.gh)
        fixture = REMOTE_MODULE._TestRemoteGh(symlink, {})
        with mock.patch.object(REMOTE_MODULE, "_TEST_REMOTE_GH", fixture):
            with self.assertRaises(REMOTE_MODULE.MergeError):
                REMOTE_MODULE.require_gh()

        drifting = self.fake_bin / "drifting-gh"
        shutil.copyfile(self.gh, drifting)
        drifting.chmod(0o755)
        fixture = REMOTE_MODULE._TestRemoteGh(
            drifting,
            {
                "FAKE_GH_STATE": str(self.state),
                "FAKE_GH_CALLS": str(self.calls),
                "FAKE_GH_DRIFT": str(drifting),
            },
        )
        with mock.patch.object(REMOTE_MODULE, "_TEST_REMOTE_GH", fixture):
            with REMOTE_MODULE.require_gh() as gh, tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(REMOTE_MODULE.MergeError):
                    REMOTE_MODULE.gh_run(
                        gh,
                        ["api", "repos/owner/repo/pulls/12/reviews/77", "--hostname", "github.com"],
                        cwd=Path(temp),
                        env=REMOTE_MODULE.scrubbed_remote_env(),
                    )

    def test_nominal_private_fixture_uses_bound_descriptor_and_scrubbed_environment(self) -> None:
        fake_home = Path(self.tmp.name, "caller-home")
        fake_config = Path(self.tmp.name, "caller-gh-config")
        result = self.run_merge(
            caller_environment={
                "PATH": f"{self.fake_bin}:/usr/bin:/bin",
                "HOME": str(fake_home),
                "GH_CONFIG_DIR": str(fake_config),
                "XDG_CONFIG_HOME": str(fake_config),
                "GH_HOST": "attacker.example",
                "HTTPS_PROXY": "http://attacker.example:8080",
                "GH_TOKEN": "fixture-token",
                "GITHUB_TOKEN": "fixture-github-token",
                "GH_ENTERPRISE_TOKEN": "enterprise-token",
            }
        )
        self.assertEqual(0, result.returncode, result.stderr)
        remote_commands = [
            command for command in self.process_calls if command[0].startswith("/proc/self/fd/")
        ]
        self.assertTrue(remote_commands)
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        for call in self.calls_json():
            self.assertEqual(str(home), call["env"]["HOME"])
            self.assertEqual(str(home / ".config" / "gh"), call["env"]["GH_CONFIG_DIR"])
            self.assertEqual(REMOTE_MODULE.REMOTE_RUNTIME_PATH, call["env"]["PATH"])
            self.assertIsNone(call["env"]["GH_HOST"])
            self.assertIsNone(call["env"]["HTTPS_PROXY"])
            self.assertIsNone(call["env"]["GH_ENTERPRISE_TOKEN"])
            self.assertEqual("fixture-token", call["env"]["GH_TOKEN"])
            self.assertEqual("fixture-github-token", call["env"]["GITHUB_TOKEN"])

    def prepare_external_merge_directory(self, name: str) -> tuple[Path, Path, Path, bytes]:
        external_merge = Path(self.tmp.name, name)
        external_merge.mkdir(parents=True)
        authorization = external_merge / "issue-i1-authorization.json"
        authorization.write_text(
            json.dumps(self.authorization_document()) + "\n", encoding="utf-8"
        )
        sentinel = external_merge / "issue-i1.json"
        sentinel.write_text("outside receipt sentinel\n", encoding="utf-8")
        return external_merge, authorization, sentinel, sentinel.read_bytes()

    def test_symlinked_pm_parent_refuses_before_remote_or_outside_write(self) -> None:
        external_pm = Path(self.tmp.name, "outside-pm")
        external_merge, authorization, sentinel, original = self.prepare_external_merge_directory(
            "outside-pm/receipts/merge"
        )
        artifact = external_pm / "verdicts/i1.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("outside artifact sentinel\n", encoding="utf-8")
        shutil.rmtree(self.repo / ".pm")
        (self.repo / ".pm").symlink_to(external_pm, target_is_directory=True)

        result = self.invoke_merge(
            self.repo / ".pm/receipts/merge" / authorization.name,
            self.repo / ".pm/receipts/merge" / sentinel.name,
        )

        self.assert_receipt_attack_refused_before_mutation(result, sentinel, original)
        self.assertTrue(external_merge.is_dir())

    def test_symlinked_receipts_parent_refuses_before_remote_or_outside_write(self) -> None:
        external_receipts = Path(self.tmp.name, "outside-receipts")
        _, authorization, sentinel, original = self.prepare_external_merge_directory(
            "outside-receipts/merge"
        )
        (self.repo / ".pm/receipts").symlink_to(
            external_receipts, target_is_directory=True
        )

        result = self.invoke_merge(
            self.repo / ".pm/receipts/merge" / authorization.name,
            self.repo / ".pm/receipts/merge" / sentinel.name,
        )

        self.assert_receipt_attack_refused_before_mutation(result, sentinel, original)

    def test_symlinked_merge_parent_refuses_before_remote_or_outside_write(self) -> None:
        external_merge, authorization, sentinel, original = self.prepare_external_merge_directory(
            "outside-merge"
        )
        merge_parent = self.repo / ".pm/receipts/merge"
        merge_parent.parent.mkdir(parents=True)
        merge_parent.symlink_to(external_merge, target_is_directory=True)

        result = self.invoke_merge(
            merge_parent / authorization.name,
            merge_parent / sentinel.name,
        )

        self.assert_receipt_attack_refused_before_mutation(result, sentinel, original)

    def test_authorization_symlink_leaf_refuses_before_remote_or_outside_write(self) -> None:
        external_merge, authorization, _, _ = self.prepare_external_merge_directory(
            "outside-authorization-symlink"
        )
        original = authorization.read_bytes()
        merge_parent = self.repo / ".pm/receipts/merge"
        merge_parent.mkdir(parents=True)
        local_authorization = merge_parent / authorization.name
        local_authorization.symlink_to(authorization)

        result = self.invoke_merge(local_authorization, merge_parent / "new-receipt.json")

        self.assert_receipt_attack_refused_before_mutation(result, authorization, original)
        self.assertTrue(external_merge.is_dir())

    def test_authorization_hardlink_leaf_refuses_before_remote_or_outside_write(self) -> None:
        _, authorization, _, _ = self.prepare_external_merge_directory(
            "outside-authorization-hardlink"
        )
        original = authorization.read_bytes()
        merge_parent = self.repo / ".pm/receipts/merge"
        merge_parent.mkdir(parents=True)
        local_authorization = merge_parent / authorization.name
        os.link(authorization, local_authorization)

        result = self.invoke_merge(local_authorization, merge_parent / "new-receipt.json")

        self.assert_receipt_attack_refused_before_mutation(result, authorization, original)
        self.assertEqual(2, authorization.stat().st_nlink)

    def test_receipt_symlink_leaf_refuses_before_remote_or_outside_write(self) -> None:
        _, _, sentinel, original = self.prepare_external_merge_directory(
            "outside-receipt-symlink"
        )
        merge_parent = self.repo / ".pm/receipts/merge"
        merge_parent.mkdir(parents=True)
        authorization = merge_parent / "issue-i1-authorization.json"
        authorization.write_text(
            json.dumps(self.authorization_document()) + "\n", encoding="utf-8"
        )
        local_receipt = merge_parent / "issue-i1.json"
        local_receipt.symlink_to(sentinel)

        result = self.invoke_merge(authorization, local_receipt)

        self.assert_receipt_attack_refused_before_mutation(result, sentinel, original)

    def test_receipt_hardlink_leaf_refuses_before_remote_or_outside_write(self) -> None:
        _, _, sentinel, original = self.prepare_external_merge_directory(
            "outside-receipt-hardlink"
        )
        merge_parent = self.repo / ".pm/receipts/merge"
        merge_parent.mkdir(parents=True)
        authorization = merge_parent / "issue-i1-authorization.json"
        authorization.write_text(
            json.dumps(self.authorization_document()) + "\n", encoding="utf-8"
        )
        local_receipt = merge_parent / "issue-i1.json"
        os.link(sentinel, local_receipt)

        result = self.invoke_merge(authorization, local_receipt)

        self.assert_receipt_attack_refused_before_mutation(result, sentinel, original)
        self.assertEqual(2, sentinel.stat().st_nlink)

    def test_durable_receipt_write_is_exclusive_durable_and_idempotent(self) -> None:
        receipt_path = self.repo / ".pm/receipts/merge/direct-write.json"
        receipt_path.parent.mkdir(parents=True)
        value = {"schema": "test-receipt/v1", "state": "MERGED"}

        REMOTE_MODULE._write_receipt(self.repo, receipt_path, value)
        first = receipt_path.read_bytes()
        REMOTE_MODULE._write_receipt(self.repo, receipt_path, value)

        self.assertEqual(first, receipt_path.read_bytes())
        self.assertEqual(1, receipt_path.stat().st_nlink)
        self.assertEqual(0o600, receipt_path.stat().st_mode & 0o777)
        self.assertEqual([], list(receipt_path.parent.glob(".review-pr-*.tmp")))

        different = first + b"different\n"
        receipt_path.write_bytes(different)
        with self.assertRaises(REMOTE_MODULE.MergeError):
            REMOTE_MODULE._write_receipt(self.repo, receipt_path, value)
        self.assertEqual(different, receipt_path.read_bytes())

    def test_publish_race_cannot_replace_symlink_or_hardlink_leaf(self) -> None:
        receipt_path = self.repo / ".pm/receipts/merge/raced-write.json"
        receipt_path.parent.mkdir(parents=True)
        sentinel = Path(self.tmp.name, "publish-race-sentinel.json")
        sentinel.write_text("outside publish-race sentinel\n", encoding="utf-8")
        original = sentinel.read_bytes()
        real_link = REMOTE_MODULE.os.link
        value = {"schema": "test-receipt/v1", "state": "MERGED"}

        for attack in ("symlink", "hardlink"):
            with self.subTest(attack=attack):
                receipt_path.unlink(missing_ok=True)

                def install_attack_then_link(
                    source,
                    destination,
                    *,
                    src_dir_fd=None,
                    dst_dir_fd=None,
                    follow_symlinks=True,
                ):
                    if attack == "symlink":
                        receipt_path.symlink_to(sentinel)
                    else:
                        real_link(sentinel, receipt_path)
                    return real_link(
                        source,
                        destination,
                        src_dir_fd=src_dir_fd,
                        dst_dir_fd=dst_dir_fd,
                        follow_symlinks=follow_symlinks,
                    )

                with mock.patch.object(
                    REMOTE_MODULE.os, "link", side_effect=install_attack_then_link
                ), self.assertRaises(REMOTE_MODULE.MergeError):
                    REMOTE_MODULE._write_receipt(self.repo, receipt_path, value)

                self.assertEqual(original, sentinel.read_bytes())
                self.assertEqual([], list(receipt_path.parent.glob(".review-pr-*.tmp")))

    def test_open_green_exact_approval_merges_and_revalidates(self) -> None:
        before = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=self.repo)
        result = self.run_merge()
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual("MERGED", receipt["state"])
        self.assertEqual("c" * 40, receipt["mergeCommit"])
        self.assertEqual("main", receipt["baseRefName"])
        self.assertEqual("b" * 40, receipt["baseRefOid"])
        self.assertTrue(receipt["deleteBranch"])
        self.assertEqual(
            "sha256:" + hashlib.sha256(
                (self.repo / ".pm/receipts/merge/issue-i1-authorization.json").read_bytes()
            ).hexdigest(),
            receipt["authorizationReceiptSha256"],
        )
        self.assertEqual(
            receipt,
            json.loads((self.repo / ".pm/receipts/merge/issue-i1.json").read_text()),
        )
        self.assertEqual(self.head, subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip())
        self.assertEqual(before, subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=self.repo))
        calls = self.calls_json()
        self.assertTrue(all(call["cwd"] != str(self.repo) for call in calls))
        self.assertTrue(all("--repo" in call["argv"] or call["argv"][0] == "api" for call in calls))
        api = next(call for call in calls if call["argv"][0] == "api")
        self.assertNotIn("--repo", api["argv"])
        self.assertIn("--hostname", api["argv"])
        self.assertIn("github.com", api["argv"])
        self.assertEqual("repos/owner/repo/pulls/12/reviews/77", api["argv"][1])
        checks = [call for call in calls if call["argv"][:2] == ["pr", "checks"]]
        self.assertEqual(3, len(checks))
        self.assertTrue(all("--repo" in call["argv"] for call in checks))
        merge = next(call for call in calls if call["argv"][:2] == ["pr", "merge"])
        self.assertIn("--match-head-commit", merge["argv"])
        self.assertIn("--delete-branch", merge["argv"])

    def test_branch_deletion_and_base_are_exactly_authorized(self) -> None:
        no_delete = self.run_merge(delete_branch=False)
        self.assertEqual(0, no_delete.returncode, no_delete.stderr)
        merge = next(
            call for call in self.calls_json() if call["argv"][:2] == ["pr", "merge"]
        )
        self.assertNotIn("--delete-branch", merge["argv"])

        self.calls.unlink(missing_ok=True)
        self.write_state()
        mismatched = self.run_merge(
            authorization_updates={"baseRefOid": "d" * 40}
        )
        self.assertEqual(2, mismatched.returncode)
        self.assertIn("authorized base", mismatched.stderr.lower())
        self.assertFalse(any(
            call["argv"][:2] == ["pr", "merge"] for call in self.calls_json()
        ))

    def test_wrong_repository_binding_refuses_before_gh(self) -> None:
        result = self.run_merge(repository="other/repo")
        self.assertEqual(2, result.returncode)
        self.assertIn("remote", result.stderr)
        self.assertEqual([], self.calls_json())

    def test_stale_or_nonapproved_review_refuses(self) -> None:
        for approval in (
            {"id": 78, "state": "APPROVED", "commit_id": self.head},
            {"id": 77, "state": "CHANGES_REQUESTED", "commit_id": self.head},
            {"id": 77, "state": "APPROVED", "commit_id": "d" * 40},
        ):
            with self.subTest(approval=approval):
                self.calls.unlink(missing_ok=True)
                self.write_state(approval=approval)
                result = self.run_merge()
                self.assertEqual(2, result.returncode)
                self.assertNotIn("pr merge", " ".join(" ".join(x["argv"]) for x in self.calls_json()))

    def test_draft_head_drift_closed_and_bad_ci_refuse(self) -> None:
        clean_view = {
            "state":"OPEN","isDraft":False,"headRefOid":self.head,
            "baseRefName":"main","baseRefOid":"b"*40,
            "mergeStateStatus":"CLEAN","mergeCommit":None,"reviewDecision":"APPROVED",
        }
        passing = "tests\tpass\t1m\thttps://github.com/owner/repo/actions/runs/1\n"
        cases = [
            ({**clean_view, "isDraft": True}, 0, passing),
            ({**clean_view, "headRefOid": "e" * 40}, 0, passing),
            ({**clean_view, "state": "CLOSED"}, 0, passing),
            ({**clean_view, "reviewDecision": "CHANGES_REQUESTED"}, 0, passing),
            (clean_view, 1, "tests\tfail\t1m\thttps://github.com/owner/repo/actions/runs/1\n"),
            (clean_view, 8, "tests\tpending\t0\thttps://github.com/owner/repo/actions/runs/1\n"),
            (clean_view, 0, ""),
        ]
        for view, checks_exit, checks_output in cases:
            with self.subTest(view=view, checks_exit=checks_exit, checks_output=checks_output):
                self.calls.unlink(missing_ok=True)
                self.write_state(view=view, checksExit=checks_exit, checksOutput=checks_output)
                result = self.run_merge()
                self.assertEqual(2, result.returncode)
                self.assertFalse(any(x["argv"][:2] == ["pr", "merge"] for x in self.calls_json()))

    def test_exact_already_merged_recovers_without_second_merge(self) -> None:
        self.write_state(
            view={
                "state":"MERGED","isDraft":False,"headRefOid":self.head,
                "baseRefName":"main","baseRefOid":"b"*40,
                "mergeStateStatus":"UNKNOWN","mergeCommit":{"oid":"f"*40},
                "reviewDecision":"APPROVED",
            }
        )
        result = self.run_merge()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("f" * 40, json.loads(result.stdout)["mergeCommit"])
        self.assertFalse(any(x["argv"][:2] == ["pr", "merge"] for x in self.calls_json()))

    def test_artifact_authority_env_overrides_are_ignored(self) -> None:
        malicious_preflight = Path(self.tmp.name, "malicious-preflight")
        malicious_preflight.write_text("#!/bin/sh\ntouch PWNED\n", encoding="utf-8")
        malicious_preflight.chmod(0o755)
        env = dict(os.environ)
        env.update(
            {
                "REVIEW_PR_PREFLIGHT": str(malicious_preflight),
                "REVIEW_PR_WORKFLOW_PROOF_HELPER": str(malicious_preflight),
            }
        )
        argv = [
            "merge.py", "--checkout", str(self.repo), "--repository", "other/repo",
            "--pr", "12", "--approval-review-id", "77", "--kind", "issue",
            "--id", "i1", "--approved-head", self.head,
            "--authorization-receipt",
            str(self.repo / ".pm/receipts/merge/issue-i1-authorization.json"),
            "--receipt", str(self.repo / ".pm/receipts/merge/issue-i1.json"),
        ]
        auth = self.repo / ".pm/receipts/merge/issue-i1-authorization.json"
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text("{}\n", encoding="utf-8")
        with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(2, REMOTE_MODULE.main())
        self.assertFalse((self.repo / "PWNED").exists())
        self.assertNotIn("REVIEW_PR_PREFLIGHT", REMOTE.read_text(encoding="utf-8"))
        self.assertNotIn("REVIEW_PR_WORKFLOW_PROOF_HELPER", REMOTE.read_text(encoding="utf-8"))

    def test_insecure_or_ambiguous_repository_names_and_remotes_refuse(self) -> None:
        for repository in ("./repo", "owner/.."):
            with self.subTest(repository=repository):
                self.calls.unlink(missing_ok=True)
                result = self.run_merge(repository=repository)
                self.assertEqual(2, result.returncode)
                self.assertEqual([], self.calls_json())
        subprocess.run(
            ["git", "remote", "set-url", "origin", "http://github.com/owner/repo.git"],
            cwd=self.repo,
            check=True,
        )
        result = self.run_merge()
        self.assertEqual(2, result.returncode)
        self.assertIn("SSH or HTTPS", result.stderr)
        self.assertEqual([], self.calls_json())

    def test_verify_merged_github_accepts_merged_without_invoking_merge(self) -> None:
        self.write_state(
            view={
                "state": "MERGED",
                "isDraft": False,
                "headRefOid": self.head,
                "baseRefName": "main",
                "baseRefOid": "b" * 40,
                "mergeStateStatus": "UNKNOWN",
                "mergeCommit": {"oid": "c" * 40},
                "reviewDecision": "APPROVED",
            }
        )
        fixture = REMOTE_MODULE._TestRemoteGh(
            self.gh,
            {"FAKE_GH_STATE": str(self.state), "FAKE_GH_CALLS": str(self.calls)},
        )
        with mock.patch.object(REMOTE_MODULE, "_TEST_REMOTE_GH", fixture):
            payload = REMOTE_MODULE.verify_merged_github(
                repository="owner/repo",
                pr="12",
                approval_review_id="77",
                approved_head=self.head,
                base_ref_name="main",
                base_ref_oid="b" * 40,
                merge_commit="c" * 40,
            )
        self.assertEqual("MERGED", payload["view"]["state"])
        recorded = self.calls_json()
        self.assertTrue(any(call["argv"][:1] == ["api"] for call in recorded))
        self.assertTrue(any(call["argv"][:2] == ["pr", "view"] for call in recorded))
        self.assertFalse(any(call["argv"][:2] == ["pr", "merge"] for call in recorded))
        self.assertFalse(any(call["argv"][:2] == ["pr", "checks"] for call in recorded))

    def test_verify_merged_github_refuses_open_mismatch_and_missing_gh(self) -> None:
        with mock.patch.object(
            REMOTE_MODULE, "TRUSTED_GH_INSTALL_ROOTS", (Path("/review-pr-missing-gh"),)
        ):
            with self.assertRaises(REMOTE_MODULE.MergeError) as raised:
                REMOTE_MODULE.verify_merged_github(
                    repository="owner/repo",
                    pr="12",
                    approval_review_id="77",
                    approved_head=self.head,
                    base_ref_name="main",
                    base_ref_oid="b" * 40,
                    merge_commit="c" * 40,
                )
        self.assertIn("gh", str(raised.exception).lower())

        fixture = REMOTE_MODULE._TestRemoteGh(
            self.gh,
            {"FAKE_GH_STATE": str(self.state), "FAKE_GH_CALLS": str(self.calls)},
        )
        with mock.patch.object(REMOTE_MODULE, "_TEST_REMOTE_GH", fixture):
            with self.assertRaises(REMOTE_MODULE.MergeError):
                REMOTE_MODULE.verify_merged_github(
                    repository="owner/repo",
                    pr="12",
                    approval_review_id="77",
                    approved_head=self.head,
                    base_ref_name="main",
                    base_ref_oid="b" * 40,
                    merge_commit="c" * 40,
                )
        self.assertFalse(
            any(call["argv"][:2] == ["pr", "merge"] for call in self.calls_json())
        )


if __name__ == "__main__":
    unittest.main()
