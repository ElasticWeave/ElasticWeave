---
name: first-principles
description: "Use before accepting a hard decision or experiment at face value; derive from constraints and trace observations to mechanisms."
---
> **Model tier:** HIGH — the reasoning discipline itself


# First Principles Reasoning

## Overview
First principles thinking strips away assumptions and conventions to reveal fundamental truths, then reconstructs solutions from those basics. This approach, championed by Elon Musk and rooted in Aristotle's philosophy, enables breakthrough solutions by escaping the trap of reasoning by analogy.

**Core Principle:** Don't accept "that's how it's always done." Reduce to fundamentals, then rebuild.

## When to Use
- Conventional approaches have failed or seem inadequate
- You're told something is "impossible" or "too expensive"
- Designing new systems/products from scratch
- Challenging industry assumptions or pricing
- Problem seems intractable using existing mental models
- You need innovation rather than incremental improvement
- **An experiment/measurement returned a result and you're about to accept the surface interpretation** (revert, declare "infeasible", move on) **without finding the mechanism that produced it** — see "Root-Causing an Empirical Result" below.

Decision flow:
```
Problem intractable? → yes → Are you reasoning from analogy? → yes → APPLY FIRST PRINCIPLES
                                                            ↘ no → Already at fundamentals
                  ↘ no → Standard problem-solving may suffice
```

## When NOT to Use
- The constraint is genuinely physics/cost/regulation, not convention — verify, then accept it and move on.
- A well-trodden, working solution already exists; reinventing from scratch adds risk without lift. Use the convention.
- Routine implementation where the "why" is settled — don't re-derive a standard library, protocol, or known-good pattern.
- Time-critical incidents: act on the most likely cause first (occams-razor), reserve first-principles for the post-incident redesign.

## Root-Causing an Empirical Result (drill to the root; don't stop at the surface)

The other half of first-principles: not only design calls, but **making sense of what an experiment
actually did**. The most common agent failure is accepting the SURFACE reading and acting on it —
"it got slower → revert", "the test still fails → try the next thing", "no improvement → this
direction is infeasible" — without finding the MECHANISM that produced the result. That is staying at
the surface; the job is to reach the most fundamental source (the base cause).

**The rule: a result you cannot EXPLAIN is a result you cannot TRUST.** A number moved — but do you
know *why*? Until you can name the mechanism, you don't know whether it's the real effect, measurement
noise, an implementation bug, or your change interacting with something unrelated. Acting on an
unexplained result is how agents thrash and how false conclusions get locked in.

Before you revert / conclude / move on, DRILL:
1. **State the surface reading AND the predicted effect.** What did you expect vs. get? The
   **discrepancy between predicted and observed is the finding** — not the raw number itself.
2. **5 Whys on the result.** "It got 3% slower." Why? → "the inner loop ran more iterations." Why? →
   … keep going until you hit a mechanism you can point at in the code/data, not a restatement of the
   symptom. Most "it didn't work" outcomes die at Why #2 as a shallow bug, not as infeasibility.
3. **Attribute to a concrete cause, not a vibe.** Tie the result to a specific mechanism (a resource,
   a dependency, a code path, a data property). "It's just slow", "the model isn't good enough",
   "must be the framework" are non-attributions — reject them and keep digging.
4. **Evidence must match the uncertainty.** A single noisy or unisolated run is not a finding;
   repeat it or use statistical evidence proportional to the variance. A deterministic isolated
   failing/passing or counterfactual probe can be sufficient once when it genuinely distinguishes
   the mechanism from alternatives.
