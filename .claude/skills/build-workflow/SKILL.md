---
name: build-workflow
description: "Design or revise an admitted platform-neutral workflow graph; use for fan-out/fan-in, typed state, lifecycle, replay-safe effects, or verdict routing. Execution belongs to subagent-workflow."
---
> **Model tier:** HIGH — flow design (a strong model writes the graph cheap executors run)
>
> **Memory abstraction:** Design the deterministic spine before choosing a harness: typed state, explicit terminals, replay-safe side effects, evidence-bearing routes, and for optimization a persistent idea board that separates coverage from score.

# build-workflow — design the workflow before mapping it

Turn an admitted graph-shaped task into a canonical `.workflow/designs/<name>.workflow.json` whose
semantics do not depend on any current or future harness. The design owns intent, state,
routes, budgets, evidence, and terminal meanings. A platform skill owns syntax, installation, and
execution.

## Admission before authoring

Confirm that at least one workflow reason from `run-workflow` is true. A normal implement→review
pair is usually a native-subagent task, not a graph. Once admitted, try these bases in order:

1. an existing `.workflow/designs/*.workflow.json`
2. an existing project design or reusable reference pattern
3. `marketplace-search` for an adaptable base
4. a new design only when the first three do not fit

Record the reason the graph is needed and the fact that would demote it to a native subagent. Do
not preserve a workflow after its graph semantics disappear.

A named multi-step discipline is not automatically a workflow. In particular,
`$generate-insight`'s experiment-to-lemma-to-verify-to-rethink-to-solidate/fail states can be owned
by one bounded native subagent. Author a neutral design only when the concrete invocation also
needs independent model leaves exchanging state, fan-out/fan-in, machine-routed decisions, durable
cross-owner recovery, or another visible admission reason. The fact that the skill records typed
state is not enough.

## Required inputs

The coordinator supplies:

- the task goal and explicit issue/design acceptance criteria;
- any deterministic acceptance evidence to collect after the final write, preferably as an argv
  invocation with expected exit/output semantics rather than an implicit shell gate;
- the workspace and any required write isolation;
- relevant constraints from `constraint.md`;
- the dispatch contract: admission reasons, review policy, estimated and maximum model calls, and
  demotion condition;
- success, unresolved/escalation, and hard-failure workflow semantics, plus who supplies the
  session's terminal reviewer;
- representative acceptance cases when output quality is graded rather than binary;
- for long-running flows, the resume and compatibility expectation after a graph or prompt change.
- for iterative work, the effective `autonomous` or `human_guided` policy snapshot, live human
  check-in lease when applicable, and unavailable-human fallback;
- any required harness capabilities, and whether a specific harness is mandated or may be selected
  after design.

Before drawing nodes, fill the compact worksheet in
`templates/workflow-design-contract.md`. Keep it beside the flow only when the project benefits
from a durable design record; otherwise use it as a private authoring checklist.

## Procedure

### 1. Declare the design and dispatch contract

```json
{
  "schema": "workflow-design/v1",
  "id": "example-flow",
  "entry": "seed",
  "admission": {
    "reasons": ["multi-stage", "stateful-loop"],
    "demotionCondition": "..."
  },
  "governance": {
    "reviewPolicy": "independent",
    "completionProof": "none",
    "malformedDecision": "unresolved"
  },
  "budgets": {"maxModelCalls": 10},
  "requiredCapabilities": ["conditional-routing", "observability"]
}
```

Valid reasons are `multi-stage`, `fanout-fanin`, `stateful-loop`, `lifecycle-boundary`, `batch`,
`machine-gated-review`, `protected-control-plane`, `long-running`, and `engine-validation`. Claim
only reasons visible in the graph. `machine-gated` also requires `maxReviewCycles: 1..3`.

`reviewPolicy` names where the mandatory terminal review runs; it is not permission to skip
review. The supported values are `independent` (the top session dispatches a fresh external
reviewer after the workflow returns) and `machine-gated` (a final in-graph reviewer controls the
success route). Legacy `none` designs must migrate to `independent`; the validator reports this
explicitly instead of silently changing their semantics.

