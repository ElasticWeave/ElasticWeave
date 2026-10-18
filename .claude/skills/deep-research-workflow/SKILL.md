---
name: deep-research-workflow
description: "Run falsification-first research for broad evidence gaps or a real plateau; cover history, primary sources, and reward-hacking audit. Use generate-insight for one causal hypothesis."
---
> **Model tier:** HIGH — framing, coverage judgment, and synthesis require strong reasoning; collection and validation use cheaper roles or deterministic tools.
>
> **Memory abstraction:** Fix the user's core problem, attack the most uncertain assumptions that could invalidate the most downstream work, and continue only after a valid fail-fast gate; then reach claim coverage and run applicable experiments only behind a deterministic READY resource gate.

# deep-research-workflow

Turn a question or idea into an auditable decision. Separate the invariant user outcome from the
mutable candidate, seek decisive negative evidence before broad confirmatory work, and keep facts,
inferences, and extrapolations distinct. Preserve unresolved gaps instead of smoothing them away.

Load references progressively:

- Read `references/source-strategy.md` when planning sources, tools, or evidence records.
- Read `references/falsification-gate.md` before broad research whenever a decision, idea, method,
  technical target, proxy, or other load-bearing assumption could be invalidated.
- Read `references/delivery-workflow.md` only when publishing to Linear, Notion, or another
  external destination.
- Read `references/reference-design.md` when the task passes workflow admission and needs typed
  fan-out/fan-in, bounded refills, recovery, or machine-routed review. It is a reference, not an
  executable mapping.
- Read `references/model-routing.md` before assigning models or reasoning effort to research or
  experiment nodes. It defines the global posture, intelligence classes, semantic roles, and
  calibration boundary.
- Read `references/experimental-validation.md` after the falsification gate permits continuation
  and knowledge coverage passes when conclusions admit empirical tests, the user requests
  Auto Research or ablations, or a validation plan is part of the deliverable.
- Read `references/humanize-native-reference.md` when authoring, launching, or testing the bundled
  trusted Humanize2 native Deep Research or Auto Research reference flow. These Python references
  do not replace the platform-neutral design and must not be represented as narrow-adapter
  mappings.
- Read `../pm-dispatch/references/iteration-modes.md` before planning an iterative research round.
  It defines the canonical mode, human check-in lease, fallback, retained gates, checkpoint, and
  evaluation fields inherited by this skill.

Also inherit `../writing-plans/references/invocation-envelope.md`. A root research assignment owns
its lifecycle only when its `invocationId` equals `terminalOwner`. A **child evidence scan** is
`invocationMode=child`, normally `authority=read_only`: honor the caller's claim/source scope and
active stack, return a scoped evidence receipt, and must not emit a terminal verdict or write memory.
All native research-agent CLI turns use
`../default-mode/references/native-cli-execution.md`: 3600 seconds per turn and no more than four
transient retries with the fixed 5m/10m/30m/1h backoff. Research evidence, quota, auth,
invalid-model, and malformed-output failures are not retryable.
If `rethink-workflow-design` is already active, report graph/search-policy concerns to that caller;
do not re-enter it.

## Iteration mode contract

Resolve the issue-bound `iteration_mode` before proposing work. A missing legacy mode is
`autonomous`; `Fast` is only a UI alias for `human_guided` and is never persisted. Keep this
choice separate from research width, reasoning posture, model-call budget, `debug`, `ui_mode`, and
terminal `review_policy`.

A PM-managed native Deep/Auto launch must pass
`--pm-issue .pm/issues/<milestone>/<issue>.md`. The launcher no-follow reads the issue, validates
its snapshot digest and creation-time consistency, derives the four iteration config fields, and
records the issue path/raw-byte digest/snapshot digest in its sealed launch receipt. Those fields
cannot also appear in `--config`. An older issue with no snapshot receives a marked conservative
autonomous default. A launch without `--pm-issue` is explicitly standalone and must not claim PM
provenance.

- `autonomous`: propose, run bounded idea/counterevidence review, execute an experiment or proof,
  reduce evidence deterministically, then plan the next round.
- `human_guided`: propose, pass the minimum alignment/authority/safety/resource/measurement/
  falsifier gate, execute, reduce, emit the typed human-observation checkpoint, and return. Do not
  plan the next round until feedback or an explicit autonomous-fallback record is bound.

