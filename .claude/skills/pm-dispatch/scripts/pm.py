#!/usr/bin/env python3
"""pm.py — the coordinator's file-first board (CCPM-adapted). The .pm board is canonical;

Canonical project state lives in `.pm/` as markdown+frontmatter (git-diffable, survives
sessions). Deterministic reporting = zero-token standup/status. In ui_mode=linear a bounded admin
worker mirrors it to Linear (and a bounded PM worker stores the returned id in each item's
`linear_id`); in interactive/file mode the board IS the UI.

Layout:
  .pm/config.json                 UI plus the selected research iteration mode and human lease
  .pm/goal.md                     immutable Goal + Acceptance Criteria (set once) + mutable log
  .pm/milestones/<id>.md          id,title,status,order,depends_on,review_*
  .pm/issues/<milestone>/<id>.md  dispatch/review fields plus a digest-bound iteration snapshot
  .pm/tasks/<issue-id>/task.json  task/issue/iteration-snapshot binding
  .pm/tasks/<issue-id>/events.jsonl canonical digest-chained task observations
  .pm/tasks/<issue-id>/experiments/ task-owned experiment files
  .pm/log.md                      evolution / rework log
  .pm/workflow-reviews/<id>.md    machine-gated workflow proof (disjoint from .pm/reviews/)
  .pm/reviews/<id>.md             independent milestone terminal artifact
  .pm/verdicts/<id>.md            independent issue terminal artifact

Commands:
  init [--debug] [--ui-mode linear|interactive|file]
       [--iteration-mode autonomous|human_guided]
       [--human-checkin-lease-minutes 5..1440]
       [--human-unavailable-fallback pause|autonomous_review]
  iteration-mode show
  iteration-mode set <autonomous|human_guided> [lease/fallback options]
  iteration-mode renew [--human-checkin-lease-minutes 5..1440]
  task-workspace init <issue-id>                 # explicitly migrate an existing issue
  task-workspace show <issue-id>                 # verify and render task state
  task-workspace record <issue-id> --kind <kind> --summary <text>
        [--experiment-path <task-owned-directory>] [--file <repo-relative-file>]...
  milestone add <id> <title> --acceptance <criteria-or-check>
        --review-policy independent --review-reason <why> [--order N] [--depends a,b]
  issue <id> <milestone> <title> --lane <direct|native-subagent|workflow> --lane-reason <why>
        --acceptance <criteria-or-check> --review-policy <independent|machine-gated> --review-reason <why>
        --estimated-model-calls N --max-model-calls N --promotion-triggers <facts|none>
        [--direct-scope <read-only-inspection|disposable-verification>]
        [--reasoning-posture P --task-class C --semantic-role R --launch-receipt PATH]
        [--launch-plan PATH]
        [--depth N] [--depends a,b] [--parallel]
  bind-workflow-receipts <id> --receipt-manifest PATH  # after adapter-emitted native receipts
  set <milestone|issue> <id> <status>          # forward FSM; terminal transitions need receipts
  migrate-review-policy <milestone|issue> <id> --to <independent|machine-gated> --reason <why>
  reopen <milestone|issue> <id> --reason "..." # rework/rollback
  next [--milestone M]                         # ready issues (deps done) in current/﻿given milestone
  standup                                      # deterministic status report
"""
import argparse
import ast
import contextlib
import errno
try:
    import fcntl
except ImportError:  # pragma: no cover - the supported runtime is POSIX
    fcntl = None
import glob
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

STATUSES = [
    "todo",
    "in_progress",
    "review",
    "awaiting_human",
    "reopened",
    "finalizing",
    "done",
]
REVIEW_POLICIES = ["independent", "machine-gated"]
TERMINAL_VERDICTS = ["COMPLETE", "HUMAN_BLOCKED", "REVISE"]
HUMAN_REQUEST_KINDS = ["authorization", "decision", "credential", "external-action"]
DIRECT_SCOPES = ["read-only-inspection", "disposable-verification"]
STRONG_WORKFLOW_DIGEST_FIELDS = (
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
)
STRONG_WORKFLOW_FIELDS = (
    "TERMINAL",
    "EVIDENCE-COMMITMENT",
    "REVIEW_POLICY",
    "COMPLETION_PROOF",
    "ITEM_ID",
    "DESIGN_ID",
    "RUN_ID",
    "SUCCESS_TERMINAL",
    "MODEL_CALLS",
    "MAX_MODEL_CALLS",
    *STRONG_WORKFLOW_DIGEST_FIELDS,
    "MACHINE_REVIEW_CALL",
    "NATIVE_TRACE_CALLS",
    "EVIDENCE_PRODUCER_NODE",
    "EVIDENCE_PRODUCER_ACTIVATION",
    "STATE_BINDING_JSON",
    "AUTHORIZATION_MODE",
    "AUTHORIZATION_SOURCE",
    "THREAT_BOUNDARY",
)
STATE_BOUND_THREAT_BOUNDARY = (
    "Practical no-follow byte binding; not an OS sandbox against a concurrent same-UID pathname attacker."
)
NONTERMINAL_TRANSITIONS = {
    "todo": {"in_progress"},
    "reopened": {"in_progress"},
    "in_progress": {"review"},
    "review": set(),
    "awaiting_human": set(),
    "finalizing": set(),
    "done": set(),
}
TERMINAL_TRANSITIONS = {
    ("review", "done"),
    ("review", "awaiting_human"),
}
SKILLS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REWARD_EVIDENCE_HELPER = os.path.join(
    SKILLS_ROOT,
    "subagent-workflow",
    "scripts",
    "native",
    "reward_hacking_evidence.py",
)
ISSUE_REVIEW_HELPER = os.path.join(SKILLS_ROOT, "codex-verify", "scripts", "verify.py")
MILESTONE_REVIEW_HELPER = os.path.join(
    SKILLS_ROOT, "milestone-review", "scripts", "milestone_review.py"
)
CONFIG_SYNC_ROOT = os.path.join(
    SKILLS_ROOT, "update-platform-config", "scripts", "config-sync"
)
NATIVE_LAUNCH_RECEIPT_SCHEMA = os.path.join(
    CONFIG_SYNC_ROOT, "native_launch_receipt.schema.json"
)
LAUNCH_SPEC_RESOLVER = os.path.join(CONFIG_SYNC_ROOT, "resolve_launch_spec.py")
MODEL_ROLES_PATH = os.path.join("config", "workflows", "models.json")
MODEL_ROLES_PREFIX = os.path.join("config", "workflows")
LAUNCH_RECEIPT_PREFIX = os.path.join(".pm", "evidence", "launch-receipts")
LAUNCH_PLAN_PREFIX = os.path.join(".pm", "evidence", "workflow-launch-plans")
RECEIPT_MANIFEST_PREFIX = os.path.join(".pm", "evidence", "workflow-receipt-manifests")
MERGE_RECEIPT_PREFIX = os.path.join(".pm", "receipts", "merge")
HUMAN_RECEIPT_PREFIX = os.path.join(".pm", "receipts", "human")
DESIGN_PREFIX = os.path.join(".workflow", "designs")
MAPPING_PREFIXES = (
    os.path.join(".subagent", "flows"),
    os.path.join(".humanize", "mappings"),
    os.path.join(".humanize", "flows"),
    os.path.join(".humanize", "generated-flows"),
)
HUMANIZE_CONTRACT_PATH = os.path.join(
    SKILLS_ROOT, "humanize-workflow", "scripts", "safe_mapping.py"
)
HUMANIZE_SPEC_PREFIX = os.path.join(".humanize", "specs")
HUMANIZE_GENERATED_PREFIX = os.path.join(".humanize", "generated-flows")
HUMANIZE_CONTRACT_NAMES = (
    "MAPPING_SCHEMA", "SPEC_SCHEMA", "UPSTREAM_REPOSITORY", "UPSTREAM_REVISION",
    "HMZ_DISTRIBUTION_VERSION", "ADAPTER_VERSION", "HUMAN_REQUEST_KINDS",
)
HUMANIZE_MAPPING_KEYS = (
    "schema", "adapterVersion", "upstream", "designPath", "designSha256", "specPath",
    "specSha256", "artifact", "artifactSha256", "inputPath", "reasoningPosture", "nodeAgents",
    "terminalProtocol",
)
HUMANIZE_UPSTREAM_KEYS = ("repository", "revision", "distribution")
HUMANIZE_TERMINAL_KEYS = ("field", "values", "humanRequestKinds", "independentReviewRequired")
UNBOUND = "unbound"
WORKFLOW_LAUNCH_PLAN_SCHEMA = "workflow-launch-plan/v1"
WORKFLOW_RECEIPT_MANIFEST_SCHEMA = "workflow-receipt-manifest/v1"
LAUNCH_PLAN_KEYS = (
    "schema", "designPath", "designSha256", "mappingPath", "mappingSha256",
    "policyPath", "policySha256", "leaves", "planSha256",
)
LAUNCH_PLAN_LEAF_KEYS = (
    "leafId", "taskClass", "reasoningPosture", "semanticRole", "reviewMode", "writerRole",
)
RECEIPT_MANIFEST_KEYS = ("schema", "planPath", "planSha256", "leaves", "manifestSha256")
RECEIPT_MANIFEST_LEAF_KEYS = ("leafId", "receiptPath", "receiptSha256")
REASONING_POSTURES = ("economy", "balanced", "frontier")
REVIEW_MODES = ("none", "exact-opposite")
ITERATION_MODES = ("autonomous", "human_guided")
HUMAN_UNAVAILABLE_FALLBACKS = ("pause", "autonomous_review")
ITERATION_CONFIG_KEYS = (
    "iteration_mode",
    "human_checkin_lease_minutes",
    "human_unavailable_fallback",
    "human_checkin_lease_expires_at",
)
ITERATION_SNAPSHOT_KEYS = (
    "iteration_mode_selected",
    "iteration_mode_effective",
    "human_checkin_lease_minutes",
    "human_unavailable_fallback",
    "human_checkin_lease_expires_at",
    "human_checkin_lease_status",
    "iteration_mode_decision_at",
)
TASK_WORKSPACE_SCHEMA = "pm-task-workspace/v1"
TASK_EVENT_SCHEMA = "pm-task-event/v1"
TASK_EVENT_KINDS = (
    "expectation",
    "experiment",
    "observation",
    "deviation",
    "simplify",
    "verify",
)
TASK_EVIDENCE_REQUIRED_KINDS = frozenset(TASK_EVENT_KINDS) - {"expectation"}
TASK_EVENT_GENESIS = "sha256:" + ("0" * 64)
TASK_WORKSPACE_KEYS = (
    "schema",
    "issueId",
    "issuePath",
    "issueSnapshotSha256",
    "iterationModeSnapshotSha256",
    "eventLog",
    "experimentRoot",
)
TASK_EVENT_KEYS = (
    "schema",
    "issueId",
    "sequence",
    "kind",
    "summary",
    "experimentPath",
    "evidence",
    "recordedAt",
    "previousEventSha256",
    "eventSha256",
)
TASK_EVIDENCE_KEYS = ("path", "bytes", "sha256")
TASK_WORKSPACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# Board status, UI linkage, human-routing receipts, and finalization are lifecycle fields. All
# goal, acceptance, dispatch, policy, and issue-body bytes remain part of the task snapshot.
TASK_MUTABLE_ISSUE_KEYS = frozenset({
    "status", "linear_id", "receipt_manifest_path", "receipt_manifest_sha256",
    "finalization_transaction", "finalization_receipt", "human_request_kind",
    "human_request", "human_request_key", "expected_human_id", "human_send_receipt",
})
MIN_HUMAN_CHECKIN_LEASE_MINUTES = 5
MAX_HUMAN_CHECKIN_LEASE_MINUTES = 1440
CONTROL_FILE_MODE = 0o600
CONTROL_DIRECTORY_MODE = 0o700
TRUSTED_GIT_CANDIDATES = ("/usr/bin/git", "/bin/git")
_TRUSTED_GIT_BINDING = None
# ---------- frontmatter (minimal, dependency-free) ----------
def parse(text):
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    meta, body = {}, "\n".join(lines[end + 1:])
    for line in lines[1:end]:
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k] = [x.strip() for x in inner.split(",")] if inner else []
        else:
            meta[k] = v
    return meta, body


def dump(meta, body=""):
    out = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            out.append(f"{k}: [{', '.join(v)}]")
        else:
            out.append(f"{k}: {v}")
    out.append("---")
    return "\n".join(out) + "\n" + body


def read(path):
    with open(path, encoding="utf-8") as f:
        return parse(f.read())


def write(path, meta, body=""):
    _atomic_text(path, dump(meta, body))


def _mtime_ns(path):
    return os.stat(path, follow_symlinks=False).st_mtime_ns


