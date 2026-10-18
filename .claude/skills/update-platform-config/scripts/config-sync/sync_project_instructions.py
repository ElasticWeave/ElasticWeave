#!/usr/bin/env python3
"""Merge the default-mode-owned project session contract into native root instruction files.

The canonical block lives under the sibling ``default-mode`` skill. This renderer owns only the
marked region in ``AGENTS.md`` and ``CLAUDE.md``; all repository-specific instructions outside
that region remain byte-for-byte unchanged. Both targets are preflighted before the first write.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

from project_paths import project_root, skills_root
from sync_policy import (
    PathState,
    SyncPlan,
    SyncTransactionError,
    capture_path_state,
    commit_plan,
)


START_MARKER = b"<!-- general-agent:project-session-contract:start -->"
END_MARKER = b"<!-- general-agent:project-session-contract:end -->"
MODE_START_MARKER = b"<!-- general-agent:startup-mode:start -->"
MODE_END_MARKER = b"<!-- general-agent:startup-mode:end -->"
MODE_HEADER = re.compile(
    rb"^### Startup operating mode: `(default|fast)`\r?$", re.MULTILINE
)
TARGETS = (Path("AGENTS.md"), Path("CLAUDE.md"))


def canonical_contract(path: Path, content: bytes | None = None) -> bytes:
    if content is None:
        state = capture_path_state(path)
        if state.kind != "regular file":
            raise RuntimeError(f"canonical contract is not a regular file: {path}")
        assert state.content is not None
        content = state.content
    stripped = content.rstrip(b"\r\n")
    if (
        content.count(START_MARKER) != 1
        or content.count(END_MARKER) != 1
        or not content.startswith(START_MARKER)
        or not stripped
        or stripped.splitlines()[-1] != END_MARKER
        or content.find(START_MARKER) >= content.find(END_MARKER)
    ):
        raise RuntimeError(
            f"canonical contract must contain exactly one complete managed block: {path}"
        )
    return content


def _managed_block(current: bytes, target: Path) -> bytes | None:
    """Return only the target's managed block; ignore user-owned surrounding bytes."""

    starts = current.count(START_MARKER)
    ends = current.count(END_MARKER)
    if starts == 0 and ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise RuntimeError(
            f"{target}: managed markers must appear exactly once as a balanced pair"
        )
    start = current.find(START_MARKER)
    end_start = current.find(END_MARKER)
    if start >= end_start:
        raise RuntimeError(f"{target}: managed markers are out of order")
    return current[start : end_start + len(END_MARKER)]


def render_target(current: bytes, managed: bytes, target: Path) -> bytes:
    existing_block = _managed_block(current, target)
    if existing_block is None:
        if not current:
            return managed
        separator = b"" if current.endswith(b"\n\n") else (b"\n" if current.endswith(b"\n") else b"\n\n")
        return current + separator + managed
    start = current.find(START_MARKER)
    end_start = current.find(END_MARKER)
    end = end_start + len(END_MARKER)
    # Newline bytes after the closing marker belong to the repository-owned suffix. Strip only
    # the canonical source's file terminator so replacement preserves that suffix byte-for-byte.
    return current[:start] + managed.rstrip(b"\r\n") + current[end:]


def _startup_mode_section(
    block: bytes, target: Path
) -> tuple[str, bytes] | None:
    """Return the selected startup mode and its exact section body, if present."""

    starts = block.count(MODE_START_MARKER)
    ends = block.count(MODE_END_MARKER)
    if starts == 0 and ends == 0:
        return None
    if starts != 1 or ends != 1:
        raise RuntimeError(
            f"{target}: startup-mode markers must appear exactly once as a balanced pair"
        )
    start = block.find(MODE_START_MARKER)
    end_start = block.find(MODE_END_MARKER)
    if start >= end_start:
        raise RuntimeError(f"{target}: startup-mode markers are out of order")
    body_start = start + len(MODE_START_MARKER)
    body = block[body_start:end_start]
    matches = list(MODE_HEADER.finditer(body))
    if len(matches) != 1:
        raise RuntimeError(
            f"{target}: startup-mode section must declare exactly one default or fast mode"
        )
    return matches[0].group(1).decode("ascii"), body


