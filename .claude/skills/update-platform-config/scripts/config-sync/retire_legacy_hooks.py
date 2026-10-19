#!/usr/bin/env python3
"""Remove only exact package-owned executable artifacts that have been retired."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path
import stat
import sys
from typing import Mapping

from project_paths import project_root
from sync_policy import (
    PathSnapshot,
    PathState,
    SyncPlan,
    capture_path_state,
    commit_plan,
    lstat_snapshot,
    path_kind,
    read_regular_bytes,
)


LEGACY_HOOK_SHA256: dict[Path, str] = {
    Path(".omp/hooks/pre/flow-start-lint.ts"): (
        "156dc87ee37cc3e7ab480a10bea3f3fc21f7a45361d334671559decce6b3f021"
    ),
    Path(".omp/hooks/pre/workflow-dispatch.ts"): (
        "34ed1aaac3c1bd2a0ed79f65813a07d8b260b0b64c50deb2d5ccf03d918b6b37"
    ),
}
LEGACY_SCHEDULER = Path(".humanize/flows/scheduler.py")
LEGACY_PROJECT_ARTIFACT_SHA256: dict[Path, str] = {
    **LEGACY_HOOK_SHA256,
    # Root and canonical Scheduler bytes shipped by c58c75a before the directory migration.
    LEGACY_SCHEDULER: (
        "c122fbf891b175e90ddd6a02daf43ab17345d3468cc57b0fd77edc9ab30334b5"
    ),
}


@dataclass(frozen=True)
class RetiredHookTarget:
    relative: Path
    expected: PathSnapshot | None
    legacy_sha256: str
    preimage: PathState


@dataclass(frozen=True)
class RetiredHookPlan:
    project_root: Path
    targets: tuple[RetiredHookTarget, ...]


def _absolute_without_resolving(path: Path) -> Path:
    return path.expanduser().absolute()


def _validate_relative(relative: Path) -> None:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RuntimeError(f"invalid retired-artifact path: {relative}")


def _parents_are_real_directories(root: Path, relative: Path) -> bool:
    root_snapshot = lstat_snapshot(root)
    if root_snapshot is None or not stat.S_ISDIR(root_snapshot.mode):
        raise RuntimeError(
            f"project root must be a real directory: {root} ({path_kind(root_snapshot)})"
        )

    current = root
    for component in relative.parent.parts:
        current /= component
        snapshot = lstat_snapshot(current)
        if snapshot is None:
            return False
        if not stat.S_ISDIR(snapshot.mode):
            raise RuntimeError(
                f"{relative}: parent {current} is a {path_kind(snapshot)}, not a real directory"
            )
    return True


def _current_snapshot(root: Path, relative: Path) -> PathSnapshot | None:
    if not _parents_are_real_directories(root, relative):
        return None
    return lstat_snapshot(root / relative)


def _verify_legacy_regular_file(
    root: Path,
    relative: Path,
    expected_sha256: str,
    snapshot: PathSnapshot,
) -> None:
    if not stat.S_ISREG(snapshot.mode):
        raise RuntimeError(
            f"{relative}: retired-artifact target is a {path_kind(snapshot)}, not a regular file"
        )
    content, opened_snapshot = read_regular_bytes(root / relative)
    if opened_snapshot != snapshot:
        raise RuntimeError(f"{relative}: changed while retirement was preflighted")
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"{relative}: bytes do not match the exact package-owned legacy artifact; preserving it"
        )


def preflight_retired_hooks(
    root: Path,
    legacy_hooks: Mapping[Path, str] = LEGACY_PROJECT_ARTIFACT_SHA256,
) -> RetiredHookPlan:
    """Inspect every target before deletion; ambiguity leaves the whole set untouched."""
    project = _absolute_without_resolving(root)
    targets: list[RetiredHookTarget] = []
    errors: list[str] = []

    for relative, expected_sha256 in legacy_hooks.items():
        try:
            _validate_relative(relative)
            snapshot = _current_snapshot(project, relative)
            if snapshot is not None:
                _verify_legacy_regular_file(
                    project, relative, expected_sha256, snapshot
                )
            preimage = capture_path_state(project / relative)
            if preimage.snapshot != snapshot:
                raise RuntimeError(f"{relative}: changed while retirement was preflighted")
            targets.append(
                RetiredHookTarget(relative, snapshot, expected_sha256, preimage)
            )
        except (OSError, RuntimeError) as error:
            errors.append(str(error))

    if errors:
        raise RuntimeError("retired-artifact conflicts:\n  " + "\n  ".join(errors))
    return RetiredHookPlan(project, tuple(targets))


def build_retired_hooks_plan(plan: RetiredHookPlan) -> SyncPlan:
    """Convert an exact retirement preflight into recoverable delete actions."""

    transaction = SyncPlan("retired-hooks")
    removed: list[str] = []
    for target in plan.targets:
        absolute = plan.project_root / target.relative
        if target.expected is None:
            parent_snapshot = lstat_snapshot(absolute.parent)
            if parent_snapshot is not None and stat.S_ISDIR(parent_snapshot.mode):
                transaction.add_directory_chain(absolute.parent, mode=0o755)
            transaction.add_guard(
                absolute,
                target.preimage,
                label=f"absent retired artifact {target.relative}",
            )
            continue
        transaction.add_directory_chain(absolute.parent, mode=0o755)
        transaction.add_delete(
            absolute,
            before=target.preimage,
            allowed_kinds=frozenset({"regular file"}),
            label=f"exact retired artifact {target.relative}",
        )
        removed.append(target.relative.as_posix())
    transaction.metadata["retired_hooks"] = tuple(removed)
    return transaction


def plan_retired_hooks(
    root: Path,
    legacy_hooks: Mapping[Path, str] = LEGACY_PROJECT_ARTIFACT_SHA256,
) -> SyncPlan:
    return build_retired_hooks_plan(preflight_retired_hooks(root, legacy_hooks))


def apply_retired_hooks(plan: RetiredHookPlan) -> list[Path]:
    """Commit retirement through the shared recoverable transaction boundary."""

    transaction = build_retired_hooks_plan(plan)
    commit_plan(transaction)
    return [Path(name) for name in transaction.metadata["retired_hooks"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retire exact package-owned executable artifacts without following links."
    )
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = preflight_retired_hooks(args.project_root or project_root())
        removed = [] if args.check else apply_retired_hooks(plan)
    except (OSError, RuntimeError) as error:
        print(f"[retire-legacy-artifacts] {error}", file=sys.stderr)
        return 2
    names = ", ".join(path.as_posix() for path in removed) or "none"
    action = "preflighted" if args.check else "removed"
    print(f"[retire-legacy-artifacts] {action}: {names}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
