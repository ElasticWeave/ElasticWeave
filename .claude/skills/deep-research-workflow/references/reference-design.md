# Platform-neutral reference design

This is a non-executable `workflow-design/v1` reference for graph-shaped deep research. It does not
select a harness, pin a model ID, or authorize creation of a canonical `.workflow/designs` artifact.
Use `$build-workflow` to specialize and validate a real design only after workflow admission.

## Contents

- Dispatch contract and capability mapping
- Autonomous and human-guided iteration policy
- Core-problem lock and uncertainty-first falsification gate
- Structured width profile and deterministic validation
- Global reasoning posture and cost-aware semantic-role routing
- State and node contracts
- Knowledge-to-experiment applicability and resource-gated validation
- Routes, loops, and terminal meanings
- Budget and fan-out/fan-in rules
- Side effects, recovery, and review policies
- Specialization checklist

## Dispatch contract

- **Goal:** preserve the user's core outcome and constraints, attack the most uncertain assumptions
  that could invalidate the most downstream work, and continue only after the current candidate
  survives a valid falsification gate; then produce an auditable research artifact whose material
  claims are covered by suitable evidence and whose contradictions and gaps are explicit. When
  controlled validation applies, run it only with a ready resource pipeline or attach a complete
  unexecuted validation plan.
- **Admission:** independent evidence fan-out/fan-in, stateful targeted refill and width escalation,
  replay-safe external delivery, or machine-routed review is required.
- **Demotion:** use one bounded native-subagent owner when it can gather and synthesize the evidence
  without durable branch state, recovery, or machine routing. A top session only inspects and
  dispatches; it does not perform research or edit the artifact.
- **Acceptance:** the versioned core-problem contract is intact; the ranked critical-assumption
  ledger cannot prefer easy certain work over a more uncertain blocker; every preflight check is
  valid and the gate permits continuation, or a stop/refactor/user-decision terminal is honestly
  reached. On continuation, coverage and quality pass; claims retain support links and types;
  material contradictions are resolved or decision-bounded; applicability and resource verdicts
  are evidenced; any experiment or plan is reconciled into one final artifact; requested delivery
  is reconciled.
- **Review:** coordinator selects `independent` or `machine-gated` before execution. A child returns
  its graph/domain review receipts to the inherited `terminalOwner`; only that outer owner obtains
  the assignment's final `COMPLETE | HUMAN_BLOCKED | REVISE`. Coordinator self-approval is not a
  policy.

Select and validate the initial width and global reasoning posture once during coordinator
preflight, then choose the execution lane. A `quick` profile with no other graph requirement
demotes to one bounded native subagent.
When dynamic widening, durable fan-in, recovery, or machine routing admits a workflow, either pass
that validated profile into `seed` or execute `select_profile` as the first model judgment; never do
both. Width describes research breadth, posture bounds reasoning allocation, and admission describes
why a graph is useful. Read `model-routing.md` before specializing node roles.

## Iteration policy

Every iterative specialization carries a validated top-level `iterationPolicy` as defined by
`build-workflow`: `mode`, typed state pointers, complete risk-trigger vocabulary, reducer node,
and mode-specific review/checkpoint nodes. It inherits the PM issue snapshot unchanged.

```text
autonomous:    propose -> bounded idea/counterevidence review -> execute/prove -> reduce -> plan
human_guided:  propose -> minimum validity gate -> execute/prove -> reduce
               -> checkpoint terminal -> graph-external human observation -> next run
```

Human interaction never becomes a model or human node inside the graph. A `human_guided` run ends
at an unresolved checkpoint terminal after reduction; resume evidence in a later run must bind
the human feedback or an explicit `autonomous_review` fallback. Lease expiry with `pause` permits
no new dispatch. Lease expiry with `autonomous_review` records the fallback and restores idea
review before execution. Risk-triggered review is mandatory for ambiguity, contradiction,
plateau/repeated failure, irreversible/high-cost/safety-sensitive work, weak proxies, and stale or
absent human feedback. All safety, validity, code/alignment, evidence, proof, and terminal-review
controls remain mode-independent.

## VeriTrace-style mental-model and deviation contract

The autonomous default needs an explicit state update between execution and the next proposal. For
each load-bearing node or trial, persist an append-only record with:

```text
candidate/version and acceptance criteria
expected outcome and expected measurement
strategy: exploit | verify | substitute | pivot | explore
raw observation and evidence lineage
agent interpretation and alternate interpretation
deviation classification and next strategy
structural_review_required and repeated-deviation count
```

