#!/usr/bin/env python3
"""Fresh authenticated native catalog producer for launch-spec resolution.

This module owns the only production path that may invoke a native model catalog
command. It runs a fixed, hard-coded argv per backend (Claude Code's no-turn
``list_models`` control request, ``codex debug models``, and ``cursor-agent
models``), resolves the executable deterministically, captures the raw catalog
bytes and digest, and verifies that a requested (backend, model, effort) triple
is covered by the freshly authenticated account catalog.

Cursor is launched as the bound absolute ``node`` plus absolute ``index.js``,
never through the shell wrapper or unbound ``realpath``/``readlink`` helpers.
The logical wrapper and the complete install-tree manifest (every stable
no-follow regular file under the version directory, excluding only the
``.running`` runtime-state subtree) are still bound. After every real catalog
subprocess exits — including nonzero exits — the executable and full closure are
re-resolved, re-hashed, and compared exactly. A persistent swap fails. A
same-UID attacker who swaps bytes during the running command and restores them
before the post-exit rehash is outside this module's claim; that residual
window is documented, not closed.

Production callers may not override the commands, binaries, evidence, or runner.
Test callers may inject a ``runner`` callable that returns the catalog bytes for a
fixed argv, so unit tests never depend on a live CLI or network.
"""

from __future__ import annotations

import hashlib
import grp
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence


# Fixed production argv. A production caller may not substitute, append to, or
# reorder these. ``runner`` is the only injection surface for the catalog bytes
# and it is test-only.
ALLOWED_COMMANDS: dict[str, tuple[str, ...]] = {
    "claude": (
        "claude", "-p", "--input-format", "stream-json",
        "--output-format", "stream-json", "--verbose",
    ),
    "codex": ("codex", "debug", "models"),
    "cursor": ("cursor-agent", "models"),
}

# Claude Code answers this control request before a conversational turn begins.
# Keep the bytes literal and fixed: production has no prompt/input override.
CLAUDE_CONTROL_REQUEST = (
    b'{"type": "control_request", "request_id": "models", '
    b'"request": {"subtype": "list_models"}}\n'
)
_CLAUDE_REQUEST_ID = "models"
_CLAUDE_WINDOW_SUFFIX = re.compile(r"\[[^\]]*\]$")

# Effort levels each backend may expose. Used only to validate parsed shapes.
EFFORT_ORDER: dict[str, tuple[str, ...]] = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("none", "low", "medium", "high", "xhigh", "max", "ultra"),
    "cursor": ("none", "low", "medium", "high", "xhigh", "max"),
}
KNOWN_EFFORTS: dict[str, frozenset[str]] = {
    backend: frozenset(levels) for backend, levels in EFFORT_ORDER.items()
}

# Anthropic families routed through Cursor expose a ``-thinking-<effort>`` concrete
# id variant; OpenAI families expose a ``-<effort>`` variant. This is the only
# normalization rule and it is deterministic: one base+effort maps to exactly one
# candidate catalog id. If that candidate is absent, verification fails closed.
_THINKING_PREFIXES = ("claude-fable-5-1", "claude-opus-5", "claude-sonnet-5")
_PLAIN_PREFIXES = ("gpt-5.6-sol", "gpt-5.6-luna")
_GROK_PREFIXES = ("grok-4.6", "grok-4.5", "cursor-grok-4.6", "cursor-grok-4.5")

# The exact Cursor ``models`` trailer. The parser accepts this line and only this
# line after the model list; any other ``Tip:``-prefixed or non-model line fails
# closed so a malformed or duplicated trailer cannot be smuggled past coverage.
CURSOR_TIP_TRAILER = (
    "Tip: use --model <id> (or /model <id> in interactive mode) to switch. "
    "Parameterized models also accept quoted overrides, e.g. --model "
    "'claude-opus-4-8[context=1m,effort=high,fast=false]'."
)

# Trusted install roots for the final resolved executable. The resolved target
# must live under the current user's home or one of the system roots; this is the
# residual same-UID/local-install trust boundary. ``candidate_roots`` overrides
# this set for tests only.
_TRUSTED_SYSTEM_ROOTS = ("/usr/local", "/usr")

# Environment variables that override endpoints, inject code, or reroute TLS /
# proxy trust. These are stripped from the catalog subprocess environment so a
# caller cannot redirect the authenticated catalog command at an alternate
# endpoint, account, or credential store, or preload code. Claude, OpenAI,
# Codex, and Cursor environment credentials are removed so their canonical
# OS-account credential stores are authoritative.
_SCRUBBED_ENV_VARS = frozenset({
    "CURSOR_API_ENDPOINT", "CURSOR_API_KEY", "OPENAI_BASE_URL", "CODEX_API_BASE", "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_URL", "OPENAI_API_BASE", "ANTHROPIC_CUSTOM_HEADERS",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN",
    "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
    "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_GATEWAY",
    "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_BEDROCK",
    "NODE_OPTIONS", "NODE_TLS_REJECT_UNAUTHORIZED", "NODE_DEBUG", "NODE_DEBUG_NATIVE",
    "OPENSSL_CONF", "OPENSSL_ENGINES", "SSLKEYLOGFILE",
    "LD_PRELOAD", "PYTHONPATH", "DYLD_INSERT_LIBRARIES",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS", "GIT_SSL_NO_VERIFY",
    # Shell/runtime startup hooks. Catalog launch does not execute the Cursor
    # bash wrapper, but these would still affect any residual helper or a
    # non-Cursor shebang entry point.
    "BASH_ENV", "ENV", "KSH_ENV", "SHELLOPTS", "BASHOPTS", "BASH_FUNC_", "ZDOTDIR",
    "CDPATH", "IFS", "GLOBIGNORE", "PS4",
    "LD_LIBRARY_PATH", "LD_AUDIT", "LD_DEBUG",
    "PERL5OPT", "PERL5LIB", "RUBYOPT", "GEM_PATH",
    "NODE_REPL_EXTERNAL_MODULE", "NODE_PATH", "NODE_V8_COVERAGE",
    # The Cursor wrapper derives a V8 compile cache directory from these and lets
    # the bound bundle load code from it, so a caller-chosen cache path is
    # executable input. AGENT_CLI_CREDENTIAL_STORE switches which credential store
    # the catalog query authenticates against. The remaining variables select
    # alternate config or credential roots and can therefore change the account
    # whose catalog is queried.
    "NODE_COMPILE_CACHE", "XDG_CACHE_HOME", "AGENT_CLI_CREDENTIAL_STORE",
    "CURSOR_INVOKED_AS",
    "CURSOR_AUTH_TOKEN",
    "CODEX_HOME", "CLAUDE_CONFIG_DIR", "CURSOR_CONFIG_DIR",
    "CURSOR_AGENT_CONFIG_DIR", "AGENT_CLI_CONFIG_DIR",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME",
})

