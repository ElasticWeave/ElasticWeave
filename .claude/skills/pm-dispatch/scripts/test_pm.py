from __future__ import annotations

import argparse
import gc
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import re
import warnings


HERE = Path(__file__).resolve().parent
PM = HERE / "pm.py"
SPEC = importlib.util.spec_from_file_location("pm_board", PM)
assert SPEC is not None and SPEC.loader is not None
pm_board = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pm_board
SPEC.loader.exec_module(pm_board)
REWARD_HELPER = (
    Path(__file__).resolve().parents[2]
    / "subagent-workflow"
    / "scripts"
    / "native"
    / "reward_hacking_evidence.py"
)
ISSUE_REVIEW = Path(__file__).resolve().parents[2] / "codex-verify" / "scripts" / "verify.py"
MILESTONE_REVIEW = (
    Path(__file__).resolve().parents[2]
    / "milestone-review"
    / "scripts"
    / "milestone_review.py"
)
COMMITMENT = "a" * 64
HUMANIZE_CONTRACT = (
    Path(__file__).resolve().parents[2] / "humanize-workflow" / "scripts" / "safe_mapping.py"
)
# Declared independently of pm.py so a silent drift between PM and the canonical adapter pin
# fails a test instead of agreeing with itself.
HUMANIZE_ADAPTER_VERSION = 1
HUMANIZE_UPSTREAM_REPOSITORY = "https://github.com/humanfia/humanize2.git"
HUMANIZE_UPSTREAM_REVISION = "8fbb2b718d74b7ef1d3d0650a9768415ec489f26"
HUMANIZE_DISTRIBUTION = "hmz==0.1.0"
HUMANIZE_SPEC_SCHEMA = "humanize-workflow-spec/v1"
HUMANIZE_HUMAN_REQUEST_KINDS = ["authorization", "credential", "decision", "external-action"]
CONFIG_SYNC_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "update-platform-config"
    / "scripts"
    / "config-sync"
)
sys.path.insert(0, str(CONFIG_SYNC_SCRIPTS))
from resolve_launch_spec import LaunchSpecError, resolve_launch_spec  # noqa: E402

CODEX_CATALOG = json.dumps(
    {
        "models": [
            {
                "slug": model,
                "supported_reasoning_levels": [
                    {"effort": effort}
                    for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
                ],
            }
            for model in ("gpt-5.6-luna", "gpt-5.6-sol")
        ]
    }
) + "\n"
CURSOR_CATALOG = (
    "Available models\n\n"
    "claude-opus-5-thinking-max - Opus Max\n"
    "claude-opus-5-thinking-xhigh - Opus XHigh\n"
    "claude-sonnet-5-thinking-high - Sonnet High\n"
    "Tip: use --model <id> (or /model <id> in interactive mode) to switch. "
    "Parameterized models also accept quoted overrides, e.g. --model "
    "'claude-opus-4-8[context=1m,effort=high,fast=false]'.\n"
)
CLAUDE_CATALOG_ARGV = (
    "claude",
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
)
CLAUDE_CATALOG = (
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
                            "supportedEffortLevels": [
                                "low",
                                "medium",
                                "high",
                                "xhigh",
                                "max",
                            ],
                        }
                        for model in ("claude-opus-5", "claude-sonnet-5")
                    ]
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    + b"\n"
)


def static_catalog_runner(argv: tuple[str, ...]) -> tuple[int, bytes, bytes]:
    if tuple(argv) == ("codex", "debug", "models"):
        return 0, CODEX_CATALOG.encode(), b""
    if tuple(argv) == ("cursor-agent", "models"):
        return 0, CURSOR_CATALOG.encode(), b""
    if tuple(argv) == CLAUDE_CATALOG_ARGV:
        return 0, CLAUDE_CATALOG, b""
    raise AssertionError(f"unexpected catalog argv: {argv!r}")


def unavailable_catalog_runner(argv: tuple[str, ...]) -> tuple[int, bytes, bytes]:
    return 17, b"", f"catalog unavailable for {argv!r}".encode()


PM_TEST_ENTRYPOINT = "\n".join(
    [
        "import importlib.util, os, sys",
        "from pathlib import Path",
        "script = sys.argv[1]",
        "spec = importlib.util.spec_from_file_location('pm_board_under_test', script)",
        "if spec is None or spec.loader is None: raise SystemExit('cannot import pm.py')",
        "module = importlib.util.module_from_spec(spec)",
        "sys.modules[spec.name] = module",
        "spec.loader.exec_module(module)",
        f"CODEX_CATALOG = {CODEX_CATALOG!r}",
        f"CURSOR_CATALOG = {CURSOR_CATALOG!r}",
        f"CLAUDE_CATALOG_ARGV = {CLAUDE_CATALOG_ARGV!r}",
        f"CLAUDE_CATALOG = {CLAUDE_CATALOG!r}",
        "def catalog_runner(argv):",
        "    if tuple(argv) == ('codex', 'debug', 'models'):",
        "        return 0, CODEX_CATALOG.encode(), b''",
        "    if tuple(argv) == ('cursor-agent', 'models'):",
        "        return 0, CURSOR_CATALOG.encode(), b''",
        "    if tuple(argv) == CLAUDE_CATALOG_ARGV:",
        "        return 0, CLAUDE_CATALOG, b''",
        "    raise AssertionError(f'unexpected catalog argv: {argv!r}')",
        "reviewer_fixture = Path.cwd() / '.pm' / 'test-reviewer-fixture'",
        "reviewer_fixture = reviewer_fixture if reviewer_fixture.is_file() else None",
        "test_remote_path = os.environ.get('PM_TEST_GH_PATH')",
        "test_remote_gh = None",
        "if test_remote_path:",
        "    test_remote_gh = {'executable': test_remote_path, 'environment': {key: os.environ[key] for key in ('FAKE_GH_STATE', 'FAKE_GH_CALLS') if key in os.environ}}",
        "raise SystemExit(module.main(sys.argv[2:], _test_catalog_runner=catalog_runner, "
        "_test_reviewer_executable=reviewer_fixture, _test_remote_gh=test_remote_gh))",
    ]
)


