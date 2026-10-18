#!/usr/bin/env python3
"""Shared evidence and terminal-verdict contract for independent reviews.

The scripts using this module collect evidence; they never execute acceptance commands. A
builder/check agent records the fresh evidence, and a reviewer selected by semantic role from the
canonical workflow-model-roles/v3 policy judges the work. The evidence commitment makes a verdict
stale whenever any reviewed input, role identity, or role-document byte changes.

Launch authority comes from one place only: an exact fresh ``resolve_launch_spec`` result over the
bound policy bytes, which re-authenticates the native catalog for the reviewer and writer backends
on every call. The persisted ``catalogResolution`` block is an audit snapshot, so this module checks
only its structural schema; its ``status``, ``verifiedRoles``, ``verifiedBackends``, and
``evidenceReceipt`` neither grant nor deny a review. A bootstrap-unverified snapshot therefore
succeeds whenever the fresh resolution succeeds, and a forged ``verified`` snapshot cannot rescue a
launch the fresh resolution refuses.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


CONFIG_SYNC_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "update-platform-config"
    / "scripts"
    / "config-sync"
)
if str(CONFIG_SYNC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(CONFIG_SYNC_SCRIPTS))

import live_catalog  # noqa: E402
from resolve_launch_spec import (  # noqa: E402
    LaunchSpecError,
    resolve_launch_spec as resolve_canonical_launch_spec,
)
from validate_model_roles import (  # noqa: E402
    ValidationError as ModelRoleValidationError,
    validate as validate_canonical_model_roles,
)


TERMINALS = ("COMPLETE", "HUMAN_BLOCKED", "REVISE")

# COMPLETE authorizes project completion. HUMAN_BLOCKED authorizes a workflow stop and one concrete
# human action. Neither authority may come from repository bytes alone; both must be re-established
# by a fresh canonical reviewer during artifact verification. REVISE authorizes no stop or state
# transition and may therefore remain an audit-only artifact.
AUTHORITATIVE_TERMINALS = frozenset({"COMPLETE", "HUMAN_BLOCKED"})
HUMAN_REQUEST_KINDS = ("authorization", "decision", "credential", "external-action")
CONTROL_PREFIXES = (
    ".pm/evidence/",
    ".pm/review-briefs/",
    ".pm/reviewer-replies/",
    ".pm/reviews/",
    ".pm/receipts/human/",
    ".pm/receipts/merge/",
    ".pm/finalizations/",
    ".pm/self_evolve/",
    ".pm/verdicts/",
)
CATALOG_RESOLUTION_SCHEMA = "workflow-model-catalog-resolution/v1"
REVIEWER_OUTPUT_DIR = Path(".pm/reviewer-replies")
CANONICAL_MODEL_ROLES_PATH = "config/workflows/models.json"
OWNED_REVIEW_INVOCATION_SCHEMA = "verifier-owned-native-review/v1"

# A reviewer backend is launchable only if the canonical resolver can freshly authenticate a model
# catalog for it, because that fresh catalog is the sole launch authority. Backends without a fixed
# catalog command can never be authorized, so they get no launch profile here either. A test pins
# this set to ``live_catalog.ALLOWED_COMMANDS`` so the two cannot drift apart.
SUPPORTED_REVIEW_BACKENDS = frozenset({"claude", "codex", "cursor"})
NATIVE_REVIEWER_EXECUTABLES = {
    "claude": "claude",
    "codex": "codex",
    "cursor": "cursor-agent",
}
NATIVE_REVIEW_TIMEOUT_SECONDS = 3600
NATIVE_REVIEW_RETRY_DELAYS_SECONDS = (300, 600, 1800, 3600)
MAX_REVIEW_OUTPUT_BYTES = 4 * 1024 * 1024
_SYSTEM_NATIVE_EXECUTABLE_DIRS = (
    Path("/usr/local/bin"),
    Path("/opt/homebrew/bin"),
    Path("/usr/bin"),
    Path("/bin"),
    Path("/snap/bin"),
)

# The child PATH is replaced with system-only helper directories. The reviewer executable itself is
# never resolved through PATH; this value only decides where the bound reviewer may find helpers such
# as ``git``, so keeping a user-writable directory out of it removes a same-account swap surface.
_SYSTEM_HELPER_PATH = ("/usr/local/bin", "/usr/bin", "/bin")

# Git produces every reviewed input: the base, the changed paths, and both diffs. Resolving it
# through the caller PATH would therefore let a caller author the entire review bundle, so the
# search list is the system directories only. ``~/.local/bin`` is deliberately absent: it is
# writable by the same account the review is trying to hold to a commitment.
_SYSTEM_GIT_DIRS = _SYSTEM_NATIVE_EXECUTABLE_DIRS

# Top-level git options applied to every review invocation. ``core.fsmonitor`` and ``core.hooksPath``
# are the repository-config knobs that turn a read-only plumbing command into arbitrary local code
# execution. Replacement refs can silently substitute a different tree while preserving the printed
# object id, so they are disabled both here and in the rebuilt environment. These command-line values
# win over every repository config file.
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

_GIT_DIFF_OPTIONS = (
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
    "--ignore-submodules=none",
    "--no-color",
    "--no-color-moved",
    "--diff-algorithm=myers",
    "--no-indent-heuristic",
)

# Private worktree snapshots may redirect only these git paths. They are never copied from the
# caller environment; the values are absolute paths created for this review.
_GIT_SNAPSHOT_ENV_KEYS = frozenset(
    {
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    }
)

# Options that would hand the reviewer write authority, redirect it at a caller-chosen endpoint,
# account, or config, load caller code, widen its filesystem reach, or resume caller-seeded history.
# The built argv is checked against this set before launch, so a future profile edit cannot quietly
# reintroduce one.
FORBIDDEN_NATIVE_REVIEWER_OPTIONS = frozenset(
    {
        "-H",
        "-e",
        "-f",
        "-w",
        "--add-dir",
        "--agents",
        "--allowedTools",
        "--api-key",
        "--append-system-prompt",
        "--approve-mcps",
        "--auto-review",
        "--bedrock",
        "--continue",
        "--dangerously-skip-permissions",
        "--endpoint",
        "--force",
        "--fork-session",
        "--header",
        "--ignore-user-config",
        "--mcp-config",
        "--permission-prompt-tool",
        "--plugin-dir",
        "--resume",
        "--session-id",
        "--settings",
        "--system-prompt",
        "--worktree",
        "--worktree-base",
        "--yolo",
    }
)


class ContractError(RuntimeError):
    """A mechanical input/integrity failure.  It always routes back to REVISE."""


@dataclass(frozen=True)
class RoleIdentity:
    semantic_role: str
    backend: str
    provider: str
    upstream_provider_family: str
    model: str
    model_family: str
    effort: str
    intelligence_class: str
    dispatch_status: str

    def record(self) -> dict[str, str]:
        return {
            "semanticRole": self.semantic_role,
            "backend": self.backend,
            "provider": self.provider,
            "upstreamProviderFamily": self.upstream_provider_family,
            "model": self.model,
            "modelFamily": self.model_family,
            "effort": self.effort,
            "intelligenceClass": self.intelligence_class,
            "dispatchStatus": self.dispatch_status,
        }

    def digest(self) -> str:
        encoded = json.dumps(
            self.record(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class ReviewIdentityBinding:
    document_path: str
    document_sha256: str
    catalog_resolution_sha256: str
    capability_policy_sha256: str
    reasoning_policy_sha256: str
    reviewer_reasoning_postures: frozenset[str]
    builder_identities: tuple[RoleIdentity, ...]
    reviewer_identity: RoleIdentity
    raw_document: bytes


@dataclass(frozen=True)
class ReviewerLaunchReceipt:
    builder_role: str
    path: str
    sha256: str

    def record(self) -> dict[str, str]:
        return {
            "builderRole": self.builder_role,
            "path": self.path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ReviewerLaunchResolution:
    """Per-builder launch receipts plus the one concrete reviewer model they all agree on."""

    receipts: tuple[ReviewerLaunchReceipt, ...]
    catalog_model_id: str
    reviewer_catalog_command: dict[str, Any]


@dataclass(frozen=True)
class ReviewBundle:
    item_id: str
    item_kind: str
    base_sha: str
    head_sha: str
    changed_paths: tuple[str, ...]
    committed_diff: str
    worktree_diff: str
    criteria_text: str
    context_text: str
    evidence_text: str
    evidence_paths: tuple[str, ...]
    model_roles_path: str
    model_roles_sha256: str
    catalog_resolution_sha256: str
    reviewer_launch_receipts: tuple[ReviewerLaunchReceipt, ...]
    builder_identities: tuple[RoleIdentity, ...]
    reviewer_identity: RoleIdentity
    reviewer_catalog_model_id: str
    reviewer_catalog_command: dict[str, Any]
    commitment: str


@dataclass(frozen=True)
class TerminalVerdict:
    terminal: str
    human_request_kind: str = ""
    human_request: str = ""


@dataclass(frozen=True)
class RepoRegularFileBinding:
    relative_path: str
    raw_bytes: bytes
    mtime_ns: int


@dataclass(frozen=True)
class ReviewerOutputBinding:
    relative_path: str
    text: str
    sha256: str


@dataclass(frozen=True)
class NativeExecutableMember:
    """One digested member of the code that actually answers the review."""

    role: str
    path: str
    sha256: str

    def record(self) -> dict[str, str]:
        return {"role": self.role, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class NativeExecutableBinding:
    """One catalog-owned executable closure and the exact process invocation prefix.

    For Cursor, ``bound_closure_sha256`` includes the public catalog producer's opaque full install
    manifest in addition to the visible entrypoint/runtime/bundle members. It must therefore be
    carried from ``ResolvedExecutable`` rather than recomputed from ``closure`` alone.
    """

    path: Path
    sha256: str
    invocation_prefix: tuple[str, ...]
    closure: tuple[NativeExecutableMember, ...]
    bound_closure_sha256: str

    @property
    def closure_sha256(self) -> str:
        return self.bound_closure_sha256

    @property
    def interpreter(self) -> str:
        if len(self.invocation_prefix) == 2 and self.invocation_prefix[-1] == str(self.path):
            return self.invocation_prefix[0]
        return ""

    def launch_argv(self, argv: Sequence[str]) -> list[str]:
        """Replace a logical argv0 with the exact catalog-bound process prefix."""
        if not argv or argv[0] != str(self.path):
            raise ContractError("native reviewer logical argv does not match its bound entrypoint")
        return [*self.invocation_prefix, *argv[1:]]


@dataclass(frozen=True)
class NativeConfigBinding:
    """A non-secret native configuration file bound by path and digest.

    The verifier never records the file contents. Codex needs its OS-account provider/profile
    configuration to select the authenticated route, so the artifact records only the bound path and
    digest and replay rechecks both.
    """

    path: str
    sha256: str


@dataclass(frozen=True)
class NativeReviewerInvocation:
    output: ReviewerOutputBinding
    backend: str
    executable: str
    executable_sha256: str
    executable_closure_sha256: str
    config_path: str
    config_sha256: str
    model: str
    argv_sha256: str


@dataclass(frozen=True)
class ArtifactInputs:
    terminal: str
    human_request_kind: str
    human_request: str
    commitment: str
    base_sha: str
    head_sha: str
    goal_path: str
    evidence_paths: tuple[str, ...]
    model_roles_path: str
    model_roles_sha256: str
    catalog_resolution_sha256: str
    reviewer_launch_receipts: tuple[ReviewerLaunchReceipt, ...]
    builder_identities: tuple[RoleIdentity, ...]
    reviewer_identity: RoleIdentity
    allow_empty: bool
    reviewer_output_path: str
    reviewer_output_sha256: str
    reviewer_invocation_schema: str
    reviewer_backend: str
    reviewer_executable: str
    reviewer_executable_sha256: str
    reviewer_executable_closure_sha256: str
    reviewer_config_path: str
    reviewer_config_sha256: str
    reviewer_model: str
    reviewer_argv_sha256: str


_TRUSTED_GIT: tuple[Path, str] | None = None


def _bind_trusted_git() -> tuple[Path, str]:
    """Bind the git executable from the system directories, never from the caller PATH.

    The binding is remembered for the process and rechecked on every call, so a git that is swapped,
    rebuilt, or shadowed by an earlier system directory part-way through a review fails closed
    instead of silently producing a different bundle than the one already committed to.
    """
    global _TRUSTED_GIT
    for directory in _SYSTEM_GIT_DIRS:
        candidate = directory / "git"
        if not os.path.lexists(candidate):
            continue
        binding = _bind_native_executable_bytes(candidate, "trusted git executable")
        if _TRUSTED_GIT is not None and _TRUSTED_GIT != binding:
            raise ContractError(
                "trusted git executable drift: the bound git path or digest changed during this "
                "review"
            )
        _TRUSTED_GIT = binding
        return binding
    locations = ", ".join(str(value) for value in _SYSTEM_GIT_DIRS)
    raise ContractError(f"no trusted system git executable is installed in: {locations}")


def _git_environment() -> dict[str, str]:
    """Build git's environment from scratch so no caller variable survives into a review command.

    Every ``GIT_*`` selector — alternate config files, config key/value pairs, object and index
    directories, external diff and askpass helpers, ssh and proxy commands — is dropped by
    construction rather than denylisted. System and global config are additionally pinned to
    ``/dev/null`` so only the repository's own reviewed config applies.
    """
    return {
        "PATH": os.pathsep.join(_SYSTEM_HELPER_PATH),
        "HOME": str(_account_home()),
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


def _git_command_env(extra_env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Rebuild git's environment, optionally attaching one private snapshot redirect."""
    env = _git_environment()
    if extra_env is None:
        return env
    if set(extra_env) != _GIT_SNAPSHOT_ENV_KEYS:
        raise ContractError(
            "internal git snapshot environment must contain exactly the private index and "
            "object-store redirects"
        )
    for key in _GIT_SNAPSHOT_ENV_KEYS:
        value = extra_env[key]
        if not isinstance(value, str) or not value or not os.path.isabs(value):
            raise ContractError(f"{key} must be an absolute path")
    env.update(dict(extra_env))
    return env


