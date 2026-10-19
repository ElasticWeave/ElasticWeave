# Built-in subagent mapping for review-gated-loop

## Scope

- Design: `.workflow/designs/review-gated-loop.workflow.json`
- Design SHA-256: `eec29d7923b5acd237a0b8426e3f11cc795ad55753ad99111adf73a55ce9a625`
- Mapping: `.subagent/flows/review-gated-loop.json`
- Mapping SHA-256: `10a79cf951d3e72f1a5ef3ccec29139b5d16066d37e9989f0d4e57f2341175ad`
- Contract input: `.subagent/fixtures/review-gated-loop.input.json`
- Resource closure: `.workflow/resources/review-gated-loop/manifest.json`
- Execution adapter: built-in `subagent-scheduler`

This mapping does not depend on the runtime, graph state, session objects, or routing results of any other
workflow harness. Production runs should use `--input` to supply reviewed task input; the tracked fixture
validates only the input schema, does not indicate that the workflow has executed successfully, and does
not generate proof in advance.

## Per-node mapping

| Design node | built-in handler | Description |
|---|---|---|
| `seed` | `input` | Binds `itemId`, `task`, `acceptance`, and bounded task-specific reward-hacking hypotheses, then mechanically initializes all controller state |
| `capture-baseline` | `command` | Runs the frozen evidence action/helper and may only add `artifacts/baseline.json` |
| `implement-r0..r2` | `agent` / role `task` | Uses an independent native invocation for each round; the final two rounds continue only through checkpoint state and do not reuse the provider conversation |
| `capture-evidence-r0..r2` | `command` | Runs acceptance against the same baseline and captures byte-level evidence; each round has exactly one declared run artifact |
| `review-r0..r2` | `decision` / role `advisor` | Fresh, read-only, cross-provider review; strictly outputs `PASS/REVISE/BLOCK` |
| `advance-r0..r1` | `increment` | Increments the revision count and records the reviewer that produced the feedback |
| `exhaust-revisions` | `increment` | Mechanically records third-round exhaustion and `forcedReason`; may proceed only to unresolved |
| `archive-proof` | `template` | Declares only the run-scoped candidate path that the controller will create and gains no protected write permission |
| `success`,`unresolved` | `terminal` | Terminal semantics are determined entirely by the design and controller |

The stable body of the implementation and review prompts resides in the resource closure. The mapping prompt
passes only the state declared by the design and requires each node to read the corresponding resources in full.
The reviewer receives inline evidence, path, digest, state binding, and the scheduler/main-supplied
task-specific reward-hacking hypotheses; before invoking the reviewer, the controller rechecks the
evidence file bytes and writes this input binding to the machine-review ledger. Hypotheses are bounded
to five unique strings of at most 2048 bytes each and are treated as untrusted review leads, never as
proof of a finding or its absence.

## Scheduling and budget

This design is a statically unrolled three-round DAG. The frontier on every legal path contains exactly one
node, so:

- `scheduler.mode=ambiguous-only`;
- `scheduler.maxCalls=0`;
- the scheduler makes no model calls;
- the worst-case path contains three `task` calls and three `advisor` calls, exactly covered by
  `maxModelCalls=6`.

The deadline for every executable design node is explicitly 7200 or 18000 seconds, and the deadline for the
controller current-state helper is 7200 seconds. The design no longer declares a global wall-time limit, and
the controller has no hidden workflow watchdog; lifecycle boundaries are determined by each node's deadline
and the finite DAG.

## Machine gate and proof closure

Every workspace-writing attempt that could reach success is covered by the corresponding `review-r*`. The
mapping declares only the `task` and `advisor` roles and does not pin a provider or local CLI path; based on
the actual native provider family identified during preflight, the controller rejects a reviewer from the
same provider family.

The state-bound proof freezes and verifies:

- the design, mapping, role config, native map, and input for this run;
- the controller, native core, proof module, evidence action, and Python helper;
- the implement/review prompt resources and manifest;
- the controller runtime, Python, Git, `task`, and `advisor` executable snapshots resolved live during
  preflight;
- the final evidence/state digest, review prompt/input/response receipt, native trace, and model-call ledger.

Through `runWrites`, each of the four command handlers declares one non-overlapping run-scoped JSON file. The
run must fail closed if an external process adds, deletes, or modifies other controller evidence.
`archive-proof` does not receive `GENERAL_AGENT_*`; only after the controller reaches the success terminal,
confirms an empty frontier, and completes the final workspace re-audit may it use tier-1 authority to write
`.pm/workflow-reviews/<itemId>.md` idempotently.

The project mapping and resource manifest store only the portable source union. During preflight on every
start, the controller resolves these sources to canonical paths and SHA-256 digests and freezes the snapshot
into the run-local evidence closure; it does not write local paths or executable digests back to project JSON.
Command handlers read the frozen values only through `{{executable.<label>.path}}` and
`{{executable.<label>.sha256}}`.

## Recovery semantics

An atomic checkpoint is saved after every activation. During recovery, the controller rechecks the frozen
bytes, event log, model-call ledger, command receipts, machine-review ledger, and completed DAG prefix. While
the controller remains alive, recoverable network failures, service rate limits, and heartbeat stalls inject
`continue` into the same provider session; a physical recovery attempt does not increase the logical model-call
count. If the controller itself exits while a writer is running, that writer is still not replayed automatically;
instead, it is closed as unresolved pending manual verification of the side effect. `finalize-only` permits
candidate/proof finalization to be redone only from a state-bound checkpoint that has already reached the
success terminal, and does not invoke the scheduler or any model.

## Validation

```bash
python3 .claude/skills/build-workflow/scripts/validate_design.py \
  .workflow/designs/review-gated-loop.workflow.json

bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate \
  .subagent/flows/review-gated-loop.json
```

The neutral design currently validates as clean. The mapping contract required by the built-in controller is
also in place: `session=state-mediated` still starts a fresh native invocation and reads prior state only from
the checkpoint; `runWrites` verifies files added by command handlers byte for byte; and bare native CLI names are
resolved to frozen canonical executables before invocation. The current CLI validation returns `clean=true`.

The independent mapping test is located at
`.claude/skills/subagent-workflow/tests/controller/review_gated_loop_mapping.test.ts`. It covers one-to-one
design/handler correspondence, zero scheduler calls for a single-node frontier, all deadlines exceeding 600
seconds, the command artifact allowlist, complete resources and portable executable sources, review evidence
input, the fixture carrying no verdict/proof, and the active mapping having no dependency on an external harness
or local absolute paths.
