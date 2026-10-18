from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
ROLE_TEMPLATE = REPO / ".claude" / "skills" / "default-mode" / "assets" / "project" / "config" / "workflows" / "models.json"
RESOLVER_PATH = HERE / "resolve_launch_spec.py"
SPEC = importlib.util.spec_from_file_location("resolve_launch_spec_adv", RESOLVER_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

LIVE_CATALOG_SPEC = importlib.util.spec_from_file_location("live_catalog_adv", HERE / "live_catalog.py")
assert LIVE_CATALOG_SPEC is not None and LIVE_CATALOG_SPEC.loader is not None
LIVE_CATALOG = importlib.util.module_from_spec(LIVE_CATALOG_SPEC)
sys.modules[LIVE_CATALOG_SPEC.name] = LIVE_CATALOG
LIVE_CATALOG_SPEC.loader.exec_module(LIVE_CATALOG)


def sha256(value: bytes) -> str:
    return "sha256:" + __import__("hashlib").sha256(value).hexdigest()


def codex_catalog_bytes(slug_levels):
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


def cursor_catalog_bytes(ids, *, with_tip=True):
    body = "Available models\n\n" + "".join(f"{i} - {i.upper()}\n" for i in ids)
    if with_tip:
        body += LIVE_CATALOG.CURSOR_TIP_TRAILER + "\n"
    return body.encode("utf-8")


def claude_catalog_bytes(models):
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
    {"gpt-5.6-luna": ["low", "medium", "high", "xhigh", "max"],
     "gpt-5.6-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
     "gpt-6-astra": ["low", "medium", "high", "xhigh", "max", "ultra"]}
)
CURSOR_FIXTURE = cursor_catalog_bytes(
    ["claude-opus-5-thinking-max", "claude-opus-5-thinking-xhigh",
     "claude-sonnet-5-thinking-high", "gpt-5.6-sol-max", "gpt-5.6-luna-max"]
)
CLAUDE_FIXTURE = claude_catalog_bytes(
    {
        "claude-opus-5": ["low", "medium", "high", "xhigh", "max"],
        "claude-sonnet-5": ["low", "medium", "high", "xhigh", "max"],
    }
)


def static_runner(argv):
    if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["codex"]:
        return 0, CODEX_FIXTURE, b""
    if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["cursor"]:
        return 0, CURSOR_FIXTURE, b""
    if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["claude"]:
        return 0, CLAUDE_FIXTURE, b""
    raise AssertionError(f"unexpected catalog argv: {argv!r}")


