import json
import re
import unittest
from pathlib import Path


SKILLS = Path(__file__).resolve().parents[2]
ENVELOPE = SKILLS / "writing-plans" / "references" / "invocation-envelope.md"
FORWARD_FIXTURES = SKILLS / "writing-plans" / "tests" / "forward_fixtures.json"


def read_skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def load_lifecycle_policy() -> dict:
    text = ENVELOPE.read_text(encoding="utf-8")
    match = re.search(
        r"```json\s*(\{\s*\"schema\":\s*\"skill-invocation-lifecycle/v1\".*?\})\s*```",
        text,
        re.DOTALL,
    )
    if not match:
        raise AssertionError("canonical lifecycle policy JSON block is missing")
    return json.loads(match.group(1))


def load_iteration_policy() -> dict:
    text = ENVELOPE.read_text(encoding="utf-8")
    match = re.search(
        r"```json\s*(\{\s*\"schema\":\s*\"skill-invocation-iteration-policy/v1\".*?\})\s*```",
        text,
        re.DOTALL,
    )
    if not match:
        raise AssertionError("canonical iteration policy JSON block is missing")
    return json.loads(match.group(1))


def lifecycle_actions(
    policy: dict,
    *,
    assignment_id: str,
    invocation_mode: str,
    terminal_owner: str,
    worker_class: str,
) -> tuple[bool, bool]:
    worker = policy["workerPolicies"][worker_class]
    owns_terminal = invocation_mode == "root" and assignment_id == terminal_owner
    may_review = worker["terminalReview"] == "owner-only" and owns_terminal
    may_write_memory = worker["memory"] == "owner-only" and owns_terminal
    return may_review, may_write_memory


