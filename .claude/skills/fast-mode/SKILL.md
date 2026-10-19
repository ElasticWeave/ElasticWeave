---
name: fast-mode
description: "Start human-guided short cycles with minimum bootstrap, bounded experiment/reduction, a human checkpoint, and terminal review. Use for fast-mode startup."
---
> **Model tier:** HIGH for mode and risk decisions; bounded workers retain their configured roles.

# fast-mode - human-guided short cycle

For context selection, read `../default-mode/references/context/manifest.json`. Load only this mode's
bootstrap entry. Consult a pack as a routing index when its trigger applies, then load only exact
skills for the bounded task; never bulk-load a pack. Keep worker prompts explicit with the existing
`workflow-skills/v1` required/on-demand contract.
Read `../default-mode/references/context/workflow-node-context.md` when dispatching a workflow.
Fast mode keeps human direction at the supervisor layer; workflow nodes receive only their declared
domain skills and do not inherit management or model-routing context.

When assigning heterogeneous members, read
`../default-mode/references/heterogeneous-team-management.md`. For concrete model-to-role choices, also read `../default-mode/references/model-routing-heuristics.md`; it gives the cost-aware candidate order,
harness guidance, and live-ID caveats. If the startup brief declares third-party models, have a
bounded setup step prepare the reference's idempotent aliases for `~/.bashrc` before dispatch, but
only after `GENERAL_AGENT_ALLOW_USER_CONFIG_WRITE=1` is explicitly set; derive them from the declared
available model set, preserve user text, and never write secrets. Aliases are interactive convenience;
native dispatch still requires a fresh route receipt. Fast keeps the human's direction and
plan while members choose methods within their assignments. Use task evidence, explicit support
contracts, and a small real assignment when capability is unknown; reserve all required reviews and
integration. Choose team size and parallel width for the bounded cycle's delivery burden. Capability
does not select the mode or transfer the user's goals, acceptance criteria, or permissions.

Use this as a startup entrypoint when the human is actively available. `fast` is an operating-mode
prompt, not a new persisted PM enum and not a `review_policy`. The canonical PM policy is
`human_guided`, with a finite check-in lease and an explicit expiry fallback (`pause` or
`autonomous_review`). Do not silently continue after lease expiry.

Mode changes are explicit live transitions. Before switching between `/default-mode` and `/fast-mode`, stop starting new dispatches, run the canonical materializer for the target mode so it atomically updates the managed blocks in both root `AGENTS.md` and `CLAUDE.md`, verify the sync, then reread both files and continue under the new contract in this same top session. User-owned text is preserved and malformed or conflicting blocks fail closed. Finish or stop in-flight work at the boundary; never combine old-mode dispatch decisions with the new managed contract.

## Operating contract

The top Codex session remains a team leader and monitor. Delegate implementation and workflow
execution to bounded subagents; the top session does not perform the workflow's intern-level steps.
Use the shortest valid loop:

`propose potential subtask -> experiment or prove -> deterministic reduction -> human observation -> replan`

Routine low-risk idea-merit debate may be skipped to reduce latency. Never skip authority, safety,
measurement/falsifier validity, experiment/proof correctness, code review, alignment review, evidence
integrity, contradiction handling, reward-hacking checks, or the universal terminal review. Record the
raw observation, interpretation, alternate interpretation, and candidate next tasks at each checkpoint.
Use a freshly resolved opposite-provider reviewer when available, especially when a result is
surprising or the human checkpoint exposes a mismatch. Strong candidates are Fable 5.1
(`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`); Astra is intentionally slow and should use
`xhigh` with the full 3600-second turn budget. Neither replaces the configured exact
terminal pairing without reviewed policy activation. If the selected exact route is unavailable or
unverifiable, report the review unresolved; never silently substitute another model, provider,
backend, or effort.