def _atomic_text(path, content, mode=None):
    """Atomically replace a control file through a no-follow, repository-anchored directory FD."""
    root = os.path.abspath(os.path.dirname(pmroot()))
    target = os.path.abspath(path)
    if os.path.commonpath((root, target)) != root or target == root:
        sys.exit("REFUSED: control output must remain inside the repository root")
    relative = os.path.relpath(target, root)
    parts = relative.split(os.sep)
    if any(part in ("", ".", "..") for part in parts):
        sys.exit("REFUSED: control output path is not a plain repository-relative path")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        sys.exit("REFUSED: control output requires no-follow file-descriptor support")
    if mode is not None and (not isinstance(mode, int) or mode & ~0o700):
        sys.exit("REFUSED: control output mode is unsafe")
    selected_mode = CONTROL_FILE_MODE if mode is None else mode
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    leaf_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    root_fd = parent_fd = temporary_fd = None
    temporary_name = None
    temporary_stat = None
    try:
        root_fd = os.open(root, directory_flags)
        root_stat = os.fstat(root_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            sys.exit("REFUSED: repository root is not a directory")
        parent_fd = root_fd
        parent_identity = [(root_stat.st_dev, root_stat.st_ino)]
        for component in parts[:-1]:
            try:
                before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                os.mkdir(component, CONTROL_DIRECTORY_MODE, dir_fd=parent_fd)
                before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                sys.exit(f"REFUSED: control output parent is not a plain directory: {component}")
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            opened = os.fstat(next_fd)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                os.close(next_fd)
                sys.exit("REFUSED: control output parent changed while it was opened")
            if parent_fd != root_fd:
                os.close(parent_fd)
            parent_fd = next_fd
            parent_identity.append((opened.st_dev, opened.st_ino))

        leaf = parts[-1]
        try:
            original_leaf = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            original_leaf = None
        if original_leaf is not None and (
            not stat.S_ISREG(original_leaf.st_mode) or original_leaf.st_nlink != 1
        ):
            sys.exit("REFUSED: control output leaf must be a single-link regular file")

        for _ in range(32):
            candidate = f".{leaf}.{os.getpid()}.{secrets.token_hex(16)}.tmp"
            try:
                temporary_fd = os.open(candidate, leaf_flags, CONTROL_FILE_MODE, dir_fd=parent_fd)
                temporary_name = candidate
                break
            except FileExistsError:
                continue
        if temporary_fd is None:
            sys.exit("REFUSED: could not allocate an atomic control-output temporary file")
        payload = content.encode("utf-8")
        offset = 0
        while offset < len(payload):
            written = os.write(temporary_fd, payload[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "atomic control-output write made no progress (zero-byte write)")
            offset += written
        os.fchmod(temporary_fd, selected_mode)
        os.fsync(temporary_fd)
        temporary_stat = os.fstat(temporary_fd)

        # Rewalk from the held root FD before replacement. A pathname swap cannot redirect the
        # held write, and this check refuses instead of publishing to a stale unlinked parent.
        check_fd = os.dup(root_fd)
        try:
            for component, expected in zip(parts[:-1], parent_identity[1:]):
                before = os.stat(component, dir_fd=check_fd, follow_symlinks=False)
                if not stat.S_ISDIR(before.st_mode) or (before.st_dev, before.st_ino) != expected:
                    sys.exit("REFUSED: control output parent drifted during atomic replacement")
                next_fd = os.open(component, directory_flags, dir_fd=check_fd)
                opened = os.fstat(next_fd)
                os.close(check_fd)
                check_fd = next_fd
                if (opened.st_dev, opened.st_ino) != expected:
                    sys.exit("REFUSED: control output parent drifted during atomic replacement")
            if original_leaf is None:
                try:
                    os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    sys.exit("REFUSED: control output leaf appeared during atomic replacement")
            else:
                current = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
                if (
                    not stat.S_ISREG(current.st_mode)
                    or any(getattr(current, field) != getattr(original_leaf, field) for field in stable_fields)
                ):
                    sys.exit("REFUSED: control output leaf drifted during atomic replacement")
            if os.fstat(temporary_fd).st_ino != temporary_stat.st_ino:
                sys.exit("REFUSED: control-output temporary file drifted before replacement")
            os.replace(temporary_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temporary_name = None
            os.fsync(parent_fd)
        finally:
            os.close(check_fd)
        os.close(temporary_fd)
        temporary_fd = None
    except OSError as error:
        sys.exit(f"REFUSED: cannot write control output without following links: {error}")
    finally:
        if temporary_name is not None and parent_fd is not None:
            with contextlib.suppress(OSError):
                current = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
                cleanup_stat = temporary_stat
                if cleanup_stat is None and temporary_fd is not None:
                    cleanup_stat = os.fstat(temporary_fd)
                if cleanup_stat is not None and (
                    (current.st_dev, current.st_ino) == (cleanup_stat.st_dev, cleanup_stat.st_ino)
                ):
                    os.unlink(temporary_name, dir_fd=parent_fd)
        if temporary_fd is not None:
            with contextlib.suppress(OSError):
                os.close(temporary_fd)
        if parent_fd is not None and parent_fd != root_fd:
            os.close(parent_fd)
        if root_fd is not None:
            os.close(root_fd)


def _trusted_git_head(repository):
    """Read HEAD from a root-owned, no-follow Git binding under a scrubbed Git environment."""
    global _TRUSTED_GIT_BINDING
    if _TRUSTED_GIT_BINDING is None:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        for candidate in TRUSTED_GIT_CANDIDATES:
            try:
                before = os.stat(candidate, follow_symlinks=False)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != 0
                    or before.st_mode & 0o022
                    or not before.st_mode & 0o111
                ):
                    continue
                descriptor = os.open(candidate, flags)
                opened = os.fstat(descriptor)
                stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_uid", "st_mode")
                if any(getattr(before, field) != getattr(opened, field) for field in stable_fields):
                    os.close(descriptor)
                    continue
                digest = hashlib.sha256()
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                after = os.fstat(descriptor)
                if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
                    os.close(descriptor)
                    continue
                _TRUSTED_GIT_BINDING = {
                    "descriptor": descriptor,
                    "digest": digest.hexdigest(),
                    "executable": f"/proc/self/fd/{descriptor}",
                }
                break
            except OSError:
                continue
        if _TRUSTED_GIT_BINDING is None:
            sys.exit("REFUSED: no trusted system Git executable is available for PM provenance")
    binding = _TRUSTED_GIT_BINDING
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    try:
        result = subprocess.run(
            [
                binding["executable"], "--no-replace-objects", "-c", "core.replaceRefs=false",
                "rev-parse", "--verify", "HEAD^{commit}",
            ],
            cwd=repository,
            env=environment,
            pass_fds=(binding["descriptor"],),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        sys.exit(f"REFUSED: trusted Git provenance lookup failed: {error}")
    head = result.stdout.strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", head):
        sys.exit("REFUSED: trusted Git could not resolve a plain commit HEAD for PM provenance")
    return head


def pmroot():
    return os.path.join(os.getcwd(), ".pm")


def _utc_now():
    return datetime.now(timezone.utc)


def _format_utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimeError("internal: UTC timestamp requires an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value, label):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        sys.exit(f"REFUSED: {label} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        sys.exit(f"REFUSED: {label} is not a valid UTC timestamp: {error}")
    if parsed.utcoffset() != timedelta(0):
        sys.exit(f"REFUSED: {label} must be UTC")
    return parsed


def _validate_lease_minutes(value, *, required):
    if value is None and not required:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_HUMAN_CHECKIN_LEASE_MINUTES <= value <= MAX_HUMAN_CHECKIN_LEASE_MINUTES
    ):
        sys.exit(
            "REFUSED: human_checkin_lease_minutes must be an integer in "
            f"{MIN_HUMAN_CHECKIN_LEASE_MINUTES}..{MAX_HUMAN_CHECKIN_LEASE_MINUTES}"
        )
    return value


def _new_iteration_fields(mode, lease_minutes, fallback, *, now=None):
    if mode not in ITERATION_MODES:
        sys.exit(f"REFUSED: iteration_mode must be one of {', '.join(ITERATION_MODES)}")
    if fallback not in HUMAN_UNAVAILABLE_FALLBACKS:
        sys.exit(
            "REFUSED: human_unavailable_fallback must be one of "
            + ", ".join(HUMAN_UNAVAILABLE_FALLBACKS)
        )
    if mode == "autonomous":
        if lease_minutes is not None:
            sys.exit("REFUSED: autonomous mode cannot carry a human check-in lease")
        return {
            "iteration_mode": mode,
            "human_checkin_lease_minutes": None,
            "human_unavailable_fallback": fallback,
            "human_checkin_lease_expires_at": None,
        }
    lease_minutes = _validate_lease_minutes(lease_minutes, required=True)
    current = now or _utc_now()
    return {
        "iteration_mode": mode,
        "human_checkin_lease_minutes": lease_minutes,
        "human_unavailable_fallback": fallback,
        "human_checkin_lease_expires_at": _format_utc(
            current + timedelta(minutes=lease_minutes)
        ),
    }


def _load_pm_config():
    """Read config through the repository no-follow path and validate iteration fields.

    A config with no iteration fields is the one supported legacy shape and resolves
    conservatively to autonomous. Once any mode field exists, all four are mandatory.
    """
    _, _, raw = _repo_regular_file(
        ".pm/config.json", prefix=".pm", label="PM config", exclusive=True
    )
    _, config = _load_json_regular(raw, "PM config")
    present = set(config).intersection(ITERATION_CONFIG_KEYS)
    if not present:
        normalized = dict(config)
        normalized.update(
            {
                "iteration_mode": "autonomous",
                "human_checkin_lease_minutes": None,
                "human_unavailable_fallback": "pause",
                "human_checkin_lease_expires_at": None,
            }
        )
        return config, normalized, True
    if present != set(ITERATION_CONFIG_KEYS):
        missing = sorted(set(ITERATION_CONFIG_KEYS) - present)
        sys.exit(
            "REFUSED: PM config has a partial iteration-mode contract; missing "
            + ", ".join(missing)
        )
    mode = config["iteration_mode"]
    fallback = config["human_unavailable_fallback"]
    if mode not in ITERATION_MODES:
        sys.exit(f"REFUSED: iteration_mode must be one of {', '.join(ITERATION_MODES)}")
    if fallback not in HUMAN_UNAVAILABLE_FALLBACKS:
        sys.exit(
            "REFUSED: human_unavailable_fallback must be one of "
            + ", ".join(HUMAN_UNAVAILABLE_FALLBACKS)
        )
    lease = config["human_checkin_lease_minutes"]
    expiry = config["human_checkin_lease_expires_at"]
    if mode == "autonomous":
        if lease is not None or expiry is not None:
            sys.exit("REFUSED: autonomous mode must not carry a human check-in lease or expiry")
    else:
        _validate_lease_minutes(lease, required=True)
        _parse_utc(expiry, "human_checkin_lease_expires_at")
    return config, dict(config), False


def _iteration_mode_state(config, *, legacy=False, now=None):
    current = now or _utc_now()
    selected = config["iteration_mode"]
    expiry = config["human_checkin_lease_expires_at"]
    if selected == "autonomous":
        effective = "autonomous"
        lease_status = "not_applicable"
        dispatch_allowed = True
    elif _parse_utc(expiry, "human_checkin_lease_expires_at") > current:
        effective = "human_guided"
        lease_status = "active"
        dispatch_allowed = True
    elif config["human_unavailable_fallback"] == "autonomous_review":
        effective = "autonomous"
        lease_status = "expired"
        dispatch_allowed = True
    else:
        effective = None
        lease_status = "expired"
        dispatch_allowed = False
    return {
        "selected_iteration_mode": selected,
        "effective_iteration_mode": effective,
        "human_checkin_lease_minutes": config["human_checkin_lease_minutes"],
        "human_unavailable_fallback": config["human_unavailable_fallback"],
        "human_checkin_lease_expires_at": expiry,
        "human_checkin_lease_status": lease_status,
        "dispatch_allowed": dispatch_allowed,
        "decision_timestamp": _format_utc(current),
        "legacy_config": bool(legacy),
    }


def _iteration_issue_snapshot():
    _, config, legacy = _load_pm_config()
    state = _iteration_mode_state(config, legacy=legacy)
    if not state["dispatch_allowed"]:
        sys.exit(
            "REFUSED: human_guided check-in lease expired and human_unavailable_fallback=pause; "
            "renew the lease or explicitly set autonomous mode before creating an issue"
        )
    snapshot = {
        "iteration_mode_selected": state["selected_iteration_mode"],
        "iteration_mode_effective": state["effective_iteration_mode"],
        "human_checkin_lease_minutes": (
            "none"
            if state["human_checkin_lease_minutes"] is None
            else str(state["human_checkin_lease_minutes"])
        ),
        "human_unavailable_fallback": state["human_unavailable_fallback"],
        "human_checkin_lease_expires_at": state["human_checkin_lease_expires_at"] or "none",
        "human_checkin_lease_status": state["human_checkin_lease_status"],
        "iteration_mode_decision_at": state["decision_timestamp"],
    }
    snapshot["iteration_mode_snapshot_sha256"] = _canonical_sha256(snapshot)
    return snapshot


def _legacy_iteration_issue_snapshot():
    """Canonical conservative interpretation for issues created before mode snapshots."""
    snapshot = {
        "iteration_mode_selected": "autonomous",
        "iteration_mode_effective": "autonomous",
        "human_checkin_lease_minutes": "none",
        "human_unavailable_fallback": "pause",
        "human_checkin_lease_expires_at": "none",
        "human_checkin_lease_status": "not_applicable",
        "iteration_mode_decision_at": "1970-01-01T00:00:00Z",
    }
    snapshot["iteration_mode_snapshot_sha256"] = _canonical_sha256(snapshot)
    return snapshot


def _validate_iteration_snapshot(meta):
    digest_key = "iteration_mode_snapshot_sha256"
    present = {key for key in (*ITERATION_SNAPSHOT_KEYS, digest_key) if key in meta}
    if not present:
        return  # Existing issues predate the snapshot contract.
    expected_keys = set(ITERATION_SNAPSHOT_KEYS) | {digest_key}
    if present != expected_keys:
        sys.exit("REFUSED: issue has a partial iteration-mode dispatch snapshot")
    snapshot = {key: meta[key] for key in ITERATION_SNAPSHOT_KEYS}
    if meta[digest_key] != _canonical_sha256(snapshot):
        sys.exit("REFUSED: issue iteration-mode dispatch snapshot digest is invalid")
    selected = snapshot["iteration_mode_selected"]
    effective = snapshot["iteration_mode_effective"]
    fallback = snapshot["human_unavailable_fallback"]
    status = snapshot["human_checkin_lease_status"]
    if selected not in ITERATION_MODES or effective not in ITERATION_MODES:
        sys.exit("REFUSED: issue iteration-mode dispatch snapshot uses an unknown mode")
    if fallback not in HUMAN_UNAVAILABLE_FALLBACKS:
        sys.exit("REFUSED: issue iteration-mode dispatch snapshot uses an unknown fallback")
    decision_at = _parse_utc(
        snapshot["iteration_mode_decision_at"], "iteration_mode_decision_at"
    )
    lease = snapshot["human_checkin_lease_minutes"]
    expiry = snapshot["human_checkin_lease_expires_at"]
    if selected == "autonomous":
        valid = effective == "autonomous" and status == "not_applicable" and lease == "none" and expiry == "none"
    elif status == "active":
        valid = effective == "human_guided" and lease != "none" and expiry != "none"
    else:
        valid = (
            status == "expired"
            and effective == "autonomous"
            and fallback == "autonomous_review"
            and lease != "none"
            and expiry != "none"
        )
    if lease != "none":
        try:
            parsed_lease = int(lease)
        except (TypeError, ValueError):
            parsed_lease = None
        _validate_lease_minutes(parsed_lease, required=True)
    expiry_at = None
    if expiry != "none":
        expiry_at = _parse_utc(expiry, "human_checkin_lease_expires_at")
    if selected == "human_guided" and expiry_at is not None:
        if status == "active" and expiry_at <= decision_at:
            valid = False
        if status == "expired" and expiry_at > decision_at:
            valid = False
    if not valid:
        sys.exit("REFUSED: issue iteration-mode dispatch snapshot is contradictory")


def _ui_mode():
    """UI channel from .pm/config.json: linear | interactive | file. Default: linear iff
    LINEAR_API_KEY is set, else file. Linear-specific mirroring only runs in 'linear' mode."""
    try:
        with open(os.path.join(pmroot(), "config.json")) as stream:
            config = json.load(stream)
        return config.get("ui_mode") or (
            "linear" if os.environ.get("LINEAR_API_KEY") else "file"
        )
    except Exception:
        return "linear" if os.environ.get("LINEAR_API_KEY") else "file"


def m_path(mid):
    return os.path.join(pmroot(), "milestones", f"{mid}.md")


def i_path(mid, iid):
    return os.path.join(pmroot(), "issues", mid, f"{iid}.md")


def all_issues(mid=None):
    base = os.path.join(pmroot(), "issues")
    out = []
    for dp, _, files in os.walk(base):
        if mid and os.path.basename(dp) != mid:
            continue
        for f in sorted(files):
            if f.endswith(".md"):
                meta, _ = read(os.path.join(dp, f))
                out.append(meta)
    return out


def all_milestones():
    base = os.path.join(pmroot(), "milestones")
    out = []
    if os.path.isdir(base):
        for f in sorted(os.listdir(base)):
            if f.endswith(".md"):
                meta, _ = read(os.path.join(base, f))
                out.append(meta)
    return sorted(out, key=lambda m: int(m.get("order", "0") or 0))


def log(msg):
    p = os.path.join(pmroot(), "log.md")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as stream:
            prev = stream.read()
    else:
        prev = "# Evolution / rework log\n"
    with open(p, "w", encoding="utf-8") as stream:
        stream.write(prev + f"- {msg}\n")


# ---------- commands ----------
def cmd_init(a):
    # ui_mode: linear (a Linear workspace is wired) | interactive (human in native agent session) | file
    # (headless, .pm board + .pm/ask/). Default: linear iff LINEAR_API_KEY is set, else file.
    ui = getattr(a, "ui_mode", None) or ("linear" if os.environ.get("LINEAR_API_KEY") else "file")
    cfg = os.path.join(pmroot(), "config.json")
    if not os.path.lexists(cfg):
        mode = getattr(a, "iteration_mode", None) or "autonomous"
        fallback = getattr(a, "human_unavailable_fallback", None) or "pause"
        iteration = _new_iteration_fields(
            mode,
            getattr(a, "human_checkin_lease_minutes", None),
            fallback,
        )
        config = {"debug": bool(a.debug), "ui_mode": ui, **iteration}
        _atomic_text(cfg, json.dumps(config, indent=2, sort_keys=True) + "\n")
    else:
        _, config, legacy = _load_pm_config()
        requested_mode = getattr(a, "iteration_mode", None)
        requested_lease = getattr(a, "human_checkin_lease_minutes", None)
        requested_fallback = getattr(a, "human_unavailable_fallback", None)
        if requested_mode is not None or requested_lease is not None or requested_fallback is not None:
            sys.exit(
                "REFUSED: .pm/config.json already exists; use `pm.py iteration-mode set` "
                "for an explicit atomic mode transition"
            )
        # Loading is intentional even when init is otherwise idempotent: malformed or
        # contradictory mode fields must never be silently accepted.
        _iteration_mode_state(config, legacy=legacy)
    # The config read/create above first proves that .pm and config.json are plain no-follow
    # control paths; do not create board children through an unvalidated .pm parent.
    os.makedirs(os.path.join(pmroot(), "milestones"), exist_ok=True)
    os.makedirs(os.path.join(pmroot(), "issues"), exist_ok=True)
    _plain_repo_directory(".pm/tasks", label="PM task-workspace root", create=True)
    goal = os.path.join(pmroot(), "goal.md")
    if not os.path.exists(goal):
        with open(goal, "w") as stream:
            stream.write(
                "# Goal Tracker\n\n## Ultimate Goal (IMMUTABLE — set once)\n<one sentence>\n\n"
                "## Acceptance Criteria (IMMUTABLE)\n- AC-1: <verifiable>\n\n"
                "## Active / Done / Deferred (mutable)\n\n## Plan Evolution Log\n"
            )
    configured, selected, legacy = _load_pm_config()
    state = _iteration_mode_state(selected, legacy=legacy)
    print(
        f"initialized .pm/ (debug={configured.get('debug')}, "
        f"ui_mode={configured.get('ui_mode') or ui}, "
        f"iteration_mode={state['selected_iteration_mode']}, "
        f"effective={state['effective_iteration_mode'] or 'paused'})"
    )


def cmd_iteration_mode(a):
    original, config, legacy = _load_pm_config()
    action = a.action
    if action == "show":
        if a.mode is not None or a.human_checkin_lease_minutes is not None or a.human_unavailable_fallback is not None:
            sys.exit("REFUSED: iteration-mode show takes no mode, lease, or fallback mutation")
        print(json.dumps(_iteration_mode_state(config, legacy=legacy), sort_keys=True))
        return

    fallback = a.human_unavailable_fallback or config["human_unavailable_fallback"]
    if action == "set":
        if a.mode is None:
            sys.exit("REFUSED: iteration-mode set requires autonomous or human_guided")
        fields = _new_iteration_fields(
            a.mode,
            a.human_checkin_lease_minutes,
            fallback,
        )
    elif action == "renew":
        if a.mode is not None or a.human_unavailable_fallback is not None:
            sys.exit("REFUSED: renew changes only the active human_guided lease; use set for mode/fallback")
        if config["iteration_mode"] != "human_guided":
            sys.exit("REFUSED: only selected human_guided mode has a lease to renew")
        lease = a.human_checkin_lease_minutes
        if lease is None:
            lease = config["human_checkin_lease_minutes"]
        fields = _new_iteration_fields("human_guided", lease, fallback)
    else:
        raise RuntimeError(f"internal: unknown iteration-mode action {action!r}")

    updated = dict(original)
    updated.update(fields)
    _atomic_text(
        os.path.join(pmroot(), "config.json"),
        json.dumps(updated, indent=2, sort_keys=True) + "\n",
    )
    _, persisted, persisted_legacy = _load_pm_config()
    print(json.dumps(_iteration_mode_state(persisted, legacy=persisted_legacy), sort_keys=True))


def cmd_milestone(a):
    if a.action == "add":
        if not a.acceptance.strip():
            sys.exit("--acceptance must be a non-empty executable or human-judged criterion")
        if not a.review_reason.strip():
            sys.exit("--review-reason must be non-empty")
        if os.path.exists(m_path(a.id)) or os.path.islink(m_path(a.id)):
            sys.exit(f"milestone {a.id} already exists; use the board's explicit update/reopen path")
        write(m_path(a.id), {"id": a.id, "title": a.title, "status": "todo",
                             "order": str(a.order), "depends_on": (a.depends.split(",") if a.depends else []),
                             "review_policy": a.review_policy, "review_reason": a.review_reason},
              f"## Description\n\n## Acceptance Criteria\n\n{a.acceptance.strip()}\n")
        print(f"milestone {a.id} added (review={a.review_policy})")
    elif a.action == "list":
        for m in all_milestones():
            print(f"  [{m.get('status'):>11}] {m['id']} (order {m.get('order')}, "
                  f"review={m.get('review_policy', 'independent')}) — {m.get('title')}")


def cmd_issue(a):
    _task_issue_id(a.id)
    if not a.acceptance.strip():
        sys.exit("--acceptance must be a non-empty executable or human-judged criterion")
    if not a.lane_reason.strip():
        sys.exit("--lane-reason must be non-empty")
    if not a.promotion_triggers.strip():
        sys.exit("--promotion-triggers must be non-empty (use 'none' when no promotion is expected)")
    if not a.review_reason.strip():
        sys.exit("--review-reason must be non-empty")
    if a.review_policy == "machine-gated" and a.lane != "workflow":
        sys.exit("review-policy machine-gated requires --lane workflow")
    if a.lane == "direct" and not a.direct_scope:
        sys.exit("direct coordination issues require --direct-scope " + "|".join(DIRECT_SCOPES))
    if a.lane != "direct" and a.direct_scope:
        sys.exit("--direct-scope is valid only with --lane direct")
    if a.estimated_model_calls < 0 or a.max_model_calls < 0:
        sys.exit("model-call budgets must be non-negative")
    if a.estimated_model_calls > a.max_model_calls:
        sys.exit("--estimated-model-calls cannot exceed --max-model-calls")
    if a.estimated_model_calls < 1 or a.max_model_calls < 1:
        sys.exit("every issue needs estimated and maximum model calls >= 1, including a reserved terminal review")
    destination = i_path(a.milestone, a.id)
    existing = _find_issue(a.id)
    if existing:
        sys.exit(
            f"issue id {a.id!r} must be globally unique; it already exists at "
            f"{os.path.relpath(existing, os.path.dirname(pmroot()))}"
        )
    if os.path.exists(destination) or os.path.islink(destination):
        sys.exit(f"issue {a.id} already exists in {a.milestone}; use the board's explicit update/reopen path")
    workspace_paths = _task_workspace_relatives(a.id)
    repository_root = os.path.abspath(os.path.dirname(pmroot()))
    if os.path.lexists(os.path.join(repository_root, *workspace_paths["base"].split("/"))):
        sys.exit(
            f"REFUSED: canonical task workspace for issue {a.id!r} already exists without "
            "that issue; repair the orphaned PM state before creating it"
        )
    dispatch = _dispatch_metadata(a)
    iteration = _iteration_issue_snapshot()
    meta = {
        "id": a.id, "milestone": a.milestone, "depth": str(a.depth), "status": "todo",
        "dispatch_lane": a.lane, "dispatch_reason": a.lane_reason,
        "estimated_model_calls": str(a.estimated_model_calls),
        "max_model_calls": str(a.max_model_calls),
        "promotion_triggers": a.promotion_triggers,
        "direct_scope": a.direct_scope,
        "review_policy": a.review_policy, "review_reason": a.review_reason,
        "depends_on": (a.depends.split(",") if a.depends else []),
        "parallel": str(bool(a.parallel)).lower(), "linear_id": "",
        "task_workspace": f"tasks/{a.id}",
    }
    meta.update(dispatch)
    meta.update(iteration)
    issue_body = f"## Description\n\n## Acceptance Criteria\n\n{a.acceptance.strip()}\n"
    issue_raw = dump(meta, issue_body).encode("utf-8")
    # Refuse an obviously unwritable target before publishing the issue. The rollback below still
    # covers failures after publication (for example a process interruption during workspace init).
    issue_parent = os.path.dirname(destination)
    _existing_plain_ancestor(issue_parent, repository_root, label="PM issue parent")
    tasks_parent = os.path.join(repository_root, ".pm", "tasks")
    _existing_plain_ancestor(tasks_parent, repository_root, label="PM task workspace parent")
    write(
        destination,
        meta,
        issue_body,
    )
    try:
        _initialize_task_workspace(a.id)
    except BaseException:
        _rollback_new_issue(
            os.path.relpath(destination, repository_root).replace(os.sep, "/"),
            issue_raw,
            a.id,
        )
        raise
    print(f"issue {a.id} added to {a.milestone} (lane={a.lane}, review={a.review_policy}, "
          f"max_calls={a.max_model_calls}, depth={a.depth}, "
          f"selected_mode={iteration['iteration_mode_selected']}, "
          f"effective_mode={iteration['iteration_mode_effective']}, "
          f"lease={iteration['human_checkin_lease_status']})")


def _find_issue(iid):
    base = os.path.join(pmroot(), "issues")
    if not os.path.isdir(base):
        return None
    matches = []
    for m in sorted(os.listdir(base)):
        p = i_path(m, iid)
        if os.path.exists(p):
            matches.append(p)
    if len(matches) > 1:
        sys.exit(
            f"REFUSED: duplicate issue id {iid!r} exists in multiple milestones; repair the "
            "canonical board before using an id-only command"
        )
    return matches[0] if matches else None


def _canonical_sha256(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _issue_snapshot_sha256(meta, body):
    immutable = {
        key: value for key, value in meta.items() if key not in TASK_MUTABLE_ISSUE_KEYS
    }
    return _canonical_sha256({"meta": immutable, "body": body})


def _repo_regular_file(relative, *, prefix=None, prefixes=None, label, exclusive=False):
    if not isinstance(relative, str) or not relative.strip() or os.path.isabs(relative):
        sys.exit(f"REFUSED: {label} must be a repository-relative path")
    normalized = os.path.normpath(relative)
    posix = normalized.replace(os.sep, "/")
    allowed = []
    if prefix:
        allowed.append(prefix.replace(os.sep, "/").rstrip("/"))
    if prefixes:
        allowed.extend(item.replace(os.sep, "/").rstrip("/") for item in prefixes)
    if not allowed:
        raise RuntimeError("internal: repository file prefix required")
    if posix == ".." or posix.startswith("../") or not any(
        posix == item or posix.startswith(item + "/") for item in allowed
    ):
        shown = ", ".join(item + "/" for item in allowed)
        sys.exit(f"REFUSED: {label} must be stored under {shown}")
    root = os.path.abspath(os.path.dirname(pmroot()))
    path = os.path.abspath(os.path.join(root, normalized))
    if os.path.commonpath([root, path]) != root:
        sys.exit(f"REFUSED: {label} must be a regular, non-symlink repository file")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        sys.exit(f"REFUSED: {label} requires no-follow file-descriptor support")

    parts = normalized.split(os.sep)
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    leaf_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | close_on_exec
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
    )
    directory_fd = None
    leaf_fd = None
    try:
        directory_fd = os.open(root, directory_flags)
        for component in parts[:-1]:
            before = os.stat(component, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                sys.exit(
                    f"REFUSED: {label} must be a regular, non-symlink repository file"
                )
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            opened = os.fstat(next_fd)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                os.close(next_fd)
                sys.exit(f"REFUSED: {label} path changed while it was being opened")
            os.close(directory_fd)
            directory_fd = next_fd

        before = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            sys.exit(f"REFUSED: {label} must be a regular, non-symlink repository file")
        leaf_fd = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
        opened = os.fstat(leaf_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            sys.exit(f"REFUSED: {label} path changed while it was being opened")
        if exclusive and opened.st_nlink != 1:
            sys.exit(f"REFUSED: {label} must be an exclusive regular file, not a hardlink")
        chunks = []
        while True:
            chunk = os.read(leaf_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        after = os.fstat(leaf_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
            any(getattr(opened, field) != getattr(after, field) for field in stable_fields)
            or len(raw) != after.st_size
            or (exclusive and after.st_nlink != 1)
        ):
            sys.exit(f"REFUSED: {label} changed while it was being read")
    except OSError as error:
        sys.exit(f"REFUSED: cannot read {label} without following links: {error}")
    finally:
        if leaf_fd is not None:
            os.close(leaf_fd)
        if directory_fd is not None:
            os.close(directory_fd)
    return posix, path, raw


def _load_json_regular(raw, label):
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        sys.exit(f"REFUSED: cannot read {label}: {error}")
    if not isinstance(value, dict):
        sys.exit(f"REFUSED: {label} must contain one JSON object")
    return raw, value


def _exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        sys.exit(f"REFUSED: {label} must contain exactly the keys {', '.join(keys)}")


def _require_sha256_token(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        sys.exit(f"REFUSED: {label} must be a sha256:<64hex> token")
    return value


def _bytes_sha256_token(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _task_issue_id(value):
    if not isinstance(value, str) or not TASK_WORKSPACE_ID.fullmatch(value):
        sys.exit(
            "REFUSED: task workspace issue id must be a safe direct name containing only "
            "letters, digits, dot, underscore, or hyphen"
        )
    return value


def _plain_repo_relative(value, label):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or os.path.isabs(value)
    ):
        sys.exit(f"REFUSED: {label} must be a plain repository-relative path")
    parts = value.split(os.sep)
    if any(part in ("", ".", "..") for part in parts):
        sys.exit(f"REFUSED: {label} must be a plain repository-relative path")
    normalized = os.path.normpath(value)
    if normalized != value or normalized == ".git" or normalized.startswith(".git" + os.sep):
        sys.exit(f"REFUSED: {label} must be a plain repository-relative path outside .git")
    return normalized, normalized.replace(os.sep, "/")


def _open_plain_repo_directory(relative, *, label, create=False, exclusive_create=False):
    """Open one repository-relative directory without following any named component."""
    normalized, posix = _plain_repo_relative(relative, label)
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        sys.exit(f"REFUSED: {label} requires no-follow file-descriptor support")
    root = os.path.abspath(os.path.dirname(pmroot()))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    current_fd = None
    try:
        current_fd = os.open(root, flags)
        parts = normalized.split(os.sep)
        for index, component in enumerate(parts):
            made = False
            try:
                before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    sys.exit(f"REFUSED: {label} does not exist as a plain directory")
                os.mkdir(component, CONTROL_DIRECTORY_MODE, dir_fd=current_fd)
                before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
                made = True
            if index == len(parts) - 1 and exclusive_create and not made:
                sys.exit(f"REFUSED: {label} already exists")
            if not stat.S_ISDIR(before.st_mode):
                sys.exit(f"REFUSED: {label} must be a plain non-symlink directory")
            next_fd = os.open(component, flags, dir_fd=current_fd)
            opened = os.fstat(next_fd)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                os.close(next_fd)
                sys.exit(f"REFUSED: {label} changed while it was being opened")
            os.close(current_fd)
            current_fd = next_fd
    except OSError as error:
        if current_fd is not None:
            os.close(current_fd)
        sys.exit(f"REFUSED: cannot inspect {label} without following links: {error}")
    except BaseException:
        if current_fd is not None:
            os.close(current_fd)
        raise
    return current_fd, posix


def _plain_repo_directory(relative, *, label, create=False, exclusive_create=False):
    """Validate or create one repository-relative directory without following links."""
    directory_fd, posix = _open_plain_repo_directory(
        relative, label=label, create=create, exclusive_create=exclusive_create
    )
    os.close(directory_fd)
    return posix


def _open_plain_parent(relative, label):
    normalized, _posix = _plain_repo_relative(relative, label)
    parts = normalized.split(os.sep)
    root = os.path.abspath(os.path.dirname(pmroot()))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    if len(parts) == 1:
        return os.open(root, flags), parts[0]
    return _open_plain_repo_directory(
        os.sep.join(parts[:-1]), label=f"{label} parent"
    )[0], parts[-1]


def _unlink_owned_regular(relative, expected, *, label):
    """Remove one exact regular file, refusing aliases or bytes changed since the check."""
    parent_fd = leaf_fd = None
    try:
        parent_fd, leaf = _open_plain_parent(relative, label)
        try:
            before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            return False
        leaf_fd = os.open(
            leaf,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_fd,
        )
        raw = b""
        chunks = []
        while True:
            chunk = os.read(leaf_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        after = os.fstat(leaf_fd)
        stable = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if raw != expected or any(
            getattr(before, field) != getattr(after, field) for field in stable
        ):
            return False
        os.unlink(leaf, dir_fd=parent_fd)
        return True
    except OSError:
        return False
    finally:
        if leaf_fd is not None:
            with contextlib.suppress(OSError):
                os.close(leaf_fd)
        if parent_fd is not None:
            with contextlib.suppress(OSError):
                os.close(parent_fd)


def _remove_empty_directory(relative, *, label):
    parent_fd = child_fd = None
    try:
        parent_fd, leaf = _open_plain_parent(relative, label)
        try:
            before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return True
        if not stat.S_ISDIR(before.st_mode):
            return False
        child_fd = os.open(
            leaf,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        if os.listdir(child_fd):
            return False
        os.rmdir(leaf, dir_fd=parent_fd)
        return True
    except OSError:
        return False
    finally:
        if child_fd is not None:
            with contextlib.suppress(OSError):
                os.close(child_fd)
        if parent_fd is not None:
            with contextlib.suppress(OSError):
                os.close(parent_fd)


def _existing_plain_ancestor(path, repository_root, *, label):
    candidate = os.path.abspath(path)
    root = os.path.abspath(repository_root)
    while not os.path.lexists(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate or os.path.commonpath((root, parent)) != root:
            sys.exit(f"REFUSED: {label} has no repository-owned parent")
        candidate = parent
    relative = os.path.relpath(candidate, root).replace(os.sep, "/")
    _plain_repo_directory(relative, label=label)
    if not os.access(candidate, os.W_OK | os.X_OK):
        sys.exit(f"REFUSED: {label} is not writable")
    return candidate


def _task_workspace_relatives(issue_id):
    issue_id = _task_issue_id(issue_id)
    base = f".pm/tasks/{issue_id}"
    return {
        "base": base,
        "task": f"{base}/task.json",
        "events": f"{base}/events.jsonl",
        "experiments": f"{base}/experiments",
        "lock": f"{base}/.writer.lock",
    }


def _rollback_new_task_workspace(issue_id):
    """Best-effort cleanup of only the exact empty state this command can create."""
    paths = _task_workspace_relatives(issue_id)
    root = os.path.abspath(os.path.dirname(pmroot()))
    task = {
        "schema": TASK_WORKSPACE_SCHEMA,
        **_issue_task_binding(issue_id),
        "eventLog": paths["events"],
        "experimentRoot": paths["experiments"],
    }
    task_raw = (json.dumps(task, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    _unlink_owned_regular(paths["events"], b"", label="new task workspace event log")
    _unlink_owned_regular(paths["task"], task_raw, label="new task workspace metadata")
    _remove_empty_directory(paths["experiments"], label="new task workspace experiment root")
    _remove_empty_directory(paths["base"], label="new task workspace")


def _rollback_new_issue(issue_relative, issue_raw, issue_id):
    with contextlib.suppress(BaseException):
        _rollback_new_task_workspace(issue_id)
    _unlink_owned_regular(issue_relative, issue_raw, label="new PM issue")


def _issue_task_binding(issue_id):
    issue_id = _task_issue_id(issue_id)
    issue_path = _find_issue(issue_id)
    if issue_path is None:
        sys.exit(f"REFUSED: issue {issue_id!r} does not exist")
    root = os.path.abspath(os.path.dirname(pmroot()))
    relative = os.path.relpath(os.path.abspath(issue_path), root).replace(os.sep, "/")
    _posix, _absolute, raw = _repo_regular_file(
        relative,
        prefix=".pm/issues",
        label="task workspace issue",
        exclusive=True,
    )
    try:
        meta, body = parse(raw.decode("utf-8"))
    except UnicodeDecodeError as error:
        sys.exit(f"REFUSED: task workspace issue is not UTF-8: {error}")
    if meta.get("id") != issue_id:
        sys.exit("REFUSED: task workspace issue id does not match its canonical issue file")
    _validate_iteration_snapshot(meta)
    snapshot = meta.get("iteration_mode_snapshot_sha256")
    if snapshot is None:
        snapshot = _legacy_iteration_issue_snapshot()["iteration_mode_snapshot_sha256"]
    _require_sha256_token(snapshot, "task workspace iteration-mode snapshot")
    expected_pointer = f"tasks/{issue_id}"
    if "task_workspace" in meta and meta["task_workspace"] != expected_pointer:
        sys.exit("REFUSED: issue task_workspace pointer does not match its canonical task workspace")
    return {
        "issueId": issue_id,
        "issuePath": relative,
        "issueSnapshotSha256": _issue_snapshot_sha256(meta, body),
        "iterationModeSnapshotSha256": snapshot,
    }


def _initialize_task_workspace(issue_id):
    binding = _issue_task_binding(issue_id)
    paths = _task_workspace_relatives(issue_id)
    _plain_repo_directory(
        paths["base"], label="task workspace", create=True
    )
    _plain_repo_directory(
        paths["experiments"],
        label="task workspace experiment root",
        create=True,
    )
    task = {
        "schema": TASK_WORKSPACE_SCHEMA,
        **binding,
        "eventLog": paths["events"],
        "experimentRoot": paths["experiments"],
    }

    root = os.path.abspath(os.path.dirname(pmroot()))

    def existing(relative, label):
        target = os.path.join(root, *relative.split("/"))
        if not os.path.lexists(target):
            return None
        return _repo_regular_file(
            relative,
            prefix=paths["base"],
            label=label,
            exclusive=True,
        )[2]

    task_raw = existing(paths["task"], "task workspace metadata")
    if task_raw is None:
        _atomic_text(
            os.path.join(root, *paths["task"].split("/")),
            json.dumps(task, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )
    else:
        _unused, stored_task = _load_json_regular(task_raw, "task workspace metadata")
        _exact_keys(stored_task, TASK_WORKSPACE_KEYS, "task workspace metadata")
        if stored_task != task:
            sys.exit(
                "REFUSED: task workspace metadata does not match its issue; "
                "repair the workspace contents before retrying initialization"
            )

    event_raw = existing(paths["events"], "task workspace event log")
    if event_raw is None:
        _atomic_text(os.path.join(root, *paths["events"].split("/")), "")
    elif event_raw:
        _load_task_events(event_raw, issue_id)

    # Initialization is deliberately idempotent: a process interrupted after creating either
    # directory or control file can retry, while any conflicting bytes still fail closed.
    return task


def _task_hash_chain_refusal(detail):
    sys.exit(f"REFUSED: task event hash chain is invalid: {detail}")


def _load_task_events(raw, issue_id):
    if not raw:
        return []
    lines = raw.splitlines(keepends=True)
    events = []
    previous = TASK_EVENT_GENESIS
    for expected_sequence, line in enumerate(lines, start=1):
        if not line.endswith(b"\n") or line in (b"\n", b"\r\n"):
            _task_hash_chain_refusal("each event must be one non-empty newline-terminated record")
        try:
            event = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            _task_hash_chain_refusal(f"event {expected_sequence} is not canonical JSON: {error}")
        if not isinstance(event, dict) or set(event) != set(TASK_EVENT_KEYS):
            _task_hash_chain_refusal(f"event {expected_sequence} has a non-canonical shape")
        canonical = (
            json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        if line != canonical:
            _task_hash_chain_refusal(f"event {expected_sequence} is not canonically serialized")
        if event["schema"] != TASK_EVENT_SCHEMA or event["issueId"] != issue_id:
            _task_hash_chain_refusal(f"event {expected_sequence} is bound to the wrong task")
        if isinstance(event["sequence"], bool) or event["sequence"] != expected_sequence:
            _task_hash_chain_refusal(f"event {expected_sequence} has the wrong sequence")
        if event["kind"] not in TASK_EVENT_KINDS:
            _task_hash_chain_refusal(f"event {expected_sequence} has an unknown kind")
        if (
            not isinstance(event["summary"], str)
            or not event["summary"].strip()
            or event["summary"] != event["summary"].strip()
        ):
            _task_hash_chain_refusal(f"event {expected_sequence} has an invalid summary")
        if event["experimentPath"] is not None:
            try:
                _normalized, experiment_posix = _plain_repo_relative(
                    event["experimentPath"], "task event experiment path"
                )
            except SystemExit as error:
                _task_hash_chain_refusal(str(error))
            if experiment_posix != event["experimentPath"]:
                _task_hash_chain_refusal(f"event {expected_sequence} has a non-canonical experiment path")
            experiment_root = f".pm/tasks/{issue_id}/experiments"
            if not (
                experiment_posix == experiment_root
                or experiment_posix.startswith(experiment_root + "/")
            ):
                _task_hash_chain_refusal(
                    f"event {expected_sequence} experiment path is outside its task-owned root"
                )
        if not isinstance(event["evidence"], list):
            _task_hash_chain_refusal(f"event {expected_sequence} evidence must be a list")
        if event["kind"] in TASK_EVIDENCE_REQUIRED_KINDS and not event["evidence"]:
            _task_hash_chain_refusal(f"event {expected_sequence} lacks required evidence")
        seen_paths = set()
        for evidence in event["evidence"]:
            if not isinstance(evidence, dict) or set(evidence) != set(TASK_EVIDENCE_KEYS):
                _task_hash_chain_refusal(f"event {expected_sequence} has malformed evidence")
            try:
                _normalized, evidence_posix = _plain_repo_relative(
                    evidence["path"], "task event evidence path"
                )
            except SystemExit as error:
                _task_hash_chain_refusal(str(error))
            if evidence_posix != evidence["path"] or evidence_posix in seen_paths:
                _task_hash_chain_refusal(f"event {expected_sequence} has duplicate or non-canonical evidence")
            seen_paths.add(evidence_posix)
            if (
                isinstance(evidence["bytes"], bool)
                or not isinstance(evidence["bytes"], int)
                or evidence["bytes"] < 0
            ):
                _task_hash_chain_refusal(f"event {expected_sequence} has an invalid evidence size")
            try:
                _require_sha256_token(evidence["sha256"], "task event evidence digest")
            except SystemExit as error:
                _task_hash_chain_refusal(str(error))
            try:
                current = _task_evidence(issue_id, evidence["path"])
            except SystemExit as error:
                _task_hash_chain_refusal(str(error))
            if current != evidence:
                _task_hash_chain_refusal(
                    f"event {expected_sequence} evidence bytes changed after recording"
                )
        if [item["path"] for item in event["evidence"]] != sorted(seen_paths):
            _task_hash_chain_refusal(f"event {expected_sequence} evidence is not path-sorted")
        try:
            _parse_utc(event["recordedAt"], "task event recordedAt")
        except SystemExit as error:
            _task_hash_chain_refusal(str(error))
        if event["previousEventSha256"] != previous:
            _task_hash_chain_refusal(f"event {expected_sequence} does not reference its predecessor")
        expected_digest = _canonical_sha256(
            {key: event[key] for key in TASK_EVENT_KEYS if key != "eventSha256"}
        )
        if event["eventSha256"] != expected_digest:
            _task_hash_chain_refusal(f"event {expected_sequence} digest does not match its content")
        previous = event["eventSha256"]
        events.append(event)
    return events


def _load_task_workspace(issue_id):
    binding = _issue_task_binding(issue_id)
    paths = _task_workspace_relatives(issue_id)
    root = os.path.abspath(os.path.dirname(pmroot()))
    if not os.path.lexists(os.path.join(root, *paths["base"].split("/"))):
        sys.exit(
            f"REFUSED: issue {issue_id!r} has no task workspace; run "
            f"`pm.py task-workspace init {issue_id}` explicitly"
        )
    _plain_repo_directory(paths["base"], label="task workspace")
    _task_posix, _task_path, task_raw = _repo_regular_file(
        paths["task"], prefix=paths["base"], label="task workspace metadata", exclusive=True
    )
    _unused, task = _load_json_regular(task_raw, "task workspace metadata")
    _exact_keys(task, TASK_WORKSPACE_KEYS, "task workspace metadata")
    expected_task = {
        "schema": TASK_WORKSPACE_SCHEMA,
        **binding,
        "eventLog": paths["events"],
        "experimentRoot": paths["experiments"],
    }
    if task != expected_task:
        sys.exit(
            "REFUSED: task workspace metadata does not match its immutable issue snapshot "
            "or iteration snapshot"
        )
    _plain_repo_directory(paths["experiments"], label="task workspace experiment root")
    _event_posix, _event_path, event_raw = _repo_regular_file(
        paths["events"], prefix=paths["base"], label="task workspace event log", exclusive=True
    )
    return task, _load_task_events(event_raw, issue_id), paths


def _existing_task_workspace_state(issue_id):
    """Capture the exact task metadata that must be rebound during an issue migration."""
    paths = _task_workspace_relatives(issue_id)
    root = os.path.abspath(os.path.dirname(pmroot()))
    if not os.path.lexists(os.path.join(root, *paths["base"].split("/"))):
        return None
    _plain_repo_directory(paths["base"], label="task workspace")
    _posix, task_path, task_raw = _repo_regular_file(
        paths["task"], prefix=paths["base"], label="task workspace metadata", exclusive=True
    )
    _unused, stored = _load_json_regular(task_raw, "task workspace metadata")
    _exact_keys(stored, TASK_WORKSPACE_KEYS, "task workspace metadata")
    current_binding = _issue_task_binding(issue_id)
    if (
        stored.get("schema") != TASK_WORKSPACE_SCHEMA
        or stored.get("issueId") != issue_id
        or stored.get("issuePath") != current_binding["issuePath"]
        or stored.get("eventLog") != paths["events"]
        or stored.get("experimentRoot") != paths["experiments"]
    ):
        sys.exit("REFUSED: task workspace metadata has invalid migration bindings")
    return task_path, task_raw


def _rebound_task_workspace_raw(issue_id):
    """Build task metadata for the issue bytes currently published on disk."""
    paths = _task_workspace_relatives(issue_id)
    binding = _issue_task_binding(issue_id)
    task = {
        "schema": TASK_WORKSPACE_SCHEMA,
        **binding,
        "eventLog": paths["events"],
        "experimentRoot": paths["experiments"],
    }
    return (json.dumps(task, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _open_task_workspace_directory(issue_id):
    paths = _task_workspace_relatives(issue_id)
    directory_fd, _posix = _open_plain_repo_directory(
        paths["base"], label="task workspace"
    )
    return directory_fd


@contextlib.contextmanager
def _task_workspace_lock(issue_id):
    paths = _task_workspace_relatives(issue_id)
    root = os.path.abspath(os.path.dirname(pmroot()))
    workspace = os.path.join(root, *paths["base"].split("/"))
    if not os.path.lexists(workspace):
        sys.exit(
            f"REFUSED: issue {issue_id!r} has no task workspace; run "
            f"`pm.py task-workspace init {issue_id}` explicitly"
        )
    if fcntl is None:
        sys.exit("REFUSED: task workspace single-writer locking requires POSIX fcntl support")
    directory_fd = lock_fd = None
    locked = False
    try:
        directory_fd = _open_task_workspace_directory(issue_id)
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            lock_fd = os.open(".writer.lock", flags, CONTROL_FILE_MODE, dir_fd=directory_fd)
        except OSError as error:
            sys.exit(f"REFUSED: cannot open task workspace writer lock: {error}")
        opened = os.fstat(lock_fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            sys.exit("REFUSED: task workspace writer lock must be a single-link regular file")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                sys.exit("REFUSED: another writer currently owns this task workspace")
            sys.exit(f"REFUSED: cannot lock task workspace: {error}")
        locked = True
    except OSError as error:
        sys.exit(f"REFUSED: cannot lock task workspace without following links: {error}")
    try:
        yield
    finally:
        if locked and lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        if lock_fd is not None:
            os.close(lock_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _task_evidence(issue_id, path):
    normalized, posix = _plain_repo_relative(path, "task evidence file")
    paths = _task_workspace_relatives(issue_id)
    protected = {paths["task"], paths["events"], paths["lock"]}
    if posix in protected or (
        posix.startswith(paths["base"] + "/")
        and not posix.startswith(paths["experiments"] + "/")
    ):
        sys.exit("REFUSED: task evidence cannot bind mutable task workspace control files")
    task_parts = posix.split("/")
    if (
        len(task_parts) >= 4
        and task_parts[0:2] == [".pm", "tasks"]
        and task_parts[-1] in {"task.json", "events.jsonl", ".writer.lock"}
    ):
        sys.exit("REFUSED: task evidence cannot bind another task workspace control file")
    prefix = normalized.split(os.sep, 1)[0]
    recorded_path, _absolute, raw = _repo_regular_file(
        normalized,
        prefix=prefix,
        label="task evidence file",
        exclusive=True,
    )
    return {
        "path": recorded_path,
        "bytes": len(raw),
        "sha256": _bytes_sha256_token(raw),
    }


def cmd_task_workspace(a):
    issue_id = _task_issue_id(a.id)
    if a.action == "init":
        if a.kind is not None or a.summary or a.experiment_path or a.files:
            sys.exit("REFUSED: task-workspace init does not accept record fields")
        task = _initialize_task_workspace(issue_id)
        print(json.dumps(task, sort_keys=True, ensure_ascii=False))
        return
    if a.action == "show":
        if a.kind is not None or a.summary or a.experiment_path or a.files:
            sys.exit("REFUSED: task-workspace show is read-only and does not accept record fields")
        task, events, _paths = _load_task_workspace(issue_id)
        print(json.dumps({"task": task, "events": events}, sort_keys=True, ensure_ascii=False))
        return
    if a.kind is None:
        sys.exit("REFUSED: task-workspace record requires --kind")
    summary = a.summary.strip()
    if not summary:
        sys.exit("REFUSED: task-workspace record requires a non-empty --summary")
    if a.kind in TASK_EVIDENCE_REQUIRED_KINDS and not a.files:
        sys.exit(f"REFUSED: {a.kind} events require at least one actual evidence file")
    with _task_workspace_lock(issue_id):
        _task, events, paths = _load_task_workspace(issue_id)
        experiment_path = None
        if a.experiment_path:
            _normalized, experiment_path = _plain_repo_relative(
                a.experiment_path, "task experiment path"
            )
            if not (
                experiment_path == paths["experiments"]
                or experiment_path.startswith(paths["experiments"] + "/")
            ):
                sys.exit(
                    "REFUSED: task experiment path must be inside the task-owned experiment root "
                    f"{paths['experiments']}/"
                )
            _plain_repo_directory(experiment_path, label="task experiment path")
        evidence = [_task_evidence(issue_id, path) for path in a.files]
        evidence.sort(key=lambda item: item["path"])
        if len({item["path"] for item in evidence}) != len(evidence):
            sys.exit("REFUSED: task-workspace record received duplicate evidence paths")
        previous = events[-1]["eventSha256"] if events else TASK_EVENT_GENESIS
        event = {
            "schema": TASK_EVENT_SCHEMA,
            "issueId": issue_id,
            "sequence": len(events) + 1,
            "kind": a.kind,
            "summary": summary,
            "experimentPath": experiment_path,
            "evidence": evidence,
            "recordedAt": _format_utc(_utc_now()),
            "previousEventSha256": previous,
        }
        event["eventSha256"] = _canonical_sha256(event)
        serialized = "".join(
            json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            for item in [*events, event]
        )
        root = os.path.abspath(os.path.dirname(pmroot()))
        _atomic_text(os.path.join(root, *paths["events"].split("/")), serialized)
    print(
        f"task workspace {issue_id} recorded {a.kind} sequence={event['sequence']} "
        f"digest={event['eventSha256']}"
    )


def _require_bare_sha256_hex(value, label):
    # Adapter-owned mapping artifacts carry bare hex, not the PM sha256:<hex> token.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        sys.exit(f"REFUSED: {label} must be bare 64-character lowercase hex")
    return value


def _policy_role_membership(
    policy, *, task_class, posture, semantic_role, review_mode, label, writer_role=None,
):
    """Rederive one dispatch's task class, posture admission, and review pairing from policy.

    Native leaves and workflow leaves share this derivation so no receipt or launch plan can
    assert a task class or posture that the current canonical policy does not grant its role.
    """
    reasoning = policy.get("reasoningPolicy")
    postures = reasoning.get("postures") if isinstance(reasoning, dict) else None
    if not isinstance(postures, dict) or posture not in postures:
        sys.exit(
            f"REFUSED: {label} reasoningPosture {posture!r} is not admitted by the current policy"
        )
    allowed_roles = postures[posture].get("allowedRoles")
    if not isinstance(allowed_roles, list):
        sys.exit("REFUSED: current reasoning posture has no allowedRoles contract")
    capability = policy.get("capabilityPolicy")
    task_classes = capability.get("taskClassRoles") if isinstance(capability, dict) else None
    if not isinstance(task_classes, dict):
        sys.exit("REFUSED: current policy has no taskClassRoles contract")
    if semantic_role not in allowed_roles:
        sys.exit(
            f"REFUSED: {label} semantic role {semantic_role!r} is not admitted by global "
            f"reasoningPosture {posture!r} allowedRoles"
        )
    memberships = sorted(
        class_name
        for class_name, roles in task_classes.items()
        if isinstance(roles, list) and semantic_role in roles
    )
    if len(memberships) != 1:
        sys.exit(
            f"REFUSED: semantic role {semantic_role!r} must belong to exactly one task class"
        )
    if task_class != memberships[0]:
        sys.exit(
            f"REFUSED: {label} taskClass {task_class!r} does not match the current policy "
            f"membership {memberships[0]!r} of semantic role {semantic_role!r}"
        )
    roles = policy.get("roles")
    role = roles.get(semantic_role) if isinstance(roles, dict) else None
    intelligence = role.get("intelligenceClass") if isinstance(role, dict) else None
    if intelligence == "verification" and review_mode != "exact-opposite":
        sys.exit(
            f"REFUSED: {label} semantic role {semantic_role!r} has verification "
            "intelligenceClass, so it may only run as an exact-opposite reviewer of a paired "
            f"writerRole; reviewMode {review_mode!r} would dispatch a reviewer as its own writer"
        )
    if writer_role is None:
        return
    if writer_role not in allowed_roles:
        sys.exit(
            f"REFUSED: {label} writerRole {writer_role!r} is not admitted by global "
            f"reasoningPosture {posture!r} allowedRoles"
        )
    writer_memberships = sorted(
        class_name
        for class_name, class_roles in task_classes.items()
        if isinstance(class_roles, list) and writer_role in class_roles
    )
    if len(writer_memberships) != 1:
        sys.exit(
            f"REFUSED: writerRole {writer_role!r} must belong to exactly one task class"
        )
    writer_obj = roles.get(writer_role) if isinstance(roles, dict) else None
    writer_intel = writer_obj.get("intelligenceClass") if isinstance(writer_obj, dict) else None
    if writer_intel == "verification":
        sys.exit(
            f"REFUSED: {label} writerRole {writer_role!r} has verification intelligenceClass "
            "and cannot be the paired writer of an exact-opposite review"
        )


def _fresh_canonical_launch_receipt(
    raw_models,
    *,
    task_class,
    reasoning_posture,
    semantic_role,
    review_mode,
    writer_role,
    _test_catalog_runner=None,
):
    """Resolve one receipt from the exact no-follow policy bytes PM just read."""
    if not os.path.isfile(LAUNCH_SPEC_RESOLVER):
        sys.exit("REFUSED: canonical native launch resolver is unavailable")
    original_path = list(sys.path)
    sys.path.insert(0, CONFIG_SYNC_ROOT)
    try:
        spec = importlib.util.spec_from_file_location(
            "_pm_canonical_launch_spec_resolver", LAUNCH_SPEC_RESOLVER
        )
        if spec is None or spec.loader is None:
            sys.exit("REFUSED: canonical native launch resolver could not be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module.resolve_launch_spec(
            raw_models,
            task_class=task_class,
            reasoning_posture=reasoning_posture,
            semantic_role=semantic_role,
            review_mode=review_mode,
            writer_role=writer_role,
            catalog_runner=_test_catalog_runner,
        )
    except SystemExit:
        raise
    except Exception as error:
        sys.exit(f"REFUSED: fresh canonical launch resolution failed: {error}")
    finally:
        sys.path[:] = original_path


def _native_launch_binding(
    reasoning_posture, task_class, semantic_role, relative,
    *, review_mode="none", writer_role=None, _test_catalog_runner=None,
):
    stored, _, raw_receipt = _repo_regular_file(
        relative, prefix=LAUNCH_RECEIPT_PREFIX, label="native launch receipt"
    )
    _, receipt = _load_json_regular(raw_receipt, "native launch receipt")
    try:
        import jsonschema

        with open(NATIVE_LAUNCH_RECEIPT_SCHEMA, encoding="utf-8") as stream:
            schema = json.load(stream)
        jsonschema.Draft202012Validator(schema).validate(receipt)
    except (ImportError, OSError, json.JSONDecodeError) as error:
        sys.exit(f"REFUSED: cannot load native launch receipt schema: {error}")
    except Exception as error:
        sys.exit(f"REFUSED: native launch receipt schema validation failed: {error}")

    request = receipt["request"]
    if request != {
        "taskClass": task_class,
        "reasoningPosture": reasoning_posture,
        "semanticRole": semantic_role,
        "writerRole": writer_role,
        "reviewMode": review_mode,
    }:
        sys.exit("REFUSED: native launch receipt request does not match the PM dispatch envelope")
    claimed = receipt["receiptSha256"]
    if claimed != _canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receiptSha256"}
    ):
        sys.exit("REFUSED: native launch receipt self-digest is invalid")

    _, _, raw_models, models = _load_current_roles_policy()
    _policy_role_membership(
        models,
        task_class=task_class,
        posture=reasoning_posture,
        semantic_role=semantic_role,
        review_mode=review_mode,
        label="native launch receipt",
        writer_role=writer_role,
    )
    freshly_resolved = _fresh_canonical_launch_receipt(
        raw_models,
        task_class=task_class,
        reasoning_posture=reasoning_posture,
        semantic_role=semantic_role,
        review_mode=review_mode,
        writer_role=writer_role,
        _test_catalog_runner=_test_catalog_runner,
    )
    if receipt != freshly_resolved:
        sys.exit(
            "REFUSED: native launch receipt does not exactly match the fresh canonical resolver "
            "output for the current no-follow model-role policy bytes"
        )
    return {
        "reasoning_posture": reasoning_posture,
        "task_class": task_class,
        "semantic_role": semantic_role,
        "launch_receipt_path": stored,
        "launch_receipt_sha256": "sha256:" + hashlib.sha256(raw_receipt).hexdigest(),
    }


def _planned_leaf_request(leaf):
    _exact_keys(leaf, LAUNCH_PLAN_LEAF_KEYS, "workflow launch-plan leaf")
    leaf_id = leaf["leafId"]
    if not isinstance(leaf_id, str) or not leaf_id.strip():
        sys.exit("REFUSED: workflow launch-plan leafId must be a non-empty string")
    if leaf["reasoningPosture"] not in REASONING_POSTURES:
        sys.exit("REFUSED: workflow launch-plan reasoningPosture is not an admitted posture")
    if leaf["reviewMode"] not in REVIEW_MODES:
        sys.exit("REFUSED: workflow launch-plan reviewMode must be none or exact-opposite")
    writer = leaf["writerRole"]
    if leaf["reviewMode"] == "none":
        if writer is not None:
            sys.exit("REFUSED: reviewMode none requires writerRole null")
    elif not isinstance(writer, str) or not writer.strip():
        sys.exit("REFUSED: exact-opposite leaves require a writerRole")
    for field in ("taskClass", "semanticRole"):
        if not isinstance(leaf[field], str) or not leaf[field].strip():
            sys.exit(f"REFUSED: workflow launch-plan {field} must be a non-empty string")
    return leaf_id


def _load_current_roles_policy():
    stored, policy_path, raw_policy = _repo_regular_file(
        MODEL_ROLES_PATH, prefix=MODEL_ROLES_PREFIX, label="canonical workflow model-role policy"
    )
    _, policy = _load_json_regular(raw_policy, "canonical workflow model-role policy")
    validator_path = os.path.join(CONFIG_SYNC_ROOT, "validate_model_roles.py")
    if not os.path.isfile(validator_path):
        sys.exit("REFUSED: canonical workflow model-role policy validator is unavailable")
    original_path = list(sys.path)
    sys.path.insert(0, CONFIG_SYNC_ROOT)
    try:
        spec = importlib.util.spec_from_file_location("_pm_model_roles_validator", validator_path)
        if spec is None or spec.loader is None:
            sys.exit("REFUSED: canonical workflow model-role policy validator could not be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.validate(policy)
    except SystemExit:
        raise
    except Exception as error:
        sys.exit(f"REFUSED: canonical workflow model-role policy failed validation: {error}")
    finally:
        sys.path[:] = original_path
    return stored, policy_path, raw_policy, policy


def _load_admitted_workflow_design(relative):
    stored, design_path, raw_design = _repo_regular_file(
        relative, prefix=DESIGN_PREFIX, label="admitted workflow design"
    )
    _, design = _load_json_regular(raw_design, "admitted workflow design")
    if stored != relative:
        sys.exit("REFUSED: workflow launch plan designPath is not the canonical repository path")
    validator_path = os.path.join(
        SKILLS_ROOT, "build-workflow", "scripts", "validate_design.py"
    )
    if not os.path.isfile(validator_path):
        sys.exit("REFUSED: admitted workflow design validator is unavailable")
    try:
        spec = importlib.util.spec_from_file_location("_pm_design_validator", validator_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("validator module could not be loaded")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        errors = module.validate(design)
    except Exception as error:
        sys.exit(f"REFUSED: cannot validate admitted workflow design: {error}")
    if errors:
        detail = "; ".join(str(error) for error in errors[:8])
        suffix = " ..." if len(errors) > 8 else ""
        sys.exit(f"REFUSED: admitted workflow design failed its current validator: {detail}{suffix}")
    nodes = design.get("nodes")
    if not isinstance(nodes, list):
        sys.exit("REFUSED: admitted workflow design nodes must be a list")
    by_id = {}
    for node in nodes:
        node_id = node.get("id")
        if node.get("kind") in {"agent", "decision"}:
            if not isinstance(node.get("role"), str) or not node["role"].strip():
                sys.exit(f"REFUSED: model node {node_id!r} must declare a role")
            by_id[node_id] = node
    if not by_id:
        sys.exit("REFUSED: admitted workflow design must contain at least one model node")
    return stored, design_path, raw_design, design, by_id


def _validate_subagent_mapping(mapping, design_relative, design_hex, design, model_nodes):
    allowed_keys = {
        "schema", "designPath", "designSha256", "inputPath", "reasoningPosture",
        "scheduler", "proof", "handlers",
    }
    unknown = sorted(set(mapping) - allowed_keys)
    if unknown:
        sys.exit(f"REFUSED: subagent mapping has unsupported keys {unknown}")
    if mapping.get("schema") != "subagent-workflow/v1":
        sys.exit("REFUSED: subagent mapping must use subagent-workflow/v1")
    if mapping.get("designPath") != design_relative:
        sys.exit("REFUSED: subagent mapping designPath does not match the launch plan")
    _require_bare_sha256_hex(mapping.get("designSha256"), "subagent mapping designSha256")
    if mapping["designSha256"] != design_hex:
        sys.exit("REFUSED: subagent mapping designSha256 does not match the admitted design")
    if not isinstance(mapping.get("inputPath"), str) or not mapping["inputPath"].strip():
        sys.exit("REFUSED: subagent mapping inputPath must be a non-empty path")
    posture = mapping.get("reasoningPosture")
    if not isinstance(posture, str) or not posture.strip():
        sys.exit("REFUSED: subagent mapping reasoningPosture must be a non-empty string")
    if not isinstance(mapping.get("scheduler"), dict):
        sys.exit("REFUSED: subagent mapping scheduler must be an object")
    handlers = mapping.get("handlers")
    if not isinstance(handlers, dict):
        sys.exit("REFUSED: subagent mapping handlers must be an object")
    design_nodes = {
        node["id"]: node for node in design.get("nodes", []) if isinstance(node, dict)
    }
    missing = sorted(set(design_nodes) - set(handlers))
    if missing:
        sys.exit(f"REFUSED: subagent mapping is missing handler for node {missing[0]!r}")
    extra = sorted(set(handlers) - set(design_nodes))
    if extra:
        sys.exit(f"REFUSED: subagent mapping has extra handler {extra[0]!r}")
    allowed_types = {"input", "agent", "decision", "increment", "template", "command", "terminal"}
    for node_id, node in design_nodes.items():
        handler = handlers[node_id]
        if not isinstance(handler, dict) or handler.get("type") not in allowed_types:
            sys.exit(f"REFUSED: subagent mapping handler {node_id!r} is malformed")
        expected = node.get("kind")
        if expected in {"agent", "decision"} and handler["type"] != expected:
            sys.exit(
                f"REFUSED: subagent mapping handler {node_id!r} type does not match "
                f"design node kind {expected!r}"
            )
    for node_id, handler in handlers.items():
        if node_id not in design_nodes and isinstance(handler, dict) and handler.get("type") in {
            "agent", "decision"
        }:
            sys.exit(f"REFUSED: subagent mapping has extra model handler {node_id!r}")
    return posture


def _humanize_contract():
    """Read the canonical adapter pins out of safe_mapping.py as literal data.

    PM must not import or execute the adapter contract: that module generates and audits
    untrusted flow source. Parsing its module-level literals keeps one canonical pin while
    leaving deterministic generation proof with the adapter and the harness selector.
    """
    try:
        with open(HUMANIZE_CONTRACT_PATH, encoding="utf-8") as stream:
            tree = ast.parse(stream.read(), filename=HUMANIZE_CONTRACT_PATH)
    except (OSError, SyntaxError, ValueError) as error:
        sys.exit(f"REFUSED: cannot read the canonical Humanize mapping contract: {error}")
    wanted = set(HUMANIZE_CONTRACT_NAMES)
    found = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or target.id not in wanted:
            continue
        try:
            found[target.id] = ast.literal_eval(statement.value)
        except ValueError:
            sys.exit(
                f"REFUSED: canonical Humanize mapping contract {target.id} is not a literal"
            )
    missing = sorted(wanted - set(found))
    if missing:
        sys.exit(f"REFUSED: canonical Humanize mapping contract is missing {missing}")
    kinds = found["HUMAN_REQUEST_KINDS"]
    if not isinstance(kinds, (set, frozenset, list, tuple)) or not kinds:
        sys.exit("REFUSED: canonical Humanize contract HUMAN_REQUEST_KINDS is not a collection")
    return {
        "mapping_schema": found["MAPPING_SCHEMA"],
        "spec_schema": found["SPEC_SCHEMA"],
        "adapter_version": found["ADAPTER_VERSION"],
        "upstream": {
            "repository": found["UPSTREAM_REPOSITORY"],
            "revision": found["UPSTREAM_REVISION"],
            "distribution": f"hmz=={found['HMZ_DISTRIBUTION_VERSION']}",
        },
        "human_request_kinds": sorted(kinds),
    }


def _validate_humanize_mapping(mapping, design_relative, design_hex, design, model_nodes):
    contract = _humanize_contract()
    unknown = sorted(set(mapping) - set(HUMANIZE_MAPPING_KEYS))
    if unknown:
        sys.exit(f"REFUSED: Humanize mapping has unsupported keys {unknown}")
    missing = sorted(set(HUMANIZE_MAPPING_KEYS) - set(mapping))
    if missing:
        sys.exit(f"REFUSED: Humanize mapping is missing required keys {missing}")
    if mapping.get("schema") != contract["mapping_schema"]:
        sys.exit(f"REFUSED: Humanize mapping must use {contract['mapping_schema']}")
    if mapping.get("adapterVersion") != contract["adapter_version"]:
        sys.exit(
            "REFUSED: Humanize mapping adapterVersion does not match the canonical adapter "
            f"pin {contract['adapter_version']!r}"
        )
    if mapping.get("upstream") != contract["upstream"]:
        sys.exit(
            "REFUSED: Humanize mapping upstream is not bound to the audited humanize2 "
            f"repository/revision/distribution {contract['upstream']}"
        )
    if mapping.get("designPath") != design_relative:
        sys.exit("REFUSED: Humanize mapping designPath does not match the launch plan")
    _require_bare_sha256_hex(mapping.get("designSha256"), "Humanize mapping designSha256")
    if mapping["designSha256"] != design_hex:
        sys.exit("REFUSED: Humanize mapping designSha256 does not match the admitted design")
    posture = mapping.get("reasoningPosture")
    if not isinstance(posture, str) or not posture.strip():
        sys.exit("REFUSED: Humanize mapping reasoningPosture must be a non-empty string")
    if mapping.get("terminalProtocol") != {
        "field": "TERMINAL",
        "values": list(TERMINAL_VERDICTS),
        "humanRequestKinds": contract["human_request_kinds"],
        "independentReviewRequired": True,
    }:
        sys.exit(
            "REFUSED: Humanize mapping terminalProtocol must declare the exact canonical "
            "TERMINAL field, verdicts, human-request kinds, and independentReviewRequired true"
        )
    bindings = mapping.get("nodeAgents")
    if not isinstance(bindings, list):
        sys.exit("REFUSED: Humanize mapping nodeAgents must be a list")
    expected_ids = set(model_nodes)
    observed = {}
    for binding in bindings:
        if (
            not isinstance(binding, dict)
            or set(binding) != {"node", "role", "workspaceAccess"}
            or not isinstance(binding.get("node"), str)
            or not isinstance(binding.get("role"), str)
            or binding.get("workspaceAccess") not in {"read", "write"}
        ):
            sys.exit("REFUSED: Humanize mapping contains a malformed nodeAgents binding")
        node_id = binding["node"]
        if node_id in observed:
            sys.exit(f"REFUSED: Humanize mapping repeats nodeAgents binding for {node_id!r}")
        if node_id not in expected_ids:
            sys.exit(f"REFUSED: Humanize mapping has extra model node {node_id!r}")
        node = model_nodes[node_id]
        expected_access = node.get("workspaceAccess", "read")
        if binding["role"] != node["role"]:
            sys.exit(f"REFUSED: Humanize mapping role does not match model node {node_id!r}")
        if binding["workspaceAccess"] != expected_access:
            sys.exit(f"REFUSED: Humanize mapping workspaceAccess does not match model node {node_id!r}")
        observed[node_id] = binding
    missing = sorted(expected_ids - set(observed))
    if missing:
        sys.exit(f"REFUSED: Humanize mapping is missing nodeAgents binding for {missing[0]!r}")
    _validate_humanize_spec_and_artifact(mapping, contract, design)
    return posture


def _validate_humanize_spec_and_artifact(mapping, contract, design):
    """Bind the exact spec/artifact bytes and namespaces the adapter would consume."""
    _require_bare_sha256_hex(mapping.get("specSha256"), "Humanize mapping specSha256")
    _require_bare_sha256_hex(mapping.get("artifactSha256"), "Humanize mapping artifactSha256")
    spec_relative, _, raw_spec = _repo_regular_file(
        mapping.get("specPath"), prefix=HUMANIZE_SPEC_PREFIX, label="Humanize adapter spec"
    )
    if spec_relative != mapping.get("specPath"):
        sys.exit("REFUSED: Humanize mapping specPath is not the canonical repository path")
    if hashlib.sha256(raw_spec).hexdigest() != mapping["specSha256"]:
        sys.exit("REFUSED: Humanize mapping specSha256 does not match the pinned spec bytes")
    _, spec = _load_json_regular(raw_spec, "Humanize adapter spec")
    if spec.get("schema") != contract["spec_schema"]:
        sys.exit(f"REFUSED: Humanize adapter spec must use {contract['spec_schema']}")
    input_path = mapping.get("inputPath")
    if not isinstance(input_path, str) or not input_path.strip():
        sys.exit("REFUSED: Humanize mapping inputPath must be a non-empty path")
    if spec.get("inputPath") != input_path:
        sys.exit("REFUSED: Humanize mapping inputPath disagrees with its pinned adapter spec")
    expected_artifact = os.path.join(
        HUMANIZE_GENERATED_PREFIX, f"{design.get('id')}.py"
    ).replace(os.sep, "/")
    if mapping.get("artifact") != expected_artifact:
        sys.exit(
            "REFUSED: Humanize mapping artifact must be the isolated adapter-generated flow "
            f"{expected_artifact}"
        )
    _, _, raw_artifact = _repo_regular_file(
        mapping["artifact"], prefix=HUMANIZE_GENERATED_PREFIX, label="Humanize generated flow"
    )
    if hashlib.sha256(raw_artifact).hexdigest() != mapping["artifactSha256"]:
        sys.exit(
            "REFUSED: Humanize mapping artifactSha256 does not match the generated flow bytes"
        )


def _validate_workflow_mapping(relative, design_relative, design_hex, design, model_nodes):
    stored, mapping_path, raw_mapping = _repo_regular_file(
        relative, prefixes=MAPPING_PREFIXES, label="admitted workflow mapping"
    )
    _, mapping = _load_json_regular(raw_mapping, "admitted workflow mapping")
    if stored != relative:
        sys.exit("REFUSED: workflow launch plan mappingPath is not the canonical repository path")
    if mapping.get("schema") == "subagent-workflow/v1":
        mapping_label = "subagent"
        mapping_posture = _validate_subagent_mapping(
            mapping, design_relative, design_hex, design, model_nodes
        )
    elif mapping.get("schema") == "humanize-workflow-mapping/v1":
        mapping_label = "Humanize"
        mapping_posture = _validate_humanize_mapping(
            mapping, design_relative, design_hex, design, model_nodes
        )
    else:
        sys.exit("REFUSED: workflow mapping uses an unsupported current adapter schema")
    return mapping_path, raw_mapping, mapping_posture, mapping_label


def _validate_workflow_launch_plan_leaves(
    plan, design, model_nodes, policy, *, mapping_posture=None, mapping_label="mapping"
):
    leaves = plan["leaves"]
    if not isinstance(leaves, list) or not leaves:
        sys.exit("REFUSED: workflow launch plan must declare at least one model leaf")
    planned = {}
    for leaf in leaves:
        leaf_id = _planned_leaf_request(leaf)
        if leaf_id in planned:
            sys.exit(f"REFUSED: duplicate workflow launch-plan leafId {leaf_id!r}")
        planned[leaf_id] = leaf
    expected = set(model_nodes)
    actual = set(planned)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            sys.exit(f"REFUSED: workflow launch plan is missing model leaf {missing[0]!r}")
        sys.exit(f"REFUSED: workflow launch plan has extra model leaf {extra[0]!r}")

    posture_values = {leaf["reasoningPosture"] for leaf in leaves}
    if len(posture_values) != 1:
        sys.exit("REFUSED: workflow launch plan leaves must use one global reasoningPosture")
    posture = next(iter(posture_values))
    if mapping_posture is not None and posture != mapping_posture:
        sys.exit(
            f"REFUSED: {mapping_label} mapping reasoningPosture {mapping_posture!r} disagrees "
            f"with workflow launch-plan reasoningPosture {posture!r}"
        )
    for node_id, node in model_nodes.items():
        leaf = planned[node_id]
        if leaf["semanticRole"] != node["role"]:
            sys.exit(f"REFUSED: workflow leaf {node_id!r} semanticRole does not match node.role")
        _policy_role_membership(
            policy,
            task_class=leaf["taskClass"],
            posture=posture,
            semantic_role=node["role"],
            review_mode=leaf["reviewMode"],
            label=f"workflow leaf {node_id!r}",
            writer_role=leaf["writerRole"],
        )
        if node["kind"] == "agent":
            if leaf["reviewMode"] != "none" or leaf["writerRole"] is not None:
                sys.exit(
                    f"REFUSED: ordinary agent leaf {node_id!r} requires reviewMode none "
                    "and writerRole null"
                )
        elif node["kind"] == "decision":
            if leaf["reviewMode"] != "exact-opposite":
                sys.exit(f"REFUSED: decision leaf {node_id!r} requires exact-opposite reviewMode")
            reviews = node.get("reviews")
            reviewed_models = [
                model_nodes[reviewed_id]
                for reviewed_id in reviews or []
                if reviewed_id in model_nodes
            ] if isinstance(reviews, list) else []
            if len(reviewed_models) != 1:
                sys.exit(
                    f"REFUSED: decision node {node_id!r} must review exactly one model node"
                )
            expected_writer = reviewed_models[0]["role"]
            if leaf["writerRole"] != expected_writer:
                sys.exit(
                    f"REFUSED: decision leaf {node_id!r} writerRole must be derived from "
                    "its one reviewed model node"
                )
    return planned


def _workflow_launch_plan_binding(relative):
    stored, _, raw_plan = _repo_regular_file(
        relative, prefix=LAUNCH_PLAN_PREFIX, label="workflow launch plan"
    )
    _, plan = _load_json_regular(raw_plan, "workflow launch plan")
    _exact_keys(plan, LAUNCH_PLAN_KEYS, "workflow launch plan")
    if plan["schema"] != WORKFLOW_LAUNCH_PLAN_SCHEMA:
        sys.exit("REFUSED: workflow launch plan must use workflow-launch-plan/v1")
    _require_sha256_token(plan["planSha256"], "workflow launch plan digest")
    if plan["planSha256"] != _canonical_sha256(
        {key: value for key, value in plan.items() if key != "planSha256"}
    ):
        sys.exit("REFUSED: workflow launch plan self-digest is invalid")
    policy_rel, _, raw_policy, policy = _load_current_roles_policy()
    if plan["policyPath"] != policy_rel:
        sys.exit("REFUSED: workflow launch plan policyPath is not the canonical repository path")
    if plan["policySha256"] != "sha256:" + hashlib.sha256(raw_policy).hexdigest():
        sys.exit("REFUSED: workflow launch plan policy digest is stale")
    design_rel, _, raw_design, design, model_nodes = _load_admitted_workflow_design(
        plan["designPath"]
    )
    _, raw_mapping, mapping_posture, mapping_label = _validate_workflow_mapping(
        plan["mappingPath"],
        design_rel,
        hashlib.sha256(raw_design).hexdigest(),
        design,
        model_nodes,
    )
    if plan["designSha256"] != _bytes_sha256_token(raw_design):
        sys.exit("REFUSED: workflow launch plan design digest is stale")
    if plan["mappingSha256"] != _bytes_sha256_token(raw_mapping):
        sys.exit("REFUSED: workflow launch plan mapping digest is stale")
    _validate_workflow_launch_plan_leaves(
        plan,
        design,
        model_nodes,
        policy,
        mapping_posture=mapping_posture,
        mapping_label=mapping_label,
    )
    return {
        "launch_plan_path": stored,
        "launch_plan_sha256": "sha256:" + hashlib.sha256(raw_plan).hexdigest(),
        "receipt_manifest_path": UNBOUND,
        "receipt_manifest_sha256": UNBOUND,
    }


def _workflow_receipt_manifest_binding(
    relative, issue_meta, *, _test_catalog_runner=None
):
    stored, _, raw_manifest = _repo_regular_file(
        relative, prefix=RECEIPT_MANIFEST_PREFIX, label="workflow receipt manifest"
    )
    _, manifest = _load_json_regular(raw_manifest, "workflow receipt manifest")
    _exact_keys(manifest, RECEIPT_MANIFEST_KEYS, "workflow receipt manifest")
    if manifest["schema"] != WORKFLOW_RECEIPT_MANIFEST_SCHEMA:
        sys.exit("REFUSED: workflow receipt manifest must use workflow-receipt-manifest/v1")
    _require_sha256_token(manifest["manifestSha256"], "workflow receipt manifest digest")
    if manifest["manifestSha256"] != _canonical_sha256(
        {key: value for key, value in manifest.items() if key != "manifestSha256"}
    ):
        sys.exit("REFUSED: workflow receipt manifest self-digest is invalid")
    plan_binding = _workflow_launch_plan_binding(issue_meta.get("launch_plan_path", ""))
    if plan_binding["launch_plan_sha256"] != issue_meta.get("launch_plan_sha256"):
        sys.exit("REFUSED: workflow launch plan bytes changed after PM issue creation")
    _, _, raw_plan = _repo_regular_file(
        issue_meta.get("launch_plan_path", ""), prefix=LAUNCH_PLAN_PREFIX, label="workflow launch plan"
    )
    _, plan = _load_json_regular(raw_plan, "workflow launch plan")
    if manifest.get("planPath") != issue_meta.get("launch_plan_path"):
        sys.exit("REFUSED: workflow receipt manifest planPath does not match the committed launch plan")
    if manifest.get("planSha256") != plan["planSha256"]:
        sys.exit("REFUSED: workflow receipt manifest plan digest does not match the committed launch plan")
    recorded = manifest["leaves"]
    if not isinstance(recorded, list):
        sys.exit("REFUSED: workflow receipt manifest leaves must be a list")
    planned = {leaf["leafId"]: leaf for leaf in plan["leaves"]}
    recorded_ids = []
    receipt_paths = []
    for item in recorded:
        _exact_keys(item, RECEIPT_MANIFEST_LEAF_KEYS, "workflow receipt-manifest leaf")
        leaf_id = item["leafId"]
        if leaf_id not in planned:
            sys.exit(f"REFUSED: workflow receipt manifest has extra leaf {leaf_id!r}")
        if leaf_id in recorded_ids:
            sys.exit(f"REFUSED: duplicate workflow receipt-manifest leafId {leaf_id!r}")
        recorded_ids.append(leaf_id)
        if item["receiptPath"] in receipt_paths:
            sys.exit("REFUSED: workflow receipt manifest reuses a receipt path")
        receipt_paths.append(item["receiptPath"])
        _require_sha256_token(item["receiptSha256"], f"workflow leaf {leaf_id!r} receipt digest")
        planned_leaf = planned[leaf_id]
        current = _native_launch_binding(
            planned_leaf["reasoningPosture"],
            planned_leaf["taskClass"],
            planned_leaf["semanticRole"],
            item["receiptPath"],
            review_mode=planned_leaf["reviewMode"],
            writer_role=planned_leaf["writerRole"],
            _test_catalog_runner=_test_catalog_runner,
        )
        if current["launch_receipt_sha256"] != item["receiptSha256"]:
            sys.exit(
                f"REFUSED: workflow leaf {leaf_id!r} receipt bytes do not match the manifest digest"
            )
    missing = [leaf_id for leaf_id in planned if leaf_id not in recorded_ids]
    if missing:
        sys.exit(f"REFUSED: workflow receipt manifest is missing leaf {missing[0]!r}")
    return {
        "receipt_manifest_path": stored,
        "receipt_manifest_sha256": "sha256:" + hashlib.sha256(raw_manifest).hexdigest(),
    }


def _dispatch_metadata(a):
    values = (
        str(getattr(a, "reasoning_posture", None) or ""),
        str(getattr(a, "task_class", None) or ""),
        str(getattr(a, "semantic_role", None) or ""),
        str(getattr(a, "launch_receipt", None) or ""),
    )
    launch_plan = str(getattr(a, "launch_plan", None) or "")
    if a.lane == "direct":
        if values != ("direct", "direct", "none", "none"):
            sys.exit(
                "REFUSED: direct issues require the exact non-launch dispatch sentinels "
                "reasoning_posture=direct, task_class=direct, semantic_role=none, "
                "launch_receipt_path=none"
            )
        return dict(zip(
            ("reasoning_posture", "task_class", "semantic_role", "launch_receipt_path"),
            values,
        ), launch_receipt_sha256="none")
    if a.lane == "workflow":
        if any(values) or not launch_plan:
            sys.exit(
                "REFUSED: workflow issues require --launch-plan and must not pass "
                "--reasoning-posture, --task-class, --semantic-role, or --launch-receipt; "
                "the workflow-managed placeholder is invalid because it claims receipt "
                "evidence before adapter-emitted canonical workflow-native-launch-receipt/v1 "
                "files exist"
            )
        binding = _workflow_launch_plan_binding(launch_plan)
        return binding
    if not all(values):
        sys.exit(
            "REFUSED: native-subagent issues require --reasoning-posture, --task-class, "
            "--semantic-role, and --launch-receipt"
        )
    return _native_launch_binding(
        *values, _test_catalog_runner=getattr(a, "_test_catalog_runner", None)
    )


def _validate_dispatch_binding(
    meta, *, require_receipts=False, _test_catalog_runner=None
):
    _validate_iteration_snapshot(meta)
    lane = meta.get("dispatch_lane")
    recorded = (
        meta.get("reasoning_posture", ""), meta.get("task_class", ""),
        meta.get("semantic_role", ""), meta.get("launch_receipt_path", ""),
    )
    if lane == "direct":
        if recorded != ("direct", "direct", "none", "none") or meta.get("launch_receipt_sha256") != "none":
            sys.exit("REFUSED: direct issue has an invalid dispatch sentinel envelope")
        return
    if lane == "workflow":
        current = _workflow_launch_plan_binding(meta.get("launch_plan_path", ""))
        if current["launch_plan_sha256"] != meta.get("launch_plan_sha256"):
            sys.exit("REFUSED: workflow launch plan bytes changed after PM issue creation")
        recorded_manifest = meta.get("receipt_manifest_path", UNBOUND)
        if recorded_manifest in (None, "", UNBOUND):
            if meta.get("receipt_manifest_sha256", UNBOUND) != UNBOUND:
                sys.exit("REFUSED: workflow issue has an invalid unbound receipt-manifest envelope")
            if require_receipts:
                sys.exit(
                    "REFUSED: workflow native launch receipts are unbound; the adapter must emit "
                    "canonical workflow-native-launch-receipt/v1 files and a PM worker must run "
                    "bind-workflow-receipts before report or terminal close"
                )
            return
        bound = _workflow_receipt_manifest_binding(
            recorded_manifest, meta, _test_catalog_runner=_test_catalog_runner
        )
        if bound["receipt_manifest_sha256"] != meta.get("receipt_manifest_sha256"):
            sys.exit("REFUSED: workflow receipt manifest bytes changed after binding")
        return
    if lane != "native-subagent":
        sys.exit(f"REFUSED: issue has unknown dispatch_lane={lane!r}")
    current = _native_launch_binding(
        *recorded, _test_catalog_runner=_test_catalog_runner
    )
    if current["launch_receipt_sha256"] != meta.get("launch_receipt_sha256"):
        sys.exit("REFUSED: native launch receipt bytes changed after PM issue creation")


def _gql(key, query, variables):
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    if out.get("errors"):
        raise RuntimeError(json.dumps(out["errors"])[:200])
    return out.get("data") or {}


def _linear_close(key, issue_id):
    """Idempotently close one exact Linear item and return a read-back receipt."""
    d = _gql(key, "query($id:String!){issue(id:$id){state{type} team{states{nodes{id type position}}}}}",
             {"id": issue_id})
    iss = d.get("issue") or {}
    if not iss:
        raise RuntimeError("issue not found")
    if (iss.get("state") or {}).get("type") == "completed":
        return {"linearId": issue_id, "stateType": "completed", "idempotent": True}
    states = ((iss.get("team") or {}).get("states") or {}).get("nodes") or []
    done = sorted((s for s in states if s.get("type") == "completed"), key=lambda s: s.get("position", 0))
    if not done:
        raise RuntimeError("team has no completed-type state")
    d2 = _gql(key, "mutation($id:String!,$sid:String!){issueUpdate(id:$id,input:{stateId:$sid}){success}}",
              {"id": issue_id, "sid": done[0]["id"]})
    if not ((d2.get("issueUpdate") or {}).get("success")):
        raise RuntimeError("issueUpdate failed")
    after = _gql(key, "query($id:String!){issue(id:$id){state{type}}}", {"id": issue_id})
    if ((after.get("issue") or {}).get("state") or {}).get("type") != "completed":
        raise RuntimeError("Linear close read-back is not completed")
    return {"linearId": issue_id, "stateType": "completed", "idempotent": False}


def cmd_bind_workflow_receipts(a):
    """Bind adapter-emitted canonical native receipts to a committed workflow launch plan."""
    p = _find_issue(a.id)
    if not p or not os.path.exists(p):
        sys.exit(f"issue {a.id} not found")
    meta, body = read(p)
    if meta.get("dispatch_lane") != "workflow":
        sys.exit("REFUSED: bind-workflow-receipts applies only to workflow issues")
    if meta.get("status") in ("awaiting_human", "finalizing", "done"):
        sys.exit("REFUSED: cannot bind workflow receipts after a terminal lifecycle state")
    recorded = meta.get("receipt_manifest_path", UNBOUND)
    if recorded not in (None, "", UNBOUND):
        current = _workflow_receipt_manifest_binding(
            a.receipt_manifest,
            meta,
            _test_catalog_runner=getattr(a, "_test_catalog_runner", None),
        )
        if (
            current["receipt_manifest_path"] == recorded
            and current["receipt_manifest_sha256"] == meta.get("receipt_manifest_sha256")
        ):
            print(f"issue {a.id} workflow receipts already bound")
            return
        sys.exit("REFUSED: workflow receipts are already bound; overwrite is forbidden")
    bound = _workflow_receipt_manifest_binding(
        a.receipt_manifest,
        meta,
        _test_catalog_runner=getattr(a, "_test_catalog_runner", None),
    )
    meta.update(bound)
    write(p, meta, body)
    print(f"issue {a.id} bound workflow receipt manifest {bound['receipt_manifest_path']}")


def cmd_report(a):
    """Write a fresh local PM report. UI mirroring is a separate delegated side effect."""
    p = m_path(a.id) if a.kind == "milestone" else _find_issue(a.id)
    if not p or not os.path.exists(p):
        sys.exit(f"{a.kind} {a.id} not found")
    item_meta = read(p)[0]
    status = item_meta.get("status", "")
    if status in ("awaiting_human", "finalizing", "done"):
        sys.exit(
            f"REFUSED: {a.kind} {a.id} is in terminal lifecycle state {status!r}; reports are "
            "pre-terminal reviewed inputs and cannot be added afterward"
        )
    if a.kind == "issue":
        _validate_dispatch_binding(
            item_meta,
            require_receipts=True,
            _test_catalog_runner=getattr(a, "_test_catalog_runner", None),
        )
    if not a.summary.strip():
        sys.exit("--summary required (the write-up: what you did + result/outcome + diff/PR + next)")
    _atomic_text(
        os.path.join(pmroot(), "reports", f"{a.id}.md"),
        f"# report: {a.kind} {a.id}\n\n{a.summary}\n",
    )
    if _ui_mode() == "linear":
        lid = a.linear or read(p)[0].get("linear_id", "")
        print(
            f"recorded {a.kind} {a.id} report to the local .pm board; DELEGATE the Linear "
            f"comment/status mirror for linear_id '{lid or 'UNSET; a PM worker must link it first'}'."
        )
    else:
        print(f"recorded {a.kind} {a.id} report to the .pm board (ui_mode={_ui_mode()}; no Linear mirror).")


def cmd_milestone_evolve(a):
    """Open one idempotent milestone/self-evolution checkpoint before terminal review."""
    if not os.path.exists(m_path(a.id)):
        sys.exit(f"milestone {a.id} not found")
    meta, _ = read(m_path(a.id))
    if meta.get("status") == "done":
        sys.exit(f"milestone {a.id} is already done; a completed checkpoint cannot be reopened")
    lifecycle = os.path.join(pmroot(), "self_evolve")
    os.makedirs(lifecycle, exist_ok=True)
    pending = os.path.join(lifecycle, f"{a.id}.pending")
    sealed = os.path.join(lifecycle, f"{a.id}.sealed")
    archived = os.path.join(lifecycle, f"{a.id}.done")
    if os.path.exists(archived):
        sys.exit(f"milestone {a.id} has an archived done checkpoint but is not done; repair state")
    if a.reopen and not a.reason.strip():
        sys.exit("--reopen requires a non-empty --reason")
    if a.reason.strip() and not a.reopen:
        sys.exit("--reason is valid only with --reopen")
    if os.path.exists(pending) and os.path.exists(sealed):
        if not a.reopen:
            sys.exit(f"milestone {a.id} has conflicting pending and sealed checkpoints")
        prior = sorted(glob.glob(os.path.join(lifecycle, f"{a.id}.superseded.*")))
        superseded = os.path.join(lifecycle, f"{a.id}.superseded.{len(prior) + 1}")
        os.replace(sealed, superseded)
        print(f"milestone {a.id}: completed interrupted reopen; self-evolution is PENDING")
        return
    if os.path.exists(sealed) and not a.reopen:
        print(
            f"milestone {a.id}: self-evolution already SEALED; rerun fresh acceptance/evidence, "
            "report, terminal review, artifact verification, then set done. To change any reviewed "
            "project byte after a failed check, REVISE, or resumed HUMAN_BLOCKED, first delegate "
            "`milestone-evolve <id> --reopen --reason <why>`."
        )
        return
    if os.path.exists(sealed) and a.reopen:
        prior = sorted(glob.glob(os.path.join(lifecycle, f"{a.id}.superseded.*")))
        superseded = os.path.join(lifecycle, f"{a.id}.superseded.{len(prior) + 1}")
        _atomic_text(
            pending,
            f"state: pending-self-evolution\nreopen_reason: {a.reason.strip()}\n",
        )
        os.replace(sealed, superseded)
        print(
            f"milestone {a.id}: prior seal SUPERSEDED; self-evolution round "
            f"{len(prior) + 2} is PENDING — mutate only through the bounded completion worker, "
            "then reseal and rerun all final evidence/review."
        )
        return
    if os.path.exists(pending):
        print(f"milestone {a.id}: self-evolution checkpoint already PENDING; resume the bounded worker")
        return
    if not os.path.exists(pending):
        _atomic_text(pending, "state: pending-self-evolution\n")
    print(
        f"milestone {a.id}: completion checkpoint OPEN. Complete every self-evolution mutation "
        f"first, then `pm.py self-improve close {a.id} --note ...` to seal it; only afterward "
        "rerun acceptance, evidence, report, terminal review, verify-artifact, and set done."
    )


def _terminal_artifact(path, anchor):
    """Parse one fresh canonical terminal artifact, returning (fields, error).

    The first and only TERMINAL line is authoritative. Strict singleton fields prevent a later
    COMPLETE or human-request line in the reviewer's prose from rescuing a REVISE or malformed
    HUMAN_BLOCKED verdict.
    """
    artifact_ns = _mtime_ns(path) if os.path.exists(path) else -1
    anchor_ns = int(anchor) if isinstance(anchor, int) else round(float(anchor) * 1_000_000_000)
    if artifact_ns <= anchor_ns:
        return None, f"missing or stale terminal review artifact '{path}'"
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            lines = [line.strip() for line in stream]
    except OSError as error:
        return None, f"cannot read terminal review artifact '{path}': {error}"

    terminals = []
    commitments = []
    kinds = []
    requests = []
    for line in lines:
        match = re.fullmatch(r"TERMINAL:\s*(COMPLETE|HUMAN_BLOCKED|REVISE)", line, flags=re.I)
        if match:
            terminals.append(match.group(1).upper())
        match = re.fullmatch(r"EVIDENCE-COMMITMENT:\s*sha256:([0-9a-f]{64})", line, flags=re.I)
        if match:
            commitments.append(match.group(1).lower())
        match = re.fullmatch(
            r"HUMAN-REQUEST-KIND:\s*(authorization|decision|credential|external-action)",
            line,
            flags=re.I,
        )
        if match:
            kinds.append(match.group(1).lower())
        match = re.fullmatch(r"HUMAN-REQUEST:\s*(\S.*)", line)
        if match:
            requests.append(match.group(1).strip())

    if len(terminals) != 1:
        return None, "terminal artifact needs exactly one canonical TERMINAL line"
    if len(commitments) != 1:
        return None, "terminal artifact needs exactly one EVIDENCE-COMMITMENT: sha256:<64hex> line"
    terminal = terminals[0]
    if terminal == "HUMAN_BLOCKED":
        if len(kinds) != 1 or len(requests) != 1:
            return None, (
                "TERMINAL: HUMAN_BLOCKED needs exactly one HUMAN-REQUEST-KIND and one non-empty "
                "HUMAN-REQUEST line"
            )
        if requests[0].startswith("<"):
            return None, "HUMAN-REQUEST must name the concrete human action; placeholders are invalid"
    elif kinds or requests:
        return None, f"TERMINAL: {terminal} must not carry HUMAN-REQUEST fields"
    return {
        "terminal": terminal,
        "evidence_commitment": commitments[0],
        "human_request_kind": kinds[0] if kinds else "",
        "human_request": requests[0] if requests else "",
    }, None


def _terminal_artifact_path(kind, item_id, review_policy):
    if review_policy == "machine-gated":
        relative = _canonical_workflow_proof_relative(item_id)
        if relative is None:
            sys.exit("REFUSED: workflow proof item id is not a safe direct filename")
        return os.path.join(os.path.dirname(pmroot()), *relative.split("/"))
    directory = "verdicts" if kind == "issue" else "reviews"
    return os.path.join(pmroot(), directory, f"{item_id}.md")


SAFE_ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
WORKFLOW_PROOF_PREFIX = os.path.join(".pm", "workflow-reviews")


def _canonical_workflow_proof_relative(item_id):
    if not isinstance(item_id, str) or not SAFE_ITEM_ID.fullmatch(item_id):
        return None
    return f".pm/workflow-reviews/{item_id}.md"


def _legacy_workflow_proof_relative(item_id):
    if not isinstance(item_id, str) or not SAFE_ITEM_ID.fullmatch(item_id):
        return None
    return f".pm/reviews/workflow-{item_id}.md"


def _inspect_relative_kind(relative):
    """Classify a repository-relative path without following parent or leaf symlinks."""
    if not isinstance(relative, str) or not relative.strip() or os.path.isabs(relative):
        return "other"
    posix = relative.replace(os.sep, "/")
    if posix == ".." or posix.startswith("../"):
        return "other"
    parts = [part for part in posix.split("/") if part]
    if not parts or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return "other"
    root = os.path.abspath(os.path.dirname(pmroot()))
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    directory_fd = None
    try:
        directory_fd = os.open(root, directory_flags)
        for index, component in enumerate(parts):
            try:
                info = os.stat(component, dir_fd=directory_fd, follow_symlinks=False)
            except OSError:
                return "missing"
            is_last = index == len(parts) - 1
            if stat.S_ISLNK(info.st_mode):
                return "symlink-leaf" if is_last else "symlink-parent"
            if is_last:
                return "regular" if stat.S_ISREG(info.st_mode) else "other"
            if not stat.S_ISDIR(info.st_mode):
                return "other"
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            opened = os.fstat(next_fd)
            if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                os.close(next_fd)
                return "other"
            os.close(directory_fd)
            directory_fd = next_fd
        return "missing"
    except OSError:
        return "other"
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _workflow_proof_error(item_id, anchor):
    """Return None for a fresh, state-bound workflow proof, else a concrete refusal reason."""
    relative = _canonical_workflow_proof_relative(item_id)
    if relative is None:
        return "workflow proof item id is not a safe direct filename"
    kind = _inspect_relative_kind(relative)
    if kind == "missing":
        legacy = _legacy_workflow_proof_relative(item_id)
        legacy_kind = _inspect_relative_kind(legacy) if legacy else "missing"
        if legacy_kind != "missing":
            return (
                f"legacy machine workflow proof path '{legacy}' is ambiguous by construction "
                "and is not accepted as a compatibility alias; rerun the controller to write "
                f"'{relative}'"
            )
        proof = os.path.join(os.path.dirname(pmroot()), *relative.split("/"))
        return (
            f"missing or stale workflow proof '{proof}' (the selected machine-gated workflow must "
            "declare completionProof=state-bound and write a canonical TERMINAL verdict after the issue changed)"
        )
    if kind in ("symlink-parent", "symlink-leaf"):
        return (
            f"workflow proof '{relative}' cannot be read without following a symlink parent or leaf"
        )
    if kind != "regular":
        return f"workflow proof '{relative}' is not a regular no-follow file"
    try:
        _posix, proof, raw = _repo_regular_file(
            relative, prefix=WORKFLOW_PROOF_PREFIX, label="workflow proof"
        )
    except SystemExit as error:
        return str(error)
    if _mtime_ns(proof) <= int(anchor):
        return (
            f"missing or stale workflow proof '{proof}' (the selected machine-gated workflow must "
            "declare completionProof=state-bound and write a canonical TERMINAL verdict after the issue changed)"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        return f"cannot read workflow proof: {error}"
    schema_error = _strong_workflow_proof_error(text, item_id)
    if schema_error:
        return schema_error
    result = subprocess.run(
        [
            sys.executable,
            REWARD_EVIDENCE_HELPER,
            "verify-proof",
            "--root",
            os.path.dirname(pmroot()),
            "--item-id",
            item_id,
            "--proof",
            proof,
        ],
        cwd=os.path.dirname(pmroot()),
        capture_output=True,
        text=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key != "GENERAL_AGENT_CONTROLLER_RUNTIME"
            and not key.startswith("GENERAL_AGENT_ALLOW_")
        },
    )
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout or "workflow proof validation failed").strip()


def _strong_workflow_proof_error(text, item_id):
    """Reject legacy/weak proofs that omit the graph's independent-review bindings."""
    lines = text.splitlines()
    fields = {}
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in STRONG_WORKFLOW_FIELDS:
            fields.setdefault(key, []).append(value.strip())
    missing_or_duplicate = [
        field for field in STRONG_WORKFLOW_FIELDS
        if len(fields.get(field, [])) != 1 or not fields.get(field, [""])[0]
    ]
    if missing_or_duplicate:
        return (
            "workflow proof is legacy/weak or ambiguous; the active machine-gated controller "
            "must emit exactly one non-empty binding for: " + ", ".join(missing_or_duplicate)
        )
    value = {field: fields[field][0] for field in STRONG_WORKFLOW_FIELDS}
    if value["TERMINAL"] != "COMPLETE":
        return "workflow proof requires exact TERMINAL: COMPLETE"
    candidate = value["CANDIDATE_DIGEST"]
    if value["EVIDENCE-COMMITMENT"] != f"sha256:{candidate}":
        return "workflow EVIDENCE-COMMITMENT must equal sha256:<CANDIDATE_DIGEST>"
    if value["REVIEW_POLICY"] != "machine-gated" or value["COMPLETION_PROOF"] != "state-bound":
        return "workflow proof needs exact machine-gated/state-bound policy fields"
    if value["ITEM_ID"] != item_id:
        return "workflow proof ITEM_ID does not match"
    safe_id = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    for field in ("DESIGN_ID", "RUN_ID", "EVIDENCE_PRODUCER_NODE"):
        if not safe_id.fullmatch(value[field]):
            return f"workflow proof {field} is not a safe concrete identifier"
    digest = re.compile(r"^[0-9a-f]{64}$")
    for field in STRONG_WORKFLOW_DIGEST_FIELDS:
        if not digest.fullmatch(value[field]):
            return f"workflow proof {field} must be lowercase sha256 hex"
    integers = {}
    for field in (
        "MODEL_CALLS",
        "MAX_MODEL_CALLS",
        "MACHINE_REVIEW_CALL",
        "NATIVE_TRACE_CALLS",
        "EVIDENCE_PRODUCER_ACTIVATION",
    ):
        if not re.fullmatch(r"[1-9][0-9]*", value[field]):
            return f"workflow proof {field} must be a positive integer"
        integers[field] = int(value[field])
    if integers["MODEL_CALLS"] > integers["MAX_MODEL_CALLS"]:
        return "workflow proof MODEL_CALLS exceeds MAX_MODEL_CALLS"
    if integers["MACHINE_REVIEW_CALL"] > integers["MODEL_CALLS"]:
        return "workflow proof machine reviewer call is outside terminal model-call accounting"
    if value["AUTHORIZATION_MODE"] != "controller-tier1-env":
        return "workflow proof authorization mode is not controller-only"
    if value["AUTHORIZATION_SOURCE"] != "GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1":
        return "workflow proof authorization source is invalid"
    if value["THREAT_BOUNDARY"] != STATE_BOUND_THREAT_BOUNDARY:
        return "workflow proof threat boundary is missing or changed"
    try:
        json.loads(value["STATE_BINDING_JSON"])
    except json.JSONDecodeError:
        return "workflow proof STATE_BINDING_JSON is invalid"
    return None


def _independent_artifact_error(
    kind,
    item_id,
    *,
    _test_catalog_runner=None,
    _test_reviewer_executable=None,
):
    """Return None when the independent artifact still binds current bytes/evidence, else why."""
    helper = ISSUE_REVIEW_HELPER if kind == "issue" else MILESTONE_REVIEW_HELPER
    if _test_catalog_runner is not None:
        original_path = list(sys.path)
        original_cwd = os.getcwd()
        output = io.StringIO()
        error_output = io.StringIO()
        module_name = f"_pm_{kind}_artifact_verifier"
        previous_module = sys.modules.get(module_name)
        try:
            sys.path.insert(0, os.path.dirname(helper))
            spec = importlib.util.spec_from_file_location(module_name, helper)
            if spec is None or spec.loader is None:
                return "independent terminal artifact verifier could not be loaded"
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            os.chdir(os.path.dirname(pmroot()))
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error_output):
                try:
                    return_code = module.main(
                        [item_id, "--verify-artifact"],
                        _test_catalog_runner=_test_catalog_runner,
                        _test_reviewer_executable=_test_reviewer_executable,
                    )
                except SystemExit as exit_error:
                    return_code = exit_error.code if isinstance(exit_error.code, int) else 1
        except Exception as error:
            return f"independent terminal artifact validation failed: {error}"
        finally:
            os.chdir(original_cwd)
            sys.path[:] = original_path
            if previous_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module
        if return_code == 0:
            return None
        return (
            error_output.getvalue()
            or output.getvalue()
            or "independent terminal artifact validation failed"
        ).strip()
    result = subprocess.run(
        [sys.executable, helper, item_id, "--verify-artifact"],
        cwd=os.path.dirname(pmroot()),
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout or "independent terminal artifact validation failed").strip()


def _assert_transition(kind, item_id, current, target, *, via_reopen=False):
    if current not in NONTERMINAL_TRANSITIONS:
        sys.exit(f"REFUSED: {kind} {item_id} has unknown current status {current!r}")
    if via_reopen:
        if current not in ("review", "awaiting_human"):
            suffix = "; done is terminal" if current == "done" else ""
            sys.exit(
                f"REFUSED: {kind} {item_id} cannot reopen from {current!r}{suffix}; "
                "only review or awaiting_human may start a new revision round"
            )
        return
    if target == "reopened":
        sys.exit(
            f"REFUSED: use `pm.py reopen {kind} {item_id} --reason <review finding>`; "
            "set cannot create a revision round"
        )
    if (current, target) in TERMINAL_TRANSITIONS:
        return
    if target not in NONTERMINAL_TRANSITIONS[current]:
        suffix = "; done is terminal" if current == "done" else ""
        sys.exit(
            f"REFUSED: invalid status transition {kind} {item_id}: {current} -> {target}{suffix}"
        )


def _terminal_artifact_for(kind, item_id, review_policy, anchor):
    artifact_path = _terminal_artifact_path(kind, item_id, review_policy)
    terminal, error = _terminal_artifact(artifact_path, anchor)
    if error:
        sys.exit(
            f"REFUSED: '{item_id}' needs a FRESH canonical terminal reviewer artifact.\n  {error}"
        )
    return artifact_path, terminal


def _cli_repo_relative(raw, label):
    if not isinstance(raw, str) or not raw.strip():
        sys.exit(f"REFUSED: {label} path is required")
    root = os.path.abspath(os.path.dirname(pmroot()))
    candidate = raw if os.path.isabs(raw) else os.path.join(root, raw)
    candidate = os.path.abspath(candidate)
    try:
        common = os.path.commonpath([root, candidate])
    except ValueError:
        common = ""
    if common != root:
        sys.exit(f"REFUSED: {label} must be a repository path")
    posix = os.path.relpath(candidate, root).replace(os.sep, "/")
    if posix == ".." or posix.startswith("../"):
        sys.exit(f"REFUSED: {label} must be a repository-relative path")
    return posix


def _unique_digest_file(prefix, digest, *, exclude, label):
    root = os.path.abspath(os.path.dirname(pmroot()))
    abs_prefix = os.path.join(root, prefix)
    try:
        names = os.listdir(abs_prefix)
    except OSError as error:
        sys.exit(f"REFUSED: cannot list {label} directory: {error}")
    matches = []
    exclude_posix = exclude.replace(os.sep, "/")
    for name in sorted(names):
        relative = os.path.join(prefix, name).replace(os.sep, "/")
        if relative == exclude_posix:
            continue
        candidate = os.path.join(root, relative)
        try:
            info = os.lstat(candidate)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            continue
        stored, path, raw = _repo_regular_file(
            relative, prefix=prefix, exclusive=True, label=label
        )
        if "sha256:" + hashlib.sha256(raw).hexdigest() == digest:
            matches.append((stored, path, raw))
    if len(matches) != 1:
        sys.exit(
            f"REFUSED: {label} authorization sidecar was not found uniquely under {prefix}/"
        )
    return matches[0]


def _verify_merged_github_live(
    *,
    repository,
    pr,
    approval_review_id,
    approved_head,
    base_ref_name,
    base_ref_oid,
    merge_commit,
    _test_remote_gh=None,
):
    verifier_path = os.path.join(
        SKILLS_ROOT, "codex-verify", "scripts", "review_lifecycle", "pr_merge.py"
    )
    spec = importlib.util.spec_from_file_location("_pm_review_pr_merge", verifier_path)
    if spec is None or spec.loader is None:
        sys.exit("REFUSED: live GitHub merge read-back verifier could not be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        sys.exit(f"REFUSED: live GitHub merge read-back verifier could not be loaded: {error}")
    if _test_remote_gh is not None:
        if not isinstance(_test_remote_gh, dict):
            sys.exit("REFUSED: private test GitHub fixture has an unsupported shape")
        fixture_type = getattr(module, "_TestRemoteGh", None)
        if fixture_type is None:
            sys.exit("REFUSED: live GitHub merge read-back verifier has no private test fixture")
        try:
            module._TEST_REMOTE_GH = fixture_type(
                _test_remote_gh["executable"],
                dict(_test_remote_gh.get("environment", {})),
            )
        except (KeyError, TypeError, ValueError) as error:
            sys.exit(f"REFUSED: private test GitHub fixture is malformed: {error}")
    try:
        module.verify_merged_github(
            repository=repository,
            pr=pr,
            approval_review_id=approval_review_id,
            approved_head=approved_head,
            base_ref_name=base_ref_name,
            base_ref_oid=base_ref_oid,
            merge_commit=merge_commit,
        )
    except module.MergeError as error:
        sys.exit(f"REFUSED: live GitHub merge read-back failed: {error}")
    except OSError as error:
        sys.exit(f"REFUSED: live GitHub merge read-back failed: {error}")


def _receipt_object(path, label):
    if not path:
        sys.exit(f"REFUSED: {label} path is required")
    candidate = os.path.abspath(path)
    try:
        common = os.path.commonpath([os.path.abspath(pmroot()), candidate])
    except ValueError:
        common = ""
    if common != os.path.abspath(pmroot()) or os.path.islink(candidate):
        sys.exit(f"REFUSED: {label} must be a regular file inside .pm/")
    try:
        with open(candidate, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        sys.exit(f"REFUSED: cannot read {label}: {error}")
    if not isinstance(value, dict):
        sys.exit(f"REFUSED: {label} must contain one JSON object")
    return candidate, value


def _human_send_receipt(a, terminal, meta):
    mode = _ui_mode()
    if mode == "file":
        sys.exit(
            "REFUSED: ui_mode=file has no authenticated send producer or read-back; "
            "a caller-authored send receipt cannot authorize awaiting_human"
        )
    if mode == "interactive":
        if a.send_receipt:
            sys.exit(
                "REFUSED: ui_mode=interactive is the controlling session; "
                "--send-receipt is not an authenticated channel and cannot authorize awaiting_human"
            )
        return "", {
            "request_key": f"{a.kind}-{a.id}-session",
            "expected_human_id": "controlling-session",
            "channel": "interactive",
        }
    if mode != "linear":
        sys.exit(f"REFUSED: unknown ui_mode {mode!r}")
    relative = _cli_repo_relative(a.send_receipt, "human request send receipt")
    stored, _, raw = _repo_regular_file(
        relative,
        prefix=HUMAN_RECEIPT_PREFIX,
        exclusive=True,
        label="human request send receipt",
    )
    _, receipt = _load_json_regular(raw, "human request send receipt")
    required = {
        "schema", "request_key", "channel", "item_kind", "item_id",
        "expected_human_id", "request_hash", "sent_at",
    }
    if set(receipt) != required or receipt.get("schema") != "human-request-send-receipt/v1":
        sys.exit("REFUSED: human request send receipt has an unsupported or ambiguous schema")
    expected_hash = "sha256:" + hashlib.sha256(
        terminal["human_request"].encode("utf-8")
    ).hexdigest()
    if (
        receipt.get("item_kind") != a.kind
        or receipt.get("item_id") != a.id
        or receipt.get("request_hash") != expected_hash
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(receipt.get("request_key", "")))
        or not str(receipt.get("expected_human_id", "")).strip()
        or receipt.get("channel") != "linear"
        or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z", str(receipt.get("sent_at", "")))
    ):
        sys.exit("REFUSED: human request send receipt does not bind the reviewed request and target")
    linear_id = str(meta.get("linear_id", "")).strip()
    if not linear_id:
        sys.exit("REFUSED: linear awaiting_human requires linear_id on the board item")
    key = str(os.environ.get("LINEAR_API_KEY", "")).strip()
    if not key:
        sys.exit("REFUSED: LINEAR_API_KEY is required for authenticated Linear send read-back")
    try:
        data = _gql(
            key,
            "query($id:String!){issue(id:$id){comments{nodes{id body}}}}",
            {"id": linear_id},
        )
    except Exception as error:
        sys.exit(f"REFUSED: Linear send read-back failed: {error}")
    comments = ((data.get("issue") or {}).get("comments") or {}).get("nodes") or []
    request_key = str(receipt.get("request_key", ""))
    matched = False
    for comment in comments:
        body = str((comment or {}).get("body") or "")
        keys = re.findall(r"^REQUEST-KEY:\s*(\S+)\s*$", body, flags=re.MULTILINE)
        digests = re.findall(
            r"^REQUEST-SHA256:\s*(sha256:[0-9a-f]{64})\s*$", body, flags=re.MULTILINE
        )
        if keys == [request_key] and digests == [expected_hash]:
            matched = True
            break
    if not matched:
        sys.exit(
            "REFUSED: Linear send read-back did not find a comment with the exact REQUEST-KEY "
            "and REQUEST-SHA256 of the reviewed HUMAN-REQUEST"
        )
    return stored, receipt


def _sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path):
    try:
        with open(path, "rb") as stream:
            return _sha256_bytes(stream.read())
    except OSError as error:
        sys.exit(f"REFUSED: cannot hash required lifecycle artifact {path!r}: {error}")


def _one_artifact_field(path, field):
    prefix = field + ":"
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            values = [
                line[len(prefix):].strip()
                for line in stream
                if line.startswith(prefix)
            ]
    except OSError as error:
        sys.exit(f"REFUSED: cannot read terminal artifact {path!r}: {error}")
    if len(values) != 1 or not values[0]:
        sys.exit(f"REFUSED: terminal artifact requires exactly one {field} field")
    return values[0]


def _artifact_builder_roles(path):
    """Return the exact semantic roles an independent artifact recorded as reviewed builders."""
    raw = _one_artifact_field(path, "BUILDER-IDENTITIES")
    try:
        recorded = json.loads(raw)
    except json.JSONDecodeError as error:
        sys.exit(f"REFUSED: terminal artifact BUILDER-IDENTITIES is not valid JSON: {error}")
    if not isinstance(recorded, list) or not recorded:
        sys.exit("REFUSED: terminal artifact BUILDER-IDENTITIES must be a non-empty JSON array")
    roles = []
    for identity in recorded:
        role = identity.get("semanticRole") if isinstance(identity, dict) else None
        if not isinstance(role, str) or not role.strip():
            sys.exit(
                "REFUSED: every terminal artifact BUILDER-IDENTITIES entry needs a semanticRole"
            )
        roles.append(role)
    return roles


def _merge_receipt(a, meta, terminal, artifact_path, *, _test_remote_gh=None):
    repository = str(meta.get("pr_repository", "")).strip().lower()
    number = str(meta.get("pr_number", "")).strip()
    if not repository and not number:
        if a.merge_receipt:
            sys.exit("REFUSED: --merge-receipt is valid only for a PR-backed board item")
        return "", None
    if not repository or not re.fullmatch(r"[1-9][0-9]*", number):
        sys.exit(
            "REFUSED: PR-backed board item needs canonical pr_repository and positive pr_number"
        )
    relative = _cli_repo_relative(a.merge_receipt, "exact merge receipt")
    stored, _, raw = _repo_regular_file(
        relative,
        prefix=MERGE_RECEIPT_PREFIX,
        exclusive=True,
        label="exact merge receipt",
    )
    _, receipt = _load_json_regular(raw, "exact merge receipt")
    required = {
        "schema", "transactionId", "authorizationReceiptSha256", "itemKind", "itemId",
        "repository", "pr", "approvedHead", "baseRefName", "baseRefOid", "strategy",
        "deleteBranch", "terminalArtifact", "terminalArtifactSha256", "terminalCommitment",
        "approvalReviewId", "headRefOid", "mergeCommit", "state",
    }
    if set(receipt) != required or receipt.get("schema") != "resolve-pr-merge-receipt/v1":
        sys.exit("REFUSED: exact merge receipt has an unsupported or ambiguous schema")
    artifact_relative = os.path.relpath(artifact_path, os.path.dirname(pmroot()))
    head = _trusted_git_head(os.path.dirname(pmroot()))
    approval_id = str(receipt.get("approvalReviewId", ""))
    if (
        receipt.get("itemKind") != a.kind
        or receipt.get("itemId") != a.id
        or str(receipt.get("repository", "")).lower() != repository
        or receipt.get("pr") != int(number)
        or receipt.get("state") != "MERGED"
        or receipt.get("headRefOid") != receipt.get("approvedHead")
        or receipt.get("approvedHead") != head
        or receipt.get("terminalCommitment") != "sha256:" + terminal["evidence_commitment"]
        or receipt.get("terminalArtifact") != artifact_relative
        or receipt.get("terminalArtifactSha256") != "sha256:" + _sha256_file(artifact_path)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(receipt.get("authorizationReceiptSha256", "")))
        or not re.fullmatch(r"[0-9a-f]{40,64}", str(receipt.get("baseRefOid", "")))
        or not str(receipt.get("baseRefName", "")).strip()
        or receipt.get("strategy") not in ("squash", "merge", "rebase")
        or not isinstance(receipt.get("deleteBranch"), bool)
        or not re.fullmatch(r"[0-9a-f]{40,64}", str(receipt.get("mergeCommit", "")))
        or not re.fullmatch(r"[1-9][0-9]*", approval_id)
    ):
        sys.exit(
            "REFUSED: exact merge receipt does not bind the current item, terminal commitment, "
            "reviewed HEAD, repository/PR/base, strategy, approvalReviewId, and branch-deletion "
            "authorization"
        )
    _, _, auth_raw = _unique_digest_file(
        MERGE_RECEIPT_PREFIX,
        receipt["authorizationReceiptSha256"],
        exclude=stored,
        label="merge authorization sidecar",
    )
    _, authorization = _load_json_regular(auth_raw, "merge authorization sidecar")
    auth_required = {
        "schema", "transactionId", "authorizationKind", "authorizationRef", "itemKind",
        "itemId", "repository", "pr", "approvedHead", "baseRefName", "baseRefOid",
        "strategy", "deleteBranch",
    }
    if set(authorization) != auth_required or authorization.get("schema") != "resolve-pr-authorization/v1":
        sys.exit("REFUSED: merge authorization sidecar has an unsupported or ambiguous schema")
    if (
        authorization.get("transactionId") != receipt.get("transactionId")
        or authorization.get("itemKind") != receipt.get("itemKind")
        or authorization.get("itemId") != receipt.get("itemId")
        or str(authorization.get("repository", "")).lower() != repository
        or authorization.get("pr") != receipt.get("pr")
        or authorization.get("approvedHead") != receipt.get("approvedHead")
        or authorization.get("baseRefName") != receipt.get("baseRefName")
        or authorization.get("baseRefOid") != receipt.get("baseRefOid")
        or authorization.get("strategy") != receipt.get("strategy")
        or authorization.get("deleteBranch") != receipt.get("deleteBranch")
        or authorization.get("authorizationKind") not in ("user", "project-policy")
        or not str(authorization.get("authorizationRef", "")).strip()
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(authorization.get("transactionId", "")))
    ):
        sys.exit(
            "REFUSED: merge authorization sidecar does not bind the exact merge receipt fields"
        )
    _verify_merged_github_live(
        repository=receipt["repository"],
        pr=receipt["pr"],
        approval_review_id=approval_id,
        approved_head=receipt["approvedHead"],
        base_ref_name=receipt["baseRefName"],
        base_ref_oid=receipt["baseRefOid"],
        merge_commit=receipt["mergeCommit"],
        _test_remote_gh=_test_remote_gh,
    )
    return stored, receipt


_POST_VERDICT_CONTROL_PREFIXES = (
    ".pm/finalizations/",
    ".pm/receipts/human/",
    ".pm/receipts/merge/",
)


def _workspace_digest(item_path, canonical_item, ignored_paths=()):
    root = os.path.abspath(os.path.dirname(pmroot()))
    item = os.path.abspath(item_path)
    ignored = {
        os.path.normpath(relative).replace(os.sep, "/") for relative in ignored_paths
    }
    records = []
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False):
        # os.walk classifies a symlink to an existing directory in `dirs` even when it will not
        # follow that link. Bind the link itself and remove it from the recursion frontier.
        directory_symlinks = []
        walk_dirs = []
        for name in sorted(d for d in dirs if d != ".git"):
            path = os.path.join(directory, name)
            if os.path.islink(path):
                directory_symlinks.append((name, path))
            else:
                walk_dirs.append(name)
        dirs[:] = walk_dirs
        for name in sorted(files):
            path = os.path.join(directory, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if relative in ignored or any(
                relative.startswith(prefix) for prefix in _POST_VERDICT_CONTROL_PREFIXES
            ):
                continue
            if os.path.abspath(path) == item:
                kind = "file"
                payload = canonical_item
            elif os.path.islink(path):
                kind = "symlink"
                payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
            elif os.path.isfile(path):
                kind = "file"
                with open(path, "rb") as stream:
                    payload = stream.read()
            else:
                kind = "special"
                payload = b"<special>"
            records.append((relative, kind, _sha256_bytes(payload)))
        for _, path in directory_symlinks:
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if relative in ignored or any(
                relative.startswith(prefix) for prefix in _POST_VERDICT_CONTROL_PREFIXES
            ):
                continue
            payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
            records.append((relative, "symlink", _sha256_bytes(payload)))
    encoded = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "sha256:" + _sha256_bytes(encoded)


def _finalization_path(kind, item_id):
    return os.path.join(pmroot(), "finalizations", f"{kind}-{item_id}.json")


def _write_json(path, value):
    _atomic_text(path, json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def _begin_linear_finalization(a, p, meta, body, terminal, artifact_path, merge_path):
    transaction = str(a.transaction_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", transaction):
        sys.exit(
            "REFUSED: Linear completion requires --transaction-id with one stable non-secret id"
        )
    linear_id = str(meta.get("linear_id", "")).strip()
    if not linear_id:
        sys.exit(
            f"REFUSED: ui_mode=linear requires {a.kind} {a.id} to have a canonical linear_id "
            "before terminal review/finalization"
        )
    receipt_path = _finalization_path(a.kind, a.id)
    if os.path.exists(receipt_path):
        _, existing = _receipt_object(receipt_path, "completion finalization transaction")
        if existing.get("transactionId") != transaction:
            sys.exit(
                "REFUSED: a different completion-finalization transaction already exists"
            )
        # The receipt is a write-ahead record. If the process stopped before the board acquired
        # `finalizing`, the exact same set-done request may attach it and continue.
        shadow = dict(meta)
        shadow["status"] = "finalizing"
        shadow["finalization_transaction"] = transaction
        shadow["finalization_receipt"] = os.path.relpath(
            receipt_path, os.path.dirname(pmroot())
        )
        _validate_finalization(a.kind, a.id, transaction, p, shadow, body)
        write(p, shadow, body)
        return receipt_path, existing
    canonical_item = dump(meta, body).encode("utf-8")
    relative_artifact = os.path.relpath(artifact_path, os.path.dirname(pmroot()))
    relative_merge = merge_path or ""
    token_paths = []
    token_sha256 = ""
    if a.kind == "milestone":
        token_paths = [
            f".pm/self_evolve/{a.id}.sealed", f".pm/self_evolve/{a.id}.done"
        ]
        existing_tokens = [
            os.path.join(os.path.dirname(pmroot()), relative)
            for relative in token_paths
            if os.path.isfile(os.path.join(os.path.dirname(pmroot()), relative))
        ]
        if len(existing_tokens) != 1:
            sys.exit("REFUSED: milestone finalization needs exactly one sealed lifecycle token")
        token_sha256 = "sha256:" + _sha256_file(existing_tokens[0])
    receipt = {
        "schema": "pm-completion-finalization/v1",
        "transactionId": transaction,
        "itemKind": a.kind,
        "itemId": a.id,
        "linearId": linear_id,
        "terminalArtifact": relative_artifact,
        "terminalArtifactSha256": "sha256:" + _sha256_file(artifact_path),
        "terminalCommitment": "sha256:" + terminal["evidence_commitment"],
        "reviewedHead": _trusted_git_head(os.path.dirname(pmroot())),
        "reviewedItemCanonicalSha256": "sha256:" + _sha256_bytes(canonical_item),
        "reviewedWorkspaceDigest": _workspace_digest(p, canonical_item, token_paths),
        "milestoneTokenSha256": token_sha256,
        "mergeReceipt": relative_merge,
        "mergeReceiptSha256": (
            "sha256:" + _sha256_file(os.path.join(os.path.dirname(pmroot()), relative_merge))
            if relative_merge else ""
        ),
        "state": "pending-linear-close",
        "linearReadBack": None,
    }
    _write_json(receipt_path, receipt)
    meta["status"] = "finalizing"
    meta["finalization_transaction"] = transaction
    meta["finalization_receipt"] = os.path.relpath(receipt_path, os.path.dirname(pmroot()))
    write(p, meta, body)
    return receipt_path, receipt


def _validate_finalization(kind, item_id, transaction, p, meta, body):
    receipt_path = _finalization_path(kind, item_id)
    _, receipt = _receipt_object(receipt_path, "completion finalization transaction")
    required = {
        "schema", "transactionId", "itemKind", "itemId", "linearId", "terminalArtifact",
        "terminalArtifactSha256", "terminalCommitment", "reviewedHead",
        "reviewedItemCanonicalSha256", "reviewedWorkspaceDigest", "mergeReceipt",
        "mergeReceiptSha256", "milestoneTokenSha256", "state", "linearReadBack",
    }
    if set(receipt) != required or receipt.get("schema") != "pm-completion-finalization/v1":
        sys.exit("REFUSED: completion finalization transaction has an unsupported schema")
    if (
        receipt.get("transactionId") != transaction
        or receipt.get("itemKind") != kind
        or receipt.get("itemId") != item_id
        or meta.get("finalization_transaction") != transaction
        or meta.get("finalization_receipt")
        != os.path.relpath(receipt_path, os.path.dirname(pmroot()))
        or meta.get("linear_id") != receipt.get("linearId")
        or meta.get("status") not in ("finalizing", "done")
    ):
        sys.exit("REFUSED: completion finalization transaction does not bind current board state")
    artifact_path = os.path.join(os.path.dirname(pmroot()), str(receipt["terminalArtifact"]))
    terminal, terminal_error = _terminal_artifact(artifact_path, 0)
    if (
        terminal_error
        or terminal.get("terminal") != "COMPLETE"
        or "sha256:" + terminal.get("evidence_commitment", "") != receipt["terminalCommitment"]
        or "sha256:" + _sha256_file(artifact_path) != receipt["terminalArtifactSha256"]
    ):
        sys.exit("REFUSED: original COMPLETE artifact changed during completion finalization")
    head = _trusted_git_head(os.path.dirname(pmroot()))
    restored = dict(meta)
    restored["status"] = "review"
    restored.pop("finalization_transaction", None)
    restored.pop("finalization_receipt", None)
    canonical_item = dump(restored, body).encode("utf-8")
    token_paths = []
    if kind == "milestone":
        token_paths = [
            f".pm/self_evolve/{item_id}.sealed", f".pm/self_evolve/{item_id}.done"
        ]
        tokens = [
            os.path.join(os.path.dirname(pmroot()), relative)
            for relative in token_paths
            if os.path.isfile(os.path.join(os.path.dirname(pmroot()), relative))
        ]
        if (
            len(tokens) != 1
            or "sha256:" + _sha256_file(tokens[0]) != receipt["milestoneTokenSha256"]
        ):
            sys.exit("REFUSED: milestone lifecycle token drifted during completion finalization")
    elif receipt["milestoneTokenSha256"]:
        sys.exit("REFUSED: issue completion finalization carries a milestone token")
    if (
        head != receipt["reviewedHead"]
        or "sha256:" + _sha256_bytes(canonical_item) != receipt["reviewedItemCanonicalSha256"]
        or _workspace_digest(p, canonical_item, token_paths) != receipt["reviewedWorkspaceDigest"]
    ):
        sys.exit("REFUSED: project or reviewed item drifted during completion finalization")
    merge_relative = receipt.get("mergeReceipt", "")
    if merge_relative:
        merge_path = os.path.join(os.path.dirname(pmroot()), merge_relative)
        if "sha256:" + _sha256_file(merge_path) != receipt.get("mergeReceiptSha256"):
            sys.exit("REFUSED: exact merge receipt drifted during completion finalization")
    return receipt_path, receipt


def _archive_milestone_token(item_id):
    lifecycle = os.path.join(pmroot(), "self_evolve")
    sealed = os.path.join(lifecycle, f"{item_id}.sealed")
    archive = os.path.join(lifecycle, f"{item_id}.done")
    if os.path.exists(sealed):
        os.replace(sealed, archive)


def _finish_linear_finalization(kind, item_id, transaction):
    p = m_path(item_id) if kind == "milestone" else _find_issue(item_id)
    if not p or not os.path.exists(p):
        sys.exit(f"{kind} {item_id} not found")
    meta, body = read(p)
    receipt_path, receipt = _validate_finalization(kind, item_id, transaction, p, meta, body)
    # Local `state` / `linearReadBack` are recovery/audit fields only. A writable
    # `linear-closed` JSON must never authorize skipping the authenticated close.
    if receipt.get("state") not in ("pending-linear-close", "linear-closed"):
        sys.exit("REFUSED: completion finalization has an unknown state")
    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        sys.exit(
            "REFUSED: completion remains finalizing; LINEAR_API_KEY is required to close and "
            "read back the exact transaction"
        )
    try:
        read_back = _linear_close(key, receipt["linearId"])
    except Exception as error:
        sys.exit(
            f"REFUSED: completion remains finalizing; exact Linear close/read-back failed: {error}"
        )
    receipt["state"] = "linear-closed"
    receipt["linearReadBack"] = read_back
    _write_json(receipt_path, receipt)
    if meta.get("status") != "done":
        if kind == "milestone":
            _archive_milestone_token(item_id)
        meta["status"] = "done"
        write(p, meta, body)
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))


def cmd_set(a):
    if a.status not in STATUSES:
        sys.exit(f"status must be one of {STATUSES}")
    p = m_path(a.id) if a.kind == "milestone" else _find_issue(a.id)
    if not p or not os.path.exists(p):
        sys.exit(f"{a.kind} {a.id} not found")
    meta, body = read(p)
    current_status = meta.get("status", "todo")
    _assert_transition(a.kind, a.id, current_status, a.status)
    if a.status == "finalizing":
        sys.exit("REFUSED: finalizing is internal; request `set ... done` to start completion")
    if a.kind == "issue" and a.status in ("review", "awaiting_human", "done"):
        _validate_dispatch_binding(
            meta,
            require_receipts=a.status in ("awaiting_human", "done"),
            _test_catalog_runner=getattr(a, "_test_catalog_runner", None),
        )
    milestone_sealed = ""
    milestone_archive = ""
    milestone_checkpoint = ""
    completion_target = a.status == "done"
    if completion_target and a.kind == "milestone":
        lifecycle = os.path.join(pmroot(), "self_evolve")
        pending = os.path.join(lifecycle, f"{a.id}.pending")
        sealed = os.path.join(lifecycle, f"{a.id}.sealed")
        archive = os.path.join(lifecycle, f"{a.id}.done")
        if (
            os.path.exists(pending)
            or (os.path.exists(sealed) == os.path.exists(archive))
        ):
            sys.exit(
                f"REFUSED: milestone {a.id} needs a SEALED mandatory self-evolution checkpoint "
                "before its final evidence/report/review. A bounded completion worker must run "
                f"`pm.py milestone-evolve {a.id}`, complete all memory/lemma mutations, and run "
                f"`pm.py self-improve close {a.id} --note ...`; only then rerun acceptance, report, "
                "terminal review, and set done."
            )
        milestone_sealed = sealed
        milestone_archive = archive
        milestone_checkpoint = archive if os.path.exists(archive) else sealed
        incomplete = [
            f"{child.get('id')}={child.get('status')}"
            for child in all_issues(a.id)
            if child.get("status") != "done"
        ]
        if incomplete:
            sys.exit(
                f"REFUSED: milestone {a.id} cannot complete until all child issues are done: "
                + ", ".join(incomplete)
            )
    if a.status == "awaiting_human" and a.kind == "milestone":
        lifecycle = os.path.join(pmroot(), "self_evolve")
        pending = os.path.join(lifecycle, f"{a.id}.pending")
        sealed = os.path.join(lifecycle, f"{a.id}.sealed")
        if not os.path.exists(sealed) or os.path.exists(pending):
            sys.exit(
                f"REFUSED: milestone {a.id} may await human input only from a sealed lifecycle "
                "round reviewed as HUMAN_BLOCKED. After the human response, delegate "
                f"`pm.py milestone-evolve {a.id} --reopen --reason \"human input received\"` "
                "before any project mutation."
            )
        milestone_checkpoint = sealed
    if completion_target or a.status == "awaiting_human":
        # The report is evidence, so the terminal reviewer must run AFTER it and after every item byte
        # covered by this decision. For a milestone, child issue bytes are part of that evidence set.
        work_anchor = _mtime_ns(p)
        if a.kind == "milestone":
            work_anchor = max([work_anchor] + [_mtime_ns(f)
                                               for f in glob.glob(os.path.join(pmroot(), "issues", a.id, "*.md"))])
            # The lifecycle seal is part of the final reviewed state. This forces reports and
            # terminal review to follow self-evolution rather than accepting the historical
            # report/review -> self-evolve ordering.
            work_anchor = max(work_anchor, _mtime_ns(milestone_checkpoint))
        report = os.path.join(pmroot(), "reports", f"{a.id}.md")
        if not (os.path.exists(report) and _mtime_ns(report) > work_anchor):
            sys.exit(f"""REFUSED: '{a.id} -> {a.status}' needs a FRESH write-up REPORTED to the team before terminal review.
  delegate: python3 .claude/skills/pm-dispatch/scripts/pm.py report {a.kind} {a.id} --summary "<results + focused acceptance evidence + outcome or exact human dependency>"
  (this writes local .pm evidence only; delegate any Linear/UI mirror separately).""")
        # Every terminal reviewer runs after the final local report and any delegated UI mirror.
        # For machine-gated work the report must therefore be produced by the workflow before its
        # final reviewer node/proof; a post-proof report would be an unreviewed durable mutation.
        report_anchor = _mtime_ns(report)
        # Old board items predate explicit review_policy. Preserve their former conservative behavior
        # instead of silently weakening completion after a framework upgrade.
        review_policy = meta.get("review_policy") or "independent"
        if review_policy == "none":
            sys.exit(
                f"REFUSED: '{a.id}' uses removed review_policy=none. Migrate it explicitly before "
                f"terminal review:\n  python3 .claude/skills/pm-dispatch/scripts/pm.py "
                f"migrate-review-policy {a.kind} {a.id} --to independent --reason \"<why this reviewer route>\""
            )
        if review_policy not in REVIEW_POLICIES:
            sys.exit(f"REFUSED: '{a.id}' has unknown review_policy={review_policy!r}")
        if review_policy == "machine-gated" and not (
                a.kind == "issue" and meta.get("dispatch_lane") == "workflow"):
            sys.exit("REFUSED: review_policy=machine-gated is valid only for workflow-lane issues")
        # State-bound v1 proofs establish COMPLETE only. Controller-emitted HUMAN_BLOCKED text is
        # candidate evidence, not PM-terminal authority, so awaiting_human uses an independent owner.
        terminal_anchor = max(work_anchor, report_anchor)
        if review_policy == "machine-gated" and completion_target:
            workflow_error = _workflow_proof_error(a.id, terminal_anchor)
            if workflow_error:
                sys.exit(
                    f"REFUSED: '{a.id} -> {a.status}' needs a FRESH workflow proof bound to "
                    f"the current root/workspace byte state.\n  {workflow_error}"
                )
        artifact_policy = "independent" if a.status == "awaiting_human" else review_policy
        artifact_path = _terminal_artifact_path(a.kind, a.id, artifact_policy)
        terminal, terminal_error = _terminal_artifact(artifact_path, terminal_anchor)
        if terminal_error:
            sys.exit(
                f"REFUSED: '{a.id} -> {a.status}' needs a FRESH canonical terminal reviewer artifact.\n"
                f"  {terminal_error}"
            )
        if artifact_policy == "independent":
            independent_error = _independent_artifact_error(
                a.kind,
                a.id,
                _test_catalog_runner=getattr(a, "_test_catalog_runner", None),
                _test_reviewer_executable=getattr(
                    a, "_test_reviewer_executable", None
                ),
            )
            if independent_error:
                sys.exit(
                    f"REFUSED: '{a.id} -> {a.status}' terminal artifact no longer binds the "
                    f"current criteria, project bytes, or evidence.\n  {independent_error}"
                )
            dispatched_role = str(meta.get("semantic_role", "")).strip()
            if a.kind == "issue" and dispatched_role and dispatched_role != "none":
                # The reviewer may cover several builders, but the role PM actually dispatched
                # must be one of them; otherwise the verdict reviewed different work.
                builders = _artifact_builder_roles(artifact_path)
                if dispatched_role not in builders:
                    sys.exit(
                        f"REFUSED: '{a.id} -> {a.status}' terminal artifact BUILDER-IDENTITIES "
                        f"{sorted(set(builders))} omit the dispatched semantic_role "
                        f"{dispatched_role!r}; the independent reviewer must review the role "
                        "this issue dispatched"
                    )
        expected = "COMPLETE" if completion_target else "HUMAN_BLOCKED"
        if terminal["terminal"] != expected:
            sys.exit(
                f"REFUSED: '{a.id} -> {a.status}' requires TERMINAL: {expected}; reviewer returned "
                f"TERMINAL: {terminal['terminal']}. REVISE means continue/re-dispatch, and only a "
                "structured HUMAN_BLOCKED verdict permits awaiting_human."
            )
        if a.status == "awaiting_human":
            send_path, send = _human_send_receipt(a, terminal, meta)
            meta["human_request_kind"] = terminal["human_request_kind"]
            meta["human_request"] = terminal["human_request"]
            meta["human_request_key"] = send["request_key"]
            meta["expected_human_id"] = send["expected_human_id"]
            meta["human_send_receipt"] = send_path
        else:
            meta.pop("human_request_kind", None)
            meta.pop("human_request", None)
            meta.pop("human_request_key", None)
            meta.pop("expected_human_id", None)
            meta.pop("human_send_receipt", None)
            merge_path, _ = _merge_receipt(
                a,
                meta,
                terminal,
                artifact_path,
                _test_remote_gh=getattr(a, "_test_remote_gh", None),
            )
            if _ui_mode() == "linear":
                receipt_path, receipt = _begin_linear_finalization(
                    a, p, meta, body, terminal, artifact_path, merge_path
                )
                if os.environ.get("LINEAR_API_KEY"):
                    _finish_linear_finalization(a.kind, a.id, receipt["transactionId"])
                    return
                print(
                    f"{a.kind} {a.id} -> finalizing; retry `pm.py linear-close {a.id} "
                    f"--transaction-id {receipt['transactionId']}` to close/read back Linear and finish"
                )
                return
    if completion_target and a.kind == "milestone" and os.path.exists(milestone_sealed):
        # Archive first. If the following board replace is interrupted, a retry accepts this
        # archived token, revalidates the still-current artifact, and completes the transition.
        os.replace(milestone_sealed, milestone_archive)
    meta["status"] = a.status
    write(p, meta, body)
    print(f"{a.kind} {a.id} -> {a.status}")


def cmd_reopen(a):
    p = m_path(a.id) if a.kind == "milestone" else _find_issue(a.id)
    if not p or not os.path.exists(p):
        sys.exit(f"{a.kind} {a.id} not found")
    meta, body = read(p)
    _assert_transition(a.kind, a.id, meta.get("status", "todo"), "reopened", via_reopen=True)
    if a.kind == "milestone":
        lifecycle = os.path.join(pmroot(), "self_evolve")
        if os.path.exists(os.path.join(lifecycle, f"{a.id}.done")):
            sys.exit(f"REFUSED: milestone {a.id} has a done checkpoint; done is terminal")
    meta["status"] = "reopened"
    write(p, meta, body)
    log(f"REWORK: reopened {a.kind} {a.id} — {a.reason}")
    print(f"reopened {a.kind} {a.id} (logged). Re-dispatch it.")


def cmd_migrate_review_policy(a):
    p = m_path(a.id) if a.kind == "milestone" else _find_issue(a.id)
    if not p or not os.path.exists(p):
        sys.exit(f"{a.kind} {a.id} not found")
    meta, body = read(p)
    if meta.get("review_policy") != "none":
        sys.exit(
            f"REFUSED: migration is only for legacy review_policy=none items; {a.id} currently has "
            f"review_policy={meta.get('review_policy', '(missing)')}"
        )
    if a.kind == "milestone" and a.to != "independent":
        sys.exit("milestones require --to independent")
    if a.to == "machine-gated" and not (
        a.kind == "issue" and meta.get("dispatch_lane") == "workflow"
    ):
        sys.exit("--to machine-gated is valid only for workflow-lane issues")
    if not a.reason.strip():
        sys.exit("--reason must be non-empty")
    original_issue_raw = None
    task_state = None
    if a.kind == "issue":
        repository_root = os.path.abspath(os.path.dirname(pmroot()))
        issue_relative = os.path.relpath(os.path.abspath(p), repository_root).replace(os.sep, "/")
        _unused, _issue_path, original_issue_raw = _repo_regular_file(
            issue_relative,
            prefix=".pm/issues",
            label="issue migration source",
            exclusive=True,
        )
        task_state = _existing_task_workspace_state(a.id)
    meta["review_policy"] = a.to
    meta["review_reason"] = a.reason.strip()
    write(p, meta, body)
    if task_state is not None:
        task_path, original_task_raw = task_state
        try:
            _atomic_text(task_path, _rebound_task_workspace_raw(a.id).decode("utf-8"))
        except BaseException:
            # Keep the issue and its immutable task binding in lockstep. If a concurrent writer
            # wins the rollback race, fail closed rather than claiming a coherent migration.
            try:
                _atomic_text(p, original_issue_raw.decode("utf-8"))
                _atomic_text(task_path, original_task_raw.decode("utf-8"))
            except BaseException as rollback_error:
                raise RuntimeError(
                    "review-policy migration rollback could not restore issue/task bytes"
                ) from rollback_error
            raise
    log(f"MIGRATION: {a.kind} {a.id} review_policy none -> {a.to} — {a.reason.strip()}")
    print(f"{a.kind} {a.id}: migrated review_policy none -> {a.to}; obtain a fresh terminal review")


def cmd_link(a):
    p = _find_issue(a.id) or (m_path(a.id) if os.path.exists(m_path(a.id)) else None)
    if not p:
        sys.exit(f"{a.id} not found")
    meta, body = read(p)
    meta["linear_id"] = a.linear_id
    write(p, meta, body)
    print(f"{a.id} linked -> {a.linear_id}")


def cmd_linear_close(a):
    """Resume only the exact durable completion finalization transaction."""
    if not a.transaction_id:
        sys.exit(
            "REFUSED: linear-close is not a standalone mutation; provide the exact completion "
            "finalization transaction with --transaction-id"
        )
    issue = _find_issue(a.id)
    milestone = m_path(a.id)
    if issue:
        _finish_linear_finalization("issue", a.id, a.transaction_id)
    elif os.path.exists(milestone):
        _finish_linear_finalization("milestone", a.id, a.transaction_id)
    else:
        sys.exit(f"item {a.id} not found")


def cmd_next(a):
    done = {i["id"] for i in all_issues() if i.get("status") == "done"}
    ready = []
    for i in all_issues(a.milestone):
        if i.get("status") in ("todo", "reopened"):
            deps = i.get("depends_on", []) or []
            if all(d in done for d in deps if d):
                ready.append(i)
    if not ready:
        print("no ready issues")
    for i in ready:
        _validate_iteration_snapshot(i)
        print(f"  READY {i['id']} (m={i.get('milestone')}, lane={i.get('dispatch_lane')}, "
              f"review={i.get('review_policy', 'independent')}, max_calls={i.get('max_model_calls')}, "
              f"parallel={i.get('parallel')}, "
              f"selected_mode={i.get('iteration_mode_selected', 'autonomous')}, "
              f"effective_mode={i.get('iteration_mode_effective', 'autonomous')}, "
              f"lease={i.get('human_checkin_lease_status', 'not_applicable')}, "
              f"fallback={i.get('human_unavailable_fallback', 'pause')}, "
              f"mode_decided={i.get('iteration_mode_decision_at', 'legacy')})")


def cmd_standup(a):
    original, config, legacy = _load_pm_config()
    iteration = _iteration_mode_state(config, legacy=legacy)
    print(
        f"=== STANDUP (debug={original.get('debug')}, "
        f"iteration_mode={iteration['selected_iteration_mode']}, "
        f"effective={iteration['effective_iteration_mode'] or 'paused'}, "
        f"lease={iteration['human_checkin_lease_status']}, "
        f"fallback={iteration['human_unavailable_fallback']}) ==="
    )
    for m in all_milestones():
        issues = all_issues(m["id"])
        by = {s: sum(1 for i in issues if i.get("status") == s) for s in STATUSES}
        tally = " ".join(f"{s}={by[s]}" for s in STATUSES if by[s])
        print(f"[{m.get('status'):>11}] {m['id']} {m.get('title')} :: {tally or 'no issues'}")
    finalizing = [
        (item.get("id"), item.get("finalization_transaction"))
        for item in [*all_issues(), *all_milestones()]
        if item.get("status") == "finalizing"
    ]
    if finalizing:
        rendered = ", ".join(f"{item}({transaction})" for item, transaction in finalizing)
        print(
            f"⚠ {len(finalizing)} completion finalization(s) PENDING Linear read-back: {rendered}; "
            "retry only the exact transaction with `pm.py linear-close <id> --transaction-id <id>`."
        )


def _validate_self_improve_note(note):
    """The self-improve close --note must ATTEST the checkpoint in labeled fields, so trace-observation is not
    silently skipped (the self-evolve half of 'never self-grade'). Returns an error string, or None if OK.
    Robust/lenient: checks for the four labels + a substantive reason when traces_read is 'none' — it does not
    parse paths (transcripts live outside the repo), so it detects a skipped attestation without false-blocking."""
    low = (note or "").lower()
    required = ("traces_read:", "trace_findings:", "memory_delta:", "distilled:")
    missing = [f for f in required if f not in low]
    if missing:
        return ("--note must ATTEST the checkpoint in labeled fields (so trace-observation isn't skipped) — "
                "missing: " + ", ".join(missing) + ". Required shape:\n"
                "  traces_read: <transcripts / .pm/reports / .log / .pm/eval / .pm/verdicts you observed, "
                "or 'none: <substantive reason>'>\n"
                "  trace_findings: <what the traces revealed beyond curated memory>\n"
                "  memory_delta: <the delta since the last checkpoint>\n"
                "  distilled: <BitLesson + any skill/hook, or why nothing was distillable>")
    tail = note[low.find("traces_read:") + len("traces_read:"):]
    val = tail.split("\n")[0].split("|")[0].strip()  # the traces_read value, up to the next field (newline or |)
    if val.lower().startswith("none") and len(val[4:].lstrip(" :").strip()) < 8:
        return ("traces_read: 'none' needs a substantive reason (e.g. 'none: M1, .pm/eval not yet populated') "
                "— observe the run traces whenever they exist.")
    return None


def cmd_self_improve(a):
    lifecycle = os.path.join(pmroot(), "self_evolve")
    pend = os.path.join(lifecycle, a.id + ".pending")
    sealed = os.path.join(lifecycle, a.id + ".sealed")
    if a.action != "close":
        sys.exit("only `self-improve close <id>` is supported")
    if os.path.exists(sealed):
        print(
            f"self-improve checkpoint {a.id} already sealed; rerun fresh acceptance/evidence, "
            "report, terminal review, verify-artifact, then set milestone done."
        )
        return
    if not os.path.exists(pend):
        sys.exit(f"no pending self-improve checkpoint for '{a.id}'")
    milestone = m_path(a.id)
    if not os.path.exists(milestone):
        sys.exit(f"milestone {a.id} not found")
    if read(milestone)[0].get("status") == "done":
        sys.exit("REFUSED: self-evolution must be sealed before milestone done and final review")
    if not a.note.strip():
        sys.exit("REFUSED: --note required — summarize what you distilled this milestone "
                 "(the BitLesson + any new skill/role/workflow/doc, or why nothing was distillable).")
    note_err = _validate_self_improve_note(a.note)
    if note_err:
        sys.exit("REFUSED: " + note_err)
    proj = os.path.dirname(pmroot())
    head = _trusted_git_head(proj)
    prior = sorted(glob.glob(os.path.join(lifecycle, "*.done")))
    prior = os.path.basename(prior[-1])[:-len(".done")] if prior else "(none)"
    # `.memory/` is the durable project store. Rewrite the one existing token, then atomically
    # rename it. If either operation is interrupted, only one lifecycle path exists and a retry is
    # safe; the old write-sealed/remove-pending sequence could expose a conflicting double state.
    # The payload records the attested self-evolve note and git head after the caller has written
    # source-layer observations and any warranted lemma.
    _atomic_text(
        pend,
        f"# self-improve checkpoint {a.id} - sealed before final review\n"
        f"note: {a.note}\ngit: {head}\nprior_checkpoint: {prior}\n",
    )
    os.replace(pend, sealed)
    print(
        f"self-improve checkpoint {a.id} SEALED (git {head[:8]}). Source-layer .memory and any "
        "separately reviewed generalized changes must now remain unchanged; rerun fresh "
        "acceptance/evidence, report, milestone review, verify-artifact, then set done."
    )


def main(
    argv=None,
    *,
    _test_catalog_runner=None,
    _test_reviewer_executable=None,
    _test_remote_gh=None,
):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init"); s.add_argument("--debug", action="store_true"); s.add_argument("--ui-mode", dest="ui_mode", choices=["linear", "interactive", "file"], default=None)
    s.add_argument("--iteration-mode", choices=ITERATION_MODES, default=None)
    s.add_argument("--human-checkin-lease-minutes", type=int, default=None)
    s.add_argument("--human-unavailable-fallback", choices=HUMAN_UNAVAILABLE_FALLBACKS, default=None)
    s = sub.add_parser("task-workspace")
    s.add_argument("action", choices=["init", "show", "record"])
    s.add_argument("id")
    s.add_argument("--kind", choices=TASK_EVENT_KINDS, default=None)
    s.add_argument("--summary", default="")
    s.add_argument("--experiment-path", default="")
    s.add_argument("--file", dest="files", action="append", default=[])
    s = sub.add_parser("iteration-mode")
    s.add_argument("action", choices=["show", "set", "renew"])
    s.add_argument("mode", nargs="?", choices=ITERATION_MODES, default=None)
    s.add_argument("--human-checkin-lease-minutes", type=int, default=None)
    s.add_argument("--human-unavailable-fallback", choices=HUMAN_UNAVAILABLE_FALLBACKS, default=None)
    s = sub.add_parser("milestone"); s.add_argument("action", choices=["add", "list"])
    s.add_argument("id", nargs="?"); s.add_argument("title", nargs="?", default="")
    s.add_argument("--order", type=int, default=0); s.add_argument("--depends", default="")
    s.add_argument("--acceptance", default=None)
    s.add_argument("--review-policy", choices=["independent"], required=False, default=None)
    s.add_argument("--review-reason", default=None)
    s = sub.add_parser("issue"); s.add_argument("id"); s.add_argument("milestone"); s.add_argument("title")
    s.add_argument("--lane", choices=["direct", "native-subagent", "workflow"], required=True)
    s.add_argument("--lane-reason", required=True)
    s.add_argument("--direct-scope", choices=DIRECT_SCOPES, default="")
    s.add_argument("--estimated-model-calls", type=int, required=True)
    s.add_argument("--max-model-calls", type=int, required=True)
    s.add_argument("--promotion-triggers", required=True)
    s.add_argument("--reasoning-posture", default=None)
    s.add_argument("--task-class", default=None)
    s.add_argument("--semantic-role", default=None)
    s.add_argument("--launch-receipt", default=None)
    s.add_argument("--launch-plan", default=None)
    s.add_argument("--acceptance", required=True)
    s.add_argument("--review-policy", choices=REVIEW_POLICIES, required=True)
    s.add_argument("--review-reason", required=True)
    s.add_argument("--depth", type=int, default=0); s.add_argument("--depends", default="")
    s.add_argument("--parallel", action="store_true")
    s = sub.add_parser("set"); s.add_argument("kind", choices=["milestone", "issue"])
    s.add_argument("id"); s.add_argument("status")
    s.add_argument("--merge-receipt", default="")
    s.add_argument("--send-receipt", default="")
    s.add_argument("--transaction-id", default="")
    s = sub.add_parser("reopen"); s.add_argument("kind", choices=["milestone", "issue"])
    s.add_argument("id"); s.add_argument("--reason", required=True)
    s = sub.add_parser("migrate-review-policy"); s.add_argument("kind", choices=["milestone", "issue"])
    s.add_argument("id"); s.add_argument("--to", choices=REVIEW_POLICIES, required=True)
    s.add_argument("--reason", required=True)
    s = sub.add_parser("link"); s.add_argument("id"); s.add_argument("linear_id")
    s = sub.add_parser("linear-close"); s.add_argument("id")
    s.add_argument("--transaction-id", required=False, default="")
    s = sub.add_parser("milestone-evolve"); s.add_argument("id")
    s.add_argument("--reopen", action="store_true")
    s.add_argument("--reason", default="")
    s = sub.add_parser("next"); s.add_argument("--milestone", default=None)
    sub.add_parser("standup")
    s = sub.add_parser("bind-workflow-receipts"); s.add_argument("id")
    s.add_argument("--receipt-manifest", required=True)
    s = sub.add_parser("report"); s.add_argument("kind", choices=["milestone", "issue"]); s.add_argument("id"); s.add_argument("--summary", required=True); s.add_argument("--linear", default="")
    s = sub.add_parser("self-improve"); s.add_argument("action", choices=["close"])
    s.add_argument("id"); s.add_argument("--note", default="")
    a = ap.parse_args(argv)
    # There is deliberately no CLI or production-environment equivalent. Tests may inject
    # deterministic catalog bytes, reviewer fixtures, and a bound remote fixture only through
    # this private Python entry point.
    a._test_catalog_runner = _test_catalog_runner
    a._test_reviewer_executable = _test_reviewer_executable
    a._test_remote_gh = _test_remote_gh
    if a.cmd == "milestone" and a.action == "add":
        if a.acceptance is None:
            ap.error("milestone add requires --acceptance")
        if a.review_policy is None:
            ap.error("milestone add requires --review-policy")
        if a.review_reason is None:
            ap.error("milestone add requires --review-reason")
    {"init": cmd_init, "iteration-mode": cmd_iteration_mode,
     "task-workspace": cmd_task_workspace,
     "milestone": cmd_milestone, "issue": cmd_issue, "set": cmd_set,
     "reopen": cmd_reopen, "link": cmd_link, "linear-close": cmd_linear_close,
     "migrate-review-policy": cmd_migrate_review_policy,
     "milestone-evolve": cmd_milestone_evolve, "next": cmd_next,
     "standup": cmd_standup, "self-improve": cmd_self_improve,
     "bind-workflow-receipts": cmd_bind_workflow_receipts,
     "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
