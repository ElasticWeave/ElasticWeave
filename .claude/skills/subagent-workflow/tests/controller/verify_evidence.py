#!/usr/bin/env python3
"""Offline, fail-closed verification of the final subagent scheduler candidate evidence."""

from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


EVIDENCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "evidence"
    / "historical-workflow-evidence"
    / "subagent-scheduler-smoke"
)
MANIFEST_PATH = EVIDENCE_ROOT / "manifest.json"

EXPECTED_CANDIDATE_SHA256 = {
    ".claude/skills/subagent-workflow/scripts/controller/run.ts": "8d3c9485754d259b816146a5eba53f763485d819c958c1287d46084afab57260",
    ".claude/skills/subagent-workflow/scripts/native/run.ts": "6c4d46f649b8cb1a37d7318a82e6a88c57b97e1df9a4999d780e727cadccb850",
    ".workflow/designs/subagent-scheduler-smoke.workflow.json": "199d1068ff317d46a4c5bf4cd82779637a003c226a60c4641a7b1a156b8378a2",
    ".workflow/designs/subagent-long-node-probe.workflow.json": "7fe7d5f42ad1c37114da22be65230945c191ada6fa1f33654e360c4a7bce250c",
    ".subagent/flows/subagent-scheduler-smoke.json": "0319df1acaa04ab7e6fb6533d4652380509a08563ddb77a378c74ad3c29439f5",
    ".subagent/flows/subagent-long-node-probe.json": "59f8da38faefe9c345128ff7d6a4f2c1108e8880dfbaefd341a0c4936d0601e7",
    "config/workflows/models.json": "7ebf40c6681d86f55683bc1710168539fe2db9021e0c11665c58822f32373f6a",
    ".claude/skills/subagent-workflow/scripts/native/native-map.json": "d27692fd2a315cb8379579a99981ef6eddce256c380a298eef04a79da714d83b",
    ".subagent/fixtures/subagent-scheduler-smoke.input.json": "253d6c67eb39066e59696f70c95e9fa5fc8a2efb10b728174336f60642b45ed8",
    ".subagent/fixtures/subagent-long-node-probe.input.json": "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356",
}

EXPECTED_SMOKE_FILES = {
    "events.jsonl",
    "native-calls.jsonl",
    "native-responses/scout-a.result.json",
    "native-responses/scout-a.stdout",
    "native-responses/scout-b.result.json",
    "native-responses/scout-b.stdout",
    "result.json",
    "scheduler-decisions.jsonl",
    "scheduler-responses/1.result.json",
    "scheduler-responses/1.stderr",
    "scheduler-responses/1.stdout",
    "state.json",
}

EXPECTED_OMITTED = {
    "native-responses/scout-a.stderr": {
        "bytes": 153265,
        "sha256": "2a1cde23e4d6dc57ed7bc4cdb73f0cc16c11d23bdc9735819743d009c1d88b67",
    },
    "native-responses/scout-b.stderr": {
        "bytes": 17462,
        "sha256": "b6e7b8bb30e67e7ee9eab8ae96de9ecc39e6d45577ab023eb8334891557b76e9",
    },
}