def run_git(
    args: Sequence[str],
    cwd: Path,
    *,
    extra_env: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one git command with a bound absolute argv and a rebuilt environment."""
    executable, _ = _bind_trusted_git()
    try:
        worktree = cwd.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"cannot resolve the review worktree: {cwd}: {exc}") from exc
    run_kwargs: dict[str, Any] = {
        "cwd": worktree,
        "capture_output": True,
        "check": False,
        "env": _git_command_env(extra_env),
    }
    if input_bytes is None:
        run_kwargs["text"] = True
        run_kwargs["stdin"] = subprocess.DEVNULL
    else:
        run_kwargs["input"] = input_bytes
    try:
        result = subprocess.run(
            [
                str(executable),
                *_GIT_SAFETY_OPTIONS,
                f"--work-tree={worktree}",
                *args,
            ],
            **run_kwargs,
        )
    except OSError as exc:
        raise ContractError(f"cannot run git: {exc}") from exc
    _bind_trusted_git()
    if input_bytes is None:
        return result
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def _git_value(args: Sequence[str], cwd: Path) -> str:
    result = run_git(args, cwd)
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    return result.stdout.strip().splitlines()[0]


def validate_item_id(item_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item_id):
        raise ContractError("item id must contain only letters, digits, '.', '_' and '-'")
    return item_id


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"sha256:{_hash_bytes(encoded)}"


def _role_name(value: str, label: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise ContractError(f"{label} must be a semantic role name")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    return value.strip()


def _identity_from_record(value: Any, label: str) -> RoleIdentity:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    expected = {
        "semanticRole",
        "backend",
        "provider",
        "upstreamProviderFamily",
        "model",
        "modelFamily",
        "effort",
        "intelligenceClass",
        "dispatchStatus",
    }
    if set(value) != expected:
        raise ContractError(f"{label} must contain the exact role-identity fields")
    return RoleIdentity(
        semantic_role=_role_name(_string(value.get("semanticRole"), label), label),
        backend=_string(value.get("backend"), f"{label}.backend"),
        provider=_string(value.get("provider"), f"{label}.provider"),
        upstream_provider_family=_string(
            value.get("upstreamProviderFamily"), f"{label}.upstreamProviderFamily"
        ),
        model=_string(value.get("model"), f"{label}.model"),
        model_family=_string(value.get("modelFamily"), f"{label}.modelFamily"),
        effort=_string(value.get("effort"), f"{label}.effort"),
        intelligence_class=_string(
            value.get("intelligenceClass"), f"{label}.intelligenceClass"
        ),
        dispatch_status=_string(value.get("dispatchStatus"), f"{label}.dispatchStatus"),
    )


def _resolve_role(*, name: str, roles: Mapping[str, Any]) -> RoleIdentity:
    name = _role_name(name, "role")
    raw = roles.get(name)
    if not isinstance(raw, dict):
        raise ContractError(f"semantic role {name!r} is not declared")
    fields = {
        key: raw.get(key)
        for key in (
            "backend",
            "provider",
            "upstreamProviderFamily",
            "model",
            "modelFamily",
            "effort",
            "intelligenceClass",
            "dispatchStatus",
        )
    }
    identity = _identity_from_record(
        {"semanticRole": name, **fields}, f"role {name!r}"
    )
    if identity.dispatch_status != "active":
        raise ContractError(f"semantic role {name!r} is not active")
    return identity


def _pairing_for_writer(policy: Mapping[str, Any], writer: RoleIdentity) -> Mapping[str, Any] | None:
    pairings = policy.get("reviewPairings")
    if not isinstance(pairings, list):
        raise ContractError("capabilityPolicy.reviewPairings must be an array")
    matches: list[Mapping[str, Any]] = []
    for raw_pair in pairings:
        if not isinstance(raw_pair, dict):
            raise ContractError("capabilityPolicy.reviewPairings entries must be objects")
        raw_writer = raw_pair.get("writer")
        raw_reviewer = raw_pair.get("reviewer")
        if not isinstance(raw_writer, dict) or not isinstance(raw_reviewer, dict):
            raise ContractError("each review pairing requires writer and reviewer objects")
        efforts = raw_writer.get("efforts")
        if not isinstance(efforts, list) or not all(isinstance(item, str) for item in efforts):
            raise ContractError("review pairing writer efforts must be a string array")
        if (
            raw_writer.get("upstreamProviderFamily") == writer.upstream_provider_family
            and raw_writer.get("modelFamily") == writer.model_family
            and writer.effort in efforts
        ):
            matches.append(raw_reviewer)
    if len(matches) > 1:
        raise ContractError(f"ambiguous exact review pairing for writer role {writer.semantic_role!r}")
    return matches[0] if matches else None


def _enforce_reviewer_policy(
    *, policy: Mapping[str, Any], writer: RoleIdentity, reviewer: RoleIdentity
) -> None:
    if reviewer.intelligence_class != "verification":
        raise ContractError(
            f"reviewer role {reviewer.semantic_role!r} is not a verification role"
        )
    if reviewer.effort != "max":
        raise ContractError(f"reviewer role {reviewer.semantic_role!r} must resolve to Max effort")
    if reviewer.upstream_provider_family == writer.upstream_provider_family:
        raise ContractError(
            f"reviewer role {reviewer.semantic_role!r} shares upstream provider family "
            f"{writer.upstream_provider_family!r} with writer role {writer.semantic_role!r}"
        )

    exact = _pairing_for_writer(policy, writer)
    if exact is not None:
        expected = {
            "role": reviewer.semantic_role,
            "upstreamProviderFamily": reviewer.upstream_provider_family,
            "modelFamily": reviewer.model_family,
            "effort": reviewer.effort,
        }
        if dict(exact) != expected:
            raise ContractError(
                f"reviewer role {reviewer.semantic_role!r} does not satisfy the exact review "
                f"pairing for writer role {writer.semantic_role!r}"
            )
        return

    if (
        writer.upstream_provider_family,
        writer.model_family,
    ) in {
        ("openai", "gpt-6-astra"),
        ("anthropic", "claude-opus-5"),
    } and writer.effort in {"max", "xhigh"}:
        raise ContractError(
            f"exact Astra/Opus review pairing is missing for writer role {writer.semantic_role!r}"
        )

    defaults = policy.get("providerReviewerDefaults")
    if not isinstance(defaults, dict):
        raise ContractError("capabilityPolicy.providerReviewerDefaults must be an object")
    default = defaults.get(writer.upstream_provider_family)
    if not isinstance(default, dict):
        raise ContractError(
            f"no reviewer default exists for writer upstream family "
            f"{writer.upstream_provider_family!r}"
        )
    expected_role = _string(default.get("reviewerRole"), "reviewer default role")
    expected_effort = _string(default.get("requiredEffort"), "reviewer default effort")
    if set(default) != {"reviewerRole", "requiredEffort"}:
        raise ContractError("reviewer default must contain only reviewerRole and requiredEffort")
    if expected_effort != "max":
        raise ContractError("provider reviewer defaults must require Max effort")
    if reviewer.semantic_role != expected_role or reviewer.effort != expected_effort:
        raise ContractError(
            f"reviewer role {reviewer.semantic_role!r} does not satisfy providerReviewerDefaults "
            f"for writer role {writer.semantic_role!r}"
        )


def resolve_review_identities(
    *,
    repo: Path,
    model_roles_path: Path,
    builder_roles: Sequence[str],
    reviewer_role: str,
) -> ReviewIdentityBinding:
    repo = repo.resolve()
    requested = model_roles_path if model_roles_path.is_absolute() else repo / model_roles_path
    try:
        binding = read_repo_regular_file(repo, requested, "model-role document")
        relative = binding.relative_path
        raw_bytes = binding.raw_bytes
        document = json.loads(raw_bytes)
    except ContractError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read model-role document: {requested}: {exc}") from exc
    if relative != CANONICAL_MODEL_ROLES_PATH:
        raise ContractError(
            "model-role policy must be the project-root canonical file at "
            f"{CANONICAL_MODEL_ROLES_PATH}"
        )
    if not isinstance(document, dict) or document.get("schema") != "workflow-model-roles/v3":
        raise ContractError("model-role document must use workflow-model-roles/v3")
    try:
        validate_canonical_model_roles(document)
    except ModelRoleValidationError as exc:
        raise ContractError(f"canonical model-role policy is invalid: {exc}") from exc

    # Structural schema only. Whether the snapshot says bootstrap-unverified or verified, and whatever
    # roles, backends, or evidence receipt it lists, is audit metadata that the resolver records into
    # its receipt. Launch authority is the fresh resolution in resolve_reviewer_launch_receipts.
    catalog = document.get("catalogResolution")
    if not isinstance(catalog, dict):
        raise ContractError("model-role document needs catalogResolution")
    if catalog.get("schema") != CATALOG_RESOLUTION_SCHEMA:
        raise ContractError(f"catalogResolution must use {CATALOG_RESOLUTION_SCHEMA}")
    roles = document.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ContractError("model-role document needs a non-empty roles object")
    policy = document.get("capabilityPolicy")
    if not isinstance(policy, dict) or policy.get("schema") != "workflow-capability-policy/v1":
        raise ContractError("model-role document needs workflow-capability-policy/v1")
    reasoning_policy = document.get("reasoningPolicy")
    if (
        not isinstance(reasoning_policy, dict)
        or reasoning_policy.get("schema") != "workflow-reasoning-policy/v2"
    ):
        raise ContractError("model-role document needs workflow-reasoning-policy/v2")

    builder_names = tuple(sorted({_role_name(value, "--builder-role") for value in builder_roles}))
    if not builder_names:
        raise ContractError("at least one --builder-role is required")
    reviewer_name = _role_name(reviewer_role, "--reviewer-role")
    builders = tuple(_resolve_role(name=name, roles=roles) for name in builder_names)
    reviewer = _resolve_role(name=reviewer_name, roles=roles)
    for writer in builders:
        _enforce_reviewer_policy(policy=policy, writer=writer, reviewer=reviewer)
    task_class_roles = policy.get("taskClassRoles")
    if (
        not isinstance(task_class_roles, dict)
        or not isinstance(task_class_roles.get("verification"), list)
        or reviewer.semantic_role not in task_class_roles["verification"]
    ):
        raise ContractError(
            f"reviewer role {reviewer.semantic_role!r} is not qualified for verification"
        )
    postures = reasoning_policy.get("postures")
    if not isinstance(postures, dict):
        raise ContractError("reasoningPolicy.postures must be an object")
    allowed_postures = frozenset(
        name
        for name, posture in postures.items()
        if isinstance(name, str)
        and isinstance(posture, dict)
        and isinstance(posture.get("allowedRoles"), list)
        and reviewer.semantic_role in posture["allowedRoles"]
    )
    if not allowed_postures:
        raise ContractError(
            f"reviewer role {reviewer.semantic_role!r} is not allowed by any reasoning posture"
        )
    return ReviewIdentityBinding(
        document_path=relative,
        document_sha256=f"sha256:{_hash_bytes(raw_bytes)}",
        catalog_resolution_sha256=_hash_json(catalog),
        capability_policy_sha256=_hash_json(policy),
        reasoning_policy_sha256=_hash_json(reasoning_policy),
        reviewer_reasoning_postures=allowed_postures,
        builder_identities=builders,
        reviewer_identity=reviewer,
        raw_document=raw_bytes,
    )


def _load_reviewer_launch_receipt(
    repo: Path, receipt_path: Path
) -> tuple[RepoRegularFileBinding, Any]:
    requested = receipt_path if receipt_path.is_absolute() else repo / receipt_path
    try:
        binding = read_repo_regular_file(repo, requested, "reviewer launch receipt")
        return binding, json.loads(binding.raw_bytes)
    except ContractError:
        raise
    except json.JSONDecodeError as exc:
        raise ContractError(f"cannot read reviewer launch receipt: {requested}: {exc}") from exc


_CATALOG_EVIDENCE_FIELDS = {
    "backend",
    "catalogBytesSha256",
    "command",
    "coverage",
    "exitCode",
    "stderrSha256",
}
_CATALOG_COMMAND_FIELDS = {
    "argv",
    "executable",
    "executableSha256",
    "invokedArgv",
    "executionClosure",
    "executionClosureSha256",
}
_CATALOG_CLOSURE_FIELDS = {"role", "path", "sha256"}
_CATALOG_CLOSURE_ROLES = frozenset({"interpreter", "entrypoint", "runtime", "bundle"})


def _catalog_invocation_prefix(
    command: Mapping[str, Any], backend: str, *, allow_synthetic: bool
) -> tuple[str, ...]:
    expected = live_catalog.ALLOWED_COMMANDS.get(backend)
    invoked = command.get("invokedArgv")
    executable = command.get("executable")
    if not isinstance(expected, tuple) or not isinstance(invoked, list):
        raise ContractError(f"fresh {backend} catalog command has no canonical invocation prefix")
    if allow_synthetic and (
        executable == f"test-runner:{expected[0]}" and tuple(invoked) == expected
    ):
        return (expected[0],)
    tail = expected[1:]
    prefix_length = len(invoked) - len(tail)
    if prefix_length < 1 or tuple(invoked[prefix_length:]) != tail:
        raise ContractError(f"fresh {backend} catalog command has a malformed fixed invocation prefix")
    prefix = tuple(invoked[:prefix_length])
    direct_or_script = (
        len(prefix) in (1, 2)
        and all(isinstance(value, str) and value and os.path.isabs(value) for value in prefix)
        and prefix[-1] == executable
    )
    cursor_runtime = (
        backend == "cursor"
        and len(prefix) == 3
        and isinstance(prefix[0], str)
        and os.path.isabs(prefix[0])
        and prefix[1] == "--use-system-ca"
        and isinstance(prefix[2], str)
        and os.path.isabs(prefix[2])
    )
    if not direct_or_script and not cursor_runtime:
        raise ContractError(f"fresh {backend} catalog command has a malformed fixed invocation prefix")
    return prefix


def _validated_catalog_command(
    evidence: Any, backend: str, *, allow_synthetic: bool
) -> dict[str, Any]:
    """Validate and retain the public catalog-owned executable closure record."""
    if not isinstance(evidence, dict) or set(evidence) != _CATALOG_EVIDENCE_FIELDS:
        raise ContractError(f"fresh {backend} catalog evidence has unsupported fields")
    if (
        evidence.get("backend") != backend
        or evidence.get("exitCode") != 0
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(evidence.get("catalogBytesSha256")))
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(evidence.get("stderrSha256")))
    ):
        raise ContractError(f"fresh {backend} catalog evidence has malformed provenance")
    command = evidence.get("command")
    if not isinstance(command, dict) or set(command) != _CATALOG_COMMAND_FIELDS:
        raise ContractError(f"fresh {backend} catalog command has unsupported fields")
    expected = live_catalog.ALLOWED_COMMANDS.get(backend)
    if not isinstance(expected, tuple) or command.get("argv") != list(expected):
        raise ContractError(f"fresh {backend} catalog command is not the canonical native probe")
    executable = command.get("executable")
    executable_sha256 = command.get("executableSha256")
    closure_sha256 = command.get("executionClosureSha256")
    if (
        not isinstance(executable, str)
        or not executable
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(executable_sha256))
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(closure_sha256))
    ):
        raise ContractError(f"fresh {backend} catalog executable binding is malformed")
    prefix = _catalog_invocation_prefix(command, backend, allow_synthetic=allow_synthetic)
    closure = command.get("executionClosure")
    if not isinstance(closure, list) or not closure:
        raise ContractError(f"fresh {backend} catalog execution closure is empty")
    identities: set[tuple[str, str]] = set()
    entrypoints = 0
    interpreters: list[str] = []
    runtimes: list[str] = []
    bundles: list[str] = []
    closure_lines: list[str] = []
    for index, raw_entry in enumerate(closure):
        if not isinstance(raw_entry, dict) or set(raw_entry) != _CATALOG_CLOSURE_FIELDS:
            raise ContractError(
                f"fresh {backend} catalog closure entry {index + 1} is malformed"
            )
        role = raw_entry.get("role")
        path = raw_entry.get("path")
        digest = raw_entry.get("sha256")
        synthetic_path = allow_synthetic and path == executable
        if (
            role not in _CATALOG_CLOSURE_ROLES
            or not isinstance(path, str)
            or not path
            or (not synthetic_path and not os.path.isabs(path))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(digest))
        ):
            raise ContractError(
                f"fresh {backend} catalog closure entry {index + 1} is malformed"
            )
        identity = (role, path)
        if identity in identities:
            raise ContractError(f"fresh {backend} catalog execution closure contains a duplicate")
        identities.add(identity)
        closure_lines.append(f"{role}\t{path}\t{digest}")
        if role == "entrypoint":
            entrypoints += 1
            if path != executable or digest != executable_sha256:
                raise ContractError(
                    f"fresh {backend} catalog entrypoint disagrees with its executable binding"
                )
        elif role == "interpreter":
            interpreters.append(path)
        elif role == "runtime":
            runtimes.append(path)
        elif role == "bundle":
            bundles.append(path)
    if entrypoints != 1:
        raise ContractError(f"fresh {backend} catalog closure must bind one entrypoint")
    if allow_synthetic and executable.startswith("test-runner:"):
        prefix_bound = prefix == (expected[0],) and len(closure) == 1
    elif backend == "cursor":
        prefix_bound = (
            len(interpreters) == 0
            and runtimes == [prefix[0]]
            and bundles == [prefix[2]]
        )
    else:
        prefix_bound = (
            prefix[-1] == executable
            and len(interpreters) == len(prefix) - 1
            and (not interpreters or interpreters == [prefix[0]])
            and not runtimes
            and not bundles
        )
    if not prefix_bound:
        raise ContractError(
            f"fresh {backend} catalog closure does not bind its invocation prefix"
        )
    if backend != "cursor" or executable.startswith("test-runner:"):
        visible_digest = f"sha256:{_hash_bytes(chr(10).join(closure_lines).encode('utf-8'))}"
        if visible_digest != closure_sha256:
            raise ContractError(f"fresh {backend} catalog execution closure digest is invalid")
    # Round-trip through canonical JSON to detach the retained command from the parsed receipt.
    return json.loads(json.dumps(command, sort_keys=True, separators=(",", ":")))


def _resolve_loaded_reviewer_launch_receipt(
    *,
    binding: RepoRegularFileBinding,
    receipt: Any,
    identity: ReviewIdentityBinding,
    builder: RoleIdentity,
    catalog_runner: Callable[[tuple[str, ...]], tuple[int, bytes, bytes]] | None = None,
) -> tuple[ReviewerLaunchReceipt, str, dict[str, Any]]:
    if not isinstance(receipt, dict) or receipt.get("schema") != "workflow-native-launch-receipt/v1":
        raise ContractError("reviewer launch receipt must use workflow-native-launch-receipt/v1")
    request = receipt.get("request")
    if not isinstance(request, dict) or set(request) != {
        "taskClass",
        "reasoningPosture",
        "semanticRole",
        "writerRole",
        "reviewMode",
    } or (
        request.get("taskClass") != "verification"
        or request.get("semanticRole") != identity.reviewer_identity.semantic_role
        or request.get("reasoningPosture") not in identity.reviewer_reasoning_postures
        or request.get("reviewMode") != "exact-opposite"
    ):
        raise ContractError("reviewer launch receipt request does not match the review assignment")
    if request.get("writerRole") != builder.semantic_role:
        raise ContractError("reviewer launch receipt writerRole does not match the builder role")
    try:
        canonical_receipt = resolve_canonical_launch_spec(
            identity.raw_document,
            task_class=request["taskClass"],
            reasoning_posture=request["reasoningPosture"],
            semantic_role=request["semanticRole"],
            review_mode=request["reviewMode"],
            writer_role=request["writerRole"],
            catalog_runner=catalog_runner,
        )
    except LaunchSpecError as exc:
        raise ContractError(f"canonical reviewer launch resolution failed: {exc}") from exc
    # The fresh resolution above is the authorization. Byte equality is what makes the persisted
    # receipt usable at all, so nothing a caller can write into the file grants a launch the fresh
    # resolution refused.
    if receipt != canonical_receipt:
        raise ContractError(
            "reviewer launch receipt does not exactly match the canonical v3 resolver output"
        )
    resolved_record = receipt.get("resolved")
    writer_record = receipt.get("writer")
    if not isinstance(resolved_record, dict) or not isinstance(writer_record, dict):
        raise ContractError("reviewer launch receipt identities must be objects")
    resolved_without_catalog = dict(resolved_record)
    writer_without_catalog = dict(writer_record)
    catalog_model_id = _string(
        resolved_without_catalog.pop("catalogModelId", None),
        "reviewer launch resolved catalogModelId",
    )
    _string(
        writer_without_catalog.pop("catalogModelId", None),
        "reviewer launch writer catalogModelId",
    )
    resolved = _identity_from_record(
        resolved_without_catalog, "reviewer launch resolved identity"
    )
    writer = _identity_from_record(writer_without_catalog, "reviewer launch writer identity")
    if resolved != identity.reviewer_identity or writer != builder:
        raise ContractError("reviewer launch receipt identities do not match the bound role document")
    # These checks detect a resolver schema change, not a caller edit: a receipt that reached this
    # point already equals the fresh resolver output byte for byte. catalogStatus is recorded from the
    # persisted snapshot and is deliberately not constrained to any value.
    policy = receipt.get("policy")
    if not isinstance(policy, dict) or set(policy) != {
        "modelsSchema",
        "modelsDocumentSha256",
        "catalogResolutionSha256",
        "capabilityPolicySha256",
        "reasoningPolicySha256",
        "catalogStatus",
        "catalogSource",
        "catalogEvidenceReceipt",
        "liveCatalogAuthenticatedByResolver",
        "freshCatalogEvidence",
    } or (
        policy.get("modelsSchema") != "workflow-model-roles/v3"
        or policy.get("modelsDocumentSha256") != identity.document_sha256
        or policy.get("catalogResolutionSha256") != identity.catalog_resolution_sha256
        or policy.get("capabilityPolicySha256") != identity.capability_policy_sha256
        or policy.get("reasoningPolicySha256") != identity.reasoning_policy_sha256
        or policy.get("catalogSource") != "authenticated-native-cli"
        or policy.get("liveCatalogAuthenticatedByResolver") is not True
    ):
        raise ContractError("reviewer launch receipt policy does not match the bound model-role document")
    fresh_evidence = policy.get("freshCatalogEvidence")
    if not isinstance(fresh_evidence, dict) or not fresh_evidence:
        raise ContractError("reviewer launch receipt carries no fresh catalog evidence")
    for backend in (identity.reviewer_identity.backend, builder.backend):
        if backend not in fresh_evidence:
            raise ContractError(
                f"reviewer launch receipt has no fresh catalog evidence for backend {backend!r}"
            )
    reviewer_catalog_command = _validated_catalog_command(
        fresh_evidence[identity.reviewer_identity.backend],
        identity.reviewer_identity.backend,
        allow_synthetic=catalog_runner is not None,
    )
    claimed = receipt.get("receiptSha256")
    without_digest = {key: value for key, value in receipt.items() if key != "receiptSha256"}
    if claimed != _hash_json(without_digest):
        raise ContractError("reviewer launch receipt self-digest is invalid")
    if set(receipt) != {"schema", "request", "resolved", "writer", "policy", "receiptSha256"}:
        raise ContractError("reviewer launch receipt fields do not match schema v1")
    return (
        ReviewerLaunchReceipt(
            builder_role=builder.semantic_role,
            path=binding.relative_path,
            sha256=f"sha256:{_hash_bytes(binding.raw_bytes)}",
        ),
        catalog_model_id,
        reviewer_catalog_command,
    )


def resolve_reviewer_launch_receipts(
    *,
    repo: Path,
    receipt_paths: Sequence[Path],
    identity: ReviewIdentityBinding,
    catalog_runner: Callable[[tuple[str, ...]], tuple[int, bytes, bytes]] | None = None,
) -> ReviewerLaunchResolution:
    if len(receipt_paths) != len(identity.builder_identities):
        raise ContractError(
            "provide exactly one --reviewer-launch-receipt for each distinct --builder-role"
        )
    builders = {value.semantic_role: value for value in identity.builder_identities}
    resolved: dict[str, ReviewerLaunchReceipt] = {}
    catalog_model_ids: dict[str, str] = {}
    catalog_commands: dict[str, dict[str, Any]] = {}
    for path in receipt_paths:
        binding, receipt = _load_reviewer_launch_receipt(repo, path)
        request = receipt.get("request") if isinstance(receipt, dict) else None
        writer_role = request.get("writerRole") if isinstance(request, dict) else None
        builder = builders.get(writer_role)
        if builder is None:
            raise ContractError(
                "reviewer launch receipt writerRole is not one of the bound builder roles"
            )
        if writer_role in resolved:
            raise ContractError(
                f"duplicate reviewer launch receipt for builder role {writer_role!r}"
            )
        (
            resolved[writer_role],
            catalog_model_ids[writer_role],
            catalog_commands[writer_role],
        ) = (
            _resolve_loaded_reviewer_launch_receipt(
                binding=binding,
                receipt=receipt,
                identity=identity,
                builder=builder,
                catalog_runner=catalog_runner,
            )
        )
    missing = set(builders) - set(resolved)
    if missing:
        raise ContractError(
            "reviewer launch receipts do not cover builder role(s): " + ", ".join(sorted(missing))
        )
    # One reviewer role resolves to one concrete catalog model. Per-builder receipts that disagree
    # mean the fresh catalogs disagreed between resolutions, so there is no single model this review
    # can honestly claim to have run.
    distinct = sorted(set(catalog_model_ids.values()))
    if len(distinct) != 1:
        raise ContractError(
            "per-builder fresh launch receipts disagree on the reviewer catalog model id: "
            + ", ".join(distinct)
        )
    distinct_commands = {
        json.dumps(value, sort_keys=True, separators=(",", ":"))
        for value in catalog_commands.values()
    }
    if len(distinct_commands) != 1:
        raise ContractError(
            "per-builder fresh launch receipts disagree on the reviewer executable closure"
        )
    return ReviewerLaunchResolution(
        receipts=tuple(resolved[name] for name in sorted(resolved)),
        catalog_model_id=distinct[0],
        reviewer_catalog_command=json.loads(next(iter(distinct_commands))),
    )


def resolve_base(repo: Path, explicit: str) -> tuple[str, str]:
    """Resolve an auditable task base without a mutable project-global baseline file."""
    head = _git_value(["rev-parse", "--verify", "HEAD^{commit}"], repo)
    if not head:
        raise ContractError("HEAD does not resolve to a commit")

    if explicit:
        base = _git_value(
            ["rev-parse", "--verify", "--end-of-options", f"{explicit}^{{commit}}"],
            repo,
        )
        if not base:
            raise ContractError(f"diff base {explicit!r} does not resolve to a commit")
    else:
        candidates: list[str] = []
        for ref in ("origin/main", "origin/master"):
            candidate = _git_value(["rev-parse", "--verify", f"{ref}^{{commit}}"], repo)
            if candidate and candidate not in candidates:
                candidates.append(candidate)
        upstream = _git_value(["rev-parse", "--verify", "@{upstream}^{commit}"], repo)
        if upstream and upstream not in candidates:
            candidates.append(upstream)
        base = ""
        for candidate in candidates:
            merged = _git_value(["merge-base", head, candidate], repo)
            if merged:
                base = merged
                break
        if not base:
            raise ContractError(
                "no upstream task base resolves; pass --base <task-start commit/ref> "
                "(the reviewer will not guess a local-only baseline)"
            )

    ancestor = run_git(["merge-base", "--is-ancestor", base, head], repo)
    if ancestor.returncode != 0:
        raise ContractError(
            f"diff base {base!r} is not an ancestor of HEAD; refusing an incomplete/ambiguous diff"
        )
    return base, head


def _split_nul(value: str) -> list[str]:
    return [part for part in value.split("\0") if part]


def _repo_relative(repo: Path, path: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError as exc:
        raise ContractError(f"review input path escapes the repository: {path}") from exc


def resolve_evidence(repo: Path, item_id: str, requested: Sequence[str]) -> tuple[Path, ...]:
    raw_paths = list(requested) or [f".pm/evidence/{item_id}.md"]
    resolved: list[Path] = []
    for raw in raw_paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = repo / candidate
        binding = read_repo_regular_file(repo, candidate, "acceptance evidence")
        if not binding.raw_bytes:
            raise ContractError(f"acceptance evidence is empty: {binding.relative_path}")
        lexical = repo / binding.relative_path
        if lexical not in resolved:
            resolved.append(lexical)
    return tuple(resolved)


def _is_control_path(path: str, evidence_paths: set[str], ignored_paths: set[str]) -> bool:
    normalized = path.replace(os.sep, "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return (
        normalized in evidence_paths
        or normalized in ignored_paths
        or any(normalized.startswith(prefix) for prefix in CONTROL_PREFIXES)
    )


def _git_blob_digest(payload: bytes, object_format: str) -> str:
    """Digest worktree bytes the same way Git names a blob, without reading the index cache."""
    header = f"blob {len(payload)}\0".encode("ascii") + payload
    if object_format == "sha1":
        return hashlib.sha1(header, usedforsecurity=False).hexdigest()
    if object_format == "sha256":
        return hashlib.sha256(header).hexdigest()
    raise ContractError(f"unsupported git object format: {object_format}")


def _null_git_oid(object_format: str) -> str:
    if object_format == "sha1":
        return "0" * 40
    if object_format == "sha256":
        return "0" * 64
    raise ContractError(f"unsupported git object format: {object_format}")


def _parse_ls_tree(stdout: str) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for record in _split_nul(stdout):
        try:
            meta, path = record.split("\t", 1)
            mode, _obj_type, object_id = meta.split(" ", 2)
        except ValueError as exc:
            raise ContractError(f"git ls-tree produced an unreadable record: {record!r}") from exc
        entries[Path(path).as_posix()] = (mode, object_id)
    return entries


def _worktree_tracked_mode_and_bytes(path: Path) -> tuple[str, bytes] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return "120000", os.readlink(path).encode("utf-8", errors="surrogateescape")
    if stat.S_ISREG(info.st_mode):
        mode = "100755" if info.st_mode & 0o100 else "100644"
        return mode, path.read_bytes()
    return None


def _tracked_worktree_byte_changes(
    repo: Path,
    tracked_paths: Sequence[str],
    evidence_rel: set[str],
    ignored_rel: set[str],
) -> set[str]:
    """Inventory tracked worktree bytes against HEAD without Git's index stat shortcuts.

    ``git diff HEAD`` can skip a dirty regular file or symlink when the cached size/mtime still
    match. Deleted paths, type changes, and gitlinks stay on the existing diff/ls-files walk.
    """
    object_format = _git_value(["rev-parse", "--show-object-format"], repo) or "sha1"
    if object_format not in {"sha1", "sha256"}:
        raise ContractError(f"unsupported git object format: {object_format}")
    head = run_git(["ls-tree", "-r", "-z", "--full-tree", "HEAD"], repo)
    if head.returncode != 0:
        raise ContractError(
            f"git ls-tree HEAD failed: {(head.stderr or head.stdout).strip()}"
        )
    head_entries = _parse_ls_tree(head.stdout)
    found: set[str] = set()
    for path in tracked_paths:
        normalized = Path(path).as_posix()
        if _is_control_path(normalized, evidence_rel, ignored_rel):
            continue
        work = _worktree_tracked_mode_and_bytes(repo / normalized)
        if work is None:
            continue
        mode, payload = work
        digest = _git_blob_digest(payload, object_format)
        head_meta = head_entries.get(normalized)
        if head_meta is None or head_meta[0] != mode or head_meta[1] != digest:
            found.add(normalized)
    return found


def collect_changed_paths(
    repo: Path, base_sha: str, evidence: Sequence[Path], ignored_paths: Sequence[Path]
) -> tuple[str, ...]:
    index = run_git(["ls-files", "-v", "-z"], repo)
    if index.returncode != 0:
        raise ContractError(
            f"git ls-files -v failed: {(index.stderr or index.stdout).strip()}"
        )
    hidden_by_index = []
    tracked_paths = []
    for record in _split_nul(index.stdout):
        if len(record) < 3 or record[1] != " ":
            continue
        tag, path = record[0], record[2:]
        if tag == "S" or tag.islower():
            hidden_by_index.append(path)
        else:
            tracked_paths.append(path)
    if hidden_by_index:
        raise ContractError(
            "review worktree uses assume-unchanged or skip-worktree index flags that can hide "
            "reviewed bytes: " + ", ".join(sorted(hidden_by_index))
        )
    commands = (
        [
            "diff",
            *_GIT_DIFF_OPTIONS,
            "--name-only",
            "-z",
            "--diff-filter=ACDMRTUXB",
            base_sha,
            "HEAD",
        ],
        [
            "diff",
            *_GIT_DIFF_OPTIONS,
            "--name-only",
            "-z",
            "--diff-filter=ACDMRTUXB",
            "HEAD",
        ],
        # Do not use --exclude-standard: .git/info/exclude and core.excludesFile are mutable Git
        # metadata outside the evidence commitment and must not be able to hide a worktree file.
        ["ls-files", "--others", "-z"],
    )
    evidence_rel = {_repo_relative(repo, path) for path in evidence}
    ignored_rel = {_repo_relative(repo, path) for path in ignored_paths if path.is_relative_to(repo)}
    found: set[str] = set()
    for command in commands:
        result = run_git(command, repo)
        if result.returncode != 0:
            raise ContractError(
                f"git {' '.join(command)} failed: {(result.stderr or result.stdout).strip()}"
            )
        for path in _split_nul(result.stdout):
            normalized = Path(path).as_posix()
            if not _is_control_path(normalized, evidence_rel, ignored_rel):
                found.add(normalized)
    found.update(
        _tracked_worktree_byte_changes(repo, tracked_paths, evidence_rel, ignored_rel)
    )
    return tuple(sorted(found))


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _no_native_config() -> NativeConfigBinding:
    return NativeConfigBinding(path="<none>", sha256=f"sha256:{_hash_bytes(b'')}")


def lexical_repo_path(repo: Path, requested: Path, role: str) -> Path:
    """Return a normalized repository path without resolving any filesystem component."""
    repo = repo.resolve()
    try:
        candidate = requested if requested.is_absolute() else repo / requested
        lexical = Path(os.path.abspath(os.fspath(candidate)))
        relative = lexical.relative_to(repo)
    except (OSError, ValueError) as exc:
        raise ContractError(f"{role} path escapes the repository: {requested}") from exc
    if not relative.parts:
        raise ContractError(f"{role} path must name a repository file")
    return repo / relative


def _open_controlled_output_parent(
    repo: Path,
    requested: Path,
    role: str,
    *,
    create_parents: bool,
) -> tuple[Path, list[int]]:
    """Open a repo-anchored parent chain without following any filesystem link."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ContractError(
            f"this platform cannot safely persist {role} without following symlinks"
        )
    repo = repo.resolve()
    lexical = lexical_repo_path(repo, requested, role)
    relative = lexical.relative_to(repo)
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(repo, directory_flags))
        for part in relative.parts[:-1]:
            try:
                descriptor = os.open(part, directory_flags, dir_fd=descriptors[-1])
            except FileNotFoundError:
                if not create_parents:
                    return relative, descriptors
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    # Another writer created something after the failed open. The no-follow open
                    # below decides whether it is the expected directory or an unsafe replacement.
                    pass
                descriptor = os.open(part, directory_flags, dir_fd=descriptors[-1])
            descriptors.append(descriptor)
    except OSError as exc:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise ContractError(
            f"{role} parent must be a repository-local non-symlink directory: "
            f"{relative.as_posix()}: {exc}"
        ) from exc
    return relative, descriptors


