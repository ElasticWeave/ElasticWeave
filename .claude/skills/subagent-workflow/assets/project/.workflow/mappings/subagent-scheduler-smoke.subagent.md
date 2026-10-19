# subagent-scheduler-smoke → built-in subagent scheduler

- Canonical design: `.workflow/designs/subagent-scheduler-smoke.workflow.json`
- Canonical design SHA-256: `fc601586f4f4bae9e51b11f038f0ce14cbf3934f932f418846c02ee78f89bb88`
- Mapping: `.subagent/flows/subagent-scheduler-smoke.json`
- Mapping SHA-256: `f3df4cda1cc7cb7a349eb21e3d3a095baa8a33373eb312b7348bd4d16e3b0c36`
- Adapter: `subagent-scheduler`
- Scheduler policy: configured `smol` role, `ambiguous-only`, at most one scheduler call.
- Leaf policy: configured `task` role with provider-native reasoning effort propagation.
- Validation: neutral design validator, mapping digest validation, controller fault-injection tests,
  stub native-agent integration, and an authenticated native-CLI smoke run.

| Neutral requirement | Mapping owner |
|---|---|
| Two independent analysis leaves | Controller frontier contains both leaves; the scheduler may select only those IDs. |
| Scheduler isolation | Dedicated tool-free native profile; only the explicit `scheduler.reads` state projection is exposed. |
| Fan-in | `merge.waitFor` requires both completed leaves before the deterministic template handler can run. |
| Structured output | The native runner normalizes the last workflow activation JSON; controller rejects undeclared or omitted state writes. |
| Budgets | Scheduler and leaf calls are reserved before dispatch and reconciled with the native-call journal. Both count against `maxModelCalls=4`; scheduler has the narrower cap of one. |
| Deadline | Every model or command node must declare a positive deadline. The smoke leaves use 240 seconds; values above 600 are allowed. Deadline cleanup sends SIGTERM, then SIGKILL after a bounded grace period, to the complete POSIX process group. |
| Durability | State is atomically replaced after every boundary; events and decisions are fsynced. Checkpoints bind the frozen design, mapping, roles, native route map, and input bytes. Resume reconstructs the legal DAG prefix from source responses and journals rather than trusting checkpoint state. |
| Evidence paths | The controller pins the run-directory device/inode, rejects symlinks, uses no-follow writes, and verifies ordinary evidence bytes before and after every native node. Unsafe or changed evidence stops without writing a success result. Same-UID adversarial pathname races remain an explicit process-isolation boundary; the controller does not claim an OS sandbox. |
| Scheduler evidence | Raw stdout/stderr, legal frontier, visible fields, termination class, and byte digests are retained per call. |
| Resume | Completed nodes and pending scheduler selections are retained. An in-flight scheduler or node becomes `AMBIGUOUS_SIDE_EFFECT`; it is not replayed. |
| Terminal | Only the deterministic `mergeStatus=PASS` edge reaches controller success. Missing or malformed bytes stop unresolved. Under `reviewPolicy=independent`, success is candidate-ready and the top session dispatches a fresh external terminal reviewer before stopping. |

The controller currently executes runnable leaves sequentially even when the design exposes a
fan-out set. This preserves dependency semantics through deterministic serialization but does not claim parallel
wall-time reduction. Concurrent isolated workspaces are a later capability, not an implicit v1
guarantee.

## Live evidence — 2026-08-01

These immutable runs predate the migration from `reviewPolicy=none` to
`reviewPolicy=independent`. They remain evidence for scheduler isolation, native execution,
deadline, checkpoint, and trace mechanics only; they are not evidence that an external terminal
review ran. The current governance/mapping binding is covered by the active validator and mapping
tests.

- `subagent-1785607327755-7362`: authenticated `success`, 3 total model calls, 1 scheduler call,
  2 leaf calls, 279.32 seconds. The scheduler used the tool-free Claude `smol` profile. Both leaves
  used `codex/gpt-5.6-sol` with the configured `task` role, and the native trace records
  `effort:"high"` for both invocations. The deterministic merge and terminal completed.
- `subagent-1785607672865-8848`: deterministic `success`, 605.08 controller seconds, zero model,
  scheduler, or leaf calls. The committed probe design declares `timeoutSeconds=620`, a 660-second
  global cap, and a command that requests `time.sleep(605)`. Event elapsed times span more than
  600 seconds with exactly one command activation. This demonstrates that the built-in controller
  has no implicit 600-second progress watchdog while retaining explicit node/global deadlines.

The committed evidence pack under `.workflow/evidence/subagent-scheduler-smoke/` preserves the raw
state, event, response, decision, and call-trace bytes plus their source/runtime inputs. Its offline
verifier checks hashes, counts, effort propagation, deadlines, and the greater-than-600-second span.

This mapping has `reviewPolicy=independent` and `completionProof=none`. The controller does not
manufacture completion: after either reference flow returns, the top session gives the explicit
criteria, result, and run evidence to a fresh terminal reviewer. That reviewer emits `COMPLETE`,
`REVISE`, or a narrowly structured `HUMAN_BLOCKED` artifact with an evidence commitment.

The v1 adapter still fails closed on unsupported persistent-session and cyclic designs before any
model call. The active registry disables legacy external adapters during migration, so unsupported
designs stay closed until equivalent built-in semantics are implemented and tested; finite
iterations may be unrolled into a DAG without weakening their bounded review routes.