When the next proposal has two materially plausible directions, or a result is ambiguous enough to
make the next experiment unreliable, use one bounded consultation round from
`../default-mode/references/multimodel-consultation.md`. Generate the primary and advice outputs
independently, reduce them once, and return the disagreements in the human checkpoint. Skip this
extra call when the human can directly resolve a low-risk choice or the result is already decisive;
Fast mode must not recreate autonomous broad idea review.

If one observed result needs a causal explanation before the next human decision, dispatch one
bounded `$generate-insight` native owner. Give it the raw evidence and one candidate mechanism; it
runs at most one new verifying experiment and returns a `SOLIDATED`, `FAILED`, or nonterminal
`RETHINK` record. Do not turn the skill's internal stage names into a workflow graph, and do not let
it become broad literature collection. Return its reduction at the human checkpoint before
choosing another experiment. A separate neutral workflow
is warranted only when independent graph semantics such as multiple owners, fan-out/fan-in, or
machine-routed lifecycle control are actually required.

An apparent plateau is a human checkpoint before it is an infrastructure project. Show the human
the task-scoped attempt/result history, the unchanged objective, proxy divergence, contradictions,
and the remaining candidate directions. If bound human feedback supplies a new falsifiable direction,
run it directly. If the human requests deeper diagnosis, feedback still leaves no discriminating
direction, or an expired lease explicitly falls back to autonomous review, dispatch one bounded
`$deep-research-workflow` plateau diagnosis. It must reconstruct the complete available task history,
search current primary papers and official projects, and audit exercised metrics/evidence/infra for
reward hacking or harness distortion. Start with the smallest useful research width, return its
compact result at the next checkpoint, and do not use it to delay the opening cycle or perfect
infrastructure preemptively.

Use the same canonical `.pm/tasks/<issue-id>/task.json`, `events.jsonl`, and `experiments/`
workspace as Default mode. The shorter cadence changes which review is useful, not where evidence
lives or whether it is real. Delegated PM workers append the expectation, observation, deviation,
simplify, and verify events; the top session only inspects them.