def _controlled_output_leaf_state(
    directory_fd: int, leaf: str, role: str
) -> os.stat_result | None:
    """Return one safe destination snapshot, rejecting links and special files."""
    try:
        value = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ContractError(f"cannot inspect {role} destination {leaf}: {exc}") from exc
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise ContractError(f"{role} destination must be a single-link regular file: {leaf}")
    return value


def _controlled_output_identity(value: os.stat_result | None) -> tuple[int, ...] | None:
    if value is None:
        return None
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _write_controlled_output(
    repo: Path, requested: Path, raw_bytes: bytes, role: str
) -> None:
    """Atomically publish one repo-controlled file without opening the destination inode.

    Existing single-link regular outputs are supported for normal reruns. Symlinks, hardlinks, and
    special leaves are rejected before the temporary file is created. A same-directory temporary
    file is fully written, fsynced, and validated before an atomic descriptor-relative replacement;
    the parent directory is fsynced afterwards.
    """
    relative, descriptors = _open_controlled_output_parent(
        repo, requested, role, create_parents=True
    )
    parent_fd = descriptors[-1]
    leaf = relative.name
    temporary = f".{leaf}.tmp-{secrets.token_hex(12)}"
    temporary_created = False
    temporary_fd = -1
    try:
        initial = _controlled_output_leaf_state(parent_fd, leaf, role)
        file_flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        temporary_fd = os.open(temporary, file_flags, 0o600, dir_fd=parent_fd)
        temporary_created = True
        os.fchmod(temporary_fd, 0o600)
        view = memoryview(raw_bytes)
        while view:
            written = os.write(temporary_fd, view)
            if written <= 0:
                raise ContractError(f"cannot persist {role}: a write made no progress")
            view = view[written:]
        os.fsync(temporary_fd)
        temporary_state = os.fstat(temporary_fd)
        if (
            not stat.S_ISREG(temporary_state.st_mode)
            or temporary_state.st_nlink != 1
            or temporary_state.st_size != len(raw_bytes)
            or stat.S_IMODE(temporary_state.st_mode) != 0o600
        ):
            raise ContractError(f"temporary {role} is not a mode-safe single-link regular file")
        os.close(temporary_fd)
        temporary_fd = -1

        current = _controlled_output_leaf_state(parent_fd, leaf, role)
        if _controlled_output_identity(current) != _controlled_output_identity(initial):
            raise ContractError(f"{role} destination changed concurrently before publication")
        os.replace(
            temporary,
            leaf,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        temporary_created = False
        os.fsync(parent_fd)
        published = _controlled_output_leaf_state(parent_fd, leaf, role)
        if (
            published is None
            or published.st_dev != temporary_state.st_dev
            or published.st_ino != temporary_state.st_ino
            or published.st_size != len(raw_bytes)
            or stat.S_IMODE(published.st_mode) != 0o600
        ):
            raise ContractError(f"{role} changed during atomic publication")
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(f"cannot persist {role} {relative.as_posix()}: {exc}") from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _remove_controlled_output(repo: Path, requested: Path, role: str) -> None:
    """Remove one existing controlled output without following its parent or leaf."""
    relative, descriptors = _open_controlled_output_parent(
        repo, requested, role, create_parents=False
    )
    # A missing parent is represented by a descriptor chain that did not reach the leaf parent.
    if len(descriptors) != len(relative.parts):
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        return
    parent_fd = descriptors[-1]
    try:
        initial = _controlled_output_leaf_state(parent_fd, relative.name, role)
        if initial is None:
            return
        current = _controlled_output_leaf_state(parent_fd, relative.name, role)
        if _controlled_output_identity(current) != _controlled_output_identity(initial):
            raise ContractError(f"{role} destination changed concurrently before removal")
        os.unlink(relative.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(f"cannot remove {role} {relative.as_posix()}: {exc}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def read_repo_regular_file(
    repo: Path,
    requested: Path,
    role: str,
    *,
    require_single_link: bool = False,
) -> RepoRegularFileBinding:
    """Bind stable bytes from a repo-local regular file without following any symlink."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ContractError(
            f"this platform cannot safely read {role} without following symlinks"
        )
    repo = repo.resolve()
    lexical = lexical_repo_path(repo, requested, role)
    relative = lexical.relative_to(repo)
    relative_text = relative.as_posix()
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
    )
    directory_fds: list[int] = []
    file_fd = -1
    try:
        directory_fds.append(os.open(repo, directory_flags))
        for part in relative.parts[:-1]:
            directory_fds.append(
                os.open(part, directory_flags, dir_fd=directory_fds[-1])
            )
        file_fd = os.open(relative.name, file_flags, dir_fd=directory_fds[-1])
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or (
            require_single_link and before.st_nlink != 1
        ):
            qualifier = "single-link " if require_single_link else ""
            raise ContractError(
                f"{role} must be a {qualifier}regular file: {relative_text}"
            )
        handle = os.fdopen(file_fd, "rb", closefd=True)
        file_fd = -1
        with handle:
            raw_bytes = handle.read()
            after = os.fstat(handle.fileno())
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (
            any(getattr(before, field) != getattr(after, field) for field in stable_fields)
            or len(raw_bytes) != after.st_size
        ):
            raise ContractError(f"{role} changed while it was being read: {relative_text}")
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(
            f"{role} must resolve through repository-local non-symlink directories to a "
            f"regular file: {relative_text}: {exc}"
        ) from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
    return RepoRegularFileBinding(
        relative_path=relative_text,
        raw_bytes=raw_bytes,
        mtime_ns=after.st_mtime_ns,
    )


def _validate_reviewer_output_relative(relative: Path) -> str:
    if (
        relative.parent != REVIEWER_OUTPUT_DIR
        or relative.suffix != ".txt"
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", relative.stem)
    ):
        raise ContractError(
            "reviewer output must be a direct .txt file under .pm/reviewer-replies/"
        )
    return relative.as_posix()


def authorize_reviewer_output_path(repo: Path, raw: str) -> tuple[Path, str]:
    """Authorize one lexical control-path target without following symlinks."""
    if not raw:
        raise ContractError("reviewer output path is required")
    repo = repo.resolve()
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = repo / candidate
        lexical = Path(os.path.abspath(os.fspath(candidate)))
        relative = lexical.relative_to(repo)
    except (OSError, ValueError) as exc:
        raise ContractError(
            "reviewer output must be a direct .txt file under .pm/reviewer-replies/"
        ) from exc
    relative_text = _validate_reviewer_output_relative(relative)

    current = repo
    parts = relative.parts
    for index, part in enumerate(parts):
        current /= part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ContractError(
                f"cannot inspect reviewer output path {relative_text}: {exc}"
            ) from exc
        if stat.S_ISLNK(current_stat.st_mode):
            raise ContractError(
                f"reviewer output path must not contain symlinks: {relative_text}"
            )
        if index < len(parts) - 1 and not stat.S_ISDIR(current_stat.st_mode):
            raise ContractError(
                f"reviewer output parent must be a directory: {relative_text}"
            )
        if index == len(parts) - 1:
            if not stat.S_ISREG(current_stat.st_mode):
                raise ContractError(
                    f"reviewer output must be a regular file: {relative_text}"
                )
            if current_stat.st_nlink != 1:
                raise ContractError(
                    f"reviewer output must be a single-link regular file: {relative_text}"
                )
    return lexical, relative_text


def read_reviewer_output(repo: Path, raw: str) -> ReviewerOutputBinding:
    """Read an authorized reply through anchored, no-follow directory descriptors."""
    lexical, relative_text = authorize_reviewer_output_path(repo, raw)
    binding = read_repo_regular_file(
        repo,
        lexical,
        "reviewer output",
        require_single_link=True,
    )
    return ReviewerOutputBinding(
        relative_path=relative_text,
        text=binding.raw_bytes.decode("utf-8", errors="replace"),
        sha256=f"sha256:{_hash_bytes(binding.raw_bytes)}",
    )


def _bind_native_executable_bytes(path: Path, label: str) -> tuple[Path, str]:
    try:
        resolved = path.resolve(strict=True)
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(resolved, flags)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or not os.access(resolved, os.X_OK):
                raise ContractError(f"{label} must resolve to an executable regular file")
            if before.st_mode & 0o022:
                raise ContractError(f"{label} must not be group- or world-writable")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(f"cannot bind {label}: {path}: {exc}") from exc
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise ContractError(f"{label} changed while it was being bound")
    return resolved, f"sha256:{digest.hexdigest()}"


_TRUSTED_TEST_SCRIPT_INTERPRETERS: dict[str, tuple[Path, ...]] = {
    "bash": (Path("/usr/bin/bash"), Path("/bin/bash")),
    "sh": (Path("/usr/bin/sh"), Path("/bin/sh")),
    "dash": (Path("/usr/bin/dash"), Path("/bin/dash")),
}


def _bound_native_bytes(path: Path, expected_sha256: str, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ContractError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(f"cannot read {label}: {path}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
        raise ContractError(f"{label} changed while it was being read")
    actual = f"sha256:{_hash_bytes(payload)}"
    if actual != expected_sha256:
        raise ContractError(f"{label} bytes changed while its closure was being bound")
    return payload


def _test_script_interpreter(path: Path, raw_bytes: bytes, label: str) -> Path | None:
    """Resolve the deliberately narrow interpreter set for a private test fixture."""
    if not raw_bytes.startswith(b"#!"):
        return None
    line = raw_bytes[2:256].split(b"\n", 1)[0]
    try:
        tokens = line.decode("utf-8").strip().split()
    except UnicodeDecodeError as exc:
        raise ContractError(f"{label} shebang is not valid UTF-8") from exc
    if not tokens:
        raise ContractError(f"{label} shebang names no interpreter")
    if Path(tokens[0]).name == "env":
        if len(tokens) != 2:
            raise ContractError(f"{label} env shebang has unsupported arguments")
        name = tokens[1]
    else:
        if len(tokens) != 1:
            raise ContractError(f"{label} shebang has unsupported interpreter arguments")
        name = Path(tokens[0]).name
    candidates = _TRUSTED_TEST_SCRIPT_INTERPRETERS.get(name)
    if not candidates:
        raise ContractError(f"{label} shebang names untrusted interpreter {name!r}")
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
        except OSError:
            continue
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and not info.st_mode & 0o022
            and info.st_mode & stat.S_IXUSR
        ):
            return resolved
    raise ContractError(f"{label} has no trusted installed interpreter for {name!r}")


def _bind_native_executable(path: Path, label: str) -> NativeExecutableBinding:
    """Bind one private test executable without borrowing catalog-private helpers."""
    resolved, digest = _bind_native_executable_bytes(path, label)
    raw_bytes = _bound_native_bytes(resolved, digest, label)
    interpreter = _test_script_interpreter(resolved, raw_bytes, label)
    members: list[NativeExecutableMember] = []
    if interpreter is not None:
        _, interpreter_digest = _bind_native_executable_bytes(
            interpreter, f"{label} interpreter"
        )
        members.append(
            NativeExecutableMember(
                role="interpreter", path=str(interpreter), sha256=interpreter_digest
            )
        )
    members.append(
        NativeExecutableMember(role="entrypoint", path=str(resolved), sha256=digest)
    )
    joined = "\n".join(
        f"{member.role}\t{member.path}\t{member.sha256}" for member in members
    )
    return NativeExecutableBinding(
        path=resolved,
        sha256=digest,
        invocation_prefix=(str(interpreter), str(resolved))
        if interpreter is not None
        else (str(resolved),),
        closure=tuple(members),
        bound_closure_sha256=f"sha256:{_hash_bytes(joined.encode('utf-8'))}",
    )


def _binding_from_catalog_executable(
    executable: live_catalog.ResolvedExecutable, label: str
) -> NativeExecutableBinding:
    """Consume the public catalog closure object without reconstructing its opaque manifest."""
    matches = [
        backend
        for backend, argv in live_catalog.ALLOWED_COMMANDS.items()
        if tuple(executable.argv) == tuple(argv)
    ]
    if len(matches) != 1:
        raise ContractError(f"{label} does not carry one canonical catalog argv")
    backend = matches[0]
    record = executable.record()
    _validated_catalog_command(
        {
            "backend": backend,
            "catalogBytesSha256": "sha256:" + "0" * 64,
            "command": record,
            "coverage": {},
            "exitCode": 0,
            "stderrSha256": "sha256:" + "0" * 64,
        },
        backend,
        allow_synthetic=False,
    )
    prefix = _catalog_invocation_prefix(record, backend, allow_synthetic=False)
    return NativeExecutableBinding(
        path=Path(executable.executable),
        sha256=executable.executable_sha256,
        invocation_prefix=prefix,
        closure=tuple(
            NativeExecutableMember(entry.role, entry.path, entry.sha256)
            for entry in executable.closure
        ),
        bound_closure_sha256=executable.closure_sha256,
    )


def _account_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
    except (KeyError, OSError) as exc:
        raise ContractError("cannot resolve the current OS account home directory") from exc


def _read_absolute_regular_file_no_follow(path: Path, role: str) -> NativeConfigBinding:
    """Hash an absolute regular file while refusing symlinked parents and unsafe modes."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ContractError(
            f"this platform cannot safely read {role} without following symlinks"
        )
    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
    except OSError as exc:
        raise ContractError(f"{role} path cannot be normalized: {path}: {exc}") from exc
    if not lexical.is_absolute() or ".." in lexical.parts:
        raise ContractError(f"{role} path must be absolute without '..': {path}")

    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
    )
    directory_fds: list[int] = []
    file_fd = -1
    try:
        directory_fds.append(os.open(os.sep, directory_flags))
        for part in lexical.parts[1:-1]:
            directory_fds.append(
                os.open(part, directory_flags, dir_fd=directory_fds[-1])
            )
        file_fd = os.open(lexical.name, file_flags, dir_fd=directory_fds[-1])
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ContractError(f"{role} must be a single-link regular file: {lexical}")
        if before.st_mode & 0o022:
            raise ContractError(f"{role} must not be group- or world-writable: {lexical}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(file_fd)
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(
            f"{role} must resolve through non-symlink directories to a regular file: "
            f"{lexical}: {exc}"
        ) from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)

    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise ContractError(f"{role} changed while it was being read")
    return NativeConfigBinding(path=str(lexical), sha256=f"sha256:{digest.hexdigest()}")


