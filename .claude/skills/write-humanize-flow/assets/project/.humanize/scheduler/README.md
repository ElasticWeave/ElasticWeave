# Codex scheduler protocol

`.humanize/flows/scheduler` is a bounded outer Humanize2 directory flow whose sole entry point is
`__init__.py`. Every planning round calls the scheduler
agent directly, which creates a fresh Codex session. That session is the team-leader coordinator: it
may inspect the task, workspace, prior child outcome, and run evidence, then design or tune the next
Slot, but it must delegate implementation, research, task-level planning, experiments, reduction,
and review to that approved bounded Slot/subagent. It must not execute task work, call a configured
child role directly, schedule individual workflow nodes, or launch native Codex subagents. All
workflow execution happens only inside an approved Slot. A normally returned child is followed by
one separate fresh scheduler turn that may only inspect and type the resulting evidence; it is not
another planning round and may not perform or repair child work.

Start a direct TUI with all repository-versioned Agent defaults applied automatically by running
`./.humanize/run_scheduler.py` from the project root. For PM-managed work, run
`./.humanize/run_scheduler.py --pm-issue .pm/issues/<milestone>/<issue>.md` instead. Use
`./.humanize/run_scheduler.py --check` to print the exact pinned CLI and all eleven ordered defaults
without opening the TUI or making a model call. No `.bashrc`, global Humanize setting, or repeated
manual setup is required. Humanize's `/agents` screen remains available for deliberate changes in
that TUI session. Running `hmz -f local/scheduler` directly bypasses the automatic defaults and uses
Humanize's saved Agent choices instead.

The launcher rejects re-entry through that entry when it inherits a sealed scheduler context and
holds one nonblocking project-local owner lock until the TUI exits, so a concurrent launcher for the
same project fails immediately. Direct `hmz` bypasses these launcher guards as well as the defaults;
Humanize does not tool-confine the Scheduler. Do not invoke `.humanize/run_scheduler.py`, `hmz`, or
`hmz -f local/scheduler` from inside an active scheduler.
The launcher also sets `HUMANIZE_SENTRY=off` for this process only, suppressing telemetry and its
first-run question without changing the user's saved global preference. The guard coordinates
ordinary processes; it is not confinement against a malicious same-UID process.

The PM-managed launcher no-follow reads the issue, validates its digest-bound iteration snapshot,
and seals that context for Humanize. Every outer boundary revalidates the issue bytes and ages only
its frozen lease; later `.pm/config.json` changes cannot switch or renew the run. An older issue
without a snapshot uses a marked conservative autonomous default. A direct launch without
`--pm-issue` no-follow reads the PM-owned mode from `.pm/config.json` at every outer boundary.
Legacy config with no mode fields is autonomous.
The canonical values are `iteration_mode: autonomous | human_guided`, a human-guided-only
`human_checkin_lease_minutes` from 5 through 1440, a UTC
`human_checkin_lease_expires_at` ending in `Z`, and
`human_unavailable_fallback: pause | autonomous_review`. `Fast` is only a UI nickname and is not a
persisted value. Malformed, partial, contradictory, linked, or special-file config fails closed.
If a human-guided lease expires, `pause` returns without any scheduler-model or child turn;
`autonomous_review` switches explicitly to review-heavy autonomous behavior.

Humanize's live `Turns` panel aggregates turn starts and ends across the outer flow and every called
Slot into one transition graph; it is neither a nesting diagram nor a chronological transcript. Each
arrow counts a transition from the last agent whose turn ended to the next agent whose turn began,
and the displayed lines are sorted by count. For example, `scheduler -> planner` can mark entry
into a Slot, while `planner -> scheduler` marks that child returning to the next outer round. The
Slot validator forbids a `scheduler` child role, so the scheduler shown in `Turns` is always the one
outer scheduler. For exact boundaries, inspect the persisted cycle directory: the outer
`cycle.jsonl` records `called` and `returned` events that name the child record, and each Slot call
has its own `cycle.<flow>_<id>.jsonl` beside it.

On each Agent row, `×N` is how many real turns that named Agent has begun; it is not calculated
from arrow endpoints. `▶` means at least one of that Agent's turns is still open, while `·` means
the Agent has participated but is idle at that moment. An arrow's own `×N` is how often that exact
last-ended/next-started transition has occurred during the run. Scheduler `×N` therefore includes
both planning turns and evidence-only post-child assessment turns; it does not mean that several
outer scheduler instances are running.

Before returning, the scheduler must atomically replace `dispatch.txt` with one of these exact
payloads:

```text
false
```

or:

```text
true
.humanize/flows/slot.py
```