Use only capability names exported by `scripts/validate_design.py`. The validator derives every
graph-visible requirement: model/decision nodes require invocation and structured output; `fresh`
sessions require fresh-session support; conditional edges and decisions require routing;
reducers, multi-branch non-decision fan-out, and any nonempty `waitFor` require fan-out/fan-in;
`state-mediated` sessions require stateful continuation; declared node skills require skill
loading; graph cycles require bounded-loop support; and governance derives machine-gated review and
state-bound proof. Do not omit a derived requirement or
claim a structure-bound requirement the graph does not contain. Capabilities whose need comes from
an external lifecycle promise, such as durable recovery, remain explicit authoring decisions.

### 2. Design state and terminal semantics first

Define the minimal `state` list before nodes or prompts.

- Store typed, raw facts and artifact paths, not prose duplicated for each consumer.
- Give every field one meaning and, where feasible, one writer.
- Separate decision state from evidence state: a verdict never substitutes for the evidence that
  produced it.
- Include numeric counters for every cycle and `forced` or `forcedReason` for cap exits.
- Identify the successful terminal, the unresolved/escalation terminal, and any fail-fast abort.
  Every reachable route must end in one of them; a malformed or missing verdict must never advance.
- Keep graph outcomes distinct from session terminal artifacts. A graph `success` under
  `independent` means "candidate ready for terminal review," not permission for the top session to
  stop. Before stopping, the configured terminal reviewer emits exactly one of
  `TERMINAL: COMPLETE`, `TERMINAL: REVISE`, or `TERMINAL: HUMAN_BLOCKED`, bound to the evidence it
  reviewed. `HUMAN_BLOCKED` is reserved for a request only a human can satisfy: authorization,
  decision, credential, or external action. It also records `HUMAN-REQUEST-KIND` and a concrete
  `HUMAN-REQUEST`; ordinary test failures, budget exhaustion, missing model capacity, or unfinished
  agent work are `REVISE`, not human-blocked.
- For long-running flows whose definitions can change, add an input/schema version or an explicit
  compatibility rule. Define whether recovery needs a durable checkpoint, a new bounded attempt,
  or only side-effect reconciliation; the harness adapter must prove it can preserve that contract.

### 3. Choose the smallest graph pattern

Use one dominant pattern and add complexity only when the task needs it:

- **sequence** for real data dependencies;
- **fan-out/fan-in** only for independent branches with an explicit deterministic reducer;
- **router/handoff** when state determines the next specialist;
- **bounded feedback loop** when external evidence can improve a candidate;
- **checkpoint/escalation** when the run must stop without claiming success;
- **batch** when the same contract applies across independently isolated items.

Prefer a script node over a model node for parsing, counting, validation, selection, aggregation,
budgeting, and state updates. Parallelize only branches that share no hidden ordering dependency.

A consequential planning proposal or ambiguous result analysis may justify one bounded
consultation fan-out: a primary planning/analysis branch and one independently prompted advice
branch read the same frozen constraints and evidence, then one reducer preserves disagreements and
records the selected rationale. Keep the branches independent until fan-in, cap the consultation at
one round, and do not use majority voting. This non-terminal advice does not replace the workflow's
review policy. A simple local judgment without useful concurrency remains a native subagent task,
not a workflow-admission reason.

Use the closest worked reference before inventing a graph:

| Need | Reference design |
|---|---|
| Ordered implementation with mechanical evidence | `references/designs/sequence-evidence-gate.md` |
| Independent research/review branches and one reducer | `references/designs/fanout-fanin-research.md` |
| Long-horizon optimization that must avoid premature convergence | `references/designs/idea-board-optimization.md` |
| Cheap handling of easy cases, heavy handling only when warranted | `references/designs/adaptive-cost-cascade.md` |
| Many isolated items with bounded concurrency and partial failure | `references/designs/batch-isolated-items.md` |
| Protected or load-bearing writes with fail-closed review | `references/designs/machine-gated-write.md` |
| Long-running work with checkpoints and ambiguous side effects | `references/designs/long-running-recovery.md` |
| Bounded Simplify -> Verify rounds before presentation | `references/designs/simplify-verify-loop.workflow.json` |

These are design references, not copy-paste schemas. Specialize their state and routes, validate
the neutral artifact, and map it only afterward.

### 3a. Declare an iterative policy when the graph repeats

`iterationPolicy` is an optional top-level object for backward compatibility with finite,
non-iterative designs. A design that proposes, experiments, reduces, or plans another cycle must
declare it explicitly. The validator accepts only these canonical modes:

- `autonomous`: run the declared idea/counterevidence review before the experiment, then reduce
  deterministically and continue while the bounded loop permits;
