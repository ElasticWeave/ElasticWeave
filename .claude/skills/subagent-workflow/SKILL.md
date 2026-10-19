---
name: subagent-workflow
description: "Run a platform-neutral workflow through the built-in deterministic controller; use after run-workflow selects the subagent-scheduler adapter."
---
> **Model tier:** LOW for scheduling; mapped workflow nodes retain their configured roles.
>
> **Memory abstraction:** The model proposes a legal next action; deterministic code owns the graph, budgets, process lifecycle, state, checkpoints, and terminal meaning.

# subagent-workflow

Use the built-in `subagent-scheduler` adapter without installing an external harness. The scheduler
is not the workflow runtime. It is a constrained policy helper inside a deterministic controller.
Controller code, native execution code, tests, and reference workflow assets live in this skill.
`default-mode` or this skill materializes project-specific `.workflow/` and `.subagent/` state only when
needed; existing target files are never overwritten silently.

## Contract

Node execution is context-scoped. The controller materializes a bounded workflow allowlist derived
from node declarations, while the node prompt names the exact `skills.required` and
`skills.onDemand` paths that this node may load. A copy-on-write workspace can therefore contain
the union needed by concurrent nodes without making that union ambient authority: nodes must not
enumerate `.claude/skills` or bulk-load it, and must return an explicit escalation when their
contract lacks needed expertise. Supervisor skills and unrelated project skills remain outside the
node contract. Workflow topology, role assignment, and model-routing heuristics remain supervisor
responsibilities.

- Canonical intent remains `.workflow/designs/<id>.workflow.json`.
- The adapter mapping is `.subagent/flows/<id>.json` with schema `subagent-workflow/v1`.
- The mapping binds the exact neutral-design SHA-256 and supplies executable handlers and prompts.
- The controller computes the legal frontier, validates every scheduler choice, launches native
  provider CLIs, validates state writes, counts every model invocation, and owns terminals.
- Every design uses a terminal reviewer policy. With `independent`, controller `success` is a
  candidate-ready return and the top session must dispatch a fresh external reviewer before it
  stops. With `machine-gated`, a fresh final different-provider in-graph receipt that classifies a
  terminal and binds the final reviewed state is the terminal review; a state-bound proof, when
  declared, additionally binds PM completion. Do not duplicate either valid review externally.
- Scheduler calls use the configured `scheduler.role`, normally `smol`; never pin a model in the
  mapping.
- Roles resolve only from the canonical project-root `config/workflows/models.json` with schema
  `workflow-model-roles/v3`. Before any native process the controller enforces the same contract as
  `update-platform-config/scripts/config-sync/validate_model_roles.py`: required roles, audited
  identity map, verified catalog receipt with matching evidence digests, capability policy,
  posture ceilings, and exact cross-provider review pairings. A `v1`/`v2` document, a
  `bootstrap-unverified` catalog, partial or forged catalog evidence, an inactive or unverified
  route, and any policy inconsistency all fail closed before the run directory exists.
- The top session authorizes the run's global reasoning posture before mapping, and the mapping
  states it explicitly as `reasoningPosture`. The controller never defaults or infers it, and it
  refuses any role outside that posture's ceiling. A bounded designer/mapper selects and validates
  concrete node roles, and the top inspects its evidence. Every model node has one semantic role
  fixed in the neutral design; the scheduler cannot choose a role, model, provider, or effort.
  Changing a role requires another bounded owner to produce a new validated design/mapping binding,
  not a state patch or scheduler response.
- Every ordinary native activation, including scheduler calls, materializes a canonical
  `workflow-native-launch-receipt/v1` under `launch-receipts/<call>.json` before the process starts.
  Its shape is exactly the shared resolver contract: self-digested `request`, `resolved`, `writer`,
  and `policy` objects, with no controller fields added to that schema. A separate self-digested
  `workflow-controller-launch-binding/v1` under `launch-bindings/<call>.json` binds run/call/node,
  workspace access, the audited native execution route, all native-model review pairings, frozen
  controller inputs, and the canonical receipt byte digest. Both files are created exclusively before
  execution. Their digests enter the native trace, checkpoint ledger, and `result.json`; a missing,
  changed, extra, or swapped receipt or binding fails closed on replay.