# Whole environment families are denied when every member can change code loading
# or the authenticated account. ``CODEX_HOME`` is deliberately caught here and
# then restored from the passwd-derived account home in :func:`_scrub_env`.
# ``CURSOR_`` is caught the same way; the only restored Cursor variable is the
# non-secret ``CURSOR_INVOKED_AS`` wrapper marker in :func:`_catalog_subprocess_env`.
_SCRUBBED_ENV_PREFIXES = (
    "PYTHON", "BASH_FUNC_", "npm_", "NODE_", "OPENAI_", "CODEX_", "CURSOR_",
)

# Cursor's catalog entry is a bash wrapper beside a Node runtime, the main
# bundle, and a large install tree of dynamically loaded chunks. The wrapper is
# bound but not executed; launch is the bound absolute node + absolute index.js.
_CURSOR_ENTRY = "cursor-agent"
_CURSOR_RUNTIME = "node"
_CURSOR_BUNDLE = "index.js"
_CURSOR_RUNTIME_STATE = ".running"
_CURSOR_NODE_FLAGS = ("--use-system-ca",)
_STABLE_FILE_FIELDS = (
    "st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
)

MAX_SYMLINK_DEPTH = 40

# A script entry point is executed through one of these fixed absolute
# interpreters. The shebang only selects *which* trusted interpreter; it can never
# introduce a new one, and ``/usr/bin/env`` name lookup is resolved here instead of
# through the caller PATH.
_TRUSTED_SCRIPT_INTERPRETERS: dict[str, tuple[str, ...]] = {
    "bash": ("/usr/bin/bash", "/bin/bash"),
    "sh": ("/usr/bin/sh", "/bin/sh"),
    "dash": ("/usr/bin/dash", "/bin/dash"),
}

MAX_SHEBANG_BYTES = 256


class CatalogError(RuntimeError):
    """A fresh catalog could not be produced or could not cover a requested role."""


@dataclass(frozen=True)
class ClosureEntry:
    """One bound member of the executed code closure."""

    role: str
    path: str
    sha256: str

    def record(self) -> dict[str, Any]:
        return {"role": self.role, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True)
class ManifestMember:
    """One stable regular file in a Cursor install-tree manifest."""

    relative_path: str
    sha256: str


@dataclass(frozen=True)
class ResolvedExecutable:
    """A fixed catalog command bound to the exact bytes that will run.

    ``argv`` is the declared logical command. ``invoked_argv`` is what is actually
    handed to :func:`subprocess.run`. For Cursor that is the bound absolute node
    plus absolute ``index.js``; for a non-Cursor script entry point it is prefixed
    with the fixed trusted interpreter so no ``#!`` lookup happens through PATH.
    ``closure`` names the executed pieces. ``install_manifest`` is the complete
    sorted install-tree digest for Cursor, and empty otherwise.
    """

    argv: tuple[str, ...]
    executable: str
    executable_sha256: str
    invoked_argv: tuple[str, ...]
    closure: tuple[ClosureEntry, ...]
    install_manifest: tuple[ManifestMember, ...] = ()

    @property
    def closure_sha256(self) -> str:
        named = "\n".join(f"{e.role}\t{e.path}\t{e.sha256}" for e in self.closure)
        if not self.install_manifest:
            payload = named
        else:
            manifest = "\n".join(
                f"{member.relative_path}\t{member.sha256}" for member in self.install_manifest
            )
            payload = f"{named}\n--install-manifest--\n{manifest}"
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def install_manifest_sha256(self) -> str:
        joined = "\n".join(
            f"{member.relative_path}\t{member.sha256}" for member in self.install_manifest
        )
        return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def record(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "executable": self.executable,
            "executableSha256": self.executable_sha256,
            "invokedArgv": list(self.invoked_argv),
            "executionClosure": [entry.record() for entry in self.closure],
            "executionClosureSha256": self.closure_sha256,
        }


@dataclass(frozen=True)
class CatalogEvidence:
    backend: str
    executable: ResolvedExecutable
    raw_bytes: bytes
    bytes_sha256: str
    exit_code: int
    stderr_bytes: bytes
    coverage: dict[str, list[str]]

    def record(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "command": self.executable.record(),
            "catalogBytesSha256": self.bytes_sha256,
            "exitCode": self.exit_code,
            "stderrSha256": "sha256:" + hashlib.sha256(self.stderr_bytes).hexdigest(),
            "coverage": self.coverage,
        }


def _home_root() -> str:
    """Return the OS account home of the *running uid*.

    Deliberately never ``expanduser``/``$HOME``: a caller that controls HOME could
    otherwise point the fixed install lookup at a directory it fully owns and get a
    forged catalog binary bound into the receipt.
    """
    try:
        entry = pwd.getpwuid(os.getuid())
    except KeyError as error:  # pragma: no cover - no passwd entry for this uid
        raise CatalogError(f"no OS account home for uid {os.getuid()}: {error}") from error
    home = entry.pw_dir
    if not home or not os.path.isabs(home):
        raise CatalogError(f"OS account home for uid {os.getuid()} is not an absolute path")
    return os.path.realpath(home)


