---
name: rethink-workflow-design
description: "Audit and redesign a plateaued workflow or search process using evidence, current sources, and a discriminating next experiment; do not use for startup hardening."
---
> **Model tier:** HIGH — diagnose the search process and select mechanism-level changes; use cheap
> deterministic tools or configured execution roles for mechanical collection and experiments.
>
> **Memory abstraction:** Rethink the search policy, not only the incumbent: preserve a persistent idea board, make underexplored non-terminal, trigger explicit plateau escapes, and compress cost only against a pinned quality baseline.

# rethink-workflow-design

Treat the workflow as an optimization target. Diagnose its search dynamics and costs, scan current
community mechanisms, maintain a durable idea board, and test bounded redesigns. Popularity is a
discovery signal; transfer requires primary evidence and a mechanism that fits the local failure.

## Required inputs

- workflow, flow trace, or skill to rethink;
- pinned task acceptance and constraints;
- current baseline: quality/pass rate plus model calls, input/output tokens, wall time, and scarce
  external compute where available;
- whether the task is analysis-only or authorizes implementation;
- experiment budget and time horizon.
- the declared iteration policy (`autonomous` or `human_guided`), human check-in lease and fallback
  if present, plus the review gates that must remain invariant.

Before acting, inherit `../writing-plans/references/invocation-envelope.md`. Treat the user's
authority as a hard ceiling. An analysis-only request is `authority=read_only`: keep the idea board
ephemeral in working context or a disposable temporary location, do not create/update the durable
board, do not run an experiment, and return recommendations plus proposed falsifiers only. Durable
board writes require `write`; trials require `experiment` plus declared resources and budget.

If a baseline is missing, measure the smallest representative one before recommending cost or
quality improvements. Do not infer convergence from a single run.

For an iterative design, preserve the neutral `iterationPolicy` contract while changing the search
policy. `autonomous` keeps idea/counterevidence review before a trial. `human_guided` may skip only
that idea-level review after the minimum validity gate, then must reduce deterministically and exit
to a checkpoint for bound human observation before another planning turn. It never skips authority,
safety, experiment/proof validity, evidence reconciliation, code/alignment review, or terminal
review. A lease expiry, unavailable human, contradiction, ambiguity, plateau/repeated failure, weak
proxy, or irreversible/high-cost/safety-sensitive action must record a risk-triggered disposition
and route to targeted idea review or the explicit autonomous fallback.

Treat the supplied mode, lease, and fallback as an outer-owner snapshot. A nested evidence scan or
experiment inherits it and cannot renew the lease or silently tune itself into the other mode; it
returns the checkpoint or fallback evidence to the owner.

## Procedure

### 1. Reconstruct the current search policy

Read the flow, prompts, state schema, selector, stop rules, and recent observability traces. Produce
a compact map of:

- what constitutes an idea, candidate, trial, score, failure, and terminal;
- which routes are actually used and which remain unreachable;
- how much budget each route consumes;
- where context grows, evidence is lost, or a noisy proxy becomes a hard verdict;
- whether unsuccessful work is retained with reasons;
- whether `underexplored`, `standby`, `exhausted`, and `invalid` are distinguishable.

Use outcome quality and route-level traces together. A final score says whether the workflow
improved; the trace says what to change.

### 2. Open or resume the idea board

Use `references/idea-board-template.md`.

The paths below apply only with write authority. For analysis-only work, render the same schema as
an ephemeral board in the returned artifact and leave repository, `.pm`, memory, and external state
unchanged.

- Use a coordinator-supplied path when present.
- Otherwise, if `.pm/config.json` exists, use `.pm/ideas/workflow-<slug>.md`.
- Otherwise use `workflow-output/rethink/<slug>/idea-board.md`.

Separate mechanism-level ideas from concrete hypotheses/trials. Import existing attempts before
adding new ones. Deduplicate by mechanism, retain negative evidence, and record a reactivation
condition. Do not erase failed or pruned directions.

### 3. Scan current community evidence

