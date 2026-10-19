#!/usr/bin/env python3
"""Prepare/finalize the mandatory independent milestone terminal review."""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

CONTRACT_DIR = Path(__file__).resolve().parents[2] / "codex-verify" / "scripts"
sys.path.insert(0, str(CONTRACT_DIR))

from review_contract import (  # noqa: E402
    CANONICAL_MODEL_ROLES_PATH,
    ContractError,
    TerminalVerdict,
    _remove_controlled_output,
    _write_controlled_output,
    build_review_bundle,
    invoke_native_reviewer,
    lexical_repo_path,
    parse_artifact_inputs,
    parse_terminal_output,
    read_repo_regular_file,
    terminal_exit_code,
    validate_item_id,
    verify_stored_artifact,
    write_terminal_artifact,
)


def _repo() -> Path:
    return Path.cwd().resolve()


def _milestone_path(repo: Path, milestone_id: str) -> Path:
    path = repo / ".pm/milestones" / f"{milestone_id}.md"
    if not path.is_file():
        raise ContractError(f"milestone file does not exist: {path.relative_to(repo)}")
    return path


def _issue_paths(repo: Path, milestone_id: str) -> list[Path]:
    return [Path(path) for path in sorted(glob.glob(str(repo / ".pm/issues" / milestone_id / "*.md")))]


def _context_paths(repo: Path, milestone_id: str, issues: list[Path]) -> list[Path]:
    ids = [milestone_id, *(path.stem for path in issues)]
    reports: list[Path] = []
    for item_id in ids:
        report = repo / ".pm/reports" / f"{item_id}.md"
        if not report.is_file() or report.is_symlink():
            raise ContractError(
                f"final report is required before PREPARE: {report.relative_to(repo)}"
            )
        reports.append(report)
    return reports


def _sealed_checkpoint_path(
    repo: Path, milestone_id: str, *, allow_archived: bool = False
) -> tuple[Path, str]:
    lifecycle = repo / ".pm/self_evolve"
    pending = lifecycle / f"{milestone_id}.pending"
    sealed = lifecycle / f"{milestone_id}.sealed"
    archived = lifecycle / f"{milestone_id}.done"
    if pending.exists():
        raise ContractError(
            f"milestone {milestone_id} self-evolution is still pending; complete every project-memory "
            "and generalized-change mutation, then seal it before terminal review"
        )
    if sealed.is_file() and not sealed.is_symlink():
        if archived.exists():
            raise ContractError(
                f"milestone {milestone_id} has conflicting sealed and archived checkpoints"
            )
        return sealed, sealed.relative_to(repo).as_posix()
    if allow_archived and archived.is_file() and not archived.is_symlink():
        return archived, sealed.relative_to(repo).as_posix()
    if not sealed.is_file() or sealed.is_symlink():
        raise ContractError(
            f"milestone {milestone_id} needs a sealed self-improve checkpoint at "
            f".pm/self_evolve/{milestone_id}.sealed before terminal review"
        )
    raise AssertionError("unreachable")


def _require_post_seal_evidence(bundle, sealed: Path, repo: Path) -> None:
    seal_mtime = read_repo_regular_file(
        repo, sealed, "sealed self-improve checkpoint"
    ).mtime_ns
    for relative in bundle.evidence_paths:
        evidence = read_repo_regular_file(
            repo, repo / relative, "acceptance evidence"
        )
        if evidence.mtime_ns < seal_mtime:
            raise ContractError(
                f"acceptance evidence {relative} predates sealed self-improve checkpoint; "
                "delegate the post-seal checks again and replace the evidence"
            )


