---
name: proof-rigor
description: "Validate theorem or proof derivations with structured obligations, adversarial review, and optional proof-assistant checks. Use for any proof task."
---
> **Model tier:** HIGH — rigor is the whole point


# proof-rigor — make rigor un-gameable (rigorous where it's hard, compressed where it's easy)

Inherit `../writing-plans/references/invocation-envelope.md`. The proof writer, per-step skeptic,
proof assistant, and presentation editor are child roles unless this skill is itself the outer
`terminalOwner`. Gate B is a domain acceptance receipt, not permission for each child to commission
another terminal reviewer or write memory.

The failure this prevents (documented): an LLM glosses a hard step ("clearly / it follows that"), and a naive
reviewer **fills the missing premise itself** and credits the step as proven — the LLM gap-filling / gloss-over
mode. A plain "review the proof" pass does NOT catch this. Three gates make a hidden gap impossible.

## Gate A — the WRITER's structured-proof contract
The subagent writing the proof MUST:
1. **Hierarchical numbered steps** (Lamport): `⟨1⟩1, ⟨1⟩2, … ⟨2⟩1 …`. Expand the hard steps to deeper levels;
   leave routine steps a single line — granularity is adaptive (full where hard, terse where easy).
2. **Every non-trivial step names its justification + lists its dependencies** — a cited lemma / theorem / prior
   `⟨n⟩` step + the named inference rule. **"clearly / obviously / easy to see / it follows that" are BANNED as a
   terminal justification.** (A hard step then can't be silently deleted: a later step names it as a dependency,
   so dropping it breaks the dependency graph — mechanically detectable.)
3. **Tag each step `novel-core | standard | routine`** and honor the **rigor budget**: `novel-core` → argue in
   full; `standard` → cite a named result; `routine` → one line. Over-expanding a `routine` step is a defect,
   exactly like under-justifying a `novel-core` one.
4. **Obligations ledger** — any step you cannot fully justify is marked **`ADMITTED`** and listed at the end as a
   tracked obligation ("the proof holds *if* these hold"). A gap may be DECLARED, never hidden; the proof is not
   done while the ledger is non-empty.

## Gate B — the ADVERSARIAL different-provider review (the anti-gap-filling judge)
Hand the structured proof to the configured reviewer whose provider differs from the writer. Resolve
the writer and reviewer providers explicitly; never assume Codex or Claude is independent merely
from the agent name. In skeptic mode instruct it: **"assume each step is WRONG until its named rule /
sub-proof convinces you; do NOT supply a missing premise yourself."** It returns a **mandatory
per-step verdict table**:

| step | deps valid? | justification = {named-rule \| sub-proof \| **HAND-WAVE**} | evidence quote | counterexample attempt |

- Force the **evidence quote** (point to *where* in the proof the step is justified) so it can't holistically
  rubber-stamp; force a **counterexample attempt** on every `novel-core` step (a step that resists one has earned
  trust). Flag **over-description** of routine steps too.
- Output two lists: **gloss-over gaps** (caught hand-waving → fix or `ADMIT`) and **over-rigor** (→ compress).

## Gate C — proof-assistant gate (the 1–3 load-bearing lemmas ONLY)
For the genuinely novel core lemmas the ledger surfaces — NOT the whole proof (full formalization is too
expensive) — autoformalize (Draft-Sketch-Prove → Lean / Isabelle + `hammer` / Sledgehammer). **A core lemma that
won't formalize is a confirmed hand-wave.** Keep the routine scaffolding in natural language.

## Loop
writer (Gate A) → different-provider review (Gate B) → if `novel-core` obligations remain, formalize
them (Gate C) → repeat until the obligations ledger is empty. A residual mathematical gap is
`REVISE` or an explicitly unresolved proof obligation, not automatically a human block. Use
`HUMAN_BLOCKED` only when a named premise, scope decision, permission, credential, or external action
can come only from the human and safe agent-side alternatives are exhausted.

Only the outer `terminalOwner` requests the assignment terminal review over the current proof and
fresh proof-assistant or other acceptance evidence. It returns exactly one of `COMPLETE`,
`HUMAN_BLOCKED`, or `REVISE`; `COMPLETE` requires an empty obligations ledger and every scoped proof
criterion. Child proof invocations return the proof, obligations, and Gate B/C receipts without a
terminal verdict or memory write. A pure reviewer returns its requested verdict directly.

## Presentation handoff

The direction is one-way: `proof-rigor → writeup-human-cleanup`. Complete Gates A–C first, then
invoke cleanup in child presentation-only mode over the accepted proof bytes. Cleanup may compress
routine exposition but cannot change a theorem, dependency, justification, obligation, or proof
status. If cleanup detects a substantive gap, it returns `NEEDS_PROOF_RIGOR` to this invocation; it
must not recursively invoke `proof-rigor`. After any cleanup edit, the owning proof invocation
reruns the affected Gate B/C checks before the outer terminal review.

## Why it's un-gameable
The model can't make a hard step vanish: (i) the dependency graph breaks if it's deleted (caught), (ii) the
ledger forces it to *name* the gap as `ADMITTED` debt, (iii) the adversarial different-provider reviewer
assumes-wrong + must find a named rule or a counterexample (can't gap-fill), (iv) the core lemmas face a proof
assistant. Rigor is spent exactly where it's hard; routine steps stay one line.

## Notes
- This skill **is** the synthesized theory/proof domain expert — off-the-shelf theorem-proving skills are thin or
  absent in the marketplaces (see `default-mode`'s domain bootstrap), so for proof domains we synthesize, not adopt.
- Hands an accepted proof once to **`writeup-human-cleanup`** for presentation-only editing under the
  one-way contract above.
- Sources: Lamport "How to Write a 21st-Century Proof"; OpenAI Prover-Verifier Games; the IMO-2025
  verify-and-refine rubric; QEDBench (gap-filling); RULERS (evidence anchoring); Draft-Sketch-Prove / LeanDojo.