Read
`.claude/skills/default-mode/references/review-and-simplify-verify.md` before selecting review
routes or presenting a PR, report, paper section, or equivalent candidate. Fast mode may shorten
the simplify planning pass and omit broad idea debate. At each task boundary, decide whether the
loop is required and why before dispatch. Treat the active agent instruction/brief as authoritative
(`AGENTS.md` or `CLAUDE.md`'s managed block, or the bounded worker brief) and add these exact
fields there, then mirror them in the PM task record:

`SIMPLIFY_VERIFY_DECISION: required|not_required`

`SIMPLIFY_VERIFY_REASON: <one concrete sentence>`

Task type alone is not a trigger: ordinary code, algorithm, workflow, configuration, multi-file, or
externally visible work may use `not_required`. Use `required` only for observed redundancy,
unnecessary abstraction, speculative fallback/recovery, mock or synthetic scaffolding, repeated
failed revisions, a high-impact/irreversible/security-sensitive change, weakly measurable
consequential logic, or an explicit user or acceptance
requirement. Otherwise record `not_required`, then reassess after candidate evidence and before
presentation. Focused tests, code review, alignment review, and independent terminal review remain
in force either way. When required, capture actual candidate, change, and acceptance evidence, run
all three verifier lanes, reduce them deterministically, and obtain the outer independent terminal
review. Simplify -> Verify does not require Grok; Grok remains an optional general-diversity route.
An unavailable explicitly selected route or a missing/ambiguous decision is unresolved, not a
reason to substitute a model. Native agent CLI calls follow
`../default-mode/references/native-cli-execution.md` and receive a 3600-second timeout. Roles are
fixed by a current validation of the selected mapping before their first use and cannot be changed
by the scheduler during the run.

## Checkpoints are nonterminal

Follow the managed lifecycle in `../default-mode/references/project-session-contract.md`.
`progress_update` and `checkpoint` are native observations, `worker_result` is a scoped handoff,
`terminal_review` judges its named scope, and `final_return` ends the top turn. Present the checkpoint
in commentary or the authorized human-input route, then resume monitoring: it pauses only the next
human-dependent planning cycle. Already-authorized workers, workflows, reviews, evidence capture,
and required fan-in continue until settled. A result from one worker never abandons its siblings.
Observation timeout means wait again on the valid handle or perform legal recovery, never `COMPLETE`.
If only a genuine human decision remains, obtain the existing independent `HUMAN_BLOCKED` verdict;
checkpoint cadence or lease expiry alone is not that verdict. Return finally only under the shared
goal-level completion, reviewed human-block, or explicit user cancellation/replacement rules.

## Minimum viable startup

Use one focused repository inspection and at most one bootstrap worker before the first useful
experiment dispatch. Do not review the bootstrap itself, construct a complete board, validate every
model route, or reconcile optional project infrastructure before starting. Preserve Default mode's
top-session delegation, safety, evidence, and actual-task terminal-review boundaries without running
Default mode as a second entrypoint.

1. **Adopt and materialize only as needed.** Preserve the repository's remote, existing instructions,
   and project-owned configuration. When the managed block is missing or stale, dispatch one bounded
   bootstrap worker to run:

   ```bash
   python3 .claude/skills/default-mode/scripts/materialize.py \
     --project-root . --mode fast --minimum-bootstrap
   ```
   This scope touches only the managed `AGENTS.md`/`CLAUDE.md` contract and does not enumerate,
   conflict-check, or copy optional project surfaces. Skip it when that block is current. Conflicts
   fail closed; do not create a parallel workaround tree.
2. **Bind the human-guided checkpoint.** Reuse a valid current policy or have the same bounded worker
   initialize or renew canonical `human_guided` with a `5..1440` minute lease and explicit
   `pause|autonomous_review` fallback. Configure only the UI path needed for this checkpoint. Never
   write PM, UI, or memory state from the top session.
3. **Pin the first experiment.** Record the immediate user outcome, minimum validity condition, one
   measurable next step, expected outcome, measurement, and falsifier. Resolve only ambiguity that
   changes this experiment; do not design the whole project before a reversible first result.
4. **Resolve the first dispatch only.** Verify the exact worker, tool, account, model/effort, and
   opposite-provider review route needed by this experiment. Obtain fresh authenticated per-dispatch
   resolution for every selected route; resolve reviewers/advisers against the actual writer.
   Keep a partially covered snapshot `bootstrap-unverified`; persisted `verified` requires all
   active roles/backends and does not authorize launch. Defer unused routes; do not silently
   substitute an unavailable required route.
5. **Run the short cycle.** Dispatch the experiment or proof in the opening cycle, reduce its real
   evidence, and return the bound human checkpoint before selecting the next task. A checkpoint is
   not a completion verdict or `HUMAN_BLOCKED`; continue monitoring pending authorized work.
   The actual task or milestone still receives its configured independent terminal review.

## Lazy reconciliation

After useful work is underway, reconcile `.pm` board detail, project memory, full goal/evaluation,
resources and constraints, marketplace discovery, the full model catalog, platform surfaces, and
unused workflow mappings only when the active task consumes them. Use the canonical Default-mode
references and supporting skills at that point; do not weaken their safety or evidence contracts.
In particular, initialize only the PM/UI state the live checkpoint needs, read memory at a relevant
task boundary, query a fresh live catalog for every selected reviewer/adviser and any newly selected
execution route, populate resources or constraints when consumed, search the marketplace before
authoring a capability, and run
`update-platform-config` only after an actual platform-owned source changes.

Admit infrastructure work only for a reproduced current-task failure, an explicit requirement, or a
current safety/authority conflict. Dispatch one bounded repair and focused check, then resume the
experiment. If it still fails, reassess the route or report the concrete blocker instead of adding
speculative fallbacks, synthetic mock environments, or repeated setup review.
