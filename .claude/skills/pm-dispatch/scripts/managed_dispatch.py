#!/usr/bin/env python3
"""Dependency-aware owner dispatch, without a global PM board or a second workflow engine.

CLI: managed_dispatch.py run PLAN.json --run-dir RUN_DIR
     managed_dispatch.py status RUN_DIR

PLAN uses managed-dispatch-plan/v1: request_id, inherited envelope and nodes.
Each node declares id, owner, action_class, workspace, reads, writes, resources,
depends_on, brief, envelope and launch. launch is {kind: native, request: canonical
launch request} or {kind: workflow, mapping: repository-relative admitted mapping}.
There is deliberately no shell/argv/provider/model override field. Native calls use
our existing fresh resolver and native retry runner; workflow owners use the existing
serial controller in isolated workspaces. Evidence roots are controller-assigned.

Only registered owners are governed. Scopes prevent conflicting dispatch; they are
not a sandbox or a Codex tool hook. Events and scoped returns are accounting, not
independently authenticated completion proofs. A drained batch returns candidates to
its outer owner. It never emits final_return, claims project acceptance, or starts a
second terminal-review lifecycle. The existing review/proof route remains authoritative.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any

import cow_workspace

SKILLS = Path(__file__).resolve().parents[2]
SCHEMA = "managed-dispatch-plan/v1"
STATES = ("planned", "dispatched", "running", "result_received", "reviewed_or_routed", "complete")
NONTERMINAL_EVENTS = {"progress_update", "checkpoint", "observation_timeout"}
AUTHORITY = {"read_only": 0, "write": 1, "experiment": 2}


class DispatchError(ValueError):
    pass


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispatchError(f"{label} must be nonempty text")
    return value


def exact(value: Any, required: set[str], optional: set[str], label: str) -> dict:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise DispatchError(f"{label} has missing or unsupported fields")
    return value


def strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value) or len(set(value)) != len(value):
        raise DispatchError(f"{label} must be a unique string list")
    return value


def plain_path(path: Path) -> Path:
    """Reject symlinks including existing ancestors; no security/sandbox claim."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise DispatchError(f"symlink scope or evidence path: {part}")
    return path


def scoped_path(root: Path, name: str) -> Path:
    relative = Path(text(name, "scope"))
    if relative.is_absolute() or ".." in relative.parts:
        raise DispatchError("scopes must be relative and cannot escape their workspace")
    return plain_path(root / relative)


def overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def action_preflight(action_class: str, owner: str, writes: list) -> None:
    if action_class not in {"inspect", "disposable_verify", "mutation", "external_side_effect"}:
        raise DispatchError("unknown action_class")
    if owner not in {"top", "bounded_worker", "admitted_workflow"}:
        raise DispatchError("unknown mutation owner")
    if owner == "top" and (action_class in {"mutation", "external_side_effect"} or writes):
        raise DispatchError("top mutation is forbidden; delegate to a bounded owner")
    if action_class == "mutation" and not writes:
        raise DispatchError("mutation requires concrete write scopes")
    if action_class in {"inspect", "disposable_verify"} and writes:
        raise DispatchError("inspection/disposable verification cannot declare persistent writes")


