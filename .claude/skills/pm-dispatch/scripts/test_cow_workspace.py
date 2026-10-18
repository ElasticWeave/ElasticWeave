from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import cow_workspace


class CowWorkspaceTests(unittest.TestCase):
    def test_filters_top_context_and_records_changes(self) -> None:
        with tempfile.TemporaryDirectory() as source_name, tempfile.TemporaryDirectory() as parent_name:
            source = Path(source_name)
            (source / ".claude/skills/subagent-workflow").mkdir(parents=True)
            (source / ".claude/skills/update-platform-config").mkdir(parents=True)
            (source / ".claude/skills/first-principles").mkdir(parents=True)
            (source / ".claude/skills/unrelated").mkdir(parents=True)
            (source / ".claude/skills/subagent-workflow/SKILL.md").write_text("runtime\n")
            (source / ".claude/skills/update-platform-config/SKILL.md").write_text("runtime\n")
            (source / ".claude/skills/first-principles/SKILL.md").write_text("domain\n")
            (source / ".claude/skills/unrelated/SKILL.md").write_text("remove\n")
            (source / ".claude/skills/subagent-workflow/marker").write_text("keep\n")
            (source / "AGENTS.md").write_text("supervisor\n")
            (source / "CLAUDE.md").write_text("supervisor\n")
            (source / "project.txt").write_text("before\n")
            (source / ".subagent/flows").mkdir(parents=True)
            (source / ".workflow/designs").mkdir(parents=True)
            mapping = {"designPath": ".workflow/designs/demo.workflow.json"}
            design = {"nodes": [{"skills": {"required": ["first-principles"], "onDemand": []}}]}
            (source / ".subagent/flows/demo.json").write_text(json.dumps(mapping))
            (source / ".workflow/designs/demo.workflow.json").write_text(json.dumps(design))
            workspace, manifest = cow_workspace.create(source, ".subagent/flows/demo.json", Path(parent_name), "demo")
            self.assertFalse((workspace / "AGENTS.md").exists())
            self.assertFalse((workspace / "CLAUDE.md").exists())
            self.assertFalse((workspace / ".claude/skills/unrelated").exists())
            self.assertTrue((workspace / ".claude/skills/first-principles/SKILL.md").exists())
            # The source may move on while a worker runs; the diff still uses
            # the bytes captured at copy time.
            (source / "project.txt").write_text("source changed concurrently\n")
            (workspace / "project.txt").write_text("after\n")
            result = cow_workspace.finalize(workspace, manifest)
            self.assertIn("project.txt", result["changed_paths"])
            self.assertIn("-before", result["diffs"]["project.txt"])
            self.assertNotIn("source changed concurrently", result["diffs"]["project.txt"])

    def test_remove_rejects_symlink_and_wrong_parent(self) -> None:
        with tempfile.TemporaryDirectory() as parent_name:
            parent = Path(parent_name)
            wrong = parent / "elasticweave-wrong"
            wrong.mkdir()
            with self.assertRaises(ValueError):
                cow_workspace.remove(wrong)
            cow = parent / "cow"
            cow.mkdir()
            target = cow / "elasticweave-target"
            target.mkdir()
            link = cow / "elasticweave-link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ValueError):
                cow_workspace.remove(link)
            cow_workspace.remove(target)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