class PMTestCase(unittest.TestCase):
    def run_pm(self, cwd, *args, env=None):
        return subprocess.run(
            ["python3", "-c", PM_TEST_ENTRYPOINT, str(PM), *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env,
        )

    def init_board(self, root):
        result = self.run_pm(root, "init", "--ui-mode", "file")
        self.assertEqual(0, result.returncode, result.stderr)

    def init_git(self, root):
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "PM Test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "pm@example.invalid"], cwd=root, check=True)
        Path(root, "baseline.txt").write_text("baseline\n", encoding="utf-8")
        subprocess.run(["git", "add", "baseline.txt"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True)
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

    def setup_board(self, root):
        self.init_git(root)
        self.init_board(root)
        self.assertEqual(0, self.add_milestone(root).returncode)
        self.assertEqual(0, self.add_issue(root).returncode)

    def test_atomic_text_failure_does_not_write_target_or_leave_temporary_file(self):
        for failure_point in ("write", "fchmod", "fsync"):
            with self.subTest(failure_point=failure_point), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "nested" / "control.txt"
                target.parent.mkdir()
                target.write_text("before\n", encoding="utf-8")
                outside = root / "outside.txt"
                outside.write_text("outside\n", encoding="utf-8")

                def fail(*_args):
                    raise OSError(f"injected {failure_point} failure")

                with mock.patch.object(pm_board, "pmroot", return_value=str(root / ".pm")):
                    with mock.patch.object(pm_board.os, failure_point, side_effect=fail):
                        with self.assertRaises(SystemExit) as raised:
                            pm_board._atomic_text(str(target), "after\n")

                self.assertIn("REFUSED", str(raised.exception))
                self.assertEqual("before\n", target.read_text(encoding="utf-8"))
                self.assertEqual("outside\n", outside.read_text(encoding="utf-8"))
                self.assertEqual([], list(target.parent.glob(".control.txt.*.tmp")))

    def test_atomic_text_zero_byte_write_fails_closed_without_loop_or_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "control.txt"
            target.write_text("before\n", encoding="utf-8")
            with mock.patch.object(pm_board, "pmroot", return_value=str(root / ".pm")):
                with mock.patch.object(pm_board.os, "write", return_value=0) as write:
                    with self.assertRaises(SystemExit) as raised:
                        pm_board._atomic_text(str(target), "after\n")

            self.assertIn("zero-byte", str(raised.exception))
            self.assertEqual(1, write.call_count)
            self.assertEqual("before\n", target.read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob(".control.txt.*.tmp")))

    def write_verified_roles(self, root):
        template = (
            HERE.parents[1]
            / "default-mode"
            / "assets"
            / "project"
            / "config"
            / "workflows"
            / "models.json"
        )
        document = json.loads(template.read_text(encoding="utf-8"))
        roles = document["roles"]
        active_roles = sorted(
            name for name, role in roles.items() if role.get("dispatchStatus") == "active"
        )
        active_backends = sorted({roles[name]["backend"] for name in active_roles})
        identities = [
            {
                "semanticRole": name,
                **{
                    field: roles[name].get(field)
                    for field in (
                        "backend",
                        "provider",
                        "upstreamProviderFamily",
                        "model",
                        "modelFamily",
                        "effort",
                        "intelligenceClass",
                        "dispatchStatus",
                    )
                },
            }
            for name in active_roles
        ]
        role_digest = self.canonical_sha256(identities)
        document["catalogResolution"] = {
            "schema": "workflow-model-catalog-resolution/v1",
            "status": "verified",
            "source": "authenticated-native-cli",
            "verifiedBackends": active_backends,
            "verifiedRoles": active_roles,
            "evidenceReceipt": {
                "schema": "workflow-model-catalog-evidence/v1",
                "assurance": "caller-supplied-structural-evidence-not-live-authentication",
                "backendCatalogSha256": {
                    backend: "sha256:" + (chr(97 + index) * 64)
                    for index, backend in enumerate(active_backends)
                },
                "verifiedRoleIdentitiesSha256": role_digest,
            },
        }
        path = Path(root, "config", "workflows", "models.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return document

    def write_bootstrap_roles(self, root):
        template = (
            HERE.parents[1]
            / "default-mode"
            / "assets"
            / "project"
            / "config"
            / "workflows"
            / "models.json"
        )
        document = json.loads(template.read_text(encoding="utf-8"))
        self.assertEqual("bootstrap-unverified", document["catalogResolution"]["status"])
        path = Path(root, "config", "workflows", "models.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return document

    def write_launch_receipt(
        self,
        root,
        *,
        task_class="analysis",
        reasoning_posture="balanced",
        semantic_role="anthropic_task",
        review_mode="none",
        writer_role=None,
        name="implementation",
        persisted_status="verified",
    ):
        document = (
            self.write_verified_roles(root)
            if persisted_status == "verified"
            else self.write_bootstrap_roles(root)
        )
        models_path = Path(root, "config", "workflows", "models.json")
        receipt = resolve_launch_spec(
            models_path.read_bytes(),
            task_class=task_class,
            reasoning_posture=reasoning_posture,
            semantic_role=semantic_role,
            review_mode=review_mode,
            writer_role=writer_role,
            catalog_runner=static_catalog_runner,
        )
        path = Path(root, ".pm", "evidence", "launch-receipts", f"{name}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def canonical_sha256(self, value):
        return "sha256:" + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    def default_workflow_leaves(self):
        return [
            {
                "leafId": "implement",
                "taskClass": "analysis",
                "reasoningPosture": "balanced",
                "semanticRole": "anthropic_task",
                "reviewMode": "none",
                "writerRole": None,
            },
            {
                "leafId": "review",
                "taskClass": "verification",
                "reasoningPosture": "balanced",
                "semanticRole": "codex_advisor",
                "reviewMode": "exact-opposite",
                "writerRole": "anthropic_task",
            },
        ]

    def write_admitted_workflow_artifacts(self, root, *, design_id="review-flow"):
        design = Path(root, ".workflow", "designs", f"{design_id}.workflow.json")
        mapping = Path(root, ".subagent", "flows", f"{design_id}.json")
        design.parent.mkdir(parents=True, exist_ok=True)
        mapping.parent.mkdir(parents=True, exist_ok=True)
        design.write_text(
            json.dumps(
                {
                    "schema": "workflow-design/v1",
                    "id": design_id,
                    "entry": "implement",
                    "goal": {
                        "objective": "Run the bounded implementation and review pair.",
                        "acceptance": "The implementation is independently reviewed.",
                    },
                    "admission": {
                        "reasons": ["multi-stage"],
                        "demotionCondition": "Use a native subagent for a single leaf.",
                    },
                    "governance": {
                        "reviewPolicy": "independent",
                        "completionProof": "none",
                        "malformedDecision": "unresolved",
                    },
                    "budgets": {"maxModelCalls": 2},
                    "requiredCapabilities": [
                        "agent-invocation",
                        "fresh-sessions",
                        "conditional-routing",
                        "structured-output",
                        "observability",
                    ],
                    "state": [],
                    "nodes": [
                        {
                            "id": "implement",
                            "kind": "agent",
                            "role": "anthropic_task",
                            "session": "fresh",
                            "workspaceAccess": "write",
                            "reads": [],
                            "writes": [],
                            "timeoutSeconds": 60,
                        },
                        {
                            "id": "review",
                            "kind": "decision",
                            "role": "codex_advisor",
                            "session": "fresh",
                            "workspaceAccess": "read",
                            "reads": [],
                            "writes": [],
                            "timeoutSeconds": 60,
                            "outcomes": ["PASS", "REVISE", "FAIL"],
                            "onMissing": "unresolved",
                            "reviews": ["implement"],
                        },
                        {
                            "id": "success",
                            "kind": "terminal",
                            "outcome": "success",
                            "reads": [],
                            "writes": [],
                        },
                        {
                            "id": "unresolved",
                            "kind": "terminal",
                            "outcome": "unresolved",
                            "reads": [],
                            "writes": [],
                        },
                        {
                            "id": "failure",
                            "kind": "terminal",
                            "outcome": "failure",
                            "reads": [],
                            "writes": [],
                        },
                    ],
                    "transitions": [
                        {"from": "implement", "to": "review"},
                        {
                            "from": "review",
                            "to": "success",
                            "when": {"field": "review.verdict", "equals": "PASS"},
                        },
                        {
                            "from": "review",
                            "to": "unresolved",
                            "when": {"field": "review.verdict", "equals": "REVISE"},
                        },
                        {
                            "from": "review",
                            "to": "failure",
                            "when": {"field": "review.verdict", "equals": "FAIL"},
                        },
                    ],
                    "terminals": {
                        "success": "success",
                        "unresolved": "unresolved",
                        "failure": "failure",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        mapping.write_text(
            json.dumps(
                {
                    "schema": "subagent-workflow/v1",
                    "designPath": design.relative_to(root).as_posix(),
                    "designSha256": hashlib.sha256(design.read_bytes()).hexdigest(),
                    "inputPath": ".subagent/input.json",
                    "reasoningPosture": "balanced",
                    "scheduler": {
                        "role": "smol",
                        "mode": "ambiguous-only",
                        "maxCalls": 0,
                    },
                    "handlers": {
                        "implement": {"type": "agent", "prompt": "implement"},
                        "review": {"type": "decision", "prompt": "review"},
                        "success": {"type": "terminal"},
                        "unresolved": {"type": "terminal"},
                        "failure": {"type": "terminal"},
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return design, mapping

    def write_humanize_mapping(self, root, design, *, posture="balanced"):
        """Write the canonical humanize manifest plus the spec and artifact bytes it pins."""
        design_document = json.loads(design.read_text(encoding="utf-8"))
        design_id = design_document["id"]
        model_nodes = [
            node for node in design_document["nodes"] if node["kind"] in {"agent", "decision"}
        ]
        spec = Path(root, ".humanize", "specs", f"{design_id}.json")
        artifact = Path(root, ".humanize", "generated-flows", f"{design_id}.py")
        mapping = Path(root, ".humanize", "mappings", f"{design_id}.json")
        for path in (spec, artifact, mapping):
            path.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text(
            json.dumps(
                {
                    "schema": HUMANIZE_SPEC_SCHEMA,
                    "inputPath": ".humanize/input.json",
                    "initialState": {},
                    "prompts": {node["id"]: f"Run node {node['id']}." for node in model_nodes},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        artifact.write_text(
            '"""Adapter-generated humanize2 flow; PM binds these exact bytes."""\n',
            encoding="utf-8",
        )
        payload = {
            "schema": "humanize-workflow-mapping/v1",
            "adapterVersion": HUMANIZE_ADAPTER_VERSION,
            "upstream": {
                "repository": HUMANIZE_UPSTREAM_REPOSITORY,
                "revision": HUMANIZE_UPSTREAM_REVISION,
                "distribution": HUMANIZE_DISTRIBUTION,
            },
            "designPath": design.relative_to(root).as_posix(),
            "designSha256": hashlib.sha256(design.read_bytes()).hexdigest(),
            "specPath": spec.relative_to(root).as_posix(),
            "specSha256": hashlib.sha256(spec.read_bytes()).hexdigest(),
            "artifact": artifact.relative_to(root).as_posix(),
            "artifactSha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "inputPath": ".humanize/input.json",
            "reasoningPosture": posture,
            "nodeAgents": [
                {
                    "node": node["id"],
                    "role": node["role"],
                    "workspaceAccess": node.get("workspaceAccess", "read"),
                }
                for node in model_nodes
            ],
            "terminalProtocol": {
                "field": "TERMINAL",
                "values": ["COMPLETE", "HUMAN_BLOCKED", "REVISE"],
                "humanRequestKinds": list(HUMANIZE_HUMAN_REQUEST_KINDS),
                "independentReviewRequired": True,
            },
        }
        mapping.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return mapping

    def write_launch_plan(
        self,
        root,
        *,
        iid="i1",
        design_id="review-flow",
        leaves=None,
        mapping_factory=None,
        mutate_mapping=None,
    ):
        self.write_verified_roles(root)
        design, mapping = self.write_admitted_workflow_artifacts(root, design_id=design_id)
        if mapping_factory is not None:
            mapping = mapping_factory(root, design)
        if mutate_mapping is not None:
            payload = json.loads(mapping.read_text(encoding="utf-8"))
            mutate_mapping(payload)
            mapping.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        policy = Path(root, "config", "workflows", "models.json")
        plan = {
            "schema": "workflow-launch-plan/v1",
            "designPath": design.relative_to(root).as_posix(),
            "designSha256": "sha256:" + hashlib.sha256(design.read_bytes()).hexdigest(),
            "mappingPath": mapping.relative_to(root).as_posix(),
            "mappingSha256": "sha256:" + hashlib.sha256(mapping.read_bytes()).hexdigest(),
            "policyPath": policy.relative_to(root).as_posix(),
            "policySha256": "sha256:" + hashlib.sha256(policy.read_bytes()).hexdigest(),
            "leaves": list(leaves if leaves is not None else self.default_workflow_leaves()),
        }
        plan["planSha256"] = self.canonical_sha256(
            {key: value for key, value in plan.items() if key != "planSha256"}
        )
        path = Path(root, ".pm", "evidence", "workflow-launch-plans", f"{iid}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path, plan

    def write_receipt_manifest(self, root, iid="i1", plan=None, *, mutate=None):
        if plan is None:
            plan = json.loads(
                Path(root, ".pm", "evidence", "workflow-launch-plans", f"{iid}.json").read_text(
                    encoding="utf-8"
                )
            )
        leaves = []
        for leaf in plan["leaves"]:
            receipt = self.write_launch_receipt(
                root,
                task_class=leaf["taskClass"],
                reasoning_posture=leaf["reasoningPosture"],
                semantic_role=leaf["semanticRole"],
                review_mode=leaf["reviewMode"],
                writer_role=leaf["writerRole"],
                name=f"{iid}-{leaf['leafId']}",
            )
            leaves.append({
                "leafId": leaf["leafId"],
                "receiptPath": receipt.relative_to(root).as_posix(),
                "receiptSha256": "sha256:" + hashlib.sha256(receipt.read_bytes()).hexdigest(),
            })
        plan_path = Path(root, ".pm", "evidence", "workflow-launch-plans", f"{iid}.json")
        manifest = {
            "schema": "workflow-receipt-manifest/v1",
            "planPath": plan_path.relative_to(root).as_posix(),
            "planSha256": plan["planSha256"],
            "leaves": leaves,
        }
        if mutate is not None:
            mutate(root, manifest)
        manifest["manifestSha256"] = self.canonical_sha256(
            {key: value for key, value in manifest.items() if key != "manifestSha256"}
        )
        path = Path(root, ".pm", "evidence", "workflow-receipt-manifests", f"{iid}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def add_workflow_issue(
        self,
        root,
        *,
        iid="i1",
        policy="machine-gated",
        leaves=None,
        mutate_mapping=None,
        plan_path=None,
    ):
        if plan_path is None:
            plan_path, _ = self.write_launch_plan(
                root, iid=iid, leaves=leaves, mutate_mapping=mutate_mapping
            )
        else:
            plan_path = Path(plan_path)
        return self.run_pm(
            root,
            "issue",
            iid,
            "m1",
            "Issue",
            "--lane",
            "workflow",
            "--lane-reason",
            "graph state and machine-gated review",
            "--estimated-model-calls",
            "3",
            "--max-model-calls",
            "6",
            "--promotion-triggers",
            "none",
            "--acceptance",
            "Run the focused check and judge the stated deliverable from its fresh output.",
            "--review-policy",
            policy,
            "--review-reason",
            "graph contains the reviewer and verdict routing",
            "--launch-plan",
            plan_path.relative_to(root).as_posix(),
        )

    def bind_workflow_receipts(self, root, iid="i1", *, mutate=None):
        manifest = self.write_receipt_manifest(root, iid, mutate=mutate)
        return self.run_pm(
            root,
            "bind-workflow-receipts",
            iid,
            "--receipt-manifest",
            manifest.relative_to(root).as_posix(),
        )

    def add_milestone(self, root, review_policy="independent"):
        result = self.run_pm(
            root,
            "milestone",
            "add",
            "m1",
            "Milestone",
            "--acceptance",
            "Reviewer confirms the bounded milestone criteria from fresh evidence.",
            "--review-policy",
            review_policy,
            "--review-reason",
            "universal independent terminal review",
        )
        return result

    def add_issue(self, root, *, lane="native-subagent", policy="independent", iid="i1"):
        delegated = lane != "direct"
        args = [
            "issue",
            iid,
            "m1",
            "Issue",
            "--lane",
            lane,
            "--lane-reason",
            "single delegated owner" if delegated else "read-only coordinator inspection",
            "--estimated-model-calls",
            "1",
            "--max-model-calls",
            "2" if delegated else "1",
            "--promotion-triggers",
            "graph state emerges" if lane == "native-subagent" else "none",
            "--acceptance",
            "Run the focused check and judge the stated deliverable from its fresh output.",
            "--review-policy",
            policy,
            "--review-reason",
            "terminal review route for this item",
        ]
        if lane == "direct":
            args.extend([
                "--direct-scope", "read-only-inspection",
                "--reasoning-posture", "direct",
                "--task-class", "direct",
                "--semantic-role", "none",
                "--launch-receipt", "none",
            ])
        elif lane == "workflow":
            plan_path, _ = self.write_launch_plan(root, iid=iid)
            args.extend([
                "--launch-plan", plan_path.relative_to(root).as_posix(),
            ])
        else:
            receipt = self.write_launch_receipt(root, name=f"{iid}-implementation")
            args.extend([
                "--reasoning-posture", "balanced",
                "--task-class", "analysis",
                "--semantic-role", "anthropic_task",
                "--launch-receipt", receipt.relative_to(root).as_posix(),
            ])
        return self.run_pm(root, *args)

    def write_report(self, root, iid="i1"):
        result = self.run_pm(
            root,
            "report",
            "issue",
            iid,
            "--summary",
            "Focused check passed; evidence and remaining gaps were recorded.",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        time.sleep(0.01)

    def write_milestone_report(self, root, mid="m1"):
        result = self.run_pm(
            root,
            "report",
            "milestone",
            mid,
            "--summary",
            "Final self-evolution, acceptance evidence, and milestone outcome were recorded.",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        time.sleep(0.01)

    def seal_self_evolution(self, root, mid="m1"):
        result = self.run_pm(
            root,
            "self-improve",
            "close",
            mid,
            "--note",
            "traces_read: .pm reports and raw worker traces | "
            "trace_findings: bounded lifecycle ordering was inspected | "
            "memory_delta: project observation recorded or none warranted | "
            "distilled: no generalized lemma warranted by this single project",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        return result

    def write_terminal(
        self,
        root,
        terminal,
        *,
        iid="i1",
        kind="issue",
        human_kind=None,
        human_request=None,
        duplicate=False,
        commitment=COMMITMENT,
    ):
        directory = "verdicts" if kind == "issue" else "reviews"
        path = Path(root, ".pm", directory, f"{iid}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"TERMINAL: {terminal}", f"EVIDENCE-COMMITMENT: sha256:{commitment}"]
        if human_kind is not None:
            lines.append(f"HUMAN-REQUEST-KIND: {human_kind}")
        if human_request is not None:
            lines.append(f"HUMAN-REQUEST: {human_request}")
        if duplicate:
            lines.append(f"TERMINAL: {terminal}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def write_independent_terminal(
        self,
        root,
        terminal,
        *,
        iid="i1",
        kind="issue",
        human_kind=None,
        human_request=None,
        builder_roles=("anthropic_task",),
    ):
        self.write_verified_roles(root)
        reviewer_launches = [
            self.write_launch_receipt(
                root,
                task_class="verification",
                reasoning_posture="balanced",
                semantic_role="codex_advisor",
                review_mode="exact-opposite",
                writer_role=builder_role,
                name=f"{iid}-reviewer-{builder_role}",
            )
            for builder_role in builder_roles
        ]
        reviewer_payload = Path(root, ".pm", "test-reviewer-payload.py")
        fixture = Path(root, ".pm", "test-reviewer-fixture")
        fixture.write_text(
            "#!/usr/bin/env bash\n"
            f'exec "{sys.executable}" "{reviewer_payload}" "$@"\n',
            encoding="utf-8",
        )
        fixture.chmod(0o700)
        reviewer_payload.write_text(
            "import os, re, sys\n"
            "prompt = sys.stdin.read()\n"
            "def field(name):\n"
            "    match = re.search(rf'(?m)^{name}: (\\S+)$', prompt)\n"
            "    if not match:\n"
            "        raise SystemExit(f'missing {name}')\n"
            "    return match.group(1)\n"
            "terminal = os.environ.get('REVIEW_FIXTURE_TERMINAL')\n"
            "extra = os.environ.get('REVIEW_FIXTURE_EXTRA', '')\n"
            "commitment = re.search(r'(?m)^EVIDENCE-COMMITMENT: (\\S+)$', prompt)\n"
            "if terminal is None:\n"
            "    for directory in ('verdicts', 'reviews'):\n"
            "        artifact_dir = os.path.join('.pm', directory)\n"
            "        try:\n"
            "            names = os.listdir(artifact_dir)\n"
            "        except OSError:\n"
            "            continue\n"
            "        for name in names:\n"
            "            if not name.endswith('.md'):\n"
            "                continue\n"
            "            try:\n"
            "                with open(os.path.join(artifact_dir, name), encoding='utf-8') as stream:\n"
            "                    artifact = stream.read()\n"
            "            except OSError:\n"
            "                continue\n"
            "            if commitment and not re.search(rf'(?m)^EVIDENCE-COMMITMENT: {re.escape(commitment.group(1))}$', artifact):\n"
            "                continue\n"
            "            match = re.search(r'(?m)^TERMINAL: (COMPLETE|HUMAN_BLOCKED|REVISE)$', artifact)\n"
            "            if not match or match.group(1) != 'HUMAN_BLOCKED':\n"
            "                continue\n"
            "            terminal = match.group(1)\n"
            "            kind = re.search(r'(?m)^HUMAN-REQUEST-KIND: (.+)$', artifact)\n"
            "            request = re.search(r'(?m)^HUMAN-REQUEST: (.+)$', artifact)\n"
            "            if kind and request:\n"
            "                extra = f'HUMAN-REQUEST-KIND: {kind.group(1)}\\nHUMAN-REQUEST: {request.group(1)}\\n'\n"
            "            break\n"
            "        if terminal is not None:\n"
            "            break\n"
            "if terminal is None:\n"
            "    terminal = 'COMPLETE'\n"
            "print(f'TERMINAL: {terminal}')\n"
            "print(f'EVIDENCE-COMMITMENT: {field(\"EVIDENCE-COMMITMENT\")}')\n"
            "print(f'REVIEWER-ROLE: {field(\"REVIEWER-ROLE\")}')\n"
            "print(f'REVIEWER-IDENTITY-SHA256: {field(\"REVIEWER-IDENTITY-SHA256\")}')\n"
            "if extra:\n"
            "    print(extra, end='' if extra.endswith('\\n') else '\\n')\n"
            "print('REASONS: Current evidence was checked against every scoped criterion.')\n",
            encoding="utf-8",
        )
        wrapper = Path(root, ".pm", "test-review-cli-wrapper.py")
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            "script, fixture = Path(sys.argv[1]), Path(sys.argv[2])\n"
            "sys.path.insert(0, str(script.parent))\n"
            "spec = importlib.util.spec_from_file_location('review_cli_under_test', script)\n"
            "if spec is None or spec.loader is None:\n"
            "    raise SystemExit('cannot import review CLI')\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules[spec.name] = module\n"
            "spec.loader.exec_module(module)\n"
            f"CODEX_CATALOG = {CODEX_CATALOG!r}\n"
            f"CURSOR_CATALOG = {CURSOR_CATALOG!r}\n"
            "def catalog_runner(argv):\n"
            "    if tuple(argv) == ('codex', 'debug', 'models'):\n"
            "        return 0, CODEX_CATALOG.encode(), b''\n"
            "    if tuple(argv) == ('cursor-agent', 'models'):\n"
            "        return 0, CURSOR_CATALOG.encode(), b''\n"
            f"    if tuple(argv) == {CLAUDE_CATALOG_ARGV!r}:\n"
            f"        return 0, {CLAUDE_CATALOG!r}, b''\n"
            "    raise AssertionError(f'unexpected catalog argv: {argv!r}')\n"
            "raise SystemExit(module.main(sys.argv[3:], _test_reviewer_executable=fixture, "
            "_test_catalog_runner=catalog_runner))\n",
            encoding="utf-8",
        )
        evidence = Path(root, ".pm", "evidence", f"{iid}.md")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            "# Fresh acceptance evidence\n\nFocused check: exit 0; scoped criteria inspected.\n",
            encoding="utf-8",
        )
        base = subprocess.check_output(
            ["git", "rev-list", "--max-parents=0", "HEAD"], cwd=root, text=True
        ).strip()
        extra = ""
        if human_kind is not None:
            extra += f"HUMAN-REQUEST-KIND: {human_kind}\n"
        if human_request is not None:
            extra += f"HUMAN-REQUEST: {human_request}\n"
        script = ISSUE_REVIEW if kind == "issue" else MILESTONE_REVIEW
        argv = [
            "python3",
            str(wrapper),
            str(script),
            str(fixture),
            iid,
            "--base",
            base,
            "--evidence",
            evidence.relative_to(root).as_posix(),
            "--reviewer-role",
            "codex_advisor",
            "--allow-empty",
        ]
        for builder_role, reviewer_launch in zip(builder_roles, reviewer_launches):
            argv.extend(
                [
                    "--builder-role",
                    builder_role,
                    "--reviewer-launch-receipt",
                    reviewer_launch.relative_to(root).as_posix(),
                ]
            )
        argv.append("--finalize")
        env = dict(os.environ)
        env["REVIEW_FIXTURE_TERMINAL"] = terminal
        env["REVIEW_FIXTURE_EXTRA"] = extra
        finalized = subprocess.run(
            argv, cwd=root, capture_output=True, text=True, env=env
        )
        expected_code = {"COMPLETE": 0, "HUMAN_BLOCKED": 3, "REVISE": 2}[terminal]
        self.assertEqual(
            expected_code,
            finalized.returncode,
            finalized.stderr + "\n" + finalized.stdout,
        )

    def set_ui_mode(self, root, mode):
        path = Path(root, ".pm", "config.json")
        config = json.loads(path.read_text(encoding="utf-8"))
        config["ui_mode"] = mode
        path.write_text(json.dumps(config) + "\n", encoding="utf-8")

    def attach_pr(self, root, *, iid="i1", repository="owner/repo", number=12):
        issue = Path(root, ".pm", "issues", "m1", f"{iid}.md")
        issue.write_text(
            issue.read_text(encoding="utf-8").replace(
                "linear_id: ",
                f"linear_id: \npr_repository: {repository}\npr_number: {number}\n",
                1,
            ),
            encoding="utf-8",
        )

    def write_complete_merge_bundle(
        self,
        root,
        *,
        iid="i1",
        kind="issue",
        approval_id="77",
        merge_commit=None,
        mutate_receipt=None,
        mutate_authorization=None,
    ):
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        artifact = Path(root, ".pm", "verdicts" if kind == "issue" else "reviews", f"{iid}.md")
        commitment = None
        for line in artifact.read_text(encoding="utf-8").splitlines():
            if line.startswith("EVIDENCE-COMMITMENT:"):
                commitment = line.split(":", 1)[1].strip()
        self.assertIsNotNone(commitment)
        authorization = {
            "schema": "resolve-pr-authorization/v1",
            "transactionId": f"merge-{iid}-12",
            "authorizationKind": "user",
            "authorizationRef": "terminal-session:user-approved",
            "itemKind": kind,
            "itemId": iid,
            "repository": "owner/repo",
            "pr": 12,
            "approvedHead": head,
            "baseRefName": "main",
            "baseRefOid": "b" * 40,
            "strategy": "squash",
            "deleteBranch": True,
        }
        if mutate_authorization is not None:
            mutate_authorization(authorization)
        auth_path = Path(root, ".pm", "receipts", "merge", f"{kind}-{iid}-authorization.json")
        receipt_path = Path(root, ".pm", "receipts", "merge", f"{kind}-{iid}.json")
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        auth_path.write_text(json.dumps(authorization, sort_keys=True) + "\n", encoding="utf-8")
        receipt = {
            "schema": "resolve-pr-merge-receipt/v1",
            "transactionId": authorization["transactionId"],
            "authorizationReceiptSha256": "sha256:" + hashlib.sha256(auth_path.read_bytes()).hexdigest(),
            "itemKind": kind,
            "itemId": iid,
            "repository": "owner/repo",
            "pr": 12,
            "approvedHead": head,
            "baseRefName": authorization["baseRefName"],
            "baseRefOid": authorization["baseRefOid"],
            "strategy": authorization["strategy"],
            "deleteBranch": authorization["deleteBranch"],
            "terminalArtifact": artifact.relative_to(root).as_posix(),
            "terminalArtifactSha256": "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "terminalCommitment": commitment,
            "approvalReviewId": approval_id,
            "headRefOid": head,
            "mergeCommit": merge_commit or "c" * 40,
            "state": "MERGED",
        }
        if mutate_receipt is not None:
            mutate_receipt(receipt)
        receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
        return receipt_path, auth_path

    def install_fake_gh(
        self,
        root,
        *,
        head,
        merge_commit=None,
        pr_state="MERGED",
        approval_id=77,
        approval_state="APPROVED",
        approval_commit=None,
        review_decision="APPROVED",
        base_oid=None,
    ):
        bin_dir = Path(root, ".pm", "receipts", "merge", ".bin")
        bin_dir.mkdir(parents=True, exist_ok=True)
        state_path = bin_dir / "fake-gh-state.json"
        calls_path = bin_dir / "fake-gh-calls.jsonl"
        calls_path.write_text("", encoding="utf-8")
        merge_commit = merge_commit or "c" * 40
        state_path.write_text(
            json.dumps(
                {
                    "approval": {
                        "id": approval_id,
                        "state": approval_state,
                        "commit_id": approval_commit or head,
                    },
                    "view": {
                        "state": pr_state,
                        "isDraft": False,
                        "headRefOid": head,
                        "baseRefName": "main",
                        "baseRefOid": base_oid or "b" * 40,
                        "mergeStateStatus": "UNKNOWN",
                        "mergeCommit": {"oid": merge_commit} if pr_state == "MERGED" else None,
                        "reviewDecision": review_decision,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        gh = bin_dir / "gh"
        gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "state=json.loads(Path(os.environ['FAKE_GH_STATE']).read_text())\n"
            "calls=Path(os.environ['FAKE_GH_CALLS'])\n"
            "argv=sys.argv[1:]\n"
            "with calls.open('a') as f: f.write(json.dumps({'argv':argv})+'\\n')\n"
            "if argv[:1]==['api']:\n"
            " print(json.dumps(state['approval'])); raise SystemExit(0)\n"
            "if argv[:2]==['pr','view']:\n"
            " print(json.dumps(state['view'])); raise SystemExit(0)\n"
            "raise SystemExit(99)\n",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        env["FAKE_GH_STATE"] = str(state_path)
        env["FAKE_GH_CALLS"] = str(calls_path)
        env["PM_TEST_GH_PATH"] = str(gh)
        return env, calls_path

    def write_human_send_receipt(
        self,
        root,
        *,
        iid="i1",
        channel="file",
        request="Contact the vendor to restore the unavailable account.",
    ):
        receipt = Path(root, ".pm", "receipts", "human", f"issue-{iid}.json")
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(
            json.dumps(
                {
                    "schema": "human-request-send-receipt/v1",
                    "request_key": f"issue-{iid}-human-1",
                    "channel": channel,
                    "item_kind": "issue",
                    "item_id": iid,
                    "expected_human_id": "project-owner",
                    "request_hash": "sha256:" + hashlib.sha256(request.encode("utf-8")).hexdigest(),
                    "sent_at": "2026-08-13T12:00:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return receipt


class DispatchMetadataTests(PMTestCase):
    def test_skill_uses_multimodel_advice_before_terminal_review(self):
        skill = (HERE.parent / "SKILL.md").read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", skill)
        for expected in (
            "planning proposals or ambiguous result analysis",
            "references/multimodel-consultation.md",
            "independent branches over the same frozen inputs before cross-exposure",
            "frontier_advice_fable",
            "hard or materially uncertain advice",
            "effortPolicy: highest-available",
            "does not replace the worker/workflow reviewer or terminal review",
        ):
            self.assertIn(expected, normalized)

    def test_skill_orders_terminal_review_before_post_review_linear_close(self):
        skill = (HERE.parent / "SKILL.md").read_text(encoding="utf-8")
        resolution = skill.split("5. **Resolve the issue in evidence order:**", 1)[1].split(
            "6. **Milestone done", 1
        )[0]
        self.assertNotIn("eventual close mirror now", resolution)
        self.assertNotIn(
            "no completion/report/status/UI mirror write may follow the verdict",
            resolution,
        )
        preamble = resolution.split("For `independent`", 1)[0]
        preamble_flat = re.sub(r"\s+", " ", preamble)
        self.assertIn("After `COMPLETE` or `REVISE`", preamble_flat)
        self.assertIn("no ordinary report, status, or UI mirror", preamble_flat)
        self.assertIn("`COMPLETE` authorizes only the tightly bounded", preamble_flat)
        self.assertIn("`set ... done --transaction-id`", preamble_flat)
        self.assertIn("Linear close/readback", preamble_flat)
        self.assertIn("exact review-owned PR-merge transition", preamble_flat)
        self.assertIn("`REVISE` authorizes no completion", preamble_flat)
        self.assertIn(
            "`HUMAN_BLOCKED`'s exact request route below separate",
            preamble_flat,
        )
        self.assertNotIn("the sole exception", preamble_flat)
        self.assertNotIn("linear-close", preamble)
        self.assertLess(
            resolution.index("For `independent`"),
            resolution.index("pm.py set issue <id> done --transaction-id <stable-id>"),
        )
        self.assertLess(
            resolution.index("For `independent`"),
            resolution.index("pm.py linear-close <id> --transaction-id <stable-id>"),
        )
        complete = resolution.split("`TERMINAL: COMPLETE`", 1)[1].split(
            "`TERMINAL: HUMAN_BLOCKED`", 1
        )[0]
        human = resolution.split("`TERMINAL: HUMAN_BLOCKED`", 1)[1].split(
            "`TERMINAL: REVISE`", 1
        )[0]
        revise = resolution.split("`TERMINAL: REVISE`", 1)[1]
        self.assertIn("pm.py set issue <id> done --transaction-id <stable-id>", complete)
        self.assertIn("pm.py linear-close <id> --transaction-id <stable-id>", complete)
        self.assertIn("review-owned PR-merge worker", complete)
        self.assertIn("bounded UI-routing worker", human)
        self.assertNotIn("pm.py set issue <id> done --transaction-id <stable-id>", human)
        self.assertNotIn("pm.py linear-close <id> --transaction-id <stable-id>", human)
        self.assertIn("do not stop terminally or mark done", revise)
        self.assertNotIn("pm.py set issue <id> done --transaction-id <stable-id>", revise)
        self.assertNotIn("pm.py linear-close <id> --transaction-id <stable-id>", revise)
        milestone = skill.split("6. **Milestone done", 1)[1].split(
            "## Deciding under uncertainty", 1
        )[0]
        self.assertIn("before the terminal\n   review", milestone)
        self.assertIn("pm.py set milestone done --transaction-id <stable-id>", milestone)
        self.assertIn("set milestone done --transaction-id <stable-id>", milestone)
        self.assertIn("pm.py linear-close <id> --transaction-id <stable-id>", milestone)
        self.assertNotIn("Delegate the Linear close separately", milestone)
        for match in re.finditer(
            r"pm\.py (?:set (?:issue <id>|milestone) done|linear-close <id>)[^`\n]*",
            skill,
        ):
            self.assertIn("--transaction-id <stable-id>", match.group(0), match.group(0))

    def test_skill_command_examples_require_stable_transaction_id(self):
        skill = (HERE.parent / "SKILL.md").read_text(encoding="utf-8")
        commands = skill.split("Canonical state lives in `.pm/`", 1)[1].split("```", 2)[1]
        self.assertEqual(1, commands.count("pm.py set issue <id> done"))
        self.assertIn(
            "pm.py set issue <id> done --transaction-id <stable-id>",
            commands,
        )
        self.assertEqual(1, commands.count("pm.py linear-close"))
        self.assertIn(
            "pm.py linear-close <id> --transaction-id <stable-id>",
            commands,
        )
        self.assertNotIn("pm.py linear-close <id>         #", commands)
        self.assertNotIn("pm.py set issue <id> done       #", commands)

    def test_skill_documents_disjoint_machine_workflow_proof_namespace(self):
        skill = (HERE.parent / "SKILL.md").read_text(encoding="utf-8")
        gated = skill.split("`machine-gated`", 1)[1].split("Legacy `review_policy: none`", 1)[0]
        self.assertIn("`.pm/workflow-reviews/<itemId>.md`", gated)
        self.assertNotIn("`.pm/reviews/workflow-<itemId>.md`", gated)
        self.assertNotIn("`.pm/reviews/workflow-<id>.md`", gated)

    def test_items_require_and_record_acceptance_and_review_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            milestone = self.add_milestone(tmp)
            self.assertEqual(0, milestone.returncode, milestone.stderr)
            missing = self.run_pm(tmp, "issue", "i1", "m1", "Issue")
            self.assertNotEqual(0, missing.returncode)

            created = self.add_issue(tmp)
            self.assertEqual(0, created.returncode, created.stderr)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text()
            self.assertIn("dispatch_lane: native-subagent", issue)
            self.assertIn("review_policy: independent", issue)
            self.assertIn("## Acceptance Criteria", issue)
            self.assertIn("fresh output", issue)
            self.assertNotIn("owner:", issue)

            ready = self.run_pm(tmp, "next", "--milestone", "m1")
            self.assertEqual(0, ready.returncode)
            self.assertIn("lane=native-subagent", ready.stdout)
            self.assertIn("review=independent", ready.stdout)

    def test_create_commands_do_not_overwrite_existing_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            milestone_path = Path(tmp, ".pm", "milestones", "m1.md")
            milestone_before = milestone_path.read_bytes()
            duplicate_milestone = self.add_milestone(tmp)
            self.assertNotEqual(0, duplicate_milestone.returncode)
            self.assertIn("already exists", duplicate_milestone.stderr)
            self.assertEqual(milestone_before, milestone_path.read_bytes())

            self.assertEqual(0, self.add_issue(tmp).returncode)
            issue_path = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue_before = issue_path.read_bytes()
            duplicate_issue = self.add_issue(tmp)
            self.assertNotEqual(0, duplicate_issue.returncode)
            self.assertIn("already exists", duplicate_issue.stderr)
            self.assertEqual(issue_before, issue_path.read_bytes())

    def test_new_none_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            milestone = self.run_pm(
                tmp,
                "milestone",
                "add",
                "m1",
                "Milestone",
                "--acceptance",
                "criteria",
                "--review-policy",
                "none",
                "--review-reason",
                "legacy",
            )
            self.assertNotEqual(0, milestone.returncode)
            self.assertIn("invalid choice", milestone.stderr)

            self.assertEqual(0, self.add_milestone(tmp).returncode)
            issue = self.run_pm(
                tmp,
                "issue",
                "i1",
                "m1",
                "Issue",
                "--lane",
                "native-subagent",
                "--lane-reason",
                "single owner",
                "--estimated-model-calls",
                "1",
                "--max-model-calls",
                "1",
                "--promotion-triggers",
                "none",
                "--reasoning-posture",
                "direct",
                "--task-class",
                "direct",
                "--semantic-role",
                "none",
                "--launch-receipt",
                "none",
                "--acceptance",
                "criteria",
                "--review-policy",
                "none",
                "--review-reason",
                "legacy",
            )
            self.assertNotEqual(0, issue.returncode)
            self.assertIn("invalid choice", issue.stderr)

    def test_legacy_none_item_requires_explicit_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp).returncode)
            path = Path(tmp, ".pm", "issues", "m1", "i1.md")
            text = path.read_text().replace("review_policy: independent", "review_policy: none")
            path.write_text(text)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            self.write_report(tmp)
            self.write_terminal(tmp, "COMPLETE")
            refused = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("migrate-review-policy", refused.stderr)

            migrated = self.run_pm(
                tmp,
                "migrate-review-policy",
                "issue",
                "i1",
                "--to",
                "independent",
                "--reason",
                "adopt universal terminal review",
            )
            self.assertEqual(0, migrated.returncode, migrated.stderr)
            self.assertIn("review_policy: independent", path.read_text())

    def test_review_policy_migration_rebinds_existing_task_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            # Reproduce a legacy board edited in place before the explicit migration command.
            text = issue.read_text(encoding="utf-8")
            text = text.replace("review_policy: independent", "review_policy: none", 1)
            text = text.replace(
                "review_reason: terminal review route for this item",
                "review_reason: legacy item awaiting migration",
                1,
            )
            issue.write_text(text, encoding="utf-8")

            migrated = self.run_pm(
                tmp,
                "migrate-review-policy",
                "issue",
                "i1",
                "--to",
                "independent",
                "--reason",
                "restored independent terminal review",
            )
            self.assertEqual(0, migrated.returncode, migrated.stderr)
            shown = self.run_pm(tmp, "task-workspace", "show", "i1")
            self.assertEqual(0, shown.returncode, shown.stderr)
            task = json.loads(
                Path(tmp, ".pm", "tasks", "i1", "task.json").read_text(encoding="utf-8")
            )
            meta, body = pm_board.read(issue)
            self.assertEqual(pm_board._issue_snapshot_sha256(meta, body), task["issueSnapshotSha256"])

    def test_issue_creation_rolls_back_when_task_workspace_initialization_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            argv = [
                "issue", "i-fail", "m1", "Injected failure",
                "--lane", "direct", "--lane-reason", "read-only coordinator inspection",
                "--direct-scope", "read-only-inspection",
                "--estimated-model-calls", "1", "--max-model-calls", "1",
                "--promotion-triggers", "none", "--reasoning-posture", "direct",
                "--task-class", "direct", "--semantic-role", "none", "--launch-receipt", "none",
                "--acceptance", "Run the focused check and judge the stated deliverable from its fresh output.",
                "--review-policy", "independent", "--review-reason", "terminal review route for this item",
            ]
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                with mock.patch.object(
                    pm_board,
                    "_initialize_task_workspace",
                    side_effect=RuntimeError("injected workspace failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "injected workspace failure"):
                        pm_board.main(argv)
            finally:
                os.chdir(previous)
            self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i-fail.md").exists())
            self.assertFalse(Path(tmp, ".pm", "tasks", "i-fail").exists())

    def test_direct_lane_requires_explicit_coordination_scope_and_review_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            accepted = self.add_issue(tmp, lane="direct")
            self.assertEqual(0, accepted.returncode, accepted.stderr)
            direct = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text()
            self.assertIn("direct_scope: read-only-inspection", direct)
            rejected = self.run_pm(
                tmp,
                "issue",
                "i2",
                "m1",
                "Bad direct",
                "--lane",
                "direct",
                "--lane-reason",
                "claimed implementation",
                "--estimated-model-calls",
                "0",
                "--max-model-calls",
                "0",
                "--promotion-triggers",
                "none",
                "--reasoning-posture",
                "direct",
                "--task-class",
                "direct",
                "--semantic-role",
                "none",
                "--launch-receipt",
                "none",
                "--acceptance",
                "criteria",
                "--review-policy",
                "independent",
                "--review-reason",
                "review",
            )
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("--direct-scope", rejected.stderr)

            legacy_mutating_direct = self.run_pm(
                tmp,
                "issue",
                "i3",
                "m1",
                "Legacy mutating direct scope",
                "--lane",
                "direct",
                "--direct-scope",
                "local-pm-bookkeeping",
                "--lane-reason",
                "claimed board bookkeeping",
                "--estimated-model-calls",
                "1",
                "--max-model-calls",
                "1",
                "--promotion-triggers",
                "none",
                "--reasoning-posture",
                "direct",
                "--task-class",
                "direct",
                "--semantic-role",
                "none",
                "--launch-receipt",
                "none",
                "--acceptance",
                "criteria",
                "--review-policy",
                "independent",
                "--review-reason",
                "terminal review",
            )
            self.assertNotEqual(0, legacy_mutating_direct.returncode)
            self.assertIn("invalid choice: 'local-pm-bookkeeping'", legacy_mutating_direct.stderr)
            self.assertIn("read-only-inspection", legacy_mutating_direct.stderr)
            self.assertIn("disposable-verification", legacy_mutating_direct.stderr)

            no_budget = self.run_pm(
                tmp,
                "issue",
                "i4",
                "m1",
                "Direct without review budget",
                "--lane",
                "direct",
                "--direct-scope",
                "disposable-verification",
                "--lane-reason",
                "disposable coordinator check",
                "--estimated-model-calls",
                "0",
                "--max-model-calls",
                "0",
                "--promotion-triggers",
                "none",
                "--reasoning-posture",
                "direct",
                "--task-class",
                "direct",
                "--semantic-role",
                "none",
                "--launch-receipt",
                "none",
                "--acceptance",
                "criteria",
                "--review-policy",
                "independent",
                "--review-reason",
                "terminal review",
            )
            self.assertNotEqual(0, no_budget.returncode)
            self.assertIn("reserved terminal review", no_budget.stderr)

    def test_estimate_cannot_exceed_maximum(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            result = self.run_pm(
                tmp,
                "issue",
                "i1",
                "m1",
                "Issue",
                "--lane",
                "native-subagent",
                "--lane-reason",
                "single owner",
                "--estimated-model-calls",
                "4",
                "--max-model-calls",
                "2",
                "--promotion-triggers",
                "fan-out emerges",
                "--reasoning-posture",
                "balanced",
                "--task-class",
                "analysis",
                "--semantic-role",
                "anthropic_task",
                "--launch-receipt",
                ".pm/evidence/launch-receipts/unused.json",
                "--acceptance",
                "criteria",
                "--review-policy",
                "independent",
                "--review-reason",
                "terminal review",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("cannot exceed", result.stderr)


class PMResourceLifecycleTests(unittest.TestCase):
    def test_pm_file_handles_close_without_resource_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm_dir = Path(tmp, ".pm")
            pm_dir.mkdir()
            config = pm_dir / "config.json"
            config.write_text(json.dumps({"ui_mode": "file"}), encoding="utf-8")
            artifact = Path(tmp, "artifact.md")
            artifact.write_text("TERMINAL: COMPLETE\n", encoding="utf-8")

            with (
                mock.patch.object(pm_board, "pmroot", return_value=str(pm_dir)),
                warnings.catch_warnings(record=True) as caught,
            ):
                warnings.simplefilter("always", ResourceWarning)
                self.assertEqual("file", pm_board._ui_mode())
                self.assertEqual(
                    "COMPLETE",
                    pm_board._one_artifact_field(str(artifact), "TERMINAL"),
                )
                pm_board.log("resource lifecycle regression")
                pm_board.cmd_standup(argparse.Namespace())
                pm_board.cmd_init(argparse.Namespace(debug=False, ui_mode="file"))
                gc.collect()

            self.assertEqual([], [warning for warning in caught if issubclass(
                warning.category, ResourceWarning
            )])


class TerminalArtifactParserTests(unittest.TestCase):
    def parse(self, raw):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "artifact.md")
            path.write_text(raw, encoding="utf-8")
            return pm_board._terminal_artifact(str(path), 0)

    def test_complete_schema(self):
        fields, error = self.parse(
            f"TERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:{COMMITMENT}\n"
        )
        self.assertIsNone(error)
        self.assertEqual("COMPLETE", fields["terminal"])

    def test_human_blocked_needs_exact_structured_request(self):
        fields, error = self.parse(
            f"TERMINAL: HUMAN_BLOCKED\nEVIDENCE-COMMITMENT: sha256:{COMMITMENT}\n"
            "HUMAN-REQUEST-KIND: credential\n"
            "HUMAN-REQUEST: Sign in to the private package registry.\n"
        )
        self.assertIsNone(error)
        self.assertEqual("credential", fields["human_request_kind"])

        _, missing = self.parse(
            f"TERMINAL: HUMAN_BLOCKED\nEVIDENCE-COMMITMENT: sha256:{COMMITMENT}\n"
        )
        self.assertIn("needs exactly one", missing)
        _, bad_kind = self.parse(
            f"TERMINAL: HUMAN_BLOCKED\nEVIDENCE-COMMITMENT: sha256:{COMMITMENT}\n"
            "HUMAN-REQUEST-KIND: test-failure\nHUMAN-REQUEST: Fix the tests for me.\n"
        )
        self.assertIn("needs exactly one", bad_kind)

    def test_complete_rejects_human_fields_and_duplicate_terminal(self):
        _, human = self.parse(
            f"TERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:{COMMITMENT}\n"
            "HUMAN-REQUEST-KIND: decision\nHUMAN-REQUEST: Pick an API shape.\n"
        )
        self.assertIn("must not carry", human)
        _, duplicate = self.parse(
            f"TERMINAL: REVISE\nTERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:{COMMITMENT}\n"
        )
        self.assertIn("exactly one canonical TERMINAL", duplicate)

    def test_commitment_is_required_and_strict(self):
        _, missing = self.parse("TERMINAL: COMPLETE\n")
        self.assertIn("EVIDENCE-COMMITMENT", missing)
        _, malformed = self.parse(
            "TERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:not-a-digest\n"
        )
        self.assertIn("EVIDENCE-COMMITMENT", malformed)

    def test_equal_mtime_is_not_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "artifact.md")
            path.write_text(
                f"TERMINAL: COMPLETE\nEVIDENCE-COMMITMENT: sha256:{COMMITMENT}\n",
                encoding="utf-8",
            )
            anchor = path.stat().st_mtime_ns
            fields, error = pm_board._terminal_artifact(str(path), anchor)
            self.assertIsNone(fields)
            self.assertIn("missing or stale", error)


class PMStateMachineTests(PMTestCase):
    def test_issue_ids_are_globally_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_board(tmp)
            second = self.run_pm(
                tmp,
                "milestone",
                "add",
                "m2",
                "Second milestone",
                "--acceptance",
                "Reviewer confirms the second milestone.",
                "--review-policy",
                "independent",
                "--review-reason",
                "universal terminal review",
            )
            self.assertEqual(0, second.returncode, second.stderr)
            # add_issue targets m1, so construct the cross-milestone duplicate explicitly.
            duplicate = self.run_pm(
                tmp,
                "issue",
                "i1",
                "m2",
                "Duplicate id",
                "--lane",
                "native-subagent",
                "--lane-reason",
                "single owner",
                "--estimated-model-calls",
                "1",
                "--max-model-calls",
                "2",
                "--promotion-triggers",
                "none",
                "--reasoning-posture",
                "balanced",
                "--task-class",
                "analysis",
                "--semantic-role",
                "anthropic_task",
                "--launch-receipt",
                ".pm/evidence/launch-receipts/unused.json",
                "--acceptance",
                "focused check",
                "--review-policy",
                "independent",
                "--review-reason",
                "terminal review",
            )
            self.assertNotEqual(0, duplicate.returncode)
            self.assertIn("globally unique", duplicate.stderr)
            self.assertFalse(Path(tmp, ".pm", "issues", "m2", "i1.md").exists())

    def test_set_enforces_forward_fsm_and_done_is_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_board(tmp)
            skipped = self.run_pm(tmp, "set", "issue", "i1", "review")
            self.assertNotEqual(0, skipped.returncode)
            self.assertIn("invalid status transition", skipped.stderr)

            started = self.run_pm(tmp, "set", "issue", "i1", "in_progress")
            self.assertEqual(0, started.returncode, started.stderr)
            review = self.run_pm(tmp, "set", "issue", "i1", "review")
            self.assertEqual(0, review.returncode, review.stderr)

            # Exercise terminal immutability without constructing a new review artifact: a
            # historical done item must not be assignable back into an active state.
            path = Path(tmp, ".pm", "issues", "m1", "i1.md")
            path.write_text(path.read_text().replace("status: review", "status: done", 1))
            for target in ("todo", "in_progress", "review", "reopened"):
                with self.subTest(target=target):
                    result = self.run_pm(tmp, "set", "issue", "i1", target)
                    self.assertNotEqual(0, result.returncode)
                    self.assertRegex(result.stderr.lower(), r"terminal|reopen")

    def test_reopen_is_the_only_review_rework_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_board(tmp)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            direct = self.run_pm(tmp, "set", "issue", "i1", "reopened")
            self.assertNotEqual(0, direct.returncode)
            self.assertIn("reopen", direct.stderr.lower())
            reopened = self.run_pm(
                tmp, "reopen", "issue", "i1", "--reason", "terminal reviewer requested revision"
            )
            self.assertEqual(0, reopened.returncode, reopened.stderr)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text()
            self.assertIn("status: reopened", issue)
            resumed = self.run_pm(tmp, "set", "issue", "i1", "in_progress")
            self.assertEqual(0, resumed.returncode, resumed.stderr)

    def test_report_is_preterminal_and_cannot_mutate_done_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_board(tmp)
            path = Path(tmp, ".pm", "issues", "m1", "i1.md")
            path.write_text(path.read_text().replace("status: todo", "status: done", 1))
            result = self.run_pm(
                tmp,
                "report",
                "issue",
                "i1",
                "--summary",
                "late report must not be accepted",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("terminal", result.stderr.lower())
            self.assertFalse(Path(tmp, ".pm", "reports", "i1.md").exists())

    def test_pr_backed_done_requires_exact_durable_merge_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_board(tmp)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(
                issue.read_text().replace(
                    "linear_id: ",
                    "linear_id: \npr_repository: owner/repo\npr_number: 12\n",
                    1,
                ),
                encoding="utf-8",
            )
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            self.write_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE")

            missing = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("merge receipt", missing.stderr.lower())

            receipt = Path(tmp, ".pm", "receipts", "merge", "issue-i1.json")
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(
                json.dumps(
                    {
                        "schema": "resolve-pr-merge-receipt/v1",
                        "transactionId": "merge-i1-12",
                        "itemKind": "issue",
                        "itemId": "i1",
                        "repository": "owner/repo",
                        "pr": 12,
                        "approvedHead": subprocess.check_output(
                            ["git", "rev-parse", "HEAD"], cwd=tmp, text=True
                        ).strip(),
                        "terminalCommitment": "sha256:" + "0" * 64,
                        "state": "MERGED",
                    }
                ) + "\n",
                encoding="utf-8",
            )
            mismatched = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--merge-receipt", str(receipt)
            )
            self.assertNotEqual(0, mismatched.returncode)
            self.assertRegex(mismatched.stderr.lower(), r"commitment|merge receipt")

    def test_human_blocked_requires_matching_send_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_board(tmp)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            self.write_report(tmp)
            request = "Contact the vendor to restore the unavailable account."
            self.write_independent_terminal(
                tmp,
                "HUMAN_BLOCKED",
                human_kind="external-action",
                human_request=request,
            )
            missing = self.run_pm(tmp, "set", "issue", "i1", "awaiting_human")
            self.assertNotEqual(0, missing.returncode)
            self.assertRegex(missing.stderr.lower(), r"send receipt|file mode|authenticated")
            self.assertIn(
                "status: review",
                Path(tmp, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8"),
            )

            receipt = self.write_human_send_receipt(tmp, request=request)
            forged = self.run_pm(
                tmp, "set", "issue", "i1", "awaiting_human", "--send-receipt", str(receipt)
            )
            self.assertNotEqual(0, forged.returncode)
            self.assertRegex(forged.stderr.lower(), r"file mode|authenticated|send receipt")
            self.assertIn(
                "status: review",
                Path(tmp, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8"),
            )

    def test_linear_close_needs_same_finalization_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_board(tmp)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(
                issue.read_text().replace("linear_id: ", "linear_id: linear-i1", 1),
                encoding="utf-8",
            )
            arbitrary = self.run_pm(tmp, "linear-close", "i1", env={**os.environ, "LINEAR_API_KEY": "x"})
            self.assertNotEqual(0, arbitrary.returncode)
            self.assertIn("finalization transaction", arbitrary.stderr.lower())


class WorkspaceDigestTests(PMTestCase):
    def digest(self, root):
        item = Path(root, ".pm", "goal.md")
        cwd = os.getcwd()
        try:
            os.chdir(root)
            return pm_board._workspace_digest(item, item.read_bytes())
        finally:
            os.chdir(cwd)

    def test_directory_symlink_path_and_type_are_bound(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as target,
        ):
            self.init_board(tmp)
            nested = Path(tmp, "nested")
            nested.mkdir()
            linked = nested / "first"
            linked.symlink_to(target, target_is_directory=True)
            first = self.digest(tmp)

            renamed = nested / "second"
            linked.rename(renamed)
            second = self.digest(tmp)
            self.assertNotEqual(first, second)

            renamed.unlink()
            renamed.mkdir()
            regular_directory = self.digest(tmp)
            self.assertNotEqual(second, regular_directory)

    def test_directory_symlink_target_contents_are_not_traversed(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as target,
        ):
            self.init_board(tmp)
            Path(tmp, "linked").symlink_to(target, target_is_directory=True)
            payload = Path(target, "outside.txt")
            payload.write_text("before\n", encoding="utf-8")
            before = self.digest(tmp)
            payload.write_text("after\n", encoding="utf-8")
            self.assertEqual(before, self.digest(tmp))

    def test_git_directory_symlink_remains_excluded(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as first_target,
            tempfile.TemporaryDirectory() as second_target,
        ):
            self.init_board(tmp)
            git_entry = Path(tmp, ".git")
            git_entry.symlink_to(first_target, target_is_directory=True)
            before = self.digest(tmp)
            git_entry.unlink()
            git_entry.symlink_to(second_target, target_is_directory=True)
            self.assertEqual(before, self.digest(tmp))


class TerminalStateTests(PMTestCase):
    def setup_issue(self, root, *, lane="native-subagent", policy="independent"):
        self.init_git(root)
        self.init_board(root)
        self.assertEqual(0, self.add_milestone(root).returncode)
        created = self.add_issue(root, lane=lane, policy=policy)
        self.assertEqual(0, created.returncode, created.stderr)
        self.assertEqual(0, self.run_pm(root, "set", "issue", "i1", "in_progress").returncode)
        self.assertEqual(0, self.run_pm(root, "set", "issue", "i1", "review").returncode)
        Path(root, "deliverable.txt").write_text("implemented\n", encoding="utf-8")
        self.write_report(root)

    def test_done_accepts_only_complete(self):
        for terminal, succeeds in (("COMPLETE", True), ("REVISE", False), ("HUMAN_BLOCKED", False)):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as tmp:
                self.setup_issue(tmp)
                if terminal == "HUMAN_BLOCKED":
                    self.write_independent_terminal(
                        tmp,
                        terminal,
                        human_kind="authorization",
                        human_request="Approve deployment to production.",
                    )
                else:
                    self.write_independent_terminal(tmp, terminal)
                result = self.run_pm(tmp, "set", "issue", "i1", "done")
                self.assertEqual(succeeds, result.returncode == 0, result.stderr)
                if not succeeds:
                    self.assertIn("requires TERMINAL: COMPLETE", result.stderr)

    def test_awaiting_human_accepts_only_structured_human_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.set_ui_mode(tmp, "interactive")
            self.write_independent_terminal(
                tmp,
                "HUMAN_BLOCKED",
                human_kind="external-action",
                human_request="Contact the vendor to restore the unavailable account.",
            )
            result = self.run_pm(tmp, "set", "issue", "i1", "awaiting_human")
            self.assertEqual(0, result.returncode, result.stderr)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text()
            self.assertIn("status: awaiting_human", issue)
            # The exact dependency is part of the one atomic board transition; terminal routing
            # must not append a second, unreviewed PM-log mutation.
            self.assertIn("human_request_kind: external-action", issue)
            self.assertIn("human_request: Contact the vendor", issue)
            self.assertIn("human_request_key: issue-i1-session", issue)
            self.assertIn("expected_human_id: controlling-session", issue)
            self.assertFalse(Path(tmp, ".pm", "log.md").exists())

    def test_linear_done_enters_recoverable_finalization_transaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            config = Path(tmp, ".pm", "config.json")
            config.write_text('{"debug": false, "ui_mode": "linear"}\n', encoding="utf-8")
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(issue.read_text().replace("linear_id: ", "linear_id: linear-i1", 1))
            self.write_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE")
            result = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--transaction-id", "finish-i1-1"
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("status: finalizing", issue.read_text())
            receipt = Path(tmp, ".pm", "finalizations", "issue-i1.json")
            self.assertTrue(receipt.is_file())
            self.assertEqual("pending-linear-close", json.loads(receipt.read_text())["state"])

    def test_forged_linear_closed_state_cannot_skip_authenticated_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            config = Path(tmp, ".pm", "config.json")
            config.write_text('{"debug": false, "ui_mode": "linear"}\n', encoding="utf-8")
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(issue.read_text().replace("linear_id: ", "linear_id: linear-i1", 1))
            self.write_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE")
            started = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--transaction-id", "finish-i1-1"
            )
            self.assertEqual(0, started.returncode, started.stderr)
            receipt_path = Path(tmp, ".pm", "finalizations", "issue-i1.json")
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual("pending-linear-close", receipt["state"])
            receipt["state"] = "linear-closed"
            receipt["linearReadBack"] = {
                "linearId": "linear-i1",
                "stateType": "completed",
                "idempotent": True,
            }
            receipt_path.write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ):
                    os.environ.pop("LINEAR_API_KEY", None)
                    with mock.patch.object(pm_board, "_linear_close") as close, self.assertRaises(
                        SystemExit
                    ):
                        pm_board._finish_linear_finalization("issue", "i1", "finish-i1-1")
                    close.assert_not_called()
                self.assertEqual("finalizing", pm_board.read(issue)[0]["status"])

                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board, "_linear_close", side_effect=RuntimeError("remote unavailable")
                ) as close, self.assertRaises(SystemExit):
                    pm_board._finish_linear_finalization("issue", "i1", "finish-i1-1")
                close.assert_called_once()
                self.assertEqual("finalizing", pm_board.read(issue)[0]["status"])

                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board,
                    "_linear_close",
                    return_value={
                        "linearId": "linear-i1",
                        "stateType": "completed",
                        "idempotent": False,
                    },
                ) as close:
                    pm_board._finish_linear_finalization("issue", "i1", "finish-i1-1")
                close.assert_called_once()
                self.assertEqual("done", pm_board.read(issue)[0]["status"])
                finished = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.assertEqual("linear-closed", finished["state"])
                self.assertEqual(
                    {"linearId": "linear-i1", "stateType": "completed", "idempotent": False},
                    finished["linearReadBack"],
                )
                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board,
                    "_linear_close",
                    return_value={
                        "linearId": "linear-i1",
                        "stateType": "completed",
                        "idempotent": True,
                    },
                ) as close:
                    pm_board._finish_linear_finalization("issue", "i1", "finish-i1-1")
                close.assert_called_once()
            finally:
                os.chdir(cwd)
            self.assertEqual("done", pm_board.read(issue)[0]["status"])
            retried = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual("linear-closed", retried["state"])
            self.assertEqual(
                {"linearId": "linear-i1", "stateType": "completed", "idempotent": True},
                retried["linearReadBack"],
            )

    def test_linear_finalization_failure_stays_incomplete_and_exact_retry_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            config = Path(tmp, ".pm", "config.json")
            config.write_text('{"debug": false, "ui_mode": "linear"}\n', encoding="utf-8")
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(issue.read_text().replace("linear_id: ", "linear_id: linear-i1", 1))
            self.write_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE")
            started = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--transaction-id", "finish-i1-1"
            )
            self.assertEqual(0, started.returncode, started.stderr)

            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board, "_linear_close", side_effect=RuntimeError("remote unavailable")
                ), self.assertRaises(SystemExit):
                    pm_board._finish_linear_finalization("issue", "i1", "finish-i1-1")
                self.assertEqual("finalizing", pm_board.read(issue)[0]["status"])
                with self.assertRaises(SystemExit):
                    pm_board._finish_linear_finalization("issue", "i1", "different-transaction")
                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board,
                    "_linear_close",
                    return_value={"linearId": "linear-i1", "stateType": "completed", "idempotent": False},
                ):
                    pm_board._finish_linear_finalization("issue", "i1", "finish-i1-1")
            finally:
                os.chdir(cwd)
            self.assertEqual("done", pm_board.read(issue)[0]["status"])
            receipt = json.loads(
                Path(tmp, ".pm", "finalizations", "issue-i1.json").read_text()
            )
            self.assertEqual("linear-closed", receipt["state"])

    def test_linear_finalization_rejects_directory_symlink_retarget(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as first_target,
            tempfile.TemporaryDirectory() as second_target,
        ):
            self.setup_issue(tmp)
            config = Path(tmp, ".pm", "config.json")
            config.write_text('{"debug": false, "ui_mode": "linear"}\n', encoding="utf-8")
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(issue.read_text().replace("linear_id: ", "linear_id: linear-i1", 1))
            linked = Path(tmp, "nested", "deliverables")
            linked.parent.mkdir()
            linked.symlink_to(first_target, target_is_directory=True)
            self.write_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE")
            started = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--transaction-id", "finish-i1-1"
            )
            self.assertEqual(0, started.returncode, started.stderr)

            linked.unlink()
            linked.symlink_to(second_target, target_is_directory=True)
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board,
                    "_linear_close",
                    return_value={
                        "linearId": "linear-i1",
                        "stateType": "completed",
                        "idempotent": False,
                    },
                ) as close, self.assertRaises(SystemExit) as raised:
                    pm_board._finish_linear_finalization("issue", "i1", "finish-i1-1")
                close.assert_not_called()
            finally:
                os.chdir(cwd)
            self.assertIn("drifted", str(raised.exception).lower())
            self.assertEqual("finalizing", pm_board.read(issue)[0]["status"])

    def test_linear_receipt_ignores_poisoned_path_and_git_redirection(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as redirected:
            self.setup_issue(tmp)
            replaced_head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=tmp, text=True
            ).strip()
            Path(tmp, "replacement-target.txt").write_text("replacement target\n", encoding="utf-8")
            subprocess.run(["git", "add", "replacement-target.txt"], cwd=tmp, check=True)
            subprocess.run(["git", "commit", "-qm", "replacement target"], cwd=tmp, check=True)
            expected_head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=tmp, text=True
            ).strip()
            subprocess.run(["git", "replace", expected_head, replaced_head], cwd=tmp, check=True)
            self.init_git(redirected)
            config = Path(tmp, ".pm", "config.json")
            config.write_text('{"debug": false, "ui_mode": "linear"}\n', encoding="utf-8")
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(issue.read_text().replace("linear_id: ", "linear_id: linear-i1", 1))
            shim_dir = Path(tmp, "git-shim")
            shim_dir.mkdir()
            shim = shim_dir / "git"
            shim.write_text("#!/bin/sh\nprintf '%s\\n' 'f'$(printf 'f%.0s' $(seq 1 39))\n", encoding="utf-8")
            shim.chmod(0o755)
            self.write_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE")
            env = dict(os.environ)
            env.update({
                "PATH": str(shim_dir) + os.pathsep + env.get("PATH", ""),
                "GIT_DIR": str(Path(redirected, ".git")),
                "GIT_WORK_TREE": redirected,
                "GIT_REPLACE_REF_BASE": str(Path(tmp, ".git", "refs", "replace")),
            })
            result = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--transaction-id", "finish-i1-1", env=env
            )
            self.assertEqual(0, result.returncode, result.stderr)
            receipt = json.loads(
                Path(tmp, ".pm", "finalizations", "issue-i1.json").read_text(encoding="utf-8")
            )
            self.assertEqual(expected_head, receipt["reviewedHead"])

    def test_provenance_binding_refusal_precedes_receipt_or_board_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(issue.read_text().replace("linear_id: ", "linear_id: linear-i1", 1))
            artifact = Path(tmp, "terminal-artifact.md")
            artifact.write_text("TERMINAL: COMPLETE\n", encoding="utf-8")
            meta, body = pm_board.read(issue)
            before = issue.read_bytes()
            args = argparse.Namespace(kind="issue", id="i1", transaction_id="finish-i1-1")
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.object(pm_board, "TRUSTED_GIT_CANDIDATES", ()), mock.patch.object(
                    pm_board, "_TRUSTED_GIT_BINDING", None
                ), self.assertRaises(SystemExit) as raised:
                    pm_board._begin_linear_finalization(
                        args,
                        str(issue),
                        meta,
                        body,
                        {"evidence_commitment": COMMITMENT},
                        str(artifact),
                        "",
                    )
            finally:
                os.chdir(cwd)
            self.assertIn("trusted system git", str(raised.exception).lower())
            self.assertEqual(before, issue.read_bytes())
            self.assertFalse(Path(tmp, ".pm", "finalizations", "issue-i1.json").exists())

    def test_report_refuses_symlinked_parent_without_writing_outside(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            self.setup_board(tmp)
            outside = Path(outside_raw, "reports")
            outside.mkdir()
            target = outside / "i1.md"
            target.write_text("outside report\n", encoding="utf-8")
            Path(tmp, ".pm", "reports").symlink_to(outside, target_is_directory=True)
            result = self.run_pm(tmp, "report", "issue", "i1", "--summary", "new report")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"refused|symlink|no-follow|link")
            self.assertEqual("outside report\n", target.read_text(encoding="utf-8"))

    def test_report_refuses_hardlinked_leaf_without_writing_outside(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            self.setup_board(tmp)
            outside = Path(outside_raw, "report.md")
            outside.write_text("outside report\n", encoding="utf-8")
            reports = Path(tmp, ".pm", "reports")
            reports.mkdir()
            os.link(outside, reports / "i1.md")
            result = self.run_pm(tmp, "report", "issue", "i1", "--summary", "new report")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"refused|hardlink|link")
            self.assertEqual("outside report\n", outside.read_text(encoding="utf-8"))

    def test_finalization_refuses_symlinked_parent_without_board_or_outside_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            self.setup_board(tmp)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            outside = Path(outside_raw, "finalizations")
            outside.mkdir()
            target = outside / "issue-i1.json"
            target.write_text('{"outside":true}\n', encoding="utf-8")
            Path(tmp, ".pm", "finalizations").symlink_to(outside, target_is_directory=True)
            before = issue.read_bytes()
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with self.assertRaises(SystemExit) as raised:
                    pm_board._write_json(
                        str(Path(tmp, ".pm", "finalizations", "issue-i1.json")),
                        {"transactionId": "finish-i1-1"},
                    )
            finally:
                os.chdir(cwd)
            self.assertRegex(str(raised.exception).lower(), r"refused|symlink|no-follow|link")
            self.assertEqual(before, issue.read_bytes())
            self.assertEqual('{"outside":true}\n', target.read_text(encoding="utf-8"))

    def test_finalizing_cannot_bypass_original_transaction_with_set_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(issue.read_text().replace("status: review", "status: finalizing", 1))
            bypass = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--transaction-id", "new-transaction"
            )
            self.assertNotEqual(0, bypass.returncode)
            self.assertIn("invalid status transition", bypass.stderr)
            self.assertIn("status: finalizing", issue.read_text())

    def test_missing_stale_or_duplicate_artifact_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            missing = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("missing or stale", missing.stderr)
            self.write_terminal(tmp, "COMPLETE", duplicate=True)
            duplicate = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, duplicate.returncode)
            self.assertIn("exactly one canonical TERMINAL", duplicate.stderr)

    def test_report_must_precede_terminal_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_git(tmp)
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp).returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            self.write_terminal(tmp, "COMPLETE")
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("REPORTED", result.stderr)

    def test_independent_artifact_rejects_post_review_byte_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.write_independent_terminal(tmp, "COMPLETE")
            Path(tmp, "deliverable.txt").write_text("changed after review\n", encoding="utf-8")
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("no longer binds", result.stderr)
            self.assertTrue(
                "commitment drift" in result.stderr or "evidence" in result.stderr and "stale" in result.stderr,
                result.stderr,
            )


class MilestoneLifecycleTests(PMTestCase):
    def setup_milestone(self, root):
        self.init_git(root)
        self.init_board(root)
        self.assertEqual(0, self.add_milestone(root).returncode)
        self.assertEqual(0, self.run_pm(root, "set", "milestone", "m1", "in_progress").returncode)
        self.assertEqual(0, self.run_pm(root, "set", "milestone", "m1", "review").returncode)

    def test_self_evolution_is_sealed_before_final_review_and_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_milestone(tmp)

            no_checkpoint = self.run_pm(tmp, "set", "milestone", "m1", "done")
            self.assertNotEqual(0, no_checkpoint.returncode)
            self.assertIn("SEALED mandatory self-evolution", no_checkpoint.stderr)

            opened = self.run_pm(tmp, "milestone-evolve", "m1")
            self.assertEqual(0, opened.returncode, opened.stderr)
            self.assertTrue(Path(tmp, ".pm", "self_evolve", "m1.pending").is_file())

            # Prove the old order is rejected at PREPARE: terminal review is impossible while
            # self-evolution remains pending.
            self.write_milestone_report(tmp)
            base = subprocess.check_output(
                ["git", "rev-list", "--max-parents=0", "HEAD"], cwd=tmp, text=True
            ).strip()
            pre_seal_review = subprocess.run(
                [
                    "python3",
                    str(MILESTONE_REVIEW),
                    "m1",
                    "--base",
                    base,
                    "--builder-role",
                    "anthropic_task",
                    "--reviewer-role",
                    "codex_advisor",
                    "--allow-empty",
                ],
                cwd=tmp,
                capture_output=True,
                text=True,
            )
            self.assertEqual(2, pre_seal_review.returncode)
            self.assertIn("self-evolution is still pending", pre_seal_review.stderr)
            Path(tmp, ".memory").mkdir()
            Path(tmp, ".memory", "project-observation.md").write_text(
                "Project-only observation from the completed milestone.\n", encoding="utf-8"
            )
            self.seal_self_evolution(tmp)
            self.assertFalse(Path(tmp, ".pm", "self_evolve", "m1.pending").exists())
            self.assertTrue(Path(tmp, ".pm", "self_evolve", "m1.sealed").is_file())
            stale = self.run_pm(tmp, "set", "milestone", "m1", "done")
            self.assertNotEqual(0, stale.returncode)
            self.assertIn("needs a FRESH write-up REPORTED", stale.stderr)

            # Final evidence/report/review is deliberately after every self-evolution mutation.
            self.write_milestone_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE", iid="m1", kind="milestone")
            completed = self.run_pm(tmp, "set", "milestone", "m1", "done")
            self.assertEqual(0, completed.returncode, completed.stderr)
            milestone = Path(tmp, ".pm", "milestones", "m1.md").read_text(encoding="utf-8")
            self.assertIn("status: done", milestone)
            self.assertTrue(Path(tmp, ".pm", "self_evolve", "m1.done").is_file())
            self.assertFalse(Path(tmp, ".pm", "self_evolve", "m1.sealed").exists())

    def test_revise_reopens_and_archives_the_prior_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_milestone(tmp)
            self.assertEqual(0, self.run_pm(tmp, "milestone-evolve", "m1").returncode)
            self.seal_self_evolution(tmp)

            reopened = self.run_pm(
                tmp,
                "milestone-evolve",
                "m1",
                "--reopen",
                "--reason",
                "terminal reviewer requested another implementation round",
            )
            self.assertEqual(0, reopened.returncode, reopened.stderr)
            self.assertTrue(Path(tmp, ".pm", "self_evolve", "m1.pending").is_file())
            self.assertTrue(Path(tmp, ".pm", "self_evolve", "m1.superseded.1").is_file())
            self.assertFalse(Path(tmp, ".pm", "self_evolve", "m1.sealed").exists())
            cannot_finish = self.run_pm(tmp, "set", "milestone", "m1", "done")
            self.assertNotEqual(0, cannot_finish.returncode)
            self.assertIn("SEALED mandatory self-evolution", cannot_finish.stderr)

    def test_done_recovers_after_token_archival_before_board_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_milestone(tmp)
            self.assertEqual(0, self.run_pm(tmp, "milestone-evolve", "m1").returncode)
            self.seal_self_evolution(tmp)
            self.write_milestone_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE", iid="m1", kind="milestone")

            lifecycle = Path(tmp, ".pm", "self_evolve")
            # Fault injection: set-done archived the token, then crashed before replacing the
            # milestone board file. A retry must revalidate the same bound bytes and finish.
            (lifecycle / "m1.sealed").replace(lifecycle / "m1.done")
            milestone = Path(tmp, ".pm", "milestones", "m1.md")
            self.assertIn("status: review", milestone.read_text(encoding="utf-8"))

            recovered = self.run_pm(tmp, "set", "milestone", "m1", "done")
            self.assertEqual(0, recovered.returncode, recovered.stderr)
            self.assertIn("status: done", milestone.read_text(encoding="utf-8"))
            self.assertTrue((lifecycle / "m1.done").is_file())

    def test_milestone_done_requires_every_child_issue_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_milestone(tmp)
            self.assertEqual(0, self.add_issue(tmp).returncode)
            self.assertEqual(0, self.run_pm(tmp, "milestone-evolve", "m1").returncode)
            self.seal_self_evolution(tmp)
            self.write_report(tmp)
            self.write_milestone_report(tmp)
            self.write_independent_terminal(tmp, "COMPLETE", iid="m1", kind="milestone")

            result = self.run_pm(tmp, "set", "milestone", "m1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("child issues", result.stderr)
            self.assertIn("i1=todo", result.stderr)
            self.assertTrue(Path(tmp, ".pm", "self_evolve", "m1.sealed").is_file())
            self.assertFalse(Path(tmp, ".pm", "self_evolve", "m1.done").exists())

    def test_reopen_rejects_done_checkpoint_without_corrupting_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_milestone(tmp)
            lifecycle = Path(tmp, ".pm", "self_evolve")
            lifecycle.mkdir(parents=True, exist_ok=True)
            (lifecycle / "m1.done").write_text("archived\n", encoding="utf-8")
            milestone = Path(tmp, ".pm", "milestones", "m1.md")
            milestone.write_text(milestone.read_text().replace("status: review", "status: done", 1))

            result = self.run_pm(
                tmp, "reopen", "milestone", "m1", "--reason", "attempt invalid rollback"
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("terminal", result.stderr.lower())
            self.assertIn("status: done", milestone.read_text())
            self.assertTrue((lifecycle / "m1.done").is_file())
            self.assertFalse((lifecycle / "m1.pending").exists())


class WorkflowLaunchBindingTests(PMTestCase):
    def create_workflow_issue(self, root, **kwargs):
        self.init_board(root)
        self.assertEqual(0, self.add_milestone(root).returncode)
        created = self.add_workflow_issue(root, **kwargs)
        self.assertEqual(0, created.returncode, created.stderr)
        return created

    def test_workflow_managed_placeholder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            result = self.run_pm(
                tmp,
                "issue",
                "i1",
                "m1",
                "Issue",
                "--lane",
                "workflow",
                "--lane-reason",
                "graph state",
                "--estimated-model-calls",
                "3",
                "--max-model-calls",
                "6",
                "--promotion-triggers",
                "none",
                "--acceptance",
                "focused check",
                "--review-policy",
                "machine-gated",
                "--review-reason",
                "graph reviewer",
                "--reasoning-posture",
                "workflow",
                "--task-class",
                "workflow",
                "--semantic-role",
                "design-bound",
                "--launch-receipt",
                "workflow-managed",
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("launch-plan", result.stderr)
            self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def test_issue_creation_binds_plan_without_claiming_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text()
            self.assertIn("launch_plan_path: .pm/evidence/workflow-launch-plans/i1.json", issue)
            self.assertIn("receipt_manifest_path: unbound", issue)
            self.assertIn("receipt_manifest_sha256: unbound", issue)
            self.assertNotIn("workflow-managed", issue)
            self.assertNotIn("launch_receipt_sha256: sha256:", issue)
            self.assertFalse(any(Path(tmp, ".pm", "evidence", "launch-receipts").glob("*.json")))
            started = self.run_pm(tmp, "set", "issue", "i1", "in_progress")
            self.assertEqual(0, started.returncode, started.stderr)
            review = self.run_pm(tmp, "set", "issue", "i1", "review")
            self.assertEqual(0, review.returncode, review.stderr)

    def test_launch_plan_rejects_symlinked_parent_for_immutable_inputs(self):
        parents = {
            "policy": Path("config/workflows"),
            "design": Path(".workflow/designs"),
            "mapping": Path(".subagent/flows"),
            "launch-plan": Path(".pm/evidence/workflow-launch-plans"),
        }
        for label, relative_parent in parents.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp, "repo")
                root.mkdir()
                self.init_board(root)
                self.assertEqual(0, self.add_milestone(root).returncode)
                plan_path, _ = self.write_launch_plan(root)
                original_parent = root / relative_parent
                outside_parent = Path(tmp, "outside", label)
                outside_parent.parent.mkdir(parents=True, exist_ok=True)
                original_parent.rename(outside_parent)
                original_parent.symlink_to(outside_parent, target_is_directory=True)
                sentinel = outside_parent / "outside-sentinel.txt"
                sentinel.write_text("must remain unchanged\n", encoding="utf-8")
                before = sentinel.read_bytes()

                result = self.add_workflow_issue(root, plan_path=plan_path)

                self.assertNotEqual(0, result.returncode, f"followed {label} parent symlink")
                self.assertRegex(result.stderr.lower(), r"regular|symlink|cannot read|refused")
                self.assertEqual(before, sentinel.read_bytes())
                self.assertFalse(Path(root, ".pm", "issues", "m1", "i1.md").exists())

    def test_launch_plan_rejects_omitted_model_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            result = self.add_workflow_issue(
                tmp, leaves=[self.default_workflow_leaves()[0]]
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("missing model leaf", result.stderr)

    def test_launch_plan_rejects_extra_model_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            leaves = self.default_workflow_leaves() + [
                {
                    "leafId": "invented",
                    "taskClass": "analysis",
                    "reasoningPosture": "balanced",
                    "semanticRole": "anthropic_task",
                    "reviewMode": "none",
                    "writerRole": None,
                }
            ]
            result = self.add_workflow_issue(tmp, leaves=leaves)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("extra model leaf", result.stderr)

    def test_launch_plan_rejects_wrong_role_class_writer_and_posture_split(self):
        mutations = {
            "role": lambda leaves: leaves[0].update(semanticRole="codex_advisor"),
            "class": lambda leaves: leaves[0].update(taskClass="verification"),
            "writer": lambda leaves: leaves[1].update(writerRole="codex_advisor"),
            "posture": lambda leaves: leaves[1].update(reasoningPosture="economy"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                self.init_board(tmp)
                self.assertEqual(0, self.add_milestone(tmp).returncode)
                leaves = [dict(leaf) for leaf in self.default_workflow_leaves()]
                mutate(leaves)
                result = self.add_workflow_issue(tmp, leaves=leaves)
                self.assertNotEqual(0, result.returncode)
                self.assertRegex(
                    result.stderr,
                    r"semanticRole|taskClass|writerRole|global reasoningPosture",
                )

    def test_launch_plan_rejects_missing_extra_and_mismatched_mapping_model_binding(self):
        mutations = {
            "missing": lambda mapping: mapping["handlers"].pop("review"),
            "extra": lambda mapping: mapping["handlers"].update(
                {"invented": {"type": "agent", "prompt": "not admitted"}}
            ),
            "mismatch": lambda mapping: mapping["handlers"]["review"].update(
                {"type": "agent"}
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                self.init_board(tmp)
                self.assertEqual(0, self.add_milestone(tmp).returncode)
                result = self.add_workflow_issue(tmp, mutate_mapping=mutate)
                self.assertNotEqual(0, result.returncode)
                self.assertRegex(result.stderr, r"mapping|handler|model")

    def test_launch_plan_requires_valid_subagent_mapping_reasoning_posture(self):
        mutations = {
            "missing": lambda mapping: mapping.pop("reasoningPosture"),
            "blank": lambda mapping: mapping.update(reasoningPosture="   "),
            "non-string": lambda mapping: mapping.update(reasoningPosture=None),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                self.init_board(tmp)
                self.assertEqual(0, self.add_milestone(tmp).returncode)
                result = self.add_workflow_issue(tmp, mutate_mapping=mutate)
                self.assertNotEqual(0, result.returncode)
                self.assertRegex(
                    result.stderr,
                    r"mapping reasoningPosture must be a non-empty string",
                )

    def test_launch_plan_rejects_unknown_subagent_mapping_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            result = self.add_workflow_issue(
                tmp,
                mutate_mapping=lambda mapping: mapping.update(unexpected=True),
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("unsupported keys ['unexpected']", result.stderr)

    def test_launch_plan_posture_must_match_subagent_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            result = self.add_workflow_issue(
                tmp,
                mutate_mapping=lambda mapping: mapping.update(reasoningPosture="economy"),
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(
                result.stderr,
                r"mapping reasoningPosture .* disagrees with .* launch-plan reasoningPosture",
            )

    def mapping_adapters(self):
        return {
            "subagent": (
                None,
                Path(".subagent", "flows", "review-flow.json"),
                "subagent mapping",
            ),
            "humanize": (
                self.write_humanize_mapping,
                Path(".humanize", "mappings", "review-flow.json"),
                "Humanize mapping",
            ),
        }

    def test_launch_plan_accepts_canonical_bare_hex_mapping_design_digest(self):
        for label, (factory, relative, _) in self.mapping_adapters().items():
            with self.subTest(adapter=label), tempfile.TemporaryDirectory() as tmp:
                self.init_board(tmp)
                self.assertEqual(0, self.add_milestone(tmp).returncode)
                plan_path, _ = self.write_launch_plan(tmp, mapping_factory=factory)

                created = self.add_workflow_issue(tmp, plan_path=plan_path)

                self.assertEqual(0, created.returncode, created.stderr)
                design = Path(tmp, ".workflow", "designs", "review-flow.workflow.json")
                mapping = json.loads(Path(tmp, relative).read_text(encoding="utf-8"))
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                self.assertEqual(
                    hashlib.sha256(design.read_bytes()).hexdigest(), mapping["designSha256"]
                )
                self.assertEqual("sha256:" + mapping["designSha256"], plan["designSha256"])

    def test_launch_plan_rejects_prefixed_mapping_design_digest(self):
        for label, (factory, _, error_label) in self.mapping_adapters().items():
            with self.subTest(adapter=label), tempfile.TemporaryDirectory() as tmp:
                self.init_board(tmp)
                self.assertEqual(0, self.add_milestone(tmp).returncode)
                plan_path, _ = self.write_launch_plan(
                    tmp,
                    mapping_factory=factory,
                    mutate_mapping=lambda mapping: mapping.update(
                        designSha256="sha256:" + mapping["designSha256"]
                    ),
                )

                result = self.add_workflow_issue(tmp, plan_path=plan_path)

                self.assertNotEqual(0, result.returncode)
                self.assertIn(f"{error_label} designSha256 must be bare", result.stderr)
                self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def test_launch_plan_rejects_stale_bare_hex_mapping_design_digest(self):
        for label, (factory, _, error_label) in self.mapping_adapters().items():
            with self.subTest(adapter=label), tempfile.TemporaryDirectory() as tmp:
                self.init_board(tmp)
                self.assertEqual(0, self.add_milestone(tmp).returncode)
                plan_path, _ = self.write_launch_plan(
                    tmp,
                    mapping_factory=factory,
                    mutate_mapping=lambda mapping: mapping.update(designSha256="0" * 64),
                )

                result = self.add_workflow_issue(tmp, plan_path=plan_path)

                self.assertNotEqual(0, result.returncode)
                self.assertIn(
                    f"{error_label} designSha256 does not match the admitted design",
                    result.stderr,
                )
                self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def test_launch_plan_rejects_stale_roles_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)
            policy = Path(tmp, "config", "workflows", "models.json")
            document = json.loads(policy.read_text(encoding="utf-8"))
            document["reasoningPolicy"]["defaultPosture"] = "frontier"
            policy.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            refused = self.run_pm(tmp, "set", "issue", "i1", "review")
            self.assertNotEqual(0, refused.returncode)
            self.assertRegex(refused.stderr.lower(), r"policy|stale|digest")

    def test_report_and_terminal_close_refuse_unbound_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            reported = self.run_pm(
                tmp,
                "report",
                "issue",
                "i1",
                "--summary",
                "workflow finished without native launch receipts",
            )
            self.assertNotEqual(0, reported.returncode)
            self.assertRegex(reported.stderr.lower(), r"unbound|receipt")
            self.assertFalse(Path(tmp, ".pm", "reports", "i1.md").exists())

    def test_bind_then_report_reconciles_adapter_emitted_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            bound = self.bind_workflow_receipts(tmp)
            self.assertEqual(0, bound.returncode, bound.stderr)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text()
            self.assertIn(
                "receipt_manifest_path: .pm/evidence/workflow-receipt-manifests/i1.json",
                issue,
            )
            self.assertNotIn("receipt_manifest_sha256: unbound", issue)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            self.write_report(tmp)
            self.assertTrue(Path(tmp, ".pm", "reports", "i1.md").is_file())

    def test_bind_rejects_symlinked_parent_for_pm_evidence_without_mutation(self):
        parents = {
            "launch-plan": Path(".pm/evidence/workflow-launch-plans"),
            "receipt-manifest": Path(".pm/evidence/workflow-receipt-manifests"),
            "native-receipt": Path(".pm/evidence/launch-receipts"),
        }
        for label, relative_parent in parents.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp, "repo")
                root.mkdir()
                self.create_workflow_issue(root)
                manifest = self.write_receipt_manifest(root)
                issue = Path(root, ".pm", "issues", "m1", "i1.md")
                issue_before = issue.read_bytes()
                original_parent = root / relative_parent
                outside_parent = Path(tmp, "outside", label)
                outside_parent.parent.mkdir(parents=True, exist_ok=True)
                original_parent.rename(outside_parent)
                original_parent.symlink_to(outside_parent, target_is_directory=True)
                sentinel = outside_parent / "outside-sentinel.txt"
                sentinel.write_text("must remain unchanged\n", encoding="utf-8")
                before = sentinel.read_bytes()

                result = self.run_pm(
                    root,
                    "bind-workflow-receipts",
                    "i1",
                    "--receipt-manifest",
                    manifest.relative_to(root).as_posix(),
                )

                self.assertNotEqual(0, result.returncode, f"followed {label} parent symlink")
                self.assertRegex(result.stderr.lower(), r"regular|symlink|cannot read|refused")
                self.assertEqual(before, sentinel.read_bytes())
                self.assertEqual(issue_before, issue.read_bytes())

    def test_missing_leaf_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)
            bound = self.bind_workflow_receipts(
                tmp,
                mutate=lambda _root, manifest: manifest["leaves"].pop(),
            )
            self.assertNotEqual(0, bound.returncode)
            self.assertRegex(bound.stderr.lower(), r"missing|reconcile|leaf")

    def test_extra_leaf_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)

            def extra(_root, manifest):
                extra_receipt = self.write_launch_receipt(
                    tmp,
                    name="i1-extra",
                    task_class="analysis",
                    semantic_role="anthropic_task",
                )
                manifest["leaves"].append({
                    "leafId": "extra-1",
                    "receiptPath": extra_receipt.relative_to(tmp).as_posix(),
                    "receiptSha256": "sha256:" + hashlib.sha256(extra_receipt.read_bytes()).hexdigest(),
                })

            bound = self.bind_workflow_receipts(tmp, mutate=extra)
            self.assertNotEqual(0, bound.returncode)
            self.assertRegex(bound.stderr.lower(), r"extra|reconcile|leaf")

    def test_swapped_leaf_receipts_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)

            def swap(_root, manifest):
                first, second = manifest["leaves"]
                first["receiptPath"], second["receiptPath"] = second["receiptPath"], first["receiptPath"]
                first["receiptSha256"], second["receiptSha256"] = (
                    second["receiptSha256"], first["receiptSha256"]
                )

            bound = self.bind_workflow_receipts(tmp, mutate=swap)
            self.assertNotEqual(0, bound.returncode)
            self.assertRegex(bound.stderr.lower(), r"swap|request|does not match|reconcile")

    def test_stale_receipt_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)
            self.assertEqual(0, self.bind_workflow_receipts(tmp).returncode)
            receipt = Path(tmp, ".pm", "evidence", "launch-receipts", "i1-implement.json")
            receipt.write_bytes(receipt.read_bytes() + b"\n")
            reported = self.run_pm(
                tmp, "report", "issue", "i1", "--summary", "stale receipt bytes"
            )
            self.assertNotEqual(0, reported.returncode)
            self.assertRegex(reported.stderr.lower(), r"stale|changed|digest")

    def test_malformed_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)

            def malformed(root, manifest):
                path = Path(root, manifest["leaves"][0]["receiptPath"])
                path.write_text("{not-json\n", encoding="utf-8")
                manifest["leaves"][0]["receiptSha256"] = "sha256:" + hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()

            bound = self.bind_workflow_receipts(tmp, mutate=malformed)
            self.assertNotEqual(0, bound.returncode)
            self.assertRegex(bound.stderr.lower(), r"malformed|json|schema|cannot read")

    def test_self_digest_invalid_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)

            def invalidate(root, manifest):
                path = Path(root, manifest["leaves"][0]["receiptPath"])
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["receiptSha256"] = "sha256:" + "0" * 64
                path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                manifest["leaves"][0]["receiptSha256"] = "sha256:" + hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()

            bound = self.bind_workflow_receipts(tmp, mutate=invalidate)
            self.assertNotEqual(0, bound.returncode)
            self.assertRegex(bound.stderr.lower(), r"self-digest|self digest")

    def test_policy_stale_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)
            self.assertEqual(0, self.bind_workflow_receipts(tmp).returncode)
            roles = json.loads(Path(tmp, "config", "workflows", "models.json").read_text())
            roles["roles"]["anthropic_task"]["effort"] = "max"
            Path(tmp, "config", "workflows", "models.json").write_text(
                json.dumps(roles, indent=2) + "\n", encoding="utf-8"
            )
            reported = self.run_pm(
                tmp, "report", "issue", "i1", "--summary", "policy drifted after launch"
            )
            self.assertNotEqual(0, reported.returncode)
            self.assertRegex(reported.stderr.lower(), r"stale|policy|does not bind")

    def test_alternate_path_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)

            def relocate(root, manifest):
                original = Path(root, manifest["leaves"][0]["receiptPath"])
                alternate = Path(root, "outside", "copied-receipt.json")
                alternate.parent.mkdir(parents=True)
                alternate.write_bytes(original.read_bytes())
                manifest["leaves"][0]["receiptPath"] = alternate.relative_to(root).as_posix()

            bound = self.bind_workflow_receipts(tmp, mutate=relocate)
            self.assertNotEqual(0, bound.returncode)
            self.assertRegex(bound.stderr.lower(), r"launch-receipts|alternate|must be stored")

    def test_bind_does_not_synthesize_missing_receipt_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.create_workflow_issue(tmp)
            plan = json.loads(
                Path(tmp, ".pm", "evidence", "workflow-launch-plans", "i1.json").read_text()
            )
            manifest = {
                "schema": "workflow-receipt-manifest/v1",
                "planPath": ".pm/evidence/workflow-launch-plans/i1.json",
                "planSha256": plan["planSha256"],
                "leaves": [
                    {
                        "leafId": leaf["leafId"],
                        "receiptPath": f".pm/evidence/launch-receipts/i1-{leaf['leafId']}.json",
                        "receiptSha256": "sha256:" + "a" * 64,
                    }
                    for leaf in plan["leaves"]
                ],
            }
            manifest["manifestSha256"] = self.canonical_sha256(
                {key: value for key, value in manifest.items() if key != "manifestSha256"}
            )
            path = Path(tmp, ".pm", "evidence", "workflow-receipt-manifests", "i1.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            bound = self.run_pm(
                tmp,
                "bind-workflow-receipts",
                "i1",
                "--receipt-manifest",
                path.relative_to(tmp).as_posix(),
            )
            self.assertNotEqual(0, bound.returncode)
            self.assertFalse(any(Path(tmp, ".pm", "evidence", "launch-receipts").glob("*.json")))
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text()
            self.assertIn("receipt_manifest_path: unbound", issue)


class HumanizeMappingAdmissionTests(PMTestCase):
    def test_canonical_contract_still_declares_the_pinned_adapter_identity(self):
        contract = HUMANIZE_CONTRACT.read_text(encoding="utf-8")
        for declaration in (
            'MAPPING_SCHEMA = "humanize-workflow-mapping/v1"',
            f'SPEC_SCHEMA = "{HUMANIZE_SPEC_SCHEMA}"',
            f"ADAPTER_VERSION = {HUMANIZE_ADAPTER_VERSION}",
            f'UPSTREAM_REPOSITORY = "{HUMANIZE_UPSTREAM_REPOSITORY}"',
            f'UPSTREAM_REVISION = "{HUMANIZE_UPSTREAM_REVISION}"',
            'HMZ_DISTRIBUTION_VERSION = "0.1.0"',
        ):
            self.assertIn(declaration, contract)

    def create_humanize_issue(self, root, *, mutate=None, posture="balanced"):
        self.init_board(root)
        self.assertEqual(0, self.add_milestone(root).returncode)
        plan_path, _ = self.write_launch_plan(
            root,
            mapping_factory=lambda project, design: self.write_humanize_mapping(
                project, design, posture=posture
            ),
            mutate_mapping=(
                None if mutate is None else lambda payload: mutate(Path(root), payload)
            ),
        )
        return self.add_workflow_issue(root, plan_path=plan_path)

    def test_canonical_humanize_mapping_is_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = self.create_humanize_issue(tmp)
            self.assertEqual(0, created.returncode, created.stderr)
            self.assertTrue(Path(tmp, ".pm", "issues", "m1", "i1.md").is_file())

    def test_humanize_mapping_must_bind_the_canonical_adapter_contract(self):
        def relocate_spec(root, payload):
            moved = Path(root, ".humanize", "elsewhere", "review-flow.json")
            moved.parent.mkdir(parents=True, exist_ok=True)
            moved.write_bytes(Path(root, payload["specPath"]).read_bytes())
            payload["specPath"] = moved.relative_to(root).as_posix()

        def relocate_artifact(root, payload):
            moved = Path(root, ".humanize", "generated-flows", "not-the-design.py")
            moved.write_bytes(Path(root, payload["artifact"]).read_bytes())
            payload["artifact"] = moved.relative_to(root).as_posix()
            payload["artifactSha256"] = hashlib.sha256(moved.read_bytes()).hexdigest()

        def forge_spec_schema(root, payload):
            spec = Path(root, payload["specPath"])
            document = json.loads(spec.read_text(encoding="utf-8"))
            document["schema"] = "humanize-workflow-spec/v2"
            spec.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            payload["specSha256"] = hashlib.sha256(spec.read_bytes()).hexdigest()

        cases = {
            "unknown-key": (
                lambda root, payload: payload.update(unexpected=True),
                r"unsupported keys \['unexpected'\]",
            ),
            "adapter-version": (
                lambda root, payload: payload.update(adapterVersion=2),
                r"adapterVersion",
            ),
            "upstream-missing": (
                lambda root, payload: payload.pop("upstream"),
                r"upstream",
            ),
            "upstream-revision": (
                lambda root, payload: payload["upstream"].update(revision="0" * 40),
                r"upstream",
            ),
            "upstream-distribution": (
                lambda root, payload: payload["upstream"].update(distribution="hmz==9.9.9"),
                r"upstream",
            ),
            "spec-missing": (
                lambda root, payload: payload.pop("specPath"),
                r"spec",
            ),
            "spec-digest": (
                lambda root, payload: payload.update(specSha256="0" * 64),
                r"specSha256",
            ),
            "spec-namespace": (relocate_spec, r"spec"),
            "spec-schema": (forge_spec_schema, r"humanize-workflow-spec/v1"),
            "spec-input-path": (
                lambda root, payload: payload.update(inputPath=".humanize/other-input.json"),
                r"inputPath",
            ),
            "artifact-digest": (
                lambda root, payload: payload.update(artifactSha256="0" * 64),
                r"artifactSha256",
            ),
            "artifact-namespace": (relocate_artifact, r"artifact"),
            "terminal-protocol": (
                lambda root, payload: payload["terminalProtocol"].update(
                    independentReviewRequired=False
                ),
                r"terminalProtocol",
            ),
            "terminal-protocol-kinds": (
                lambda root, payload: payload["terminalProtocol"].update(
                    humanRequestKinds=["decision"]
                ),
                r"terminalProtocol",
            ),
            "posture-missing": (
                lambda root, payload: payload.pop("reasoningPosture"),
                r"missing required keys \['reasoningPosture'\]",
            ),
            "posture-blank": (
                lambda root, payload: payload.update(reasoningPosture="   "),
                r"mapping reasoningPosture must be a non-empty string",
            ),
        }
        for label, (mutate, pattern) in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                result = self.create_humanize_issue(tmp, mutate=mutate)
                self.assertNotEqual(0, result.returncode, f"{label} was admitted")
                self.assertRegex(result.stderr, pattern)
                self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def test_humanize_mapping_posture_must_equal_the_global_leaf_posture(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.create_humanize_issue(tmp, posture="economy")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(
                result.stderr,
                r"mapping reasoningPosture .* disagrees with .* launch-plan reasoningPosture",
            )
            self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())


class NativeLaunchPolicyMembershipTests(PMTestCase):
    def add_native_issue(self, root, *, posture, task_class, role, iid="i1"):
        self.init_board(root)
        self.assertEqual(0, self.add_milestone(root).returncode)
        try:
            receipt = self.write_launch_receipt(
                root,
                task_class=task_class,
                reasoning_posture=posture,
                semantic_role=role,
                name=f"{iid}-implementation",
            )
        except LaunchSpecError:
            # Invalid dispatch-envelope tests still need a well-formed canonical
            # receipt to present to PM; the requested envelope remains invalid.
            receipt = self.write_launch_receipt(
                root,
                task_class="analysis",
                reasoning_posture="balanced",
                semantic_role="anthropic_task",
                name=f"{iid}-implementation",
            )
        return self.run_pm(
            root,
            "issue",
            iid,
            "m1",
            "Issue",
            "--lane",
            "native-subagent",
            "--lane-reason",
            "single delegated owner",
            "--estimated-model-calls",
            "1",
            "--max-model-calls",
            "2",
            "--promotion-triggers",
            "none",
            "--acceptance",
            "Run the focused check and judge the stated deliverable from its fresh output.",
            "--review-policy",
            "independent",
            "--review-reason",
            "terminal review route for this item",
            "--reasoning-posture",
            posture,
            "--task-class",
            task_class,
            "--semantic-role",
            role,
            "--launch-receipt",
            receipt.relative_to(root).as_posix(),
        )

    def test_policy_qualified_native_dispatch_is_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = self.add_native_issue(
                tmp, posture="balanced", task_class="analysis", role="anthropic_task"
            )
            self.assertEqual(0, created.returncode, created.stderr)

    def test_native_dispatch_rejects_task_class_outside_the_policy_membership(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.add_native_issue(
                tmp, posture="balanced", task_class="planning", role="anthropic_task"
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr, r"taskClass|dispatch envelope")
            self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def test_native_dispatch_rejects_role_outside_the_posture_allowed_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.add_native_issue(
                tmp, posture="economy", task_class="analysis", role="task"
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr, r"reasoningPosture|allowedRoles|dispatch envelope")
            self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def test_native_dispatch_rejects_verification_role_without_exact_opposite_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.add_native_issue(
                tmp, posture="balanced", task_class="verification", role="codex_advisor"
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr, r"verification|dispatch envelope")
            self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def invoke_native_binding(
        self,
        root,
        *,
        posture,
        task_class,
        role,
        review_mode,
        writer_role,
        persisted_status="verified",
        catalog_runner=static_catalog_runner,
        mutate_receipt=None,
    ):
        try:
            receipt = self.write_launch_receipt(
                root,
                task_class=task_class,
                reasoning_posture=posture,
                semantic_role=role,
                review_mode=review_mode,
                writer_role=writer_role,
                name="exact-opposite-review",
                persisted_status=persisted_status,
            )
        except LaunchSpecError:
            receipt = self.write_launch_receipt(
                root,
                task_class="analysis",
                reasoning_posture="balanced",
                semantic_role="anthropic_task",
                name="exact-opposite-review",
                persisted_status=persisted_status,
            )
        if mutate_receipt is not None:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
            mutate_receipt(payload)
            payload["receiptSha256"] = self.canonical_sha256(
                {key: value for key, value in payload.items() if key != "receiptSha256"}
            )
            receipt.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        cwd = os.getcwd()
        try:
            os.chdir(root)
            return pm_board._native_launch_binding(
                posture,
                task_class,
                role,
                receipt.relative_to(root).as_posix(),
                review_mode=review_mode,
                writer_role=writer_role,
                _test_catalog_runner=catalog_runner,
            )
        finally:
            os.chdir(cwd)

    def test_bootstrap_snapshot_with_successful_fresh_resolution_is_admitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            bound = self.invoke_native_binding(
                tmp,
                posture="balanced",
                task_class="analysis",
                role="anthropic_task",
                review_mode="none",
                writer_role=None,
                persisted_status="bootstrap-unverified",
            )
            self.assertEqual("anthropic_task", bound["semantic_role"])

    def test_python_entrypoint_admits_bootstrap_only_after_fresh_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            receipt = self.write_launch_receipt(
                tmp,
                name="bootstrap-implementation",
                persisted_status="bootstrap-unverified",
            )
            created = self.run_pm(
                tmp,
                "issue",
                "i1",
                "m1",
                "Bootstrap policy issue",
                "--lane",
                "native-subagent",
                "--lane-reason",
                "single delegated owner",
                "--estimated-model-calls",
                "1",
                "--max-model-calls",
                "2",
                "--promotion-triggers",
                "none",
                "--acceptance",
                "Freshly resolve the exact bootstrap policy before dispatch.",
                "--review-policy",
                "independent",
                "--review-reason",
                "universal independent terminal review",
                "--reasoning-posture",
                "balanced",
                "--task-class",
                "analysis",
                "--semantic-role",
                "anthropic_task",
                "--launch-receipt",
                receipt.relative_to(tmp).as_posix(),
            )
            self.assertEqual(0, created.returncode, created.stderr)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8")
            self.assertIn("semantic_role: anthropic_task", issue)

    def test_persisted_verified_snapshot_cannot_authorize_when_fresh_catalog_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            with self.assertRaises(SystemExit) as raised:
                self.invoke_native_binding(
                    tmp,
                    posture="balanced",
                    task_class="analysis",
                    role="anthropic_task",
                    review_mode="none",
                    writer_role=None,
                    catalog_runner=unavailable_catalog_runner,
                )
            self.assertRegex(str(raised.exception).lower(), r"fresh catalog|canonical resolver")

    def test_caller_rewritten_fresh_catalog_evidence_cannot_authorize(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)

            def forge(payload):
                payload["policy"]["freshCatalogEvidence"]["cursor"][
                    "catalogBytesSha256"
                ] = "sha256:" + "0" * 64

            with self.assertRaises(SystemExit) as raised:
                self.invoke_native_binding(
                    tmp,
                    posture="balanced",
                    task_class="analysis",
                    role="anthropic_task",
                    review_mode="none",
                    writer_role=None,
                    mutate_receipt=forge,
                )
            self.assertRegex(str(raised.exception).lower(), r"fresh|canonical resolver|receipt")

    def test_symlinked_models_leaf_is_refused_before_fresh_catalog_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            receipt = self.write_launch_receipt(tmp)
            models = Path(tmp, "config", "workflows", "models.json")
            outside = Path(tmp, "models-outside.json")
            models.replace(outside)
            models.symlink_to(outside)
            calls = []

            def recording_runner(argv):
                calls.append(tuple(argv))
                return static_catalog_runner(argv)

            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with self.assertRaises(SystemExit) as raised:
                    pm_board._native_launch_binding(
                        "balanced",
                        "analysis",
                        "anthropic_task",
                        receipt.relative_to(tmp).as_posix(),
                        _test_catalog_runner=recording_runner,
                    )
            finally:
                os.chdir(cwd)
            self.assertRegex(str(raised.exception).lower(), r"symlink|regular|follow|links")
            self.assertEqual([], calls)

    def test_symlinked_models_parent_is_refused_before_fresh_catalog_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            receipt = self.write_launch_receipt(tmp)
            workflows = Path(tmp, "config", "workflows")
            outside = Path(tmp, "workflows-outside")
            workflows.replace(outside)
            workflows.symlink_to(outside, target_is_directory=True)
            calls = []

            def recording_runner(argv):
                calls.append(tuple(argv))
                return static_catalog_runner(argv)

            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with self.assertRaises(SystemExit) as raised:
                    pm_board._native_launch_binding(
                        "balanced",
                        "analysis",
                        "anthropic_task",
                        receipt.relative_to(tmp).as_posix(),
                        _test_catalog_runner=recording_runner,
                    )
            finally:
                os.chdir(cwd)
            self.assertRegex(str(raised.exception).lower(), r"symlink|regular|follow|links")
            self.assertEqual([], calls)

    def test_exact_opposite_writer_role_is_admitted_when_policy_qualified(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            bound = self.invoke_native_binding(
                tmp,
                posture="balanced",
                task_class="verification",
                role="codex_advisor",
                review_mode="exact-opposite",
                writer_role="anthropic_task",
            )
            self.assertEqual("codex_advisor", bound["semantic_role"])

    def test_exact_opposite_writer_role_must_be_in_posture_allowed_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            with self.assertRaises(SystemExit) as raised:
                self.invoke_native_binding(
                    tmp,
                    posture="economy",
                    task_class="verification",
                    role="codex_advisor",
                    review_mode="exact-opposite",
                    writer_role="task",
                )
            self.assertRegex(
                str(raised.exception), r"writerRole|allowedRoles|dispatch envelope"
            )

    def test_exact_opposite_writer_role_cannot_be_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            with self.assertRaises(SystemExit) as raised:
                self.invoke_native_binding(
                    tmp,
                    posture="balanced",
                    task_class="verification",
                    role="codex_advisor",
                    review_mode="exact-opposite",
                    writer_role="advisor",
                )
            self.assertRegex(str(raised.exception), r"writerRole|verification")

    def test_native_dispatch_rejects_the_controller_flat_same_schema_receipt(self):
        """The subagent controller writes run-local receipts that reuse this schema id with a flat
        shape. PM binds only canonical producer receipts, so a copied controller receipt must be
        refused instead of silently satisfying the dispatch envelope."""
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.write_verified_roles(tmp)
            receipt = Path(tmp, ".pm", "evidence", "launch-receipts", "controller-shaped.json")
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text(
                json.dumps(
                    {
                        "schema": "workflow-native-launch-receipt/v1",
                        "runId": "run-1",
                        "designId": "review-flow",
                        "call": 1,
                        "activation": "implement",
                        "taskClass": "analysis",
                        "reasoningPosture": "balanced",
                        "semanticRole": "anthropic_task",
                        "workspaceAccess": "write",
                        "reviewPairings": [],
                        "policy": {"rolesSha256": "sha256:" + "0" * 64},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            result = self.run_pm(
                tmp,
                "issue",
                "i1",
                "m1",
                "Issue",
                "--lane",
                "native-subagent",
                "--lane-reason",
                "single delegated owner",
                "--estimated-model-calls",
                "1",
                "--max-model-calls",
                "2",
                "--promotion-triggers",
                "none",
                "--acceptance",
                "Run the focused check and judge the stated deliverable from its fresh output.",
                "--review-policy",
                "independent",
                "--review-reason",
                "terminal review route for this item",
                "--reasoning-posture",
                "balanced",
                "--task-class",
                "analysis",
                "--semantic-role",
                "anthropic_task",
                "--launch-receipt",
                receipt.relative_to(tmp).as_posix(),
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr, r"schema validation failed")
            self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def test_recorded_native_envelope_is_revalidated_on_every_transition(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = self.add_native_issue(
                tmp, posture="balanced", task_class="analysis", role="anthropic_task"
            )
            self.assertEqual(0, created.returncode, created.stderr)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(
                issue.read_text(encoding="utf-8").replace(
                    "task_class: analysis", "task_class: planning", 1
                ),
                encoding="utf-8",
            )
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            refused = self.run_pm(tmp, "set", "issue", "i1", "review")
            self.assertNotEqual(0, refused.returncode)
            self.assertRegex(refused.stderr, r"taskClass|does not match the PM dispatch envelope")


class IndependentBuilderBindingTests(PMTestCase):
    def setup_reviewed_issue(self, root):
        self.init_git(root)
        self.init_board(root)
        self.assertEqual(0, self.add_milestone(root).returncode)
        created = self.add_issue(root)
        self.assertEqual(0, created.returncode, created.stderr)
        self.assertEqual(0, self.run_pm(root, "set", "issue", "i1", "in_progress").returncode)
        self.assertEqual(0, self.run_pm(root, "set", "issue", "i1", "review").returncode)
        Path(root, "deliverable.txt").write_text("implemented\n", encoding="utf-8")
        self.write_report(root)

    def test_done_refuses_artifact_that_omits_the_dispatched_builder_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_reviewed_issue(tmp)
            self.assertIn(
                "semantic_role: anthropic_task",
                Path(tmp, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8"),
            )
            self.write_independent_terminal(
                tmp, "COMPLETE", builder_roles=("frontier_execute_anthropic",)
            )
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr, r"BUILDER-IDENTITIES")
            self.assertIn(
                "status: review",
                Path(tmp, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8"),
            )

    def test_done_accepts_several_builders_that_include_the_dispatched_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_reviewed_issue(tmp)
            self.write_independent_terminal(
                tmp,
                "COMPLETE",
                builder_roles=("anthropic_task", "frontier_execute_anthropic"),
            )
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(
                "status: done",
                Path(tmp, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8"),
            )


class MergeReceiptLiveReadbackTests(PMTestCase):
    def setup_pr_complete(self, root):
        self.setup_board(root)
        self.attach_pr(root)
        self.assertEqual(0, self.run_pm(root, "set", "issue", "i1", "in_progress").returncode)
        self.assertEqual(0, self.run_pm(root, "set", "issue", "i1", "review").returncode)
        self.write_report(root)
        self.write_independent_terminal(root, "COMPLETE")

    def issue_status(self, root):
        return Path(root, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8")

    def test_parent_symlink_complete_receipt_does_not_authorize_done(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            self.setup_board(tmp)
            self.attach_pr(tmp)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            self.write_report(tmp)
            merge_dir = Path(tmp, ".pm", "receipts", "merge")
            merge_dir.parent.mkdir(parents=True, exist_ok=True)
            merge_dir.symlink_to(outside_raw)
            self.write_independent_terminal(tmp, "COMPLETE")
            receipt_path, _ = self.write_complete_merge_bundle(tmp)
            result = self.run_pm(
                tmp,
                "set",
                "issue",
                "i1",
                "done",
                "--merge-receipt",
                str(receipt_path),
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"symlink|regular|follow|merge")
            self.assertIn("status: review", self.issue_status(tmp))

    def test_hardlink_complete_receipt_does_not_authorize_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_pr_complete(tmp)
            receipt_path, _ = self.write_complete_merge_bundle(tmp)
            linked = receipt_path.parent / "issue-i1-hardlink.json"
            os.link(receipt_path, linked)
            result = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--merge-receipt", str(linked)
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"hardlink|exclusive|regular")
            self.assertIn("status: review", self.issue_status(tmp))

    def test_forged_complete_receipt_without_gh_leaves_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_pr_complete(tmp)
            receipt_path, _ = self.write_complete_merge_bundle(tmp)
            env = self.hide_system_gh(tmp)
            result = self.run_pm(
                tmp,
                "set",
                "issue",
                "i1",
                "done",
                "--merge-receipt",
                str(receipt_path),
                env=env,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"gh|read-back|readback|unavailable")
            self.assertIn("status: review", self.issue_status(tmp))

    def test_zero_approval_review_id_refuses_before_github_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_pr_complete(tmp)
            receipt_path, _ = self.write_complete_merge_bundle(
                tmp, approval_id="0"
            )
            result = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--merge-receipt", str(receipt_path)
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"approvalreviewid|approval review")
            self.assertIn("status: review", self.issue_status(tmp))

    def test_missing_authorization_sidecar_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_pr_complete(tmp)
            receipt_path, auth_path = self.write_complete_merge_bundle(tmp)
            auth_path.unlink()
            result = self.run_pm(
                tmp, "set", "issue", "i1", "done", "--merge-receipt", str(receipt_path)
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"authorization")
            self.assertIn("status: review", self.issue_status(tmp))

    def test_remote_mismatch_refuses_and_honest_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_pr_complete(tmp)
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=tmp, text=True
            ).strip()
            receipt_path, _ = self.write_complete_merge_bundle(tmp, merge_commit="c" * 40)
            env, calls = self.install_fake_gh(tmp, head=head, pr_state="OPEN")
            mismatched = self.run_pm(
                tmp,
                "set",
                "issue",
                "i1",
                "done",
                "--merge-receipt",
                str(receipt_path),
                env=env,
            )
            self.assertNotEqual(0, mismatched.returncode)
            self.assertRegex(mismatched.stderr.lower(), r"merged|read-back|readback|mismatch")
            self.assertIn("status: review", self.issue_status(tmp))
            self.assertTrue(calls.exists())
            recorded = [
                json.loads(line)["argv"] for line in calls.read_text().splitlines() if line
            ]
            self.assertTrue(any(argv[:1] == ["api"] for argv in recorded))
            self.assertTrue(any(argv[:2] == ["pr", "view"] for argv in recorded))
            self.assertFalse(any(argv[:2] == ["pr", "merge"] for argv in recorded))

            env, calls = self.install_fake_gh(
                tmp, head=head, pr_state="MERGED", merge_commit="c" * 40
            )
            retried = self.run_pm(
                tmp,
                "set",
                "issue",
                "i1",
                "done",
                "--merge-receipt",
                str(receipt_path),
                env=env,
            )
            self.assertEqual(0, retried.returncode, retried.stderr)
            self.assertIn("status: done", self.issue_status(tmp))
            recorded = [
                json.loads(line)["argv"] for line in calls.read_text().splitlines() if line
            ]
            self.assertTrue(any(argv[:1] == ["api"] for argv in recorded))
            self.assertTrue(any(argv[:2] == ["pr", "view"] for argv in recorded))
            self.assertFalse(any(argv[:2] == ["pr", "merge"] for argv in recorded))

    def test_live_readback_never_invokes_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_pr_complete(tmp)
            head = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=tmp, text=True
            ).strip()
            receipt_path, _ = self.write_complete_merge_bundle(tmp)
            env, calls = self.install_fake_gh(tmp, head=head, pr_state="MERGED")
            result = self.run_pm(
                tmp,
                "set",
                "issue",
                "i1",
                "done",
                "--merge-receipt",
                str(receipt_path),
                env=env,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("status: done", self.issue_status(tmp))
            recorded = [
                json.loads(line)["argv"] for line in calls.read_text().splitlines() if line
            ]
            self.assertTrue(any(argv[:1] == ["api"] for argv in recorded))
            self.assertTrue(any(argv[:2] == ["pr", "view"] for argv in recorded))
            self.assertFalse(any("merge" in argv for argv in recorded))

    def hide_system_gh(self, root):
        bin_dir = Path(root, ".pm", "receipts", "merge", ".bin")
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ("python3", "python", "git"):
            found = shutil.which(name)
            if found is None:
                continue
            dest = bin_dir / name
            if not dest.exists():
                os.symlink(os.path.realpath(found), dest)
        env = dict(os.environ)
        env["PATH"] = str(bin_dir)
        env["PM_TEST_GH_PATH"] = str(bin_dir / "missing-gh")
        return env


class HumanSendChannelTests(PMTestCase):
    REQUEST = "Contact the vendor to restore the unavailable account."

    def setup_human_blocked(self, root, *, ui_mode="file", linear_id=None):
        self.setup_board(root)
        self.set_ui_mode(root, ui_mode)
        if linear_id is not None:
            issue = Path(root, ".pm", "issues", "m1", "i1.md")
            issue.write_text(
                issue.read_text(encoding="utf-8").replace(
                    "linear_id: ", f"linear_id: {linear_id}", 1
                ),
                encoding="utf-8",
            )
        self.assertEqual(0, self.run_pm(root, "set", "issue", "i1", "in_progress").returncode)
        self.assertEqual(0, self.run_pm(root, "set", "issue", "i1", "review").returncode)
        self.write_report(root)
        self.write_independent_terminal(
            root,
            "HUMAN_BLOCKED",
            human_kind="external-action",
            human_request=self.REQUEST,
        )

    def issue_status(self, root):
        return Path(root, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8")

    def test_file_mode_parent_symlink_receipt_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            self.setup_board(tmp)
            self.set_ui_mode(tmp, "file")
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "in_progress").returncode)
            self.assertEqual(0, self.run_pm(tmp, "set", "issue", "i1", "review").returncode)
            self.write_report(tmp)
            human_dir = Path(tmp, ".pm", "receipts", "human")
            human_dir.parent.mkdir(parents=True, exist_ok=True)
            human_dir.symlink_to(outside_raw)
            self.write_independent_terminal(
                tmp,
                "HUMAN_BLOCKED",
                human_kind="external-action",
                human_request=self.REQUEST,
            )
            receipt = self.write_human_send_receipt(tmp, request=self.REQUEST)
            result = self.run_pm(
                tmp,
                "set",
                "issue",
                "i1",
                "awaiting_human",
                "--send-receipt",
                str(receipt),
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("status: review", self.issue_status(tmp))

    def test_interactive_refuses_caller_authored_send_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_human_blocked(tmp, ui_mode="interactive")
            receipt = self.write_human_send_receipt(
                tmp, channel="interactive", request=self.REQUEST
            )
            result = self.run_pm(
                tmp,
                "set",
                "issue",
                "i1",
                "awaiting_human",
                "--send-receipt",
                str(receipt),
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"interactive|send-receipt|controlling session")
            self.assertIn("status: review", self.issue_status(tmp))

    def test_linear_caller_json_without_live_readback_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_human_blocked(tmp, ui_mode="linear", linear_id="linear-i1")
            receipt = self.write_human_send_receipt(
                tmp, channel="linear", request=self.REQUEST
            )
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board,
                    "_gql",
                    return_value={"issue": {"comments": {"nodes": []}}},
                ):
                    with self.assertRaises(SystemExit):
                        pm_board.cmd_set(
                            argparse.Namespace(
                                kind="issue",
                                id="i1",
                                status="awaiting_human",
                                send_receipt=str(receipt),
                                merge_receipt="",
                                transaction_id="",
                                _test_catalog_runner=static_catalog_runner,
                                _test_reviewer_executable=Path(
                                    tmp, ".pm", "test-reviewer-fixture"
                                ),
                            )
                        )
            finally:
                os.chdir(cwd)
            self.assertIn("status: review", self.issue_status(tmp))

    def test_linear_live_comment_readback_authorizes_and_retries_after_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_human_blocked(tmp, ui_mode="linear", linear_id="linear-i1")
            receipt = self.write_human_send_receipt(
                tmp, channel="linear", request=self.REQUEST
            )
            digest = "sha256:" + hashlib.sha256(self.REQUEST.encode("utf-8")).hexdigest()
            ns = argparse.Namespace(
                kind="issue",
                id="i1",
                status="awaiting_human",
                send_receipt=str(receipt),
                merge_receipt="",
                transaction_id="",
                _test_catalog_runner=static_catalog_runner,
                _test_reviewer_executable=Path(
                    tmp, ".pm", "test-reviewer-fixture"
                ),
            )
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board,
                    "_gql",
                    return_value={
                        "issue": {
                            "comments": {
                                "nodes": [
                                    {
                                        "id": "c0",
                                        "body": "REQUEST-KEY: other-key\nREQUEST-SHA256: sha256:"
                                        + ("0" * 64),
                                    }
                                ]
                            }
                        }
                    },
                ):
                    with self.assertRaises(SystemExit):
                        pm_board.cmd_set(ns)
                self.assertIn("status: review", self.issue_status(tmp))
                with mock.patch.dict(os.environ, {"LINEAR_API_KEY": "test"}), mock.patch.object(
                    pm_board,
                    "_gql",
                    return_value={
                        "issue": {
                            "comments": {
                                "nodes": [
                                    {
                                        "id": "c1",
                                        "body": (
                                            f"REQUEST-KEY: issue-i1-human-1\n"
                                            f"REQUEST-SHA256: {digest}\nPlease restore access."
                                        ),
                                    }
                                ]
                            }
                        }
                    },
                ):
                    pm_board.cmd_set(ns)
            finally:
                os.chdir(cwd)
            self.assertIn("status: awaiting_human", self.issue_status(tmp))
            self.assertIn("human_request_key: issue-i1-human-1", self.issue_status(tmp))

    def test_linear_missing_api_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_human_blocked(tmp, ui_mode="linear", linear_id="linear-i1")
            receipt = self.write_human_send_receipt(
                tmp, channel="linear", request=self.REQUEST
            )
            env = dict(os.environ)
            env.pop("LINEAR_API_KEY", None)
            result = self.run_pm(
                tmp,
                "set",
                "issue",
                "i1",
                "awaiting_human",
                "--send-receipt",
                str(receipt),
                env=env,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"linear_api_key|authenticated")
            self.assertIn("status: review", self.issue_status(tmp))


class WorkflowProofTests(PMTestCase):
    def setup_issue(self, root, iid="i1"):
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "PM Test"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "pm@example.invalid"], cwd=root, check=True)
        self.init_board(root)
        self.assertEqual(0, self.add_milestone(root).returncode)
        created = self.add_issue(root, lane="workflow", policy="machine-gated", iid=iid)
        self.assertEqual(0, created.returncode, created.stderr)
        self.assertEqual(0, self.run_pm(root, "set", "issue", iid, "in_progress").returncode)
        bound = self.bind_workflow_receipts(root, iid=iid)
        self.assertEqual(0, bound.returncode, bound.stderr)
        self.assertEqual(0, self.run_pm(root, "set", "issue", iid, "review").returncode)
        subprocess.run(["git", "add", ".pm"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "board"], cwd=root, check=True)

    def current_binding(self, root):
        result = subprocess.run(
            ["python3", str(REWARD_HELPER), "current-state", "--root", str(root)],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)["stateBinding"]

    def issue_file(self, root, iid="i1"):
        return Path(root, ".pm", "issues", "m1", f"{iid}.md")

    def canonical_proof_path(self, root, iid="i1"):
        return Path(root, ".pm", "workflow-reviews", f"{iid}.md")

    def legacy_proof_path(self, root, iid="i1"):
        return Path(root, ".pm", "reviews", f"workflow-{iid}.md")

    def write_workflow_proof(self, root, *, policy="machine-gated", iid="i1", directory=None):
        binding = self.current_binding(root)
        proof_dir = Path(root, directory) if directory else Path(root, ".pm", "workflow-reviews")
        proof_dir.mkdir(parents=True, exist_ok=True)
        path = proof_dir / f"{iid}.md"
        path.write_text(
            "\n".join(
                [
                    "TERMINAL: COMPLETE",
                    f"EVIDENCE-COMMITMENT: sha256:{'b' * 64}",
                    f"REVIEW_POLICY: {policy}",
                    "COMPLETION_PROOF: state-bound",
                    f"ITEM_ID: {iid}",
                    "DESIGN_ID: review-flow",
                    "RUN_ID: run-1",
                    "SUCCESS_TERMINAL: success",
                    "MODEL_CALLS: 3",
                    "MAX_MODEL_CALLS: 6",
                    f"FROZEN_INPUTS_DIGEST: {'c' * 64}",
                    f"MACHINE_REVIEW_DIGEST: {'d' * 64}",
                    "MACHINE_REVIEW_CALL: 3",
                    f"MACHINE_REVIEW_PROMPT_SHA256: {'e' * 64}",
                    f"MACHINE_REVIEWED_STATE_DIGEST: {binding['finalStateDigest']}",
                    f"MACHINE_REVIEWED_INPUTS_DIGEST: {'1' * 64}",
                    f"MACHINE_REVIEW_EVIDENCE_DIGEST: {'b' * 64}",
                    f"NATIVE_TRACE_SHA256: {'3' * 64}",
                    "NATIVE_TRACE_CALLS: 3",
                    f"MACHINE_REVIEW_RESPONSE_SHA256: {'4' * 64}",
                    f"MACHINE_REVIEW_RECEIPT_SHA256: {'5' * 64}",
                    f"MACHINE_REVIEW_PROMPT_BYTES_SHA256: {'6' * 64}",
                    f"BASELINE_DIGEST: {'a' * 64}",
                    f"EVIDENCE_DIGEST: {'b' * 64}",
                    "EVIDENCE_PRODUCER_NODE: acceptance-check",
                    "EVIDENCE_PRODUCER_ACTIVATION: 1",
                    f"EVIDENCE_PRODUCER_DIGEST: {'7' * 64}",
                    f"EVIDENCE_PRODUCER_RECEIPT_SHA256: {'8' * 64}",
                    f"FINAL_STATE_DIGEST: {binding['finalStateDigest']}",
                    "STATE_BINDING_JSON: "
                    + json.dumps(binding, sort_keys=True, separators=(",", ":")),
                    f"CANDIDATE_DIGEST: {'b' * 64}",
                    "AUTHORIZATION_MODE: controller-tier1-env",
                    "AUTHORIZATION_SOURCE: GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1",
                    "THREAT_BOUNDARY: Practical no-follow byte binding; not an OS sandbox against a concurrent same-UID pathname attacker.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def write_legacy_workflow_proof(self, root, *, iid="i1"):
        binding = self.current_binding(root)
        path = self.legacy_proof_path(root, iid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "TERMINAL: COMPLETE",
                    f"EVIDENCE-COMMITMENT: sha256:{'b' * 64}",
                    "REVIEW_POLICY: machine-gated",
                    "COMPLETION_PROOF: state-bound",
                    f"ITEM_ID: {iid}",
                    "DESIGN_ID: review-flow",
                    "RUN_ID: run-1",
                    "SUCCESS_TERMINAL: success",
                    "MODEL_CALLS: 3",
                    "MAX_MODEL_CALLS: 6",
                    f"FROZEN_INPUTS_DIGEST: {'c' * 64}",
                    f"MACHINE_REVIEW_DIGEST: {'d' * 64}",
                    "MACHINE_REVIEW_CALL: 3",
                    f"MACHINE_REVIEW_PROMPT_SHA256: {'e' * 64}",
                    f"MACHINE_REVIEWED_STATE_DIGEST: {binding['finalStateDigest']}",
                    f"MACHINE_REVIEWED_INPUTS_DIGEST: {'1' * 64}",
                    f"MACHINE_REVIEW_EVIDENCE_DIGEST: {'b' * 64}",
                    f"NATIVE_TRACE_SHA256: {'3' * 64}",
                    "NATIVE_TRACE_CALLS: 3",
                    f"MACHINE_REVIEW_RESPONSE_SHA256: {'4' * 64}",
                    f"MACHINE_REVIEW_RECEIPT_SHA256: {'5' * 64}",
                    f"MACHINE_REVIEW_PROMPT_BYTES_SHA256: {'6' * 64}",
                    f"BASELINE_DIGEST: {'a' * 64}",
                    f"EVIDENCE_DIGEST: {'b' * 64}",
                    "EVIDENCE_PRODUCER_NODE: acceptance-check",
                    "EVIDENCE_PRODUCER_ACTIVATION: 1",
                    f"EVIDENCE_PRODUCER_DIGEST: {'7' * 64}",
                    f"EVIDENCE_PRODUCER_RECEIPT_SHA256: {'8' * 64}",
                    f"FINAL_STATE_DIGEST: {binding['finalStateDigest']}",
                    "STATE_BINDING_JSON: "
                    + json.dumps(binding, sort_keys=True, separators=(",", ":")),
                    f"CANDIDATE_DIGEST: {'b' * 64}",
                    "AUTHORIZATION_MODE: controller-tier1-env",
                    "AUTHORIZATION_SOURCE: GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1",
                    "THREAT_BOUNDARY: Practical no-follow byte binding; not an OS sandbox against a concurrent same-UID pathname attacker.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return path

    def simulate_milestone_revise_unlink(self, root, milestone_id):
        # Contract of milestone-review._revised_artifact: unlink .pm/reviews/<id>.md.
        artifact = Path(root, ".pm", "reviews", f"{milestone_id}.md")
        try:
            artifact.unlink()
        except FileNotFoundError:
            pass
        return artifact

    def test_workflow_issue_refuses_missing_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.write_report(tmp)
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("workflow proof", result.stderr.lower())

    def test_handwritten_strong_looking_proof_is_not_controller_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.write_report(tmp)
            self.write_workflow_proof(tmp)
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(
                result.stderr.lower(), r"canonical mapping|proof binding|item_kind"
            )
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8")
            self.assertIn("status: review", issue)

    def test_unbound_controller_runtime_override_cannot_authorize_forged_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            Path(tmp, "workflow-output", "subagent", "review-flow", "run-1").mkdir(
                parents=True
            )
            controller = Path(
                tmp,
                ".claude",
                "skills",
                "subagent-workflow",
                "scripts",
                "controller",
                "run.ts",
            )
            controller.parent.mkdir(parents=True)
            controller.write_text("throw new Error('not a controller fixture');\n")
            fake_runtime = Path(tmp, "fake-controller-runtime.py")
            fake_runtime.write_text(
                """#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
proof = Path('.pm/workflow-reviews/i1.md')
print(json.dumps({
    'terminal': 'success',
    'reviewerTerminal': 'COMPLETE',
    'designId': 'review-flow',
    'runId': 'run-1',
    'evidenceCommitment': 'sha256:' + 'b' * 64,
    'proof': {
        'path': '.pm/workflow-reviews/i1.md',
        'sha256': hashlib.sha256(proof.read_bytes()).hexdigest(),
    },
}))
""",
                encoding="utf-8",
            )
            fake_runtime.chmod(0o755)
            self.write_report(tmp)
            self.write_workflow_proof(tmp)
            env = dict(os.environ)
            env["GENERAL_AGENT_CONTROLLER_RUNTIME"] = str(fake_runtime)
            result = self.run_pm(tmp, "set", "issue", "i1", "done", env=env)
            self.assertNotEqual(0, result.returncode, "an unbound runtime forged completion")
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md").read_text(encoding="utf-8")
            self.assertIn("status: review", issue)

    def test_workflow_issue_rejects_legacy_weak_state_bound_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.write_report(tmp)
            binding = self.current_binding(tmp)
            proof = self.canonical_proof_path(tmp)
            proof.parent.mkdir(parents=True, exist_ok=True)
            proof.write_text(
                "\n".join(
                    [
                        "TERMINAL: COMPLETE",
                        f"EVIDENCE-COMMITMENT: sha256:{'b' * 64}",
                        "REVIEW_POLICY: machine-gated",
                        "COMPLETION_PROOF: state-bound",
                        "ITEM_ID: i1",
                        f"BASELINE_DIGEST: {'a' * 64}",
                        f"EVIDENCE_DIGEST: {'b' * 64}",
                        f"FINAL_STATE_DIGEST: {binding['finalStateDigest']}",
                        "STATE_BINDING_JSON: "
                        + json.dumps(binding, sort_keys=True, separators=(",", ":")),
                        f"CANDIDATE_DIGEST: {'b' * 64}",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("legacy/weak", result.stderr)
            self.assertIn("MACHINE_REVIEW", result.stderr)

    def test_workflow_issue_refuses_proof_after_byte_state_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.write_report(tmp)
            self.write_workflow_proof(tmp)
            Path(tmp, "changed-after-review.txt").write_text("changed\n")
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(
                result.stderr.lower(),
                r"state binding|proof binding|canonical mapping|controller proof audit|final state|item_kind",
            )

    def test_machine_gated_policy_rejected_outside_workflow_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            result = self.add_issue(tmp, lane="native-subagent", policy="machine-gated")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("requires --lane workflow", result.stderr)

    def test_old_machine_proof_path_collides_with_independent_milestone_workflow_x(self):
        issue_id = "x"
        milestone_id = f"workflow-{issue_id}"
        old_machine = f".pm/reviews/workflow-{issue_id}.md"
        independent = f".pm/reviews/{milestone_id}.md"
        canonical = f".pm/workflow-reviews/{issue_id}.md"
        self.assertEqual(old_machine, independent)
        self.assertNotEqual(canonical, independent)
        self.assertNotEqual(canonical, f".pm/verdicts/{issue_id}.md")
        self.assertNotEqual(canonical, f".pm/reviews/{issue_id}.md")

    def test_milestone_workflow_x_revise_cannot_delete_machine_proof_for_issue_x(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp, iid="x")
            self.write_report(tmp, iid="x")
            proof = self.write_workflow_proof(tmp, iid="x")
            independent = Path(tmp, ".pm", "reviews", "workflow-x.md")
            independent.parent.mkdir(parents=True, exist_ok=True)
            independent.write_text("TERMINAL: REVISE\nindependent milestone workflow-x\n", encoding="utf-8")
            proof_bytes = proof.read_bytes()
            board_before = self.issue_file(tmp, "x").read_bytes()
            self.simulate_milestone_revise_unlink(tmp, "workflow-x")
            self.assertFalse(independent.exists())
            self.assertTrue(proof.is_file())
            self.assertEqual(proof_bytes, proof.read_bytes())
            result = self.run_pm(tmp, "set", "issue", "x", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(board_before, self.issue_file(tmp, "x").read_bytes())
            self.assertIn("status: review", self.issue_file(tmp, "x").read_text(encoding="utf-8"))
            self.assertTrue(proof.is_file())
            self.assertEqual(proof_bytes, proof.read_bytes())

    def test_legacy_only_workflow_proof_is_refused_without_board_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.write_report(tmp)
            legacy = self.write_legacy_workflow_proof(tmp)
            before = self.issue_file(tmp).read_bytes()
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(
                result.stderr.lower(),
                r"legacy|ambiguous|workflow-reviews|rerun",
            )
            self.assertEqual(before, self.issue_file(tmp).read_bytes())
            self.assertIn("status: review", self.issue_file(tmp).read_text(encoding="utf-8"))
            self.assertTrue(legacy.is_file())
            self.assertFalse(self.canonical_proof_path(tmp).exists())

    def test_swapped_workflow_proof_is_refused_without_board_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.write_report(tmp)
            swapped = self.write_workflow_proof(tmp, iid="other")
            self.write_legacy_workflow_proof(tmp, iid="i1")
            verdict = Path(tmp, ".pm", "verdicts", "i1.md")
            verdict.parent.mkdir(parents=True, exist_ok=True)
            verdict.write_text(swapped.read_text(encoding="utf-8"), encoding="utf-8")
            before = self.issue_file(tmp).read_bytes()
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"missing|stale|legacy|ambiguous|workflow-reviews")
            self.assertEqual(before, self.issue_file(tmp).read_bytes())
            self.assertIn("status: review", self.issue_file(tmp).read_text(encoding="utf-8"))
            self.assertFalse(self.canonical_proof_path(tmp).exists())

    def test_missing_canonical_workflow_proof_retry_does_not_mutate_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.setup_issue(tmp)
            self.write_report(tmp)
            before = self.issue_file(tmp).read_bytes()
            first = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, first.returncode)
            self.assertRegex(first.stderr.lower(), r"missing|stale|workflow proof")
            self.assertEqual(before, self.issue_file(tmp).read_bytes())
            second = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, second.returncode)
            self.assertRegex(second.stderr.lower(), r"missing|stale|workflow proof")
            self.assertEqual(before, self.issue_file(tmp).read_bytes())
            self.assertIn("status: review", self.issue_file(tmp).read_text(encoding="utf-8"))
            self.assertFalse(self.canonical_proof_path(tmp).exists())

    def test_workflow_proof_parent_symlink_is_refused_without_board_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            self.setup_issue(tmp)
            self.write_report(tmp)
            outside = Path(outside_raw, "workflow-reviews")
            outside.mkdir()
            (outside / "i1.md").write_text("TERMINAL: COMPLETE\noutside\n", encoding="utf-8")
            proof_dir = Path(tmp, ".pm", "workflow-reviews")
            proof_dir.symlink_to(outside)
            before = self.issue_file(tmp).read_bytes()
            result = self.run_pm(tmp, "set", "issue", "i1", "done")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"symlink|regular|follow|plain|cannot read")
            self.assertEqual(before, self.issue_file(tmp).read_bytes())
            self.assertIn("status: review", self.issue_file(tmp).read_text(encoding="utf-8"))
            self.assertEqual("TERMINAL: COMPLETE\noutside\n", (outside / "i1.md").read_text(encoding="utf-8"))


class IterationModeTests(PMTestCase):
    def read_config(self, root):
        return json.loads(Path(root, ".pm", "config.json").read_text(encoding="utf-8"))

    def write_expired_config(self, root, *, fallback):
        config = self.read_config(root)
        config.update(
            {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_unavailable_fallback": fallback,
                "human_checkin_lease_expires_at": "2000-01-01T00:00:00Z",
            }
        )
        Path(root, ".pm", "config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )

    def test_init_defaults_to_conservative_autonomous_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            config = self.read_config(tmp)
            self.assertEqual("autonomous", config["iteration_mode"])
            self.assertIsNone(config["human_checkin_lease_minutes"])
            self.assertEqual("pause", config["human_unavailable_fallback"])
            self.assertIsNone(config["human_checkin_lease_expires_at"])

            shown = self.run_pm(tmp, "iteration-mode", "show")
            self.assertEqual(0, shown.returncode, shown.stderr)
            state = json.loads(shown.stdout)
            self.assertEqual("autonomous", state["selected_iteration_mode"])
            self.assertEqual("autonomous", state["effective_iteration_mode"])
            self.assertEqual("not_applicable", state["human_checkin_lease_status"])
            self.assertTrue(state["dispatch_allowed"])

    def test_human_guided_init_requires_a_bounded_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            rejected_alias = self.run_pm(
                tmp, "init", "--ui-mode", "file", "--iteration-mode", "Fast"
            )
            self.assertNotEqual(0, rejected_alias.returncode)
            self.assertIn("invalid choice", rejected_alias.stderr)

        for supplied in (None, "4", "1441"):
            with self.subTest(supplied=supplied), tempfile.TemporaryDirectory() as tmp:
                args = ["init", "--ui-mode", "file", "--iteration-mode", "human_guided"]
                if supplied is not None:
                    args.extend(["--human-checkin-lease-minutes", supplied])
                result = self.run_pm(tmp, *args)
                self.assertNotEqual(0, result.returncode)
                self.assertRegex(result.stderr, r"lease|5\.\.1440")

        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_pm(
                tmp,
                "init",
                "--ui-mode",
                "file",
                "--iteration-mode",
                "human_guided",
                "--human-checkin-lease-minutes",
                "60",
                "--human-unavailable-fallback",
                "autonomous_review",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            config = self.read_config(tmp)
            self.assertEqual("human_guided", config["iteration_mode"])
            self.assertEqual(60, config["human_checkin_lease_minutes"])
            self.assertEqual("autonomous_review", config["human_unavailable_fallback"])
            self.assertRegex(config["human_checkin_lease_expires_at"], r"Z$")

    def test_active_human_guided_mode_is_snapshotted_for_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            initialized = self.run_pm(
                tmp,
                "init",
                "--ui-mode",
                "file",
                "--iteration-mode",
                "human_guided",
                "--human-checkin-lease-minutes",
                "60",
                "--human-unavailable-fallback",
                "pause",
            )
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            created = self.add_issue(tmp, lane="direct")
            self.assertEqual(0, created.returncode, created.stderr)
            meta, _ = pm_board.read(Path(tmp, ".pm", "issues", "m1", "i1.md"))
            self.assertEqual("human_guided", meta["iteration_mode_selected"])
            self.assertEqual("human_guided", meta["iteration_mode_effective"])
            self.assertEqual("active", meta["human_checkin_lease_status"])
            self.assertEqual("pause", meta["human_unavailable_fallback"])

            ready = self.run_pm(tmp, "next")
            self.assertEqual(0, ready.returncode, ready.stderr)
            self.assertIn("selected_mode=human_guided", ready.stdout)
            self.assertIn("effective_mode=human_guided", ready.stdout)

    def test_issue_snapshot_rejects_creation_time_status_contradictions(self):
        base = {
            "iteration_mode_selected": "human_guided",
            "iteration_mode_effective": "human_guided",
            "human_checkin_lease_minutes": "60",
            "human_unavailable_fallback": "pause",
            "human_checkin_lease_expires_at": "2029-12-31T23:59:59Z",
            "human_checkin_lease_status": "active",
            "iteration_mode_decision_at": "2030-01-01T00:00:00Z",
        }
        base["iteration_mode_snapshot_sha256"] = pm_board._canonical_sha256(base)
        with self.assertRaisesRegex(SystemExit, "contradictory"):
            pm_board._validate_iteration_snapshot(base)

        expired = dict(base)
        expired.update(
            iteration_mode_effective="autonomous",
            human_unavailable_fallback="autonomous_review",
            human_checkin_lease_status="expired",
            human_checkin_lease_expires_at="2030-01-01T00:00:01Z",
        )
        expired.pop("iteration_mode_snapshot_sha256", None)
        expired["iteration_mode_snapshot_sha256"] = pm_board._canonical_sha256(
            {
                key: expired[key] for key in pm_board.ITERATION_SNAPSHOT_KEYS
            }
        )
        with self.assertRaisesRegex(SystemExit, "contradictory"):
            pm_board._validate_iteration_snapshot(expired)

    def test_legacy_config_reads_as_autonomous_but_partial_mode_config_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            path = Path(tmp, ".pm", "config.json")
            path.write_text('{"debug": false, "ui_mode": "file"}\n', encoding="utf-8")
            shown = self.run_pm(tmp, "iteration-mode", "show")
            self.assertEqual(0, shown.returncode, shown.stderr)
            state = json.loads(shown.stdout)
            self.assertTrue(state["legacy_config"])
            self.assertEqual("autonomous", state["effective_iteration_mode"])

            path.write_text(
                '{"debug": false, "ui_mode": "file", "iteration_mode": "autonomous"}\n',
                encoding="utf-8",
            )
            refused = self.run_pm(tmp, "iteration-mode", "show")
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("partial", refused.stderr.lower())

    def test_malformed_or_contradictory_mode_config_fails_closed(self):
        invalid = (
            {
                "iteration_mode": "Fast",
                "human_checkin_lease_minutes": None,
                "human_unavailable_fallback": "pause",
                "human_checkin_lease_expires_at": None,
            },
            {
                "iteration_mode": "autonomous",
                "human_checkin_lease_minutes": 60,
                "human_unavailable_fallback": "pause",
                "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
            },
            {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": True,
                "human_unavailable_fallback": "pause",
                "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
            },
            {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_unavailable_fallback": "continue",
                "human_checkin_lease_expires_at": "2099-01-01T00:00:00Z",
            },
            {
                "iteration_mode": "human_guided",
                "human_checkin_lease_minutes": 60,
                "human_unavailable_fallback": "pause",
                "human_checkin_lease_expires_at": "2099-01-01T01:00:00+01:00",
            },
        )
        for fields in invalid:
            with self.subTest(fields=fields), tempfile.TemporaryDirectory() as tmp:
                self.init_board(tmp)
                config = self.read_config(tmp)
                config.update(fields)
                Path(tmp, ".pm", "config.json").write_text(
                    json.dumps(config) + "\n", encoding="utf-8"
                )
                refused = self.run_pm(tmp, "iteration-mode", "show")
                self.assertNotEqual(0, refused.returncode)
                self.assertIn("REFUSED", refused.stderr)

    def test_set_renew_and_return_to_autonomous_have_explicit_utc_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            selected = self.run_pm(
                tmp,
                "iteration-mode",
                "set",
                "human_guided",
                "--human-checkin-lease-minutes",
                "5",
                "--human-unavailable-fallback",
                "pause",
            )
            self.assertEqual(0, selected.returncode, selected.stderr)
            first = json.loads(selected.stdout)
            self.assertEqual("active", first["human_checkin_lease_status"])
            first_expiry = first["human_checkin_lease_expires_at"]

            renewed = self.run_pm(
                tmp,
                "iteration-mode",
                "renew",
                "--human-checkin-lease-minutes",
                "10",
            )
            self.assertEqual(0, renewed.returncode, renewed.stderr)
            second = json.loads(renewed.stdout)
            self.assertGreater(second["human_checkin_lease_expires_at"], first_expiry)
            self.assertEqual(10, second["human_checkin_lease_minutes"])

            autonomous = self.run_pm(tmp, "iteration-mode", "set", "autonomous")
            self.assertEqual(0, autonomous.returncode, autonomous.stderr)
            final = json.loads(autonomous.stdout)
            self.assertEqual("autonomous", final["effective_iteration_mode"])
            self.assertIsNone(final["human_checkin_lease_minutes"])
            self.assertIsNone(final["human_checkin_lease_expires_at"])
            persisted = self.read_config(tmp)
            self.assertFalse(persisted["debug"])
            self.assertEqual("file", persisted["ui_mode"])

            refused = self.run_pm(tmp, "iteration-mode", "renew")
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("human_guided", refused.stderr)

    def test_expired_autonomous_review_fallback_is_snapshotted_into_new_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.write_expired_config(tmp, fallback="autonomous_review")
            result = self.add_issue(tmp, lane="direct")
            self.assertEqual(0, result.returncode, result.stderr)

            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            meta, _ = pm_board.read(issue)
            self.assertEqual("human_guided", meta["iteration_mode_selected"])
            self.assertEqual("autonomous", meta["iteration_mode_effective"])
            self.assertEqual("expired", meta["human_checkin_lease_status"])
            self.assertEqual("autonomous_review", meta["human_unavailable_fallback"])
            self.assertRegex(meta["iteration_mode_decision_at"], r"Z$")
            self.assertRegex(meta["iteration_mode_snapshot_sha256"], r"^sha256:[0-9a-f]{64}$")

            changed = self.run_pm(
                tmp,
                "iteration-mode",
                "set",
                "human_guided",
                "--human-checkin-lease-minutes",
                "60",
                "--human-unavailable-fallback",
                "pause",
            )
            self.assertEqual(0, changed.returncode, changed.stderr)
            unchanged, _ = pm_board.read(issue)
            self.assertEqual("autonomous", unchanged["iteration_mode_effective"])
            self.assertEqual("expired", unchanged["human_checkin_lease_status"])

            ready = self.run_pm(tmp, "next")
            self.assertEqual(0, ready.returncode, ready.stderr)
            self.assertIn("selected_mode=human_guided", ready.stdout)
            self.assertIn("effective_mode=autonomous", ready.stdout)
            self.assertIn("lease=expired", ready.stdout)
            self.assertIn("fallback=autonomous_review", ready.stdout)

    def test_expired_pause_fallback_blocks_new_issue_without_partial_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.write_expired_config(tmp, fallback="pause")
            result = self.add_issue(tmp, lane="direct")
            self.assertNotEqual(0, result.returncode)
            self.assertRegex(result.stderr.lower(), r"expired|pause")
            self.assertFalse(Path(tmp, ".pm", "issues", "m1", "i1.md").exists())

    def test_tampered_issue_mode_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(
                issue.read_text(encoding="utf-8").replace(
                    "iteration_mode_effective: autonomous",
                    "iteration_mode_effective: human_guided",
                    1,
                ),
                encoding="utf-8",
            )
            refused = self.run_pm(tmp, "next")
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("snapshot digest", refused.stderr)

    def test_iteration_mode_mutation_refuses_symlink_and_hardlink_config(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            outside = Path(outside_raw, "pm")
            outside.mkdir()
            Path(tmp, ".pm").symlink_to(outside)
            refused = self.run_pm(tmp, "init", "--ui-mode", "file")
            self.assertNotEqual(0, refused.returncode)
            self.assertRegex(refused.stderr.lower(), r"plain|follow|directory")
            self.assertEqual([], list(outside.iterdir()))

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            self.init_board(tmp)
            config = Path(tmp, ".pm", "config.json")
            outside = Path(outside_raw, "config.json")
            outside.write_bytes(config.read_bytes())
            before = outside.read_bytes()
            config.unlink()
            config.symlink_to(outside)
            refused = self.run_pm(tmp, "iteration-mode", "set", "autonomous")
            self.assertNotEqual(0, refused.returncode)
            self.assertRegex(refused.stderr.lower(), r"follow|regular|symlink")
            self.assertEqual(before, outside.read_bytes())

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside_raw:
            self.init_board(tmp)
            config = Path(tmp, ".pm", "config.json")
            outside = Path(outside_raw, "linked.json")
            os.link(config, outside)
            before = config.read_bytes()
            refused = self.run_pm(tmp, "iteration-mode", "set", "autonomous")
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("hardlink", refused.stderr.lower())
            self.assertEqual(before, config.read_bytes())
            self.assertEqual(before, outside.read_bytes())

    def test_new_issue_creates_bound_task_workspace_and_show_verifies_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertTrue(Path(tmp, ".pm", "tasks").is_dir())
            self.assertEqual([], list(Path(tmp, ".pm", "tasks").iterdir()))
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            created = self.add_issue(tmp, lane="direct")
            self.assertEqual(0, created.returncode, created.stderr)

            workspace = Path(tmp, ".pm", "tasks", "i1")
            task = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
            self.assertEqual("pm-task-workspace/v1", task["schema"])
            self.assertEqual("i1", task["issueId"])
            self.assertEqual(".pm/issues/m1/i1.md", task["issuePath"])
            self.assertRegex(task["iterationModeSnapshotSha256"], r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(b"", (workspace / "events.jsonl").read_bytes())
            self.assertTrue((workspace / "experiments").is_dir())

            shown = self.run_pm(tmp, "task-workspace", "show", "i1")
            self.assertEqual(0, shown.returncode, shown.stderr)
            rendered = json.loads(shown.stdout)
            self.assertEqual(task, rendered["task"])
            self.assertEqual([], rendered["events"])

    def test_task_workspace_init_recovers_a_directory_only_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            workspace = Path(tmp, ".pm", "tasks", "i1")
            task = workspace / "task.json"
            events = workspace / "events.jsonl"
            task.unlink()
            events.unlink()

            recovered = self.run_pm(tmp, "task-workspace", "init", "i1")
            self.assertEqual(0, recovered.returncode, recovered.stderr)
            self.assertTrue(task.is_file())
            self.assertEqual(b"", events.read_bytes())

            repeat = self.run_pm(tmp, "task-workspace", "init", "i1")
            self.assertEqual(0, repeat.returncode, repeat.stderr)

    def test_task_workspace_init_refuses_conflicting_partial_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            task = Path(tmp, ".pm", "tasks", "i1", "task.json")
            task.write_text("{\"schema\":\"wrong\"}\n", encoding="utf-8")

            refused = self.run_pm(tmp, "task-workspace", "init", "i1")
            self.assertNotEqual(0, refused.returncode)
            self.assertRegex(refused.stderr, r"workspace metadata|repair the workspace contents")

    def test_task_workspace_mode_boundary_excludes_naive(self):
        contract = (HERE.parent / "references" / "iteration-modes.md").read_text(
            encoding="utf-8"
        )
        skill = (HERE.parent / "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(("autonomous", "human_guided"), pm_board.ITERATION_MODES)
        self.assertIn("Naive mode", contract)
        self.assertIn("never calls these commands", contract)
        self.assertIn("Naive mode", skill)
        self.assertIn("never initializes, reads, writes", skill)

    def test_legacy_issue_requires_explicit_task_workspace_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            shutil.rmtree(Path(tmp, ".pm", "tasks", "i1"))
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            snapshot_keys = {
                *pm_board.ITERATION_SNAPSHOT_KEYS,
                "iteration_mode_snapshot_sha256",
                "task_workspace",
            }
            issue.write_text(
                "\n".join(
                    line
                    for line in issue.read_text(encoding="utf-8").splitlines()
                    if line.partition(":")[0] not in snapshot_keys
                )
                + "\n",
                encoding="utf-8",
            )

            refused = self.run_pm(
                tmp,
                "task-workspace",
                "record",
                "i1",
                "--kind",
                "expectation",
                "--summary",
                "expect the focused check to pass",
            )
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("task-workspace init", refused.stderr)

            migrated = self.run_pm(tmp, "task-workspace", "init", "i1")
            self.assertEqual(0, migrated.returncode, migrated.stderr)
            self.assertTrue(Path(tmp, ".pm", "tasks", "i1", "task.json").is_file())

    def test_task_workspace_records_real_paths_and_hash_chained_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            experiment = Path(tmp, ".pm", "tasks", "i1", "experiments", "trial-1")
            experiment.mkdir(parents=True)
            evidence = experiment / "result.txt"
            evidence.write_text("observed result\n", encoding="utf-8")

            expectation = self.run_pm(
                tmp,
                "task-workspace",
                "record",
                "i1",
                "--kind",
                "expectation",
                "--summary",
                "expect one observed result",
                "--experiment-path",
                ".pm/tasks/i1/experiments/trial-1",
            )
            self.assertEqual(0, expectation.returncode, expectation.stderr)
            observed = self.run_pm(
                tmp,
                "task-workspace",
                "record",
                "i1",
                "--kind",
                "observation",
                "--summary",
                "the real trial produced one result",
                "--experiment-path",
                ".pm/tasks/i1/experiments/trial-1",
                "--file",
                ".pm/tasks/i1/experiments/trial-1/result.txt",
            )
            self.assertEqual(0, observed.returncode, observed.stderr)

            shown = self.run_pm(tmp, "task-workspace", "show", "i1")
            self.assertEqual(0, shown.returncode, shown.stderr)
            events = json.loads(shown.stdout)["events"]
            self.assertEqual([1, 2], [event["sequence"] for event in events])
            self.assertEqual(pm_board.TASK_EVENT_GENESIS, events[0]["previousEventSha256"])
            self.assertEqual(events[0]["eventSha256"], events[1]["previousEventSha256"])
            recorded = events[1]["evidence"][0]
            self.assertEqual(".pm/tasks/i1/experiments/trial-1/result.txt", recorded["path"])
            self.assertEqual(len(evidence.read_bytes()), recorded["bytes"])
            self.assertEqual(
                "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest(),
                recorded["sha256"],
            )

    def test_task_workspace_show_rejects_drift_in_recorded_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            evidence = Path(tmp, ".pm", "tasks", "i1", "experiments", "result.txt")
            evidence.write_text("original\n", encoding="utf-8")
            recorded = self.run_pm(
                tmp, "task-workspace", "record", "i1", "--kind", "observation",
                "--summary", "recorded output", "--file", ".pm/tasks/i1/experiments/result.txt",
            )
            self.assertEqual(0, recorded.returncode, recorded.stderr)
            evidence.write_text("changed after recording\n", encoding="utf-8")

            refused = self.run_pm(tmp, "task-workspace", "show", "i1")
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("evidence bytes changed", refused.stderr)

    def test_task_workspace_binds_immutable_issue_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            issue = Path(tmp, ".pm", "issues", "m1", "i1.md")
            issue.write_text(
                issue.read_text(encoding="utf-8").replace("focused check", "changed acceptance"),
                encoding="utf-8",
            )

            refused = self.run_pm(tmp, "task-workspace", "show", "i1")
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("issue snapshot", refused.stderr)

    def test_task_workspace_rejects_other_task_control_file_as_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            # A second task control file is not a candidate observation, even though it is a
            # regular file under the repository root.
            other = Path(tmp, ".pm", "tasks", "other")
            other.mkdir()
            (other / "task.json").write_text("not evidence\n", encoding="utf-8")
            refused = self.run_pm(
                tmp, "task-workspace", "record", "i1", "--kind", "observation",
                "--summary", "wrong control file", "--file", ".pm/tasks/other/task.json",
            )
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("another task workspace control file", refused.stderr)

    def test_task_workspace_rejects_tamper_missing_evidence_and_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            experiment = Path(tmp, ".pm", "tasks", "i1", "experiments", "trial")
            experiment.mkdir(parents=True)
            regular = experiment / "regular.txt"
            regular.write_text("real\n", encoding="utf-8")

            outside_experiment = Path(tmp, "experiments", "outside")
            outside_experiment.mkdir(parents=True)
            refused_experiment = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "expectation", "--summary", "outside path",
                "--experiment-path", "experiments/outside",
            )
            self.assertNotEqual(0, refused_experiment.returncode)
            self.assertIn("task-owned experiment root", refused_experiment.stderr)

            no_evidence = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "verify", "--summary", "verification ran",
            )
            self.assertNotEqual(0, no_evidence.returncode)
            self.assertIn("actual evidence", no_evidence.stderr)

            missing = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "experiment", "--summary", "trial ran",
                "--file", ".pm/tasks/i1/experiments/trial/missing.txt",
            )
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("without following links", missing.stderr)

            escaped = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "experiment", "--summary", "trial ran",
                "--file", "../outside.txt",
            )
            self.assertNotEqual(0, escaped.returncode)
            self.assertIn("repository-relative", escaped.stderr)

            linked_directory = experiment.parent / "linked-trial"
            linked_directory.symlink_to(experiment.name, target_is_directory=True)
            linked_experiment = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "expectation", "--summary", "linked trial must be rejected",
                "--experiment-path", ".pm/tasks/i1/experiments/linked-trial",
            )
            self.assertNotEqual(0, linked_experiment.returncode)
            self.assertRegex(linked_experiment.stderr.lower(), r"directory|follow|symlink")

            symlink = experiment / "linked.txt"
            symlink.symlink_to(regular.name)
            linked = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "experiment", "--summary", "trial ran",
                "--file", ".pm/tasks/i1/experiments/trial/linked.txt",
            )
            self.assertNotEqual(0, linked.returncode)
            self.assertRegex(linked.stderr.lower(), r"regular|follow|symlink")

            hardlink = experiment / "hard.txt"
            os.link(regular, hardlink)
            hard = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "experiment", "--summary", "trial ran",
                "--file", ".pm/tasks/i1/experiments/trial/hard.txt",
            )
            self.assertNotEqual(0, hard.returncode)
            self.assertIn("hardlink", hard.stderr.lower())
            hardlink.unlink()

            fifo = experiment / "fifo"
            os.mkfifo(fifo)
            special = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "experiment", "--summary", "trial ran",
                "--file", ".pm/tasks/i1/experiments/trial/fifo",
            )
            self.assertNotEqual(0, special.returncode)
            self.assertIn("regular", special.stderr.lower())

            ok = self.run_pm(
                tmp, "task-workspace", "record", "i1",
                "--kind", "experiment", "--summary", "real trial ran",
                "--file", ".pm/tasks/i1/experiments/trial/regular.txt",
            )
            self.assertEqual(0, ok.returncode, ok.stderr)
            log = Path(tmp, ".pm", "tasks", "i1", "events.jsonl")
            document = json.loads(log.read_text(encoding="utf-8"))
            document["summary"] = "tampered"
            log.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
            refused = self.run_pm(tmp, "task-workspace", "show", "i1")
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("hash chain", refused.stderr)

    def test_task_workspace_single_writer_lock_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.init_board(tmp)
            self.assertEqual(0, self.add_milestone(tmp).returncode)
            self.assertEqual(0, self.add_issue(tmp, lane="direct").returncode)
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                with pm_board._task_workspace_lock("i1"):
                    blocked = self.run_pm(
                        tmp, "task-workspace", "record", "i1",
                        "--kind", "expectation", "--summary", "one writer only",
                    )
            finally:
                os.chdir(previous)
            self.assertNotEqual(0, blocked.returncode)
            self.assertIn("another writer", blocked.stderr)
            shown = self.run_pm(tmp, "task-workspace", "show", "i1")
            self.assertEqual(0, shown.returncode, shown.stderr)
            self.assertEqual([], json.loads(shown.stdout)["events"])


if __name__ == "__main__":
    unittest.main()
