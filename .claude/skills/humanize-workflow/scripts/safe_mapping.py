#!/usr/bin/env python3
"""Generate and verify the only Python flow shape trusted by this adapter."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

CONFIG_SYNC_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "update-platform-config"
    / "scripts"
    / "config-sync"
)
if str(CONFIG_SYNC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CONFIG_SYNC_SCRIPTS))

from validate_model_roles import ValidationError as ModelRoleValidationError
from validate_model_roles import validate as validate_model_roles

MAPPING_SCHEMA = "humanize-workflow-mapping/v1"
SPEC_SCHEMA = "humanize-workflow-spec/v1"
REASONING_POSTURES = ("economy", "balanced", "frontier")
MAPPING_KEYS = (
    "schema",
    "adapterVersion",
    "upstream",
    "designPath",
    "designSha256",
    "specPath",
    "specSha256",
    "artifact",
    "artifactSha256",
    "inputPath",
    "reasoningPosture",
    "nodeAgents",
    "terminalProtocol",
)
UPSTREAM_REPOSITORY = "https://github.com/humanfia/humanize2.git"
UPSTREAM_REVISION = "8fbb2b718d74b7ef1d3d0650a9768415ec489f26"
HMZ_VERSION = "hmz 0.1.0"
HMZ_DISTRIBUTION_VERSION = "0.1.0"
HMZ_ENTRY_POINT = "console_scripts:hmz=hmz.cli:main"
HMZ_LAUNCH_CODE = (
    "import sys; source=sys.argv.pop(1); packages=sys.argv.pop(1); "
    "sys.path[:0]=[source,packages]; "
    "from hmz.cli import main; raise SystemExit(main())"
)
ADAPTER_VERSION = 1
NEUTRAL_VALIDATOR = Path(
    ".claude/skills/build-workflow/scripts/validate_design.py"
)
IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
HUMAN_REQUEST_KINDS = {
    "authorization",
    "decision",
    "credential",
    "external-action",
}
TYPE_MAP = {
    "string": "str",
    "number": "FiniteFloat",
    "boolean": "bool",
    "array": "list[object]",
    "object": "dict[str, object]",
}
RESERVED_NATIVE_FLOW_IDS = {"default", "slot"}
_SYSTEM_GIT_DIRS = (
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
    Path("/usr/bin"),
    Path("/bin"),
    Path("/snap/bin"),
)
_SYSTEM_HELPER_PATH = ("/usr/local/bin", "/usr/bin", "/bin")
_GIT_SAFETY_OPTIONS = (
    "--no-pager",
    "--no-optional-locks",
    "--no-replace-objects",
    "-c",
    "core.bare=false",
    "-c",
    "core.fileMode=true",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.ignoreCase=false",
    "-c",
    "core.symlinks=true",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "diff.external=",
    "-c",
    "diff.renames=false",
    "-c",
    "protocol.ext.allow=never",
)
_TRUSTED_GIT: tuple[Path, str] | None = None
_EXECUTABLE_STABLE_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


class MappingError(ValueError):
    """A neutral design cannot be represented by the safe v1 mapping."""


def canonical_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MappingError(f"{path}: root must be an object")
    return value


def _require_reasoning_posture(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{label} must be a non-empty string")
    if value not in REASONING_POSTURES:
        raise MappingError(f"{label} {value!r} is not an admitted posture")
    return value


def canonical_reasoning_posture(
    root: Path, spec: dict[str, Any]
) -> tuple[str, set[str]]:
    """Read the current canonical default; a spec can only bind that value."""
    raw = _read_regular_nofollow(
        root / "config/workflows/models.json", "canonical model role policy"
    )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MappingError(
            f"canonical model role policy is not valid UTF-8 JSON: {error}"
        ) from error
    if not isinstance(document, dict):
        raise MappingError("canonical model role policy root must be an object")
    try:
        validate_model_roles(document)
    except ModelRoleValidationError as error:
        raise MappingError(str(error)) from error
    reasoning = document.get("reasoningPolicy")
    postures = reasoning.get("postures") if isinstance(reasoning, dict) else None
    default = (
        reasoning.get("defaultPosture") if isinstance(reasoning, dict) else None
    )
    posture = _require_reasoning_posture(
        default, "canonical policy defaultPosture"
    )
    if not isinstance(postures, dict) or not isinstance(postures.get(posture), dict):
        raise MappingError(
            f"canonical policy defaultPosture {posture!r} is not declared"
        )
    allowed = postures[posture].get("allowedRoles")
    if not isinstance(allowed, list) or not all(
        isinstance(role, str) and role for role in allowed
    ):
        raise MappingError(
            f"canonical policy posture {posture!r} has malformed allowedRoles"
        )
    if "reasoningPosture" in spec:
        bound = _require_reasoning_posture(
            spec["reasoningPosture"], "spec.reasoningPosture"
        )
        if bound != posture:
            raise MappingError(
                "spec reasoningPosture differs from canonical policy defaultPosture"
            )
    return posture, set(allowed)


def ensure_posture_allows_roles(
    posture: str, allowed_roles: set[str], model_nodes: list[dict[str, Any]]
) -> None:
    denied = sorted(
        {
            str(node["role"])
            for node in model_nodes
            if str(node["role"]) not in allowed_roles
        }
    )
    if denied:
        raise MappingError(
            f"canonical policy defaultPosture {posture!r} does not admit "
            f"semantic roles {denied!r}"
        )


def project_path(root: Path, value: str, label: str, *, must_exist: bool = True) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise MappingError(f"{label} must be a project-relative path without '..'")
    candidate = root / path
    resolved_root = root.resolve()
    resolved = candidate.resolve(strict=must_exist)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise MappingError(f"{label} escapes the project root")
    if must_exist and candidate.is_symlink():
        raise MappingError(f"{label} must not be a symlink")
    return candidate


def _state_fields(design: dict[str, Any]) -> dict[str, dict[str, Any]]:
    state = design.get("state")
    if not isinstance(state, list):
        raise MappingError("design.state must be a list")
    result: dict[str, dict[str, Any]] = {}
    for field in state:
        if not isinstance(field, dict):
            raise MappingError("every state field must be an object")
        name = field.get("name")
        kind = field.get("type")
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            raise MappingError(f"state field is not a safe Python identifier: {name!r}")
        if name.startswith("humanizeAdapter") or name in {"verdict", "model_config"}:
            raise MappingError(f"state field is reserved by the adapter: {name}")
        if kind not in TYPE_MAP:
            raise MappingError(f"state field {name} has unsupported type: {kind!r}")
        result[name] = field
    return result


def _matches_state_type(kind: str, value: object) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
        )
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return False


def validate_compatible(design: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    if design.get("schema") != "workflow-design/v1":
        raise MappingError("design.schema must be workflow-design/v1")
    design_id = design.get("id")
    if not isinstance(design_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", design_id):
        raise MappingError("design.id must be lower kebab-case")
    if design_id in RESERVED_NATIVE_FLOW_IDS:
        raise MappingError(
            f"design.id {design_id!r} is reserved for the trusted native Humanize root/slot namespace"
        )
    governance = design.get("governance")
    if not isinstance(governance, dict):
        raise MappingError("design.governance must be an object")
    if governance.get("reviewPolicy") != "independent":
        raise MappingError("humanize v1 requires governance.reviewPolicy independent")
    if governance.get("completionProof") != "none":
        raise MappingError("humanize v1 requires governance.completionProof none")
    if spec.get("schema") != SPEC_SCHEMA:
        raise MappingError(f"spec.schema must be {SPEC_SCHEMA}")
    input_path = spec.get("inputPath")
    if not isinstance(input_path, str) or not input_path:
        raise MappingError("spec.inputPath must be a project-relative JSON path")
    if Path(input_path).is_absolute() or ".." in Path(input_path).parts:
        raise MappingError("spec.inputPath must be project-relative without '..'")

    fields = _state_fields(design)
    initial = spec.get("initialState", {})
    if not isinstance(initial, dict) or any(name not in fields for name in initial):
        raise MappingError("spec.initialState must contain only declared state fields")
    for name, value in initial.items():
        if not _matches_state_type(fields[name]["type"], value):
            raise MappingError(f"spec.initialState.{name} does not match its declared type")
    prompts = spec.get("prompts")
    if not isinstance(prompts, dict):
        raise MappingError("spec.prompts must be an object")

    nodes = design.get("nodes")
    transitions = design.get("transitions")
    if not isinstance(nodes, list) or not isinstance(transitions, list):
        raise MappingError("design nodes and transitions must be lists")
    node_by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise MappingError("every node must be an object with an id")
        node_id = node["id"]
        if node_id in node_by_id:
            raise MappingError(f"duplicate node id: {node_id}")
        if node.get("kind") not in {"script", "agent", "decision", "terminal"}:
            raise MappingError(f"{node_id}: humanize v1 does not support {node.get('kind')}")
        node_by_id[node_id] = node

    entry = design.get("entry")
    if entry not in node_by_id or node_by_id[entry].get("kind") != "script":
        raise MappingError("humanize v1 requires one script entry seed")
    for node_id, node in node_by_id.items():
        kind = node.get("kind")
        if kind == "script" and node_id != entry:
            raise MappingError(f"{node_id}: only the entry seed script is supported")
        reads = node.get("reads", [])
        writes = node.get("writes", [])
        if not isinstance(reads, list) or not isinstance(writes, list):
            raise MappingError(f"{node_id}: reads and writes must be lists")
        for name in [*reads, *writes]:
            if name not in fields:
                raise MappingError(f"{node_id}: unknown state field {name!r}")
        if kind in {"agent", "decision"}:
            if node.get("session") != "fresh":
                raise MappingError(f"{node_id}: humanize v1 requires session fresh")
            skills = node.get("skills", {})
            if skills and (
                not isinstance(skills, dict)
                or skills.get("required", [])
                or skills.get("onDemand", [])
            ):
                raise MappingError(f"{node_id}: node skill loading is unsupported")
            if not isinstance(node.get("role"), str) or not node["role"]:
                raise MappingError(f"{node_id}: model nodes require a semantic role")
            timeout = node.get("timeoutSeconds")
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise MappingError(f"{node_id}: model nodes require a positive finite timeoutSeconds")
            if not isinstance(prompts.get(node_id), str) or not prompts[node_id].strip():
                raise MappingError(f"spec.prompts.{node_id} must be a non-empty string")
        if kind == "decision":
            outcomes = node.get("outcomes")
            if not isinstance(outcomes, list) or not outcomes or not all(
                isinstance(outcome, str) and outcome for outcome in outcomes
            ):
                raise MappingError(f"{node_id}: decision outcomes must be non-empty strings")
            if node.get("onMissing") not in {"unresolved", "failure"}:
                raise MappingError(f"{node_id}: decision onMissing must fail closed")
        if node.get("waitFor", []):
            raise MappingError(f"{node_id}: waitFor joins are unsupported")

    input_fields = {
        name for name, field in fields.items() if field.get("source") == "input"
    }
    entry_node = node_by_id[entry]
    if entry_node.get("reads", []) or set(entry_node.get("writes", [])) != input_fields:
        raise MappingError("entry seed must read nothing and write exactly the input state fields")

    wall_time = design.get("budgets", {}).get("maxWallTimeSeconds")
    if wall_time is not None:
        raise MappingError("humanize v1 does not safely enforce maxWallTimeSeconds")
    if design.get("sideEffects", []):
        raise MappingError("humanize v1 does not support declared replay-safe side effects")

    extra_prompts = sorted(set(prompts) - {
        node_id for node_id, node in node_by_id.items()
        if node.get("kind") in {"agent", "decision"}
    })
    if extra_prompts:
        raise MappingError(f"spec.prompts contains unknown model nodes: {extra_prompts}")

    outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in node_by_id}
    incoming: dict[str, int] = {node_id: 0 for node_id in node_by_id}
    for edge in transitions:
        if not isinstance(edge, dict) or edge.get("from") not in node_by_id or edge.get("to") not in node_by_id:
            raise MappingError(f"invalid transition: {edge!r}")
        outgoing[edge["from"]].append(edge)
        incoming[edge["to"]] += 1

    for node_id, node in node_by_id.items():
        edges = outgoing[node_id]
        kind = node.get("kind")
        if kind == "terminal":
            if edges:
                raise MappingError(f"{node_id}: terminal must not have outgoing transitions")
            continue
        if kind in {"script", "agent"}:
            if len(edges) != 1 or "when" in edges[0]:
                raise MappingError(f"{node_id}: requires exactly one unconditional successor")
            continue
        outcomes = set(node["outcomes"])
        routed: set[str] = set()
        for edge in edges:
            when = edge.get("when")
            if (
                not isinstance(when, dict)
                or set(when) != {"field", "equals"}
                or when.get("field") != f"{node_id}.verdict"
            ):
                raise MappingError(f"{node_id}: routes must test {node_id}.verdict")
            outcome = when.get("equals")
            if outcome not in outcomes or outcome in routed:
                raise MappingError(f"{node_id}: duplicate or unknown verdict route {outcome!r}")
            routed.add(outcome)
        if routed != outcomes:
            raise MappingError(f"{node_id}: every decision outcome needs one exact route")

    # Kahn's algorithm proves that the executable graph is finite and acyclic.
    queue = sorted(node_id for node_id, count in incoming.items() if count == 0)
    visited: list[str] = []
    while queue:
        node_id = queue.pop(0)
        visited.append(node_id)
        for edge in outgoing[node_id]:
            successor = edge["to"]
            incoming[successor] -= 1
            if incoming[successor] == 0:
                queue.append(successor)
                queue.sort()
    if len(visited) != len(node_by_id):
        raise MappingError("humanize v1 rejects cycles")

    reachable: set[str] = set()
    pending = [entry]
    while pending:
        node_id = pending.pop()
        if node_id in reachable:
            continue
        reachable.add(node_id)
        pending.extend(edge["to"] for edge in outgoing[node_id])
    if reachable != set(node_by_id):
        raise MappingError(f"unreachable nodes: {sorted(set(node_by_id) - reachable)}")

    request_kind_field = spec.get("humanRequestKindField")
    request_field = spec.get("humanRequestField")
    if (request_kind_field is None) != (request_field is None):
        raise MappingError("human request kind and request fields must be declared together")
    if request_kind_field is not None:
        if request_kind_field not in fields or fields[request_kind_field]["type"] != "string":
            raise MappingError("humanRequestKindField must name a declared string field")
        if request_field not in fields or fields[request_field]["type"] != "string":
            raise MappingError("humanRequestField must name a declared string field")

    model_nodes = [
        node for node in nodes if node.get("kind") in {"agent", "decision"}
    ]
    max_calls = design.get("budgets", {}).get("maxModelCalls")
    if not isinstance(max_calls, int) or max_calls < len(model_nodes):
        raise MappingError("maxModelCalls must cover every v1 model node exactly once")
    return {
        "id": design_id,
        "entry": entry,
        "fields": fields,
        "nodes": nodes,
        "nodeById": node_by_id,
        "outgoing": outgoing,
        "modelNodes": model_nodes,
        "inputFields": sorted(input_fields),
        "initialState": initial,
        "prompts": prompts,
        "requestKindField": request_kind_field,
        "requestField": request_field,
    }


def _class_name(node_id: str) -> str:
    return "Output" + "".join(part.capitalize() for part in node_id.split("-"))


def _quoted(value: object) -> str:
    return repr(value)


def generate_source(design: dict[str, Any], spec: dict[str, Any]) -> str:
    model = validate_compatible(design, spec)
    fields = model["fields"]
    model_nodes = model["modelNodes"]
    agent_types = ", ".join("Agent" for _ in model_nodes) or "()"
    prompts: dict[str, str] = {}
    objective = design["goal"]["objective"]
    acceptance = design["goal"]["acceptance"]
    for node in model_nodes:
        node_id = node["id"]
        prompts[node_id] = (
            f"{model['prompts'][node_id].strip()}\n\n"
            f"Workflow objective: {objective}\nAcceptance: {acceptance}\n"
            f"Node: {node_id}. Return only the schema requested by the runtime."
        )

    lines = [
        '"""Adapter-generated humanize2 flow. Do not edit; validate exact bytes before use."""',
        "",
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import json",
        "from pathlib import Path",
        "from typing import Literal",
        "",
        "from hmz.flows import Agent, flow",
        "from pydantic import BaseModel, ConfigDict, FiniteFloat",
        "",
        f"DESIGN_ID = {_quoted(model['id'])}",
        f"ENTRY = {_quoted(model['entry'])}",
        f"INPUT_FIELDS = {_quoted(model['inputFields'])}",
        f"INITIAL_STATE = {_quoted(model['initialState'])}",
        f"PROMPTS = {_quoted(prompts)}",
        f"HUMAN_REQUEST_KINDS = {_quoted(sorted(HUMAN_REQUEST_KINDS))}",
        f"HUMAN_REQUEST_KIND_FIELD = {_quoted(model['requestKindField'])}",
        f"HUMAN_REQUEST_FIELD = {_quoted(model['requestField'])}",
        f"MAX_STEPS = {len(model['nodes'])}",
        "",
    ]
    lines.extend(
        [
            "class InputState(BaseModel):",
            '    model_config = ConfigDict(extra="forbid", strict=True)',
        ]
    )
    if model["inputFields"]:
        for name in model["inputFields"]:
            lines.append(f"    {name}: {TYPE_MAP[fields[name]['type']]}")
    else:
        lines.append("    pass")
    lines.append("")
    for node in model_nodes:
        lines.extend([f"class {_class_name(node['id'])}(BaseModel):", '    model_config = ConfigDict(extra="forbid", strict=True)'])
        if node.get("kind") == "decision":
            literal = ", ".join(repr(outcome) for outcome in node["outcomes"])
            lines.append(f"    verdict: Literal[{literal}]")
        writes = node.get("writes", [])
        if not writes and node.get("kind") != "decision":
            lines.append("    summary: str")
        for name in writes:
            lines.append(f"    {name}: {TYPE_MAP[fields[name]['type']]}")
        lines.append("")

    lines.extend(
        [
            "def _finish(payload: dict[str, object], state: dict[str, object], terminal: str, model_calls: int, error: str = \"\") -> None:",
            '    path = Path(str(payload["humanizeAdapterResultPath"])).resolve()',
            '    workspace = Path.cwd().resolve()',
            '    expected = workspace / "workflow-output" / "humanize" / DESIGN_ID',
            "    allowed = expected.resolve()",
            "    if allowed != expected:",
            '        raise RuntimeError("adapter output root contains a symlink")',
            "    if allowed not in path.parents:",
            '        raise RuntimeError("adapter result path escapes the design output root")',
            "    path.parent.mkdir(parents=True, exist_ok=True)",
            "    kind = \"\"",
            "    request = \"\"",
            '    if terminal == "HUMAN_BLOCKED":',
            "        kind = str(state.get(HUMAN_REQUEST_KIND_FIELD, \"\"))",
            "        request = str(state.get(HUMAN_REQUEST_FIELD, \"\"))",
            "        if kind not in HUMAN_REQUEST_KINDS or not request.strip():",
            '            terminal = "REVISE"',
            "            kind = \"\"",
            "            request = \"\"",
            "    result = {",
            '        "designId": DESIGN_ID,',
            '        "harness": "humanize",',
            '        "terminal": terminal,',
            '        "humanRequestKind": kind,',
            '        "humanRequest": request,',
            '        "state": state,',
            '        "error": error,',
            '        "metrics": {"modelCalls": model_calls, "inputTokens": None, "outputTokens": None},',
            '        "evidence": [str(path)],',
            '        "nextAction": "dispatch the graph-external independent terminal reviewer" if terminal == "COMPLETE" else request,',
            "    }",
            '    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\\n", encoding="utf-8")',
            '    print(f"TERMINAL: {terminal}")',
            '    if terminal == "HUMAN_BLOCKED":',
            '        print(f"HUMAN-REQUEST-KIND: {kind}")',
            '        print(f"HUMAN-REQUEST: {request}")',
            "",
            "@flow",
            f"async def run(agents: tuple[{agent_types}], task: str) -> None:",
            "    payload = json.loads(task)",
            "    if not isinstance(payload, dict):",
            '        raise ValueError("adapter task payload must be a JSON object")',
            "    state = INITIAL_STATE.copy()",
            "    for field in INPUT_FIELDS:",
            "        if field not in payload:",
            '            _finish(payload, state, "REVISE", 0, f"missing input field: {field}")',
            "            return",
            "    try:",
            "        input_state = InputState.model_validate({field: payload[field] for field in INPUT_FIELDS})",
            "    except Exception as error:",
            '        _finish(payload, state, "REVISE", 0, str(error))',
            "        return",
            "    state.update(input_state.model_dump())",
            "    current = ENTRY",
            "    verdicts: dict[str, str] = {}",
            "    model_calls = 0",
            "    steps = 0",
            "    try:",
            "        while steps < MAX_STEPS:",
            "            steps += 1",
        ]
    )

    model_index = {node["id"]: index for index, node in enumerate(model_nodes)}
    for at, node in enumerate(model["nodes"]):
        node_id = node["id"]
        prefix = "if" if at == 0 else "elif"
        lines.append(f"            {prefix} current == {_quoted(node_id)}:")
        kind = node["kind"]
        if kind == "terminal":
            outcome = node.get("outcome")
            terminal = "COMPLETE" if outcome == "success" else "HUMAN_BLOCKED" if outcome == "unresolved" else "REVISE"
            lines.extend([f'                _finish(payload, state, "{terminal}", model_calls)', "                return"])
        elif kind == "script":
            lines.append(f"                current = {_quoted(model['outgoing'][node_id][0]['to'])}")
        else:
            reads = node.get("reads", [])
            lines.extend(
                [
                    "                model_calls += 1",
                    f"                node_state = {{name: state[name] for name in {_quoted(reads)}}}",
                    f"                prompt = PROMPTS[{_quoted(node_id)}] + \"\\n\\nINPUT STATE:\\n\" + json.dumps(node_state, sort_keys=True)",
                    "                try:",
                    f"                    answered = await asyncio.wait_for(agents[{model_index[node_id]}].aturn(prompt, schema={_class_name(node_id)}), timeout={node['timeoutSeconds']!r})",
                    "                except TimeoutError:",
                    f"                    agents[{model_index[node_id]}].stop()",
                    f'                    raise RuntimeError("{node_id}: timeoutSeconds exceeded")',
                    "                if answered is None:",
                    f'                    raise RuntimeError("{node_id}: missing structured output")',
                    "                written = answered.model_dump()",
                ]
            )
            if kind == "decision":
                lines.extend([f"                verdicts[{_quoted(node_id)}] = str(written.pop(\"verdict\"))", "                state.update(written)"])
                for edge_at, edge in enumerate(model["outgoing"][node_id]):
                    branch = "if" if edge_at == 0 else "elif"
                    lines.extend([f"                {branch} verdicts[{_quoted(node_id)}] == {_quoted(edge['when']['equals'])}:", f"                    current = {_quoted(edge['to'])}"])
                lines.extend(["                else:", f'                    raise RuntimeError("{node_id}: unmapped verdict")'])
            else:
                writes = node.get("writes", [])
                if not writes:
                    lines.append('                written.pop("summary")')
                lines.extend(["                state.update(written)", f"                current = {_quoted(model['outgoing'][node_id][0]['to'])}"])
    lines.extend(
        [
            "            else:",
            '                raise RuntimeError(f"unknown node: {current}")',
            '        raise RuntimeError("finite graph exceeded its generated step bound")',
            "    except Exception as error:",
            '        _finish(payload, state, "REVISE", model_calls, str(error))',
            "",
        ]
    )
    source = "\n".join(lines)
    audit_generated_ast(source)
    return source


def _call_name(node: ast.Call) -> str:
    value: ast.expr = node.func
    parts: list[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def audit_generated_ast(source: str) -> None:
    """Reject imports and calls outside the exact generated runtime surface."""
    tree = ast.parse(source)
    allowed_from = {
        "__future__": {"annotations"},
        "pathlib": {"Path"},
        "typing": {"Literal"},
        "hmz.flows": {"Agent", "flow"},
        "pydantic": {"BaseModel", "ConfigDict", "FiniteFloat"},
    }
    allowed_calls = {
        "ConfigDict",
        "Path",
        "Path.cwd",
        "RuntimeError",
        "ValueError",
        "_finish",
        "asyncio.wait_for",
        "INITIAL_STATE.copy",
        "InputState.model_validate",
        "answered.model_dump",
        "aturn",
        "expected.resolve",
        "input_state.model_dump",
        "isinstance",
        "json.dumps",
        "json.loads",
        "path.parent.mkdir",
        "path.write_text",
        "print",
        "request.strip",
        "resolve",
        "state.get",
        "state.update",
        "stop",
        "str",
        "written.pop",
    }
    banned_nodes = (ast.Delete, ast.Global, ast.Nonlocal, ast.Lambda, ast.TryStar, ast.With)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if [(alias.name, alias.asname) for alias in node.names] not in [
                [("asyncio", None)],
                [("json", None)],
            ]:
                raise MappingError("generated flow contains a non-whitelisted import")
        elif isinstance(node, ast.ImportFrom):
            names = {alias.name for alias in node.names}
            if node.module not in allowed_from or names - allowed_from[node.module]:
                raise MappingError("generated flow contains a non-whitelisted from-import")
        elif isinstance(node, banned_nodes):
            raise MappingError(f"generated flow contains forbidden AST node {type(node).__name__}")
        elif isinstance(node, ast.Call):
            called = _call_name(node)
            if called not in allowed_calls:
                raise MappingError(f"generated flow contains non-whitelisted call: {called}")


def build_manifest(root: Path, design_path: Path, spec_path: Path, artifact_path: Path) -> dict[str, Any]:
    design = load_object(design_path)
    spec = load_object(spec_path)
    model = validate_compatible(design, spec)
    posture, allowed_roles = canonical_reasoning_posture(root, spec)
    ensure_posture_allows_roles(posture, allowed_roles, model["modelNodes"])
    return {
        "schema": MAPPING_SCHEMA,
        "adapterVersion": ADAPTER_VERSION,
        "upstream": {"repository": UPSTREAM_REPOSITORY, "revision": UPSTREAM_REVISION, "distribution": "hmz==0.1.0"},
        "designPath": design_path.relative_to(root).as_posix(),
        "designSha256": sha256_file(design_path),
        "specPath": spec_path.relative_to(root).as_posix(),
        "specSha256": sha256_file(spec_path),
        "artifact": artifact_path.relative_to(root).as_posix(),
        "artifactSha256": sha256_file(artifact_path),
        "inputPath": spec["inputPath"],
        "reasoningPosture": posture,
        "nodeAgents": [
            {"node": node["id"], "role": node["role"], "workspaceAccess": node.get("workspaceAccess", "read")}
            for node in model["modelNodes"]
        ],
        "terminalProtocol": {
            "field": "TERMINAL",
            "values": ["COMPLETE", "HUMAN_BLOCKED", "REVISE"],
            "humanRequestKinds": sorted(HUMAN_REQUEST_KINDS),
            "independentReviewRequired": True,
        },
    }


def validate_manifest(root: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = load_object(manifest_path)
    if manifest.get("schema") != MAPPING_SCHEMA or manifest.get("adapterVersion") != ADAPTER_VERSION:
        raise MappingError("unsupported humanize mapping schema/version")
    unknown = sorted(set(manifest) - set(MAPPING_KEYS))
    if unknown:
        raise MappingError(f"mapping has unsupported keys {unknown}")
    missing = sorted(set(MAPPING_KEYS) - set(manifest))
    if missing:
        raise MappingError(f"mapping is missing required keys {missing}")
    upstream = manifest.get("upstream")
    if not isinstance(upstream, dict) or upstream.get("repository") != UPSTREAM_REPOSITORY or upstream.get("revision") != UPSTREAM_REVISION:
        raise MappingError("mapping is not bound to the audited humanize2 revision")
    design_path = project_path(root, manifest.get("designPath", ""), "designPath")
    spec_path = project_path(root, manifest.get("specPath", ""), "specPath")
    artifact_path = project_path(root, manifest.get("artifact", ""), "artifact")
    design = load_object(design_path)
    design_id = design.get("id")
    if not isinstance(design_id, str):
        raise MappingError("mapping design has no string id")
    expected_artifact = root / ".humanize" / "generated-flows" / f"{design_id}.py"
    if artifact_path != expected_artifact:
        raise MappingError("adapter artifact must use the isolated .humanize/generated-flows namespace")
    for label, path in (("design", design_path), ("spec", spec_path), ("artifact", artifact_path)):
        expected = manifest.get(f"{label}Sha256")
        if expected != sha256_file(path):
            raise MappingError(f"{label} digest mismatch")
    spec = load_object(spec_path)
    expected_source = generate_source(design, spec)
    actual_source = artifact_path.read_text(encoding="utf-8")
    if actual_source != expected_source:
        raise MappingError("artifact bytes differ from deterministic adapter generation")
    audit_generated_ast(actual_source)
    model = validate_compatible(design, spec)
    expected_agents = [
        {"node": node["id"], "role": node["role"], "workspaceAccess": node.get("workspaceAccess", "read")}
        for node in model["modelNodes"]
    ]
    if manifest.get("nodeAgents") != expected_agents or manifest.get("inputPath") != spec["inputPath"]:
        raise MappingError("manifest execution metadata differs from design/spec")
    posture = _require_reasoning_posture(
        manifest.get("reasoningPosture"), "mapping reasoningPosture"
    )
    canonical_posture, allowed_roles = canonical_reasoning_posture(root, spec)
    if posture != canonical_posture:
        raise MappingError(
            "mapping reasoningPosture differs from canonical policy defaultPosture"
        )
    ensure_posture_allows_roles(posture, allowed_roles, model["modelNodes"])
    return manifest


def _subprocess_detail(completed: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if completed.stdout.strip():
        parts.append(f"stdout={completed.stdout.strip()!r}")
    if completed.stderr.strip():
        parts.append(f"stderr={completed.stderr.strip()!r}")
    detail = "; ".join(parts) or "no output"
    return detail[:4000]


def validate_full_mapping(root: Path, manifest_path: Path) -> dict[str, Any]:
    """Validate the manifest, generated flow, adapter capabilities, and neutral design."""
    root = root.resolve()
    try:
        resolved_manifest = manifest_path.resolve(strict=True)
    except OSError as error:
        raise MappingError(f"mapping manifest is unavailable: {error}") from error
    if root != resolved_manifest and root not in resolved_manifest.parents:
        raise MappingError("manifest must be inside the project root")
    if manifest_path.is_symlink():
        raise MappingError("manifest must not be a symlink")

    manifest = validate_manifest(root, resolved_manifest)
    design_path = project_path(root, manifest["designPath"], "designPath")
    validator = root / NEUTRAL_VALIDATOR
    try:
        validator_status = validator.stat()
        if validator.is_symlink() or not stat.S_ISREG(validator_status.st_mode):
            raise MappingError(
                f"neutral design validator must be a regular project file: {validator}"
            )
        if not validator_status.st_mode & 0o444:
            raise MappingError(f"neutral design validator is unreadable: {validator}")
        validator.read_bytes()
    except FileNotFoundError as error:
        raise MappingError(f"neutral design validator is missing: {validator}") from error
    except PermissionError as error:
        raise MappingError(f"neutral design validator is unreadable: {validator}") from error
    except OSError as error:
        raise MappingError(f"neutral design validator is unavailable: {error}") from error

    try:
        completed = subprocess.run(
            [sys.executable, str(validator), str(design_path)],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MappingError(
            f"neutral design validator could not execute via Python: {error}"
        ) from error
    if completed.returncode != 0:
        raise MappingError(
            "neutral design validation failed "
            f"(rc={completed.returncode}): {_subprocess_detail(completed)}"
        )
    if completed.stdout != "DESIGN: clean\n" or completed.stderr:
        raise MappingError(
            "neutral design validator returned a malformed result: "
            f"{_subprocess_detail(completed)}"
        )
    return manifest


def _read_regular_nofollow(path: Path, label: str) -> bytes:
    """Read a regular file through no-follow descriptors for every named component."""
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise MappingError(f"{label} requires Linux no-follow descriptor support")
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    parts = list(expanded.parts)[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise MappingError(f"{label} has an unsafe path")
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    leaf_flags = os.O_RDONLY | os.O_NOFOLLOW | close_on_exec
    directory_fd: int | None = None
    leaf_fd: int | None = None
    try:
        directory_fd = os.open(expanded.anchor, directory_flags)
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            previous_fd = directory_fd
            directory_fd = next_fd
            os.close(previous_fd)
        leaf_fd = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
        status = os.fstat(leaf_fd)
        if not stat.S_ISREG(status.st_mode):
            raise MappingError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(leaf_fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except MappingError:
        raise
    except OSError as error:
        raise MappingError(f"{label} cannot be read without following links: {error}") from error
    finally:
        try:
            if leaf_fd is not None:
                os.close(leaf_fd)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)


def _console_script(interpreter: Path) -> bytes:
    return (
        f"#!{interpreter}\n"
        "# -*- coding: utf-8 -*-\n"
        "import re\n"
        "import sys\n"
        "from hmz.cli import main\n"
        "if __name__ == '__main__':\n"
        "    sys.argv[0] = re.sub(r'(-script\\.pyw|\\.exe)?$', '', sys.argv[0])\n"
        "    sys.exit(main())\n"
    ).encode("utf-8")


def _uv_console_script(interpreter: Path) -> bytes:
    """Return the launcher emitted by the current uv/uv_build installer."""
    return (
        f"#!{interpreter}\n"
        "import sys\n"
        "from hmz.cli import main\n"
        "if __name__ == '__main__':\n"
        "    sys.argv[0] = sys.argv[0].removesuffix('.exe')\n"
        "    sys.exit(main())\n"
    ).encode("utf-8")


def _console_scripts(interpreter: Path) -> tuple[bytes, ...]:
    """Return the two exact entry-point forms used by audited installers."""
    return (_console_script(interpreter), _uv_console_script(interpreter))


def _probe_code() -> str:
    return (
        "import importlib.metadata as m,json,sys;"
        "source=sys.argv.pop(1);packages=sys.argv.pop(1);sys.path[:0]=[source,packages];"
        "import hmz,hmz.cli;d=m.distribution('hmz');"
        "print(json.dumps({'executable':sys.executable,'python':list(sys.version_info[:2]),"
        "'hmz':hmz.__file__,'cli':hmz.cli.__file__,'dist':str(d._path),"
        "'version':d.version,'directUrl':json.loads(d.read_text('direct_url.json')),"
        "'entryPoints':sorted(f'{e.group}:{e.name}={e.value}' for e in d.entry_points)}))"
    )


def _bind_native_executable_bytes(path: Path, label: str) -> tuple[Path, str]:
    """Bind one executable as an absolute regular file plus SHA-256 identity.

    Discovery may follow a trusted-directory symlink once to locate the file. The bound
    object itself is opened with O_NOFOLLOW, must stay a regular executable with
    unambiguous permissions, and is identified by path plus digest. A path-only resolve()
    result is not a binding.
    """
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_absolute():
            raise MappingError(f"{label} must resolve to an absolute path")
        leaf = os.lstat(resolved)
        if stat.S_ISLNK(leaf.st_mode):
            raise MappingError(f"{label} must not be a symlink")
        if not stat.S_ISREG(leaf.st_mode):
            raise MappingError(f"{label} must be a regular file")
        if not leaf.st_mode & 0o111:
            raise MappingError(f"{label} must be executable")
        if leaf.st_mode & 0o022:
            raise MappingError(f"{label} must not be group- or world-writable")
        if os.access(resolved, os.W_OK):
            raise MappingError(f"{label} must not be user-writable")
        if not os.access(resolved, os.X_OK):
            raise MappingError(f"{label} must be executable")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(resolved, flags)
        try:
            before = os.fstat(descriptor)
            if (
                (before.st_dev, before.st_ino) != (leaf.st_dev, leaf.st_ino)
                or not stat.S_ISREG(before.st_mode)
                or before.st_mode != leaf.st_mode
            ):
                raise MappingError(f"{label} changed while it was being opened")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except MappingError:
        raise
    except OSError as error:
        raise MappingError(f"cannot bind {label}: {path}: {error}") from error
    if any(
        getattr(before, field) != getattr(after, field)
        for field in _EXECUTABLE_STABLE_FIELDS
    ):
        raise MappingError(f"{label} changed while it was being bound")
    return resolved, f"sha256:{digest.hexdigest()}"


def _bind_trusted_git() -> tuple[Path, str]:
    """Bind git from fixed system directories, never from the caller PATH.

    The binding is remembered for the process and rechecked on every call. A swapped,
    rebuilt, or earlier-directory-shadowed git fails closed instead of inspecting a
    different checkout than the one already committed to.
    """
    global _TRUSTED_GIT
    for directory in _SYSTEM_GIT_DIRS:
        candidate = directory / "git"
        if not os.path.lexists(candidate):
            continue
        binding = _bind_native_executable_bytes(candidate, "trusted git executable")
        if _TRUSTED_GIT is not None and _TRUSTED_GIT != binding:
            raise MappingError(
                "trusted git executable drift: the bound git path or digest changed "
                "during pinned-source validation"
            )
        _TRUSTED_GIT = binding
        return binding
    locations = ", ".join(str(value) for value in _SYSTEM_GIT_DIRS)
    raise MappingError(
        f"no trusted system git executable is installed in: {locations}"
    )


def _git_environment() -> dict[str, str]:
    """Rebuild git's environment so no caller PATH or GIT_* selector survives."""
    return {
        "PATH": os.pathsep.join(_SYSTEM_HELPER_PATH),
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "LC_ALL": "C",
        "LANG": "C",
        "TZ": "UTC",
    }