def _trusted_roots(candidate_roots: Sequence[str] | None) -> tuple[str, ...]:
    if candidate_roots is not None:
        return tuple(os.path.realpath(r) for r in candidate_roots)
    return (_home_root(), *_TRUSTED_SYSTEM_ROOTS)


def _entry_dirs(entry_dirs: Sequence[str] | None) -> tuple[str, ...]:
    if entry_dirs is not None:
        return tuple(os.path.realpath(d) for d in entry_dirs)
    home = _home_root()
    return (
        os.path.join(home, ".local", "bin"),
        "/usr/local/bin",
        "/usr/bin",
    )


def _candidate_entry_points(name: str, entry_dirs: Sequence[str] | None) -> list[str]:
    return [os.path.join(directory, name) for directory in _entry_dirs(entry_dirs)]


def _under_any_root(path: str, roots: Iterable[str]) -> bool:
    resolved = os.path.realpath(path)
    for root in roots:
        rroot = os.path.realpath(root)
        if resolved == rroot or resolved.startswith(rroot + os.sep):
            return True
    return False


def _owned_by_trusted(st: os.stat_result) -> bool:
    return st.st_uid == os.geteuid() or st.st_uid == 0


def _group_is_private_to_current_account(gid: int) -> bool:
    """Whether ``gid`` is demonstrably usable only by the effective account.

    A group-writable component can be safe within the documented same-UID trust
    boundary only when its group grants no write capability to another account.
    Prove that from both NSS views of membership: every passwd entry whose
    primary gid is ``gid`` must be the effective account, and every explicit
    group member must be that account's login name. Missing or malformed account
    data fails closed.
    """
    uid = os.geteuid()
    try:
        account = pwd.getpwuid(uid)
        group = grp.getgrgid(gid)
        passwd_entries = pwd.getpwall()
    except (KeyError, OSError):
        return False
    account_name = getattr(account, "pw_name", None)
    if (
        not isinstance(account_name, str)
        or not account_name
        or getattr(account, "pw_uid", None) != uid
        or getattr(group, "gr_gid", None) != gid
    ):
        return False
    members = getattr(group, "gr_mem", None)
    if not isinstance(members, (list, tuple)) or any(
        not isinstance(member, str) or member != account_name for member in members
    ):
        return False
    has_current_member = account.pw_gid == gid or account_name in members
    if not has_current_member:
        return False
    for entry in passwd_entries:
        if getattr(entry, "pw_gid", None) != gid:
            continue
        if (
            getattr(entry, "pw_uid", None) != uid
            or getattr(entry, "pw_name", None) != account_name
        ):
            return False
    return True


def _private_group_writable_by_current_account(st: os.stat_result) -> bool:
    """Return whether a group-writable component stays inside same-UID trust."""
    return st.st_uid == os.geteuid() and _group_is_private_to_current_account(st.st_gid)


def _validate_parent_safe(path: str) -> None:
    parent = os.path.dirname(path) or "/"
    try:
        st = os.lstat(parent)
    except OSError as error:
        raise CatalogError(f"cannot stat parent directory {parent!r}: {error}") from error
    if not stat.S_ISDIR(st.st_mode):
        raise CatalogError(f"parent {parent!r} is not a directory")
    # A directory owner can always replace children, even if its write mode
    # otherwise looks narrow. Every traversed parent must therefore be owned by
    # this uid or root. The resolver calls this for every path component, not
    # only the final executable parent.
    if not _owned_by_trusted(st):
        raise CatalogError(
            f"parent directory is not owned by the current user or root: {parent!r}"
        )
    # A trusted sticky directory protects an existing current-uid/root entry
    # from world peers (the standard /tmp case). Group write is allowed only
    # when NSS proves the group is private to this effective account; the sticky
    # bit does not establish that account-group boundary.
    sticky = bool(st.st_mode & stat.S_ISVTX)
    if (st.st_mode & stat.S_IWOTH) and not sticky:
        raise CatalogError(f"parent directory is world-writable without sticky bit: {parent!r}")
    if (st.st_mode & stat.S_IWGRP) and not _private_group_writable_by_current_account(st):
        raise CatalogError(
            f"parent directory is group-writable by a shared or unverifiable group: {parent!r}"
        )


def _resolve_symlink_chain(entry: str, roots: tuple[str, ...]) -> str:
    """Resolve ``entry`` to its fully-resolved regular-executable target.

    Every component is walked with lstat/no-follow: intermediate directory
    symlinks and the final symlink are both followed, validating each parent
    directory (trusted-owned and not non-sticky group/world-writable) and each
    link (owned by the current user or root) before reading it. The final
    regular executable must be owned by a trusted uid, user-executable, and
    live under a trusted install root. World-writable components fail closed;
    group-writable components are admitted only when the group is demonstrably
    private to the effective account. Loops and overly-deep chains fail closed.
    """
    if not os.path.isabs(entry):
        raise CatalogError(f"catalog entry must be an absolute path: {entry!r}")
    resolved = os.sep
    remaining = [part for part in entry.split(os.sep) if part]
    visited: set[str] = set()
    steps = 0
    while remaining:
        part = remaining.pop(0)
        candidate = os.path.join(resolved, part)
        steps += 1
        if steps > MAX_SYMLINK_DEPTH * 4:
            raise CatalogError(f"symlink chain too deep at {entry!r}")
        _validate_parent_safe(candidate)
        try:
            st = os.lstat(candidate)
        except OSError as error:
            raise CatalogError(f"cannot stat catalog entry {candidate!r}: {error}") from error
        if stat.S_ISLNK(st.st_mode):
            if not _owned_by_trusted(st):
                raise CatalogError(f"symlink {candidate!r} is not owned by the current user or root")
            if candidate in visited:
                raise CatalogError(f"symlink chain loops at {candidate!r}")
            visited.add(candidate)
            try:
                link_target = os.readlink(candidate)
            except OSError as error:
                raise CatalogError(f"cannot read symlink {candidate!r}: {error}") from error
            target = link_target if os.path.isabs(link_target) else os.path.join(resolved, link_target)
            target = os.path.normpath(target)
            resolved = os.sep
            remaining = [p for p in target.split(os.sep) if p] + remaining
            continue
        if stat.S_ISDIR(st.st_mode):
            resolved = candidate
            continue
        if stat.S_ISREG(st.st_mode):
            if remaining:
                raise CatalogError(f"regular file {candidate!r} has trailing components {remaining!r}")
            if not _owned_by_trusted(st):
                raise CatalogError(f"catalog executable {candidate!r} is not owned by the current user or root")
            if st.st_mode & stat.S_IWOTH:
                raise CatalogError(f"catalog executable {candidate!r} is group/world-writable")
            if (st.st_mode & stat.S_IWGRP) and not _private_group_writable_by_current_account(st):
                raise CatalogError(
                    f"catalog executable {candidate!r} is group-writable by a shared or unverifiable group"
                )
            if not (st.st_mode & stat.S_IXUSR):
                raise CatalogError(f"catalog executable {candidate!r} is not user-executable")
            if not _under_any_root(candidate, roots):
                raise CatalogError(f"catalog executable {candidate!r} is outside the trusted install roots")
            return candidate
        raise CatalogError(f"catalog entry {candidate!r} is neither a symlink, directory, nor regular file")
    raise CatalogError(f"catalog entry {entry!r} resolved to a directory, not an executable")


