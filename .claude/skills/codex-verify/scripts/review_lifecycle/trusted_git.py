#!/usr/bin/env python3
"""Fixed system Git binding for the review-owned PR lifecycle."""

from __future__ import annotations

import hashlib
import os
import pwd
import stat
import subprocess
from pathlib import Path
from typing import NamedTuple, Sequence


TRUSTED_GIT_INSTALL_ROOTS = (
    Path("/usr/bin"),
    Path("/usr/local/bin"),
    Path("/usr/lib/git-core"),
)
TRUSTED_GIT_OWNER_UID = 0
GIT_RUNTIME_PATH = "/usr/bin:/bin"
GIT_SAFETY_OPTIONS = (
    "--no-pager",
    "--no-optional-locks",
    "--literal-pathspecs",
    "--no-replace-objects",
    "-c",
    "core.bare=false",
    "-c",
    "core.fileMode=true",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.gitProxy=",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.ignoreCase=false",
    "-c",
    "core.replaceRefs=false",
    "-c",
    "core.sshCommand=",
    "-c",
    "core.symlinks=true",
    "-c",
    "core.untrackedCache=false",
    "-c",
    "diff.external=",
    "-c",
    "protocol.ext.allow=never",
)
FETCH_SAFETY_OPTIONS = (
    "-c",
    "credential.helper=",
    "-c",
    "http.proxy=",
    "-c",
    "https.proxy=",
    "-c",
    "fetch.followTags=false",
    "-c",
    "fetch.recurseSubmodules=false",
    "-c",
    "remote.origin.tagOpt=--no-tags",
    "-c",
    "protocol.file.allow=never",
    "-c",
    "protocol.git.allow=never",
)
SAFE_ORIGIN_FETCH_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"
UNSAFE_LOCAL_CONFIG_KEYS = {
    "core.alternaterefscommand": "alternateRefsCommand",
    "core.gitproxy": "gitProxy",
    "extensions.worktreeconfig": "worktreeConfig",
    "include.path": "include/includeIf",
    "remote.origin.mirror": "origin mirror",
    "remote.origin.proxy": "origin proxy",
    "remote.origin.receivepack": "origin receivepack",
    "remote.origin.uploadpack": "origin uploadpack",
    "remote.origin.vcs": "remote helper",
}


class GitBindingError(RuntimeError):
    pass


class LocalGitOrigin(NamedTuple):
    url: str
    work_tree: Path
    git_dir: Path
    config_digest: str


class _TestLocalGit:
    """Private in-process fixture seam. The production CLI cannot set this."""

    def __init__(self, executable: Path, environment: dict[str, str]) -> None:
        self.executable = executable
        self.environment = environment


_TEST_LOCAL_GIT: _TestLocalGit | None = None


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


