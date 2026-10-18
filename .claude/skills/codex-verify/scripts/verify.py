#!/usr/bin/env python3
"""Prepare/finalize an independent issue terminal review.

This script never runs an acceptance command and has no dependency on `.pm/accept.sh`,
`.pm/immutable`, or `.pm/verify_base`.  An implementation/check agent must first record fresh
acceptance evidence. PREPARE commits the issue criteria, actual diff, evidence, and verified
semantic model-role identities into a review brief; FINALIZE recomputes the bundle, rejects drift,
and strictly parses the configured reviewer's terminal verdict.
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from review_contract import (
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
    terminal_exit_code,
    validate_item_id,
    verify_stored_artifact,
    write_terminal_artifact,
)


def _repo() -> Path:
    return Path.cwd().resolve()


def _issue_path(repo: Path, item_id: str) -> Path:
    matches = [Path(path) for path in glob.glob(str(repo / ".pm/issues/*" / f"{item_id}.md"))]
    if len(matches) != 1:
        raise ContractError(
            f"issue {item_id!r} must resolve to exactly one .pm/issues/<milestone>/{item_id}.md; found {len(matches)}"
        )
    return matches[0]


def _context_paths(repo: Path, item_id: str) -> list[Path]:
    report = repo / ".pm/reports" / f"{item_id}.md"
    if not report.is_file() or report.is_symlink():
        raise ContractError(
            f"final issue report is required before PREPARE: {report.relative_to(repo)}"
        )
    return [report]


def _brief(bundle) -> str:
    changed = "\n".join(f"- {path}" for path in bundle.changed_paths) or "- (none)"
    builders = ", ".join(value.semantic_role for value in bundle.builder_identities)
    reviewer = bundle.reviewer_identity
    return f"""# Independent issue terminal review — {bundle.item_id}

You are the independent terminal reviewer. Run READ-ONLY as semantic role
`{reviewer.semantic_role}` ({reviewer.backend}/{reviewer.upstream_provider_family}/
{reviewer.model_family}/{reviewer.effort}). The implementation/check agents used role(s)
`{builders}`. These identities and per-writer launch-spec receipts were resolved from the bound,
verified model-role document. If your actual launch identity does not match, return REVISE. Do not
repair files, run implementation, or
invent evidence. Inspect the changed files directly; the pasted diff can be truncated by a UI.

Judge the actual work against every issue/milestone acceptance criterion using all three inputs:

1. the repository diff/current files;
2. the criteria below; and
3. the implementation/check agent's fresh evidence below.

The evidence file is a claim plus check trace, not self-authenticating truth. Cross-check it against
the diff and criteria. Do not accept hardcoding, fixture-only behavior, weakened checks, workaround
claims, or incomplete criteria. Do not use budget, failed tests, debugging, ordinary technical
difficulty, or unfinished implementation as HUMAN_BLOCKED; those are REVISE.

## Terminal meanings

- `COMPLETE`: every criterion is met and the fresh evidence supports it.
- `HUMAN_BLOCKED`: completion cannot proceed solely because a human must provide authorization,
  a product/scope decision, a credential, or an external action. Name exactly one kind and a precise
  request. The implementation must otherwise be as complete as possible.
- `REVISE`: anything the implementation/check agents can still do, any missing/failed check, or
  insufficient proof.

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

Then add `REASONS:` with criterion-by-criterion evidence. Do not repeat any structured field.

## Evidence commitment

`{bundle.commitment}`

## Goal and issue acceptance criteria

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


def _artifact_path(repo: Path, item_id: str) -> Path:
    return repo / ".pm/verdicts" / f"{item_id}.md"


def _revised_artifact(repo: Path, item_id: str, reason: str) -> None:
    # Input failures happen before a trustworthy bundle exists.  Do not synthesize a fake
    # commitment; remove any stale prior verdict so a board transition cannot reuse it.
    artifact = _artifact_path(repo, item_id)
    try:
        _remove_controlled_output(repo, artifact, "issue terminal artifact")
    except ContractError as exc:
        reason = f"{reason}; unsafe stale artifact left in place: {exc}"
    sys.stderr.write(f"[codex-verify] {item_id}: REVISE — {reason}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("id")
    parser.add_argument(
        "--base",
        default="",
        help="task-start commit/ref; default resolves merge-base with origin/main/master or upstream",
    )
    parser.add_argument("--goal", default=".pm/goal.md")
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        help="fresh check-evidence file (repeatable); default .pm/evidence/<id>.md",
    )
    parser.add_argument(
        "--builder-role",
        action="append",
        default=[],
        help="semantic role used by each implementation/check agent (repeatable)",
    )
    parser.add_argument(
        "--reviewer-role",
        default="",
        help="semantic verification role selected by capability policy",
    )
    parser.add_argument(
        "--reviewer-launch-receipt",
        action="append",
        default=[],
        help=(
            "repo-local workflow-native-launch-receipt/v1 launch-spec receipt "
            "(repeat once per --builder-role)"
        ),
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="permit no implementation diff only when acceptance genuinely requires no repository change",
    )
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
        sys.stderr.write(f"[codex-verify] invalid id: {exc}\n")
        return 2
    if args.finalize and args.verify_artifact:
        sys.stderr.write("[codex-verify] --finalize and --verify-artifact are mutually exclusive\n")
        return 2
    artifact_inputs = None
    if args.verify_artifact:
        try:
            artifact_inputs = parse_artifact_inputs(_artifact_path(repo, args.id), "issue")
        except ContractError as exc:
            sys.stderr.write(f"[codex-verify] {args.id}: REVISE — {exc}\n")
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
        issue = _issue_path(repo, args.id)
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
            item_kind="issue",
            base_ref=base_ref,
            criteria_paths=[goal, issue],
            context_paths=_context_paths(repo, args.id),
            evidence_args=evidence_args,
            model_roles_path=Path(CANONICAL_MODEL_ROLES_PATH),
            builder_roles=builders,
            reviewer_role=reviewer,
            reviewer_launch_receipts=[Path(value) for value in launch_receipt_args],
            allow_empty=allow_empty,
            catalog_runner=_test_catalog_runner,
        )
    except ContractError as exc:
        if args.verify_artifact:
            sys.stderr.write(f"[codex-verify] {args.id}: REVISE — {exc}\n")
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
            sys.stderr.write(f"[codex-verify] {args.id}: REVISE — {exc}\n")
            return 2
        print(f"[codex-verify] {args.id}: {note}")
        return 0

    if not args.finalize:
        brief_path = repo / ".pm/review-briefs" / f"{args.id}.md"
        try:
            _write_controlled_output(
                repo,
                brief_path,
                _brief(bundle).encode("utf-8"),
                "issue review brief",
            )
        except ContractError as exc:
            _revised_artifact(repo, args.id, str(exc))
            return 2
        print(
            f"[codex-verify] READY-FOR-NATIVE-REVIEW: {args.id}\n"
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
        f"[codex-verify] {args.id}: {verdict.terminal} -> "
        f"{_artifact_path(repo, args.id).relative_to(repo)}"
    )
    if parser_note:
        sys.stderr.write(f"[codex-verify] reviewer output rejected: {parser_note}\n")
    return terminal_exit_code(verdict.terminal)


if __name__ == "__main__":
    raise SystemExit(main())
