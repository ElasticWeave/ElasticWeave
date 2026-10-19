---
name: humanize-workflow
description: "Map, validate, install, smoke, or run an eligible workflow through humanize2; use when the Humanize adapter is selected."
---

# humanize-workflow

Native model turns must follow `../default-mode/references/native-cli-execution.md`: give the real
agent CLI a 3600-second timeout and use at most four retries with 300/600/1800/3600-second backoff
for transient API, transport, service, heartbeat, or unexpected-process failures only.
Deterministic hmz probes and cleanup may retain their shorter bounds. Do not retry authentication,
quota, invalid-request, malformed-output, evidence, or policy failures.

## Explicit runtime boundary

This skill is not the default workflow runner for Codex. A normal Default or Fast session should
delegate workflow execution to the built-in `$subagent-workflow` scheduler. Invoke this skill only
after `$run-workflow` has explicitly selected the narrow Humanize adapter for a compatible neutral
design, or from inside an already-running trusted Humanize flow that is guaranteed to load and call
an admitted, digest-bound child as a sub-flow. Writing a flow does not itself authorize entering
the Humanize runtime: use `$write-humanize-flow` for authoring, and require validator evidence for
the parent/child relationship before dispatch. If that relationship is absent, uncertain, or merely
described in prose, route the runtime through the built-in subagent scheduler instead.

Map only capability-compatible `workflow-design/v1` artifacts into adapter-generated Python
flows. Treat every Python flow as executable code: never import, list, inspect, or run an arbitrary
user or flowverse flow during selection or validation.

## Pinned upstream

- Repository: `https://github.com/humanfia/humanize2.git`
- Audited revision: `8fbb2b718d74b7ef1d3d0650a9768415ec489f26`
- Distribution/CLI: `hmz==0.1.0`, Python 3.12+, Apache-2.0

Install the actual `humanize2` repository at the pinned revision. The pin includes the required
directory-flow entry point, callable-flow API, Codex reconnect completion fix, Cursor profile,
fleet events, callback tools, board, container option, per-place fallback policy, and flow-owned
skill mounting. The optional execution features remain inactive in the bundled scheduler. Its one
mounted `plateau-diagnosis` skill is part of the byte-exact scheduler tree and is validated before
launch; an added, missing, linked, or modified mounted file fails admission. Changing the pin
requires a fresh audit and receipt.

## Capability boundary

Read `references/capabilities.md` before mapping a new design. v1 accepts only:

- `governance.reviewPolicy: independent` and `completionProof: none`;
- a finite acyclic, single-successor graph of an input seed, fresh `agent`/`decision` nodes,
  and explicit terminals;
- typed state, Pydantic-validated node outputs, exact decision verdict routes, backend-native
  read/write permission rungs, the native humanize cycle journal, and an adapter result file.

Reject reducers, fan-out/fan-in, loops, persistent sessions, node skill declarations, durable
checkpoints, interrupted-session resume, in-graph machine gates, and state-bound proof. Humanize's
Python flexibility is not an adapter guarantee.

## Latest native flow capabilities

The audited revision's async, recursive-flow, reload, and resume tests passed locally. It adds
native composition that is intentionally outside the neutral adapter above:

- `async def` flows may `await asyncio.gather(...)` over independent `load()` calls; each call gets
  its own flow record and must use a distinct `Agent` clone or `drives` mapping;
- `load()` keeps child skills isolated by default and accepts `inherit_skills=True` only when the
  wrapper explicitly wants to pass its own skills through;
- `Session.loads([...])` can select a fine-grained subset of the called flow's own skills for each
  node session, and `loads(None)`/`loads([])` have distinct all/none semantics;
- `@flow(resumable=True)` supplies a durable state dictionary, which `/resume` or
  `Runner(..., resume=epic)` passes back after an interrupted run; and
- the daemon and `/epics` UI provide detached execution, run history, and per-call records.