Record `idea_review_disposition` as `performed`, `skipped_human_guided`, or
`triggered_by_risk`. Human-guided mode restores targeted idea review for an ambiguous proxy or
falsifier, contradictory or unexpectedly strong evidence, repeated failure or plateau,
irreversible/high-cost/safety-sensitive work, proof claims, scope change, publication/production,
or a stale human lease. It never waives experiment/proof validity, code/build/test review,
alignment, safety/authority, evidence integrity, deterministic reduction, or terminal review.

## Default mental-model ledger

In the default autonomous mode, every load-bearing candidate or trial carries a small, append-only
mental-model record. Before dispatch, state the expected outcome, observable measurement, relevant
acceptance criterion, and current strategy (`exploit`, `verify`, `substitute`, `pivot`, or `explore`).
After the worker returns, compare the raw observation with that expectation and classify the deviation
as satisfying, redundant, contradictory, inaccessible, unexpectedly strong, or otherwise unresolved.
Keep raw evidence and its lineage separate from the agent's interpretation; do not overwrite an old
belief to make a result look consistent.

A repeated mismatch, contradictory evidence, weak or manipulable proxy, or reward-hacking/jailbreak
signal requires a bounded structural review of the candidate graph or a clean stop. It may not be
handled by retrying the same strategy with a new prompt. Structural changes are new workflow designs
and must pass the normal validator; the ledger is evidence for that decision, not permission to mutate
topology at runtime. Do not mark an unresolved node complete merely because a score improved. This is
the repository's lightweight application of [VeriTrace](https://arxiv.org/abs/2605.26081), not a claim
that a prompt-level check is a security proof.

If this ledger exposes one observed anomaly and one plausible causal mechanism, prefer a child
`$generate-insight` owner over widening the research profile. Freeze the exact observation and
evidence lineage, ask for one discriminating prediction and experiment, and consume its scoped
`SOLIDATED` or `FAILED` record as research evidence. In `human_guided`, a nonterminal `RETHINK`
record returns to the external checkpoint before another experiment. It does not perform literature coverage,
replace the falsification gate, authorize cross-task promotion, or emit this invocation's terminal
verdict. Its procedural states remain inside one bounded owner unless separate graph semantics are
independently admitted.

## Plateau diagnostic entry

When a caller supplies an evidence-backed plateau, inherit its pinned goal and acceptance contract;
do not repeat cold start, build a new project-management layer, or audit unused infrastructure. A
valid plateau contains task-scoped raw evidence from bounded, materially different attempts, repeated
comparable mismatch or no objective progress, and no remaining discriminating hypothesis. Difficulty,
elapsed time, or a first failure is insufficient.

Run three linked investigations before recommending another attempt:

1. reconstruct the complete available scoped session, experiment, and scheduler history into a
   strategy/outcome/assumption ledger, distinguishing repeated work from genuinely unexplored paths;
2. browse live primary papers, official project documentation and implementations, current stronger
   baselines, negative results, and counterevidence relevant to the blocking mechanism; and
3. inspect the exercised evaluator, metrics, evidence lineage, and runtime assumptions for reward
   hacking, leakage, prompt injection, proxy gaming, stale state, or harness distortion.

Start at `quick` width and widen only for a named load-bearing coverage gap. Return the likely plateau
mechanism, source-backed external options, any measurement/infra defect actually reproduced, preserved
contradictions, ranked new hypotheses, and one cheapest discriminating next experiment or proof step.
Do not rewrite infrastructure unless a reproduced defect blocks that step. In `human_guided`, return
this result as the next external checkpoint; in `autonomous`, hand it back to the scheduler for one
new bounded dispatch.

## 1. Lock the core problem before searching

Fix the user's desired outcome, decision, success criteria, hard constraints, non-goals, authority,
audience, time horizon, deliverable, and evidence standard. Separate those invariants from the
current idea, method, technical target, and proxy metrics, which may be revised. If a material
choice is missing or changing it could alter what success means, checkpoint a candidate human
dependency with the exact choice and exhausted agent-side alternatives. Outside the cold-start
launching conversation, return that checkpoint to `terminalOwner`; only the owner dispatches a
fresh terminal reviewer and may route its exact request through the controlling session or
configured UI after typed `HUMAN_BLOCKED`. `REVISE` continues or redispatches investigation. Do
not put a human node inside a headless workflow or treat an internal
ask route as permission to stop.

Validate a versioned `coreProblemContract`. A method or technical target may be refactored without
asking only when the user outcome and every hard constraint remain unchanged. Changing the outcome,
acceptance metric, authority, risk tolerance, or constraint requires a new user decision.

## 2. Attack the most uncertain load-bearing assumptions

