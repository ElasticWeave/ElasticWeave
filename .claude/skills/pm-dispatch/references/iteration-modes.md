# Iteration Modes

This contract controls research-cycle pace. It does not change authority, model routing,
reasoning posture, issue review policy, or terminal completion rules.

## Contents

- Canonical state and CLI
- Lease resolution
- Cycle contracts and retained gates
- Coordinator decision and evaluation
- Evidence basis
- Per-task evidence workspace

## Canonical state

Persist only these modes:

- `autonomous`: the agent must carry the idea-level challenge before execution and may begin the
  next planning cycle after deterministic reduction.
- `human_guided`: the agent optimizes for short cycles while a human feedback lease is active.
  `Fast` is a prose or UI alias only. It is invalid in config, workflow state, receipts, and issue
  metadata.

`.pm/config.json` carries all four fields together:

```json
{
  "iteration_mode": "human_guided",
  "human_checkin_lease_minutes": 60,
  "human_unavailable_fallback": "pause",
  "human_checkin_lease_expires_at": "2026-08-20T13:00:00Z"
}
```

The lease is an integer from 5 through 1440 minutes. Its expiry is UTC and ends in `Z`.
Autonomous mode has `null` lease minutes and expiry. A config with none of these four fields is a
legacy config and resolves conservatively to autonomous. A partial, malformed, or contradictory
set fails closed.

Use the delegated PM mutations below. `set human_guided` starts a new lease from the current UTC
time; `renew` starts a replacement lease from the current UTC time, not from the old expiry.

```text
pm.py iteration-mode show
pm.py iteration-mode set human_guided --human-checkin-lease-minutes 60 \
  --human-unavailable-fallback pause
pm.py iteration-mode renew [--human-checkin-lease-minutes 60]
pm.py iteration-mode set autonomous
```

## Lease resolution

| Selected mode | Lease state | Fallback | Effective mode | New dispatch |
| --- | --- | --- | --- | --- |
| `autonomous` | not applicable | either | `autonomous` | allowed |
| `human_guided` | active | either | `human_guided` | allowed |
| `human_guided` | expired | `autonomous_review` | `autonomous` | allowed with idea review |
| `human_guided` | expired | `pause` | none | refused until renew or explicit mode change |

Expiry never silently extends human-guided behavior. `pause` prevents a new PM issue; it is not
assignment completion and does not manufacture a `HUMAN_BLOCKED` verdict. Already-authorized
workers and workflows retain their launch snapshot and continue to be monitored and reviewed.
The authenticated configured human route remains limited to a fresh reviewer-authorized human-only dependency.

Every new issue snapshots and digest-binds the selected mode, effective mode, lease duration,
lease expiry/status, fallback, and UTC decision time in its dispatch metadata. Workers use this
issue snapshot, not a later project-config value. `pm.py next` renders the effective snapshot in
the invocation-visible ready line.

Both persisted modes use the issue's `.pm/tasks/<issue-id>/` evidence workspace. Naive mode is not
a persisted PM iteration mode and must not initialize, inspect, mutate, or fall back to this state.

Native Humanize workers enforce that inheritance at launch. Scheduler runs use
`.humanize/run_scheduler.py --pm-issue .pm/issues/<milestone>/<issue>.md`; native Deep/Auto runs
use `run_humanize_research.py --pm-issue` with the same path. Both no-follow bind the issue bytes
and snapshot digest before Humanize starts. Direct Scheduler or standalone research launches are
explicitly outside a PM issue context and cannot claim issue provenance.

## Cycle contracts

Autonomous:

```text
propose -> idea/counterevidence review -> experiment or prove -> deterministic reduce -> plan
```

Human-guided:

```text
propose -> minimum validity gate -> experiment or prove -> deterministic reduce
        -> human observation checkpoint -> plan using bound feedback
```

The minimum validity gate checks alignment, authority, safety, resource feasibility, the measured
proxy, and at least one falsifier. Human-guided mode may skip only redundant idea-level logic
review. It never skips code review, alignment review, safety/authority checks, experiment or proof
validity, evidence integrity, deterministic reduction, or the configured independent or
machine-gated terminal review.

