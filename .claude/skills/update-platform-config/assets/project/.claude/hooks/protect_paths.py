#!/usr/bin/env python3
"""protect_paths.py — Claude Code port of the .omp protect-paths gate (V2 native runtime).

Native workflow-node agents (and any Claude Code session in this repo) don't load .omp extensions;
this hook gives them the SAME protection. Wiring (.claude/settings.json):
  PreToolUse  matcher "Write|Edit|MultiEdit|NotebookEdit|Bash" -> `python3 .claude/hooks/protect_paths.py pre`
  PostToolUse matcher "Bash"                                    -> `python3 .claude/hooks/protect_paths.py post`
Contract: JSON on stdin ({tool_name, tool_input, session_id, ...}); exit 2 blocks (stderr -> model).

Semantics mirror .omp/hooks/pre/protect-paths.ts exactly:
- WRITE-family path block on authoritative proof dirs / control-plane (READ tools untouched —
  Claude Code's Read/Grep/Glob don't match the matcher at all).
- Bash literal-mention block, EXCEPT strict read/execute-only shapes (no metachars/newlines or env
  prefixes; allowlisted programs include built-in validation and project-management inspection).
- Built-in start/finalize-only require exact argv plus a literal tier-1 prefix.
- Scoped ack: GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 (leading assignment, literal truthy value)
  lifts the literal block and proof audit for one deliberate reviewer/workflow command.
- Post-bash snapshot/restore audit for authoritative proof dirs (state-based and
  obfuscation-resistant), stashed per-session under /tmp between pre/post. General control-plane
  source edits require acknowledgement where this hook can observe them, but are not silently
  restored after a native workflow node.
"""
import json
import os
import re
import secrets
import shlex
import stat
import sys

PROOF_DIRS = [".pm/verdicts", ".pm/reviews", ".pm/proofs", ".pm/workflow-reviews"]
CONTROL_PLANE_FILES = [
    ".claude/hooks/protect_paths.py",
    ".claude/skills/run-workflow/scripts/select_harness.py",
    ".workflow/harnesses.json",
    "config/workflows/models.json",
    ".claude/skills/subagent-workflow/scripts/native/native-map.json",
    ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py",
    ".claude/skills/subagent-workflow/scripts/native/run.ts",
    ".claude/skills/subagent-workflow/scripts/controller/run.ts",
    ".claude/skills/subagent-workflow/scripts/controller/state_bound_proof.ts",
    ".claude/skills/pm-dispatch/scripts/pm.py",
    "constraint.md",
]
FRAMEWORK_PROTECTED = (
    [
        ".pm/reviews/",
        ".pm/verdicts/",
        ".pm/proofs/",
        ".pm/workflow-reviews/",
        ".omp/hooks/",
        ".workflow/designs/",
        ".subagent/flows/",
        ".claude/skills/subagent-workflow/scripts/controller/actions/",
    ]
    + CONTROL_PLANE_FILES
)


class ProjectProtectionConfigError(RuntimeError):
    """A configured project protection layer exists but is unsafe to consume."""


class UnsafeProofPath(RuntimeError):
    """An authoritative proof tree contains a symlink or other unsafe path component."""


class UnsafeGateStateError(RuntimeError):
    """The private pre/post hand-off location or file is missing an integrity guarantee."""


