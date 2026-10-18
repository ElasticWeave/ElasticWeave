#!/usr/bin/env python3
"""Validate an explicit trusted-seed scope or every admitted project flow."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import sys
import uuid
from pathlib import Path


class FlowValidationError(ValueError):
    """A project flow violates the trusted seed contract."""


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


RESEARCH_REFERENCE_NAMES = {
    "_research_reference.py",
    "auto_research.py",
    "deep_research.py",
}
SCHEDULER_FLOW_NAME = "scheduler/__init__.py"
PARALLEL_FLOW_NAMES = frozenset(
    {"parallel/__init__.py", "parallel_worker/__init__.py"}
)
SLOT_LINK_NAME = "slot.py"
SLOT_VERSION_PATTERN = re.compile(r"^versions/slot-([0-9a-f]{64})\.py$")
SLOT_AGENT_ROLE_POOL = (
    "executor",
    "reviewer",
    "planner",
    "preflight",
    "collector",
    "counterevidence",
    "reducer",
    "knowledge",
    "applicability",
    "experimenter",
)
APPROVED_SCHEDULER_FLOW_SHA256 = (
    "697031c14e648967e5f96ba169528a0a8d13ae289722c0c0e61499d90abbeff8"
)
SCHEDULER_TREE_DIRECTORIES = frozenset(
    {
        "skills",
        "skills/plateau-diagnosis",
        "skills/plateau-diagnosis/agents",
    }
)
APPROVED_SCHEDULER_TREE_SHA256 = {
    "__init__.py": APPROVED_SCHEDULER_FLOW_SHA256,
    "skills/plateau-diagnosis/SKILL.md": (
        "b4ad80b383c0b3a97a01d20cf77a8b130126c198b9399ecdb43e3c5b3fe94e26"
    ),
    "skills/plateau-diagnosis/agents/openai.yaml": (
        "290d8dd55731ee86a4dafa8173681cbb7c853014deff7c6c6ed5577bdb5639ab"
    ),
}
APPROVED_RESEARCH_REFERENCE_SHA256 = {
    "_research_reference.py": "76954cc31d5f7c77eff772efab35e0470c9c8b2813b8314e26e212432c9b4881",
    "auto_research.py": "0c85975d126f2161d2e3115142bef575901434f99e57bca515d8e0884634468e",
    "deep_research.py": "5650567a61609f27209f9545d5b75add89ecd9452f4ae2fb8208c543009d7e35",
}
APPROVED_PARALLEL_FLOW_SHA256 = {
    "parallel/__init__.py": "504d0f35f8dfa06eaf6c22bb101cf5274de9f6719abaed944b3ef5f94fe0c0d8",
    "parallel_worker/__init__.py": "368cce95d88dc4f54eb1ded6af85049bfb0c0978cad661b78507659989e6527e",
    "parallel_worker/skills/analysis/SKILL.md": "8cde9053324645e12f23266c6c0b87b72a1ee50c29669a44991f42642306751c",
    "parallel_worker/skills/first-principles/SKILL.md": "9155a3eeca1ee10d0cc6499ae4e272f34bfa2680ef1420b7c3183f690f7d14af",
    "parallel_worker/skills/implementation/SKILL.md": "c9671649f5c342bcf32183fbe1c2e29bd22487e3ca66328e95137d46bdc05a67",
    "parallel_worker/skills/research/SKILL.md": "3083db9fffc21b826bbc6a4109f17ab35260af18dc72f96f924358ff22cb0873",
    "parallel_worker/skills/review/SKILL.md": "cbb3ef4fbaaa36f5c010d9b4a71fbfef3432529389eac94acb3dac95d0af6e64",
}


def regular_bytes(path: Path, label: str) -> bytes:
    """Read one regular file without following any named path component."""
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise FlowValidationError(f"{label} requires Linux no-follow file-descriptor support")

    expanded = path.expanduser()
    parts = list(expanded.parts)
    if expanded.is_absolute():
        start = expanded.anchor
        parts = parts[1:]
    else:
        start = "."
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise FlowValidationError(f"{label} has an unsafe path")

    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | close_on_exec
    leaf_flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | close_on_exec
    directory_fd: int | None = None
    leaf_fd: int | None = None
    try:
        directory_fd = os.open(start, directory_flags)
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            previous_fd = directory_fd
            directory_fd = next_fd
            os.close(previous_fd)
        leaf_fd = os.open(parts[-1], leaf_flags, dir_fd=directory_fd)
        details = os.fstat(leaf_fd)
        if not stat.S_ISREG(details.st_mode):
            raise FlowValidationError(f"{label} must be a regular file, not a link")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(leaf_fd, 1024 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except FlowValidationError:
        raise
    except OSError as error:
        raise FlowValidationError(
            f"{label} must be a regular file, not a link or linked path: {error}"
        ) from error
    finally:
        try:
            if leaf_fd is not None:
                os.close(leaf_fd)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)


def _call_name(node: ast.Call) -> str:
    value: ast.expr = node.func
    parts: list[str] = []
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
    return ".".join(reversed(parts))


def _decorated_run(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef:
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(isinstance(item, ast.Name) and item.id == "flow" for item in node.decorator_list):
            found.append(node)
    if len(found) != 1 or found[0].name != "run":
        raise FlowValidationError("slot must expose exactly one @flow function named run")
    run = found[0]
    if run.args.vararg or run.args.kwarg or run.args.kwonlyargs or len(run.args.args) != 2:
        raise FlowValidationError("slot run signature must be exactly (agents, task)")
    if [argument.arg for argument in run.args.args] != ["agents", "task"]:
        raise FlowValidationError("slot run arguments must be named agents and task")
    annotation = run.args.args[0].annotation
    if not isinstance(annotation, ast.Name) or annotation.id != "Agents":
        raise FlowValidationError("slot agents annotation must be the fixed Agents NamedTuple")
    return run


def _declared_slot_agent_roles(tree: ast.Module) -> tuple[str, ...]:
    declarations = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Agents"
    ]
    if len(declarations) != 1:
        raise FlowValidationError("slot must declare exactly one Agents NamedTuple")
    declaration = declarations[0]
    if (
        len(declaration.bases) != 1
        or not isinstance(declaration.bases[0], ast.Name)
        or declaration.bases[0].id != "NamedTuple"
    ):
        raise FlowValidationError("slot Agents must directly extend NamedTuple")
    roles: list[str] = []
    for index, statement in enumerate(declaration.body):
        if index == 0 and _docstring(statement):
            continue
        if not isinstance(statement, ast.AnnAssign):
            raise FlowValidationError("slot Agents may contain only Agent role fields")
        if (
            not isinstance(statement.target, ast.Name)
            or statement.simple != 1
            or statement.value is not None
            or not isinstance(statement.annotation, ast.Name)
            or statement.annotation.id != "Agent"
        ):
            raise FlowValidationError("every slot Agents field must be annotated exactly Agent")
        role = statement.target.id
        if role not in SLOT_AGENT_ROLE_POOL:
            raise FlowValidationError(f"slot requests unsupported child-agent role {role}")
        if role in roles:
            raise FlowValidationError(f"slot repeats child-agent role {role}")
        roles.append(role)
    if not roles:
        raise FlowValidationError("slot must request at least one child-agent role")
    if "reviewer" not in roles:
        raise FlowValidationError("slot must include the fixed reviewer role")
    return tuple(roles)


_IMPORTED_NAMES = {
    "Agent",
    "BaseModel",
    "ConfigDict",
    "Literal",
    "NamedTuple",
    "Path",
    "asyncio",
    "load",
    "flow",
    "hashlib",
    "json",
    "os",
}

_FORBIDDEN_NODES = (
    ast.Delete,
    ast.Global,
    ast.Lambda,
    ast.Nonlocal,
    ast.TryStar,
    ast.With,
    ast.While,
)
_FORBIDDEN_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "input",
    "open",
    "os.chdir",
    "os.chmod",
    "os.chown",
    "os.execv",
    "os.execve",
    "os.popen",
    "os.remove",
    "os.removedirs",
    "os.rmdir",
    "os.system",
    "os.unlink",
}


def _docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _validate_annotation(expression: ast.expr | None, label: str) -> None:
    """Allow inert annotation syntax only; future annotations keeps it unevaluated."""
    if expression is None:
        return

    def visit(node: ast.expr, *, literal: bool = False) -> None:
        if isinstance(node, ast.Name):
            if literal or node.id.startswith("__"):
                raise FlowValidationError(f"{label} contains an unsafe annotation name")
            return
        if isinstance(node, ast.Constant):
            if literal:
                if node.value is None or isinstance(node.value, (bool, int, float, str)):
                    return
            elif node.value is None or (
                isinstance(node.value, str) and node.value.isidentifier()
            ):
                return
            raise FlowValidationError(f"{label} contains an unsafe annotation constant")
        if isinstance(node, ast.Tuple):
            for item in node.elts:
                visit(item, literal=literal)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) and not literal:
            visit(node.left)
            visit(node.right)
            return
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and not literal:
            if node.value.id.startswith("__"):
                raise FlowValidationError(f"{label} contains an unsafe annotation base")
            visit(node.slice, literal=node.value.id == "Literal")
            return
        raise FlowValidationError(f"{label} contains an executable annotation expression")

    visit(expression)


def _validate_function_header(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    run: bool,
    label: str,
) -> None:
    decorators = node.decorator_list
    if run:
        if len(decorators) != 1 or not isinstance(decorators[0], ast.Name) or decorators[0].id != "flow":
            raise FlowValidationError("slot run decorator must be the bare imported @flow")
    elif decorators:
        raise FlowValidationError(f"{label} decorators execute during flow discovery")
    arguments = node.args
    if arguments.defaults or any(default is not None for default in arguments.kw_defaults):
        raise FlowValidationError(f"{label} defaults execute during flow discovery")
    if getattr(node, "type_params", []):
        raise FlowValidationError(f"{label} type parameters are outside the narrow slot contract")
    all_arguments = [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
    ]
    if arguments.vararg is not None:
        all_arguments.append(arguments.vararg)
    if arguments.kwarg is not None:
        all_arguments.append(arguments.kwarg)
    for argument in all_arguments:
        _validate_annotation(argument.annotation, f"{label}.{argument.arg}")
    _validate_annotation(node.returns, f"{label} return")


def _literal(expression: ast.expr) -> bool:
    if isinstance(expression, ast.Constant):
        return expression.value is None or isinstance(expression.value, (bool, int, float, str))
    if isinstance(expression, (ast.List, ast.Set, ast.Tuple)):
        return all(_literal(item) for item in expression.elts)
    if isinstance(expression, ast.Dict):
        return all(
            key is not None and _literal(key) and _literal(value)
            for key, value in zip(expression.keys, expression.values, strict=True)
        )
    return False


def _model_config(statement: ast.Assign) -> bool:
    if (
        len(statement.targets) != 1
        or not isinstance(statement.targets[0], ast.Name)
        or statement.targets[0].id != "model_config"
        or not isinstance(statement.value, ast.Call)
        or not isinstance(statement.value.func, ast.Name)
        or statement.value.func.id != "ConfigDict"
        or statement.value.args
        or len(statement.value.keywords) != 1
    ):
        return False
    keyword = statement.value.keywords[0]
    return (
        keyword.arg == "extra"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "forbid"
    )


def _validate_class(node: ast.ClassDef) -> None:
    if node.decorator_list or node.keywords or getattr(node, "type_params", []):
        raise FlowValidationError(f"class {node.name} has an executable header")
    if (
        len(node.bases) != 1
        or not isinstance(node.bases[0], ast.Name)
        or node.bases[0].id not in {"BaseModel", "NamedTuple"}
    ):
        raise FlowValidationError(f"class {node.name} must directly use BaseModel or NamedTuple")
    for index, statement in enumerate(node.body):
        if index == 0 and _docstring(statement):
            continue
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.AnnAssign):
            if not isinstance(statement.target, ast.Name) or statement.simple != 1:
                raise FlowValidationError(f"class {node.name} field target must be a simple name")
            if statement.target.id in _IMPORTED_NAMES or statement.target.id.startswith("_"):
                raise FlowValidationError(f"class {node.name} field shadows an imported runtime name")
            _validate_annotation(statement.annotation, f"class {node.name}.{statement.target.id}")
            if statement.value is not None and not _literal(statement.value):
                raise FlowValidationError(f"class {node.name} field default must be inert literal data")
            continue
        if isinstance(statement, ast.Assign) and _model_config(statement):
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if statement.name in _IMPORTED_NAMES or statement.name.startswith("_"):
                raise FlowValidationError(f"class {node.name} method shadows an imported runtime name")
            _validate_function_header(
                statement,
                run=False,
                label=f"class {node.name}.{statement.name}",
            )
            continue
        raise FlowValidationError(
            f"class {node.name} contains executable discovery-time statement {type(statement).__name__}"
        )


def _validate_import_time(tree: ast.Module, label: str) -> None:
    """Restrict every expression Python evaluates while runpy discovers the flow."""
    future_annotations = False
    bound: set[str] = set()
    for index, statement in enumerate(tree.body):
        if index == 0 and _docstring(statement):
            continue
        if isinstance(statement, ast.Import):
            continue
        if isinstance(statement, ast.ImportFrom):
            if statement.module == "__future__" and any(alias.name == "annotations" for alias in statement.names):
                future_annotations = True
            continue
        if isinstance(statement, ast.ClassDef):
            if statement.name in _IMPORTED_NAMES or statement.name in bound:
                raise FlowValidationError(f"class {statement.name} shadows or duplicates a runtime name")
            bound.add(statement.name)
            _validate_class(statement)
            continue
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                statement.name in _IMPORTED_NAMES
                or statement.name in bound
                or statement.name.startswith("__")
            ):
                raise FlowValidationError(f"function {statement.name} shadows or duplicates a runtime name")
            bound.add(statement.name)
            _validate_function_header(
                statement,
                run=statement.name == "run",
                label=f"function {statement.name}",
            )
            continue
        raise FlowValidationError(
            f"{label} contains executable discovery-time statement {type(statement).__name__}"
        )
    if not future_annotations:
        raise FlowValidationError(f"{label} must defer annotations during flow discovery")


def _validate_ast_policy(
    tree: ast.Module,
    *,
    label: str,
    allowed_imports: set[str],
    allowed_from: dict[str, set[str]],
) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.asname or alias.name not in allowed_imports for alias in node.names):
                raise FlowValidationError(f"{label} imports a module outside the allowlist")
        elif isinstance(node, ast.ImportFrom):
            names = {alias.name for alias in node.names}
            if (
                node.level
                or node.module not in allowed_from
                or names - allowed_from[node.module]
                or any(alias.asname for alias in node.names)
            ):
                raise FlowValidationError(f"{label} uses a from-import outside the allowlist")
        elif isinstance(node, _FORBIDDEN_NODES):
            raise FlowValidationError(f"{label} contains forbidden {type(node).__name__}")
        elif isinstance(node, ast.Call) and _call_name(node) in _FORBIDDEN_CALLS:
            raise FlowValidationError(f"{label} contains forbidden call {_call_name(node)}")
    _validate_import_time(tree, label)


def validate_slot_bytes(payload: bytes) -> str:
    try:
        source = payload.decode("utf-8")
        tree = ast.parse(source)
    except (UnicodeDecodeError, SyntaxError) as error:
        raise FlowValidationError(f"slot is not valid UTF-8 Python: {error}") from error
    _decorated_run(tree)
    _declared_slot_agent_roles(tree)

    allowed_imports = {"asyncio", "json", "os"}
    allowed_from = {
        "__future__": {"annotations"},
        "pathlib": {"Path"},
        "typing": {"Literal", "NamedTuple"},
        "hmz.flows": {"Agent", "flow"},
        "pydantic": {"BaseModel", "ConfigDict"},
    }
    _validate_ast_policy(
        tree,
        label="slot",
        allowed_imports=allowed_imports,
        allowed_from=allowed_from,
    )
    return digest(payload)


def slot_agent_roles(payload: bytes) -> tuple[str, ...]:
    """Return a validated Slot's ordered subset of the fixed scheduler agent pool."""

    try:
        tree = ast.parse(payload.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as error:
        raise FlowValidationError(f"slot is not valid UTF-8 Python: {error}") from error
    _decorated_run(tree)
    return _declared_slot_agent_roles(tree)


def validate_default_bytes(payload: bytes) -> str:
    try:
        tree = ast.parse(payload.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError) as error:
        raise FlowValidationError(f"default flow is invalid: {error}") from error
    run = _decorated_run(tree)
    _validate_ast_policy(
        tree,
        label="default",
        allowed_imports={"hashlib", "os"},
        allowed_from={
            "__future__": {"annotations"},
            "pathlib": {"Path"},
            "typing": {"NamedTuple"},
            "hmz.flows": {"Agent", "flow", "load"},
        },
    )
    fresh_loads = [
        node
        for node in ast.walk(run)
        if isinstance(node, ast.Call)
        and _call_name(node) == "load"
    ]
    if len(fresh_loads) != 1:
        raise FlowValidationError("default run must freshly evaluate load exactly once")
    if any(
        isinstance(node, ast.Call) and _call_name(node) == "load"
        for statement in tree.body
        if statement is not run
        for node in ast.walk(statement)
    ):
        raise FlowValidationError("default must not cache a callable outside run")
    if "HUMANIZE_SLOT_SHA256" not in payload.decode("utf-8"):
        raise FlowValidationError("default must require the approved slot digest")
    return digest(payload)


def _read_fd(file_descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(file_descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _directory_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required):
        raise FlowValidationError("versioned slot requires Linux no-follow descriptor support")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_project_directory(root: Path, components: tuple[str, ...]) -> int:
    descriptor = os.open(root, _directory_flags())
    try:
        for component in components:
            if component in {"", ".", ".."} or "/" in component:
                raise FlowValidationError("versioned slot path contains an unsafe component")
            next_descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _regular_status(
    status: os.stat_result,
    label: str,
    *,
    writable: bool = True,
    allow_owner_write: bool = False,
) -> None:
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise FlowValidationError(f"{label} must be a single-link regular file")
    if not writable:
        write_mask = 0o022 if allow_owner_write else 0o222
        if stat.S_IMODE(status.st_mode) & write_mask:
            qualifier = "group/world-writable" if allow_owner_write else "writable"
            raise FlowValidationError(f"{label} must not be {qualifier}")


def _descriptor_bytes(
    descriptor: int,
    label: str,
    *,
    maximum: int = 1024 * 1024,
    writable: bool = True,
    allow_owner_write: bool = False,
) -> bytes:
    before = os.fstat(descriptor)
    _regular_status(
        before,
        label,
        writable=writable,
        allow_owner_write=allow_owner_write,
    )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise FlowValidationError(f"{label} exceeds its bounded size")
    after = os.fstat(descriptor)
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after):
        raise FlowValidationError(f"{label} changed while it was read")
    return b"".join(chunks)


def _normalize_checkout_slot_permissions(
    descriptor: int, expected_digest: str
) -> bytes:
    """Narrow a Git-restored slot mode only after its addressed bytes verify.

    Git records only the executable bit.  A checkout made with a collaborative
    umask can therefore restore a tracked ``100644`` slot as ``0664``.  That is
    not an admission of a writable slot: read and bind the exact addressed
    bytes first, then remove only group/world write bits through the already
    no-follow descriptor, and finally perform the ordinary strict read again.
    """
    status = os.fstat(descriptor)
    _regular_status(status, "versioned slot target")
    if status.st_uid != os.geteuid():
        raise FlowValidationError("versioned slot target must be owned by the effective user")
    if stat.S_IMODE(status.st_mode) & 0o022:
        candidate = _descriptor_bytes(descriptor, "versioned slot target")
        if digest(candidate) != expected_digest:
            raise FlowValidationError("versioned slot filename digest does not match its bytes")
        os.fchmod(descriptor, stat.S_IMODE(status.st_mode) & ~0o022)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
    payload = _descriptor_bytes(
        descriptor,
        "versioned slot target",
        writable=False,
        allow_owner_write=True,
    )
    status = os.fstat(descriptor)
    _regular_status(status, "versioned slot target", writable=False, allow_owner_write=True)
    if status.st_uid != os.geteuid():
        raise FlowValidationError("versioned slot target must be owned by the effective user")
    if digest(payload) != expected_digest:
        raise FlowValidationError("versioned slot filename digest does not match its bytes")
    return payload


def _slot_link_target(flows_fd: int) -> str:
    try:
        status = os.stat(SLOT_LINK_NAME, dir_fd=flows_fd, follow_symlinks=False)
        target = os.readlink(SLOT_LINK_NAME, dir_fd=flows_fd)
    except OSError as error:
        raise FlowValidationError(f"slot.py must be a relative controlled symlink: {error}") from error
    if not stat.S_ISLNK(status.st_mode) or status.st_nlink != 1:
        raise FlowValidationError("slot.py must be a single-link symlink")
    match = SLOT_VERSION_PATTERN.fullmatch(target)
    if match is None:
        raise FlowValidationError(
            "slot.py must target exactly versions/slot-<64 lowercase hex>.py"
        )
    return target


def _read_version_target(flows_fd: int, target: str) -> bytes:
    match = SLOT_VERSION_PATTERN.fullmatch(target)
    if match is None:
        raise FlowValidationError("slot target is outside the controlled versions directory")
    versions_fd: int | None = None
    descriptor: int | None = None
    try:
        versions_fd = os.open("versions", _directory_flags(), dir_fd=flows_fd)
        descriptor = os.open(
            target.split("/", 1)[1],
            os.O_RDONLY
            | os.O_NONBLOCK
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=versions_fd,
        )
        expected = match.group(1)
        return _normalize_checkout_slot_permissions(descriptor, expected)
    except FlowValidationError:
        raise
    except OSError as error:
        raise FlowValidationError(f"versioned slot target is unsafe or missing: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if versions_fd is not None:
            os.close(versions_fd)


def read_versioned_slot(root: Path) -> tuple[str, bytes, str]:
    """Read the only admitted slot link and bind its target name, bytes, and digest."""
    flows_fd: int | None = None
    try:
        flows_fd = _open_project_directory(root, (".humanize", "flows"))
        before = os.stat(SLOT_LINK_NAME, dir_fd=flows_fd, follow_symlinks=False)
        target = _slot_link_target(flows_fd)
        payload = _read_version_target(flows_fd, target)
        after = os.stat(SLOT_LINK_NAME, dir_fd=flows_fd, follow_symlinks=False)
        if (
            before.st_dev,
            before.st_ino,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or _slot_link_target(flows_fd) != target:
            raise FlowValidationError("slot.py changed while its version was read")
    except FlowValidationError:
        raise
    except OSError as error:
        raise FlowValidationError(f"versioned slot path is unsafe: {error}") from error
    finally:
        if flows_fd is not None:
            os.close(flows_fd)
    slot_sha = digest(payload)
    return target, payload, slot_sha


def install_approved_snapshot(root: Path, payload: bytes, slot_sha: str) -> Path:
    """Create one content-addressed child without ever replacing an existing name."""
    if digest(payload) != slot_sha or len(slot_sha) != 64:
        raise FlowValidationError("approved snapshot digest does not match its bytes")
    flows = root / ".humanize/flows"
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    leaf_read_flags = (
        os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    leaf_create_flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    flows_fd: int | None = None
    approved_fd: int | None = None
    leaf_fd: int | None = None
    created_directory = False
    name = f"slot-{slot_sha}.py"
    try:
        flows_fd = _open_project_directory(root, (".humanize", "flows"))
        try:
            approved_fd = os.open(".approved", directory_flags, dir_fd=flows_fd)
        except FileNotFoundError:
            os.mkdir(".approved", mode=0o700, dir_fd=flows_fd)
            created_directory = True
            approved_fd = os.open(".approved", directory_flags, dir_fd=flows_fd)
        directory_status = os.fstat(approved_fd)
        if not stat.S_ISDIR(directory_status.st_mode):
            raise FlowValidationError("approved snapshot path must be a real directory")
        if not created_directory and stat.S_IMODE(directory_status.st_mode) & 0o222:
            raise FlowValidationError("approved snapshot directory must not be writable")
        try:
            leaf_fd = os.open(name, leaf_read_flags, dir_fd=approved_fd)
        except FileNotFoundError:
            os.fchmod(approved_fd, 0o700)
            try:
                leaf_fd = os.open(name, leaf_create_flags, 0o400, dir_fd=approved_fd)
                view = memoryview(payload)
                while view:
                    written = os.write(leaf_fd, view)
                    if written <= 0:
                        raise FlowValidationError("approved snapshot write made no progress")
                    view = view[written:]
                os.fsync(leaf_fd)
                os.fchmod(leaf_fd, 0o444)
            finally:
                os.fchmod(approved_fd, 0o555)
            os.close(leaf_fd)
            leaf_fd = os.open(name, leaf_read_flags, dir_fd=approved_fd)
        status = os.fstat(leaf_fd)
        _regular_status(status, "approved snapshot", writable=False)
        installed = _read_fd(leaf_fd)
        if installed != payload or digest(installed) != slot_sha:
            raise FlowValidationError("approved digest name already contains different bytes")
        if stat.S_IMODE(os.fstat(approved_fd).st_mode) & 0o222:
            raise FlowValidationError("approved snapshot directory remained writable")
    except FlowValidationError:
        raise
    except OSError as error:
        raise FlowValidationError(f"approved snapshot could not be installed safely: {error}") from error
    finally:
        try:
            if leaf_fd is not None:
                os.close(leaf_fd)
        finally:
            try:
                if approved_fd is not None:
                    os.close(approved_fd)
            finally:
                if flows_fd is not None:
                    os.close(flows_fd)
    return flows / ".approved" / name


def install_version_snapshot(root: Path, payload: bytes, slot_sha: str) -> Path:
    """Publish one digest-bound version under the controlled versions directory."""
    if digest(payload) != slot_sha or len(slot_sha) != 64:
        raise FlowValidationError("version snapshot digest does not match its bytes")
    flows = root / ".humanize/flows"
    flows_fd: int | None = None
    versions_fd: int | None = None
    descriptor: int | None = None
    name = f"slot-{slot_sha}.py"
    temporary = f".slot-{slot_sha}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary_created = False
    try:
        flows_fd = _open_project_directory(root, (".humanize", "flows"))
        try:
            versions_fd = os.open("versions", _directory_flags(), dir_fd=flows_fd)
        except FileNotFoundError:
            os.mkdir("versions", mode=0o755, dir_fd=flows_fd)
            versions_fd = os.open("versions", _directory_flags(), dir_fd=flows_fd)
        if not stat.S_ISDIR(os.fstat(versions_fd).st_mode):
            raise FlowValidationError("version snapshot path must be a real directory")
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | os.O_NONBLOCK
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=versions_fd,
            )
        except FileNotFoundError:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                0o400,
                dir_fd=versions_fd,
            )
            temporary_created = True
            try:
                view = memoryview(payload)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise FlowValidationError("version snapshot write made no progress")
                    view = view[written:]
                os.fsync(descriptor)
                os.fchmod(descriptor, 0o444)
            finally:
                os.close(descriptor)
                descriptor = None
            descriptor = os.open(
                temporary,
                os.O_RDONLY
                | os.O_NONBLOCK
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=versions_fd,
            )
            staged = _descriptor_bytes(
                descriptor,
                "staged version snapshot",
                writable=False,
            )
            if staged != payload or digest(staged) != slot_sha:
                raise FlowValidationError("staged version snapshot changed before publication")
            os.close(descriptor)
            descriptor = None
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=versions_fd,
                    dst_dir_fd=versions_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                pass
            os.unlink(temporary, dir_fd=versions_fd)
            temporary_created = False
            os.fsync(versions_fd)
            descriptor = os.open(
                name,
                os.O_RDONLY
                | os.O_NONBLOCK
                | os.O_NOFOLLOW
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=versions_fd,
            )
        installed = _descriptor_bytes(
            descriptor,
            "version snapshot",
            writable=False,
            allow_owner_write=True,
        )
        if installed != payload or digest(installed) != slot_sha:
            raise FlowValidationError("version digest name already contains different bytes")
        os.fsync(versions_fd)
    except FlowValidationError:
        raise
    except OSError as error:
        raise FlowValidationError(f"version snapshot could not be installed safely: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if versions_fd is not None:
            if temporary_created:
                try:
                    os.unlink(temporary, dir_fd=versions_fd)
                except FileNotFoundError:
                    pass
            os.close(versions_fd)
        if flows_fd is not None:
            os.close(flows_fd)
    return flows / "versions" / name


def atomic_retarget_slot(root: Path, target: str) -> None:
    """Atomically retarget the existing controlled slot symlink to one version name."""
    if SLOT_VERSION_PATTERN.fullmatch(target) is None:
        raise FlowValidationError("slot retarget target is outside the controlled versions directory")
    flows_fd: int | None = None
    temporary = f".slot.py.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    temporary_created = False
    try:
        flows_fd = _open_project_directory(root, (".humanize", "flows"))
        _slot_link_target(flows_fd)
        _read_version_target(flows_fd, target)
        os.symlink(target, temporary, dir_fd=flows_fd)
        temporary_created = True
        os.replace(temporary, SLOT_LINK_NAME, src_dir_fd=flows_fd, dst_dir_fd=flows_fd)
        temporary_created = False
        os.fsync(flows_fd)
    except FlowValidationError:
        raise
    except OSError as error:
        raise FlowValidationError(f"slot symlink could not be retargeted safely: {error}") from error
    finally:
        if flows_fd is not None:
            if temporary_created:
                try:
                    os.unlink(temporary, dir_fd=flows_fd)
                except FileNotFoundError:
                    pass
            os.close(flows_fd)


def install_versioned_slot(root: Path, payload: bytes, slot_sha: str) -> str:
    """Install approved bytes and atomically publish their versioned slot pointer."""
    target = f"versions/slot-{slot_sha}.py"
    if validate_slot_bytes(payload) != slot_sha:
        raise FlowValidationError("versioned slot bytes failed the source validator")
    read_versioned_slot(root)
    install_version_snapshot(root, payload, slot_sha)
    install_approved_snapshot(root, payload, slot_sha)
    atomic_retarget_slot(root, target)
    installed_target, installed, installed_sha = read_versioned_slot(root)
    if (installed_target, installed, installed_sha) != (target, payload, slot_sha):
        raise FlowValidationError("versioned slot changed after atomic publication")
    return target


def _project_executable_names(flow_directory: Path) -> list[str]:
    """Enumerate direct file and directory flow entry points without importing them."""
    names: list[str] = []
    flows = os.open(flow_directory, _directory_flags())
    try:
        with os.scandir(flows) as entries:
            for entry in entries:
                if entry.name.endswith(".py"):
                    names.append(entry.name)
                    continue
                if entry.is_symlink():
                    # A link with an extensionless name could be followed by Humanize as a
                    # directory flow. Report it so all-admitted cannot silently accept it.
                    names.append(f"{entry.name}/__init__.py")
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    continue
                directory = os.open(entry.name, _directory_flags(), dir_fd=flows)
                try:
                    try:
                        status = os.stat(
                            "__init__.py", dir_fd=directory, follow_symlinks=False
                        )
                    except FileNotFoundError:
                        continue
                    if stat.S_ISREG(status.st_mode) or stat.S_ISLNK(status.st_mode):
                        names.append(f"{entry.name}/__init__.py")
                finally:
                    os.close(directory)
    finally:
        os.close(flows)
    return sorted(names)


def _unvalidated_flow_names(
    executable_names: list[str], admitted: set[str]
) -> list[str]:
    return [
        name
        for name in executable_names
        if not name.startswith("_")
        and name not in {"default.py", "slot.py"}
        and name not in admitted
    ]


def _unvalidated_helper_names(
    executable_names: list[str], admitted: set[str]
) -> list[str]:
    return [
        name
        for name in executable_names
        if name.startswith("_") and name not in admitted
    ]


def _admit_research_references(
    flow_directory: Path, executable_names: list[str]
) -> list[str]:
    present = RESEARCH_REFERENCE_NAMES.intersection(executable_names)
    if not present:
        return []
    if present != RESEARCH_REFERENCE_NAMES:
        missing = sorted(RESEARCH_REFERENCE_NAMES - present)
        raise FlowValidationError(
            "shipped research reference set is incomplete: " + ", ".join(missing)
        )
    approved_root = (
        Path(__file__).resolve().parents[2]
        / "deep-research-workflow/assets/project/.humanize/flows"
    )
    for name in sorted(RESEARCH_REFERENCE_NAMES):
        approved = regular_bytes(
            approved_root / name, f"controller-approved research reference {name}"
        )
        expected = APPROVED_RESEARCH_REFERENCE_SHA256[name]
        if digest(approved) != expected:
            raise FlowValidationError(
                f"controller-approved research reference {name} disagrees with its validator-owned digest"
            )
        project_payload = regular_bytes(
            flow_directory / name, f"project research reference {name}"
        )
        if project_payload != approved:
            raise FlowValidationError(
                f"project research reference {name} differs from the controller-approved bytes"
            )
    return sorted(RESEARCH_REFERENCE_NAMES)


def _admit_parallel_flows(
    flow_directory: Path, executable_names: list[str]
) -> list[str]:
    """Admit the isolated concurrent meta-flow and its private worker as a fixed tree."""
    present = PARALLEL_FLOW_NAMES.intersection(executable_names)
    if not present:
        return []
    if present != PARALLEL_FLOW_NAMES:
        raise FlowValidationError(
            "parallel flow set is incomplete: "
            + ", ".join(sorted(PARALLEL_FLOW_NAMES - present))
        )
    expected = set(APPROVED_PARALLEL_FLOW_SHA256)
    seen: set[str] = set()
    for current, directories, files in os.walk(
        flow_directory / "parallel_worker", topdown=True, followlinks=False
    ):
        current_path = Path(current)
        prefix = current_path.relative_to(flow_directory).as_posix()
        for directory in directories:
            if (current_path / directory).is_symlink():
                raise FlowValidationError(
                    f"parallel worker tree entry must not be a link: {prefix}/{directory}"
                )
        for name in files:
            relative = f"{prefix}/{name}"
            if relative not in expected:
                raise FlowValidationError(
                    f"parallel worker tree contains unvalidated content: {relative}"
                )
            seen.add(relative)
    if seen != {name for name in expected if name.startswith("parallel_worker/") }:
        raise FlowValidationError("parallel worker tree is missing an admitted file")
    for relative, expected_digest in APPROVED_PARALLEL_FLOW_SHA256.items():
        payload = regular_bytes(flow_directory / relative, f"project parallel flow {relative}")
        if digest(payload) != expected_digest:
            raise FlowValidationError(
                f"project parallel flow {relative} differs from the admitted bytes"
            )
    return sorted(PARALLEL_FLOW_NAMES)


def _validate_scheduler_tree(path: Path, label: str) -> None:
    """Reject every scheduler-mounted file not covered by the byte contract."""
    expected = SCHEDULER_TREE_DIRECTORIES | APPROVED_SCHEDULER_TREE_SHA256.keys()
    seen: set[str] = set()

    def visit(directory: int, prefix: str = "") -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                if relative not in expected:
                    raise FlowValidationError(
                        f"{label} contains unvalidated mounted content: {relative}"
                    )
                if entry.is_symlink():
                    raise FlowValidationError(
                        f"{label} entry must not be a link: {relative}"
                    )
                if relative in SCHEDULER_TREE_DIRECTORIES:
                    if not entry.is_dir(follow_symlinks=False):
                        raise FlowValidationError(
                            f"{label} entry must be a directory: {relative}"
                        )
                    child = os.open(entry.name, _directory_flags(), dir_fd=directory)
                    try:
                        seen.add(relative)
                        visit(child, relative)
                    finally:
                        os.close(child)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise FlowValidationError(
                        f"{label} entry must be a regular file: {relative}"
                    )
                seen.add(relative)

    try:
        root = os.open(path, _directory_flags())
    except OSError as error:
        raise FlowValidationError(
            f"{label} must be a regular directory, not a link: {error}"
        ) from error
    try:
        visit(root)
    except FlowValidationError:
        raise
    except OSError as error:
        raise FlowValidationError(
            f"{label} could not be inspected without following links: {error}"
        ) from error
    finally:
        os.close(root)

    missing = sorted(expected - seen)
    if missing:
        raise FlowValidationError(
            f"{label} is missing validated mounted content: {', '.join(missing)}"
        )


def _admit_scheduler_flow(
    flow_directory: Path, executable_names: list[str]
) -> list[str]:
    """Admit the exact scheduler source and every skill Humanize mounts with it."""
    if SCHEDULER_FLOW_NAME not in executable_names:
        return []
    approved_root = (
        Path(__file__).resolve().parents[1]
        / "assets/project/.humanize/flows"
        / "scheduler"
    )
    project_root = flow_directory / "scheduler"
    _validate_scheduler_tree(approved_root, "controller-approved scheduler tree")
    _validate_scheduler_tree(project_root, "project scheduler tree")
    for relative, expected in APPROVED_SCHEDULER_TREE_SHA256.items():
        display = f"scheduler/{relative}"
        approved = regular_bytes(
            approved_root / relative, f"controller-approved native flow {display}"
        )
        if digest(approved) != expected:
            raise FlowValidationError(
                f"controller-approved native flow {display} "
                "disagrees with its validator-owned digest"
            )
        project_payload = regular_bytes(
            project_root / relative,
            f"project native flow {display}",
        )
        if project_payload != approved:
            raise FlowValidationError(
                f"project native flow {display} differs from the controller-approved bytes"
            )
    return [SCHEDULER_FLOW_NAME]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--install-slot", action="store_true")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument(
        "--trusted-seed",
        action="store_true",
        help="validate only the bundle-owned default.py/slot.py seed and report other flows",
    )
    scope.add_argument(
        "--all-admitted",
        action="store_true",
        help="fail unless every project Python flow is covered by a supported validation contract",
    )
    args = parser.parse_args()
    try:
        root = args.root.expanduser().resolve(strict=True)
        humanize_directory = root / ".humanize"
        flow_directory = humanize_directory / "flows"
        if humanize_directory.is_symlink() or flow_directory.is_symlink():
            raise FlowValidationError("project .humanize and flows directories must not be links")
        if flow_directory.resolve(strict=True) != root / ".humanize/flows":
            raise FlowValidationError("project flow directory escapes the project root")
        executable_names = _project_executable_names(flow_directory)
        admitted_research = (
            _admit_research_references(flow_directory, executable_names)
            if args.all_admitted
            else []
        )
        admitted_scheduler = _admit_scheduler_flow(flow_directory, executable_names)
        admitted_parallel = _admit_parallel_flows(flow_directory, executable_names)
        admitted = (
            set(admitted_research)
            | set(admitted_scheduler)
            | set(admitted_parallel)
        )
        unvalidated = _unvalidated_flow_names(executable_names, admitted)
        unvalidated_helpers = _unvalidated_helper_names(executable_names, admitted)
        if args.all_admitted:
            if unvalidated:
                raise FlowValidationError(
                    "project flow is not covered by a supported validation contract: "
                    + ", ".join(unvalidated)
                )
            if unvalidated_helpers:
                raise FlowValidationError(
                    "unknown executable helper is not covered by a supported validation contract: "
                    + ", ".join(unvalidated_helpers)
                )
        default_path = root / ".humanize/flows/default.py"
        default_sha = validate_default_bytes(regular_bytes(default_path, "default flow"))
        if args.install_slot:
            if args.candidate is None:
                raise FlowValidationError("--install-slot requires --candidate")
            candidate = args.candidate.expanduser()
            payload = regular_bytes(candidate, "slot candidate")
            slot_sha = validate_slot_bytes(payload)
            slot_target = install_versioned_slot(root, payload, slot_sha)
        else:
            if args.candidate is not None:
                raise FlowValidationError("--candidate is valid only with --install-slot")
            slot_target, slot_payload, slot_sha = read_versioned_slot(root)
            if validate_slot_bytes(slot_payload) != slot_sha:
                raise FlowValidationError("versioned slot failed its source validator")
    except (OSError, FlowValidationError) as error:
        print(f"humanize-flow-validation: {error}", file=sys.stderr)
        return 2
    print(json.dumps({
        "scope": "all-admitted" if args.all_admitted else "trusted-seed",
        "defaultSha256": default_sha,
        "slotSha256": slot_sha,
        "slotTarget": slot_target,
        "admittedSchedulerFlow": admitted_scheduler,
        "admittedParallelFlows": admitted_parallel,
        "admittedResearchReferences": admitted_research,
        "unvalidatedFlows": unvalidated,
        "unvalidatedHelpers": unvalidated_helpers,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