Before broad collection, derive a `criticalAssumptionLedger` for the current candidate. Include
problem-value, mechanism, feasibility, measurement, scaling, safety/authority, and dependency
assumptions. For each, record uncertainty, evidence, falsifier, downstream work invalidated if
false, and the minimum valid check.

Remove assumptions already resolved by valid evidence. Within the remaining blocker/load-bearing
set, rank uncertainty together with downstream invalidation and discriminating power; a more
uncertain assumption comes first when its invalidation impact is comparable or greater. Use cost
only to select the smallest valid check for that assumption. Do not start with well-understood
tasks because they are easy. Setup work may precede a critical check only when it is a recorded
dependency of that check.

The kill set must cover every unresolved blocker and every unresolved load-bearing assumption whose
failure would invalidate the candidate or substantial downstream work. If the current profile
cannot validate that set, widen within the authorized envelope or stop unresolved; do not omit a
hard assumption to make the budget look feasible.

## 3. Select adaptive width

Have a planning model emit a structured `researchProfile` plus a global `reasoningProfile`; then
validate both schemas and requested budgets deterministically against the user, dispatch, current
role file, and profile caps. `reasoningProfile` selects `economy | balanced | frontier`, assigns an
intelligence class and semantic role to each model-node class, names escalation triggers, and caps
the strongest automatic role. Never let prose silently override a cap or write an exact model id.
The three minimum width profiles are:

| Width | Use | Default planning breadth |
|---|---|---|
| `quick` | Establish background, terminology, and the current baseline quickly | 1 bounded research owner; about 3–6 strong sources |
| `standard` | Support a decision or technical comparison across the main claim dimensions | 3 branches; about 10–18 strong sources |
| `broad` | Search opportunities, frontier mechanisms, limitations, and counterevidence | 6 branches; about 20–36 strong sources |

Counts plan effort; they do not prove completion. Success depends on coverage of material claims,
source quality, temporal validity, and explicit treatment of contradictions. The full profile
schema and cumulative caps are in `references/reference-design.md`.

Choose the smallest delegated execution lane after validating the profile. `quick` normally uses
one bounded native subagent. `standard` may also use one bounded research owner unless its evidence
slices are truly independent. `broad` usually admits a workflow because it needs fan-out/fan-in and
bounded widening, but width alone is not an admission reason; the graph must still provide useful
state, concurrency, recovery, or machine routing. A top session only inspects, dispatches, and
reviews evidence; it never performs the research or edits the deliverable itself.

Treat width and intelligence as separate controls. Wider research normally adds distinct cheap
collection branches before it raises reasoning strength. Use the cheapest live-qualified
`research_collect` or `research_collect_anthropic` role for scoped search and extraction,
`research_analyze`/`task` for interpretation-heavy branches, and `plan` for width, coverage,
synthesis, hypothesis, or root-cause judgments. Reserve `frontier_execute` (preferred) or
`frontier_execute_anthropic` for an admitted top-intelligence task. Strong scoped advice candidates
are Fable 5.1 (`claude-fable-5-1`) through `frontier_advice_fable` and GPT-6 Astra
(`gpt-6-astra`) through `frontier_advice_astra`; use either only after fresh live-catalog route,
effort, retention-policy, and reviewed project-policy admission. Select every adviser from
the upstream provider opposite the actual primary writer. An unavailable or unverifiable selection
is unresolved and is never silently substituted. Upgrade only the failing judgment node. The
workflow scheduler stays on `smol`
and never chooses or changes node roles.

## 4. Run the falsification gate

Run a bounded search for failed replications, null results, impossibility or lower-bound evidence,
boundary failures, invalid proxies, stronger baselines, leakage, scaling limits, and resource or
authority blockers. Then execute the ranked kill set one check at a time. Use a predeclared minimum
fair tuning envelope when needed to distinguish method failure from a bad default, but do not run
full optimization before viability is established.

In `human_guided`, the ordinary low-risk path compresses this phase to the minimum validity gate;
it does not run general novelty or interestingness debate. The complete falsification gate remains
mandatory for any risk trigger listed in the iteration-mode contract. `autonomous` retains the
full bounded gate by default. Both paths freeze a hypothesis, observable falsifier,
measurement/comparator, reversibility, authority, safety, and resource evidence before execution.

Continue only on `CONTINUE`. A valid refutation stops the current idea or triggers one bounded
in-scope method/technical-target refactor and a new candidate version. A change to the core problem
routes to the user. An inconclusive, invalid, unsafe, or resource-blocked critical check stops at
`UNRESOLVED_PREFLIGHT_VALIDATION`; it is not permission to fill time with easier research. Read
`references/falsification-gate.md` for schemas, ranking, routes, and terminal evidence.

