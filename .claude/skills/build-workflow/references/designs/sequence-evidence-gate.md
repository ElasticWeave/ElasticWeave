# Reference design: sequence with evidence gates

Use for ordered work in which each stage consumes a durable artifact from the previous stage and
completion depends on mechanical acceptance. If one owner can safely perform all stages without a
lifecycle boundary, use a native subagent instead.

## Shape

```text
seed → baseline → implement → deterministic_check ─PASS→ bind_evidence → success
                                      └─FAIL→ bounded_revision → deterministic_check
                                                   └─cap→ unresolved
```

## State

| Field | Purpose |
|---|---|
| `run_id` | Stable attempt and side-effect key |
| `baseline_ref` | Pre-write bytes, metric, or acceptance output |
| `candidate_ref` | Current artifact or commit/diff pointer |
| `check_evidence_ref` | Machine-readable acceptance evidence |
| `revision_count` | Loop cap |
| `forced_reason` | Why a non-success terminal was taken |

## Contracts

- `baseline` and `deterministic_check` are scripts unless judgment is irreducibly semantic.
- `implement` receives the pinned goal, allowed files, baseline, and exact output contract.
- A failed check gives `bounded_revision` the smallest actionable delta, not the full transcript.
- `bind_evidence` verifies that evidence was produced after the final write and refers to the final
  candidate bytes.
- The check implements explicit issue/design criteria. Prefer a direct argv invocation with bound
  status/output evidence; do not assume default-mode created `.pm/accept.sh`.
- Missing or malformed check output routes to failure/unresolved; it never advances.

## Cost controls

- Run formatting, schema, import, and focused tests before broad suites.
- Cache immutable setup and pass artifact pointers between nodes.
- If the same repair is attempted twice without changed evidence, exit to unresolved and require a
  new hypothesis rather than spending another revision call.

## Completion audit

- Acceptance ran after the final writer.
- Evidence identifies the exact candidate.
- The cap route cannot be mistaken for success.
- The workflow reason still exists; otherwise demote the next run.
- Under `reviewPolicy: independent`, graph success is only candidate-ready; the top session
  dispatches a fresh terminal reviewer before stopping.
