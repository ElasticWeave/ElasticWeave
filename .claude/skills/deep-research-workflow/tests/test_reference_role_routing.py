from __future__ import annotations

from pathlib import Path
import re
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_ROOT.parent
REFERENCES = SKILL_ROOT / "references"
SKILL = SKILL_ROOT / "SKILL.md"
REFERENCE_DESIGN = REFERENCES / "reference-design.md"
EXPERIMENTAL_VALIDATION = REFERENCES / "experimental-validation.md"
FALSIFICATION_GATE = REFERENCES / "falsification-gate.md"
DELIVERY_WORKFLOW = REFERENCES / "delivery-workflow.md"
MODEL_ROUTING = REFERENCES / "model-routing.md"
GRIND = SKILLS_ROOT / "grind" / "SKILL.md"
MARKETPLACE_SEARCH = SKILLS_ROOT / "marketplace-search" / "SKILL.md"
SYSTEMATIC_DEBUGGING = SKILLS_ROOT / "systematic-debugging" / "SKILL.md"
WRITING_PLANS = SKILLS_ROOT / "writing-plans" / "SKILL.md"
PLAN_REVIEW_PROMPT = SKILLS_ROOT / "writing-plans" / "plan-document-reviewer-prompt.md"
FIRST_PRINCIPLES = SKILLS_ROOT / "first-principles" / "SKILL.md"
SUBAGENT_WORKFLOW = SKILLS_ROOT / "subagent-workflow" / "SKILL.md"
SESSION_IMPORT = SKILLS_ROOT / "session-import" / "SKILL.md"
SESSION_SOURCE_ADAPTERS = SKILLS_ROOT / "session-import" / "references" / "source-adapters.md"
WORKFLOW_UPDATER = (
    SKILLS_ROOT
    / "write-subagent"
    / "assets"
    / "project"
    / ".omp"
    / "agents"
    / "workflow-updater.md"
)


