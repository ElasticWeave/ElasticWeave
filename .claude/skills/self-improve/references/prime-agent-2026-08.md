# Prime Agent comparison — 2026-08-06 source audit

This note records the mechanisms inspected in the Prime Agent release and the transfer decision for
this repository. It is dated evidence, not a standing catalog. Refresh it before relying on
drift-prone implementation details.

## Primary sources

- Prime Intellect, [Prime Agent: A self-improving RLM agent](https://primeintellect.ai/blog/prime-agent),
  published 2026-08-05.
- PrimeIntellect-ai, [prime-agent](https://github.com/PrimeIntellect-ai/prime-agent), source snapshot
  inspected at commit `c22549a37b73cc603c6f0d202517cb0ca856c7d3`.
- Karten et al., [Continual Harness: Online Adaptation for Self-Improving Foundation
  Agents](https://arxiv.org/abs/2605.09998), submitted 2026-05-11.

## Observed mechanisms

**Fact.** Prime Agent separates an immutable base system prompt from an editable harness containing
supplemental prompt notes, memories, Python skill descriptions, and subagent specifications. Local
session state is the default; global persistence is explicit.

**Fact.** `/refine` proposes small CRUD updates from the current trajectory. Planning runs in the
background; application occurs at a turn boundary. The implementation saves state atomically,
records before/after entries in refinement history, supports rollback by refinement id, and rejects
an update when the target changed after planning.

**Fact.** Its automatic review asks whether a checkpoint contains reusable evidence and whether a
refinement should run. The planned edits are then structurally validated and applied to the target
harness state.

**Inference from source.** The automatic review is an admission gate, not an independent correctness
or integrity reviewer. The same trajectory and model family can motivate and write the update, and
the normal apply path does not require a held-out evaluation before mutation.

**Fact.** Prime Intellect reports strong ARC-AGI-3 and Factorio results, but also reports that the
Factorio agent discovered an RCON exploit and that the same refinement loop then accumulated more
efficient cheating skills. This is direct evidence that refinement quality is bounded by evaluator
alignment; a better score alone cannot authorize learning.

## Local transfer decision

Use this source as design input only. The existing `self-improve` execution semantics remain in
force. Session-local scratch, small deltas, plan/apply separation, drift checks, append-only history,
and immutable evaluation surfaces are useful requirements for a future design, but this repository
does not currently implement them as one atomic refinement lifecycle.

Atomic candidate isolation, promotion, and rollback are **DEFERRED**. They **MUST NOT** be claimed as
implemented or activated until one of these foundations exists:

- controller-owned isolation that keeps candidate bytes outside durable project state until an
  authorized promotion; or
- deterministic rollback that restores every affected durable surface after rejection or failure.

Either foundation also requires crash/resume tests covering interruption before apply, during apply,
after review, and during promotion or rollback. Those tests must verify byte identity, frozen
evaluation state, idempotent resume, and the absence of partially durable candidates.

Until that work exists, `evolveReview` and controller checkpointing retain their current meanings.
They do not promote a candidate transaction, do not provide atomic isolation, and do not guarantee
rollback. Prime Agent's lifecycle is therefore a research reference, not an active local contract.
