---
id: example-mechanistic-claim
status: conjecture
skill: example-skill
scope: "The tasks, inputs, and runtime conditions where this claim is intended to hold."
evidence:
  kind: none
  artifacts: []
  runs: []
  probe_type: none
  probe: ""
  independence: ""
transfer:
  task_contexts: []
  artifacts: []
  independence: ""
  project_identifiers_removed: false
  applicability: ""
  counterexamples: ""
  falsifier: ""
derived_changes: []
superseded_by: ""
---

# Example mechanistic claim

A lemma is a falsifiable cross-task operating claim, not a mathematical proof. Keep raw source
material in `references/`, terse single-project observations in `.memory/skills/<skill>.md`, and only
de-identified causal claims that pass transfer admission here. An adopted skill lemma directory
contains `INDEX.md` plus one file per lemma.

`transfer.task_contexts` and `transfer.artifacts` each cite at least two distinct task contexts and
their raw observations. Reruns/seeds of the same task remain one context. Explain independence,
declare that reusable prose has had project identifiers removed, and state applicability, known
counterexamples/boundaries, and a falsifier. These fields are required even for `conjecture`; if the
cross-task evidence is not yet present, keep the candidate in project `.memory` instead.

Statuses:

- `conjecture`: no usable discriminating experiment yet; cannot justify durable behavior changes.
- `tested`: an experiment ran but evidence is inconclusive or insufficiently robust; cannot justify
  durable behavior changes.
- `supported`: proportional, discriminating evidence supports the claim within its declared scope;
  it may justify a separately reviewed durable change.
- `refuted`: falsified. Retain it so future runs do not retry the same dead lever blindly. It may
  remove or narrow behavior previously linked in `derived_changes`, but cannot introduce a new rule.
- `superseded`: replaced by an existing `supported` lemma; retain it and set `superseded_by` to that
  replacement id. Corrective behavior changes may only remove, narrow, or redirect prior links.

`derived_changes` records links to separately reviewed durable carriers. It is not an epistemic
status and must remain empty for `conjecture` and `tested` lemmas. On a refutation or supersession,
these historical links identify the behavior that a corrective diff is allowed to remove or narrow.

## Observation

What the raw trace, reference, or skill memory actually showed. Point to exact artifacts in
frontmatter; do not substitute a builder narrative for first-hand evidence.

## Problem

The discrepancy, bottleneck, failure mode, or unexplained result that needs a mechanism.

## Hypothesis

One falsifiable causal explanation, stated within the declared scope.

## Prediction

What should be observed before running the intervention if the hypothesis is right, and what would
differentiate it from plausible alternatives.

## Intervention

The smallest discriminating experiment or counterfactual probe. If it cannot fit the current round,
say why and leave the lemma as `conjecture`.

## Result

The observed outcome. For stochastic or graded behavior, record at least two distinct run references
and statistical evidence proportional to the noise; one aggregate artifact may summarize them. A
deterministic isolated failing/passing probe may be sufficient once.

## Mechanism

How the intervention produced the result. Separate a supported mechanism from a merely plausible
story and name competing explanations that remain.

## Boundaries and Falsifier

Where the claim should not be generalized, plus the observation that would refute it.

## Synthesis

The current conclusion and why its status is warranted. One unisolated success is never a permanent
rule.

## Next Probe

The next discriminating experiment if uncertainty remains, or `NONE` when the current scope is
adequately resolved.