5. **Separate "the idea is wrong" from "my execution was wrong."** First attempts fail for shallow
   reasons far more often than because the idea is infeasible — assume an implementation bug first
   (this is `grind`'s L2), and only conclude "infeasible" once you have a falsifying experiment for WHY.

Forbidden — each one means you stopped at the surface:
- Reverting because the metric worsened, without naming which mechanism worsened it.
- Declaring a direction "impossible/infeasible" with no experiment that isolates the cause.
- Accepting an improvement you can't explain (it may be a fluke — or a correctness regression in disguise).
- "The result speaks for itself." Results don't speak; mechanisms do.

Pair with the **analyzer** (it attributes cost to cause from the fine-grained view) and with
**grind** L2 (root-cause a failing attempt). When the mechanism resists after an honest 5-Whys,
dispatch the configured different-provider reviewer—but an UNEXPLAINED result is itself a finding
to hand over, not a shrug. This closes the loop with this skill's rule above: read the fine-grained
evidence after every change; the predicted-versus-observed discrepancy is the finding.

When this reasoning should change a skill's generalized behavior, use `self-improve`'s lemma
contract: record the predeclared prediction, discriminating intervention, raw evidence, mechanism,
scope, and falsifier. A `conjecture` or inconclusive `tested` result is useful knowledge but cannot
become a durable instruction.

## Trigger Card

When a constraint is treated as fixed ("too expensive", "impossible", "always done this way"):

1. **Ask: is this constraint physics or convention?** If it's a law of nature, accept it. If it's "how we've always done it," it's negotiable.
2. **Identify the irreducible truths** — what must be true no matter what solution you pick?
3. **Rebuild from those truths** — derive a solution that satisfies the real constraints but ignores the artificial ones. If a simpler rebuild solves it, stop.

Skip if the "why" is settled (standard library, known-good pattern). In an incident, act on the most likely cause first; reserve this for post-incident redesign.

## The Process

### Step 1: Identify Current Assumptions
List everything you "know" or assume about the problem:
- What are the constraints everyone accepts?
- What costs/limitations are considered fixed?
- What's the conventional wisdom?
- Why do people say it can't be done?

### Step 2: Break Down to Fundamentals
Ask repeatedly: "What are we absolutely certain is true?"
- What are the physical/mathematical constraints?
- What are the raw inputs required?
- What laws of physics/economics apply?
- What is the minimum viable version?

### Step 3: Challenge Each Assumption
For each assumption, ask:
- Is this actually true, or just convention?
- What evidence supports this?
- Under what conditions would this be false?
- Who benefits from this assumption persisting?

Use the "5 Whys" to drill deeper.

### Step 4: Rebuild from Fundamentals
Starting ONLY from verified truths:
- What's the simplest solution that satisfies fundamental requirements?
- What new approaches become possible without false constraints?
- How would you solve this if starting from scratch today?
- What would a solution look like with 10x less resources?

### Step 5: Validate Against Reality
- Does your reconstructed solution violate any physics/laws?
- What practical constraints remain?
- What's the minimum viable test to prove/disprove it quickly?

## Mental Traps to Avoid

| Trap | Description | Antidote |
|------|-------------|----------|
| Reasoning by Analogy | "Others do it this way" | Ask "but is it optimal?" |
| Appeal to Authority | "Experts say it's impossible" | "What specifically makes it impossible?" |
| Sunk Cost | "We've always done it this way" | "What if we started fresh today?" |
| Complexity Bias | Assuming complex = better | "What's the simplest version?" |
| False Constraints | Accepting artificial limits | "Is this a law of physics or convention?" |

## Output Format

When the call is non-trivial, structure the analysis as:

```markdown
## First Principles Analysis: [Topic]

### 1. Problem Essence
**Core problem:** [One sentence — stated as an outcome, not a solution]
**Success criteria:** [Measurable outcomes]

### 2. Assumptions Challenged
| Assumption | Challenge | Verdict (Keep/Discard/Modify) |
|------------|-----------|-------------------------------|
| [Assumption] | [Why question it] | [Verdict] |

### 3. Ground Truths
- [Irreducible fact 1]
- [Irreducible fact 2]

### 4. Reasoning Chain
Ground Truth → [Step 1] → [Step 2] → Solution

### 5. Conclusion
**Recommended approach:** [Description]
**Key insight:** [What the first-principles analysis revealed]
**Trade-offs acknowledged:** [What we're accepting]
```

## Pair with a different-provider reviewer (convergence over consensus)
For a nonterminal design analysis, carry the actual writer semantic role into dispatch and resolve
it through the verified model-role document. Use `cross-review-openai-writer` for an OpenAI-family
writer or `cross-review-anthropic-writer` for an Anthropic-family writer in **planning-review mode**;
`resolve_reviewer_assignment` must bind that exact writer role to the named asset and
canonical opposite-family reviewer before launch. It returns `APPROVE | REVISE` while adversarially asking:
*which "ground truths" are actually unverified assumptions? which "physics" constraints are really
just convention — or vice versa?* Reconcile toward the most defensible choice, not the fastest
agreement.

If the analysis is instead the worker's terminal assignment artifact, invoke
**terminal-completion mode**, not planning-review mode. Include the scoped acceptance contract and
fresh evidence; the reviewer returns `COMPLETE`, `HUMAN_BLOCKED`, or `REVISE`. `REVISE` means
continue or redispatch. `HUMAN_BLOCKED` requires a concrete permission, decision, credential, or
external action that only a human can provide after safe agent-side alternatives are exhausted. A
session invoked solely to issue this terminal verdict returns it directly and does not recursively
commission another reviewer.

In either mode, never infer reviewer direction from the top session, omit the actual writer role,
or use a generic reviewer name. Missing, mismatched, or unverified routing fails closed.

## Verification Checklist
- [ ] Listed all assumptions about the problem
- [ ] Identified which assumptions are physics/math vs convention
- [ ] Challenged at least 3 "obvious" constraints
- [ ] Rebuilt solution using only verified fundamentals
- [ ] Validated that solution doesn't violate actual constraints
- [ ] Identified a minimum viable test to prove/disprove the approach

## Key Questions
- "What do we know to be absolutely true?"
- "Why does everyone assume this constraint exists?"
- "What would we do if we had to solve this with 10% of the budget?"
- "If we were starting from scratch today, would we build it this way?"
- "What would a complete outsider try?"

---
*Provenance: body verbatim from `tjboudreaux/cc-thinking-skills` (MIT, 587★); additional triggers + Output Format grafted from `awesome-skills/first-principles-skill` (MIT).*
