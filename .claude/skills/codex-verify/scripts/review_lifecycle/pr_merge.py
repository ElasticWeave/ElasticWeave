#!/usr/bin/env python3
"""Mechanically validate and merge one exact reviewed GitHub pull request."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import trusted_git as _trusted_git  # noqa: E402


SAFE_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
SAFE_NUMBER = re.compile(r"[1-9][0-9]*\Z")
SAFE_SHA = re.compile(r"[0-9a-f]{40,64}\Z")
MERGE_RECEIPT_PREFIX = (".pm", "receipts", "merge")
TRUSTED_GH_INSTALL_ROOTS = (Path("/usr/bin"), Path("/usr/local/bin"))
TRUSTED_GH_OWNER_UID = 0
REMOTE_RUNTIME_PATH = "/usr/bin:/bin"
REMOTE_CREDENTIAL_ENV = ("GH_TOKEN", "GITHUB_TOKEN")
TRUSTED_GIT_INSTALL_ROOTS = _trusted_git.TRUSTED_GIT_INSTALL_ROOTS
TRUSTED_GIT_OWNER_UID = _trusted_git.TRUSTED_GIT_OWNER_UID
GIT_RUNTIME_PATH = _trusted_git.GIT_RUNTIME_PATH
GitBindingError = _trusted_git.GitBindingError
_TestLocalGit = _trusted_git._TestLocalGit
STABLE_FILE_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


class MergeError(RuntimeError):
    pass


class _TestRemoteGh:
    """Private in-process fixture seam. The production CLI cannot set this."""

    def __init__(self, executable: Path, environment: dict[str, str]) -> None:
        self.executable = executable
        self.environment = environment


_TEST_REMOTE_GH: _TestRemoteGh | None = None
_TEST_LOCAL_GIT: _TestLocalGit | None = None


def scrubbed_git_env() -> dict[str, str]:
    return _trusted_git.scrubbed_git_env(test_git=_TEST_LOCAL_GIT)


def require_git() -> _trusted_git._TrustedGit:
    return _trusted_git.require_git(test_git=_TEST_LOCAL_GIT)


def git_run(
    git: _trusted_git._TrustedGit, argv: list[str], *, cwd: Path
) -> subprocess.CompletedProcess[str]:
    return _trusted_git.git_run(git, argv, cwd=cwd, env=scrubbed_git_env())


def run_git(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    with require_git() as git:
        return git_run(git, argv, cwd=cwd)


def _fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_uid,
        value.st_gid,
    )


class _TrustedGh:
    def __init__(
        self,
        *,
        path: Path,
        leaf: str,
        parent_descriptors: list[int],
        directory_components: tuple[str, ...],
        executable_fd: int,
        fingerprint: tuple[int, ...],
        digest: str,
        require_system_trust: bool,
    ) -> None:
        self.path = path
        self.leaf = leaf
        self.parent_descriptors = parent_descriptors
        self.directory_components = directory_components
        self.executable_fd = executable_fd
        self.fingerprint = fingerprint
        self.digest = digest
        self.require_system_trust = require_system_trust

    def __enter__(self) -> _TrustedGh:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def execution_path(self) -> str:
        return f"/proc/self/fd/{self.executable_fd}"

    def close(self) -> None:
        if self.executable_fd >= 0:
            os.close(self.executable_fd)
            self.executable_fd = -1
        _close_descriptors(self.parent_descriptors)
        self.parent_descriptors = []

    def revalidate(self) -> None:
        _revalidate_trusted_gh(self)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr", required=True)
    parser.add_argument("--approval-review-id", required=True)
    parser.add_argument("--kind", required=True, choices=("issue", "milestone"))
    parser.add_argument("--id", required=True)
    parser.add_argument("--approved-head", required=True)
    parser.add_argument("--strategy", choices=("squash", "merge", "rebase"), default="squash")
    parser.add_argument("--authorization-receipt", required=True)
    parser.add_argument("--receipt", required=True)
    return parser.parse_args()


def _os_account_home() -> Path:
    try:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError as exc:
        raise MergeError("cannot derive the current OS account home") from exc
    if not home.is_absolute():
        raise MergeError("the current OS account home is not absolute")
    return Path(os.path.abspath(os.fspath(home)))


def scrubbed_remote_env() -> dict[str, str]:
    """Build the only environment granted to trusted GitHub CLI operations."""
    home = _os_account_home()
    config_home = home / ".config"
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "GH_CONFIG_DIR": str(config_home / "gh"),
        "PATH": REMOTE_RUNTIME_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "GH_PROMPT_DISABLED": "1",
    }
    for key in REMOTE_CREDENTIAL_ENV:
        value = os.environ.get(key)
        if value:
            env[key] = value
    if _TEST_REMOTE_GH is not None:
        env.update(_TEST_REMOTE_GH.environment)
    return env


def run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": env,
        "capture_output": True,
        "text": True,
        "check": False,
    }
    if pass_fds:
        kwargs["pass_fds"] = pass_fds
    return subprocess.run(argv, **kwargs)


def require_json(result: subprocess.CompletedProcess[str], label: str) -> object:
    if result.returncode != 0:
        raise MergeError(
            f"{label} failed: {(result.stderr or result.stdout or 'no diagnostic').strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MergeError(f"{label} returned malformed JSON") from exc


def canonical_checkout(raw: str) -> Path:
    try:
        checkout = Path(raw).resolve(strict=True)
    except OSError as exc:
        raise MergeError(f"reviewed checkout is unavailable: {exc}") from exc
    work_tree, git_dir = _trusted_git.discover_repository(checkout)
    if work_tree != checkout:
        raise MergeError(f"--checkout must be the Git top level: {work_tree}")
    result = run_git(["rev-parse", "--show-toplevel"], cwd=checkout)
    if result.returncode != 0:
        raise MergeError("reviewed checkout is not a Git repository")
    top = Path(result.stdout.strip()).resolve()
    if top != checkout:
        raise MergeError(f"--checkout must be the Git top level: {top}")
    absolute = run_git(["rev-parse", "--absolute-git-dir"], cwd=checkout)
    if absolute.returncode != 0:
        raise MergeError("reviewed checkout git-dir is unreadable")
    pinned = Path(absolute.stdout.strip()).resolve()
    if pinned != git_dir.resolve():
        raise MergeError("reviewed checkout git-dir does not match the pinned git directory")
    return checkout


def canonical_github_repo(remote: str) -> str:
    value = remote.strip()
    if value.startswith("git@github.com:"):
        path = value[len("git@github.com:") :]
    elif value.startswith("ssh://git@github.com/"):
        parsed = urlparse(value)
        if (
            parsed.scheme != "ssh"
            or parsed.hostname != "github.com"
            or parsed.username != "git"
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise MergeError("origin remote is not canonical github.com")
        path = parsed.path.lstrip("/")
    elif value.startswith("https://github.com/"):
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise MergeError("origin remote is not canonical github.com")
        path = parsed.path.lstrip("/")
    else:
        raise MergeError("origin remote must be a canonical github.com SSH or HTTPS URL")
    if path.endswith(".git"):
        path = path[:-4]
    if not SAFE_REPO.fullmatch(path):
        raise MergeError("origin remote does not resolve to canonical OWNER/REPO")
    owner, name = path.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise MergeError("origin remote contains an unsafe repository segment")
    return path.lower()


def canonical_repository(raw: str) -> str:
    if not SAFE_REPO.fullmatch(raw):
        raise MergeError("--repository must be canonical OWNER/REPO")
    owner, name = raw.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."}:
        raise MergeError("--repository contains an unsafe segment")
    return raw.lower()


def local_preflight(
    checkout: Path, preflight: Path, kind: str, item_id: str, approved_head: str
) -> dict[str, object]:
    result = run(
        [
            sys.executable,
            str(preflight),
            "--kind",
            kind,
            "--id",
            item_id,
            "--approved-head",
            approved_head,
        ],
        cwd=checkout,
    )
    payload = require_json(result, "local terminal preflight")
    if not isinstance(payload, dict):
        raise MergeError("local terminal preflight returned a non-object receipt")
    if (
        payload.get("approvedHead") != approved_head
        or payload.get("itemId") != item_id
        or payload.get("itemKind") != kind
        or payload.get("terminal") != "COMPLETE"
    ):
        raise MergeError("local terminal preflight receipt disagrees with the dispatch")
    return payload


def gh_json(
    gh: _TrustedGh, argv: list[str], *, cwd: Path, env: dict[str, str]
) -> object:
    return require_json(
        gh_run(gh, argv, cwd=cwd, env=env),
        "gh " + " ".join(argv[:2]),
    )


def require_pr_view(
    value: object, approved_head: str, base_ref_name: str, base_ref_oid: str
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MergeError("PR view is not a JSON object")
    if value.get("isDraft") is not False:
        raise MergeError("PR is draft or draft state is unavailable")
    if value.get("headRefOid") != approved_head:
        raise MergeError("PR head no longer equals the approved reviewed HEAD")
    if value.get("baseRefName") != base_ref_name or value.get("baseRefOid") != base_ref_oid:
        raise MergeError("PR base name/OID no longer equals the authorized base")
    if value.get("reviewDecision") != "APPROVED":
        raise MergeError("PR overall reviewDecision is not APPROVED")
    state = value.get("state")
    if state not in ("OPEN", "MERGED"):
        raise MergeError(f"PR state {state!r} is not mergeable or recoverable")
    if state == "MERGED":
        merge_commit = value.get("mergeCommit")
        if not isinstance(merge_commit, dict) or not SAFE_SHA.fullmatch(
            str(merge_commit.get("oid", ""))
        ):
            raise MergeError("already-MERGED PR has no canonical merge commit")
    return value


def _trusted_directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise MergeError("this platform cannot safely bind a trusted gh executable")
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _trusted_file_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise MergeError("this platform cannot safely bind a trusted gh executable")
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
    )


def _require_trusted_directory(value: os.stat_result, label: str) -> None:
    if not stat.S_ISDIR(value.st_mode):
        raise MergeError(f"{label} must be a directory")
    if value.st_uid != TRUSTED_GH_OWNER_UID or value.st_gid != TRUSTED_GH_OWNER_UID:
        raise MergeError(f"{label} is not owned by the trusted system account")
    if value.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise MergeError(f"{label} is writable by a non-owner")


def _require_trusted_executable(
    value: os.stat_result, label: str, *, require_system_trust: bool
) -> None:
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise MergeError(f"{label} must be a single-link regular executable")
    if not value.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise MergeError(f"{label} is not executable")
    if value.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise MergeError(f"{label} is writable by a non-owner")
    if require_system_trust and (
        value.st_uid != TRUSTED_GH_OWNER_UID or value.st_gid != TRUSTED_GH_OWNER_UID
    ):
        raise MergeError(f"{label} is not owned by the trusted system account")


def _digest_descriptor(descriptor: int) -> str:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return "sha256:" + digest.hexdigest()
    except OSError as exc:
        raise MergeError(f"trusted gh executable cannot be hashed: {exc}") from exc


def _open_trusted_gh(path: Path, *, require_system_trust: bool) -> _TrustedGh:
    path = Path(path)
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise MergeError("trusted gh path is not a fixed absolute path")
    if not Path("/proc/self/fd").is_dir():
        raise MergeError("this platform cannot execute a verified gh descriptor")

    components = path.parts[1:]
    if not components:
        raise MergeError("trusted gh path has no executable leaf")
    descriptors: list[int] = []
    executable_fd = -1
    try:
        descriptors.append(os.open(path.anchor, _trusted_directory_flags()))
        if require_system_trust:
            _require_trusted_directory(os.fstat(descriptors[-1]), "trusted gh root")
        for component in components[:-1]:
            entry = os.stat(component, dir_fd=descriptors[-1], follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode):
                raise MergeError("trusted gh path contains a non-directory or symlink component")
            if require_system_trust:
                _require_trusted_directory(entry, "trusted gh directory")
            opened = os.open(component, _trusted_directory_flags(), dir_fd=descriptors[-1])
            opened_stat = os.fstat(opened)
            if (entry.st_dev, entry.st_ino) != (opened_stat.st_dev, opened_stat.st_ino):
                os.close(opened)
                raise MergeError("trusted gh path changed while it was being opened")
            if require_system_trust:
                _require_trusted_directory(opened_stat, "trusted gh directory")
            descriptors.append(opened)

        leaf = components[-1]
        entry = os.stat(leaf, dir_fd=descriptors[-1], follow_symlinks=False)
        _require_trusted_executable(
            entry, "trusted gh executable", require_system_trust=require_system_trust
        )
        executable_fd = os.open(leaf, _trusted_file_flags(), dir_fd=descriptors[-1])
        opened = os.fstat(executable_fd)
        _require_trusted_executable(
            opened, "trusted gh executable", require_system_trust=require_system_trust
        )
        if _fingerprint(entry) != _fingerprint(opened):
            raise MergeError("trusted gh executable changed while it was being opened")
        digest = _digest_descriptor(executable_fd)
        after_hash = os.fstat(executable_fd)
        if _fingerprint(opened) != _fingerprint(after_hash):
            raise MergeError("trusted gh executable changed while it was being hashed")
        return _TrustedGh(
            path=path,
            leaf=leaf,
            parent_descriptors=descriptors,
            directory_components=tuple(components[:-1]),
            executable_fd=executable_fd,
            fingerprint=_fingerprint(opened),
            digest=digest,
            require_system_trust=require_system_trust,
        )
    except MergeError:
        if executable_fd >= 0:
            os.close(executable_fd)
        _close_descriptors(descriptors)
        raise
    except OSError as exc:
        if executable_fd >= 0:
            os.close(executable_fd)
        _close_descriptors(descriptors)
        raise MergeError(f"trusted gh executable is unavailable: {exc}") from exc


def _revalidate_trusted_gh(gh: _TrustedGh) -> None:
    if gh.executable_fd < 0 or not gh.parent_descriptors:
        raise MergeError("trusted gh executable binding is closed")
    if len(gh.parent_descriptors) != len(gh.directory_components) + 1:
        raise MergeError("trusted gh executable binding is malformed")
    try:
        root = os.fstat(gh.parent_descriptors[0])
        if gh.require_system_trust:
            _require_trusted_directory(root, "trusted gh root")
        for index, component in enumerate(gh.directory_components):
            entry = os.stat(
                component, dir_fd=gh.parent_descriptors[index], follow_symlinks=False
            )
            opened = os.fstat(gh.parent_descriptors[index + 1])
            if (
                not stat.S_ISDIR(entry.st_mode)
                or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise MergeError("trusted gh path changed while it was in use")
            if gh.require_system_trust:
                _require_trusted_directory(entry, "trusted gh directory")
                _require_trusted_directory(opened, "trusted gh directory")

        entry = os.stat(
            gh.leaf, dir_fd=gh.parent_descriptors[-1], follow_symlinks=False
        )
        opened = os.fstat(gh.executable_fd)
        _require_trusted_executable(
            entry,
            "trusted gh executable",
            require_system_trust=gh.require_system_trust,
        )
        _require_trusted_executable(
            opened,
            "trusted gh executable",
            require_system_trust=gh.require_system_trust,
        )
        if (
            _fingerprint(entry) != gh.fingerprint
            or _fingerprint(opened) != gh.fingerprint
            or _digest_descriptor(gh.executable_fd) != gh.digest
        ):
            raise MergeError("trusted gh executable changed while it was in use")
    except MergeError:
        raise
    except OSError as exc:
        raise MergeError(f"trusted gh executable changed while it was in use: {exc}") from exc


def require_gh() -> _TrustedGh:
    if _TEST_REMOTE_GH is not None:
        return _open_trusted_gh(
            _TEST_REMOTE_GH.executable, require_system_trust=False
        )
    failures: list[str] = []
    for root in TRUSTED_GH_INSTALL_ROOTS:
        candidate = root / "gh"
        try:
            return _open_trusted_gh(candidate, require_system_trust=True)
        except MergeError as exc:
            failures.append(str(exc))
    detail = "; ".join(failures) or "no configured install roots"
    raise MergeError(f"no trusted gh executable is available: {detail}")


def gh_run(
    gh: _TrustedGh,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    gh.revalidate()
    try:
        return run(
            [gh.execution_path, *argv],
            cwd=cwd,
            env=env,
            pass_fds=(gh.executable_fd,),
        )
    finally:
        gh.revalidate()


def verify_merged_github(
    *,
    repository: str,
    pr: str | int,
    approval_review_id: str | int,
    approved_head: str,
    base_ref_name: str,
    base_ref_oid: str,
    merge_commit: str,
) -> dict[str, object]:
    """Read-only GitHub confirmation that one exact PR is already MERGED.

    Never invokes ``gh pr merge``, checks, fetch, or checkout mutation.
    """
    repository = canonical_repository(repository)
    pr_s = str(pr)
    review_s = str(approval_review_id)
    if not SAFE_NUMBER.fullmatch(pr_s) or not SAFE_NUMBER.fullmatch(review_s):
        raise MergeError("pr and approval-review-id must be positive decimal identifiers")
    if not SAFE_SHA.fullmatch(approved_head) or not SAFE_SHA.fullmatch(str(merge_commit)):
        raise MergeError("approved-head and merge-commit must be full lowercase commit SHAs")
    if not str(base_ref_name).strip() or not SAFE_SHA.fullmatch(str(base_ref_oid)):
        raise MergeError("authorized base name/OID is missing or not canonical")
    remote_env = scrubbed_remote_env()
    with tempfile.TemporaryDirectory(prefix="review-pr-verify-") as temp:
        neutral = Path(temp)
        with require_gh() as gh:
            approval_value = gh_json(
                gh,
                [
                    "api",
                    f"repos/{repository}/pulls/{pr_s}/reviews/{review_s}",
                    "--hostname",
                    "github.com",
                ],
                cwd=neutral,
                env=remote_env,
            )
            if not isinstance(approval_value, dict):
                raise MergeError("approval review is not a JSON object")
            if str(approval_value.get("id")) != review_s:
                raise MergeError("approval review id does not match the dispatch")
            if approval_value.get("state") != "APPROVED":
                raise MergeError("exact GitHub review is not APPROVED")
            if approval_value.get("commit_id") != approved_head:
                raise MergeError("exact GitHub approval is stale for the approved HEAD")
            view_value = require_pr_view(
                gh_json(
                    gh,
                    [
                        "pr",
                        "view",
                        pr_s,
                        "--repo",
                        f"github.com/{repository}",
                        "--json",
                        "state,isDraft,headRefOid,baseRefName,baseRefOid,mergeStateStatus,mergeCommit,reviewDecision",
                    ],
                    cwd=neutral,
                    env=remote_env,
                ),
                approved_head,
                str(base_ref_name),
                str(base_ref_oid),
            )
            if view_value.get("state") != "MERGED":
                raise MergeError("PR read-back is not MERGED")
            merge_oid = view_value["mergeCommit"]["oid"]
            if merge_oid != merge_commit:
                raise MergeError("PR merge commit does not match the bound merge receipt")
    return {"approval": approval_value, "view": view_value}


def _directory_flags(label: str) -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise MergeError(f"this platform cannot safely access {label} without following links")
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _merge_receipt_relative(checkout: Path, raw: str | Path, label: str) -> Path:
    requested = Path(raw)
    candidate = requested if requested.is_absolute() else checkout / requested
    try:
        lexical = Path(os.path.abspath(os.fspath(candidate)))
        relative = lexical.relative_to(checkout)
    except (OSError, ValueError) as exc:
        raise MergeError(f"{label} must be stored under .pm/receipts/merge/") from exc
    if (
        len(relative.parts) <= len(MERGE_RECEIPT_PREFIX)
        or relative.parts[: len(MERGE_RECEIPT_PREFIX)] != MERGE_RECEIPT_PREFIX
        or not relative.name
    ):
        raise MergeError(f"{label} must be stored under .pm/receipts/merge/")
    return relative


def _open_receipt_parent(
    checkout: Path, relative: Path, label: str
) -> tuple[list[int], list[str]]:
    """Open checkout and receipt parents one component at a time without following links."""
    checkout = Path(os.path.abspath(os.fspath(checkout)))
    flags = _directory_flags(label)
    components = [*checkout.parts[1:], *relative.parent.parts]
    descriptors: list[int] = []
    try:
        descriptors.append(os.open(checkout.anchor, flags))
        for component in components:
            before = os.stat(component, dir_fd=descriptors[-1], follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise MergeError(f"{label} path must contain only non-symlink directories")
            opened = os.open(component, flags, dir_fd=descriptors[-1])
            opened_stat = os.fstat(opened)
            if (before.st_dev, before.st_ino) != (opened_stat.st_dev, opened_stat.st_ino):
                os.close(opened)
                raise MergeError(f"{label} path changed while it was being opened")
            descriptors.append(opened)
        return descriptors, components
    except MergeError:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise
    except OSError as exc:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise MergeError(
            f"{label} must resolve through existing non-symlink directories: {exc}"
        ) from exc


def _validate_directory_chain(
    descriptors: list[int], components: list[str], label: str
) -> None:
    for index, component in enumerate(components):
        try:
            entry = os.stat(component, dir_fd=descriptors[index], follow_symlinks=False)
            opened = os.fstat(descriptors[index + 1])
        except OSError as exc:
            raise MergeError(f"{label} path changed while it was in use: {exc}") from exc
        if (
            not stat.S_ISDIR(entry.st_mode)
            or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise MergeError(f"{label} path changed while it was in use")


def _close_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)


def _read_bound_regular(
    parent_fd: int,
    leaf: str,
    label: str,
) -> bytes:
    file_fd = -1
    try:
        before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise MergeError(f"{label} must be a single-link regular non-symlink file")
        file_fd = os.open(
            leaf,
            os.O_RDONLY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOCTTY", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise MergeError(f"{label} changed while it was being opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        after = os.fstat(file_fd)
        final_entry = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        if (
            any(getattr(opened, field) != getattr(after, field) for field in STABLE_FILE_FIELDS)
            or any(getattr(after, field) != getattr(final_entry, field) for field in STABLE_FILE_FIELDS)
            or len(raw) != after.st_size
            or after.st_nlink != 1
        ):
            raise MergeError(f"{label} changed while it was being read")
        return raw
    except MergeError:
        raise
    except OSError as exc:
        raise MergeError(f"{label} must be a single-link regular non-symlink file: {exc}") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)


def _read_merge_receipt(checkout: Path, raw: str | Path, label: str) -> bytes:
    relative = _merge_receipt_relative(checkout, raw, label)
    descriptors, components = _open_receipt_parent(checkout, relative, label)
    try:
        value = _read_bound_regular(descriptors[-1], relative.name, label)
        _validate_directory_chain(descriptors, components, label)
        return value
    finally:
        _close_descriptors(descriptors)


def _inside_merge_receipts(checkout: Path, raw: str, label: str, *, must_exist: bool) -> Path:
    relative = _merge_receipt_relative(checkout, raw, label)
    descriptors, components = _open_receipt_parent(checkout, relative, label)
    try:
        try:
            details = os.stat(relative.name, dir_fd=descriptors[-1], follow_symlinks=False)
        except FileNotFoundError:
            if must_exist:
                raise MergeError(f"{label} is unavailable")
        else:
            if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
                raise MergeError(f"{label} must be a single-link regular non-symlink file")
        _validate_directory_chain(descriptors, components, label)
    finally:
        _close_descriptors(descriptors)
    return checkout / relative


def _authorization(
    checkout: Path,
    raw_path: str,
    *,
    kind: str,
    item_id: str,
    repository: str,
    pr: int,
    approved_head: str,
    strategy: str,
) -> tuple[dict[str, object], str]:
    try:
        raw = _read_merge_receipt(checkout, raw_path, "merge authorization receipt")
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MergeError(f"merge authorization receipt is unreadable: {exc}") from exc
    required = {
        "schema", "transactionId", "authorizationKind", "authorizationRef", "itemKind",
        "itemId", "repository", "pr", "approvedHead", "baseRefName", "baseRefOid",
        "strategy", "deleteBranch",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise MergeError("merge authorization receipt has an unsupported or ambiguous schema")
    if (
        value.get("schema") != "resolve-pr-authorization/v1"
        or value.get("authorizationKind") not in ("user", "project-policy")
        or not str(value.get("authorizationRef", "")).strip()
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", str(value.get("transactionId", "")))
        or value.get("itemKind") != kind
        or value.get("itemId") != item_id
        or str(value.get("repository", "")).lower() != repository
        or value.get("pr") != pr
        or value.get("approvedHead") != approved_head
        or value.get("strategy") != strategy
        or not isinstance(value.get("deleteBranch"), bool)
        or not str(value.get("baseRefName", "")).strip()
        or not SAFE_SHA.fullmatch(str(value.get("baseRefOid", "")))
    ):
        raise MergeError(
            "merge authorization receipt does not exactly bind target, head/base, strategy, "
            "and branch deletion"
        )
    return value, "sha256:" + hashlib.sha256(raw).hexdigest()


def _write_receipt(checkout: Path, path: Path, value: dict[str, object]) -> None:
    rendered = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    label = "durable merge receipt"
    relative = _merge_receipt_relative(checkout, path, label)
    descriptors, components = _open_receipt_parent(checkout, relative, label)
    parent_fd = descriptors[-1]
    temporary = f".review-pr-{secrets.token_hex(16)}.tmp"
    temporary_created = False
    file_fd = -1
    try:
        try:
            existing = _read_bound_regular(parent_fd, relative.name, label)
        except MergeError:
            try:
                os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            else:
                raise
        if existing is not None:
            if existing != rendered:
                raise MergeError("durable merge receipt path already contains different bytes")
            _validate_directory_chain(descriptors, components, label)
            return

        file_fd = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        temporary_created = True
        view = memoryview(rendered)
        while view:
            written = os.write(file_fd, view)
            if written <= 0:
                raise MergeError("durable merge receipt write made no progress")
            view = view[written:]
        os.fsync(file_fd)
        temporary_stat = os.fstat(file_fd)
        if not stat.S_ISREG(temporary_stat.st_mode) or temporary_stat.st_nlink != 1:
            raise MergeError("durable merge receipt temporary is not a single-link regular file")
        os.close(file_fd)
        file_fd = -1
        _validate_directory_chain(descriptors, components, label)
        try:
            os.link(
                temporary,
                relative.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            existing = _read_bound_regular(parent_fd, relative.name, label)
            if existing != rendered:
                raise MergeError("durable merge receipt path already contains different bytes")
        os.unlink(temporary, dir_fd=parent_fd)
        temporary_created = False
        os.fsync(parent_fd)
        readback = _read_bound_regular(parent_fd, relative.name, label)
        if readback != rendered:
            raise MergeError("durable merge receipt changed during idempotent read-back")
        _validate_directory_chain(descriptors, components, label)
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if temporary_created:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        _close_descriptors(descriptors)


def gh_checks(
    gh: _TrustedGh, argv: list[str], *, cwd: Path, env: dict[str, str]
) -> dict[str, object]:
    """Require GitHub CLI's aggregate PR-check decision and retain a normalized snapshot."""
    result = gh_run(gh, argv, cwd=cwd, env=env)
    output = "\n".join(line.rstrip() for line in result.stdout.splitlines()).strip()
    if result.returncode != 0:
        detail = (result.stderr or output or "no diagnostic").strip()
        raise MergeError(f"PR checks are failing, pending, or unavailable: {detail}")
    if not output:
        raise MergeError("PR checks returned success without a check snapshot")
    return {"exitCode": result.returncode, "stdout": output}


