#!/usr/bin/env python3
"""Shared fail-closed execution context for trusted Humanize launchers.

The canonical catalog authenticates native code under the current OS account.  This
module carries that identity into the actual Humanize process: it builds a clean
environment, stages the exact native bytes behind the bare names Humanize invokes,
seals every executable Python input, and revalidates the complete closure after exit.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence


SCHEMA = "humanize-execution-context/v1"
ALLOWED_BACKENDS = frozenset({"claude", "codex"})
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
PROVIDER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
PROVIDER_FIELDS = frozenset({"cli", "name", "way", "env", "args", "made"})
LAUNCHER_ENV_PREFIXES = ("HUMANIZE_", "HMZ_")
FIXED_SYSTEM_PATH = ("/usr/bin", "/bin")

FORBIDDEN_PROVIDER_ENV = frozenset(
    {
        "HOME",
        "PATH",
        "SHELL",
        "BASH_ENV",
        "ENV",
        "IFS",
        "CDPATH",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
    }
)
FORBIDDEN_PROVIDER_PREFIXES = (
    "PYTHON",
    "NODE",
    "LD_",
    "DYLD_",
    "GENERAL_AGENT",
    "HUMANIZE",
    "HMZ",
)

PROVIDER_WAYS = {
    "claude": frozenset({"env", "login", "token", "key", "gateway", "bedrock", "vertex"}),
    "codex": frozenset({"env", "login", "device", "key", "token", "gateway"}),
}
CODEX_GATEWAY_ARGS = (
    "-c",
    "model_provider=humanize",
    "-c",
    "model_providers.humanize.name=humanize",
    "-c",
    "model_providers.humanize.base_url={CODEX_PROVIDER_URL}",
    "-c",
    "model_providers.humanize.env_key=CODEX_PROVIDER_KEY",
    "-c",
    "model_providers.humanize.wire_api={CODEX_PROVIDER_WIRE}",
)

# Humanize resolves an explicit path with realpath before runpy.  A memfd's realpath is
# deliberately not a reopenable pathname, so the fixed launcher keeps authenticated
# /proc/self/fd paths intact.  For the research references it also loads the sealed helper
# into exactly the module name imported by both flow files.
SEALED_HMZ_LAUNCH_CODE = r"""
import os,re,sys,types
source=sys.argv.pop(1);packages=sys.argv.pop(1);helper=sys.argv.pop(1)
sys.path[:0]=[source,packages]
import hmz.flows as _flows
_original_find=_flows.find
_sealed=re.compile(r"/proc/self/fd/[0-9]+\Z")
def _sealed_find(named):
    text=str(named);at,sep,inside=text.rpartition(":")
    candidate=at if sep and "/" not in inside else text
    if _sealed.fullmatch(candidate) and os.path.isfile(candidate):
        return candidate
    return _original_find(text)
_flows.find=_sealed_find
if helper != "-":
    if not _sealed.fullmatch(helper) or not os.path.isfile(helper):
        raise RuntimeError("sealed research helper descriptor is unavailable")
    module=types.ModuleType("_research_reference")
    module.__file__=helper
    module.__package__=""
    sys.modules[module.__name__]=module
    with open(helper,"rb",buffering=0) as stream:
        code=compile(stream.read(),helper,"exec")
    exec(code,module.__dict__)
