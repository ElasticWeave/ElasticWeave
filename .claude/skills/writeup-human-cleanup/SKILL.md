---
name: writeup-human-cleanup
description: "Clean AI-looking docs into concise, source-faithful prose; preserve evidence, caveats, and proof boundaries."
---
> **Model tier:** MEDIUM — editing pass


# Write-up Human Cleanup

Clean prose so a busy human can decide what to do next.

Inherit `../writing-plans/references/invocation-envelope.md`. Cleanup preserves the caller's
authority and terminal owner. As a child it returns edited bytes, a change log, and gap findings; it
does not issue the assignment terminal or write memory.

This skill does **not** add vibes, typos, slang, or fake personality. It removes AI-shaped padding,
restores the reader's task, and keeps every factual claim traceable to the source material. It
optimizes information per reading effort, not characters at any cost.

## Trigger this skill when

Use this skill when the user asks for any of these:

- “make this README sound human”
- “clean up this report”
- “less AI, more human”
- “remove AI slop”
- “tighten this write-up”
- “make this less verbose”
- “rewrite this in a founder / engineer / analyst voice”
- “turn this draft into something a human would actually read”

Also use it proactively when a draft has obvious AI tells: throat-clearing, generic hype,
table-of-contents bloat, bold-first bullet walls, “not just X but Y” contrasts, em-dash padding,
repetitive three-part rhythms, passive voice, vague claims, decorative metaphors, or report sections
that make the reader decode a narrative before reaching the facts.

## Do not use this skill for

- Hiding required AI disclosure.
- Rewriting legal, medical, safety, or financial claims without source verification.
- Making text “undetectable” by adding errors.
- Removing attribution, caveats, limitations, citations, or license notices.
- Replacing technical precision with casual wording.

If a fact is unsupported, mark it as unsupported or ask for a source. Never invent proof.

## Core principle

A human write-up earns trust by doing four things fast:

1. It says what the thing is.
2. It says why the reader should care.
3. It proves the claim with a concrete detail.
4. It tells the reader what to do next.

Everything else must justify its space. Prefer the largest number of source-supported,
decision-relevant facts that the intended reader can understand directly. A sentence is not dense
when it merely packs in jargon, drops necessary qualifiers, or forces the reader to translate a
metaphor before recovering the claim.

## Operating procedure

### 1. Identify the reader and the job

Infer the primary reader from the text. If the reader is unclear, use the safest default.

- README: developer trying to decide whether to install, use, or contribute.
- Report: decision-maker trying to understand the answer, evidence, implication, and risk.
- PR / changelog / launch note: reviewer trying to understand what changed and why.
- Profile or personal README: visitor trying to understand the person, work, and contact path.

Write the reader's job in one private sentence before editing.

### 2. Build a claim inventory

Before rewriting, list the important claims mentally:

- product or project purpose
- supported platforms, versions, APIs, formats, limits
- performance, accuracy, cost, adoption, or benchmark claims
- security, privacy, compliance, or safety claims
- roadmap, status, maturity, or maintenance claims

Preserve supported claims. Soften or flag unsupported claims. Remove claims that are decorative.

For a substantial draft, identify its direct fact units before rewriting. A direct fact unit is one
independently checkable proposition with an explicit subject, relationship, value, condition, or
outcome. A citation by itself, an unsupported adjective, a repeated conclusion, and a raw number
without its relationship do not count. See `references/fact-density-and-metaphor.md` for the
language-normalized audit and visual rules.

### 3. Fix the first screen

For a README, the first 150–250 words should answer:

- What is it?
- Who is it for?
- What problem does it solve?
- What is the fastest way to try it?
- What is one concrete proof point?

For a report, the first screen should answer:

- What is the answer?
- What evidence supports it?
- What should the reader do?
- What caveat could change the decision?

Do not start with “In today's fast-paced world,” “This report explores,” “This project aims to,” or a generic mission statement.

### 4. Cut AI-shaped surface patterns

Remove or rewrite these patterns:

