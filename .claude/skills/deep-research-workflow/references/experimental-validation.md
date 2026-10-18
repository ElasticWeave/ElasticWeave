# Experimental validation after uncertainty-first preflight and knowledge research

Use this reference only after the core-problem contract is fixed, the uncertainty-first
falsification gate returns `CONTINUE`, the knowledge coverage audit passes, and an auditable
knowledge synthesis exists. It defines a platform-neutral experimental fork. An adapter may map it
to another experiment runner, but it must preserve the contracts, budgets, evidence, and terminal
meanings below.

## Contents

- Entry condition and applicability gate
- Hypothesis ledger
- Deterministic resource gate
- `NOT_READY` validation plan
- `READY` frozen contract and baseline
- Idea board, trial records, and bounded loop
- Cost-aware reasoning and evidence-width escalation
- Concurrency, recovery, and versioning
- Reconciliation and completion audit

## Entry condition

Do not use full experiments to avoid an unresolved preflight blocker or repair weak knowledge
coverage. Enter only when:

1. a validated `coreProblemContract` and candidate version exist;
2. `preflightDecision == CONTINUE` and every unresolved blocker/load-bearing item in the complete
   critical set has valid `VALIDATED` or accepted `NARROWED` evidence;
3. `coverageDecision == PASS` for the research question;
4. `knowledgeArtifactRef` identifies a versioned, auditable synthesis; and
5. the user requested experimental validation or a material conclusion may admit a useful
   empirical test within the declared scope.

`STOP_IDEA_REFUTED`, `NEEDS_USER_DECISION`, and `UNRESOLVED_PREFLIGHT_VALIDATION` cannot enter this
phase. Full tuning, ablations, or confirmatory benchmarks are not substitutes for the missing
preflight decision.

Do not publish the knowledge-only synthesis yet. It is an input to the final reconciliation, not
the final deliverable.

## Experiment applicability gate

Classify applicability before generating hypotheses or inspecting execution resources. A planning
model emits a structured judgment; a deterministic validator checks enum values, known claim IDs,
required fields, and scope/safety constraints.

```json
{
  "experimentApplicability": {
    "verdict": "APPLICABLE",
    "evaluatedClaimIds": ["C-003", "C-011"],
    "claimDispositions": [
      {
        "claimId": "C-003",
        "mode": "EXPERIMENTAL",
        "reason": "a controlled benchmark can distinguish the mechanism",
        "observationalBoundary": null,
        "hypothesisCandidateRef": "candidate://H-001"
      },
      {
        "claimId": "C-011",
        "mode": "OBSERVATIONAL",
        "reason": "the regulatory claim cannot be intervened on within scope",
        "observationalBoundary": {
          "falsifier": "future rule or enforcement evidence that changes the claim",
          "sourceClass": "official-regulatory-record",
          "horizon": "declared review date",
          "updateTrigger": "new rule, ruling, or enforcement action",
          "causalLimit": "observational evidence cannot establish intervention effect"
        },
        "hypothesisCandidateRef": null
      }
    ],
    "reason": "why a controlled minimal test is meaningful"
  }
}
```

The only allowed overall verdicts are `APPLICABLE | NOT_APPLICABLE | INVALID`; values such as
`partial_applicability`, `protocol_only`, or any adapter-defined extension are malformed. The only
claim modes are `EXPERIMENTAL | OBSERVATIONAL`. Require exactly one disposition for every
claim, no duplicates, and the matching non-null field:

- `EXPERIMENTAL` requires `hypothesisCandidateRef` and a null `observationalBoundary`.
- `OBSERVATIONAL` requires a complete `observationalBoundary` and a null
  `hypothesisCandidateRef`.

Compute the overall verdict mechanically after validating the dispositions:

- at least one material `EXPERIMENTAL` claim → `APPLICABLE`, including mixed artifacts;
- all material claims `OBSERVATIONAL` → `NOT_APPLICABLE`;
- missing/duplicate/unknown claims, illegal enum values, inconsistent fields, or unsafe/prohibited
  interventions → `INVALID`.

The computed verdict must equal the emitted verdict; a mismatch is `INVALID` and cannot create a
new route.

Apply these meanings:

- `APPLICABLE`: at least one material claim can be tested by a permissible controlled intervention
  or fixed benchmark with a meaningful comparator, measurable outcome, and potential falsifier.
  Resource availability is not part of this judgment.