from hmz.cli import main
raise SystemExit(main())
""".strip()

PROVIDER_PROBE_CODE = r"""
import json,sys
source=sys.argv.pop(1);packages=sys.argv.pop(1);backend=sys.argv.pop(1);account=sys.argv.pop(1)
sys.path[:0]=[source,packages]
from hmz import models
found=models.ask(backend,account)
print(json.dumps({"models":[{"name":one.name,"efforts":list(one.efforts)} for one in found]},sort_keys=True,separators=(",",":")))
""".strip()


class ExecutionContextError(ValueError):
    """The authenticated Humanize execution closure could not be preserved."""


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _canonical_digest(value: object) -> str:
    return _digest(_canonical_bytes(value))


@dataclass(frozen=True)
class AccountBinding:
    uid: int
    gid: int
    name: str
    home: str
    humanize_home: str

    def record(self, mode: str) -> dict[str, object]:
        return {
            "mode": mode,
            "uid": self.uid,
            "gid": self.gid,
            "name": self.name,
            "home": self.home,
            "humanizeHome": self.humanize_home,
        }


@dataclass(frozen=True)
class SealedSource:
    label: str
    fd: int
    sha256: str
    size: int

    @property
    def path(self) -> str:
        return f"/proc/self/fd/{self.fd}"

    def record(self) -> dict[str, object]:
        return {"label": self.label, "sha256": self.sha256, "size": self.size}


@dataclass(frozen=True)
class NativeFile:
    backend: str
    name: str
    source: str
    source_sha256: str
    staged: str
    staged_sha256: str
    role: str

    def record(self) -> dict[str, str]:
        return {
            "backend": self.backend,
            "name": self.name,
            "role": self.role,
            "source": self.source,
            "sourceSha256": self.source_sha256,
            "staged": self.staged,
            "stagedSha256": self.staged_sha256,
        }


def current_account() -> AccountBinding:
    """Bind identity from the effective uid and passwd database, never caller HOME."""

    uid = os.geteuid()
    try:
        record = pwd.getpwuid(uid)
    except KeyError as error:
        raise ExecutionContextError(f"effective uid {uid} has no passwd record") from error
    home = Path(record.pw_dir)
    if not home.is_absolute() or not record.pw_name:
        raise ExecutionContextError("effective account has an invalid passwd home or name")
    try:
        status = home.stat(follow_symlinks=False)
    except OSError as error:
        raise ExecutionContextError(f"passwd home is unavailable: {error}") from error
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != uid:
        raise ExecutionContextError("passwd home must be a directory owned by the effective uid")
    return AccountBinding(
        uid=uid,
        gid=record.pw_gid,
        name=record.pw_name,
        home=str(home),
        humanize_home=str(home / ".humanize"),
    )


def _read_regular(path: Path, label: str) -> bytes:
    """Read one absolute regular file without following any path component."""

    candidate = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    parent_fd: int | None = None
    descriptor: int | None = None
    try:
        parent_fd = _open_directory(candidate.parent, f"{label} parent")
        descriptor = os.open(candidate.name, flags, dir_fd=parent_fd)
    except OSError as error:
        raise ExecutionContextError(f"{label} cannot be opened without following links: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ExecutionContextError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ExecutionContextError(f"{label} changed while it was read")
        return b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_fd is not None:
            os.close(parent_fd)


def _open_directory(path: Path, label: str) -> int:
    candidate = Path(os.path.abspath(path))
    if not candidate.is_absolute():
        raise ExecutionContextError(f"{label} must be absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(candidate.anchor, flags)
        for component in candidate.parts[1:]:
            following = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise ExecutionContextError(f"{label} cannot be opened without following links: {error}") from error


def _manifest_directory(path: Path, label: str) -> dict[str, object]:
    root_fd = _open_directory(path, label)
    entries: list[tuple[str, str]] = []

    def walk(directory_fd: int, prefix: str) -> None:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as error:
            raise ExecutionContextError(f"{label} cannot be enumerated: {error}") from error
        for name in names:
            if name in {"", ".", ".."} or "/" in name:
                raise ExecutionContextError(f"{label} has an unsafe directory member")
            relative = f"{prefix}/{name}" if prefix else name
            try:
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as error:
                raise ExecutionContextError(f"{label} member {relative} is unavailable: {error}") from error
            if stat.S_ISDIR(before.st_mode):
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
                child = os.open(name, flags, dir_fd=directory_fd)
                try:
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise ExecutionContextError(f"{label} directory {relative} changed before open")
                    walk(child, relative)
                    after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                        raise ExecutionContextError(f"{label} directory {relative} changed during scan")
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(before.st_mode):
                raise ExecutionContextError(f"{label} member {relative} must be regular or a directory")
            flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            leaf = os.open(name, flags, dir_fd=directory_fd)
            try:
                opened = os.fstat(leaf)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ExecutionContextError(f"{label} file {relative} changed before open")
                hasher = hashlib.sha256()
                while chunk := os.read(leaf, 1024 * 1024):
                    hasher.update(chunk)
                after_open = os.fstat(leaf)
                after_path = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                if identity != (
                    after_open.st_dev,
                    after_open.st_ino,
                    after_open.st_size,
                    after_open.st_mtime_ns,
                ) or identity != (
                    after_path.st_dev,
                    after_path.st_ino,
                    after_path.st_size,
                    after_path.st_mtime_ns,
                ):
                    raise ExecutionContextError(f"{label} file {relative} changed during scan")
                entries.append((relative, "sha256:" + hasher.hexdigest()))
            finally:
                os.close(leaf)

    try:
        status = os.fstat(root_fd)
        walk(root_fd, "")
        post = os.fstat(root_fd)
        if (status.st_dev, status.st_ino) != (post.st_dev, post.st_ino):
            raise ExecutionContextError(f"{label} root changed during scan")
    finally:
        os.close(root_fd)
    payload = "\n".join(f"{name}\t{value}" for name, value in entries).encode()
    return {
        "path": str(Path(os.path.abspath(path))),
        "fileCount": len(entries),
        "manifestSha256": _digest(payload),
    }


def _humanize_closure(pinned: Mapping[str, Any]) -> dict[str, object]:
    try:
        root = Path(str(pinned["root"]))
        wrapper = Path(str(pinned["binary"]))
        interpreter = Path(str(pinned["interpreter"]))
        prefix = pinned["argvPrefix"]
    except (KeyError, TypeError) as error:
        raise ExecutionContextError("pinned Humanize provenance is incomplete") from error
    if not isinstance(prefix, list) or len(prefix) != 8:
        raise ExecutionContextError("pinned Humanize argv prefix is malformed")
    source = Path(str(prefix[6])) / "hmz"
    packages = Path(str(prefix[7]))
    wrapper_bytes = _read_regular(wrapper, "pinned hmz wrapper")
    interpreter_bytes = _read_regular(interpreter, "pinned Humanize interpreter")
    return {
        "repository": str(pinned.get("repository", "")),
        "revision": str(pinned.get("revision", "")),
        "version": str(pinned.get("version", "")),
        "root": str(Path(os.path.abspath(root))),
        "wrapper": {"path": str(Path(os.path.abspath(wrapper))), "sha256": _digest(wrapper_bytes)},
        "interpreter": {
            "path": str(Path(os.path.abspath(interpreter))),
            "sha256": _digest(interpreter_bytes),
        },
        "source": _manifest_directory(source, "pinned Humanize source closure"),
        "packages": _manifest_directory(packages, "pinned Humanize package closure"),
        "launchCodeSha256": _digest(SEALED_HMZ_LAUNCH_CODE.encode()),
    }


def _seal(label: str, payload: bytes) -> SealedSource:
    if not hasattr(os, "memfd_create"):
        raise ExecutionContextError("Humanize execution requires Linux memfd sealing")
    flags = getattr(os, "MFD_CLOEXEC", 0) | getattr(os, "MFD_ALLOW_SEALING", 0)
    descriptor = os.memfd_create(f"humanize-{label}", flags=flags)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ExecutionContextError(f"sealed source {label} write made no progress")
            view = view[written:]
        os.lseek(descriptor, 0, os.SEEK_SET)
        seals = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
        fcntl.fcntl(descriptor, fcntl.F_ADD_SEALS, seals)
        os.set_inheritable(descriptor, True)
        return SealedSource(label=label, fd=descriptor, sha256=_digest(payload), size=len(payload))
    except BaseException:
        os.close(descriptor)
        raise


def _is_sealed(descriptor: int) -> bool:
    required = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
    try:
        return fcntl.fcntl(descriptor, fcntl.F_GET_SEALS) & required == required
    except OSError:
        return False


def _descriptor_bytes(descriptor: int) -> bytes:
    try:
        size = os.fstat(descriptor).st_size
        return os.pread(descriptor, size, 0)
    except OSError as error:
        raise ExecutionContextError(f"sealed source descriptor is unavailable: {error}") from error


def _command_for_backend(routes: Sequence[Mapping[str, Any]], backend: str) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    for route in routes:
        if route.get("backend") != backend:
            continue
        receipt = route.get("launchReceipt")
        policy = receipt.get("policy") if isinstance(receipt, dict) else None
        evidence = policy.get("freshCatalogEvidence") if isinstance(policy, dict) else None
        record = evidence.get(backend) if isinstance(evidence, dict) else None
        command = record.get("command") if isinstance(record, dict) else None
        if not isinstance(command, dict):
            raise ExecutionContextError(f"{backend} launch receipt has no authenticated command")
        commands.append(command)
    if not commands:
        raise ExecutionContextError(f"no route authenticates native backend {backend}")
    if any(command != commands[0] for command in commands[1:]):
        raise ExecutionContextError(f"{backend} routes disagree on authenticated executable closure")
    command = commands[0]
    required = {
        "argv",
        "executable",
        "executableSha256",
        "invokedArgv",
        "executionClosure",
        "executionClosureSha256",
    }
    if set(command) != required:
        raise ExecutionContextError(f"{backend} authenticated command has malformed fields")
    closure = command.get("executionClosure")
    if not isinstance(closure, list) or not closure:
        raise ExecutionContextError(f"{backend} authenticated command has no execution closure")
    lines: list[str] = []
    for entry in closure:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"role", "path", "sha256"}
            or not all(isinstance(entry.get(key), str) and entry[key] for key in entry)
            or SHA256.fullmatch(entry["sha256"]) is None
        ):
            raise ExecutionContextError(f"{backend} authenticated execution closure is malformed")
        lines.append(f"{entry['role']}\t{entry['path']}\t{entry['sha256']}")
    if command.get("executionClosureSha256") != _digest("\n".join(lines).encode()):
        raise ExecutionContextError(f"{backend} authenticated execution closure digest is invalid")
    return command


def _copy_bound(source: Path, destination: Path, expected: str, label: str) -> None:
    raw = _read_regular(source, label)
    if _digest(raw) != expected:
        raise ExecutionContextError(f"{label} differs from its authenticated digest")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(destination, flags, 0o500)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ExecutionContextError(f"{label} staging write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o500)
    finally:
        os.close(descriptor)
    if _digest(_read_regular(destination, f"staged {label}")) != expected:
        raise ExecutionContextError(f"staged {label} does not preserve authenticated bytes")


def _stage_native(
    routes: Sequence[Mapping[str, Any]],
    bin_dir: Path,
    native_source_override: Mapping[str, Mapping[str, Path]] | None,
) -> list[NativeFile]:
    files: list[NativeFile] = []
    backends = sorted({str(route.get("backend")) for route in routes})
    if not backends or any(backend not in ALLOWED_BACKENDS for backend in backends):
        raise ExecutionContextError("Humanize routes must use only native Claude or Codex backends")
    for backend in backends:
        command = _command_for_backend(routes, backend)
        executable = command.get("executable")
        executable_digest = command.get("executableSha256")
        argv = command.get("argv")
        invoked = command.get("invokedArgv")
        if (
            not isinstance(executable, str)
            or not Path(executable).is_absolute()
            or not isinstance(executable_digest, str)
            or SHA256.fullmatch(executable_digest) is None
            or not isinstance(argv, list)
            or not argv
            or argv[0] != backend
            or not isinstance(invoked, list)
            or not invoked
            or invoked[0] != executable
        ):
            raise ExecutionContextError(f"{backend} command is not a directly bound native executable")
        override = native_source_override.get(backend) if native_source_override else None
        source = Path(override[backend]) if override is not None else Path(executable)
        primary_payload = _read_regular(source, f"authenticated {backend} executable")
        primary_digest = _digest(primary_payload)
        if primary_digest != executable_digest:
            raise ExecutionContextError(f"authenticated {backend} executable digest drifted")
        staged = bin_dir / backend
        _copy_bound(source, staged, primary_digest, f"native {backend} executable")
        files.append(
            NativeFile(
                backend=backend,
                name=backend,
                source=str(source),
                source_sha256=primary_digest,
                staged=str(staged),
                staged_sha256=primary_digest,
                role="entrypoint",
            )
        )
        if backend == "codex":
            companion_source = (
                Path(override["codex-code-mode-host"])
                if override is not None and "codex-code-mode-host" in override
                else Path(executable).parent / "codex-code-mode-host"
            )
            try:
                companion_payload = _read_regular(companion_source, "Codex code-mode companion")
            except ExecutionContextError as error:
                raise ExecutionContextError(
                    "Codex companion codex-code-mode-host is required for faithful private staging"
                ) from error
            companion_digest = _digest(companion_payload)
            companion_staged = bin_dir / "codex-code-mode-host"
            _copy_bound(
                companion_source,
                companion_staged,
                companion_digest,
                "Codex code-mode companion",
            )
            files.append(
                NativeFile(
                    backend=backend,
                    name="codex-code-mode-host",
                    source=str(companion_source),
                    source_sha256=companion_digest,
                    staged=str(companion_staged),
                    staged_sha256=companion_digest,
                    role="required-companion",
                )
            )
    return files


def _validate_provider_record(binding: Mapping[str, Any], account: AccountBinding) -> dict[str, Any]:
    backend = binding.get("backend")
    name = binding.get("providerAccount")
    expected_digest = binding.get("providerRecordSha256")
    expected_path = binding.get("providerRecordPath")
    if (
        backend not in ALLOWED_BACKENDS
        or not isinstance(name, str)
        or PROVIDER_NAME.fullmatch(name) is None
        or not isinstance(expected_digest, str)
        or SHA256.fullmatch(expected_digest) is None
    ):
        raise ExecutionContextError("named provider binding is incomplete")
    canonical_path = Path(account.humanize_home) / "providers" / str(backend) / name / "provider.json"
    if expected_path is not None and Path(str(expected_path)) != canonical_path:
        raise ExecutionContextError("provider record path is not the canonical Humanize account record")
    raw = _read_regular(canonical_path, f"provider record {backend}/{name}")
    if _digest(raw) != expected_digest:
        raise ExecutionContextError(f"provider record {backend}/{name} digest mismatch")
    try:
        record = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExecutionContextError(f"provider record {backend}/{name} is not valid JSON") from error
    if not isinstance(record, dict) or set(record) != PROVIDER_FIELDS:
        raise ExecutionContextError(f"provider record {backend}/{name} has malformed fields")
    if record.get("cli") != backend or record.get("name") != name:
        raise ExecutionContextError(f"provider record {backend}/{name} identity mismatch")
    way = record.get("way")
    environment = record.get("env")
    arguments = record.get("args")
    if way not in PROVIDER_WAYS[str(backend)] or not isinstance(environment, dict) or not isinstance(arguments, list):
        raise ExecutionContextError(f"provider record {backend}/{name} has invalid routing data")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
        raise ExecutionContextError(f"provider record {backend}/{name} environment must contain strings")
    for key in environment:
        if key in FORBIDDEN_PROVIDER_ENV or key.startswith(FORBIDDEN_PROVIDER_PREFIXES):
            raise ExecutionContextError(f"provider environment {key!r} is a loader, shell, or config-root injection")
    if not all(isinstance(value, str) for value in arguments):
        raise ExecutionContextError(f"provider record {backend}/{name} arguments must contain strings")
    expected_arguments: tuple[str, ...] = ()
    if backend == "codex" and way == "gateway":
        try:
            expected_arguments = tuple(argument.format_map(environment) for argument in CODEX_GATEWAY_ARGS)
        except KeyError as error:
            raise ExecutionContextError("Codex gateway provider record lacks its fixed argument input") from error
    if tuple(arguments) != expected_arguments:
        raise ExecutionContextError(f"provider record {backend}/{name} contains unapproved backend arguments")
    return {
        "backend": backend,
        "account": name,
        "path": str(canonical_path),
        "sha256": expected_digest,
        "catalogModelId": binding.get("catalogModelId"),
        "effort": binding.get("effort"),
    }


def _parse_provider_catalog(
    stdout: bytes,
    *,
    backend: str,
    account: str,
    model: object,
    effort: object,
) -> list[dict[str, object]]:
    try:
        document = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExecutionContextError(f"provider catalog {backend}/{account} returned malformed JSON") from error
    models = document.get("models") if isinstance(document, dict) and set(document) == {"models"} else None
    if not isinstance(models, list):
        raise ExecutionContextError(f"provider catalog {backend}/{account} has malformed coverage")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in models:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "efforts"}
            or not isinstance(item.get("name"), str)
            or not item["name"]
            or item["name"] in seen
            or not isinstance(item.get("efforts"), list)
            or not item["efforts"]
            or not all(isinstance(value, str) and value for value in item["efforts"])
            or len(set(item["efforts"])) != len(item["efforts"])
        ):
            raise ExecutionContextError(f"provider catalog {backend}/{account} has malformed model entries")
        seen.add(item["name"])
        normalized.append({"name": item["name"], "efforts": list(item["efforts"])})
    covered = next((item for item in normalized if item["name"] == model), None)
    if covered is None or effort not in covered["efforts"]:
        raise ExecutionContextError(
            f"provider catalog {backend}/{account} does not cover the exact model and effort"
        )
    return normalized


def _run_provider_probe(
    pinned: Mapping[str, Any],
    backend: str,
    account: str,
    environment: Mapping[str, str],
) -> tuple[int, bytes, bytes]:
    prefix = pinned.get("argvPrefix")
    if not isinstance(prefix, list) or len(prefix) != 8:
        raise ExecutionContextError("pinned Humanize argv prefix is malformed")
    command = [
        str(prefix[0]),
        "-I",
        "-S",
        "-B",
        "-c",
        PROVIDER_PROBE_CODE,
        str(prefix[6]),
        str(prefix[7]),
        backend,
        account,
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(environment),
            check=False,
            timeout=240,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ExecutionContextError(f"provider catalog {backend}/{account} could not execute: {error}") from error
    return completed.returncode, completed.stdout, completed.stderr


def _remove_tree(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        status = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(status.st_mode):
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            try:
                _remove_tree(child)
            finally:
                os.close(child)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


class HumanizeExecutionContext:
    """One account-, code-, environment-, and source-bound Humanize dispatch."""

    def __init__(
        self,
        *,
        pinned: Mapping[str, Any],
        routes: Sequence[Mapping[str, Any]],
        account: AccountBinding,
        account_mode: str,
        run_root: Path,
        run_fd: int,
        bin_dir: Path,
        sources: dict[str, SealedSource],
        native_files: list[NativeFile],
        environment: dict[str, str],
        humanize_closure: dict[str, object],
        provider_records: list[dict[str, Any]],
        provider_catalogs: list[dict[str, Any]],
        account_override: AccountBinding | None,
    ) -> None:
        self.pinned = dict(pinned)
        self.routes = list(routes)
        self.account = account
        self.account_mode = account_mode
        self.run_root = run_root
        self._run_fd = run_fd
        self.bin_dir = bin_dir
        self.sources = sources
        self.native_files = native_files
        self.environment = environment
        self.humanize_closure = humanize_closure
        self.provider_records = provider_records
        self.provider_catalogs = provider_catalogs
        self._account_override = account_override
        self._cleaned = False
        unsigned = {
            "schema": SCHEMA,
            "account": account.record(account_mode),
            "environmentSha256": _canonical_digest(environment),
            "humanizeClosure": humanize_closure,
            "nativeFiles": [item.record() for item in native_files],
            "sealedSources": [sources[name].record() for name in sorted(sources)],
            "providerCatalogs": provider_catalogs,
        }
        self.receipt = {**unsigned, "receiptSha256": _canonical_digest(unsigned)}

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return tuple(source.fd for source in self.sources.values())

    @staticmethod
    def is_sealed(descriptor: int) -> bool:
        return _is_sealed(descriptor)

    def source_path(self, label: str) -> str:
        try:
            return self.sources[label].path
        except KeyError as error:
            raise ExecutionContextError(f"unknown sealed source {label!r}") from error

    def argv_prefix(self, *, helper_label: str | None = None) -> list[str]:
        prefix = self.pinned.get("argvPrefix")
        if not isinstance(prefix, list) or len(prefix) != 8:
            raise ExecutionContextError("pinned Humanize argv prefix is malformed")
        helper = self.source_path(helper_label) if helper_label is not None else "-"
        return [
            str(prefix[0]),
            "-I",
            "-S",
            "-B",
            "-c",
            SEALED_HMZ_LAUNCH_CODE,
            str(prefix[6]),
            str(prefix[7]),
            helper,
        ]

    def revalidate(self) -> None:
        if self._cleaned:
            raise ExecutionContextError("Humanize execution context is already cleaned")
        if os.geteuid() != self.account.uid or os.getegid() != self.account.gid:
            raise ExecutionContextError("effective OS account changed during Humanize dispatch")
        if self._account_override is None and current_account() != self.account:
            raise ExecutionContextError("passwd account binding changed during Humanize dispatch")
        if _canonical_digest(self.environment) != self.receipt["environmentSha256"]:
            raise ExecutionContextError("sanitized Humanize environment changed during dispatch")
        if _humanize_closure(self.pinned) != self.humanize_closure:
            raise ExecutionContextError("pinned Humanize source, interpreter, or package closure changed")
        for source in self.sources.values():
            if not _is_sealed(source.fd) or _digest(_descriptor_bytes(source.fd)) != source.sha256:
                raise ExecutionContextError(f"sealed source {source.label} changed during dispatch")
        for item in self.native_files:
            if _digest(_read_regular(Path(item.source), f"native source {item.name}")) != item.source_sha256:
                raise ExecutionContextError(f"native source {item.name} changed during dispatch")
            if _digest(_read_regular(Path(item.staged), f"staged native {item.name}")) != item.staged_sha256:
                raise ExecutionContextError(f"staged native {item.name} changed during dispatch")
        for record in self.provider_records:
            raw = _read_regular(Path(record["path"]), f"provider record {record['backend']}/{record['account']}")
            if _digest(raw) != record["sha256"]:
                raise ExecutionContextError(
                    f"provider record {record['backend']}/{record['account']} changed during dispatch"
                )

    def cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        for source in self.sources.values():
            try:
                os.close(source.fd)
            except OSError:
                pass
        try:
            _remove_tree(self._run_fd)
        except OSError:
            pass
        try:
            status = os.stat(self.run_root, follow_symlinks=False)
            held = os.fstat(self._run_fd)
            if (status.st_dev, status.st_ino) == (held.st_dev, held.st_ino):
                os.rmdir(self.run_root)
        except OSError:
            pass
        try:
            os.close(self._run_fd)
        except OSError:
            pass

    def __enter__(self) -> "HumanizeExecutionContext":
        return self

    def __exit__(self, _kind: object, _error: object, _traceback: object) -> None:
        self.cleanup()


def build_execution_context(
    *,
    pinned: Mapping[str, Any],
    routes: Sequence[Mapping[str, Any]],
    sources: Mapping[str, bytes],
    account_mode: str,
    provider_bindings: Sequence[Mapping[str, Any]] = (),
    launcher_environment: Mapping[str, str] | None = None,
    source_environment: Mapping[str, str] | None = None,
    account_override: AccountBinding | None = None,
    native_source_override: Mapping[str, Mapping[str, Path]] | None = None,
    provider_probe: Callable[[str, str, Mapping[str, str]], tuple[int, bytes, bytes]] | None = None,
) -> HumanizeExecutionContext:
    """Build the only environment and code closure admitted for one Humanize run.

    The three ``*_override``/``provider_probe`` parameters are Python-call-only test seams.
    Launchers expose none of them through argv or environment.
    """

    if account_mode not in {"ambient", "named"}:
        raise ExecutionContextError("Humanize account mode must be ambient or named")
    if account_mode == "ambient" and provider_bindings:
        raise ExecutionContextError("ambient Humanize launch cannot carry named provider bindings")
    if account_mode == "named" and not provider_bindings:
        raise ExecutionContextError("named Humanize launch requires exact provider bindings")
    if not sources or not all(
        isinstance(label, str) and label and isinstance(payload, bytes)
        for label, payload in sources.items()
    ):
        raise ExecutionContextError("Humanize launch requires named source bytes")
    account = account_override if account_override is not None else current_account()
    if account.uid != os.geteuid() or account.gid != os.getegid():
        raise ExecutionContextError("Humanize account binding must match the effective process account")
    run_root = Path(tempfile.mkdtemp(prefix=f"humanize-{account.uid}-", dir="/tmp"))
    os.chmod(run_root, 0o700)
    run_fd = os.open(
        run_root,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    context: HumanizeExecutionContext | None = None
    sealed: dict[str, SealedSource] = {}
    try:
        root_status = os.fstat(run_fd)
        if root_status.st_uid != account.uid or stat.S_IMODE(root_status.st_mode) != 0o700:
            raise ExecutionContextError("private Humanize run directory has unsafe ownership or mode")
        bin_dir = run_root / "bin"
        temporary_dir = run_root / "tmp"
        bin_dir.mkdir(mode=0o700)
        temporary_dir.mkdir(mode=0o700)
        sealed = {label: _seal(label, payload) for label, payload in sources.items()}
        native_files = _stage_native(routes, bin_dir, native_source_override)
        humanize_closure = _humanize_closure(pinned)
        environment = {
            "HOME": account.home,
            "USER": account.name,
            "LOGNAME": account.name,
            "HUMANIZE_HOME": account.humanize_home,
            "PATH": os.pathsep.join((str(bin_dir), *FIXED_SYSTEM_PATH)),
            "CODEX_HOME": str(Path(account.home) / ".codex"),
            "XDG_CONFIG_HOME": str(Path(account.home) / ".config"),
            "XDG_DATA_HOME": str(Path(account.home) / ".local/share"),
            "TMPDIR": str(temporary_dir),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        for key, value in (launcher_environment or {}).items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key.startswith(LAUNCHER_ENV_PREFIXES)
                or key in {"HUMANIZE_HOME"}
            ):
                raise ExecutionContextError(f"launcher environment variable {key!r} is not an exact receipt variable")
            environment[key] = value
        for key, label in (source_environment or {}).items():
            if not isinstance(key, str) or not key.startswith(LAUNCHER_ENV_PREFIXES):
                raise ExecutionContextError(f"source environment variable {key!r} is not an exact launcher variable")
            if label not in sealed:
                raise ExecutionContextError(f"source environment variable {key!r} names an unknown source")
            environment[key] = str(sealed[label].fd)

        provider_records = [_validate_provider_record(binding, account) for binding in provider_bindings]
        provider_catalogs: list[dict[str, Any]] = []
        probe = provider_probe or (lambda backend, name, env: _run_provider_probe(pinned, backend, name, env))
        for record in provider_records:
            code, stdout, stderr = probe(record["backend"], record["account"], environment)
            if not isinstance(code, int) or not isinstance(stdout, bytes) or not isinstance(stderr, bytes):
                raise ExecutionContextError("provider catalog probe returned malformed process evidence")
            if code != 0:
                raise ExecutionContextError(
                    f"provider catalog {record['backend']}/{record['account']} exited {code}; stderrSha256={_digest(stderr)}"
                )
            coverage = _parse_provider_catalog(
                stdout,
                backend=record["backend"],
                account=record["account"],
                model=record["catalogModelId"],
                effort=record["effort"],
            )
            native = next(item for item in native_files if item.backend == record["backend"] and item.role == "entrypoint")
            provider_catalogs.append(
                {
                    "backend": record["backend"],
                    "account": record["account"],
                    "providerRecordSha256": record["sha256"],
                    "catalogModelId": record["catalogModelId"],
                    "effort": record["effort"],
                    "stdoutSha256": _digest(stdout),
                    "stderrSha256": _digest(stderr),
                    "coverage": coverage,
                    "environmentSha256": _canonical_digest(environment),
                    "stagedExecutableSha256": native.staged_sha256,
                }
            )

        context = HumanizeExecutionContext(
            pinned=pinned,
            routes=routes,
            account=account,
            account_mode=account_mode,
            run_root=run_root,
            run_fd=run_fd,
            bin_dir=bin_dir,
            sources=sealed,
            native_files=native_files,
            environment=environment,
            humanize_closure=humanize_closure,
            provider_records=provider_records,
            provider_catalogs=provider_catalogs,
            account_override=account_override,
        )
        context.revalidate()
        return context
    except BaseException:
        if context is not None:
            context.cleanup()
        else:
            for source in sealed.values():
                try:
                    os.close(source.fd)
                except OSError:
                    pass
            try:
                _remove_tree(run_fd)
            except OSError:
                pass
            try:
                os.close(run_fd)
            finally:
                shutil.rmtree(run_root, ignore_errors=True)
        raise


__all__ = [
    "AccountBinding",
    "ExecutionContextError",
    "HumanizeExecutionContext",
    "SCHEMA",
    "SEALED_HMZ_LAUNCH_CODE",
    "build_execution_context",
    "current_account",
]
