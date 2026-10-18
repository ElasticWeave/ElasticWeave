from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "select_harness", HERE / "select_harness.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

HUMANIZE_SPEC = importlib.util.spec_from_file_location(
    "humanize_safe_mapping",
    HERE.parents[1] / "humanize-workflow/scripts/safe_mapping.py",
)
assert HUMANIZE_SPEC is not None and HUMANIZE_SPEC.loader is not None
HUMANIZE = importlib.util.module_from_spec(HUMANIZE_SPEC)
sys.modules[HUMANIZE_SPEC.name] = HUMANIZE
HUMANIZE_SPEC.loader.exec_module(HUMANIZE)

MODEL_ROLE_POLICY_TEMPLATE = (
    HERE.parents[1]
    / "default-mode"
    / "assets"
    / "project"
    / "config"
    / "workflows"
    / "models.json"
)


def independent_design(design_id: str = "portable") -> dict[str, object]:
    return {
        "schema": "workflow-design/v1",
        "id": design_id,
        "entry": "seed",
        "admission": {
            "reasons": ["multi-stage"],
            "demotionCondition": "Use one native subagent when the dependent stage disappears.",
        },
        "governance": {
            "reviewPolicy": "independent",
            "completionProof": "none",
            "malformedDecision": "unresolved",
        },
        "goal": {"objective": "Produce a result.", "acceptance": "result is present"},
        "budgets": {"maxModelCalls": 1},
        "requiredCapabilities": [
            "agent-invocation",
            "fresh-sessions",
            "structured-output",
            "observability",
        ],
        "state": [
            {"name": "topic", "type": "string"},
            {"name": "result", "type": "string"},
        ],
        "nodes": [
            {"id": "seed", "kind": "script", "reads": [], "writes": ["topic"]},
            {
                "id": "worker",
                "kind": "agent",
                "role": "task",
                "session": "fresh",
                "workspaceAccess": "read",
                "reads": ["topic"],
                "writes": ["result"],
                "timeoutSeconds": 60,
            },
            {
                "id": "success",
                "kind": "terminal",
                "outcome": "success",
                "reads": ["result"],
                "writes": [],
            },
        ],
        "transitions": [
            {"from": "seed", "to": "worker"},
            {"from": "worker", "to": "success"},
        ],
        "terminals": {"success": "success"},
    }


def routed_design(design_id: str = "routed") -> dict[str, object]:
    document = independent_design(design_id)
    document["budgets"] = {"maxModelCalls": 2}
    document["requiredCapabilities"] = [
        "agent-invocation",
        "fresh-sessions",
        "conditional-routing",
        "structured-output",
        "observability",
    ]
    document["nodes"] = [
        document["nodes"][0],
        document["nodes"][1],
        {
            "id": "review",
            "kind": "decision",
            "role": "advisor",
            "session": "fresh",
            "workspaceAccess": "read",
            "reads": ["result"],
            "writes": [],
            "outcomes": ["PASS", "BLOCK"],
            "onMissing": "unresolved",
            "timeoutSeconds": 60,
        },
        document["nodes"][2],
        {
            "id": "unresolved",
            "kind": "terminal",
            "outcome": "unresolved",
            "reads": [],
            "writes": [],
        },
    ]
    document["transitions"] = [
        {"from": "seed", "to": "worker"},
        {"from": "worker", "to": "review"},
        {
            "from": "review",
            "to": "success",
            "when": {"field": "review.verdict", "equals": "PASS"},
        },
        {
            "from": "review",
            "to": "unresolved",
            "when": {"field": "review.verdict", "equals": "BLOCK"},
        },
    ]
    document["terminals"] = {"success": "success", "unresolved": "unresolved"}
    return document


