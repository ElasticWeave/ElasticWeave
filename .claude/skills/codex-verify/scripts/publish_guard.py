#!/usr/bin/env python3
"""Prepare and verify an explicitly authorized management-artifact publication.

This helper never commits, pushes, or opens a PR.  It stages one exact, allowlisted batch and
emits a receipt that binds the authorization, previous HEAD, and staged bytes.  Callers must
re-verify the receipt immediately before committing and again before any external action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


SCHEMA = "publish-management-artifacts/v1"
ACTIONS = ("commit-only", "push", "open-pr")
ROOT_FILES = {"plan.md", "bitlesson.md", "constraint.md", "resources.md"}
ALLOWED_DIRS = (".memory/", "templates/")
PM_DENIED = (".pm/verdicts/", ".pm/reviews/", ".pm/proofs/", ".pm/workflow-reviews/")
MANAGEMENT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".csv", ".done"}
_SYSTEM_GIT_DIRS = (
    Path("/usr/bin"),
    Path("/usr/local/bin"),
    Path("/usr/lib/git-core"),
    Path("/bin"),
)
_SYSTEM_HELPER_PATH = ("/usr/bin", "/bin")
_GIT_SAFETY_OPTIONS = (
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
    "core.splitIndex=false",
    "-c",
    "core.attributesFile=/dev/null",
    "-c",
    "diff.external=",
    "-c",
    "filter.lfs.clean=",
    "-c",
    "filter.lfs.smudge=",
    "-c",
    "filter.lfs.process=",
    "-c",
    "protocol.ext.allow=never",
)
_EXECUTABLE_STABLE_FIELDS = (
    "st_dev",
    "st_ino",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)
_TRUSTED_GIT: tuple[Path, str] | None = None
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.I),
    re.compile(rb"(?:xox[a-z]-)[A-Za-z0-9-]{16,}", re.I),
    re.compile(rb"(?:npm_[A-Za-z0-9]{20,}|//registry\.npmjs\.org/:_authToken=\S+)"),
    re.compile(rb"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(rb"(?:lin_api_|gh[pousr]_|github_pat_|sk-(?:ant-)?)[A-Za-z0-9_-]{16,}", re.I),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"Authorization:\s*Bearer\s+\S+", re.I),
    re.compile(rb"(?:api[_ -]?key|access[_ -]?token|secret|password|passwd|credential)\s*[:=]\s*\S{12,}", re.I),
    re.compile(rb"https?://[^\s/:]+:[^\s/@]+@", re.I),
)


class GuardError(RuntimeError):
    pass


def _os_account_home() -> Path:
    try:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError as exc:
        raise GuardError("cannot derive the current OS account home") from exc
    if not home.is_absolute():
        raise GuardError("the current OS account home is not absolute")
    return Path(os.path.abspath(os.fspath(home)))


def _git_environment() -> dict[str, str]:
    """Rebuild git's environment so caller PATH, HOME, and GIT_* selectors do not survive."""
    return {
        "PATH": os.pathsep.join(_SYSTEM_HELPER_PATH),
        "HOME": str(_os_account_home()),
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


def _bind_native_executable_bytes(path: Path, label: str) -> tuple[Path, str]:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_absolute():
            raise GuardError(f"{label} must resolve to an absolute path")
        leaf = os.lstat(resolved)
        if stat.S_ISLNK(leaf.st_mode):
            raise GuardError(f"{label} must not be a symlink")
        if not stat.S_ISREG(leaf.st_mode):
            raise GuardError(f"{label} must be a regular file")
        if not leaf.st_mode & 0o111:
            raise GuardError(f"{label} must be executable")
        if leaf.st_mode & 0o022:
            raise GuardError(f"{label} must not be group- or world-writable")
        if os.access(resolved, os.W_OK):
            raise GuardError(f"{label} must not be user-writable")
        if not os.access(resolved, os.X_OK):
            raise GuardError(f"{label} must be executable")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(resolved, flags)
        try:
            before = os.fstat(descriptor)
            if (
                (before.st_dev, before.st_ino) != (leaf.st_dev, leaf.st_ino)
                or not stat.S_ISREG(before.st_mode)
                or before.st_mode != leaf.st_mode
            ):
                raise GuardError(f"{label} changed while it was being opened")
            digest = hashlib.sha256()
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except GuardError:
        raise
    except OSError as error:
        raise GuardError(f"cannot bind {label}: {path}: {error}") from error
    if any(
        getattr(before, field) != getattr(after, field)
        for field in _EXECUTABLE_STABLE_FIELDS
    ):
        raise GuardError(f"{label} changed while it was being bound")
    return resolved, f"sha256:{digest.hexdigest()}"


def _bind_trusted_git() -> tuple[Path, str]:
    """Bind git from fixed system directories, never from the caller PATH.

    The binding is remembered for the process and rechecked on every call. A swapped,
    rebuilt, or earlier-directory-shadowed git fails closed instead of inspecting a
    different checkout than the one already authorized.
    """
    global _TRUSTED_GIT
    for directory in _SYSTEM_GIT_DIRS:
        candidate = directory / "git"
        if not os.path.lexists(candidate):
            continue
        binding = _bind_native_executable_bytes(candidate, "trusted git executable")
        if _TRUSTED_GIT is not None and _TRUSTED_GIT != binding:
            raise GuardError(
                "trusted git executable drift: the bound git path or digest changed "
                "during publication"
            )
        _TRUSTED_GIT = binding
        return binding
    locations = ", ".join(str(value) for value in _SYSTEM_GIT_DIRS)
    raise GuardError(f"no trusted system git executable is installed in: {locations}")


def _repository_layout(repo: Path) -> tuple[Path, Path]:
    """Locate git-dir/work-tree from the filesystem, ignoring caller GIT_* redirects."""
    try:
        work_tree = Path(repo).resolve(strict=True)
    except OSError as extra:
        raise GuardError(f"git working directory is unavailable: {extra}") from extra
    if not work_tree.is_dir():
        raise GuardError("--repo is not a git worktree")
    git_path = work_tree / ".git"
    try:
        meta = os.lstat(git_path)
    except FileNotFoundError:
        raise GuardError("--repo must be the exact Git toplevel")
    if stat.S_ISLNK(meta.st_mode):
        raise GuardError("repository .git must not be a symlink")
    if not stat.S_ISDIR(meta.st_mode):
        raise GuardError("repository .git must be a directory")
    git_dir = Path(os.path.abspath(os.fspath(git_path)))
    for name, reason in (
        ("commondir", "commondir"),
        ("config.worktree", "config.worktree"),
    ):
        if os.path.lexists(git_dir / name):
            raise GuardError(f"repository git-dir uses redirected {reason} metadata")
    return work_tree, git_dir


def git(
    repo: Path,
    *args: str,
    check: bool = True,
    text: bool = False,
    input_bytes: bytes | None = None,
):
    executable, _ = _bind_trusted_git()
    work_tree, git_dir = _repository_layout(repo)
    if input_bytes is not None:
        if text:
            raise GuardError("refusing to decode git stdin as text")
        if not isinstance(input_bytes, (bytes, bytearray)):
            raise GuardError("git stdin must be bytes")
    run_kwargs = {
        "cwd": work_tree,
        "env": _git_environment(),
        "capture_output": True,
        "text": text,
        "check": False,
    }
    if input_bytes is None:
        run_kwargs["stdin"] = subprocess.DEVNULL
    else:
        run_kwargs["input"] = bytes(input_bytes)
    try:
        cp = subprocess.run(
            [
                str(executable),
                *_GIT_SAFETY_OPTIONS,
                f"--git-dir={git_dir}",
                f"--work-tree={work_tree}",
                *args,
            ],
            **run_kwargs,
        )
    except OSError as extra:
        raise GuardError(f"trusted git provenance lookup failed: {extra}") from extra
    finally:
        _bind_trusted_git()
    if check and cp.returncode:
        detail = (cp.stderr if text else cp.stderr.decode(errors="replace")).strip()
        raise GuardError(f"git {' '.join(args)} failed: {detail}")
    return cp


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(data) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def normalize_path(raw: str) -> str:
    if not raw or "\0" in raw:
        raise GuardError("empty or NUL-containing path")
    posix = raw.replace("\\", "/")
    p = PurePosixPath(posix)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise GuardError(f"path must be a normalized repository-relative path: {raw!r}")
    return p.as_posix()


def allowed(path: str) -> bool:
    if PurePosixPath(path).suffix.lower() not in MANAGEMENT_SUFFIXES:
        return False
    if path in ROOT_FILES:
        return True
    if path.startswith(PM_DENIED):
        return False
    return path.startswith(".pm/") or path.startswith(ALLOWED_DIRS)


def validate_target(repo: Path, path: str) -> None:
    if not allowed(path):
        raise GuardError(f"path is outside the management-artifact allowlist or is a source file: {path}")
    target = repo / path
    parent = target.parent
    while parent != repo:
        if parent.is_symlink():
            raise GuardError(f"symlinked parent is forbidden: {path}")
        parent = parent.parent
    if target.is_symlink():
        raise GuardError(f"symlink target is forbidden: {path}")
    if target.exists() and not target.is_file():
        raise GuardError(f"target must be a regular file or a tracked deletion: {path}")


def current_branch(repo: Path) -> str:
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", text=True).stdout.strip()
    if not branch:
        raise GuardError("detached HEAD is not publishable")
    return branch


def require_repo_root(repo: Path) -> None:
    root = git(repo, "rev-parse", "--show-toplevel", text=True).stdout.strip()
    if not root or Path(root).resolve() != repo:
        raise GuardError("--repo must be the exact Git toplevel")


def assert_no_secret(data: bytes, label: str) -> None:
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        raise GuardError(f"{label} failed the secret gate")


def safe_regular_bytes(raw: str, label: str) -> tuple[Path, bytes]:
    original = Path(raw).expanduser().absolute()
    if original.is_symlink():
        raise GuardError(f"{label} must be a regular non-symlink file")
    try:
        path = original.resolve(strict=True)
    except OSError as exc:
        raise GuardError(f"{label} is unavailable: {exc}") from exc
    if not path.is_file():
        raise GuardError(f"{label} must be a regular non-symlink file")
    return path, path.read_bytes()


def validate_receipt_target(raw: str, repo: Path) -> Path:
    path = Path(raw).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise GuardError("receipt path must be fresh and must not be a symlink")
    parent = path.parent
    try:
        canonical_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise GuardError(f"receipt parent is unavailable: {exc}") from exc
    if canonical_parent != parent or not parent.is_dir():
        raise GuardError("receipt parent must be an exact non-symlink directory")
    candidate = canonical_parent / path.name
    if candidate.is_relative_to(repo):
        raise GuardError("receipt must be outside the repository and staged set")
    return candidate


def remote_url(repo: Path, remote: str) -> str:
    result = git(repo, "remote", "get-url", remote, text=True)
    return result.stdout.strip()


def index_is_empty(repo: Path) -> bool:
    cp = git(repo, "diff", "--cached", "--quiet", "--exit-code", check=False)
    if cp.returncode not in (0, 1):
        raise GuardError("unable to inspect git index")
    return cp.returncode == 0


def _shared_index_names(git_dir: Path) -> list[str]:
    try:
        names = os.listdir(git_dir)
    except OSError as extra:
        raise GuardError(f"cannot inspect git-dir index metadata: {extra}") from extra
    return sorted(name for name in names if name.startswith("sharedindex."))


def revalidate_live_index(repo: Path) -> None:
    """Bind the live index as a regular, non-symlink, non-hardlinked file.

    Split/shared index metadata can redirect writes, so it is refused rather than followed.
    """
    _, git_dir = _repository_layout(repo)
    if _shared_index_names(git_dir):
        raise GuardError("repository git-dir uses redirected shared index metadata")
    index = git_dir / "index"
    try:
        meta = os.lstat(index)
    except FileNotFoundError as extra:
        raise GuardError("git index is missing") from extra
    except OSError as extra:
        raise GuardError(f"cannot inspect git index: {extra}") from extra
    if stat.S_ISLNK(meta.st_mode):
        raise GuardError("git index must not be a symlink")
    if not stat.S_ISREG(meta.st_mode):
        raise GuardError("git index must be a regular file")
    if meta.st_nlink != 1:
        raise GuardError("git index must not be hardlinked")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(index, flags)
    except OSError as extra:
        raise GuardError(f"cannot bind git index: {extra}") from extra
    try:
        opened = os.fstat(descriptor)
        if (
            (opened.st_dev, opened.st_ino) != (meta.st_dev, meta.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
        ):
            raise GuardError("git index changed while it was being bound")
    finally:
        os.close(descriptor)


def _read_worktree_regular_file(repo: Path, path: str) -> tuple[bytes, str] | None:
    target = repo / path
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(target, flags)
    except FileNotFoundError:
        return None
    except OSError as extra:
        raise GuardError(f"cannot read {path}: {extra}") from extra
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GuardError(f"target must be a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
        if info.st_size != len(payload):
            raise GuardError(f"file size changed while reading: {path}")
        mode = "100755" if info.st_mode & 0o111 else "100644"
        return payload, mode
    finally:
        os.close(descriptor)


def _null_oid(repo: Path) -> str:
    fmt = git(repo, "rev-parse", "--show-object-format", text=True).stdout.strip() or "sha1"
    if fmt == "sha1":
        return "0" * 40
    if fmt == "sha256":
        return "0" * 64
    raise GuardError(f"unsupported git object format: {fmt}")


def _hash_blob_no_filters(repo: Path, payload: bytes) -> str:
    result = git(
        repo,
        "hash-object",
        "-t",
        "blob",
        "--stdin",
        "--no-filters",
        "-w",
        input_bytes=payload,
    )
    oid = result.stdout.decode("ascii", "strict").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
        raise GuardError("git hash-object returned an unreadable object id")
    stored = git(repo, "cat-file", "blob", oid).stdout
    if stored != payload:
        raise GuardError("hashed blob does not match source bytes")
    return oid


def _stage_authorized_paths(repo: Path, paths: list[str]) -> None:
    """Stage exact worktree bytes without consulting attributes or filter commands."""
    null_oid = _null_oid(repo)
    records: list[bytes] = []
    expected: list[tuple[str, str, bytes | None, str | None, str | None]] = []
    for path in paths:
        read = _read_worktree_regular_file(repo, path)
        if read is None:
            if os.path.lexists(repo / path):
                raise GuardError(f"target must be a regular file or a tracked deletion: {path}")
            tracked = git(repo, "ls-tree", "HEAD", "--", path, text=True).stdout.strip()
            if not tracked:
                raise GuardError(f"absent path is not a tracked deletion: {path}")
            records.append(f"000000 {null_oid}\t{path}\0".encode("utf-8"))
            expected.append((path, "D", None, None, None))
            continue
        payload, mode = read
        oid = _hash_blob_no_filters(repo, payload)
        records.append(f"{mode} {oid}\t{path}\0".encode("utf-8"))
        expected.append((path, "M", payload, mode, oid))
    git(repo, "update-index", "-z", "--index-info", input_bytes=b"".join(records))
    for path, status, payload, mode, oid in expected:
        if status == "D":
            if os.path.lexists(repo / path):
                raise GuardError(f"worktree reappeared while staging deletion: {path}")
            continue
        again = _read_worktree_regular_file(repo, path)
        if again != (payload, mode):
            raise GuardError(f"worktree changed while staging: {path}")
        staged = git(repo, "show", f":{path}").stdout
        if staged != payload:
            raise GuardError(f"staged blob does not match source bytes: {path}")
        raw = git(repo, "ls-files", "--stage", "-z", "--", path).stdout
        if not raw:
            raise GuardError(f"staged path missing from index: {path}")
        meta = raw.split(b"\0", 1)[0]
        parts = meta.split(None, 2)
        if len(parts) < 3:
            raise GuardError(f"staged index entry is unreadable: {path}")
        staged_mode = parts[0].decode("ascii", "strict")
        staged_oid = parts[1].decode("ascii", "strict")
        if staged_mode != mode or staged_oid != oid:
            raise GuardError(f"staged index entry does not match source blob: {path}")


def parse_name_status(raw: bytes):
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise GuardError("unexpected git name-status output")
    return [(fields[i].decode("utf-8", "surrogateescape"),
             fields[i + 1].decode("utf-8", "surrogateescape"))
            for i in range(0, len(fields), 2)]


def _mode_for_index(repo: Path, path: str):
    raw = git(repo, "ls-files", "--stage", "-z", "--", path).stdout
    if not raw:
        return None
    mode = raw.split(None, 1)[0].decode("ascii", "strict")
    return mode


def staged_manifest(repo: Path, revision: str | None = None):
    if revision is None:
        raw = git(repo, "diff", "--cached", "--name-status", "--no-renames", "-z").stdout
        pairs = parse_name_status(raw)
        blob_spec = lambda path: f":{path}"
    else:
        raw = git(repo, "diff-tree", "--root", "--no-commit-id", "--name-status",
                  "--no-renames", "-r", "-z", revision).stdout
        pairs = parse_name_status(raw)
        blob_spec = lambda path: f"{revision}:{path}"
    out = []
    for status, path in pairs:
        entry = {"path": path, "status": status}
        if status != "D":
            blob = git(repo, "show", blob_spec(path)).stdout
            entry["bytes_sha256"] = sha256(blob)
            mode_line = git(repo, "ls-tree", revision or "HEAD", "--", path, text=True).stdout.strip() if revision else ""
            if revision and mode_line:
                entry["mode"] = mode_line.split(None, 1)[0]
            elif not revision:
                mode = _mode_for_index(repo, path)
                if mode:
                    entry["mode"] = mode
        out.append(entry)
    return sorted(out, key=lambda x: x["path"])


def load_receipt(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GuardError(f"invalid receipt: {exc}") from exc
    if data.get("schema") != SCHEMA:
        raise GuardError("unsupported receipt schema")
    return data


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def validate_authorization(a, repo: Path):
    branch = current_branch(repo)
    if branch != a.head:
        raise GuardError(f"current head branch {branch!r} does not match authorized --head {a.head!r}")
    if a.action in ("push", "open-pr"):
        missing = [flag for flag, value in (
            ("--remote", a.remote), ("--remote-url", a.remote_url), ("--base", a.base)
        ) if not value]
        if missing:
            raise GuardError(f"{a.action} requires {' and '.join(missing)}")
        remotes = git(repo, "remote", text=True).stdout.split()
        if a.remote not in remotes:
            raise GuardError(f"authorized remote does not exist: {a.remote}")
        if remote_url(repo, a.remote) != a.remote_url:
            raise GuardError("authorized remote URL does not match current Git configuration")
    elif a.remote or a.remote_url or a.base:
        raise GuardError("commit-only must not carry remote/base/URL external-action authority")
    if a.action == "open-pr" and (not a.pr_title or not a.pr_body_file):
        raise GuardError("open-pr requires exact --pr-title and --pr-body-file")
    if a.action != "open-pr" and (a.pr_title or a.pr_body_file):
        raise GuardError("PR title/body are valid only for action=open-pr")
    if not a.message.strip():
        raise GuardError("--message must be non-empty")
    assert_no_secret((a.message.rstrip() + "\n").encode(), "commit message")
    return branch


def cmd_prepare(a):
    repo = Path(a.repo).resolve(strict=True)
    if git(repo, "rev-parse", "--is-inside-work-tree", text=True).stdout.strip() != "true":
        raise GuardError("--repo is not a git worktree")
    require_repo_root(repo)
    receipt_path = validate_receipt_target(a.receipt, repo)
    branch = validate_authorization(a, repo)
    paths = sorted({normalize_path(p) for p in a.file})
    if not paths or len(paths) != len(a.file):
        raise GuardError("--file list must be non-empty and contain no duplicates")
    for path in paths:
        validate_target(repo, path)
        # Git modes other than regular/non-executable or regular/executable are not management files.
        if (repo / path).exists():
            mode = (repo / path).stat().st_mode
            if not (mode & 0o170000) == 0o100000:
                raise GuardError(f"target must be a regular file: {path}")
    revalidate_live_index(repo)
    if not index_is_empty(repo):
        raise GuardError("git index is not empty; refuse pre-existing staged state")
    # Every input is validated before this single index transaction.
    try:
        _stage_authorized_paths(repo, paths)
        revalidate_live_index(repo)
        manifest = staged_manifest(repo)
        if [e["path"] for e in manifest] != paths:
            raise GuardError("staged paths do not exactly match the authorized file list")
        for entry in manifest:
            if entry.get("status") != "D" and entry.get("mode") != "100644":
                raise GuardError(f"executable or unsupported staged file mode: {entry['path']}")
            if entry.get("status") != "D":
                source = _read_worktree_regular_file(repo, entry["path"])
                blob = git(repo, "show", f":{entry['path']}").stdout
                if source is None or source[0] != blob:
                    raise GuardError(
                        f"staged blob digest does not match worktree bytes: {entry['path']}"
                    )
                if sha256(source[0]) != entry["bytes_sha256"]:
                    raise GuardError(
                        f"receipt blob digest does not match source bytes: {entry['path']}"
                    )
                assert_no_secret(blob, f"staged file {entry['path']}")
        auth = {
            "action": a.action,
            "head": branch,
            "remote": a.remote,
            "remote_url": a.remote_url,
            "base": a.base,
            "commit_message_sha256": sha256((a.message.rstrip() + "\n").encode()),
        }
        if a.action == "open-pr":
            body_path, body = safe_regular_bytes(a.pr_body_file, "PR body")
            assert_no_secret(body, "PR body")
            assert_no_secret(a.pr_title.encode(), "PR title")
            auth.update({"pr_title": a.pr_title, "pr_body_path": str(body_path),
                         "pr_body_sha256": sha256(body)})
        receipt = {
            "schema": SCHEMA,
            "repo": str(repo),
            "pre_commit": git(repo, "rev-parse", "HEAD", text=True).stdout.strip(),
            "authorization": auth,
            "staged": manifest,
            "staged_digest": sha256(canonical(manifest)),
        }
        revalidate_live_index(repo)
        atomic_json(receipt_path, receipt)
        print(json.dumps(receipt, sort_keys=True))
    except Exception:
        git(repo, "reset", "--quiet", "HEAD", "--", *paths, check=False)
        raise


def verify_common(repo: Path, receipt):
    require_repo_root(repo)
    revalidate_live_index(repo)
    if set(receipt) != {"schema", "repo", "pre_commit", "authorization", "staged", "staged_digest"}:
        raise GuardError("receipt fields are invalid")
    if str(repo) != receipt.get("repo"):
        raise GuardError("receipt repository mismatch")
    auth = receipt.get("authorization") or {}
    common_auth = {"action", "head", "remote", "remote_url", "base", "commit_message_sha256"}
    expected_auth = common_auth | (
        {"pr_title", "pr_body_path", "pr_body_sha256"}
        if auth.get("action") == "open-pr" else set()
    )
    if not isinstance(auth, dict) or set(auth) != expected_auth:
        raise GuardError("receipt authorization fields are invalid")
    if auth.get("action") not in ACTIONS:
        raise GuardError("receipt action is invalid")
    if not all(isinstance(auth.get(name), str) and auth.get(name) for name in ("head", "commit_message_sha256")):
        raise GuardError("receipt authorization types are invalid")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", auth["commit_message_sha256"]):
        raise GuardError("receipt commit message digest is invalid")
    if current_branch(repo) != auth.get("head"):
        raise GuardError("authorized head branch mismatch")
    manifest = receipt.get("staged")
    if not isinstance(manifest, list) or not manifest:
        raise GuardError("receipt staged manifest is invalid")
    if receipt.get("staged_digest") != sha256(canonical(manifest)):
        raise GuardError("receipt staged digest is invalid")
    for entry in manifest:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise GuardError("receipt staged manifest entry is invalid")
        expected_entry = {"path", "status"} if entry.get("status") == "D" else {
            "path", "status", "mode", "bytes_sha256"
        }
        if set(entry) != expected_entry:
            raise GuardError("receipt staged manifest entry fields are invalid")
        path = normalize_path(entry["path"])
        if not allowed(path):
            raise GuardError(f"receipt path is outside the management-artifact allowlist: {path}")
        if entry.get("status") not in ("A", "M", "D"):
            raise GuardError(f"receipt status is unsupported: {path}")
        if entry.get("status") != "D" and entry.get("mode") != "100644":
            raise GuardError(f"receipt contains an executable or unsupported file mode: {path}")
        if entry.get("status") != "D" and not re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(entry.get("bytes_sha256") or "")
        ):
            raise GuardError(f"receipt staged blob digest is invalid: {path}")
    if auth.get("action") in ("push", "open-pr"):
        if not auth.get("remote") or not auth.get("remote_url") or not auth.get("base"):
            raise GuardError("external receipt coordinates are incomplete")
        if remote_url(repo, auth["remote"]) != auth["remote_url"]:
            raise GuardError("remote URL changed after authorization")
    elif any(auth.get(field) for field in ("remote", "remote_url", "base")):
        raise GuardError("commit-only receipt carries external authority")
    if auth.get("action") == "open-pr":
        body_path, body = safe_regular_bytes(auth.get("pr_body_path") or "", "PR body")
        if sha256(body) != auth.get("pr_body_sha256"):
            raise GuardError("PR body changed after authorization")
        assert_no_secret(body, "PR body")
        assert_no_secret(auth["pr_title"].encode(), "PR title")
    return auth


def scan_manifest_blobs(repo: Path, manifest, revision: str | None = None) -> None:
    for entry in manifest:
        if entry["status"] == "D":
            continue
        spec = f"{revision}:{entry['path']}" if revision else f":{entry['path']}"
        assert_no_secret(git(repo, "show", spec).stdout, f"published file {entry['path']}")


def cmd_verify_stage(a):
    repo = Path(a.repo).resolve(strict=True)
    receipt = load_receipt(Path(a.receipt))
    verify_common(repo, receipt)
    if git(repo, "rev-parse", "HEAD", text=True).stdout.strip() != receipt.get("pre_commit"):
        raise GuardError("HEAD changed after prepare")
    manifest = staged_manifest(repo)
    digest = sha256(canonical(manifest))
    if digest != receipt.get("staged_digest") or manifest != receipt.get("staged"):
        raise GuardError("staged digest does not match receipt")
    scan_manifest_blobs(repo, manifest)
    revalidate_live_index(repo)
    print(json.dumps({"stage": "verified", "staged_digest": digest}, sort_keys=True))


def cmd_verify_commit(a):
    repo = Path(a.repo).resolve(strict=True)
    receipt = load_receipt(Path(a.receipt))
    auth = verify_common(repo, receipt)
    commit = git(repo, "rev-parse", "HEAD", text=True).stdout.strip()
    parents = git(repo, "rev-list", "--parents", "-n", "1", commit, text=True).stdout.split()
    if len(parents) != 2 or parents[1] != receipt.get("pre_commit"):
        raise GuardError("commit is not the single direct child of the prepared HEAD")
    manifest = staged_manifest(repo, commit)
    digest = sha256(canonical(manifest))
    if digest != receipt.get("staged_digest") or manifest != receipt.get("staged"):
        raise GuardError("committed digest does not match staged receipt")
    scan_manifest_blobs(repo, manifest, commit)
    message = git(repo, "show", "-s", "--format=%B", commit).stdout
    message = message.rstrip() + b"\n"
    if sha256(message) != auth.get("commit_message_sha256"):
        raise GuardError("commit message does not match authorized message")
    assert_no_secret(message, "commit message")
    revalidate_live_index(repo)
    print(json.dumps({"authorization": auth, "commit": commit, "head": auth["head"],
                      "staged_digest": digest}, sort_keys=True))


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--repo", required=True)
    p.add_argument("--action", required=True, choices=ACTIONS)
    p.add_argument("--head", required=True)
    p.add_argument("--remote")
    p.add_argument("--remote-url")
    p.add_argument("--base")
    p.add_argument("--message", required=True)
    p.add_argument("--pr-title")
    p.add_argument("--pr-body-file")
    p.add_argument("--file", action="append", required=True)
    p.add_argument("--receipt", required=True)
    p.set_defaults(func=cmd_prepare)
    for name, func in (("verify-stage", cmd_verify_stage), ("verify-commit", cmd_verify_commit)):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True)
        p.add_argument("--receipt", required=True)
        p.set_defaults(func=func)
    return ap


def main():
    a = parser().parse_args()
    a.func(a)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GuardError as exc:
        print(f"publish_guard: {exc}", file=sys.stderr)
        raise SystemExit(2)