- `NOT_APPLICABLE`: every material conclusion is descriptive, historical, market-wide, regulatory,
  ethical, or otherwise not reasonably answerable by a controlled experiment or mechanism
  ablation. Preserve each claim-level observational boundary, then finalize the knowledge report
  without a hypothesis ledger, resource gate, or experiment plan.
- `INVALID`: the mapping cites unknown claims, proposes prohibited/unsafe intervention, or omits the
  evidence needed to distinguish applicability. Preserve the judgment and exit unresolved.

Do not mark a claim `NOT_APPLICABLE` merely because resources are absent; an applicable but
resource-constrained experiment belongs to `NOT_READY`. Do not fabricate hypotheses, controls, or
ablations for a non-experimental research question.

## Hypothesis ledger

Only after `APPLICABLE`, have a planning model derive the ledger, then validate it mechanically
against the claim matrix. Include only claims whose disposition is `EXPERIMENTAL`.

```json
{
  "ledgerVersion": "hypotheses/v1",
  "knowledgeArtifactRef": "artifact://...#sha256",
  "hypotheses": [
    {
      "id": "H-001",
      "sourceClaimIds": ["C-003"],
      "preflightAssumptionIds": ["A-001"],
      "materiality": "material",
      "uncertainty": "high | medium | low",
      "downstreamInvalidation": "all | major | local",
      "hypothesis": "one falsifiable statement",
      "mechanism": "why the intervention should cause the prediction",
      "prediction": {
        "direction": "increase | decrease | invariant | bounded",
        "effectOrRange": "predeclared magnitude, interval, or qualitative bound",
        "conditions": ["fixed workload and scope"]
      },
      "intervention": "one controlled change",
      "controlOrBaseline": "fixed comparator",
      "primaryMetrics": ["decision metric with estimator and threshold"],
      "secondaryMetrics": ["diagnostic or safety metric"],
      "falsifier": "observable outcome that refutes the mechanism or prediction",
      "confounders": ["known alternative explanation"],
      "requiredResources": {
        "compute": ["type and minimum capacity"],
        "data": ["dataset/workload and version"],
        "harness": ["capability, not product name"],
        "budget": ["trials, compute, wall time"],
        "correctnessGate": ["fixed acceptance checks"],
        "isolation": ["workspace and side-effect boundary"]
      },
      "risk": "low | medium | high",
      "priority": 1,
      "priorityRationale": "why uncertainty and downstream invalidation place this before easier work",
      "status": "PROPOSED"
    }
  ]
}
```

The deterministic validator rejects unknown claim IDs, any `OBSERVATIONAL` claim ID, duplicate
hypothesis IDs, an intervention without a control, non-measurable metrics, a falsifier that cannot
differ from the predicted outcome, missing resource classes, or a material `EXPERIMENTAL` claim
without hypothesis coverage. It also rejects a priority order that advances a lower-uncertainty,
local hypothesis while a runnable higher-uncertainty blocker or major-invalidating hypothesis is
unresolved. Cost may select the smallest valid trial for that hypothesis; it cannot select an
easier hypothesis. Preserve the invalid ledger as evidence; do not repair it by inventing sources.
Observational boundaries remain alongside the ledger and outside the experiment loop.

## Resource inventory and deterministic gate

Before inventory, draft and hash one exact `minimalExperimentContractCandidate` from the hypothesis
ledger: workload, data/splits, seeds/repeats, intervention/control, metrics/statistics, correctness
and safety gates, isolation/off-limits, required harness capabilities, maximum trials/compute/wall
time/model calls, and minimum evidence required for a valid decision. This draft executes nothing.

Build `resourceInventory` against that exact candidate from declared project state, beginning with
`resources.md`. Store proof references and digests, not secrets. `READY` freezes the same candidate;
any scope or budget change reruns the resource gate.

| Resource class | Required evidence for readiness |
|---|---|
| Resource declaration | Existing `resources.md`, path, digest, and compatible schema |
| Compute | Available capacity, access scope, authorization reference, expiry, and reservation limit |
| Data/workload | Location, version/digest, license/authority, split policy, and minimum sample size |
| Harness | Required runner capabilities, version, available slots, logging, cancellation, and resume semantics |
| Budget | Authorized maximum trials, model calls, compute, wall time, storage, and any paid external service |
| Correctness/safety gate | Fixed command or criterion, digest, expected outputs, and fail-closed behavior |
| Isolation | Writable paths, off-limits paths, candidate rollback, job namespace, and side-effect boundary |

For every hypothesis/resource requirement, record:

