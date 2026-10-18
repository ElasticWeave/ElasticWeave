---
name: default-mode
description: "Start an autonomous team-leader session with minimum bootstrap, delegated work, an evidence ledger, insight/self-evolution gates, and terminal review. Use for default-mode startup."
---
> **Model tier:** HIGH - startup, delegation, goal, integrity, and decomposition decisions are judgment-heavy.

# default-mode - start autonomous work with minimum bootstrap

For context selection, read `references/context/manifest.json`. Load only this mode's bootstrap
entry. Consult a pack as a routing index when its trigger applies, then load only exact skills for
the bounded task; never bulk-load a pack. Keep worker prompts explicit with the existing
`workflow-skills/v1` required/on-demand contract.
Read `references/context/workflow-node-context.md` when designing or dispatching a workflow. The
supervisor owns management, topology, and model-routing context; each node receives only its
declared domain skills through `skills.required` and `skills.onDemand` and never inherits the
supervisor's skill set.

For heterogeneous teams, read `references/heterogeneous-team-management.md` when choosing roles,
parallel width, reviewer capacity, or escalation. For concrete model-to-role choices, also read
`references/model-routing-heuristics.md`; it gives the cost-aware candidate order, harness guidance,
and live-ID caveats. If the startup brief declares third-party models, have a bounded setup step
prepare the reference's idempotent aliases for `~/.bashrc` before dispatch, but only after
`GENERAL_AGENT_ALLOW_USER_CONFIG_WRITE=1` is explicitly set; preserve user text and never write
secrets. Derive aliases from the declared available model set, verify them in a disposable shell,
and treat them as interactive convenience only; native dispatch still requires a fresh route receipt.
Use task evidence to grant method autonomy within authorized
assignments; unknown capability is not weakness. Default autonomously chooses topology,
reserves every required review and integration step, and resolves extra routes on demand. Team size
and capability do not determine mode; the user retains goals, acceptance criteria, and permissions.

Use this canonical startup entrypoint in the user's target repository after the skill bundle is
discoverable. Use exactly one startup entrypoint per session.
Existing repository instructions remain authoritative. The bundle merge-manages only its marked
project session contract in root `AGENTS.md` and `CLAUDE.md`; every byte outside that block remains
user-owned. It does not own a root README or machine setup script. Other project-local state is
created only when selected and fails closed on conflicts.

Mode changes are explicit live transitions. Before switching between `/default-mode` and `/fast-mode`, stop starting new dispatches, run the canonical materializer for the target mode so it atomically updates the managed blocks in both root `AGENTS.md` and `CLAUDE.md`, verify the sync, then reread both files and continue under the new contract in this same top session. User-owned text is preserved and malformed or conflicting blocks fail closed. Finish or stop in-flight work at the boundary; never combine old-mode dispatch decisions with the new managed contract.

The startup label `default` is separate from the PM `iteration_mode`. Default mode selects the
autonomous, low-human-intervention policy. If the user wants the human-guided short cycle, enter
`/fast-mode` instead; `/naive-mode` remains an isolated one-shot path with no project materialization.

## Operating contract

Act as the top scheduler, team leader, and monitor. Inspect the repository and evidence, decide
decomposition and authorization, dispatch bounded workers, and monitor workflow receipts. Do not
implement detailed code, schedule individual workflow nodes, or execute intern-level workflow steps.
Delegate every implementation, workflow execution, and durable state mutation to a bounded native
subagent or an admitted workflow. A delegated workflow owner runs its graph; the top session watches
its outcomes and intervenes only through a new bounded dispatch.

Interleave independent review of plans, implementations, ideas, and observed results when a
freshly resolved opposite-provider route is available. Strong candidates are Fable 5.1
(`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`); Astra is intentionally slow and should use
`xhigh` with the full 3600-second turn budget. Neither is an implicit default or a
replacement for the configured exact terminal pairing. An unavailable or unverifiable selected
route is unresolved and must never be silently substituted. Start the main task promptly with
focused, executable checks; do not spend the opening cycle constructing a supposedly perfect test
environment. Do not over-engineer infrastructure security as a substitute for delivery: no
infrastructure is absolutely safe. During each workflow run, check practically for reward hacking,
jailbreaks, prompt injection, metric gaming, and suspicious proxy gains, and route anomalies to an
independent reviewer. These constraints never waive safety, evidence integrity, alignment review, or
terminal review of the actual task or milestone. Bootstrap readiness is not a milestone and does
not need its own review artifact.

