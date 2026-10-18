#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "publish_guard.py"
SPEC = importlib.util.spec_from_file_location("publish_guard", SCRIPT)
GUARD = importlib.util.module_from_spec(SPEC)
sys.modules["publish_guard"] = GUARD
SPEC.loader.exec_module(GUARD)


def run(*args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
    )


class PublishGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "work"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "fixture@example.invalid"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Fixture"], cwd=self.repo, check=True)
        (self.repo / "plan.md").write_text("old\n", encoding="utf-8")
        subprocess.run(["git", "add", "plan.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "initial"], cwd=self.repo, check=True)
        (self.repo / "plan.md").write_text("new\n", encoding="utf-8")
        self.receipt = Path(self.tmp.name) / "receipt.json"

    def tearDown(self):
        self.tmp.cleanup()

    def prepare(self, *extra):
        return run(
            "prepare",
            "--repo", self.repo,
            "--action", "commit-only",
            "--head", "work",
            "--message", "publish plan",
            "--file", "plan.md",
            "--receipt", self.receipt,
            *extra,
        )

    def test_requires_explicit_external_coordinates(self):
        result = run(
            "prepare", "--repo", self.repo, "--action", "push",
            "--message", "publish plan",
            "--file", "plan.md", "--receipt", self.receipt,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("--remote", result.stderr)
        self.assertFalse(self.receipt.exists())

    def test_rejects_preexisting_staged_state(self):
        (self.repo / "resources.md").write_text("foreign\n", encoding="utf-8")
        subprocess.run(["git", "add", "resources.md"], cwd=self.repo, check=True)
        result = self.prepare()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("index", result.stderr.lower())
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "-z"], cwd=self.repo,
            check=True, capture_output=True,
        ).stdout.split(b"\0")
        self.assertIn(b"resources.md", staged)

    def test_rejects_non_management_path_without_partial_stage(self):
        (self.repo / "app.py").write_text("print('x')\n", encoding="utf-8")
        result = run(
            "prepare", "--repo", self.repo, "--action", "commit-only", "--head", "work",
            "--message", "publish plan",
            "--file", "plan.md", "--file", "app.py", "--receipt", self.receipt,
        )
        self.assertNotEqual(0, result.returncode)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo,
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertEqual("", staged)

    def test_rejects_canonical_workflow_review_proof(self):
        proof = self.repo / ".pm" / "workflow-reviews" / "issue-1.md"
        proof.parent.mkdir(parents=True)
        proof.write_text("TERMINAL: COMPLETE\n", encoding="utf-8")
        result = run(
            "prepare", "--repo", self.repo, "--action", "commit-only", "--head", "work",
            "--message", "publish workflow proof",
            "--file", ".pm/workflow-reviews/issue-1.md", "--receipt", self.receipt,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("allowlist", result.stderr.lower())
        self.assertFalse(self.receipt.exists())

    def test_allows_non_authoritative_pm_evidence(self):
        evidence = self.repo / ".pm" / "evidence" / "check.md"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("pytest: passed\n", encoding="utf-8")
        result = run(
            "prepare", "--repo", self.repo, "--action", "commit-only", "--head", "work",
            "--message", "publish evidence",
            "--file", ".pm/evidence/check.md", "--receipt", self.receipt,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [".pm/evidence/check.md"],
            [entry["path"] for entry in json.loads(self.receipt.read_text(encoding="utf-8"))["staged"]],
        )

    def test_receipt_binds_exact_staged_bytes_and_head(self):
        result = self.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual("publish-management-artifacts/v1", receipt["schema"])
        self.assertEqual("commit-only", receipt["authorization"]["action"])
        self.assertEqual("work", receipt["authorization"]["head"])
        self.assertEqual(["plan.md"], [e["path"] for e in receipt["staged"]])
        self.assertRegex(receipt["staged_digest"], r"^sha256:[0-9a-f]{64}$")

        verified = run("verify-stage", "--repo", self.repo, "--receipt", self.receipt)
        self.assertEqual(0, verified.returncode, verified.stderr)

        (self.repo / "plan.md").write_text("changed after receipt\n", encoding="utf-8")
        subprocess.run(["git", "add", "plan.md"], cwd=self.repo, check=True)
        stale = run("verify-stage", "--repo", self.repo, "--receipt", self.receipt)
        self.assertNotEqual(0, stale.returncode)
        self.assertIn("digest", stale.stderr.lower())

    def test_commit_verification_binds_receipt_to_new_commit(self):
        prepared = self.prepare()
        self.assertEqual(0, prepared.returncode, prepared.stderr)
        subprocess.run(["git", "commit", "-qm", "publish plan"], cwd=self.repo, check=True)
        verified = run("verify-commit", "--repo", self.repo, "--receipt", self.receipt)
        self.assertEqual(0, verified.returncode, verified.stderr)
        out = json.loads(verified.stdout)
        self.assertRegex(out["commit"], r"^[0-9a-f]{40,64}$")
        self.assertEqual("work", out["head"])

    def test_rejects_git_subdirectory_as_repository_root(self):
        sub = self.repo / "nested"
        sub.mkdir()
        (sub / "plan.md").write_text("nested\n", encoding="utf-8")
        result = run(
            "prepare", "--repo", sub, "--action", "commit-only", "--head", "work",
            "--message", "publish plan", "--file", "plan.md", "--receipt", self.receipt,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("toplevel", result.stderr.lower())

    def test_verify_rejects_forged_receipt_for_source_file(self):
        source = self.repo / "app.py"
        source.write_text("print('unsafe')\n", encoding="utf-8")
        subprocess.run(["git", "add", "app.py"], cwd=self.repo, check=True)
        blob = subprocess.run(
            ["git", "show", ":app.py"], cwd=self.repo, check=True, capture_output=True,
        ).stdout
        manifest = [{
            "path": "app.py", "status": "A", "mode": "100644",
            "bytes_sha256": "sha256:" + hashlib.sha256(blob).hexdigest(),
        }]
        forged = {
            "schema": "publish-management-artifacts/v1",
            "repo": str(self.repo.resolve()),
            "pre_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                capture_output=True, text=True,
            ).stdout.strip(),
            "authorization": {
                "action": "commit-only", "head": "work", "remote": None,
                "remote_url": None, "base": None,
                "commit_message_sha256": "sha256:" + "0" * 64,
            },
            "staged": manifest,
            "staged_digest": "sha256:" + hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        self.receipt.write_text(json.dumps(forged), encoding="utf-8")
        result = run("verify-stage", "--repo", self.repo, "--receipt", self.receipt)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("allowlist", result.stderr.lower())

    def test_rejects_source_extensions_and_executable_management_files(self):
        source = self.repo / ".pm" / "hook.py"
        source.parent.mkdir()
        source.write_text("print('unsafe')\n", encoding="utf-8")
        result = run(
            "prepare", "--repo", self.repo, "--action", "commit-only", "--head", "work",
            "--message", "publish hook", "--file", ".pm/hook.py", "--receipt", self.receipt,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("source", result.stderr.lower())

        source.unlink()
        script = self.repo / ".pm" / "note.md"
        script.write_text("note\n", encoding="utf-8")
        script.chmod(0o755)
        result = run(
            "prepare", "--repo", self.repo, "--action", "commit-only", "--head", "work",
            "--message", "publish note", "--file", ".pm/note.md", "--receipt", self.receipt,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("executable", result.stderr.lower())

    def test_external_receipt_binds_remote_url_and_pr_body(self):
        remote = Path(self.tmp.name) / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=self.repo, check=True)
        body = Path(self.tmp.name) / "body.md"
        body.write_text("reviewed body\n", encoding="utf-8")
        result = run(
            "prepare", "--repo", self.repo, "--action", "open-pr", "--head", "work",
            "--remote", "origin", "--remote-url", str(remote), "--base", "main",
            "--message", "publish plan", "--pr-title", "Plan", "--pr-body-file", body,
            "--file", "plan.md", "--receipt", self.receipt,
        )
        self.assertEqual(0, result.returncode, result.stderr)

        subprocess.run(["git", "remote", "set-url", "origin", str(remote) + "-changed"], cwd=self.repo, check=True)
        stale_remote = run("verify-stage", "--repo", self.repo, "--receipt", self.receipt)
        self.assertNotEqual(0, stale_remote.returncode)
        self.assertIn("remote", stale_remote.stderr.lower())

        subprocess.run(["git", "remote", "set-url", "origin", str(remote)], cwd=self.repo, check=True)
        body.write_text("changed body\n", encoding="utf-8")
        stale_body = run("verify-stage", "--repo", self.repo, "--receipt", self.receipt)
        self.assertNotEqual(0, stale_body.returncode)
        self.assertIn("body", stale_body.stderr.lower())

    def test_verify_scans_real_staged_blob_and_receipt_strings_for_secrets(self):
        secret = "xox" + "b-" + "123456789012-abcdefghijklmnopqrstuvwxyz"
        note = self.repo / ".pm" / "note.md"
        note.parent.mkdir()
        note.write_text(secret + "\n", encoding="utf-8")
        subprocess.run(["git", "add", ".pm/note.md"], cwd=self.repo, check=True)
        blob = subprocess.run(
            ["git", "show", ":.pm/note.md"], cwd=self.repo, check=True, capture_output=True,
        ).stdout
        manifest = [{
            "path": ".pm/note.md", "status": "A", "mode": "100644",
            "bytes_sha256": "sha256:" + hashlib.sha256(blob).hexdigest(),
        }]
        forged = {
            "schema": "publish-management-artifacts/v1",
            "repo": str(self.repo.resolve()),
            "pre_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
                capture_output=True, text=True,
            ).stdout.strip(),
            "authorization": {
                "action": "commit-only", "head": "work", "remote": None,
                "remote_url": None, "base": None,
                "commit_message_sha256": "sha256:" + "0" * 64,
            },
            "staged": manifest,
            "staged_digest": "sha256:" + hashlib.sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        self.receipt.write_text(json.dumps(forged), encoding="utf-8")
        result = run("verify-stage", "--repo", self.repo, "--receipt", self.receipt)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("secret", result.stderr.lower())
        self.assertNotIn(secret, result.stderr)

    def test_receipt_path_must_be_fresh_outside_repository(self):
        inside = self.repo / ".pm" / "receipt.json"
        inside.parent.mkdir()
        result = run(
            "prepare", "--repo", self.repo, "--action", "commit-only", "--head", "work",
            "--message", "publish plan", "--file", "plan.md", "--receipt", inside,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(inside.exists())

    def _poisoned_caller_env(self):
        decoy = Path(self.tmp.name) / "decoy"
        decoy.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "decoy"], cwd=decoy, check=True)
        subprocess.run(["git", "config", "user.email", "decoy@example.invalid"], cwd=decoy, check=True)
        subprocess.run(["git", "config", "user.name", "Decoy"], cwd=decoy, check=True)
        (decoy / "plan.md").write_text("forged decoy\n", encoding="utf-8")
        subprocess.run(["git", "add", "plan.md"], cwd=decoy, check=True)
        subprocess.run(["git", "commit", "-qm", "decoy"], cwd=decoy, check=True)
        (decoy / "resources.md").write_text("decoy staged\n", encoding="utf-8")
        subprocess.run(["git", "add", "resources.md"], cwd=decoy, check=True)

        fake_bin = Path(self.tmp.name) / "fake-bin"
        fake_bin.mkdir()
        marker = fake_bin / "git-was-run"
        fake_git = fake_bin / "git"
        fake_git.write_text(
            "#!/bin/sh\n"
            f"printf ran >> '{marker}'\n"
            "exec /usr/bin/git \"$@\"\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)

        poison_home = Path(self.tmp.name) / "poison-home"
        poison_home.mkdir()
        (poison_home / ".gitconfig").write_text(
            "[alias]\n"
            "  add = \"!f() { printf hijacked >> '"
            + str(poison_home / "alias-ran")
            + "'; exit 1; }; f\"\n"
            "[core]\n"
            "  hooksPath = "
            + str(poison_home)
            + "\n"
            "  gitProxy = true\n",
            encoding="utf-8",
        )
        askpass = poison_home / "askpass"
        askpass.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        askpass.chmod(0o755)
        external_diff = poison_home / "external-diff"
        external_diff.write_text(
            "#!/bin/sh\nprintf ran >> '" + str(poison_home / "filter-ran") + "'\n",
            encoding="utf-8",
        )
        external_diff.chmod(0o755)

        env = os.environ.copy()
        env.update({
            "PATH": os.pathsep.join((str(fake_bin), env.get("PATH", ""))),
            "HOME": str(poison_home),
            "GIT_DIR": str(decoy / ".git"),
            "GIT_WORK_TREE": str(decoy),
            "GIT_INDEX_FILE": str(decoy / ".git" / "index"),
            "GIT_OBJECT_DIRECTORY": str(decoy / ".git" / "objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(decoy / ".git" / "objects"),
            "GIT_CONFIG_GLOBAL": str(poison_home / ".gitconfig"),
            "GIT_CONFIG_SYSTEM": str(poison_home / ".gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(poison_home),
            "GIT_ASKPASS": str(askpass),
            "GIT_EXTERNAL_DIFF": str(external_diff),
            "GIT_EXEC_PATH": str(fake_bin),
        })
        return env, marker, poison_home, decoy

    def test_prepare_ignores_caller_path_home_and_git_redirection(self):
        env, marker, poison_home, decoy = self._poisoned_caller_env()
        result = run(
            "prepare",
            "--repo", self.repo,
            "--action", "commit-only",
            "--head", "work",
            "--message", "publish plan",
            "--file", "plan.md",
            "--receipt", self.receipt,
            env=env,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists(), "caller PATH git interpreted the publication")
        self.assertFalse((poison_home / "alias-ran").exists())
        self.assertFalse((poison_home / "filter-ran").exists())
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(["plan.md"], [entry["path"] for entry in receipt["staged"]])
        self.assertEqual("work", receipt["authorization"]["head"])
        real_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(real_head, receipt["pre_commit"])
        decoy_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=decoy, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertNotEqual(decoy_head, receipt["pre_commit"])
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo,
            check=True, capture_output=True, text=True,
        ).stdout.split()
        self.assertEqual(["plan.md"], staged)

    def test_replacement_objects_cannot_rewrite_head_or_staged_manifest(self):
        other = self.repo / "other.md"
        other.write_text("replacement tree\n", encoding="utf-8")
        subprocess.run(["git", "add", "other.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "replacement"], cwd=self.repo, check=True)
        replacement = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=self.repo, check=True,
                       capture_output=True)
        real_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        subprocess.run(["git", "replace", real_head, replacement], cwd=self.repo, check=True)
        followed = subprocess.run(
            ["git", "log", "-1", "--format=%s", "HEAD"], cwd=self.repo,
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual("replacement", followed)
        (self.repo / "plan.md").write_text("new\n", encoding="utf-8")

        result = self.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(real_head, receipt["pre_commit"])
        self.assertEqual(["plan.md"], [entry["path"] for entry in receipt["staged"]])
        self.assertNotIn("other.md", [entry["path"] for entry in receipt["staged"]])

    def test_bound_git_environment_and_argv_are_isolated(self):
        captured = []
        real_run = GUARD.subprocess.run

        def wrapped(*args, **kwargs):
            argv = args[0] if args else kwargs.get("args")
            if isinstance(argv, (list, tuple)) and argv and Path(str(argv[0])).name == "git":
                captured.append({"argv": [str(part) for part in argv], "env": kwargs.get("env")})
            return real_run(*args, **kwargs)

        injected = {
            "PATH": "/tmp/not-git",
            "HOME": str(self.repo / "forged-home"),
            "GIT_DIR": str(self.repo / "absent.git"),
            "GIT_WORK_TREE": str(self.repo.parent),
            "GIT_INDEX_FILE": str(self.repo / "absent.index"),
            "GIT_OBJECT_DIRECTORY": str(self.repo / "absent-objects"),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.repo.parent),
            "GIT_CONFIG_GLOBAL": str(self.repo / "forged.gitconfig"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.hooksPath",
            "GIT_CONFIG_VALUE_0": str(self.repo),
            "GIT_EXTERNAL_DIFF": str(self.repo / "diff"),
            "GIT_EXEC_PATH": str(self.repo),
        }
        with mock.patch.dict(os.environ, injected, clear=False):
            environment = GUARD._git_environment()
            with mock.patch.object(GUARD.subprocess, "run", side_effect=wrapped):
                GUARD.git(self.repo, "rev-parse", "--show-toplevel", text=True)
        self.assertTrue(captured)
        bound, digest = GUARD._bind_trusted_git()
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        self.assertIn(bound.parent, GUARD._SYSTEM_GIT_DIRS)
        self.assertNotEqual(injected["PATH"], environment["PATH"])
        self.assertNotEqual(injected["HOME"], environment["HOME"])
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_KEY_0",
            "GIT_CONFIG_VALUE_0",
            "GIT_EXTERNAL_DIFF",
            "GIT_EXEC_PATH",
        ):
            self.assertNotIn(name, environment)
        self.assertEqual("1", environment["GIT_CONFIG_NOSYSTEM"])
        self.assertEqual(os.devnull, environment["GIT_CONFIG_GLOBAL"])
        self.assertNotEqual(injected["GIT_CONFIG_GLOBAL"], environment["GIT_CONFIG_GLOBAL"])
        self.assertEqual(os.devnull, environment["GIT_CONFIG_SYSTEM"])
        self.assertEqual("1", environment["GIT_NO_REPLACE_OBJECTS"])
        self.assertEqual("1", environment["GIT_LITERAL_PATHSPECS"])
        for call in captured:
            argv = call["argv"]
            env = call["env"]
            self.assertEqual(Path(argv[0]), bound)
            self.assertIn("--no-replace-objects", argv)
            self.assertIn("--literal-pathspecs", argv)
            self.assertIn(f"--git-dir={self.repo / '.git'}", argv)
            self.assertIn(f"--work-tree={self.repo.resolve()}", argv)
            self.assertIsInstance(env, dict)
            self.assertEqual(environment, env)

    def test_bound_git_fails_closed_on_executable_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory)
            stub = install / "git"
            original = b"#!/usr/bin/env bash\nexit 0\n"
            stub.write_bytes(original)
            stub.chmod(0o555)
            real_run = GUARD.subprocess.run

            def wrapped(*args, **kwargs):
                completed = real_run(*args, **kwargs)
                stub.chmod(0o755)
                stub.write_bytes(b"#!/usr/bin/env bash\nprintf swapped\nexit 0\n")
                stub.chmod(0o555)
                return completed

            previous = GUARD._TRUSTED_GIT
            try:
                GUARD._TRUSTED_GIT = None
                with mock.patch.object(GUARD, "_SYSTEM_GIT_DIRS", (install,)):
                    bound, digest = GUARD._bind_trusted_git()
                    self.assertEqual(stub.resolve(), bound)
                    self.assertEqual(
                        digest, "sha256:" + hashlib.sha256(original).hexdigest()
                    )
                    with mock.patch.object(GUARD.subprocess, "run", side_effect=wrapped):
                        with self.assertRaisesRegex(GUARD.GuardError, "trusted git executable drift"):
                            GUARD.git(self.repo, "rev-parse", "--show-toplevel", text=True)
            finally:
                GUARD._TRUSTED_GIT = previous

    def test_allowlist_still_refuses_source_under_poisoned_git_env(self):
        env, marker, _, _ = self._poisoned_caller_env()
        (self.repo / "app.py").write_text("print('x')\n", encoding="utf-8")
        result = run(
            "prepare", "--repo", self.repo, "--action", "commit-only", "--head", "work",
            "--message", "publish plan",
            "--file", "plan.md", "--file", "app.py", "--receipt", self.receipt,
            env=env,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("allowlist", result.stderr.lower())
        self.assertFalse(marker.exists())
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo,
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertEqual("", staged)

    def test_prepare_does_not_run_repo_clean_filter_or_rewrite_bytes(self):
        marker = Path(self.tmp.name) / "clean-filter-ran"
        cleaner = Path(self.tmp.name) / "poison-clean"
        cleaner.write_text(
            "#!/bin/sh\n"
            f"printf ran >> '{marker}'\n"
            "printf '%s\\n' FILTERED\n",
            encoding="utf-8",
        )
        cleaner.chmod(0o755)
        (self.repo / ".gitattributes").write_text("plan.md filter=poison\n", encoding="utf-8")
        subprocess.run(
            ["git", "config", "filter.poison.clean", str(cleaner)],
            cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "config", "filter.poison.smudge", "cat"],
            cwd=self.repo, check=True,
        )
        worktree = (self.repo / "plan.md").read_bytes()
        self.assertEqual(b"new\n", worktree)

        subprocess.run(["git", "add", "--", "plan.md"], cwd=self.repo, check=True)
        self.assertTrue(marker.exists(), "fixture clean filter did not run under git add")
        rewritten = subprocess.run(
            ["git", "show", ":plan.md"], cwd=self.repo, check=True, capture_output=True,
        ).stdout
        self.assertEqual(b"FILTERED\n", rewritten)
        self.assertNotEqual(worktree, rewritten)
        subprocess.run(["git", "reset", "-q", "HEAD", "--", "plan.md"], cwd=self.repo, check=True)
        marker.unlink()
        self.assertFalse(marker.exists())

        result = self.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists(), "authorized prepare executed a repo clean filter")
        staged = subprocess.run(
            ["git", "show", ":plan.md"], cwd=self.repo, check=True, capture_output=True,
        ).stdout
        self.assertEqual(worktree, staged)
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(
            "sha256:" + hashlib.sha256(worktree).hexdigest(),
            receipt["staged"][0]["bytes_sha256"],
        )

    def test_prepare_rejects_symlinked_index(self):
        index = self.repo / ".git" / "index"
        real = self.repo / ".git" / "index.real"
        os.rename(index, real)
        index.symlink_to(real)
        result = self.prepare()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("index", result.stderr.lower())
        self.assertFalse(self.receipt.exists())
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo,
            capture_output=True, text=True,
        ).stdout
        self.assertEqual("", staged)

    def test_prepare_rejects_hardlinked_index(self):
        index = self.repo / ".git" / "index"
        other = Path(self.tmp.name) / "index.hardlink"
        os.link(index, other)
        result = self.prepare()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("index", result.stderr.lower())
        self.assertFalse(self.receipt.exists())
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=self.repo,
            check=True, capture_output=True, text=True,
        ).stdout
        self.assertEqual("", staged)

    def test_prepare_rejects_split_or_shared_index(self):
        subprocess.run(["git", "update-index", "--split-index"], cwd=self.repo, check=True)
        shared = list((self.repo / ".git").glob("sharedindex.*"))
        self.assertTrue(shared)
        result = self.prepare()
        self.assertNotEqual(0, result.returncode)
        self.assertRegex(result.stderr.lower(), r"index|shared")
        self.assertFalse(self.receipt.exists())

    def test_verify_stage_rejects_symlinked_index(self):
        prepared = self.prepare()
        self.assertEqual(0, prepared.returncode, prepared.stderr)
        index = self.repo / ".git" / "index"
        real = self.repo / ".git" / "index.real"
        os.rename(index, real)
        index.symlink_to(real)
        verified = run("verify-stage", "--repo", self.repo, "--receipt", self.receipt)
        self.assertNotEqual(0, verified.returncode)
        self.assertIn("index", verified.stderr.lower())

    def test_prepare_stages_tracked_deletion(self):
        (self.repo / "plan.md").unlink()
        result = self.prepare()
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(
            [{"path": "plan.md", "status": "D"}],
            receipt["staged"],
        )


if __name__ == "__main__":
    unittest.main()
