# Review routing and Simplify -> Verify

Resolve every route from the live native catalog and the project-owned role policy before dispatch.
These preferences do not authorize a direct model launch that bypasses the canonical resolver,
launch receipt, role binding, or actual-writer-opposite terminal-review policy.

For native agent CLI calls, also read `native-cli-execution.md`. Every real turn gets a
3600-second timeout; short deterministic probes used to resolve tools or drain a process are not
agent turns.

## Review routing

- Strong review and advice candidates are Fable 5.1 (`claude-fable-5-1`) and GPT-6 Astra
  (`gpt-6-astra`), but naming them does not activate a route or replace the project's exact
  terminal-review pairings. Resolve the actual writer execution first. The policy-selected
  reviewer or adviser must use a different upstream provider: Fable is eligible only opposite a
  non-Anthropic writer, while Astra is eligible only opposite a non-OpenAI writer. A planned role,
  coordinator model, CLI name, or relay label is not writer-identity evidence.
- Every selected concrete reviewer or advice identity must resolve through the authenticated live
  catalog for its exact native route immediately before dispatch. If the exact candidate, required
  effort, or opposite-provider route is unavailable or unverifiable, report the selection as
  unresolved. Never silently substitute another model, version, alias, provider, backend, or
  effort.
- The bundled `frontier_advice_fable` role is exact Fable 5.1 through Cursor, while
  `frontier_advice_astra` is the exact active Astra route through Codex. The latter remains
  unavailable until both a reviewed policy activation and fresh live-catalog resolution succeed.
  They are non-terminal planning and result-analysis advice roles, not completion verdicts. Apply
  the actual-writer opposite-provider rule to every selection. Never relabel an advice route as a
  native verifier or use either to replace an existing terminal pairing silently.
- Fable 5.1 is incompatible with zero-data-retention arrangements and uses the vendor's stated
  30-day retention path. Do not route restricted data to it unless the project's data policy
  permits that retention. See Anthropic's current
  [model overview](https://platform.claude.com/docs/en/about-claude/models/overview).
- Grok may remain a fresh-catalog diversity probe for a named correlated blind spot, but
  Simplify -> Verify never requires Grok and must not fail merely because Grok is unavailable.
  If a task separately selects Grok as a required diversity route, that exact selection still
  fails unresolved rather than falling back.
- These are routing preferences, not terminal verdicts. The configured fresh independent terminal
  reviewer remains the only session completion authority for an `independent` workflow.

## Bounded quality loop

At each task boundary, first record whether this loop is needed and why in the active agent
instruction/brief and the PM task record. In a managed project, that brief is the marked block in
`AGENTS.md` or `CLAUDE.md`; for a worker it is the bounded dispatch brief. Use these exact fields:

`SIMPLIFY_VERIFY_DECISION: required|not_required`

`SIMPLIFY_VERIFY_REASON: <one concrete sentence>`

Task type alone is not a trigger. Ordinary code, algorithm, workflow,
configuration, multi-file, or externally visible work may record `not_required`. Record `required`
only when at least one current evidence/risk trigger is present:

- the candidate shows redundancy, unnecessary abstraction, speculative fallback/recovery, mock or
  synthetic scaffolding, or another concrete simplification target;
- the bounded change has accumulated repeated failed revisions;
- the change is high-impact, irreversible, or security-sensitive;
- consequential logic is weakly measurable, making independent verification materially valuable;
- an explicit user or acceptance requirement calls for this loop.

Record the initial decision before dispatch, then reassess it after candidate evidence and
before presentation. Upgrade `not_required` when a trigger emerges; do not run the loop merely to
produce review artifacts. A missing or ambiguous decision is unresolved and must not be dispatched.
Focused tests, ordinary code review, alignment review, evidence capture, and the
outer independent terminal review remain governed by their own contracts for both decisions.

When the decision is **required**, run:

`simplify plan -> simplify write -> capture real evidence -> three fresh verifies -> deterministic reduce`

Repeat only on concrete `REVISE` findings, for at most three statically declared rounds. The
planner removes redundancy, unnecessary abstraction, speculative recovery branches, and fabricated
test scaffolding while naming behavior that must remain. The writer applies that bounded plan.
Evidence is the actual candidate, actual diff or change report, actual acceptance output, and raw
reviewer response artifacts. A generated placeholder or synthetic success record is not evidence.

Each round requires all three configured verification routes. The deterministic reducer emits:

- `PASS` only when all three evidence-bound verdicts are `PASS`;
- `REVISE` when at least one concrete revision finding remains and every route returned validly;
- `UNRESOLVED` when any required route or artifact is unavailable, missing, or malformed.

There is no vote, substitution, or partial-pass path. Third-round `REVISE` ends unresolved; it never
becomes success through budget exhaustion. Graph success means only candidate-ready and must still
go through the outer independent terminal review. When the recorded decision is **not required**,
do not create synthetic Simplify/Verify artifacts; deterministic checks and the terminal review
remain the evidence.

The neutral sample is
`.claude/skills/build-workflow/references/designs/simplify-verify-loop.workflow.json`. It defines
three statically bounded rounds with semantic roles only. It is deliberately not a runnable mapping:
before execution, copy and specialize it as a project design, bind a real task, acceptance argv and
project-relative candidate path, then use `run-workflow` to select and validate a compatible adapter.
A three-node `waitFor` barrier in each round requires all verification branches before reduction.

The planner and writer may resolve only to validated project roles. The three semantic
verification roles must map to three freshly resolved routes that satisfy the selected mapping and
use an upstream provider opposite the actual writer execution. Fable 5.1
(`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`) are strong candidates in their legal
directions only after explicit project-policy admission; they do not replace the configured exact
terminal-review pairing. No verifier role is intrinsically Grok. Grok remains optional general
diversity, not a Simplify -> Verify prerequisite. If any explicitly selected verifier is absent
from its live catalog or cannot prove the required provider separation and effort, the round is unresolved;
never substitute or mutate a role, provider, model, backend, or effort in workflow state or midway
through execution.

Fast mode may shorten the planning pass and skip broad idea debate. It may not remove a verification
branch, weaken real-evidence capture, or skip terminal review. Default mode may use the same sample
for autonomous cleanup before presentation.
