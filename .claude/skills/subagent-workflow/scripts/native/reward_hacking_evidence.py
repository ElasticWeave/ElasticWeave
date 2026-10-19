#!/usr/bin/env python3
"""Deterministic byte-state evidence for machine-gated workflow review.

This is practical state binding, not an OS sandbox. It snapshots the root Git repository plus
Git repositories rooted under ``workspace/``. Each snapshot binds HEAD, index entries, and the
SHA-256/type/mode/symlink state of tracked and non-ignored untracked paths. Model reviewers see
only task/criteria/check/baseline/evidence facts; builder prose is deliberately absent.  There is
no repository-wide acceptance script or default-mode-pinned gate-file baseline in this contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


VERSION = 1
ITEM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
CONTROLLER_RELATIVE = Path(
    ".claude/skills/subagent-workflow/scripts/controller/run.ts"
)
CANONICAL_PROOF_FIELDS = {
    "PROOF_BINDING_DIGEST",
    "ROLE_MODEL_POLICY_DIGEST",
    "FROZEN_INPUTS_DIGEST",
    "MACHINE_REVIEW_DIGEST",
    "MACHINE_REVIEW_PROMPT_SHA256",
    "MACHINE_REVIEWED_STATE_DIGEST",
    "MACHINE_REVIEWED_INPUTS_DIGEST",
    "MACHINE_REVIEW_EVIDENCE_DIGEST",
    "NATIVE_TRACE_SHA256",
    "MACHINE_REVIEW_RESPONSE_SHA256",
    "MACHINE_REVIEW_RECEIPT_SHA256",
    "MACHINE_REVIEW_PROMPT_BYTES_SHA256",
    "BASELINE_DIGEST",
    "EVIDENCE_DIGEST",
    "EVIDENCE_PRODUCER_DIGEST",
    "EVIDENCE_PRODUCER_RECEIPT_SHA256",
    "FINAL_STATE_DIGEST",
    "CANDIDATE_DIGEST",
}
CANONICAL_COUNTER_FIELDS = {
    "MODEL_CALLS",
    "MAX_MODEL_CALLS",
    "MACHINE_REVIEW_CALL",
    "NATIVE_TRACE_CALLS",
    "EVIDENCE_PRODUCER_ACTIVATION",
}
CONTROLLER_AUTHORIZATION_MODE = "controller-tier1-env"
CONTROLLER_AUTHORIZATION_SOURCE = "GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1"
CONTROLLER_THREAT_BOUNDARY = (
    "Practical no-follow byte binding; not an OS sandbox against a concurrent "
    "same-UID pathname attacker."
)
EXCLUDED_ROOT_PREFIXES = (
    ".git",
    "workspace",
    "workflow-output",
)
BYTE_BOUND_PM_PREFIXES = (
    ".pm/goal.md",
    ".pm/eval",
    ".pm/evidence",
    ".pm/review-briefs",
    ".pm/reviews",
    ".pm/verdicts",
    ".pm/proofs",
)
MACHINE_WORKFLOW_PROOF_PREFIX = ".pm/workflow-reviews"


class EvidenceError(RuntimeError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest_json(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise EvidenceError(f"git {' '.join(args)} failed in {repo}: {message}")
    return result


def git_top(path: Path) -> Path | None:
    result = run_git(path, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        return None
    return Path(result.stdout.decode("utf-8", "surrogateescape").strip()).resolve()


def discover_repositories(root: Path) -> list[tuple[str, Path]]:
    root = root.resolve()
    root_top = git_top(root)
    if root_top is None:
        raise EvidenceError(f"root is not inside a Git repository: {root}")
    if root_top != root:
        raise EvidenceError(f"--root must be the Git top level ({root_top}), got {root}")

    found: dict[Path, str] = {root: "."}
    workspace_link = root / "workspace"
    if not os.path.lexists(workspace_link):
        return [(label, repo) for repo, label in found.items()]

    workspace = workspace_link.resolve()
    workspace_top = git_top(workspace)
    if workspace_top is not None and workspace_top != root:
        found[workspace_top] = "workspace"

    if workspace.is_dir():
        for current, dirs, files in os.walk(workspace, followlinks=False):
            current_path = Path(current)
            if ".git" in dirs or ".git" in files:
                top = git_top(current_path)
                if top is not None and top != root and top not in found:
                    try:
                        rel = top.relative_to(workspace).as_posix()
                        found[top] = "workspace" if rel == "." else f"workspace/{rel}"
                    except ValueError:
                        found[top] = "workspace"
                dirs[:] = [name for name in dirs if name != ".git"]

    return sorted(((label, repo) for repo, label in found.items()), key=lambda item: item[0])


def decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def excluded_path(label: str, path: str) -> bool:
    if label != ".":
        return False
    normalized = path.rstrip("/")
    # Machine workflow proofs bind themselves through TERMINAL / EVIDENCE-COMMITMENT.
    # Including the file being created in its own state digest would be recursive.
    # Independent `.pm/reviews/<milestone>.md` artifacts remain byte-bound.
    if normalized == MACHINE_WORKFLOW_PROOF_PREFIX or normalized.startswith(
        MACHINE_WORKFLOW_PROOF_PREFIX + "/"
    ):
        return True
    if any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in BYTE_BOUND_PM_PREFIXES
    ):
        return False
    if normalized == ".pm" or normalized.startswith(".pm/"):
        return True
    return any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in EXCLUDED_ROOT_PREFIXES)


def index_entries(repo: Path, label: str) -> list[dict]:
    raw = run_git(repo, "ls-files", "--stage", "-z").stdout
    entries = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, sep, raw_path = record.partition(b"\t")
        if not sep:
            raise EvidenceError(f"unexpected git ls-files --stage record in {repo}")
        fields = metadata.decode("ascii", "replace").split()
        if len(fields) != 3:
            raise EvidenceError(f"unexpected index metadata in {repo}: {metadata!r}")
        path = decode_path(raw_path)
        if excluded_path(label, path):
            continue
        entries.append({"path": path, "mode": fields[0], "oid": fields[1], "stage": int(fields[2])})
    return sorted(entries, key=lambda entry: (entry["path"], entry["stage"]))


def listed_paths(repo: Path, label: str, indexed: list[dict]) -> list[str]:
    raw = run_git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
    paths = {decode_path(item) for item in raw.split(b"\0") if item}
    paths.update(entry["path"] for entry in indexed)
    return sorted(path for path in paths if not excluded_path(label, path))


def excluded_paths(repo: Path, label: str) -> dict:
    ordinary = run_git(repo, "ls-files", "--cached", "--others", "--exclude-standard", "-z").stdout
    explicit = sorted(
        decode_path(item)
        for item in ordinary.split(b"\0")
        if item and excluded_path(label, decode_path(item))
    )
    ignored_raw = run_git(
        repo,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
        "-z",
    ).stdout
    ignored = sorted(decode_path(item) for item in ignored_raw.split(b"\0") if item)
    return {
        "ignoredUntrackedPolicy": "excluded",
        "ignoredPaths": ignored[:200],
        "ignoredPathCount": len(ignored),
        "ignoredPathsTruncated": len(ignored) > 200,
        "ignoredPathsDigest": digest_json(ignored),
        "explicitExcludedPaths": explicit[:200],
        "explicitExcludedPathCount": len(explicit),
        "explicitExcludedPathsTruncated": len(explicit) > 200,
        "explicitExcludedPathsDigest": digest_json(explicit),
    }


def dirty_paths(repo: Path, label: str) -> list[dict]:
    raw = run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=no").stdout
    records = [decode_path(item) for item in raw.split(b"\0") if item]
    dirty = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        status_code = record[:2]
        paths = [record[3:]]
        if status_code[0] in {"R", "C"} and index < len(records):
            paths.append(records[index])
            index += 1
        if all(excluded_path(label, path) for path in paths):
            continue
        dirty.append({"status": status_code, "paths": paths})
    return dirty


def regular_digest(path: Path) -> tuple[str, int, bool]:
    hasher = hashlib.sha256()
    size = 0
    binary = False
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            if b"\0" in chunk:
                binary = True
            size += len(chunk)
            hasher.update(chunk)
    return hasher.hexdigest(), size, binary


def worktree_entry(repo: Path, rel: str) -> dict:
    path = repo / rel
    if not os.path.lexists(path):
        return {"kind": "absent"}
    info = path.lstat()
    mode = format(stat.S_IMODE(info.st_mode), "04o")
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(os.fsencode(path))
        if isinstance(target, str):
            target = os.fsencode(target)
        return {
            "kind": "symlink",
            "mode": mode,
            "sha256": sha256_bytes(target),
            "size": len(target),
            "binary": False,
        }
    if stat.S_ISREG(info.st_mode):
        digest, size, binary = regular_digest(path)
        return {"kind": "file", "mode": mode, "sha256": digest, "size": size, "binary": binary}
    if stat.S_ISDIR(info.st_mode):
        return {"kind": "directory", "mode": mode}
    return {"kind": "special", "mode": mode}


def repo_snapshot(label: str, repo: Path, require_clean: bool = False) -> dict:
    dirty = dirty_paths(repo, label)
    if require_clean and dirty:
        details = ", ".join(
            f"{item['status']} {' -> '.join(item['paths'])}" for item in dirty[:20]
        )
        raise EvidenceError(f"dirty baseline in {label}: {details}")
    head_result = run_git(repo, "rev-parse", "HEAD", check=False)
    head = head_result.stdout.decode("ascii", "replace").strip() if head_result.returncode == 0 else "(unborn)"
    indexed = index_entries(repo, label)
    worktree = {}
    for path in listed_paths(repo, label, indexed):
        worktree[path] = worktree_entry(repo, path)
    return {
        "label": label,
        "head": head,
        "indexEntries": indexed,
        "indexDigest": digest_json(indexed),
        "worktreeEntries": worktree,
        "worktreeDigest": digest_json(worktree),
        "exclusions": excluded_paths(repo, label),
    }


def snapshot(root: Path, require_clean: bool = False) -> dict:
    repos = [repo_snapshot(label, repo, require_clean=require_clean) for label, repo in discover_repositories(root)]
    material = {"version": VERSION, "repositories": repos}
    digest_material = {
        "version": VERSION,
        "repositories": [
            {key: value for key, value in repo.items() if key != "exclusions"}
            for repo in repos
        ],
    }
    return {**material, "finalStateDigest": digest_json(digest_material)}


def state_binding(state: dict) -> dict:
    heads = {repo["label"]: repo["head"] for repo in state["repositories"] if repo["label"] != "."}
    root_repo = next(repo for repo in state["repositories"] if repo["label"] == ".")
    return {
        "version": VERSION,
        "finalStateDigest": state["finalStateDigest"],
        "rootHead": root_repo["head"],
        "workspaceHeads": heads,
    }


def baseline_summary(state: dict) -> dict:
    return {
        "version": VERSION,
        "repositories": [
            {
                "label": repo["label"],
                "head": repo["head"],
                "indexDigest": repo["indexDigest"],
                "worktreeDigest": repo["worktreeDigest"],
            }
            for repo in state["repositories"]
        ],
        "exclusions": [
            {"label": repo["label"], **repo["exclusions"]}
            for repo in state["repositories"]
        ],
        "stateDigest": state["finalStateDigest"],
    }


def write_json(path: Path, value, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_baseline(path: Path, root: Path, expected_digest: str | None = None) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"baseline is unreadable: {path}: {error}") from error
    if not isinstance(data, dict) or not isinstance(data.get("state"), dict):
        raise EvidenceError("baseline has no structured captured state")
    if data.get("version") != VERSION or Path(data.get("root", "")).resolve() != root.resolve():
        raise EvidenceError("baseline does not belong to this root or helper version")
    if data.get("baselineDigest") != digest_json(data.get("state")):
        raise EvidenceError("baseline digest does not match its captured state")
    try:
        expected_summary = baseline_summary(data["state"])
    except (KeyError, TypeError) as error:
        raise EvidenceError(f"baseline captured state is malformed: {error}") from error
    if data.get("summary") != expected_summary:
        raise EvidenceError("baseline summary does not match its captured state")
    if expected_digest is not None and data.get("baselineDigest") != expected_digest:
        raise EvidenceError("baseline file does not match the expected baseline digest from seed state")
    return data


def mapping(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for entry in entries:
        out.setdefault(entry["path"], []).append({key: value for key, value in entry.items() if key != "path"})
    return out


def index_changes(before: list[dict], after: list[dict]) -> list[dict]:
    old, new = mapping(before), mapping(after)
    changes = []
    for path in sorted(set(old) | set(new)):
        if old.get(path) == new.get(path):
            continue
        change = "added" if path not in old else "deleted" if path not in new else "modified"
        changes.append({"path": path, "change": change, "before": old.get(path), "after": new.get(path)})
    return changes


def worktree_change_kind(before: dict | None, after: dict | None) -> str | None:
    before_absent = before is None or before.get("kind") == "absent"
    after_absent = after is None or after.get("kind") == "absent"
    if before_absent and after_absent:
        return None
    if before_absent:
        return "added"
    if after_absent:
        return "deleted"
    if before.get("kind") != after.get("kind"):
        return "type-change"
    if before.get("kind") == "symlink" and before.get("sha256") != after.get("sha256"):
        return "symlink-target-change"
    if before.get("sha256") != after.get("sha256"):
        return "modified"
    if before.get("mode") != after.get("mode"):
        return "mode-change"
    return None


def worktree_changes(before: dict, after: dict) -> tuple[list[dict], list[dict]]:
    changes = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        kind = worktree_change_kind(old, new)
        if kind:
            changes.append({"path": path, "change": kind, "before": old, "after": new})

    deleted: dict[tuple, list[str]] = {}
    added: dict[tuple, list[str]] = {}
    for change in changes:
        entry = change.get("before") if change["change"] == "deleted" else change.get("after")
        if not entry or entry.get("kind") not in {"file", "symlink"}:
            continue
        signature = (entry.get("kind"), entry.get("sha256"), entry.get("size"))
        target = deleted if change["change"] == "deleted" else added if change["change"] == "added" else None
        if target is not None:
            target.setdefault(signature, []).append(change["path"])
    renames = []
    for signature in sorted(set(deleted) & set(added), key=str):
        for old, new in zip(sorted(deleted[signature]), sorted(added[signature])):
            renames.append({"from": old, "to": new})
    return changes, renames


def committed_changes(repo: Path, before: str, after: str) -> list[dict]:
    if before == after or "(unborn)" in {before, after}:
        return []
    result = run_git(repo, "diff", "--name-status", "--find-renames", "-z", before, after, "--", check=False)
    if result.returncode != 0:
        return [{"change": "head-changed", "detail": "name-status unavailable"}]
    fields = [decode_path(item) for item in result.stdout.split(b"\0") if item]
    out = []
    i = 0
    while i < len(fields):
        status_code = fields[i]
        i += 1
        if status_code.startswith(("R", "C")) and i + 1 < len(fields):
            out.append({"change": status_code, "from": fields[i], "to": fields[i + 1]})
            i += 2
        elif i < len(fields):
            out.append({"change": status_code, "path": fields[i]})
            i += 1
    return out


def compare_states(root: Path, before: dict, after: dict) -> list[dict]:
    old = {repo["label"]: repo for repo in before["repositories"]}
    new = {repo["label"]: repo for repo in after["repositories"]}
    locations = {label: repo for label, repo in discover_repositories(root)}
    evidence = []
    for label in sorted(set(old) | set(new)):
        prior, current = old.get(label), new.get(label)
        if prior is None or current is None:
            evidence.append({"label": label, "repositoryChange": "added" if prior is None else "deleted"})
            continue
        changes, renames = worktree_changes(prior["worktreeEntries"], current["worktreeEntries"])
        evidence.append(
            {
                "label": label,
                "head": {
                    "before": prior["head"],
                    "after": current["head"],
                    "changed": prior["head"] != current["head"],
                    "changes": committed_changes(locations[label], prior["head"], current["head"]),
                },
                "index": {
                    "beforeDigest": prior["indexDigest"],
                    "afterDigest": current["indexDigest"],
                    "changes": index_changes(prior["indexEntries"], current["indexEntries"]),
                },
                "worktree": {
                    "beforeDigest": prior["worktreeDigest"],
                    "afterDigest": current["worktreeDigest"],
                    "changes": changes,
                    "renames": renames,
                },
                "exclusions": current["exclusions"],
            }
        )
    return evidence


def check_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if (
            key in {"ENV", "BASH_ENV", "SHELLOPTS", "BASHOPTS", "CDPATH"}
            or key.startswith("BASH_FUNC_")
            or key.startswith("GENERAL_AGENT_")
        ):
            env.pop(key, None)
    return env


def parse_acceptance_argv(value: str) -> list[str]:
    try:
        argv = json.loads(value)
    except json.JSONDecodeError as error:
        raise EvidenceError(f"acceptance argv JSON is invalid: {error}") from error
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(item, str) or not item or "\0" in item for item in argv)
    ):
        raise EvidenceError("acceptance argv JSON must be a non-empty array of non-empty strings")
    return argv


def run_acceptance(root: Path, argv_json: str, timeout: int) -> dict:
    argv = parse_acceptance_argv(argv_json)
    try:
        result = subprocess.run(
            argv,
            cwd=root,
            env=check_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
        return {
            "argv": argv,
            "exitCode": result.returncode,
            "stdoutBytes": len(result.stdout),
            "stdoutSha256": sha256_bytes(result.stdout),
            "stderrBytes": len(result.stderr),
            "stderrSha256": sha256_bytes(result.stderr),
            "timedOut": False,
        }
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or b""
        stderr = error.stderr or b""
        return {
            "argv": argv,
            "exitCode": 124,
            "stdoutBytes": len(stdout),
            "stdoutSha256": sha256_bytes(stdout),
            "stderrBytes": len(stderr),
            "stderrSha256": sha256_bytes(stderr),
            "timedOut": True,
        }


def validate_item_id(item_id: str) -> None:
    if not ITEM_RE.fullmatch(item_id):
        raise EvidenceError(f"invalid item id: {item_id!r}")


def canonical_workflow_proof_relative(item_id: str) -> str:
    validate_item_id(item_id)
    return f"{MACHINE_WORKFLOW_PROOF_PREFIX}/{item_id}.md"


def legacy_workflow_proof_relative(item_id: str) -> str:
    validate_item_id(item_id)
    return f".pm/reviews/workflow-{item_id}.md"


def project_relative_no_follow(root: Path, raw: str, label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise EvidenceError(f"workflow proof {label} path is required")
    root = root.resolve()
    candidate = Path(raw)
    candidate = candidate if candidate.is_absolute() else root / candidate
    candidate = Path(os.path.normpath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise EvidenceError(f"workflow proof {label} must be inside the project root") from error
    posix = relative.as_posix()
    if posix == ".." or posix.startswith("../") or any(part in {"", ".", ".."} for part in relative.parts):
        raise EvidenceError(f"workflow proof {label} path is unsafe")
    return posix


def require_canonical_workflow_proof_relative(root: Path, item_id: str, proof_arg: str) -> str:
    relative = project_relative_no_follow(root, proof_arg, "proof")
    canonical = canonical_workflow_proof_relative(item_id)
    legacy = legacy_workflow_proof_relative(item_id)
    if relative == legacy or relative.startswith(".pm/reviews/workflow-"):
        raise EvidenceError(
            "legacy machine workflow proof path is ambiguous by construction; "
            f"rerun the controller to write {canonical}"
        )
    if relative != canonical:
        raise EvidenceError("workflow proof path is not the canonical protected artifact")
    return canonical


def ensure_plain_parents(root: Path, relative: str) -> Path:
    posix = relative.replace("\\", "/")
    parts = [part for part in posix.split("/") if part]
    if not parts:
        raise EvidenceError("workflow proof path is unsafe")
    current = root
    for part in parts[:-1]:
        current = current / part
        if os.path.lexists(current):
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise EvidenceError(f"workflow proof parent is not a plain directory: {current}")
        else:
            current.mkdir(mode=0o700)
    return root.joinpath(*parts)


def validate_digest(name: str, value: str) -> None:
    if not DIGEST_RE.fullmatch(value):
        raise EvidenceError(f"{name} is not a SHA-256 digest")


def validate_counter(name: str, value: str, *, positive: bool = False) -> int:
    if not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise EvidenceError(f"workflow proof {name} is not a canonical non-negative integer")
    parsed = int(value)
    if positive and parsed < 1:
        raise EvidenceError(f"workflow proof {name} must be positive")
    return parsed


def candidate_path(root: Path, item_id: str) -> Path:
    validate_item_id(item_id)
    root_key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:20]
    return Path(tempfile.gettempdir(), f"general-agent-workflow-{root_key}-{item_id}.json")


def proof_fields(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise EvidenceError(f"workflow proof is unreadable: {path}: {error}") from error
    out = {}
    for line in lines:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        if key in out:
            raise EvidenceError(f"workflow proof has duplicate {key} field")
        out[key] = value.strip()
    return out


def canonical_existing_child(root: Path, relative: Path, label: str) -> Path:
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise EvidenceError(f"workflow proof {label} path is unsafe")
    candidate = root.joinpath(relative)
    try:
        resolved = candidate.resolve(strict=True)
        root_resolved = root.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, RuntimeError, ValueError) as error:
        raise EvidenceError(f"workflow proof canonical {label} is unavailable: {candidate}: {error}") from error
    if resolved != candidate:
        raise EvidenceError(f"workflow proof canonical {label} must not traverse symlinks: {candidate}")
    return resolved


def frozen_executable_closure(run_dir: Path) -> tuple[str, str]:
    manifest = run_dir / "frozen-artifacts.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"controller proof audit cannot read the frozen executable closure: {error}") from error
    executables = payload.get("executables") if isinstance(payload, dict) else None
    if not isinstance(executables, list):
        raise EvidenceError("controller proof audit has no frozen executable closure")
    seen_labels: set[str] = set()
    directories: list[str] = []
    runtime = ""
    for binding in executables:
        if not isinstance(binding, dict):
            raise EvidenceError("frozen executable binding is malformed")
        label = binding.get("label")
        executable_path = binding.get("path")
        executable_digest = binding.get("sha256")
        if not isinstance(label, str) or not ITEM_RE.fullmatch(label) or label in seen_labels:
            raise EvidenceError("frozen executable labels are malformed or duplicated")
        seen_labels.add(label)
        if not isinstance(executable_path, str) or not Path(executable_path).is_absolute():
            raise EvidenceError(f"frozen {label} path is malformed")
        validate_digest(f"frozen {label} digest", str(executable_digest or ""))
        try:
            executable = Path(executable_path).resolve(strict=True)
            info = executable.stat()
        except (OSError, RuntimeError) as error:
            raise EvidenceError(f"frozen {label} is unavailable: {error}") from error
        if str(executable) != executable_path or not executable.is_file() or not os.access(executable, os.X_OK):
            raise EvidenceError(f"frozen {label} is not a canonical executable regular file")
        identity = {
            "dev": str(info.st_dev),
            "ino": str(info.st_ino),
            "ctimeNs": str(info.st_ctime_ns),
            "mtimeNs": str(info.st_mtime_ns),
            "size": str(info.st_size),
        }
        for name, value in identity.items():
            if binding.get(name) != value:
                raise EvidenceError(f"frozen {label} {name} identity changed")
        if binding.get("bytes") != info.st_size or binding.get("mode") != stat.S_IMODE(info.st_mode):
            raise EvidenceError(f"frozen {label} size or mode changed")
        try:
            actual_digest = sha256_bytes(executable.read_bytes())
        except OSError as error:
            raise EvidenceError(f"frozen {label} bytes are unreadable: {error}") from error
        if actual_digest != executable_digest:
            raise EvidenceError(f"frozen {label} bytes changed")
        directory = str(executable.parent)
        if directory not in directories:
            directories.append(directory)
        if label == "controller-runtime":
            runtime = str(executable)
    if not runtime:
        raise EvidenceError("controller proof audit requires exactly one frozen controller-runtime")
    return runtime, os.pathsep.join(directories)


def run_controller_proof_audit(root: Path, fields: dict[str, str], proof: Path) -> dict:
    design_id = fields.get("DESIGN_ID", "")
    run_id = fields.get("RUN_ID", "")
    if not ITEM_RE.fullmatch(design_id) or not ITEM_RE.fullmatch(run_id):
        raise EvidenceError("workflow proof DESIGN_ID/RUN_ID must be safe canonical identifiers")
    mapping = canonical_existing_child(
        root, Path(".subagent", "flows", f"{design_id}.json"), "mapping"
    )
    run_dir = canonical_existing_child(
        root, Path("workflow-output", "subagent", design_id, run_id), "run directory"
    )
    if not mapping.is_file() or not run_dir.is_dir():
        raise EvidenceError("workflow proof canonical mapping/run directory has the wrong type")
    controller = canonical_existing_child(root, CONTROLLER_RELATIVE, "controller")
    if not controller.is_file():
        raise EvidenceError("workflow proof canonical controller has the wrong type")
    expected_proof = canonical_existing_child(
        root, Path(canonical_workflow_proof_relative(fields["ITEM_ID"])), "proof"
    )
    if proof != expected_proof:
        raise EvidenceError("workflow proof path is not the canonical protected artifact")

    runtime, frozen_path = frozen_executable_closure(run_dir)
    audit_env = check_env()
    audit_env["PATH"] = frozen_path
    result = subprocess.run(
        [
            runtime,
            str(controller),
            "verify-proof",
            str(mapping),
            "--resume",
            str(run_dir),
        ],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=audit_env,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise EvidenceError(f"controller proof audit failed: {detail or 'no diagnostic'}")
    try:
        audited = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise EvidenceError(f"controller proof audit returned malformed JSON: {error}") from error
    if not isinstance(audited, dict):
        raise EvidenceError("controller proof audit returned a non-object result")
    if audited.get("terminal") != "success" or audited.get("reviewerTerminal") != "COMPLETE":
        raise EvidenceError("controller proof audit did not attest terminal success/COMPLETE")
    if audited.get("designId") != design_id or audited.get("runId") != run_id:
        raise EvidenceError("controller proof audit design/run binding disagrees with the proof")
    if audited.get("evidenceCommitment") != fields["EVIDENCE-COMMITMENT"]:
        raise EvidenceError("controller proof audit evidence commitment disagrees with the proof")
    proof_binding = audited.get("proof")
    if not isinstance(proof_binding, dict):
        raise EvidenceError("controller proof audit returned no proof binding")
    expected_relative = canonical_workflow_proof_relative(fields["ITEM_ID"])
    if proof_binding.get("path") != expected_relative:
        raise EvidenceError("controller proof audit returned the wrong protected proof path")
    validate_digest("controller proof digest", str(proof_binding.get("sha256", "")))
    if sha256_bytes(proof.read_bytes()) != proof_binding["sha256"]:
        raise EvidenceError("controller proof audit digest disagrees with the protected proof bytes")
    return audited


def load_evidence(
    path: Path,
    expected_baseline_digest: str,
    expected_evidence_digest: str,
    expected_binding: dict,
) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"evidence file is unreadable: {path}: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("evidence"), dict):
        raise EvidenceError("evidence file has no structured evidence payload")
    evidence = payload["evidence"]
    actual_digest = digest_json(evidence)
    if payload.get("evidenceDigest") != actual_digest or actual_digest != expected_evidence_digest:
        raise EvidenceError("evidence file does not match the expected evidence digest")
    if evidence.get("baselineDigest") != expected_baseline_digest:
        raise EvidenceError("evidence baseline digest does not match the candidate baseline digest")
    if evidence.get("stateBinding") != expected_binding:
        raise EvidenceError("evidence state binding does not match the candidate state binding")
    return payload


def verify_binding(root: Path, expected: dict) -> None:
    current = state_binding(snapshot(root))
    if current != expected:
        raise EvidenceError(
            "workflow proof state binding is stale: reviewed root/workspace HEAD or byte state no longer matches"
        )


def cmd_baseline(args) -> dict:
    root = Path(args.root).resolve()
    state = snapshot(root, require_clean=True)
    directory = Path(tempfile.mkdtemp(prefix="general-agent-reward-baseline-"))
    os.chmod(directory, 0o700)
    output = Path(args.output).resolve() if args.output else directory / "baseline.json"
    payload = {
        "version": VERSION,
        "root": str(root),
        "state": state,
        "baselineDigest": digest_json(state),
        "summary": baseline_summary(state),
    }
    write_json(output, payload)
    return {
        "baselinePath": str(output),
        "baselineDigest": payload["baselineDigest"],
        "baselineSummary": payload["summary"],
    }


def cmd_evidence(args) -> dict:
    root = Path(args.root).resolve()
    baseline_path = Path(args.baseline).resolve()
    validate_digest("expected baseline digest", args.expected_baseline_digest)
    baseline = load_baseline(baseline_path, root, args.expected_baseline_digest)
    acceptance = run_acceptance(root, args.acceptance_argv_json, args.timeout)
    current = snapshot(root)
    binding = state_binding(current)
    evidence = {
        "version": VERSION,
        "baselineDigest": baseline["baselineDigest"],
        "baseline": baseline["summary"],
        "coveragePolicy": {
            "covered": "root/workspace Git HEAD, index, tracked and non-ignored untracked worktree bytes",
            "excluded": "Git metadata, ignored untracked paths, explicit framework management/runtime artifacts, and parent workspace paths captured as separate repositories",
        },
        "acceptance": acceptance,
        "repositories": compare_states(root, baseline["state"], current),
        "stateBinding": binding,
    }
    evidence_digest = digest_json(evidence)
    output = (
        Path(args.output).resolve()
        if args.output
        else baseline_path.parent / f"evidence-{time.time_ns()}.json"
    )
    write_json(output, {"evidenceDigest": evidence_digest, "evidence": evidence})
    return {
        "evidencePath": str(output),
        "evidenceDigest": evidence_digest,
        "finalStateDigest": binding["finalStateDigest"],
        "stateBinding": binding,
        "evidence": evidence,
    }


def cmd_current_state(args) -> dict:
    return {"stateBinding": state_binding(snapshot(Path(args.root).resolve()))}


def cmd_clear_candidate(args) -> dict:
    path = candidate_path(Path(args.root), args.item_id)
    path.unlink(missing_ok=True)
    return {"candidatePath": str(path), "cleared": True}


def cmd_candidate(args) -> dict:
    root = Path(args.root).resolve()
    validate_digest("baseline digest", args.baseline_digest)
    validate_digest("evidence digest", args.evidence_digest)
    try:
        binding = json.loads(args.binding_json)
    except json.JSONDecodeError as error:
        raise EvidenceError(f"binding JSON is invalid: {error}") from error
    validate_digest("final state digest", str(binding.get("finalStateDigest", "")))
    evidence_path = Path(args.evidence_path).resolve()
    load_evidence(evidence_path, args.baseline_digest, args.evidence_digest, binding)
    path = candidate_path(root, args.item_id)
    material = {
            "version": VERSION,
            "root": str(root),
            "itemId": args.item_id,
            "baselineDigest": args.baseline_digest,
            "evidenceDigest": args.evidence_digest,
            "evidencePath": str(evidence_path),
            "stateBinding": binding,
        }
    candidate_digest = digest_json(material)
    write_json(path, {**material, "candidateDigest": candidate_digest})
    return {"candidatePath": str(path), "candidateDigest": candidate_digest}


def cmd_finalize_candidate(args) -> dict:
    root = Path(args.root).resolve()
    path = candidate_path(root, args.item_id)
    try:
        candidate = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceError(f"successful workflow archive candidate is missing or unreadable: {path}: {error}") from error
    if candidate.get("root") != str(root) or candidate.get("itemId") != args.item_id:
        raise EvidenceError("archive candidate root/item binding does not match this workflow")
    expected_candidate_digest = digest_json(
        {key: value for key, value in candidate.items() if key != "candidateDigest"}
    )
    if candidate.get("candidateDigest") != expected_candidate_digest:
        raise EvidenceError("archive candidate digest does not match its bound evidence material")
    load_evidence(
        Path(candidate.get("evidencePath", "")),
        candidate.get("baselineDigest", ""),
        candidate.get("evidenceDigest", ""),
        candidate.get("stateBinding", {}),
    )
    verify_binding(root, candidate.get("stateBinding", {}))
    proof = ensure_plain_parents(root, canonical_workflow_proof_relative(args.item_id))
    if os.path.lexists(proof):
        info = proof.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise EvidenceError(f"refusing symlink or special workflow proof target: {proof}")
    body = "\n".join(
        [
            "TERMINAL: COMPLETE",
            f"EVIDENCE-COMMITMENT: sha256:{candidate['candidateDigest']}",
            "REVIEW_POLICY: machine-gated",
            "COMPLETION_PROOF: state-bound",
            f"ITEM_ID: {args.item_id}",
            f"BASELINE_DIGEST: {candidate['baselineDigest']}",
            f"EVIDENCE_DIGEST: {candidate['evidenceDigest']}",
            f"FINAL_STATE_DIGEST: {candidate['stateBinding']['finalStateDigest']}",
            "STATE_BINDING_JSON: " + canonical_json(candidate["stateBinding"]),
            f"CANDIDATE_DIGEST: {candidate['candidateDigest']}",
            "",
        ]
    )
    fd, temp_name = tempfile.mkstemp(prefix=proof.name + ".", dir=proof.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, proof)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    path.unlink(missing_ok=True)
    return {"proofPath": str(proof), "stateBinding": candidate["stateBinding"]}


def cmd_verify_proof(args) -> dict:
    root = Path(args.root).resolve()
    validate_item_id(args.item_id)
    relative = require_canonical_workflow_proof_relative(root, args.item_id, args.proof)
    proof = canonical_existing_child(root, Path(relative), "proof")
    fields = proof_fields(proof)
    if fields.get("TERMINAL") != "COMPLETE":
        raise EvidenceError("workflow proof needs exact TERMINAL: COMPLETE")
    commitment = fields.get("EVIDENCE-COMMITMENT", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", commitment):
        raise EvidenceError("workflow proof needs exact EVIDENCE-COMMITMENT: sha256:<64hex>")
    validate_digest("candidate digest", fields.get("CANDIDATE_DIGEST", ""))
    if commitment != f"sha256:{fields['CANDIDATE_DIGEST']}":
        raise EvidenceError("workflow proof evidence commitment does not match its candidate digest")
    if fields.get("REVIEW_POLICY") != "machine-gated":
        raise EvidenceError("workflow proof needs exact REVIEW_POLICY: machine-gated")
    if fields.get("COMPLETION_PROOF") != "state-bound":
        raise EvidenceError("workflow proof needs exact COMPLETION_PROOF: state-bound")
    if fields.get("ITEM_ID") != args.item_id:
        raise EvidenceError("workflow proof ITEM_ID does not match")
    if fields.get("ITEM_KIND") not in {"issue", "milestone", "none"}:
        raise EvidenceError("workflow proof ITEM_KIND is missing or malformed")
    if fields.get("PROOF_SCOPE") not in {"pm-item", "criteria-artifact", "no-pm"}:
        raise EvidenceError("workflow proof PROOF_SCOPE is missing or malformed")
    if not ITEM_RE.fullmatch(fields.get("SUCCESS_TERMINAL", "")):
        raise EvidenceError("workflow proof SUCCESS_TERMINAL is missing or malformed")
    for name in CANONICAL_PROOF_FIELDS:
        validate_digest(name.lower().replace("_", " "), fields.get(name, ""))
    for name in CANONICAL_COUNTER_FIELDS:
        validate_counter(name, fields.get(name, ""), positive=name not in {"MODEL_CALLS", "MAX_MODEL_CALLS"})
    model_calls = validate_counter("MODEL_CALLS", fields["MODEL_CALLS"])
    max_model_calls = validate_counter("MAX_MODEL_CALLS", fields["MAX_MODEL_CALLS"])
    review_call = validate_counter("MACHINE_REVIEW_CALL", fields["MACHINE_REVIEW_CALL"], positive=True)
    trace_calls = validate_counter("NATIVE_TRACE_CALLS", fields["NATIVE_TRACE_CALLS"], positive=True)
    if model_calls < 1 or model_calls > max_model_calls or review_call != model_calls or trace_calls != model_calls:
        raise EvidenceError("workflow proof model/reviewer/trace call accounting is inconsistent")
    if fields["MACHINE_REVIEWED_STATE_DIGEST"] != fields["FINAL_STATE_DIGEST"]:
        raise EvidenceError("workflow proof reviewer state digest does not bind the final state")
    if fields["MACHINE_REVIEW_EVIDENCE_DIGEST"] != fields["EVIDENCE_DIGEST"]:
        raise EvidenceError("workflow proof reviewer evidence digest does not bind the final evidence")
    if fields.get("AUTHORIZATION_MODE") != CONTROLLER_AUTHORIZATION_MODE:
        raise EvidenceError("workflow proof has the wrong controller authorization mode")
    if fields.get("AUTHORIZATION_SOURCE") != CONTROLLER_AUTHORIZATION_SOURCE:
        raise EvidenceError("workflow proof has the wrong controller authorization source")
    if fields.get("THREAT_BOUNDARY") != CONTROLLER_THREAT_BOUNDARY:
        raise EvidenceError("workflow proof has the wrong state-bound threat boundary")
    if not ITEM_RE.fullmatch(fields.get("EVIDENCE_PRODUCER_NODE", "")):
        raise EvidenceError("workflow proof EVIDENCE_PRODUCER_NODE is missing or malformed")
    try:
        binding = json.loads(fields.get("STATE_BINDING_JSON", ""))
    except json.JSONDecodeError as error:
        raise EvidenceError(f"workflow proof STATE_BINDING_JSON is invalid: {error}") from error
    if not isinstance(binding, dict):
        raise EvidenceError("workflow proof STATE_BINDING_JSON is not an object")
    if binding.get("finalStateDigest") != fields["FINAL_STATE_DIGEST"]:
        raise EvidenceError("workflow proof final digest and state binding disagree")
    try:
        proof_binding = json.loads(fields.get("PROOF_BINDING_JSON", ""))
    except json.JSONDecodeError as error:
        raise EvidenceError(f"workflow proof PROOF_BINDING_JSON is invalid: {error}") from error
    if not isinstance(proof_binding, dict):
        raise EvidenceError("workflow proof PROOF_BINDING_JSON is not an object")
    if canonical_json(proof_binding) != fields.get("PROOF_BINDING_JSON"):
        raise EvidenceError("workflow proof PROOF_BINDING_JSON is not canonical")
    if digest_json({key: value for key, value in proof_binding.items() if key != "digest"}) != fields["PROOF_BINDING_DIGEST"]:
        raise EvidenceError("workflow proof binding digest does not match its canonical JSON")
    if (
        proof_binding.get("digest") != fields["PROOF_BINDING_DIGEST"]
        or proof_binding.get("roleModelPolicyDigest") != fields["ROLE_MODEL_POLICY_DIGEST"]
        or proof_binding.get("scope") != fields["PROOF_SCOPE"]
        or proof_binding.get("itemKind") != fields["ITEM_KIND"]
        or proof_binding.get("itemId") != args.item_id
    ):
        raise EvidenceError("workflow proof binding identity disagrees with scalar proof fields")
    audited = run_controller_proof_audit(root, fields, proof)
    if (
        audited.get("proofBinding") != proof_binding
        or audited.get("proofBindingDigest") != fields["PROOF_BINDING_DIGEST"]
        or audited.get("itemKind") != fields["ITEM_KIND"]
        or audited.get("itemId") != args.item_id
    ):
        raise EvidenceError("controller proof audit canonical PM binding disagrees with the protected proof")
    return {
        "valid": True,
        "stateBinding": binding,
        "designId": fields["DESIGN_ID"],
        "runId": fields["RUN_ID"],
        "evidenceCommitment": fields["EVIDENCE-COMMITMENT"],
        "proofBinding": proof_binding,
        "proofBindingDigest": fields["PROOF_BINDING_DIGEST"],
        "itemKind": fields["ITEM_KIND"],
        "itemId": fields["ITEM_ID"],
        "proof": audited["proof"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("baseline")
    command.add_argument("--root", required=True)
    command.add_argument("--output")
    command.set_defaults(func=cmd_baseline)

    command = sub.add_parser("evidence")
    command.add_argument("--root", required=True)
    command.add_argument("--baseline", required=True)
    command.add_argument("--expected-baseline-digest", required=True)
    command.add_argument("--acceptance-argv-json", required=True)
    command.add_argument("--timeout", type=int, default=3600)
    command.add_argument("--output")
    command.set_defaults(func=cmd_evidence)

    command = sub.add_parser("current-state")
    command.add_argument("--root", required=True)
    command.set_defaults(func=cmd_current_state)

    command = sub.add_parser("clear-candidate")
    command.add_argument("--root", required=True)
    command.add_argument("--item-id", required=True)
    command.set_defaults(func=cmd_clear_candidate)

    command = sub.add_parser("candidate")
    command.add_argument("--root", required=True)
    command.add_argument("--item-id", required=True)
    command.add_argument("--baseline-digest", required=True)
    command.add_argument("--evidence-digest", required=True)
    command.add_argument("--evidence-path", required=True)
    command.add_argument("--binding-json", required=True)
    command.set_defaults(func=cmd_candidate)

    command = sub.add_parser("finalize-candidate")
    command.add_argument("--root", required=True)
    command.add_argument("--item-id", required=True)
    command.set_defaults(func=cmd_finalize_candidate)

    command = sub.add_parser("verify-proof")
    command.add_argument("--root", required=True)
    command.add_argument("--item-id", required=True)
    command.add_argument("--proof", required=True)
    command.set_defaults(func=cmd_verify_proof)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = args.func(args)
    except EvidenceError as error:
        print(f"workflow-evidence: {error}", file=sys.stderr)
        return 2
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
