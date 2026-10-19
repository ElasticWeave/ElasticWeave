#!/usr/bin/env python3
"""constraint_gate.py — compile constraint.md's deterministic block into path protection
and produce deterministic commit-review evidence.

The deterministic half of constraint.md (the JSON fence marked
`constraints:deterministic`) holds the HARD, no-judgment rules:
  - protected_paths        : globs that must not appear in a commit's staged diff
  - forbidden_diff_patterns: regexes that must NOT appear in ADDED lines
  - pre_commit_checks      : focused argv arrays (run in repo root, without a shell) that must exit 0

Two-stage decision: deterministic rules run first. If they pass, the commit is allowed immediately
(no model call, fast, headless-safe). If they flag, the caller resolves the actual writer semantic
role and dispatches `commit-review-openai-writer` or `commit-review-anthropic-writer` to receive the
evidence and decide BLOCK vs ALLOW; omitted/mismatched identity or an unavailable reviewer fails closed.

The repo is the canonical policy source. This script (a) emits the protected-paths JSON consumed
by the shared platform protections, and (b) runs the deterministic commit check invoked by the
resolved direction-specific native commit-review agent.

Subcommands:
  protected-paths  Print constraint.md §3 protected_paths as a JSON array of path PREFIXES
                   (globs normalized) for inspection.
  install-protected-paths
                   Atomically validate and install that array at .omp/hooks/protected-paths.json.
  check-commit     Run the deterministic stage on the STAGED diff (+ unstaged tracked with
                   --include-unstaged, for `git commit -a`). Deterministic rules first; a FLAG goes
                   back to the resolved direction-specific commit reviewer as exit 3 with an evidence
                   brief. Exit 2 is a deterministic/policy failure. That reviewer runs this.
  show             Print the parsed deterministic block (debug).

Paths resolve from --constraint (default: <repo>/constraint.md). The default-mode skill's constraint
template is a bootstrap source for first-time protected-path materialization only; it cannot
authorize a commit or replace existing project protections. --repo defaults to git toplevel of cwd.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from fnmatch import fnmatch

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))  # .claude/skills/constraints-to-hooks/scripts -> repo root


class PolicyError(RuntimeError):
    """A deterministic policy or its required Git evidence is unsafe to consume."""


def _run(argv, cwd=None):
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        shell=False,
    )


def _git(repo, *args):
    try:
        result = _run(["git", *args], cwd=repo)
    except OSError as error:
        raise PolicyError(f"git {' '.join(args)} could not start: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        tail = " | ".join(detail[-3:]) or "no diagnostic output"
        raise PolicyError(f"git {' '.join(args)} failed with exit {result.returncode}: {tail}")
    return result.stdout


def repo_root(start=None):
    r = _run(["git", "rev-parse", "--show-toplevel"], cwd=start or os.getcwd())
    return r.stdout.strip() if r.returncode == 0 else (start or os.getcwd())


def find_constraint_file(repo, explicit=None):
    return select_constraint_source(repo, explicit)[0]


def _fallback_constraint_path():
    return os.path.join(
        FRAMEWORK_ROOT,
        ".claude",
        "skills",
        "default-mode",
        "references",
        "constraint.template.md",
    )


def select_constraint_source(repo, explicit=None):
    """Return (path, kind), preserving unsafe project entries for fail-closed parsing."""
    fallback = _fallback_constraint_path()
    if explicit is not None:
        kind = "fallback" if os.path.realpath(os.fspath(explicit)) == os.path.realpath(fallback) else "explicit"
        return explicit, kind
    project = os.path.join(repo, "constraint.md")
    if os.path.lexists(project):
        return project, "project"
    if os.path.lexists(fallback):
        return fallback, "fallback"
    return None, "missing"


def select_install_constraint_source(repo, explicit=None):
    """Select only the target policy, with the bundle template as a bootstrap fallback."""
    canonical = os.path.abspath(os.path.join(repo, "constraint.md"))
    fallback = os.path.abspath(_fallback_constraint_path())
    if explicit is None:
        if os.path.lexists(canonical):
            return canonical, "project"
        if os.path.lexists(fallback):
            return fallback, "fallback"
        return None, "missing"

    explicit_path = os.path.abspath(os.fspath(explicit))
    if explicit_path == canonical:
        return canonical, "project"
    if explicit_path == fallback and not os.path.lexists(canonical):
        return fallback, "fallback"
    raise PolicyError(
        "install policy source must be the target repository canonical constraint.md "
        "or the exact bundle fallback when constraint.md is missing"
    )


def glob_to_prefix(g):
    """Normalize a constraint.md protected-path glob to a prefix the protect-paths gate matches:
    `tests/**` -> `tests/`; `foo/*.txt` -> `foo/`; a wildcard-free path stays as-is."""
    g = g.strip().replace("\\", "/")
    if g.endswith("/**"):
        return g[:-2]  # keep trailing slash → dir prefix
    wildcard_positions = [index for index in (g.find("*"), g.find("?"), g.find("[")) if index != -1]
    if not wildcard_positions:
        return g
    wildcard = min(wildcard_positions)
    slash = g.rfind("/", 0, wildcard)
    return g[: slash + 1] if slash != -1 else g[:wildcard]


def parse_block(constraint_path):
    """Extract the JSON fence following the `constraints:deterministic` marker."""
    if not constraint_path:
        return {}
    try:
        if os.path.islink(constraint_path) or not os.path.isfile(constraint_path):
            raise PolicyError("policy source must be one existing ordinary file")
        with open(constraint_path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeError, PolicyError) as error:
        print(f"constraint_gate: cannot read deterministic policy: {error}", file=sys.stderr)
        return None
    return parse_block_text(text)


def parse_block_text(text):
    """Parse and schema-check one policy source snapshot."""
    m = re.search(r"constraints:deterministic\s*-->\s*```(?:json)?\s*(.*?)```", text, re.S)
    if not m:
        print("constraint_gate: deterministic policy fence is missing", file=sys.stderr)
        return None
    try:
        block = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"constraint_gate: bad JSON in deterministic block: {e}", file=sys.stderr)
        return None   # present-but-unparseable -> caller fails CLOSED (a broken policy must not silently pass)
    try:
        return validate_block(block)
    except PolicyError as error:
        print(f"constraint_gate: invalid deterministic policy: {error}", file=sys.stderr)
        return None


def validate_block(block):
    """Return a schema-checked policy. Commands are argv arrays, never shell source."""
    if not isinstance(block, dict):
        raise PolicyError("deterministic block must be a JSON object")
    allowed = {"protected_paths", "forbidden_diff_patterns", "pre_commit_checks", "notes"}
    unknown = sorted(set(block) - allowed)
    if unknown:
        raise PolicyError(f"unknown field(s): {', '.join(unknown)}")
    required = ("protected_paths", "forbidden_diff_patterns", "pre_commit_checks")
    missing = [field for field in required if field not in block]
    if missing:
        raise PolicyError(f"missing field(s): {', '.join(missing)}")

    protected = block["protected_paths"]
    if not isinstance(protected, list):
        raise PolicyError("protected_paths must be an array of repo-relative strings")
    for index, value in enumerate(protected):
        if not isinstance(value, str) or not value.strip() or "\0" in value or "\n" in value or "\r" in value:
            raise PolicyError(f"protected_paths[{index}] must be one non-empty single-line string")
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or any(part == ".." for part in normalized.split("/")):
            raise PolicyError(f"protected_paths[{index}] must stay inside the repository")
        prefix = glob_to_prefix(value)
        if not prefix or prefix in (".", "./"):
            raise PolicyError(f"protected_paths[{index}] must compile to a non-root path prefix")

    patterns = block["forbidden_diff_patterns"]
    if not isinstance(patterns, list):
        raise PolicyError("forbidden_diff_patterns must be an array of regex strings")
    for index, value in enumerate(patterns):
        if not isinstance(value, str) or not value or "\0" in value:
            raise PolicyError(f"forbidden_diff_patterns[{index}] must be a non-empty string")
        try:
            re.compile(value)
        except re.error as error:
            raise PolicyError(f"forbidden_diff_patterns[{index}] is invalid: {error}") from error

    checks = block["pre_commit_checks"]
    if not isinstance(checks, list):
        raise PolicyError("pre_commit_checks must be an array of argv arrays")
    for index, argv in enumerate(checks):
        if not isinstance(argv, list) or not argv:
            raise PolicyError(f"pre_commit_checks[{index}] must be a non-empty argv array")
        if any(not isinstance(arg, str) or "\0" in arg for arg in argv):
            raise PolicyError(f"pre_commit_checks[{index}] arguments must be NUL-free strings")
        if not argv[0]:
            raise PolicyError(f"pre_commit_checks[{index}][0] must name an executable")
    if "notes" in block and not isinstance(block["notes"], str):
        raise PolicyError("notes must be a string when present")
    return block


def _git_names(repo, *, staged):
    args = ["diff", "--name-only", "--no-renames", "-z"]
    if staged:
        args.insert(1, "--cached")
    return [name for name in _git(repo, *args).split("\0") if name]


def _git_diff(repo, *, staged, unified=None):
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
    if staged:
        args.append("--cached")
    if unified is not None:
        args.append(f"--unified={unified}")
    return _git(repo, *args)


def _policy_source_dirty(repo, constraint_path):
    """Bind executable policy to HEAD bytes, including ignored and index-only candidates."""
    if not constraint_path:
        return None
    repo_abs = os.path.abspath(repo)
    policy_abs = os.path.abspath(os.fspath(constraint_path))
    try:
        if os.path.commonpath([repo_abs, policy_abs]) != repo_abs:
            raise PolicyError("policy source must resolve inside the repository")
    except ValueError as error:
        raise PolicyError("policy source cannot share the repository path domain") from error
    relative = os.path.relpath(policy_abs, repo_abs).replace(os.sep, "/")
    head_entry = _head_blob_entry(repo_abs, relative)
    if head_entry is None:
        return relative if os.path.lexists(policy_abs) else None
    _, head_oid = head_entry
    index = _run(["git", "diff-index", "--quiet", "--no-ext-diff", "HEAD", "--", relative], cwd=repo_abs)
    if index.returncode == 1:
        return relative
    if index.returncode != 0:
        detail = (index.stderr or index.stdout).strip() or "no diagnostic output"
        raise PolicyError(f"git diff-index failed with exit {index.returncode}: {detail}")
    if os.path.islink(policy_abs) or not os.path.isfile(policy_abs):
        return relative
    worktree_oid = _git(repo_abs, "hash-object", "--no-filters", "--", relative).strip()
    return relative if worktree_oid != head_oid else None


def _policy_source_binding(constraint_path):
    """Bind a selected policy path to the Git repository that reviews those exact bytes."""
    if not constraint_path:
        raise PolicyError("no deterministic policy source is available")
    policy_abs = os.path.abspath(os.fspath(constraint_path))
    source_parent = os.path.dirname(policy_abs)
    source_repo = os.path.abspath(_git(source_parent, "rev-parse", "--show-toplevel").strip())
    try:
        if os.path.commonpath([source_repo, policy_abs]) != source_repo:
            raise PolicyError("policy source must resolve inside its reviewing repository")
    except ValueError as error:
        raise PolicyError("policy source cannot share its reviewing repository path domain") from error
    relative = os.path.relpath(policy_abs, source_repo).replace(os.sep, "/")
    return source_repo, relative


def _policy_label(source_repo, relative, target_repo):
    if os.path.abspath(source_repo) == os.path.abspath(target_repo):
        return relative
    return f"{source_repo}:{relative}"


def _head_blob_entry(repo, relative):
    """Return (mode, oid) for an exact HEAD path without embedding the path in a revision spec."""
    head = _run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo)
    if head.returncode != 0:
        return None
    listing = _git(repo, "ls-tree", "-z", "HEAD", "--", relative)
    for record in (item for item in listing.split("\0") if item):
        metadata, separator, found = record.partition("\t")
        if not separator or found != relative:
            continue
        mode, kind, oid = metadata.split()
        if kind != "blob":
            raise PolicyError(f"HEAD policy source is not a blob: {relative}")
        return mode, oid
    return None


def _head_policy(repo, relative):
    """Load the last reviewed policy bytes; a new project policy has no prior checks."""
    entry = _head_blob_entry(repo, relative)
    if entry is None:
        return {}
    mode, oid = entry
    if mode == "120000":
        raise PolicyError(f"reviewed HEAD policy must not be a symlink: {relative}")
    text = _git(repo, "cat-file", "blob", oid)
    block = parse_block_text(text)
    if block is None:
        raise PolicyError(f"reviewed HEAD policy is malformed: {relative}")
    return block


def _path_matches(path, globs):
    for g in globs:
        gg = g[:-1] if g.endswith("/**") else g          # tests/** -> tests/
        if fnmatch(path, g) or fnmatch(path, g.rstrip("*")) or path.startswith(gg.rstrip("/") + "/") or path == gg.rstrip("/"):
            return g
    return None


def check_commit(repo, block, include_unstaged=False):
    validate_block(block)
    violations = []
    names = _git_names(repo, staged=True)
    diff = _git_diff(repo, staged=True, unified=0)
    if include_unstaged:  # `git commit -a` stages tracked changes AT commit time (after this hook) — include them
        names += _git_names(repo, staged=False)
        diff += _git_diff(repo, staged=False, unified=0)
    # 1. protected paths
    for p in sorted(set(names)):
        hit = _path_matches(p, block.get("protected_paths", []))
        if hit:
            violations.append(f"PROTECTED PATH staged: {p}  (matches '{hit}')")
    # 2. forbidden patterns in ADDED lines
    pats = block.get("forbidden_diff_patterns", [])
    if pats:
        added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        body = "\n".join(added)
        for pat in pats:
            if re.search(pat, body):
                violations.append(f"FORBIDDEN PATTERN in added lines: /{pat}/")
    # 3. required pre-commit checks
    for argv in block.get("pre_commit_checks", []):
        try:
            r = _run(argv, cwd=repo)
        except OSError as error:
            violations.append(f"PRE-COMMIT CHECK COULD NOT START: {json.dumps(argv)}\n    {error}")
            continue
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
            violations.append(
                f"PRE-COMMIT CHECK FAILED: {json.dumps(argv)}\n    " + "\n    ".join(tail)
            )
    return violations


def _review_context(repo, include_unstaged=False):
    names = _git_names(repo, staged=True)
    diff = _git_diff(repo, staged=True)
    if include_unstaged:
        names += _git_names(repo, staged=False)
        diff += _git_diff(repo, staged=False)
    return (f"FILES: {', '.join(sorted(set(names))) or '(none)'}\n\n"
            f"DIFF (truncated to 24k — READ the files for the rest):\n{diff[:24000]}")


def cmd_check_commit(args):
    repo = os.path.abspath(args.repo or repo_root())
    inc = getattr(args, "include_unstaged", False)
    try:
        _git(repo, "rev-parse", "--show-toplevel")
        constraint_path, source_kind = select_constraint_source(repo, args.constraint)
        source_repo, source_relative = _policy_source_binding(constraint_path)
        dirty_policy = _policy_source_dirty(source_repo, constraint_path)
        policy_label = _policy_label(source_repo, source_relative, repo)
        if source_kind == "fallback":
            if dirty_policy:
                raise PolicyError(
                    f"bootstrap fallback policy source changed: {policy_label}; "
                    "candidate pre_commit_checks were not executed"
                )
            block = parse_block(constraint_path)
            if block is None:
                raise PolicyError(f"bootstrap fallback policy is malformed: {policy_label}")
            raise PolicyError(
                "project constraint.md is absent; the bootstrap fallback is initialization-only "
                "and cannot authorize a commit"
            )
        if dirty_policy:
            v = [
                f"POLICY SOURCE changed: {policy_label} — do not execute candidate pre_commit_checks "
                "until these policy bytes receive review"
            ]
            prior_policy = _head_policy(source_repo, source_relative)
            if prior_policy:
                v += check_commit(repo, prior_policy, include_unstaged=inc)
        else:
            block = parse_block(constraint_path)
            if block is None:
                print("constraint_gate: constraint.md deterministic block is unavailable — failing CLOSED.", file=sys.stderr)
                return 2
            # Deterministic rules run first. PASS -> allow immediately (no model call, fast, headless-safe).
            v = check_commit(repo, block, include_unstaged=inc)
    except PolicyError as error:
        print(f"constraint_gate: policy evidence unavailable — failing CLOSED: {error}", file=sys.stderr)
        return 2
    if not v:
        return 0
    # A FLAG is NOT an outright block: the resolved direction-specific native commit reviewer (read-only)
    # reviews the diff and DECIDES block vs allow. Even a
    # protected/immutable path can have a legitimate edit — Codex is trusted to tell real violations from false
    # positives (the diff is untrusted input; a planted VERDICT line -> fail CLOSED). Only Codex path; fail-closed.
    # V2: no internal harness judge. check-commit's CALLER is already either
    # commit-review-openai-writer or commit-review-anthropic-writer, selected from the actual
    # writer semantic role through agent_roles.resolve_reviewer_assignment — a flagged commit hands the
    # BLOCK/ALLOW judgment BACK to that caller, with the evidence.
    try:
        ctx = _review_context(repo, inc)
        os.makedirs(os.path.join(repo, ".pm", "review-briefs"), exist_ok=True)
        brief = os.path.join(repo, ".pm", "review-briefs", "commit-flags.md")
        _atomic_write_within_repo(
            repo,
            brief,
            "DETERMINISTIC FLAGS:\n" + "\n".join("- " + x for x in v) + "\n\n" + ctx,
        )
    except (OSError, PolicyError) as error:
        print(f"constraint_gate: could not produce review evidence — failing CLOSED: {error}", file=sys.stderr)
        return 2
    print("\n\033[1;33m⚠ FLAGGED — REVIEW REQUIRED (exit 3):\033[0m", file=sys.stderr)
    for x in v:
        print("  - " + x, file=sys.stderr)
    print(f"  YOU — the resolved direction-specific native commit reviewer running this check — make the BLOCK/ALLOW call\n"
          f"  in your verdict, citing these flags (evidence brief: {brief}). A flag you cannot\n"
          f"  positively justify as a false positive is a BLOCK. Do not launch another reviewer.", file=sys.stderr)
    return 3


def cmd_protected_paths(args):
    """Emit constraint.md §3 protected_paths as a JSON array of prefixes for the protect-paths gate."""
    repo = args.repo or repo_root()
    block = parse_block(find_constraint_file(repo, args.constraint))
    if block is None:
        print("constraint_gate: constraint.md deterministic block is unparseable.", file=sys.stderr)
        return 2
    prefixes = sorted({glob_to_prefix(g) for g in (block or {}).get("protected_paths", []) if g and g.strip()})
    print(json.dumps(prefixes, indent=2))
    return 0


def _compiled_prefixes(repo, constraint):
    source, source_kind = select_install_constraint_source(repo, constraint)
    block = parse_block(source)
    if block is None:
        raise PolicyError("constraint.md deterministic block is unparseable")
    validate_block(block)
    prefixes = sorted({glob_to_prefix(g) for g in block["protected_paths"]})
    return prefixes, source_kind


def _read_installed_prefixes(target):
    if not os.path.lexists(target):
        return None
    if os.path.islink(target) or not os.path.isfile(target):
        raise PolicyError("existing protected-path config must be one ordinary file")
    try:
        with open(target, encoding="utf-8") as handle:
            existing = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PolicyError(f"existing protected-path config is unreadable: {error}") from error
    if not isinstance(existing, list) or any(not isinstance(item, str) or not item for item in existing):
        raise PolicyError("existing protected-path config must be an array of non-empty strings")
    return existing


def cmd_install_protected_paths(args):
    """Atomically replace the one canonical project config after full policy validation."""
    repo = os.path.abspath(args.repo or repo_root())
    target = os.path.abspath(args.output or os.path.join(repo, ".omp", "hooks", "protected-paths.json"))
    canonical = os.path.join(repo, ".omp", "hooks", "protected-paths.json")
    if target != canonical:
        print(f"constraint_gate: --output must be the canonical project config: {canonical}", file=sys.stderr)
        return 2
    try:
        prefixes, source_kind = _compiled_prefixes(repo, args.constraint)
        target_exists = os.path.lexists(target)
        try:
            existing = _read_installed_prefixes(target)
        except PolicyError:
            if source_kind == "fallback":
                raise
            existing = None
        if existing == prefixes:
            print(f"constraint_gate: already current {target}")
            return 0
        if source_kind == "fallback" and target_exists:
            raise PolicyError(
                "bootstrap policy cannot replace existing project protections; create constraint.md first"
            )
        if target_exists and not prefixes and not args.allow_empty:
            raise PolicyError(
                "refusing to replace an existing project protection config with [] without --allow-empty"
            )
        payload = json.dumps(prefixes, indent=2) + "\n"
        _atomic_write_within_repo(repo, target, payload)
    except (OSError, PolicyError) as error:
        print(f"constraint_gate: protected-path install failed; prior config preserved: {error}", file=sys.stderr)
        return 2
    print(f"constraint_gate: installed {target}")
    return 0


def _atomic_write_within_repo(repo, target, payload):
    repo_real = os.path.realpath(repo)
    parent = os.path.dirname(os.path.abspath(target))
    if os.path.commonpath([repo_real, os.path.realpath(parent)]) != repo_real:
        raise PolicyError("output parent resolves outside the repository")
    os.makedirs(parent, exist_ok=True)
    if os.path.commonpath([repo_real, os.path.realpath(parent)]) != repo_real:
        raise PolicyError("output parent changed to resolve outside the repository")
    fd, temporary = tempfile.mkstemp(prefix=".constraint-gate.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def cmd_show(args):
    repo = args.repo or repo_root()
    block = parse_block(find_constraint_file(repo, args.constraint))
    if block is None:
        return 2
    print(json.dumps(block, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="repo root (default: git toplevel of cwd)")
    ap.add_argument("--constraint", help="path to constraint.md")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cc = sub.add_parser("check-commit"); cc.add_argument("--include-unstaged", action="store_true"); cc.set_defaults(func=cmd_check_commit)
    sub.add_parser("protected-paths").set_defaults(func=cmd_protected_paths)
    install = sub.add_parser("install-protected-paths")
    install.add_argument("--output", help="must resolve to <repo>/.omp/hooks/protected-paths.json")
    install.add_argument(
        "--allow-empty",
        action="store_true",
        help="confirm an intentional project-policy transition from non-empty protections to []",
    )
    install.set_defaults(func=cmd_install_protected_paths)
    sub.add_parser("show").set_defaults(func=cmd_show)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