def main() -> int:
    args = parse_args()
    try:
        repository = canonical_repository(args.repository)
        if not SAFE_NUMBER.fullmatch(args.pr) or not SAFE_NUMBER.fullmatch(args.approval_review_id):
            raise MergeError("--pr and --approval-review-id must be positive decimal identifiers")
        if not SAFE_SHA.fullmatch(args.approved_head):
            raise MergeError("--approved-head must be a full lowercase commit SHA")
        checkout = canonical_checkout(args.checkout)
        authorization, authorization_sha256 = _authorization(
            checkout,
            args.authorization_receipt,
            kind=args.kind,
            item_id=args.id,
            repository=repository,
            pr=int(args.pr),
            approved_head=args.approved_head,
            strategy=args.strategy,
        )
        receipt_path = _inside_merge_receipts(
            checkout, args.receipt, "durable merge receipt", must_exist=False
        )
        origin = _trusted_git.read_local_origin_url(cwd=checkout, test_git=_TEST_LOCAL_GIT)
        if origin.work_tree != checkout:
            raise MergeError(
                f"--checkout must be the Git top level: {origin.work_tree}"
            )
        remote_repo = canonical_github_repo(origin.url)
        if remote_repo != repository:
            raise MergeError(
                f"repository does not match reviewed checkout origin remote: {remote_repo}"
            )

        preflight = Path(__file__).resolve().with_name("pr_preflight.py")
        before_receipt = local_preflight(
            checkout, preflight, args.kind, args.id, args.approved_head
        )
        remote_env = scrubbed_remote_env()
        with require_gh() as gh:
            with tempfile.TemporaryDirectory(prefix="review-pr-") as temp:
                neutral = Path(temp)
                remote_repo = f"github.com/{repository}"
                approval_args = [
                    "api",
                    f"repos/{repository}/pulls/{args.pr}/reviews/{args.approval_review_id}",
                    "--hostname",
                    "github.com",
                ]
                view_args = [
                    "pr",
                    "view",
                    args.pr,
                    "--repo",
                    remote_repo,
                    "--json",
                    "state,isDraft,headRefOid,baseRefName,baseRefOid,mergeStateStatus,mergeCommit,reviewDecision",
                ]
                checks_args = [
                    "pr",
                    "checks",
                    args.pr,
                    "--repo",
                    remote_repo,
                ]

                def inspect_remote() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
                    approval_value = gh_json(
                        gh, approval_args, cwd=neutral, env=remote_env
                    )
                    if not isinstance(approval_value, dict):
                        raise MergeError("approval review is not a JSON object")
                    if str(approval_value.get("id")) != args.approval_review_id:
                        raise MergeError("approval review id does not match the dispatch")
                    if approval_value.get("state") != "APPROVED":
                        raise MergeError("exact GitHub review is not APPROVED")
                    if approval_value.get("commit_id") != args.approved_head:
                        raise MergeError("exact GitHub approval is stale for the approved HEAD")
                    view_value = require_pr_view(
                        gh_json(gh, view_args, cwd=neutral, env=remote_env),
                        args.approved_head,
                        str(authorization["baseRefName"]),
                        str(authorization["baseRefOid"]),
                    )
                    checks_value = gh_checks(
                        gh, checks_args, cwd=neutral, env=remote_env
                    )
                    return approval_value, view_value, checks_value

                approval, view, checks = inspect_remote()
                if view["state"] == "OPEN":
                    # Close the inspection-to-merge window as far as the API permits: immediately
                    # re-read approval, HEAD/decision, and checks before match-head merge.
                    approval, view, checks = inspect_remote()
                    if view["state"] != "OPEN":
                        raise MergeError("PR state changed before the authorized merge")
                    strategy = {
                        "squash": "--squash",
                        "merge": "--merge",
                        "rebase": "--rebase",
                    }[args.strategy]
                    merge_args = [
                        "pr",
                        "merge",
                        args.pr,
                        "--repo",
                        remote_repo,
                        strategy,
                        "--match-head-commit",
                        args.approved_head,
                    ]
                    if authorization["deleteBranch"]:
                        merge_args.append("--delete-branch")
                    merged = gh_run(gh, merge_args, cwd=neutral, env=remote_env)
                    if merged.returncode != 0:
                        raise MergeError(
                            f"atomic PR merge failed: {(merged.stderr or merged.stdout).strip()}"
                        )
                final_approval, after_view, final_checks = inspect_remote()
                if after_view["state"] != "MERGED":
                    raise MergeError("post-merge PR read-back is not MERGED")

        fetched = _trusted_git.fetch_canonical_origin(
            origin,
            cwd=checkout,
            env=scrubbed_git_env(),
            test_git=_TEST_LOCAL_GIT,
        )
        if fetched.returncode != 0:
            raise MergeError(f"post-merge fetch failed: {(fetched.stderr or fetched.stdout).strip()}")
        after_receipt = local_preflight(
            checkout, preflight, args.kind, args.id, args.approved_head
        )
        for field in ("artifact", "approvedHead", "commitment", "itemId", "itemKind", "reviewPolicy"):
            if before_receipt.get(field) != after_receipt.get(field):
                raise MergeError(f"post-merge local preflight changed {field}")
    except (MergeError, GitBindingError, OSError) as exc:
        sys.stderr.write(f"[review-pr] REFUSED: {exc}\n")
        return 2

    merge_commit = str(after_view["mergeCommit"]["oid"])
    artifact_path = checkout / str(after_receipt["artifact"])
    if not artifact_path.is_file() or artifact_path.is_symlink():
        sys.stderr.write("[review-pr] REFUSED: terminal artifact is unavailable for receipt\n")
        return 2
    receipt = {
        "schema": "resolve-pr-merge-receipt/v1",
        "transactionId": authorization["transactionId"],
        "authorizationReceiptSha256": authorization_sha256,
        "itemKind": args.kind,
        "itemId": args.id,
        "repository": repository,
        "pr": int(args.pr),
        "approvedHead": args.approved_head,
        "baseRefName": authorization["baseRefName"],
        "baseRefOid": authorization["baseRefOid"],
        "strategy": args.strategy,
        "deleteBranch": authorization["deleteBranch"],
        "terminalArtifact": str(after_receipt["artifact"]),
        "terminalArtifactSha256": "sha256:" + hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
        "terminalCommitment": str(after_receipt["commitment"]),
        "approvalReviewId": args.approval_review_id,
        "headRefOid": str(after_view["headRefOid"]),
        "mergeCommit": merge_commit,
        "state": "MERGED",
    }
    try:
        _write_receipt(checkout, receipt_path, receipt)
    except (MergeError, GitBindingError, OSError) as exc:
        sys.stderr.write(f"[review-pr] REFUSED: merged but durable receipt failed: {exc}\n")
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
