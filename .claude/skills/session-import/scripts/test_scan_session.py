#!/usr/bin/env python3
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("scan_session.py")


class ScanSessionTests(unittest.TestCase):
    @staticmethod
    def write_codex(path: Path, repo: Path, messages: list[str]) -> None:
        path.write_text(
            "\n".join(
                [json.dumps({"type": "session_meta", "payload": {"cwd": str(repo)}})]
                + [json.dumps({"message": {"content": message}}) for message in messages]
            ) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def write_claude(path: Path, messages: list[str]) -> None:
        path.write_text(
            "\n".join(
                json.dumps({"type": "user", "message": {"content": message}})
                for message in messages
            ) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def claude_project_dir(home: Path, repo: Path) -> Path:
        return home / ".claude" / "projects" / str(repo).replace("/", "-")

    def test_rejects_output_outside_repo_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            source = root / "session.jsonl"
            self.write_codex(source, repo, ["The goal is safe."])
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source),
                 "--out", str(root / "elsewhere")],
                capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("migration", result.stderr.lower())

    def test_repo_boundary_does_not_accept_prefix_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            sibling = root / "repo-other"
            repo.mkdir(); sibling.mkdir()
            source = root / "session.jsonl"
            self.write_codex(source, sibling, ["The goal is poison."])
            out = repo / "migration"
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source),
                 "--out", str(out)], capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("repo", result.stderr.lower())

    def test_explicit_claude_directory_requires_exact_repo_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            repo = root / "repo"
            other = root / "other"
            repo.mkdir(); other.mkdir()
            wrong_dir = self.claude_project_dir(home, other)
            wrong_dir.mkdir(parents=True)
            self.write_claude(wrong_dir / "session.jsonl", ["The goal is poison."])
            env = os.environ.copy()
            env["HOME"] = str(home)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--claude-dir", str(wrong_dir)],
                capture_output=True, text=True, env=env,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("repo", result.stderr.lower())
            self.assertFalse((repo / "migration" / "signals.json").exists())

    def test_explicit_claude_directory_for_exact_repo_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            repo = root / "repo"
            repo.mkdir()
            claude_dir = self.claude_project_dir(home, repo)
            claude_dir.mkdir(parents=True)
            self.write_claude(claude_dir / "session.jsonl", ["The goal is safe."])
            env = os.environ.copy()
            env["HOME"] = str(home)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--claude-dir", str(claude_dir)],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            signals = json.loads((repo / "migration" / "signals.json").read_text())
            self.assertEqual(1, len(signals["goal"]))

    def test_explicit_claude_rejects_symlinked_source_parents_and_leaves(self):
        for link_kind in ("parent", "leaf"):
            with self.subTest(link_kind=link_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                home = root / "home"
                repo = root / "repo"
                repo.mkdir()
                claude_dir = self.claude_project_dir(home, repo)
                if link_kind == "parent":
                    real_dir = root / "real-claude-dir"
                    real_dir.mkdir()
                    self.write_claude(real_dir / "session.jsonl", ["The goal is poison."])
                    claude_dir.parent.mkdir(parents=True)
                    claude_dir.symlink_to(real_dir, target_is_directory=True)
                else:
                    claude_dir.mkdir(parents=True)
                    external = root / "external.jsonl"
                    self.write_claude(external, ["The goal is poison."])
                    (claude_dir / "session.jsonl").symlink_to(external)
                env = os.environ.copy()
                env["HOME"] = str(home)
                result = subprocess.run(
                    ["python3", str(SCRIPT), "--repo", str(repo),
                     "--claude-dir", str(claude_dir)],
                    capture_output=True, text=True, env=env,
                )
                self.assertNotEqual(0, result.returncode)
                self.assertIn("symlink", result.stderr.lower())
                self.assertFalse((repo / "migration" / "signals.json").exists())

    def test_explicit_codex_requires_session_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            source = root / "session.jsonl"
            source.write_text(
                "\n".join([
                    json.dumps({"type": "session_meta", "payload": {"id": "missing-cwd"}}),
                    json.dumps({"message": {"content": "The goal is poison."}}),
                ]) + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("cwd", result.stderr.lower())
            self.assertFalse((repo / "migration" / "signals.json").exists())

    def test_redacts_pem_body_jwt_npm_and_slack_with_exact_line_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            tokens = [
                "xox" + "b-" + "123456789012-abcdefghijklmnop",
                "npm_abcdefghijklmnopqrstuvwxyz123456",
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signatureABC",
                "-----BEGIN PRIVATE KEY-----\\nABCDEFSECRETBODY\\n-----END PRIVATE KEY-----",
            ]
            source = root / "session.jsonl"
            self.write_codex(source, repo, [f"The goal is safe and token {token}." for token in tokens])
            out = repo / "migration"
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source),
                 "--out", str(out)], capture_output=True, text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            combined = (out / "signals.json").read_text() + (out / "report.md").read_text()
            for token in tokens:
                self.assertNotIn(token.replace("\\n", "\n"), combined)
            self.assertNotIn("ABCDEFSECRETBODY", combined)
            signals = json.loads((out / "signals.json").read_text())
            self.assertRegex(signals["goal"][0]["src"], r":line-[2-5]$")

    def test_cursor_workspace_identity_is_required_and_global_db_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            ws = root / "workspaceStorage" / "hash"
            ws.mkdir(parents=True)
            (ws / "workspace.json").write_text(json.dumps({"folder": (root / "other").as_uri()}))
            db = ws / "state.vscdb"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
            con.execute("INSERT INTO ItemTable VALUES (?, ?)", ("aiService.prompts", json.dumps({"x": "The goal is poison."})))
            con.commit(); con.close()
            global_dir = root / "globalStorage"
            global_dir.mkdir()
            (global_dir / "state.vscdb").write_bytes(db.read_bytes())
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--cursor-workspace", str(ws),
                 "--out", str(repo / "migration")], capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("workspace", result.stderr.lower())

    def test_output_dotdot_and_symlink_leaves_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            source = root / "session.jsonl"
            self.write_codex(source, repo, ["The goal is safe."])
            escape = repo / "escape"
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source),
                 "--out", str(repo / "migration" / ".." / "escape")],
                capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(escape.exists())

            migration = repo / "migration"
            migration.mkdir()
            outside = root / "outside.json"
            (migration / "signals.json").symlink_to(outside)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(outside.exists())

    def test_hardlinked_output_leaf_is_rejected_without_mutating_external_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            source = root / "session.jsonl"
            self.write_codex(source, repo, ["The goal is safe."])
            migration = repo / "migration"
            migration.mkdir()
            outside = root / "outside.json"
            outside.write_text("sentinel\n", encoding="utf-8")
            os.link(outside, migration / "signals.json")
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source)],
                capture_output=True, text=True,
            )
            self.assertEqual("sentinel\n", outside.read_text(encoding="utf-8"))
            self.assertNotEqual(0, result.returncode)
            self.assertIn("single-link", result.stderr.lower())
            self.assertFalse((migration / "report.md").exists())

    def test_single_link_outputs_are_replaced_atomically_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            source = root / "session.jsonl"
            self.write_codex(source, repo, ["The goal is the first version."])
            command = [
                "python3", str(SCRIPT), "--repo", str(repo),
                "--codex-file", str(source),
            ]
            first = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, first.returncode, first.stderr)
            signals_path = repo / "migration" / "signals.json"
            first_inode = signals_path.stat().st_ino

            self.write_codex(source, repo, ["The goal is the second version."])
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertNotEqual(first_inode, signals_path.stat().st_ino)
            self.assertEqual(1, signals_path.stat().st_nlink)
            signals = json.loads(signals_path.read_text(encoding="utf-8"))
            self.assertEqual("The goal is the second version.", signals["goal"][0]["text"])

    def test_cursor_rejects_symlinked_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            ws = root / "workspaceStorage" / "hash"
            ws.mkdir(parents=True)
            (ws / "workspace.json").write_text(json.dumps({"folder": repo.as_uri()}))
            db = root / "unrelated.vscdb"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
            con.commit(); con.close()
            (ws / "state.vscdb").symlink_to(db)
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--cursor-workspace", str(ws)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("symlink", result.stderr.lower())

    def test_reports_acceptance_evaluation_and_off_limits_without_gate_buckets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            source = root / "session.jsonl"
            self.write_codex(source, repo, [
                "The goal is to make exports deterministic.",
                "Done when python3 -m pytest tests/export passes.",
                "Use the held-out fixture fixtures/golden.json; baseline accuracy 0.91 and threshold 0.95.",
                "Do not modify fixtures/golden.json because it is the reference output.",
            ])
            out = repo / "migration"
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source), "--out", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            signals = json.loads((out / "signals.json").read_text(encoding="utf-8"))
            self.assertTrue(signals["acceptance_criteria"])
            self.assertTrue(signals["graded_eval"])
            self.assertTrue(signals["evaluation_fixtures"])
            self.assertTrue(signals["off_limits"])
            self.assertNotIn("acceptance_verify", signals)
            self.assertNotIn("immutables", signals)

            report = (out / "report.md").read_text(encoding="utf-8")
            for heading in (
                "## acceptance_criteria",
                "## graded_eval",
                "## evaluation_fixtures",
                "## off_limits",
            ):
                self.assertIn(heading, report)
            self.assertGreaterEqual(report.count("⚠ CONFIRM"), 4)

    def test_unicode_escaped_chinese_setup_cues_remain_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            source = root / "session.jsonl"
            self.write_codex(
                source,
                repo,
                [
                    (
                        "\u4e0d\u8981\u4fee\u6539 fixtures/golden.json"
                        "\uff0c\u56e0\u4e3a\u5b83\u662f\u56fa\u5b9a\u7684\u8bc4\u4f30\u8f93\u51fa\u3002"
                    ),
                    (
                        "\u8fd9\u4e2a\u65b9\u6848\u4e0d\u884c"
                        "\uff0c\u56e0\u4e3a\u5b83\u4f1a\u7834\u574f\u57fa\u7ebf\u3002"
                    ),
                ],
            )
            out = repo / "migration"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--repo",
                    str(repo),
                    "--codex-file",
                    str(source),
                    "--out",
                    str(out),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            signals = json.loads((out / "signals.json").read_text(encoding="utf-8"))
            self.assertTrue(signals["off_limits"])
            self.assertTrue(signals["constraints"])
            self.assertTrue(signals["rejected_paths"])

    def test_never_emits_canonical_or_executable_gate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            source = root / "session.jsonl"
            self.write_codex(source, repo, ["Acceptance is pytest; never edit tests/fixtures.json."])
            out = repo / "migration"
            result = subprocess.run(
                ["python3", str(SCRIPT), "--repo", str(repo), "--codex-file", str(source), "--out", str(out)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({"signals.json", "report.md"}, {path.name for path in out.iterdir()})
            for legacy in ("accept.suggested.sh", "immutable.suggested", "accept.sh", "verify_base"):
                self.assertFalse((out / legacy).exists())
            self.assertFalse((repo / ".pm").exists())


if __name__ == "__main__":
    unittest.main()
