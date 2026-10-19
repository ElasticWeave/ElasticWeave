---
name: write-humanize-flow
description: "Author trusted native humanize2 flows with validated composition, retries, reload, and digest-bound slots; use for scheduler or flow source changes."
---

# write-humanize-flow

Every real native agent turn used by an authored flow or its scheduler gets a 3600-second timeout
and the bounded transient retry policy in
`../default-mode/references/native-cli-execution.md`; short bounds are reserved for deterministic
probes and cleanup. Record the task's Simplify -> Verify decision before dispatch and run the
sample loop only when that decision says it is warranted. Simplify -> Verify never requires Grok;
Grok remains optional general diversity for a named blind spot.

Author native, trusted `humanize2` flows. Use `humanize-workflow` instead when mapping a
platform-neutral `workflow-design/v1` artifact through the narrow safe adapter.

Authoring a Humanize flow does not make Humanize the default runtime. Codex normally executes
workflows through the built-in `$subagent-workflow` scheduler. Use the Humanize runtime for a
trusted flow only when the caller explicitly selects that route or the flow is guaranteed by its
validated composition to be loaded and called as another trusted flow's sub-flow. A standalone
flow, an exploratory prototype, or a prose claim that it may become a sub-flow is insufficient;
until the parent/child contract is validated and digest-bound, run the work through a native
subagent or the built-in scheduler.

## Before writing

Read `references/authoring-contract.md`. Treat every flow as executable project code. Never use
humanize discovery, `loaded()`, `held()`, or `load()` to inspect untrusted Python: each reads a
flow by executing its top level. Do not fetch or run Flowverse entries as discovery.

Confirm that every requested role is supported by both the installed Humanize backend and the
specific bundled launcher. The upstream pin exposes Cursor Agent, but the audited default,
research, and neutral-adapter launch contexts currently stage only native Claude Code and Codex.
Fable 5.1 (`claude-fable-5-1`) remains scoped advice and GPT-6 Astra
(`gpt-6-astra`) is the active OpenAI review/advice route for Anthropic-family writers. Resolve the actual writer execution first and select only a policy-qualified
reviewer/adviser from the opposite upstream provider. The exact identity, effort, and
opposite-provider relation must resolve from fresh authenticated live catalogs; Astra is active in
the canonical policy and still requires live Codex availability. If any selected
route is unavailable or unverifiable, report it unresolved; never relabel or silently substitute
another model, version, provider, backend, or effort. A Cursor-only activation must still fail
before `hmz exec`. Route Cursor-only
Kimi/GLM/Grok diversity work through the built-in subagent scheduler, or report it unsupported.

## Authoring procedure

1. Declare one ordinary Python function with `@flow`. Import `Agent` from `hmz.flows` and annotate
   `agents` as a fixed tuple or, preferably, a `NamedTuple` whose fields express semantic roles.
2. Add an optional third argument as `Config | None`, where `Config` is a Pydantic model with
   finite bounds and cross-field validators. Default with `config or Config()` because `hmz exec`
   supplies no interactive configuration.
3. Ask for machine-readable decisions with Pydantic output models. Reject `None`, malformed
   output, unknown verdicts, exhausted bounds, and missing terminal evidence.
4. Use `agent.aturn()` for a fresh session. Use `agent.new()` once and reuse that session only when
   history is an intentional state boundary. Use `async def` plus `asyncio.gather()`/`abatch()`
   only for independent branches; never overlap turns in one session.
5. Bound calls, rounds, wall-time assumptions, and retry paths explicitly. Return or raise at every
   terminal; a cycle journal is a trace, not a durable checkpoint or completion proof.
6. Keep the flow's import-time behavior declarative. Do not read secrets, mutate the workspace,
   run commands, or make network calls at module top level.
7. Use the explicit validator scope that matches the action. `--trusted-seed` validates the
bundle-owned default/slot contract and, when present, admits only the byte-exact bundled
`scheduler` directory, including every file Humanize mounts from its flow-owned `skills/` tree; it
reports every other direct `.py` entry or directory containing
`__init__.py` as unvalidated without importing it (underscore-prefixed direct Python modules are
helpers rather than offered flows). An extensionless direct symlink is reported under the same
`<name>/__init__.py` namespace and is never followed or admitted. `--all-admitted`
additionally fails when any other discoverable native Python flow lacks a supported deterministic
validator. It also admits the complete byte-exact bundled `deep_research.py`, `auto_research.py`,
and `_research_reference.py` set against validator-owned approved digests. Any other direct
underscore Python helper fails `--all-admitted`; underscore is not a generic bypass. Additional
native flows must have an explicit admission contract; the bundled `parallel`/`parallel_worker`
tree is admitted with its fixed digest manifest. Neither mode claims arbitrary trusted Python behavior is safe;
source review and normal code review remain mandatory. For a generated or replaced slot, record
the validator's SHA-256 and compare it immediately before dispatch.

