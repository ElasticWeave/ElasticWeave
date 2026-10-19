from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import jsonschema


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
ROLE_TEMPLATE = REPO / ".claude" / "skills" / "default-mode" / "assets" / "project" / "config" / "workflows" / "models.json"
RESOLVER_PATH = HERE / "resolve_launch_spec.py"
RECEIPT_SCHEMA_PATH = HERE / "native_launch_receipt.schema.json"
SPEC = importlib.util.spec_from_file_location("resolve_launch_spec", RESOLVER_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

LIVE_CATALOG_SPEC = importlib.util.spec_from_file_location("live_catalog_for_tests", HERE / "live_catalog.py")
assert LIVE_CATALOG_SPEC is not None and LIVE_CATALOG_SPEC.loader is not None
LIVE_CATALOG = importlib.util.module_from_spec(LIVE_CATALOG_SPEC)
sys.modules[LIVE_CATALOG_SPEC.name] = LIVE_CATALOG
LIVE_CATALOG_SPEC.loader.exec_module(LIVE_CATALOG)


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def codex_catalog_bytes(slug_levels: dict[str, list[str]]) -> bytes:
    return (
        json.dumps(
            {
                "models": [
                    {
                        "slug": slug,
                        "default_reasoning_level": levels[0] if levels else "low",
                        "supported_reasoning_levels": [{"effort": e} for e in levels],
                    }
                    for slug, levels in slug_levels.items()
                ]
            }
        ).encode("utf-8")
        + b"\n"
    )


def cursor_catalog_bytes(ids: list[str], *, with_tip: bool = True) -> bytes:
    body = "Available models\n\n" + "".join(f"{i} - {i.upper()}\n" for i in ids)
    if with_tip:
        body += LIVE_CATALOG.CURSOR_TIP_TRAILER + "\n"
    return body.encode("utf-8")


def claude_catalog_bytes(models: dict[str, list[str]]) -> bytes:
    return (
        json.dumps(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": "models",
                    "response": {
                        "models": [
                            {
                                "value": model,
                                "resolvedModel": model + "[1m]",
                                "supportedEffortLevels": efforts,
                            }
                            for model, efforts in models.items()
                        ]
                    },
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


CODEX_FIXTURE = codex_catalog_bytes(
    {
        "gpt-5.6-luna": ["low", "medium", "high", "xhigh", "max"],
        "gpt-5.6-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
        "gpt-6-astra": ["low", "medium", "high", "xhigh", "max", "ultra"],
    }
)
CURSOR_FIXTURE = cursor_catalog_bytes(
    [
        "claude-fable-5-1-thinking-max",
        "claude-fable-5-1-thinking-xhigh",
        "claude-opus-5-thinking-max",
        "claude-opus-5-thinking-xhigh",
        "claude-sonnet-5-thinking-high",
        "cursor-grok-4.6-high",
        "cursor-grok-4.6-xhigh",
        "gpt-5.6-sol-max",
        "gpt-5.6-luna-max",
    ]
)
CLAUDE_FIXTURE = claude_catalog_bytes(
    {
        "claude-opus-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-sonnet-5": ["low", "medium", "high", "xhigh", "max"],
    }
)


def static_runner(argv: tuple[str, ...]) -> tuple[int, bytes, bytes]:
    if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["codex"]:
        return 0, CODEX_FIXTURE, b""
    if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["cursor"]:
        return 0, CURSOR_FIXTURE, b""
    if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["claude"]:
        return 0, CLAUDE_FIXTURE, b""
    raise AssertionError(f"unexpected catalog argv: {argv!r}")


def verified_document() -> dict:
    document = json.loads(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    active = {
        name: role
        for name, role in document["roles"].items()
        if role["dispatchStatus"] == "active"
    }
    backends = sorted({role["backend"] for role in active.values()})
    document["catalogResolution"].update(
        status="verified",
        verifiedBackends=backends,
        verifiedRoles=sorted(active),
        evidenceReceipt={
            "schema": "workflow-model-catalog-evidence/v1",
            "assurance": "caller-supplied-structural-evidence-not-live-authentication",
            "backendCatalogSha256": {
                backend: sha256(f"authenticated {backend} catalog".encode())
                for backend in backends
            },
            "verifiedRoleIdentitiesSha256": "pending",
        },
    )
    document["catalogResolution"]["evidenceReceipt"]["verifiedRoleIdentitiesSha256"] = MODULE.canonical_sha256(
        MODULE.resolved_role_identities(document["roles"], sorted(active))
    )
    return document


def encoded(document: dict, *, indent: int | None = 2) -> bytes:
    return (json.dumps(document, indent=indent, sort_keys=True) + "\n").encode()


class LaunchSpecResolverTest(unittest.TestCase):
    def resolve(
        self,
        document: dict,
        *,
        task_class: str = "analysis",
        posture: str = "balanced",
        semantic_role: str = "task",
        review_mode: str = "none",
        writer_role: str | None = None,
        raw: bytes | None = None,
        runner=None,
    ) -> dict:
        return MODULE.resolve_launch_spec(
            encoded(document) if raw is None else raw,
            task_class=task_class,
            reasoning_posture=posture,
            semantic_role=semantic_role,
            review_mode=review_mode,
            writer_role=writer_role,
            catalog_runner=runner if runner is not None else static_runner,
        )

    def test_cursor_fixture_includes_exact_terminal_tip_trailer(self) -> None:
        self.assertTrue(CURSOR_FIXTURE.decode("utf-8").endswith(
            LIVE_CATALOG.CURSOR_TIP_TRAILER + "\n"
        ))

    def test_fresh_catalog_authentication_is_required_and_bound(self) -> None:
        document = verified_document()
        raw = encoded(document)
        receipt = self.resolve(document, raw=raw)
        self.assertEqual("workflow-native-launch-receipt/v1", receipt["schema"])
        self.assertEqual("codex", receipt["resolved"]["backend"])
        self.assertEqual("gpt-6-astra", receipt["resolved"]["model"])
        self.assertEqual("gpt-6-astra", receipt["resolved"]["catalogModelId"])
        self.assertEqual(sha256(raw), receipt["policy"]["modelsDocumentSha256"])
        self.assertTrue(receipt["policy"]["liveCatalogAuthenticatedByResolver"])
        self.assertIn("codex", receipt["policy"]["freshCatalogEvidence"])
        self.assertEqual(
            ["codex", "debug", "models"],
            receipt["policy"]["freshCatalogEvidence"]["codex"]["command"]["argv"],
        )
        # The receipt binds the canonical executable path and digest for the
        # fresh catalog command, plus the raw catalog and stderr digests.
        codex_evidence = receipt["policy"]["freshCatalogEvidence"]["codex"]
        self.assertIn("executable", codex_evidence["command"])
        self.assertIn("executableSha256", codex_evidence["command"])
        self.assertEqual(sha256(CODEX_FIXTURE), codex_evidence["catalogBytesSha256"])
        self.assertEqual("sha256:" + hashlib.sha256(b"").hexdigest(), codex_evidence["stderrSha256"])
        # catalogSource is a fixed constant: this resolver IS the authenticated
        # native CLI path regardless of what the persisted snapshot claims.
        self.assertEqual("authenticated-native-cli", receipt["policy"]["catalogSource"])
        self.assertEqual(receipt, self.resolve(document, raw=raw))
        self.assertRegex(receipt["receiptSha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("parentModel", json.dumps(receipt))

    def test_persisted_receipt_schema_names_the_full_identity_and_policy_binding(self) -> None:
        schema = json.loads(RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            "https://humanfia.example/schemas/workflow-native-launch-receipt-v1.json",
            schema["$id"],
        )
        self.assertEqual(
            set(MODULE.IDENTITY_FIELDS) | {"semanticRole", "catalogModelId"},
            set(schema["$defs"]["identity"]["required"]),
        )
        policy_required = set(schema["properties"]["policy"]["required"])
        self.assertIn("modelsDocumentSha256", policy_required)
        self.assertIn("catalogResolutionSha256", policy_required)
        self.assertIn("freshCatalogEvidence", policy_required)
        self.assertEqual(True, schema["properties"]["policy"]["properties"]["liveCatalogAuthenticatedByResolver"]["const"])

    def test_emitted_receipt_validates_against_draft_2020_12_schema(self) -> None:
        document = verified_document()
        schema = json.loads(RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        receipts = [
            self.resolve(document),
            self.resolve(
                document,
                task_class="verification",
                posture="balanced",
                semantic_role="advisor",
                review_mode="exact-opposite",
                writer_role="frontier_execute",
            ),
        ]
        for receipt in receipts:
            with self.subTest(backend=receipt["resolved"]["backend"]):
                jsonschema.Draft202012Validator(schema).validate(receipt)

    def test_raw_document_digest_changes_when_only_file_bytes_change(self) -> None:
        document = verified_document()
        compact = encoded(document, indent=None)
        pretty = encoded(document, indent=2)
        compact_receipt = self.resolve(document, raw=compact)
        pretty_receipt = self.resolve(document, raw=pretty)
        self.assertNotEqual(
            compact_receipt["policy"]["modelsDocumentSha256"],
            pretty_receipt["policy"]["modelsDocumentSha256"],
        )

    def test_task_class_and_posture_are_machine_enforced(self) -> None:
        document = verified_document()
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "not qualified for taskClass"):
            self.resolve(document, task_class="collection", semantic_role="task")
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "exceeds.*economy"):
            self.resolve(document, posture="economy", semantic_role="task")
        self.resolve(document, posture="balanced", semantic_role="frontier_execute", task_class="topIntelligence")
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "exceeds.*balanced"):
            self.resolve(
                document,
                posture="balanced",
                semantic_role="frontier_execute_anthropic",
                task_class="topIntelligence",
            )
        self.resolve(
            document,
            posture="frontier",
            semantic_role="frontier_execute_anthropic",
            task_class="topIntelligence",
        )

    def test_top_intelligence_has_dedicated_xhigh_roles(self) -> None:
        document = verified_document()
        qualified = set(document["capabilityPolicy"]["taskClasses"]["topIntelligence"]["qualifiedRoles"])
        self.assertEqual(
            {
                "frontier_execute",
                "frontier_execute_xhigh",
                "frontier_execute_anthropic",
                "frontier_execute_anthropic_xhigh",
            },
            qualified,
        )
        self.assertNotIn("slow", qualified)
        for role_name in ("frontier_execute_xhigh", "frontier_execute_anthropic_xhigh"):
            self.assertEqual("xhigh", document["roles"][role_name]["effort"])
            self.assertEqual("topIntelligence", document["roles"][role_name]["intelligenceClass"])

    def test_fable_and_opus_advice_routes_resolve_exact_live_models(self) -> None:
        document = verified_document()
        opus = self.resolve(
            document,
            task_class="advice",
            posture="balanced",
            semantic_role="frontier_advice",
        )
        fable = self.resolve(
            document,
            task_class="advice",
            posture="frontier",
            semantic_role="frontier_advice_fable",
        )
        self.assertEqual("claude-opus-5-thinking-max", opus["resolved"]["catalogModelId"])
        self.assertEqual("claude-fable-5-1-thinking-max", fable["resolved"]["catalogModelId"])
        self.assertEqual("frontier_advice_fable", fable["resolved"]["semanticRole"])

    def test_highest_available_policy_rejects_a_lower_cursor_effort(self) -> None:
        document = verified_document()
        document["roles"]["frontier_advice_fable"]["effort"] = "xhigh"
        active = sorted(
            name for name, role in document["roles"].items()
            if role["dispatchStatus"] == "active"
        )
        document["catalogResolution"]["evidenceReceipt"]["verifiedRoleIdentitiesSha256"] = (
            MODULE.canonical_sha256(MODULE.resolved_role_identities(document["roles"], active))
        )
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "highest available effort is 'max'"):
            self.resolve(
                document,
                task_class="advice",
                posture="frontier",
                semantic_role="frontier_advice_fable",
            )

    def test_highest_available_policy_accepts_xhigh_when_max_is_absent(self) -> None:
        document = verified_document()
        document["roles"]["diversity_grok"]["effort"] = "xhigh"

        def no_max_grok(argv: tuple[str, ...]) -> tuple[int, bytes, bytes]:
            if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["cursor"]:
                return 0, cursor_catalog_bytes(
                    ["cursor-grok-4.6-high", "cursor-grok-4.6-xhigh"]
                ), b""
            return static_runner(argv)

        receipt = self.resolve(
            document,
            task_class="diversityReview",
            posture="frontier",
            semantic_role="diversity_grok",
            runner=no_max_grok,
        )
        self.assertEqual("xhigh", receipt["resolved"]["effort"])
        self.assertEqual("cursor-grok-4.6-xhigh", receipt["resolved"]["catalogModelId"])

    def test_catalog_candidates_future_and_excluded_roles_fail_closed(self) -> None:
        document = verified_document()
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "not dispatchable"):
            self.resolve(document, task_class="diversityReview", posture="frontier", semantic_role="diversity_kimi")
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "future candidate"):
            self.resolve(document, task_class="diversityReview", posture="frontier", semantic_role="diversity_deepseek")
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "excluded"):
            self.resolve(document, task_class="diversityReview", posture="frontier", semantic_role="diversity_composer")

    def test_review_mode_requires_and_enforces_exact_opposite_pair(self) -> None:
        document = verified_document()
        receipt = self.resolve(
            document,
            task_class="verification",
            posture="balanced",
            semantic_role="advisor",
            review_mode="exact-opposite",
            writer_role="frontier_execute",
        )
        self.assertEqual("anthropic", receipt["resolved"]["upstreamProviderFamily"])
        self.assertEqual("claude", receipt["resolved"]["backend"])
        self.assertEqual("anthropic", receipt["resolved"]["provider"])
        self.assertEqual("max", receipt["resolved"]["effort"])
        self.assertEqual("claude-opus-5", receipt["resolved"]["catalogModelId"])
        self.assertEqual("frontier_execute", receipt["writer"]["semanticRole"])
        evidence = receipt["policy"]["freshCatalogEvidence"]
        self.assertIn("codex", evidence)
        self.assertIn("claude", evidence)
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "requires writerRole"):
            self.resolve(document, task_class="verification", semantic_role="advisor", review_mode="exact-opposite")
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "exact reviewer.*codex_advisor"):
            self.resolve(
                document,
                task_class="verification",
                semantic_role="advisor",
                review_mode="exact-opposite",
                writer_role="frontier_execute_anthropic",
            )

    def test_economy_writer_still_resolves_opposite_max_terminal_reviewer(self) -> None:
        document = verified_document()
        receipt = self.resolve(
            document,
            task_class="verification",
            posture="economy",
            semantic_role="codex_advisor",
            review_mode="exact-opposite",
            writer_role="research_collect_anthropic",
        )
        self.assertEqual("openai", receipt["resolved"]["upstreamProviderFamily"])
        self.assertEqual("max", receipt["resolved"]["effort"])
        self.assertEqual("anthropic", receipt["writer"]["upstreamProviderFamily"])
        self.assertEqual("claude-sonnet-5-thinking-high", receipt["writer"]["catalogModelId"])

    def test_cli_requires_semantic_role_posture_task_class_and_review_mode(self) -> None:
        document = verified_document()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "models.json"
            path.write_bytes(encoded(document))
            process = subprocess.run(
                [sys.executable, str(RESOLVER_PATH), "--models", str(path)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(2, process.returncode)
        self.assertIn("--task-class", process.stderr)
        self.assertIn("--reasoning-posture", process.stderr)
        self.assertIn("--semantic-role", process.stderr)
        self.assertIn("--review-mode", process.stderr)

    def test_cli_persists_a_new_receipt_without_overwriting_existing_evidence(self) -> None:
        document = verified_document()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "models.json"
            output = root / "launch-receipt.json"
            path.write_bytes(encoded(document))
            command = [
                "--models", str(path),
                "--task-class", "analysis", "--reasoning-posture", "balanced",
                "--semantic-role", "task", "--review-mode", "none", "--output", str(output),
            ]
            first = MODULE.main(command, catalog_runner=static_runner)
            before = output.read_bytes()
            second = MODULE.main(command, catalog_runner=static_runner)
            after = output.read_bytes()
        self.assertEqual(0, first)
        self.assertEqual(2, second)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