def protected_list(cwd: str) -> list:
    """Framework list + the project layer (.omp/hooks/protected-paths.json) — same as the TS gate."""
    out = list(FRAMEWORK_PROTECTED)
    config = os.path.join(cwd, ".omp", "hooks", "protected-paths.json")
    try:
        stat_result = os.lstat(config)
    except FileNotFoundError:
        return out  # cold start: default-mode has not materialized the optional project layer yet
    except OSError as error:
        raise ProjectProtectionConfigError(f"cannot inspect project protection config: {error}") from error
    try:
        if os.path.islink(config) or not os.path.isfile(config):
            raise ProjectProtectionConfigError("config must be one ordinary file")
        with open(config, encoding="utf-8") as handle:
            extra = json.load(handle)
    except ProjectProtectionConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProjectProtectionConfigError(f"malformed project protection config: {error}") from error
    if not isinstance(extra, list):
        raise ProjectProtectionConfigError("project protection config must be a JSON array")
    for index, value in enumerate(extra):
        if not isinstance(value, str) or not value.strip() or "\0" in value or "\n" in value or "\r" in value:
            raise ProjectProtectionConfigError(
                f"project protection config entry {index} must be one non-empty string"
            )
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or any(part == ".." for part in normalized.split("/")):
            raise ProjectProtectionConfigError(
                f"project protection config entry {index} must stay inside the repository"
            )
        if normalized in (".", "./") or any(char in normalized for char in "*?["):
            raise ProjectProtectionConfigError(
                f"project protection config entry {index} must be one compiled non-root prefix"
            )
    out += extra
    return out
ACK = "GENERAL_AGENT_ALLOW_PROTECTED_WRITE"
RO_METACHAR = re.compile(r"[;&|<>`$()#\n\r\0]")
RO_CMD = re.compile(
    r"^\s*(?:python3?\s+(?:\./)?\.claude/skills/pm-dispatch/scripts/pm\.py"
    r"|cat|grep|ls|head|tail|wc|diff|file|stat)(?:\s|$)")
ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(?:'([^']*)'|\"([^\"]*)\"|([^'\"\s;|&]*))\s+")


def leading_assignments(cmd: str):
    out = {}
    rest = re.sub(r"^env\s+", "", cmd.lstrip())
    while True:
        m = ASSIGN.match(rest)
        if not m:
            break
        out[m.group(1)] = next(g for g in m.groups()[1:] if g is not None)
        rest = rest[m.end():]
    return out


def truthy_literal(v):
    if v is None or "$" in v or "`" in v:
        return False
    return v.lower() not in ("", "0", "false", "no")


