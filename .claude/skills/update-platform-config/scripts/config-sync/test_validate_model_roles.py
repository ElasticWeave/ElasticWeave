from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
ROLE_TEMPLATE = REPO / ".claude" / "skills" / "default-mode" / "assets" / "project" / "config" / "workflows" / "models.json"
SPEC = importlib.util.spec_from_file_location("validate_model_roles", HERE / "validate_model_roles.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ModelRolePolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(ROLE_TEMPLATE.read_text(encoding="utf-8"))

    def test_current_bootstrap_policy_is_structurally_valid(self) -> None:
        MODULE.validate(copy.deepcopy(self.document))
        self.assertEqual("bootstrap-unverified", self.document["catalogResolution"]["status"])

    def test_rejects_unknown_role_in_intelligence_class(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["reasoningPolicy"]["intelligenceClasses"]["collection"].append("missing")
        with self.assertRaisesRegex(MODULE.ValidationError, "unknown role"):
            MODULE.validate(candidate)

    def test_rejects_cross_family_effort_equivalence(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["reasoningPolicy"]["crossFamilyEffortComparable"] = True
        with self.assertRaisesRegex(MODULE.ValidationError, "must not be marked comparable"):
            MODULE.validate(candidate)

    def test_rejects_missing_builtin_role(self) -> None:
        candidate = copy.deepcopy(self.document)
        del candidate["roles"]["advisor"]
        with self.assertRaisesRegex(MODULE.ValidationError, "missing required roles"):
            MODULE.validate(candidate)

    def test_rejects_backend_effort_outside_declared_native_control(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["roles"]["task"]["effort"] = "maximum"
        with self.assertRaisesRegex(MODULE.ValidationError, "invalid for backend codex"):
            MODULE.validate(candidate)

    def test_review_roles_are_exact_max_cross_family_pair(self) -> None:
        self.assertEqual("claude", self.document["roles"]["advisor"]["backend"])
        self.assertEqual("anthropic", self.document["roles"]["advisor"]["provider"])
        self.assertEqual("max", self.document["roles"]["advisor"]["effort"])
        self.assertEqual("claude-opus-5", self.document["roles"]["advisor"]["modelFamily"])
        self.assertEqual("max", self.document["roles"]["codex_advisor"]["effort"])
        self.assertEqual("gpt-6-astra", self.document["roles"]["codex_advisor"]["modelFamily"])

        candidate = copy.deepcopy(self.document)
        candidate["roles"]["advisor"]["effort"] = "xhigh"
        with self.assertRaisesRegex(MODULE.ValidationError, "advisor must be native Claude Code"):
            MODULE.validate(candidate)

    def test_native_claude_effort_policy_matches_authenticated_catalog(self) -> None:
        self.assertEqual(
            ["low", "medium", "high", "xhigh", "max"],
            self.document["reasoningPolicy"]["backendEffortLevels"]["claude"],
        )

        candidate = copy.deepcopy(self.document)
        candidate["reasoningPolicy"]["backendEffortLevels"]["claude"].append("ultra")
        with self.assertRaisesRegex(MODULE.ValidationError, "Claude backend effort levels"):
            MODULE.validate(candidate)

    def test_rejects_provider_relabeling(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["roles"]["advisor"]["provider"] = "codex"
        with self.assertRaisesRegex(MODULE.ValidationError, "does not match backend"):
            MODULE.validate(candidate)

    def test_cursor_diversity_uses_highest_fresh_effort(self) -> None:
        policy = self.document["capabilityPolicy"]["taskClasses"]["diversityReview"]
        self.assertEqual("highest-available", policy["requiredEffort"])
        self.assertEqual(["subagent-scheduler"], policy["activeHarnesses"])
        for role_name in ("diversity_kimi", "diversity_glm"):
            role = self.document["roles"][role_name]
            self.assertEqual("catalog-candidate", role["dispatchStatus"])
            self.assertIsNone(role["effort"])
            self.assertEqual("highest-available", role["requiredEffort"])

        grok = self.document["roles"]["diversity_grok"]
        self.assertEqual("active", grok["dispatchStatus"])
        self.assertEqual("xhigh", grok["effort"])
        self.assertEqual("highest-available", grok["effortPolicy"])

        candidate = copy.deepcopy(self.document)
        del candidate["roles"]["diversity_grok"]["effortPolicy"]
        with self.assertRaisesRegex(MODULE.ValidationError, "highest-available effort policy"):
            MODULE.validate(candidate)

    def test_top_executor_and_collection_hints_are_exact(self) -> None:
        roles = self.document["roles"]
        self.assertEqual(("gpt-6-astra", "max"), (roles["frontier_execute"]["modelFamily"], roles["frontier_execute"]["effort"]))
        self.assertEqual(("claude-opus-5", "max"), (roles["frontier_execute_anthropic"]["modelFamily"], roles["frontier_execute_anthropic"]["effort"]))
        self.assertEqual(("gpt-5.6-luna", "max"), (roles["research_collect"]["modelFamily"], roles["research_collect"]["effort"]))
        self.assertEqual(("claude-sonnet-5", "high"), (roles["research_collect_anthropic"]["modelFamily"], roles["research_collect_anthropic"]["effort"]))

    def test_future_and_excluded_candidates_are_not_active_roles(self) -> None:
        policy = self.document["capabilityPolicy"]
        self.assertEqual("deepseek", policy["futureCandidates"][0]["modelFamily"])
        self.assertEqual("highest-available", policy["futureCandidates"][0]["requiredEffort"])
        self.assertEqual("composer-2.5", policy["excludedFamilies"][0]["modelFamily"])
        self.assertNotIn("diversity_deepseek", self.document["roles"])

    def test_bootstrap_cannot_claim_partial_verification(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["catalogResolution"]["verifiedRoles"] = ["task"]
        with self.assertRaisesRegex(MODULE.ValidationError, "cannot claim verified"):
            MODULE.validate(candidate)

    def test_advice_roles_include_active_astra(self) -> None:
        roles = self.document["roles"]
        self.assertEqual("claude-opus-5", roles["frontier_advice"]["modelFamily"])
        self.assertEqual("claude-fable-5-1", roles["frontier_advice_fable"]["modelFamily"])
        for name in ("frontier_advice", "frontier_advice_fable"):
            self.assertEqual("cursor", roles[name]["backend"])
            self.assertEqual("highest-available", roles[name]["effortPolicy"])

        astra = roles["frontier_advice_astra"]
        self.assertEqual("gpt-6-astra", astra["modelFamily"])
        self.assertEqual("codex", astra["backend"])
        self.assertEqual("active", astra["dispatchStatus"])
        self.assertEqual("max", astra["effort"])
        top = self.document["capabilityPolicy"]["taskClasses"]["topIntelligence"]
        self.assertIn("frontier_advice_astra", top["qualifiedAdviceRoles"])
        self.assertIn("frontier_advice_astra", self.document["reasoningPolicy"]["postures"]["balanced"]["allowedRoles"])
        self.assertIn("frontier_advice_astra", self.document["reasoningPolicy"]["postures"]["frontier"]["allowedRoles"])

    def test_stale_fable_5_identity_is_rejected_even_if_the_identity_map_is_resealed(self) -> None:
        candidate = copy.deepcopy(self.document)
        role = candidate["roles"]["frontier_advice_fable"]
        role["model"] = "claude-fable-5"
        role["modelFamily"] = "claude-fable-5"
        for binding in candidate["auditedModelIdentities"]["bindings"]:
            if binding["backend"] == "cursor" and binding["model"] == "claude-fable-5-1":
                binding["model"] = "claude-fable-5"
                binding["modelFamily"] = "claude-fable-5"
                break
        with self.assertRaisesRegex(MODULE.ValidationError, "Fable 5.1"):
            MODULE.validate(candidate)

    def test_verified_catalog_must_cover_every_active_role_and_backend(self) -> None:
        candidate = copy.deepcopy(self.document)
        roles = sorted(name for name, role in candidate["roles"].items() if role["dispatchStatus"] == "active")
        backends = sorted({candidate["roles"][name]["backend"] for name in roles})
        candidate["catalogResolution"].update(
            status="verified",
            verifiedBackends=backends,
            verifiedRoles=roles,
            evidenceReceipt={
                "schema": "workflow-model-catalog-evidence/v1",
                "assurance": "caller-supplied-structural-evidence-not-live-authentication",
                "backendCatalogSha256": {
                    backend: "sha256:" + hashlib.sha256(backend.encode()).hexdigest()
                    for backend in backends
                },
                "verifiedRoleIdentitiesSha256": MODULE.canonical_sha256(
                    MODULE.resolved_role_identities(candidate["roles"], roles)
                ),
            },
        )
        MODULE.validate(candidate)
        candidate["catalogResolution"]["verifiedRoles"].remove("task")
        with self.assertRaisesRegex(MODULE.ValidationError, "exactly cover every active role"):
            MODULE.validate(candidate)

    def test_rejects_posture_escalations_above_global_cap(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["reasoningPolicy"]["postures"]["economy"]["maxAutomaticEscalations"] = 3
        with self.assertRaisesRegex(MODULE.ValidationError, "within the global cap"):
            MODULE.validate(candidate)

    def test_static_policy_disables_automatic_role_changes(self) -> None:
        policy = self.document["reasoningPolicy"]
        self.assertEqual(policy["maxAutomaticEscalations"], 0)
        for posture in policy["postures"].values():
            self.assertEqual(posture["maxAutomaticEscalations"], 0)

        rules = " ".join(policy["rules"])
        self.assertIn("static workflow never changes roles automatically", rules.lower())
        self.assertIn("explicitly expanded and validated branch", rules.lower())

    def test_postures_have_machine_distinct_allowed_role_ceilings(self) -> None:
        postures = self.document["reasoningPolicy"]["postures"]
        economy = set(postures["economy"]["allowedRoles"])
        balanced = set(postures["balanced"]["allowedRoles"])
        frontier = set(postures["frontier"]["allowedRoles"])
        self.assertLess(economy, balanced)
        self.assertLess(balanced, frontier)
        self.assertNotIn("task", economy)
        self.assertNotIn("frontier_execute_anthropic", balanced)
        self.assertIn("frontier_execute_anthropic", frontier)

    def test_exact_model_cannot_lie_about_family_or_upstream(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["roles"]["task"]["modelFamily"] = "claude-opus-5"
        with self.assertRaisesRegex(MODULE.ValidationError, "audited model identity"):
            MODULE.validate(candidate)

        candidate = copy.deepcopy(self.document)
        candidate["roles"]["task"]["upstreamProviderFamily"] = "anthropic"
        with self.assertRaisesRegex(MODULE.ValidationError, "audited model identity"):
            MODULE.validate(candidate)

    def test_verified_catalog_requires_structural_evidence_receipt(self) -> None:
        candidate = copy.deepcopy(self.document)
        roles = sorted(name for name, role in candidate["roles"].items() if role["dispatchStatus"] == "active")
        candidate["catalogResolution"].update(
            status="verified",
            verifiedBackends=sorted({candidate["roles"][name]["backend"] for name in roles}),
            verifiedRoles=roles,
        )
        with self.assertRaisesRegex(MODULE.ValidationError, "evidenceReceipt"):
            MODULE.validate(candidate)

    def test_global_max_automatic_escalations_rejects_bool(self) -> None:
        # bool is a subclass of int in Python; isinstance(True, int) is True, so a
        # structural validator that uses isinstance would silently accept True/False.
        # The validator must use type(value) is int to reject bool.
        candidate = copy.deepcopy(self.document)
        candidate["reasoningPolicy"]["maxAutomaticEscalations"] = True
        with self.assertRaisesRegex(MODULE.ValidationError, "maxAutomaticEscalations must be an integer"):
            MODULE.validate(candidate)
        candidate = copy.deepcopy(self.document)
        candidate["reasoningPolicy"]["maxAutomaticEscalations"] = False
        with self.assertRaisesRegex(MODULE.ValidationError, "maxAutomaticEscalations must be an integer"):
            MODULE.validate(candidate)

    def test_posture_max_automatic_escalations_rejects_bool(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["reasoningPolicy"]["postures"]["economy"]["maxAutomaticEscalations"] = True
        with self.assertRaisesRegex(MODULE.ValidationError, "maxAutomaticEscalations must be within the global cap"):
            MODULE.validate(candidate)
        candidate = copy.deepcopy(self.document)
        candidate["reasoningPolicy"]["postures"]["balanced"]["maxAutomaticEscalations"] = False
        with self.assertRaisesRegex(MODULE.ValidationError, "maxAutomaticEscalations must be within the global cap"):
            MODULE.validate(candidate)

    def test_role_relative_cost_rejects_bool(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["roles"]["task"]["relativeCost"] = True
        with self.assertRaisesRegex(MODULE.ValidationError, "positive integer relativeCost"):
            MODULE.validate(candidate)
        candidate = copy.deepcopy(self.document)
        candidate["roles"]["smol"]["relativeCost"] = False
        with self.assertRaisesRegex(MODULE.ValidationError, "positive integer relativeCost"):
            MODULE.validate(candidate)


if __name__ == "__main__":
    unittest.main()