- Every native activation launches the exact canonical executable prefix in the receipt's fresh
  catalog command evidence, never a same-named command re-resolved through the controller caller's
  `PATH`. The controller revalidates the executable and its interpreter/bundle closure before call
  reservation and the runner revalidates it before and after each physical start/resume attempt and
  before result handoff. Trace and attempt evidence bind the concrete catalog model id, catalog
  entrypoint, actual process executable, closure, invocation-prefix, argv, scrubbed environment, and
  receipt digests. Any path, byte, closure, native-map, environment, or model disagreement fails
  before a new model activation can publish output.
- The resolver and native process share one account-pinned environment with endpoint, config-root,
  credential/account-selector, proxy, TLS, and pre-main injection overrides removed, including the
  Cursor `CURSOR_` family. Native Cursor launches restore only the non-secret
  `CURSOR_INVOKED_AS=cursor-agent` wrapper marker. Each activation adds one mode-0700 private
  `XDG_RUNTIME_DIR`; non-Claude runners pair the hook's exact
  `general-agent-gate/gate-node-<safe-id>.json` pre/post state, bind its pre-consumption digest in the
  native trace, and remove the complete test-owned runtime tree on every exit path. Shared `/tmp` is
  never scanned for hook state.
- Scheduler calls use a dedicated tool-free native CLI profile and see only the state fields
  explicitly listed in `scheduler.reads`; the default projection is empty.
- In `ambiguous-only` mode, a single legal successor is executed mechanically. The scheduler is
  called only when two or more legal nodes are runnable.
- In `deterministic-all` mode, the controller selects the entire legal frontier without a scheduler
  model call. The current controller still executes that selection serially; a graph frontier is
  not a claim of physical concurrency.
  Independent workflow owners can run concurrently through the outer managed dispatcher in
  `pm-dispatch`, with separate workspaces and run directories. Its cancellation signal and
  parent model-call/wall-time allocation narrow this controller's execution; they never change
  the admitted design, the native retry policy, or terminal-review meaning.
- The scheduler cannot emit shell commands, state patches, budgets, verdicts, or success. Its only
  accepted action is a non-empty subset of the current legal frontier.

## v1 capability boundary

Supported:

- fresh native CLI invocations;
- typed state patches checked against each node's declared writes;
- deterministic conditional edges;
- sequentialized fan-out plus explicit `waitFor` fan-in;
- machine-gated review with exact outcome routes, review coverage of workspace writers, and a
  reviewer whose resolved provider family differs from every reviewed writer;
- controller-owned state-bound completion: final review inputs, native receipts, mechanical byte
  evidence, acceptance, frozen control-plane bytes, and final workspace state are bound before the
  protected proof is finalized;
- bounded model and scheduler calls, reserved before dispatch and reconciled with the native-call journal;
- explicit per-node deadlines, process-group cleanup, and no implicit workflow-wide cutoff;
- bounded recovery inside one logical activation: deterministic failure classification,
  provider session-id tracking when available, heartbeat/inactivity detection, bounded backoff,
  and a `continue` prompt for retryable transport, throttling, or stall failures; sessionless
  providers explicitly restart a fresh turn under their declared provider-level profile;
- atomic state checkpoints and append-only JSONL observability whose paths reject symlinks;
- fail-closed resume: completed nodes are retained; an interrupted in-flight node becomes
  `AMBIGUOUS_SIDE_EFFECT` and requires reconciliation.

Not yet supported:

- a provider-native persistent conversation across nodes;
- recovery of authentication, permission, malformed-output, permanent-model, or unknown successful
  output failures (an unexpected non-zero process exit is retried only within the native four-retry
  cap);
- recovery for a sessionless provider that has not declared an audited provider-level timeout and
  backoff profile;
