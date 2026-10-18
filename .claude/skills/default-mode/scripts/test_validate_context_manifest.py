#!/usr/bin/env python3

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import validate_context_manifest as validator


ROOT = Path(__file__).resolve().parents[4]
MANIFEST = ROOT / ".claude/skills/default-mode/references/context/manifest.json"
SKILLS = ROOT / ".claude/skills"


class ContextManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_shipped_manifest_is_valid(self) -> None:
        validator.validate_manifest(self.document, SKILLS)

    def test_every_discovered_skill_is_accounted_for_once(self) -> None:
        discovered = {path.parent.name for path in SKILLS.glob("*/SKILL.md")}
        seen = list(self.document["shared"]["skills"])
        for mode in self.document["modes"].values():
            seen.extend(mode["bootstrap"])
            seen.extend(mode["core"])
        for pack in self.document["packs"].values():
            seen.extend(pack["skills"])
        self.assertEqual(discovered, set(seen))
        self.assertEqual(len(seen), len(set(seen)))

    def test_startup_keeps_all_mode_core_lists_empty(self) -> None:
        self.assertEqual(
            {"default": [], "fast": [], "naive": []},
            {mode: value["core"] for mode, value in self.document["modes"].items()},
        )

    def test_mode_and_pack_context_policies_are_explicit(self) -> None:
        self.assertEqual("declared-per-node", self.document["modes"]["default"]["nodeSkills"])
        self.assertEqual("none", self.document["modes"]["naive"]["nodeSkills"])
        self.assertEqual("supervisor", self.document["packs"]["delivery"]["audience"])

    def test_naive_mode_cannot_reference_a_project_skill(self) -> None:
        document = copy.deepcopy(self.document)
        document["modes"]["naive"]["core"] = ["memory"]
        with self.assertRaisesRegex(validator.ContextManifestError, "naive mode"):
            validator.validate_manifest(document, SKILLS)

    def test_unknown_skill_and_pack_names_fail(self) -> None:
        document = copy.deepcopy(self.document)
        document["packs"]["delivery"]["skills"].append("missing-skill")
        with self.assertRaisesRegex(validator.ContextManifestError, "unknown skill"):
            validator.validate_manifest(document, SKILLS)

    def test_pack_dependencies_are_acyclic(self) -> None:
        document = copy.deepcopy(self.document)
        document["packs"]["coordination"]["dependsOn"] = ["delivery"]
        document["packs"]["delivery"]["dependsOn"] = ["coordination"]
        with self.assertRaisesRegex(validator.ContextManifestError, "cycle"):
            validator.validate_manifest(document, SKILLS)

    def test_duplicate_accounting_fails(self) -> None:
        document = copy.deepcopy(self.document)
        document["modes"]["default"]["core"] = ["pm-dispatch"]
        document["modes"]["fast"]["core"] = ["pm-dispatch"]
        with self.assertRaisesRegex(validator.ContextManifestError, "exactly once"):
            validator.validate_manifest(document, SKILLS)


if __name__ == "__main__":
    unittest.main()