```json
{
  "requirementId": "H-001.compute.0",
  "declared": true,
  "available": true,
  "authorized": true,
  "sufficientForMinimalValidTest": true,
  "proofRef": "resources.md#... or artifact://...",
  "reason": "one deterministic explanation"
}
```

`resource_gate` computes exactly one verdict:

- `READY`: every required entry is declared, currently available, explicitly authorized, and
  sufficient to complete the fixed minimal valid test; the correctness gate and isolation can be
  applied before the first trial.
- `NOT_READY`: `resources.md` is absent, or any required resource is missing, unavailable,
  unauthorized, expired, or insufficient. Partial capacity that cannot complete a valid minimal
  test is `NOT_READY`, even if it can start one.
- `INVALID`: the inventory/schema is malformed or contradictory, the harness cannot preserve the
  correctness/isolation contract, or proof references do not bind the claimed resource state.

Unknown never becomes `READY`. The workflow does not request access, provision infrastructure,
spend undeclared funds, download restricted data, or use ambient credentials. It records what is
needed and follows the non-executing path.

`NOT_READY` is an execution firewall: execute no experiment. It does not authorize even a smoke, baseline, preprocessing, or trial.
No later selector, plateau escape, nested skill, or model advice
may override it; only a new deterministic resource gate over newly authorized declarations can
produce `READY`.

## `NOT_READY`: complete validation plan

Run no benchmark, baseline, smoke trial, or data preprocessing that consumes experimental
resources. The artifact must display:

> **NOT EXECUTED — NOT EXPERIMENTALLY VALIDATED**

Produce a complete plan containing all of:

1. Experimental claim-to-hypothesis mapping with source claim IDs and materiality, plus the
   unchanged observational boundaries for non-experimental claims.
2. Main experiment with the intervention, comparator, prediction, and primary decision metric.
3. Baselines and controls, including a current/standard baseline where applicable.
4. Component ablations and mechanism ablations that separate causal explanations.
5. Negative controls that should not change the target metric.
6. Sensitivity, robustness, and scaling tests over the relevant ranges.
7. Datasets/workloads, immutable versions, train/dev/test or other splits, and leakage rules.
8. Seeds, repeats, estimators, uncertainty intervals/tests, multiplicity rule, noise floor, and the
   criterion for repeating a low-confidence result.
9. Correctness and safety gates that every candidate must pass before metric interpretation.
10. Resource estimate by phase: compute, data, harness capability, wall time, storage, and cost.
11. Dependency-aware execution order: minimum instrumentation → baseline → highest-uncertainty
    load-bearing falsification tests → main test → ablations → robustness/scaling → final coverage
    audit. Easier confirmatory work cannot precede a runnable critical uncertainty.
12. Per-trial and global stopping rules, including invalid, crash, plateau, and budget exits.
13. An outcome-to-decision table covering support, refutation, boundary changes, and inconclusive
    evidence for each material hypothesis.
14. Reproducibility artifacts: frozen contract, environment/harness manifest, data digests, seeds,
    commands/config, raw logs, metrics, patches, board, and report schema.

List the exact missing/insufficient resource entries and the minimum declaration needed to become
`READY`. This is a plan, not an implicit access request.

## `READY`: freeze the experiment contract

Before baseline or trial execution, freeze and hash `experimentContract`:

```json
{
  "schema": "experiment-contract/v1",
  "contractVersion": 1,
  "segmentId": "segment-001",
  "hypothesisLedgerDigest": "sha256:...",
  "benchmarkAndWorkload": {"refs": ["..."], "digests": ["..."]},
  "datasetsAndSplits": {"refs": ["..."], "digests": ["..."], "leakageRules": ["..."]},
  "seedsAndRepeats": {"seeds": [1, 2, 3], "minimumRepeats": 3, "repeatRule": "..."},
  "metrics": {"primary": ["..."], "secondary": ["..."], "statistics": "..."},
  "correctnessAndSafetyGates": {"refs": ["..."], "digests": ["..."]},
  "scope": {"allowed": ["..."], "offLimits": ["..."]},
  "resources": {"inventoryDigest": "sha256:...", "authorizationRefs": ["..."]},
  "budgets": {
    "maxTrials": 12,
    "maxCompute": "...",
    "maxWallTime": "...",
    "maxExperimentModelCalls": 20
  },
  "harness": {"capabilities": ["..."], "versionRef": "...", "availableSlots": 2},
  "isolation": {"workspaceRef": "...", "jobNamespace": "...", "rollback": "..."}
}
```

