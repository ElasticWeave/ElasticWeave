# Reference design: adaptive cost cascade

Use when most cases can be handled cheaply but a minority need a larger multi-stage workflow. The
gate must be testable; otherwise the cascade can save tokens by silently dropping hard cases.

## Shape

```text
ingest → deterministic_prefilter → lightweight_gate
                                  ├─EASY→ compact_executor → acceptance
                                  ├─HARD→ heavy_subflow → acceptance
                                  └─UNCERTAIN→ heavy_subflow
```

## Gate contract

The gate emits structured fields: `route`, `confidence`, `risk_factors`, and `evidence_ref`.
Default uncertain or malformed decisions to the safer lane. Never let the cheap lane modify the
acceptance target.

Calibrate on representative cases before deployment:

- false-easy rate: hard cases incorrectly sent to the compact lane;
- cost per accepted case, not cost per attempt;
- quality delta relative to always-heavy baseline;
- route distribution drift after prompt/model/workload changes.

## Cost controls

- Put deterministic rules before the model gate.
- The compact executor gets only the state needed for its case.
- Reuse the same acceptance node for both paths.
- Promote a case after one evidence-bearing failure; do not run many cheap retries that cost more
  than the heavy path.
- Recalibrate when the gate, model, or case distribution changes.

## Failure boundary

If the false-easy rate exceeds the pinned tolerance, disable the cheap route and return to the
always-heavy baseline until the gate is repaired. Savings are invalid when acceptance regresses
beyond that tolerance.
