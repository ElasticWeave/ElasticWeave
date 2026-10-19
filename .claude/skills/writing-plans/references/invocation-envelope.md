# Skill invocation envelope

Use this envelope whenever one skill, agent, or workflow invokes another. The user request and its
explicit authority remain the source of truth; an envelope can narrow that authority, never expand
it.

```text
schema: skill-invocation-envelope/v1
invocationId: stable id for this assignment
invocationMode: root | child
authority: read_only | write | experiment
terminalOwner: invocationId of the one outer lifecycle owner
workerClass: execution | pure-collector | terminal-reviewer | machine-gated-node |
             post-verdict-mechanical
activeSkillStack: ordered skill names, outermost first
budgets: remaining model calls, wall time, retries, experiments, compute, and external spend
requiredReceipts: evidence the caller needs back
parentReceipts: immutable authority/resource/review receipts supplied by the caller
iterationPolicy: immutable PM issue snapshot, or the conservative legacy autonomous default
humanFeedbackReceipt: null, or immutable feedback bound to one completed observation checkpoint
```

## Derivation rules

1. A root invocation sets `terminalOwner` to its own `invocationId`. Every child inherits that value,
   appends its skill to `activeSkillStack`, and returns to its caller; it does not become a second
   lifecycle owner.
2. Authority is monotone. `read_only` permits inspection and disposable non-mutating checks only;
   `write` additionally permits the explicitly scoped project edits and ordinary validation;
   `experiment` additionally permits the explicitly scoped empirical trials and declared resource
   use. External writes, credentials, paid services, and destructive operations still need their
   own explicit user-authority receipt. Ambient capability is not authority.
3. A child receives no more authority or budget than its parent. Missing authority defaults to
   `read_only`; missing experiment or external-effect receipts fail closed.
4. Before invoking a skill already present in `activeSkillStack`, return a typed finding to the
   nearest owner instead of re-entering it. A deliberately iterative algorithm stays inside one
   invocation and its declared budget; it does not recursively invoke the same skill.
5. Each child returns only its requested artifact plus receipts for evidence, mutations, resource
   use, remaining budget, unresolved items, and optional `learningCandidates`. It does not issue the
   outer assignment's terminal verdict.

## Iteration policy and human feedback

Iteration policy controls cycle shape, not authority, model effort, or completion review. The root
copies the exact digest-bound PM issue snapshot into `iterationPolicy`. Every child inherits those
bytes unchanged: a child cannot select another mode, renew a lease, reinterpret an expired lease,
or replace the snapshot from a later `.pm/config.json`. An older assignment with no snapshot uses
`autonomous`; a partial or invalid snapshot fails closed.

An active `human_guided` snapshot permits the short cycle described by the planning and workflow
contracts. An expired snapshot with `human_unavailable_fallback=autonomous_review` keeps the
selected mode as `human_guided` but sets the effective mode to `autonomous` and records the expiry
before dispatch. An expired snapshot with `pause` permits no new planning-cycle dispatch; the
already-authorized cycle retains its bound snapshot while its workers, evidence collection,
fan-in, and required reviews are drained. A checkpoint or lease expiry never cancels those
obligations or completes the outer request. Neither fallback
weakens authority, safety, evidence, code/alignment review, or the outer terminal review.

`humanFeedbackReceipt` is `null` until the outer owner receives real feedback for a completed
observation checkpoint. A resumed planning invocation carries the checkpoint artifact path and
SHA-256, the exact feedback, its receipt time, and a self-digest. The caller must verify the
checkpoint bytes before dispatch. Feedback for another checkpoint, unbound prose, or a receipt
with a bad self-digest cannot authorize the next human-guided plan. A finite Humanize run returns
at the checkpoint; this envelope starts a new invocation and does not claim in-graph durable
resume support.

The machine-checkable field contract is:

```json
{
  "schema": "skill-invocation-iteration-policy/v1",
  "modes": ["autonomous", "human_guided"],
  "humanUnavailableFallbacks": ["pause", "autonomous_review"],
  "issueSnapshotFields": [
    "iteration_mode_selected",
    "iteration_mode_effective",
    "human_checkin_lease_minutes",
    "human_unavailable_fallback",
    "human_checkin_lease_expires_at",
    "human_checkin_lease_status",
    "iteration_mode_decision_at",
    "iteration_mode_snapshot_sha256"
  ],
  "humanFeedbackReceiptFields": [
    "schema",
    "checkpoint_artifact",
    "checkpoint_sha256",
    "checkpoint_id",
    "human_feedback",
    "received_at",
    "receipt_sha256"
  ],
  "invariants": {
    "legacyDefault": "autonomous",
    "childMayMutateSnapshot": false,
    "modeChangesAuthority": false,
    "modeChangesTerminalReview": false,
    "humanGuidedRequiresLiveLease": true,
    "nextPlanRequiresBoundFeedback": true
  }
}
```

## Lifecycle ownership

Only the invocation whose `invocationId == terminalOwner` may initiate the assignment's final
review/proof route and its one closing memory/self-improvement beat. Schedule memory before the
final evidence seal so the reviewer binds the final authorized state. A top coordinator may
delegate the exact memory mutation, but that bounded worker is executing the owner's decision; it
must not start another memory beat or reviewer.

An already-valid machine-gated proof is a receipt consumed by the terminal owner, not a reason to
commission a duplicate external review. Domain checks such as proof-step review, source validation,
or a plan review are nonterminal child receipts.

The worker-class policy is mechanically represented here for context-contract tests:

```json
{
  "schema": "skill-invocation-lifecycle/v1",
  "workerPolicies": {
    "execution": {"terminalReview": "owner-only", "memory": "owner-only"},
    "pure-collector": {"terminalReview": "none", "memory": "none"},
    "terminal-reviewer": {"terminalReview": "none", "memory": "none"},
    "machine-gated-node": {"terminalReview": "none", "memory": "none"},
    "post-verdict-mechanical": {"terminalReview": "none", "memory": "none"}
  }
}
```

- A `terminal-reviewer` returns its verdict directly.
- A `pure-collector` returns evidence and provenance only.
- A `machine-gated-node` performs its declared graph node and returns its state-bound receipt; it
  does not independently review the graph or write memory.
- A `post-verdict-mechanical` worker performs only the exact authorized transition or external
  reconciliation and returns its receipt. It never mutates the reviewed artifact.

If an older skill's local lifecycle prose conflicts with this reference, apply this envelope and
return the conflict as evidence instead of inventing a second terminal owner.