- `human_guided`: run the minimum validity gate (authority, safety, alignment, measurement,
  resource, and falsifier checks), experiment, and reduce deterministically, then exit to an
  unresolved observation checkpoint. The outer owner may resume only with bound human feedback.

`Fast` or `human-guided` are presentation aliases only; never persist them in a design, state,
receipt, or mapping. Do not add provider, harness, model, or effort identifiers to this contract.

The object must point to declared state and nodes using the following neutral shape (the validator
rejects unknown keys and mode-inapplicable keys):

```json
{
  "iterationPolicy": {
    "mode": "autonomous",
    "ideaReviewDispositionState": "idea_review_disposition",
    "ideaReviewEvidenceState": "idea_review_evidence",
    "riskTriggerState": "idea_review_risk_triggers",
    "checkpointState": "observation_checkpoint",
    "resumeEvidenceState": "human_feedback",
    "reducerNode": "reduce-cycle",
    "ideaReviewNode": "idea-review",
    "riskTriggers": [
      "lease_expired", "human_unavailable", "contradiction", "ambiguity",
      "plateau_or_repeated_failure", "irreversible_or_high_cost",
      "safety_sensitive", "weak_proxy"
    ]
  }
}
```

The listed triggers are the mandatory baseline. A domain may append distinct lower-case
snake-case triggers that also force review; it may not remove or rename the baseline.

For `human_guided`, replace `ideaReviewNode` with `minimumValidityGateNode`,
`minimumValidityEvidenceState`, and `riskReviewNode`; add a bounded
`humanCheckinLeaseMinutes` (5–1440), `leaseExpiryState`, `leaseStatusState`,
`humanUnavailableFallback` (`pause` or `autonomous_review`), `fallbackRecordState`, and an
`unresolved` `checkpointTerminal`. A live lease is required at entry. Expiry or absent human
feedback must be recorded and follow the configured fallback; it must never silently continue as
human-guided.

Every iterative design declares state for `idea_review_disposition` with exactly
`performed | skipped_human_guided | triggered_by_risk`, evidence for that disposition, and a
checkpoint object containing `checkpoint_id`, `hypothesis`, `actual_operation`, `raw_observation`,
`agent_interpretation`, `anomaly_or_alternate_interpretation`, `candidate_next_tasks`, and
`human_feedback`. Resume evidence must bind the checkpoint id, feedback, and receipt time.

Resolve the effective project `iteration_mode`, lease, and fallback once at dispatch and snapshot
them into the design/run envelope. Nested workflows inherit that snapshot unchanged; a child may
neither renew the lease nor silently select another mode. It returns an expired/unavailable record
to the outer owner, which either pauses or materializes an autonomous policy according to the
snapshotted fallback. Resuming human-guided work requires feedback bound to the emitted checkpoint.

The reducer must consume validity/review evidence and write the checkpoint. No headless design may
contain a human node. A human-guided route has no model node after reduction and no success terminal
at the checkpoint; it returns control to the top/UI owner. The checkpoint is not assignment
completion or `HUMAN_BLOCKED`. Risk triggers (lease expiry, contradiction, ambiguity, plateau or
repeated failure, irreversible/high-cost or safety-sensitive action, or weak proxy) force targeted
idea review or autonomous fallback. Both modes retain safety/authority, experiment or proof
validity, code and alignment review, evidence integrity, and the fresh independent or machine-gated
terminal review.

### 4. Add an idea board for optimization workflows

Do not drive an open-ended optimization workflow with only `current_best` and `retry_count`. That
state cannot distinguish a bad direction from a promising direction that has not received enough
evidence.

Use an idea board when the run searches across approaches, prompts, algorithms, architectures, or
workflow variants:

- Separate a **macro idea** from its **micro hypotheses/trials**. Keep parent links so the system
  can branch or backtrack without losing provenance.
- Persist positive and negative outcomes. Deduplicate by conceptual mechanism, not wording.
- Use explicit states such as `BACKLOG`, `READY`, `EXPLORING`, `PROMISING`, `STANDBY`,
  `VALIDATED`, and `EXHAUSTED`. `EXPLORING` and `STANDBY` are non-terminal.
- Mark an idea `EXHAUSTED` only after a hard falsifier or its declared minimum evidence budget has
  been spent across meaningfully distinct trials. Record the rejection reason and reactivation
  condition.