Search each time the skill is used for a material redesign; the bundled evidence note is a dated
starting point, not a current catalog.

Use `$deep-research-workflow` as a **child evidence scan** when installed, passing
`invocationMode=child`, `authority=read_only`, the inherited `terminalOwner`, and an
`activeSkillStack` containing `rethink-workflow-design`. That child must not re-enter `rethink-workflow-design`,
run experiments, emit the outer terminal, or write memory; it returns
only scoped evidence and provenance. Otherwise follow the same evidence-first process below.

1. Search local project skills/flows and use `$marketplace-search` before greenfield authoring.
2. Search current GitHub repositories for implemented mechanisms. Verify stars, recent activity,
   license, documentation, and the exact code or docs that support the claimed behavior.
3. Search arXiv, OpenReview, and official proceedings for primary papers. Prefer the paper/repo from
   the authors over summaries.
4. Query the observed failure mode and proposed mechanism, not only broad phrases such as “best
   agent workflow.” Include contrary evidence and limitations.
5. For a focused rethink, target at least 10 strong primary sources; for a major redesign, target
   about 20 when the evidence exists. Record the search date.
6. Label every important statement as **Fact**, **Inference**, or **Proposal**. Separate the current
   state from forecasts.

Record live star and citation counts with the retrieval date when they are used to rank sources.
They prioritize what to inspect; they do not validate the mechanism.

Useful query families:

- `agent workflow early termination persistent search solution tree`
- `agent optimization local optima backtracking idea pool archive diversity`
- `adaptive agent workflow token efficiency pruning scheduler`
- `Kaggle agent iterative improvement experiment memory`

Do not install or execute community code during research. Inspect dependencies, license, and trust
boundary before proposing adoption. Load `references/community-evidence-2026-07.md` when the failure
matches its scope, then refresh drift-prone claims live.

### 4. Add mechanism-distinct ideas

For every new board entry record:

- source and source date;
- local failure it addresses;
- transfer mechanism, not a copied product label;
- novelty relative to existing clusters;
- falsifier and minimum evidence budget;
- estimated calls/tokens/time/compute;
- dependencies and safety constraints.

Keep the board hierarchical: a macro idea may own several atomic trials. Reject paraphrase-only
novelty. Use a bounded active set so context stays small; inactive ideas move to `STANDBY` with a
summary and remain recallable.

### 5. Select a balanced experiment portfolio

Pin a deterministic policy over normalized dimensions:

- expected quality or exploit value;
- uncertainty and coverage deficit;
- mechanism novelty/diversity;
- estimated cost and reversibility;
- cooldown for very recent failures.

Reserve explicit budget for uncovered clusters or use a non-zero exploration probability. Never
let current score be the sole selector. Start with the cheapest experiment that can falsify the
mechanism, not the cheapest experiment overall.

Under analysis-only/read-only authority, this is a proposed portfolio: do not run an experiment or
record a trial as executed.

For multiple independent workers, prefer isolated islands with occasional evidence exchange over a
shared transcript. Cross over ideas only when their mechanisms and state assumptions are compatible.

### 6. Run bounded, staged experiments

Enter this step only with `authority=experiment` and the required resource/budget receipts. With
`read_only` or `write`, emit the experiment contract and expected evidence without executing it.

Each trial changes one mechanism where practical and declares:

- hypothesis and expected trace change;
- candidate artifact;
- deterministic validity check;
- cheap proxy and its known blind spots;
- representative acceptance set;
- maximum resource budget;
- decision rule for promote, standby, or exhaust.

Evaluation order is deterministic validity → cheap proxy → covering acceptance → full expensive
review. A proxy may avoid expensive evaluation, but one noisy failure cannot exhaust a new idea
before its minimum evidence rule. Update the board after every trial, including failures.

### 7. Escape a plateau explicitly

Use scale-normalized relative progress and a smoothed momentum window rather than raw score change
alone. When the pinned plateau threshold is reached, prohibit another identical retry. Choose one:

1. backtrack to an earlier parent and discard only the recent micro-context;
2. activate a viable idea from a different cluster;
3. recombine compatible ideas from separate branches;
4. refresh current community evidence for a mechanism not represented on the board;
5. challenge the evaluation or problem decomposition without weakening acceptance.

Write why the next action differs from the failed lever. An exhausted per-trial budget sends its
parent idea to `STANDBY` unless the idea-level falsifier or minimum evidence condition is met.

In `human_guided`, a plateau is never hidden by the shorter route: it is one of the conditions that
restores idea-level review or changes the mode. The reduced checkpoint must preserve the raw
observation, the agent's interpretation, an alternate interpretation, and candidate next tasks so
the human can add ideas without having to reconstruct the experiment from a transcript.

### 8. Compress cost with a quality guard

Apply cost changes separately so their contribution is measurable:

- replace repeated deterministic tool-call sequences with regression-tested scripts/meta-tools;
- pass summaries, state deltas, and artifact pointers instead of full trajectories;
- route easy, high-confidence cases to a compact lane and uncertain/high-risk cases to the heavy
  workflow;
- remove, merge, or cheapen nodes only against the same baseline acceptance set;
- cache immutable evidence and reuse validated development cases during deployment;
- track a quality-cost Pareto frontier instead of optimizing tokens alone.

Record cost per accepted result. A cheaper run that fails more often may be more expensive in use.

### 9. Implement only the supported redesign

If implementation is authorized, use `$build-workflow` and the closest reference in its
`references/designs/` directory. Change the canonical design before changing a platform mapping,
unless the defect is adapter-only. Validate the design, then invoke the selected harness skill and
run representative acceptance. A top session dispatches that implementation to a bounded native
subagent or admitted workflow and only inspects the diff, trace, and evidence; it never edits the
design, mapping, tests, or harness itself. Protected control-plane changes still require the
sanctioned authorization and selected independent or machine-gated review route.

Do not embed an autonomous web crawler into every routine workflow. Community refresh is a
plateau/new-scope event; stable exploitation should consume the curated board and compact evidence.

### 10. Close with a coverage audit

Report:

- baseline and final quality/cost metrics;
- board changes and experiments run;
- accepted, standby, and exhausted ideas with evidence;
- graph/prompt/state changes and validation output;
- open evidence gaps and one next selector decision.

Claim convergence only when acceptance passes and the board has no eligible promising or
high-uncertainty underexplored direction within the declared horizon. Budget expiry with such ideas
remaining is a checkpoint, not success. Follow the shared invocation envelope: a child returns its
coverage artifact and receipts to its caller without terminal review or memory. Only this
assignment's `terminalOwner` sends the assembled final state to the selected review route. It
handles `COMPLETE | HUMAN_BLOCKED | REVISE`; a terminal reviewer returns directly without recursion.

## Non-negotiable rules

- `UNDEREXPLORED`, `EXPLORING`, and `STANDBY` are not synonyms for failure.
- Treat `iterationPolicy` as a platform-neutral contract. Never add a harness, provider, model, or
  effort identifier while redesigning it, and never use `Fast` as a persisted mode value.
- Keep negative experiment memory so dead levers are not retried without a stated change.
- Do not weaken acceptance to make a cost optimization look successful.
- Do not use star count, citation count, or a leaderboard claim as mechanism evidence by itself.
- Do not pin model ids from memory; use the project config and live catalog.
- Keep full source extracts and traces outside prompt state; use compact, linked evidence.
- Treat paper metrics as study-specific until locally reproduced.
- Do not let the top session implement a redesign or self-approve completion. Use a delegated
  worker; only the outer `terminalOwner` requests the fresh independent terminal review.

## References

- `references/idea-board-template.md` — durable board, state transitions, and stop audit.
- `references/community-evidence-2026-07.md` — dated primary-source synthesis covering Agent K,
  AIDE, PACEvolve, search diversity, early stopping, and token-efficient workflow optimization.
- `../build-workflow/references/designs/idea-board-optimization.md` — graph-level reference.
- `../build-workflow/references/designs/adaptive-cost-cascade.md` — cost-routing reference.