Changing benchmark, workload, data split, seeds/repeat policy, metrics, correctness gates, scope,
off-limits paths, or budgets bumps `contractVersion` and starts a new comparable contract. Changing
the harness or runner implementation always bumps `segmentId` and reruns the baseline; do not pool
cross-segment results without a predeclared calibration.

Run the complete correctness/safety gate and fixed baseline before initializing trials. A broken
baseline, invalid harness, or non-reproducible setup does not become a plan-only success; preserve
the evidence and exit `UNRESOLVED_EXPERIMENTAL_VALIDATION`.

## Persistent idea board and trial records

Seed a persistent idea board from the validated hypothesis ledger. Keep mechanism-level hypotheses
separate from atomic trials. Store positive and negative outcomes permanently; code candidates may
be reverted, but experiment records may not be deleted or rewritten.

Each trial changes one intervention where practical and records:

- stable `trialId`, parent hypothesis/idea and preflight assumption, contract digest, segment,
  seed/repeat cell, and attempt;
- predeclared prediction, control/baseline, changed surface, and expected mechanism trace;
- `PENDING`/job identity before launch, start/end times, resource use, and recovery state;
- candidate patch/artifact hash, raw logs, metric samples, correctness/safety results, and errors;
- root-cause account, confounder checks, confidence, and one final outcome.

Allowed final outcomes include:

| Outcome | Meaning |
|---|---|
| `SUPPORTED` | Prediction holds with declared confidence and gates pass; mechanism evidence is adequate |
| `REFUTED` | Predeclared falsifier or contrary result holds under a valid test |
| `INCONCLUSIVE` | Noise, power, ambiguity, or unresolved confounding prevents attribution |
| `INVALID` | Contract, data, leakage, correctness, safety, or measurement validity was violated |
| `CRASH` | Execution failed before a valid result; retain logs and recovery state |

Low-confidence valid outcomes receive only the predeclared repeat/seed cells within budget. An
unexplained improvement or degradation remains `INCONCLUSIVE` until `$first-principles` root-causing
identifies a mechanism supported by raw trace evidence. It cannot update the research conclusion.

## Bounded autoresearch loop

Before each trial, the reducer/selector must bind an expected outcome, expected measurement, and
strategy to the frozen contract. After execution, classify the raw result against that expectation
and append the deviation, evidence lineage, alternate interpretation, and next strategy to the trial
record. Repeated deviations or reward-hacking signals require a structural review or stop before a
new trial; they are not a retry reason. Keep this ledger separate from the model's prose explanation
so a surprising metric cannot erase the negative evidence. The same rule applies in both iteration
modes even though human-guided mode moves the ordinary idea discussion to its external checkpoint.

The loop inherits `iteration_mode`. Autonomous mode may continue from a valid reduction into the
next selector after bounded idea/counterevidence review. Human-guided mode completes at most one
propose/execute/reduce cycle per invocation: after `experimental_coverage_audit`, emit the
structured human-observation checkpoint and return before another selection. A later invocation
may select again only with bound feedback or an explicit autonomous-review fallback. Risk triggers
restore targeted idea review before the trial; they never weaken the frozen experiment contract,
correctness/safety gates, deterministic ledger, root-cause analysis, or terminal review.

```text
freeze_contract → baseline → initialize_board
  → select_highest_uncertainty_atomic_trial → persist_PENDING → run → validate_and_log
  → root_cause → classify → update_board → experimental_coverage_audit
       ├─ CONTINUE → select_highest_uncertainty_atomic_trial
       ├─ PLATEAU → one distinct escape → select_highest_uncertainty_atomic_trial
       ├─ CANDIDATE_REFUTED → reconcile_refutation → IDEA_REFUTED
       ├─ COMPLETE → reconcile_report
       └─ HARD_EXIT → reconcile_partial → unresolved
```

For human-guided execution, replace each loop-back edge above with
`reduce_checkpoint -> HUMAN_OBSERVATION_REQUIRED`; the human remains outside the workflow graph.
The checkpoint separately records the hypothesis, actual operation, raw intervention/control
observations, agent interpretation, anomaly or alternate interpretation, candidate next trials,
and questions for the human.

The selector is uncertainty-first. Order blocker/material hypotheses by downstream invalidation,
then current uncertainty and discriminating value; use cost, latency, and implementation ease only
to choose among valid trials for the same priority band. It must not consume the board's easy,
well-understood work while a runnable high-uncertainty load-bearing hypothesis remains. Default to
one trial at a time for attribution. Parallelize only independent repeats or predeclared matrix
cells that share no mutable candidate or hidden ordering.