## Outer Codex scheduler

The directory flow `.humanize/flows/scheduler` is the Humanize TUI `/flow` entry for a bounded
outer loop. Its first agent must have `backend == "codex"`. The remaining positions are configured
once as executor, reviewer, planner, preflight, collector, counterevidence, reducer, knowledge,
applicability, and experimenter. Each Slot declares an ordered subset of that pool and must include
reviewer; the scheduler passes the same already-configured Agent objects, so retargeting the Slot
does not change models, accounts, effort, permissions, or sessions.

Before tuning a Slot, the scheduler decides whether one bounded multi-model consultation is useful.
For a consequential proposal it may independently fan out to differently configured roles such as
`planner` and `counterevidence`, then use `reducer`; for surprising, contradictory, inconclusive,
or plateaued evidence it may use independent `reducer` and `applicability` result interpretations.
The branches receive the same frozen constraints/evidence before cross-exposure, preserve
disagreement, and fan in once. This is not majority voting and does not replace the Slot's required
`reviewer` or the outer terminal review. The scheduler cannot change a role's model, backend,
account, or effort. Each Slot must use a reviewer whose upstream provider is opposite its actual
writer. Fable 5.1 (`claude-fable-5-1`) remains scoped advice and GPT-6 Astra (`gpt-6-astra`) is the active
OpenAI review/advice route after fresh live-catalog resolution; it does not replace a configured
child role merely by being named. If the fixed child pool cannot supply the required direction, the
Slot is unresolved and must not substitute. Cursor-only diversity uses the built-in scheduler.

Each direct scheduler call is a
fresh Codex session acting as team leader: it may inspect state and evidence, design or tune a Slot,
and decide the next dispatch, but it must delegate every implementation, research, experiment,
reduction, and review action to that approved bounded Slot/subagent. It must not perform task work,
call a configured child role directly, schedule individual workflow nodes, or launch native Codex
subagents; those actions belong only to an approved Slot. Before it
returns, Codex must atomically replace `.humanize/scheduler/dispatch.txt` with exactly `false\n`,
or `true\n.humanize/flows/slot.py\n`. `false` returns from the flow. `true` is accepted only after
the existing slot installer has approved the exact bytes and atomically retargeted the controlled
`slot.py -> versions/slot-<sha256>.py` link. Before the first scheduler turn, the
outer flow no-follow reads and sealed-memfd loads the validator once; every `true` uses that held
callable, no-follow reads the matching digest snapshot, and copies its verified bytes into a sealed
memfd. Pinned Humanize resolves `/proc/self/fd/<memfd>` to a non-filesystem pseudo-name and rejects
it, so the scheduler copies only those sealed bytes into a randomized private read-only
`.humanize/scheduler/.slot-load-*/slot.py`, freshly evaluates real `load()` on that path, rechecks
the staged inode and bytes after the child returns, removes it, and starts another fresh session.