ElasticWeave's admitted `.humanize/flows/parallel` uses these native features. It stages each branch
in a disposable reflink-or-copy workspace, removes ambient project instruction/skill surfaces, and
mounts only the node's selected skills. The neutral adapter remains intentionally unchanged and
continues to reject these features.

## Install and smoke

Check without changing the machine:

```bash
python3 .claude/skills/humanize-workflow/scripts/ensure_humanize.py --check
```

Install only after selecting this adapter or receiving an explicit request. The install is pinned
and isolated under a source-local virtual environment; it never runs `pip` into the user or system
Python:

```bash
python3 .claude/skills/humanize-workflow/scripts/ensure_humanize.py --install
HUMANIZE2_SMOKE_HMZ=/absolute/source/.venv/bin/hmz \
  python3 .claude/skills/humanize-workflow/scripts/smoke_humanize.py \
    --integration \
    --receipt-out .claude/skills/humanize-workflow/tests/evidence/humanize2-smoke-receipt.json
python3 .claude/skills/humanize-workflow/scripts/smoke_humanize.py \
  --verify-receipt .claude/skills/humanize-workflow/tests/evidence/humanize2-smoke-receipt.json
```

The smoke runs two trusted no-paid fixtures under temporary HOME directories. The first exercises
the real CLI and cycle journal with a zero-agent callable flow. The second copies the bundled
`write-humanize-flow` `default.py` and content-addressed seed slot into a temporary project, approves two zero-turn
slot variants through the real installer, and drives the two-role root with pinned
`hmz.runner.Runner` plus real `hmz.flows.load` and two `AgentBase` stubs whose `new()` method
fails if a turn is attempted. This covers the actual bundled root without constructing CLI backend
agents or making a model call. Integration mode fails when the variable
is absent or provenance does not bind the canonical wrapper, trusted interpreter, clean audited
checkout, editable distribution, exact imported source and entry point, and `hmz 0.1.0`. Execution
uses the resolved non-user-writable interpreter with fixed `python -I -S -B -c` argv and explicit
pinned source/dependency paths instead of running the wrapper. Its only stdout
is a `humanize2-smoke-receipt/v3` JSON receipt recording the repository, revision, version, zero
model calls, cycle counts, exact results, and SHA-256 commitments to both fixtures, the bundled
default/seed, the approved children, validator/probe, and stable semantic contract. The CLI fixture
proves a saved callable rereads v2 after the child changes, while an already in-flight v1 function
finishes as v1 and the next invocation sees v2. The bundled-root fixture
proves approved v1 finishes after an in-flight replacement attempt while a second root run sees
v2 only after its digest-named version is separately approved and selected. Refreshing the committed receipt
requires the explicit real pinned run; the write is atomic. Default tests never install, contact
the network, or skip: they verify that the
committed receipt is exact and current. The receipt is evidence of that pinned zero-agent run, not
proof that every machine executed it live. Runtime execution independently revalidates the exact
checkout and CLI version before every run.

## Map and validate

Author a data-only `humanize-workflow-spec/v1` JSON file. It supplies `inputPath`, a prompt string
for each model node, deterministic initial state, and optional human-request field names. A spec
may bind `reasoningPosture`, but it cannot choose it: the mapper derives the value from the current
canonical policy's `reasoningPolicy.defaultPosture` and rejects any different binding. It never
contains Python or commands.

```bash
python3 .claude/skills/humanize-workflow/scripts/map_design.py \
  .workflow/designs/<id>.workflow.json \
  --spec .humanize/specs/<id>.json

python3 .claude/skills/humanize-workflow/scripts/validate_mapping.py \
  .humanize/mappings/<id>.json
```

Mapping writes `.humanize/generated-flows/<id>.py` and `.humanize/mappings/<id>.json`. The
generated namespace is separate from trusted native `.humanize/flows/default.py` and `slot.py`;
neutral design IDs `default` and `slot` are reserved and rejected. Validation never
imports the flow. It requires and executes the owning build-workflow neutral validator, revalidates
adapter capabilities, verifies design/spec/source digests, regenerates the only accepted source
bytes, and performs a restrictive AST import/call audit. A missing, unreadable, non-running,
nonzero, or malformed neutral-validator result and any mapping mismatch fail closed.

