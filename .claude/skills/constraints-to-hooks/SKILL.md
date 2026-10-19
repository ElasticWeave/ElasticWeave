---
name: constraints-to-hooks
description: "Compile constraint.md §3 into deterministic path protection and commit-review evidence; use at cold start or when constraints change."
---
> **Model tier:** HIGH — deriving the right constraints is judgment


# constraints-to-hooks

Turns the **deterministic** half of `constraint.md` (§3, the `constraints:deterministic` JSON
fence) into mechanical enforcement shared by adapter hooks and native-harness protection
hooks/audits. A constraint compiles to one canonical project config plus the commit-review evidence check.
Judgment-based constraints (§1 machine, §2 functional/form) stay prose the agents + the
resolved direction-specific commit reviewer read on-demand.

> **Top rung of the knowledge-routing ladder.** When you learn a *prohibition*
> ("never touch X"), first ask "can this be mechanical?" — if deterministic, route it HERE (a
> protected path / exact low-false-positive pattern / required check), before merely recording a
> note. Do not turn speculative reward-hacking behaviors into regexes: those evolve and belong in
> the standard round's byte-evidence reviewer. Deterministic protection enforces what is decidable;
> review judges behavior. Flow/control-plane edits are themselves path-protected — land them
> through reviewed work.

## Two enforcement targets
1. **Protected paths → `.omp/hooks/protected-paths.json`** (a JSON array of path prefixes).
   The `protect-paths` extension reads it as its PROJECT layer (on top of the framework
   list) and blocks any edit/write/bash touching those paths — in the main session AND inside
   every subagent (verified). Live in the NEXT session (extensions load at session start).
2. **Forbidden diff patterns + focused pre-commit checks → `constraint_gate.py check-commit`**,
   which the resolved **`commit-review-openai-writer`** or **`commit-review-anthropic-writer`**
   agent runs as deterministic step 1. The caller passes the actual writer semantic role through
   `agent_roles.resolve_reviewer_assignment`, selects the asset whose `writer-family` matches, and
   fails closed on omitted/mismatched identity or an unverified catalog. The check scans the staged
   diff for protected-path hits + forbidden patterns + runs the required checks, and on a FLAG
   returns an evidence brief (exit 3) to that already-running native reviewer, which decides
   BLOCK vs ALLOW. Missing independent review fails closed; no Pi model is launched. These commands
   are project-selected evidence checks, not one global repository-wide acceptance gate.

There is NO Claude Code `settings.json` compilation and NO `.codex/hooks.json` mirror anymore.

## When to run
- **Cold start** (default-mode step 7): after `constraint.md` §3 is derived, merge its `protected_paths`
  into `.omp/hooks/protected-paths.json`.
- **Workflow-update:** whenever §3 changes (new protected path / pattern / check), re-merge.

## Procedure
1. **Confirm §3 is current.** Ensure the `constraints:deterministic` block lists this repo's real
   `protected_paths`, `forbidden_diff_patterns`, `pre_commit_checks` (derive via
   `scripts/extract_constraints.py`; keep it MINIMAL + high-signal). A forbidden pattern must name
   an exact mechanically invalid construct, not guess at future hardcoding/test-detection tactics.
   Express every `pre_commit_checks` item as a JSON argv array, such as
   `["python3", "-m", "pytest", "tests/unit"]`; raw shell strings are invalid and never execute.
2. **Emit project protected paths:**
   ```bash
   GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 python3 .claude/skills/constraints-to-hooks/scripts/constraint_gate.py install-protected-paths
   ```
   The tier-1 ack prefix is REQUIRED because the command writes the path-protected `.omp/hooks/`
   layer. The compiler validates the complete policy, writes a sibling temporary file, and atomically
   replaces the canonical config; malformed input preserves the last-known-good bytes. Before a
   project `constraint.md` exists, the bundle template may initialize a missing config but cannot
   replace existing project protections. A reviewed project policy that intentionally moves a
   non-empty or malformed existing config to `[]` must also pass `--allow-empty`; otherwise the
   prior bytes stay intact. A validated non-empty project policy may repair a malformed config.
   Hooks read the project file on each action, so the change is immediately visible.
3. **Smoke-test the commit-review evidence check** (what the resolved commit reviewer runs) without committing:
   ```bash
   python3 .claude/skills/constraints-to-hooks/scripts/constraint_gate.py check-commit
   # exit 0 = clean; exit 3 = flagged for direction-specific reviewer judgment; exit 2 = policy failure
   ```
   `check-commit` requires project `constraint.md`; the empty bootstrap template is initialization
   input, not an operational policy that can authorize a commit. The gate binds the selected policy
   to the Git repository that reviews those bytes and never executes checks from a dirty candidate.

## Adding a new deterministic constraint later

A bounded constraint owner edits §3 (path/pattern/check), reruns step 2 plus the smoke test, and
records its own source-layer `.memory` residue; if the change is a paradigm shift, it flags the
workflow-updater. When the caller is the top-level project session, the top only authorizes and
inspects this change and delegates every mutation (§3 edits, config compilation, smoke-test
evidence, and memory write) to that owner. **Never** weaken §3 to make a check pass—a real
loosening is a deliberate re-pin, recorded.

## Notes / limits
- The project protection config is materialized by `default-mode` and travels with the target repository. Platform
  surfaces are installed by `update-platform-config`.
- Defense-in-depth, not a replacement for judgment: subtle functional/form constraints still
  need the resolved direction-specific commit reviewer reading §1/§2.
- Keep the check fast; it runs on every commit attempt.
