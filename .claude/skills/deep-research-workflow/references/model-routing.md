# Cost-aware model routing for research workflows

This reference maps node intelligence demand to semantic roles. Exact model ids live only in
`config/workflows/models.json`; they are refreshable snapshots, not portable workflow semantics.
The top session authorizes one global reasoning posture and dispatches bounded planning/mapping
owners that choose the cheapest qualified role for each node; the top inspects the resulting
profile and mapping. A subagent inherits that envelope; it does not inherit the top session's model
merely because the parent used it.

## Evidence boundary

Use three evidence layers and keep them separate:

1. **Availability:** the host's live native catalogs and authenticated routes decide whether a
   model can be selected now.
2. **Price:** current provider or relay billing documentation decides monetary cost. Do not infer
   price from a model name, effort label, or benchmark rank.
3. **Capability:** held-out project tasks decide whether a cheaper role meets the node's acceptance
   threshold. Community reports and vendor benchmarks generate candidates; they do not establish
   project equivalence.

Resolve availability at dispatch time. Load the canonical project role config, query each required
role through its authenticated native CLI, and verify the exact model id, provider identity, route,
and supported effort vocabulary before materializing a brief or mapping. A model mentioned by an
API, benchmark, or earlier run is not available evidence for this dispatch. For independent review,
compare against the actual resolved writer execution and verify that the reviewer's upstream
provider differs from it. In short, the reviewer provider differs from the writer provider, where
"writer" means the actual resolved execution rather than the plan. A planned semantic role or CLI label is insufficient. Strong
direction-qualified routes are Fable 5.1 (`claude-fable-5-1`) for an OpenAI-family writer and
GPT-6 Astra (`gpt-6-astra`) for an Anthropic-family writer, subject to fresh admission.

Fail closed when a required role is absent, authentication fails, effort support is ambiguous, or
provider separation cannot be established. Return an unresolved routing result with the failed
check; do not invent an id, reuse an earlier catalog observation, silently substitute a model, or
degrade independent review to the writer's provider.

For a role explicitly marked `effortPolicy: highest-available`, prefer `max` but allow the exact
same model/backend at the highest provider-local effort present in the fresh catalog when `max` is
absent. Pin that actual effort before dispatch and require the resolver to prove that no higher
declared effort exists. This is not a post-failure downgrade and never changes model, provider, or
backend. A literal-Max task remains unresolved without `max`.

The role file's `relativeCost` is a project routing rank, not a dollar price. Recalibrate it when
billing or model behavior changes. Treat `backendEffortLevels` as backend-local dispatch
vocabulary, validate every role/effort pair against the live native route, and never compare effort
labels across providers as if they shared a scale.

## Global reasoning posture

Choose one posture before dispatch and include it in every native-subagent brief or workflow
specialization:

| Posture | Use | Default behavior |
|---|---|---|
| `economy` | Orientation, routine maintenance, or high-volume collection | Deterministic work and the cheapest calibrated collection role first; use a cheaper analysis candidate only through an explicit calibration experiment |
| `balanced` | Normal decision-grade research and implementation | Cheapest calibrated role per node; keep the project-configured incumbent for standard execution |
| `frontier` | Broad opportunity search, load-bearing theory, or a plateau with underexplored directions | Widen evidence first; selectively strengthen planning, synthesis, and verification; leave mechanical collection cheap |

The posture is a dispatch envelope, not one provider's raw `effort` flag. Effort labels from
different backends are not assumed equivalent. Treat any proposal to replace an incumbent with a
cheaper role as an uncalibrated candidate hypothesis. Expose it only for an explicit economy
experiment; keep the standard role on its calibrated route until a covering held-out replay
supports promotion.

Every delegated contract records:

```json
{
  "reasoningPosture": "balanced",
  "intelligenceClass": "collection | analysis | planning | topIntelligence | verification | frontierAdvice | diversity",
  "role": "research_collect",
  "reason": "why this is the cheapest role likely to pass",
  "escalationTrigger": "observable failure that justifies the next role",
  "maximumRole": "frontier_execute"
}
```

If the current role cannot meet the contract, the leaf returns `NEEDS_ESCALATION` with evidence.
It must not silently switch models, raise effort, or spawn an expensive verifier.
For a native-subagent lane, the top may authorize a new, explicit dispatch. A static
workflow changes roles only when its validated design and mapping already contain a statically
expanded escalation branch and deterministic route. Otherwise, escalation requires a newly
validated design and mapping from a bounded designer/mapper before another workflow run; the top
inspects that evidence, and the current run does not change roles.

## Intelligence classes and role ladder

