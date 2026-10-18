"""Shared opt-in policy and process-lifetime filesystem transaction support."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any


USER_CONFIG_WRITE_ENV = "GENERAL_AGENT_ALLOW_USER_CONFIG_WRITE"
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in TRUTHY_ENV_VALUES


def user_config_write_enabled() -> bool:
    return enabled(USER_CONFIG_WRITE_ENV)


@dataclass(frozen=True)
class PathSnapshot:
    """No-follow identity used to detect target changes after preflight."""

    mode: int
    device: int
    inode: int
    size: int
    modified_ns: int


def lstat_snapshot(path: Path) -> PathSnapshot | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    return PathSnapshot(
        metadata.st_mode,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def path_kind(snapshot: PathSnapshot | None) -> str:
    if snapshot is None:
        return "missing"
    if stat.S_ISLNK(snapshot.mode):
        return "symbolic link"
    if stat.S_ISREG(snapshot.mode):
        return "regular file"
    if stat.S_ISDIR(snapshot.mode):
        return "directory"
    return "special file"


def _required_open_flag(name: str) -> int:
    value = getattr(os, name, None)
    if not isinstance(value, int) or value == 0:
        raise RuntimeError(f"platform lacks required no-follow filesystem flag {name}")
    return value


def read_regular_bytes(path: Path) -> tuple[bytes, PathSnapshot]:
    snapshot = lstat_snapshot(path)
    if snapshot is None or not stat.S_ISREG(snapshot.mode):
        raise RuntimeError(f"{path}: expected a regular file, found {path_kind(snapshot)}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | _required_open_flag("O_NOFOLLOW")
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(f"{path}: unable to open a no-follow regular file: {error}") from error
    try:
        opened = os.fstat(descriptor)
        opened_snapshot = PathSnapshot(
            opened.st_mode,
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        if opened_snapshot != snapshot or not stat.S_ISREG(opened.st_mode):
            raise RuntimeError(f"{path}: changed while it was being opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
    finally:
        os.close(descriptor)
    if lstat_snapshot(path) != snapshot:
        raise RuntimeError(f"{path}: changed while it was being read")
    return content, snapshot


def render_managed_block(
    current: bytes,
    block: bytes,
    begin: bytes,
    end: bytes,
    target: Path,
) -> bytes:
    """Replace one managed block while preserving every byte outside it."""

    if block.count(begin) != 1 or block.count(end) != 1 or block.find(begin) >= block.find(end):
        raise RuntimeError("generated managed block is malformed")
    starts = current.count(begin)
    ends = current.count(end)
    if starts == 0 and ends == 0:
        if not current:
            return block
        separator = b"" if current.endswith(b"\n\n") else (b"\n" if current.endswith(b"\n") else b"\n\n")
        return current + separator + block
    if starts != 1 or ends != 1:
        raise RuntimeError(f"{target}: managed markers must appear exactly once as a balanced pair")
    start = current.find(begin)
    end_start = current.find(end)
    if start >= end_start:
        raise RuntimeError(f"{target}: managed markers are out of order")
    end_offset = end_start + len(end)
    return current[:start] + block.rstrip(b"\r\n") + current[end_offset:]


@dataclass(frozen=True)
class PathState:
    """Exact no-follow state needed for validation and process-lifetime rollback."""

    snapshot: PathSnapshot | None
    content: bytes | None = None
    link_target: str | None = None

    @property
    def kind(self) -> str:
        return path_kind(self.snapshot)


@dataclass(frozen=True)
class InputGuard:
    path: Path
    expected: PathState
    label: str


@dataclass(frozen=True)
class DirectoryAction:
    path: Path
    expected: PathSnapshot | None
    mode: int


@dataclass(frozen=True)
class MutationAction:
    path: Path
    before: PathState
    desired_kind: str
    content: bytes | None = None
    link_target: str | None = None
    mode: int | None = None
    label: str = "managed target"


@dataclass(frozen=True)
class SyncResult:
    changed: tuple[Path, ...]
    metadata: dict[str, Any]


class SyncTransactionError(RuntimeError):
    """A transaction failed, with a distinct signal when rollback was incomplete."""

    def __init__(self, message: str, *, rollback_incomplete: bool = False) -> None:
        super().__init__(message)
        self.rollback_incomplete = rollback_incomplete


@dataclass(frozen=True)
class CanonicalAgentSource:
    path: Path
    state: PathState
    metadata: dict[str, Any]
    body: str
    route: Any


@dataclass(frozen=True)
class CanonicalInputs:
    """One exact read and validation of models.json plus canonical agent sources."""

    model_path: Path
    model_state: PathState
    role_document: dict[str, Any]
    agent_directory: Path
    agent_directory_state: PathState
    agents: tuple[CanonicalAgentSource, ...]


def load_canonical_inputs(model_path: Path, agent_directory: Path) -> CanonicalInputs:
    """Read and validate canonical model/agent bytes once for an aggregate sync."""

    from agent_roles import AgentRoleError, parse_frontmatter, resolve_agent
    from validate_model_roles import ValidationError, validate

    models = _absolute(model_path)
    agents_root = _absolute(agent_directory)
    model_state = capture_path_state(models)
    _require_kind(model_state, models, {"regular file"}, "canonical model policy")
    assert model_state.content is not None
    try:
        document = json.loads(model_state.content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AgentRoleError(f"cannot load canonical model roles from {models}: {error}") from error
    if not isinstance(document, dict):
        raise AgentRoleError(f"canonical model roles at {models} must be a JSON object")
    try:
        validate(document)
    except ValidationError as error:
        raise AgentRoleError(f"invalid canonical model-role document: {error}") from error
    resolution = document.get("catalogResolution")
    if not isinstance(resolution, dict) or resolution.get("status") != "verified":
        raise AgentRoleError(
            "canonical model roles are bootstrap-unverified; resolve every active role through "
            "authenticated native catalogs before runtime sync"
        )
    roles = document.get("roles")
    active_roles = {
        name
        for name, role in roles.items()
        if isinstance(role, dict) and role.get("dispatchStatus") == "active"
    } if isinstance(roles, dict) else set()
    if set(resolution.get("verifiedRoles", [])) != active_roles:
        raise AgentRoleError("canonical verifiedRoles does not exactly cover active roles")

    directory_state = capture_path_state(agents_root)
    _require_kind(directory_state, agents_root, {"directory"}, "canonical agent directory")
    captured: list[CanonicalAgentSource] = []
    for path in sorted(agents_root.iterdir(), key=lambda item: item.name):
        if path.suffix != ".md":
            continue
        source_state = capture_path_state(path)
        _require_kind(source_state, path, {"regular file"}, "canonical agent source")
        assert source_state.content is not None
        try:
            text = source_state.content.decode("utf-8")
        except UnicodeError as error:
            raise AgentRoleError(f"{path}: canonical agent source is not UTF-8") from error
        metadata, body = parse_frontmatter(text)
        route = resolve_agent(metadata, document, str(path))
        captured.append(
            CanonicalAgentSource(path, source_state, metadata, body, route)
        )
    if capture_path_state(agents_root) != directory_state:
        raise AgentRoleError(f"{agents_root}: canonical agent directory changed while read")
    if not captured:
        raise AgentRoleError(f"no canonical agent definitions in {agents_root}")
    names = [item.route.name for item in captured]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise AgentRoleError(f"duplicate canonical agent names: {', '.join(duplicates)}")
    return CanonicalInputs(
        models,
        model_state,
        document,
        agents_root,
        directory_state,
        tuple(captured),
    )


def add_canonical_input_guards(plan: "SyncPlan", inputs: CanonicalInputs) -> None:
    plan.add_directory_chain(inputs.model_path.parent, mode=0o755)
    plan.add_file_guard(
        inputs.model_path,
        inputs.model_state,
        label="canonical model policy",
    )
    plan.add_directory_chain(inputs.agent_directory, mode=0o755)
    plan.add_guard(
        inputs.agent_directory,
        inputs.agent_directory_state,
        label="canonical agent directory membership",
    )
    for agent in inputs.agents:
        plan.add_file_guard(
            agent.path,
            agent.state,
            label=f"canonical agent source {agent.path.name}",
        )


def add_role_document_guard(
    plan: "SyncPlan", model_path: Path, role_document: dict[str, Any]
) -> None:
    """Bind a directly loaded role document to the exact model-policy source bytes."""

    absolute = _absolute(model_path)
    plan.add_directory_chain(absolute.parent, mode=0o755)
    state = plan.add_file_guard(absolute, label="canonical model policy")
    assert state.content is not None
    try:
        captured = json.loads(state.content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{absolute}: invalid canonical model policy: {error}") from error
    if captured != role_document:
        raise RuntimeError(
            f"{absolute}: canonical model policy changed while its routes were rendered"
        )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def capture_path_state(path: Path) -> PathState:
    """Capture a leaf without following it and bind regular/link payload bytes."""

    path = _absolute(path)
    snapshot = lstat_snapshot(path)
    if snapshot is None:
        return PathState(None)
    if stat.S_ISREG(snapshot.mode):
        content, opened = read_regular_bytes(path)
        if opened != snapshot:
            raise RuntimeError(f"{path}: changed while its state was captured")
        return PathState(opened, content=content)
    if stat.S_ISLNK(snapshot.mode):
        target = os.readlink(path)
        if lstat_snapshot(path) != snapshot:
            raise RuntimeError(f"{path}: changed while its link target was captured")
        return PathState(snapshot, link_target=target)
    return PathState(snapshot)


def _require_kind(state: PathState, path: Path, allowed: set[str], label: str) -> None:
    if state.kind not in allowed:
        choices = ", ".join(sorted(allowed))
        raise RuntimeError(
            f"{path}: {label} must be one of {choices}, found {state.kind}"
        )


def _directory_identity_matches(
    current: PathSnapshot | None, expected: PathSnapshot | None
) -> bool:
    if current is None or expected is None:
        return current is expected
    return (
        stat.S_ISDIR(current.mode)
        and stat.S_ISDIR(expected.mode)
        and current.device == expected.device
        and current.inode == expected.inode
        and current.mode == expected.mode
    )


def _logical_state_matches(current: PathState, expected: PathState) -> bool:
    if current.kind != expected.kind:
        return False
    if expected.kind == "missing":
        return True
    if expected.kind == "regular file":
        assert current.snapshot is not None and expected.snapshot is not None
        return (
            current.content == expected.content
            and stat.S_IMODE(current.snapshot.mode) == stat.S_IMODE(expected.snapshot.mode)
        )
    if expected.kind == "symbolic link":
        return current.link_target == expected.link_target
    return current.snapshot == expected.snapshot


class SyncPlan:
    """One preflight-only plan that can be combined before the first mutation."""

    def __init__(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        self.name = name
        self.guards: dict[Path, InputGuard] = {}
        self.directories: dict[Path, DirectoryAction] = {}
        self.actions: list[MutationAction] = []
        self.metadata: dict[str, Any] = dict(metadata or {})

    def add_guard(
        self, path: Path, expected: PathState | None = None, *, label: str = "input"
    ) -> PathState:
        absolute = _absolute(path)
        captured = capture_path_state(absolute) if expected is None else expected
        guard = InputGuard(absolute, captured, label)
        previous = self.guards.get(absolute)
        if previous is not None and previous.expected != captured:
            raise RuntimeError(f"{absolute}: conflicting transaction guards")
        self.guards[absolute] = guard
        return captured

    def add_file_guard(
        self, path: Path, expected: PathState | None = None, *, label: str = "source"
    ) -> PathState:
        absolute = _absolute(path)
        captured = capture_path_state(absolute) if expected is None else expected
        _require_kind(captured, absolute, {"regular file"}, label)
        return self.add_guard(absolute, captured, label=label)

    def add_directory(self, path: Path, *, mode: int = 0o700) -> None:
        absolute = _absolute(path)
        snapshot = lstat_snapshot(absolute)
        if snapshot is not None and not stat.S_ISDIR(snapshot.mode):
            raise RuntimeError(
                f"{absolute}: expected a directory or missing path, found {path_kind(snapshot)}"
            )
        requested = DirectoryAction(absolute, snapshot, stat.S_IMODE(mode))
        previous = self.directories.get(absolute)
        if previous is not None:
            if not _directory_identity_matches(requested.expected, previous.expected):
                raise RuntimeError(f"{absolute}: directory changed during preflight")
            if previous.expected is None and previous.mode != requested.mode:
                raise RuntimeError(f"{absolute}: conflicting creation modes")
            return
        if any(action.path == absolute for action in self.actions):
            raise RuntimeError(f"{absolute}: directory is also a transaction target")
        self.directories[absolute] = requested

    def add_directory_chain(self, path: Path, *, mode: int = 0o700) -> None:
        absolute = _absolute(path)
        current = Path(absolute.anchor)
        self.add_directory(current, mode=mode)
        for part in absolute.parts[1:]:
            current /= part
            self.add_directory(current, mode=mode)

    def _append_action(self, action: MutationAction) -> None:
        if action.path in self.directories:
            raise RuntimeError(f"{action.path}: target is also a transaction directory")
        if any(existing.path == action.path for existing in self.actions):
            raise RuntimeError(f"{action.path}: duplicate transaction target")
        if action.path in self.guards:
            raise RuntimeError(f"{action.path}: target is also an immutable transaction guard")
        self.actions.append(action)

    def add_write(
        self,
        path: Path,
        content: bytes,
        *,
        mode: int,
        before: PathState | None = None,
        label: str = "managed file",
    ) -> bool:
        absolute = _absolute(path)
        captured = capture_path_state(absolute) if before is None else before
        _require_kind(captured, absolute, {"missing", "regular file"}, label)
        desired_mode = stat.S_IMODE(mode)
        if (
            captured.kind == "regular file"
            and captured.content == content
            and captured.snapshot is not None
            and stat.S_IMODE(captured.snapshot.mode) == desired_mode
        ):
            self.add_guard(absolute, captured, label=f"unchanged {label}")
            return False
        self._append_action(
            MutationAction(
                absolute,
                captured,
                "regular file",
                content=bytes(content),
                mode=desired_mode,
                label=label,
            )
        )
        return True

    def add_symlink(
        self,
        path: Path,
        target: str,
        *,
        before: PathState | None = None,
        label: str = "managed link",
    ) -> bool:
        absolute = _absolute(path)
        captured = capture_path_state(absolute) if before is None else before
        _require_kind(captured, absolute, {"missing", "symbolic link"}, label)
        if captured.kind == "symbolic link" and captured.link_target == target:
            self.add_guard(absolute, captured, label=f"unchanged {label}")
            return False
        self._append_action(
            MutationAction(
                absolute,
                captured,
                "symbolic link",
                link_target=target,
                label=label,
            )
        )
        return True

    def add_delete(
        self,
        path: Path,
        *,
        before: PathState | None = None,
        allowed_kinds: frozenset[str] = frozenset({"regular file", "symbolic link"}),
        label: str = "managed deletion",
    ) -> bool:
        absolute = _absolute(path)
        captured = capture_path_state(absolute) if before is None else before
        if captured.kind == "missing":
            self.add_guard(absolute, captured, label=f"absent {label}")
            return False
        _require_kind(captured, absolute, set(allowed_kinds), label)
        self._append_action(
            MutationAction(absolute, captured, "missing", label=label)
        )
        return True

    def extend(self, other: "SyncPlan") -> None:
        existing_targets = {action.path for action in self.actions}
        incoming_targets = {action.path for action in other.actions}
        duplicates = sorted(existing_targets & incoming_targets, key=os.fspath)
        if duplicates:
            raise RuntimeError(
                "duplicate transaction target(s): "
                + ", ".join(os.fspath(path) for path in duplicates)
            )
        if incoming_targets & self.directories.keys() or existing_targets & other.directories.keys():
            raise RuntimeError("transaction target conflicts with a directory action")
        if incoming_targets & self.guards.keys() or existing_targets & other.guards.keys():
            raise RuntimeError("transaction target conflicts with an immutable guard")
        for path, guard in other.guards.items():
            current = self.guards.get(path)
            if current is not None and current.expected != guard.expected:
                raise RuntimeError(f"{path}: conflicting aggregate guard")
            self.guards[path] = guard
        for path, directory in other.directories.items():
            current = self.directories.get(path)
            if current is not None:
                if not _directory_identity_matches(
                    directory.expected, current.expected
                ):
                    raise RuntimeError(f"{path}: conflicting aggregate directory snapshot")
                if current.expected is None and current.mode != directory.mode:
                    raise RuntimeError(f"{path}: conflicting aggregate directory mode")
            else:
                self.directories[path] = directory
        self.actions.extend(other.actions)
        for key, value in other.metadata.items():
            if key in self.metadata and self.metadata[key] != value:
                raise RuntimeError(f"conflicting aggregate metadata field: {key}")
            self.metadata[key] = value


@dataclass
class _StagedAction:
    action: MutationAction
    path: Path
    state: PathState | None
    parent: "_DirectoryHandle"
    backup_path: Path | None = None


@dataclass
class _AppliedAction:
    action: MutationAction
    postimage: PathState
    staged: _StagedAction
    backup_state: PathState | None = None


@dataclass
class _CreatedDirectory:
    path: Path
    parent_path: Path
    name: str
    identity: PathSnapshot


@dataclass
class _DirectoryHandle:
    path: Path
    descriptor: int
    identity: PathSnapshot
    parent_path: Path | None
    name: str | None


def _snapshot(metadata: os.stat_result) -> PathSnapshot:
    return PathSnapshot(
        metadata.st_mode,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | _required_open_flag("O_DIRECTORY")
        | _required_open_flag("O_NOFOLLOW")
    )


def _entry_snapshot(descriptor: int, name: str) -> PathSnapshot | None:
    try:
        return _snapshot(os.stat(name, dir_fd=descriptor, follow_symlinks=False))
    except FileNotFoundError:
        return None


def _capture_entry(descriptor: int, name: str, display: Path) -> PathState:
    """Capture one entry relative to a held parent directory descriptor."""

    snapshot = _entry_snapshot(descriptor, name)
    if snapshot is None:
        return PathState(None)
    if stat.S_ISREG(snapshot.mode):
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | _required_open_flag("O_NOFOLLOW")
        )
        opened = os.open(name, flags, dir_fd=descriptor)
        try:
            opened_snapshot = _snapshot(os.fstat(opened))
            if opened_snapshot != snapshot or not stat.S_ISREG(opened_snapshot.mode):
                raise RuntimeError(f"{display}: changed while opened through its parent")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(opened, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            content = b"".join(chunks)
        finally:
            os.close(opened)
        if _entry_snapshot(descriptor, name) != snapshot:
            raise RuntimeError(f"{display}: changed while read through its parent")
        return PathState(snapshot, content=content)
    if stat.S_ISLNK(snapshot.mode):
        target = os.readlink(name, dir_fd=descriptor)
        if _entry_snapshot(descriptor, name) != snapshot:
            raise RuntimeError(f"{display}: symbolic link changed while read")
        return PathState(snapshot, link_target=target)
    return PathState(snapshot)


def _capture_bound_state(
    path: Path, handles: dict[Path, _DirectoryHandle]
) -> PathState:
    handle = handles.get(path)
    if handle is not None:
        return PathState(_snapshot(os.fstat(handle.descriptor)))
    parent = handles.get(path.parent)
    if parent is None:
        return capture_path_state(path)
    return _capture_entry(parent.descriptor, path.name, path)


def _validate_directory_handle(
    handle: _DirectoryHandle, handles: dict[Path, _DirectoryHandle]
) -> None:
    opened = _snapshot(os.fstat(handle.descriptor))
    if not _directory_identity_matches(opened, handle.identity):
        raise RuntimeError(f"{handle.path}: held directory changed after it was opened")
    if handle.parent_path is None:
        return
    parent = handles.get(handle.parent_path)
    if parent is None or handle.name is None:
        raise RuntimeError(f"{handle.path}: held directory has no anchored parent")
    current = _entry_snapshot(parent.descriptor, handle.name)
    if not _directory_identity_matches(current, handle.identity):
        raise RuntimeError(f"{handle.path}: directory binding changed after preflight")


def _validate_directory_handles(handles: dict[Path, _DirectoryHandle]) -> None:
    for path in sorted(handles, key=lambda item: len(item.parts)):
        _validate_directory_handle(handles[path], handles)


def _fsync_handle(handle: _DirectoryHandle) -> None:
    os.fsync(handle.descriptor)


def _validate_guard(
    guard: InputGuard, handles: dict[Path, _DirectoryHandle] | None = None
) -> None:
    current = (
        capture_path_state(guard.path)
        if handles is None
        else _capture_bound_state(guard.path, handles)
    )
    if current != guard.expected:
        raise RuntimeError(f"{guard.path}: {guard.label} changed after preflight")


def _validate_directory(
    action: DirectoryAction, handles: dict[Path, _DirectoryHandle]
) -> None:
    handle = handles.get(action.path)
    if handle is not None:
        _validate_directory_handle(handle, handles)
        if action.expected is not None and not _directory_identity_matches(
            handle.identity, action.expected
        ):
            raise RuntimeError(f"{action.path}: opened a different preflight directory")
        return
    current = lstat_snapshot(action.path)
    expected = action.expected
    if not _directory_identity_matches(current, expected):
        raise RuntimeError(f"{action.path}: directory changed after preflight")


def _validate_action(
    action: MutationAction, handles: dict[Path, _DirectoryHandle] | None = None
) -> None:
    current = (
        capture_path_state(action.path)
        if handles is None
        else _capture_bound_state(action.path, handles)
    )
    if current != action.before:
        raise RuntimeError(f"{action.path}: {action.label} changed after preflight")


def _validate_all(
    plan: SyncPlan, handles: dict[Path, _DirectoryHandle] | None = None
) -> None:
    for guard in plan.guards.values():
        _validate_guard(guard, handles)
    for directory in plan.directories.values():
        if handles is None:
            current = lstat_snapshot(directory.path)
            if not _directory_identity_matches(current, directory.expected):
                raise RuntimeError(f"{directory.path}: directory changed after preflight")
        else:
            _validate_directory(directory, handles)
    for action in plan.actions:
        _validate_action(action, handles)


def _validate_plan_structure(plan: SyncPlan) -> None:
    if plan.actions and _RENAMEAT2 is None:
        raise RuntimeError("platform lacks required atomic renameat2 support")
    if plan.actions:
        _required_open_flag("O_DIRECTORY")
        _required_open_flag("O_NOFOLLOW")
    for directory in plan.directories.values():
        anchor = Path(directory.path.anchor)
        if directory.path != anchor and directory.path.parent not in plan.directories:
            raise RuntimeError(
                f"{directory.path}: directory plan has no anchored parent"
            )
    for guard in plan.guards.values():
        if (
            guard.expected.kind != "missing"
            and guard.path not in plan.directories
            and guard.path.parent not in plan.directories
        ):
            raise RuntimeError(f"{guard.path}: input guard has no anchored parent")
    seen: set[Path] = set()
    for action in plan.actions:
        if action.path in seen:
            raise RuntimeError(f"{action.path}: duplicate transaction target")
        seen.add(action.path)
        if action.path.parent not in plan.directories:
            raise RuntimeError(
                f"{action.path}: target parent has no no-follow directory plan"
            )
        if action.desired_kind == "regular file" and (
            action.content is None or action.mode is None
        ):
            raise RuntimeError(f"{action.path}: regular-file action is incomplete")
        if action.desired_kind == "symbolic link" and action.link_target is None:
            raise RuntimeError(f"{action.path}: symlink action is incomplete")
        if action.desired_kind not in {"regular file", "symbolic link", "missing"}:
            raise RuntimeError(
                f"{action.path}: unsupported desired state {action.desired_kind!r}"
            )


def _validate_runtime_context(
    plan: SyncPlan, handles: dict[Path, _DirectoryHandle]
) -> None:
    _validate_directory_handles(handles)
    for guard in plan.guards.values():
        _validate_guard(guard, handles)
    for directory in plan.directories.values():
        _validate_directory(directory, handles)


def _open_directory(
    action: DirectoryAction,
    handles: dict[Path, _DirectoryHandle],
) -> _DirectoryHandle:
    anchor = Path(action.path.anchor)
    if action.path == anchor:
        descriptor = os.open(action.path, _directory_flags())
        parent_path = None
        name = None
    else:
        parent = handles.get(action.path.parent)
        if parent is None:
            raise RuntimeError(f"{action.path}: directory parent is not anchored")
        descriptor = os.open(action.path.name, _directory_flags(), dir_fd=parent.descriptor)
        parent_path = parent.path
        name = action.path.name
    identity = _snapshot(os.fstat(descriptor))
    if action.expected is not None and not _directory_identity_matches(
        identity, action.expected
    ):
        os.close(descriptor)
        raise RuntimeError(f"{action.path}: directory changed while it was opened")
    handle = _DirectoryHandle(action.path, descriptor, identity, parent_path, name)
    handles[action.path] = handle
    _validate_directory_handle(handle, handles)
    return handle


def _prepare_directories(
    plan: SyncPlan,
    handles: dict[Path, _DirectoryHandle],
    created: list[_CreatedDirectory],
) -> None:
    ordered = sorted(plan.directories.values(), key=lambda item: len(item.path.parts))
    for action in ordered:
        _validate_directory_handles(handles)
        if action.expected is not None:
            _open_directory(action, handles)
            continue
        _validate_all(plan, handles)
        parent = handles.get(action.path.parent)
        if parent is None:
            raise RuntimeError(f"{action.path}: missing directory parent is not anchored")
        _validate_directory_handle(parent, handles)
        try:
            os.mkdir(action.path.name, action.mode, dir_fd=parent.descriptor)
        except FileExistsError as error:
            raise RuntimeError(f"{action.path}: appeared after directory preflight") from error
        snapshot = _entry_snapshot(parent.descriptor, action.path.name)
        if snapshot is not None:
            record = _CreatedDirectory(
                action.path, parent.path, action.path.name, snapshot
            )
            created.append(record)
        if snapshot is None or not stat.S_ISDIR(snapshot.mode):
            raise RuntimeError(f"{action.path}: directory creation did not publish a directory")
        created_action = DirectoryAction(action.path, snapshot, action.mode)
        handle = _open_directory(created_action, handles)
        os.fchmod(handle.descriptor, action.mode)
        snapshot = _snapshot(os.fstat(handle.descriptor))
        current = _entry_snapshot(parent.descriptor, action.path.name)
        if not _directory_identity_matches(current, snapshot):
            raise RuntimeError(
                f"{action.path}: created directory changed during mode publication"
            )
        handle.identity = snapshot
        record.identity = snapshot
        _fsync_handle(handle)
        _fsync_handle(parent)


def _temporary_name(descriptor: int, prefix: str) -> str:
    stem = prefix[:120]
    for _ in range(128):
        name = f"{stem}{secrets.token_hex(12)}"
        if _entry_snapshot(descriptor, name) is None:
            return name
    raise RuntimeError("unable to allocate a transaction-private name")


def _stage_regular(action: MutationAction, parent: _DirectoryHandle) -> _StagedAction:
    assert action.content is not None and action.mode is not None
    name = _temporary_name(parent.descriptor, f".{action.path.name}.ga-stage-")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | _required_open_flag("O_NOFOLLOW")
    )
    descriptor = os.open(name, flags, 0o600, dir_fd=parent.descriptor)
    stage = action.path.parent / name
    opened = _snapshot(os.fstat(descriptor))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(action.content)
            handle.flush()
            os.fchmod(handle.fileno(), action.mode)
            os.fsync(handle.fileno())
        state = _capture_entry(parent.descriptor, name, stage)
        if (
            state.snapshot is None
            or state.snapshot.device != opened.device
            or state.snapshot.inode != opened.inode
        ):
            raise RuntimeError(f"{stage}: staged file identity changed while it was written")
        return _StagedAction(action, stage, state, parent)
    except BaseException as error:
        if descriptor >= 0:
            os.close(descriptor)
        current = _entry_snapshot(parent.descriptor, name)
        if (
            current is not None
            and stat.S_ISREG(current.mode)
            and current.device == opened.device
            and current.inode == opened.inode
        ):
            os.unlink(name, dir_fd=parent.descriptor)
            _fsync_handle(parent)
        elif current is not None:
            raise RuntimeError(
                f"{stage}: staged path changed concurrently and was preserved: {error}"
            ) from error
        raise


def _stage_action(action: MutationAction, parent: _DirectoryHandle) -> _StagedAction:
    if action.desired_kind == "regular file":
        staged = _stage_regular(action, parent)
        if action.before.kind != "missing":
            name = _temporary_name(
                parent.descriptor, f".{action.path.name}.ga-backup-"
            )
            staged.backup_path = action.path.parent / name
        return staged
    if action.desired_kind == "symbolic link":
        assert action.link_target is not None
        name = _temporary_name(parent.descriptor, f".{action.path.name}.ga-stage-")
        stage = action.path.parent / name
        os.symlink(action.link_target, name, dir_fd=parent.descriptor)
        created = _entry_snapshot(parent.descriptor, name)
        try:
            state = _capture_entry(parent.descriptor, name, stage)
            if state.kind != "symbolic link" or state.link_target != action.link_target:
                raise RuntimeError(f"{stage}: staged symlink identity changed")
        except BaseException:
            current = _entry_snapshot(parent.descriptor, name)
            if (
                current == created
                and current is not None
                and stat.S_ISLNK(current.mode)
                and os.readlink(name, dir_fd=parent.descriptor) == action.link_target
            ):
                os.unlink(name, dir_fd=parent.descriptor)
                _fsync_handle(parent)
            raise
        backup_path = None
        if action.before.kind != "missing":
            backup_name = _temporary_name(
                parent.descriptor, f".{action.path.name}.ga-backup-"
            )
            backup_path = action.path.parent / backup_name
        return _StagedAction(action, stage, state, parent, backup_path)
    if action.desired_kind == "missing":
        name = _temporary_name(parent.descriptor, f".{action.path.name}.ga-backup-")
        return _StagedAction(action, action.path.parent / name, None, parent)
    raise RuntimeError(f"unsupported desired state: {action.desired_kind}")


def _desired_matches(action: MutationAction, current: PathState) -> bool:
    if action.desired_kind == "missing":
        return current.kind == "missing"
    if action.desired_kind == "regular file":
        return (
            current.kind == "regular file"
            and current.content == action.content
            and current.snapshot is not None
            and action.mode is not None
            and stat.S_IMODE(current.snapshot.mode) == action.mode
        )
    return current.kind == "symbolic link" and current.link_target == action.link_target


_LIBC = ctypes.CDLL(None, use_errno=True)
_RENAMEAT2 = getattr(_LIBC, "renameat2", None)
_RENAME_NOREPLACE = 1
if _RENAMEAT2 is not None:
    _RENAMEAT2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    _RENAMEAT2.restype = ctypes.c_int


def _renameat2(
    source_descriptor: int,
    source_name: str,
    target_descriptor: int,
    target_name: str,
    flags: int,
) -> None:
    if _RENAMEAT2 is None:
        raise RuntimeError("platform lacks required atomic renameat2 support")
    ctypes.set_errno(0)
    result = _RENAMEAT2(
        source_descriptor,
        os.fsencode(source_name),
        target_descriptor,
        os.fsencode(target_name),
        flags,
    )
    if result != 0:
        number = ctypes.get_errno()
        raise OSError(
            number,
            os.strerror(number),
            f"{source_name} -> {target_name}",
        )


def _rename_noreplace(parent: _DirectoryHandle, source: str, target: str) -> None:
    _renameat2(
        parent.descriptor,
        source,
        parent.descriptor,
        target,
        _RENAME_NOREPLACE,
    )


def _restore_moved_entry(
    parent: _DirectoryHandle,
    moved_path: Path,
    target_path: Path,
    displaced: PathState,
) -> bool:
    if _capture_entry(parent.descriptor, target_path.name, target_path).kind != "missing":
        return False
    if _capture_entry(parent.descriptor, moved_path.name, moved_path) != displaced:
        return False
    try:
        _rename_noreplace(parent, moved_path.name, target_path.name)
    except OSError as error:
        if error.errno == errno.EEXIST:
            return False
        raise
    _fsync_handle(parent)
    return (
        _capture_entry(parent.descriptor, target_path.name, target_path) == displaced
        and _capture_entry(parent.descriptor, moved_path.name, moved_path).kind == "missing"
    )


def _publish_action(action: MutationAction, staged: _StagedAction) -> _AppliedAction:
    """Publish through a held parent FD using atomic compare-preserving renames."""

    parent = staged.parent
    if not _directory_identity_matches(
        _snapshot(os.fstat(parent.descriptor)), parent.identity
    ):
        raise RuntimeError(f"{parent.path}: held target directory changed before publication")
    current = _capture_entry(parent.descriptor, action.path.name, action.path)
    if current != action.before:
        raise RuntimeError(f"{action.path}: changed immediately before publication")
    _validate_staged(staged)

    if action.desired_kind == "missing":
        _rename_noreplace(parent, action.path.name, staged.path.name)
        _fsync_handle(parent)
        backup = _capture_entry(parent.descriptor, staged.path.name, staged.path)
        postimage = _capture_entry(parent.descriptor, action.path.name, action.path)
        if backup != action.before or postimage.kind != "missing":
            if _restore_moved_entry(parent, staged.path, action.path, backup):
                raise RuntimeError(
                    f"{action.path}: concurrent replacement detected during atomic deletion"
                )
            raise RuntimeError(
                f"{action.path}: deletion raced with concurrent changes; backup was preserved"
            )
        return _AppliedAction(action, PathState(None), staged, backup)

    assert staged.state is not None
    if action.before.kind == "missing":
        _rename_noreplace(parent, staged.path.name, action.path.name)
        _fsync_handle(parent)
        postimage = _capture_entry(parent.descriptor, action.path.name, action.path)
        if postimage != staged.state:
            raise RuntimeError(f"{action.path}: published postimage changed immediately")
        return _AppliedAction(action, staged.state, staged)

    if staged.backup_path is None:
        raise RuntimeError(f"{action.path}: replacement has no recoverable preimage path")
    _rename_noreplace(parent, action.path.name, staged.backup_path.name)
    _fsync_handle(parent)
    backup = _capture_entry(
        parent.descriptor, staged.backup_path.name, staged.backup_path
    )
    if backup != action.before:
        if _restore_moved_entry(parent, staged.backup_path, action.path, backup):
            raise RuntimeError(
                f"{action.path}: concurrent replacement detected during atomic publication"
            )
        raise RuntimeError(
            f"{action.path}: publication raced with concurrent changes; displaced entry retained"
        )
    _rename_noreplace(parent, staged.path.name, action.path.name)
    _fsync_handle(parent)
    postimage = _capture_entry(parent.descriptor, action.path.name, action.path)
    if postimage != staged.state:
        raise RuntimeError(f"{action.path}: published postimage changed immediately")
    return _AppliedAction(action, staged.state, staged, backup)


def _validate_staged(staged: _StagedAction) -> None:
    current = _capture_entry(
        staged.parent.descriptor, staged.path.name, staged.path
    )
    if staged.backup_path is not None:
        backup = _capture_entry(
            staged.parent.descriptor,
            staged.backup_path.name,
            staged.backup_path,
        )
        if backup.kind != "missing":
            raise RuntimeError(
                f"{staged.backup_path}: recoverable preimage path appeared before publish"
            )
    if staged.action.desired_kind == "missing":
        if current.kind != "missing":
            raise RuntimeError(f"{staged.path}: deletion backup path appeared before publish")
        return
    if staged.state is None or current != staged.state:
        raise RuntimeError(f"{staged.path}: staged output changed before publish")


def _validate_postimages(applied: list[_AppliedAction]) -> None:
    for published in applied:
        current = _capture_entry(
            published.staged.parent.descriptor,
            published.action.path.name,
            published.action.path,
        )
        if current != published.postimage:
            raise RuntimeError(
                f"{published.action.path}: published target changed before transaction completion"
            )


def _detect_applied(action: MutationAction, staged: _StagedAction) -> _AppliedAction | None:
    parent = staged.parent
    target = _capture_entry(parent.descriptor, action.path.name, action.path)
    temporary = _capture_entry(parent.descriptor, staged.path.name, staged.path)
    if action.before.kind == "missing":
        if staged.state is not None and temporary.kind == "missing":
            return _AppliedAction(action, staged.state, staged)
        return None
    backup_path = (
        staged.path if action.desired_kind == "missing" else staged.backup_path
    )
    backup = (
        PathState(None)
        if backup_path is None
        else _capture_entry(parent.descriptor, backup_path.name, backup_path)
    )
    if backup == action.before:
        postimage = (
            PathState(None)
            if action.desired_kind == "missing" or temporary == staged.state
            else staged.state
        )
        assert postimage is not None
        return _AppliedAction(action, postimage, staged, backup)
    if _desired_matches(action, target) and action.desired_kind == "missing":
        return _AppliedAction(action, PathState(None), staged, temporary)
    return None


def _cleanup_owned_path(
    staged: _StagedAction, expected: PathState, issues: list[str]
) -> None:
    path = staged.path
    parent = staged.parent
    try:
        current = _capture_entry(parent.descriptor, path.name, path)
        if current.kind == "missing":
            return
        if current != expected:
            issues.append(f"{path}: temporary path changed concurrently; preserved")
            return
        os.unlink(path.name, dir_fd=parent.descriptor)
        _fsync_handle(parent)
    except BaseException as error:
        issues.append(f"{path}: temporary cleanup failed: {error}")


def _restore_action(applied: _AppliedAction, issues: list[str]) -> bool:
    action = applied.action
    staged = applied.staged
    parent = staged.parent
    try:
        current = _capture_entry(parent.descriptor, action.path.name, action.path)
        if current != applied.postimage:
            issues.append(
                f"{action.path}: rollback incomplete because the published target changed; "
                "concurrent bytes were preserved"
            )
            return False
        if action.before.kind == "missing":
            temporary = _capture_entry(parent.descriptor, staged.path.name, staged.path)
            if temporary.kind != "missing":
                raise RuntimeError("rollback temporary path is no longer empty")
            _rename_noreplace(parent, action.path.name, staged.path.name)
            _fsync_handle(parent)
            moved = _capture_entry(parent.descriptor, staged.path.name, staged.path)
            if moved != applied.postimage:
                if _restore_moved_entry(parent, staged.path, action.path, moved):
                    issues.append(
                        f"{action.path}: rollback saw a concurrent replacement and preserved it"
                    )
                else:
                    issues.append(
                        f"{staged.path}: concurrent replacement retained during rollback"
                    )
                return False
            _cleanup_owned_path(staged, moved, issues)
            return True

        backup_path = (
            staged.path if action.desired_kind == "missing" else staged.backup_path
        )
        if backup_path is None:
            raise RuntimeError("recoverable preimage path is unavailable")
        backup_state = _capture_entry(parent.descriptor, backup_path.name, backup_path)
        if applied.backup_state is None or backup_state != applied.backup_state:
            raise RuntimeError("recoverable preimage changed before rollback")
        if action.desired_kind == "missing":
            _rename_noreplace(parent, backup_path.name, action.path.name)
            _fsync_handle(parent)
            restored = _capture_entry(parent.descriptor, action.path.name, action.path)
            if restored != action.before:
                raise RuntimeError("restored deletion preimage does not match")
            return True

        if applied.postimage.kind == "missing":
            _rename_noreplace(parent, backup_path.name, action.path.name)
            _fsync_handle(parent)
            restored = _capture_entry(parent.descriptor, action.path.name, action.path)
            if restored != action.before:
                raise RuntimeError("restored partial-publication preimage does not match")
            return True

        rollback_name = _temporary_name(
            parent.descriptor, f".{action.path.name}.ga-rollback-"
        )
        rollback_path = action.path.parent / rollback_name
        _rename_noreplace(parent, action.path.name, rollback_name)
        _fsync_handle(parent)
        displaced = _capture_entry(parent.descriptor, rollback_name, rollback_path)
        if displaced != applied.postimage:
            if _restore_moved_entry(parent, rollback_path, action.path, displaced):
                issues.append(
                    f"{action.path}: rollback saw a concurrent replacement and preserved it"
                )
            else:
                issues.append(
                    f"{rollback_path}: concurrent replacement retained during rollback"
                )
            return False
        try:
            _rename_noreplace(parent, backup_path.name, action.path.name)
        except BaseException:
            rollback_staged = _StagedAction(
                action, rollback_path, displaced, parent
            )
            _cleanup_owned_path(rollback_staged, displaced, issues)
            raise
        _fsync_handle(parent)
        restored = _capture_entry(parent.descriptor, action.path.name, action.path)
        if restored != action.before:
            raise RuntimeError("rollback did not restore the exact preimage")
        rollback_staged = _StagedAction(action, rollback_path, displaced, parent)
        _cleanup_owned_path(rollback_staged, displaced, issues)
        return True
    except BaseException as error:
        issues.append(f"{action.path}: rollback failed: {error}")
        return False


def _rollback_directories(
    created: list[_CreatedDirectory],
    handles: dict[Path, _DirectoryHandle],
    issues: list[str],
) -> None:
    for directory in reversed(created):
        try:
            parent = handles.get(directory.parent_path)
            if parent is None:
                issues.append(f"{directory.path}: created directory parent is unavailable")
                continue
            current = _entry_snapshot(parent.descriptor, directory.name)
            if not _directory_identity_matches(current, directory.identity):
                issues.append(
                    f"{directory.path}: created directory changed concurrently; preserved"
                )
                continue
            tombstone_name = _temporary_name(
                parent.descriptor, f".{directory.name}.ga-rmdir-"
            )
            tombstone = directory.path.parent / tombstone_name
            _rename_noreplace(parent, directory.name, tombstone_name)
            _fsync_handle(parent)
            moved = _entry_snapshot(parent.descriptor, tombstone_name)
            if not _directory_identity_matches(moved, directory.identity):
                moved_state = PathState(moved)
                if not _restore_moved_entry(
                    parent, tombstone, directory.path, moved_state
                ):
                    issues.append(
                        f"{tombstone}: substituted directory retained during rollback"
                    )
                else:
                    issues.append(
                        f"{directory.path}: concurrent directory replacement was preserved"
                    )
                continue
            handle = handles.get(directory.path)
            if handle is None:
                descriptor = os.open(
                    tombstone_name,
                    _directory_flags(),
                    dir_fd=parent.descriptor,
                )
            else:
                descriptor = handle.descriptor
            if os.listdir(descriptor):
                state = PathState(moved)
                if not _restore_moved_entry(
                    parent, tombstone, directory.path, state
                ):
                    issues.append(
                        f"{tombstone}: created directory is not empty and was retained"
                    )
                else:
                    issues.append(
                        f"{directory.path}: created directory is not empty; preserved"
                    )
                if handle is None:
                    os.close(descriptor)
                continue
            if handle is None:
                os.close(descriptor)
            if not _directory_identity_matches(
                _entry_snapshot(parent.descriptor, tombstone_name),
                directory.identity,
            ):
                issues.append(
                    f"{tombstone}: created directory changed before final removal; preserved"
                )
                continue
            os.rmdir(tombstone_name, dir_fd=parent.descriptor)
            _fsync_handle(parent)
        except BaseException as error:
            issues.append(f"{directory.path}: directory rollback failed: {error}")


def _close_directory_handles(handles: dict[Path, _DirectoryHandle]) -> None:
    for path in sorted(handles, key=lambda item: len(item.parts), reverse=True):
        try:
            os.close(handles[path].descriptor)
        except OSError:
            pass


def commit_plan(plan: SyncPlan) -> SyncResult:
    """Commit a combined plan with process-lifetime conditional rollback.

    This is deliberately not a durable recovery journal. Abrupt process death, power loss, and
    filesystem failure can interrupt it outside Python's exception-driven rollback boundary. It
    is also not isolation: move-aside publication is briefly observable, and final cleanup cannot
    defend a private random name from a deliberately racing same-UID process.
    """

    try:
        _validate_plan_structure(plan)
        _validate_all(plan)
    except BaseException as error:
        raise SyncTransactionError(
            f"sync transaction preflight failed before mutation: {error}"
        ) from error

    handles: dict[Path, _DirectoryHandle] = {}
    created: list[_CreatedDirectory] = []
    staged_actions: list[_StagedAction] = []
    applied: list[_AppliedAction] = []
    try:
        _prepare_directories(plan, handles, created)
        _validate_all(plan, handles)

        for action in plan.actions:
            _validate_runtime_context(plan, handles)
            _validate_action(action, handles)
            parent = handles[action.path.parent]
            staged_actions.append(_stage_action(action, parent))

        for action, staged in zip(plan.actions, staged_actions, strict=True):
            _validate_runtime_context(plan, handles)
            _validate_postimages(applied)
            _validate_action(action, handles)
            _validate_staged(staged)
            try:
                published = _publish_action(action, staged)
            except BaseException:
                detected = _detect_applied(action, staged)
                if detected is not None:
                    applied.append(detected)
                raise
            applied.append(published)
            _validate_runtime_context(plan, handles)
            _validate_postimages(applied)

        _validate_runtime_context(plan, handles)
        _validate_postimages(applied)
    except BaseException as error:
        issues: list[str] = []
        retained_backups: set[Path] = set()
        for published in reversed(applied):
            restored = _restore_action(published, issues)
            if not restored and published.backup_state is not None:
                backup = (
                    published.staged.path
                    if published.action.desired_kind == "missing"
                    else published.staged.backup_path
                )
                if backup is None:
                    issues.append(
                        f"{published.action.path}: recoverable transaction preimage path is missing"
                    )
                    continue
                if _capture_entry(
                    published.staged.parent.descriptor, backup.name, backup
                ).kind != "missing":
                    retained_backups.add(backup)
                    issues.append(
                        f"{backup}: recoverable transaction preimage retained"
                    )
        for staged in reversed(staged_actions):
            if staged.path in retained_backups:
                continue
            state = _capture_entry(staged.parent.descriptor, staged.path.name, staged.path)
            if state.kind == "missing":
                continue
            expected = staged.state
            if expected is not None:
                _cleanup_owned_path(staged, expected, issues)
            backup = staged.backup_path
            if backup is not None and backup not in retained_backups:
                backup_state = _capture_entry(
                    staged.parent.descriptor, backup.name, backup
                )
                if backup_state.kind != "missing":
                    issues.append(
                        f"{backup}: unexpected recoverable entry retained"
                    )
        _rollback_directories(created, handles, issues)
        if issues:
            detail = "; ".join(issues)
            _close_directory_handles(handles)
            raise SyncTransactionError(
                f"sync transaction failed; rollback incomplete: {error}; {detail}",
                rollback_incomplete=True,
            ) from error
        _close_directory_handles(handles)
        raise SyncTransactionError(
            f"sync transaction failed and was rolled back: {error}"
        ) from error

    retained_cleanup: list[str] = []
    for published in applied:
        if published.backup_state is None:
            continue
        backup_path = (
            published.staged.path
            if published.action.desired_kind == "missing"
            else published.staged.backup_path
        )
        if backup_path is None:
            retained_cleanup.append(
                f"{published.action.path}: recoverable cleanup path is missing"
            )
            continue
        cleanup_issues: list[str] = []
        cleanup_staged = _StagedAction(
            published.action,
            backup_path,
            published.backup_state,
            published.staged.parent,
        )
        _cleanup_owned_path(cleanup_staged, published.backup_state, cleanup_issues)
        retained_cleanup.extend(cleanup_issues)
    metadata = dict(plan.metadata)
    if retained_cleanup:
        metadata["transaction_cleanup_retained"] = tuple(retained_cleanup)
    _close_directory_handles(handles)
    return SyncResult(tuple(action.path for action in plan.actions), metadata)