Both examples end with a newline. `false` returns from the outer flow. `true` asks the outer flow
to validate and execute the approved child. Autonomous mode then starts another fresh Codex
scheduler round. Human-guided mode instead writes
`workflow-output/humanize/scheduler/observation-checkpoint.json`, changes dispatch
to `false`, and returns after the completed child assessment so a human can observe before another plan. A
missing file, the reset `pending` value, uppercase spelling, extra line, absolute or traversing
path, symlink/special file, the scheduler directory, `default.py`, or any other Python path fails
closed.

Before `true`, the scheduler also atomically writes `.cycle-decision.tmp` with schema
`humanize-iteration-decision/v3`, one exact `idea_review_disposition` (`performed`,
`skipped_human_guided`, or `triggered_by_risk`), a bounded risk-trigger list, non-empty bounded
`reward_hacking_risks` list of at most five task-specific hypotheses, non-empty bounded
`expected_outcome` and `expected_measurement` strings, and one strategy (`exploit`, `verify`,
`substitute`, `pivot`, or `explore`). After a normal child return, a fresh evidence-only scheduler
turn emits `humanize-child-assessment/v1`: an observed outcome, separate `met | missed |
inconclusive` statuses for the expected outcome and measurement, a typed deviation, reward-hacking
signals, and one or more non-scheduler `workflow-output/humanize/...#sha256:<digest>` references. The runtime
no-follow reads each single-link regular file and verifies its current digest before appending a
bounded `humanize-cycle-ledger/v1` record to `history.jsonl` with observed outcome, typed deviation (`none`,
`relevance`, `authority`, `accessibility`, `unexpected`, `contradiction`, `repeated_failure`, or
`reward_hacking`), next strategy, digest-bound evidence references, and a
`structural_review_required` flag. `none` is accepted only when both expectation statuses are `met`;
the runtime also scans the referenced bytes for explicit contradiction and reward-hacking markers.
Only consecutive deviations under the same expected outcome and measurement count as repeated.
When launched with a sealed PM issue, each history record also carries `history_scope` derived from
that issue's path and SHA-256. Ledger reads and repeat detection ignore records from every other
scope, so the bounded scheduler trace cannot carry one issue's deviation or reward-hacking state
into another. The per-issue `.pm/tasks/<issue-id>/events.jsonl` chain remains the durable PM evidence
authority.
Repeated deviations and reward-hacking/jailbreak signals require `pivot`/`explore`, a changed
expected outcome or measurement, or a clean stop; they cannot be retried blindly. These checks make
label-only repetition fail closed, but they do not prove semantic correctness or prevent all model
tool misuse. The effective
mode, decision, and ledger are repeated in the child task. Human-guided may skip only idea-level
logic review after a minimum alignment/safety/authority/resource/measurement/falsifier gate. Both
modes retain code and alignment review, experiment/proof validity, evidence integrity,
deterministic reduction, and terminal review. Contradiction, ambiguity, plateau/repeated failure,
irreversible/high-cost or safety-sensitive action, a weak proxy, and human/lease absence restore
targeted idea review or the configured fallback.

The external `workflow-output/humanize/scheduler/observation-checkpoint.json` has the hypothesis,
agent interpretation, anomaly/alternate interpretation, candidate next tasks, evidence locations,
an empty `human_feedback` position, task/policy/issue bindings, and a canonical checkpoint ID. It
sets `next_planning_allowed` to false. The top/UI owner restarts with the same `--pm-issue` when
applicable and `--human-feedback '<observation>'`. The launcher binds that response to the exact
checkpoint bytes in a self-digested receipt; missing, stale, tampered, cross-task, or cross-issue
feedback fails before another Scheduler turn. First use atomically publishes a content-addressed
record under `workflow-output/humanize/scheduler/feedback-consumed/`; the same checkpoint cannot
be consumed by a second invocation, and every retry revalidates both records. The file is not completion,
`HUMAN_BLOCKED`, an in-flow human node, or Humanize resume authority.

## Preparing a child

The TUI configures one fixed child pool after the scheduler: executor, reviewer, planner,
preflight, collector, counterevidence, reducer, knowledge, applicability, and experimenter. A
candidate Slot declares any ordered subset of those roles and must include reviewer. The same Agent
objects, including their selected models and accounts, survive every Slot change. The bundled Seed
Slot selects executor followed by reviewer.

When proposal choice or result interpretation is consequential, the scheduler may author one
bounded consultation Slot. Proposal branches such as `planner` and `counterevidence` work from the
same frozen constraints without seeing each other's answer, then `reducer` preserves disagreement
and the chosen rationale. Result analysis may similarly pair `reducer` with `applicability` over the
same raw evidence. This is one fan-out/fan-in, not voting, and `reviewer` remains mandatory. The
scheduler cannot change any configured model or effort; a required Cursor/Fable route is outside the
bundled Humanize backend set and must use the built-in scheduler.