## 5. Build the evidence ledger

After the falsification gate permits continuation, run the `bitlesson-selector` agent before
dispatched research subtasks and bind only its selected lessons to the branch contract. Give each
branch a distinct question or source class. Reuse validated preflight evidence. Record every usable
item with a claim ID, `Fact | Inference | Extrapolation` type, canonical link, source class, date,
supporting location, uncertainty, and contradiction key.

Prefer primary and official evidence. Browse live for drift-prone facts. Use lower-quality sources
as discovery or explicitly labeled context, not as sole support for a load-bearing claim. Preserve
conflicting records separately.

## 6. Reduce and audit coverage

Use deterministic tools to reject malformed records, canonicalize links, deduplicate, merge branch
artifacts, and compute a claim-by-source matrix. Do not ask a model to perform a merge that can be
specified mechanically.

Then audit the actual question:

- Are all decision-relevant claim dimensions supported?
- Do high-impact or controversial claims have evidence proportional to risk?
- Are contrary findings and boundary cases represented?
- Are facts, inferences, and extrapolations worded according to their evidence?
- Do unresolved contradictions change the recommendation?

A named, local gap may receive a targeted refill. Do not restart all branches.

## 7. Reframe before widening

Escalate `quick → standard → broad` when a high-impact gap persists, sources materially conflict,
frontier or opportunity coverage is thin, coverage plateaus, or repeated iteration difficulty
remains unexplained. Before widening, apply `$first-principles` to restate the question, challenge
assumptions, and identify the discriminating evidence. Reuse validated evidence and add only the
new branches needed by the wider profile.

At `broad`, a remaining material gap, unexplained result, or budget exhaustion ends as
`unresolved_with_gaps`; it is not permission to lower the evidence standard. If traces instead show
that the workflow's search policy or graph is the failing object, use `$rethink-workflow-design`.
Do not use workflow redesign as a substitute for domain research.

Model escalation does not replace width escalation. If the missing object is a source class,
counterexample, frontier mechanism, or unexplored opportunity, add coverage. Use
`frontier_execute` for an admitted load-bearing top-intelligence judgment, with
`frontier_execute_anthropic` as the qualified alternative and `slow` only as the permitted Sol
X-High compatibility path. Use `frontier_advice` for one normal scoped alternative proposal or
result challenge; Fable 5.1 (`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`) are stronger
candidates for hard or materially uncertain advice only when the chosen exact route is freshly
admitted. If it is unavailable, report the advice unresolved rather than substituting. Use `diversity_glm`,
`diversity_kimi`, or `diversity_grok` only at a live-verified highest-effort Cursor route to test a named
correlated blind spot; do not ensemble by default. A required Cursor diversity route makes the
Humanize adapter ineligible and routes to the built-in scheduler without substitution.

## 8. Gate full experimental validation

Enter this phase only after the uncertainty-first preflight gate permits continuation. After
knowledge coverage passes, first write an auditable knowledge synthesis. Before deriving
hypotheses, classify experimental applicability as `APPLICABLE | NOT_APPLICABLE | INVALID` and
validate the structured result. Do not invent an intervention or ablation for market, historical,
regulatory, or other conclusions that cannot reasonably be tested experimentally.

For a mixed artifact, emit exactly one disposition for every claim:
`EXPERIMENTAL | OBSERVATIONAL`, with its reason and either an observational boundary or hypothesis
candidate reference. If any material claim is `EXPERIMENTAL`, the overall verdict is `APPLICABLE`;
only an all-observational set is `NOT_APPLICABLE`. Values such as `partial_applicability` or
`protocol_only` are invalid. Derive hypotheses only for experimental claims; observational claims
retain their falsifiability/update boundaries and never inherit experimental validation.

- `NOT_APPLICABLE`: record claim-level falsifiability, future observational checks, update triggers,
  and the limits of causal inference; then reconcile and continue through the knowledge-research
  `SUCCESS` terminal. State that no controlled experiment ran. Do not create an experiment plan
  merely to satisfy the branch.
- `INVALID`: execute nothing and preserve the malformed or unsafe applicability evidence for an
  `UNRESOLVED_EXPERIMENTAL_VALIDATION` result.
