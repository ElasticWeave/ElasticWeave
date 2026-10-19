import copy
import hashlib
import importlib.util
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sync_policy as policy_sync


HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


claude_sync = load("gen_claude_agents")
codex_sync = load("sync_codex")
agent_roles = load("agent_roles")

REPO = HERE.parents[4]
AGENT_DIR = REPO / ".claude" / "skills" / "write-subagent" / "assets" / "project" / ".omp" / "agents"
ROLE_DOCUMENT = json.loads(
    (REPO / ".claude" / "skills" / "default-mode" / "assets" / "project" / "config" / "workflows" / "models.json").read_text()
)


def verified_document(document):
    verified = copy.deepcopy(document)
    active = {
        name: role for name, role in verified["roles"].items()
        if role["dispatchStatus"] == "active"
    }
    backends = sorted({role["backend"] for role in active.values()})
    validator_path = HERE / "validate_model_roles.py"
    spec = importlib.util.spec_from_file_location("routing_test_validator", validator_path)
    assert spec is not None and spec.loader is not None
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    verified["catalogResolution"].update(
        status="verified",
        verifiedBackends=backends,
        verifiedRoles=sorted(active),
        evidenceReceipt={
            "schema": "workflow-model-catalog-evidence/v1",
            "assurance": "caller-supplied-structural-evidence-not-live-authentication",
            "backendCatalogSha256": {
                backend: "sha256:" + hashlib.sha256(backend.encode()).hexdigest()
                for backend in backends
            },
            "verifiedRoleIdentitiesSha256": validator.canonical_sha256(
                validator.resolved_role_identities(verified["roles"], sorted(active))
            ),
        },
    )
    return verified


AGENT = """---
name: cross-review-anthropic-writer
description: "independent reviewer"
cross-provider: true
writer-role: anthropic_task
model-role: codex_advisor
---
Review independently.
"""


CLAUDE_REVIEWER = AGENT.replace(
    "name: cross-review-anthropic-writer", "name: cross-review-openai-writer"
).replace(
    "writer-role: anthropic_task\nmodel-role: codex_advisor",
    "writer-role: task\nmodel-role: advisor",
)


