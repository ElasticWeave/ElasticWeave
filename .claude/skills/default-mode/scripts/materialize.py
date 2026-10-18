#!/usr/bin/env python3
"""Materialize skill-owned project templates without overwriting existing state.

The command performs a full preflight before its first write. Existing identical files are
idempotent; existing different files, symlinks, and non-file collisions fail closed. Root agent
instructions are the sole merge-managed exception: a marked skill-owned block is appended or
updated while every byte outside the markers remains user-owned. The optional startup mode selects
the bounded instruction section inside that block; it never changes project templates or PM state.
Minimum-bootstrap scope merges only that instruction block and does not enumerate, conflict-check,
or copy optional project templates.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


@dataclass(frozen=True)
class Template:
    source: Path
    relative: Path
    link_target: str | None = None


@dataclass(frozen=True)
class ManagedWrite:
    relative: Path
    content: bytes
    mode: int


PACKAGE_SOURCES = (
    ("default-mode", Path("assets/project")),
    ("memory", Path("assets/project")),
    ("subagent-workflow", Path("assets/project")),
    ("write-humanize-flow", Path("assets/project")),
    ("deep-research-workflow", Path("assets/project")),
    ("update-platform-config", Path("assets/project")),
    ("write-subagent", Path("assets/project")),
)

PYTHON_BYTECODE_SUFFIXES = frozenset({".pyc", ".pyo"})
EXTERNAL_HARNESS_ROOTS = frozenset({".external-harness", "config/external-harness"})
SESSION_CONTRACT_SOURCE = Path("default-mode/references/project-session-contract.md")
SESSION_CONTRACT_TARGETS = (Path("AGENTS.md"), Path("CLAUDE.md"))
MANAGED_START = b"<!-- general-agent:project-session-contract:start -->"
MANAGED_END = b"<!-- general-agent:project-session-contract:end -->"
MODE_SECTION_START = b"<!-- general-agent:startup-mode:start -->"
MODE_SECTION_END = b"<!-- general-agent:startup-mode:end -->"
# Naive mode is prompt-only and deliberately has no project materialization path.
MATERIALIZABLE_MODES = ("default", "fast")
# The canonical default section lives in project-session-contract.md; only fast needs a splice.
STARTUP_MODE_INSTRUCTIONS: dict[str, bytes] = {
    "fast": b'''### Startup operating mode: `fast`

This is the human-guided, short-cycle startup mode. Treat `fast` as an operating-mode prompt, not
as a persisted PM review-policy value: the selected PM iteration policy remains `human_guided` and
must carry its finite check-in lease and explicit expiry fallback. The top Codex session remains the
team leader and delegates bounded workflow execution to subagents. Run the shortest valid cycle:
propose a subtask, experiment or prove it, reduce the result, then present the external human
observation checkpoint before replanning. This checkpoint pauses only the next human-dependent
planning cycle; keep all already-authorized workers, workflows, reviews, and fan-in monitored.
Progress updates, checkpoints, result gates, and observation timeouts never authorize final return
while work remains pending. Follow the shared managed session lifecycle. Skip only routine low-risk
idea-merit debate; retain safety,
validity, code review, alignment review, evidence integrity, contradiction handling, reward-hacking
checks, and terminal review. Persist each managed task's real evidence through delegated PM workers
under `.pm/tasks/<issue-id>/`; the top session only inspects it. Discuss results with the human
promptly when the checkpoint is due. Use one bounded proposal or result-analysis consultation only
when independent alternatives are likely to prevent immediate rework or clarify an ambiguous human
checkpoint; reduce once and preserve disagreement instead of voting. At each task boundary, decide whether Simplify -> Verify is
required before dispatch. Treat the active agent instruction brief (the managed block in
AGENTS.md/CLAUDE.md, or a worker brief), and add these exact fields there:
SIMPLIFY_VERIFY_DECISION: required|not_required and
SIMPLIFY_VERIFY_REASON: <one concrete sentence>; mirror both fields in the PM task record. Run
the bounded loop only for a required decision; a missing or ambiguous decision blocks dispatch.
Give every native agent CLI turn 3600 seconds and
use only the bounded 5m/10m/30m/1h transient retry schedule.''',
}
SLOT_LINK_RELATIVE = Path(".humanize/flows/slot.py")
SLOT_LINK_PATTERN = re.compile(r"^versions/slot-[0-9a-f]{64}\.py$")


def has_python_cache_component(path: Path) -> bool:
    return any(part.casefold() == "__pycache__" for part in path.parts)


def valid_slot_link(path: Path) -> bool:
    """Accept only a digest-bound project-local slot version pointer."""
    if not path.is_symlink():
        return False
    target = os.readlink(path)
    match = SLOT_LINK_PATTERN.fullmatch(target)
    if match is None:
        return False
    version = path.parent / target
    if (
        version.parent.is_symlink()
        or not version.is_file()
        or version.is_symlink()
        or version.stat().st_nlink != 1
        or version.stat().st_mode & 0o022
    ):
        return False
    expected = target.removeprefix("versions/slot-").removesuffix(".py")
    return hashlib.sha256(version.read_bytes()).hexdigest() == expected


def skills_root(script: Path = Path(__file__)) -> Path:
    resolved = script.resolve()
    for candidate in resolved.parents:
        if candidate.name == "skills" and (candidate / "default-mode").is_dir():
            return candidate
    raise RuntimeError(f"cannot locate sibling skills from {resolved}")


def collect_templates(root: Path) -> list[Template]:
    templates: list[Template] = []
    missing: list[str] = []
    seen: dict[Path, Path] = {}
    for skill, relative_source in PACKAGE_SOURCES:
        source_root = root / skill / relative_source
        if not source_root.is_dir():
            missing.append(f"{skill}/{relative_source.as_posix()}")
            continue
        for source in sorted(source_root.rglob("*")):
            if (
                has_python_cache_component(source)
                or source.suffix.lower() in PYTHON_BYTECODE_SUFFIXES
            ):
                continue
            relative = source.relative_to(source_root)
            if source.is_symlink():
                if relative != SLOT_LINK_RELATIVE:
                    raise RuntimeError(f"unsupported project asset symlink: {relative}")
                target = os.readlink(source)
                if SLOT_LINK_PATTERN.fullmatch(target) is None or not valid_slot_link(source):
                    raise RuntimeError(f"unsafe Humanize slot link target: {target}")
                template = Template(source, relative, target)
            else:
                if not source.is_file():
                    continue
                template = Template(source, relative)
            owner = seen.get(relative)
            if owner is not None:
                raise RuntimeError(
                    f"duplicate materializer destination {relative}: {owner} and {source}"
                )
            first_two = "/".join(relative.parts[:2])
            if (
                relative.parts[0] in EXTERNAL_HARNESS_ROOTS
                or first_two in EXTERNAL_HARNESS_ROOTS
            ):
                raise RuntimeError(
                    f"unselected external harness asset entered default bootstrap: {relative}"
                )
            seen[relative] = source
            templates.append(template)
    if missing:
        raise RuntimeError("missing required skill asset roots: " + ", ".join(missing))
    if not templates:
        raise RuntimeError("skill bundle contains no project templates")
    return templates


def file_bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"destination is not a regular file: {path}")
    return path.read_bytes()


def _validate_startup_mode(mode: str) -> str:
    if mode == "naive":
        raise RuntimeError(
            "naive mode is isolated and cannot be materialized; run /naive-mode in a fresh temporary directory"
        )
    if mode not in MATERIALIZABLE_MODES:
        allowed = ", ".join(MATERIALIZABLE_MODES)
        raise RuntimeError(f"startup mode must be one of: {allowed}")
    return mode


def session_contract_block(root: Path, mode: str = "default") -> bytes:
    mode = _validate_startup_mode(mode)
    source = root / SESSION_CONTRACT_SOURCE
    if not source.is_file() or source.is_symlink():
        raise RuntimeError(f"missing regular managed contract source: {SESSION_CONTRACT_SOURCE}")
    content = source.read_bytes()
    if content.count(MANAGED_START) != 1 or content.count(MANAGED_END) != 1:
        raise RuntimeError("managed contract source must contain exactly one start/end marker")
    start = content.index(MANAGED_START)
    end = content.index(MANAGED_END, start) + len(MANAGED_END)
    if content[:start].strip() or content[end:].strip():
        raise RuntimeError("managed contract source may contain only its marked block")
    block = content[start:end]
    if (
        block.count(MODE_SECTION_START) != 1
        or block.count(MODE_SECTION_END) != 1
    ):
        raise RuntimeError(
            "managed contract source must contain exactly one startup-mode section"
        )
    section_start = block.index(MODE_SECTION_START)
    section_end = block.index(MODE_SECTION_END, section_start)
    if section_end < section_start:
        raise RuntimeError("managed startup-mode section markers are out of order")
    if mode == "default":
        section = block[section_start + len(MODE_SECTION_START) : section_end]
        if b"Startup operating mode: `default`" not in section:
            raise RuntimeError("managed contract default startup-mode section is malformed")
        return block
    start_body = section_start + len(MODE_SECTION_START)
    return (
        block[:start_body]
        + b"\n"
        + STARTUP_MODE_INSTRUCTIONS[mode]
        + b"\n"
        + block[section_end:]
    )


def merge_managed_block(existing: bytes, block: bytes, relative: Path) -> bytes:
    """Return a byte-preserving managed-block merge or fail closed on ambiguous markers."""
    starts = existing.count(MANAGED_START)
    ends = existing.count(MANAGED_END)
    if starts == 0 and ends == 0:
        if not existing:
            return block + b"\n"
        if existing.endswith(b"\n\n"):
            separator = b""
        elif existing.endswith(b"\n"):
            separator = b"\n"
        else:
            separator = b"\n\n"
        return existing + separator + block + b"\n"
    if starts != 1 or ends != 1:
        raise RuntimeError(
            f"{relative}: managed block markers are incomplete or duplicated "
            f"(start={starts}, end={ends})"
        )
    start = existing.index(MANAGED_START)
    end_start = existing.index(MANAGED_END)
    if end_start < start:
        raise RuntimeError(f"{relative}: managed block end marker precedes start marker")
    end = end_start + len(MANAGED_END)
    return existing[:start] + block + existing[end:]


def preflight_managed(
    project_root: Path, root: Path, mode: str = "default"
) -> tuple[list[ManagedWrite], list[Path]]:
    block = session_contract_block(root, mode)
    writes: list[ManagedWrite] = []
    unchanged: list[Path] = []
    errors: list[str] = []
    for relative in SESSION_CONTRACT_TARGETS:
        destination = project_root / relative
        if not destination.exists() and not destination.is_symlink():
            writes.append(ManagedWrite(relative, block + b"\n", 0o644))
            continue
        try:
            existing = file_bytes(destination)
            merged = merge_managed_block(existing, block, relative)
            if merged == existing:
                unchanged.append(relative)
            else:
                writes.append(ManagedWrite(relative, merged, destination.stat().st_mode & 0o777))
        except (OSError, RuntimeError) as error:
            errors.append(str(error))
    if errors:
        raise RuntimeError("managed instruction conflicts:\n  " + "\n  ".join(errors))
    return writes, unchanged


def preflight(project_root: Path, templates: list[Template]) -> tuple[list[Template], list[Path]]:
    missing: list[Template] = []
    unchanged: list[Path] = []
    conflicts: list[str] = []
    for template in templates:
        destination = project_root / template.relative
        if not destination.exists() and not destination.is_symlink():
            parent = destination.parent
            while parent != project_root and not parent.exists():
                parent = parent.parent
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                conflicts.append(f"{template.relative}: parent path is not a real directory")
            else:
                missing.append(template)
            continue
        try:
            if template.link_target is not None:
                parent = destination.parent
                while parent != project_root and parent != parent.parent:
                    if parent.is_symlink() or not parent.is_dir():
                        conflicts.append(f"{template.relative}: parent path is not a real directory")
                        break
                    parent = parent.parent
                if (
                    parent != project_root
                    or not valid_slot_link(destination)
                ):
                    conflicts.append(f"{template.relative}: existing link target differs")
                else:
                    unchanged.append(template.relative)
            elif file_bytes(destination) == template.source.read_bytes():
                unchanged.append(template.relative)
            else:
                conflicts.append(f"{template.relative}: existing bytes differ")
        except (OSError, RuntimeError) as error:
            conflicts.append(f"{template.relative}: {error}")
    if conflicts:
        raise RuntimeError("materialization conflicts:\n  " + "\n  ".join(conflicts))
    return missing, unchanged


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        target = os.readlink(source)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            os.symlink(target, temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.is_symlink() or temporary.exists():
                temporary.unlink()
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, source.stat().st_mode & 0o777)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write(content: bytes, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def materialize(
    project_root: Path,
    bundle_root: Path,
    mode: str = "default",
    *,
    minimum_bootstrap: bool = False,
) -> tuple[list[Path], list[Path]]:
    """Inject the mode contract and, unless deferred, materialize the full bundle."""
    _validate_startup_mode(mode)
    project = project_root.expanduser().resolve()
    if not project.is_dir():
        raise RuntimeError(f"project root is not a directory: {project}")
    if minimum_bootstrap:
        missing: list[Template] = []
        unchanged: list[Path] = []
    else:
        templates = collect_templates(bundle_root)
        missing, unchanged = preflight(project, templates)
    managed_writes, managed_unchanged = preflight_managed(project, bundle_root, mode)
    # Publish version files before the controlled slot pointer so a partial bootstrap
    # cannot leave a dangling symlink behind.
    missing.sort(key=lambda template: template.link_target is not None)
    for template in missing:
        atomic_copy(template.source, project / template.relative)
    for managed in managed_writes:
        atomic_write(managed.content, project / managed.relative, managed.mode)
    return (
        [template.relative for template in missing] + [item.relative for item in managed_writes],
        unchanged + managed_unchanged,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create missing project runtime files from the installed skill bundle."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--skills-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--mode",
        choices=MATERIALIZABLE_MODES,
        default="default",
        help="materialized startup mode to inject into AGENTS.md/CLAUDE.md (default: default; naive is isolated)",
    )
    parser.add_argument(
        "--minimum-bootstrap",
        action="store_true",
        help="merge only the managed AGENTS.md/CLAUDE.md contract; defer all project templates",
    )
    args = parser.parse_args(argv)
    try:
        bundle = (args.skills_root or skills_root()).expanduser().resolve()
        created, unchanged = materialize(
            args.project_root,
            bundle,
            args.mode,
            minimum_bootstrap=args.minimum_bootstrap,
        )
    except (OSError, RuntimeError) as error:
        print(f"materialize: {error}", file=sys.stderr)
        return 2
    print(
        f"materialize: mode={args.mode} "
        f"scope={'minimum-bootstrap' if args.minimum_bootstrap else 'full'} "
        f"created={len(created)} unchanged={len(unchanged)} "
        f"humanize_assets={'deferred' if args.minimum_bootstrap else 'materialized'} "
        "harnesses=not-installed"
    )
    for relative in created:
        print(f"  created {relative.as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
