# Reference design: long-running recovery

Use when work crosses lifecycle boundaries, invokes slow external systems, or can time out after a
side effect succeeds. Declare whether the design requires persisted resume or permits a new bounded
attempt with reconciliation; adapters must not treat those as equivalent.

## Shape

```text
seed_or_reconcile → inspect_frontier → perform_one_stage → checkpoint_evidence
        ↑                                      │
        └──────────── new bounded attempt ─────┘
```

## Durable state

| Field | Purpose |
|---|---|
| `run_id`, `schema_version` | Attempt identity and compatibility |
| `stage`, `frontier_ref` | Last mechanically verified boundary |
| `side_effect_keys` | Idempotency/reconciliation lookup |
| `artifact_hashes` | Detect stale or externally changed inputs |
| `budget_spent` | Calls, tokens, wall time, and external compute |
| `next_action` | Deterministic handoff for the next attempt |

## Recovery rules

- Check whether an external effect already happened before repeating it.
- Advance `stage` only after evidence for the stage is durably written.
- On incompatible schema or changed inputs, stop for migration/replanning; do not reinterpret old
  state silently.
- Make compensation explicit when it exists. If an effect cannot be reversed, prefer a stable
  idempotency key or read-after-write reconciliation.
- A checkpoint is a first-class non-success terminal with evidence and one named next action.
- A checkpoint is not automatically `HUMAN_BLOCKED`. The top session's terminal reviewer uses
  `REVISE` unless the next action is a concrete human-exclusive authorization, decision,
  credential, or external action.

## Cost controls

- Each attempt performs a bounded amount of work and reloads compact state, not every transcript.
- Polling and retry timing live in deterministic tools.
- Do not spend model calls waiting for an external job whose state can be queried mechanically.

## Completion audit

Verify the final artifact hashes, acceptance evidence, and all externally visible effects. State
which historical checkpoints were consumed and whether any ambiguous effects remain.
