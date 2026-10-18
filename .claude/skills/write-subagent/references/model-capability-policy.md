# Semantic model capability policy

Use this reference when selecting or repinning a reusable agent role. The policy separates three
facts that must not be collapsed:

1. `backend` and `provider` identify the executable native route.
2. `upstreamProviderFamily` and `modelFamily` identify model provenance for the required reviewer
   independence checks; they do not prove task competence or uncorrelated errors.
3. `model` and `effort` are refreshable project snapshots proven through that backend's
   authenticated live catalog.

The distributable `models.json` is intentionally `bootstrap-unverified`. Resolve only the exact
routes needed by the first dispatch, then additional roles on demand. Every selected route needs
fresh authenticated per-dispatch resolution of its exact identity, backend, and expressible effort.
The existing launch resolver and its `workflow-native-launch-receipt/v1` supply that authorization;
persisted catalog status and caller-authored evidence do not. A bootstrap snapshot can remain
unverified while selected routes dispatch through successful fresh resolution.

Do not mark a partial catalog snapshot `verified`. The validator requires `verifiedRoles` and
`verifiedBackends` to exactly cover every active role/backend with the required evidence receipt;
`bootstrap-unverified` keeps both lists empty and omits `evidenceReceipt`. Full verification is deferred until that complete
snapshot is needed. Never copy receipts between machines/accounts or use an old receipt as launch
authority. Missing models, ambiguous aliases, authentication failures, or unsupported effort leave
the selected route unresolved; never silently substitute model, version, provider, backend, or effort.

Eligibility is separate from demonstrated capability. Select among policy-allowed roles using
task evidence for competence, autonomy, ambiguity handling, instruction/tool reliability,
coordination, and uncertainty honesty. Minimize total briefing, calls, review, rework, and integration
burden, not call price alone. Unknown capability warrants a small real, low-risk assignment or early
sample when needed, not a full cold-start benchmark. Read
`../../default-mode/references/heterogeneous-team-management.md` for assignment and escalation rules,
and `../../default-mode/references/model-routing-heuristics.md` for the cost-aware task-to-role order,
harness mapping, current candidate IDs, effort defaults, and dynamic alias policy.

## Stable routing intent

- Prefer `frontier_execute` (GPT-6 Astra Max) for the main quality-first executor. `slow` remains
  an explicit Sol X-High compatibility route; `frontier_execute_anthropic` is the Opus 5 Max alternative.
- Use `frontier_advice` for normal complex Opus 5 advice. Strong candidates for hard or materially
  uncertain advice are the project Fable 5.1 compatibility snapshot (`claude-fable-5-1`) through
  `frontier_advice_fable` and GPT-6
  Astra (`gpt-6-astra`) through `frontier_advice_astra` after fresh live-catalog admission;
  neither replaces the completion reviewer.
- Use `research_collect` for Luna Max collection or `research_collect_anthropic` for Sonnet 5 High
  collection. Re-evaluate relative cost on the current project; role rank is not vendor pricing.
- Resolve the actual writer execution before review. The shipped exact pairing reviews an OpenAI
  Astra writer with the Anthropic Opus `advisor` at Max and an Anthropic Opus writer with the OpenAI
  Astra `codex_advisor` at Max. Treat any selected pairing as exact
  and opposite-provider, not merely a different role or CLI name. An unavailable or unverifiable
  selection remains unresolved.
- `diversity_kimi` and `diversity_glm` are Cursor candidates. `diversity_grok` is active only at the
  highest effort proven for its exact Cursor model. An adaptive Cursor advice or diversity role
  prefers literal `max`; when the exact model lacks it, pin the highest provider-local effort and
  declare `effortPolicy: highest-available`. This is a pre-dispatch resolution, never a retry
  downgrade or backend substitution. These roles remain ineligible through a harness that cannot
  preserve the route.
- DeepSeek is a future Cursor candidate behind the same live-model-plus-highest-effort gate.
  Composer 2.5 is excluded until an explicit policy revision.

OpenAI's current model guidance documents `gpt-5.6-sol` as the flagship family member,
`gpt-5.6-luna` as the cost-sensitive member, and `none` through `max` as GPT-5.6 reasoning-effort
settings: <https://developers.openai.com/api/docs/guides/latest-model>. Treat non-OpenAI family
labels in the bootstrap as requested semantic targets, not vendor availability claims; native
catalog verification remains authoritative for the current account.

## Candidate activation

The field changes below are sufficient only when the synchronized Python validator, TypeScript
controller policy, and native identity map already admit that backend/family as active. Astra is
admitted by those synchronized surfaces. Terminal review remains opposite-provider: Astra writers
use Anthropic Opus, while Anthropic Opus writers use Astra.

For a candidate whose enforcement surfaces already support activation:

1. Repin `model` to the exact live id.
2. Set `effort` to the highest provider-local control proven for that exact model, remove
   `requiredEffort`, and add `effortPolicy: highest-available`.
3. Set `dispatchStatus` to `active` and add the effort to `backendEffortLevels` only if the native
   backend actually exposes it.
4. Keep `catalogResolution` `bootstrap-unverified` with empty coverage lists and no `evidenceReceipt`
   unless fresh evidence covers every active role/backend; only then rebuild the exact lists, bind
   the required receipt, and mark the snapshot `verified`.
5. Run `validate_model_roles.py` and the agent-routing tests before platform sync. A fresh
   per-dispatch receipt is enough to authorize that dispatch; it is not enough to generate or
   synchronize native agent surfaces. Before any platform sync, the synchronized loader still
   requires a complete, authenticated, all-active-role/backend catalog snapshot marked `verified`.
   If that coverage is unavailable, keep the snapshot `bootstrap-unverified` and defer sync while
   leaving unrelated, freshly resolved dispatches eligible.

Never mark a role active merely because the model name appears in a document, another provider, or
a different account's catalog. Candidate activation never authorizes silent fallback: if the exact
selected id, provider family, route, or effort cannot be freshly verified, leave it unresolved.

For nonterminal planning/result consultation and pre-presentation Simplify -> Verify, also read
`.claude/skills/default-mode/references/multimodel-consultation.md` and
`.claude/skills/default-mode/references/review-and-simplify-verify.md`. Strong scoped advice
candidates are the project Fable 5.1 compatibility snapshot (`claude-fable-5-1`) and GPT-6 Astra
(`gpt-6-astra`) after fresh exact
route resolution; the Cursor-hosted Opus role remains normal complex advice. Use a freshly resolved
Cursor Grok route only for an optional named diversity blind spot. Simplify -> Verify never requires
Grok and must not block solely because Grok is unavailable. These preferences do not weaken or
replace the actual-writer-opposite literal-Max terminal-review pairing, and no unavailable selected
route may be silently substituted.
