#!/usr/bin/env python3
"""Read-only local preflight for an exact, independently reviewed PR merge."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# ``pr_preflight.py`` is owned by the review skill, but the workflow proof and
# sibling review helpers live at the shared skill root.  Keep this derived from
# the file location so callers can execute the script from any checkout.
SKILLS = Path(__file__).resolve().parents[3]
CONTRACT_DIR = SKILLS / "codex-verify" / "scripts"
SCRIPTS = Path(__file__).resolve().parent
WORKFLOW_PROOF_HELPER = (
    SKILLS / "subagent-workflow" / "scripts" / "native" / "reward_hacking_evidence.py"
)
sys.path.insert(0, str(CONTRACT_DIR))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from review_contract import ContractError, parse_artifact_inputs, validate_item_id  # noqa: E402
from trusted_git import GitBindingError, run_git  # noqa: E402


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=("issue", "milestone"))
    parser.add_argument("--id", required=True)
    parser.add_argument("--approved-head", required=True)
    return parser.parse_args(argv)


def _repo() -> Path:
    result = run_git(["rev-parse", "--show-toplevel"], cwd=Path.cwd())
    if result.returncode or not result.stdout.strip():
        raise ContractError("current directory is not inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def _item_path(repo: Path, kind: str, item_id: str) -> Path:
    if kind == "milestone":
        candidates = [repo / ".pm/milestones" / f"{item_id}.md"]
    else:
        candidates = list((repo / ".pm/issues").glob(f"*/{item_id}.md"))
    candidates = [path for path in candidates if path.is_file() and not path.is_symlink()]
    if len(candidates) != 1:
        raise ContractError(
            f"expected exactly one regular {kind} file for {item_id!r}; found {len(candidates)}"
        )
    return candidates[0].resolve()


def _frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ContractError(f"board item lacks canonical frontmatter: {path}")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ContractError(f"board item has unterminated frontmatter: {path}") from exc
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if key in fields:
            raise ContractError(f"board item has duplicate frontmatter field {key!r}")
        fields[key] = value
    if not fields.get("status"):
        raise ContractError("board item needs exactly one non-empty status field")
    return fields


def _independent_artifact_path(repo: Path, kind: str, item_id: str) -> Path:
    directory = "verdicts" if kind == "issue" else "reviews"
    path = repo / ".pm" / directory / f"{item_id}.md"
    if not path.is_file() or path.is_symlink():
        raise ContractError(f"canonical terminal artifact is unavailable: {path.relative_to(repo)}")
    return path.resolve()


def _reviewer_helper(kind: str) -> Path:
    if kind == "issue":
        return SKILLS / "codex-verify" / "scripts" / "verify.py"
    return SKILLS / "milestone-review" / "scripts" / "milestone_review.py"


def _canonical_workflow_proof_path(repo: Path, item_id: str) -> Path:
    spec = importlib.util.spec_from_file_location(
        "_review_pr_workflow_proof", WORKFLOW_PROOF_HELPER
    )
    if spec is None or spec.loader is None:
        raise ContractError(
            f"cannot load workflow proof helper: {WORKFLOW_PROOF_HELPER}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    relative = module.canonical_workflow_proof_relative(item_id)
    return repo / relative


def _run_independent(
    repo: Path,
    kind: str,
    item_id: str,
    approved_head: str,
    *,
    test_reviewer_executable: Path | None = None,
    test_catalog_runner=None,
) -> tuple[Path, str, dict[str, object]]:
    artifact = _independent_artifact_path(repo, kind, item_id)
    parsed = parse_artifact_inputs(artifact, kind)
    if parsed.terminal != "COMPLETE":
        raise ContractError(f"terminal artifact is {parsed.terminal}, not COMPLETE")
    if parsed.head_sha != approved_head:
        raise ContractError(
            f"approved HEAD mismatch: artifact {parsed.head_sha}, dispatch {approved_head}"
        )
    if (test_reviewer_executable is None) != (test_catalog_runner is None):
        raise ContractError(
            "private test reviewer dependencies must be supplied together"
        )
    if test_reviewer_executable is None:
        verified = subprocess.run(
            [sys.executable, str(_reviewer_helper(kind)), item_id, "--verify-artifact"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        helper = _reviewer_helper(kind)
        spec = importlib.util.spec_from_file_location(
            "_review_pr_test_reviewer", helper
        )
        if spec is None or spec.loader is None:
            raise ContractError(f"cannot load test reviewer helper: {helper}")
        module = importlib.util.module_from_spec(spec)
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                spec.loader.exec_module(module)
                code = module.main(
                    [item_id, "--verify-artifact"],
                    _test_reviewer_executable=test_reviewer_executable,
                    _test_catalog_runner=test_catalog_runner,
                )
        except Exception as exc:
            verified = subprocess.CompletedProcess(
                [], 2, stdout.getvalue(), f"{stderr.getvalue()}{exc}\n"
            )
        else:
            verified = subprocess.CompletedProcess(
                [], code, stdout.getvalue(), stderr.getvalue()
            )
    if verified.returncode != 0:
        detail = (verified.stderr or verified.stdout).strip()
        raise ContractError(f"terminal artifact is not current: {detail}")
    return artifact, parsed.commitment, {}


def _run_machine_gated(
    repo: Path, item_id: str, approved_head: str
) -> tuple[Path, str, dict[str, object]]:
    proof = _canonical_workflow_proof_path(repo, item_id)
    if not proof.is_file() or proof.is_symlink():
        raise ContractError(
            f"canonical machine-gated proof is unavailable: {proof.relative_to(repo)}"
        )
    helper = WORKFLOW_PROOF_HELPER.resolve()
    result = subprocess.run(
        [
            sys.executable,
            str(helper),
            "verify-proof",
            "--root",
            str(repo),
            "--item-id",
            item_id,
            "--proof",
            str(proof),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            key: value
            for key, value in os.environ.items()
            if key != "GENERAL_AGENT_CONTROLLER_RUNTIME"
            and not key.startswith("GENERAL_AGENT_ALLOW_")
        },
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ContractError(f"machine-gated proof is not current: {detail}")
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ContractError("machine-gated proof verifier returned malformed JSON") from exc
    if not isinstance(receipt, dict) or receipt.get("valid") is not True:
        raise ContractError("machine-gated proof verifier did not attest valid: true")
    binding = receipt.get("stateBinding")
    if not isinstance(binding, dict) or binding.get("rootHead") != approved_head:
        raise ContractError(
            "approved HEAD mismatch: machine-gated state binding does not equal dispatch HEAD"
        )
    commitment = receipt.get("evidenceCommitment")
    if not isinstance(commitment, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", commitment):
        raise ContractError("machine-gated proof verifier returned no canonical commitment")
    return proof.resolve(), commitment, {
        "designId": receipt.get("designId", ""),
        "runId": receipt.get("runId", ""),
        "proof": receipt.get("proof", {}),
    }


def main(
    argv: list[str] | None = None,
    *,
    _test_reviewer_executable: Path | None = None,
    _test_catalog_runner=None,
) -> int:
    args = _args(argv)
    try:
        validate_item_id(args.id)
        if not re.fullmatch(r"[0-9a-f]{40,64}", args.approved_head):
            raise ContractError("--approved-head must be a full lowercase commit SHA")
        repo = _repo()
        item = _item_path(repo, args.kind, args.id)
        fields = _frontmatter(item)
        status = fields["status"]
        if status == "done":
            raise ContractError(
                "item is already done; the review-owned PR merge requires the reviewed pre-transition board state"
            )
        review_policy = fields.get("review_policy") or "independent"
        if review_policy not in ("independent", "machine-gated"):
            raise ContractError(f"unsupported review_policy={review_policy!r}")
        if review_policy == "machine-gated":
            if args.kind != "issue" or fields.get("dispatch_lane") != "workflow":
                raise ContractError(
                    "machine-gated is valid only for a workflow-lane issue"
                )
            artifact, commitment, extra = _run_machine_gated(
                repo, args.id, args.approved_head
            )
        else:
            artifact, commitment, extra = _run_independent(
                repo,
                args.kind,
                args.id,
                args.approved_head,
                test_reviewer_executable=_test_reviewer_executable,
                test_catalog_runner=_test_catalog_runner,
            )
        current = run_git(
            ["rev-parse", "--verify", "HEAD^{commit}"],
            cwd=repo,
        )
        if current.returncode or current.stdout.strip() != args.approved_head:
            raise ContractError("current HEAD no longer equals the approved reviewed HEAD")
    except (ContractError, GitBindingError, OSError) as exc:
        sys.stderr.write(f"[review-pr] REFUSED: {exc}\n")
        return 2

    print(
        json.dumps(
            {
                "artifact": artifact.relative_to(repo).as_posix(),
                "approvedHead": args.approved_head,
                "commitment": commitment,
                "itemId": args.id,
                "itemKind": args.kind,
                "reviewPolicy": review_policy,
                "status": status,
                "terminal": "COMPLETE",
                **extra,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