- `APPLICABLE`: derive a structured `hypothesisLedger` from `EXPERIMENTAL` claims, with source
  claim IDs, mechanism, prediction, intervention, control, metrics, falsifier, confounders,
  required resources, risk, and priority; only then inspect resources.

Inspect the declared resource pipeline deterministically. Return only `READY | NOT_READY |
INVALID`. `READY` requires an existing `resources.md` plus explicit, available, sufficient, and
authorized compute, data, harness, budget, correctness gates, and isolation for a fixed minimal
experiment. Do not infer readiness from ambient hardware or credentials. Do not request or consume
new permission or external resources automatically.

- `READY`: freeze/version the experiment contract, establish a baseline, and run bounded atomic
  trials with an append-only idea/experiment board. Select the most uncertain load-bearing
  unresolved hypothesis before easier confirmatory or tuning work. Root-cause every result before
  updating the conclusion. Reach `SUCCESS_EXPERIMENTALLY_CHECKED` only when every material
  experimental hypothesis is supported or refuted by valid evidence; otherwise exit
  `UNRESOLVED_EXPERIMENTAL_VALIDATION` with the durable checkpoint.
- `NOT_READY`: execute no experiment. Produce a complete main-experiment and ablation plan, marked
  **not executed and not experimentally validated**, with resources, statistics, gates, stopping
  rules, and outcome-to-decision mapping. For any `APPLICABLE` artifact, including a mixed one,
  missing `resources.md` reaches `SUCCESS_WITH_EXPERIMENT_PLAN`, never an unresolved terminal by
  itself.
- resource `INVALID`: execute nothing and preserve the malformed, unsafe, or incompatible resource/harness
  evidence for `UNRESOLVED_EXPERIMENTAL_VALIDATION`.

Read `references/experimental-validation.md` for the schemas, loop, recovery, and coverage rules.
Keep trial execution and bookkeeping deterministic/tool-driven. `plan` selects hypotheses and
atomic trials and root-causes evidence; `frontier_execute` or the qualified
`frontier_execute_anthropic` alternative is reserved for a named plateau or load-bearing mechanism
failure. `frontier_advice` or the qualified `frontier_advice_fable` independently challenges one
specific proposal or result interpretation; the active `frontier_advice_astra` provides the
corresponding Astra route. Each selected identity requires fresh live-catalog resolution and remains
unresolved if unavailable. Neither becomes the trial selector, general experiment author, or
completion reviewer.

## 9. Reconcile, review, and deliver

Reconcile experimental outcomes or the unexecuted validation plan into one new report. A supported
hypothesis may strengthen a conclusion; a refuted one must revise it; an inconclusive result must
narrow it. Refutation is a valid research outcome, not workflow failure. Never deliver the earlier
knowledge-only draft and then publish a second report.

Write the answer first, then the evidence that changes the decision, implications, contradictions,
unknowns, sources, and experimental status. Label forecasts explicitly. Apply `$proof-rigor` only
to actual proofs or load-bearing derivations, and keep any unproved obligation visible. Apply
`$writeup-human-cleanup` to the human-facing artifact without removing citations or caveats.

Use the coordinator-selected review policy only when this invocation is `terminalOwner`; `none` and
coordinator self-approval are retired:

- `independent`: after the final write and focused acceptance checks, the top dispatches one
  bounded `$codex-verify` reviewer whose configured upstream provider is opposite the actual writer,
  then inspects its verdict. The reviewer receives the scoped acceptance contract, current
  artifact/diff, and fresh evidence.
- `machine-gated`: resolve the actual final writer family, then route every writing path after its
  final write to `cross-review-openai-writer` for an OpenAI-family writer or
  `cross-review-anthropic-writer` for an Anthropic-family writer. Bind the internal
  `PASS`/`APPROVE` or revision outcome to the reviewed state, fail closed on omitted/mismatched
  writer identity or missing/malformed/stale output, and let the controller emit the terminal
  `COMPLETE | HUMAN_BLOCKED | REVISE` classification.

The completion reviewer returns exactly one terminal verdict: `COMPLETE`, `HUMAN_BLOCKED`, or
`REVISE`. `COMPLETE` requires current evidence for every scoped acceptance criterion. `REVISE`
continues or redispatches the work. `HUMAN_BLOCKED` is valid only for a named permission, decision,
credential, or external action that only a human can provide after agent-side alternatives are
exhausted; test failure, difficulty, disagreement, tool failure, time, or budget is insufficient.
A session whose sole assignment is this terminal review returns its verdict directly and does not
recursively commission another reviewer.