## Run

```bash
python3 .claude/skills/humanize-workflow/scripts/run_mapping.py \
  .humanize/mappings/<id>.json \
  --hmz <reported-binary>
```

The runner validates before `hmz exec` and consumes only the selected `--root` project's canonical
`config/workflows/models.json`; alternate `--roles`, v1/v2, partial catalog coverage, forged
catalog evidence, inactive routes, and inconsistent policy fail closed. It reuses the complete
canonical model-role validator and launch-spec resolver. Ordinary agent task classes come from the
validated one-role/one-task-class mapping; a decision node's explicit independent-review contract
requires exactly one model predecessor and resolves `verification` with an exact-opposite writer.
All activations require the mapping's bound `reasoningPosture` to equal the current canonical
`defaultPosture`; every mapped semantic role must also remain in that posture's `allowedRoles`.
Neither the spec nor the manifest can select a fallback. Every decision reviewer remains a
verified Max role. Account/profile labels select a Humanize backend; review diversity is proved
only by the separate verified `upstreamProviderFamily` and `modelFamily` fields.

Before the subprocess starts, the shared canonical fresh resolver issues each mapped activation a
`workflow-native-launch-receipt/v1` under
`workflow-output/humanize/<id>/<run-id>/evidence/model-launches/`. Each receipt binds the exact
request, current policy bytes, fresh catalog evidence, concrete `catalogModelId`, and its own
digest. Persisted catalog snapshots are audit-only; a fresh concrete ID must be covered by the
receipt's catalog evidence. The adapter binds receipt and launch-evidence digests into its JSON
input, finalized `result.json`, and `terminal-evidence.json`; missing, swapped, modified, or
resolver-drifting receipts and policy changes fail closed. The per-run namespace rejects links and
overwrite collisions.

All three Humanize launchers enter the same fail-closed execution context after this admission.
It derives the effective account and canonical Humanize home from `pwd.getpwuid`, not caller
`HOME` or `HUMANIZE_HOME`; starts from an empty environment; and restores only account-derived
roots, fixed system helpers, and exact launcher receipt variables. Each run creates a private
`0700` bin, stages the receipt-authenticated native executable under Humanize's bare `claude` or
`codex` name, and puts only that bin first in `PATH`. Codex also requires a staged
`codex-code-mode-host` companion. Generated/default/slot/research sources reach `hmz` through
sealed memfd descriptors and `pass_fds`, never a mutable project or skill pathname. The launch
context validates its account, environment digest, pinned Humanize source/interpreter/package
closure, staged native bytes, receipts, and policy before dispatch and after exit.

Generic and default launches use the ambient passwd-derived account. Named research launches also
open the exact provider record through no-follow descriptors, reject loader/shell/config-root
injection, and obtain fresh zero-turn `models.ask(backend, provider)` coverage through the pinned
Humanize provider redirect with the same staged executable and scrubbed environment. Ambient
catalog output, a cache, or an account alias alone cannot authorize a named provider route.

The generated flow emits exactly one candidate terminal:

```text
TERMINAL: COMPLETE|HUMAN_BLOCKED|REVISE
```

`HUMAN_BLOCKED` additionally emits:

```text
HUMAN-REQUEST-KIND: authorization|decision|credential|external-action
HUMAN-REQUEST: <specific request>
```

`COMPLETE` is not permission for the top session to stop. Because this adapter accepts only
`independent`, the top session must dispatch the required terminal reviewer over the actual result,
workspace, acceptance evidence, and relevant diff. It may stop only after that reviewer confirms
the milestone or confirms a concrete human-only boundary.

Native callable flows are a separate authoring surface. Use `write-humanize-flow` for a trusted
project root and replaceable slot; do not infer nested-flow, reload, fan-out, or persistent-session
support for this narrow neutral adapter.