- cyclic or stateful retry loops (unroll a finite DAG only when that preserves the canonical contract);
- physical concurrent branch execution or concurrent workspace writers;
- an operating-system sandbox between workflow nodes. Write scopes, byte evidence, hooks, and
  review are integrity controls, not same-user process isolation.

The harness registry declares these as unsupported. When a design requires them, fail closed until
the built-in controller implements and tests equivalent semantics. Do not weaken the design or
re-enable a disabled legacy adapter to force execution.

## Mapping shape

```json
{
  "schema": "subagent-workflow/v1",
  "designPath": ".workflow/designs/example.workflow.json",
  "designSha256": "<sha256>",
  "inputPath": ".pm/round_input.json",
  "reasoningPosture": "balanced",
  "scheduler": {
    "role": "smol",
    "mode": "ambiguous-only",
    "maxCalls": 2,
    "timeoutSeconds": 3600,
    "reads": ["routingHint"]
  },
  "handlers": {
    "seed": {"type": "input", "bindings": {"task": "task"}},
    "worker": {"type": "agent", "prompt": "... {{task}} ..."},
    "merge": {"type": "template", "values": {"summary": "{{workerOutput}}"}},
    "success": {"type": "terminal"}
  }
}
```

Supported handlers are `input`, `agent`, `decision`, `increment`, `template`, `command`, and
`terminal`. A `command` handler uses an argv array rather than a shell string and must return one
`WorkflowActivationOutput` JSON object. Prefer built-ins and deterministic scripts over model work.

`reasoningPosture` is required and must name a posture declared by the role policy's
`reasoningPolicy.postures`. Each activated role's task class comes from the policy's
`capabilityPolicy.taskClassRoles`, so the mapping declares the posture and the design declares the
role; neither the controller nor the scheduler invents either value.

For a state-bound mapping, `proof.executables` contains portable source declarations, never
host-specific paths or precomputed host digests:

```json
{
  "controller-runtime": {"source": "controller-runtime"},
  "python": {"source": "path", "command": "python3"},
  "git": {"source": "path", "command": "git"},
  "native-task": {"source": "native-role", "role": "task"},
  "native-advisor": {"source": "native-role", "role": "advisor"}
}
```

Every command argv0 is an exact `{{executable.<label>.path}}` placeholder. Where an audited
action accepts an executable digest, pass `{{executable.<label>.sha256}}`. Start preflight resolves
PATH commands and configured native roles to canonical executable bytes outside the project root;
the proof `python` and `git` helpers are stricter and resolve only their fixed system locations;
the resolved paths, digests, modes, and file identities enter the frozen run proof. Resume and
finalize-only re-resolve and compare that closure before any model or command replay.

## Validate and run

```bash
python3 .claude/skills/build-workflow/scripts/validate_design.py \
  .workflow/designs/<id>.workflow.json

bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate .subagent/flows/<id>.json

GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 \
  bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/<id>.json
```

`validate` launches nothing, so it enforces the static half of the contract: the canonical v3 policy,
the audited identity map, the declared posture ceiling, role routes, and executable closure. It
reports `dispatchAdmitted` and, when false, the exact reason a run would be refused — typically a
`bootstrap-unverified` catalog in a project that has not yet run the authenticated
`update-platform-config` catalog sync. A clean structural pass is not permission to start: `start`,
`--resume`, `finalize-only`, and `verify-proof` additionally require a verified catalog receipt that
covers every dispatched role and backend, and refuse before creating or touching a run otherwise.

Resume only a compatible nonterminal run:

```bash
GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 \
  bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/<id>.json \
  --resume workflow-output/subagent/<id>/<run-id>
```

Resume binds the design, mapping, role config, native route map, and input bytes. It reconciles call
reservations with `native-calls.jsonl`. Automatic provider-session recovery happens while the
controller remains alive and the node deadline remains open. If the controller itself exits while
a scheduler or node is in flight, workflow resume still closes unresolved instead of guessing that
the provider-side turn is safe to replay; inspect and reconcile its byte side effects first.