class InvocationContextContractTests(unittest.TestCase):
    def test_iteration_policy_is_immutable_and_feedback_is_checkpoint_bound(self) -> None:
        policy = load_iteration_policy()
        self.assertEqual(["autonomous", "human_guided"], policy["modes"])
        self.assertEqual(["pause", "autonomous_review"], policy["humanUnavailableFallbacks"])
        self.assertEqual(
            [
                "iteration_mode_selected",
                "iteration_mode_effective",
                "human_checkin_lease_minutes",
                "human_unavailable_fallback",
                "human_checkin_lease_expires_at",
                "human_checkin_lease_status",
                "iteration_mode_decision_at",
                "iteration_mode_snapshot_sha256",
            ],
            policy["issueSnapshotFields"],
        )
        self.assertEqual(
            [
                "schema",
                "checkpoint_artifact",
                "checkpoint_sha256",
                "checkpoint_id",
                "human_feedback",
                "received_at",
                "receipt_sha256",
            ],
            policy["humanFeedbackReceiptFields"],
        )
        self.assertEqual(
            {
                "legacyDefault": "autonomous",
                "childMayMutateSnapshot": False,
                "modeChangesAuthority": False,
                "modeChangesTerminalReview": False,
                "humanGuidedRequiresLiveLease": True,
                "nextPlanRequiresBoundFeedback": True,
            },
            policy["invariants"],
        )

    def test_every_nested_skill_points_to_one_canonical_envelope(self) -> None:
        for name in (
            "memory",
            "grind",
            "systematic-debugging",
            "rethink-workflow-design",
            "deep-research-workflow",
            "proof-rigor",
            "writeup-human-cleanup",
            "writing-plans",
        ):
            with self.subTest(skill=name):
                self.assertIn("invocation-envelope.md", read_skill(name))

    def test_only_outer_terminal_owner_runs_terminal_review_and_memory(self) -> None:
        policy = load_lifecycle_policy()
        expected_classes = {
            "execution",
            "pure-collector",
            "terminal-reviewer",
            "machine-gated-node",
            "post-verdict-mechanical",
        }
        self.assertEqual(expected_classes, set(policy["workerPolicies"]))

        self.assertEqual(
            (True, True),
            lifecycle_actions(
                policy,
                assignment_id="root-1",
                invocation_mode="root",
                terminal_owner="root-1",
                worker_class="execution",
            ),
        )
        for worker_class in expected_classes:
            if worker_class == "execution":
                continue
            with self.subTest(worker_class=worker_class):
                self.assertEqual(
                    (False, False),
                    lifecycle_actions(
                        policy,
                        assignment_id="child-1",
                        invocation_mode="child",
                        terminal_owner="root-1",
                        worker_class=worker_class,
                    ),
                )
        self.assertEqual(
            (False, False),
            lifecycle_actions(
                policy,
                assignment_id="child-exec",
                invocation_mode="child",
                terminal_owner="root-1",
                worker_class="execution",
            ),
        )

    def test_nested_context_never_expands_authority_and_reentry_returns_to_owner(self) -> None:
        rank = {"read_only": 0, "write": 1, "experiment": 2}

        def child_authority(parent: str, requested: str) -> str:
            return parent if rank[parent] <= rank[requested] else requested

        self.assertEqual("read_only", child_authority("read_only", "experiment"))
        self.assertEqual("write", child_authority("experiment", "write"))
        self.assertEqual("experiment", child_authority("experiment", "experiment"))

        def next_action(active_stack: tuple[str, ...], skill: str) -> str:
            return "RETURN_FINDING" if skill in active_stack else "INVOKE_CHILD"

        self.assertEqual(
            "RETURN_FINDING",
            next_action(("rethink-workflow-design", "deep-research-workflow"), "rethink-workflow-design"),
        )
        self.assertEqual(
            "INVOKE_CHILD",
            next_action(("rethink-workflow-design",), "deep-research-workflow"),
        )

    def test_diagnose_only_stops_before_phase_four_mutation(self) -> None:
        text = read_skill("systematic-debugging")
        self.assertIn("authority=read_only", text)
        self.assertIn("root-cause artifact", text)
        self.assertIn("Do not enter Phase 4", text)

    def test_analysis_only_rethink_is_ephemeral_and_non_experimental(self) -> None:
        text = read_skill("rethink-workflow-design")
        self.assertRegex(text, re.compile(r"analysis-only.*ephemeral", re.DOTALL))
        self.assertIn("do not run an experiment", text)
        self.assertIn("child evidence scan", text)
        self.assertIn("must not re-enter `rethink-workflow-design`", text)

    def test_child_research_scan_returns_without_terminal_or_memory(self) -> None:
        text = read_skill("deep-research-workflow")
        self.assertIn("child evidence scan", text)
        self.assertIn("return a scoped evidence receipt", text)
        self.assertIn("must not emit a terminal verdict or write memory", text)

    def test_deep_research_uses_direction_specific_reviewer_assets(self) -> None:
        skill = read_skill("deep-research-workflow")
        design = (
            SKILLS / "deep-research-workflow" / "references" / "reference-design.md"
        ).read_text(encoding="utf-8")
        combined = f"{skill}\n{design}"
        self.assertIn("`cross-review-openai-writer`", combined)
        self.assertIn("`cross-review-anthropic-writer`", combined)
        self.assertNotIn("`cross-reviewer`", combined)
        self.assertIn("omitted/mismatched", combined)

    def test_research_supporting_references_return_human_dependencies_to_owner(self) -> None:
        references = SKILLS / "deep-research-workflow" / "references"
        gate = (references / "falsification-gate.md").read_text(encoding="utf-8")
        delivery = (references / "delivery-workflow.md").read_text(encoding="utf-8")
        design = (references / "reference-design.md").read_text(encoding="utf-8")
        for text in (gate, delivery, design):
            with self.subTest(reference=text[:40]):
                self.assertIn("`terminalOwner`", text)
        self.assertIn("return the checkpoint", gate)
        self.assertIn("return the checkpoint", delivery)
        self.assertIn("children return learning candidates", design)

    def test_not_ready_never_executes_auto_research_experiments(self) -> None:
        skill = read_skill("deep-research-workflow")
        reference = (
            SKILLS
            / "deep-research-workflow"
            / "references"
            / "experimental-validation.md"
        ).read_text(encoding="utf-8")
        for text in (skill, reference):
            with self.subTest(path="skill" if text is skill else "reference"):
                self.assertIn("`NOT_READY`", text)
                self.assertIn("execute no experiment", text)
        self.assertIn("does not authorize even a smoke, baseline, preprocessing, or trial", reference)

    def test_not_ready_resource_gate_is_an_execution_firewall(self) -> None:
        def experimental_actions(verdict: str) -> tuple[str, ...]:
            if verdict != "READY":
                return ()
            return ("baseline", "preprocess", "trial")

        for verdict in ("NOT_READY", "INVALID"):
            with self.subTest(verdict=verdict):
                self.assertEqual((), experimental_actions(verdict))
        self.assertEqual(
            ("baseline", "preprocess", "trial"),
            experimental_actions("READY"),
        )

    def test_proof_cleanup_handoff_is_one_way(self) -> None:
        proof = read_skill("proof-rigor")
        cleanup = read_skill("writeup-human-cleanup")
        self.assertIn("proof-rigor → writeup-human-cleanup", proof)
        self.assertIn("must not invoke `proof-rigor`", cleanup)
        self.assertIn("NEEDS_PROOF_RIGOR", cleanup)

    def test_grind_exempts_non_owners_from_recursive_lifecycle_work(self) -> None:
        text = read_skill("grind")
        for worker_class in (
            "terminal-reviewer",
            "pure-collector",
            "machine-gated-node",
            "post-verdict-mechanical",
        ):
            with self.subTest(worker_class=worker_class):
                self.assertIn(worker_class, text)
        self.assertIn("do not commission a reviewer or write memory", text)

    def test_systematic_failure_thresholds_match_grind(self) -> None:
        text = read_skill("systematic-debugging")
        for exact in (
            "2nd failure | L1",
            "3rd failure | L2",
            "4th failure | L3",
            "5+ failures | L4",
        ):
            with self.subTest(exact=exact):
                self.assertIn(exact, text)
        self.assertNotIn("≥3 failed fixes means question the architecture", text)

    def test_forward_fixtures_are_blind_prompts_with_machine_checkable_invariants(self) -> None:
        fixtures = json.loads(FORWARD_FIXTURES.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(fixtures), 3)
        for fixture in fixtures:
            with self.subTest(fixture=fixture["id"]):
                self.assertTrue(fixture["prompt"].startswith("Use $"))
                self.assertNotRegex(
                    fixture["prompt"].lower(),
                    r"test the skill|expected answer|suspected bug|intended fix|must mention",
                )
                self.assertTrue(fixture["mustContain"])
                self.assertTrue(fixture["mustNotContain"])


if __name__ == "__main__":
    unittest.main()