def harness_entry(
    capabilities: dict[str, str],
    *,
    skill: str = "subagent-workflow",
    artifact: str = ".subagent/flows/<design-id>.json",
    review_policies: list[str] | None = None,
    enabled: bool = True,
) -> dict[str, object]:
    explicit_capabilities = {
        capability: "not-supported-v1"
        for capability in MODULE.CANONICAL_CAPABILITIES
    }
    explicit_capabilities.update(capabilities)
    return {
        "enabled": enabled,
        "reviewPolicies": review_policies or ["independent", "machine-gated"],
        "skill": skill,
        "artifact": artifact,
        "install": "built-in",
        "commands": {
            "check": "true",
            "ensure": "true",
            "validate": "validate <artifact>",
            "run": "run <artifact>",
        },
        "capabilities": explicit_capabilities,
    }


def registry(
    harnesses: dict[str, dict[str, object]], *, default: str = "auto"
) -> dict[str, object]:
    return {
        "schema": "workflow-harness-registry/v1",
        "default": default,
        "harnesses": harnesses,
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


OMITTED = object()


ROUTING_CAPABILITIES = {
    "agent-invocation": "native",
    "fresh-sessions": "native",
    "conditional-routing": "native-edge-conditions",
    "structured-output": "native",
    "observability": "native",
}


class SelectHarnessTest(unittest.TestCase):
    def test_humanize_selector_pin_matches_mapping_contract(self) -> None:
        self.assertEqual(
            MODULE.HUMANIZE_UPSTREAM_REPOSITORY,
            HUMANIZE.UPSTREAM_REPOSITORY,
        )
        self.assertEqual(
            MODULE.HUMANIZE_UPSTREAM_REVISION,
            HUMANIZE.UPSTREAM_REVISION,
        )

    def _bundled_registry_and_design(self, name: str):
        root = HERE.parents[1] / "subagent-workflow" / "assets" / "project"
        registry_document = MODULE.load_object(root / ".workflow/harnesses.json")
        design_path = root / f".workflow/designs/{name}.workflow.json"
        design = MODULE.load_object(design_path)
        checks = MODULE.inspect_mappings(design, registry_document, root, design_path)
        return root, registry_document, design, checks

    def _write_design(self, root: Path, document: dict[str, object]) -> Path:
        path = root / ".workflow/designs" / f"{document['id']}.workflow.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return path

    def _write_valid_subagent_mapping(
        self,
        root: Path,
        design_path: Path,
        *,
        report_digest: str | None = None,
        posture: object = "balanced",
        extra: dict[str, object] | None = None,
    ) -> tuple[Path, Path]:
        design_id = design_path.name.removesuffix(".workflow.json")
        digest = sha256(design_path)
        artifact = root / ".subagent/flows" / f"{design_id}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        mapping: dict[str, object] = {
            "schema": "subagent-workflow/v1",
            "designPath": f".workflow/designs/{design_id}.workflow.json",
            "designSha256": digest,
            "inputPath": ".subagent/fixtures/mapped.input.json",
            "reasoningPosture": posture,
            "scheduler": {
                "role": "task",
                "mode": "ambiguous-only",
                "maxCalls": 0,
            },
            "handlers": {},
        }
        if posture is OMITTED:
            del mapping["reasoningPosture"]
        mapping.update(extra or {})
        artifact.write_text(
            json.dumps(mapping, indent=2) + "\n",
            encoding="utf-8",
        )
        report = root / ".workflow/mappings" / f"{design_id}.subagent.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "\n".join(
                [
                    f"# {design_id} mapping",
                    "",
                    f"- Design: `.workflow/designs/{design_id}.workflow.json`",
                    f"- Design SHA-256: `{report_digest or digest}`",
                    f"- Mapping: `.subagent/flows/{design_id}.json`",
                    f"- Mapping SHA-256: `{sha256(artifact)}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return artifact, report

    def _write_canonical_model_policy(self, root: Path) -> Path:
        policy = root / "config" / "workflows" / "models.json"
        policy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MODEL_ROLE_POLICY_TEMPLATE, policy)
        return policy

    def _write_valid_humanize_mapping(
        self, root: Path, design_path: Path, *, with_policy: bool = True
    ) -> tuple[Path, Path, Path]:
        design = json.loads(design_path.read_text(encoding="utf-8"))
        design["state"][0]["source"] = "input"
        design_path.write_text(
            HUMANIZE.canonical_json(design), encoding="utf-8"
        )
        if with_policy:
            self._write_canonical_model_policy(root)
        design_id = str(design["id"])
        spec = {
            "schema": "humanize-workflow-spec/v1",
            "inputPath": f".humanize/inputs/{design_id}.json",
            "initialState": {},
            "prompts": {
                "worker": "Produce the bounded result.",
                "review": "Review the bounded result.",
            },
            "reasoningPosture": "balanced",
        }
        spec_path = root / ".humanize/specs" / f"{design_id}.json"
        flow_path = root / ".humanize/generated-flows" / f"{design_id}.py"
        manifest_path = root / ".humanize/mappings" / f"{design_id}.json"
        validator = root / ".claude/skills/build-workflow/scripts/validate_design.py"
        for path in (spec_path, flow_path, manifest_path, validator):
            path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(HUMANIZE.canonical_json(spec), encoding="utf-8")
        flow_path.write_text(
            HUMANIZE.generate_source(design, spec), encoding="utf-8"
        )
        manifest_path.write_text(
            HUMANIZE.canonical_json(
                HUMANIZE.build_manifest(root, design_path, spec_path, flow_path)
            ),
            encoding="utf-8",
        )
        shutil.copy2(
            HERE.parents[1] / "build-workflow/scripts/validate_design.py",
            validator,
        )
        return spec_path, flow_path, manifest_path

    def test_real_registry_selects_builtin_for_machine_gated_designs(self) -> None:
        for name in ("standard-round", "review-gated-loop"):
            with self.subTest(name=name):
                _root, registry_document, design, checks = (
                    self._bundled_registry_and_design(name)
                )
                result = MODULE.select(
                    design, registry_document, mapping_checks=checks
                )
                self.assertEqual(result["harness"], "subagent-scheduler")
                self.assertEqual(
                    result["rejected"], {"humanize": ["review-policy:machine-gated"]}
                )

    def test_bundled_standard_round_does_not_claim_stateful_continuation(self) -> None:
        _root, _registry_document, design, _checks = (
            self._bundled_registry_and_design("standard-round")
        )
        model_nodes = [
            node
            for node in design["nodes"]
            if node["kind"] in {"agent", "decision"}
        ]
        self.assertTrue(model_nodes)
        self.assertTrue(all(node["session"] == "fresh" for node in model_nodes))
        self.assertNotIn("stateful-continuation", design["requiredCapabilities"])
        self.assertNotIn(
            "stateful-continuation",
            MODULE.DESIGN_CONTRACT.derive_required_capabilities(design),
        )

    def test_bundled_standard_round_mapping_passes_static_verification(self) -> None:
        root, registry_document, design, checks = self._bundled_registry_and_design(
            "standard-round"
        )
        mapping = MODULE.load_object(root / ".subagent/flows/standard-round.json")
        posture = mapping.get("reasoningPosture")
        self.assertIsInstance(posture, str)
        self.assertTrue(str(posture).strip())
        self.assertEqual(checks["subagent-scheduler"]["issues"], [])
        self.assertEqual(checks["subagent-scheduler"]["status"], "valid")
        result = MODULE.select(design, registry_document, mapping_checks=checks)
        self.assertTrue(result["mappingExists"])
        self.assertFalse(result["mappingRequired"])

    def test_every_real_bundled_subagent_mapping_is_valid(self) -> None:
        root = HERE.parents[1] / "subagent-workflow" / "assets" / "project"
        registry_document = MODULE.load_object(root / ".workflow/harnesses.json")
        design_paths = sorted((root / ".workflow/designs").glob("*.workflow.json"))
        self.assertTrue(design_paths)
        for design_path in design_paths:
            name = design_path.name.removesuffix(".workflow.json")
            with self.subTest(name=name):
                design = MODULE.load_object(design_path)
                artifact = root / ".subagent/flows" / f"{name}.json"
                report = root / ".workflow/mappings" / f"{name}.subagent.md"
                design_digest = sha256(design_path)
                artifact_digest = sha256(artifact)
                report_text = report.read_text(encoding="utf-8")
                self.assertIn(f"`.workflow/designs/{name}.workflow.json`", report_text)
                self.assertIn(f"`{design_digest}`", report_text)
                self.assertIn(f"`.subagent/flows/{name}.json`", report_text)
                self.assertIn(f"`{artifact_digest}`", report_text)
                checks = MODULE.inspect_mappings(
                    design, registry_document, root, design_path
                )
                verification = checks["subagent-scheduler"]
                self.assertEqual(verification["issues"], [])
                self.assertEqual(verification["status"], "valid")
                result = MODULE.select(
                    design, registry_document, mapping_checks=checks
                )
                self.assertTrue(result["mappingExists"])
                self.assertFalse(result["mappingRequired"])

    def test_mapping_requires_declared_posture_and_still_rejects_unknown_keys(self) -> None:
        registry_document = registry(
            {"subagent-scheduler": harness_entry(ROUTING_CAPABILITIES)},
            default="subagent-scheduler",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design = routed_design("postured")
            design_path = self._write_design(root, design)

            self._write_valid_subagent_mapping(root, design_path)
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertEqual(checks["subagent-scheduler"]["issues"], [])

            for posture in (OMITTED, "", "   ", None, 1, True, ["balanced"]):
                with self.subTest(posture=posture):
                    self._write_valid_subagent_mapping(
                        root, design_path, posture=posture
                    )
                    checks = MODULE.inspect_mappings(
                        design, registry_document, root, design_path
                    )
                    self.assertIn(
                        "mapping-reasoning-posture",
                        checks["subagent-scheduler"]["issues"],
                    )
                    result = MODULE.select(
                        design, registry_document, mapping_checks=checks
                    )
                    self.assertFalse(result["mappingExists"])
                    self.assertTrue(result["mappingRequired"])

            self._write_valid_subagent_mapping(
                root, design_path, extra={"reasoningEffort": "max"}
            )
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertIn(
                "mapping-unknown-keys", checks["subagent-scheduler"]["issues"]
            )
            self.assertNotIn(
                "mapping-reasoning-posture", checks["subagent-scheduler"]["issues"]
            )

    def test_real_registry_keeps_humanize_eligible_only_for_independent_designs(self) -> None:
        _root, registry_document, _design, _checks = self._bundled_registry_and_design(
            "standard-round"
        )
        result = MODULE.select(
            routed_design("humanize-sequence"),
            registry_document,
            requested="humanize",
        )
        self.assertEqual(result["harness"], "humanize")
        self.assertFalse(result["mappingExists"])
        self.assertTrue(result["mappingRequired"])

    def test_explicit_pin_never_falls_back(self) -> None:
        registry_document = registry(
            {
                "light": harness_entry(
                    {**ROUTING_CAPABILITIES, "durable-checkpoints": "not-supported-v1"}
                ),
                "graph": harness_entry(
                    {**ROUTING_CAPABILITIES, "durable-checkpoints": "native"}
                ),
            },
            default="graph",
        )
        design = routed_design("resume")
        design["requiredCapabilities"].append("durable-checkpoints")
        with self.assertRaisesRegex(ValueError, "light: durable-checkpoints"):
            MODULE.select(design, registry_document, requested="light")

    def test_humanize_pin_rejects_even_single_wait_for_dependency(self) -> None:
        registry_document = registry(
            {
                "humanize": harness_entry(
                    ROUTING_CAPABILITIES,
                    skill="humanize-workflow",
                    artifact=".humanize/mappings/<design-id>.json",
                    review_policies=["independent"],
                )
            },
            default="humanize",
        )
        design = routed_design("wait-for-one")
        design["nodes"][2]["waitFor"] = ["worker"]
        design["requiredCapabilities"].append("fanout-fanin")
        with self.assertRaisesRegex(ValueError, "humanize: fanout-fanin"):
            MODULE.select(design, registry_document, requested="humanize")

    def test_auto_prefers_configured_compatible_default(self) -> None:
        registry_document = registry(
            {
                "nativegraph": harness_entry(ROUTING_CAPABILITIES),
                "pythonhost": harness_entry(
                    {
                        **ROUTING_CAPABILITIES,
                        "conditional-routing": "adapter-host-python",
                    },
                    skill="humanize-workflow",
                    artifact=".humanize/mappings/<design-id>.json",
                    review_policies=["independent"],
                ),
            },
            default="pythonhost",
        )
        result = MODULE.select(routed_design(), registry_document)
        self.assertEqual(result["harness"], "pythonhost")
        self.assertTrue(result["defaultApplied"])

    def test_registry_requires_schema_and_review_policy_allowlist(self) -> None:
        missing_schema = registry({"graph": harness_entry(ROUTING_CAPABILITIES)})
        del missing_schema["schema"]
        with self.assertRaisesRegex(ValueError, "registry.schema"):
            MODULE.select(routed_design(), missing_schema)

        missing_policies = registry({"graph": harness_entry(ROUTING_CAPABILITIES)})
        del missing_policies["harnesses"]["graph"]["reviewPolicies"]
        with self.assertRaisesRegex(ValueError, "reviewPolicies"):
            MODULE.select(routed_design(), missing_policies)

    def test_registry_rejects_unknown_capability_and_arbitrary_status(self) -> None:
        for capabilities, expected in (
            ({**ROUTING_CAPABILITIES, "telepathy": "native"}, "unknown capabilities"),
            ({**ROUTING_CAPABILITIES, "observability": "sure-works"}, "status"),
        ):
            with self.subTest(capabilities=capabilities):
                with self.assertRaisesRegex(ValueError, expected):
                    MODULE.select(
                        routed_design(),
                        registry({"graph": harness_entry(capabilities)}),
                    )

        missing = harness_entry(ROUTING_CAPABILITIES)
        del missing["capabilities"]["bounded-loops"]
        with self.assertRaisesRegex(ValueError, "omits explicit capability statuses"):
            MODULE.select(routed_design(), registry({"graph": missing}))

    def test_registry_rejects_malformed_entry_paths_and_commands(self) -> None:
        unsafe = harness_entry(ROUTING_CAPABILITIES)
        unsafe["artifact"] = "../outside/<design-id>.json"
        with self.assertRaisesRegex(ValueError, "artifact"):
            MODULE.select(routed_design(), registry({"graph": unsafe}))

        commands = harness_entry(ROUTING_CAPABILITIES)
        del commands["commands"]["validate"]
        with self.assertRaisesRegex(ValueError, "commands"):
            MODULE.select(routed_design(), registry({"graph": commands}))

        unbound = harness_entry(ROUTING_CAPABILITIES)
        unbound["commands"]["run"] = "run"
        with self.assertRaisesRegex(ValueError, "reference <artifact>"):
            MODULE.select(routed_design(), registry({"graph": unbound}))

    def test_design_unknown_or_structure_missing_capability_fails_closed(self) -> None:
        unknown = routed_design()
        unknown["requiredCapabilities"].append("telepathy")
        with self.assertRaisesRegex(ValueError, "non-canonical"):
            MODULE.select(
                unknown, registry({"graph": harness_entry(ROUTING_CAPABILITIES)})
            )

        missing = routed_design()
        missing["requiredCapabilities"].remove("conditional-routing")
        with self.assertRaisesRegex(ValueError, "derived capabilities"):
            MODULE.select(
                missing, registry({"graph": harness_entry(ROUTING_CAPABILITIES)})
            )

    def test_mapping_requires_valid_schema_design_digest_and_report(self) -> None:
        registry_document = registry(
            {"subagent-scheduler": harness_entry(ROUTING_CAPABILITIES)},
            default="subagent-scheduler",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design = routed_design("mapped")
            design_path = self._write_design(root, design)
            artifact, report = self._write_valid_subagent_mapping(root, design_path)

            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            result = MODULE.select(
                design, registry_document, mapping_checks=checks
            )
            self.assertTrue(result["mappingExists"])
            self.assertEqual(result["mappingVerification"]["status"], "valid")

            artifact.write_text("{}\n", encoding="utf-8")
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertEqual(checks["subagent-scheduler"]["status"], "invalid")
            self.assertIn("mapping-schema", checks["subagent-scheduler"]["issues"])
            result = MODULE.select(
                design, registry_document, mapping_checks=checks
            )
            self.assertFalse(result["mappingExists"])
            self.assertTrue(result["mappingRequired"])

            self._write_valid_subagent_mapping(root, design_path)
            report.unlink()
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertIn("mapping-report-missing", checks["subagent-scheduler"]["issues"])

            self._write_valid_subagent_mapping(root, design_path, report_digest="0" * 64)
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertIn("mapping-report-design-digest", checks["subagent-scheduler"]["issues"])

            artifact, report = self._write_valid_subagent_mapping(root, design_path)
            report.write_text(
                report.read_text(encoding="utf-8").replace(
                    f"- Mapping SHA-256: `{sha256(artifact)}`\n", ""
                ),
                encoding="utf-8",
            )
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertIn(
                "mapping-report-artifact-digest",
                checks["subagent-scheduler"]["issues"],
            )

            artifact, _report = self._write_valid_subagent_mapping(root, design_path)
            mapping = json.loads(artifact.read_text(encoding="utf-8"))
            mapping["inputPath"] = "../outside.json"
            artifact.write_text(
                json.dumps(mapping, indent=2) + "\n", encoding="utf-8"
            )
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertIn("mapping-input-path", checks["subagent-scheduler"]["issues"])

    def test_unicode_escaped_legacy_mapping_report_fields_remain_supported(self) -> None:
        registry_document = registry(
            {"subagent-scheduler": harness_entry(ROUTING_CAPABILITIES)},
            default="subagent-scheduler",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design = routed_design("legacy-report")
            design_path = self._write_design(root, design)
            artifact, report = self._write_valid_subagent_mapping(root, design_path)
            design_relative = ".workflow/designs/legacy-report.workflow.json"
            artifact_relative = ".subagent/flows/legacy-report.json"
            report.write_text(
                "\n".join(
                    [
                        "# legacy mapping",
                        "",
                        f"- \u8bbe\u8ba1\uff1a `{design_relative}`",
                        f"- \u8bbe\u8ba1 SHA-256\uff1a `{sha256(design_path)}`",
                        f"- \u6620\u5c04\uff1a `{artifact_relative}`",
                        f"- \u6620\u5c04 SHA-256\uff1a `{sha256(artifact)}`",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )

            self.assertEqual("valid", checks["subagent-scheduler"]["status"])

    def test_mapping_report_rejects_leaf_symlink(self) -> None:
        registry_document = registry(
            {"subagent-scheduler": harness_entry(ROUTING_CAPABILITIES)},
            default="subagent-scheduler",
        )
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside_directory:
            root = Path(directory)
            design = routed_design("linked-report")
            design_path = self._write_design(root, design)
            _artifact, report = self._write_valid_subagent_mapping(root, design_path)
            outside = Path(outside_directory) / "linked-report.subagent.md"
            expected = report.read_bytes()
            outside.write_bytes(expected)
            report.unlink()
            report.symlink_to(outside)

            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )

            self.assertEqual(checks["subagent-scheduler"]["status"], "invalid")
            self.assertTrue(
                any(
                    "symlink" in issue
                    for issue in checks["subagent-scheduler"]["issues"]
                )
            )
            self.assertEqual(outside.read_bytes(), expected)

    def test_mapping_report_rejects_symlinked_parent_directory(self) -> None:
        registry_document = registry(
            {"subagent-scheduler": harness_entry(ROUTING_CAPABILITIES)},
            default="subagent-scheduler",
        )
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside_directory:
            root = Path(directory)
            design = routed_design("linked-report-parent")
            design_path = self._write_design(root, design)
            _artifact, report = self._write_valid_subagent_mapping(root, design_path)
            outside_parent = Path(outside_directory) / "mappings"
            outside_parent.mkdir()
            outside = outside_parent / report.name
            expected = report.read_bytes()
            outside.write_bytes(expected)
            report.unlink()
            report.parent.rmdir()
            report.parent.symlink_to(outside_parent, target_is_directory=True)

            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )

            self.assertEqual(checks["subagent-scheduler"]["status"], "invalid")
            self.assertTrue(
                any(
                    "symlink" in issue
                    for issue in checks["subagent-scheduler"]["issues"]
                )
            )
            self.assertEqual(outside.read_bytes(), expected)

    def test_mapping_report_rejects_duplicate_canonical_fields(self) -> None:
        registry_document = registry(
            {"subagent-scheduler": harness_entry(ROUTING_CAPABILITIES)},
            default="subagent-scheduler",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design = routed_design("duplicate-report")
            design_path = self._write_design(root, design)
            _artifact, report = self._write_valid_subagent_mapping(root, design_path)
            report.write_text(
                report.read_text(encoding="utf-8")
                + "\n".join(
                    [
                        "- Design: `.workflow/designs/other.workflow.json`",
                        f"- Design SHA-256: `{'0' * 64}`",
                        "- Mapping: `.subagent/flows/other.json`",
                        f"- Mapping SHA-256: `{'f' * 64}`",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )

            self.assertEqual(checks["subagent-scheduler"]["status"], "invalid")
            self.assertIn(
                "mapping-report-duplicate-fields",
                checks["subagent-scheduler"]["issues"],
            )

    def test_humanize_selector_runs_full_deterministic_mapping_validation(self) -> None:
        registry_document = registry(
            {
                "humanize": harness_entry(
                    ROUTING_CAPABILITIES,
                    skill="humanize-workflow",
                    artifact=".humanize/mappings/<design-id>.json",
                    review_policies=["independent"],
                )
            },
            default="humanize",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design = routed_design("humanized")
            design_path = self._write_design(root, design)
            spec_path, flow_path, manifest_path = self._write_valid_humanize_mapping(
                root, design_path
            )
            design = MODULE.load_object(design_path)
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertEqual(checks["humanize"]["status"], "valid")

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            execution_marker = root / "forged-flow-executed"
            flow_path.write_text(
                "from pathlib import Path\n"
                f"Path({str(execution_marker)!r}).write_text('executed')\n",
                encoding="utf-8",
            )
            manifest["artifactSha256"] = sha256(flow_path)
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertEqual(checks["humanize"]["status"], "invalid")
            self.assertTrue(
                any(
                    "deterministic adapter generation" in issue
                    for issue in checks["humanize"]["issues"]
                )
            )
            self.assertFalse(execution_marker.exists())

            valid_spec = {
                "schema": "humanize-workflow-spec/v1",
                "inputPath": ".humanize/inputs/humanized.json",
                "initialState": {},
                "prompts": {
                    "worker": "Produce the bounded result.",
                    "review": "Review the bounded result.",
                },
            }
            flow_path.write_text(
                HUMANIZE.generate_source(design, valid_spec), encoding="utf-8"
            )
            spec_path.write_text("{}\n", encoding="utf-8")
            manifest["artifactSha256"] = sha256(flow_path)
            manifest["specSha256"] = sha256(spec_path)
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertEqual(checks["humanize"]["status"], "invalid")
            self.assertTrue(
                any(
                    "spec.schema must be humanize-workflow-spec/v1" in issue
                    for issue in checks["humanize"]["issues"]
                )
            )

    def test_humanize_mapping_fails_closed_without_canonical_model_policy(self) -> None:
        registry_document = registry(
            {
                "humanize": harness_entry(
                    ROUTING_CAPABILITIES,
                    skill="humanize-workflow",
                    artifact=".humanize/mappings/<design-id>.json",
                    review_policies=["independent"],
                )
            },
            default="humanize",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            design = routed_design("policyless")
            design_path = self._write_design(root, design)

            with self.assertRaisesRegex(
                HUMANIZE.MappingError, "canonical model role policy"
            ):
                self._write_valid_humanize_mapping(
                    root, design_path, with_policy=False
                )

            self._write_valid_humanize_mapping(root, design_path)
            design = MODULE.load_object(design_path)
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertEqual(checks["humanize"]["status"], "valid")

            (root / "config" / "workflows" / "models.json").unlink()
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertEqual(checks["humanize"]["status"], "invalid")
            self.assertTrue(
                any(
                    "canonical model role policy" in issue
                    for issue in checks["humanize"]["issues"]
                )
            )

    def test_mapping_paths_reject_symlink_escape_and_symlink_aliases(self) -> None:
        registry_document = registry(
            {"subagent-scheduler": harness_entry(ROUTING_CAPABILITIES)},
            default="subagent-scheduler",
        )
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside_directory:
            root = Path(directory)
            design = routed_design("linked")
            design_path = self._write_design(root, design)
            outside = Path(outside_directory) / "linked.json"
            outside.write_text("{}\n", encoding="utf-8")
            flows = root / ".subagent/flows"
            flows.mkdir(parents=True)
            (flows / "linked.json").symlink_to(outside)
            checks = MODULE.inspect_mappings(
                design, registry_document, root, design_path
            )
            self.assertEqual(checks["subagent-scheduler"]["status"], "invalid")
            self.assertTrue(
                any("symlink" in issue for issue in checks["subagent-scheduler"]["issues"])
            )

            link = root / "design-link.workflow.json"
            link.symlink_to(design_path)
            with self.assertRaisesRegex(ValueError, "symlink"):
                MODULE.inspect_mappings(design, registry_document, root, link)

    def test_cli_uses_bundled_fallback_from_foreign_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory, tempfile.TemporaryDirectory() as foreign_directory:
            root = Path(project_directory)
            design_path = self._write_design(root, independent_design("fallback"))
            completed = subprocess.run(
                [sys.executable, str(HERE / "select_harness.py"), str(design_path)],
                cwd=foreign_directory,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["registrySource"], "bundled")
            self.assertEqual(result["harness"], "subagent-scheduler")
            self.assertTrue(result["mappingRequired"])

    def test_malformed_project_override_fails_without_bundled_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory, tempfile.TemporaryDirectory() as foreign_directory:
            root = Path(project_directory)
            design_path = self._write_design(root, independent_design("bad-override"))
            registry_path = root / ".workflow/harnesses.json"
            registry_path.write_text("{}\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(HERE / "select_harness.py"), str(design_path)],
                cwd=foreign_directory,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("registry.schema", completed.stderr)

    def test_cli_rejects_symlinked_design_and_project_registry(self) -> None:
        with tempfile.TemporaryDirectory() as project_directory, tempfile.TemporaryDirectory() as outside_directory:
            root = Path(project_directory)
            design_path = self._write_design(root, independent_design("real-design"))
            design_link = design_path.with_name("linked-design.workflow.json")
            design_link.symlink_to(design_path)
            completed = subprocess.run(
                [sys.executable, str(HERE / "select_harness.py"), str(design_link)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("design path must not traverse a symlink", completed.stderr)

            registry_path = root / ".workflow/harnesses.json"
            outside_registry = Path(outside_directory) / "harnesses.json"
            outside_registry.write_text(
                json.dumps(
                    registry(
                        {"subagent-scheduler": harness_entry(ROUTING_CAPABILITIES)},
                        default="subagent-scheduler",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            registry_path.symlink_to(outside_registry)
            completed = subprocess.run(
                [sys.executable, str(HERE / "select_harness.py"), str(design_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("registry path must not traverse a symlink", completed.stderr)


if __name__ == "__main__":
    unittest.main()