For a tunable method, the first load-bearing trial includes only the frozen minimum fair tuning
envelope inherited from preflight. Full tuning begins only after mechanism/feasibility blockers
survive and cannot be used to search until the original falsifier disappears. If validity requires
an unbounded search, classify the hypothesis unresolved instead of representing optimization
activity as validation.

### Reasoning and search allocation

Apply the run's global `reasoningProfile` to the experimental phase:

| Experiment work | Initial role |
|---|---|
| Contract validation, resource gate, pending-job records, metric parsing, board updates, budget and coverage counters | deterministic tool |
| Targeted literature or implementation lookup for an already-scoped mechanism | Cheapest live-qualified `research_collect` (Luna-family Max) or `research_collect_anthropic` (Sonnet-family High) |
| Bounded experiment implementation or interpretation-heavy evidence extraction | `task` or `research_analyze` |
| Hypothesis derivation, trial selection, root cause, coverage judgment, report reconciliation | `plan` |
| Evidence-complete but unresolved plateau or mechanism judgment | A distinct `frontier_execute` node, or qualified `frontier_execute_anthropic` alternative, only when that branch was statically expanded and validated before start; `slow` is the permitted Sol X-High compatibility route |
| Challenge one writer-originated hypothesis, simulation, or validation chain | `frontier_advice`, or strong candidates Fable 5.1 (`claude-fable-5-1`) through `frontier_advice_fable` and GPT-6 Astra (`gpt-6-astra`) through `frontier_advice_astra` for hard/materially uncertain advice, one scoped fresh-catalog advisory call |
| Test one named model-family blind spot | One of `diversity_glm`, `diversity_kimi`, or `diversity_grok`, through a live-verified highest-effort Cursor route |

Do not run every trial-selection or root-cause turn on the strongest model. Deterministic single
successors need no model. A leaf that cannot finish within its role returns `NEEDS_ESCALATION` and
the failed acceptance evidence. That evidence sends the current run to a durable checkpoint and
`UNRESOLVED_EXPERIMENTAL_VALIDATION`; it does not mutate the leaf's role or retry it automatically.
A stronger-role node may run in the same design only when it is a distinct, statically expanded
branch with a deterministic route in the already validated design and mapping. Otherwise the top
dispatches a bounded designer/mapper to generate and validate a new design and mapping before a new
workflow run, then inspects the evidence.

When a plateau appears, classify the deficit before spending another strong-model call:

1. **Mechanism/evidence space underexplored:** resume a bounded Deep Research refill. Widen source
   classes or frontier/opportunity branches with one live-qualified collection role; preserve
   prior evidence and import only validated new records into the experiment board.
2. **Evidence present, judgment unresolved:** record `NEEDS_ESCALATION` for the owning
   planning/root-cause node. Route to a distinct `frontier_execute` or qualified
   `frontier_execute_anthropic` node only if that branch was statically expanded and validated
   before start; `slow` is the permitted Sol X-High compatibility path. Otherwise checkpoint the
   current run unresolved and let the top dispatch a bounded designer/mapper, then inspect the
   newly validated design and mapping before a new workflow run.
3. **Writer-originated speculative reasoning needs a challenge:** run one `frontier_advice` brief
   over the exact hypothesis, validation design, simulation, or causal chain. Use
   Fable 5.1 (`claude-fable-5-1`) through `frontier_advice_fable` or GPT-6 Astra
   (`gpt-6-astra`) through `frontier_advice_astra` only when that advice is hard or materially
   uncertain and the selected exact route and retention policy are freshly admitted. If it is
   unavailable or unverifiable, report the advice unresolved and never silently substitute.
4. **Correlated family blind spot is named:** run one live-qualified Kimi-, GLM-, or Grok-family
   Cursor probe at the highest effort proven for that exact model and reconcile the disagreement
   against evidence; do not vote. Prefer literal `max`; when it is absent, pin the provider-local
   highest available effort before dispatch. A future
   DeepSeek-family route requires the same live qualification. Humanize cannot host this route;
   use the built-in scheduler without substitution.

Later experimental iterations may therefore pay for broader search, but not for indiscriminate
model upgrades. Quick research supplies background; plateau widening searches for new mechanisms,
counterexamples, and frontier opportunities.

Compute concurrency as:

```text
effectiveTrialConcurrency = min(
  researchProfile.maxConcurrentBranches,
  adapterAvailableSlots,
  resourceOrServiceLimit
)
```

