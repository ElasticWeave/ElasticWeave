from __future__ import annotations

from pathlib import Path
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent
SKILL = SKILL_ROOT / "SKILL.md"
FALSIFICATION_GATE = SKILL_ROOT / "references" / "falsification-gate.md"
REFERENCE_DESIGN = SKILL_ROOT / "references" / "reference-design.md"
EXPERIMENTAL_VALIDATION = SKILL_ROOT / "references" / "experimental-validation.md"
FANOUT_DESIGN = (
    SKILLS_ROOT
    / "build-workflow"
    / "references"
    / "designs"
    / "fanout-fanin-research.md"
)


class FalsificationGateContractTest(unittest.TestCase):
    def test_iteration_modes_keep_mandatory_gates_and_external_checkpoint(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        gate = FALSIFICATION_GATE.read_text(encoding="utf-8")
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        experiment = EXPERIMENTAL_VALIDATION.read_text(encoding="utf-8")
        combined = "\n".join((skill, gate, design, experiment))
        for value in (
            "`autonomous`",
            "`human_guided`",
            "idea_review_disposition",
            "skipped_human_guided",
            "triggered_by_risk",
            "minimum validity gate",
            "human-observation checkpoint",
            "graph-external",
            "autonomous-review fallback",
        ):
            with self.subTest(value=value):
                self.assertIn(value, combined)
        self.assertIn("next round until feedback", skill)
        self.assertIn("terminal review", skill)
        self.assertIn("replace each loop-back edge", experiment)

    def test_skill_attacks_uncertainty_before_selecting_broad_work(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        attack = text.index("## 2. Attack the most uncertain load-bearing assumptions")
        width = text.index("## 3. Select adaptive width")
        gate = text.index("## 4. Run the falsification gate")
        evidence = text.index("## 5. Build the evidence ledger")
        self.assertLess(attack, width)
        self.assertLess(width, gate)
        self.assertLess(gate, evidence)
        self.assertIn("coreProblemContract", text)
        self.assertIn("UNRESOLVED_PREFLIGHT_VALIDATION", text)
        self.assertIn("Do not start with well-understood tasks because they are easy", normalized)

    def test_falsification_gate_makes_cost_a_last_order_tiebreaker(self) -> None:
        text = FALSIFICATION_GATE.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        lowered = normalized.lower()
        self.assertIn("Rank lexicographically", text)
        self.assertLess(
            lowered.index("hard-constraint blockers"),
            lowered.index("cost, latency, and implementation convenience"),
        )
        self.assertIn("Cost chooses the smallest **valid** check", normalized)
        self.assertIn("every unresolved blocker and every unresolved load-bearing assumption", text)
        self.assertIn("never shrink the set to fit the budget", text)
        self.assertIn('"minimumFairTuneEnvelope"', text)
        self.assertIn("Do not turn this into broad hyperparameter optimization", normalized)

    def test_workflow_cannot_reach_full_branches_before_preflight_continue(self) -> None:
        text = REFERENCE_DESIGN.read_text(encoding="utf-8")
        routes = text.split("## Routes and bounded loops", maxsplit=1)[1]
        first_plan_branches = routes.index("plan_branches")
        preflight_continue = routes.index("preflight_gate.CONTINUE")
        self.assertLess(preflight_continue, first_plan_branches)
        self.assertIn("validate_assumption_ranking", routes[:first_plan_branches])
        self.assertIn("preflight_resource_gate", routes[:first_plan_branches])
        self.assertIn("STOP_IDEA_REFUTED", routes)
        self.assertIn("NEEDS_USER_DECISION", routes)
        self.assertIn("UNRESOLVED_PREFLIGHT_VALIDATION", routes)

    def test_autoresearch_is_uncertainty_first_and_stops_on_blocker_refutation(self) -> None:
        text = EXPERIMENTAL_VALIDATION.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn("preflightDecision == CONTINUE", text)
        self.assertIn("select_highest_uncertainty_atomic_trial", text)
        self.assertIn("CANDIDATE_REFUTED", text)
        self.assertIn("must not consume the board's easy, well-understood work", normalized)
        self.assertIn("minimum fair tuning envelope", normalized)

    def test_generic_fanout_design_has_a_sequential_preflight_gate(self) -> None:
        text = FANOUT_DESIGN.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertLess(text.index("sequential_preflight_gate"), text.index("repo_scan"))
        self.assertIn("the checks themselves are sequential", normalized)
        self.assertIn("Cost selects the smallest valid check", text)


if __name__ == "__main__":
    unittest.main()