def _native_reviewer_configuration(backend: str) -> NativeConfigBinding:
    if backend == "codex":
        # Only the provider/profile config is bound. Credential and auth-secret files are neither
        # read nor recorded in the artifact.
        return _read_absolute_regular_file_no_follow(
            _account_home() / ".codex" / "config.toml",
            "codex OS-account configuration",
        )
    if backend in ("claude", "cursor"):
        # Neither CLI needs a caller-visible provider/profile file to select its authenticated
        # route: the account credential store is reached through the pinned HOME and, for Claude,
        # the pinned CLAUDE_CONFIG_DIR. Nothing outside that account root is read or recorded.
        return _no_native_config()
    raise ContractError(f"cannot bind configuration for reviewer backend {backend!r}")


def _installed_native_reviewer(
    backend: str, expected_command: Mapping[str, Any]
) -> NativeExecutableBinding:
    """Resolve the reviewer through the public catalog producer's complete closure."""
    if backend not in SUPPORTED_REVIEW_BACKENDS:
        raise ContractError(
            f"reviewer backend {backend!r} has no allowlisted native read-only launcher"
        )
    try:
        evidence = live_catalog.produce(backend)
    except live_catalog.CatalogError as exc:
        raise ContractError(
            f"cannot bind {backend} reviewer through the authenticated native catalog: {exc}"
        ) from exc
    command = evidence.executable.record()
    if command != dict(expected_command):
        raise ContractError(
            f"{backend} reviewer executable closure disagrees with fresh launch authorization"
        )
    return _binding_from_catalog_executable(
        evidence.executable, f"{backend} reviewer executable"
    )