def _resolve_executable(
    argv: tuple[str, ...],
    *,
    candidate_roots: Sequence[str] | None = None,
    entry_dirs: Sequence[str] | None = None,
) -> ResolvedExecutable:
    """Resolve the argv0 to a canonical executable from a fixed trusted search order.

    The caller PATH is never consulted. The fixed-name entry point is searched in
    ``entry_dirs`` (production: ``~/.local/bin/<name>``, ``/usr/local/bin/<name>``,
    ``/usr/bin/<name>``); ``entry_dirs`` overrides the search directories for
    tests only. The entry point may be a symlink; its chain is resolved with
    lstat/no-follow at every step, validating each parent and link, and the final
    regular executable is bound and executed. The final target must live under a
    trusted install root (production: current home, ``/usr/local``, ``/usr``);
    ``candidate_roots`` overrides those roots for tests only. World-writable
    targets, group-writable targets with a shared or unverifiable group, unsafe
    links, loops, and targets outside the trusted roots fail closed.
    """
    if not argv:
        raise CatalogError("catalog command argv is empty")
    name = argv[0]
    if not isinstance(name, str) or not name:
        raise CatalogError("catalog command argv0 is not a non-empty string")
    roots = _trusted_roots(candidate_roots)
    entry: str | None = None
    for candidate in _candidate_entry_points(name, entry_dirs):
        if os.path.lexists(candidate):
            entry = candidate
            break
    if entry is None:
        searched = _candidate_entry_points(name, entry_dirs)
        raise CatalogError(f"no trusted install location for {name!r}; searched {searched}")
    target = _resolve_symlink_chain(entry, roots)
    target_bytes = _read_bound_bytes(target)
    digest = "sha256:" + hashlib.sha256(target_bytes).hexdigest()
    if argv[0] == _CURSOR_ENTRY:
        invoked_argv, closure, manifest = _bind_cursor_install(target, argv, digest)
        return ResolvedExecutable(
            argv=argv,
            executable=target,
            executable_sha256=digest,
            invoked_argv=invoked_argv,
            closure=tuple(closure),
            install_manifest=tuple(manifest),
        )
    interpreter = _script_interpreter(target, target_bytes)
    closure: list[ClosureEntry] = []
    if interpreter is None:
        invoked_argv = (target, *argv[1:])
    else:
        interpreter_bytes = _read_bound_bytes(interpreter)
        closure.append(ClosureEntry(
            role="interpreter",
            path=interpreter,
            sha256="sha256:" + hashlib.sha256(interpreter_bytes).hexdigest(),
        ))
        invoked_argv = (interpreter, target, *argv[1:])
    closure.append(ClosureEntry(role="entrypoint", path=target, sha256=digest))
    return ResolvedExecutable(
        argv=argv,
        executable=target,
        executable_sha256=digest,
        invoked_argv=invoked_argv,
        closure=tuple(closure),
    )


def _validate_install_member_stat(
    path: str, st: os.stat_result, *, require_executable: bool = False,
) -> None:
    if not _owned_by_trusted(st):
        raise CatalogError(
            f"catalog install member {path!r} is not owned by the current user or root"
        )
    if st.st_mode & stat.S_IWOTH:
        raise CatalogError(f"catalog install member {path!r} is world-writable")
    if (st.st_mode & stat.S_IWGRP) and not _private_group_writable_by_current_account(st):
        raise CatalogError(
            f"catalog install member {path!r} is group-writable by a shared or unverifiable group"
        )
    if require_executable and not (st.st_mode & stat.S_IXUSR):
        raise CatalogError(f"catalog install member {path!r} is not user-executable")