def _brief(bundle) -> str:
    changed = "\n".join(f"- {path}" for path in bundle.changed_paths) or "- (none)"
    builders = ", ".join(value.semantic_role for value in bundle.builder_identities)
    reviewer = bundle.reviewer_identity
    return f"""# Independent milestone terminal review — {bundle.item_id}

You are the independent terminal reviewer. Run READ-ONLY as semantic role
`{reviewer.semantic_role}` ({reviewer.backend}/{reviewer.upstream_provider_family}/
{reviewer.model_family}/{reviewer.effort}). The implementation/check agents used role(s)
`{builders}`. These identities and per-writer launch-spec receipts were resolved from the bound,
verified model-role document. If your actual launch identity does not match, return REVISE. Do not
repair the work. Inspect the actual
files and diff; reports are claims, and evidence is a check trace that must be cross-checked.

Review every milestone and child-issue acceptance criterion. Reduce each deliverable to its real
objective and ask whether the approach is sound, the progress is real/general (not hardcoded or
surface-only), the evidence actually tests the required behavior, and the milestone advances the
pinned goal. Passing checks are necessary evidence, not proof of soundness.

The reviewed work includes finalized self-evolution from the sealed checkpoint. Inspect every
`.memory/**` residue as project-local memory. Inspect any lemma, learned reference, skill, hook, or
workflow change as a separately reviewed generalized change: reject project-specific promotion,
unsupported reuse claims, or any self-evolution mutation not covered by the post-seal checks. A
terminal COMPLETE commits to these final bytes; the completion path permits no later deliverable,
memory, report, or UI mutation after the atomic PM status transition.

## Terminal meanings

- `COMPLETE`: all milestone/issue criteria are met, the approach makes sense, and fresh evidence
  supports the actual diff.
- `HUMAN_BLOCKED`: only a human authorization, product/scope decision, credential, or external
  action prevents completion. Name the exact request; unfinished implementation, failed/missing
  checks, budgets, debugging, and ordinary technical difficulty are REVISE.
- `REVISE`: continue delegated implementation/check work.

Your first four lines must be exactly:

```text
TERMINAL: COMPLETE|HUMAN_BLOCKED|REVISE
EVIDENCE-COMMITMENT: {bundle.commitment}
REVIEWER-ROLE: {reviewer.semantic_role}
REVIEWER-IDENTITY-SHA256: {reviewer.digest()}
```

For HUMAN_BLOCKED, immediately add exactly:

```text
HUMAN-REQUEST-KIND: authorization|decision|credential|external-action
HUMAN-REQUEST: <one concrete request naming what the human must provide/do>
```

Then add `REASONS:` with a finding for every deliverable. Do not repeat structured fields.

## Evidence commitment

`{bundle.commitment}`

## Goal, milestone, and child-issue acceptance criteria

{bundle.criteria_text}

## Reports/context (claims; verify them)

{bundle.context_text or "(none)"}

## Changed paths

{changed}

## Committed diff ({bundle.base_sha}..{bundle.head_sha})

```diff
{bundle.committed_diff[:30000] or "(none)"}
```

## Current worktree diff

```diff
{bundle.worktree_diff[:30000] or "(none)"}
```

## Fresh implementation/check evidence

{bundle.evidence_text}
"""


def _artifact_path(repo: Path, milestone_id: str) -> Path:
    return repo / ".pm/reviews" / f"{milestone_id}.md"