def _native_reviewer_command(
    repo: Path, reviewer: RoleIdentity, executable: Path, catalog_model_id: str
) -> list[str]:
    """Build the fixed read-only argv for one reviewer backend.

    ``catalog_model_id`` is the concrete id the fresh catalog verified for this role's base model and
    effort. Cursor expresses reasoning effort inside the concrete id rather than as a flag, so passing
    the base model would silently launch a different reasoning tier than the one that was authorized.
    """
    catalog_model_id = _string(catalog_model_id, "reviewer catalog model id")
    if reviewer.backend == "claude":
        # The audited read-only Claude vector: safe mode, no permission prompts, and only the three
        # read tools. Effort is a real flag on this backend rather than part of the concrete id, so
        # both the policy effort and the fresh catalog id are passed explicitly. ``-p`` carries no
        # positional prompt: with stdin attached, Claude reads the brief from stdin only.
        command = [
            str(executable),
            "--effort",
            reviewer.effort,
            "--model",
            catalog_model_id,
            "-p",
            "--output-format",
            "text",
            "--safe-mode",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Read,Glob,Grep",
        ]
    elif reviewer.backend == "codex":
        command = [
            str(executable),
            "-c",
            f'model_reasoning_effort="{reviewer.effort}"',
            "exec",
            "--model",
            catalog_model_id,
            "--sandbox",
            "read-only",
            "--disable",
            "multi_agent",
            "--ephemeral",
            "--ignore-rules",
            "--color",
            "never",
            "--cd",
            str(repo),
            "-",
        ]
    elif reviewer.backend == "cursor":
        # Read-only plan mode, no project configs, no indexing or codebase retrieval, no auto-update.
        # The prompt is never an argument: it arrives only on stdin.
        command = [
            str(executable),
            "--print",
            "--output-format",
            "text",
            "--single-turn",
            "--mode",
            "plan",
            "--model",
            catalog_model_id,
            "--workspace",
            str(repo),
            "--trust",
            "--disable-project-configs",
            "--disable-auto-update",
            "--disable-indexing",
            "--disable-codebase-ref",
        ]
    else:
        raise ContractError(
            f"reviewer backend {reviewer.backend!r} has no allowlisted native read-only launcher"
        )
    forbidden = sorted(FORBIDDEN_NATIVE_REVIEWER_OPTIONS.intersection(command))
    if forbidden:
        raise ContractError(
            "native reviewer argv must not carry write, endpoint, config, plugin, resume, or "
            "additional-directory options: " + ", ".join(forbidden)
        )
    return command