For a state-bound run that reached its successful terminal but crashed before publishing the
proof/result, finalize only the existing checkpoint. This command performs no scheduler or workflow
model call and refuses a running, incompatible, or unbound checkpoint:

```bash
GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 \
  bun .claude/skills/subagent-workflow/scripts/controller/run.ts finalize-only .subagent/flows/<id>.json \
  --resume workflow-output/subagent/<id>/<run-id>
```

The literal tier-1 authorization is required because a state-bound start or finalize may create
`.pm/workflow-reviews/<itemId>.md`. It authorizes only that controller-owned proof write; it does not
authorize changing the issue's acceptance criteria, protected project state, or another artifact.

To verify an already-finalized canonical run without writing, recreating a missing proof, or
requiring tier-1 authority, use:

```bash
bun .claude/skills/subagent-workflow/scripts/controller/run.ts verify-proof .subagent/flows/<id>.json \
  --resume workflow-output/subagent/<id>/<run-id>
```

This revalidates frozen inputs and executables, replayed controller state, the candidate, final
review prompt/response/receipt/trace, deterministic evidence and its producer, current workspace
bytes, exact protected proof bytes, and `result.json`. A valid response is the standard success
envelope with `reviewerTerminal: "COMPLETE"` and `modelCallsReplayed: 0`; any missing or changed
artifact fails closed.

## Mapping review

Native agent turns launched by this adapter use the shared
`../default-mode/references/native-cli-execution.md` contract: a 3600-second no-output/heartbeat
timeout, at most four transient retries, and 300/600/1800/3600-second backoff. Providers without
resumable sessions (currently Kimi) declare the same policy at provider level and retry a fresh
turn. Authentication, quota, invalid-request/model, malformed-output, evidence, and policy
failures stop immediately.
The `timeoutSeconds` on a workflow node is its outer graph deadline; it must leave at least 3600
seconds when the node invokes a native agent.

Before paid calls, verify:

1. mapping digest matches the neutral design;
2. every design node has exactly one handler and no unknown handler exists;
3. every model-written state field is declared in the node's `writes`;
4. scheduler calls plus workflow model nodes fit the design's `maxModelCalls`;
5. parallel-looking branches are safe when executed serially in either order;
6. `waitFor` names every required fan-in parent;
7. machine-gated review covers every relevant writer, every outcome has one fail-closed edge, and
   the reviewer resolves to a different provider family;
8. the selected scheduler role resolves from the canonical `config/workflows/models.json`
   `workflow-model-roles/v3` policy and its verified catalog receipt;
9. `reasoningPosture` names the authorized posture, and every model-node role is the lowest-cost
   qualified role inside that posture's ceiling, with a named acceptance/escalation trigger outside
   the graph; no leaf merely inherited the coordinator model;
10. selected frontier-advisor or diversity roles have their narrow trigger and bounded call count;
    adaptive Cursor advice/diversity roles pin the highest effort proven for that exact model before
    dispatch, preferring literal `max` and never changing model or effort mid-run; routine review
    still uses the native `backend=claude` Max-effort `advisor` for Codex writers or
    `codex_advisor` for Claude writers, and family-diverse advice is not treated as a vote;
11. state-bound executable declarations use only the portable source union, every command argv0 is
   covered by that closure, and tracked mapping/resource bytes contain no host installation path;
12. the mapping report records any sequentialization or unsupported semantics.
13. `reviewPolicy` is `independent` or `machine-gated`; a legacy `none` design is migrated before
    paid calls rather than treated as permission to skip terminal review.

For `independent`, also record the evidence bundle the top session will pass to the external
terminal reviewer. For `machine-gated`, verify that the final review inputs bind the declared
criteria, deterministic acceptance evidence when applicable, and final workspace bytes.

## Output

Runtime evidence is stored under `workflow-output/subagent/<design>/<run-id>/`:

