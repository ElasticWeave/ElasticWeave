import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().with_name("validate.py")
ASSET_MEMORY = SCRIPT.parent.parent / "assets" / "project" / ".memory"
SKILL_MD = SCRIPT.parent.parent / "SKILL.md"
# Project-local test data only; do not source validator fixtures from reusable references.
def memory_abstraction() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    marker = "> **Memory abstraction:** "
    line = next(line for line in text.splitlines() if line.startswith(marker))
    return f"abstraction: {line.removeprefix(marker)}\n"


class MemoryValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ASSET_MEMORY, self.root / ".memory")
        skill_dir = self.root / ".claude" / "skills" / "memory"
        skill_dir.mkdir(parents=True)
        shutil.copy2(SKILL_MD, skill_dir / "SKILL.md")
        (self.root / ".memory" / "skills" / "memory.md").write_text(
            memory_abstraction(),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def validate(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT)],
            cwd=self.root,
            capture_output=True,
            text=True,
        )

    def test_seeded_project_memory_is_clean(self):
        result = self.validate()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_cross_task_carrier_cannot_be_staged_in_project_memory(self):
        (self.root / ".memory" / "main" / "promoted-reference.md").write_text(
            """---
kind: cross-task-reference
source: self-evolve
---

# Reusable guidance
""",
            encoding="utf-8",
        )
        result = self.validate()
        self.assertEqual(2, result.returncode)
        self.assertIn(".memory is project-level", result.stdout)

    def test_quoted_cross_task_kind_is_also_rejected(self):
        (self.root / ".memory" / "main" / "quoted-reference.md").write_text(
            """---
kind: "cross-task-reference"
---

# Reusable guidance
""",
            encoding="utf-8",
        )
        result = self.validate()
        self.assertEqual(2, result.returncode)
        self.assertIn(".memory is project-level", result.stdout)

    def test_self_evolve_project_observation_remains_allowed(self):
        (self.root / ".memory" / "main" / "round-observation.md").write_text(
            """---
kind: project-observation
source: self-evolve
---

# Local observation

This remains scoped to the current project.
""",
            encoding="utf-8",
        )
        result = self.validate()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_only_exact_private_subtree_is_exempt_from_secret_scan(self):
        private = self.root / ".memory" / "private"
        private.mkdir(exist_ok=True)
        (private / "allowed.md").write_text("github_pat_private_ok\n", encoding="utf-8")
        notes = self.root / ".memory" / "private-notes"
        notes.mkdir()
        (notes / "leak.md").write_text("github_pat_sibling_leak\n", encoding="utf-8")
        extra = self.root / ".memory" / "privatefoo"
        extra.mkdir()
        (extra / "leak.md").write_text("sk-ant-sibling-leak\n", encoding="utf-8")
        result = self.validate()
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("secret-like pattern", result.stdout)
        self.assertIn(".memory/private-notes/leak.md", result.stdout)
        self.assertIn(".memory/privatefoo/leak.md", result.stdout)
        self.assertNotIn(".memory/private/allowed.md", result.stdout)

    def test_only_exact_private_subtree_is_exempt_from_cross_task_scope(self):
        carrier = """---
kind: cross-task-reference
source: self-evolve
---

# reusable guidance
"""
        nested = self.root / ".memory" / "private" / "nested"
        nested.mkdir(parents=True)
        (nested / "allowed.md").write_text(carrier, encoding="utf-8")
        notes = self.root / ".memory" / "private-notes"
        notes.mkdir()
        (notes / "carrier.md").write_text(carrier, encoding="utf-8")
        result = self.validate()
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn(".memory is project-level", result.stdout)
        self.assertIn(".memory/private-notes/carrier.md", result.stdout)
        self.assertNotIn(".memory/private/nested/allowed.md", result.stdout)

    def test_top_session_contract_delegates_all_memory_mutations(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("The top/main session never edits `.memory/`", text)
        self.assertIn("Children return `learningCandidates`", text)
        self.assertIn("never write memory after a terminal verdict", text)
        self.assertIn("Delegate the authorized mutation to one bounded triage worker", text)
        self.assertIn("return the focused diff", text)
        self.assertIn("never perform the file mutation or validator repair in the top session", text)
        self.assertNotIn("Subagents NEVER write MAIN.md directly", text)
        self.assertNotIn("workflows YOU do it", text)


if __name__ == "__main__":
    unittest.main()