- throat-clearing: “Here's the thing,” “It is worth noting,” “This section explores”
- generic value words: “robust,” “seamless,” “powerful,” “comprehensive,” “innovative”
- false contrast: “not just X, but Y,” “not because X, because Y”
- fake drama: “Let that sink in,” “game changer,” “the truth is”
- decorative metaphor: “the lemma survived,” “the evidence fired back,” “pay the synchronization
  tax,” when the literal result is shorter and clearer
- repetitive triads: three parallel bullets, three adjectives, three clauses
- em-dash padding used to sound thoughtful
- bold-first bullet walls where every bullet begins with a bold label
- passive voice that hides the actor
- inanimate false agency: “the data tells us,” “the solution empowers,” “the report explores”
- narrator distance: “people tend to,” “one might argue,” “it can be seen that”
- meta-writing: “below we will,” “in conclusion,” “the rest of this document”

Do not remove domain terms that carry meaning. “Weighted interval score” is not slop. An established
technical metaphor may stay when it is defined, used consistently, and cheaper to understand than
its literal replacement. “Leverage cutting-edge synergy” is slop.

### 5. Replace claims with observable details

When a sentence says something is “easy,” “fast,” “flexible,” or “robust,” replace it with the detail that makes it true.

Bad:

> A seamless and robust tool for managing files at scale.

Better:

> It watches a directory, retries failed uploads, and writes a JSONL audit log for every file.

Bad:

> This report provides a comprehensive overview of market dynamics.

Better:

> Revenue rose in enterprise accounts, churn stayed flat, and all growth came from customers with more than 500 seats.

### 6. Rebuild the structure around the reader's path

#### README order

Use this order unless the source demands otherwise:

1. Name and one-line description.
2. Why it exists.
3. Install or quick start.
4. Minimal working example.
5. Common tasks.
6. How it works, only after the user can run it.
7. Limits, non-goals, compatibility.
8. Development, tests, release, license.

Delete a table of contents unless the README is long enough that navigation helps. For most project READMEs, headings are enough.

#### Report order

Use this order unless the user requested another format:

1. Answer first.
2. Evidence that changes the decision.
3. Recommendation or next step.
4. Caveats and unknowns.
5. Detail by topic.
6. Appendix for methods, sources, raw tables, and long context.

Do not bury the conclusion under background.

### 7. Keep code, commands, and tables stable

Preserve these unless the user asks to edit them:

- shell commands
- code blocks
- API names
- configuration keys
- URLs
- citations
- metrics
- version numbers
- license text
- literal UI labels

When editing surrounding prose, do not change technical semantics. If a command looks wrong, flag it separately.

### 8. Rewrite by section, not by sentence

A sentence-level cleanup often leaves the same bad structure intact. Rewrite each section around its purpose:

- What should this section make the reader know or do?
- Which facts prove it?
- Which lines repeat another section?
- What can move to an appendix?

For heavy cleanup, return a new draft plus a short change log.

### 9. Calibrate voice

Use the voice the document needs, not a generic “friendly” voice.

- Engineer README: direct, concrete, slightly compressed.
- Product README: useful, grounded, with examples before claims.
- Executive report: answer-first, caveated, low drama.
- Research report: precise, cautious, evidence-led.
- Personal profile: specific projects and motives, no inspirational filler.
- Business/report prose translated from Chinese: use concrete nouns and verbs; avoid literal corporate filler such as “empower,” “ecosystem,” “key lever,” “closed loop,” “all-around,” “in-depth analysis,” “enable growth,” “build out,” and “implementation.”

### 10. Audit before delivering

Run this final check:

- Is the first screen useful?
- Can every strong claim point to a fact in the input?
- Did the rewrite preserve or increase directly readable fact yield on representative sections?
- Can each remaining metaphor pass the necessity test: more precise or shorter than literal prose,
  familiar to this audience, and used consistently?
- Does each visual state its takeaway, units, comparison, conditions, and uncertainty in the visual
  or adjacent caption?
- Did you preserve code, commands, citations, and numbers?
- Did you remove generic hype?
- Are headings reader-oriented?
- Does each section have one job?
- Did you keep caveats and non-goals?
- Is there less text than before unless the user asked for expansion?

## Output format

When editing a provided draft, return:

1. The cleaned version.
2. A compact change log.
3. Any unsupported claims or missing sources.
4. For substantial rewrites, a compact audit: representative before/after direct-fact counts,
   normalized length, metaphors removed or retained, and any readability or visual-density concern.

