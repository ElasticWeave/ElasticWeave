---
name: grind
description: "Use for hard or repeatedly failing work: take small falsifiable steps and escalate deliberately instead of quitting or simplifying away the hard part."
---
> **Model tier:** MEDIUM — escalation is run by whoever is executing


# grind — climb, don't leap; grind, don't quit

Two documented agent failure modes, one discipline:

- **The leap (overreach):** attacking a hard problem as one big undifferentiated attempt.
- **The shallow quit (stopping after a superficial attempt):** abandoning after 1–2 direct attempts fail — declaring
  "infeasible" on evidence that only shows "my first try had a bug". Research on agent behavior
  finds first attempts on hard problems mostly fail for *shallow* reasons (an implementation slip,
  not infeasibility), that agents abandon with budget remaining, and that on problems they get
  wrong they switch approaches ~4× more often instead of digging into any one of them.

## Stance — why this skill never threatens you

This skill structures persistence; it does not manufacture fear. Pressure-style prompts measurably
push agents to hide uncertainty, skip verification, and fake completion — and this framework runs
on fresh evidence plus an independent terminal reviewer. So the contract is:

> **A failure recorded honestly is progress. A success claimed without evidence is the only real
> failure here.** You never need to pretend. "Stuck, here is my ledger" is a respectable,
> reviewable deliverable; "done" without fresh acceptance evidence and a reviewer terminal
> `COMPLETE` is not.

## Before the first attempt (anti-leap)

Read `../writing-plans/references/invocation-envelope.md` and inherit its authority, lifecycle owner,
active skill stack, and budgets. Persistence never expands user authority: under `read_only`, keep
tests disposable and stop at evidence; without `experiment`, do not turn a discriminating test into
an empirical trial.

1. **Decompose** — load `writing-plans` for a plan-sized hard problem; otherwise use a compact
   working checklist. Exact paths and falsifiable checks matter, not artificial 2-minute tasks.
2. **One falsifiable hypothesis per change.** Write the PREDICTED effect *before* running;
   compare with the OBSERVED effect after. The discrepancy is the finding.
3. **Open the attempt ledger** (in your todo list / plan doc / working notes):
   `#N | hypothesis | what changed | predicted | observed | why it failed`.
   The ladder below is keyed on this ledger — and it is what you hand to Codex or the human at L4.

## The ladder — consecutive failures of the SAME goal

Track these levels from the attempt ledger yourself. Command exits, wrong outputs, and subtle
misbehavior all count when they test the same goal; no hook or deleted helper is required.

| Level | Trigger | Mandated move |
|---|---|---|
| **L1** | 2nd failure | STOP repeating. Re-read the failure signal **word-by-word**. The next attempt must differ MEANINGFULLY — a different hypothesis, not different formatting. |
| **L2** | 3rd failure | Your *hypothesis* is wrong, not your typing. Load `systematic-debugging` → back to Phase 1 (root cause). Write **3 alternative hypotheses** + a discriminating test for each; run the best discriminator first. |
| **L3** | 4th failure | The **exhaust checklist** — every box checked before attempt #5: ① failure signal re-read verbatim ② searched (web / docs / issues / prior art) ③ read the *surrounding source*, not just your patch ④ every assumption verified with a tool ⑤ the OPPOSITE hypothesis tested ⑥ a minimal reproduction built ⑦ a genuinely different approach/tool/stack tried. |
| **L4** | 5+ failures | Suspect the **PLAN, not the patch**: re-decompose (`writing-plans`), challenge the architecture cross-model (`first-principles`), and recall prior art from `.memory`, skill references/lemmas, official docs, or the web. If a genuine re-scope decision remains, return the ledger to `terminalOwner`; only that owner requests terminal classification. Only structured `HUMAN_BLOCKED` may authorize its exact request through the controlling session or configured UI; `REVISE` resumes or redispatches work. |

A reviewed ledger after genuine L3 exhaust is valid blocker evidence, not automatic permission to
ask or stop. A fresh structured `HUMAN_BLOCKED` makes the exact human request legitimate;
`REVISE` means keep working. Asking without that verdict—or "finishing" by faking—is failure.

## Forbidden moves — each one = STOP, get back on the ladder

- Declaring "impossible/infeasible" without a falsifying experiment for *why*.
- Mocking / stubbing / simplifying away the hard part, then reporting the task done.
- Quietly narrowing the goal to what already works (that's drift — the goal is pinned in `.pm/goal.md`).
- Claiming done without fresh evidence from the task's stated checks and an independent reviewer
  terminal `COMPLETE` over that evidence.
- Omitting a failed attempt from the ledger or the report.
- "One more blind try" past L2 without doing that level's mandated move first.

## The dispatch block — leaders: paste at the END of every execution-worker prompt

Do not paste the full block into a `terminal-reviewer`, `pure-collector`, `machine-gated-node`, or
`post-verdict-mechanical` prompt. Those classes inherit only scoped persistence, tool verification,
authority, and budget instructions; they do not commission a reviewer or write memory. A child
execution worker also returns its scoped receipt to `terminalOwner` without running the outer
terminal lifecycle.

```
PERSISTENCE: You are an agent — keep going until the task is fully resolved before yielding.
Never stop at the first failure: load the `grind` skill and climb its ladder (L1 vary → L2
re-hypothesize → L3 exhaust checklist → L4 challenge the plan) before concluding anything is
impossible. After a genuine L3 exhaust, submit the ledger to a fresh terminal reviewer as a
candidate human dependency only when your invocationId equals terminalOwner; otherwise return the
ledger to that owner. Route an exact human request only for structured `HUMAN_BLOCKED`, through the
controlling session or configured UI; `REVISE` means continue or re-dispatch. An honest ledger
always beats a fake "done".
TOOLS OVER GUESSES: If you are not SURE about file contents, APIs, or state — read/verify with
tools; never guess or fabricate.
PLAN, PREDICT, REFLECT: Decompose before acting (writing-plans); write the predicted effect
before each attempt; record predicted-vs-observed in your ledger after it.
INVOCATION: Obey the supplied invocation envelope. Never widen authority or budget. A child returns
artifacts, evidence, mutation/resource receipts, remaining budget, and learning candidates to its
caller without issuing the assignment terminal or writing memory.
TERMINAL REVIEW: Only when your invocationId equals terminalOwner, route the final scoped contract,
current work, and fresh evidence to the selected different-provider review path. `REVISE` continues
or redispatches. `HUMAN_BLOCKED` requires a human-only dependency after safe alternatives are
exhausted. All other worker classes return their receipt directly without reviewer recursion.
```

(The first line is the measured "agentic persistence" pattern — OpenAI reported the three-reminder
trio at ≈+20% on SWE-bench Verified — adapted to this framework's escalation + honesty rules.)

## Related

- `systematic-debugging` — the root-cause process the ladder returns to (L2+).
- `writing-plans` — the anti-leap decomposition (before attempt #1; again at L4).
- `first-principles` + the configured different-provider reviewer — the L4 cross-model challenge.
- The configured human UI channel — available at L4 only after fresh structured `HUMAN_BLOCKED`,
  with the reviewer's exact request rather than an invented prompt.
- Provenance: ladder + exhaust-checklist structure adapted from the community "PUA" skill
  (tanweai/pua); the no-threats stance from its counterpoint (wuji-labs/nopua); the four-phase
  process from obra/superpowers `systematic-debugging` (vendored in this repo).