For that separate native outer scheduler, `./.humanize/run_scheduler.py` is the supported direct
TUI entry and applies the repository's ordered defaults on every invocation. Its `--check` mode
prints the exact pin, flow, command, and eleven defaults without opening the TUI or making a model
call. PM-managed work adds
`--pm-issue .pm/issues/<milestone>/<issue>.md`; the launcher seals the validated issue snapshot,
and the flow ages only its frozen lease rather than replacing it from later project config. After
a human-guided checkpoint, `--human-feedback` is accepted only when it binds the exact pending
checkpoint bytes, ID, task, and PM context. It uses the project-owned
`.humanize/scheduler/runtime` as `HUMANIZE_HOME`, isolating saved model/UI choices and the new
per-place fallback/retry file from unrelated global Humanize state. Native CLI credential homes
remain ambient. It does not lock `/agents`; omitted provider uses the ambient account and omitted
permission is `bypass` in the pinned revision. All eleven places default native goals off, while a
user may deliberately change that setting in `/agents`. No shell startup or global Humanize
configuration is needed. Plain `hmz -f local/scheduler` deliberately bypasses the automatic
defaults and project-state isolation. The launcher rejects re-entry through that
supported entry when it inherits a sealed scheduler context, and it holds one nonblocking
project-local owner lock until process exit so a second launcher cannot own the same project. Direct
`hmz` bypasses these launcher guards as well as the defaults; the Scheduler prompt forbids it, but
Humanize does not tool-confine the Scheduler. This is process coordination, not confinement against
a malicious same-UID process. It sets `HUMANIZE_SENTRY=off` for that
process only and disables Python bytecode writes, so telemetry stays off and an ordinary run does
not dirty the audited editable checkout. It
accepts only this skill's audited source-local pin selected by `HUMANIZE2_HOME` or the standard data
path, and runs the shared strong validator before `--check` or launch. An arbitrary executable at
that location and every unrelated `hmz` on `PATH` fail closed. It treats the model strings as interactive defaults rather than fresh
catalog receipts. The outer flow defaults to 50 logical rounds and gives each fresh Codex scheduler
turn one initial attempt plus four retry rounds for explicit recoverable backend failures only. The
fixed waits are 5 minutes, 10 minutes, 30 minutes, and 1 hour. Rate limits, bounded provider-reported
usage-limit windows, transport errors, timeouts, temporary service failures, and unexpected
native-process exits retry;
each real agent CLI turn receives a 3600-second timeout. Insufficient credits or
billing/payment authority, authentication, permission, model, sandbox, and stopped app-server
failures stop immediately. Every failed attempt resets dispatch to `pending` before retrying or
raising.
The typed `Unrecoverable` failure always stops immediately, even when its diagnostic contains a
transient marker.

## Security invariants

- Never pass a user-provided `.py`, named flow, or flowverse entry to `hmz exec`.
- Never treat Humanize discovery output as admission. Resolving or loading a selected Python flow
  executes its top level, so validate source bytes before any such call.
- Never skip `validate_mapping.py` immediately before a run.
- Never claim humanize permission rungs are an OS sandbox. The selected backend owns their actual
  enforcement, and humanize itself runs agents unattended.
- Never convert a crash, malformed output, missing human request, or absent result into COMPLETE.
- The upstream pin has a Cursor Agent backend, but this adapter's audited execution context admits
  only Codex and Claude. Reject Cursor-only routes or use the built-in subagent scheduler; never
  substitute or relabel a backend silently.
- Do not carry caller proxy, TLS, Python/Node/loader, vendor endpoint/model/config/cache, account,
  `GENERAL_AGENT*`, `HUMANIZE*`, or `HMZ*` variables into a launch. The context reconstructs the
  minimal environment instead.
- Pinned-source Git binds a fixed system executable by absolute regular-file path plus SHA-256,
  rebuilds the environment, disables replacement objects, pins `--git-dir`/`--work-tree`, and
  revalidates that identity after every Git subprocess. Caller PATH and `GIT_*` cannot choose the
  executable or the checkout view.