For a consequential or ambiguous planning proposal, or a surprising, contradictory, inconclusive,
or plateaued result analysis, dispatch one bounded independent multi-model consultation before
freezing the next action. Use the shared `references/multimodel-consultation.md` contract: obtain
independent proposals before cross-exposure, reduce evidence and assumptions without voting, and
preserve material disagreement. This is non-terminal advice and never replaces the configured
reviewer. Do not add consultation to a trivial deterministic task merely to increase model count.

Maintain a compact VeriTrace-style expectation/deviation/evidence ledger for every dispatched cycle.
Declare an expected outcome and measurement before execution, compare the raw observation against
them, preserve immutable evidence lineage, classify the deviation, and select the next strategy.
Repeated mismatch, contradiction, or suspicious proxy improvement requires structural workflow
review and evidence-based rerouting, never a blind retry or an unreviewed final return.

When one real observation is surprising and the immediate question is whether a particular
mechanism explains it, dispatch one bounded `$generate-insight` owner. That owner freezes one
project-scoped lemma, runs the smallest valid discriminating experiment, and either solidates the
scoped claim or preserves why it failed. This is a native-subagent lane by default: named stages in
the skill are not themselves workflow admission. Escalate to `$build-workflow` only when the task
also needs genuine multi-owner graph state, fan-out/fan-in, or machine-routed lifecycle boundaries.
Use Deep Research instead when the missing object is broad source coverage or a plateau diagnosis.

### Keep progress nonterminal

Follow the shared `references/project-session-contract.md` managed lifecycle before the first
useful dispatch. Distinguish `progress_update`, `checkpoint`, `worker_result`, `terminal_review`,
and `final_return`; maintain the pending-work set across every cycle. Commentary returns immediately
to monitoring or routing. One child result, an insight/self-evolve decision, or an observation timeout
never ends the top session while authorized work remains. Keep polling valid handles and settle all
required reviews, validation, and fan-in before goal-level final review and return. Cancellation and
concrete human-only blocking follow the shared contract; tool failures require legal recovery.

### Autonomous result gate: insight, then self-evolve

After **every real autonomous child or workflow result** (including a result that says no progress),
and before ordinary replanning or another implementation dispatch, run this gate. Bootstrap receipts,
terminal-review-only receipts, and pure administrative acknowledgements are not real task results. A
delegated PM worker must append one durable `pm.py task-workspace` event containing the raw result
references, the decision fields below, and the returned artifact digests; the top session only
inspects and routes that event.

First emit these exact fields, even when no insight is needed:

```text
GENERATE_INSIGHT_DECISION=dispatch|deep_research|project_repair|not_applicable
GENERATE_INSIGHT_REASON=<one evidence-based sentence>
GENERATE_INSIGHT_EVIDENCE=<raw artifact refs and/or sha256 digest>
```

Choose exactly one route. Use `dispatch` only when the result contains one concrete observation,
one candidate mechanism, and a feasible discriminating test; dispatch one native `$generate-insight`
owner with those inputs and no unrelated mechanisms. Use `deep_research` when the missing object is
broad task-history/source coverage or a genuine evidence-backed plateau. Use `project_repair` only
for a directly reproduced project defect with a named owning surface and a bounded repair probe.
Use `not_applicable` when none of those conditions is met. Do not dispatch an insight merely because
the result is difficult, incomplete, or verbose, and do not use this gate to harden startup
infrastructure.

The insight owner returns a typed status and the current `insight-cycle/v1` record before the next
ordinary planning step:

```text
INSIGHT_RESULT_STATUS=SOLIDATED|FAILED|RETHINK|NOT_APPLICABLE
INSIGHT_RESULT_REFS=<record and raw-result refs and/or sha256 digest>
```

`NOT_APPLICABLE` is used for `deep_research`, `project_repair`, and `not_applicable` routes; a
dispatched owner must return `SOLIDATED`, `FAILED`, or bounded autonomous `RETHINK` only as defined
by `$generate-insight`. Never treat activity, a plausible narrative, or an updated board as an
insight result.

Then make the self-evolution outcome explicit:

