#!/usr/bin/env python3
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import find


class MarketplaceFindTests(unittest.TestCase):
    def test_project_resources_are_scanned_before_external_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / ".claude" / "skills" / "widget-research"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: widget-research\ndescription: Research reusable widgets.\n---\n",
                encoding="utf-8",
            )
            agent = root / ".omp" / "agents" / "widget-reviewer.md"
            agent.parent.mkdir(parents=True)
            agent.write_text("# Widget reviewer\n", encoding="utf-8")
            design = root / ".workflow" / "designs" / "widget.workflow.json"
            design.parent.mkdir(parents=True)
            design.write_text(json.dumps({"name": "widget fanout"}), encoding="utf-8")

            found = find.scan_project(root, find.query_regex("widget"))
            self.assertEqual(
                ["skill", "agent", "workflow-design"],
                [item["kind"] for item in found],
            )
            self.assertTrue(all(Path(item["path"]).is_relative_to(root) for item in found))

    def test_github_states_are_distinct(self):
        with mock.patch.object(find.shutil, "which", return_value=None):
            self.assertEqual("unavailable", find.github_status()["state"])

        unauth = find.CommandResult(1, "", "not logged into any GitHub hosts")
        with mock.patch.object(find.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(find, "run", side_effect=[find.CommandResult(0, "gh version", ""), unauth]):
            self.assertEqual("unauthenticated", find.github_status()["state"])

        failed = find.CommandResult(7, "", "transport failed")
        with mock.patch.object(find.shutil, "which", return_value="/usr/bin/gh"), \
             mock.patch.object(find, "run", side_effect=[find.CommandResult(0, "gh version", ""), failed]):
            status = find.github_status()
            self.assertEqual("command-failed", status["state"])
            self.assertEqual(7, status["returncode"])

    def test_search_failure_is_reported_not_converted_to_empty_results(self):
        status = {"state": "authenticated"}
        with mock.patch.object(find, "github_status", return_value=status), \
             mock.patch.object(find, "run", return_value=find.CommandResult(9, "", "rate limited")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = find.main(["--root", "/tmp", "widget"])
        self.assertEqual(3, rc)
        self.assertIn("search-failed", output.getvalue())
        self.assertIn("rc=9", output.getvalue())

    def test_query_is_one_positional_after_option_terminator(self):
        calls = []
        with mock.patch.object(find, "github_status", return_value={"state": "authenticated"}), \
             mock.patch.object(find, "run", side_effect=lambda argv: (
                 calls.append(argv) or find.CommandResult(0, "[]", "")
             )):
            find._github_search("--limit 999 widget")
        search = calls[0]
        self.assertIn("--", search)
        marker = search.index("--")
        self.assertEqual(1, len(search[marker + 1:]))
        self.assertEqual("--limit 999 widget claude code", search[marker + 1])


if __name__ == "__main__":
    unittest.main()