def _replace_startup_mode_section(
    managed: bytes, body: bytes, source: Path
) -> bytes:
    """Use the already-selected mode body while retaining the canonical shared contract."""

    details = _startup_mode_section(managed, source)
    if details is None:
        raise RuntimeError(
            f"{source}: canonical contract lacks a complete startup-mode section"
        )
    start = managed.find(MODE_START_MARKER) + len(MODE_START_MARKER)
    end = managed.find(MODE_END_MARKER)
    return managed[:start] + body + managed[end:]


def build_project_instruction_plan(
    root: Path, source: Path, targets: tuple[Path, ...] = TARGETS
) -> SyncPlan:
    """Build the complete instruction merge without mutating either target."""

    project = root.expanduser().absolute()
    source_path = source.expanduser().absolute()
    project_state = capture_path_state(project)
    if project_state.kind != "directory":
        raise RuntimeError(f"project root is not a directory: {project}")
    plan = SyncPlan("project-instructions")
    plan.add_directory_chain(project, mode=0o755)
    plan.add_directory_chain(source_path.parent, mode=0o755)
    source_state = plan.add_file_guard(source_path, label="canonical project contract")
    assert source_state.content is not None
    managed = canonical_contract(source_path, source_state.content)

    target_states: list[tuple[Path, Path, PathState, bytes]] = []
    observed_modes: dict[str, bytes] = {}
    for relative in targets:
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"invalid project instruction target: {relative}")
        destination = project / relative
        current = capture_path_state(destination)
        if current.kind not in {"missing", "regular file"}:
            raise RuntimeError(f"{relative}: destination is not a regular file")
        existing = current.content or b""
        details = _startup_mode_section(_managed_block(existing, relative) or b"", relative)
        if details is not None:
            selected, body = details
            previous = observed_modes.get(selected)
            if previous is not None and previous != body:
                raise RuntimeError(
                    f"{relative}: existing {selected} startup-mode sections differ"
                )
            observed_modes[selected] = body
        target_states.append((relative, destination, current, existing))

    if len(observed_modes) > 1:
        modes = ", ".join(sorted(observed_modes))
        raise RuntimeError(f"project instruction targets select conflicting startup modes: {modes}")
    selected_mode = next(iter(observed_modes), "default")
    if selected_mode != "default":
        managed = _replace_startup_mode_section(
            managed, observed_modes[selected_mode], source_path
        )

    changed: list[str] = []
    for relative, destination, current, existing in target_states:
        rendered = render_target(existing, managed, relative)
        mode = (
            current.snapshot.mode
            if current.snapshot is not None
            else 0o644
        )
        if plan.add_write(
            destination,
            rendered,
            mode=mode,
            before=current,
            label=f"project instruction {relative}",
        ):
            changed.append(relative.as_posix())

    plan.metadata["project_instructions_changed"] = tuple(changed)
    plan.metadata["project_instructions_mode"] = selected_mode
    return plan


def sync_project_instructions(
    root: Path, source: Path, targets: tuple[Path, ...] = TARGETS
) -> list[Path]:
    plan = build_project_instruction_plan(root, source, targets)
    commit_plan(plan)
    return [Path(name) for name in plan.metadata["project_instructions_changed"]]


def main() -> int:
    root = project_root()
    source = skills_root() / "default-mode" / "references" / "project-session-contract.md"
    try:
        changed = sync_project_instructions(root, source)
    except (OSError, RuntimeError, SyncTransactionError) as error:
        print(f"[sync-project-instructions] {error}", file=sys.stderr)
        return 2
    names = ", ".join(path.as_posix() for path in changed) or "none"
    print(f"[sync-project-instructions] changed: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
