# Workflow idea board

With write authority, keep this file durable across runs. For an analysis-only/read-only invocation,
render the schema ephemerally in the returned artifact and do not create or update a board path.
The board tracks conceptual coverage; experiment artifacts and full traces stay at their referenced
paths.

## Board contract

- Workflow/task:
- Pinned acceptance:
- Baseline artifact and date:
- Quality metrics:
- Cost metrics: model calls / input tokens / output tokens / wall time / external compute
- Search horizon and total budget:
- Active-set cap:
- Exploration reserve or probability:
- Plateau metric, window, and threshold:
- Selection rule and deterministic tie-break:
- Last community refresh date:

## Idea index

| ID | Cluster | Macro idea | Parents | Status | Trials / minimum | Best quality | Relative progress | Uncertainty | Novelty | Est. cost | Last selected | Next action |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | `BACKLOG` | 0 / | | | | | | | |

Allowed states:

- `BACKLOG`: recorded but not yet eligible;
- `READY`: eligible and not yet tried;
- `EXPLORING`: evidence is below the idea's declared minimum;
- `PROMISING`: evidence justifies further budget;
- `STANDBY`: inactive but retained for recovery or later evidence;
- `VALIDATED`: passed the full acceptance protocol;
- `EXHAUSTED`: falsified or received its declared minimum distinct evidence with a negative result.

`READY`, `EXPLORING`, `PROMISING`, and `STANDBY` are non-terminal with respect to search coverage.

## Idea records

Repeat this block for each mechanism-distinct idea.

### IDEA-000 — short name

- Cluster:
- Mechanism:
- Local failure addressed:
- Source links and dates:
- Parents / compatible crossover candidates:
- Novelty relative to board:
- Falsifier:
- Minimum distinct evidence before exhaustion:
- Reactivation condition:
- Constraints / dependencies:

| Trial | Atomic hypothesis/change | Evidence refs | Quality | Calls/tokens/time/compute | Verdict | What changed next |
|---|---|---|---|---|---|---|
| | | | | | | |

Negative result summary:

## Coverage dashboard

| Cluster | Ready | Exploring | Promising | Standby | Validated | Exhausted | Coverage gap |
|---|---:|---:|---:|---:|---:|---:|---|
| | | | | | | | |

## Plateau and escape log

| Date/run | Momentum evidence | Triggered? | Escape action | Why different | Result |
|---|---|---|---|---|---|
| | | | | | |

## Rejected and pruned memory

Keep conceptual duplicates, invalid proposals, and exhausted ideas here with reasons. Do not delete
them; later agents must be able to tell whether a proposed lever is genuinely new.

| Proposal | Related idea | Rejection/exhaustion evidence | Reactivation condition |
|---|---|---|---|
| | | | |

## Stop audit

- [ ] Pinned acceptance passes on the final candidate.
- [ ] If this invocation is `terminalOwner`, fresh acceptance evidence is attached to one selected
      different-provider terminal review; otherwise the child returned its coverage receipt.
- [ ] For a terminal owner, the terminal reviewer returned `COMPLETE`; `REVISE` was continued or redispatched, and any
      `HUMAN_BLOCKED` names a concrete human-only action after agent-side alternatives were exhausted.
- [ ] Final quality and all cost ledgers are recorded against the baseline.
- [ ] No `PROMISING` idea remains eligible inside the declared horizon.
- [ ] High-uncertainty `EXPLORING` ideas were not discarded solely by a noisy proxy or global cap.
- [ ] Every `EXHAUSTED` idea has a falsifier or the declared minimum distinct evidence.
- [ ] Every `STANDBY` idea has a next action or reactivation condition.
- [ ] The final outcome distinguishes success, checkpoint/budget exhaustion, and hard failure.