# Audited endpoint, credential, proxy/TLS, shell-startup, runtime-loader, and native-CLI selectors.
# Production now inherits no caller variables at all; this taxonomy remains pinned to the catalog
# producer so a future environment-policy refactor cannot silently become weaker than catalog
# authentication. Fixed account roots and the Cursor invocation marker are supplied separately.
_NATIVE_CHILD_ENV_DENYLIST = frozenset(
    {
        "AGENT_CLI_CREDENTIAL_STORE",
        "AGENT_CLI_CONFIG_DIR",
        "ALL_PROXY",
        "BASHOPTS",
        "BASH_ENV",
        "CDPATH",
        "CODEX_API_BASE",
        "CODEX_HOME",
        "CURL_CA_BUNDLE",
        "CURSOR_API_ENDPOINT",
        "CURSOR_AGENT_CONFIG_DIR",
        "CURSOR_AUTH_TOKEN",
        "CURSOR_CONFIG_DIR",
        "CURSOR_INVOKED_AS",
        "DENO_DIR",
        "ENV",
        "GEM_PATH",
        "GIT_ASKPASS",
        "GIT_SSL_NO_VERIFY",
        "GLOBIGNORE",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "IFS",
        "KSH_ENV",
        "LD_AUDIT",
        "LD_DEBUG",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "NO_PROXY",
        "OPENAI_API_BASE",
        "OPENAI_BASE_URL",
        "OPENSSL_CONF",
        "OPENSSL_ENGINES",
        "PERL5LIB",
        "PERL5OPT",
        "PS4",
        "REQUESTS_CA_BUNDLE",
        "RUBYOPT",
        "SHELLOPTS",
        "SSH_ASKPASS",
        "SSLKEYLOGFILE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "ZDOTDIR",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
# Prefix families retained for the same compatibility assertion. OPENAI_, CODEX_, CURSOR_, and
# AGENT_CLI_ are intentionally families: a bound provider can name a credential variable that this
# release has never seen, so enumerating only today's API-key names is not a sound trust boundary.
_NATIVE_CHILD_ENV_PREFIX_DENYLIST = (
    "AGENT_CLI_",
    "ANTHROPIC_",
    "AWS_",
    "AZURE_",
    "BASH_FUNC_",
    "CLAUDE_",
    "CODEX_",
    "CURSOR_",
    "DYLD_",
    "GENERAL_AGENT_",
    "GOOGLE_",
    "NODE_",
    "OPENAI_",
    "PYTHON",
    "npm_",
)


def _native_reviewer_environment(backend: str) -> dict[str, str]:
    if backend not in SUPPORTED_REVIEW_BACKENDS:
        raise ContractError(f"cannot build an environment for reviewer backend {backend!r}")
    # Start empty. A denylist cannot anticipate a bound Codex provider's arbitrary credentialEnvKey,
    # or the next OPENAI_/CODEX_/CURSOR_ account selector. The reviewer needs no caller environment:
    # fixed locale/terminal values and OS-account roots are sufficient for the audited native argv.
    account_home = _account_home()
    environment = {
        "HOME": str(account_home),
        "PATH": os.pathsep.join(_SYSTEM_HELPER_PATH),
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
    if backend == "claude":
        environment["CLAUDE_CONFIG_DIR"] = str(account_home / ".claude")
    if backend == "codex":
        environment["CODEX_HOME"] = str(account_home / ".codex")
    if backend == "cursor":
        # The public catalog binding launches the bundled node/index.js directly. The installed
        # wrapper would normally set this fixed identity marker before execing that same prefix.
        environment["CURSOR_INVOKED_AS"] = "cursor-agent"
    return environment


def _run_native_reviewer(
    command: Sequence[str],
    prompt: str,
    repo: Path,
    *,
    backend: str,
    test_mode: bool = False,
) -> bytes:
    environment = (
        dict(os.environ) if test_mode else _native_reviewer_environment(backend)
    )
    try:
        process = subprocess.Popen(
            list(command),
            cwd=repo,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(
                prompt.encode("utf-8"), timeout=NATIVE_REVIEW_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise ContractError(
                f"native reviewer exceeded {NATIVE_REVIEW_TIMEOUT_SECONDS} seconds"
            ) from exc
    except ContractError:
        raise
    except OSError as exc:
        raise ContractError(f"cannot launch native reviewer: {exc}") from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-2000:]
        raise ContractError(
            f"native reviewer exited {process.returncode}"
            + (f": {detail}" if detail else "")
        )
    if len(stdout) > MAX_REVIEW_OUTPUT_BYTES:
        raise ContractError(
            f"native reviewer output exceeds {MAX_REVIEW_OUTPUT_BYTES} bytes"
        )
    return stdout


def _native_reviewer_retryable(error: ContractError) -> bool:
    """Classify only transient native-review failures as retryable."""
    detail = str(error).casefold()
    if any(
        marker in detail
        for marker in (
            "authentication",
            "unauthorized",
            "permission",
            "not logged in",
            "invalid api key",
            "quota",
            "credit",
            "billing",
            "payment",
            "invalid request",
            "unsupported model",
            "unknown model",
            "malformed",
            "structured output",
            "policy",
            "evidence",
        )
    ):
        return False
    return any(
        marker in detail
        for marker in (
            "rate limit",
            "too many requests",
            "service unavailable",
            "internal server error",
            "http 5",
            "network",
            "connection",
            "timed out",
            "timeout",
            "stream disconnected",
            "temporary",
            "native reviewer exited",
            "native reviewer exceeded",
        )
    )


def _write_owned_reviewer_output(
    repo: Path, item_id: str, item_kind: str, raw_bytes: bytes
) -> ReviewerOutputBinding:
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptors: list[int] = []
    leaf = f"{item_id}-{item_kind}-{secrets.token_hex(12)}.txt"
    created = False
    try:
        descriptors.append(os.open(repo.resolve(), directory_flags))
        descriptors.append(os.open(".pm", directory_flags, dir_fd=descriptors[-1]))
        try:
            os.mkdir("reviewer-replies", mode=0o700, dir_fd=descriptors[-1])
        except FileExistsError:
            pass
        descriptors.append(
            os.open("reviewer-replies", directory_flags, dir_fd=descriptors[-1])
        )
        output_fd = os.open(leaf, file_flags, 0o600, dir_fd=descriptors[-1])
        created = True
        try:
            view = memoryview(raw_bytes)
            while view:
                written = os.write(output_fd, view)
                view = view[written:]
            os.fsync(output_fd)
            output_stat = os.fstat(output_fd)
            if not stat.S_ISREG(output_stat.st_mode) or output_stat.st_nlink != 1:
                raise ContractError("owned reviewer output is not a single-link regular file")
        finally:
            os.close(output_fd)
    except ContractError:
        if created and descriptors:
            os.unlink(leaf, dir_fd=descriptors[-1])
        raise
    except OSError as exc:
        if created and descriptors:
            try:
                os.unlink(leaf, dir_fd=descriptors[-1])
            except OSError:
                pass
        raise ContractError(f"cannot persist owned reviewer output: {exc}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    relative = (REVIEWER_OUTPUT_DIR / leaf).as_posix()
    binding = read_reviewer_output(repo, relative)
    expected = f"sha256:{_hash_bytes(raw_bytes)}"
    if binding.sha256 != expected:
        raise ContractError("owned reviewer output changed before it could be bound")
    return binding


def _reviewer_configuration(
    backend: str, test_binding: NativeExecutableBinding | None
) -> NativeConfigBinding:
    """Bind the configuration this launch is pinned to.

    ``test_binding`` is set only under the private test-executable seam. A Codex launch must bind an
    absolute, digested configuration in every mode, so the fixture reuses the already bound absolute
    test executable as its configuration artifact: same file, same digest, stated openly. That keeps
    the Codex shape identical to production — an absolute path plus a real digest of real bytes that
    replay rebinds — instead of admitting a "<none>" sentinel that a forged artifact could copy.
    Production (``test_binding is None``) always resolves the real OS-account configuration.
    """
    if test_binding is None:
        return _native_reviewer_configuration(backend)
    if backend == "codex":
        return NativeConfigBinding(path=str(test_binding.path), sha256=test_binding.sha256)
    return _no_native_config()


def _bind_reviewer_launch(
    repo: Path, bundle: ReviewBundle, test_executable: Path | None
) -> tuple[NativeExecutableBinding, list[str], NativeConfigBinding]:
    """Bind the reviewer executable closure and build its fixed argv.

    ``test_executable`` is Python-call-only (never a CLI flag, environment variable, or alternate
    policy path — see ``main``'s ``_test_reviewer_executable`` parameter). Production
    (``test_executable is None``) always resolves a real backend-specific config through
    ``_native_reviewer_configuration``, which either returns a bound absolute path with a real digest
    or raises ``ContractError``; it can never produce the "<none>" sentinel for Codex.
    """
    if test_executable is not None:
        binding = _bind_native_executable(test_executable, "test-only reviewer executable")
        return (
            binding,
            [str(binding.path)],
            _reviewer_configuration(bundle.reviewer_identity.backend, binding),
        )
    binding = _installed_native_reviewer(
        bundle.reviewer_identity.backend, bundle.reviewer_catalog_command
    )
    config = _reviewer_configuration(bundle.reviewer_identity.backend, None)
    command = _native_reviewer_command(
        repo,
        bundle.reviewer_identity,
        binding.path,
        bundle.reviewer_catalog_model_id,
    )
    return binding, command, config


def _argv_digest(argv: Sequence[str]) -> str:
    return f"sha256:{_hash_bytes(chr(0).join(argv).encode('utf-8'))}"


def invoke_native_reviewer(
    *,
    repo: Path,
    bundle: ReviewBundle,
    prompt: str,
    test_executable: Path | None = None,
) -> NativeReviewerInvocation:
    """Launch the bound reviewer and capture its stdout; test injection is Python-call-only."""
    reviewer = bundle.reviewer_identity
    test_mode = test_executable is not None
    binding, command, config = _bind_reviewer_launch(repo, bundle, test_executable)
    launch_argv = binding.launch_argv(command)
    stdout: bytes
    for retry_index in range(len(NATIVE_REVIEW_RETRY_DELAYS_SECONDS) + 1):
        try:
            stdout = _run_native_reviewer(
                launch_argv, prompt, repo, backend=reviewer.backend, test_mode=test_mode
            )
            break
        except ContractError as error:
            # Python-call-only test executables must fail quickly and deterministically. Production
            # launches get the same four bounded waits as the built-in native runner.
            if (
                test_mode
                or retry_index >= len(NATIVE_REVIEW_RETRY_DELAYS_SECONDS)
                or not _native_reviewer_retryable(error)
            ):
                raise
            time.sleep(NATIVE_REVIEW_RETRY_DELAYS_SECONDS[retry_index])
    else:  # pragma: no cover - the loop either returns or raises above
        raise ContractError("native reviewer retry loop exhausted")
    current = (
        _bind_native_executable(
            binding.path, "reviewer executable after native invocation"
        )
        if test_mode
        else _installed_native_reviewer(
            reviewer.backend, bundle.reviewer_catalog_command
        )
    )
    current_config = _reviewer_configuration(
        reviewer.backend, current if test_mode else None
    )
    if (
        current.path != binding.path
        or current.sha256 != binding.sha256
        or current.closure_sha256 != binding.closure_sha256
    ):
        raise ContractError("native reviewer executable closure changed during invocation")
    if current_config != config:
        raise ContractError("native reviewer configuration changed during invocation")
    output = _write_owned_reviewer_output(
        repo, bundle.item_id, bundle.item_kind, stdout
    )
    return NativeReviewerInvocation(
        output=output,
        backend=reviewer.backend,
        executable=str(binding.path),
        executable_sha256=binding.sha256,
        executable_closure_sha256=binding.closure_sha256,
        config_path=config.path,
        config_sha256=config.sha256,
        model=bundle.reviewer_catalog_model_id,
        argv_sha256=_argv_digest(launch_argv),
    )


def _require_launch_matches_artifact(
    artifact: ArtifactInputs,
    *,
    executable: str,
    executable_sha256: str,
    executable_closure_sha256: str,
    config_path: str,
    config_sha256: str,
    argv_sha256: str,
    model: str,
    backend: str,
) -> None:
    for label, current, stored in (
        ("backend", backend, artifact.reviewer_backend),
        ("path", executable, artifact.reviewer_executable),
        ("digest", executable_sha256, artifact.reviewer_executable_sha256),
        (
            "closure digest",
            executable_closure_sha256,
            artifact.reviewer_executable_closure_sha256,
        ),
        ("config path", config_path, artifact.reviewer_config_path),
        ("config digest", config_sha256, artifact.reviewer_config_sha256),
        ("argv digest", argv_sha256, artifact.reviewer_argv_sha256),
        ("model", model, artifact.reviewer_model),
    ):
        if current != stored:
            raise ContractError(f"native reviewer executable {label} drift")


def require_native_reviewer_current(
    repo: Path,
    bundle: ReviewBundle,
    artifact: ArtifactInputs,
    *,
    test_executable: Path | None = None,
) -> None:
    """Rebind the reviewer closure and rebuild its argv from freshly resolved inputs."""
    binding, command, config = _bind_reviewer_launch(repo, bundle, test_executable)
    _require_launch_matches_artifact(
        artifact,
        executable=str(binding.path),
        executable_sha256=binding.sha256,
        executable_closure_sha256=binding.closure_sha256,
        config_path=config.path,
        config_sha256=config.sha256,
        argv_sha256=_argv_digest(binding.launch_argv(command)),
        model=bundle.reviewer_catalog_model_id,
        backend=bundle.reviewer_identity.backend,
    )


def require_reviewer_output_current(
    repo: Path, artifact: ArtifactInputs
) -> ReviewerOutputBinding:
    current = read_reviewer_output(repo, artifact.reviewer_output_path)
    if current.sha256 != artifact.reviewer_output_sha256:
        raise ContractError(
            "reviewer output digest drift: artifact "
            f"{artifact.reviewer_output_sha256}, current {current.sha256}"
        )
    return current


def _file_record(repo: Path, relative: str) -> dict[str, object]:
    path = repo / relative
    try:
        stat = path.lstat()
    except OSError:
        return {"path": relative, "kind": "absent", "sha256": _hash_bytes(b"<absent>")}
    if path.is_symlink():
        payload = os.readlink(path).encode("utf-8", errors="surrogateescape")
        kind = "symlink"
    elif path.is_file():
        payload = path.read_bytes()
        kind = "file"
    elif path.is_dir():
        payload = b"<directory>"
        kind = "directory"
    else:
        payload = b"<special>"
        kind = "special"
    return {
        "path": relative,
        "kind": kind,
        "size": len(payload),
        "mode": stat.st_mode,
        "sha256": _hash_bytes(payload),
        "mtime_ns": stat.st_mtime_ns,
    }


def _document_record(
    repo: Path, path: Path, role: str, logical_relative: str = ""
) -> tuple[dict[str, object], str]:
    binding = read_repo_regular_file(repo, path, f"required {role} file")
    payload = binding.raw_bytes
    relative = logical_relative or binding.relative_path
    logical = Path(relative)
    if logical.is_absolute() or ".." in logical.parts:
        raise ContractError(f"logical {role} path must be repository-relative without '..': {relative}")
    return (
        {
            "path": relative,
            "role": role,
            "size": len(payload),
            "sha256": _hash_bytes(payload),
            "mtime_ns": binding.mtime_ns,
        },
        payload.decode("utf-8", errors="replace"),
    )


def _diff_for_paths(
    repo: Path,
    args: Sequence[str],
    paths: Sequence[str],
    *,
    extra_env: Mapping[str, str] | None = None,
) -> str:
    if not paths:
        return ""
    result = run_git([*args, "--", *paths], repo, extra_env=extra_env)
    if result.returncode != 0:
        raise ContractError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def _absolute_git_path(repo: Path, name: str) -> Path:
    raw = _git_value(["rev-parse", "--git-path", name], repo)
    if not raw:
        raise ContractError(f"cannot resolve git {name} path")
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return Path(os.path.abspath(os.fspath(path)))


def _rm_snapshot_root(raw: str) -> None:
    try:
        info = os.lstat(raw)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        os.unlink(raw)
        return
    shutil.rmtree(raw, ignore_errors=True)


def _create_worktree_snapshot_dirs() -> tuple[str, Path, Path]:
    """Create an exclusive private index and object store that cannot alias the live git dir."""
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ContractError(
            "this platform cannot safely snapshot the worktree without following symlinks"
        )
    raw = tempfile.mkdtemp(prefix="codex-verify-worktree-")
    try:
        flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )
        dir_fd = os.open(raw, flags)
        try:
            info = os.fstat(dir_fd)
            if not stat.S_ISDIR(info.st_mode):
                raise ContractError("worktree snapshot root must be a non-symlink directory")
            os.fchmod(dir_fd, 0o700)
            os.mkdir("objects", 0o700, dir_fd=dir_fd)
            index_fd = os.open(
                "index",
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=dir_fd,
            )
            os.close(index_fd)
        finally:
            os.close(dir_fd)
        root = Path(raw)
        return raw, root / "index", root / "objects"
    except ContractError:
        _rm_snapshot_root(raw)
        raise
    except OSError as exc:
        _rm_snapshot_root(raw)
        raise ContractError(f"cannot create a private worktree snapshot directory: {exc}") from exc


def _snapshot_extra_env(index_file: Path, objects_dir: Path, real_objects: Path) -> dict[str, str]:
    index_s = os.path.abspath(os.fspath(index_file))
    objects_s = os.path.abspath(os.fspath(objects_dir))
    real_s = os.path.abspath(os.fspath(real_objects))
    if objects_s == real_s:
        raise ContractError("refusing to write snapshot objects into the live object store")
    return {
        "GIT_INDEX_FILE": index_s,
        "GIT_OBJECT_DIRECTORY": objects_s,
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": real_s,
    }


def _hash_object_write(repo: Path, payload: bytes, extra_env: Mapping[str, str]) -> str:
    result = run_git(
        ["hash-object", "-t", "blob", "--stdin", "--no-filters", "-w"],
        repo,
        extra_env=extra_env,
        input_bytes=payload,
    )
    oid = result.stdout.strip()
    if result.returncode != 0 or not oid:
        raise ContractError(
            f"git hash-object failed: {(result.stderr or result.stdout).strip()}"
        )
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
        raise ContractError("git hash-object returned an unreadable object id")
    return oid


def _worktree_snapshot_entry(
    repo: Path, relative: str, head_entries: Mapping[str, tuple[str, str]]
) -> tuple[str, str, bytes]:
    """Return ``(kind, git_mode, payload)`` for one reviewed worktree path.

    ``kind`` is ``file``, ``symlink``, ``absent``, or ``gitlink``. Gitlinks keep the
    HEAD index entry; every other non-file/symlink/absent type fails closed.
    """
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ContractError(f"reviewed path escapes the worktree snapshot: {relative}")
    if any(char in relative for char in "\0\t\n"):
        raise ContractError(f"reviewed path is unsafe for a git snapshot: {relative!r}")
    path = repo / relative
    work = _worktree_tracked_mode_and_bytes(path)
    if work is not None:
        mode, payload = work
        return ("symlink" if mode == "120000" else "file", mode, payload)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "absent", "", b""
    except OSError as exc:
        raise ContractError(f"cannot inspect reviewed worktree path {relative}: {exc}") from exc
    if stat.S_ISDIR(info.st_mode):
        head_meta = head_entries.get(relative)
        if head_meta is not None and head_meta[0] == "160000":
            return "gitlink", "160000", b""
        return "absent", "", b""
    raise ContractError(
        f"reviewed worktree path is not a regular file, symlink, or deletion: {relative}"
    )


def _worktree_diff_for_paths(repo: Path, paths: Sequence[str]) -> str:
    """Build a worktree patch from a private index that does not trust live stat cache.

    The live index is never written. Current bytes are hashed with ``--no-filters`` into an
    isolated object store, then ``git diff --cached HEAD`` emits the exact patch, including
    regular-file content, symlink targets, mode, deletion/type changes, and binary form.
    """
    if not paths:
        return ""
    for path in paths:
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise ContractError(f"reviewed path escapes the worktree snapshot: {path}")
        if any(char in path for char in "\0\t\n"):
            raise ContractError(f"reviewed path is unsafe for a git snapshot: {path!r}")
    object_format = _git_value(["rev-parse", "--show-object-format"], repo) or "sha1"
    null_oid = _null_git_oid(object_format)
    head_before = _git_value(["rev-parse", "HEAD"], repo)
    if not head_before:
        raise ContractError("cannot resolve HEAD for the worktree review diff")
    head = run_git(["ls-tree", "-r", "-z", "--full-tree", "HEAD"], repo)
    if head.returncode != 0:
        raise ContractError(
            f"git ls-tree HEAD failed: {(head.stderr or head.stdout).strip()}"
        )
    head_entries = _parse_ls_tree(head.stdout)
    real_index = _absolute_git_path(repo, "index")
    real_objects = _absolute_git_path(repo, "objects")
    first = {
        path: _worktree_snapshot_entry(repo, path, head_entries) for path in paths
    }
    raw: str | None = None
    try:
        raw, index_file, objects_dir = _create_worktree_snapshot_dirs()
        if os.path.abspath(os.fspath(index_file)) == os.path.abspath(os.fspath(real_index)):
            raise ContractError("refusing to use the live git index as a snapshot")
        extra_env = _snapshot_extra_env(index_file, objects_dir, real_objects)
        loaded = run_git(["read-tree", "HEAD"], repo, extra_env=extra_env)
        if loaded.returncode != 0:
            raise ContractError(
                f"git read-tree HEAD failed: {(loaded.stderr or loaded.stdout).strip()}"
            )
        records: list[bytes] = []
        for path in paths:
            kind, mode, payload = first[path]
            if kind == "gitlink":
                continue
            if kind == "absent":
                records.append(f"000000 {null_oid}\t{path}\0".encode("utf-8"))
                continue
            oid = _hash_object_write(repo, payload, extra_env)
            records.append(f"{mode} {oid}\t{path}\0".encode("utf-8"))
        if records:
            updated = run_git(
                ["update-index", "-z", "--index-info"],
                repo,
                extra_env=extra_env,
                input_bytes=b"".join(records),
            )
            if updated.returncode != 0:
                raise ContractError(
                    "git update-index --index-info failed: "
                    + (updated.stderr or updated.stdout).strip()
                )
        second = {
            path: _worktree_snapshot_entry(repo, path, head_entries) for path in paths
        }
        drifted = [path for path in paths if second[path] != first[path]]
        if drifted:
            raise ContractError(
                "worktree drifted while building the review diff: " + ", ".join(drifted)
            )
        head_after = _git_value(["rev-parse", "HEAD"], repo)
        if head_after != head_before:
            raise ContractError("HEAD drifted while building the worktree review diff")
        return _diff_for_paths(
            repo,
            ["diff", *_GIT_DIFF_OPTIONS, "--binary", "--cached", "HEAD"],
            paths,
            extra_env=extra_env,
        )
    finally:
        if raw is not None:
            _rm_snapshot_root(raw)


def build_review_bundle(
    *,
    repo: Path,
    item_id: str,
    item_kind: str,
    base_ref: str,
    criteria_paths: Sequence[Path],
    context_paths: Sequence[Path],
    evidence_args: Sequence[str],
    model_roles_path: Path,
    builder_roles: Sequence[str],
    reviewer_role: str,
    reviewer_launch_receipts: Sequence[Path],
    allow_empty: bool,
    ignored_paths: Sequence[Path] = (),
    context_aliases: Mapping[Path, str] | None = None,
    catalog_runner: Callable[[tuple[str, ...]], tuple[int, bytes, bytes]] | None = None,
) -> ReviewBundle:
    repo = repo.resolve()
    validate_item_id(item_id)
    identity = resolve_review_identities(
        repo=repo,
        model_roles_path=model_roles_path,
        builder_roles=builder_roles,
        reviewer_role=reviewer_role,
    )
    launch = resolve_reviewer_launch_receipts(
        repo=repo,
        receipt_paths=reviewer_launch_receipts,
        identity=identity,
        catalog_runner=catalog_runner,
    )
    launch_receipts = launch.receipts
    base_sha, head_sha = resolve_base(repo, base_ref)
    evidence = resolve_evidence(repo, item_id, evidence_args)
    changed_paths = collect_changed_paths(
        repo,
        base_sha,
        evidence,
        [
            *ignored_paths,
            *criteria_paths,
            *context_paths,
            *(repo / receipt.path for receipt in launch_receipts),
        ],
    )
    if not changed_paths and not allow_empty:
        raise ContractError(
            f"empty implementation diff vs {base_sha}; pass --allow-empty only when the criteria "
            "genuinely require no repository change"
        )

    criteria_records: list[dict[str, object]] = []
    criteria_sections: list[str] = []
    freshness_anchor = 0
    for path in criteria_paths:
        record, content = _document_record(repo, path, "criteria")
        criteria_records.append(record)
        criteria_sections.append(f"===== {record['path']} =====\n{content}")
        freshness_anchor = max(freshness_anchor, int(record["mtime_ns"]))

    context_records: list[dict[str, object]] = []
    context_sections: list[str] = []
    seen_context: set[str] = set()
    aliases = {
        lexical_repo_path(repo, path, "context").relative_to(repo).as_posix(): value
        for path, value in (context_aliases or {}).items()
    }
    for path in context_paths:
        lexical = lexical_repo_path(repo, path, "context")
        relative = lexical.relative_to(repo).as_posix()
        if relative in seen_context:
            continue
        seen_context.add(relative)
        record, content = _document_record(
            repo, lexical, "context", logical_relative=aliases.get(relative, "")
        )
        context_records.append(record)
        context_sections.append(f"===== {record['path']} =====\n{content}")

    work_records = [_file_record(repo, path) for path in changed_paths]
    for record in work_records:
        freshness_anchor = max(freshness_anchor, int(record.get("mtime_ns", 0)))

    evidence_records: list[dict[str, object]] = []
    evidence_sections: list[str] = []
    for path in evidence:
        record, content = _document_record(repo, path, "acceptance-evidence")
        if int(record["mtime_ns"]) < freshness_anchor:
            raise ContractError(
                f"acceptance evidence {record['path']} is stale: it predates criteria or implementation "
                "state; delegate the checks again and replace the evidence"
            )
        evidence_records.append(record)
        evidence_sections.append(f"===== {record['path']} =====\n{content}")

    # ``--no-ext-diff`` and ``--no-textconv`` keep a repository-configured diff or textconv driver —
    # both of which are shell commands — from running while the reviewed diff is produced.
    # Committed history is a tree-to-tree diff and does not consult the live index. Worktree content
    # is hashed into a private index so a matching live-index stat cache cannot omit hidden bytes.
    committed_diff = _diff_for_paths(
        repo,
        ["diff", *_GIT_DIFF_OPTIONS, "--binary", base_sha, "HEAD"],
        changed_paths,
    )
    worktree_diff = _worktree_diff_for_paths(repo, changed_paths)
    commitment_payload = {
        "schema": "independent-terminal-review/v2",
        "itemId": item_id,
        "itemKind": item_kind,
        "baseSha": base_sha,
        "headSha": head_sha,
        "modelRolesPath": identity.document_path,
        "modelRolesSha256": identity.document_sha256,
        "catalogResolutionSha256": identity.catalog_resolution_sha256,
        "reviewerLaunchReceipts": [value.record() for value in launch_receipts],
        "builderIdentities": [value.record() for value in identity.builder_identities],
        "reviewerIdentity": identity.reviewer_identity.record(),
        "reviewerCatalogModelId": launch.catalog_model_id,
        "allowEmpty": allow_empty,
        "criteria": criteria_records,
        "context": context_records,
        "evidence": evidence_records,
        "work": work_records,
        "committedDiffSha256": _hash_bytes(committed_diff.encode("utf-8", errors="replace")),
        "worktreeDiffSha256": _hash_bytes(worktree_diff.encode("utf-8", errors="replace")),
    }
    encoded = json.dumps(
        commitment_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    commitment = f"sha256:{_hash_bytes(encoded)}"
    return ReviewBundle(
        item_id=item_id,
        item_kind=item_kind,
        base_sha=base_sha,
        head_sha=head_sha,
        changed_paths=changed_paths,
        committed_diff=committed_diff,
        worktree_diff=worktree_diff,
        criteria_text="\n\n".join(criteria_sections),
        context_text="\n\n".join(context_sections),
        evidence_text="\n\n".join(evidence_sections),
        evidence_paths=tuple(_repo_relative(repo, path) for path in evidence),
        model_roles_path=identity.document_path,
        model_roles_sha256=identity.document_sha256,
        catalog_resolution_sha256=identity.catalog_resolution_sha256,
        reviewer_launch_receipts=launch_receipts,
        builder_identities=identity.builder_identities,
        reviewer_identity=identity.reviewer_identity,
        reviewer_catalog_model_id=launch.catalog_model_id,
        reviewer_catalog_command=launch.reviewer_catalog_command,
        commitment=commitment,
    )


_REQUEST_CUES = {
    "authorization": re.compile(
        r"\b(authori[sz]e|approval|approve|permission|consent)\b|\b(grant|give|provide)\b.{0,24}\baccess\b|\u6388\u6743|\u6279\u51c6|\u8bb8\u53ef|\u540c\u610f|\u5f00\u901a\u6743\u9650",
        re.I,
    ),
    "decision": re.compile(
        r"\b(decide|decision|choose|select|pick|confirm|trade[- ]?off|priority|scope)\b|\u51b3\u5b9a|\u9009\u62e9|\u786e\u8ba4|\u53d6\u820d|\u4f18\u5148\u7ea7|\u8303\u56f4",
        re.I,
    ),
    "credential": re.compile(
        r"\b(credential|secret|api[- ]?key|access[- ]?token|password|login|certificate)\b|\u51ed\u8bc1|\u5bc6\u94a5|\u8bbf\u95ee\u4ee4\u724c|\u5bc6\u7801|\u767b\u5f55|\u8bc1\u4e66",
        re.I,
    ),
    "external-action": re.compile(
        r"\b(external|outside|click|sign|accept (?:the )?invitation|purchase|contact|upload|physical|provision|enable)\b|\u5916\u90e8|\u7ebf\u4e0b|\u70b9\u51fb|\u7b7e\u7f72|\u8d2d\u4e70|\u8054\u7cfb|\u4e0a\u4f20|\u4eba\u5de5\u6267\u884c|\u5f00\u901a",
        re.I,
    ),
}
_NON_HUMAN_BLOCKERS = re.compile(
    r"\b(?:tests?|testing)\b.{0,32}\b(?:fail|failed|failing|failure|broken)\b"
    r"|\b(?:fail|failed|failing|failure|broken)\b.{0,32}\b(?:tests?|testing)\b"
    r"|\bbudget\b"
    r"|\b(?:technical|implementation)\s+(?:difficulty|problem|issue)\b"
    r"|\b(?:debug(?:ging)?|ordinary bug|code bug)\b"
    r"|\u6d4b\u8bd5\u5931\u8d25|\u5931\u8d25\u7684\u6d4b\u8bd5|\u6280\u672f\u56f0\u96be|\u5b9e\u73b0\u56f0\u96be|\u8c03\u8bd5\u95ee\u9898|\u4ee3\u7801\u9519\u8bef|\u4ee4\u724c\u9884\u7b97|\u6a21\u578b\u8c03\u7528\u9884\u7b97|\u65f6\u95f4\u9884\u7b97",
    re.I,
)


def _extract_exact(lines: Sequence[str], field: str) -> list[str]:
    prefix = field + ":"
    return [line[len(prefix) :].strip() for line in lines if line.startswith(prefix)]


def _validate_human_request(
    terminal: str, kinds: Sequence[str], requests: Sequence[str]
) -> tuple[str, str]:
    if terminal != "HUMAN_BLOCKED":
        if kinds or requests:
            raise ContractError("COMPLETE/REVISE must not carry HUMAN-REQUEST fields")
        return "", ""
    if len(kinds) != 1 or kinds[0] not in HUMAN_REQUEST_KINDS:
        raise ContractError(
            "HUMAN_BLOCKED requires exactly one HUMAN-REQUEST-KIND: "
            + "|".join(HUMAN_REQUEST_KINDS)
        )
    if len(requests) != 1 or len(requests[0].strip()) < 8:
        raise ContractError("HUMAN_BLOCKED requires one non-empty, precise HUMAN-REQUEST")
    request = requests[0].strip()
    if _NON_HUMAN_BLOCKERS.search(request):
        raise ContractError(
            "test failure, budget, ordinary technical difficulty, debugging, and implementation work are REVISE, not HUMAN_BLOCKED"
        )
    if not _REQUEST_CUES[kinds[0]].search(request):
        raise ContractError(
            f"HUMAN-REQUEST does not name a concrete {kinds[0]} action only a human can provide"
        )
    return kinds[0], request


def _artifact_repo_and_path(path: Path, item_kind: str) -> tuple[Path, Path]:
    expected_directory = {"issue": "verdicts", "milestone": "reviews"}.get(item_kind)
    if expected_directory is None:
        raise ContractError(f"unknown terminal artifact kind: {item_kind!r}")
    try:
        lexical = Path(os.path.abspath(os.fspath(path)))
    except OSError as exc:
        raise ContractError(f"terminal artifact path cannot be normalized: {path}: {exc}") from exc
    if (
        lexical.parent.name != expected_directory
        or lexical.parent.parent.name != ".pm"
        or lexical.suffix != ".md"
    ):
        raise ContractError(
            f"{item_kind} terminal artifact must be a direct .md file under "
            f".pm/{expected_directory}/"
        )
    return lexical.parents[2], lexical


def parse_artifact_inputs(path: Path, expected_kind: str) -> ArtifactInputs:
    repo, lexical = _artifact_repo_and_path(path, expected_kind)
    binding = read_repo_regular_file(
        repo,
        lexical,
        "terminal artifact",
        require_single_link=True,
    )
    lines = binding.raw_bytes.decode("utf-8", errors="replace").splitlines()

    def one(field: str) -> str:
        values = _extract_exact(lines, field)
        if len(values) != 1 or not values[0]:
            raise ContractError(f"artifact requires exactly one non-empty {field}: field")
        return values[0]

    kind = one("VERDICT-TYPE")
    if kind != expected_kind:
        raise ContractError(f"artifact kind is {kind!r}, expected {expected_kind!r}")
    terminal = one("TERMINAL")
    if terminal not in TERMINALS:
        raise ContractError(f"artifact has unknown terminal {terminal!r}")
    commitment = one("EVIDENCE-COMMITMENT")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", commitment):
        raise ContractError("artifact commitment must be sha256:<64 lowercase hex>")
    base_sha = one("BASE-SHA")
    head_sha = one("HEAD-SHA")
    if not re.fullmatch(r"[0-9a-f]{40,64}", base_sha) or not re.fullmatch(r"[0-9a-f]{40,64}", head_sha):
        raise ContractError("artifact BASE-SHA/HEAD-SHA must be full lowercase commit hashes")
    goal_path = one("GOAL-PATH")
    if Path(goal_path).is_absolute() or ".." in Path(goal_path).parts:
        raise ContractError("artifact GOAL-PATH must be repository-relative without '..'")
    try:
        evidence_value = json.loads(one("EVIDENCE-PATHS"))
    except (TypeError, ValueError) as exc:
        raise ContractError("artifact EVIDENCE-PATHS must be a JSON string array") from exc
    if (
        not isinstance(evidence_value, list)
        or not evidence_value
        or any(not isinstance(value, str) or not value for value in evidence_value)
    ):
        raise ContractError("artifact EVIDENCE-PATHS must be a non-empty JSON string array")
    if any(Path(value).is_absolute() or ".." in Path(value).parts for value in evidence_value):
        raise ContractError("artifact evidence paths must be repository-relative without '..'")
    evidence_paths = tuple(evidence_value)
    model_roles_path = one("MODEL-ROLES-PATH")
    if model_roles_path != CANONICAL_MODEL_ROLES_PATH:
        raise ContractError(
            "artifact MODEL-ROLES-PATH must name the project-root canonical model policy"
        )
    model_roles_sha256 = one("MODEL-ROLES-SHA256")
    catalog_resolution_sha256 = one("CATALOG-RESOLUTION-SHA256")
    try:
        launch_value = json.loads(one("REVIEWER-LAUNCH-RECEIPTS"))
    except (TypeError, ValueError) as exc:
        raise ContractError("artifact REVIEWER-LAUNCH-RECEIPTS must be a JSON array") from exc
    if not isinstance(launch_value, list) or not launch_value:
        raise ContractError("artifact REVIEWER-LAUNCH-RECEIPTS must be a non-empty JSON array")
    launch_receipts: list[ReviewerLaunchReceipt] = []
    for index, value in enumerate(launch_value):
        if not isinstance(value, dict) or set(value) != {"builderRole", "path", "sha256"}:
            raise ContractError(
                f"artifact reviewer launch receipt {index} has invalid fields"
            )
        builder_role = _role_name(
            _string(value.get("builderRole"), "artifact launch builderRole"),
            "artifact launch builderRole",
        )
        receipt_path = _string(value.get("path"), "artifact launch receipt path")
        receipt_sha256 = _string(value.get("sha256"), "artifact launch receipt digest")
        if Path(receipt_path).is_absolute() or ".." in Path(receipt_path).parts:
            raise ContractError(
                "artifact reviewer launch receipt paths must be repository-relative without '..'"
            )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt_sha256):
            raise ContractError(
                "artifact reviewer launch receipt digest must be sha256:<64 lowercase hex>"
            )
        launch_receipts.append(
            ReviewerLaunchReceipt(builder_role, receipt_path, receipt_sha256)
        )
    if len({value.builder_role for value in launch_receipts}) != len(launch_receipts):
        raise ContractError("artifact reviewer launch receipt builder roles must be unique")
    for field, value in (
        ("MODEL-ROLES-SHA256", model_roles_sha256),
        ("CATALOG-RESOLUTION-SHA256", catalog_resolution_sha256),
    ):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ContractError(f"artifact {field} must be sha256:<64 lowercase hex>")
    try:
        builder_value = json.loads(one("BUILDER-IDENTITIES"))
        reviewer_value = json.loads(one("REVIEWER-IDENTITY"))
    except (TypeError, ValueError) as exc:
        raise ContractError("artifact role identities must be valid JSON") from exc
    if not isinstance(builder_value, list) or not builder_value:
        raise ContractError("artifact BUILDER-IDENTITIES must be a non-empty JSON array")
    builders = tuple(
        _identity_from_record(value, f"artifact builder identity {index}")
        for index, value in enumerate(builder_value)
    )
    if len({value.semantic_role for value in builders}) != len(builders):
        raise ContractError("artifact builder semantic roles must be unique")
    reviewer = _identity_from_record(reviewer_value, "artifact reviewer identity")
    allow_empty_raw = one("ALLOW-EMPTY")
    if allow_empty_raw not in ("true", "false"):
        raise ContractError("artifact ALLOW-EMPTY must be true or false")
    reviewer_output_path = one("REVIEWER-OUTPUT-PATH")
    _validate_reviewer_output_relative(Path(reviewer_output_path))
    reviewer_output_sha256 = one("REVIEWER-OUTPUT-SHA256")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", reviewer_output_sha256):
        raise ContractError(
            "artifact REVIEWER-OUTPUT-SHA256 must be sha256:<64 lowercase hex>"
        )
    reviewer_invocation_schema = one("REVIEWER-INVOCATION")
    if reviewer_invocation_schema != OWNED_REVIEW_INVOCATION_SCHEMA:
        raise ContractError(
            "artifact REVIEWER-INVOCATION must prove verifier-owned native review"
        )
    reviewer_backend = one("REVIEWER-BACKEND")
    if reviewer_backend not in SUPPORTED_REVIEW_BACKENDS or reviewer_backend != reviewer.backend:
        raise ContractError("artifact reviewer backend does not match the bound reviewer identity")
    reviewer_executable = one("REVIEWER-EXECUTABLE")
    if not Path(reviewer_executable).is_absolute():
        raise ContractError("artifact REVIEWER-EXECUTABLE must be an absolute bound path")
    reviewer_model = _string(one("REVIEWER-MODEL"), "artifact REVIEWER-MODEL")
    reviewer_executable_sha256 = one("REVIEWER-EXECUTABLE-SHA256")
    reviewer_executable_closure_sha256 = one("REVIEWER-EXECUTABLE-CLOSURE-SHA256")
    reviewer_config_path = one("REVIEWER-CONFIG-PATH")
    reviewer_config_sha256 = one("REVIEWER-CONFIG-SHA256")
    reviewer_argv_sha256 = one("REVIEWER-ARGV-SHA256")
    for field, value in (
        ("REVIEWER-EXECUTABLE-SHA256", reviewer_executable_sha256),
        ("REVIEWER-EXECUTABLE-CLOSURE-SHA256", reviewer_executable_closure_sha256),
        ("REVIEWER-CONFIG-SHA256", reviewer_config_sha256),
        ("REVIEWER-ARGV-SHA256", reviewer_argv_sha256),
    ):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ContractError(f"artifact {field} must be sha256:<64 lowercase hex>")
    if reviewer_backend == "codex":
        # A Codex review is only meaningful with a bound configuration, so the artifact must carry an
        # absolute path and a digest of real bytes in every mode. "<none>" is rejected unconditionally
        # rather than paired with the public digest of the empty string, which any caller can type.
        if not Path(reviewer_config_path).is_absolute():
            raise ContractError(
                "artifact REVIEWER-CONFIG-PATH must bind an absolute Codex configuration path"
            )
    elif reviewer_config_path != "<none>" or reviewer_config_sha256 != _no_native_config().sha256:
        raise ContractError("artifact reviewer config fields must be empty for this backend")
    human_request_kind, human_request = _validate_human_request(
        terminal,
        _extract_exact(lines, "HUMAN-REQUEST-KIND"),
        _extract_exact(lines, "HUMAN-REQUEST"),
    )
    return ArtifactInputs(
        terminal=terminal,
        human_request_kind=human_request_kind,
        human_request=human_request,
        commitment=commitment,
        base_sha=base_sha,
        head_sha=head_sha,
        goal_path=goal_path,
        evidence_paths=evidence_paths,
        model_roles_path=model_roles_path,
        model_roles_sha256=model_roles_sha256,
        catalog_resolution_sha256=catalog_resolution_sha256,
        reviewer_launch_receipts=tuple(launch_receipts),
        builder_identities=builders,
        reviewer_identity=reviewer,
        allow_empty=allow_empty_raw == "true",
        reviewer_output_path=reviewer_output_path,
        reviewer_output_sha256=reviewer_output_sha256,
        reviewer_invocation_schema=reviewer_invocation_schema,
        reviewer_backend=reviewer_backend,
        reviewer_executable=reviewer_executable,
        reviewer_executable_sha256=reviewer_executable_sha256,
        reviewer_executable_closure_sha256=reviewer_executable_closure_sha256,
        reviewer_config_path=reviewer_config_path,
        reviewer_config_sha256=reviewer_config_sha256,
        reviewer_model=reviewer_model,
        reviewer_argv_sha256=reviewer_argv_sha256,
    )


def parse_terminal_output(
    output: str, expected_commitment: str, expected_reviewer: RoleIdentity
) -> TerminalVerdict:
    lines = output.splitlines()
    if not lines or not re.fullmatch(r"TERMINAL: (COMPLETE|HUMAN_BLOCKED|REVISE)", lines[0]):
        raise ContractError(
            "reviewer first line must be exactly TERMINAL: COMPLETE, TERMINAL: HUMAN_BLOCKED, or TERMINAL: REVISE"
        )
    terminal_lines = [
        line for line in lines if re.fullmatch(r"TERMINAL: (COMPLETE|HUMAN_BLOCKED|REVISE)", line)
    ]
    if len(terminal_lines) != 1:
        raise ContractError("reviewer output must contain exactly one TERMINAL line")
    terminal = lines[0].split(":", 1)[1].strip()

    commitments = _extract_exact(lines, "EVIDENCE-COMMITMENT")
    if commitments != [expected_commitment] or len(lines) < 2 or lines[1] != f"EVIDENCE-COMMITMENT: {expected_commitment}":
        raise ContractError(
            "reviewer evidence commitment is missing, stale, duplicated, or not the exact second line"
        )

    roles = _extract_exact(lines, "REVIEWER-ROLE")
    if (
        roles != [expected_reviewer.semantic_role]
        or len(lines) < 3
        or lines[2] != f"REVIEWER-ROLE: {expected_reviewer.semantic_role}"
    ):
        raise ContractError(
            "reviewer role declaration is missing, duplicated, mismatched, or not the exact third line"
        )

    identities = _extract_exact(lines, "REVIEWER-IDENTITY-SHA256")
    if (
        identities != [expected_reviewer.digest()]
        or len(lines) < 4
        or lines[3] != f"REVIEWER-IDENTITY-SHA256: {expected_reviewer.digest()}"
    ):
        raise ContractError(
            "reviewer identity digest is missing, duplicated, mismatched, or not the exact fourth line"
        )

    kind, request = _validate_human_request(
        terminal,
        _extract_exact(lines, "HUMAN-REQUEST-KIND"),
        _extract_exact(lines, "HUMAN-REQUEST"),
    )
    return TerminalVerdict(terminal=terminal, human_request_kind=kind, human_request=request)


def require_artifact_identity_current(
    bundle: ReviewBundle, artifact: ArtifactInputs
) -> None:
    comparisons = (
        ("model-role path", bundle.model_roles_path, artifact.model_roles_path),
        ("model-role digest", bundle.model_roles_sha256, artifact.model_roles_sha256),
        (
            "catalog-resolution digest",
            bundle.catalog_resolution_sha256,
            artifact.catalog_resolution_sha256,
        ),
        (
            "reviewer launch receipts",
            bundle.reviewer_launch_receipts,
            artifact.reviewer_launch_receipts,
        ),
        ("builder identities", bundle.builder_identities, artifact.builder_identities),
        ("reviewer identity", bundle.reviewer_identity, artifact.reviewer_identity),
        (
            "reviewer catalog model id",
            bundle.reviewer_catalog_model_id,
            artifact.reviewer_model,
        ),
    )
    for label, current, stored in comparisons:
        if current != stored:
            raise ContractError(f"artifact {label} drift")


def verify_stored_artifact(
    *,
    repo: Path,
    bundle: ReviewBundle,
    artifact: ArtifactInputs,
    prompt: str,
    test_executable: Path | None = None,
) -> str:
    """Verify a stored artifact and freshly re-establish every authoritative terminal.

    Everything the caller can write — the artifact fields and the recorded reviewer output bytes —
    is treated as a drift and audit input only. Completion is authorized by one thing: launching the
    freshly resolved canonical reviewer here, against the bundle rebuilt from the current
    repository, and parsing its fresh stdout under the same commitment and reviewer identity that
    the caller cannot produce without the account. Hand-built COMPLETE or HUMAN_BLOCKED bytes
    therefore prove nothing; they only have to survive the cheap drift checks before the reviewer is
    asked again.

    The cheap checks run first so ordinary staleness is reported without spending a model call.

    Returns an audit line naming the fresh evidence. Raises ``ContractError`` on drift, on reviewer
    disagreement, and on any failure to obtain a fresh verdict.
    """
    require_artifact_identity_current(bundle, artifact)
    require_native_reviewer_current(
        repo, bundle, artifact, test_executable=test_executable
    )
    stored_output = require_reviewer_output_current(repo, artifact)
    if bundle.head_sha != artifact.head_sha:
        raise ContractError(
            f"HEAD drift: artifact {artifact.head_sha}, current {bundle.head_sha}"
        )
    if bundle.commitment != artifact.commitment:
        raise ContractError(
            f"evidence commitment drift: artifact {artifact.commitment}, current "
            f"{bundle.commitment}"
        )
    if artifact.terminal not in AUTHORITATIVE_TERMINALS:
        return f"artifact current — {bundle.commitment}"

    stored_verdict = parse_terminal_output(
        stored_output.text, bundle.commitment, bundle.reviewer_identity
    )
    artifact_verdict = TerminalVerdict(
        artifact.terminal, artifact.human_request_kind, artifact.human_request
    )
    if stored_verdict != artifact_verdict:
        raise ContractError(
            "stored reviewer output does not match the terminal artifact terminal/request fields"
        )

    invocation = invoke_native_reviewer(
        repo=repo, bundle=bundle, prompt=prompt, test_executable=test_executable
    )
    _require_launch_matches_artifact(
        artifact,
        executable=invocation.executable,
        executable_sha256=invocation.executable_sha256,
        executable_closure_sha256=invocation.executable_closure_sha256,
        config_path=invocation.config_path,
        config_sha256=invocation.config_sha256,
        argv_sha256=invocation.argv_sha256,
        model=invocation.model,
        backend=invocation.backend,
    )
    verdict = parse_terminal_output(
        invocation.output.text, bundle.commitment, bundle.reviewer_identity
    )
    if verdict != artifact_verdict:
        mismatch = verdict.terminal
        if verdict.terminal == artifact.terminal == "HUMAN_BLOCKED":
            mismatch += " with a different HUMAN-REQUEST-KIND/HUMAN-REQUEST"
        raise ContractError(
            f"fresh reviewer verdict {mismatch} does not re-establish the stored "
            f"{artifact.terminal}; fresh output {invocation.output.relative_path}"
        )
    return (
        f"artifact current and re-established {verdict.terminal} — {bundle.commitment} "
        f"(fresh reviewer output {invocation.output.relative_path})"
    )


def write_terminal_artifact(
    *,
    path: Path,
    bundle: ReviewBundle,
    verdict: TerminalVerdict,
    reviewer_invocation: NativeReviewerInvocation,
    allow_empty: bool,
    goal_path: str,
    parser_note: str = "",
) -> None:
    output = reviewer_invocation.output
    _validate_reviewer_output_relative(Path(output.relative_path))
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", output.sha256):
        raise ContractError("reviewer output digest must be sha256:<64 lowercase hex>")
    if reviewer_invocation.backend != bundle.reviewer_identity.backend:
        raise ContractError("native reviewer backend does not match the bound reviewer identity")
    if not Path(reviewer_invocation.executable).is_absolute():
        raise ContractError("native reviewer executable path must be absolute")
    for label, value in (
        ("executable", reviewer_invocation.executable_sha256),
        ("executable closure", reviewer_invocation.executable_closure_sha256),
        ("configuration", reviewer_invocation.config_sha256),
        ("argv", reviewer_invocation.argv_sha256),
    ):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ContractError(
                f"native reviewer {label} digest must be sha256:<64 lowercase hex>"
            )
    if reviewer_invocation.backend == "codex":
        # Both the production account configuration and the test seam's fixture binding are absolute
        # paths over real bytes; "<none>" is not a configuration and never reaches an artifact.
        if not Path(reviewer_invocation.config_path).is_absolute():
            raise ContractError("native reviewer Codex configuration path must be absolute")
    elif (
        reviewer_invocation.config_path != "<none>"
        or reviewer_invocation.config_sha256 != _no_native_config().sha256
    ):
        raise ContractError("native reviewer config must be empty for this backend")
    if reviewer_invocation.model != bundle.reviewer_catalog_model_id:
        raise ContractError(
            "native reviewer model does not match the freshly resolved catalog model id"
        )
    artifact_repo, artifact_path = _artifact_repo_and_path(path, bundle.item_kind)
    lines = [
        f"# Independent terminal verdict — {bundle.item_id}",
        f"VERDICT-TYPE: {bundle.item_kind}",
        f"TERMINAL: {verdict.terminal}",
        f"EVIDENCE-COMMITMENT: {bundle.commitment}",
        f"BASE-SHA: {bundle.base_sha}",
        f"HEAD-SHA: {bundle.head_sha}",
        f"GOAL-PATH: {goal_path}",
        f"EVIDENCE-PATHS: {json.dumps(bundle.evidence_paths, ensure_ascii=False, separators=(',', ':'))}",
        f"MODEL-ROLES-PATH: {bundle.model_roles_path}",
        f"MODEL-ROLES-SHA256: {bundle.model_roles_sha256}",
        f"CATALOG-RESOLUTION-SHA256: {bundle.catalog_resolution_sha256}",
        f"REVIEWER-LAUNCH-RECEIPTS: {json.dumps([value.record() for value in bundle.reviewer_launch_receipts], ensure_ascii=False, separators=(',', ':'))}",
        f"BUILDER-IDENTITIES: {json.dumps([value.record() for value in bundle.builder_identities], ensure_ascii=False, separators=(',', ':'))}",
        f"REVIEWER-IDENTITY: {json.dumps(bundle.reviewer_identity.record(), ensure_ascii=False, separators=(',', ':'))}",
        f"ALLOW-EMPTY: {'true' if allow_empty else 'false'}",
        f"REVIEWER-INVOCATION: {OWNED_REVIEW_INVOCATION_SCHEMA}",
        f"REVIEWER-BACKEND: {reviewer_invocation.backend}",
        f"REVIEWER-MODEL: {reviewer_invocation.model}",
        f"REVIEWER-EXECUTABLE: {reviewer_invocation.executable}",
        f"REVIEWER-EXECUTABLE-SHA256: {reviewer_invocation.executable_sha256}",
        f"REVIEWER-EXECUTABLE-CLOSURE-SHA256: {reviewer_invocation.executable_closure_sha256}",
        f"REVIEWER-CONFIG-PATH: {reviewer_invocation.config_path}",
        f"REVIEWER-CONFIG-SHA256: {reviewer_invocation.config_sha256}",
        f"REVIEWER-ARGV-SHA256: {reviewer_invocation.argv_sha256}",
        f"REVIEWER-OUTPUT-PATH: {output.relative_path}",
        f"REVIEWER-OUTPUT-SHA256: {output.sha256}",
    ]
    if verdict.terminal == "HUMAN_BLOCKED":
        lines.extend(
            [
                f"HUMAN-REQUEST-KIND: {verdict.human_request_kind}",
                f"HUMAN-REQUEST: {verdict.human_request}",
            ]
        )
    if parser_note:
        lines.extend(["", f"PARSER-NOTE: {parser_note}"])
    quoted = "\n".join(f"> {line}" for line in output.text[-12000:].splitlines())
    lines.extend(["", "## Reviewer output (quoted, non-authoritative)", quoted or "> (empty)", ""])
    _write_controlled_output(
        artifact_repo,
        artifact_path,
        "\n".join(lines).encode("utf-8"),
        f"{bundle.item_kind} terminal artifact",
    )


def terminal_exit_code(terminal: str) -> int:
    return {"COMPLETE": 0, "HUMAN_BLOCKED": 3, "REVISE": 2}[terminal]
