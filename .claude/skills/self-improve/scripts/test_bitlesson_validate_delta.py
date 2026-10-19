import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().with_name("bitlesson_validate_delta.sh")


def lesson(
    *,
    contexts: str = "task://alpha, task://beta",
    evidence: str = "artifact://alpha, artifact://beta",
    removed: str = "yes",
    applicability: str = "matching parser runtimes",
    counterexample: str = "a matching control that still fails",
) -> str:
    return f"""# BitLesson

## Lesson: parser-boundary
Lesson ID: BL-20260812-parser-boundary
Scope: parser adapters
Task Contexts: {contexts}
Independence: distinct repositories and input families isolate the shared parser mechanism
Project Identifiers Removed: {removed}
Applicability: {applicability}
Problem Description: adapters accepted ambiguous state
Root Cause: the boundary omitted a required discriminator
Solution: require the discriminator before reuse
Constraints: only adapters with the same boundary
Counterexamples / Falsifier: {counterexample}
Validation Evidence: {evidence}
Source Rounds: alpha-3, beta-2
"""


class BitLessonDeltaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.summary = self.root / "summary.md"
        self.kb = self.root / "bitlesson.md"

    def tearDown(self):
        self.temp.cleanup()

    def validate_delta(
        self,
        *,
        action: str = "add",
        ids: str = "BL-20260812-parser-boundary",
        notes: str = "added cross-task parser guidance",
    ) -> subprocess.CompletedProcess[str]:
        self.summary.write_text(
            f"""# Round

## BitLesson Delta
- Action: {action}
- Lesson ID(s): {ids}
- Notes: {notes}
""",
            encoding="utf-8",
        )
        return subprocess.run(
            ["bash", str(SCRIPT), str(self.summary), str(self.kb)],
            capture_output=True,
            text=True,
        )

    def test_none_does_not_require_a_bitlesson_file(self):
        result = self.validate_delta(action="none", ids="NONE", notes="no transferable evidence")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_valid_cross_task_lesson_passes(self):
        self.kb.write_text(lesson(), encoding="utf-8")
        result = self.validate_delta()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_one_task_context_is_rejected(self):
        self.kb.write_text(lesson(contexts="task://alpha"), encoding="utf-8")
        result = self.validate_delta()
        self.assertEqual(2, result.returncode)
        self.assertIn("at least two distinct Task Contexts", result.stderr)

    def test_project_identifiers_must_be_removed(self):
        self.kb.write_text(lesson(removed="no"), encoding="utf-8")
        result = self.validate_delta()
        self.assertEqual(2, result.returncode)
        self.assertIn("Project Identifiers Removed: yes", result.stderr)

    def test_two_context_names_without_two_evidence_pointers_are_rejected(self):
        self.kb.write_text(lesson(evidence="artifact://alpha"), encoding="utf-8")
        result = self.validate_delta()
        self.assertEqual(2, result.returncode)
        self.assertIn("at least two distinct Validation Evidence pointers", result.stderr)

    def test_applicability_and_falsifier_must_be_concrete(self):
        self.kb.write_text(lesson(applicability="<todo>", counterexample="TBD"), encoding="utf-8")
        result = self.validate_delta()
        self.assertEqual(2, result.returncode)
        self.assertIn("Applicability", result.stderr)


if __name__ == "__main__":
    unittest.main()
