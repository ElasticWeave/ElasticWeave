#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import re
import unittest

import report_skill_descriptions as report


ROOT = Path(__file__).resolve().parents[4]
SKILLS = ROOT / ".claude/skills"


class SkillDescriptionTests(unittest.TestCase):
    def test_all_discoverable_descriptions_fit_the_context_budget(self) -> None:
        over = []
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            length = len(report.description_text(path))
            # Mode descriptions are also kept below the ordinary limit; the
            # manifest carries mode-specific detail outside frontmatter.
            if length > 220:
                over.append(f"{path.parent.name}={length}")
        self.assertEqual([], over)

    def test_frontmatter_still_has_a_single_line_description(self) -> None:
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            text = path.read_text(encoding="utf-8")
            frontmatter = text.split("---", 2)[1]
            self.assertEqual(1, len(re.findall(r"^description:", frontmatter, re.MULTILINE)), path)


if __name__ == "__main__":
    unittest.main()
