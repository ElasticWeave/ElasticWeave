# Uncertainty-first falsification gate

The issue-bound iteration policy controls how much idea-level challenge precedes a round. In
`autonomous`, this full gate is the default. In `human_guided`, a low-risk, reversible, objectively
measurable candidate may use only the minimum validity gate before execution; any ambiguity,
contradiction, plateau or repeated failure, proof claim, irreversible/high-cost/safety-sensitive
action, scope/publication transition, or stale human lease restores this full targeted gate and
records `idea_review_disposition: triggered_by_risk`. The shortened path still freezes alignment,
authority, safety, resources, measurement/comparator, reversibility, and a real falsifier.

Use this gate before broad evidence collection or a full Auto Research loop. Its purpose is to
discover whether the current idea, method, or technical target is worth further investment while
preserving the user's actual problem and constraints.

## Contents

- Separate the core problem from the candidate
- Build and rank critical assumptions
- Search for the fastest valid falsifier
- Run sequential checks with fair tuning
- Continue, refactor, classify a candidate human dependency, or stop
- Evidence and terminal contracts

## Separate invariant intent from mutable implementation

Create and validate this contract before substantial research:

```json
{
  "coreProblemContract": {
    "userOutcome": "the outcome the user actually needs",
    "decision": "the decision this research must support",
    "successCriteria": ["observable acceptance condition"],
    "hardConstraints": ["constraint that cannot be traded away"],
    "constraintsConfirmed": true,
    "nonGoals": ["explicitly excluded outcome"],
    "authorityBoundary": "what the agent may change or execute",
    "confidence": "confirmed | high | low"
  },
  "candidate": {
    "candidateId": "idea-v1",
    "idea": "the proposed solution or explanation",
    "method": "current method",
    "technicalTarget": "mutable subgoal used to reach the user outcome",
    "proxyMetrics": ["metric and its claimed link to the user outcome"],
    "mutableWithinScope": ["method or target changes allowed without changing user intent"]
  }
}
```

Reject a missing user outcome, decision, success criterion, hard-constraint field, or confirmation
state. `hardConstraints` may be empty only when `constraintsConfirmed` records that the user or
authoritative task state has no additional hard constraint. If a material ambiguity could change
what counts as success, checkpoint a candidate human dependency with the exact choice and evidence
that safe agent-side alternatives are exhausted. Outside cold-start goal alignment, a fresh
terminal classification is owned by the inherited `terminalOwner`: return the checkpoint to that
owner, which alone may route the exact reviewed request through the controlling session or
configured UI after typed `HUMAN_BLOCKED`; `REVISE` returns to bounded
investigation. A model may revise the candidate, method, technical target, or proxy; it may not
silently revise the user outcome, acceptance criteria, risk tolerance, authority, or hard
constraints. A proposed change to those invariant fields requires the same reviewed
human-dependency route and a new contract version.

For an open question with no proposed idea, treat the framing, definitions, proxy choices, and
decision assumptions as the candidate. The gate may narrow or repair the question; it must not
manufacture an idea merely to create a kill test.

## Build the critical-assumption ledger

Decompose the candidate into assumptions that could invalidate it. Cover at least:

- problem/value: solving the technical target would materially advance the user outcome;
- mechanism: the claimed causal or logical mechanism can hold;
- feasibility: the method can work under the declared data, compute, time, and integration limits;
- measurement: the proxy measures the intended outcome and cannot be gamed trivially;
- boundary/scaling: the result survives the required regime, distribution, and scale;
- safety/compliance/authority: the method is permissible inside the hard constraints;
- dependency: upstream components or facts needed by later work are actually available and valid.

Each entry has this shape:

```json
{
  "assumptionId": "A-001",
  "statement": "one falsifiable load-bearing assumption",
  "class": "problem_value | mechanism | feasibility | measurement | boundary_scaling | safety_authority | dependency",
  "dependsOn": [],
  "materiality": "blocker | load_bearing | local",
  "uncertainty": "high | medium | low",
  "uncertaintyBasis": "what is unknown rather than merely difficult",
  "downstreamInvalidation": "all | major | local",
  "failureConsequence": "what becomes unnecessary or must change if false",
  "existingEvidenceRefs": [],
  "falsifier": "observable evidence that would defeat or materially narrow it",
  "minimumValidCheck": "smallest check that can discriminate rather than merely produce activity",
  "minimumFairTuneEnvelope": "bounded settings needed to distinguish method failure from a bad default",
  "validityRequirements": ["power, control, baseline, measurement, or scope requirement"],
  "estimatedCost": "relative cost and latency",
  "status": "UNTESTED"
}
```

The validator rejects vague assumptions, circular falsifiers, checks that cannot produce a
different result from the prediction, or a blocker without a valid-check proposal.

## Rank uncertainty before ease

Rank lexicographically. Do not collapse these fields into a cost-dominated score:

1. Remove assumptions already resolved by valid evidence; do not perform work merely to reconfirm
   them.
2. Keep hard-constraint blockers and assumptions whose failure invalidates the whole candidate in
   the critical set.
3. Within that critical set, prioritize current uncertainty together with dependency centrality
   and amount of downstream work invalidated by failure. A high-uncertainty assumption must precede
   a lower-uncertainty assumption when its downstream invalidation is comparable or greater.
4. Discriminating power and validity of the proposed check.
5. Cost, latency, and implementation convenience.