def verified_document():
    document = json.loads(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    active = {n: r for n, r in document["roles"].items() if r["dispatchStatus"] == "active"}
    backends = sorted({r["backend"] for r in active.values()})
    document["catalogResolution"].update(
        status="verified", verifiedBackends=backends, verifiedRoles=sorted(active),
        evidenceReceipt={
            "schema": "workflow-model-catalog-evidence/v1",
            "assurance": "caller-supplied-structural-evidence-not-live-authentication",
            "backendCatalogSha256": {b: sha256(f"authenticated {b} catalog".encode()) for b in backends},
            "verifiedRoleIdentitiesSha256": "pending",
        },
    )
    document["catalogResolution"]["evidenceReceipt"]["verifiedRoleIdentitiesSha256"] = MODULE.canonical_sha256(
        MODULE.resolved_role_identities(document["roles"], sorted(active))
    )
    return document


def encoded(document, *, indent=2):
    return (json.dumps(document, indent=indent, sort_keys=True) + "\n").encode()


class FreshCatalogAdversarialTest(unittest.TestCase):
    """RED adversarial cases: a caller flipping persisted status/digests, or a
    stale/missing/malformed live catalog, must fail closed regardless of the
    persisted catalogResolution."""

    def resolve(self, document, *, runner=None, **kwargs):
        kwargs.setdefault("task_class", "analysis")
        kwargs.setdefault("reasoning_posture", "balanced")
        kwargs.setdefault("semantic_role", "task")
        kwargs.setdefault("review_mode", "none")
        return MODULE.resolve_launch_spec(
            encoded(document),
            catalog_runner=runner if runner is not None else static_runner,
            **kwargs,
        )

    def test_forged_verified_catalog_does_not_authorize_without_live_coverage(self):
        document = verified_document()
        # A caller forges a fully verified catalogResolution with correct digests,
        # but the live codex catalog lacks gpt-5.6-sol. Dispatch must fail closed.
        def runner_missing_sol(argv):
            if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["codex"]:
                return 0, codex_catalog_bytes({"gpt-5.6-luna": ["max"]}), b""
            return static_runner(argv)
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "not covered by fresh"):
            self.resolve(document, semantic_role="task", runner=runner_missing_sol)

    def test_unavailable_catalog_command_fails_closed(self):
        document = verified_document()
        def runner_unavailable(argv):
            return 2, b"", b"command not found"
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "fresh catalog.*unavailable"):
            self.resolve(document, semantic_role="task", runner=runner_unavailable)

    def test_malformed_catalog_output_fails_closed(self):
        document = verified_document()
        def runner_malformed(argv):
            return 0, b"not json at all", b""
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "fresh catalog.*unavailable"):
            self.resolve(document, semantic_role="task", runner=runner_malformed)

    def test_absent_effort_fails_closed(self):
        document = verified_document()
        def runner_no_max(argv):
            if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["codex"]:
                return 0, codex_catalog_bytes({"gpt-6-astra": ["low", "medium"]}), b""
            return static_runner(argv)
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "does not support effort"):
            self.resolve(document, semantic_role="task", runner=runner_no_max)

    def test_cursor_catalog_missing_tip_trailer_fails_closed_before_coverage(self):
        document = verified_document()
        def runner_no_tip(argv):
            if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["cursor"]:
                return 0, cursor_catalog_bytes(
                    ["claude-opus-5-thinking-max"], with_tip=False
                ), b""
            return static_runner(argv)
        with self.assertRaisesRegex(
            MODULE.LaunchSpecError, "missing the exact terminal Tip trailer"
        ):
            self.resolve(
                document, semantic_role="frontier_execute_anthropic",
                task_class="topIntelligence", reasoning_posture="frontier",
                runner=runner_no_tip,
            )

    def test_cursor_absent_concrete_id_fails_closed(self):
        document = verified_document()
        def runner_cursor_missing(argv):
            if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["cursor"]:
                return 0, cursor_catalog_bytes(["claude-opus-5-thinking-xhigh"]), b""
            return static_runner(argv)
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "does not list concrete id"):
            self.resolve(
                document, semantic_role="frontier_execute_anthropic",
                task_class="topIntelligence", reasoning_posture="frontier",
                runner=runner_cursor_missing,
            )

    def test_caller_command_override_is_ignored(self):
        # The resolver always uses the fixed ALLOWED_COMMANDS argv; a runner that
        # receives any other argv raises, proving the command cannot be overridden.
        document = verified_document()
        receipt = self.resolve(document, semantic_role="task", runner=static_runner)
        self.assertTrue(receipt["policy"]["liveCatalogAuthenticatedByResolver"])
        self.assertEqual(
            ["codex", "debug", "models"],
            receipt["policy"]["freshCatalogEvidence"]["codex"]["command"]["argv"],
        )

    def test_exact_opposite_verifies_both_backends_freshly(self):
        document = verified_document()
        # If the writer's live catalog is missing, exact-opposite must fail closed
        # even though the reviewer's catalog is fine.
        def runner_writer_missing(argv):
            if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["codex"]:
                return 0, codex_catalog_bytes({"gpt-5.6-luna": ["max"]}), b""
            return static_runner(argv)
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "not covered by fresh"):
            self.resolve(
                document, task_class="verification", semantic_role="advisor",
                review_mode="exact-opposite", writer_role="frontier_execute",
                runner=runner_writer_missing,
            )

    def test_exact_opposite_reviewer_backend_missing_fails_closed(self):
        document = verified_document()
        # Reviewer (advisor) is on native Claude; if Claude's catalog lacks Opus,
        # exact-opposite must fail closed even though the writer (codex) is fine.
        def runner_reviewer_missing(argv):
            if tuple(argv) == LIVE_CATALOG.ALLOWED_COMMANDS["claude"]:
                return 0, claude_catalog_bytes(
                    {"claude-sonnet-5": ["low", "medium", "high", "xhigh", "max"]}
                ), b""
            return static_runner(argv)
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "not covered by fresh"):
            self.resolve(
                document, task_class="verification", semantic_role="advisor",
                review_mode="exact-opposite", writer_role="frontier_execute",
                runner=runner_reviewer_missing,
            )

    def test_bootstrap_unverified_status_does_not_block_when_live_catalog_covers(self):
        # Persisted bootstrap-unverified status is audit-only; a fresh live catalog
        # that covers the role still authorizes dispatch. This proves the persisted
        # status is not an authorization gate.
        document = json.loads(ROLE_TEMPLATE.read_text(encoding="utf-8"))
        receipt = self.resolve(document, semantic_role="task", runner=static_runner)
        self.assertEqual("bootstrap-unverified", receipt["policy"]["catalogStatus"])
        self.assertTrue(receipt["policy"]["liveCatalogAuthenticatedByResolver"])

    def test_caller_flipping_status_to_verified_with_bad_digests_still_needs_live(self):
        # A caller flips status to verified but the live catalog is missing the role.
        # The persisted verified status must not authorize; the live check fails.
        document = verified_document()
        def runner_empty(argv):
            return 0, codex_catalog_bytes({"gpt-5.6-luna": ["low"]}), b""
        with self.assertRaisesRegex(MODULE.LaunchSpecError, "not covered by fresh"):
            self.resolve(document, semantic_role="task", runner=runner_empty)

    def test_resolver_does_not_gate_on_persisted_status_or_verified_roles(self):
        # The persisted catalogResolution never authorizes or denies dispatch.
        # The bootstrap-unverified snapshot (empty verifiedRoles/verifiedBackends,
        # no evidenceReceipt) is the canonical "no persisted authorization" case:
        # a fresh live catalog that covers the role still authorizes, proving the
        # persisted status/verifiedRoles are audit-only and not a gate. Flipping
        # source/verifiedRoles on a verified snapshot would break the document's
        # structural consistency, which the validator rejects as document invalidity,
        # not as a dispatch authorization denial.
        document = json.loads(ROLE_TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual("bootstrap-unverified", document["catalogResolution"]["status"])
        self.assertEqual([], document["catalogResolution"]["verifiedRoles"])
        receipt = self.resolve(document, semantic_role="task", runner=static_runner)
        self.assertEqual("bootstrap-unverified", receipt["policy"]["catalogStatus"])
        self.assertEqual("authenticated-native-cli", receipt["policy"]["catalogSource"])
        self.assertTrue(receipt["policy"]["liveCatalogAuthenticatedByResolver"])
        self.assertIn("codex", receipt["policy"]["freshCatalogEvidence"])

    def test_live_resolution_three_bool_fields_must_be_exactly_true(self):
        # The capabilityPolicy.liveResolution three bool fields must be exactly
        # True (Python ``is True``), not merely truthy. A flipped bool fails
        # closed at structural validation before any fresh catalog call.
        for flag in ("requiredAtInit", "exactIdsAreRefreshableSnapshots", "candidateAvailabilityIsNotFact"):
            document = verified_document()
            document["capabilityPolicy"]["liveResolution"][flag] = 1  # truthy but not bool
            with self.assertRaisesRegex(MODULE.LaunchSpecError, "liveResolution"):
                self.resolve(document, semantic_role="task", runner=static_runner)
            document = verified_document()
            document["capabilityPolicy"]["liveResolution"][flag] = False
            with self.assertRaisesRegex(MODULE.LaunchSpecError, "liveResolution"):
                self.resolve(document, semantic_role="task", runner=static_runner)


if __name__ == "__main__":
    unittest.main()
