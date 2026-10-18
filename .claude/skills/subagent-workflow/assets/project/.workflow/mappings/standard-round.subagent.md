# standard-round → built-in subagent scheduler mapping

## Status

This is the active built-in mapping for the statically unrolled canonical design. It invokes only
the built-in controller and its configured native role executors.

## Source binding

- Design: `.workflow/designs/standard-round.workflow.json`
- Design SHA-256: `5dfecfa9ffdb430a7dfe083b78c42029d10416f2a79d467fe96253474fc06992`
- Mapping: `.subagent/flows/standard-round.json`
- Mapping SHA-256: `fa01af6be1962fe4cdf8a36f27702c48f8a3302e54292e5b4ca842713a2e4771`
- Fixture input: `.subagent/fixtures/standard-round.input.json`
- Neutral resource manifest: `.workflow/resources/standard-round/resources.json`
- Resource-manifest SHA-256: `7142c90e508e160b00573db452d51a1495538538604914dd47572c2f8dde0ade`
- Mapping builder/checker: `.workflow/resources/standard-round/build_mapping.mjs`

The mapping has one handler for each of the 122 design nodes and rejects an unknown or missing
node. The tracked fixture is for validation only. A real run must pass the reviewed project input
with `--input`; validation must not be mistaken for authorization to start a workflow.

## Scheduler and model routes

The scheduler policy is `ambiguous-only` with `maxCalls: 0` and no state reads. Every reachable
frontier in the canonical product-state DAG is a singleton, so the controller advances it
mechanically. An unexpected ambiguous frontier cannot spend a scheduler call and therefore fails
closed. The mapping references only the configured roles `task`, `plan`, and `advisor`; concrete
models and providers remain in `config/workflows/models.json` and the native route map. The mapping
declares `reasoningPosture: "balanced"`, which is exactly the ceiling those three roles occupy; the
controller refuses any role outside it and refuses to guess a posture.

The canonical graph has 193 terminal paths. Its largest path activates 23 model nodes, below the
design cap of 24. Static unrolling limits implementation to three traversals and permits one
additional self-evolution retry globally. There is no cyclic or provider-persistent session
semantics in this mapping.

## Reward-risk admission contract

Before admitting a real run, the main session or PM must inspect the task, its acceptance argv,
and any available baseline or measurement plan, then write a bounded list of concrete
reward-hacking hypotheses into the `rewardHackingRisks` input. The list may contain at most five
unique strings of at most 2048 UTF-8 bytes each. Pass `[]` only after that analysis finds no
specific hypothesis worth testing; an empty list does not waive the reviewer's generic checks.
These entries are untrusted hypotheses, not findings: every reward-hacking reviewer receives
them in its prompt and must verify or disconfirm each against the actual workspace and evidence.

## Handler mapping

| Design family | Built-in handler | Contract |
|---|---|---|
| `seed` | `input` | Binds `itemId`, `task`, `acceptanceArgv`, and bounded `rewardHackingRisks`; initializes every controller field and the first empty report. |
| `capture-baseline` | `command` | Runs the frozen reward-evidence action and owns only `artifacts/baseline.json` in the run directory. |
| `implement-*` | `agent` / `task` | Writes the project workspace and returns the declared implementation report. |
| `capture-evidence-*`, `capture-final-evidence-*` | `command` | Runs mechanical acceptance with a 17400-second inner bound and writes one node-unique evidence file. |
| `reward-hack-review-*`, `final-reward-hack-review-*` | `decision` / `advisor` | Read-only, cross-provider PASS/REVISE review bound to mechanical evidence. |
| `cross-review-*` | `decision` / `advisor` | Read-only implementation review. |
| `milestone-review-*` | `decision` / `advisor` | Read-only first-principles APPROVE/REVISE review. |
| `self-evolve-*` | `agent` / `plan` | Bounded workspace-writing self-evolution; reads built-in run evidence, not legacy activation state. |
| `validate-lemmas-*` | `command` | Runs the frozen deterministic lemma action; declares no run-directory write. |
| `evolve-review-*` | `decision` / `advisor` | Read-only APPROVE/REVISE review of the actual self-evolution bytes and validation. |
| record/counter/cap nodes | `template` or `increment` | Deterministically records the source review, counters, retry use, and cap reason. |
| `archive-proof` | `template` | Declares the controller-owned candidate name; it does not finalize proof itself. |
| terminals | `terminal` | Preserve canonical success and unresolved meanings. |

Every executable node has an explicit two-hour or five-hour deadline. Evidence commands and other
long nodes receive five hours, with a 17400-second inner acceptance bound. The design intentionally
omits a global wall-time cap. Retryable native transport, throttling, and heartbeat-stall failures
resume the same provider session inside the original logical activation; they do not increment
`maxModelCalls`. Unknown, authentication, permission, and sessionless failures still fail closed.

## Resource and executable closure

Prompts live under `.workflow/resources/standard-round/prompts/` and are inlined by the mapping
builder. The builder deterministically inserts exactly one controller-owned
`{{review.commitment}}` marker after the workspace-state binding in every reward-hacking review
handler. The prompts otherwise use the built-in WorkflowActivationOutput contract and contain no
`completedActivations` or external runtime-state dependency.

The state-bound proof mapping freezes the design, mapping, role file, native map, input, controller,
launch resolver, native core, proof module, resource manifest, reward-evidence helper/action, lemma action/validator,
all prompt files, the mapping checker, and every required or on-demand skill body declared by the
design. The tracked mapping declares portable executable sources for the controller runtime,
Python, Git, and the `task`, `plan`, and `advisor` native roles. At preflight, the controller
resolves those declarations against the live runtime and role configuration, then freezes their
canonical paths and byte identities in run-local evidence. It does not write host paths or
executable digests back into the mapping or resource manifest. Command handlers receive only
controller-rendered `{{executable.<label>.path}}` and `{{executable.<label>.sha256}}` values;
outputs are allowed only at the node's exact `runWrites` path.

All eight final reward-review nodes that can route to `archive-proof` are listed explicitly in
`proof.finalReviewNodes`; their eight direct mechanical predecessors are listed in
`proof.evidenceProducerNodes`. Every final reviewer contains exactly one controller commitment
marker. The controller, not a model node, creates and audits the state-bound candidate and performs
protected finalization.

## Validation

Run these checks before start:

```bash
bun .workflow/resources/standard-round/build_mapping.mjs --check

python3 .claude/skills/build-workflow/scripts/validate_design.py \
  .workflow/designs/standard-round.workflow.json

bun test .claude/skills/subagent-workflow/tests/controller/standard_round_mapping.test.ts

bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate \
  .subagent/flows/standard-round.json
```

The first check reconstructs the mapping from the canonical design and neutral prompt resources.
The independent Bun test checks exact node coverage, role and scheduler bounds, deadlines, final
review coverage, run-owned artifacts, executable closure, and absence of active legacy semantics.
