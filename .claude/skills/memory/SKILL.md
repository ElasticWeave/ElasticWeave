---
name: memory
description: "Read or write project memory for observations, limits, and falsifiers; keep cross-task promotion behind self-improve."
---
> **Model tier:** LOW — the mechanics are file edits; the JUDGMENT of what's worth remembering is the caller's.
>
> **Memory abstraction:** one project-local `.memory/` tree; children return learning candidates; the outer terminal owner authorizes one pre-seal closing write; top decides triage; cross-task promotion is separate and evidence-gated.

# memory — explicit, hierarchical, cross-platform (.memory/)

**Why this exists.** V2 runs MULTIPLE agent platforms over one project. Implicit per-platform
memory fragments; and the main session's context is the scarcest resource in the system — it keeps
only the essence, hands bulky context to subagents as documents it never reads itself, and uses
`.memory/MAIN.md` as the extension of what it cannot hold. Plain files are the one format every
harness reads natively — no daemon, no DB, no per-platform copies.

## The tree (all of it visible to every agent; reads are ON-DEMAND via INDEX)
```
.memory/
  MAIN.md            main session's memory — CAPPED (≤100 lines / ≤8000 chars, validator-enforced).
                     Read it at SESSION START and at task boundaries. `default-mode` and `pm-dispatch`
                     require the read; do not assume any harness auto-injects it.
  main/<slug>.md     overflow detail MAIN.md entries link to (on-demand expansion).
  inbox/new|promoted|rejected/   the UPWARD channel (see below). Lifecycle dirs = auditability.
  skills/<skill>.md  skill-attached lessons. FIRST LINE is canonical: `abstraction: <one line>`.
                     The skill's SKILL.md may cache it in its Memory abstraction header (validator
                     checks drift). Loading a skill injects the abstraction; details on demand.
  agents/<agent>.md  each recurring agent's OWN distilled bitter lessons (agent-autonomous).
  workflows/<flow>.md lessons about a FLOW itself (not any one agent/skill).
  private/           gitignored. Last resort only — memory should NEVER hold secrets at all.
  INDEX.md           one line per file — the recall surface.
```
Entry format everywhere: `- [YYYY-MM-DD] <lesson> — why: <…> (src: <run/issue/commit>)`.

Skill memory is not a causal knowledge base. Keep terse observations here; when proportional evidence
supports a scoped mechanistic claim, the `self-improve` skill may promote the reasoning (not copied
prose) into that skill's opt-in `lemmas/` directory with raw provenance and a falsifier.

## Project memory is not a reusable reference

Treat every new run lesson as **project-level by default**, including entries promoted to `MAIN.md`.
Inbox promotion raises visibility inside this project; it does not establish transferability. Keep
the following in `.memory/`:

- one-task or one-project observations, hypotheses, local decisions, paths, issue ids, and failures;
- repeated runs, seeds, or artifacts from the same task context;
- candidate lessons whose applicability, independence, or counterexample is still unknown.

Move reasoning into a cross-task carrier only through `self-improve` after all of these hold:

1. Cite raw evidence from at least two distinct task contexts. Reruns, artifacts, rounds, or seeds
   of one task improve within-task confidence but do not create a second context.
2. Explain why the contexts are independent of the same project-specific cause.
3. Remove repository, customer, issue, absolute-path, and other project identifiers from the
   reusable claim. Keep exact provenance in raw artifact pointers and `.memory/`, not its prose.
4. State applicability plus known counterexamples/boundaries and a concrete falsifier.
5. Obtain the carrier's required review. A `MAIN.md` promotion is not that review.

If any item is missing, retain a terse project observation and report `NONE`; do not create a
lemma, learned reference, BitLesson for reuse, generalized instruction, or Notion promotion.

## Write discipline (WHO writes WHERE)

Apply the shared lifecycle vocabulary in
`../writing-plans/references/invocation-envelope.md` before any write. Only the outer
`terminalOwner` schedules one closing memory/self-improvement beat, before final evidence is sealed;
a child reports `learningCandidates` to that owner instead of starting its own closing lifecycle.

- **Top/main ownership:** inspect memory, decide promote/reject, authorize exact mutations, and
  inspect returned diffs/evidence. The top/main session never edits `.memory/`, moves inbox files,
  updates INDEX, or repairs validator failures itself.
