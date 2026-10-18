#!/usr/bin/env python3
"""Open the scheduler TUI with an optional immutable PM issue context."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Mapping


FLOW = "local/scheduler"
FLOW_ENTRY = Path(".humanize/flows/scheduler/__init__.py")
HUMANIZE_REPOSITORY = "https://github.com/humanfia/humanize2.git"
HUMANIZE_REVISION = "8fbb2b718d74b7ef1d3d0650a9768415ec489f26"
CONTEXT_FD_ENV = "HUMANIZE_SCHEDULER_CONTEXT_FD"
CONTEXT_SHA_ENV = "HUMANIZE_SCHEDULER_CONTEXT_SHA256"
SENTRY_ENV = "HUMANIZE_SENTRY"
HUMANIZE_HOME_ENV = "HUMANIZE_HOME"
PYTHONDONTWRITEBYTECODE_ENV = "PYTHONDONTWRITEBYTECODE"
OBSERVATION_CHECKPOINT = Path(
    "workflow-output/humanize/scheduler/observation-checkpoint.json"
)
OWNER_LOCK = Path(".humanize/scheduler/owner.lock")
RUNTIME_HOME = Path(".humanize/scheduler/runtime")
PIN_CHECK_RELATIVE = Path(
    ".claude/skills/humanize-workflow/scripts/ensure_humanize.py"
)
MAX_CONTEXT_BYTES = 128 * 1024
MAX_ISSUE_BYTES = 1024 * 1024
MAX_FEEDBACK_LENGTH = 8192
SNAPSHOT_KEYS = (
    "iteration_mode_selected",
    "iteration_mode_effective",
    "human_checkin_lease_minutes",
    "human_unavailable_fallback",
    "human_checkin_lease_expires_at",
    "human_checkin_lease_status",
    "iteration_mode_decision_at",
)
DEFAULT_AGENTS = (
    ("scheduler", "codex/gpt-6-astra:max"),
    ("executor", "claude/claude-sonnet-5:max"),
    ("reviewer", "codex/gpt-6-astra:max"),
    ("planner", "claude/claude-opus-5:max"),
    ("preflight", "claude/claude-opus-5:xhigh"),
    ("collector", "codex/gpt-5.6-luna:max"),
    ("counterevidence", "codex/gpt-6-astra:max"),
    ("reducer", "codex/gpt-6-astra:max"),
    ("knowledge", "codex/gpt-5.6-terra:max"),
    ("applicability", "claude/claude-opus-5:xhigh"),
    ("experimenter", "codex/gpt-6-astra:xhigh"),
)


def scheduler_argv(hmz: str | Path = "hmz") -> tuple[str, ...]:
    """Build the native Humanize TUI command in scheduler role order."""
    argv = [os.fspath(hmz), "-f", FLOW]
    for _, agent in DEFAULT_AGENTS:
        argv.extend(("-a", agent))
    return tuple(argv)


def launcher_check(hmz: str | Path) -> dict[str, object]:
    """Describe the exact no-model-call scheduler launch configuration."""
    return {
        "schema": "humanize-scheduler-launch-check/v1",
        "flow": FLOW,
        "humanizeRevision": HUMANIZE_REVISION,
        "hmz": os.fspath(hmz),
        "agents": [
            {"role": role, "spec": specification}
            for role, specification in DEFAULT_AGENTS
        ],
        "argv": list(scheduler_argv(hmz)),
        "modelCalls": 0,
        "telemetry": "off",
        "nativeGoals": "off",
        "wholeRunContainer": "off",
        "callbackTools": "not-offered",
        "sourceBytecode": "disabled",
        "runtimeHome": str(RUNTIME_HOME),
        "persistedBehavior": "project-isolated",
    }


def _reject_recursive_launch(environment: Mapping[str, str]) -> None:
    """Reject a launcher inherited from an already active Scheduler process."""
    if environment.get(CONTEXT_FD_ENV) or environment.get(CONTEXT_SHA_ENV):
        raise SystemExit(
            "refusing recursive Humanize scheduler launch: "
            "an active sealed scheduler context is already present"
        )


def _acquire_owner_lock(root: Path) -> int:
    """Hold one nonblocking Scheduler owner for this project until process exit."""
    lock_path = root / OWNER_LOCK
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise SystemExit(f"scheduler owner lock is unsafe or unavailable: {error}") from error
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or details.st_uid != os.geteuid()
            or details.st_mode & 0o077
        ):
            raise SystemExit(
                "scheduler owner lock must be an owner-only single-link regular file"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SystemExit(
                "another Humanize scheduler is already active for this project"
            ) from error
        os.set_inheritable(descriptor, True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _prepare_runtime_home(root: Path) -> Path:
    """Create the project-owned Humanize state root without following aliases."""
    root = root.resolve(strict=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(root, flags)
    leaf_descriptor: int | None = None
    try:
        for component in RUNTIME_HOME.parts[:-1]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        try:
            os.mkdir(RUNTIME_HOME.name, 0o700, dir_fd=descriptor)
        except FileExistsError:
            pass
        leaf_descriptor = os.open(RUNTIME_HOME.name, flags, dir_fd=descriptor)
        details = os.fstat(leaf_descriptor)
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_uid != os.geteuid()
            or details.st_mode & 0o077
        ):
            raise SystemExit("Humanize runtime home must be an owner-only directory")
    except OSError as error:
        raise SystemExit(f"Humanize runtime home is unsafe or unavailable: {error}") from error
    finally:
        if leaf_descriptor is not None:
            os.close(leaf_descriptor)
        os.close(descriptor)
    return root / RUNTIME_HOME


def _launch_environment(
    environment: Mapping[str, str],
    context_descriptor: int,
    context_digest: str,
    runtime_home: Path,
) -> dict[str, str]:
    """Build the TUI environment with sealed context and project-local runtime state."""
    launched = dict(environment)
    launched[CONTEXT_FD_ENV] = str(context_descriptor)
    launched[CONTEXT_SHA_ENV] = context_digest
    launched[SENTRY_ENV] = "off"
    launched[HUMANIZE_HOME_ENV] = str(runtime_home)
    launched[PYTHONDONTWRITEBYTECODE_ENV] = "1"
    return launched


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise SystemExit(f"{label} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise SystemExit(f"{label} is not a valid UTC timestamp") from error


def _regular_relative_bytes(root: Path, relative: Path, label: str, maximum: int) -> bytes:
    if relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} for part in relative.parts
    ):
        raise SystemExit(f"{label} must be a plain project-relative path")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    directory_flags = flags | os.O_DIRECTORY
    descriptor = os.open(root, directory_flags)
    leaf: int | None = None
    try:
        for component in relative.parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        leaf = os.open(relative.parts[-1], flags | os.O_NONBLOCK, dir_fd=descriptor)
        status = os.fstat(leaf)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise SystemExit(f"{label} must be a single-link regular file")
        if status.st_size <= 0 or status.st_size > maximum:
            raise SystemExit(f"{label} has an invalid bounded size")
        chunks: list[bytes] = []
        remaining = status.st_size
        while remaining:
            chunk = os.read(leaf, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if remaining or len(raw) != status.st_size:
            raise SystemExit(f"{label} changed while it was read")
        after = os.fstat(leaf)
        if (
            status.st_dev,
            status.st_ino,
            status.st_size,
            status.st_mtime_ns,
            status.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise SystemExit(f"{label} changed while it was read")
        return raw
    except OSError as error:
        raise SystemExit(f"{label} is unsafe or unavailable: {error}") from error
    finally:
        if leaf is not None:
            os.close(leaf)
        os.close(descriptor)


def _legacy_issue_snapshot() -> dict[str, str]:
    snapshot = {
        "iteration_mode_selected": "autonomous",
        "iteration_mode_effective": "autonomous",
        "human_checkin_lease_minutes": "none",
        "human_unavailable_fallback": "pause",
        "human_checkin_lease_expires_at": "none",
        "human_checkin_lease_status": "not_applicable",
        "iteration_mode_decision_at": "1970-01-01T00:00:00Z",
    }
    return {**snapshot, "iteration_mode_snapshot_sha256": _canonical_sha256(snapshot)}


def _issue_snapshot(raw: bytes) -> tuple[dict[str, str], bool]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SystemExit("PM issue must be UTF-8") from error
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise SystemExit("PM issue must start with frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise SystemExit("PM issue frontmatter is unterminated") from error
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or not key or key in metadata:
            raise SystemExit("PM issue frontmatter is malformed or has duplicate keys")
        metadata[key] = value.strip()
    expected = set(SNAPSHOT_KEYS) | {"iteration_mode_snapshot_sha256"}
    present = expected.intersection(metadata)
    if not present:
        return _legacy_issue_snapshot(), True
    if present != expected:
        raise SystemExit("PM issue has a missing or partial iteration-mode snapshot")
    snapshot = {key: metadata[key] for key in SNAPSHOT_KEYS}
    if metadata["iteration_mode_snapshot_sha256"] != _canonical_sha256(snapshot):
        raise SystemExit("PM issue iteration-mode snapshot digest is invalid")
    selected = snapshot["iteration_mode_selected"]
    effective = snapshot["iteration_mode_effective"]
    fallback = snapshot["human_unavailable_fallback"]
    status = snapshot["human_checkin_lease_status"]
    if selected not in {"autonomous", "human_guided"}:
        raise SystemExit("PM issue iteration-mode snapshot has an unknown selected mode")
    if effective not in {"autonomous", "human_guided"}:
        raise SystemExit("PM issue iteration-mode snapshot has an unknown effective mode")
    if fallback not in {"pause", "autonomous_review"}:
        raise SystemExit("PM issue iteration-mode snapshot has an unknown fallback")
    decision = _utc(snapshot["iteration_mode_decision_at"], "iteration_mode_decision_at")
    lease = snapshot["human_checkin_lease_minutes"]
    expiry = snapshot["human_checkin_lease_expires_at"]
    if selected == "autonomous":
        valid = (
            effective == "autonomous"
            and status == "not_applicable"
            and lease == "none"
            and expiry == "none"
        )
    else:
        try:
            lease_value = int(lease)
        except ValueError:
            lease_value = 0
        if not 5 <= lease_value <= 1440 or expiry == "none":
            valid = False
        else:
            expiry_time = _utc(expiry, "human_checkin_lease_expires_at")
            valid = (
                status == "active"
                and effective == "human_guided"
                and expiry_time > decision
            ) or (
                status == "expired"
                and effective == "autonomous"
                and fallback == "autonomous_review"
                and expiry_time <= decision
            )
    if not valid:
        raise SystemExit("PM issue iteration-mode snapshot is contradictory")
    return (
        {**snapshot, "iteration_mode_snapshot_sha256": metadata["iteration_mode_snapshot_sha256"]},
        False,
    )


def _checkpoint_receipt(root: Path, human_feedback: str) -> dict[str, object]:
    if not human_feedback.strip() or len(human_feedback) > MAX_FEEDBACK_LENGTH:
        raise SystemExit("human feedback must be non-blank and at most 8192 characters")
    raw = _regular_relative_bytes(
        root, OBSERVATION_CHECKPOINT, "scheduler observation checkpoint", MAX_CONTEXT_BYTES
    )
    try:
        checkpoint = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SystemExit("scheduler observation checkpoint must be valid JSON") from error
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get("schema") != "humanize-external-observation-checkpoint/v2"
        or checkpoint.get("status") != "awaiting_human_feedback"
        or checkpoint.get("next_planning_allowed") is not False
        or checkpoint.get("human_feedback") is not None
        or not isinstance(checkpoint.get("checkpoint_id"), str)
    ):
        raise SystemExit("scheduler observation checkpoint is not awaiting bound feedback")
    body = dict(checkpoint)
    checkpoint_id = body.pop("checkpoint_id")
    if checkpoint_id != _canonical_sha256(body):
        raise SystemExit("scheduler observation checkpoint commitment is invalid")
    receipt = {
        "schema": "humanize-human-feedback-receipt/v1",
        "checkpoint_artifact": str(OBSERVATION_CHECKPOINT),
        "checkpoint_sha256": _sha256(raw),
        "checkpoint_id": checkpoint_id,
        "human_feedback": human_feedback,
        "received_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    receipt["receipt_sha256"] = _canonical_sha256(receipt)
    return receipt


def build_invocation_context(
    root: Path,
    *,
    pm_issue: Path | None,
    human_feedback: str | None,
) -> dict[str, object]:
    root = root.resolve(strict=True)
    snapshot: dict[str, str] | None = None
    legacy_default = False
    issue_path: str | None = None
    issue_sha256: str | None = None
    if pm_issue is not None:
        if pm_issue.is_absolute() or len(pm_issue.parts) != 4 or pm_issue.parts[:2] != (".pm", "issues") or pm_issue.suffix != ".md":
            raise SystemExit("--pm-issue must name .pm/issues/<milestone>/<issue>.md")
        issue_path = pm_issue.as_posix()
        issue_raw = _regular_relative_bytes(root, pm_issue, "PM issue", MAX_ISSUE_BYTES)
        snapshot, legacy_default = _issue_snapshot(issue_raw)
        issue_sha256 = _sha256(issue_raw)
    receipt = (
        _checkpoint_receipt(root, human_feedback)
        if human_feedback is not None
        else None
    )
    context = {
        "schema": "humanize-scheduler-invocation-context/v1",
        "policy_source": "pm_issue" if snapshot is not None else "project_config",
        "pm_issue_path": issue_path,
        "pm_issue_sha256": issue_sha256,
        "iteration_policy_snapshot": snapshot,
        "iteration_policy_legacy_default": legacy_default,
        "human_feedback_receipt": receipt,
    }
    context["context_sha256"] = _canonical_sha256(context)
    if len(_canonical_bytes(context)) > MAX_CONTEXT_BYTES:
        raise SystemExit("scheduler invocation context exceeds its bounded size")
    return context


def _sealed_context(context: dict[str, object]) -> tuple[int, str]:
    payload = _canonical_bytes(context)
    descriptor = os.memfd_create(
        "humanize-scheduler-context", getattr(os, "MFD_ALLOW_SEALING", 0)
    )
    try:
        os.write(descriptor, payload)
        fcntl.fcntl(
            descriptor,
            fcntl.F_ADD_SEALS,
            fcntl.F_SEAL_SEAL
            | fcntl.F_SEAL_SHRINK
            | fcntl.F_SEAL_GROW
            | fcntl.F_SEAL_WRITE,
        )
        os.set_inheritable(descriptor, True)
        return descriptor, _sha256(payload)
    except BaseException:
        os.close(descriptor)
        raise


def _pin_check_script() -> Path:
    """Locate the installed skill-owned provenance checker from either source layout."""
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / PIN_CHECK_RELATIVE
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise SystemExit(
        f"pinned Humanize validator is unavailable: {PIN_CHECK_RELATIVE}"
    )


def _validated_source_binary(
    source_root: Path, environment: Mapping[str, str]
) -> Path:
    """Return hmz only after the shared validator binds its source and installation."""
    root = source_root.expanduser().resolve()
    check_environment = dict(environment)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP"):
        check_environment.pop(name, None)
    check_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        checked = subprocess.run(
            [
                sys.executable,
                "-B",
                str(_pin_check_script()),
                "--check",
                "--target",
                str(root),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
            env=check_environment,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"pinned Humanize validation could not run: {error}") from error
    if checked.returncode != 0:
        detail = checked.stderr.strip() or checked.stdout.strip() or "unknown validation error"
        raise SystemExit(f"pinned Humanize failed audited source validation: {detail}")
    try:
        evidence = json.loads(checked.stdout)
        binary = Path(evidence["binary"]).resolve(strict=True)
        evidence_root = Path(evidence["root"]).resolve(strict=True)
        expected = (root / ".venv/bin/hmz").resolve(strict=True)
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as error:
        raise SystemExit("pinned Humanize validation returned malformed evidence") from error
    if (
        evidence_root != root
        or binary != expected
        or evidence.get("repository") != HUMANIZE_REPOSITORY
        or evidence.get("revision") != HUMANIZE_REVISION
        or evidence.get("version") != "hmz 0.1.0"
    ):
        raise SystemExit("pinned Humanize validation evidence does not match this launcher")
    return expected


def resolve_hmz(environ: Mapping[str, str] | None = None) -> Path:
    """Resolve only the source-local CLI admitted by the shared strong validator."""
    environment = os.environ if environ is None else environ
    configured = environment.get("HUMANIZE2_HOME")
    if configured:
        source_root = Path(configured).expanduser()
    else:
        data_home = environment.get("XDG_DATA_HOME")
        base = Path(data_home).expanduser() if data_home else Path.home() / ".local/share"
        source_root = base / "humanize2" / "source" / HUMANIZE_REVISION
    return _validated_source_binary(source_root, environment)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pm-issue", type=Path)
    parser.add_argument("--human-feedback")
    parser.add_argument(
        "--flow-history",
        action="store_true",
        help="list Humanize flow calls, agents, and recorded actions without starting a model",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="show the most recent flow record and its current branch status",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print the pinned flow and automatic agent defaults without a model call",
    )
    args = parser.parse_args(argv)
    project_root = Path(__file__).resolve().parent.parent
    flow_entry = project_root / FLOW_ENTRY
    if not flow_entry.is_file():
        raise SystemExit(f"scheduler flow is missing: {flow_entry}")
    if args.flow_history or args.status:
        if args.pm_issue is not None or args.human_feedback is not None or args.check:
            parser.error("history/status cannot be combined with launch context or --check")
        helper = project_root / ".humanize/flow_history.py"
        command = [sys.executable, str(helper), "--workspace", str(project_root)]
        if args.status:
            command.append("--status")
        subprocess.run(command, check=True)
        return
    hmz = resolve_hmz()
    if args.check:
        if args.pm_issue is not None or args.human_feedback is not None:
            parser.error("--check cannot be combined with run context options")
        print(json.dumps(launcher_check(hmz), indent=2, sort_keys=True))
        return
    _reject_recursive_launch(os.environ)
    command = list(scheduler_argv(hmz))
    owner_descriptor = _acquire_owner_lock(project_root)
    context_descriptor: int | None = None
    try:
        runtime_home = _prepare_runtime_home(project_root)
        context = build_invocation_context(
            project_root,
            pm_issue=args.pm_issue,
            human_feedback=args.human_feedback,
        )
        context_descriptor, digest = _sealed_context(context)
        environment = _launch_environment(
            os.environ, context_descriptor, digest, runtime_home
        )
        os.chdir(project_root)
        os.execve(hmz, command, environment)
    finally:
        if context_descriptor is not None:
            os.close(context_descriptor)
        os.close(owner_descriptor)


if __name__ == "__main__":
    main()