def _close_descriptors(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        os.close(descriptor)


class _TrustedGit:
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

    def __enter__(self) -> _TrustedGit:
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
        _revalidate_trusted_git(self)


def _os_account_home() -> Path:
    try:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError as exc:
        raise GitBindingError("cannot derive the current OS account home") from exc
    if not home.is_absolute():
        raise GitBindingError("the current OS account home is not absolute")
    return Path(os.path.abspath(os.fspath(home)))


def scrubbed_git_env(*, test_git: _TestLocalGit | None = None) -> dict[str, str]:
    """Build the only environment granted to trusted local Git operations."""
    home = _os_account_home()
    env = {
        "PATH": GIT_RUNTIME_PATH,
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_GRAFT_FILE": os.devnull,
        "GIT_PAGER": "cat",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
    ssh_auth_sock = os.environ.get("SSH_AUTH_SOCK")
    if ssh_auth_sock:
        env["SSH_AUTH_SOCK"] = ssh_auth_sock
    fixture = _TEST_LOCAL_GIT if test_git is None else test_git
    if fixture is not None:
        env.update(fixture.environment)
    return env


def _trusted_directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise GitBindingError("this platform cannot safely bind a trusted git executable")
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _trusted_file_flags() -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise GitBindingError("this platform cannot safely bind a trusted git executable")
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
    )


def _require_trusted_directory(value: os.stat_result, label: str) -> None:
    if not stat.S_ISDIR(value.st_mode):
        raise GitBindingError(f"{label} must be a directory")
    if value.st_uid != TRUSTED_GIT_OWNER_UID or value.st_gid != TRUSTED_GIT_OWNER_UID:
        raise GitBindingError(f"{label} is not owned by the trusted system account")
    if value.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise GitBindingError(f"{label} is writable by a non-owner")


def _require_trusted_executable(
    value: os.stat_result, label: str, *, require_system_trust: bool
) -> None:
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise GitBindingError(f"{label} must be a single-link regular executable")
    if not value.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise GitBindingError(f"{label} is not executable")
    if value.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise GitBindingError(f"{label} is writable by a non-owner")
    if require_system_trust and (
        value.st_uid != TRUSTED_GIT_OWNER_UID or value.st_gid != TRUSTED_GIT_OWNER_UID
    ):
        raise GitBindingError(f"{label} is not owned by the trusted system account")


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
        raise GitBindingError(f"trusted git executable cannot be hashed: {exc}") from exc


def _open_trusted_git(path: Path, *, require_system_trust: bool) -> _TrustedGit:
    path = Path(path)
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise GitBindingError("trusted git path is not a fixed absolute path")
    if not Path("/proc/self/fd").is_dir():
        raise GitBindingError("this platform cannot execute a verified git descriptor")

    components = path.parts[1:]
    if not components:
        raise GitBindingError("trusted git path has no executable leaf")
    descriptors: list[int] = []
    executable_fd = -1
    try:
        descriptors.append(os.open(path.anchor, _trusted_directory_flags()))
        if require_system_trust:
            _require_trusted_directory(os.fstat(descriptors[-1]), "trusted git root")
        for component in components[:-1]:
            entry = os.stat(component, dir_fd=descriptors[-1], follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode):
                raise GitBindingError("trusted git path contains a non-directory or symlink component")
            if require_system_trust:
                _require_trusted_directory(entry, "trusted git directory")
            opened = os.open(component, _trusted_directory_flags(), dir_fd=descriptors[-1])
            opened_stat = os.fstat(opened)
            if (entry.st_dev, entry.st_ino) != (opened_stat.st_dev, opened_stat.st_ino):
                os.close(opened)
                raise GitBindingError("trusted git path changed while it was being opened")
            if require_system_trust:
                _require_trusted_directory(opened_stat, "trusted git directory")
            descriptors.append(opened)

        leaf = components[-1]
        entry = os.stat(leaf, dir_fd=descriptors[-1], follow_symlinks=False)
        _require_trusted_executable(
            entry, "trusted git executable", require_system_trust=require_system_trust
        )
        executable_fd = os.open(leaf, _trusted_file_flags(), dir_fd=descriptors[-1])
        opened = os.fstat(executable_fd)
        _require_trusted_executable(
            opened, "trusted git executable", require_system_trust=require_system_trust
        )
        if _fingerprint(entry) != _fingerprint(opened):
            raise GitBindingError("trusted git executable changed while it was being opened")
        digest = _digest_descriptor(executable_fd)
        after_hash = os.fstat(executable_fd)
        if _fingerprint(opened) != _fingerprint(after_hash):
            raise GitBindingError("trusted git executable changed while it was being hashed")
        return _TrustedGit(
            path=path,
            leaf=leaf,
            parent_descriptors=descriptors,
            directory_components=tuple(components[:-1]),
            executable_fd=executable_fd,
            fingerprint=_fingerprint(opened),
            digest=digest,
            require_system_trust=require_system_trust,
        )
    except GitBindingError:
        if executable_fd >= 0:
            os.close(executable_fd)
        _close_descriptors(descriptors)
        raise
    except OSError as exc:
        if executable_fd >= 0:
            os.close(executable_fd)
        _close_descriptors(descriptors)
        raise GitBindingError(f"trusted git executable is unavailable: {exc}") from exc


def _revalidate_trusted_git(git: _TrustedGit) -> None:
    if git.executable_fd < 0 or not git.parent_descriptors:
        raise GitBindingError("trusted git executable binding is closed")
    if len(git.parent_descriptors) != len(git.directory_components) + 1:
        raise GitBindingError("trusted git executable binding is malformed")
    try:
        root = os.fstat(git.parent_descriptors[0])
        if git.require_system_trust:
            _require_trusted_directory(root, "trusted git root")
        for index, component in enumerate(git.directory_components):
            entry = os.stat(
                component, dir_fd=git.parent_descriptors[index], follow_symlinks=False
            )
            opened = os.fstat(git.parent_descriptors[index + 1])
            if (
                not stat.S_ISDIR(entry.st_mode)
                or (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise GitBindingError("trusted git path changed while it was in use")
            if git.require_system_trust:
                _require_trusted_directory(entry, "trusted git directory")
                _require_trusted_directory(opened, "trusted git directory")

        entry = os.stat(
            git.leaf, dir_fd=git.parent_descriptors[-1], follow_symlinks=False
        )
        opened = os.fstat(git.executable_fd)
        _require_trusted_executable(
            entry,
            "trusted git executable",
            require_system_trust=git.require_system_trust,
        )
        _require_trusted_executable(
            opened,
            "trusted git executable",
            require_system_trust=git.require_system_trust,
        )
        if (
            _fingerprint(entry) != git.fingerprint
            or _fingerprint(opened) != git.fingerprint
            or _digest_descriptor(git.executable_fd) != git.digest
        ):
            raise GitBindingError("trusted git executable changed while it was in use")
    except GitBindingError:
        raise
    except OSError as exc:
        raise GitBindingError(f"trusted git executable changed while it was in use: {exc}") from exc


def require_git(*, test_git: _TestLocalGit | None = None) -> _TrustedGit:
    fixture = _TEST_LOCAL_GIT if test_git is None else test_git
    if fixture is not None:
        return _open_trusted_git(fixture.executable, require_system_trust=False)
    failures: list[str] = []
    for root in TRUSTED_GIT_INSTALL_ROOTS:
        candidate = root / "git"
        try:
            return _open_trusted_git(candidate, require_system_trust=True)
        except GitBindingError as exc:
            failures.append(str(exc))
    detail = "; ".join(failures) or "no configured install roots"
    raise GitBindingError(f"no trusted git executable is available: {detail}")


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as extra:
        raise GitBindingError(f"{label} is unavailable: {extra}") from extra


def _require_regular_file(path: Path, label: str) -> os.stat_result:
    value = _lstat(path, label)
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise GitBindingError(f"{label} must be a single-link regular non-symlink file")
    return value


def _config_digest(path: Path) -> str:
    _require_regular_file(path, "reviewed git config")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _refuse_git_dir_redirection(git_dir: Path) -> None:
    for name, reason in (
        ("commondir", "commondir"),
        ("config.worktree", "config.worktree"),
    ):
        if os.path.lexists(git_dir / name):
            raise GitBindingError(
                f"reviewed checkout git-dir uses redirected {reason} metadata"
            )
    _require_regular_file(git_dir / "config", "reviewed git config")


def discover_repository(start: Path) -> tuple[Path, Path]:
    """Locate the real git-dir/work-tree from the filesystem, ignoring core.worktree."""
    try:
        current = Path(start).resolve(strict=True)
    except OSError as extra:
        raise GitBindingError(f"git working directory is unavailable: {extra}") from extra
    if not current.is_dir():
        raise GitBindingError("git working directory is not a directory")
    for directory in (current, *current.parents):
        git_path = directory / ".git"
        try:
            value = os.lstat(git_path)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(value.st_mode):
            raise GitBindingError("reviewed checkout .git must not be a symlink")
        if not stat.S_ISDIR(value.st_mode):
            raise GitBindingError("reviewed checkout .git must be a directory")
        git_dir = Path(os.path.abspath(os.fspath(git_path)))
        _refuse_git_dir_redirection(git_dir)
        return directory, git_dir
    raise GitBindingError("git working directory is not a Git repository")


def _unsafe_local_config_reason(key: str) -> str | None:
    lowered = key.lower()
    if lowered == "include.path" or lowered.startswith("includeif."):
        return "include/includeIf"
    if lowered.endswith(".insteadof") or lowered.endswith(".pushinsteadof"):
        return "URL rewrite (insteadOf/pushInsteadOf)"
    return UNSAFE_LOCAL_CONFIG_KEYS.get(lowered)


def _parse_config_list(raw: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for item in raw.split("\0"):
        if not item:
            continue
        key, separator, value = item.partition("\n")
        if not separator or not key:
            raise GitBindingError("reviewed git config list is malformed")
        entries.append((key, value))
    return entries


def inspect_local_git_config(
    git: _TrustedGit,
    *,
    work_tree: Path,
    git_dir: Path,
    env: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Read origin from the primary config file only; refuse includes and URL rewrites."""
    config_path = git_dir / "config"
    digest = _config_digest(config_path)
    listed = git_run(
        git,
        ["config", "--file", str(config_path), "--null", "--list"],
        cwd=work_tree,
        env=env,
    )
    if listed.returncode != 0:
        detail = (listed.stderr or listed.stdout or "no diagnostic").strip()
        raise GitBindingError(f"reviewed git config is unreadable: {detail}")
    origin_urls: list[str] = []
    for key, value in _parse_config_list(listed.stdout):
        reason = _unsafe_local_config_reason(key)
        if reason is not None:
            raise GitBindingError(
                f"local git config {reason} redirection is refused before any remote call"
            )
        if key.lower() == "remote.origin.url":
            origin_urls.append(value)
    if len(origin_urls) != 1 or not origin_urls[0].strip():
        raise GitBindingError("reviewed checkout must have exactly one origin URL")
    if _config_digest(config_path) != digest:
        raise GitBindingError("reviewed git config changed while it was being read")
    return origin_urls[0].strip(), digest


def read_local_origin_url(
    *, cwd: Path, test_git: _TestLocalGit | None = None
) -> LocalGitOrigin:
    work_tree, git_dir = discover_repository(cwd)
    with require_git(test_git=test_git) as git:
        url, digest = inspect_local_git_config(
            git,
            work_tree=work_tree,
            git_dir=git_dir,
            env=scrubbed_git_env(test_git=test_git),
        )
    return LocalGitOrigin(
        url=url, work_tree=work_tree, git_dir=git_dir, config_digest=digest
    )


def fetch_canonical_origin(
    origin: LocalGitOrigin,
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    test_git: _TestLocalGit | None = None,
) -> subprocess.CompletedProcess[str]:
    work_tree, git_dir = discover_repository(cwd)
    if work_tree != origin.work_tree or git_dir != origin.git_dir:
        raise GitBindingError("pinned git-dir/work-tree changed before fetch")
    extra = list(FETCH_SAFETY_OPTIONS)
    if origin.url.startswith("https://github.com/"):
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            extra.extend(["-c", f"http.extraHeader=Authorization: Bearer {token}"])
    with require_git(test_git=test_git) as git:
        url, digest = inspect_local_git_config(
            git, work_tree=work_tree, git_dir=git_dir, env=env
        )
        if url != origin.url or digest != origin.config_digest:
            raise GitBindingError("reviewed origin/config changed before fetch")
        return git_run(
            git,
            ["fetch", "--prune", "--no-tags", "--", origin.url, SAFE_ORIGIN_FETCH_REFSPEC],
            cwd=work_tree,
            env=env,
            extra_globals=tuple(extra),
        )


def git_run(
    git: _TrustedGit,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    extra_globals: Sequence[str] = (),
) -> subprocess.CompletedProcess[str]:
    git.revalidate()
    try:
        try:
            worktree = Path(cwd).resolve(strict=True)
        except OSError as extra:
            raise GitBindingError(f"git working directory is unavailable: {extra}") from extra
        work_tree, git_dir = discover_repository(worktree)
        return subprocess.run(
            [
                git.execution_path,
                *GIT_SAFETY_OPTIONS,
                *extra_globals,
                f"--git-dir={git_dir}",
                f"--work-tree={work_tree}",
                *argv,
            ],
            cwd=work_tree,
            env=env if env is not None else scrubbed_git_env(),
            capture_output=True,
            text=True,
            check=False,
            pass_fds=(git.executable_fd,),
            stdin=subprocess.DEVNULL,
        )
    except OSError as extra:
        raise GitBindingError(f"trusted git provenance lookup failed: {extra}") from extra
    finally:
        git.revalidate()


def run_git(argv: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    with require_git() as git:
        return git_run(git, argv, cwd=cwd)