- **Write at the SOURCE layer**: a lesson learned executing a skill → `skills/<skill>.md`; about an
  agent's own craft → `agents/<agent>.md`; about a flow's shape → `workflows/<flow>.md`. The bounded
  closing worker appointed by the `terminalOwner` owns these writes before final sealing.
- **Upward propagation (the hierarchy's point):** if the MAIN model must know it, ALSO drop a
  one-entry file in `inbox/new/<YYYYMMDD-HHMM>-<agent>-<slug>.md` (per-writer files — parallel
  workers never share a file, so no append races). Ordinary execution workers do not write
  MAIN.md; only an explicitly authorized bounded triage worker may apply a top-approved promotion.
- **Standard-round's self-evolve node** writes the skill/agent/workflow layers + inbox entries as
  part of every round's distillation.
- Do not duplicate a lemma body in memory. Memory records the observation and links the lemma;
  the lemma owns hypothesis, experiment, mechanism, scope, and status.
- A self-evolve `NONE` outcome may still append one terse source-layer observation/inbox entry. It
  means no lemma or durable framework behavior change, not "erase the round's residue."
- Do not label an entry "cross-task" merely because it was promoted to MAIN or observed in several
  rounds of the same issue. Record such transfer candidates in the source layer until the separate
  `self-improve` gate is satisfied.

## Closing self-evolve (ONCE per outer assignment)

Before the outer `terminalOwner` seals final evidence, have it authorize one bounded closing worker
to append `- [date] lesson — why (src)` entries to the relevant skill/agent/workflow source layers
and drop an inbox entry when MAIN must know. Children return `learningCandidates`; they do not write
memory merely because a nested skill ended. A workflow's single declared selfEvolve node may fulfill
this beat when its receipt binds all final writes. If the beat was missed, schedule it and then
refresh final evidence/review; never write memory after a terminal verdict. `terminal-reviewer`,
`pure-collector`, `machine-gated-node`, and `post-verdict-mechanical` invocations never initiate or
repeat this beat. The top session authorizes and inspects; it does not backfill `.memory/` itself.

## Triage (top/main decision; bounded-worker mutation — reviewer-gated)
1. Have the top/main session inspect `.memory/inbox/new/`, relevant source memory, MAIN, and INDEX.
2. Decide promote/reject for each entry and authorize an exact one-entry mutation. **Promotion is
   reviewed**: durable? non-duplicative? correctly layered? no secrets? For a big batch, delegate a
   different-provider reviewer over the candidate set before authorization.
3. Delegate the authorized mutation to one bounded triage worker. For promotion, have it append ONE
   distilled entry to MAIN.md and `git mv` the inbox file to `inbox/promoted/`; for rejection, have
   it move the file to `inbox/rejected/`. Have the same worker update INDEX when paths change.
4. Have that worker run `python3 .claude/skills/memory/scripts/validate.py`, repair caps/format/index/
   abstraction failures within the authorization, rerun until clean, and return the focused diff
   plus validation output. Over cap means the worker condenses MAIN into `main/<slug>.md` first.
5. Have the top/main session inspect the returned diff and evidence. Authorize a bounded revision if
   needed; never perform the file mutation or validator repair in the top session.

## Relationship to external knowledge stores
- `.memory/` is project-local, cross-platform working memory. It does not synchronize with Notion
  or another external knowledge store. A user may separately request an explicit, reviewed export,
  but MAIN/inbox status never authorizes one and imported knowledge still requires local checking.
- An optional harness-local cache is never a source of truth in V2 and has no standing sync.
- The distributable skill bundle does not ship project memory. `default-mode` creates `.memory/` in the
  target repository and this skill owns its lifecycle.

## Rules
- No secrets/credentials/customer data/private-chat excerpts anywhere in `.memory/` (validator
  scans; only the exact `private/` subtree is exempt, not prefix-similar siblings such as
  `private-notes/`; `private/` is gitignored but the real rule is: don't).
- `.memory/` IS committed (versioned, reviewable, travels with the clone).
- MAIN.md grows ONE entry at a time, through triage only. If you're pasting paragraphs, you're
  polluting the thing this system exists to protect.