- Select work using performance, uncertainty/coverage, novelty, and estimated cost. Preserve an
  explicit exploration reserve or non-zero exploration probability; a greedy incumbent-only
  selector will collapse to one neighborhood.
- Keep a bounded number of active directions. Move weak ones to `STANDBY` rather than deleting
  them, and retain failure summaries so later agents do not rediscover dead levers.
- Make each trial an atomic change where practical. This makes credit assignment, rollback, and
  recombination tractable.

Define local-stagnation evidence before the run. A plateau should route to a qualitatively different
action: backtrack to an earlier parent, activate another idea cluster, cross over compatible ideas,
or request a new community scan. Repeating the same revision prompt is not an escape strategy.

Completion of an optimization workflow requires both the quality criterion and a coverage audit.
If an eligible `EXPLORING`, `PROMISING`, or deliberately deferred high-uncertainty idea remains,
the honest outcome is checkpoint/unresolved, not convergence. See
`references/designs/idea-board-optimization.md` and use `$rethink-workflow-design` when the current
search policy itself needs redesign.

### 5. Pin every node contract

Node skills are a strict context boundary. Workflow-building, model-routing, PM coordination, and
organizational-management skills belong to the supervisor or an explicitly delegated workflow
designer. A worker node receives only the domain skills declared in its own `skills.required` and
`skills.onDemand` sets; it never inherits the top session's skill list or the sibling nodes' context.
Read `.claude/skills/default-mode/references/context/workflow-node-context.md` when authoring this
boundary. If a node needs to construct or revise a workflow, model that authority as a separate
designer node with an explicit contract and review route.

Each node gets one responsibility and a complete contract:

- `reads:` lists the state it consumes;
- `writes:` lists outputs backed by the declared state contract;
- `workspaceAccess: read|write` matches actual behavior;
- `role:` names one project semantic role, never a platform-specific model id. Read
  `config/workflows/models.json.reasoningPolicy`, choose one global posture for the issue, classify
  the node's intelligence demand, and use the lowest-cost qualified role inside that envelope;
- `session: fresh|persistent|state-mediated` states the required continuity semantics;
- `skills.required` lists skills whose complete `SKILL.md` must be loaded before the node runs;
- `skills.onDemand` lists preflighted skills whose path is exposed but whose body is loaded only
  when its trigger applies;
- `timeoutSeconds` is explicit when the default bound is not justified;
- the prompt states the goal, inputs, allowed tools/files, output shape, and what must not be done;
- the failure behavior is known: abort, route through a modeled verdict, or stop at escalation;
- any side effect is either idempotent, keyed by a stable run/item id, or preceded by a
  reconciliation check that makes a new attempt safe.

Record the role rationale, observable escalation trigger, and strongest permitted role in the
design worksheet or dispatch artifact. The executable design still contains one fixed role per
node. A scheduler selects runnable nodes only; it never chooses a model, provider, role, or effort.
If a role must change after evidence, produce and validate a new design/mapping binding or use
statically declared role-specific branches. Do not put a model id in workflow state.

Use node boundaries where they improve checkpointing, isolation, observability, retry safety, or
contract clarity. Do not split a single atomic judgment into chatty micro-nodes.

The node contract must be self-contained. Harness adapters may change packaging, not responsibility,
evidence, write scope, skill dependency, or output semantics. Use `required` only for a discipline
the node always needs; overloading it repeats long instructions in fresh sessions. Put conditional
debugging, design, or review methods in `onDemand`. The adapter must preflight both sets before a
model call, fully materialize required skills, and fail closed when a declaration cannot be resolved.
For a persistent native session, it may replace a repeated required-skill body with a short reminder
after proving that the same session retained the first load.

### 6. Make fan-out and side effects safe

For every fan-out/fan-in section:

- prove branch independence;
- isolate parallel writers;
- name the reducer and its deterministic merge rule;
- define behavior for missing, duplicate, or conflicting branch outputs;
- cap concurrency when the external service or workspace has a limit.

For any node that writes files, calls an external API, sends a message, or changes control-plane
state, record the stable idempotency/reconciliation key in the design worksheet. A timeout or
crash can occur after the side effect but before the node result is recorded; author the next
attempt for that ambiguity.

### 7. Wire routes and bounded loops

Edges are the control plane. Keep route conditions explicit and based on declared state or known
node outputs.