def _cursor_install_manifest(install_dir: str) -> list[ManifestMember]:
    """Bind every stable regular file under the Cursor version directory.

    The only excluded subtree is the exact ``.running`` runtime-state directory
    beside the wrapper. Any other symlink or special file fails closed.
    """
    members: list[ManifestMember] = []
    for dirpath, dirnames, filenames in os.walk(install_dir, topdown=True, followlinks=False):
        _validate_parent_safe(os.path.join(dirpath, "."))
        try:
            directory_stat = os.lstat(dirpath)
        except OSError as error:
            raise CatalogError(f"cannot stat catalog install directory {dirpath!r}: {error}") from error
        if stat.S_ISLNK(directory_stat.st_mode):
            raise CatalogError(f"unexpected symlink in catalog install tree: {dirpath!r}")
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise CatalogError(f"unexpected special file in catalog install tree: {dirpath!r}")
        if dirpath == install_dir:
            dirnames[:] = [name for name in dirnames if name != _CURSOR_RUNTIME_STATE]
            filenames = [name for name in filenames if name != _CURSOR_RUNTIME_STATE]
        dirnames.sort()
        for name in dirnames:
            child = os.path.join(dirpath, name)
            try:
                child_stat = os.lstat(child)
            except OSError as error:
                raise CatalogError(f"cannot stat catalog install path {child!r}: {error}") from error
            if stat.S_ISLNK(child_stat.st_mode):
                raise CatalogError(f"unexpected symlink in catalog install tree: {child!r}")
            if not stat.S_ISDIR(child_stat.st_mode):
                raise CatalogError(f"unexpected special file in catalog install tree: {child!r}")
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            try:
                file_stat = os.lstat(path)
            except OSError as error:
                raise CatalogError(f"cannot stat catalog install member {path!r}: {error}") from error
            if stat.S_ISLNK(file_stat.st_mode):
                raise CatalogError(f"unexpected symlink in catalog install tree: {path!r}")
            if not stat.S_ISREG(file_stat.st_mode):
                raise CatalogError(f"unexpected special file in catalog install tree: {path!r}")
            relative = os.path.relpath(path, install_dir).replace(os.sep, "/")
            if relative.startswith("../") or os.path.isabs(relative):
                raise CatalogError(f"catalog install member escaped the install directory: {path!r}")
            _validate_install_member_stat(
                path, file_stat, require_executable=(relative == _CURSOR_RUNTIME),
            )
            digest = "sha256:" + hashlib.sha256(_read_bound_bytes(path)).hexdigest()
            members.append(ManifestMember(relative_path=relative, sha256=digest))
    members.sort(key=lambda member: member.relative_path)
    return members


def _bind_cursor_install(
    target: str, argv: tuple[str, ...], wrapper_digest: str,
) -> tuple[tuple[str, ...], list[ClosureEntry], list[ManifestMember]]:
    install_dir = os.path.dirname(target)
    manifest = _cursor_install_manifest(install_dir)
    by_relative = {member.relative_path: member for member in manifest}
    wrapper_name = os.path.basename(target)
    for required in (_CURSOR_RUNTIME, _CURSOR_BUNDLE, wrapper_name):
        if required not in by_relative:
            raise CatalogError(f"cursor catalog install is missing required member {required!r}")
    if by_relative[wrapper_name].sha256 != wrapper_digest:
        raise CatalogError("cursor catalog wrapper digest drifted from the install manifest")
    node_path = os.path.join(install_dir, _CURSOR_RUNTIME)
    bundle_path = os.path.join(install_dir, _CURSOR_BUNDLE)
    closure = [
        ClosureEntry(role="entrypoint", path=target, sha256=wrapper_digest),
        ClosureEntry(role="runtime", path=node_path, sha256=by_relative[_CURSOR_RUNTIME].sha256),
        ClosureEntry(role="bundle", path=bundle_path, sha256=by_relative[_CURSOR_BUNDLE].sha256),
    ]
    return (node_path, *_CURSOR_NODE_FLAGS, bundle_path, *argv[1:]), closure, manifest


def _read_bound_bytes(path: str) -> bytes:
    """Read a regular file without following a final-component symlink.

    ``lstat`` and ``fstat`` identity must match before and after the read. This
    rejects a swap that lands before the bytes are consumed. It does not close
    the residual same-UID swap-and-restore window across a later subprocess.
    """
    if not hasattr(os, "O_NOFOLLOW"):
        raise CatalogError("O_NOFOLLOW is required to bind catalog bytes")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise CatalogError(f"bound path is not a regular file: {path!r}")
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise CatalogError(f"bound path is not a regular file: {path!r}")
        if any(getattr(before, field) != getattr(opened, field) for field in _STABLE_FILE_FIELDS):
            raise CatalogError(f"bound path changed while it was being opened: {path!r}")
        chunks: list[bytes] = []
        while True:
            piece = os.read(descriptor, 1024 * 1024)
            if not piece:
                break
            chunks.append(piece)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        final = os.lstat(path)
        if (
            any(getattr(opened, field) != getattr(after, field) for field in _STABLE_FILE_FIELDS)
            or any(getattr(after, field) != getattr(final, field) for field in _STABLE_FILE_FIELDS)
            or len(payload) != after.st_size
        ):
            raise CatalogError(f"bound path changed while it was being read: {path!r}")
        return payload
    except CatalogError:
        raise
    except OSError as error:
        raise CatalogError(f"bound executable bytes could not be read: {path!r}: {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _script_interpreter(target: str, target_bytes: bytes) -> str | None:
    """Return the fixed trusted interpreter for a ``#!`` entry point, else ``None``.

    Cursor installs ``cursor-agent`` as a bash wrapper, so the file that the kernel
    would actually execute is bash, not the agent. Resolving the interpreter here
    means (a) the shebang cannot smuggle in an untrusted interpreter or extra
    interpreter flags, (b) ``#!/usr/bin/env bash`` never performs a PATH lookup, and
    (c) both halves of the closure end up digested in the receipt.
    """
    if not target_bytes.startswith(b"#!"):
        return None
    line = target_bytes[2:MAX_SHEBANG_BYTES].split(b"\n", 1)[0]
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CatalogError(f"shebang of {target!r} is not valid UTF-8: {error}") from error
    tokens = text.strip().split()
    if not tokens:
        raise CatalogError(f"shebang of {target!r} names no interpreter")
    first = tokens[0]
    if os.path.basename(first) == "env":
        if len(tokens) != 2:
            raise CatalogError(
                f"shebang of {target!r} uses env with unsupported extra arguments: {tokens!r}"
            )
        name = tokens[1]
    else:
        if len(tokens) != 1:
            raise CatalogError(
                f"shebang of {target!r} passes unsupported interpreter arguments: {tokens!r}"
            )
        name = os.path.basename(first)
    candidates = _TRUSTED_SCRIPT_INTERPRETERS.get(name)
    if not candidates:
        raise CatalogError(f"shebang of {target!r} names untrusted interpreter {name!r}")
    for candidate in candidates:
        try:
            st = os.stat(candidate)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode) or not (st.st_mode & stat.S_IXUSR):
            continue
        if st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise CatalogError(f"trusted interpreter {candidate!r} is not a root-owned, non-writable file")
        return os.path.realpath(candidate)
    raise CatalogError(f"no trusted interpreter installed for {name!r}; searched {list(candidates)}")