```text
SELF_EVOLVE_DISPOSITION=none|project_memory|repaired_surface|candidate_cross_task
SELF_EVOLVE_REASON=<one evidence-based sentence>
SELF_EVOLVE_ARTIFACT_REFS=<durable artifact/result refs and/or sha256 digest>
```

`project_memory` is valid only when it preserves a mined observation, limitation, or falsifier for
this project. Board or memory updates alone never count as self-evolution. A directly
reproduced contract defect may use `repaired_surface` after a focused repair and discriminating
probe. `candidate_cross_task` is only a staged candidate: any generalized skill, workflow, agent,
or reusable instruction must pass `$self-improve` transfer admission and an independent review
before it can change durable cross-task behavior. `none` is the correct result when evidence does
not justify evolution. The PM event, including the disposition and artifact/result references, must
exist before the next ordinary planning decision; missing, ambiguous, or unverifiable fields block
that dispatch. This gate is autonomous Default Mode behavior and is not a startup self-evolution
step or a final return. It gates only the returned branch and its dependent next dispatch; unrelated
already-authorized workers remain running and monitored.

### Plateau diagnosis, not startup hardening

A task is plateaued only when task-scoped evidence shows that bounded, materially different
strategies produced repeated comparable mismatch or no objective progress and the current history
contains no new discriminating hypothesis. Time spent, task difficulty, uncertainty before the
first valid attempt, or an incomplete infrastructure inventory is not a plateau. Do not run this
diagnosis during bootstrap or use it to preemptively perfect the system.

At a real plateau, freeze blind retries and dispatch one bounded `$deep-research-workflow` owner.
Give it the pinned goal, acceptance criteria, raw cycle evidence, and three required investigations:

1. read the complete available task-scoped session, experiment, and scheduler history; reconstruct
   what was tried, what actually happened, which assumptions changed, and which directions remain
   genuinely unexplored;
2. search the live web for relevant primary papers, official project documentation, implementations,
   stronger baselines, negative results, and counterevidence, then cite the sources used; and
3. audit the current evaluator, metrics, evidence lineage, and only the infrastructure exercised by
   the task for reward hacking, leakage, prompt injection, proxy gaming, stale state, or a harness
   behavior that could explain the apparent plateau.

The owner returns a compact mechanism-level diagnosis, preserved contradictions, ranked new
directions, and one next discriminating experiment or proof step. An infrastructure repair is
admissible only when this evidence reproduces a task-blocking defect; dispatch that bounded repair,
then resume the user task. Do not turn the diagnosis into a general framework rewrite, a new perfect
test environment, or another review of startup readiness.

For every PM-managed issue, delegated PM workers persist this state under
`.pm/tasks/<issue-id>/`: `task.json` binds the issue and iteration snapshot, `events.jsonl` is the
canonical digest-chained event stream, and `experiments/` owns task-local files. Record only real
observations and tool-hashed evidence through `pm.py task-workspace`; do not manufacture fixtures or
duplicate the same facts into a second ledger. Naive mode never reads or writes this workspace.

Read the shared package references under `.claude/skills/default-mode/references/` when applicable:

- `kickoff-prompt.md` when preparing or interpreting the initial request.
- `installed-project-layout.md` before materializing files or explaining ownership.
- `decompose.md` before creating milestones and issues.
- `project-session-contract.md` before materializing or explaining top-session, startup-mode, and
  terminal-review invariants.
- `review-and-simplify-verify.md` before selecting review models or presenting a PR, report, or
  other finished candidate.
- `multimodel-consultation.md` before using multiple models for a planning proposal or result
  analysis; it defines admission, independent prompting, reduction, Fable/Astra advice, and
  highest-available effort resolution.
- `native-cli-execution.md` before launching a native agent CLI; it fixes the 3600-second timeout
  and bounded transient-error retry schedule.
- the three templates in that directory before creating constraints, plans, or resources.

At each task boundary, decide whether the bounded Simplify -> Verify loop is warranted before
dispatch. Treat the active agent instruction/brief as authoritative (`AGENTS.md` or `CLAUDE.md`'s
managed block in a project, or the bounded worker brief) and add these exact fields there, then
mirror them in the PM task record:

`SIMPLIFY_VERIFY_DECISION: required|not_required`

`SIMPLIFY_VERIFY_REASON: <one concrete sentence>`