def _run_pinned_source_git(
    source_root: Path, args: Sequence[str]
) -> subprocess.CompletedProcess[str]:
    git, _ = _bind_trusted_git()
    git_dir = source_root / ".git"
    try:
        return subprocess.run(
            [
                str(git),
                *_GIT_SAFETY_OPTIONS,
                f"--git-dir={git_dir}",
                f"--work-tree={source_root}",
                *args,
            ],
            cwd=str(source_root),
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
            env=_git_environment(),
            stdin=subprocess.DEVNULL,
        )
    finally:
        _bind_trusted_git()


def validate_hmz_binary(binary: Path) -> dict[str, Any]:
    """Bind an hmz executable to the exact audited source checkout and version."""
    candidate = binary.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    candidate = Path(os.path.abspath(candidate))
    wrapper = _read_regular_nofollow(candidate, "hmz console script")
    try:
        binary_status = candidate.stat(follow_symlinks=False)
    except OSError as error:
        raise MappingError(f"hmz console script is unavailable: {error}") from error
    if not stat.S_ISREG(binary_status.st_mode) or not binary_status.st_mode & 0o111:
        raise MappingError("hmz console script must be an executable regular file")
    if (
        candidate.name != "hmz"
        or candidate.parent.name != "bin"
        or candidate.parent.parent.name != ".venv"
    ):
        raise MappingError(
            "hmz binary must be the canonical <source>/.venv/bin/hmz executable"
        )
    source_root = candidate.parent.parent.parent
    interpreter = candidate.parent / "python"
    if wrapper not in _console_scripts(interpreter):
        raise MappingError("hmz console script does not match the canonical pinned entry point")
    try:
        interpreter_target = interpreter.resolve(strict=True)
        interpreter_status = interpreter_target.stat()
    except OSError as error:
        raise MappingError(f"hmz venv interpreter is unavailable: {error}") from error
    if (
        not stat.S_ISREG(interpreter_status.st_mode)
        or not interpreter_status.st_mode & 0o111
        or os.access(interpreter_target, os.W_OK)
    ):
        raise MappingError("hmz venv interpreter must resolve to a non-user-writable executable")
    try:
        top_level = _run_pinned_source_git(
            source_root, ("rev-parse", "--show-toplevel")
        )
        revision = _run_pinned_source_git(
            source_root, ("rev-parse", "--verify", "HEAD^{commit}")
        )
        origin = _run_pinned_source_git(
            source_root, ("remote", "get-url", "origin")
        )
        clean = _run_pinned_source_git(
            source_root,
            (
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignored=matching",
                "--",
                "src/hmz",
                "pyproject.toml",
            ),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MappingError(f"hmz source checkout could not be inspected: {error}") from error
    if (
        top_level.returncode != 0
        or Path(top_level.stdout.strip()).resolve() != source_root
        or revision.returncode != 0
        or revision.stdout.strip() != UPSTREAM_REVISION
        or origin.returncode != 0
        or origin.stdout.strip() != UPSTREAM_REPOSITORY
        or clean.returncode != 0
        or clean.stdout
        or clean.stderr
    ):
        raise MappingError(
            "hmz source checkout does not match the audited revision and repository "
            f"{UPSTREAM_REPOSITORY}@{UPSTREAM_REVISION}"
        )
    source = source_root / "src"
    package_roots = sorted((source_root / ".venv/lib").glob("python3.*/site-packages"))
    if len(package_roots) != 1:
        raise MappingError("hmz venv must have exactly one Python site-packages directory")
    packages = package_roots[0]
    probe_prefix = [
        str(interpreter_target),
        "-I",
        "-S",
        "-B",
        "-c",
        _probe_code(),
        str(source),
        str(packages),
    ]
    try:
        probe = subprocess.run(
            probe_prefix,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MappingError(f"hmz version check could not execute: {error}") from error
    if probe.returncode != 0 or probe.stderr:
        raise MappingError(
            "hmz installed provenance probe failed: " f"{_subprocess_detail(probe)}"
        )
    try:
        provenance = json.loads(probe.stdout)
    except json.JSONDecodeError as error:
        raise MappingError("hmz installed provenance probe returned malformed JSON") from error
    venv = source_root / ".venv"
    expected_hmz = source / "hmz/__init__.py"
    expected_cli = source / "hmz/cli/__init__.py"
    direct_url = provenance.get("directUrl") if isinstance(provenance, dict) else None
    entry_points = provenance.get("entryPoints") if isinstance(provenance, dict) else None
    try:
        probed_executable = Path(provenance["executable"]).resolve(strict=True)
        probed_hmz = Path(provenance["hmz"]).resolve(strict=True)
        probed_cli = Path(provenance["cli"]).resolve(strict=True)
        probed_dist = Path(provenance["dist"]).resolve(strict=True)
    except (KeyError, OSError, TypeError) as error:
        raise MappingError("hmz installed provenance paths are malformed") from error
    if (
        probed_executable != interpreter_target
        or provenance.get("python") != [3, 12]
        or probed_hmz != expected_hmz.resolve(strict=True)
        or probed_cli != expected_cli.resolve(strict=True)
        or venv.resolve(strict=True) not in probed_dist.parents
        or provenance.get("version") != HMZ_DISTRIBUTION_VERSION
        or direct_url != {"dir_info": {"editable": True}, "url": source_root.as_uri()}
        or entry_points != [HMZ_ENTRY_POINT]
    ):
        raise MappingError("hmz installed provenance is not bound to the pinned checkout")
    argv_prefix = [
        str(interpreter_target),
        "-I",
        "-S",
        "-B",
        "-c",
        HMZ_LAUNCH_CODE,
        str(source),
        str(packages),
    ]
    try:
        version = subprocess.run(
            [*argv_prefix, "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise MappingError(f"hmz trusted launcher version check failed: {error}") from error
    if version.returncode != 0 or version.stdout != f"{HMZ_VERSION}\n" or version.stderr:
        raise MappingError(
            "hmz trusted launcher must report exactly hmz 0.1.0: "
            f"{_subprocess_detail(version)}"
        )
    return {
        "argvPrefix": argv_prefix,
        "binary": str(candidate),
        "interpreter": str(interpreter_target),
        "venvInterpreter": str(interpreter),
        "repository": UPSTREAM_REPOSITORY,
        "revision": UPSTREAM_REVISION,
        "root": str(source_root),
        "version": HMZ_VERSION,
    }