Missing, pending, malformed, extra-line, absolute, traversing, uncontrolled-link, special-file,
hardlinked, unowned, or still group/world-writable version, recursive, or arbitrary Python targets
fail closed. A collaborative Git checkout may restore the tracked `100644` version as `0664`; only
after a no-follow read proves the filename digest does the reader clear its group/world write bits
through the owned descriptor and revalidate its inode and bytes. `Config.max_rounds` defaults to 50
and is bounded from 1 through 64.
Each fresh scheduler turn has one initial attempt plus four retry rounds by default. The fixed waits
before those retries are 5 minutes, 10 minutes, 30 minutes, and 1 hour. Only explicit recoverable
native-Codex process diagnostics (rate limit, usage-limit window, transport, timeout, temporary
service failure, or an otherwise-unclassified non-zero process exit) are retried. Each native agent
CLI turn receives a 3600-second timeout; shorter bounds are reserved for deterministic probes and
cleanup. Insufficient credits or billing/payment authority, authentication,
permission, model, sandbox, and stopped app-server failures stop immediately. Dispatch is reset to
exact `pending\n` before each attempt and after every
exception, and bounded/redacted diagnostics are recorded in history.
Humanize's typed `Unrecoverable` failure always stops immediately, even when its message also
contains a transient marker. At the audited revision, `Agent`, `flow`, `load`, and `Unrecoverable`
are public from `hmz.flows`; native flows import the complete flow-facing API from that module.
Retry exhaustion or round-cap exhaustion raises. A normal child return is not converted directly
to generic success. One fresh evidence-only scheduler turn returns a typed assessment with separate
outcome/measurement statuses and one through six digest-qualified references under a non-scheduler
child namespace in `workflow-output/humanize/`; the runtime no-follow verifies those bytes. This turn may inspect but
may not implement, repair, plan, or dispatch, and it uses the same retry schedule without rerunning
the child. The next planning prompt receives the assessed child target/outcome and verified evidence
references. The full protocol is repeated in
that prompt, not inferred from skill discovery. Runtime dispatch/history are ignored; they are
neither checkpoints nor terminal review authority. Humanize's live `Turns` view is a flat aggregate
transition graph across the outer flow
and called Slots, not a nesting diagram or an ordered transcript; `scheduler -> planner` and
`planner -> scheduler` can therefore mean child entry and return, not a scheduler role inside the
child. The persisted cycle directory separately records the outer flow in `cycle.jsonl` and each
called Slot in its own `cycle.<flow>_<id>.jsonl`, linked by `called`/`returned` events. See
`.humanize/scheduler/README.md`.

For a PM-managed run, start the launcher with
`--pm-issue .pm/issues/<milestone>/<issue>.md`. It no-follow reads the issue, validates the exact
digest-bound iteration snapshot, and passes it to Humanize through a sealed descriptor. Every
outer boundary revalidates the issue bytes and ages only the frozen expiry; later
`.pm/config.json` changes cannot switch or renew that run. An older issue with no iteration fields
uses the conservative autonomous default; a partial, forged, or creation-time-contradictory
snapshot fails closed. A direct run without `--pm-issue` is explicitly project-config mode and
no-follow reads `.pm/config.json` at each boundary. Legacy config without iteration fields resolves
to `autonomous`; a partially present or contradictory mode contract fails closed. Canonical persisted values are `iteration_mode: autonomous | human_guided`,
`human_checkin_lease_minutes` from 5 through 1440 for human-guided only, a UTC expiry ending in
`Z`, and `human_unavailable_fallback: pause | autonomous_review`. `Fast` is only a UI description
and is never persisted. An expired `pause` lease writes an external checkpoint and returns without
a model or child turn. `autonomous_review` changes the effective mode explicitly to autonomous and
restores review-heavy idea and counterevidence review.

Every `true` decision also atomically writes a strict v3 cycle decision recording
`idea_review_disposition: performed | skipped_human_guided | triggered_by_risk`, bounded risk
triggers, at most five task-specific `reward_hacking_risks` hypotheses for reviewers to verify,
a testable `expected_outcome`, an `expected_measurement`, and one explicit strategy
(`exploit | verify | substitute | pivot | explore`). After the child assessment validates, the
runtime appends a typed cycle ledger to bounded history with assessed outcome, expectation and
measurement statuses, deviation class, next strategy, digest-bound evidence lineage, and a
structural-review flag. `none` requires both statuses to be `met`; explicit contradiction and
reward-hacking markers in the referenced bytes override it. Only consecutive comparable deviations
count as repeated. Repeated deviations or reward-hacking/jailbreak signals require pivot/explore
plus a changed expectation or measurement, or a clean stop, never blind retry. This is not proof of
semantic correctness or model tool confinement. The resolved mode,
retained safety/code/alignment/evidence/terminal gates, disposition, ledger, and observation schema
are repeated in both the fresh Scheduler prompt and the child task.
The runtime history file remains the bounded `.humanize/scheduler/history.jsonl` control stream,
but every PM-bound record carries a `history_scope` derived from the sealed issue path and
SHA-256. Ledger reads and repeat detection use only the current scope, so an issue cannot inherit
another issue's deviation or reward-hacking state. The durable per-issue
`.pm/tasks/<issue-id>/events.jsonl` chain remains the PM evidence authority and is not replaced by
this scheduler trace.
Human-guided may skip only idea-level logic review after the minimum validity gate. After its child
completes and deterministically reduces, the outer flow writes
`workflow-output/humanize/scheduler/observation-checkpoint.json`, resets dispatch to `false`, and returns instead
of scheduling another Slot. This external human observation checkpoint is neither assignment
completion, `HUMAN_BLOCKED`, a human node inside the flow, nor a Humanize resume checkpoint. The
checkpoint binds the task, policy source, PM issue bytes/snapshot when present, and its complete
body with `checkpoint_id`. The top/UI owner starts a new invocation with the same `--pm-issue`
when applicable and `--human-feedback '<observation>'`. The launcher validates the exact pending
checkpoint bytes and seals a self-digested `humanize-human-feedback-receipt/v1`; the flow verifies
that receipt before the next Scheduler model turn, atomically publishes a content-addressed
consumption record, and revalidates both checkpoint and consumption bytes before every retry or
child boundary. A checkpoint can be consumed by only one invocation. Missing, stale, replayed,
tampered, cross-task, or cross-issue feedback fails closed.

