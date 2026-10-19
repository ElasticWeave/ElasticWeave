---
name: generate-insight
description: "Turn one surprising observation plus candidate mechanism into a bounded discriminating experiment and scoped insight; use after results, not broad research or debugging."
---
> **Model tier:** HIGH — causal inference, experimental design, and adversarial interpretation

# Generate Insight

Resolve one mechanistic claim from evidence without turning a plausible story into a general rule.
The output is a project-local insight record, not a mathematical lemma or reusable instruction.

## Boundary

- Keep exactly one active lemma. Split unrelated mechanisms into separate runs.
- Use existing raw evidence first. If no observation exists, run the smallest valid exploratory
  experiment before proposing a lemma; a proposal or model narrative is not evidence.
- Do not start a literature survey, construct broad infrastructure, or create mock evidence. A
  simulation counts only when simulation is the declared experimental target.
- Keep the user outcome, acceptance criteria, authority, and safety constraints fixed. A lemma may
  change; the task contract may not change silently.
- Default to at most three lemma rounds and three verifying experiments unless the caller supplies
  tighter explicit bounds. Stop when either bound is exhausted.
- Inherit the caller's effective iteration mode. `autonomous` may continue within those caps.
  `human_guided` runs at most one new verifying experiment per invocation; if the result needs
  another lemma or experiment, return the record in `RETHINK` at the external human checkpoint.
  Human feedback may resume the same cycle and budgets, but absence of feedback never relabels the
  checkpoint as `FAILED` or permits a hidden second experiment.

Use `references/insight-cycle.schema.json` for the typed record and bind the effective
`iterationMode`. Create the record before the first experiment, append raw artifact references as
work lands, and never rewrite an adverse observation.
Increment `roundsUsed` when a lemma is frozen and `experimentsUsed` before an experiment begins, so
an interrupted attempt still consumes budget. Never start an action whose increment would exceed a
cap.

Permit only these transitions:

```text
EXPERIMENT_PROPOSED -> LEMMA_PROPOSED | FAILED
LEMMA_PROPOSED -> VERIFYING | FAILED
VERIFYING -> SOLIDATED | RETHINK | FAILED
RETHINK -> LEMMA_PROPOSED | VERIFYING | FAILED
```

The `RETHINK -> VERIFYING` edge is only for one repair of an invalid experiment; every changed
lemma returns through `LEMMA_PROPOSED`. Skip `RETHINK` only for a hard authority/safety stop,
corrupted evidence, no feasible valid test, or an exhausted cap; all other adverse results pass
through it. A cap exit always becomes `FAILED`.

## Cycle

### 1. Record `EXPERIMENT_PROPOSED`

Freeze the question, scope, budgets, expected observation, actual operation, comparator, measure,
and raw evidence references. Distinguish observation from interpretation. If the seed experiment has
not run, execute it now and record its actual result before advancing.

Reject a seed that cannot identify what changed, what was measured, or where the raw result lives.

### 2. Record `LEMMA_PROPOSED`

Propose one falsifiable lemma that explains the seed observation. State:

- the causal or logical mechanism;
- a prediction not already guaranteed by the seed result;
- the strongest plausible competing explanation;
- an observable falsifier;
- the scope and boundaries where the claim could hold.

Do not merely restate the metric change. Do not inspect verification results before freezing these
fields.

### 3. Run `VERIFYING`

Choose the smallest **valid discriminating experiment**. Predeclare the intervention, control,
measurement, decision rule, validity requirements, raw-output destination, and stop condition.
Order checks so a cheap decisive falsifier runs early, but never trade away validity, a necessary
control, or measurement integrity for speed.

Classify the result exactly once:

- `SUPPORTS`: the prediction held and distinguishes the lemma from the named alternative;
- `REFUTES`: a valid predeclared falsifier held;
- `INCONCLUSIVE`: the valid result did not discriminate;
- `INVALID`: execution, power, comparator, or measurement could not support an inference.

Fail fast on `REFUTES`, authority or safety failure, corrupted evidence, or a decisive contradiction.
An `INVALID` experiment is not evidence against the lemma. A noisy or graded result needs the
predeclared repetitions or uncertainty analysis; one convenient run is not solid evidence.

### 4. Enter `RETHINK`

Explain the predicted-versus-observed discrepancy before changing anything. Check, in order:

1. whether execution or measurement was invalid;
2. whether the competing explanation better predicts the evidence;
3. whether the lemma should be narrowed, replaced by a materially different mechanism, or rejected;
4. whether one remaining experiment can actually discriminate within budget.

Permit one clearly motivated experiment repair for an `INVALID` result. A revised lemma consumes a
new round and must receive a new prediction and falsifier. Never weaken a falsifier, swap the metric,
discard a control, or relabel evidence after seeing the result. If the next attempt would only repeat
the same mechanism or collect more non-discriminating evidence, stop.

### 5. Terminate

Set `SOLIDATED` only when all of these hold:

- a valid verifying experiment produced the predeclared discriminating signature;
- the named competing explanation is ruled out or explicitly bounded;
- all cited raw evidence is available and no material contradiction is hidden;
- the conclusion states its exact scope, limitations, and falsifier.

`SOLIDATED` means supported enough for the declared project decision. It does not authorize a global
claim, durable skill change, or cross-task reuse; route those through `self-improve` and its transfer
admission.

Otherwise set `FAILED`. Distinguish a refuted lemma from a loop that merely failed to resolve it by
using `failureClass`: `refuted`, `inconclusive_budget`, `invalid_experiment`,
`no_discriminating_test`, `authority_or_safety`, or `contradiction_unresolved`. Preserve the best
next probe when one exists. Failure is useful negative evidence, not permission to manufacture a
replacement insight.

## Return

Return the current `insight-cycle/v1` record and a short plain-language reduction. A terminal run
returns `SOLIDATED` or `FAILED`; a human-guided nonterminal run returns `RETHINK` for the external
checkpoint. Include:

1. the scoped lemma or the exact reason it did not solidate;
2. the decisive experiment and raw evidence references;
3. the strongest remaining limitation or competing explanation;
4. the decision this evidence supports now, if any.

Keep claims no stronger than the record. Do not report activity, confidence language, or artifact
count as evidence.