A child research invocation stops after returning the requested evidence/provenance, resource-use
receipts, remaining budget, gaps, and learning candidates. Domain proof or cleanup checks are child
receipts; they do not create a second completion verdict.

The reviewer role follows the actual live-resolved writer execution. The shipped exact terminal
pairing remains OpenAI Astra writer to Anthropic Opus `advisor`, and Anthropic Opus writer to OpenAI
Astra `codex_advisor`. Fable 5.1 (`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`) are active
strong scoped review/advice routes after fresh live-catalog admission. If the selected identity is unavailable or provider
separation cannot be proven, report the review unresolved and never substitute. Advice roles are
not completion-review authority. A diversity probe is evidence for a named
hypothesis, not a completion verdict unless a specialized design explicitly gives it that contract.

External delivery is optional and must be authorized, idempotent, and reconciled before retry.
Publish a durable canonical artifact before closing a Linear issue; use Notion for durable workspace
knowledge when requested. Keep non-success experimental checkpoints local unless the user explicitly
authorized publishing unresolved results; publication never changes an unresolved terminal into
success. See `references/delivery-workflow.md`.

## 10. Close with evidence and learning

Return the core problem contract, candidate versions, ranked critical assumptions, falsification
checks and gate decision, research profile, claim coverage summary, strongest sources,
contradictions, open gaps, resource verdict, hypothesis coverage, experimental status, avoided
downstream work, budget used, artifact location, and delivery status. When this invocation is the
outer `terminalOwner`, schedule one bounded `$self-improve`/`$memory` closing beat before sealing
final evidence: have the top dispatch a bounded closing owner, then review the final state. A child
returns `learningCandidates` instead. Promote generalized behavior only through the evidence-backed
lemma and review contracts.

For every round also return the selected/effective iteration mode, lease status and fallback,
`idea_review_disposition`, risk triggers, and the post-reduction checkpoint: original hypothesis,
actual operation, raw observation, agent interpretation, anomaly or alternate interpretation,
two to four candidate next tasks, questions for the human, and bound human feedback if available.
In `human_guided` the checkpoint is a graph-external return boundary, not a completion verdict or
an in-graph human node. A later native launch accepts feedback only together with the prior
reviewed terminal artifact path and exact raw-byte SHA-256; the sealed config binds all three.
The launcher additionally emits a self-digested `humanize-human-feedback-receipt/v1` carrying the
prior checkpoint ID and seals it with the PM/standalone iteration provenance in the outer receipt.
The prior terminal's task digest and iteration provenance must equal the new launch exactly, and
the launcher revalidates the prior artifact before and after the Humanize process boundary.

## Non-negotiable rules

- Do not equate a source count with coverage or quality.
- Do not prioritize certainty, ease, or cheap visible progress over a more uncertain assumption
  whose failure would invalidate more downstream work.
- Do not continue broad research or full Auto Research after a blocker is refuted or remains
  untestable; stop the candidate, refactor within the fixed contract, or checkpoint a candidate
  human dependency for `terminalOwner`. Ask only after that owner receives fresh typed
  `HUMAN_BLOCKED`.
- Do not change the user's outcome, success criteria, authority, or hard constraints to rescue an
  idea. Only the method, technical target, and proxy are autonomously mutable.
- Do not call a cheap or underpowered check fail-fast evidence; it must meet the predeclared
  validity contract and minimum fair tuning envelope.
- Do not state an inference or extrapolation as a sourced fact.
- Do not hide contrary evidence, stale dates, missing primary support, or proof obligations.
- Do not run an experiment with absent, partial, unauthorized, or insufficient declared resources.
- Do not treat an unexplained improvement or regression as support or refutation.
- Do not exceed model-call, concurrency, refill, wall-time, or external-service caps.
- Do not assign the coordinator's model to every branch, compare provider effort labels as if they
  were a common scale, or silently escalate a subagent outside the global reasoning profile.
- Do not publish, comment, close, or overwrite externally unless the requested destination and
  replay behavior are clear.
- Do not add a harness mapping merely because this reference workflow is graph-shaped; author a
  canonical design with `$build-workflow` and map it with `$run-workflow` only when execution is
  actually requested and admitted.
- Do not put concrete model IDs in a native research flow. Resolve its semantic roles through the
  current project role policy at launch, enforce its read-only/actual-writer-opposite contract before
  the first turn, and route an unsupported advice or diversity backend to the built-in scheduler
  instead of silently substituting one inside Humanize. The reusable guidance may name Fable 5.1
  (`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`) as strong candidates; the flow itself still
  stores semantic roles only.