- Advance only on positive evidence such as `verdict == "PASS"`; never use an implicit else path
  as success.
- Every review or selector node has explicit outgoing routes for all allowed outcomes.
- Every cycle has a counter, a deterministic increment node, a hard cap, and an unconditional
  cap route to escalation/checkpoint.
- Put the cap check on the path that actually repeats. Ensure a missing or malformed result cannot
  bypass it.
- Write `forced: true` or `forcedReason` on cap exit so later analysis does not credit a forced stop
  as convergence.
- Give revision nodes the latest concrete evidence or review feedback. A loop without new evidence
  is repetition, not refinement.
- Do not interpret a per-trial budget cap as evidence that its parent idea failed. Route a capped,
  insufficiently tested idea to `STANDBY` or checkpoint with its evidence deficit recorded.

For machine-gated review, declare every allowed outcome and a fail-closed missing/malformed route.
The adapter must not invent a fallback verdict. Keep `maxReviewCycles` within the project policy.

### 8. Implement the selected review and evidence policy

- `independent`: no in-graph terminal review is required. After the workflow returns, the top
  session dispatches one fresh different-provider reviewer over the criteria, candidate, and
  evidence before it stops. The top session may inspect and route the result, but it does not
  replace that reviewer with its own completion judgment.
- `machine-gated`: every workspace-writing path passes a cross-provider final review node before
  success. Use `completionProof: state-bound` for a PM issue whose completion is proven by the
  graph. A passing, evidence-bound final review is the terminal reviewer, so do not add a duplicate
  external completion review. If an in-graph stop lacks a terminal-classifying review receipt, the
  top session still dispatches an external reviewer before stopping.

The terminal artifact binds the reviewed evidence with
`EVIDENCE-COMMITMENT: sha256:<64 lowercase hex>`. `COMPLETE` means all milestone criteria are met.
`REVISE` means more agent/workflow work is possible. `HUMAN_BLOCKED` additionally requires exactly
one `HUMAN-REQUEST-KIND: authorization|decision|credential|external-action` and a specific
`HUMAN-REQUEST: ...`. A reviewer verdict named `BLOCK` does not by itself authorize
`HUMAN_BLOCKED`.

Capture a pre-write baseline and post-write mechanical evidence when the workflow can change the
workspace. Acceptance comes from the explicit issue/design criteria; default-mode does not manufacture a
global `.pm/accept.sh` gate. Prefer deterministic argv-based checks before LLM review, and bind the
argv, exit status, output digests, and final candidate identity into the evidence. A criterion may
also be semantic, in which case give the reviewer representative cases and the observable judgment
contract. If quality is graded, run a small covering case set and preserve per-case traces or
summaries; final-output review alone cannot diagnose a bad route or wasted model calls. External
tool/test evidence is stronger feedback than unaided self-critique.

### 9. Budget quality and cost together

Count the static model-node floor, loop activations, review cycles, and output-contract retries.
Keep all possible activations within `dispatch.maxModelCalls`. Maintain separate ledgers for model
calls by semantic role, input/output tokens when exposed, wall time, and scarce external compute;
one cannot safely stand in for the others. The role file's `relativeCost` is a routing rank, not a
dollar estimate; actual money cost requires provider/relay billing evidence. Also bound:

- adapter scheduler calls when the selected mapping uses a model-assisted scheduler; these are
  real top-level model invocations and must fit the same `maxModelCalls`, not an invisible overhead;

- review/revision cycles;
- node wall time through explicit positive `timeoutSeconds` on every agent, decision, and
  workspace command node; values may exceed 600 seconds, and when the design declares a global
  wall-time cap, both bounds apply;
- when an agent or decision node launches a native CLI, leave at least 3600 seconds for that
  turn and follow `../default-mode/references/native-cli-execution.md`; shorter bounds are only
  appropriate for deterministic commands or test-only doubles;
- fan-out concurrency and batch size;
- external-service retries in the script/tool that owns them.

Budget exhaustion is an unresolved terminal, never permission to lower the acceptance bar.

Apply cost controls in this order:

1. Run deterministic validity checks before any model judgment.
2. Assign the cheapest role whose calibrated capability covers the node. Collection, extraction,
   scheduling, and mechanical review must not inherit the planning model by default.
3. Use a cheap proxy or small covering sample before the full evaluation, while preventing a noisy
   proxy from exhausting a new idea before its minimum evidence budget.
