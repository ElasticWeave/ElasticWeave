# Reference design: idea-board optimization

Use for iterative optimization across multiple conceptual directions. This design prevents a high
incumbent score, a failed early trial, or a per-trial cap from silently collapsing the search.

## Iteration policy

An idea board is iterative, so its parent design must declare `iterationPolicy` and bind the board
to the policy's `riskTriggerState`, `ideaReviewDispositionState`, `checkpointState`, and
`resumeEvidenceState`. Use `autonomous` when the controller owns the next proposal and records
`performed` idea review evidence before each trial. Use `human_guided` when a human is expected to
inspect each reduced result: retain the minimum validity gate, record `skipped_human_guided` only
when no risk trigger applies, and stop at an unresolved observation checkpoint before selecting a
new idea. Contradiction, ambiguity, plateau/repeated failure, weak proxy, irreversible/high-cost or
safety-sensitive work, and lease expiry/unavailability change the disposition to
`triggered_by_risk` or follow the explicit autonomous fallback. Human feedback must identify the
checkpoint it answers; it is resume evidence, not an informal chat continuation.

## Shape

```text
seed_baseline → load_board → coverage_selector → instantiate_trial → cheap_validity
                      ↑              │                    │                │
                      │              │                    └─invalid→ repair_once
                      │              ↓
               plateau_escape ← update_board ← staged_evaluation
                      │
        backtrack | activate_cluster | crossover | community_refresh

coverage_and_quality_gate ─PASS→ success
                          └─BUDGET→ checkpoint
                          └─GAP→ selector
```

The diagram is conceptual. Express it in the neutral design first, then let the selected adapter
map it to supported harness fields and node types.

## Two-level state

An **idea** is a mechanism-level direction. A **trial** is one falsifiable implementation or prompt
change under that idea. Store them separately.

| Idea field | Meaning |
|---|---|
| `idea_id`, `cluster`, `parent_ids` | Stable provenance and diversity cell |
| `status` | `BACKLOG`, `READY`, `EXPLORING`, `PROMISING`, `STANDBY`, `VALIDATED`, `EXHAUSTED` |
| `mechanism`, `falsifier` | Why it may work and what would refute it |
| `min_evidence` | Trials/resources required before exhaustion |
| `trial_refs` | Positive and negative evidence pointers |
| `best_score`, `relative_progress` | Quality evidence |
| `uncertainty`, `novelty`, `estimated_cost` | Selection inputs |
| `reactivation_condition` | New fact that can revive standby/exhausted work |

## Selection policy

Use a deterministic multi-objective selector over normalized fields. One acceptable structure is:

```text
priority = exploit_value + coverage_value + novelty_value - cost_penalty - recent_failure_penalty
```

Do not treat this as a universal formula. Pin the terms, tie-break rule, and exploration reserve for
the task. Preserve either a minimum share of trials for uncovered clusters or a non-zero random
exploration probability. A recently failed idea may receive a cooldown without being deleted.

## Status rules

- A first failure leaves a new idea `EXPLORING` or `STANDBY`.
- `EXHAUSTED` requires its falsifier or the declared minimum evidence across distinct trials.
- `STANDBY` retains evidence and can be reactivated; it is not a success or hard failure terminal.
- A trial cap updates the board, then routes elsewhere. It does not exhaust the parent idea.
- `VALIDATED` requires the full acceptance protocol, not only a cheap proxy.

## Plateau escape

Track scale-normalized progress and a smoothed momentum signal. When momentum stays below the pinned
threshold, do not merely increase retries. Choose one of:

1. backtrack to an earlier parent and clear recent micro-context;
2. activate the least-covered viable cluster;
3. combine compatible ideas from different branches;
4. refresh current community evidence and add only mechanism-distinct ideas.

Record why the new path differs from the failed one.

## Completion

Success requires acceptance **and** a coverage audit. The audit checks that no eligible
`PROMISING` idea and no high-uncertainty `EXPLORING` idea was discarded solely due to age, a noisy
proxy, or the global stop cap. If budget ends first, emit a checkpoint containing the board and
next selector decision.

## Cost controls

- Ladder evaluation: deterministic validity → cheap proxy → covering sample → full evaluation.
- Use summaries/deltas and artifact refs; keep full traces outside prompt state.
- Maintain a Pareto set for quality versus tokens, calls, wall time, and scarce compute.
- Compile repeatedly successful deterministic tool sequences into scripts after regression tests.