| Node demand | Typical work | Initial role | Escalation |
|---|---|---|---|
| Deterministic | Schema validation, deduplication, matrices, counters, gates | no model | Fix the tool or contract; never buy reasoning for a deterministic bug |
| Mechanical | Frontier selection, simple lookup, formatting checks, commit metadata | `smol` / `tiny` / `commit` | `research_collect` only if semantic reading is actually required |
| Collection | Locate, read, extract, and cite already-scoped evidence | `research_collect` | `research_analyze` for conflicting or interpretation-heavy evidence |
| Analysis / execution | Compare sources, implement a bounded change, diagnose a local issue | `task`; `research_analyze` only as an explicit economy/candidate choice | Explicit new dispatch after a candidate miss; static workflows need a pre-expanded validated branch |
| Planning / synthesis | Define claim dimensions, integrate contradictions, derive hypotheses, root-cause trials | `plan` | `frontier_execute` only for a load-bearing top-intelligence judgment already admitted by policy |
| Top-intelligence execution | Mainline advice, synthesis, or execution whose failure is load-bearing | `frontier_execute` (Astra) preferred; `frontier_execute_anthropic` qualified alternative | Both require live resolution at Max; `slow` is a Sol X-High compatibility alternative, not an automatic retry |
| Scoped frontier advice | Produce one independent alternative proposal, or challenge one writer-originated hypothesis, validation design, simulation, mechanism chain, or surprising result interpretation | `frontier_advice`; strong candidates `frontier_advice_fable` on Fable 5.1 (`claude-fable-5-1`) and `frontier_advice_astra` on GPT-6 Astra (`gpt-6-astra`) after exact live-route admission | One scoped call; not a second author or completion reviewer; a selected unavailable identity is unresolved, never substituted |
| Independent verification | Check a writer's completed claim set or implementation | Shipped exact pairing: OpenAI Astra actual writer -> Anthropic Opus `advisor`; Anthropic Opus actual writer -> OpenAI Astra `codex_advisor` | Reviewer is Max and opposite-provider; exact fresh live-catalog resolution is mandatory and an unavailable route remains unresolved |
| Diversity probe | Test a named correlated blind spot with a distinct upstream family | one of `diversity_glm`, `diversity_kimi`, `diversity_grok` | Highest fresh Cursor effort; compare disagreements rather than voting |

Escalation order is local to the failing node. A weak synthesis does not justify upgrading six
collection branches. A broad research profile usually increases branch count and source-class
coverage before it increases branch intelligence.

## Bounded proposal and result-analysis consultation

Use one consultation round when a proposal has materially different plausible approaches or when
the observation is a surprising, contradictory, or inconclusive result whose interpretation
changes the next experiment. Give the primary `plan`/analysis route and the advice route the same
constraints, acceptance criteria, and raw evidence. Obtain an independent alternative proposal
before either branch sees the other's answer.

One reducer compares assumptions, falsifiers, and evidence, must preserve material disagreements,
and records why it selected an option or remained unresolved. This is not majority voting. The
advice call does not replace terminal review, a deterministic evidence reducer, or the configured
writer-reviewer pairing. Routine deterministic choices and decisive measurements do not need this
extra call; recursive consultation requires a separately budgeted workflow.

## Live role resolution

Resolve roles from semantics rather than a remembered model catalog:

1. Read the canonical project role config and validate its role, provider, effort, cost-rank, and
   reviewer-separation fields.
2. Ask each configured native CLI for the exact route needed by the selected semantic role; verify
   authentication and effort support without mutating the role config.
3. Bind only roles that pass both config validation and live route detection. Record the resolved
   role and evidence with the dispatch artifact, not in this reusable reference.
4. Calibrate a new candidate on held-out tasks before making it an incumbent. Runtime compatibility
   or a generic provider prefix alone never makes a model a recommended role.
5. If no qualified route exists within the authorized reasoning posture, emit a typed unresolved
   routing result. Require a new reviewed role configuration or explicit dispatch decision rather
   than falling back silently.

## Deep Research node allocation

Before a workflow design is validated, classify each model node:

| Deep Research stage | Class / role |
|---|---|
| Context load, scoped source retrieval, evidence extraction, targeted refill | collection / `research_collect` (Luna-family, Max) or `research_collect_anthropic` (Sonnet-family, High), whichever live-qualified route is cheaper |
| Falsification search for an already-ranked assumption | collection / one qualified collection role; use `research_analyze` only for interpretation-heavy negative evidence |
| Core-problem lock, critical-assumption derivation/ranking, minimum-valid-check design, preflight decision | planning / `plan` |
| Bounded preflight implementation, fair-tuning envelope, or feasibility spike | analysis/execution / `task` after deterministic resource and validity gates |
| Interpretation-heavy branch, contradiction analysis, bounded implementation | analysis / `task`; `research_analyze` only when explicitly selected as an economy candidate before dispatch |
| Width selection, branch plan, coverage audit, first-principles reframe, synthesis | planning / `plan` |
| Hypothesis derivation, minimal experiment contract, trial root cause, plateau escape | planning / `plan`; use `frontier_execute` for an admitted load-bearing top-intelligence task, with `frontier_execute_anthropic` as the qualified alternate and `slow` only as the Sol X-High compatibility path |
| Reducers, schema checks, resource gate, budget checks, trial bookkeeping | deterministic / no model |
| Ordinary independent completion review | verification / shipped exact pairing: OpenAI Astra actual writer -> Anthropic Opus `advisor`; Anthropic Opus actual writer -> OpenAI Astra `codex_advisor`; reviewer Max in both directions after fresh live-catalog resolution |
| Independent alternative proposal or ambiguous result interpretation | frontier advice / one statically admitted advice role |
| Correlated-blind-spot probe | diversity / one of `diversity_glm`, `diversity_kimi`, `diversity_grok`, highest fresh Cursor effort |

Do not route assumption ranking to a cheap collection role merely because the individual searches
are cheap. This judgment determines which downstream work is allowed to exist. Conversely, once a
planning node fixes the kill set and check contract, collection and deterministic validation remain
on the cheapest qualified routes.

For a native-subagent lane, the top passes the authorized role and effort to the bounded native
dispatch and makes any later escalation a separate, explicit dispatch. For a workflow lane,
roles are fixed in the neutral design before start. The built-in scheduler may choose only among
legal runnable nodes and never changes their models. A workflow can use an escalation route only
when class-specific nodes and their deterministic route were statically expanded in the design and
validated in its mapping. Adding escalation later requires a new design/mapping validation and a
new run.

If width is selected inside an already-materialized graph, use statically declared class-specific
branch nodes and deterministic routes. Do not let a model write a model id or mutate role config in
workflow state. The simpler option is for a bounded planning owner to select and validate the
research profile during top-authorized preflight, then for a bounded designer/mapper to specialize
the design once; the top only inspects both artifacts.

## Calibration and replacement

Before promoting a new model into a normal role:

1. Verify exact id, native route, provider identity, effort support, authentication, and current
   billing. Availability through an unaudited API alone is insufficient.
2. Replay a covering held-out set for that node class. Measure acceptance pass rate, reviewer
   blocks, revisions, input/output tokens when exposed, wall time, and billed cost.
3. Compare against the incumbent on quality-cost Pareto terms. A cheaper model replaces the
   incumbent only when quality stays inside the declared tolerance.
4. Run a small forward test after changing the role. A miss, drift, prompt change, or broader task
   shape raises the role or review policy again.
5. Record the calibration inputs, results, applicability boundary, and resolved route in
   project-local evidence. Never preserve an unavailable candidate as a silent fallback.

Do not use majority voting as a substitute for calibration. Diversity is useful when the models
have a reason to fail differently and the workflow has a deterministic way to reconcile the
disagreement.

The live-resolved family posture is intentionally explicit: prefer the Astra family at Max for
`frontier_execute`; retain the Sol X-High route through `slow` when the dispatch contract permits it;
use the Opus family at Max through `frontier_execute_anthropic` as an executor alternative and
through `frontier_advice` for normal complex advice. Strong hard-advice candidates are exact
Cursor-hosted Fable 5.1 (`claude-fable-5-1`) through `frontier_advice_fable` and exact GPT-6 Astra
(`gpt-6-astra`) through `frontier_advice_astra`, after fresh route and retention-policy admission.
For completion review, resolve the actual writer execution through the shipped exact pairing: an
OpenAI Astra writer uses the Anthropic Opus `advisor` at Max and an Anthropic Opus writer uses the
OpenAI Astra `codex_advisor` at Max. If the selected route is unavailable or provider
separation is unproven, return unresolved and do not silently substitute. Never infer that matching
effort words are comparable across providers.
In the canonical family shorthand, Astra-family writer → `advisor` and Opus-family writer → `codex_advisor`;
the actual launch receipt, not the shorthand, determines the direction.

For collection, prefer the cheapest live-qualified route between the Luna-family
`research_collect` at Max and the Sonnet-family `research_collect_anthropic` at High. For a named
correlated blind spot, Kimi-, GLM-, and Grok-family Cursor roles are candidates only when the live
Cursor catalog exposes the exact route at its highest provider-local effort. A future DeepSeek-family
Cursor route needs the same
qualification before activation. Composer-family 2.5 remains excluded until an explicit reviewed
policy change. The upstream Humanize pin exposes Cursor, but the audited research launcher does
not stage or authenticate a Cursor route. A Humanize research flow that requires Fable advice or
one of these diversity roles is therefore ineligible and routes to the built-in scheduler without
substitution.