CREDENTIAL_PATTERNS = (
    ("bearer authorization", re.compile(rb"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("authorization field", re.compile(rb"(?i)\bAuthorization\s*[:=]")),
    ("API key field", re.compile(rb"(?i)\bapi[ _-]?key\s*[:=]\s*[\\\"']?[A-Za-z0-9._~+/=-]{8,}")),
    ("OpenAI-style key", re.compile(rb"\bsk-[A-Za-z0-9_-]{12,}")),
    ("GitHub token", re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("Slack token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{12,}")),
    ("generic token assignment", re.compile(rb"(?i)\btoken\s*[:=](?![=>])\s*[\\\"']?[A-Za-z0-9._~+/=-]{8,}")),
    ("token or password assignment", re.compile(rb"(?i)\b(?:access[_-]?token|refresh[_-]?token|password|passwd)\s*[:=]\s*[\\\"']?[^\s\\\"']{8,}")),
    ("private key", re.compile(rb"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")),
)

EXPECTED_LONG_FILES = {
    "command-responses/sleep-probe.result.json",
    "command-responses/sleep-probe.stderr",
    "command-responses/sleep-probe.stdout",
    "events.jsonl",
    "native-calls.jsonl",
    "result.json",
    "state.json",
}


class EvidenceError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"invalid JSON in {label}: {exc}") from exc


def load_jsonl(raw: bytes, label: str, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"invalid UTF-8 in {label}: {exc}") from exc
    if not text:
        require(allow_empty, f"empty JSONL evidence: {label}")
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        require(bool(line.strip()), f"blank JSONL row in {label}:{number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"invalid JSONL in {label}:{number}: {exc}") from exc
        require(isinstance(value, dict), f"non-object JSONL row in {label}:{number}")
        rows.append(value)
    return rows


def final_json_line(raw: bytes, label: str) -> dict[str, Any]:
    try:
        lines = [line for line in raw.decode("utf-8").splitlines() if line.strip()]
    except UnicodeDecodeError as exc:
        raise EvidenceError(f"invalid UTF-8 in {label}: {exc}") from exc
    require(bool(lines), f"empty native stdout: {label}")
    value = load_json(lines[-1].encode(), label)
    require(isinstance(value, dict), f"final output is not an object: {label}")
    return value


def iso_time(value: Any, label: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"invalid timestamp in {label}")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EvidenceError(f"invalid timestamp in {label}: {value}") from exc


def scan_credentials(raw: bytes, label: str) -> None:
    for name, pattern in CREDENTIAL_PATTERNS:
        require(pattern.search(raw) is None, f"credential pattern ({name}) found in payload: {label}")


def verify_omitted(manifest: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    entries = manifest.get("omittedSensitiveEvidence")
    require(isinstance(entries, list), "omittedSensitiveEvidence must be a list")
    omitted: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        require(isinstance(entry, dict), "malformed omitted-sensitive entry")
        category = entry.get("category")
        logical = entry.get("logicalPath")
        require(category == "authenticatedSmoke", "omitted evidence has unexpected category")
        require(logical in EXPECTED_OMITTED, f"unexpected omitted evidence: {logical}")
        require(entry.get("storage") == "externalDigest", f"omitted evidence is not externalDigest: {logical}")
        require("storedPath" not in entry and "encoding" not in entry, f"omitted evidence claims a stored payload: {logical}")
        require(entry.get("bytes") == EXPECTED_OMITTED[logical]["bytes"], f"omitted byte count mismatch: {logical}")
        require(entry.get("sha256") == EXPECTED_OMITTED[logical]["sha256"], f"omitted digest mismatch: {logical}")
        require(isinstance(entry.get("sourcePath"), str) and entry["sourcePath"].endswith(logical), f"omitted sourcePath mismatch: {logical}")
        require((category, logical) not in omitted, f"duplicate omitted evidence: {logical}")
        omitted[(category, logical)] = entry
    require({logical for _, logical in omitted} == set(EXPECTED_OMITTED), "omitted evidence set mismatch")
    return omitted


def decode_files(manifest: dict[str, Any]) -> dict[tuple[str, str], bytes]:
    entries = manifest.get("files")
    require(isinstance(entries, list) and entries, "manifest files must be a non-empty list")
    decoded: dict[tuple[str, str], bytes] = {}
    stored_paths: set[str] = set()
    for index, entry in enumerate(entries):
        require(isinstance(entry, dict), f"manifest file entry {index} is not an object")
        category = entry.get("category")
        logical = entry.get("logicalPath")
        stored = entry.get("storedPath")
        source = entry.get("sourcePath")
        require(category in {"authenticatedSmoke", "longProbe", "candidate"}, f"bad category in entry {index}")
        require(isinstance(logical, str) and logical, f"missing logicalPath in entry {index}")
        require(isinstance(stored, str) and stored, f"missing storedPath in entry {index}")
        require(isinstance(source, str) and source, f"missing sourcePath in entry {index}")
        require(source == logical or category != "candidate", f"candidate sourcePath mismatch: {logical}")
        require(entry.get("encoding") == "gzip+base64", f"unsupported encoding for {stored}")
        require(stored not in stored_paths, f"duplicate storedPath: {stored}")
        require((category, logical) not in decoded, f"duplicate evidence: {category}/{logical}")
        path = (EVIDENCE_ROOT / stored).resolve()
        try:
            path.relative_to(EVIDENCE_ROOT.resolve())
        except ValueError as exc:
            raise EvidenceError(f"storedPath escapes evidence root: {stored}") from exc
        require(path.is_file(), f"missing evidence payload: {stored}")
        try:
            compressed = base64.b64decode(b"".join(path.read_bytes().split()), validate=True)
            raw = gzip.decompress(compressed)
        except (binascii.Error, EOFError, gzip.BadGzipFile, ValueError) as exc:
            raise EvidenceError(f"invalid payload: {stored}") from exc
        require(len(raw) == entry.get("bytes"), f"byte count mismatch: {stored}")
        require(digest(raw) == entry.get("sha256"), f"SHA-256 mismatch: {stored}")
        scan_credentials(raw, f"{category}/{logical}")
        decoded[(category, logical)] = raw
        stored_paths.add(stored)
    payload_paths = {
        path.relative_to(EVIDENCE_ROOT).as_posix()
        for path in EVIDENCE_ROOT.rglob("*.gz.b64")
        if path.is_file()
    }
    require(payload_paths == stored_paths, f"manifest/payload set mismatch: {sorted(payload_paths ^ stored_paths)}")
    require(len(decoded) == 29, f"expected 29 stored payloads, found {len(decoded)}")
    return decoded


def category_set(files: dict[tuple[str, str], bytes], category: str) -> set[str]:
    return {logical for file_category, logical in files if file_category == category}


def verify_candidate(files: dict[tuple[str, str], bytes]) -> dict[str, Any]:
    actual = category_set(files, "candidate")
    require(actual == set(EXPECTED_CANDIDATE_SHA256), f"candidate file set mismatch: {sorted(actual ^ set(EXPECTED_CANDIDATE_SHA256))}")
    for logical, expected in EXPECTED_CANDIDATE_SHA256.items():
        require(digest(files[("candidate", logical)]) == expected, f"unexpected final candidate bytes: {logical}")
    controller = files[("candidate", ".claude/skills/subagent-workflow/scripts/controller/run.ts")].decode("utf-8")
    require("performance.now()" in controller, "controller lacks monotonic elapsed-time accounting")
    require("node deadline exceeded" in controller, "controller lacks node deadline")
    require("maxWallTimeSeconds" in controller, "controller lacks workflow wall-time cap")
    models = load_json(files[("candidate", "config/workflows/models.json")], "candidate roles")
    task_role = models.get("roles", {}).get("task", {})
    require(task_role == {"backend": "codex", "provider": "codex", "model": "gpt-5.6-sol", "effort": "high"}, "task role is not codex/gpt-5.6-sol high")
    native_map = load_json(files[("candidate", ".claude/skills/subagent-workflow/scripts/native/native-map.json")], "candidate native map")
    codex = native_map.get("providers", {}).get("codex", {})
    require(codex.get("bin") == "codex", "codex native provider is missing")
    require(codex.get("effort", {}).get("args") == ["-c", "model_reasoning_effort=\"{effort}\""], "codex effort forwarding changed")
    return models


def verify_result_evidence(
    category: str,
    result: dict[str, Any],
    files: dict[tuple[str, str], bytes],
    source_root: str,
    omitted: dict[tuple[str, str], dict[str, Any]],
) -> None:
    expected = category_set(files, category) - {"result.json"}
    expected |= {logical for omitted_category, logical in omitted if omitted_category == category}
    actual: set[str] = set()
    source_parts = Path(source_root).parts
    for entry in result.get("evidence", []):
        require(isinstance(entry, dict), f"malformed result evidence in {category}")
        evidence_parts = Path(entry.get("path", "")).parts
        offsets = range(len(evidence_parts) - len(source_parts) + 1)
        matches = [
            offset
            for offset in offsets
            if evidence_parts[offset : offset + len(source_parts)] == source_parts
        ]
        require(len(matches) == 1, f"result evidence path is not uniquely under run root in {category}")
        suffix = evidence_parts[matches[0] + len(source_parts) :]
        require(bool(suffix), f"result evidence path names the run root in {category}")
        logical = Path(*suffix).as_posix()
        if (category, logical) in files:
            expected_digest = digest(files[(category, logical)])
        else:
            require((category, logical) in omitted, f"result references absent evidence: {category}/{logical}")
            expected_digest = omitted[(category, logical)]["sha256"]
        require(entry.get("sha256") == expected_digest, f"result evidence digest mismatch: {category}/{logical}")
        actual.add(logical)
    require(actual == expected, f"result evidence coverage mismatch for {category}: {sorted(actual ^ expected)}")


def verify_common_run(
    category: str,
    run: dict[str, Any],
    files: dict[tuple[str, str], bytes],
    counts: tuple[int, int, int],
    wall_seconds: float,
    omitted: dict[tuple[str, str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = load_json(files[(category, "state.json")], f"{category} state")
    result = load_json(files[(category, "result.json")], f"{category} result")
    require(state.get("runId") == run.get("runId") == result.get("runId"), f"runId mismatch: {category}")
    require(state.get("designId") == run.get("designId") == result.get("designId"), f"designId mismatch: {category}")
    require(state.get("status") == run.get("observed", {}).get("status") == "success", f"state status mismatch: {category}")
    require(state.get("terminal") == result.get("terminal") == run.get("observed", {}).get("terminal") == "success", f"terminal mismatch: {category}")
    for key, expected in zip(("modelCalls", "schedulerCalls", "nodeCalls"), counts):
        require(state.get(key) == expected, f"state {key} mismatch: {category}")
        require(result.get("metrics", {}).get(key) == expected, f"result {key} mismatch: {category}")
        require(run.get("observed", {}).get(key) == expected, f"manifest {key} mismatch: {category}")
    require(result.get("metrics", {}).get("wallSeconds") == wall_seconds, f"result wallSeconds mismatch: {category}")
    require(run.get("observed", {}).get("wallSeconds") == wall_seconds, f"manifest wallSeconds mismatch: {category}")
    verify_result_evidence(category, result, files, run["sourceRoot"], omitted)
    return state, result


def verify_smoke(
    manifest: dict[str, Any],
    files: dict[tuple[str, str], bytes],
    models: dict[str, Any],
    omitted: dict[tuple[str, str], dict[str, Any]],
) -> None:
    require(category_set(files, "authenticatedSmoke") == EXPECTED_SMOKE_FILES, "authenticated smoke raw file set mismatch")
    run = manifest["runs"]["authenticatedSmoke"]
    require(run.get("runId") == "subagent-1785607327755-7362", "unexpected authenticated smoke runId")
    state, _ = verify_common_run("authenticatedSmoke", run, files, (3, 1, 2), 279.32, omitted)
    expected_chain = {
        "designSha256": EXPECTED_CANDIDATE_SHA256[".workflow/designs/subagent-scheduler-smoke.workflow.json"],
        "mappingSha256": EXPECTED_CANDIDATE_SHA256[".subagent/flows/subagent-scheduler-smoke.json"],
        "rolesSha256": EXPECTED_CANDIDATE_SHA256["config/workflows/models.json"],
        "nativeMapSha256": EXPECTED_CANDIDATE_SHA256[".claude/skills/subagent-workflow/scripts/native/native-map.json"],
        "inputSha256": EXPECTED_CANDIDATE_SHA256[".subagent/fixtures/subagent-scheduler-smoke.input.json"],
    }
    for key, expected in expected_chain.items():
        require(state.get(key) == expected, f"smoke state {key} does not bind the candidate")
    design = load_json(files[("candidate", ".workflow/designs/subagent-scheduler-smoke.workflow.json")], "smoke design")
    mapping = load_json(files[("candidate", ".subagent/flows/subagent-scheduler-smoke.json")], "smoke mapping")
    require(mapping.get("designSha256") == expected_chain["designSha256"], "smoke mapping design digest mismatch")
    roles = {node.get("id"): node.get("role") for node in design.get("nodes", []) if isinstance(node, dict)}
    require(roles.get("scout-a") == roles.get("scout-b") == "task", "smoke leaf roles are not task")
    require(models.get("roles", {}).get("task", {}).get("effort") == "high", "task role effort is not high")
    calls = load_jsonl(files[("authenticatedSmoke", "native-calls.jsonl")], "smoke native calls")
    require(len(calls) == 3, "smoke must contain exactly three model calls")
    require(calls[0].get("model") == "anthropic/claude-sonnet-5" and calls[0].get("effort") is None, "smoke scheduler call trace mismatch")
    for call in calls[1:]:
        require(call.get("model") == "codex/gpt-5.6-sol", "smoke leaf model is not codex/gpt-5.6-sol")
        require(call.get("effort") == "high", "smoke leaf effort is not high")
    require(run["observed"].get("leafModel") == "codex/gpt-5.6-sol", "manifest leaf model mismatch")
    require(run["observed"].get("leafEffort") == "high", "manifest leaf effort mismatch")
    decisions = load_jsonl(files[("authenticatedSmoke", "scheduler-decisions.jsonl")], "smoke scheduler decisions")
    require(len(decisions) == 1 and decisions[0].get("selected") == ["scout-a", "scout-b"], "smoke scheduler selection mismatch")
    scheduler_result = load_json(files[("authenticatedSmoke", "scheduler-responses/1.result.json")], "scheduler result")
    require(scheduler_result.get("exitCode") == 0 and scheduler_result.get("terminationClass") == "COMPLETED", "scheduler did not complete")
    for stream in ("stdout", "stderr"):
        raw = files[("authenticatedSmoke", f"scheduler-responses/1.{stream}")]
        require(digest(raw) == scheduler_result.get(f"{stream}Sha256"), f"scheduler {stream} digest mismatch")
    for node in ("scout-a", "scout-b"):
        result = load_json(files[("authenticatedSmoke", f"native-responses/{node}.result.json")], f"{node} result")
        require(result.get("exitCode") == 0 and result.get("terminationClass") == "COMPLETED", f"{node} did not complete")
        stdout = files[("authenticatedSmoke", f"native-responses/{node}.stdout")]
        require(digest(stdout) == result.get("stdoutSha256"), f"{node} stdout digest mismatch")
        stderr_meta = omitted[("authenticatedSmoke", f"native-responses/{node}.stderr")]
        require(result.get("stderrSha256") == stderr_meta["sha256"], f"{node} external stderr receipt mismatch")
        activation = final_json_line(files[("authenticatedSmoke", f"native-responses/{node}.stdout")], f"{node} stdout")
        require(isinstance(activation.get("statePatch"), list), f"{node} activation lacks statePatch")


def verify_long_probe(
    manifest: dict[str, Any],
    files: dict[tuple[str, str], bytes],
    omitted: dict[tuple[str, str], dict[str, Any]],
) -> None:
    require(category_set(files, "longProbe") == EXPECTED_LONG_FILES, "long probe raw file set mismatch")
    run = manifest["runs"]["longProbe"]
    require(run.get("runId") == "subagent-1785607672865-8848", "unexpected long probe runId")
    state, result = verify_common_run("longProbe", run, files, (0, 0, 0), 605.08, omitted)
    expected_chain = {
        "designSha256": EXPECTED_CANDIDATE_SHA256[".workflow/designs/subagent-long-node-probe.workflow.json"],
        "mappingSha256": EXPECTED_CANDIDATE_SHA256[".subagent/flows/subagent-long-node-probe.json"],
        "rolesSha256": EXPECTED_CANDIDATE_SHA256["config/workflows/models.json"],
        "nativeMapSha256": EXPECTED_CANDIDATE_SHA256[".claude/skills/subagent-workflow/scripts/native/native-map.json"],
        "inputSha256": EXPECTED_CANDIDATE_SHA256[".subagent/fixtures/subagent-long-node-probe.input.json"],
    }
    for key, expected in expected_chain.items():
        require(state.get(key) == expected, f"long probe state {key} does not bind the candidate")
    design_raw = files[("candidate", ".workflow/designs/subagent-long-node-probe.workflow.json")]
    design = load_json(design_raw, "long probe design")
    mapping = load_json(files[("candidate", ".subagent/flows/subagent-long-node-probe.json")], "long probe mapping")
    require(design.get("budgets", {}).get("maxWallTimeSeconds") == 660, "long probe global cap != 660")
    nodes = {node.get("id"): node for node in design.get("nodes", []) if isinstance(node, dict)}
    require(nodes.get("sleep-probe", {}).get("timeoutSeconds") == 620, "long probe node timeout != 620")
    require(mapping.get("designSha256") == digest(design_raw), "long probe mapping design digest mismatch")
    require(mapping.get("scheduler", {}).get("maxCalls") == 0, "long probe scheduler maxCalls != 0")
    handler = mapping.get("handlers", {}).get("sleep-probe", {})
    argv = handler.get("argv")
    require(handler.get("type") == "command" and isinstance(argv, list) and len(argv) == 3, "long probe command handler malformed")
    require(re.search(r"\btime\.sleep\(\s*605(?:\.0)?\s*\)", argv[2]) is not None, "long probe does not request sleep(605)")
    require(load_jsonl(files[("longProbe", "native-calls.jsonl")], "long native calls", allow_empty=True) == [], "long probe made a model call")
    events = load_jsonl(files[("longProbe", "events.jsonl")], "long events")
    require(all(event.get("modelCalls", 0) == 0 for event in events), "long probe event reports a model call")
    starts = [event for event in events if event.get("event") == "node_start" and event.get("nodeId") == "sleep-probe"]
    ends = [event for event in events if event.get("event") == "node_end" and event.get("nodeId") == "sleep-probe"]
    require(len(starts) == len(ends) == 1, "long probe must have exactly one sleep-probe start/end")
    elapsed_seconds = (iso_time(ends[0].get("time"), "long end") - iso_time(starts[0].get("time"), "long start")).total_seconds()
    elapsed_monotonic_ms = ends[0].get("elapsedMs", 0) - starts[0].get("elapsedMs", 0)
    require(elapsed_seconds > 600 and elapsed_monotonic_ms > 600_000, "long node did not span more than 600 seconds")
    require(abs(elapsed_seconds - run["observed"].get("nodeElapsedSeconds", -1)) < 0.001, "manifest long node duration mismatch")
    require(state.get("elapsedMs", 0) > 600_000 and result.get("metrics", {}).get("wallSeconds", 0) > 600, "long run did not exceed 600 seconds")
    require(run["observed"].get("nodeTimeoutSeconds") == 620, "manifest node timeout mismatch")
    require(run["observed"].get("maxWallTimeSeconds") == 660, "manifest global cap mismatch")
    require(run["observed"].get("requestedSleepSeconds") == 605, "manifest requested sleep mismatch")
    command_result = load_json(files[("longProbe", "command-responses/sleep-probe.result.json")], "long command result")
    require(command_result.get("exitCode") == 0 and command_result.get("terminationClass") == "COMPLETED", "long command did not complete")
    for stream in ("stdout", "stderr"):
        raw = files[("longProbe", f"command-responses/sleep-probe.{stream}")]
        require(digest(raw) == command_result.get(f"{stream}Sha256"), f"long command {stream} digest mismatch")
    activation = load_json(files[("longProbe", "command-responses/sleep-probe.stdout")], "long command stdout")
    require(activation.get("statePatch") == [{"op": "set", "path": "/probeStatus", "value": "PASS"}], "long command activation mismatch")


def main() -> int:
    try:
        require(MANIFEST_PATH.is_file(), f"missing manifest: {MANIFEST_PATH}")
        manifest = load_json(MANIFEST_PATH.read_bytes(), "manifest")
        require(isinstance(manifest, dict), "manifest is not an object")
        require(manifest.get("schema") == "subagent-workflow-evidence/v2", "manifest schema mismatch")
        require(set(manifest.get("runs", {})) == {"authenticatedSmoke", "longProbe"}, "manifest run set mismatch")
        omitted = verify_omitted(manifest)
        files = decode_files(manifest)
        require(not (set(files) & set(omitted)), "omitted sensitive evidence is also stored")
        models = verify_candidate(files)
        verify_smoke(manifest, files, models, omitted)
        verify_long_probe(manifest, files, omitted)
    except (EvidenceError, KeyError, TypeError, UnicodeDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: final subagent candidate, authenticated smoke, and >600-second probe evidence all verify offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