def validate_iteration(policy: Any) -> None:
    if policy == "autonomous":
        return  # Explicit conservative legacy snapshot; no PM board required.
    if not isinstance(policy, dict):
        raise DispatchError("iteration policy needs the complete immutable PM snapshot")
    # Reuse the existing pure snapshot validator, without initializing/reading PM.
    module_name = "_managed_dispatch_pm_contract"
    module = sys.modules.get(module_name)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_name, Path(__file__).with_name("pm.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    if set(policy) != set(module.ITERATION_SNAPSHOT_KEYS) | {"iteration_mode_snapshot_sha256"}:
        raise DispatchError("partial or expanded iteration snapshot")
    try:
        module._validate_iteration_snapshot(policy)
        if policy["iteration_mode_effective"] == "human_guided":
            expiry = module._parse_utc(policy["human_checkin_lease_expires_at"], "lease expiry")
            if expiry <= datetime.now(timezone.utc):
                raise DispatchError("human-guided lease expired before dispatch; outer owner must resolve the next cycle")
    except SystemExit as exc:
        raise DispatchError(str(exc)) from exc


def validate_envelope(envelope: dict, root: dict | None = None) -> None:
    required = {"schema", "invocationId", "invocationMode", "authority", "terminalOwner", "workerClass",
                "activeSkillStack", "budgets", "requiredReceipts", "parentReceipts", "iterationPolicy", "humanFeedbackReceipt"}
    exact(envelope, required, set(), "invocation envelope")
    if envelope["schema"] != "skill-invocation-envelope/v1" or envelope["authority"] not in AUTHORITY:
        raise DispatchError("invalid invocation envelope schema/authority")
    for field in ("invocationId", "terminalOwner", "workerClass"):
        text(envelope[field], field)
    strings(envelope["activeSkillStack"], "activeSkillStack")
    strings(envelope["requiredReceipts"], "requiredReceipts")
    if not isinstance(envelope["parentReceipts"], list) or not isinstance(envelope["budgets"], dict):
        raise DispatchError("envelope budgets and receipts are required")
    for key, value in envelope["budgets"].items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise DispatchError(f"invalid budget {key}")
    for required_budget in ("model_calls", "wall_time_seconds"):
        if envelope["budgets"].get(required_budget, 0) <= 0:
            raise DispatchError(f"missing positive {required_budget} budget")
    if not isinstance(envelope["budgets"]["model_calls"], int):
        raise DispatchError("model_calls must allocate whole native activations")
    if envelope["budgets"]["wall_time_seconds"] < 3600:
        raise DispatchError("native owners require at least 3600 seconds; observation timeout is separate")
    if root is None:
        validate_iteration(envelope["iterationPolicy"])
        if envelope["humanFeedbackReceipt"] is not None:
            feedback = envelope["humanFeedbackReceipt"]
            exact(feedback, {"schema", "checkpoint_artifact", "checkpoint_sha256", "checkpoint_id", "human_feedback", "received_at", "receipt_sha256"}, set(), "human feedback receipt")
            unsigned = {key: value for key, value in feedback.items() if key != "receipt_sha256"}
            if feedback["receipt_sha256"] != digest(canonical(unsigned)) or feedback["checkpoint_sha256"] != digest(plain_path(Path(feedback["checkpoint_artifact"])).read_bytes()):
                raise DispatchError("human feedback is not bound to the exact checkpoint")
        if envelope["invocationMode"] not in {"root", "child"}:
            raise DispatchError("invalid invocation mode")
        owns_terminal = envelope["terminalOwner"] == envelope["invocationId"]
        if owns_terminal != (envelope["invocationMode"] == "root"):
            raise DispatchError("nested dispatch must retain its outer lifecycle owner")
    else:
        if envelope["invocationMode"] != "child" or envelope["terminalOwner"] != root["terminalOwner"]:
            raise DispatchError("child cannot acquire terminal ownership")
        if envelope["iterationPolicy"] != root["iterationPolicy"] or envelope["humanFeedbackReceipt"] != root["humanFeedbackReceipt"]:
            raise DispatchError("child changed iteration policy/feedback")
        if AUTHORITY[envelope["authority"]] > AUTHORITY[root["authority"]]:
            raise DispatchError("child expanded authority")
        if envelope["activeSkillStack"][:len(root["activeSkillStack"])] != root["activeSkillStack"]:
            raise DispatchError("child discarded active skill stack")
        if any(key not in root["budgets"] or value > root["budgets"][key] for key, value in envelope["budgets"].items()):
            raise DispatchError("child expanded budget")


def validate_plan(plan: dict) -> dict:
    plan = deepcopy(plan)
    exact(plan, {"schema", "request_id", "envelope", "nodes"},
          {"max_concurrency", "serialization_reason", "observation_seconds"}, "plan")
    if plan["schema"] != SCHEMA or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", text(plan["request_id"], "request_id")):
        raise DispatchError("invalid plan schema/request id")
    validate_envelope(plan["envelope"])
    if not isinstance(plan["nodes"], list) or not plan["nodes"]:
        raise DispatchError("plan requires at least one real node")
    ids: set[str] = set()
    invocation_ids = {plan["envelope"]["invocationId"]}
    for node in plan["nodes"]:
        exact(node, {"id", "owner", "action_class", "workspace", "reads", "writes", "resources", "depends_on", "brief", "envelope", "launch"}, {"workspace_mode"}, "node")
        node_id = text(node["id"], "node id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", node_id) or node_id in ids:
            raise DispatchError("duplicate or unsafe node id")
        ids.add(node_id)
        if node["owner"] not in {"bounded_worker", "admitted_workflow"}:
            raise DispatchError("managed nodes need a delegated owner; top mutations are forbidden")
        if node.get("workspace_mode", "shared") not in {"shared", "copy_on_write"}:
            raise DispatchError("workspace_mode must be shared or copy_on_write")
        if node.get("workspace_mode") == "copy_on_write" and node["owner"] != "admitted_workflow":
            raise DispatchError("copy_on_write workspace mode is reserved for admitted workflows")
        action_preflight(node["action_class"], node["owner"], node["writes"])
        root = plain_path(Path(text(node["workspace"], "workspace")))
        if not root.is_dir():
            raise DispatchError(f"workspace is not a directory: {root}")
        node["workspace"] = str(root)
        for field in ("reads", "writes", "resources", "depends_on"):
            strings(node[field], field)
        for scope in node["reads"] + node["writes"]:
            scoped_path(root, scope)
        validate_envelope(node["envelope"], plan["envelope"])
        child = node["envelope"]
        if child["invocationId"] in invocation_ids:
            raise DispatchError("invocation ids must be unique")
        invocation_ids.add(child["invocationId"])
        if node["action_class"] in {"mutation", "external_side_effect"} and child["authority"] == "read_only":
            raise DispatchError("mutation has read-only authority")
        if node["action_class"] in {"inspect", "disposable_verify"} and child["authority"] != "read_only":
            raise DispatchError("read-only action cannot acquire a writable native profile")
        if node["action_class"] == "external_side_effect" and not child["parentReceipts"]:
            raise DispatchError("external side effect needs explicit parent authority receipts")
        if child["authority"] == "experiment" and not child["parentReceipts"]:
            raise DispatchError("experiment requires parent resource/authority receipts")
        text(node["brief"], "brief")
        launch = node["launch"]
        if node["owner"] == "bounded_worker":
            exact(launch, {"kind", "request"}, set(), "native launch")
            if launch["kind"] != "native":
                raise DispatchError("bounded workers use the canonical native bridge")
            request = exact(launch["request"], {"taskClass", "reasoningPosture", "semanticRole", "reviewMode", "writerRole"}, set(), "launch request")
            for field in ("taskClass", "reasoningPosture", "semanticRole"):
                text(request[field], field)
            if request["reviewMode"] not in {"none", "exact-opposite"}:
                raise DispatchError("invalid review mode")
            if request["reviewMode"] == "exact-opposite":
                text(request["writerRole"], "writerRole")
            elif request["writerRole"] is not None:
                raise DispatchError("ordinary worker cannot invent writerRole")
            if child["workerClass"] == "terminal-reviewer" and (
                    request["reviewMode"] != "exact-opposite" or child["authority"] != "read_only"):
                raise DispatchError("reviewer must be read-only and use the existing exact-opposite route")
        else:
            exact(launch, {"kind", "mapping"}, set(), "workflow launch")
            if launch["kind"] != "workflow" or not scoped_path(root, launch["mapping"]).is_file():
                raise DispatchError("workflow needs its admitted mapping; inner controller validates admission")
    by_id = {node["id"]: node for node in plan["nodes"]}
    ancestors: dict[str, set[str]] = {}
    def visit(node_id: str, visiting: set[str]) -> set[str]:
        if node_id in visiting:
            raise DispatchError("dependency cycle")
        if node_id not in by_id:
            raise DispatchError(f"unknown dependency {node_id}")
        if node_id not in ancestors:
            ancestors[node_id] = set()
            for dep in by_id[node_id]["depends_on"]:
                ancestors[node_id].add(dep)
                ancestors[node_id].update(visit(dep, visiting | {node_id}))
        return ancestors[node_id]
    for node_id in ids:
        visit(node_id, set())
    max_concurrency = plan.setdefault("max_concurrency", max(2, len(ids)))
    if not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool) or max_concurrency < 1:
        raise DispatchError("max_concurrency must be a positive integer")
    if "serialization_reason" in plan:
        reason = exact(plan.get("serialization_reason"), {"kind", "detail"}, set(), "serialization_reason")
        if reason["kind"] not in {"resource_limit", "user_instruction"} or len(text(reason["detail"], "serialization detail").strip()) < 12:
            raise DispatchError("concurrency cap requires a concrete resource limit or explicit user instruction")
    observation = plan.setdefault("observation_seconds", 30)
    if not isinstance(observation, (int, float)) or isinstance(observation, bool) or not math.isfinite(observation) or observation <= 0:
        raise DispatchError("invalid observation_seconds")
    for budget in ("model_calls", "compute", "external_spend", "experiments"):
        if sum(n["envelope"]["budgets"].get(budget, 0) for n in plan["nodes"]) > plan["envelope"]["budgets"].get(budget, 0):
            raise DispatchError(f"child allocations exceed total {budget} budget")
    return plan