4. Pass state summaries, deltas, and artifact pointers instead of replaying complete transcripts.
5. Route easy, high-confidence cases through a smaller lane and reserve the heavy graph for cases
   whose gate is uncertain or high-risk.
6. Escalate only the failing judgment node, within the global posture. Widen evidence search before
   upgrading all leaves when a plateau reflects underexplored mechanisms or source classes.
7. After traces show a recurring deterministic tool sequence, compile it into a script or
   meta-tool and regression-test it.
8. Prune, merge, or cheapen nodes only if a baseline-anchored acceptance suite shows that quality
   remains within the declared tolerance.

Track a quality-cost Pareto set rather than a single score. Separate expensive
exploration/development from a stable deployment/exploitation path when discoveries can be reused.

### 10. Validate, inspect, and hand off to an adapter

```bash
python3 .claude/skills/build-workflow/scripts/validate_design.py \
  .workflow/designs/<name>.workflow.json
```

Before start, inspect the graph against the worksheet:

- all state reads/writes exist and output shapes are machine-checkable;
- every declared node skill exists, required and on-demand sets do not overlap, and the mapped
  prompt proves required content is present before the model call;
- all nodes are reachable and all intended terminals are reachable;
- every conditional producer covers its declared outcomes;
- every cycle has a cap exit and fits the model-call budget;
- every model node has one fixed semantic role selected under the global posture, with a recorded
  reason and escalation trigger; scheduler and state cannot mutate it;
- frontier-advisor or diversity nodes, when present, have a narrow named hypothesis and bounded
  activation count rather than a standing ensemble;
- proposal or result-analysis consultation branches are independently prompted, share frozen
  inputs, fan in once, preserve disagreement, and remain separate from terminal review;
- optimization exits distinguish underexplored from exhausted and run a coverage audit;
- fan-in has a deterministic merge rule;
- side effects are safe under a new attempt;
- acceptance and evidence occur after the final writing node;
- machine-gated writing paths cannot reach success without review.
- independent success is handed back to the top session for external terminal review before stop;
- every planned `HUMAN_BLOCKED` outcome names a human-exclusive request kind and concrete request.

Then invoke `$run-workflow`. It compares `requiredCapabilities` with `.workflow/harnesses.json` and
the validator-derived requirements, then selects a capability-compatible active adapter only when
every requirement and review policy is covered. `$subagent-workflow` remains the built-in default;
`$humanize-workflow` is eligible only for its narrow independent-review finite-DAG subset. The
mapping report must name every controller-owned, adapter-implemented, unsupported, or deliberately
changed capability and bind the canonical design digest.

The built-in scheduler is also the default runtime for an ordinary Codex session. Selecting
Humanize requires an explicit adapter choice or a trusted Humanize parent that is validator-bound
to call this flow as a sub-flow. A flow's existence or authoring request alone is not evidence of
that relationship; when it cannot be demonstrated, keep the design on the built-in scheduler.

## Non-negotiable rules

- Platform adapters get no workflow-design freedom; they preserve bounded responsibilities.
- Keep deterministic control in scripts and edges, and model judgment in model nodes.
- Do not use human nodes. Questions go through the configured UI; a headless flow stops at a
  checkpoint/escalation boundary.
- Respect `constraint.md`; nodes do not touch protected paths without the sanctioned authorization
  tier.
- Do not put model ids or harness commands in the canonical design. Adapters resolve current project
  roles and keep selected reviewers in a different native provider family from writers.
- Do not treat provider effort labels as a shared capability scale, copy the coordinator model to
  every leaf, or use diversity without a named correlated-blind-spot hypothesis.
- If the task becomes open-ended or no longer needs graph semantics, stop and re-dispatch it through
  the coordinator.
- Do not install or execute community workflow code solely because it is popular. Inspect license,
  update recency, mechanism fit, and security boundaries; transfer the mechanism, not hidden trust.
- Never silently weaken a design to fit a harness. Reject the mapping or record an explicit,
  acceptance-tested adaptation.

## References

- `templates/workflow-design-contract.md` — pre-authoring contract and final inspection checklist.
- `references/harness-contract.md` — design/adapter boundary, capability negotiation, and mapping
  report contract.
- `references/designs/` — compact graph patterns for sequence, research fan-out, idea-board
  optimization, adaptive cost routing, batches, machine-gated writes, and recovery.
