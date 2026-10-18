---
name: run-workflow
description: "Select, map, and run an admitted platform-neutral workflow through a compatible adapter; use when genuine graph semantics are required."
---
> **Model tier:** LOW — selection and dispatch are mechanical; design judgment lives in
> `$build-workflow`, and executable mapping lives in the selected adapter skill.
>
> **Memory abstraction:** Validate the neutral design, select only a capability-compatible harness, install that harness on demand, and consume one standardized terminal result instead of supervising platform internals.

# run-workflow — validate the selected route, then get out of the middle

Workflows are an escalation lane, not the default delegation mechanism. For platform-neutral work,
the canonical design lives under `.workflow/designs/` and harness artifacts are derived mappings.
Trusted native Humanize Python is a separate authoring lane owned by `$write-humanize-flow`; never
present native source as a generated mapping or infer the narrow adapter's guarantees from it.

## Humanize boundary

An ordinary Default or Fast session runs admitted workflows through the built-in subagent
scheduler. Humanize is an explicit opt-in execution boundary, not a second default scheduler. Use
`$humanize-workflow` only when the selected neutral design is deliberately pinned to the Humanize
adapter, or when a trusted Humanize flow is already running and is guaranteed to call a validated,
digest-bound child flow as its sub-flow. Merely authoring a Python flow, mentioning Humanize, or
wanting a multi-step task does not satisfy that condition; use `$write-humanize-flow` to author the
flow and `$subagent-workflow` for ordinary runtime execution. If the child-flow guarantee cannot be
shown from the flow contract and validator evidence, keep the runtime on the built-in scheduler.

## 1. Confirm workflow admission

Require at least one:

- three or more dependent model leaves exchange state;
- fan-out/fan-in or consistent batch scheduling;
- a statically unrolled bounded retry/review graph with machine-controlled routes;
- explicit lifecycle, stop, checkpoint, or long-running repetition boundaries;
- protected control-plane changes need machine-enforced review;
- a reviewer verdict must select a different next node.

Otherwise use a native subagent. Record reasons, demotion condition, review policy, completion proof,
and all budgets in the canonical design.

Do not infer admission from slash-command naming or internal procedural stages. A normal
`$generate-insight` invocation keeps one lemma and its bounded experiment/rethink record under one
native owner, so it does not need an adapter mapping. Route it here only when the actual task adds
independent graph responsibilities, fan-out/fan-in, machine-controlled verdict routing, or another
admission reason above.

## 2. Validate the design

```bash
python3 .claude/skills/build-workflow/scripts/validate_design.py \
  .workflow/designs/<name>.workflow.json
```

Reject harness commands, harness field names, and concrete model ids in the design. `$build-workflow`
owns design corrections; do not repair intent while mapping.

## 3. Select an active adapter by capability

Resolve the project root from the design path, not the shell's current directory. Read the project
override `.workflow/harnesses.json` when present; otherwise use the registry bundled under
`subagent-workflow/assets/project/.workflow/harnesses.json`. A present but malformed override fails
closed; it never falls through to the bundled registry.

Both registries use `workflow-harness-registry/v1`. Every entry must declare its review-policy
allowlist and one explicit status for every capability in the closed vocabulary from
`build-workflow/scripts/validate_design.py`. Status is either `native[-detail]`,
`adapter-<tested-mechanism>`, or `not-supported[-detail]`; an arbitrary positive-sounding string is
not support.

Run the deterministic selector before mapping or starting:

```bash
python3 .claude/skills/run-workflow/scripts/select_harness.py \
  .workflow/designs/<name>.workflow.json \
  [--harness subagent-scheduler|humanize]
```

1. If the user pins a harness, verify it covers every requirement. Report a gap rather than
   silently falling back.
2. In `auto`, retain only enabled adapters whose requirements are native or supplied by focused,
   tested adapter code. The active registry enables `subagent-scheduler` and the narrow
   `humanize` adapter.
3. Require the registry's configured default to cover every explicit and deterministically derived
   requirement. The validator derives graph-visible requirements such as agent invocation, fresh
   sessions, structured decisions, conditional routes, fan-out/fan-in, node skill loading,
   bounded cycles, machine-gated review, and state-bound proof. Unknown, omitted, or contradictory
   declarations fail before adapter ranking.