def conflicts(a: dict, b: dict) -> list[str]:
    reasons = [f"shared resource: {x}" for x in sorted(set(a["resources"]) & set(b["resources"]))]
    ar, br = Path(a["workspace"]), Path(b["workspace"])
    # The inner proof controller binds whole-workspace snapshots. Disjoint edited files
    # alone cannot make two such runs safe; isolate the entire worktree and run roots.
    if overlaps(ar, br) and "admitted_workflow" in {a["owner"], b["owner"]}:
        reasons.append("workflow proof binds overlapping workspaces; use separate worktrees")
    for left, right in ((a, b), (b, a)):
        for write in left["writes"]:
            for scope in right["reads"] + right["writes"]:
                if overlaps(scoped_path(Path(left["workspace"]), write), scoped_path(Path(right["workspace"]), scope)):
                    reasons.append(f"scope conflict: {left['id']}:{write} / {right['id']}:{scope}")
    return sorted(set(reasons))


# Fixed executable bridge. It imports the canonical policy resolver/retry runner; a
# task cannot replace these modules, choose arbitrary argv or supply a forged receipt.
BRIDGE = r'''
import fs from "node:fs";
import path from "node:path";
import {createHash} from "node:crypto";
import {pathToFileURL} from "node:url";
const spec = JSON.parse(fs.readFileSync(process.argv.at(-1), "utf8"));
const load = async relative => import(pathToFileURL(path.join(spec.skills, relative)).href);
const hash = bytes => createHash("sha256").update(bytes).digest("hex");
const controller = new AbortController();
for(const sig of ["SIGINT","SIGTERM"]) process.on(sig,()=>controller.abort());
const timer = setTimeout(()=>controller.abort(),spec.node.envelope.budgets.wall_time_seconds*1000);
try {
if (spec.node.launch.kind === "workflow") {
  const {runController} = await load("subagent-workflow/scripts/controller/run.ts");
  const {state, result, runDir} = await runController({root:spec.node.workspace,
    mappingPath:spec.node.launch.mapping, rolesPath:"config/workflows/models.json",
    nativeMapPath:".claude/skills/subagent-workflow/scripts/native/native-map.json",
    outputRoot:"workflow-output/subagent", signal:controller.signal,
    parentAccess:spec.node.envelope.authority === "read_only" ? "read" : "write",
    parentBudget:{maxModelCalls:spec.node.envelope.budgets.model_calls,
      maxWallTimeSeconds:spec.node.envelope.budgets.wall_time_seconds}});
  // Independent review deliberately keeps result.terminal unresolved after a
  // successful execution. Hand the candidate to the owner; never invent COMPLETE.
  const response = {schema:"managed-worker-result/v1", request_id:spec.request_id,
    node_id:spec.node.id, status:state.status === "success" ? "complete" : "needs_revision",
    summary:JSON.stringify(result), evidence:[path.relative(spec.node.workspace,path.join(runDir,"result.json"))]};
  console.log(JSON.stringify(response));
  process.exitCode = 0; // The typed result preserves unresolved/revision evidence.
} else {
  const native = await load("subagent-workflow/scripts/native/run.ts");
  const {createBoundLaunchResolver} = await load("subagent-workflow/scripts/controller/launch_resolver.ts");
  const models = path.join(spec.node.workspace,"config/workflows/models.json");
  const resolve = createBoundLaunchResolver(spec.node.workspace,{path:models,sha256:hash(fs.readFileSync(models))});
  const receipt = resolve(spec.node.launch.request);
  const bytes = JSON.stringify(receipt)+"\n";
  fs.writeFileSync(path.join(spec.evidence_dir,"launch-receipt.json"),bytes,{flag:"wx"});
  const r = receipt.resolved;
  const cmd = receipt.policy.freshCatalogEvidence[r.backend].command;
  const prefix = cmd.invokedArgv.slice(0,cmd.invokedArgv.length-cmd.argv.length+1);
  const authorization = {backend:r.backend,provider:r.provider,logicalExecutable:cmd.argv[0],
    catalogModelId:r.catalogModelId,executable:cmd.executable,executableSha256:cmd.executableSha256,
    invocationPrefix:prefix,executionClosure:cmd.executionClosure,executionClosureSha256:cmd.executionClosureSha256,
    launchEnvironment:{...resolve.launchEnvironment},
    launchEnvironmentSha256:native.nativeChildEnvironmentSha256(resolve.launchEnvironment),
    launchReceiptSha256:hash(bytes)};
  native.verifyNativeLaunchAuthorization(authorization);
  const map = JSON.parse(fs.readFileSync(path.join(spec.skills,"subagent-workflow/scripts/native/native-map.json"),"utf8"));
  const runner = native.makeNativeRunner(map,1,path.join(spec.evidence_dir,"native-trace.jsonl"));
  const prompt = spec.prompt + '\nNative adapter: return a WorkflowActivationOutput with summary and exactly one statePatch entry '
    + '{"op":"set","path":"/managedResult","value":<the complete managed-worker-result/v1 object above>}. '
    + 'The managed result is the patch value, not the top-level output. A reviewer additionally places its same verdict '
    + 'at the top level; keep the complete scoped result, including evidence commitment, inside /managedResult.';
  const args = ["launch","--model",`${r.provider}/${r.catalogModelId}`,
    ...(["codex","claude"].includes(r.backend)?["--effort",r.effort]:[]),"-p",prompt];
  const result = await runner(args,{cwd:spec.node.workspace,signal:controller.signal,
    env:{GENERAL_AGENT_WORKSPACE_ACCESS:spec.node.envelope.authority === "read_only" ? "read" : "write"},authorization});
  fs.writeFileSync(path.join(spec.evidence_dir,"native.stdout"),result.stdout);
  fs.writeFileSync(path.join(spec.evidence_dir,"native.stderr"),result.stderr);
  if(result.exitCode === 0) {
    const activation = JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1));
    const patch = activation.statePatch;
    if(!Array.isArray(patch) || patch.length !== 1 || patch[0].op !== "set" || patch[0].path !== "/managedResult")
      throw new Error("native activation did not return exactly one scoped managed result");
    console.log(JSON.stringify(patch[0].value));
  }
  if(result.stderr) console.error(result.stderr);
  process.exitCode = result.exitCode;
}
} finally { clearTimeout(timer); }
'''