Interpret the observation against the criteria before selecting the next action. A result may satisfy
the expectation, be redundant, contradict prior evidence, be inaccessible for an operational reason,
or be unexpectedly strong/weak. Preserve the observation even when the interpretation changes. A
second occurrence of the same non-success deviation, a contradiction, a weak/manipulable proxy, or a
reward-hacking/jailbreak signal routes to a bounded structural review (`pivot`/`explore`) or a clean
stop; it must not silently retry the same strategy. Structural review proposes a new validated design
and mapping, rather than mutating the running graph. This ledger is a compact transfer of the
[VeriTrace](https://arxiv.org/abs/2605.26081) method; it is evidence and control-flow input, not a
security guarantee.

## Preflight capability mapping

Human interaction remains outside the graph. A missing user outcome, success criterion, hard
constraint, horizon, decision use, destination, or authority becomes a candidate human-dependency
checkpoint. Outside the cold-start launching conversation, the graph returns it to the inherited
outer `terminalOwner`, which dispatches a graph-external reviewer over that checkpoint; only a fresh
typed `HUMAN_BLOCKED` result may authorize the exact request through the controlling session or
configured UI. `REVISE` returns to
bounded work. A workflow cannot change invariant fields to rescue a candidate or authorize stop
with an internal ask route. Run the `bitlesson-selector` agent before dispatching branch work and
bind its exact two-line result to downstream contracts.

| Need | Existing capability | Boundary |
|---|---|---|
| Candidate human-only dependency | terminal owner → reviewer → configured human route | Graph emits only the checkpoint; the inherited outer `terminalOwner` dispatches fresh review, authorizes an exact valid `HUMAN_BLOCKED` request, and either converses directly in interactive mode or delegates Linear/file mutations |
| Lock the core problem and attack assumptions | `first-principles` | Separate invariant user intent from the mutable candidate; identify the highest-uncertainty blockers before widening |
| Select prior run lessons | `bitlesson-selector` | Read-only pre-dispatch selector |
| Cost-aware reasoning assignment | `config/workflows/models.json` + `model-routing.md` | Choose one global posture and the cheapest qualified semantic role per node before dispatch |
| In-graph adversarial review | Actual OpenAI-family writer → `cross-review-openai-writer`; actual Anthropic-family writer → `cross-review-anthropic-writer` | Fresh, read-only, direction-specific paired provider; omitted/mismatched writer identity fails closed; returns only an internal approval/revision outcome for graph routing |
| Human-facing report | `writeup-human-cleanup` | Preserve evidence, citations, and uncertainty |
| Proof or derivation | `proof-rigor` | Conditional; unresolved obligations prevent success |
| Independent issue completion | `codex-verify` | Top dispatches a bounded reviewer whose upstream provider is opposite the actual writer after the graph; not duplicated for machine-gated review |
| Run learning | `self-improve` + `memory` | Only the outer `terminalOwner` appoints one pre-seal bounded closing owner; children return learning candidates, and generalized changes need supported evidence |
| Search-policy/graph defect | `rethink-workflow-design` | Redesign the workflow, not the subject-matter answer |

## Stage-to-node mapping

| Research stage | Reference node or top-session dispatch action |
|---|---|
| Clarify user outcome, success, hard constraints, horizon, destination, and authority | Candidate dependency checkpoint → external terminal reviewer → only typed `HUMAN_BLOCKED` reaches the graph-external configured human route, then `seed` |
| Reconstruct project, repository, Linear, or Notion context | `load_context` |
| Freeze invariant intent and mutable candidate | `lock_core_problem` → `validate_core_problem` |
| Map and rank what can invalidate the candidate | `derive_critical_assumptions` → `validate_assumption_ranking` |
| Select quick, standard, or broad scope | `select_profile` → `validate_profile` |
| Seek evidence against the ranked kill set | `falsification_search[*]` → `reduce_attack_evidence` |
| Test the kill set in uncertainty-first order | `plan_preflight_checks` → `validate_preflight_plan` → sequential READY checks → `preflight_gate` |
| Decide whether to invest further | `CONTINUE`, bounded candidate refactor, candidate human-dependency checkpoint, idea-refuted stop, or unresolved preflight checkpoint |
| Build the source and question plan | `plan_branches` |
| Gather evidence with the appropriate tools | `research_branch[*]` |
| Normalize evidence and retain conflicts | `validate_reduce` |
| Check claim coverage and request focused new evidence | `coverage_audit` → `targeted_refill` or `first_principles_reframe` |
| Form the auditable knowledge conclusion | `synthesize_knowledge` |
| Decide whether controlled validation applies | `classify_experiment_applicability` → `validate_applicability` |
| Derive testable claims and inspect declared resources | `derive_hypotheses` → `validate_hypotheses` → `inventory_resources` → `resource_gate` |
| Validate or plan | READY experiment loop, or NOT_READY `build_validation_plan` |
| Reconcile and audit one final report | `reconcile_report` → optional `proof_gate` → `cleanup` → selected review route |
| Publish and close out | `reconcile_delivery` → `deliver` → selected terminal evidence |

See `falsification-gate.md` for the core-problem, assumption-ranking, minimum-valid-check, bounded
refactor, and stop contracts. See `experimental-validation.md` for the post-preflight applicability
schema, hypothesis/resource contracts, NOT_READY plan contents, READY trial loop, concurrency,
recovery, and reconciliation rules.

## Structured width and reasoning profile

The `select_profile` model node emits only this shape:

```json
{
  "researchProfile": {
    "width": "quick",
    "rationale": "one concise evidence-based reason",
    "question": "decision-shaped research question",
    "horizon": "present | future:<date> | both",
    "decisionUse": "orientation | comparison | decision | frontier",
    "claimDimensions": ["..."],
    "sourceClasses": ["official-doc", "primary-paper", "counterevidence"],
    "counterevidenceQuestions": ["..."],
    "planningSourceTarget": 5,
    "branchSlots": 1,
    "budgets": {
      "maxModelCalls": 20,
      "maxConcurrentBranches": 1,
      "maxTargetedRefills": 0,
      "maxWidthEscalations": 2,
      "maxPreflightChecks": 2,
      "maxCandidateRefactors": 1,
      "maxExternalFetches": 16,
      "maxWallMinutes": 20
    },
    "escalationSignals": ["high_impact_gap", "material_contradiction"],
    "delivery": {
      "kind": "local",
      "destinationRef": null,
      "authorizationRef": "request.deliveryAuthorization"
    }
  },
  "reasoningProfile": {
    "posture": "economy | balanced | frontier",
    "rationale": "why this envelope is sufficient",
    "nodeClasses": {
      "collection": {
        "role": "research_collect",
        "strongerRoleCandidate": "research_analyze",
        "needsEscalationTrigger": "material conflict or interpretation requirement"
      },
      "analysis": {
        "role": "research_analyze",
        "strongerRoleCandidate": "task",
        "needsEscalationTrigger": "named acceptance miss or unresolved mechanism"
      },
      "planning": {
        "role": "plan",
        "strongerRoleCandidate": "frontier_execute",
        "needsEscalationTrigger": "evidence-complete but unresolved load-bearing judgment"
      }
    },
    "highestAutomaticRole": "task | plan (must equal the selected posture's defaultRole)",
    "maxAutomaticEscalations": 0,
    "sameRunStrongerBranchPolicy": "pre-expanded-and-validated-only",
    "diversityProbe": "none | diversity_glm | diversity_kimi | diversity_grok",
    "diversityProbeTrigger": "none | named correlated-blind-spot hypothesis",
    "frontierAdvice": "none | frontier_advice | frontier_advice_fable | frontier_advice_astra",
    "frontierAdviceTrigger": "none | named writer-originated hypothesis or validation risk"
  }
}
```

`validate_profile` is deterministic. It checks required fields, enum values, non-empty unique claim
dimensions, allowed source classes, a static model-call lower bound for the requested branches and
conditional routes, every semantic role against the current role file and global posture, and every
numeric value against both the dispatch envelope and these cumulative run caps. It also checks that
the preflight budget can cover every unresolved blocker and load-bearing assumption in the
validated kill set; a smaller budget is not a reason to omit a hard or uncertain assumption. It
rejects exact
model ids, provider-local effort names, unavailable roles, a strongest role above the envelope, or
an unnamed diversity/frontier-verification trigger. A non-`none` probe requires a non-`none`
trigger; a disabled probe requires the corresponding trigger to be `none`. It also requires
`maxAutomaticEscalations == 0`, requires `highestAutomaticRole` to equal the selected posture's
`defaultRole`, and treats each `strongerRoleCandidate` as design-time input rather than a runtime
role mutation:

| Width reached | Purpose | Branch slots | Planning sources | Concurrent branches | Preflight checks | Targeted refills | Model calls | External fetches | Wall time |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `quick` | orientation and baseline | 1 | 3–6 | 1 | 2 | 0 | 20 | 16 | 20 min |
| `standard` | decision-grade comparison | 3 | 10–18 | 3 | 4 | 1 | 34 | 60 | 75 min |
| `broad` | opportunity, frontier, limitations, counterevidence | 6 | 20–36 | 4 | 6 | 2 | 52 | 140 | 180 min |

Caps are cumulative across escalation. `quick → standard` adds at most two new branch slots, and
`standard → broad` adds at most three; neither transition replays validated branches. Source targets are planning guidance and are never used as a
PASS predicate. If a requested budget exceeds a user or dispatch cap, reject the profile and allow
one structured repair; do not silently clamp scope. The validator also requires the delivery kind,
destination, and `authorizationRef` to match coordinator-supplied request state; a model cannot
grant external-write authority. A second malformed profile exits unresolved. All profiles allow
at most one structured profile repair, one bounded candidate refactor, and one batched review
revision; deterministic delivery
reconciliation may retry twice without consuming model calls. Remaining width escalations are
bounded to two from `quick`, one from `standard`, and zero from `broad`.

Width widening and stronger-role alternatives are planned independently. Add missing source classes
and branch slots before considering a stronger collection role. A leaf may return
`NEEDS_ESCALATION` with failed-acceptance evidence, but that does not change its role. The current
run reaches a checkpoint and the phase-appropriate unresolved terminal unless the validated design
and mapping already contain a statically expanded stronger-role node and deterministic route to it.
Without that branch, the top dispatches a bounded designer/mapper to generate and validate a new
design and mapping before a new workflow run, then inspects the validation evidence. The scheduler
never mutates `reasoningProfile`, a role, effort, or model id.

## State contract

| Field | Type | Writer | Meaning |
|---|---|---|---|
| `runId` | stable string | seed | Attempt and side-effect identity |
| `inputVersion` | string | seed | Resume compatibility |
| `request` | object | seed/coordinator | User outcome, question, horizon, decision use, audience, destination, authority |
| `iterationMode` | `autonomous \| human_guided` | seed from PM issue snapshot | Selected/effective collaboration policy; independent of width, reasoning, budget, and review policy |
| `ideaReviewDisposition` | enum | idea/minimum-validity/risk-review route | `performed \| skipped_human_guided \| triggered_by_risk` plus evidence |
| `iterationRiskTriggers` | validated set | deterministic admission/risk gate | Facts that restore targeted idea review |
| `humanLeaseState` | object or null | seed + deterministic lease gate | Duration, expiry, status, fallback, and fallback record; meaningful only for selected human-guided mode |
| `humanObservationCheckpoint` | object or null | deterministic reducer/checkpoint terminal | Hypothesis, operation, raw observation, interpretation, alternate interpretation, candidate tasks, questions, and bound feedback |
| `iterationResumeEvidence` | object or null | seed on a later run | Binds human feedback or an explicit autonomous-review fallback before replanning |
| `contextRefs` | array of artifact refs | load context | Authorized internal/project context and issue-tree snapshot |
| `lessonSelection` | object | bitlesson selector | Selected IDs and rationale |
| `coreProblemContract` | versioned object | lock/validate core problem | Invariant user outcome, success criteria, hard constraints, non-goals, authority, and confidence |
| `candidate` | versioned object | lock/refactor candidate | Mutable idea, method, technical target, proxy metrics, and allowed in-scope changes |
| `criticalAssumptionLedger` | validated array | derive/validate assumptions | Blocker/load-bearing/local assumptions with uncertainty, downstream invalidation, falsifier, and minimum valid check |
| `killSet` | ordered assumption IDs | validate assumption ranking | Complete unresolved blocker/load-bearing coverage ordered by invalidation and uncertainty before cost; local assumptions may remain downstream |
| `attackEvidenceLedger` | array of refs/records | falsification search/reducer | Negative, boundary, baseline, measurement, resource, and supporting evidence linked to assumptions |
| `preflightCheckPlan` | validated ordered array | plan/validate preflight | Minimum valid checks, controls, thresholds, fair-tuning envelope, resource needs, and outcome routes |
| `preflightCheckLedger` | append-only array | check validator/classifier | Raw refs and `VALIDATED | REFUTED | NARROWED | INCONCLUSIVE | INVALID` per assumption |
| `preflightDecision` | enum + evidence | preflight gate | `CONTINUE_CHECKING | CONTINUE | REFACTOR_METHOD | REFRAME_TECHNICAL_TARGET | CANDIDATE_HUMAN_DEPENDENCY | STOP_IDEA_REFUTED | UNRESOLVED_PREFLIGHT_VALIDATION | INVALID` |
| `researchProfile` | validated object | select/validate profile | Current width and cumulative caps |
| `reasoningProfile` | validated object | select/validate profile | Global posture, fixed node roles, NEEDS_ESCALATION triggers, and design-time stronger-role candidates |
| `budgetLedger` | counters | deterministic budget node | Calls, tokens, wall time, fetches, preflight checks, candidate refactors, refills, and width escalations |
| `needsEscalationEvidence` | object or null | originating leaf + deterministic recorder | Failed acceptance evidence; never an instruction to mutate a running node's role |
| `experimentBudgetLedger` | counters or null | deterministic experiment budget node | Trials, model calls, repeats, crashes, plateau escapes, compute, wall time, storage, paid services |
| `branchPlan` | array | plan branches | Non-overlapping questions, source classes, artifact refs |
| `branchArtifacts` | array of refs | research branches | Isolated raw evidence outputs, never a shared draft |
| `evidenceLedger` | array | deterministic reducer | Validated, canonical evidence records |
| `claimMatrix` | object | deterministic reducer | Claim dimensions × source quality/status |
| `contradictions` | array | reducer/coverage audit | Conflicting records and decision impact |
| `coverageDecision` | enum + reasons | coverage audit | `PASS | REFILL | REFRAME_WIDEN | UNRESOLVED | INVALID` |
| `gapSet` | array | coverage audit | Named missing or weak evidence, with impact |
| `coverageHistory` | array | audit recorder | Detect unchanged gaps and plateau |
| `knowledgeArtifactRef` | artifact ref | knowledge synthesis | Auditable coverage-passed conclusion; never delivered directly |
| `experimentApplicability` | enum + evidence | applicability classify/validate | `APPLICABLE | NOT_APPLICABLE | INVALID` |
| `claimDispositions` | array | applicability classify/validate | Exactly one per claim: `EXPERIMENTAL | OBSERVATIONAL`, reason, and matching boundary/candidate ref |
| `observationalBoundaries` | array | applicability classify/validate | Per-observational-claim falsifier, source/horizon, update trigger, causal limits |
| `hypothesisLedger` | validated object or null | hypothesis derive/validate | Claim-linked falsifiable mechanisms; only for `APPLICABLE` |
| `candidateExperimentContract` | hashed object or null | contract drafter/validator | Exact minimal valid test evaluated by the resource gate |
| `resourceInventory`, `resourceVerdict` | object + enum | inventory/gate | Declared readiness proof and `READY | NOT_READY | INVALID` |
| `experimentContract` | versioned object or null | freeze contract | Fixed workload, seeds, metrics, gates, scope, resources, and budgets |
| `experimentSegment` | stable string or null | freeze/resegment | Harness-comparable execution segment |
| `baselineRef` | artifact ref or null | baseline | Fixed pre-trial measurements and correctness evidence |
| `ideaBoardRef` | artifact ref or null | board initializer/updater | Persistent hypothesis/trial state with append-only negative evidence |
| `pendingTrial` | object or null | persist/reconcile trial | Side-effect key, job ID, candidate hash, contract/segment, attempt |
| `trialLedger` | array | trial validator/updater | Raw evidence refs and `SUPPORTED | REFUTED | INCONCLUSIVE | INVALID | CRASH` |
| `experimentalCoverageDecision` | enum + evidence | experiment audit | `CONTINUE | PLATEAU | CANDIDATE_REFUTED | COMPLETE | HARD_EXIT` |
| `validationPlanRef` | artifact ref or null | plan builder | Complete explicitly unexecuted main/ablation plan |
| `targetTerminal` | terminal enum | preflight/applicability/resource/experiment routes | Refuted, success, user-decision, or unresolved outcome preserved through finalization |
| `reconciledArtifactRef`, `finalArtifactRef` | artifact refs | reconcile/cleanup | Versioned post-experiment-or-plan report and reviewed presentation |
| `internalReviewVerdict` | enum + findings | in-graph reviewer | `PASS | APPROVE | REVISE | UNRESOLVED | INVALID`; used only for graph routing and never authorizes a human block |
| `terminalReviewVerdict` | enum + findings | controller or external terminal reviewer | `COMPLETE | HUMAN_BLOCKED | REVISE`; a human block includes `HUMAN-REQUEST-KIND`, one concrete `HUMAN-REQUEST`, exhausted-alternative evidence, and is not copied from an internal ask/block/unresolved outcome |
| `deliveryState` | object | reconcile/deliver | Stable key, remote IDs, version, read-back evidence |
| `forcedReason` | string or null | cap routes | Why a non-success exit was forced |

Decision state never substitutes for its evidence. Keep full extracts in artifacts; shared state
contains compact records and pointers.

## Nodes

| Node | Type / role | Reads | Writes | Responsibility and failure behavior |
|---|---|---|---|---|
| `seed` | deterministic | coordinator input | run/input/request state | Reconcile an existing run; reject incompatible input versions |
| `load_context` | tool or model / `research_collect` | request, authorized workspace/destination | context refs | Read repository and issue/page hierarchy before external search; missing required context is a named gap |
| `lock_core_problem` | model / `plan` | request, context refs | candidate core contract and candidate | Separate invariant user intent/constraints from the mutable method, target, and proxy; low-confidence material fields produce a candidate human-dependency checkpoint, never direct permission to ask or stop |
| `validate_core_problem` | deterministic | candidate core contract, request authority | core problem contract, candidate | Reject drift, missing success/constraint fields, unauthorized changes, or a proxy with no stated link to the user outcome |
| `select_lessons` | agent / mechanical | request, memory index | lesson selection | Run `bitlesson-selector`; malformed output is unresolved |
| `select_profile` | model / `plan` | request, context refs, lessons, validated critical assumptions, budget envelope | candidate profile | Make the width judgment around the ranked kill set; emit the exact schema |
| `validate_profile` | deterministic | candidate profile, envelope | validated profile | Check schema/caps; one repair allowed, then unresolved |
| `derive_critical_assumptions` | model / `plan` | core contract, candidate, context, lessons | candidate assumption ledger | Name falsifiable problem-value, mechanism, feasibility, measurement, scaling, safety/authority, and dependency assumptions; expose uncertainty and downstream invalidation |
| `validate_assumption_ranking` | deterministic | candidate ledger, preflight cap | validated ledger, ordered kill set | Exclude only assumptions already resolved by valid evidence; require all remaining blocker/load-bearing items; jointly rank uncertainty, invalidation, dependency centrality, and discriminating validity before cost; reject budget-driven omission, easy-work substitution, and lower-uncertainty work ahead of comparable-impact uncertainty |
| `falsification_search[*]` | model / `research_collect` or `research_analyze` | one kill-set assumption and source class | isolated attack artifact | Seek failed/null evidence, counterexamples, stronger baselines, invalid proxies, boundaries, scaling/resource blockers, and needed supporting context; never write the final report |
| `reduce_attack_evidence` | deterministic | attack artifacts | attack evidence ledger | Validate records, bind each to an assumption/falsifier/scope, dedupe, and preserve contrary evidence |
| `plan_preflight_checks` | model / `plan` | kill set, attack evidence, core contract, candidate | candidate ordered check plan | Choose the smallest valid discriminator for each blocker, with controls, thresholds, validity, minimum fair tuning, resources, and outcome routes |
| `validate_preflight_plan` | deterministic | candidate plan, ledger, budgets, constraints | preflight check plan | Reject omitted higher-priority assumptions, underpowered or non-discriminating checks, post-result falsifier changes, full optimization, or resource use without a declared gate |
| `preflight_resource_gate` | deterministic/tool | next check, declared resources and authority | `NOT_REQUIRED | READY | NOT_READY | INVALID` | Permit non-experimental evidence checks directly; permit experimental checks only when the minimum valid check is fully ready and authorized |
| `run_preflight_check` | tool/runner or bounded model / `task` | exactly one validated check | raw check artifacts | Execute only the next ranked check and its fair-tuning envelope; no lower-priority work or broad optimization |
| `classify_preflight_check` | model + deterministic validator / `plan` | check contract, raw artifacts, prior ledger | append-only check outcome | Emit `VALIDATED | REFUTED | NARROWED | INCONCLUSIVE | INVALID`; an invalid check cannot update the assumption |
| `preflight_gate` | deterministic | core contract, ledger, kill set, all current outcomes, budgets | preflight decision | Pause after every outcome and compute one continue/refactor/candidate-human-dependency/stop/unresolved/invalid route before another check; its internal decision never authorizes a human ask |
| `refactor_candidate` | model / `plan` | fixed core contract, failed assumptions, remaining refactor budget | new candidate version, affected assumption set | Revise only method or derived technical target/proxy; preserve negative evidence and rerun affected assumptions; any invariant change routes to `CANDIDATE_HUMAN_DEPENDENCY` |
| `plan_branches` | model / `plan` | profile, lessons, prior evidence/gaps | branch plan | Assign non-overlapping claim/source tasks within remaining slots |
| `research_branch[*]` | model / `research_collect` or `research_analyze` | one branch contract | isolated branch artifact | Gather linked evidence; use analysis only for a predeclared interpretation-heavy branch; no final-report or shared-state writes |
| `validate_reduce` | deterministic | branch artifacts | ledger, matrix, contradictions | Reject malformed records, canonicalize, dedupe, preserve conflicts |
| `coverage_audit` | model / `plan` | question, profile, matrix, contradictions, history | decision, gaps | Judge claim coverage/quality; never use count alone |
| `targeted_refill` | model / `research_collect` | named local gaps | new isolated artifact | Search only the gaps; cap by remaining refill/branch/fetch budget |
| `first_principles_reframe` | model / `plan` | question, unexplained difficulty, gaps, contradictions | reframed question/dimensions | Challenge assumptions and name discriminating evidence before widening |
| `escalate_profile` | deterministic | valid profile, reframe, ledger | next valid profile | Only `quick→standard→broad`; reuse evidence; fail on cap conflict |
| `synthesize_knowledge` | model / `plan` | accepted matrix, contradictions, gaps | knowledge artifact ref | Form an auditable conclusion; do not deliver this intermediate artifact |
| `classify_experiment_applicability` | model / `plan` | knowledge artifact, claim matrix, scope/safety | candidate applicability | Decide whether a permissible controlled test is meaningful; do not consider resource availability |
| `validate_applicability` | deterministic | candidate applicability, claim matrix, constraints | applicability, claim dispositions, observational boundaries | Allow only the two fixed enum sets; require one disposition per claim; compute APPLICABLE iff any material claim is experimental, NOT_APPLICABLE iff all material claims are observational, else INVALID |
| `derive_hypotheses` | model / `plan` | APPLICABLE judgment, experimental dispositions, knowledge artifact | candidate hypothesis ledger | Emit source-linked mechanism, prediction, intervention/control, metrics, falsifier, confounders, resources, risk, priority only for experimental claims |
| `validate_hypotheses` | deterministic | candidate ledger, dispositions, claim matrix | hypothesis ledger | Require coverage of every material experimental claim and reject every observational claim reference, unknown claim, non-falsifiable item, missing control/metric/resource, or duplicate |
| `draft_minimal_experiment_contract` | model + deterministic validator | hypothesis ledger, constraints | candidate experiment contract | Fix the exact no-execution workload/data/seeds/metrics/gates/isolation/harness/budgets to be evaluated |
| `inventory_resources` | deterministic/tool | candidate contract, `resources.md`, authorized manifests | resource inventory | Enumerate proof refs; never create resources, request access, or infer ambient authority |
| `resource_gate` | deterministic | hypothesis ledger, candidate contract, inventory | resource verdict | `READY` only when every resource can complete that fixed minimal test; absent `resources.md` is `NOT_READY` |
| `build_validation_plan` | model / `plan` | ledger, candidate contract, NOT_READY entries, knowledge artifact | validation plan ref | Produce the complete main/ablation plan and mark it not executed/not validated |
| `freeze_experiment_contract` | deterministic | candidate contract, READY inventory proof | contract, segment | Freeze/version the exact gate-checked benchmark, data, seeds, metrics, gates, scope, off-limits, harness capabilities, isolation, budgets |
| `run_baseline` | tool/runner | frozen contract, isolated workspace | baseline ref | Run gates and baseline before trials; failure routes unresolved |
| `initialize_idea_board` | deterministic | ledger, contract, baseline | idea board ref | Seed material hypotheses and preserve all later positive/negative trial records |
| `select_trial` | deterministic or model / `plan` | board, contract, budgets, coverage | atomic trial spec | Select the most uncertain unresolved material hypothesis with the greatest downstream invalidation, then its smallest discriminating intervention; use deterministic selection when only one legal trial remains; parallelize only independent repeats/matrix cells |
| `persist_pending_trial` | deterministic | trial spec, contract/segment, candidate hash | pending trial | Persist side-effect key before compute launch |
| `run_trial` | tool/runner | pending trial, frozen contract, isolated workspace | job/raw artifacts | Execute without changing the contract; recover ambiguous jobs before retry |
| `validate_log_trial` | deterministic | raw artifacts, contract, baseline, gates | validated trial record | Check correctness, leakage, metrics, resource use, and raw evidence; retain invalid/crash logs |
| `root_cause_trial` | model / `plan` | prediction, valid record, traces, confounders | causal account/confidence | Explain prediction-versus-result discrepancy; unexplained movement remains inconclusive |
| `update_idea_board` | deterministic | trial record, causal account | board, trial ledger | Append outcome; candidate rollback may occur but negative evidence cannot be deleted |
| `experimental_coverage_audit` | model + deterministic cap check | ledger, board, trials, budgets | experiment decision | Stop immediately on a valid candidate-invalidating blocker refutation; otherwise resolve every material hypothesis or choose continue/plateau/hard exit |
| `plateau_escape` | model / `plan` | plateau evidence, uncovered mechanisms, remaining budget | distinct next action | Choose one new measurement/control/ablation/reframe; never repeat the same failed trial |
| `reconcile_report` | model / `plan` | core contract, preflight evidence, optional knowledge artifact, applicability, trials or plan, open limits | reconciled artifact, target terminal | Preserve the user outcome; support, refute, or narrow the candidate/claims; preflight refutation is a valid report even before broad synthesis |
| `proof_gate` | conditional / `plan` | proof-bearing reconciled artifact | proof artifact/obligations | Apply `proof-rigor` only when needed; obligations route unresolved |
| `cleanup` | model / `research_analyze` | reconciled/proof artifact, audience | final artifact ref | Apply `writeup-human-cleanup`; preserve sources, experimental status, and caveats |
| `frontier_reasoning_advise` | conditional agent / `frontier_advice`, `frontier_advice_fable`, or `frontier_advice_astra` | one writer-originated hypothesis, validation design, simulation, or mechanism chain plus its evidence | scoped findings | Optional advice only; exact Fable 5.1 (`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`) are strong candidates for hard or materially uncertain advice after fresh route/retention admission; no role authors the main report or substitutes for completion review, and an unavailable selection remains unresolved |
| `diversity_probe` | conditional agent / `diversity_glm`, `diversity_kimi`, or `diversity_grok` | one named correlated-blind-spot hypothesis and shared evidence | scoped comparison | Optional one-family probe at that exact Cursor model's highest fresh-catalog effort; reconcile disagreement mechanically or in the owning planning node rather than voting; Humanize is ineligible for this node |
| `adversarial_review` | conditional direction-specific agent from the resolved writer family | request, actual writer semantic role/model/upstream family, profile, final artifact, claim matrix or preflight ledgers plus fresh acceptance evidence | internal verdict/findings | Shipped exact pairing: OpenAI Astra actual writer -> `cross-review-openai-writer` on Anthropic Opus `advisor` Max; Anthropic Opus actual writer -> `cross-review-anthropic-writer` on OpenAI Astra `codex_advisor` Max. It emits only `PASS | APPROVE | REVISE | UNRESOLVED | INVALID`, binds reviewed state and fresh live-catalog identities, and fails unresolved on unavailable, omitted, mismatched, malformed, stale, or silently substituted routes |
| `revise` | model / `research_analyze` | final artifact, batched findings | new final artifact | One evidence-bearing revision; no new unsupported claims |
| `reconcile_delivery` | deterministic/tool | destination, key, artifact version | delivery state | Read before write; detect prior/ambiguous success |
| `deliver` | tool | authorized delivery state, final artifact | remote IDs/read-back | Idempotent external write; ambiguous failure returns to reconcile |

Every model node emits a machine-checkable object plus artifact refs. Sessions are fresh except
state-mediated refill/revision; no hidden transcript continuity is required.

Rows that show alternative semantic roles are specialization choices, not runtime model choice.
The canonical design must select one exact role for every concrete node before validation. If width
or branch class remains dynamic inside the graph, statically unroll class-specific nodes and route
to them deterministically. A stronger-role retry is likewise a distinct, statically expanded node
with a validated deterministic edge, never a mutation of the originating node. If that node and
edge are absent, `NEEDS_ESCALATION` records evidence and closes the current run unresolved. No state
field may contain an executable model id.

Deterministic, planning, and collection nodes are workspace-read-only except for isolated run
artifacts. Candidate versions, attack/check ledgers, knowledge synthesis, experiment
contracts/boards/trials, reconciliation, and cleanup write only versioned artifacts in declared
isolation; delivery tools own the external destination.
Set explicit per-node timeouts within the relevant research or experiment wall-time cap. A timeout
routes to recovery, refill, or unresolved as declared; it never becomes implicit success.

## Routes and bounded loops

```text
seed → load_context → lock_core_problem → validate_core_problem
  → select_lessons → derive_critical_assumptions → validate_assumption_ranking
  → select_profile → validate_profile
  → falsification_search[*] → reduce_attack_evidence
  → plan_preflight_checks → validate_preflight_plan
  → preflight_resource_gate(next ranked check)

validate_core_problem.CANDIDATE_HUMAN_DEPENDENCY
  → finalize_human_dependency_checkpoint → NEEDS_USER_DECISION

validate_core_problem.INVALID or validate_assumption_ranking.INVALID
or validate_preflight_plan.INVALID
  → finalize_checkpoint → UNRESOLVED_PREFLIGHT_VALIDATION

preflight_resource_gate.NOT_REQUIRED or preflight_resource_gate.READY
  → run_preflight_check → classify_preflight_check → preflight_gate

preflight_resource_gate.NOT_READY or preflight_resource_gate.INVALID
  → finalize_checkpoint → UNRESOLVED_PREFLIGHT_VALIDATION

preflight_gate.CONTINUE_CHECKING
  → preflight_resource_gate(next ranked check)

preflight_gate.CONTINUE
  → plan_branches → research_branch[*] → validate_reduce → coverage_audit

preflight_gate.REFACTOR_METHOD or preflight_gate.REFRAME_TECHNICAL_TARGET
and candidate_refactor_budget_remaining
  → refactor_candidate → validate_core_problem
  → derive_critical_assumptions → validate_assumption_ranking
  → validate_profile(affected kill set and remaining cumulative budget)
  → falsification_search[*](affected assumptions only) → reduce_attack_evidence
  → plan_preflight_checks → validate_preflight_plan

preflight_gate.CANDIDATE_HUMAN_DEPENDENCY
  → finalize_human_dependency_checkpoint → NEEDS_USER_DECISION

preflight_gate.STOP_IDEA_REFUTED
or refactor decision with no candidate_refactor_budget_remaining
  → reconcile_report(refutation, target=IDEA_REFUTED)
  → finalize_success → IDEA_REFUTED

preflight_gate.UNRESOLVED_PREFLIGHT_VALIDATION or preflight_gate.INVALID
  → finalize_checkpoint → UNRESOLVED_PREFLIGHT_VALIDATION

coverage_audit.PASS
  → synthesize_knowledge → classify_experiment_applicability → validate_applicability

validate_applicability.NOT_APPLICABLE
  → reconcile_report(observational boundary, target=SUCCESS)
  → finalize_success → SUCCESS

validate_applicability.APPLICABLE (including mixed dispositions)
  → derive_hypotheses → validate_hypotheses
  → draft_minimal_experiment_contract → inventory_resources → resource_gate

validate_applicability.INVALID or validate_hypotheses.INVALID
or draft_minimal_experiment_contract.INVALID
  → reconcile_report(invalid evidence, target=UNRESOLVED_EXPERIMENTAL_VALIDATION)
  → finalize_checkpoint → UNRESOLVED_EXPERIMENTAL_VALIDATION

resource_gate.NOT_READY
  → build_validation_plan → reconcile_report(plan, target=SUCCESS_WITH_EXPERIMENT_PLAN)
  → finalize_success → SUCCESS_WITH_EXPERIMENT_PLAN

resource_gate.INVALID
  → reconcile_report(invalid resource evidence, target=UNRESOLVED_EXPERIMENTAL_VALIDATION)
  → finalize_checkpoint → UNRESOLVED_EXPERIMENTAL_VALIDATION

resource_gate.READY
  → freeze_experiment_contract → run_baseline → initialize_idea_board
  → select_trial → persist_pending_trial → run_trial → validate_log_trial
  → root_cause_trial → update_idea_board → experimental_coverage_audit

experimental_coverage_audit.CONTINUE
  → select_trial

experimental_coverage_audit.PLATEAU and escape_budget_remaining
  → plateau_escape → select_trial

experimental_coverage_audit.CANDIDATE_REFUTED
  → reconcile_report(refuted blocker and dependent hypotheses, target=IDEA_REFUTED)
  → finalize_success → IDEA_REFUTED

experimental_coverage_audit.COMPLETE
  → reconcile_report(trial ledger, target=SUCCESS_EXPERIMENTALLY_CHECKED)
  → finalize_success → SUCCESS_EXPERIMENTALLY_CHECKED

experimental_coverage_audit.HARD_EXIT or invalid baseline/harness/pending recovery
  → reconcile_report(partial evidence, target=UNRESOLVED_EXPERIMENTAL_VALIDATION)
  → finalize_checkpoint → UNRESOLVED_EXPERIMENTAL_VALIDATION

finalize_success
  := [proof_gate if needed] → cleanup
     → [machine-gated adversarial_review]
     → reconcile_delivery → deliver/skip → targetTerminal

finalize_checkpoint
  := cleanup(local checkpoint)
     → [machine-gated adversarial_review]
     → [deliver only if unresolved-result publication was explicitly authorized]
     → original unresolved targetTerminal

finalize_human_dependency_checkpoint
  := cleanup(local candidate dependency with exact request + exhausted alternatives)
     → [no ask, delivery, or terminal permission inside graph]
     → NEEDS_USER_DECISION candidate checkpoint

adversarial_review.PASS or adversarial_review.APPROVE
  → reconcile_delivery

adversarial_review.REVISE and revision_budget_remaining
  → revise → adversarial_review

adversarial_review.REVISE and revision_budget_exhausted
  → durable checkpoint → phase-appropriate unresolved targetTerminal

adversarial_review.UNRESOLVED or adversarial_review.INVALID
  → durable checkpoint → phase-appropriate unresolved targetTerminal

reconcile_delivery/deliver cannot confirm requested delivery within retry cap
  → UNRESOLVED_DELIVERY

graph_return(independent policy, any targetTerminal)
  → top session dispatches external terminal reviewer
  → terminal COMPLETE or HUMAN_BLOCKED permits session stop
  → terminal REVISE requires top-session redispatch

graph_return(any policy, NEEDS_USER_DECISION checkpoint)
  → top session dispatches fresh external terminal reviewer over exact request + alternatives
  → typed terminal HUMAN_BLOCKED authorizes the exact graph-external human request
  → terminal REVISE requires bounded investigation/redesign; no ask and no stop

coverage_audit.REFILL and refill_budget_remaining
  → targeted_refill → validate_reduce → coverage_audit

coverage_audit.REFRAME_WIDEN and wider_profile_allowed
  → first_principles_reframe → escalate_profile → validate_profile
  → plan_branches(new slots only) → research_branch[*] → validate_reduce → coverage_audit

coverage_audit.UNRESOLVED or any required cap exhausted
  → UNRESOLVED_WITH_GAPS

any_model_leaf.NEEDS_ESCALATION and prevalidated_stronger_branch_exists
  → statically_named_stronger_role_node

any_model_leaf.NEEDS_ESCALATION and no_prevalidated_stronger_branch
  → finalize_checkpoint → phase_appropriate_unresolved_terminal
```

Explicit route rules:

- The core-problem contract is validated before profile selection, attack search, or branch work.
  Any proposed invariant change checkpoints for user input; only candidate fields may refactor.
- The kill set covers every unresolved blocker and load-bearing assumption whose failure can
  invalidate the candidate or substantial downstream work. Within comparable-or-greater impact, a
  more uncertain assumption precedes a less uncertain one; cost comes only after invalidation,
  uncertainty, and check validity. A cheaper certain task cannot run while a more uncertain blocker
  is runnable, except for a declared setup dependency of that blocker. Insufficient budget widens
  within authority or stops unresolved; it never removes a critical item.
- Preflight checks run sequentially. Every outcome returns to `preflight_gate` before the next
  check, and no broad branch or full Auto Research node is reachable before `CONTINUE`.
- A tunable method receives only its predeclared minimum fair tuning envelope before refutation.
  Broad tuning is downstream work and cannot be used to postpone a blocker decision.
- `STOP_IDEA_REFUTED` preserves the unsolved user outcome and negative evidence; it never routes to
  compensatory confirmatory research. An autonomous refactor is bounded and cannot weaken the
  falsifier, acceptance metric, or hard constraints after seeing a result.
- A critical check whose minimum valid form is unready, unsafe, inconclusive, or invalid reaches
  `UNRESOLVED_PREFLIGHT_VALIDATION`; easier remaining work does not justify continuation.
- A high-impact gap, material contradiction, frontier uncertainty, repeated unexplained iteration
  difficulty, or plateau routes through first-principles reframing before widening.
- `coverage plateau` means the same material gap set survives two audits with materially different
  evidence. It cannot trigger identical refill again.
- Refills name exact gap IDs and add new evidence. Every refill increments a deterministic counter.
- At `broad`, `REFRAME_WIDEN` becomes `UNRESOLVED`; never wrap to `quick`.
- Missing/malformed coverage or review output routes to `INVALID`, never success.
- One batched review revision is the default; the specialized design must cap any additional cycle.
- A non-empty proof obligations ledger routes to `UNRESOLVED_WITH_GAPS`.
- A malformed profile routes through one structured repair and then to `UNRESOLVED_WITH_GAPS`.
- If the next width exceeds the dispatch envelope, exit unresolved with `forcedReason`.
- `NEEDS_ESCALATION` is evidence, not a runtime role mutation. A stronger-role node may run in the
  same design only when that distinct node and deterministic edge were statically expanded and the
  design and mapping were validated before start. Otherwise finalize the current checkpoint; the
  top dispatches a bounded designer/mapper to validate a new design and mapping before a new run,
  then inspects its evidence.
- Applicability is judged before hypothesis generation. `NOT_APPLICABLE` records an observational
  falsifiability boundary and never fabricates an experiment or ablation plan.
- Applicability has no partial or adapter-specific verdict. Exactly one disposition covers each
  claim; any material EXPERIMENTAL disposition makes the overall verdict APPLICABLE, while
  all-observational material claims make it NOT_APPLICABLE.
- The hypothesis ledger and experiment loop cover only EXPERIMENTAL dispositions. OBSERVATIONAL
  claims keep their falsifiability/update boundaries and are never reported as experimentally
  validated.
- Full-experiment resource readiness is judged only after a valid hypothesis ledger. Preflight
  experimental checks use the same fail-closed readiness semantics against their smaller frozen
  contracts. Missing `resources.md`, absent authorization, or insufficient partial resources is
  `NOT_READY`, never inferred `READY`.
- A malformed or internally inconsistent minimal experiment contract fails closed before resource
  inventory and routes to `UNRESOLVED_EXPERIMENTAL_VALIDATION`.
- For every APPLICABLE artifact, including a mixed one, absent `resources.md` routes through
  resource `NOT_READY` to `SUCCESS_WITH_EXPERIMENT_PLAN`; only INVALID routes unresolved.
- `NOT_READY` executes no baseline, preprocessing, smoke test, or trial; its plan is explicitly
  marked not executed and not experimentally validated.
- READY trials are atomic and sequential by default. Only independent repeats or predeclared matrix
  cells may share a batch, and no result updates a claim before root-cause attribution.
- `SUPPORTED` and `REFUTED` both resolve a hypothesis. Refutation revises the report and is not a
  workflow failure; unresolved material hypotheses, invalid harness state, ambiguous pending work,
  or hard budget exhaustion route to experimental unresolved.
- Cleanup, selected review, and delivery consume only `reconciledArtifactRef`; the intermediate
  knowledge artifact is never published first.
- Non-success experimental routes default to a versioned local checkpoint. External publication
  requires explicit unresolved-result authority and cannot alter the unresolved target terminal.

`rethink-workflow-design` is not a node on the normal domain-research loop. When traces implicate
the graph/search policy itself—unreachable useful routes, repeated same-branch behavior,
systematic loss at fan-in, persistent multi-run coverage plateau after valid reframing/widening, or
unacceptable quality-cost behavior—the top authorizes a later bounded redesign owner and inspects
its proposal/evidence; it does not perform the redesign.

Apply `../../writing-plans/references/invocation-envelope.md`. Only the outer `terminalOwner`
schedules the bounded `$self-improve`/`$memory` beat: the top dispatches a bounded closing owner
before sealing the evidence that the final reviewer/proof binds. The closing worker writes only the
authorized source layer, never `.memory/MAIN.md`, and returns its receipt without starting another
review. Child research runs,
graph nodes, terminal reviewers, and post-verdict mechanical workers return scoped receipts and do
not repeat this lifecycle.

## Terminal meanings

Every domain terminal below additionally carries a fresh different-provider terminal-review verdict
whose upstream provider is opposite the actual writer, over its scoped acceptance contract,
final/checkpoint artifact, and current evidence. `REVISE`
requires continuation or top-session redispatch and cannot close the assignment; only `COMPLETE` or
a valid structured `HUMAN_BLOCKED` permits the owning session to yield terminally.

| Terminal | Meaning | Required evidence |
|---|---|---|
| `IDEA_REFUTED` | A valid preflight or full-experiment check defeated a blocker and no allowed candidate repair survived; the current idea stops while the user's core problem remains explicit | Core-problem/candidate versions, ranked assumption/kill set, frozen check or experiment contract, raw refutation evidence, fair-tuning record, dependency impact, refactor history, avoided downstream work, reconciled negative report |
| `NEEDS_USER_DECISION` | Candidate checkpoint: continuing appears to require clarifying or changing the user outcome, success criteria, hard constraints, authority, risk, or a material tradeoff; it is not itself permission to ask or stop | Core-problem draft/version, exact unresolved choice, why candidate-only refactoring is insufficient, exhausted safe alternatives, checkpoint refs, no unauthorized downstream work; graph-external reviewer must still issue typed `HUMAN_BLOCKED` or `REVISE` |
| `UNRESOLVED_PREFLIGHT_VALIDATION` | A blocker or load-bearing assumption is untestable, inconclusive, invalid, unsafe, resource-blocked, or malformed before broad investment | Ranked ledger, next minimum valid check, resource/validity gaps, attack/check evidence, spent preflight budget, candidate version, local checkpoint |
| `SUCCESS` | Preflight permitted continuation, knowledge coverage passed, and controlled experimentation was not applicable; observational falsifiability boundaries are explicit | Core contract, candidate version, preflight `CONTINUE`, valid profile, claim matrix, coverage PASS, `NOT_APPLICABLE` evidence, reconciled artifact, fresh terminal `COMPLETE` review evidence, delivery read-back or local-only state |
| `SUCCESS_EXPERIMENTALLY_CHECKED` | Preflight permitted continuation and every material experimental hypothesis was supported or refuted by valid frozen-contract evidence and reconciled; observational claims retain separate boundaries | Core contract, preflight `CONTINUE`, contract/segment digests, baseline, append-only board/trial ledger, experimental coverage COMPLETE, observational boundaries, reconciled artifact, selected review/delivery evidence |
| `SUCCESS_WITH_EXPERIMENT_PLAN` | Preflight permitted continuation, but full applicable validation could not run because resources were `NOT_READY`; a complete plan was reviewed and no experiment was represented as executed | Core contract, preflight `CONTINUE`, hypothesis ledger, NOT_READY requirement matrix, explicit not-executed banner, main/ablation plan, reconciled artifact, selected review/delivery evidence |
| `UNRESOLVED_EXPERIMENTAL_VALIDATION` | Applicability/resource/harness state is invalid, a material hypothesis is unresolved, pending work is ambiguous, or experiment budget expired | Current contract/segment, resource verdict, trial ledger/board, pending recovery state, spent budgets, versioned local checkpoint, one next action; any external publication remains explicitly authorized and unresolved |
| `UNRESOLVED_WITH_GAPS` | Material gap, contradiction, proof obligation, invalid output, or cap prevents an honest answer | Gap set, contradictions, spent budgets, forced reason, current artifact/evidence refs, one next action |
| `UNRESOLVED_DELIVERY` | Research artifact passed but requested side effect is unavailable or ambiguous | Accepted local artifact, delivery key, reconciliation evidence, remote state if known |
| runtime abort | Corrupt/incompatible state, unauthorized side effect, or unsafe tool failure | Error plus last durable checkpoint; never a success result |

Research or experiment budget expiry is non-success when material coverage remains. Source-count
attainment is not a terminal predicate.

## Budget accounting

Maintain separate cumulative ledgers for model calls by semantic role, input/output tokens when
the native provider exposes them, research wall time,
external fetches, preflight checks, fair-tuning cells, candidate refactors, width escalations,
targeted refills, experiment trials, repeats, crashes, plateau escapes, compute, experiment wall
time, paid services, storage, review revisions, and delivery retries. Check the relevant ledger
before each activation. Polling, parsing, canonicalization,
deduplication, counter increments, and job reconciliation belong to deterministic tools.

Use the role file's `relativeCost` only as a routing rank. Actual cost reporting uses provider or
relay billing evidence; if that telemetry is unavailable, report model/effort activations and mark
money cost unknown rather than fabricating a conversion.

The specialized design's `maxModelCalls` must cover its worst reachable path, including one profile
repair, core-problem/assumption planning, all allowed preflight checks and candidate refactors, all
branch slots, audits, allowed refill/escalation, synthesis, both optional
`frontier_reasoning_verify` and `diversity_probe` activations, conditional proof/review, and
revision. If the dispatch budget is smaller, narrow the declared scope before start or
exit unresolved; do not skip coverage or review after spending the budget.

The READY experiment contract separately freezes `maxTrials`, `maxCompute`, `maxWallTime`,
`maxExperimentModelCalls`, repeats, crash retries, plateau escapes, storage, and external-service
spend. `resource_gate` returns READY only when the authorized inventory can finish the fixed minimal
valid test within those caps. A smaller partial allocation is NOT_READY; a running experiment cannot
borrow undeclared budget.

## Concurrency contract

For falsification-search branches, full research branches, and any admitted experimental matrix,
compute:

```text
effectiveConcurrency = min(
  researchProfile.maxConcurrentBranches,
  adapterAvailableSlots,
  resourceOrServiceLimit
)
```

Reserve a slot for the main coordinator/reducer when it shares the same pool. If that reservation
leaves no valid execution slot, the experimental resource verdict is NOT_READY. Falsification
searches may fan out by source class, but ranked preflight checks and experiment trials remain
serial by default for decision and causal attribution. Use concurrency greater than one only for
independent repeats or predeclared matrix cells with isolated writes. Adapters may lower the frozen
limit but may not raise it.

## Fan-out/fan-in contract

- Attack-search branches are independent because each owns one assumption/source class; full
  branches each own a distinct claim dimension/source class. Both write separate artifact paths
  keyed by `runId/branchId/attempt`.
- Cap concurrency at the validated profile value and any stricter service limit.
- `validate_reduce` rejects missing required fields, duplicate branch IDs, and unknown claim types;
  canonicalizes URLs; deduplicates by canonical URL plus normalized claim key; and preserves
  contradictions rather than averaging them.
- Missing branch output is a gap. Duplicate output does not earn extra coverage.
- Only the validated ledger and artifact pointers reach `synthesize_knowledge`.

## Side effects and recovery

| Side effect | Stable key / reconciliation | Ambiguous-success handling |
|---|---|---|
| Core-problem/candidate version | `runId/coreVersion/candidateVersion` + digest | Reject invariant drift; candidate refactors append versions and affected-assumption links |
| Branch artifact write | `runId/branchId/attempt` + content hash | Reuse matching hash; preserve conflicting attempt separately |
| Preflight check ledger | `runId/candidateVersion/checkId/attempt` | Append raw evidence/outcome; never reorder after results or delete refutations |
| Frozen experiment contract | `runId/contractVersion/segmentId` + digest | Incompatible contract changes bump version; harness changes bump segment and rerun baseline |
| Idea board/trial ledger | `runId/contractVersion/trialId/attempt` | Append new status/evidence; never delete or overwrite negative outcomes |
| Experiment job launch | persisted pending trial key + runner job ID | Reconcile job/output first; attach if running, classify if complete, relaunch only when launch provably did not occur |
| Candidate workspace | candidate hash + isolated workspace ref | Roll back code when declared; retain patch, logs, metrics, and outcome artifacts |
| Final local artifact | `runId/artifactVersion` + hash | Write versioned artifact, then read back and hash |
| Linear/Notion publication | `runId/destinationId/artifactVersion` delivery key | Read destination before retry; update/reuse existing object; record remote ID and read-back |
| Comment/link/issue close | delivery key + operation kind | Query current state; perform only missing authorized operations in order |
| Source-layer memory | `runId/skill/outcome` | Deduplicate before append; never write MAIN directly from a worker |

Advance a stage only after its evidence is durable. Persist `pendingTrial` before launching compute.
On resume, reconcile the job ID, output paths, contract/segment digest, and candidate hash. Attach
to a running job, classify a completed job exactly once, or relaunch only when launch provably did
not occur and retry/budget caps allow it. Ambiguous success stops unresolved rather than duplicating
compute. Code candidates may roll back; raw logs, patches, negative evidence, and board history may
not. On input/schema incompatibility, start a new version or segment instead of reinterpreting old
evidence. External publication remains optional: if unavailable, preserve the accepted local
artifact and exit `UNRESOLVED_DELIVERY` without rerunning research or experiments.

## Review-policy routes

- `independent`: omit in-graph completion review; after the workflow result, the top dispatches one
  bounded `$codex-verify` reviewer using the configured actual-writer-opposite advisor over the final
  artifact and fresh acceptance evidence, then inspects the verdict. This external review occurs
  strictly after `graph_return`; it is not a node inside `finalize_success` or
  `finalize_checkpoint`.
- `machine-gated`: include `adversarial_review`, declare `completionProof: state-bound`, and cover
  every internal reviewer outcome such as `PASS | APPROVE | REVISE | UNRESOLVED | INVALID` with no
  success fallback. Every writing path reaches the reviewer after its final write and before
  delivery/success; missing, malformed, or stale output fails closed. The controller may bind a
  final internal `PASS`/`APPROVE` plus successful graph state to terminal `COMPLETE`. Internal
  `REVISE | UNRESOLVED | INVALID` never becomes `HUMAN_BLOCKED`; if the graph stop lacks a
  terminal-classifying receipt, the top session dispatches an external terminal reviewer after
  `graph_return`.

Both policies review only the post-reconciliation `finalArtifactRef`. They never
approve or deliver `knowledgeArtifactRef` and then publish a second experimental report. The review
brief includes the core-problem/candidate versions, assumption ranking, preflight contracts and
decision, applicability/resource verdicts, hypothesis-to-claim mapping, experiment contract, trial
evidence or the explicit unexecuted plan, and the requested target terminal.

`COMPLETE` means every scoped acceptance criterion is satisfied by current evidence. `REVISE`
continues or redispatches the work. `HUMAN_BLOCKED` is valid only when the reviewer names one
permission, decision, credential, or external action that only a human can provide and why safe
agent-side alternatives are exhausted. Test failure, difficulty, reviewer disagreement, model/tool
failure, time, or budget does not qualify. A session invoked solely as this terminal reviewer emits
the verdict itself and does not recursively invoke another terminal reviewer.

Broad, unfamiliar, proof-bearing, or load-bearing research may justify a cross-provider coverage
review even when it is not the PM completion gate. The top authorizes the route, a bounded PM owner
records it before execution, and the top inspects the resulting state. Keep its verdict separate so
policy proof is not inferred from an ordinary quality check.

`frontier_reasoning_advise` is narrower than `adversarial_review`: it challenges one
writer-originated reasoning object and returns advice. It does not issue the completion verdict. `diversity_probe`
tests a named family-correlated blind spot and is omitted when the only rationale is variety.

## Specialization checklist

- [ ] Workflow admission and demotion condition are explicit.
- [ ] A versioned core-problem contract fixes the user outcome, decision, success criteria, hard
      constraints, non-goals, and authority separately from the mutable candidate.
- [ ] Material ambiguity or any proposed invariant change reaches a candidate
      `NEEDS_USER_DECISION` checkpoint outside the graph; only a fresh typed terminal
      `HUMAN_BLOCKED` authorizes the exact configured human request, while `REVISE` resumes delegated work, and
      method/target refactors cannot drift the user goal or acceptance metric.
- [ ] The critical-assumption validator excludes only evidence-resolved/non-material items, covers
      every remaining blocker and load-bearing candidate-invalidating item, and orders a higher-
      uncertainty comparable-impact item before lower-uncertainty work; insufficient budget stops
      rather than shrinking the kill set, and check validity precedes cost.
- [ ] Attack search seeks negative/boundary evidence before broad confirmatory collection, and
      validated preflight evidence is reused rather than repeated.
- [ ] Preflight checks run one at a time in ranked order with frozen falsifiers, validity gates, and
      only the minimum fair tuning envelope; the gate runs after every outcome.
- [ ] Broad research and full Auto Research are unreachable until preflight `CONTINUE`; refuted,
      unready, inconclusive, invalid, unsafe, and user-decision routes stop honestly.
- [ ] Candidate refactoring is versioned and bounded, retests only affected assumptions, preserves
      negative evidence, and cannot weaken a falsifier after seeing the result.
- [ ] `researchProfile` and one global `reasoningProfile` are model-selected and deterministically validated.
- [ ] Every concrete model node has one semantic role fixed before start; exact model ids and raw
      provider effort labels remain outside workflow state.
- [ ] `maxAutomaticEscalations` is zero and `highestAutomaticRole` equals the selected posture's
      default role; no running node mutates its role.
- [ ] Every same-design stronger-role path is a distinct statically expanded node reached by a
      validated deterministic `NEEDS_ESCALATION` edge; otherwise the current run checkpoints
      unresolved and the top dispatches a bounded designer/mapper, then inspects the newly
      validated design and mapping before a new run.
- [ ] Quick, standard, and broad paths fit cumulative caps.
- [ ] Source counts remain planning targets; claim coverage is the success test.
- [ ] Branches are independent, isolated, and deterministically reduced.
- [ ] Contradictions and counterevidence survive fan-in.
- [ ] Refill, width escalation, profile repair, and review loops have counters and hard exits.
- [ ] Reframing occurs before widening on unexplained difficulty or plateau.
- [ ] Applicability is validated before hypothesis derivation; non-experimental questions do not
      receive fabricated interventions or ablations.
- [ ] Applicability uses only `APPLICABLE | NOT_APPLICABLE | INVALID`; claim dispositions use only
      `EXPERIMENTAL | OBSERVATIONAL`, cover every claim exactly once, and deterministically
      produce APPLICABLE for mixed artifacts with any experimental material claim.
- [ ] Every hypothesis points to source claim IDs and declares mechanism, prediction, intervention,
      control, metrics, falsifier, confounders, required resources, risk, and priority.
- [ ] Hypotheses cover only experimental dispositions; observational claims retain falsifiability
      and update boundaries and cannot inherit experimental-validation language.
- [ ] Missing `resources.md`, missing authority, or insufficient partial resources is NOT_READY;
      no node guesses READY or provisions resources.
- [ ] APPLICABLE plus missing `resources.md` reaches `SUCCESS_WITH_EXPERIMENT_PLAN`; INVALID alone
      reaches the experimental unresolved route.
- [ ] NOT_READY runs no experimental work and produces every required main/ablation plan section
      with an explicit not-executed/not-validated label.
- [ ] READY freezes contract and segment, runs a valid baseline, persists pending jobs, and bounds
      trials, repeats, crashes, plateau escapes, compute, wall time, and paid service use.
- [ ] Trials are atomic and serial by default; only independent repeats/matrix cells parallelize,
      effective concurrency is capped by all three capacity sources, and coordinator/reducer
      capacity is reserved.
- [ ] Unexplained metric movement cannot update conclusions; code rollback cannot delete negative
      experiment records; harness changes bump the segment and rerun baseline.
- [ ] Experimental coverage resolves every material hypothesis or exits unresolved; refutation is
      reconciled as a successful research outcome rather than treated as workflow failure.
- [ ] Cleanup, selected review, and delivery consume one reconciled artifact only.
- [ ] Every terminal is reachable and malformed output fails closed.
- [ ] Proof obligations, delivery ambiguity, and budget expiry cannot reach success.
- [ ] External effects are authorized, keyed, reconciled, and read back.
- [ ] Review policy is `independent` or `machine-gated`; every final writing path reaches one fresh
      reviewer whose upstream provider is opposite the actual writer, without coordinator
      self-approval or reviewer recursion.
- [ ] Top-intelligence work uses `frontier_execute` preferentially or the qualified
      `frontier_execute_anthropic` alternative; advice stays scoped; the actual-writer-opposite
      configured Max review pairing is fresh-catalog enforced with no silent substitution, while
      Fable 5.1/Astra remain strong conditional candidates;
      GLM/Kimi/Grok highest-effort Cursor diversity probes have a named blind-spot hypothesis;
      future DeepSeek is qualification-gated and Composer 2.5 is excluded.
- [ ] No harness syntax, model ID, or implicit human node entered the canonical design.
