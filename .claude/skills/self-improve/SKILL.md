---
name: self-improve
description: "Review post-round observations for project memory, direct repair, or evidence-gated cross-task reuse; generalized changes require transfer admission."
---
> **Model tier:** HIGH — causal synthesis and high-leverage framework changes

# self-improve — turn round evidence into better operating knowledge

The canonical standard-round design anchors this discipline in `selfEvolve`; its built-in mapping
under `.subagent/flows/` runs `evolveReview` before controller checkpointing and state-bound proof.
`complete-milestone` applies the same discipline to non-workflow work at milestone boundaries.

When redesigning this lifecycle rather than executing it, load
`references/prime-agent-2026-08.md`. Routine rounds should not pay that source context cost.

## Keep the stores distinct

- `.memory/` is project-level working memory. It holds terse observations, failures, dead ends,
  local identifiers, and transfer candidates. MAIN/inbox promotion changes project visibility only.
- `references/` normally holds static upstream sources, templates, and domain documentation. A
  self-evolve-generated learned reference must be named `learned-<slug>.md`, carry the cross-task
  metadata in `references/cross-task-reference.template.md`, and derive from a supported lemma.
  The validator rejects legacy `lessons.md` carrier names and `.memory`-style dated bullets in
  learned references; it intentionally leaves dates and versions in static upstream provenance alone.
- `.claude/skills/<skill>/lemmas/` is an opt-in **cross-task** carrier for scoped mechanistic claims.
  An adopted directory has `INDEX.md` plus one file per lemma. Skills without it pay no context or
  maintenance cost.
- Notion and any reusable BitLesson are also cross-task carriers; neither is a staging area for
  one-project observations.

Do not copy the same prose across all three. A trace observation can motivate a lemma, but the lemma
must add a falsifiable mechanism, evidence, scope, and falsifier rather than restating memory.

## Transfer admission — before creating any reusable carrier

Require all five conditions below for every lemma (including a conjecture), learned reference,
reusable BitLesson, generalized instruction, or Notion promotion:

1. **Distinct task contexts:** cite raw observations from at least two tasks with materially
   different inputs, projects, or operating contexts. Multiple artifacts, rounds, controls, seeds,
   or retries of one task count as one context.
2. **Independence:** explain why a shared project quirk is not sufficient to explain both contexts.
3. **De-identification:** remove repository/customer names, issue ids, absolute paths, and local
   constants from reusable prose. Preserve exact provenance only in raw artifact pointers and
   project memory. Set `project_identifiers_removed: true`; the reviewer verifies the claim.
4. **Applicability and negative evidence:** state where the guidance applies, known counterexamples
   or boundary cases, and an observation that would falsify it.
5. **Review:** validate the declaration, then have the reviewer compare it with raw evidence.

If any condition is missing, append at most one source-layer `.memory` observation and report
`NONE`. Do not write a placeholder carrier. The mechanical validator checks declarations; it cannot
prove that contexts are genuinely independent or prose is fully de-identified.

## Two evidence-backed lanes

### REPAIR — a used artifact is directly broken

Repair a skill, flow, or agent when this round directly reproduced a concrete defect. Record the
failing probe, make the smallest fix, rerun a discriminating passing probe, and cite both raw
artifacts. This is a direct deterministic repair, not a generalized causal law and does not require
a lemma. This single-context exception applies only to project-independent contract defects such as
invalid syntax, a broken path resolver, or behavior that contradicts an existing declared contract.
It cannot introduce a new heuristic, learned reference, or broader rule. Otherwise retain the
observation in `.memory` until transfer admission passes. The edit still passes `evolveReview`.

### OPHIS — learn a mechanism before generalizing

1. **MINE.** Read first-hand round traces (`observability.json`, `progress.md`, node transcripts,
   `.pm/reports/`, per-case eval traces, and metric history), then the relevant skill's
   `references/`, `.memory/skills/<skill>.md`, and existing lemmas. Read `refuted` lemmas too so a
   failed lever is not retried without a materially different premise.