def bridge_environment() -> dict[str, str]:
    """Reuse canonical scrubbing before Bun starts, not just inside its native child."""
    name = "_managed_dispatch_live_catalog"
    if name not in sys.modules:
        source = SKILLS / "update-platform-config/scripts/config-sync/live_catalog.py"
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]._scrub_env(dict(os.environ))


def build_command(spec_path: Path) -> list[str]:
    bun = shutil.which("bun")
    if not bun:
        raise DispatchError("Bun unavailable; do not fall back to direct top mutation")
    return [str(Path(bun).resolve()), "--eval", BRIDGE, str(spec_path)]


class Dispatcher:
    def __init__(self, plan: dict, run_dir: Path):
        self.plan = validate_plan(plan)
        self.nodes = {n["id"]: n for n in self.plan["nodes"]}
        self.run_dir = plain_path(run_dir)
        # Evidence cannot be shared with any declared worker scope or proof workspace.
        for node in self.nodes.values():
            workspace = Path(node["workspace"])
            if node["owner"] == "admitted_workflow" and overlaps(workspace, self.run_dir):
                raise DispatchError("managed evidence must be outside workflow proof workspaces")
            if any(overlaps(scoped_path(workspace, scope), self.run_dir) for scope in node["writes"]):
                raise DispatchError("controller evidence overlaps worker write scope")
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "nodes").mkdir()
        (self.run_dir / "plan.json").write_bytes(canonical(self.plan) + b"\n")
        self.states = {key: "planned" for key in self.nodes}
        self.handles: dict[str, dict] = {}
        self.cow_manifests: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.events: list[dict] = []
        self.stopped = False
        self.candidate_ready = False
        self.started_monotonic = time.monotonic()
        for node_id in self.nodes:
            self.emit("planned", node_id)

    def emit(self, kind: str, node_id: str | None = None, **payload: Any) -> dict:
        event = {"sequence": len(self.events) + 1, "time": time.time(), "event": kind,
                 "request_id": self.plan["request_id"], "node_id": node_id, **payload}
        self.events.append(event)
        with (self.run_dir / "events.jsonl").open("ab") as output:
            output.write(canonical(event) + b"\n")
        snapshot = self.snapshot()
        temporary = self.run_dir / "snapshot.tmp"
        temporary.write_bytes(canonical(snapshot) + b"\n")
        temporary.replace(self.run_dir / "snapshot.json")
        return event

    def snapshot(self) -> dict:
        return {"schema": "managed-dispatch-status/v1", "request_id": self.plan["request_id"],
                "states": dict(self.states), "pending": [key for key, state in self.states.items() if state != "complete"],
                "handles": {key: {"pid": h["process"].pid, "started_at": h["started_at"], "evidence_dir": str(h["directory"]), "workspace": h.get("workspace")} for key, h in self.handles.items()},
                "results": self.results, "candidate_ready": self.candidate_ready,
                "terminal": False, "next_action": "outer owner must review or route scoped results",
                "stopped": self.stopped}

    def notify(self, kind: str, summary: str) -> None:
        if kind not in NONTERMINAL_EVENTS:
            raise DispatchError("notify accepts nonterminal progress/checkpoint/observation only")
        self.emit(kind, summary=text(summary, "summary"), pending=self.snapshot()["pending"])

    def ready(self) -> list[str]:
        return [key for key, node in self.nodes.items() if self.states[key] == "planned"
                and all(self.states[dep] == "complete" for dep in node["depends_on"])]

    def _prompt(self, node: dict) -> str:
        inputs = {key: self.results[key] for key in node["depends_on"]}
        for result in inputs.values():
            for binding in result["evidence_bindings"]:
                source = plain_path(Path(binding["path"]))
                if not source.is_file() or digest(source.read_bytes()) != binding["sha256"]:
                    raise DispatchError("dependency evidence changed before dispatch; route fresh evidence")
        reviewer = node["envelope"]["workerClass"] == "terminal-reviewer"
        suffix = ""
        if reviewer:
            suffix = ("\nReview only the scope and acceptance named in your brief against the actual "
                      "deliverables and dependency evidence. Return your scoped verdict, or REVISE for technical failure, "
                      "or HUMAN_BLOCKED only for a concrete "
                      "human-only dependency). Include verdict and evidence_commitment equal to the provided commitment. "
                      "This verdict is returned to the outer lifecycle owner; do not start another reviewer or memory beat.")
        contract = {"request_id": self.plan["request_id"], "node": node,
                    "dependency_results": inputs,
                    "evidence_commitment": digest(canonical(inputs))}
        return (node["brief"] + "\nInvocation and scope (cannot expand user authority):\n" + canonical(contract).decode()
                + "\nProduce this scoped result object for the runtime adapter: {\"schema\":\"managed-worker-result/v1\","
                + f"\"request_id\":{json.dumps(self.plan['request_id'])},\"node_id\":{json.dumps(node['id'])},"
                + '"status":"complete|needs_revision","summary":"actual outcome","evidence":["workspace-relative real file"]}.'
                + " Child complete is scoped return only, never assignment completion. Do not recursively commission terminal review."
                + suffix)

    def _start(self, node_id: str) -> None:
        node = self.nodes[node_id]
        directory = self.run_dir / "nodes" / node_id
        output = error = None
        try:
            remaining = self.plan["envelope"]["budgets"]["wall_time_seconds"] - (time.monotonic() - self.started_monotonic)
            if remaining < 3600:
                raise DispatchError("remaining parent budget cannot admit another full native turn")
            launch_node = deepcopy(node)
            execution_workspace = Path(node["workspace"])
            if node.get("workspace_mode") == "copy_on_write":
                execution_workspace, manifest = cow_workspace.create(
                    execution_workspace,
                    node["launch"]["mapping"],
                    Path(tempfile.gettempdir()) / "elasticweave" / "cow",
                    node_id,
                )
                self.cow_manifests[node_id] = manifest
                launch_node["workspace"] = str(execution_workspace)
                self.emit(
                    "cow_workspace_created", node_id,
                    workspace=str(execution_workspace),
                    declared_skills=manifest["declared_skills"],
                )
            launch_node["envelope"]["budgets"]["wall_time_seconds"] = min(
                node["envelope"]["budgets"]["wall_time_seconds"], remaining)
            directory.mkdir()
            spec = {"request_id": self.plan["request_id"], "node": launch_node,
                    "skills": str(execution_workspace / ".claude" / "skills"),
                    "evidence_dir": str(directory), "prompt": self._prompt(node)}
            spec_path = directory / "launch.json"
            spec_path.write_bytes(canonical(spec) + b"\n")
            output = (directory / "stdout").open("wb")
            error = (directory / "stderr").open("wb")
            command = build_command(spec_path)
            self.states[node_id] = "dispatched"
            self.emit("dispatched", node_id, owner=node["owner"], evidence_dir=str(directory))
            process = subprocess.Popen(command, cwd=execution_workspace, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=error, start_new_session=True,
                                       env=bridge_environment())
        except (OSError, DispatchError) as exc:
            if output:
                output.close()
            if error:
                error.close()
            self.states[node_id] = "failed"
            self.emit("dispatch_failed", node_id, error=str(exc), recovery="route a legal owner; no top mutation fallback")
            return
        self.handles[node_id] = {"process": process, "directory": directory,
                                 "workspace": str(execution_workspace),
                                 "stdout": output, "stderr": error, "started_at": time.time()}
        self.states[node_id] = "running"
        self.emit("running", node_id, pid=process.pid)

    def _receive(self, node_id: str, handle: dict, code: int) -> None:
        handle["stdout"].close()
        handle["stderr"].close()
        self.states[node_id] = "result_received"
        self.emit("worker_result", node_id, exit_code=code)
        self.emit("result_received", node_id)
        try:
            if node_id in self.cow_manifests:
                cow_result = cow_workspace.finalize(Path(handle["workspace"]), self.cow_manifests[node_id])
                (handle["directory"] / "cow-result.json").write_bytes(canonical(cow_result) + b"\n")
                handle["cow_result_path"] = handle["directory"] / "cow-result.json"
                self.emit(
                    "cow_workspace_finalized", node_id,
                    changed_paths=cow_result["changed_paths"],
                    workspace=handle["workspace"],
                )
            if code:
                raise DispatchError(f"owner exited {code}; native runner owns retry classification")
            lines = (handle["directory"] / "stdout").read_text().splitlines()
            result = json.loads(next(line for line in reversed(lines) if line.strip()))
            exact(result, {"schema", "request_id", "node_id", "status", "summary", "evidence"},
                  {"verdict", "evidence_commitment", "human_request"}, "worker result")
            if result["schema"] != "managed-worker-result/v1" or result["request_id"] != self.plan["request_id"] or result["node_id"] != node_id:
                raise DispatchError("result is not bound to this request and node")
            if result["status"] not in {"complete", "needs_revision"}:
                raise DispatchError("unknown scoped result status")
            text(result["summary"], "result summary")
            evidence = strings(result["evidence"], "result evidence")
            bindings = []
            for item in evidence:
                source = scoped_path(Path(handle.get("workspace", self.nodes[node_id]["workspace"])), item)
                if not source.is_file():
                    raise DispatchError(f"missing regular evidence file: {item}")
                bindings.append({"path": str(source), "sha256": digest(source.read_bytes())})
            if not bindings:
                # The native response is legitimate evidence for read-only advice, not
                # proof that implementation or experiments succeeded. Reviewer decides.
                source = handle["directory"] / "stdout"
                bindings.append({"path": str(source), "sha256": digest(source.read_bytes())})
            # The outer owner must be able to inspect the disposable checkout's
            # exact changed-path commitment even when the worker only returned
            # workflow-relative evidence.
            if handle.get("cow_result_path") is not None:
                source = plain_path(handle["cow_result_path"])
                bindings.append({"path": str(source), "sha256": digest(source.read_bytes())})
            result["evidence_bindings"] = bindings
            if self.nodes[node_id]["envelope"]["workerClass"] == "terminal-reviewer":
                expected = digest(canonical({key: self.results[key] for key in self.nodes[node_id]["depends_on"]}))
                if result.get("evidence_commitment") != expected or result.get("verdict") not in {"COMPLETE", "REVISE", "HUMAN_BLOCKED"}:
                    raise DispatchError("terminal review lacks exact request evidence commitment/verdict")
                self.emit("review_result", node_id, verdict=result["verdict"], evidence_commitment=expected,
                          terminal=False)
            self.results[node_id] = result
            if result["status"] != "complete":
                raise DispatchError("scoped result requires revision; evidence retained for the outer owner")
            if result.get("verdict") in {"REVISE", "HUMAN_BLOCKED"}:
                raise DispatchError("review result requires routing by the outer lifecycle owner")
            self.states[node_id] = "reviewed_or_routed"
            self.emit("reviewed_or_routed", node_id, route="outer_owner")
            self.states[node_id] = "complete"
            self.emit("complete", node_id, scope="node")
        except (DispatchError, ValueError, StopIteration, OSError) as exc:
            self.states[node_id] = "failed"
            self.emit("result_rejected", node_id, error=str(exc), recovery="route a legal owner; unrelated work continues")

    def tick(self) -> None:
        if self.stopped or self.candidate_ready:
            return
        for node_id, handle in list(self.handles.items()):
            code = handle["process"].poll()
            if code is not None:
                del self.handles[node_id]
                self._receive(node_id, handle, code)
        ready = self.ready()
        selected = []
        decisions = []
        for node_id in ready:
            active = list(self.handles) + selected
            reasons = [reason for other in active for reason in conflicts(self.nodes[node_id], self.nodes[other])]
            if reasons:
                decisions.append({"node": node_id, "serialization_reason": reasons})
            elif len(active) < self.plan["max_concurrency"]:
                selected.append(node_id)
            else:
                if not self.plan.get("serialization_reason"):
                    raise DispatchError("serializing independent ready work requires a concrete resource limit or user instruction")
                decisions.append({"node": node_id, "serialization_reason": self.plan.get("serialization_reason")})
        if selected:
            self.emit("dispatch_batch", independent_leaves=ready, dispatched_concurrently=selected,
                      serialization=decisions, fanin_condition="all declared dependencies complete")
            for node_id in selected:
                self._start(node_id)
        if not self.handles and all(state == "complete" for state in self.states.values()):
            self.candidate_ready = True
            self.emit("batch_result", scope="delegated batch", terminal=False, route="outer_owner")

    def run(self) -> dict:
        try:
            return self._run()
        except BaseException:
            # An orchestration failure must not leave live owners unmonitored.
            # Individual worker failures instead take the failure-isolated route.
            if self.handles:
                self.cancel("dispatcher failed; reconcile interrupted owners before retry")
            raise

    def _run(self) -> dict:
        last_observed = time.monotonic()
        while not self.stopped and not self.candidate_ready:
            self.tick()
            if not self.handles and not self.ready() and not self.candidate_ready and not self.stopped:
                self.stopped = True
                self.emit("routing_required", failed=[key for key, value in self.states.items() if value == "failed"],
                          pending=self.snapshot()["pending"], terminal=False)
                break
            if not self.stopped and not self.candidate_ready and time.monotonic() - last_observed >= self.plan["observation_seconds"]:
                self.notify("observation_timeout", "Observation window ended; registered handles remain monitored")
                last_observed = time.monotonic()
            if self.handles:
                time.sleep(min(0.05, self.plan["observation_seconds"]))
        return self.snapshot()

    def cancel(self, reason: str, *, superseded: bool = False) -> None:
        """Explicit cancellation only; ordinary worker failure never calls this path."""
        text(reason, "cancellation reason")
        for handle in self.handles.values():
            process = handle["process"]
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for node_id, handle in list(self.handles.items()):
            process = handle["process"]
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            handle["stdout"].close()
            handle["stderr"].close()
            self.states[node_id] = "cancelled"
            del self.handles[node_id]
        self.stopped = True
        self.emit("cancelled" if not superseded else "superseded", reason=reason, terminal=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("plan", type=Path)
    run.add_argument("--run-dir", type=Path, required=True)
    status = sub.add_parser("status")
    status.add_argument("run_dir", type=Path)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("run_dir", type=Path)
    cleanup.add_argument("--accepted", action="store_true")
    args = parser.parse_args(argv)
    dispatcher = None
    try:
        if args.command == "status":
            print(plain_path(args.run_dir / "snapshot.json").read_text(), end="")
            return 0
        if args.command == "cleanup":
            if not args.accepted:
                raise DispatchError("COW cleanup requires the explicit --accepted acknowledgement")
            run_dir = plain_path(args.run_dir)
            snapshot = json.loads((run_dir / "snapshot.json").read_text())
            if snapshot.get("handles"):
                raise DispatchError("COW cleanup requires all worker processes to be drained")
            if not snapshot.get("candidate_ready") and not snapshot.get("stopped"):
                raise DispatchError("COW cleanup requires a candidate-ready or stopped dispatch")
            created = []
            events = run_dir / "events.jsonl"
            if events.is_file():
                for line in events.read_text().splitlines():
                    event = json.loads(line)
                    if event.get("event") == "cow_workspace_created":
                        created.append(Path(event["workspace"]))
            removed = []
            for workspace in sorted(set(created)):
                cow_workspace.remove(workspace)
                removed.append(str(workspace))
            (run_dir / "cleanup.json").write_bytes(canonical({"schema": "workflow-cow-cleanup/v1", "removed": removed}) + b"\n")
            print(json.dumps({"schema": "workflow-cow-cleanup/v1", "removed": removed}, ensure_ascii=False))
            return 0
        dispatcher = Dispatcher(json.loads(plain_path(args.plan).read_text()), args.run_dir)
        def interrupted(_signum, _frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            result = dispatcher.run()
        finally:
            signal.signal(signal.SIGTERM, previous)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["candidate_ready"] else 2
    except KeyboardInterrupt:
        if dispatcher:
            dispatcher.cancel("operator interrupted managed dispatcher")
        return 130
    except (DispatchError, OSError, ValueError) as exc:
        print(f"managed dispatch refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