Cost chooses the smallest **valid** check for the same critical assumption. It does not move a
well-understood, easy task ahead of a highly uncertain load-bearing assumption. Instrumentation or
baselines may run first only when they are strict dependencies of that critical check; record this
dependency instead of relabeling setup work as progress.

Build a `killSet` that covers every unresolved blocker and every unresolved load-bearing assumption
whose failure could invalidate the current candidate or make substantial downstream work useless.
Only local assumptions may wait for the later full research loop. Omitting an apparent critical
assumption requires one of three evidenced reasons: `already_resolved`, `not_material`, or
`unsafe_or_prohibited`. “Easier later” and “expensive now” are not valid omission reasons. If the
validated profile cannot cover the complete critical set, widen the profile within authority or
stop unresolved; never shrink the set to fit the budget.

## Search for how the candidate fails

Before broad confirmatory collection, run a bounded falsification search over the kill set. Search
for failed replications, null results, lower bounds, impossibility results, adverse boundary cases,
measurement failures, leakage, stronger baselines, scaling failures, integration constraints, and
resource or authority blockers. Inspect primary evidence where it exists.

The output is not a balanced final survey. It is an attack ledger linking each record to an
assumption, falsifier, scope, evidence quality, and whether it resolves, narrows, or leaves the
assumption open. Preserve supporting evidence too when it is needed to interpret a negative result.

## Plan the fastest valid checks

For every kill-set item, choose the smallest valid discriminator:

- a direct primary-source contradiction or proof;
- a static feasibility or interface check;
- a minimal benchmark against the strongest relevant baseline;
- a negative control or mechanism ablation;
- a small data audit, scaling probe, or power calculation;
- a bounded simulation or implementation spike.

“Cheap” is not enough. Predeclare controls, metrics, thresholds, scope, power or repeat rules,
validity gates, and the outcome-to-decision mapping. If the method is tunable, run only the
`minimumFairTuneEnvelope` needed to avoid rejecting it because of a bad default. Do not turn this
into broad hyperparameter optimization; a candidate that needs full optimization before any
load-bearing claim can be tested remains unresolved.

Apply the deterministic resource and safety gate before an experimental check. If a blocker cannot
be tested validly with declared resources, stop at `UNRESOLVED_PREFLIGHT_VALIDATION` with the exact
missing resource and check plan. Do not continue the full idea merely because easier work remains.

## Execute sequentially and stop on information

Run one critical check at a time in ranked order. After each check, validate raw evidence and emit:

- `VALIDATED`: the assumption survived the predeclared discriminator within its stated boundary;
- `REFUTED`: a valid falsifier holds;
- `NARROWED`: only a smaller scope or weaker claim survived;
- `INCONCLUSIVE`: valid evidence did not discriminate with adequate confidence;
- `INVALID`: the check violated its contract and says nothing about the assumption.

Do not start lower-priority work until the gate has consumed the latest outcome. `REFUTED` and
`NARROWED` are useful research results. `INVALID` is not evidence for or against the idea.

## Continue, refactor, classify a candidate human dependency, or stop

Compute exactly one gate decision:

| Decision | Meaning | Next action |
|---|---|---|
| `CONTINUE_CHECKING` | The latest outcome is valid and another higher-priority kill-set item remains | Run exactly the next ranked check |
| `CONTINUE` | Every unresolved blocker and load-bearing assumption in the complete critical set is `VALIDATED` or acceptably `NARROWED` | Proceed to full evidence coverage and, if applicable, Auto Research |
| `REFACTOR_METHOD` | The current method failed but the core problem and constraints remain intact | Make one bounded method revision, version the candidate, rebuild affected assumptions, and rerun the gate |
| `REFRAME_TECHNICAL_TARGET` | A proxy or technical subgoal is invalid but the user outcome is unchanged | Replace only the derived target/proxy, version the candidate, and rerun the gate |
| `CANDIDATE_HUMAN_DEPENDENCY` | Progress appears to require changing user outcome, success criteria, hard constraints, authority, risk, or a material product tradeoff | Checkpoint the exact proposed request and exhausted alternatives; return it to `terminalOwner`, which alone requests graph-external classification. Only fresh typed `HUMAN_BLOCKED` may authorize the exact human request through the controlling session or configured UI; `REVISE` resumes delegated work |
| `STOP_IDEA_REFUTED` | Valid evidence defeats a blocker and no in-scope candidate repair remains | Stop the current idea and return the negative result without compensatory work |
| `UNRESOLVED_PREFLIGHT_VALIDATION` | A critical assumption remains inconclusive, invalid, or untestable within declared resources | Stop with evidence, missing requirements, and the next discriminating check |
| `INVALID` | Contract, ranking, evidence, or route is malformed or contradicts the core problem | Fail closed and preserve the malformed artifact |

Default to at most one autonomous candidate refactor before asking or stopping. A refactor cannot
delete negative evidence, weaken a falsifier after seeing the result, swap the acceptance metric,
or change a hard constraint. Reusing evidence is required; only affected assumptions are retested.

## Required closeout evidence

Return the core problem contract and version, candidate versions, ranked assumption ledger, kill
set, falsification-search records, check contracts and raw evidence, fair-tuning budget, per-check
outcomes, gate decision, and downstream work avoided. A continuation report must retain narrowed
boundaries. A stopped report must name which user outcome remains unsolved; it must not bury the
refutation beneath unrelated positive findings.