Start a direct TUI through `./.humanize/run_scheduler.py`, or a PM-bound run through
`./.humanize/run_scheduler.py --pm-issue .pm/issues/<milestone>/<issue>.md`. That executable,
repository-owned launcher invokes native `hmz -f local/scheduler` with one ordered `-a` value per
role. It reapplies the versioned defaults on every invocation without a shell-startup or global
Humanize configuration and without naming providers or permissions; pinned Humanize therefore
uses the ambient account and permission `bypass`. It sets `HUMANIZE_HOME` to the project-owned
`.humanize/scheduler/runtime`, isolating saved UI/model choices and per-place retry/fallback policy
without moving native CLI credentials. Every role defaults native goals off; `/agents` remains
available for a deliberate session override. It
offers `--check` to print the exact pin, flow, command, and eleven defaults without a model call. It
rejects re-entry through that launcher when a sealed scheduler context is inherited, and it holds
one nonblocking project-local owner lock across `exec` so a second launcher cannot own that project.
Direct `hmz` bypasses these launcher guards as well as the defaults; the Scheduler prompt forbids it,
but Humanize does not tool-confine the Scheduler. This is process coordination, not same-UID
confinement. It
sets `HUMANIZE_SENTRY=off` and disables Python bytecode writes for this process, avoiding telemetry
and preventing a normal run from dirtying the audited editable checkout. It
accepts only the audited source-local pin selected by `HUMANIZE2_HOME` or the standard data path.
Before `--check` or launch, the shared `humanize-workflow` validator binds Git, wrapper,
interpreter, editable-package metadata, imported source, and version. An arbitrary executable at
that path and every unrelated `hmz` on `PATH` fail closed. Plain
`hmz -f local/scheduler` deliberately bypasses the automatic defaults and uses Humanize's saved
choices instead. These interactive
defaults are not canonical semantic roles or fresh catalog receipts.

The bundled two-role Seed Slot uses executor and reviewer. Its reviewer prompt makes one bounded
best-effort attempt to ask an installed `cursor-agent` for a non-mutating second opinion, then
independently verifies any finding. Missing command execution, installation, authentication, model,
or quota is recorded and review continues; it is not `HUMAN_BLOCKED`. This is an advisory command
inside the configured reviewer, not an admitted Humanize Cursor route, a model substitution, or an
independent terminal-review route. Its absence is tolerable only because it is supplemental; if a
Cursor identity is explicitly selected to satisfy required review or advice, the same absence is
unresolved and cannot be replaced silently.

## Default callable slot

Copy `assets/project/.humanize/` into the project without overwriting local files. The seed contains
`flows/default.py`, the controlled `flows/slot.py` link plus its
`flows/versions/slot-<sha256>.py` seed, the byte-exact outer
`flows/scheduler/__init__.py`, and its scheduler
protocol files. For the separate non-TUI `default.py` path, the installer records approved slot
bytes under `flows/.approved/slot-<sha256>.py`, but that launcher's production dispatch does not
reopen the path. The shared default-root launcher seals the validated default and slot bytes in
memfds, passes their descriptors through `pass_fds`, and the root calls only the descriptor named
by `HUMANIZE_SLOT_FD`. Replacing the controlled link, a version, an approved snapshot, or a parent
directory after launch cannot redirect that dispatch. Approving new bytes creates the input for a later,
separately sealed default-root dispatch. The TUI scheduler follows the per-round snapshot-to-memfd
path described above instead.

Do not rely on a callable to pin old main-file bytes. A callable already returned by `load()`
rereads the child on every invocation; only an invocation already in flight keeps the function it
started with. Normally imported helper modules remain cached in `sys.modules`. Put replaceable
behavior in a regular candidate, approve it with the installer, and let the installer create a new
digest-named version; use versioned helper module names for helper code.