def _revised_artifact(repo: Path, milestone_id: str, reason: str) -> None:
    try:
        _remove_controlled_output(
            repo,
            _artifact_path(repo, milestone_id),
            "milestone terminal artifact",
        )
    except ContractError as exc:
        reason = f"{reason}; unsafe stale artifact left in place: {exc}"
    sys.stderr.write(f"[milestone-review] {milestone_id}: REVISE — {reason}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("id")
    parser.add_argument(
        "--base",
        default="",
        help="task-start commit/ref; default resolves merge-base with origin/main/master or upstream",
    )
    parser.add_argument("--goal", default=".pm/goal.md")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--builder-role", action="append", default=[])
    parser.add_argument("--reviewer-role", default="")
    parser.add_argument("--reviewer-launch-receipt", action="append", default=[])
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument(
        "--verify-artifact",
        action="store_true",
        help="mechanically recompute current inputs and verify the stored terminal artifact",
    )
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    _test_reviewer_executable: Path | None = None,
    _test_catalog_runner=None,
) -> int:
    args = parse_args(argv)
    repo = _repo()
    try:
        validate_item_id(args.id)
    except ContractError as exc:
        sys.stderr.write(f"[milestone-review] invalid id: {exc}\n")
        return 2
    if args.finalize and args.verify_artifact:
        sys.stderr.write("[milestone-review] --finalize and --verify-artifact are mutually exclusive\n")
        return 2
    artifact_inputs = None
    if args.verify_artifact:
        try:
            artifact_inputs = parse_artifact_inputs(_artifact_path(repo, args.id), "milestone")
        except ContractError as exc:
            sys.stderr.write(f"[milestone-review] {args.id}: REVISE — {exc}\n")
            return 2
    builders = (
        [value.semantic_role for value in artifact_inputs.builder_identities]
        if artifact_inputs
        else args.builder_role
    )
    reviewer = (
        artifact_inputs.reviewer_identity.semantic_role
        if artifact_inputs
        else args.reviewer_role
    )
    try:
        milestone = _milestone_path(repo, args.id)
        issues = _issue_paths(repo, args.id)
        sealed_checkpoint, sealed_logical_path = _sealed_checkpoint_path(
            repo, args.id, allow_archived=args.verify_artifact
        )
        goal_arg = artifact_inputs.goal_path if artifact_inputs else args.goal
        goal = lexical_repo_path(repo, Path(goal_arg), "goal/acceptance file")
        if not goal.is_file():
            raise ContractError(f"goal/acceptance file does not exist: {args.goal}")
        base_ref = artifact_inputs.base_sha if artifact_inputs else args.base
        evidence_args = list(artifact_inputs.evidence_paths) if artifact_inputs else args.evidence
        allow_empty = artifact_inputs.allow_empty if artifact_inputs else args.allow_empty
        launch_receipt_args = (
            [value.path for value in artifact_inputs.reviewer_launch_receipts]
            if artifact_inputs
            else args.reviewer_launch_receipt
        )
        bundle = build_review_bundle(
            repo=repo,
            item_id=args.id,
            item_kind="milestone",
            base_ref=base_ref,
            criteria_paths=[goal, milestone, *issues],
            context_paths=[*_context_paths(repo, args.id, issues), sealed_checkpoint],
            evidence_args=evidence_args,
            model_roles_path=Path(CANONICAL_MODEL_ROLES_PATH),
            builder_roles=builders,
            reviewer_role=reviewer,
            reviewer_launch_receipts=[Path(value) for value in launch_receipt_args],
            allow_empty=allow_empty,
            context_aliases={sealed_checkpoint: sealed_logical_path},
            catalog_runner=_test_catalog_runner,
        )
        _require_post_seal_evidence(bundle, sealed_checkpoint, repo)
    except ContractError as exc:
        if args.verify_artifact:
            sys.stderr.write(f"[milestone-review] {args.id}: REVISE — {exc}\n")
        else:
            _revised_artifact(repo, args.id, str(exc))
        return 2

    if args.verify_artifact:
        assert artifact_inputs is not None
        try:
            note = verify_stored_artifact(
                repo=repo,
                bundle=bundle,
                artifact=artifact_inputs,
                prompt=_brief(bundle),
                test_executable=_test_reviewer_executable,
            )
        except ContractError as exc:
            sys.stderr.write(f"[milestone-review] {args.id}: REVISE — {exc}\n")
            return 2
        print(f"[milestone-review] {args.id}: {note}")
        return 0

    if not args.finalize:
        brief_path = repo / ".pm/review-briefs" / f"{args.id}-milestone.md"
        try:
            _write_controlled_output(
                repo,
                brief_path,
                _brief(bundle).encode("utf-8"),
                "milestone review brief",
            )
        except ContractError as exc:
            _revised_artifact(repo, args.id, str(exc))
            return 2
        print(
            f"[milestone-review] READY-FOR-NATIVE-REVIEW: {args.id}\n"
            f"  commitment: {bundle.commitment}\n"
            f"  FINALIZE will launch role '{bundle.reviewer_identity.semantic_role}' through its bound native read-only backend\n"
            "  rerun this command with the same role/base/evidence arguments plus --finalize"
        )
        return 0

    try:
        reviewer_invocation = invoke_native_reviewer(
            repo=repo,
            bundle=bundle,
            prompt=_brief(bundle),
            test_executable=_test_reviewer_executable,
        )
    except ContractError as exc:
        _revised_artifact(repo, args.id, str(exc))
        return 2
    reviewer_output = reviewer_invocation.output.text
    parser_note = ""
    try:
        verdict = parse_terminal_output(reviewer_output, bundle.commitment, bundle.reviewer_identity)
    except ContractError as exc:
        verdict = TerminalVerdict("REVISE")
        parser_note = str(exc)
    try:
        write_terminal_artifact(
            path=_artifact_path(repo, args.id),
            bundle=bundle,
            verdict=verdict,
            reviewer_invocation=reviewer_invocation,
            allow_empty=allow_empty,
            goal_path=goal.relative_to(repo).as_posix(),
            parser_note=parser_note,
        )
    except ContractError as exc:
        _revised_artifact(repo, args.id, str(exc))
        return 2
    print(
        f"[milestone-review] {args.id}: {verdict.terminal} -> "
        f"{_artifact_path(repo, args.id).relative_to(repo)}"
    )
    if parser_note:
        sys.stderr.write(f"[milestone-review] reviewer output rejected: {parser_note}\n")
    return terminal_exit_code(verdict.terminal)


if __name__ == "__main__":
    raise SystemExit(main())