def project_config_repair_shape(command: str) -> bool:
    """Only the canonical atomic compiler may repair a malformed configured project layer."""
    if RO_METACHAR.search(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return words in (
        [ACK + "=1", "python3", ".claude/skills/constraints-to-hooks/scripts/constraint_gate.py", "install-protected-paths"],
        [ACK + "=1", "python", ".claude/skills/constraints-to-hooks/scripts/constraint_gate.py", "install-protected-paths"],
    )


def canon(p: str) -> str:
    parts = []
    for seg in p.replace("\\", "/").split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            parts and parts.pop()
            continue
        parts.append(seg)
    return ("/" if p.startswith("/") else "") + "/".join(parts)


def hits_list(candidate: str, prot: list):
    c = canon(candidate)
    for entry in prot:
        e = canon(entry)
        if c == e or c.startswith(e + "/"):
            return entry
        if "/" in e and (("/" + e + "/") in c or c.endswith("/" + e)):
            return entry
    return None


def _canon_cmd(c: str) -> str:
    """Collapse /./  //  and /seg/../ to a FIXPOINT so repeated-dot / .. evasion of a protected
    path (.omp/././hooks, .omp/foo/../hooks) still matches the literal scan."""
    prev = None
    while prev != c:
        prev = c
        c = c.replace("/./", "/")
        c = re.sub(r"/{2,}", "/", c)
        c = re.sub(r"/[^/]+/\.\./", "/", c)
    return c


SAFE_MAPPING = re.compile(
    r"^(?:\./)?\.subagent/flows/([A-Za-z0-9][A-Za-z0-9_-]*)\.json$")
SAFE_RESUME = re.compile(
    r"^workflow-output/subagent/[A-Za-z0-9][A-Za-z0-9_-]*/"
    r"[A-Za-z0-9][A-Za-z0-9_-]*$")
SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_input_path(value: str) -> bool:
    """Accept one plain repo-relative input path, never an option, traversal, or shell spelling."""
    if not value or value.startswith(("/", "-")) or "\\" in value:
        return False
    if value.startswith("./"):
        value = value[2:]
    parts = value.split("/")
    return bool(parts) and all(
        part not in ("", ".", "..") and SAFE_PATH_SEGMENT.fullmatch(part)
        for part in parts
    )


def parse_subagent_runner_shape(cmd: str):
    """Return the exact built-in action, or None for malformed/unauthorized argv.

    Validate is read-only and unprefixed. Start/finalize-only may create protected state-bound
    proof and therefore require exactly `GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1` as the sole leading
    assignment. The controller owns its output root, and resume is confined to its canonical tree.
    """
    if RO_METACHAR.search(cmd):
        return None
    words = cmd.strip().split()
    authorized = False
    if words and words[0] == f"{ACK}=1":
        authorized = True
        words = words[1:]
    if len(words) < 4 or words[0] != "bun":
        return None
    if words[1] not in (
            ".claude/skills/subagent-workflow/scripts/controller/run.ts",
            "./.claude/skills/subagent-workflow/scripts/controller/run.ts"):
        return None
    action, mapping = words[2], words[3]
    if action not in ("validate", "start", "finalize-only") or not SAFE_MAPPING.fullmatch(mapping):
        return None
    if action == "validate":
        return action if len(words) == 4 and not authorized else None
    if not authorized:
        return None
    if action == "finalize-only":
        return action if (
            len(words) == 6 and words[4] == "--resume"
            and SAFE_RESUME.fullmatch(words[5])
        ) else None

    seen = set()
    index = 4
    while index < len(words):
        flag = words[index]
        if flag not in ("--input", "--resume") or flag in seen or index + 1 >= len(words):
            return None
        value = words[index + 1]
        if flag == "--input" and not _safe_input_path(value):
            return None
        if flag == "--resume" and not SAFE_RESUME.fullmatch(value):
            return None
        seen.add(flag)
        index += 2
    return action


def subagent_runner_shape(cmd: str) -> bool:
    return parse_subagent_runner_shape(cmd) is not None


def bash_hit(cmd: str, prot: list, cwd: str | None = None):
    if (parse_subagent_runner_shape(cmd) == "validate"
            or (not RO_METACHAR.search(cmd) and RO_CMD.match(cmd))):
        return None
    stripped = re.sub(r"['\"\\]", "", cmd)
    c = _canon_cmd(re.sub(r"\s+", " ", stripped))
    for entry in prot:
        if entry == "constraint.md":
            if mentions_root_policy(c, cwd):
                return entry
            continue
        if mentions_path(c, canon(entry)):
            return entry
    return None


def mentions_root_policy(text: str, cwd: str | None) -> bool:
    """Match the canonical root policy, not an unrelated nested file with the same basename."""
    if cwd and mentions_path(text, canon(os.path.abspath(cwd)) + "/constraint.md"):
        return True
    for spelling in ("constraint.md", "./constraint.md"):
        offset = 0
        while offset <= len(text):
            index = text.find(spelling, offset)
            if index < 0:
                break
            before = text[index - 1] if index else ""
            after_index = index + len(spelling)
            after = text[after_index] if after_index < len(text) else ""
            if (not before or not re.match(r"[A-Za-z0-9_./-]", before)) and (
                    not after or not re.match(r"[A-Za-z0-9_./-]", after)):
                return True
            offset = index + 1
    return False


def mentions_path(text: str, target: str) -> bool:
    offset = 0
    while offset <= len(text):
        index = text.find(target, offset)
        if index < 0:
            return False
        before = text[index - 1] if index else ""
        after_index = index + len(target)
        after = text[after_index] if after_index < len(text) else ""
        left_boundary = not before or not re.match(r"[A-Za-z0-9_.-]", before)
        right_boundary = not after or after == "/" or not re.match(r"[A-Za-z0-9_.-]", after)
        if left_boundary and right_boundary:
            return True
        offset = index + 1
    return False


_DIRECTORY_FLAGS = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


def _proof_parts(relative: str) -> list[str]:
    normalized = relative.replace("\\", "/")
    parts = normalized.split("/")
    if (not normalized or normalized.startswith("/") or any(
            part in ("", ".", "..") for part in parts)):
        raise UnsafeProofPath(f"unsafe proof-relative path: {relative!r}")
    if not any(normalized.startswith(root + "/") for root in PROOF_DIRS):
        raise UnsafeProofPath(f"path is outside authoritative proof roots: {relative!r}")
    return parts


def _open_safe_directory(cwd: str, relative: str, *, create: bool) -> int | None:
    """Open a repository directory without following cwd or any relative component."""
    try:
        descriptor = os.open(cwd, _DIRECTORY_FLAGS)
    except OSError as error:
        raise UnsafeProofPath(f"repository root is not one ordinary directory: {error}") from error
    traversed: list[str] = []
    try:
        for component in relative.replace("\\", "/").split("/") if relative else ():
            if component in ("", ".", ".."):
                raise UnsafeProofPath(f"unsafe proof directory component: {component!r}")
            traversed.append(component)
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except OSError as error:
                    raise UnsafeProofPath(
                        f"cannot safely create proof directory {'/'.join(traversed)}: {error}"
                    ) from error
            except OSError as error:
                raise UnsafeProofPath(
                    f"proof directory {'/'.join(traversed)} is a symlink or non-directory: {error}"
                ) from error
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _snapshot_directory(descriptor: int, relative: str, proof: dict[str, str]) -> None:
    try:
        names = sorted(os.listdir(descriptor))
    except OSError as error:
        raise UnsafeProofPath(f"cannot inspect proof directory {relative}: {error}") from error
    for name in names:
        child_relative = relative + "/" + name
        try:
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as error:
            raise UnsafeProofPath(f"cannot lstat proof path {child_relative}: {error}") from error
        if stat.S_ISLNK(before.st_mode):
            raise UnsafeProofPath(f"proof path {child_relative} is a symbolic link")
        if stat.S_ISDIR(before.st_mode):
            try:
                child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except OSError as error:
                raise UnsafeProofPath(
                    f"cannot open proof directory {child_relative} without following links: {error}"
                ) from error
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise UnsafeProofPath(f"proof directory {child_relative} changed while opening")
                _snapshot_directory(child, child_relative, proof)
            finally:
                os.close(child)
            continue
        if not stat.S_ISREG(before.st_mode):
            raise UnsafeProofPath(f"proof path {child_relative} is not a regular file")
        try:
            child = os.open(name, _READ_FLAGS, dir_fd=descriptor)
        except OSError as error:
            raise UnsafeProofPath(
                f"cannot open proof file {child_relative} without following links: {error}"
            ) from error
        try:
            opened = os.fstat(child)
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
                raise UnsafeProofPath(f"proof file {child_relative} changed while opening")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(child, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(child)
        try:
            after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as error:
            raise UnsafeProofPath(f"proof file {child_relative} changed while reading: {error}") from error
        if ((after.st_mode, after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (before.st_mode, before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)):
            raise UnsafeProofPath(f"proof file {child_relative} changed while reading")
        proof[child_relative] = b"".join(chunks).decode("utf-8", errors="replace")


def snapshot_proof(cwd: str) -> dict[str, str]:
    proof: dict[str, str] = {}
    for directory in PROOF_DIRS:
        descriptor = _open_safe_directory(cwd, directory, create=False)
        if descriptor is None:
            continue
        try:
            _snapshot_directory(descriptor, directory, proof)
        finally:
            os.close(descriptor)
    return proof


def _restore_proof_file(cwd: str, relative: str, content: str) -> None:
    """Atomically replace one proof leaf without ever opening the destination for writing."""
    parts = _proof_parts(relative)
    descriptor = _open_safe_directory(cwd, "/".join(parts[:-1]), create=True)
    if descriptor is None:  # create=True makes this unreachable, but fail closed if that changes.
        raise UnsafeProofPath(f"cannot open proof parent for {relative}")
    leaf = parts[-1]
    temporary = f".{leaf}.ga-restore-{secrets.token_hex(8)}"
    temporary_created = False
    try:
        try:
            current = os.stat(leaf, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not (stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode)):
            raise UnsafeProofPath(f"proof leaf {relative} is not replaceable as one file")
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        output = os.open(temporary, flags, 0o600, dir_fd=descriptor)
        temporary_created = True
        try:
            with os.fdopen(output, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            # fdopen owns and closes output even on an exception.
            raise
        os.replace(temporary, leaf, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        temporary_created = False
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
        os.close(descriptor)


def _remove_proof_file(cwd: str, relative: str) -> None:
    """Remove a forged proof leaf with unlinkat semantics; never follow its final component."""
    parts = _proof_parts(relative)
    descriptor = _open_safe_directory(cwd, "/".join(parts[:-1]), create=False)
    if descriptor is None:
        return
    try:
        try:
            current = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not (stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode)):
            raise UnsafeProofPath(f"forged proof leaf {relative} is not one removable file")
        os.unlink(parts[-1], dir_fd=descriptor)
    finally:
        os.close(descriptor)


_GATE_STATE_DIR_NAME = "general-agent-gate"
_GATE_STATE_HOME_COMPONENTS = (".cache", "general-agent", "gate")


def _private_runtime_root() -> tuple[str, bool]:
    """A fixed, account-scoped runtime root for pre/post hand-off state — NEVER the shared,
    world-writable system temp directory, and never influenced by hook payload fields. Prefer
    the kernel/pam-managed per-account runtime directory (mode 0700, torn down with the login
    session); fall back to one owned directory chain under HOME when no such directory is
    exposed (e.g. a bare service/CI account)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime and os.path.isabs(runtime):
        return runtime, True
    home = os.path.expanduser("~")
    if not home or home == "~" or not os.path.isabs(home):
        raise UnsafeGateStateError(
            "cannot resolve a private account-scoped runtime directory (no XDG_RUNTIME_DIR or HOME)"
        )
    return home, False


def _verify_private_dir(descriptor: int, label: str) -> None:
    """Fail closed unless this is a plain directory, owned by this account, with no group/other
    write access — the property that makes it impossible for another local account to plant
    anything inside before we ever get here."""
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafeGateStateError(f"runtime state path {label} is not one ordinary directory")
    if info.st_uid != os.geteuid():
        raise UnsafeGateStateError(f"runtime state path {label} is not owned by this account")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise UnsafeGateStateError(f"runtime state path {label} is group/other-writable")


def _open_private_gate_dir(*, create: bool) -> int | None:
    """Open (creating on first use) the fixed, per-account gate hand-off directory — anchored
    with O_NOFOLLOW at every component, never the shared system temp directory, and never a
    location influenced by hook payload fields. Returns None (only when create=False) if a
    component is simply absent, meaning no `pre` snapshot was ever written for any session — a
    benign case for `post`. Fails closed if an EXISTING component is a symlink, non-directory,
    foreign-owned, or group/other-writable."""
    root, is_runtime_dir = _private_runtime_root()
    try:
        descriptor = os.open(root, _DIRECTORY_FLAGS)
    except FileNotFoundError:
        if not create:
            return None
        raise UnsafeGateStateError(f"private runtime root {root} does not exist") from None
    except OSError as error:
        raise UnsafeGateStateError(f"cannot open private runtime root {root}: {error}") from error
    traversed = [root]
    try:
        _verify_private_dir(descriptor, root)
        components = (_GATE_STATE_DIR_NAME,) if is_runtime_dir else _GATE_STATE_HOME_COMPONENTS
        for component in components:
            traversed.append(component)
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except OSError as error:
                    raise UnsafeGateStateError(
                        f"cannot privately create runtime state directory {'/'.join(traversed)}: {error}"
                    ) from error
            except OSError as error:
                raise UnsafeGateStateError(
                    f"runtime state path {'/'.join(traversed)} is a symlink or non-directory: {error}"
                ) from error
            _verify_private_dir(child, "/".join(traversed))
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _gate_state_name(payload: dict) -> str:
    key = payload.get("tool_use_id") or payload.get("session_id") or "na"
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "", str(key))
    return f"gate-{sanitized or 'na'}.json"


def write_private_state(payload: dict, data: dict) -> None:
    """Persist pre/post hand-off state for exactly this tool call. Anchored to one fixed,
    private, account-scoped directory (never the shared system temp dir); creation is an
    exclusive, randomly-named write followed by an atomic same-directory rename, so nothing is
    ever opened at a name an attacker could have pre-created or symlinked."""
    descriptor = _open_private_gate_dir(create=True)
    if descriptor is None:
        raise UnsafeGateStateError("cannot open the private gate-state directory")
    name = _gate_state_name(payload)
    temporary = f".{name}.{secrets.token_hex(16)}.tmp"
    temporary_created = False
    try:
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0))
        output = os.open(temporary, flags, 0o600, dir_fd=descriptor)
        temporary_created = True
        with os.fdopen(output, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        temporary_created = False
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
        os.close(descriptor)


def read_and_clear_private_state(payload: dict) -> dict | None:
    """Read exactly this tool call's hand-off state and remove it. Returns None when nothing was
    ever written for this key (benign — not every tool call snapshots proof). Raises
    UnsafeGateStateError if a file exists at the private, anchored name but fails a strict
    regular-file/ownership/mode/no-symlink check: that mismatch means the content cannot be
    trusted, and `post` must not use it to decide what belongs back in protected proof."""
    descriptor = _open_private_gate_dir(create=False)
    if descriptor is None:
        return None
    try:
        name = _gate_state_name(payload)
        try:
            handle = os.open(name, _READ_FLAGS, dir_fd=descriptor)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise UnsafeGateStateError(
                f"cannot open private gate state {name} without following links: {error}"
            ) from error
        try:
            info = os.fstat(handle)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077):
                raise UnsafeGateStateError(
                    f"private gate state {name} is not a private regular file owned by this account"
                )
            chunks: list[bytes] = []
            while True:
                chunk = os.read(handle, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(handle)
        try:
            os.unlink(name, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        os.close(descriptor)


def block(msg: str):
    sys.stderr.write(msg)
    sys.exit(2)


def pre(payload: dict):
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}
    cwd = payload.get("cwd") or os.getcwd()
    tool_l = tool.lower().replace("_", "")
    controller_invocation = None
    if any(k in tool_l for k in ("bash", "shell", "exec", "terminal")):
        command = ti.get("command", "") or ""
        if ".claude/skills/subagent-workflow/scripts/controller/run.ts" in command:
            invocation = parse_subagent_runner_shape(command)
            if invocation is None:
                block("BLOCKED by general_agent governance: malformed or unauthorized built-in "
                      "workflow command. Use plain `bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate "
                      ".subagent/flows/<name>.json`, or prefix an exact start/finalize-only command "
                      f"with `{ACK}=1`.")
            if invocation == "validate":
                return
            controller_invocation = invocation
    try:
        prot = protected_list(cwd)
    except ProjectProtectionConfigError as error:
        if project_config_repair_shape(ti.get("command", "") or ""):
            return
        block(f"BLOCKED by general_agent governance: {error}. Repair or atomically recompile "
              "`.omp/hooks/protected-paths.json` before any tool action.")
    if controller_invocation is not None:
        return
    if any(k in tool_l for k in ("write", "edit", "replace", "notebook", "patch")):
        p = ti.get("file_path") or ti.get("path") or ti.get("notebook_path") or ""
        rel = os.path.relpath(p, cwd) if os.path.isabs(p) else p
        hit = hits_list(rel, prot)
        if hit:
            block(f"BLOCKED by general_agent governance: {hit} is protected. Write tools can't carry the "
                  f"authorization env var — use a bash command prefixed with `{ACK}=1` (e.g. "
                  f"`{ACK}=1 tee {hit} <<'EOF' … EOF`).")
        return
    if any(k in tool_l for k in ("bash", "shell", "exec", "terminal")):
        cmd = ti.get("command", "") or ""
        if truthy_literal(leading_assignments(cmd).get(ACK)):
            return
        hit = bash_hit(cmd, prot, cwd)
        if hit:
            block(f"BLOCKED by general_agent governance: {hit} is protected (authoritative review "
                  f"proof or self-protected control-plane). If the change is genuinely intended, "
                  f"re-run prefixed with `{ACK}=1 <cmd>`. If you were only READING it, use the Read tool.")
        try:
            proof = snapshot_proof(cwd)
        except UnsafeProofPath as error:
            block(f"BLOCKED by general_agent governance: cannot safely snapshot authoritative "
                  f"proof paths: {error}.")
        write_private_state(payload, {"proof": proof, "cwd": cwd})


def post(payload: dict):
    try:
        state = read_and_clear_private_state(payload)
    except UnsafeGateStateError as error:
        block(f"BLOCKED by general_agent governance: the private gate hand-off state for this "
              f"tool call failed an integrity check ({error}) — treat as compromised. No proof "
              f"audit could be trusted, so nothing was auto-restored; inspect protected paths "
              f"by hand.")
        return
    if state is None:
        return
    cwd = state.get("cwd") or os.getcwd()
    proof = state["proof"]
    forged, failed = [], []
    try:
        after = snapshot_proof(cwd)
    except UnsafeProofPath as error:
        after = {}
        failed.append(f"unsafe proof tree ({error})")
    for rel, now in after.items():
        if rel in proof and proof[rel] == now:
            continue
        try:
            if rel in proof:
                _restore_proof_file(cwd, rel, proof[rel])
            else:
                _remove_proof_file(cwd, rel)
            forged.append(rel)
        except Exception as e:
            failed.append(f"{rel} ({e})")
    for rel, content in proof.items():
        if rel not in after:
            try:
                _restore_proof_file(cwd, rel, content)
                forged.append(rel)
            except Exception as e:
                failed.append(f"{rel} ({e})")
    if forged or failed:
        parts = ["general_agent governance: an unauthorized change to protected file(s) was reverted."]
        if forged:
            parts.append(f"PROOF reverted: {', '.join(forged)} — verdicts, reviews, and workflow proofs "
                         f"come from their authorized writers (run them with `{ACK}=1`), not hand-authored.")
        if failed:
            parts.append(f"COULD NOT RESTORE (fail closed — treat as compromised, restore from git): "
                         f"{', '.join(failed)}.")
        block(" ".join(parts))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    if mode in ("node-pre", "node-post"):
        # runner-level wrap for native CLIs WITHOUT hook systems (codex/kimi workflow nodes):
        #   protect_paths.py node-pre  <key> [cwd]   — snapshot authoritative proof files
        #   protect_paths.py node-post <key> [cwd]   — audit vs snapshot, revert, exit 2 on findings
        key = sys.argv[2] if len(sys.argv) > 2 else "node"
        cwd = sys.argv[3] if len(sys.argv) > 3 else os.getcwd()
        payload = {"tool_use_id": f"node-{key}", "session_id": "native-node", "cwd": cwd,
                   "tool_name": "Bash", "tool_input": {"command": ""}}
        if mode == "node-pre":
            try:
                proof = snapshot_proof(cwd)
            except UnsafeProofPath as error:
                block(f"BLOCKED by general_agent governance: cannot safely snapshot authoritative "
                      f"proof paths: {error}.")
            write_private_state(payload, {"proof": proof, "cwd": cwd})
        else:
            post(payload)
        return
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return  # unparseable → don't wedge the session
    (pre if mode == "pre" else post)(payload)


if __name__ == "__main__":
    main()