Write a regular candidate outside the discoverable flow
directory, then let the validator publish its content-addressed version and retarget the tracked link:

```bash
python3 .claude/skills/write-humanize-flow/scripts/validate_project_flows.py \
  --root . --trusted-seed \
  --candidate .humanize/scheduler/candidate.py --install-slot
```

The installer creates (or verifies) `flows/versions/slot-<sha256>.py` as a digest-bound,
single-link regular file without group/world write, preserves the matching `.approved/slot-<sha256>.py` snapshot, and
atomically replaces `flows/slot.py` with the exact relative target. A filename/content mismatch,
absolute or traversing link, linked parent or target, special file, hardlink, or unowned target fails
closed. A collaborative Git checkout can restore a tracked version as group-writable; only after a
no-follow read proves its filename digest does the reader clear those write bits and revalidate.
This makes each approved child a small, reviewable Git version while keeping the runtime decision
path fixed to `slot.py`.

Before the first Codex turn, the outer flow reads the project validator without following links,
loads its exact bytes through a sealed memfd, and holds that callable for the whole run. Every
`true` round uses the held validator on the current slot bytes, requires the matching read-only
`.humanize/flows/.approved/slot-<sha256>.py`, opens it without following links, and copies the
verified bytes into a sealed memfd. Pinned Humanize rejects a memfd pseudo-name as a flow path, so
the scheduler creates a randomized private read-only `.humanize/scheduler/.slot-load-*/slot.py`
from only those sealed bytes, calls the real `hmz.flows.load()` on that path, rechecks its inode
and digest after the child returns, and removes it. A scheduler-turn edit cannot replace the held
validator, and a later disk-snapshot edit cannot change the staged child. The outer flow never turns
`true` plus an arbitrary `.py` path into host-code authority. The child callable is resolved again
on every true round, so a newly approved slot is visible only to the next dispatch.

Every generated Slot reviewer prompt must attempt one bounded read-only `cursor-agent` second
opinion when command execution, installation, authentication, model, and quota are available. It
must forbid Cursor workspace edits, independently verify findings, and continue its own review if
Cursor is unavailable. Cursor absence is not `HUMAN_BLOCKED`, and this advisory attempt is not a
Humanize Cursor role or a replacement for the configured reviewer.

`Config.max_rounds` defaults to 50 and is bounded from 1 through 64. Each fresh scheduler turn has
one initial attempt plus four retry rounds by default (`scheduler_retry_rounds`, bounded from 0
through 4). The fixed waits before those retries are 5 minutes, 10 minutes, 30 minutes, and 1 hour.
Only native Codex process failures whose bounded stderr identifies a recoverable rate-limit,
usage-limit window, transport, timeout, or temporary-service condition are retried. Insufficient
credits or billing/payment authority, authentication, permission, model, sandbox, or a stopped
app-server process stop immediately. Before every
attempt and after every exception the dispatch file is reset to exact `pending\n`, so a failed turn
can never authorize a child. Redacted, bounded backend diagnostics and retry/stop outcomes are appended to the ignored
`history.jsonl`; the next scheduler prompt includes the prior target/outcome and any retry context,
as well as the Humanize TUI/cycle trace and `workflow-output/humanize/`. Reaching the configured cap
without `false`, or failing all five attempts, raises an error instead of silently
looping.
The post-child assessment uses the same retry schedule for recoverable native-Codex failures and
never reruns the completed child. Missing/malformed assessment, absent evidence, digest mismatch, or
a hard assessment failure stops rather than recording a generic successful outcome.
The typed Humanize `Unrecoverable` failure always stops immediately even if its diagnostic includes
a transient marker.

Humanize2 revision `8fbb2b718d74b7ef1d3d0650a9768415ec489f26` guarantees bare `@flow` and
`hmz.flows.load()`. It also exposes Cursor, callback tools, a board, fleet events, whole-run
containers, persisted per-place fallback/retry policy, and flow-owned skill mounting. This
scheduler selects none of those optional execution features: it calls no `offers()`, declares no
`Person`, passes no `--container`, chooses no Cursor default, and declares
`AgentDefaults(goals=False)` for every place. It does carry one `plateau-diagnosis` skill under its
own directory. `validate_project_flows.py` byte-compares the complete mounted tree with the
canonical asset; extra, missing, linked, or modified content fails admission. The repository
launcher isolates Humanize state under `.humanize/scheduler/runtime`, so unrelated global
fallbacks cannot silently apply. This flow does not opt into `resumable=True` and does not fetch
Flowverse code.