Replace the slot through the validator's atomic installer:

```bash
python3 .claude/skills/write-humanize-flow/scripts/validate_project_flows.py \
  --root . --trusted-seed --candidate .humanize/scheduler/candidate.py --install-slot
```

The installer accepts a narrow slot shape, rejects links and unsafe imports/top-level expressions,
validates its fixed signature, uses exclusive creation for the digest-named version and approved
snapshot, and atomically retargets only the controlled `slot.py` symlink. An existing digest name
with different bytes, a group/world-writable or hardlinked/special target, or an unsafe link fails closed. It
prints the installed SHA-256. This is race-resistant against cooperative edits, not an OS sandbox
or an immutability boundary against malicious same-user code. The AST check constrains
discovery-time execution; it is not a capability proof for function bodies. Human review remains
required for behavior.

## Run explicitly

Humanize has no native project default auto-start contract. Start the root explicitly and provide
one agent per declared role, in order, with the least permission that role needs:

```bash
python3 .claude/skills/write-humanize-flow/scripts/run_default.py --root . --hmz <pinned-hmz> \
  --executor-role frontier_execute --reviewer-role advisor \
  -- "<task>"
```

The launcher validates both seed files and binds Humanize to the audited origin, clean source HEAD,
canonical console script, trusted interpreter, editable provenance, imported source, entry point,
and version. It consumes only the selected root's `config/workflows/models.json`; neither
`--roles` nor a reasoning-posture CLI/spec input is an override surface. The complete canonical
model-role validator and launch-spec resolver require a fresh authenticated catalog and an exact
Max review pairing whose upstream provider is opposite the actual writer execution. The selected
role remains the canonical policy pairing; Fable 5.1 (`claude-fable-5-1`) remains scoped advice and GPT-6 Astra
(`gpt-6-astra`) is the active OpenAI review route for Anthropic writers. Task classes come from the canonical one-role/one-task-class mapping and
posture comes only from its declared `defaultPosture`.

For a fully supported activation set, the launcher verifies every fresh
`workflow-native-launch-receipt/v1` self-digest, exact request, current-policy digests, resolved
identity, writer identity, and fresh concrete-catalog coverage. It then writes one receipt per
activation under
`workflow-output/humanize/default/<run-id>/evidence/model-launches/`, seals a launch-evidence
digest, and passes that commitment in the child environment. It invokes the root by sealed memfd,
not `.humanize/flows/default.py`, through the shared passwd-derived execution context. That context
uses a scrubbed environment and private staged bare `claude`/`codex` executables, seals the slot
descriptor, and rechecks the policy, receipts, native closure, and pinned Humanize closure after
`hmz` exits. Bootstrap-unverified roles, catalog candidates,
missing efforts, unsupported backends, substitutions, changed receipts, and policy inconsistencies
fail closed as unresolved. Test catalog data is available only through private in-process test callables; the
production CLI and environment expose no catalog, model, or backend injection seam. Resolve live
model identifiers during default-mode startup. Do not rely on humanize's backend permission labels as an operating-system
sandbox.

## Validation

Run:

```bash
python3 .claude/skills/write-humanize-flow/scripts/validate_project_flows.py \
  --root . --all-admitted
python3 -m unittest discover -s .claude/skills/write-humanize-flow/tests -p 'test_*.py'
/absolute/pinned-humanize-source/.venv/bin/python \
  .claude/skills/write-humanize-flow/tests/real_humanize_scheduler.py -v
```

The second command is the real pinned-Humanize regression for Scheduler discovery and
Scheduler-to-Slot loading. It first validates the invoking source-local environment against the
audited revision, repository, editable package, wrapper, interpreter, and imported source, then
uses actual `hmz.flows.drives` and `hmz.flows.load`; it has no mocked Humanize loader.

Use `--trusted-seed` instead of `--all-admitted` when intentionally authoring additional trusted
native flows; inspect its `unvalidatedFlows` list and review those files as executable code before
running them. Do not call such a project all-admitted. Use `--all-admitted` only when the execution
set is required to contain no uncovered native Python flow or direct helper. The exact bundled
scheduler source, its complete mounted skill tree, and the research reference set are the only
additional admissions.

For installation, mapping, pinned CLI validation, and the real no-paid integration receipt, use
`humanize-workflow`. That receipt executes this bundled `default.py` through pinned Humanize
`Runner`/`load` with zero-turn `AgentBase` stubs and binds the exact default/seed/approved bytes.
