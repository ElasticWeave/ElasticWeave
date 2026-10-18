# Reference design: machine-gated protected write

Use only when the issue records `review_policy: machine-gated` and needs graph-enforced,
different-provider review of a load-bearing or protected write. A normal implementation plus
terminal independent review should stay in the native-subagent lane.

## Shape

```text
baseline → implement → byte_evidence → acceptance → cross_provider_review
                                                      ├─PASS→ bind_state_proof → success
                                                      ├─REVISE→ bounded_revision → byte_evidence
                                                      └─BLOCK→ unresolved
```

## Required design controls

- governance records `reviewPolicy: machine-gated`
- governance records `completionProof: state-bound`
- `maxReviewCycles` stays in the supported range and within `maxModelCalls`
- reviewer provider family different from every workspace writer
- unavailable/malformed review fails closed, with explicit verdict routes and no fallback verdict
- every writing path passes the review node before success

## Evidence packet

Give the reviewer the explicit goal and acceptance criteria, constraints, pre-write baseline, final
byte diff, deterministic argv/result evidence when applicable, and known limitations. Do not ask
the writer to summarize its own compliance as the only evidence. The acceptance command is an
issue/design choice, not a default-mode-created `.pm/accept.sh` gate.

## Protected paths

The graph does not create authority. Use only the sanctioned protected-write tier, and never set a
user-approval variable without the transcript-visible approval it asserts. A protected write that
cannot be authorized exits unresolved.

## Completion

Bind the fresh reviewer artifact and final candidate identity into the state proof. A review of
earlier bytes is stale. A passing final proof emits `TERMINAL: COMPLETE` and
`EVIDENCE-COMMITMENT: sha256:<candidate digest>`; it does not emit a legacy `WORKFLOW: PASS` marker.
Do not add a duplicate out-of-flow reviewer after machine-gated proof.

An in-graph `BLOCK` or unresolved route is not automatically `HUMAN_BLOCKED`. The latter is legal
only when the reviewer records one human-exclusive kind—authorization, decision, credential, or
external action—and a concrete request. All remediable agent/workflow findings are `REVISE`.

The selected harness must implement and test these controls. If it cannot produce a state-bound
proof or enforce the verdict route, it is ineligible for this design.