- `state.json` — atomic controller checkpoint;
- `events.jsonl` — node lifecycle and failure events;
- `scheduler-decisions.jsonl` — legal frontier and accepted selection;
- `scheduler-responses/` — raw scheduler stdout/stderr plus legal-frontier and digest metadata;
- `native-calls.jsonl` — one record per logical model activation;
- `native-attempts.jsonl` — physical start/resume attempts, classification, session id, timing,
  actual argv digest, and authenticated catalog-model/executable/closure/receipt digests;
- `native-heartbeats.jsonl` — process liveness, idle time, and stdout/stderr byte counters;
- `native-responses/` — stdout, stderr, exit, termination class, and digests;
- `launch-receipts/<call>.json` — the pre-launch `workflow-native-launch-receipt/v1` for that
  activation, exactly matching the canonical schema and resolver output for its request;
- `launch-bindings/<call>.json` — the pre-launch `workflow-controller-launch-binding/v1` carrying
  controller-only run/call/execution/review provenance and the canonical receipt byte digest; the
  combined receipt/binding ledger digest is bound into the checkpoint, `result.json`, and the
  state-bound proof, and replay recomputes both from frozen inputs;
- `state-bound-candidate.json` — controller-created state-bound candidate; never sufficient by itself;
- `result.json` — standardized terminal result.

For state-bound success the controller also writes the protected PM proof named by the mapping's
`itemIdField`. `state.status == success` is not publishable until `completionPhase == finalized` and
the proof digest agrees with `result.json`. The proof begins with `TERMINAL: COMPLETE` and
`EVIDENCE-COMMITMENT: sha256:<candidate digest>`; it does not use a legacy `WORKFLOW: PASS` marker.
The candidate digest commits to frozen inputs, reconciled terminal state, final reviewer receipts,
deterministic acceptance/workspace evidence, and its producer receipt.

When `.pm/config.json` selects `ui_mode: linear`, PM-item state-bound completion is unavailable and
fails closed before a run or native activation. The controller has no authenticated, controller-owned
Linear mutation/readback producer and no signature trust anchor, so local receipt bytes cannot prove
the remote mutation even when every field and self-digest is consistent. The strict
`linear-ui-mirror-receipt/v1` validator remains only as a future wire contract; it is unreachable
from completion authorization until authenticated controller-owned evidence exists. `interactive`
and `file` modes have no external mirror, require no UI receipt, and remain supported.

The controller writes a state-bound proof only for a reviewed success. A fresh, different-provider,
terminal-classifying in-graph review receipt that binds the final reviewed state fulfills terminal
review for a machine-gated stop even when that run does not declare a state-bound PM completion
proof; do not add a duplicate external reviewer. An independent run, or a machine-gated stop that
lacks such a fresh receipt, returns evidence to the top session, which must dispatch a terminal
reviewer before stopping. That reviewer emits exactly one of
`TERMINAL: COMPLETE`, `TERMINAL: REVISE`, or `TERMINAL: HUMAN_BLOCKED`, plus an
`EVIDENCE-COMMITMENT`. `HUMAN_BLOCKED` is reserved for a human-exclusive authorization, decision,
credential, or external action and additionally requires `HUMAN-REQUEST-KIND` and a concrete
`HUMAN-REQUEST`, together with evidence that safe agent-side alternatives were exhausted. Test
failure, budget exhaustion, provider trouble, and unfinished work are `REVISE`, not human-blocked.

Budget exhaustion, invalid scheduler output, stale design bytes, a native failure, and ambiguous
side effects all close unresolved. None may reach the success terminal.

Use `native-calls.jsonl` model/effort records and provider billing telemetry, when exposed, to
evaluate the role allocation after the run. The role file's relative cost is a routing rank, not a
dollar conversion. Missing token or billing data is reported as unknown.

## Self-evolve

After a run, the executing workflow owner records its own adapter residue under
`.memory/workflows/<design>.md` or its worker-owned inbox file. If the run has already returned to a
top session, the top dispatches one bounded closing owner to make that write and only inspects the
diff; it never appends memory itself. Promote a general rule only after fault-injection or repeated
live evidence supports it and the applicable review contract passes.
