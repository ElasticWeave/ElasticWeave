# Workflow design contract

Use this worksheet before authoring or materially changing a platform-neutral workflow design.
Keep answers short and testable. Delete sections that do not apply.

## Dispatch

- Goal:
- Acceptance criteria (explicit issue/design requirements):
- Deterministic acceptance evidence, if any (argv, expected status/output, evidence path):
- Workspace:
- Constraints:
- Admission reasons:
- Why a native subagent is insufficient:
- Demotion condition:
- Terminal review policy and reason (`independent` external reviewer or `machine-gated` in-graph):
- Top-session action after workflow return:
- Estimated / maximum model calls:
- Input/output token and external-compute budgets; optional global wall-time cap:
- Required harness capabilities (closed vocabulary; distinguish structurally derived from
  lifecycle-declared requirements):
- Active adapter requirement (built-in subagent scheduler or fail closed):

## Iteration policy (optional for non-iterative designs)

- Canonical mode (`autonomous` or `human_guided`; never `Fast` in the artifact):
- Outer policy snapshot inherited by this design; nested children cannot renew or switch it:
- Why this graph is iterative, and the demotion condition if it is not:
- `iterationPolicy` state pointers (disposition, evidence, risk triggers, checkpoint, resume):
- Reducer node and deterministic reduction evidence:
- Autonomous idea-review node and evidence, or human-guided minimum-validity gate:
- Human-guided lease duration (5–1440 minutes), expiry/status state, and fallback (`pause` or
  `autonomous_review`):
- Risk triggers that force targeted idea review or autonomous fallback:
- Checkpoint terminal and exact resume evidence required before another planning cycle:
- Review work retained in both modes; idea-level work skipped only under human-guided when no risk
  trigger is present:

## Outcomes

| Outcome | Meaning | Terminal node | Evidence required |
|---|---|---|---|
| Success | | | |
| Unresolved / escalate | | | |
| Hard failure | | runtime abort | |

The graph outcomes above are not the final session artifact. Record how a terminal reviewer maps
the evidence to `COMPLETE`, `REVISE`, or the narrowly defined `HUMAN_BLOCKED` result.

| Session terminal | Required evidence | Human request kind | Concrete request |
|---|---|---|---|
| `COMPLETE` | `EVIDENCE-COMMITMENT: sha256:<digest>` | n/a | n/a |
| `REVISE` | `EVIDENCE-COMMITMENT: sha256:<digest>` plus findings | n/a | n/a |
| `HUMAN_BLOCKED` | `EVIDENCE-COMMITMENT: sha256:<digest>` | authorization / decision / credential / external-action | |

## State contract

| Field | Type | Initial value/source | Writer | Consumers | Evidence or decision |
|---|---|---|---|---|---|
| | | | | | |

- Input/schema version or compatibility rule, if long-running:
- Seed/reconciliation behavior on a new bounded attempt:

## Node contracts

| Node | Type | Role | Session semantics | Required skills | On-demand skills | Reads | Writes | Workspace effect | Replay/idempotency key | Timeout | Failure behavior |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | |

For every model node, also record:

- the judgment that cannot be deterministic;
- its exact structured output;
- the evidence supplied to it;
- the maximum number of times it can activate.

## Routes

| From | To | Positive predicate | Missing/malformed behavior | Why this route exists |
|---|---|---|---|---|
| | | | | |

## Loops

| Cycle | New evidence per pass | Counter + increment node | Cap | Cap exit | Forced flag |
|---|---|---|---|---|---|
| | | | | | |

## Optimization search (delete if not applicable)

- Idea-board artifact and stable run id:
- Macro idea vs micro hypothesis boundary:
- Conceptual deduplication key:
- Allowed idea states:
- Minimum evidence required before `EXHAUSTED`:
- Exploration reserve or non-zero exploration rule:
- Selection objective: performance / uncertainty / novelty / cost:
- Stagnation signal and threshold:
- Plateau action: backtrack / activate another cluster / crossover / community refresh:
- Coverage conditions required for success:

| Idea field | Type | Writer | Selection use | Preserved after pruning? |
|---|---|---|---|---|
| | | | | |