4. Read `mappingExists`, `mappingRequired`, and `mappingVerification` from the selector result.
   `mappingExists=true` means the selector statically verified the mapping schema, canonical design
   path/digest, mapping byte digest, project-contained non-symlinked sources and report files, and
   exactly one canonical occurrence of each required report or manifest execution/digest binding.
   For Humanize, selection uses the same full validator as
   runtime: it revalidates adapter compatibility, runs the neutral design validator, regenerates
   the only accepted source bytes, and audits the generated AST without importing or executing the
   flow. File existence or self-reported hashes alone are never enough. An absent or invalid mapping
   remains selectable for materialization but cannot start.
5. Record the decision in `.workflow/mappings/<design>.<adapter-suffix>.md`, using `subagent` for
   `subagent-scheduler` and `humanize` for `humanize`.
6. The built-in adapter needs no install. Install humanize only after selection through
   `$humanize-workflow`'s pinned isolated-venv installer.

Current adapters:

| Harness | Skill | Main fit |
|---|---|---|
| Built-in subagent scheduler | `$subagent-workflow` | Default active adapter: fresh native calls, bounded frontier selection, typed state, machine-gated review, state-bound proof, checkpoints, and observability |
| humanize2 | `$humanize-workflow` | Active only for independent-review finite DAGs needing fresh calls, typed output, finite routing, and cycle/result observability; no checkpoints, node skills, in-graph gate, or state-bound proof |

These are the only active harness routes. The Humanize entry in this table is the narrow neutral
adapter, not a promise that arbitrary native Humanize Python satisfies its mapping contract.

The selector is a planning step: it chooses a capability-compatible adapter to map and run. It may
select the configured default or an explicitly pinned adapter before that design has a valid
mapping; the result then says `mappingExists=false` and `mappingRequired=true`, with exact issues in
`mappingVerification`. The selected mapper/runtime must materialize and validate the mapping before
start. A user-pinned adapter never silently falls back. Machine-gated review and state-bound proof
are derived from governance; an adapter lacking either remains ineligible rather than weakening
completion. Disabled adapters are never candidates, including when explicitly pinned. An
unsupported design therefore fails closed rather than weakening its requirements.

The selector also checks each adapter's explicit review-policy allowlist. Humanize accepts only
`independent`; `machine-gated` and `state-bound` designs stay on the built-in controller. Never
infer stronger guarantees from the fact that a humanize flow is arbitrary Python.

## 4. Map and validate

Invoke `$subagent-workflow`. Selection is not permission to start an unmapped design: when
`mappingRequired=true`, materialize the selected artifact first; in all cases validate the mapping
against the current design before execution.

When humanize is selected, invoke `$humanize-workflow` instead. Its validator compares the neutral
design, data-only spec, manifest, and regenerated Python source bytes without importing the flow.
Humanize discovery imports Python flows and is forbidden during selection and mapping.

### Trusted native Humanize flows

Use `$write-humanize-flow` only for explicitly selected, trusted native Python such as the
materialized `default`/`slot` seed or the Deep/Auto Research references. Validate those files as
project code before execution; do not send them through the narrow neutral mapper or treat their
presence as a selected mapping.

Humanize has no project-default auto-start contract. Start the materialized root through the
explicit launcher:

```bash
python3 .claude/skills/write-humanize-flow/scripts/run_default.py \
  --root . --hmz <pinned-hmz> \
  --executor-role frontier_execute --reviewer-role advisor \
  -- "<task>"
```

The launcher consumes only the project-root `config/workflows/models.json`; it rejects `--roles`
and cannot be redirected to a caller-selected role-policy file. It resolves the two roles only from
a verified authenticated v3 catalog, enforces the exact cross-provider Max pairing, and supplies
the required write/read-only permission split. The root evaluates `load("slot")` inside each
dispatch. Editing the slot can affect the next fresh evaluation only: it does not replace an
in-flight call, refresh an already cached callable, or reload normally imported helper modules.
Validate and atomically install each replacement, then bind its digest at launch.