class ReferenceRoleRoutingContractTest(unittest.TestCase):
    def test_iteration_mode_awareness_prompts_name_their_full_skills(self) -> None:
        prompts = {
            "deep-research-workflow": SKILL_ROOT / "agents/openai.yaml",
            "write-humanize-flow": SKILLS_ROOT / "write-humanize-flow/agents/openai.yaml",
            "rethink-workflow-design": SKILLS_ROOT
            / "rethink-workflow-design/agents/openai.yaml",
        }
        for skill_name, path in prompts.items():
            with self.subTest(skill=skill_name):
                text = path.read_text(encoding="utf-8")
                self.assertIn(f"Use ${skill_name} ", text)

    def test_iteration_mode_is_orthogonal_to_model_and_terminal_routing(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        normalized_skill = " ".join(skill.split())
        normalized_design = " ".join(design.split())
        self.assertIn(
            "Keep this choice separate from research width, reasoning posture",
            normalized_skill,
        )
        self.assertIn("terminal `review_policy`", normalized_skill)
        self.assertIn("All safety, validity, code/alignment, evidence, proof", normalized_design)
        self.assertIn("unresolved checkpoint terminal", design)

    def test_default_mode_carries_a_veritrace_deviation_ledger(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        experiment = EXPERIMENTAL_VALIDATION.read_text(encoding="utf-8")
        combined = "\n".join((skill, design, experiment))
        for invariant in (
            "expected outcome",
            "expected measurement",
            "evidence lineage",
            "deviation classification",
            "structural review",
            "reward-hacking",
            "must not silently retry the same strategy",
            "https://arxiv.org/abs/2605.26081",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(invariant, combined)
        self.assertIn("A top session only inspects, dispatches, and", skill)

    def test_model_routing_resolves_live_roles_without_host_snapshot(self) -> None:
        text = MODEL_ROUTING.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        transient_patterns = {
            "calendar-dated snapshot": re.compile(r"\b(?:19|20)\d{2}\b"),
            "host catalog prose": re.compile(
                r"(?:current|local|this)\s+host|local\s+native\s+catalogs?|"
                r"(?:catalog|route)\s+(?:snapshot|refresh)|"
                r"refresh\s+(?:found|checked)|"
                r"##\s+(?:current|detected|available)\s+(?:candidate|model)|"
                r"\|\s*Candidate\s*\|\s*Project use\s*\|\s*Native route\s*\||"
                r"No audited route on",
                re.IGNORECASE,
            ),
        }
        for label, pattern in transient_patterns.items():
            with self.subTest(label=label):
                self.assertIsNone(pattern.search(text))

        for invariant in (
            "canonical project role config",
            "authenticated native CLI",
            "provider identity",
            "provider differs from the writer provider",
            "Fail closed",
            "typed unresolved\nrouting result",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(" ".join(invariant.split()), normalized)

    def test_references_never_publish_nonzero_automatic_escalation(self) -> None:
        pattern = re.compile(r'"maxAutomaticEscalations"\s*:\s*[1-9]\d*')
        offenders = [
            path.name
            for path in sorted(REFERENCES.glob("*.md"))
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [])

    def test_references_do_not_describe_dynamic_role_mutation(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(REFERENCES.glob("*.md"))
        )
        forbidden = (
            '"escalationRole"',
            "retried at the next allowed role",
            "acceptance-triggered escalation",
        )
        for claim in forbidden:
            with self.subTest(claim=claim):
                self.assertNotIn(claim, text)

    def test_research_routing_uses_current_semantic_role_policy(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        routing = MODEL_ROUTING.read_text(encoding="utf-8")
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        experiment = EXPERIMENTAL_VALIDATION.read_text(encoding="utf-8")
        combined = "\n".join((skill, routing, design, experiment))

        for role in (
            "frontier_execute",
            "frontier_execute_anthropic",
            "frontier_advice",
            "frontier_advice_fable",
            "research_collect",
            "research_collect_anthropic",
            "advisor",
            "codex_advisor",
            "diversity_glm",
            "diversity_kimi",
            "diversity_grok",
        ):
            with self.subTest(role=role):
                self.assertIn(f"`{role}`", combined)

        self.assertIn("Astra-family writer → `advisor`", routing)
        self.assertIn("Opus-family writer → `codex_advisor`", routing)
        self.assertIn("future DeepSeek", routing)
        self.assertIn("Composer-family 2.5 remains excluded", routing)
        normalized_routing = " ".join(routing.split())
        self.assertIn("upstream Humanize pin exposes Cursor", normalized_routing)
        self.assertIn("research launcher does not stage", normalized_routing)
        retired_role = "frontier" + "_advisor"
        self.assertNotIn(retired_role, combined)

    def test_frontier_advice_supports_proposal_and_result_analysis(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        routing = MODEL_ROUTING.read_text(encoding="utf-8")
        combined = " ".join((skill + "\n" + routing).split())
        for invariant in (
            "independent alternative proposal",
            "frontier_advice_fable",
            "hard or materially uncertain advice",
            "surprising, contradictory, or inconclusive result",
            "before either branch sees the other's answer",
            "preserve material disagreements",
            "not majority voting",
            "does not replace terminal review",
        ):
            with self.subTest(invariant=invariant):
                self.assertIn(invariant, combined)

    def test_reference_design_fails_closed_without_static_branch(self) -> None:
        text = REFERENCE_DESIGN.read_text(encoding="utf-8")
        self.assertIn('"maxAutomaticEscalations": 0', text)
        self.assertIn("NEEDS_ESCALATION", text)
        self.assertIn("statically expanded", text)
        self.assertIn("new design and mapping", text)
        self.assertIn("finalize_checkpoint", text)

    def test_experimental_validation_fails_closed_without_static_branch(self) -> None:
        text = EXPERIMENTAL_VALIDATION.read_text(encoding="utf-8")
        self.assertIn("NEEDS_ESCALATION", text)
        self.assertIn("statically expanded", text)
        self.assertIn("new design and mapping", text)
        self.assertIn("UNRESOLVED_EXPERIMENTAL_VALIDATION", text)
        self.assertNotIn("only that leaf is retried", text)

    def test_top_session_never_owns_research_implementation(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        self.assertIn("A top session only inspects, dispatches, and", skill)
        self.assertIn("`quick` normally uses\none bounded native subagent", skill)
        self.assertIn("A top session only inspects and\n  dispatches", design)
        for retired in (
            "`quick` normally stays direct",
            "direct/native-subagent execution",
            "demotes to direct or one native subagent",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, skill + "\n" + design)

    def test_review_routes_require_fresh_terminal_reviewer(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        combined = skill + "\n" + design
        self.assertNotIn("- `none`:", combined)
        self.assertNotIn("coordinator runs acceptance and owns the judgment", combined)
        self.assertIn("`independent`", combined)
        self.assertIn("`machine-gated`", combined)
        self.assertIn("`COMPLETE`, `HUMAN_BLOCKED`, or\n`REVISE`", skill)
        self.assertIn("fresh acceptance evidence", design)
        self.assertNotIn("adversarial review only if requested for diagnosis", design)
        self.assertIn(
            "Every domain terminal below additionally carries a fresh different-provider",
            design,
        )
        self.assertIn(
            "does not\nrecursively commission another reviewer",
            skill,
        )
        self.assertIn("does not recursively invoke another terminal reviewer", design)
        self.assertIn(
            "`HUMAN_BLOCKED` is valid only for a named permission, decision,\n"
            "credential, or external action that only a human can provide",
            skill,
        )

    def test_internal_review_and_session_terminal_are_distinct(self) -> None:
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        self.assertIn("| `internalReviewVerdict` |", design)
        self.assertIn("| `terminalReviewVerdict` |", design)
        self.assertIn(
            "used only for graph routing and never authorizes a human block",
            design,
        )
        self.assertIn("adversarial_review.PASS or adversarial_review.APPROVE", design)
        self.assertNotIn("adversarial_review.HUMAN_BLOCKED", design)
        self.assertNotIn("returns one terminal verdict over current evidence", design)

    def test_independent_review_occurs_only_after_graph_return(self) -> None:
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        routes = design.split("## Routes and bounded loops", maxsplit=1)[1]
        finalize_success = routes.split("finalize_success", maxsplit=1)[1].split(
            "finalize_checkpoint", maxsplit=1
        )[0]
        finalize_checkpoint = routes.split("finalize_checkpoint", maxsplit=1)[1].split(
            "adversarial_review.PASS", maxsplit=1
        )[0]
        self.assertNotIn("independent review", finalize_success)
        self.assertNotIn("independent review", finalize_checkpoint)
        self.assertIn(
            "graph_return(independent policy, any targetTerminal)\n"
            "  → top session dispatches external terminal reviewer",
            routes,
        )

    def test_human_ask_requires_fresh_structured_terminal_block(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        gate = FALSIFICATION_GATE.read_text(encoding="utf-8")
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        delivery = DELIVERY_WORKFLOW.read_text(encoding="utf-8")
        combined = "\n".join((skill, gate, design, delivery))
        self.assertIn("HUMAN-REQUEST-KIND", design)
        self.assertIn("one concrete `HUMAN-REQUEST`", design)
        self.assertIn("fresh terminal reviewer", skill)
        self.assertIn("controlling session or\nconfigured UI", skill)
        self.assertIn("CANDIDATE_HUMAN_DEPENDENCY", gate)
        self.assertIn("graph_return(any policy, NEEDS_USER_DECISION checkpoint)", design)
        self.assertIn("typed terminal HUMAN_BLOCKED authorizes the exact", design)
        self.assertIn("only its typed\n`HUMAN_BLOCKED` result may authorize the exact", delivery)
        self.assertIn("bounded delivery worker", delivery)
        for retired in (
            "If a material ambiguity could change\nwhat counts as success, use `$ask-user`",
            "| `ASK_USER` |",
            "coordinator ask-user (if needed)",
            "preflight_gate.ASK_USER",
            "Coordinator `$ask-user`, then `seed`",
            "lets the coordinator ask",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, combined)

    def test_terminal_review_is_lane_aware_and_cold_start_exception_is_narrow(self) -> None:
        workflow = SUBAGENT_WORKFLOW.read_text(encoding="utf-8")
        plans = WRITING_PLANS.read_text(encoding="utf-8")
        session_import = SESSION_IMPORT.read_text(encoding="utf-8")
        source_adapters = SESSION_SOURCE_ADAPTERS.read_text(encoding="utf-8")

        self.assertIn("terminal-classifying in-graph review receipt", workflow)
        self.assertIn("even when that run does not declare a state-bound PM completion", workflow)
        self.assertIn("evidence that safe agent-side alternatives were exhausted", workflow)
        self.assertIn("do not duplicate it with an external review", plans)
        self.assertIn("If that receipt is absent,\nstale, malformed", plans)
        self.assertIn("being before startup by itself is not an exception", session_import)
        self.assertIn("cold-start launching conversation alone", source_adapters)

        combined = "\n".join((workflow, plans, session_import, source_adapters))
        for retired in (
            "machine-gated run that stops without such a proof",
            "Before a session stops terminally, invoke a separate independent reviewer",
            "unreviewed cold-start alignment exception",
            "any launching conversation may use",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, combined)

    def test_top_delegates_closing_design_and_channel_mutations(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        design = REFERENCE_DESIGN.read_text(encoding="utf-8")
        experiment = EXPERIMENTAL_VALIDATION.read_text(encoding="utf-8")
        routing = MODEL_ROUTING.read_text(encoding="utf-8")
        self.assertIn("dispatch a bounded closing owner", skill)
        self.assertIn("top dispatches a bounded closing owner", design)
        self.assertIn("top dispatches a bounded designer/mapper", design)
        self.assertIn("top\ndispatches a bounded designer/mapper", experiment)
        self.assertIn("configured UI after typed `HUMAN_BLOCKED`", skill)
        self.assertIn("bounded delivery worker", DELIVERY_WORKFLOW.read_text(encoding="utf-8"))
        self.assertIn("bounded `$codex-verify` reviewer", design)
        self.assertIn("bounded designer/mapper", routing)
        self.assertIn("top only inspects both artifacts", routing)
        for retired in (
            "coordinator must generate and validate a new design and mapping",
            "coordinator runs the bounded `$self-improve`/`$memory`",
            "the coordinator runs\n  `$codex-verify`",
            "The coordinator records that route",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, design + "\n" + experiment + "\n" + routing)

    def test_adjacent_escalation_and_memory_rules_do_not_bypass_review(self) -> None:
        grind = GRIND.read_text(encoding="utf-8")
        debugging = SYSTEMATIC_DEBUGGING.read_text(encoding="utf-8")
        plans = WRITING_PLANS.read_text(encoding="utf-8")
        marketplace = MARKETPLACE_SEARCH.read_text(encoding="utf-8")
        combined = "\n".join((grind, debugging, plans))
        self.assertIn("Only structured `HUMAN_BLOCKED`", combined)
        self.assertIn("`REVISE` means continue or\n   redispatch debugging", debugging)
        self.assertIn("`REVISE` continues or redispatches work", plans)
        self.assertIn("bounded marketplace\n   owner records its own", marketplace)
        self.assertIn("top session only authorizes and inspects", marketplace)
        self.assertNotIn("Asking a human after a genuine L3 exhaust", grind)
        self.assertNotIn("ask the human on Linear", debugging)
        self.assertNotIn("or asking the user when the choice truly", plans)

    def test_adjacent_docs_have_no_deleted_paths_or_provider_routes(self) -> None:
        first_principles = FIRST_PRINCIPLES.read_text(encoding="utf-8")
        grind = GRIND.read_text(encoding="utf-8")
        debugging = SYSTEMATIC_DEBUGGING.read_text(encoding="utf-8")
        plans = WRITING_PLANS.read_text(encoding="utf-8")
        plan_review = PLAN_REVIEW_PROMPT.read_text(encoding="utf-8")
        self.assertIn("plan-document-reviewer-prompt.md", plans)
        self.assertIn("configured different-provider native reviewer", plan_review)
        self.assertIn("This is a nonterminal\nplanning review", plan_review)
        combined = "\n".join((first_principles, grind, debugging, plans, plan_review))
        retired_startup = "in" + "it"
        for retired in (
            f"{retired_startup}/SETUP_PRINCIPLE.md",
            "codex MCP",
            "dispatch_gate",
            "skill-runner",
            "tools/grind_gate.py",
            "tools/notion_import_design.py",
            "ask-user",
            "notion-memory",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, combined)

    def test_adjacent_skills_have_executable_review_ownership(self) -> None:
        first_principles = FIRST_PRINCIPLES.read_text(encoding="utf-8")
        updater = WORKFLOW_UPDATER.read_text(encoding="utf-8")
        self.assertIn("For a nonterminal design analysis", first_principles)
        self.assertIn("**planning-review mode**", first_principles)
        self.assertIn("**terminal-completion mode**, not planning-review mode", first_principles)
        self.assertIn("spawns: false", updater)
        self.assertIn("returns a **nonterminal candidate**", updater)
        self.assertIn("The top session owns the subsequent reviewer dispatch", updater)


if __name__ == "__main__":
    unittest.main()