def _synthetic_executable(argv: tuple[str, ...]) -> ResolvedExecutable:
    """Deterministic synthetic binding for test-only runners.

    The digest is over the fixed argv so a test that changes the argv (which is
    not allowed in production) still produces a different binding, and a test
    replaying the same argv reproduces the same binding.
    """
    payload = "\0".join(argv).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    executable = f"test-runner:{argv[0]}"
    return ResolvedExecutable(
        argv=argv,
        executable=executable,
        executable_sha256=digest,
        invoked_argv=argv,
        closure=(ClosureEntry(role="entrypoint", path=executable, sha256=digest),),
    )


def _scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Drop endpoint/injection variables and pin config roots to this OS account.

    HOME and PATH are *replaced*, not preserved: the bound catalog binary resolves
    its own credential store and any helper it shells out to relative to them, so a
    caller-supplied value would move the effective trust boundary. Alternate
    config-root selectors are dropped; Codex and the XDG defaults are then
    reintroduced at their documented account-derived locations so the OS account's
    normal authentication remains reachable. Caller Cursor credentials are dropped
    with the rest of the ``CURSOR_`` family so the canonical OS-account store is
    authoritative.
    """
    home = _home_root()
    cleaned = {
        key: value
        for key, value in env.items()
        if key not in _SCRUBBED_ENV_VARS and not key.startswith(_SCRUBBED_ENV_PREFIXES)
    }
    cleaned["HOME"] = home
    cleaned["PATH"] = os.pathsep.join((os.path.join(home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"))
    cleaned["CODEX_HOME"] = os.path.join(home, ".codex")
    cleaned["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    cleaned["XDG_DATA_HOME"] = os.path.join(home, ".local", "share")
    return cleaned


def _catalog_subprocess_env(executable: ResolvedExecutable) -> dict[str, str]:
    env = _scrub_env(os.environ)
    if executable.argv[0] == _CURSOR_ENTRY:
        # Catalog launch skips the bash wrapper, which is the only writer of this
        # non-secret marker. Restore the canonical value so the bound bundle sees
        # the same invoked-as identity as `cursor-agent`.
        env["CURSOR_INVOKED_AS"] = _CURSOR_ENTRY
    return env


def _run_command(
    executable: ResolvedExecutable,
    *,
    runner: Callable[[tuple[str, ...]], tuple[int, bytes, bytes]] | None,
) -> tuple[int, bytes, bytes]:
    if runner is not None:
        return runner(executable.argv)
    cmd = list(executable.invoked_argv)
    stdin_args: dict[str, Any]
    if executable.argv == ALLOWED_COMMANDS["claude"]:
        stdin_args = {"input": CLAUDE_CONTROL_REQUEST}
    else:
        stdin_args = {"stdin": subprocess.DEVNULL}
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_catalog_subprocess_env(executable),
            check=False,
            shell=False,
            **stdin_args,
        )
    except OSError as error:
        raise CatalogError(f"catalog command failed to start {cmd}: {error}") from error
    return completed.returncode, completed.stdout, completed.stderr


def produce(
    backend: str,
    *,
    runner: Callable[[tuple[str, ...]], tuple[int, bytes, bytes]] | None = None,
    candidate_roots: Sequence[str] | None = None,
    entry_dirs: Sequence[str] | None = None,
) -> CatalogEvidence:
    """Produce fresh authenticated catalog evidence for one backend.

    ``runner``, ``candidate_roots``, and ``entry_dirs`` are test-only. Production
    callers must leave all three ``None`` so the fixed argv is invoked through
    ``subprocess.run`` with ``shell=False`` and the executable is resolved from
    the fixed trusted install-location search order. When ``runner`` is injected
    the executable binding is synthetic but deterministic (a digest of the fixed
    argv), so tests never touch the real PATH binary; ``candidate_roots`` and
    ``entry_dirs`` are ignored when ``runner`` is set because resolution is bypassed.
    After a real subprocess returns, including a nonzero exit or a start failure,
    the executable and full install closure are resolved and hashed again; any
    difference fails closed.
    """
    if backend not in ALLOWED_COMMANDS:
        raise CatalogError(f"backend {backend!r} has no fixed catalog command")
    argv = ALLOWED_COMMANDS[backend]
    resolve_kwargs = {"candidate_roots": candidate_roots, "entry_dirs": entry_dirs}
    if runner is not None:
        executable = _synthetic_executable(argv)
    else:
        executable = _resolve_executable(argv, **resolve_kwargs)
    run_error: CatalogError | None = None
    exit_code = 0
    stdout_bytes = b""
    stderr_bytes = b""
    try:
        exit_code, stdout_bytes, stderr_bytes = _run_command(executable, runner=runner)
    except CatalogError as error:
        run_error = error
    if runner is None:
        rebound = _resolve_executable(argv, **resolve_kwargs)
        if rebound != executable:
            raise CatalogError(
                "catalog executable or install closure changed after the catalog command exited"
            )
    if run_error is not None:
        raise run_error
    if exit_code != 0:
        raise CatalogError(
            f"catalog command {argv!r} exited {exit_code}; "
            f"stderr={stderr_bytes[:512]!r}"
        )
    if not stdout_bytes:
        raise CatalogError(f"catalog command {argv!r} produced no stdout bytes")
    coverage = _parse_catalog(backend, stdout_bytes)
    return CatalogEvidence(
        backend=backend,
        executable=executable,
        raw_bytes=stdout_bytes,
        bytes_sha256="sha256:" + hashlib.sha256(stdout_bytes).hexdigest(),
        exit_code=exit_code,
        stderr_bytes=stderr_bytes,
        coverage=coverage,
    )


def _parse_catalog(backend: str, raw: bytes) -> dict[str, list[str]]:
    if backend == "claude":
        return _parse_claude(raw)
    if backend == "codex":
        return _parse_codex(raw)
    if backend == "cursor":
        return _parse_cursor(raw)
    raise CatalogError(f"unsupported backend for catalog parsing: {backend!r}")


def _parse_claude(raw: bytes) -> dict[str, list[str]]:
    """Parse one successful matching Claude Code ``list_models`` response."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CatalogError(f"claude catalog is not valid UTF-8: {error}") from error

    matches: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            raise CatalogError(
                f"claude catalog line {line_number} is not valid JSON: {error}"
            ) from error
        if not isinstance(message, dict):
            raise CatalogError(f"claude catalog line {line_number} is not a JSON object")
        if message.get("type") != "control_response":
            continue
        response = message.get("response")
        if not isinstance(response, dict):
            raise CatalogError(f"claude control response on line {line_number} is malformed")
        if response.get("request_id") == _CLAUDE_REQUEST_ID:
            matches.append(response)

    if not matches:
        raise CatalogError("claude catalog has no matching models control response")
    if len(matches) != 1:
        raise CatalogError("claude catalog has duplicate matching control responses")
    answer = matches[0]
    if answer.get("subtype") != "success":
        reason = answer.get("error")
        if not isinstance(reason, str) or not reason.strip():
            reason = "models control request was not successful"
        raise CatalogError(f"claude catalog request failed: {reason}")
    body = answer.get("response")
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        raise CatalogError("claude successful control response has no models list")
    if not body["models"]:
        raise CatalogError("claude catalog lists no models")

    coverage: dict[str, list[str]] = {}
    for index, entry in enumerate(body["models"]):
        if not isinstance(entry, dict):
            raise CatalogError(f"claude catalog model entry {index} is not an object")
        resolved = entry.get("resolvedModel")
        value = entry.get("value")
        if resolved not in (None, "") and not isinstance(resolved, str):
            raise CatalogError(f"claude catalog model entry {index} has malformed resolvedModel")
        if value not in (None, "") and not isinstance(value, str):
            raise CatalogError(f"claude catalog model entry {index} has malformed value")
        selected = resolved if isinstance(resolved, str) and resolved else value
        if not isinstance(selected, str) or not selected or selected != selected.strip():
            raise CatalogError(f"claude catalog model entry {index} has no unambiguous model id")
        model = _CLAUDE_WINDOW_SUFFIX.sub("", selected)
        if not model:
            raise CatalogError(f"claude catalog model entry {index} normalizes to an empty id")

        levels = entry.get("supportedEffortLevels")
        if levels is None:
            continue
        if not isinstance(levels, list):
            raise CatalogError(f"claude catalog model {model!r} has malformed supportedEffortLevels")
        if not levels:
            continue
        efforts: list[str] = []
        for effort in levels:
            if not isinstance(effort, str) or not effort:
                raise CatalogError(f"claude catalog model {model!r} has a malformed effort")
            if effort not in KNOWN_EFFORTS["claude"]:
                raise CatalogError(f"claude catalog model {model!r} exposes unknown effort {effort!r}")
            if effort in efforts:
                raise CatalogError(f"claude catalog model {model!r} duplicates effort {effort!r}")
            efforts.append(effort)

        prior = coverage.get(model)
        if prior is not None:
            if set(prior) != set(efforts):
                raise CatalogError(f"claude catalog has conflicting duplicate model {model!r}")
            continue
        coverage[model] = efforts
    if not coverage:
        raise CatalogError("claude catalog has no models with supported effort coverage")
    return coverage