When asked to create files, write the cleaned content into the requested file path and include a short summary of what changed.

## Scoring

Score the draft out of 60:

- Reader fit: 10
- First-screen clarity: 10
- Direct fact yield: 10
- Literal clarity and metaphor cost: 10
- Readability under compression: 10
- Source fidelity: 10

Below 45: revise again. Below 35: restructure, not just edit.

## Framework integration — writer brief + one review

When another agent is dispatched to write a human-facing artifact, include these fields in its task
brief. This is part of the writing assignment, not an extra infrastructure phase:

- intended reader and decision;
- source set and the load-bearing facts that must survive;
- protected literals: citations, code, commands, measurements, names, and proof status;
- `METAPHOR_POLICY: literal-first; retain only established terms that reduce reading effort`;
- `DENSITY_POLICY: maximize supported direct facts per reading effort; do not compress past
  immediate comprehension`;
- requested artifact and any length or visual constraints.

Use the existing configured rigor/gloss-over reviewer as the write-up reviewer; do not create a
second review lane just for style. Give it both the source material and draft. It must check:

1. **Source fidelity** — every load-bearing claim, number, caveat, citation, and proof status.
2. **Direct fact yield** — a representative before/after count normalized by language, with
   repetition, unsupported claims, and undecodable jargon excluded.
3. **Metaphor cost** — list unnecessary metaphors removed, plus any retained metaphor and why it is
   cheaper than literal wording.
4. **Compression readability** — reject noun piles, undefined acronyms, tiny visual labels, and
   sentences whose qualifiers no longer attach to the right claim.
5. **Visual yield** — for each important visual, identify the direct takeaway and whether labels,
   units, comparison, conditions, uncertainty, and a text equivalent are recoverable without a
   narrative detour.

The review returns edited bytes plus its audit and gaps. A high density score cannot compensate for
a source-fidelity, proof, or readability failure.

## Framework integration — rigor + gloss-over pass (reports / proofs / rigorous docs)

Inside the framework, the same human-facing review also includes one scoped rigor pass (this skill
is the framework's single human-writing skill). Resolve a configured different-provider reviewer
from the actual writer identity; do not hardcode Codex or infer independence from an agent name.
This is a nonterminal child check over the source, draft, and audience level (`.pm/audience.md` when
present):

1. **Notation discipline** — list every newly-introduced symbol / definition; for each ask: necessary? used
   **≥2×**? as simple as possible? Cut any that is unnecessary, single-use, or over-complex — inline it.
2. **Rigor calibration** — the **novel / hard / load-bearing** steps argued in FULL; the **routine / trivial**
   ones compressed or skipped (reasonable omission of routine steps). Flag BOTH over-described trivia AND under-justified hard steps.
3. **Gloss-over / anti-hand-waving** — scan for "clearly / obviously / it follows / easy to see" covering a
   NON-trivial gap; for each, demand the real justification (a cited lemma / prior result / sub-proof) OR mark it
   an **OPEN GAP**. The reviewer must not silently fill the missing premise.
4. **Audience level** — write to the human's recorded knowledge level (`.pm/audience.md`): don't re-derive what
   they know; don't bury them in jargon they don't.

**Never** resolve a gloss-over by deleting the hard step or softening the claim. Simplify the
**expression**, never the **substance**. For an actual proof/derivation, the caller must run
`proof-rigor` before cleanup. If cleanup discovers a new substantive proof gap, return
`NEEDS_PROOF_RIGOR` with the exact affected claims to the caller and stop substantive editing; this
skill must not invoke `proof-rigor`. The owning proof invocation decides the revision and re-review,
which prevents mutual recursion. Cleanup covers only presentation, notation, and rigor calibration.

## Reference files

Consult these when needed:

- `references/fact-density-and-metaphor.md` for substantial prose or any visual explainer
- `references/cleanup-rules.md`
- `references/readme-cleanup.md`
- `references/report-cleanup.md`
- `references/anti-slop-patterns.md`
- `references/distilled-from-git-changes.md`
- `references/before-after-examples.md`
- `references/scorecard.md`