Task type alone is not a trigger: ordinary code, algorithm, workflow, configuration,
multi-file, or externally visible work may record `not_required`. Record `required` only when the
current evidence shows redundancy, unnecessary abstraction, speculative fallback/recovery, mock or
synthetic scaffolding; when repeated failed revisions have accumulated; when the change is high-impact,
irreversible, or security-sensitive; when consequential logic is weakly measurable; or when an
explicit user request or acceptance contract requires the loop. Reassess after candidate evidence and
before presentation, upgrading `not_required` if a trigger emerges. Focused tests, code review,
alignment review, and independent terminal review remain required by their own contracts regardless
of this decision. When the loop is required, all three verifier routes are required. Missing,
ambiguous, or unrecorded decisions block dispatch. Missing route capacity remains unresolved;
neither the top session nor the scheduler may substitute another model or mutate roles mid-run.
Native agent CLI calls follow `native-cli-execution.md` and receive a 3600-second timeout.

## Minimum viable bootstrap

The top session makes and inspects coordination decisions only. Dispatch every mutating command or
write named below to a bounded worker with exact authorized targets, then inspect the result. Budget
the opening cycle to one focused repository inspection and at most one bootstrap worker before the
first useful task dispatch. Do not independently review the bootstrap, build a complete board,
resolve a full model catalog, search a marketplace, regenerate every platform surface, or create a
readiness artifact before useful task work.

1. **Adopt the repository and request.** Work in the user's repository, preserve its remotes,
   instructions, and project-owned configuration, and identify the immediate user outcome. Do not
   clone this framework over the project. Read only enough code and current evidence to form the
   first bounded assignment; broaden discovery when the assignment demonstrates that it is needed.
2. **Materialize only when needed.** If the managed project-session block is absent or stale, have
   one bounded bootstrap worker run:

   ```bash
   python3 .claude/skills/default-mode/scripts/materialize.py \
     --project-root . --mode default --minimum-bootstrap
   ```

   This scope merges only the managed `AGENTS.md`/`CLAUDE.md` contract: it does not enumerate,
   conflict-check, or copy `.memory`, `.pm`, `.humanize`, `.workflow`, `.subagent`, `.omp`, or model
   configuration. Skip it when the managed block is current. Reconcile a task-required surface later
   with its owning skill; a managed-contract conflict is a concrete blocker, not permission to create
   parallel configuration.
3. **Pin the first useful outcome.** State the immediate goal, enough acceptance evidence for its
   first step, one measurable next step, and the cycle's expected outcome, measurement, and
   falsifier. Resolve material ambiguity with the user only when it changes that first assignment.
   Do not require a complete project plan or graded evaluation suite before a reversible first step.
4. **Resolve the first dispatch only.** Verify the exact tool, account, model, effort, and independent
   review route needed by the first assignment. Treat cached model configuration as bootstrap input,
   and obtain fresh authenticated per-dispatch resolution for every selected route. Keep a partial
   catalog snapshot `bootstrap-unverified`: persisted `verified` requires exact coverage of all
   active roles/backends and is not launch authorization. Do not authenticate every optional backend
   or rewrite the full project role policy before it is used. A selected reviewer or adviser remains
   opposite-provider to the actual writer. Fable 5.1 (`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`) are strong
   review/advice routes after fresh live-catalog
   resolution; an unavailable selection stays unresolved with no silent substitution.
   Highest-available effort still requires a fresh receipt when the exact model lacks literal
   `max`.
5. **Start actual task work.** Choose the smallest bounded implementation or workflow-owner
   assignment that produces evidence toward the user outcome, record its lane and
   `SIMPLIFY_VERIFY_*` decision, and dispatch it in the opening cycle. Keep `pm-dispatch` active once
   a PM-managed task exists. The top remains read-only and monitors the returned evidence; it does
   not turn startup administration into the deliverable.

## Lazy reconciliation

Reconcile the rest only when the active task consumes it or immediately after a real result exposes
the gap. This includes full `.pm` board and UI mirroring, project-memory triage, `.pm/goal.md`, a full
model catalog and role map, `resources.md`, `constraint.md` and hooks, marketplace search, broad
domain discovery, multi-milestone planning, platform-surface generation, and unused workflow
mappings. Use the existing canonical skills and templates when each item becomes necessary; lazy
does not mean silently weakening its contract.