2. **OBSERVE.** State what the raw evidence showed, including prediction-versus-result discrepancy.
   Builder summaries and curated memory are leads, not ground truth.
3. **PROBLEM.** Name one unexplained failure, bottleneck, or opportunity whose cause matters to
   future behavior.
4. **HYPOTHESIS.** Form one scoped, falsifiable causal explanation. State the predeclared prediction,
   boundaries, competing explanation, and falsifier before looking at the intervention result.
5. **EXPERIMENT.** Run the smallest feasible discriminating intervention or counterfactual probe.
   Evidence is proportional to uncertainty: noisy or graded support/refutation needs at least two
   distinct runs plus statistical evidence; one aggregate artifact may summarize them. A
   deterministic isolated failing/passing probe may be sufficient once.
   One unisolated success never becomes a permanent rule.
6. **TRANSFER CHECK.** Group evidence by task context, not artifact count. If fewer than two
   independent contexts support examining the same mechanism, stop at project memory and `NONE`.
   Otherwise de-identify the candidate and declare applicability, counterexamples, and falsifier.
7. **SYNTHESIZE.** Create or update at most one lemma and validate the directory. Use exactly one
   epistemic status: `conjecture`, `tested`, `supported`, `refuted`, or `superseded`. Durable carriers
   are links in `derived_changes`, never a status such as `promoted`.

If no discriminating experiment fits the round budget but transfer admission already passes, save a
`conjecture` with its next probe and do not mutate durable behavior from it. If transfer admission
does not pass, keep the conjecture in `.memory` instead. `tested` also cannot justify a durable behavior edit. Only a
`supported` lemma may motivate a separately reviewed generalized new behavior. A `refuted` lemma may
remove or narrow behavior already named in its `derived_changes`; a `superseded` lemma may correct or
redirect prior behavior only when its replacement is an existing `supported` lemma. Both remain in
place as anti-loop evidence. Most rounds should report `NONE`.

`NONE` means no lemma and no durable skill, agent, flow, or gate behavior change. It may still preserve
one terse source-layer `.memory` observation (plus an inbox entry when main-worthy); that residue is
not a mechanistic claim or framework behavior edit.

Run:

```bash
python3 .claude/skills/self-improve/scripts/validate_lemmas.py --root .
```

The validator checks structure and evidence declarations; it does not prove causality. The
independent reviewer must inspect cited raw artifacts and the actual diff.

## What to update

- New deterministic prohibition: route upward to the owning gate/constraint mechanism.
- Directly broken used artifact: repair the owning skill, agent, flow, or deterministic gate.
- Scoped mechanistic learning: update one opt-in lemma; only supported findings may feed a durable
  carrier through separate review.
- Terse run residue: append at its source layer under `.memory/`; use an inbox file only when the
  main session must know it.
- Cross-task proven knowledge: after the same transfer-admission check, encode the reviewed subset
  in a supported lemma, learned reference, or other repository-owned carrier. External publication
  requires a separate explicit user request and is not part of this skill.

## Distill rarely, dedupe first

Most rounds produce no new reusable artifact. When a capability genuinely emerged, search existing
skills/agents/flows first, prefer updating a near-duplicate, and distill at most one artifact. Do not
turn a conjecture into a new instruction merely to make the round look productive.

## BitLesson propagation

A sub-flow reports only its distilled BitLesson upward, not raw traces. A reusable BitLesson must
pass transfer admission; otherwise report `Action: none` and keep the observation in project memory. Use
`scripts/bitlesson_init.sh`, validate deltas with `scripts/bitlesson_validate_delta.sh`, and let the
`bitlesson-selector` agent select relevant lessons before a sub-task.

## References

- `references/lemma.template.md` — lemma schema, status meanings, and evidence contract.
- `references/cross-task-reference.template.md` — required wrapper for learned references.
- `scripts/validate_lemmas.py` — opt-in lemma directory validator.
- `references/bitlesson.template.md` — BitLesson seed.
- `.workflow/designs/standard-round.workflow.json` — canonical reviewed lifecycle.
- `.subagent/flows/standard-round.json` — active built-in mapping; validate its design digest
  before use.