That launcher is specific to the two-role `default`/`slot` seed. It does not launch the multi-role
Deep/Auto Research references. Those flows require an explicit Humanize invocation with one agent
per declared role and named provider accounts; use their domain reference contract and validated
role receipt rather than copying the default pair or relying on a bare CLI family as provider
identity.

Humanize does not expose the Cursor Agent backend. Any workflow that requires a Cursor-only route
or Kimi/GLM/Grok diversity review is ineligible for Humanize and must use a capability-compatible
built-in subagent route. If Humanize was explicitly pinned, report the incompatibility; never
silently substitute a model or provider.

### Built-in subagent scheduler

```bash
bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate .subagent/flows/<name>.json
GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 \
  bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/<name>.json
```

The small scheduler selects only from the controller-computed legal frontier. Deterministic code
owns process launch, state writes, budgets, checkpoints, verdict routing, proof finalization, and
terminal meaning. This adapter is built in; there is no external harness install. `timeoutSeconds`
on every external node is an explicit deadline, may exceed 600 seconds, and is also bounded by the
design's global wall-time budget when the design declares one. There is no hidden 600-second
watchdog.

## 5. Delegate node scheduling and monitor the owner

Two independent native tasks do not need a workflow merely to run concurrently. Use platform
subagent handles or the outer batch dispatcher in `pm-dispatch`; admit a workflow only for its
actual graph semantics. Multiple admitted workflows use separate controller owners and workspaces.

A bounded workflow owner uses the selected adapter's exact command and manages graph-internal
nodes. The top keeps the owner's run/session handle in its pending-work set, monitors compact
progress and results, and routes failures without taking over node execution. "Get out of the
middle" means delegate node scheduling; it never means abandon an active workflow. Progress,
checkpoints, and observation timeouts are nonterminal for the top session. A child workflow's
terminal result settles only that child scope, subject to its review policy and parent fan-in.

A built-in state-bound run interrupted after its successful terminal may be finalized without
replaying model work:

```bash
GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 \
  bun .claude/skills/subagent-workflow/scripts/controller/run.ts finalize-only .subagent/flows/<name>.json \
  --resume workflow-output/subagent/<name>/<run-id>
```

For humanize, a candidate `TERMINAL: COMPLETE` still requires the top session's graph-external
independent terminal reviewer before stopping. `HUMAN_BLOCKED` requires a typed request kind and a
specific request; malformed or missing fields become `REVISE`.

The main session consumes compact final state and summaries, not full transcripts. The built-in
controller exposes a result equivalent to:

```json
{
  "designId": "...",
  "harness": "...",
  "terminal": "success|unresolved|failure",
  "evidence": ["artifact paths"],
  "metrics": {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0, "wallSeconds": 0},
  "nextAction": ""
}
```

Budget exhaustion or an unsupported capability is unresolved, never permission to lower acceptance.

## 6. Close and learn

- Verify the selected review policy and any state-bound proof against final bytes.
- Store active traces under the selected adapter's `workflow-output/<harness>/` directory.
- Store workflow-design lessons in `.memory/workflows/<design>.md` and adapter lessons in the
  selected skill's memory layer.
- If the failure is semantic, update the neutral design. If it is syntax/runtime-specific, update
  only the adapter and mapping report.

## Troubleshooting

- No eligible harness → return the exact missing capabilities; do not select the least-bad option.
- Built-in runtime absent → report the exact missing Bun/native-CLI prerequisite; do not weaken the
  design by selecting an incompatible adapter.
- Cursor-only route pinned to Humanize → report the unsupported backend and preserve the route;
  select the built-in scheduler only when fallback was not pinned off.
- Reviewer result malformed → take the design's fail-closed route.
- Mapping and design disagree → `mappingVerification.status=invalid`; the canonical design wins
  until the adapter regenerates the mapping and its digest-bound report/manifest.
- Project override malformed → repair or deliberately remove the override; do not use the bundled
  registry as an implicit recovery path.
- Side effect may have succeeded before a crash → reconcile before the new bounded attempt.