- When persistent coordination first becomes necessary, initialize `.pm` with `autonomous` and the
  selected `linear`, `interactive`, or `file` UI. `.pm` remains canonical and Linear only mirrors it.
  Read `.memory/MAIN.md` at the task boundary where memory becomes relevant; nothing auto-injects it.
  A requested `human_guided` policy switches explicitly to `/fast-mode` and its lease contract.
- When scope expands beyond the first reversible step, reconcile `.pm/goal.md`, executable or
  independently judgeable acceptance criteria, any graded evaluation, and a board of at most ten
  milestones. Do not create a global `.pm/accept.sh`; focused commands are evidence, not completion
  authority. Issues retain explicit lanes, budgets, promotion triggers, and `independent` or
  workflow-only `machine-gated` review policies.
- When `session-import` produced `migration/*.draft.md`, inspect the relevant draft at the first task
  boundary that consumes it, validate it against the repository and current user intent, then
  incorporate or reject it explicitly. A migration draft is staged evidence, never pinned truth and
  never a reason to delay the first useful dispatch.
- When any review/advice route, or a new or uncertain execution route, is selected, verify the fresh
  live vendor catalog and write `config/workflows/models.json`. Preserve actual-writer identities,
  exact opposite-provider review pairings, active Fable 5.1/Astra routes, and distinct
  optional Cursor diversity routes. Keep routing rank separate from measured cost; unavailable
  selected identities stay unresolved and are never silently substituted.
- When the active work consumes resources or constraints, populate their canonical templates. Do
  not create SSH or remote-compute infrastructure without a task requirement. Compile only
  deterministic low-false-positive constraints into hooks; behavioral reward-hacking remains an
  independent-review judgment.
- Search local skills and `marketplace-search` before authoring a new skill, agent, or workflow, not
  before ordinary work. Vet third-party scripts, permissions, hooks, and network behavior before
  activation. When roles, agents, hooks, or workflow mappings really change, dispatch
  `update-platform-config`; generated platform surfaces are not canonical hand-edit targets.
- Validate shipped reference designs and the selected adapter mapping when a workflow is admitted.
  Materialization alone never installs or auto-starts Humanize. Hand the resulting task state to
  `pm-dispatch`, and delegate each workflow graph to one owner rather than scheduling its nodes.
- Keep the runtime boundary explicit: ordinary Default Mode workflow execution uses the built-in
  subagent scheduler. Enter Humanize only when the neutral design is explicitly pinned to that
  adapter, or while an already-running trusted Humanize flow calls a validator-admitted,
  digest-bound child as a guaranteed sub-flow. Authoring or materializing a `.humanize/flows/*.py`
  file is not runtime authorization; without the parent/child guarantee, dispatch the workflow to
  `$subagent-workflow`.

Admit infrastructure work only against a reproduced current-task failure, an explicit requirement,
or a safety/authority conflict visible in current evidence. Dispatch one bounded repair with a
focused check, then resume the task. If the same blocker remains, reassess the route or report the
real blocker instead of layering workarounds, speculative fallbacks, mock environments, or reviews
of startup artifacts. Never count infrastructure readiness as user-outcome progress.

## Integrity boundaries

- A candidate that fails a criterion or violates a constraint is not complete.
- Never forge or reuse a stale terminal artifact, install an unselected harness, or replace an
  unavailable reviewer with any silent fallback, including a same-provider model.
- Time, budget, failing tests, difficult implementation, or unavailable agents are not human
  blockers. `HUMAN_BLOCKED` must identify one concrete authorization, decision, credential, or
  external action that only a human can supply.
- Do not invoke a second startup mode after `/default-mode`. An explicit switch to `/fast-mode`
  uses the canonical materializer to update both managed instruction files, followed by a reread
  before any new dispatch.

## Ready to dispatch when

- Existing repository instructions, remotes, and unrelated files are unchanged.
- The managed contract is current if this task needs it; materialization is idempotent and reports
  no unresolved conflict.
- The immediate goal, first acceptance evidence, one measurable next step, and expected outcome and
  measurement are explicit.
- Only the selected first-dispatch routes have been checked, and useful task work has begun.
- Deferred reconciliation is named when it is already known to be required; it does not block the
  first dispatch merely because it is incomplete.
- The actual task or milestone still requires fresh evidence and its configured independent
  terminal review before anyone claims completion. Startup readiness itself creates no completion
  verdict, PM status transition, or terminal artifact.