| Idea transition | Predicate | Required evidence | Reactivation condition |
|---|---|---|---|
| | | | |

## Fan-out / fan-in

- Why branches are independent:
- Writer isolation:
- Logical frontier or batch cap (do not imply physical concurrency):
- Reducer:
- Deterministic merge rule:
- Missing, duplicate, or conflicting output behavior:

## Side effects and recovery

| Side effect | Stable key or reconciliation check | Ambiguous-success handling | Compensation, if any |
|---|---|---|---|
| | | | |

## Evidence and evaluation

- Pre-write baseline:
- Mechanical acceptance from explicit criteria (argv/status/output digests, not a default-mode-created gate):
- Covering cases for graded quality:
- Per-case trace/summary fields:
- Final byte/state binding:
- Reviewer input and allowed verdicts:
- Evaluation ladder: deterministic validity → cheap proxy → covering acceptance → full review:
- Quality-cost Pareto fields:
- Context compaction rule (summary/delta/artifact pointer):

## Harness mapping contract

| Required capability | Why required | Native / adapter / unsupported | Evidence or test |
|---|---|---|---|
| | | | |

- Canonical design path and digest:
- Registry source (`project` override or `bundled`) and schema validation result:
- Built-in adapter selection reason:
- Target artifact:
- Deliberate adaptations or semantic gaps:
- Adapter validation command:
- Tier-1 start command:
- State-bound finalize-only command, if applicable:

## Preflight

- [ ] The graph still passes the workflow admission test.
- [ ] If the design iterates, `iterationPolicy` is explicit, canonical, and validated; otherwise
  its absence is intentional for a finite non-iterative graph.
- [ ] `autonomous` carries idea-review evidence; `human_guided` carries the minimum validity gate,
  bounded lease, fallback record, and unresolved post-reduce observation checkpoint.
- [ ] A human-guided route cannot reach another model/planning node after reduction without bound
  resume evidence; no human node is present in the headless graph.
- [ ] Nested workflows inherit the exact mode/lease/fallback snapshot; only the outer owner records
  and applies a fallback transition or renewed human lease.
- [ ] State is typed, minimal, and separates evidence from decisions.
- [ ] Every node has one responsibility and declared reads/writes.
- [ ] Every node skill is declared as required or on-demand; the two sets do not overlap.
- [ ] Required skill bodies are justified against token cost; conditional methods are on-demand.
- [ ] Every success edge requires explicit positive evidence.
- [ ] Every cycle has an unconditional cap exit and fits the model-call budget.
- [ ] Underexplored ideas cannot be mislabeled as exhausted or convergence.
- [ ] Optimization success includes a coverage audit, not only an incumbent score.
- [ ] Token, model-call, per-node deadline, and external-compute ledgers are explicit; a global wall-time cap is present only when lifecycle policy requires one.
- [ ] Expensive evaluation is gated by cheaper evidence without making proxy failure conclusive too early.
- [ ] Every fan-in has a deterministic merge rule.
- [ ] Every side effect is safe under a new attempt.
- [ ] Acceptance runs after the final writing node.
- [ ] `reviewPolicy` is `independent` or `machine-gated`; legacy `none` is not used.
- [ ] Independent workflow success returns to the top session for a fresh external terminal review.
- [ ] Machine-gated success is controlled by a fresh evidence-bound final reviewer.
- [ ] `HUMAN_BLOCKED` is possible only for authorization, decision, credential, or external action and carries a concrete request.
- [ ] All intended nodes and terminals are reachable.
- [ ] The canonical design contains no harness syntax, command, or model id.
- [ ] `validate_design.py <design>.workflow.json` prints `DESIGN: clean`.
- [ ] The selected adapter covers every required capability without a silent downgrade.
- [ ] Declared capabilities contain every validator-derived requirement and no contradictory
  structure-bound claim.
- [ ] The selector reports a valid mapping schema, canonical design digest, mapping-byte digest,
  and report/manifest digest binding before start.
- [ ] The mapped artifact passes its harness-specific validator and branch tests.