class ProviderRoutingTests(unittest.TestCase):
    def test_aggregate_canonical_input_loader_reads_verified_sources(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            model_path = root / "config" / "workflows" / "models.json"
            model_path.parent.mkdir(parents=True)
            model_path.write_text(
                json.dumps(verified_document(ROLE_DOCUMENT)), encoding="utf-8"
            )
            agent_dir = root / ".omp" / "agents"
            agent_dir.mkdir(parents=True)
            source = agent_dir / "cross-review-openai-writer.md"
            source.write_text(CLAUDE_REVIEWER, encoding="utf-8")

            inputs = policy_sync.load_canonical_inputs(model_path, agent_dir)

            self.assertEqual(model_path, inputs.model_path)
            self.assertEqual(agent_dir, inputs.agent_directory)
            self.assertEqual(1, len(inputs.agents))
            self.assertEqual("advisor", inputs.agents[0].route.model_role)
            self.assertEqual("claude", inputs.agents[0].route.role["backend"])

    def test_directional_reviewer_assignment_uses_actual_writer_role(self):
        openai_path = AGENT_DIR / "cross-review-openai-writer.md"
        anthropic_path = AGENT_DIR / "cross-review-anthropic-writer.md"

        openai_meta, _ = agent_roles.parse_frontmatter(openai_path.read_text())
        sol_route = agent_roles.resolve_reviewer_assignment(
            openai_meta, ROLE_DOCUMENT, "task", str(openai_path)
        )
        self.assertEqual("task", sol_route.writer_role)
        self.assertEqual("advisor", sol_route.model_role)
        self.assertEqual("claude", sol_route.role["backend"])
        self.assertEqual("anthropic", sol_route.role["provider"])
        self.assertEqual("anthropic", sol_route.role["upstreamProviderFamily"])
        self.assertEqual("max", sol_route.role["effort"])

        anthropic_meta, _ = agent_roles.parse_frontmatter(anthropic_path.read_text())
        opus_route = agent_roles.resolve_reviewer_assignment(
            anthropic_meta,
            ROLE_DOCUMENT,
            "frontier_execute_anthropic",
            str(anthropic_path),
        )
        self.assertEqual("frontier_execute_anthropic", opus_route.writer_role)
        self.assertEqual("codex_advisor", opus_route.model_role)
        self.assertEqual("openai", opus_route.role["upstreamProviderFamily"])
        self.assertEqual("max", opus_route.role["effort"])

        with self.assertRaisesRegex(agent_roles.AgentRoleError, "not verified"):
            agent_roles.resolve_reviewer_assignment(
                openai_meta,
                ROLE_DOCUMENT,
                "task",
                str(openai_path),
                require_catalog_verified=True,
            )
        verified_route = agent_roles.resolve_reviewer_assignment(
            openai_meta,
            verified_document(ROLE_DOCUMENT),
            "task",
            str(openai_path),
            require_catalog_verified=True,
        )
        self.assertEqual("advisor", verified_route.model_role)

        missing_writer = verified_document(ROLE_DOCUMENT)
        missing_writer["catalogResolution"]["verifiedRoles"].remove("task")
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "actual writer role.*verifiedRoles"):
            agent_roles.resolve_reviewer_assignment(
                openai_meta,
                missing_writer,
                "task",
                str(openai_path),
                require_catalog_verified=True,
            )

    def test_directional_reviewer_rejects_wrong_actual_writer_family(self):
        path = AGENT_DIR / "cross-review-openai-writer.md"
        meta, _ = agent_roles.parse_frontmatter(path.read_text())
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "not declared"):
            agent_roles.resolve_reviewer_assignment(
                meta, ROLE_DOCUMENT, "frontier_execute_anthropic", str(path)
            )

    def test_canonical_reviewer_assets_are_direction_qualified(self):
        names = {path.stem for path in AGENT_DIR.glob("*.md")}
        self.assertFalse(
            {"cross-reviewer", "commit-reviewer", "reward-hacking-reviewer"} & names
        )
        self.assertTrue(
            {
                "cross-review-openai-writer",
                "cross-review-anthropic-writer",
                "commit-review-openai-writer",
                "commit-review-anthropic-writer",
                "reward-hacking-review-openai-writer",
                "reward-hacking-review-anthropic-writer",
            }.issubset(names)
        )

    def test_generic_reviewer_name_cannot_silently_bind_one_direction(self):
        meta, _ = agent_roles.parse_frontmatter(
            AGENT.replace("name: cross-review-anthropic-writer", "name: cross-reviewer")
        )
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "direction-qualified"):
            agent_roles.resolve_agent(meta, ROLE_DOCUMENT)

    def test_canonical_agents_do_not_duplicate_exact_model_snapshots(self):
        for path in AGENT_DIR.glob("*.md"):
            meta, _ = agent_roles.parse_frontmatter(path.read_text())
            self.assertNotIn("model", meta, path.name)

    def test_codex_reviewer_is_omitted_from_claude_generation(self):
        meta, _ = claude_sync.parse(AGENT)
        self.assertFalse(claude_sync.provider_compatible(meta, ROLE_DOCUMENT))

    def test_codex_reviewer_is_kept_in_codex_generation(self):
        meta, _ = codex_sync.split_frontmatter(AGENT)
        self.assertTrue(codex_sync.provider_compatible(meta, ROLE_DOCUMENT))

    def test_claude_reviewer_routes_the_other_way(self):
        claude_meta, _ = claude_sync.parse(CLAUDE_REVIEWER)
        codex_meta, _ = codex_sync.split_frontmatter(CLAUDE_REVIEWER)
        self.assertTrue(claude_sync.provider_compatible(claude_meta, ROLE_DOCUMENT))
        self.assertFalse(codex_sync.provider_compatible(codex_meta, ROLE_DOCUMENT))

    def test_native_generators_emit_only_the_selected_provider_pair(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            agent_dir = root / ".omp" / "agents"
            agent_dir.mkdir(parents=True)
            model_path = root / "config" / "workflows" / "models.json"
            model_path.parent.mkdir(parents=True)
            model_path.write_text(json.dumps(ROLE_DOCUMENT), encoding="utf-8")
            (agent_dir / "cross-review-anthropic-writer.md").write_text(AGENT)
            (agent_dir / "cross-review-openai-writer.md").write_text(CLAUDE_REVIEWER)

            claude_out = root / ".claude" / "agents"
            with (
                mock.patch.object(claude_sync, "SRC", str(agent_dir)),
                mock.patch.object(claude_sync, "DST", str(claude_out)),
                mock.patch.object(claude_sync, "load_role_document", return_value=ROLE_DOCUMENT),
                mock.patch.object(claude_sync, "add_role_document_guard"),
            ):
                self.assertEqual(0, claude_sync.main())
            self.assertTrue((claude_out / "cross-review-openai-writer.md").is_file())
            self.assertFalse((claude_out / "cross-review-anthropic-writer.md").exists())

            codex_out = root / ".codex"
            with (
                mock.patch.object(codex_sync, "REPO", str(root)),
                mock.patch.object(codex_sync, "CODEX", str(codex_out)),
            ):
                made = codex_sync.sync_agents({}, ROLE_DOCUMENT)
            self.assertEqual(["cross-review-anthropic-writer"], made)
            self.assertTrue(
                (codex_out / "agents" / "cross-review-anthropic-writer.toml").is_file()
            )
            self.assertFalse(
                (codex_out / "agents" / "cross-review-openai-writer.toml").exists()
            )
            rendered = (
                codex_out / "agents" / "cross-review-anthropic-writer.toml"
            ).read_text()
            self.assertIn('model = "gpt-6-astra"', rendered)
            self.assertIn('reasoning_effort = "max"', rendered)

    def test_claude_generator_emits_only_compatible_agents(self):
        with tempfile.TemporaryDirectory() as raw:
            regenerated = Path(raw) / "agents"
            with (
                mock.patch.object(claude_sync, "SRC", str(AGENT_DIR)),
                mock.patch.object(claude_sync, "DST", str(regenerated)),
                mock.patch.object(claude_sync, "load_role_document", return_value=ROLE_DOCUMENT),
                mock.patch.object(claude_sync, "add_role_document_guard"),
            ):
                self.assertEqual(0, claude_sync.main())

            self.assertEqual(
                {
                    "commit-review-openai-writer.md",
                    "cross-review-openai-writer.md",
                    "reward-hacking-review-openai-writer.md",
                },
                {path.name for path in regenerated.glob("*.md")},
            )
            generated = (regenerated / "cross-review-openai-writer.md").read_text()
            self.assertIn("model: opus", generated)
            self.assertNotIn("cursor/claude", generated)
            self.assertFalse((regenerated / "analyzer.md").exists())
            self.assertFalse((regenerated / "iterator.md").exists())
            second = claude_sync.build_claude_agent_plan(
                ROLE_DOCUMENT,
                source_root=AGENT_DIR,
                destination_root=regenerated,
            )
            self.assertEqual([], second.actions)

    def test_claude_generator_refuses_non_generated_name_collision_before_writes(self):
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "agents"
            destination.mkdir(parents=True)
            sentinel = destination / "cross-review-openai-writer.md"
            sentinel.write_text("user-owned sentinel\n")
            with (
                mock.patch.object(claude_sync, "SRC", str(AGENT_DIR)),
                mock.patch.object(claude_sync, "DST", str(destination)),
                mock.patch.object(claude_sync, "load_role_document", return_value=ROLE_DOCUMENT),
                mock.patch.object(claude_sync, "add_role_document_guard"),
            ):
                self.assertEqual(2, claude_sync.main())
            self.assertEqual("user-owned sentinel\n", sentinel.read_text())
            self.assertFalse((destination / "commit-review-openai-writer.md").exists())

    def test_relay_names_are_classified_by_model_family(self):
        self.assertEqual("opus", claude_sync.cc_model(["relay/claude-opus-4-8:xhigh"]))
        self.assertEqual("opus", claude_sync.cc_model(["anthropic/claude-opus-5"]))
        self.assertEqual("claude-fable-5-1", claude_sync.cc_model(["anthropic/claude-fable-5-1"]))
        self.assertIsNone(claude_sync.cc_model(["cursor/claude-opus-5"]))
        self.assertIsNone(claude_sync.cc_model(["cursor/claude-sonnet-5"]))
        self.assertTrue(claude_sync.is_claude_model("anthropic/claude-opus-5"))
        self.assertTrue(claude_sync.is_claude_model("cursor/claude-opus-5"))
        self.assertFalse(claude_sync.is_claude_model("cursor/gpt-5.6-sol"))
        self.assertTrue(codex_sync.is_codex_model("relay/gpt-5.6-sol:high"))

    def test_native_claude_generation_requires_backend_claude_and_provider_anthropic(self):
        path = AGENT_DIR / "cross-review-openai-writer.md"
        meta, _ = agent_roles.parse_frontmatter(path.read_text())
        route = agent_roles.resolve_agent(meta, ROLE_DOCUMENT, str(path))
        self.assertEqual("advisor", route.model_role)
        self.assertEqual("claude", route.role["backend"])
        self.assertEqual("anthropic", route.role["provider"])
        self.assertTrue(claude_sync.provider_compatible(meta, ROLE_DOCUMENT))
        self.assertEqual("opus", claude_sync.cc_model([agent_roles.expected_model_snapshot(route.role)]))

    def test_cursor_hosted_claude_roles_are_omitted_from_claude_generation(self):
        path = AGENT_DIR / "analyzer.md"
        meta, _ = agent_roles.parse_frontmatter(path.read_text())
        route = agent_roles.resolve_agent(meta, ROLE_DOCUMENT, str(path))
        self.assertEqual("anthropic_task", route.model_role)
        self.assertEqual("cursor", route.role["backend"])
        self.assertEqual("cursor", route.role["provider"])
        self.assertEqual("anthropic", route.role["upstreamProviderFamily"])
        self.assertFalse(agent_roles.route_matches_backend(route, "claude"))
        self.assertFalse(claude_sync.provider_compatible(meta, ROLE_DOCUMENT))
        self.assertFalse(codex_sync.provider_compatible(meta, ROLE_DOCUMENT))
        self.assertIsNone(claude_sync.cc_model([agent_roles.expected_model_snapshot(route.role)]))

    def test_claude_generator_omits_cursor_backend_non_claude_models(self):
        document = copy.deepcopy(ROLE_DOCUMENT)
        document["roles"]["cursor_gpt_task"] = {
            "backend": "cursor",
            "provider": "cursor",
            "upstreamProviderFamily": "openai",
            "model": "gpt-5.6-sol",
            "modelFamily": "gpt-5.6-sol",
            "effort": "max",
            "dispatchStatus": "active",
            "intelligenceClass": "analysis",
            "relativeCost": 7,
        }
        meta, _ = claude_sync.parse(
            "---\nname: cursor-gpt-task\ndescription: \"cursor gpt\"\n"
            "model-role: cursor_gpt_task\n---\nBody.\n"
        )
        self.assertFalse(claude_sync.provider_compatible(meta, document))
        self.assertFalse(codex_sync.provider_compatible(meta, document))

    def test_missing_model_role_fails_closed(self):
        meta, _ = agent_roles.parse_frontmatter(AGENT.replace("model-role: codex_advisor\n", ""))
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "missing model-role"):
            agent_roles.resolve_agent(meta, ROLE_DOCUMENT)

    def test_stale_model_role_fails_closed(self):
        meta, _ = agent_roles.parse_frontmatter(AGENT.replace("codex_advisor", "retired_advisor"))
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "unknown model-role"):
            agent_roles.resolve_agent(meta, ROLE_DOCUMENT)

    def test_agent_local_model_snapshot_fails_closed(self):
        meta, _ = agent_roles.parse_frontmatter(
            AGENT.replace("model-role: codex_advisor", "model-role: codex_advisor\nmodel: codex/gpt-stale")
        )
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "model must be omitted"):
            agent_roles.resolve_agent(meta, ROLE_DOCUMENT)

    def test_cross_provider_reviewer_requires_writer_role(self):
        meta, _ = agent_roles.parse_frontmatter(AGENT.replace("writer-role: anthropic_task\n", ""))
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "exactly one"):
            agent_roles.resolve_agent(meta, ROLE_DOCUMENT)

    def test_same_upstream_provider_reviewer_pair_fails_closed(self):
        meta, _ = agent_roles.parse_frontmatter(
            AGENT.replace("writer-role: anthropic_task", "writer-role: task")
        )
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "same upstream provider"):
            agent_roles.resolve_agent(meta, ROLE_DOCUMENT)

    def test_exact_sol_writer_requires_opus_max_reviewer(self):
        meta, _ = agent_roles.parse_frontmatter(CLAUDE_REVIEWER)
        route = agent_roles.resolve_agent(meta, ROLE_DOCUMENT)
        self.assertEqual("advisor", route.model_role)
        self.assertEqual("claude-opus-5", route.role["modelFamily"])
        self.assertEqual("max", route.role["effort"])

        stale = copy.deepcopy(ROLE_DOCUMENT)
        stale["roles"]["advisor"]["effort"] = "xhigh"
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "exact pairing"):
            agent_roles.resolve_agent(meta, stale)

    def test_exact_opus_writer_requires_astra_max_reviewer(self):
        meta, _ = agent_roles.parse_frontmatter(
            AGENT.replace("writer-role: anthropic_task", "writer-role: frontier_execute_anthropic")
        )
        route = agent_roles.resolve_agent(meta, ROLE_DOCUMENT)
        self.assertEqual("codex_advisor", route.model_role)
        self.assertEqual("gpt-6-astra", route.role["modelFamily"])
        self.assertEqual("max", route.role["effort"])

    def test_catalog_candidate_agent_fails_closed(self):
        candidate = AGENT.replace(
            "model-role: codex_advisor",
            "model-role: diversity_kimi",
        ).replace("cross-provider: true\nwriter-role: anthropic_task\n", "")
        meta, _ = agent_roles.parse_frontmatter(candidate)
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "not dispatchable"):
            agent_roles.resolve_agent(meta, ROLE_DOCUMENT)

    def test_reviewer_for_catalog_candidate_writer_fails_closed(self):
        meta, _ = agent_roles.parse_frontmatter(
            AGENT.replace("writer-role: anthropic_task", "writer-role: diversity_kimi")
        )
        with self.assertRaisesRegex(agent_roles.AgentRoleError, "writer-role.*not dispatchable"):
            agent_roles.resolve_agent(meta, ROLE_DOCUMENT)

    def test_runtime_load_fails_closed_on_unverified_bootstrap(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "models.json"
            path.write_text(json.dumps(ROLE_DOCUMENT))
            with self.assertRaisesRegex(agent_roles.AgentRoleError, "bootstrap-unverified"):
                agent_roles.load_role_document(path)

    def test_runtime_load_accepts_complete_authenticated_resolution(self):
        verified = verified_document(ROLE_DOCUMENT)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "models.json"
            path.write_text(json.dumps(verified))
            loaded = agent_roles.load_role_document(path)
            self.assertEqual("verified", loaded["catalogResolution"]["status"])

    def test_runtime_load_rejects_active_role_missing_from_receipt(self):
        verified = verified_document(ROLE_DOCUMENT)
        active = {
            name: role for name, role in verified["roles"].items()
            if role["dispatchStatus"] == "active"
        }
        verified["catalogResolution"]["verifiedRoles"] = sorted(set(active) - {"task"})
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "models.json"
            path.write_text(json.dumps(verified))
            with self.assertRaisesRegex(agent_roles.AgentRoleError, "active role|exactly cover"):
                agent_roles.load_role_document(path)

    def test_all_canonical_agents_resolve_from_role_source(self):
        agents = agent_roles.validate_agent_directory(AGENT_DIR, ROLE_DOCUMENT)
        self.assertEqual(11, len(agents))
        self.assertEqual(
            {
                "analyzer": "anthropic_task",
                "bitlesson-selector": "smol",
                "commit-review-anthropic-writer": "codex_advisor",
                "commit-review-openai-writer": "advisor",
                "cross-review-anthropic-writer": "codex_advisor",
                "cross-review-openai-writer": "advisor",
                "iterator": "anthropic_task",
                "planner": "plan",
                "reward-hacking-review-anthropic-writer": "codex_advisor",
                "reward-hacking-review-openai-writer": "advisor",
                "workflow-updater": "plan",
            },
            {agent.name: agent.model_role for agent in agents},
        )

    def test_reward_hacking_reviewers_have_explicit_opposite_directions(self):
        cases = (
            ("reward-hacking-review-anthropic-writer.md", "anthropic", "codex_advisor", "codex"),
            ("reward-hacking-review-openai-writer.md", "openai", "advisor", "claude"),
        )
        for filename, writer_family, reviewer_role, backend in cases:
            path = AGENT_DIR / filename
            meta, _ = codex_sync.split_frontmatter(path.read_text())
            route = agent_roles.resolve_agent(meta, ROLE_DOCUMENT, str(path))
            self.assertEqual("true", str(meta.get("cross-provider", "")).lower())
            self.assertEqual(writer_family, meta.get("writer-family"))
            self.assertEqual(reviewer_role, meta.get("model-role"))
            self.assertNotIn("model", meta)
            self.assertEqual("read, grep, glob, bash", meta.get("tools"))
            self.assertEqual("false", meta.get("spawns"))
            self.assertEqual(backend, route.role["backend"])

    def test_invalid_role_source_prevents_kimi_config_write(self):
        kimi_sync = load("sync_kimi")
        with mock.patch.object(
            kimi_sync,
            "validate_canonical_agents",
            side_effect=kimi_sync.AgentRoleError("bad"),
        ):
            with mock.patch.dict(
                os.environ,
                {"GENERAL_AGENT_ALLOW_USER_CONFIG_WRITE": "1"},
                clear=True,
            ):
                with mock.patch("builtins.open") as opened:
                    self.assertEqual(2, kimi_sync.main())
        opened.assert_not_called()

    def test_reward_review_prompt_binds_facts_and_task_specific_risk_hypotheses(self):
        path = (
            REPO / ".claude" / "skills" / "subagent-workflow" / "assets" / "project" /
            ".workflow" / "resources" / "standard-round" / "prompts" /
            "reward-hacking-review.md"
        )
        prompt = path.read_text()
        self.assertEqual(
            {
                "task",
                "acceptanceArgv",
                "baselinePath",
                "baselineDigest",
                "baselineSummary",
                "evidence",
                "evidencePath",
                "evidenceDigest",
                "stateBinding",
                "rewardHackingRisks",
            },
            set(re.findall(r"{{(\w+)}}", prompt)),
        )
        lowered = prompt.lower()
        self.assertNotIn("builder report", lowered)
        self.assertNotIn("prior reviewer", lowered)


class DispatchGuidanceTests(unittest.TestCase):
    def test_single_skill_or_agent_work_defaults_to_native_subagent(self):
        authoring = (REPO / ".claude" / "skills" / "write-subagent" / "SKILL.md").read_text()
        self.assertIn("single skill or agent definition", authoring)
        self.assertIn("native-subagent", authoring)

    def test_cross_skill_reviewer_calls_are_actual_writer_aware(self):
        first_principles = (
            REPO / ".claude" / "skills" / "first-principles" / "SKILL.md"
        ).read_text()
        decompose = (
            REPO / ".claude" / "skills" / "default-mode" / "references" / "decompose.md"
        ).read_text()
        bitlesson_gate = (
            REPO / ".claude" / "skills" / "self-improve" / "scripts" /
            "bitlesson_validate_delta.sh"
        ).read_text()

        self.assertIn("`cross-review-openai-writer`", first_principles)
        self.assertIn("`cross-review-anthropic-writer`", first_principles)
        self.assertIn("actual writer semantic role", first_principles)
        self.assertIn("`cross-review-openai-writer`", decompose)
        self.assertIn("actual writer", decompose)
        self.assertNotIn("`cross-reviewer`", first_principles + decompose)
        self.assertNotIn("the commit-reviewer", bitlesson_gate)

    def test_cross_review_assets_use_canonical_identity_reply_fields(self):
        for filename in (
            "cross-review-openai-writer.md",
            "cross-review-anthropic-writer.md",
        ):
            body = (AGENT_DIR / filename).read_text()
            self.assertIn("REVIEWER-ROLE:", body, filename)
            self.assertIn("REVIEWER-IDENTITY-SHA256:", body, filename)
            self.assertNotIn("REVIEWER-PROVIDER", body, filename)


if __name__ == "__main__":
    unittest.main()