def _parse_codex(raw: bytes) -> dict[str, list[str]]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError(f"codex catalog is not valid UTF-8 JSON: {error}") from error
    if not isinstance(document, dict) or not isinstance(document.get("models"), list):
        raise CatalogError("codex catalog JSON must be an object with a models list")
    coverage: dict[str, list[str]] = {}
    for entry in document["models"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
            raise CatalogError("codex catalog entry is missing a string slug")
        slug = entry["slug"]
        if not slug:
            raise CatalogError("codex catalog entry has an empty slug")
        levels = entry.get("supported_reasoning_levels")
        if not isinstance(levels, list) or not levels:
            raise CatalogError(f"codex catalog entry {slug!r} has no supported_reasoning_levels")
        efforts: list[str] = []
        for level in levels:
            if not isinstance(level, dict) or not isinstance(level.get("effort"), str):
                raise CatalogError(f"codex catalog entry {slug!r} has a malformed reasoning level")
            effort = level["effort"]
            if effort not in KNOWN_EFFORTS["codex"]:
                raise CatalogError(f"codex catalog entry {slug!r} exposes unknown effort {effort!r}")
            if effort in efforts:
                raise CatalogError(f"codex catalog entry {slug!r} duplicates effort {effort!r}")
            efforts.append(effort)
        if slug in coverage:
            raise CatalogError(f"codex catalog lists duplicate slug {slug!r}")
        coverage[slug] = efforts
    if not coverage:
        raise CatalogError("codex catalog lists no models")
    return coverage


def _parse_cursor(raw: bytes) -> dict[str, list[str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CatalogError(f"cursor catalog is not valid UTF-8: {error}") from error
    lines = text.splitlines()
    ids: list[str] = []
    seen_header = False
    seen_tip = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not seen_header:
            if stripped.lower() != "available models":
                raise CatalogError(f"cursor catalog header is not 'Available models': {stripped!r}")
            seen_header = True
            continue
        if stripped == CURSOR_TIP_TRAILER:
            if seen_tip:
                raise CatalogError("cursor catalog lists a duplicate Tip trailer")
            seen_tip = True
            continue
        # The trailer is terminal. Anything meaningful after it means the output was
        # concatenated, truncated-and-retried, or otherwise not a single authentic
        # catalog response, so the whole evidence record is refused.
        if seen_tip:
            raise CatalogError(
                f"cursor catalog continues after the Tip trailer: {stripped!r}"
            )
        if stripped.startswith("Tip:"):
            raise CatalogError(f"cursor catalog has a malformed Tip trailer: {stripped!r}")
        if " - " not in stripped:
            raise CatalogError(f"cursor catalog line is not '<id> - <display>': {stripped!r}")
        identifier = stripped.split(" - ", 1)[0].strip()
        if not identifier:
            raise CatalogError(f"cursor catalog line has an empty id: {stripped!r}")
        if identifier in ids:
            raise CatalogError(f"cursor catalog lists duplicate id {identifier!r}")
        ids.append(identifier)
    if not seen_header:
        raise CatalogError("cursor catalog is missing the 'Available models' header")
    if not ids:
        raise CatalogError("cursor catalog lists no models")
    if not seen_tip:
        raise CatalogError("cursor catalog is missing the exact terminal Tip trailer")
    # Coverage maps a base family prefix to the concrete ids observed. The
    # verifier normalizes a requested base+effort to one candidate concrete id
    # and checks membership, so coverage here is the full concrete-id list keyed
    # by an empty string sentinel that the verifier ignores; the verifier uses
    # normalize_cursor_catalog_id directly against this id set.
    return {"__cursor_ids__": ids}


def cursor_catalog_ids(evidence: CatalogEvidence) -> list[str]:
    if evidence.backend != "cursor":
        raise CatalogError("cursor_catalog_ids requires a cursor evidence record")
    return list(evidence.coverage.get("__cursor_ids__", []))


def normalize_cursor_catalog_id(base_model: str, effort: str) -> str:
    """Map a configured base model + effort to exactly one Cursor concrete id.

    The rule is deterministic and total for the supported families:
    - Anthropic families (claude-fable-5-1, claude-opus-5, claude-sonnet-5) use the
      ``-thinking-<effort>`` variant, because reasoning effort is only expressible on the
      thinking SKU.
    - OpenAI families (gpt-5.6-sol, gpt-5.6-luna) use the ``-<effort>`` variant.
    - Cursor Grok families use the concrete ``cursor-grok-<version>-<effort>`` id.

    Any other family, or an effort outside the known set, fails closed. This
    function never consults a catalog; it only produces the candidate id. The
    verifier checks membership against the fresh cursor catalog.
    """
    if not isinstance(base_model, str) or not base_model:
        raise CatalogError("cursor base model must be a non-empty string")
    if not isinstance(effort, str) or not effort:
        raise CatalogError("cursor effort must be a non-empty string")
    if effort not in KNOWN_EFFORTS["cursor"]:
        raise CatalogError(f"cursor effort {effort!r} is not a known reasoning level")
    for prefix in _THINKING_PREFIXES:
        if base_model == prefix or base_model.startswith(prefix + "-"):
            return f"{prefix}-thinking-{effort}"
    for prefix in _PLAIN_PREFIXES:
        if base_model == prefix or base_model.startswith(prefix + "-"):
            return f"{prefix}-{effort}"
    for prefix in _GROK_PREFIXES:
        if base_model == prefix or base_model.startswith(prefix + "-"):
            version = prefix.removeprefix("cursor-")
            return f"cursor-{version}-{effort}"
    raise CatalogError(f"cursor base model {base_model!r} has no audited normalization rule")


def verify_role(
    backend: str,
    model: str,
    effort: str,
    evidence: CatalogEvidence,
) -> str:
    """Verify a (backend, model, effort) triple against fresh catalog evidence.

    Returns the concrete catalog id that was matched. Raises CatalogError on any
    mismatch, missing entry, or unsupported effort.
    """
    if evidence.backend != backend:
        raise CatalogError(
            f"catalog evidence backend {evidence.backend!r} does not match requested {backend!r}"
        )
    if backend in {"claude", "codex"}:
        levels = evidence.coverage.get(model)
        if levels is None:
            raise CatalogError(f"{backend} catalog does not list model {model!r}")
        if effort not in levels:
            raise CatalogError(
                f"{backend} model {model!r} does not support effort {effort!r}; "
                f"supported={levels!r}"
            )
        return model
    if backend == "cursor":
        candidate = normalize_cursor_catalog_id(model, effort)
        ids = cursor_catalog_ids(evidence)
        if candidate not in ids:
            raise CatalogError(
                f"cursor catalog does not list concrete id {candidate!r} "
                f"for base {model!r} effort {effort!r}"
            )
        return candidate
    raise CatalogError(f"unsupported backend for role verification: {backend!r}")


def highest_available_effort(
    backend: str,
    model: str,
    evidence: CatalogEvidence,
) -> str:
    """Return the preferred highest effort proven for one exact catalog model.

    Literal ``max`` wins whenever present. Otherwise the backend's declared
    provider-local order selects the highest covered level. This function never
    changes the model or backend and never treats effort labels as cross-provider
    equivalents.
    """
    if evidence.backend != backend:
        raise CatalogError(
            f"catalog evidence backend {evidence.backend!r} does not match requested {backend!r}"
        )
    order = EFFORT_ORDER.get(backend)
    if order is None:
        raise CatalogError(f"unsupported backend for highest-effort resolution: {backend!r}")
    if backend in {"claude", "codex"}:
        covered = set(evidence.coverage.get(model, []))
    elif backend == "cursor":
        ids = set(cursor_catalog_ids(evidence))
        covered = {
            effort
            for effort in order
            if normalize_cursor_catalog_id(model, effort) in ids
        }
    else:  # pragma: no cover - guarded above and retained for defensive clarity
        covered = set()
    if not covered:
        raise CatalogError(
            f"{backend} model {model!r} has no supported effort in the fresh catalog"
        )
    if "max" in covered:
        return "max"
    return max(covered, key=order.index)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] not in ALLOWED_COMMANDS:
        print(
            f"usage: live_catalog.py <{'|'.join(sorted(ALLOWED_COMMANDS))}>",
            file=sys.stderr,
        )
        return 2
    backend = args[0]
    try:
        evidence = produce(backend)
    except CatalogError as error:
        print(f"[live-catalog] {error}", file=sys.stderr)
        return 2
    print(json.dumps(evidence.record(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