Reserve capacity for the main coordinator/reducer whenever it shares those pools. If the required
reserved slot leaves no valid worker capacity, the resource verdict is `NOT_READY`. The contract
records the resulting limit; the adapter may lower it but may not raise it.

Every loop has hard cumulative caps for trials, compute, wall time, paid service use, repeats,
crash retries, and plateau escapes. A plateau is a predeclared window with no new supported/refuted
material hypothesis, no meaningful uncertainty reduction, and no new mechanism coverage. One
escape may change measurement, activate a different material hypothesis, run a discriminating
ablation/negative control, or reframe the mechanism. It may not repeat the same failed trial.

## Pending-trial recovery

Persist `PENDING` with the trial side-effect key before launching compute. On resume:

1. Reconcile the job ID, output paths, contract/segment digest, and candidate hash.
2. If still running, attach or poll deterministically; do not launch a duplicate.
3. If complete, validate artifacts and classify it exactly once.
4. If launch never occurred, increment the attempt and relaunch only within retry and budget caps.
5. If success is ambiguous, stop until reconciliation can prove the state.
6. If the contract, harness, data, or candidate changed incompatibly, mark the attempt `INVALID`;
   start a new version/segment instead of reinterpreting old evidence.

Rollback code candidates after invalid, refuted, or crashing trials when the contract calls for it.
Rollback never removes raw logs, patches, board history, or negative conclusions.

## Experimental coverage and reconciliation

The audit maps every material hypothesis to sufficient valid evidence:

- `SUPPORTED` and `REFUTED` are resolved outcomes.
- A valid `REFUTED` blocker immediately makes its declared dependent hypotheses unnecessary and
  routes to `CANDIDATE_REFUTED`; do not run them to accumulate compensatory positive results. The
  dependency graph and refutation evidence, not post-hoc convenience, justify removing them.
- `INCONCLUSIVE`, `INVALID`, `CRASH`, or an unrun material hypothesis remains unresolved unless the
  final report explicitly removes its materiality using knowledge evidence independent of the
  failed experiment.
- Non-material unresolved trials remain visible as limitations but need not block success.
- Exhausted trial/compute/wall-time budgets, an invalid harness, or pending ambiguous work routes
  to `UNRESOLVED_EXPERIMENTAL_VALIDATION` when a material hypothesis remains.

This audit covers only `EXPERIMENTAL` dispositions. `OBSERVATIONAL` claims keep their knowledge
evidence and falsifiability/update boundaries; no experiment outcome may label them experimentally
validated.

Reconcile by claim and hypothesis, not by aggregate score:

| Experimental outcome | Required report action |
|---|---|
| `SUPPORTED` | Strengthen only the tested claim under the frozen conditions |
| `REFUTED` | Reject or revise the original claim/mechanism; this can still be a successful workflow |
| `INCONCLUSIVE` | Narrow confidence/scope and state the next discriminating test |
| `INVALID` / `CRASH` | Do not update the claim; report the harness/gate failure and recovery evidence |
| `NOT_APPLICABLE` | Preserve the knowledge conclusion and state falsifiability, observation triggers, and causal limits; do not imply experimental validation |
| `NOT_READY` plan | Preserve the knowledge conclusion, mark it unvalidated, and attach the full plan |

Only after reconciliation create the final artifact, then apply cleanup, the selected review
policy, and replay-safe delivery once on success routes. An unresolved experimental route produces
a local checkpoint by default. Publish that checkpoint externally only when the request explicitly
authorizes unresolved-result publication, and never represent publication as successful validation.
Completion outcomes are:

- `IDEA_REFUTED`: a valid blocker falsifier holds; dependent work stops, the core problem remains
  explicit, and no unrelated positive result is used to rescue the current candidate;
- `SUCCESS_EXPERIMENTALLY_CHECKED`: all material experimental hypotheses resolved under valid
  contracts; observational claims remain explicitly outside experimental validation;
- `SUCCESS_WITH_EXPERIMENT_PLAN`: no experiment ran because the deterministic gate returned
  `NOT_READY`, and the complete plan passed artifact review;
- `UNRESOLVED_EXPERIMENTAL_VALIDATION`: a material hypothesis, harness, recovery state, or hard
  experiment budget remains unresolved.

`NOT_APPLICABLE` continues through the original knowledge-research success terminal after adding
the observational boundary. Preserve the original knowledge-gap and delivery-failure terminals for
failures before this fork or after final reconciliation.
