---
name: writing-plans
description: "Create a bounded implementation plan when dependencies or design risk justify one; define interfaces, acceptance, order, lane, budget, and review."
---
> **Model tier:** HIGH — planning is judgment-heavy, but the artifact and review loop are bounded.

# Writing Plans

> Adapted from [obra/superpowers](https://github.com/obra/superpowers) @ `f268f7c` (MIT, © 2025
> Jesse Vincent; see `LICENSE`). Local policy adds dispatch lanes and strict plan/review budgets.

**Announce at start:** "I'm using the writing-plans skill to create a bounded implementation plan."

## Principle

A plan is an execution map, not a substitute for implementation. It should remove architectural,
interface, ordering, and acceptance ambiguity, then let tests resolve implementation-level
questions. Do not spend repeated model calls proving details that a cheap red/green experiment can
settle.

Before planning nested work, read `references/invocation-envelope.md` and derive one invocation
envelope. User authority is paramount: the envelope may narrow it, never turn a read-only request
into implementation or an ordinary write request into experimental or external side effects.
Resolve the outer iteration policy at the same boundary. Default an absent policy to `autonomous`;
use `human_guided` only with a live lease. Every nested task inherits the snapshot and cannot renew
the lease or switch mode on its own.

Skip a plan when the bounded worker assignment is one obvious reversible edit with one focused
check. This does not authorize a top coordinator to implement it; dispatch that worker directly.
Otherwise save the plan to `.pm/plans/YYYY-MM-DD-<feature-name>.md` unless the user specifies
another location.

## Default Budget

- At most 8 implementation tasks, 200 lines, or 12 KiB, whichever is reached first.
- Each task owns one independently testable deliverable; avoid 2-minute clerical micro-steps.
- Show exact interfaces, invariants, file paths, and commands. Include full code only for a
  load-bearing contract, migration, or algorithm whose shape cannot safely be left to execution.
- At most two independent plan-review cycles. Reviewers must batch all known blockers in one pass.
- When the budget is insufficient, split the work into independently valuable plans; do not make a
  single plan unbounded.

## Scope And Routing

Before writing tasks, map the files and dependencies, then choose the smallest sufficient lane:

| Lane | Plan it when |
|---|---|
| `direct` | A top coordinator performs read-only inspection or disposable verification that creates no durable project, `.pm`, UI, repository, or external state. |
| `native-subagent` | One owner can implement the deliverable without graph state, fan-out/fan-in, explicit lifecycle boundaries, or multi-stage routing. This is the default delegated lane. |
| `workflow` | The work needs a real graph: dependent multi-stage model leaves, fan-out/fan-in, batching, a stateful bounded loop, explicit lifecycle/stop boundaries, long-running repetition, protected control-plane review, or machine-enforced verdict routing. Use the admitted subagent scheduler or humanize2 scheduler. |

Difficulty alone is not a workflow reason. If the planned task is only builder + reviewer, prefer a
native subagent unless machine-enforced routing or another graph-only property is explicitly needed.

## Required Header

```markdown
# [Feature Name] Implementation Plan

**Goal:** [one sentence]

**Architecture:** [2-3 sentences]

**Acceptance:** `[exact command or concrete check]`

**Default dispatch lane:** `[direct | native-subagent | workflow]`

**Dispatch reason:** [why this is the smallest sufficient lane]

**Estimated / maximum model calls:** `[N / N]`

**Promotion triggers:** [facts that would require moving a native task into a workflow]

**Iteration mode:** `[autonomous | human_guided]`

**Human check-in contract:** `[n/a | lease=N minutes; fallback=pause|autonomous_review]`

**Review disposition:** `[retained; skippable idea-level; risk-triggered]`

**Post-reduction boundary:** `[next bounded autonomous plan | unresolved human observation checkpoint]`

**Root invocation:** `[invocationId; authority; terminalOwner; iterationMode; lease/fallback; activeSkillStack; budgets; required receipts]`

## Global Constraints

- [exact project-wide requirement]
- For iterative work, name exactly one canonical mode: `autonomous` or `human_guided`. State the
  human check-in lease and unavailable-human fallback when human-guided; never write `Fast` into
  persisted plan state.
- Identify review work retained in both modes, idea-level review skipped only in human-guided when
  no risk trigger applies, and the risk conditions that restore targeted review or autonomous
  fallback.
```

For a workflow lane, also list the admission reason names used by `build-workflow` and the maximum
review cycles. For a mixed plan, each task declares its own lane.