The post-reduction checkpoint records the hypothesis, actual operation, raw observation, agent
interpretation, anomaly or alternate interpretation, candidate next tasks, and human feedback.
A headless workflow may return its bounded checkpoint result; it does not contain a fake human
node. This is a `worker_result` for that workflow, not a top-session `final_return`. The top keeps
monitoring authorized siblings, reviews, and fan-in. The checkpoint pauses only the next planning
cycle, whose proposal consumes bound feedback or an explicit autonomous-fallback record.
For the native Scheduler, the top restarts the launcher with the same PM issue and
`--human-feedback`; its self-digested receipt must match the exact pending checkpoint before the
next Scheduler model turn. Native Deep/Auto feedback similarly binds the prior reviewed terminal
artifact, raw-byte SHA-256, embedded checkpoint ID, and feedback in the sealed launch receipt.

Targeted idea review is restored when any of these facts holds:

- the proxy, success criterion, or falsifier is ambiguous;
- evidence contradicts the active hypothesis or an earlier reduction;
- the same approach repeatedly fails or the search plateaus;
- an action is irreversible, expensive, safety-sensitive, or outside established authority;
- the human lease expires, the expected human is unavailable, or the feedback is too weak to
  justify the next proposal.

Each cycle records `idea_review_disposition` as `performed`, `skipped_human_guided`, or
`triggered_by_risk`.

## Coordinator decision

At session entry and every task boundary, the top coordinator runs `iteration-mode show`, states
whether it is optimizing for autonomous rigor or human-guided cycle time, and includes the issue's
mode snapshot in the bounded worker/workflow brief. It chooses the pace from task risk,
uncertainty, and the human's explicitly stated check-in cadence; absent a live cadence, it uses the
conservative autonomous default. Any mode mutation is a delegated PM state change. `debug`,
`ui_mode`, reasoning posture, semantic role, model-call budget, and
`review_policy` remain orthogonal.

Evaluate the policy with cycle latency, model calls/tokens, usable human-idea yield, invalid
experiment rate, rework/rollback, evidence corrections, alignment/safety escapes, and terminal
`REVISE` frequency. Faster cycles are useful only when retained gates and downstream correction
rates stay sound.

## Per-task evidence workspace

Creating a new issue also creates `.pm/tasks/<issue-id>/task.json`, `events.jsonl`, and
`experiments/`. `task.json` binds the workspace to the canonical issue path and immutable
iteration-mode snapshot. Existing issues are never migrated implicitly; initialize one with
`pm.py task-workspace init <issue-id>`. For an issue that predates iteration snapshots, this
explicit command binds the deterministic conservative `autonomous` legacy interpretation and does
not rewrite the issue.

Use `task-workspace record` for `expectation`, `experiment`, `observation`, `deviation`, `simplify`,
and `verify` events. Every kind except `expectation` requires at least one real, existing
repository-relative `--file`. PM reads each file without following links, rejects hardlinks and
special files, and computes the byte count and SHA-256 itself. An optional `--experiment-path`
must name an existing plain directory beneath that issue's
`.pm/tasks/<issue-id>/experiments/` root. Each event is one canonical JSON line chained to the prior
event digest; append first verifies the complete chain and takes a nonblocking single-writer lock.
`task-workspace show` performs the same metadata and chain verification without mutation.

This is the managed Default/Fast evidence ledger, not a substitute for the original files or the
terminal review. Do not create fake data merely to satisfy an event kind. Naive mode runs outside
the project configuration and PM state, so it never calls these commands.

## Evidence basis

- [Agent Laboratory](https://arxiv.org/abs/2501.04227) compares autonomous and co-pilot research
  workflows and reports that human involvement changes output quality and soundness.
- [ResearchAgent](https://arxiv.org/abs/2404.07738) reports gains from reviewing agents with
  diminishing returns across repeated review rounds.
- [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798)
  shows why ungrounded intrinsic self-review is not a reliable correctness mechanism.
- [The AI Scientist](https://arxiv.org/abs/2408.06292) supplies the autonomous
  idea-plan-experiment-write-review loop used as the contrasting cycle shape.

These results motivate the policy; they do not prove that every project benefits from the same
lease length. Treat the lease and review-trigger thresholds as observable project decisions.
