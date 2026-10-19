# Bounded multi-model consultation

Use this contract for non-terminal judgment during a planning proposal or result analysis. It is
not a standing ensemble and does not replace terminal review.

## Admission

Consult at the proposal stage when at least one of these is true:

- two or more materially different approaches remain plausible;
- architecture, hypothesis, measurement, or rollback assumptions are load-bearing;
- choosing the wrong direction would create substantial downstream work;
- the current planner and evidence share a likely correlated blind spot.

Consult at the result-analysis stage when the observation is surprising, contradictory,
inconclusive, unexpectedly strong, proxy-sensitive, or part of a repeated plateau. A routine,
well-measured local result does not need a panel.

Default mode should prefer one consultation for a consequential autonomous decision. Fast mode
uses at most one parallel consultation round only when it is likely to avoid the next failed cycle
or materially clarify what the human should inspect. It must not turn a short human-guided loop
into broad idea review.

## Execution

1. Give the primary planning/analysis route and the advice route the same bounded task, invariant
   constraints, acceptance criteria, and raw evidence. Ask each for assumptions, proposal or causal
   interpretation, falsifier, and risks.
2. Obtain independent proposals before either branch sees the other's answer. Do not ask one model
   merely to agree with, polish, or critique a revealed incumbent.
3. Use one bounded reducer to compare evidence and assumptions. Preserve material disagreements,
   record why one option was selected or why the choice remains unresolved, and keep minority
   evidence. Reduction is not majority voting.
4. Stop after that synthesis. A recursive advice panel needs a separately admitted workflow and
   explicit model-call budget.

Consultation is non-terminal. It never waives deterministic validation, experiment/proof validity,
code and alignment checks, evidence lineage, or the fresh independent/machine-gated terminal
review. An adviser may propose or interpret; it cannot declare the task complete.

## Model and effort routing

Use semantic roles from the freshly validated project model policy:

- `plan` owns the primary proposal or synthesis.
- `frontier_advice` is the Opus 5 advice route for normal complex or bounded consultation.
- `frontier_advice_fable` is the stronger Fable 5.1 (`claude-fable-5-1`) route for hard,
  long-horizon, algorithmic, or materially ambiguous advice when the exact route is fresh-catalog
  admitted and the task accepts Fable's retention policy.
- `frontier_advice_astra` is the active strong GPT-6 Astra (`gpt-6-astra`) route for the
  same scoped advice class, subject to exact Codex live-catalog admission.
- A named `diversity_*` route tests a specific correlated blind spot; it is not a default third
  voter. Cursor Grok is useful only when its distinct upstream family is relevant.

Never label Opus as Fable or a Cursor-hosted model as native Claude Code. Current availability is a
fresh native-catalog fact, not a claim in this reusable reference. Select every adviser from an
upstream provider opposite the actual primary writer: Fable 5.1 is eligible for a non-Anthropic
writer and Astra for a non-OpenAI writer, but neither name overrides the qualified-role lists or
the configured terminal-review pairing. If the selected candidate, exact effort, or live route is
unavailable, report the consultation unresolved; never silently substitute a different model,
version, provider, backend, or effort.

Prefer `max` reasoning effort. If a particular model/backend does not expose `max`, select the
highest provider-local effort actually present in the fresh catalog. The project role or mapping
must pin that exact effort before dispatch, mark `effortPolicy: highest-available`, and the resolver
must prove that no higher declared effort is present. This is a pre-dispatch resolution, not a
runtime downgrade. Never change the model, backend, provider, or effort after a failed call; a task
that explicitly requires literal `max` remains unresolved when `max` is absent.

Every native CLI turn follows `native-cli-execution.md`: a 3600-second timeout and only the bounded
transient retry schedule.