## Task Structure

```markdown
### Task N: [independently testable deliverable]

**Dispatch:** `[direct | native-subagent | workflow]` — [one-line reason]

**Invocation:** `[invocationMode; authority; terminalOwner; workerClass; activeSkillStack; child budget; receipts]`

**Files:**
- Inspect: `exact/path/file.ext`
- Modify (delegated lanes only): `exact/path/file.ext`
- Coordinator state (delegated PM worker only): `.pm/...`
- Test: `exact/path/test.ext`

**Interfaces / invariants:**
- Consumes: [existing contract]
- Produces: [new or changed contract]
- Must preserve: [constraint]
- Iteration policy: [mode, lease/fallback if applicable, retained/skipped/risk-triggered review]
- Resume evidence: [checkpoint fields and the human feedback required before the next plan]

**Acceptance:** `[focused command]`

- [ ] Add or update the failing test/fixture.
- [ ] Implement the smallest change that satisfies the contract.
- [ ] Run the focused acceptance command and record the result.
- [ ] Run the relevant broader regression check.
```

For a `direct` task, replace the implementation/test-fixture boxes with read-only inspection or
disposable verification. If evidence or coordinator state must be recorded, dispatch that exact
write to a bounded PM worker; the moment any durable edit is needed, re-route the task. For
delegated lanes, keep the implementation boxes as written.

Add exact signatures or short pseudocode when needed. Do not paste routine bodies that the
implementer can derive safely from repository patterns.

## Plan Review

First self-review once for spec coverage, placeholders, interface consistency, and runnable
commands. For load-bearing plans, use a different-provider native reviewer. The reusable review
brief is in `plan-document-reviewer-prompt.md`; load it only for that review.

The reviewer returns one consolidated response with every currently known finding classified as:

- `PLAN-BLOCKING`: an ambiguity or contradiction that can change architecture, scope, interfaces,
  safety, or acceptance. Fix before execution.
- `IMPLEMENTATION-TESTABLE`: a question best answered by a test or small experiment. Put the test
  in the plan and start implementation.
- `OPTIONAL`: polish or an alternative that does not block the goal.

After two review/revision cycles, do not request another broad planning review. Resolve remaining
items by splitting the plan or running the discriminating experiment. If a choice truly changes
scope and agent-side alternatives are exhausted, package it as a candidate human dependency for a
the outer `terminalOwner`; only that owner requests terminal classification. Structured
`HUMAN_BLOCKED` may authorize its exact request through the controlling interactive session or
configured UI channel, or permit stopping; `REVISE` continues or redispatches work.
Review must converge by batching, not by drip-feeding one objection per round.

## Execution Handoff

- `direct`: the top coordinator may inspect or run disposable verification only. Any state
  recording—including `.pm`/UI bookkeeping—and any change to source, tests, docs, workflow
  implementations, or other project deliverables must be re-routed to `native-subagent` or
  `workflow`, even when tiny. Commit/push/merge/release and Linear/Notion or other external writes
  are also delegated, not direct coordinator work.
- `native-subagent`: dispatch one bounded owner with the task section and acceptance command. For
  load-bearing results, launch the configured different-provider reviewer in its own native CLI.
- `workflow`: use `build-workflow`, include the admission metadata and budgets, then map and start
  through the admitted built-in subagent scheduler using `$run-workflow`. Select the Humanize2
  adapter only when the plan records an explicit Humanize choice or a validated trusted parent /
  child sub-flow relationship; authoring a flow alone never selects that runtime.

Workers execute task-by-task, keep the plan checked off, and load `grind` when a step repeatedly
fails. They do not expand the plan merely because implementation reveals routine details.

Apply the lifecycle table in `references/invocation-envelope.md`. A child returns scoped evidence
and receipts without an assignment terminal or independent terminal review. Only the outer
`terminalOwner` initiates that review over the assembled final state. For a `machine-gated`
workflow, its valid fresh state-bound proof is the review receipt; do not duplicate it with an external review. If that receipt is absent,
stale, malformed, or does not bind the final state, the `terminalOwner` dispatches the external
reviewer. Only `TERMINAL: COMPLETE` closes the assignment. `TERMINAL: REVISE` continues or
redispatches; `TERMINAL: HUMAN_BLOCKED` requires the
canonical concrete human-request fields. Budget exhaustion or failed attempts is not human blocking.
