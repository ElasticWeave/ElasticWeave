# Supervisor model-routing heuristics

Use this reference when the user supplies an available model set or when a planned node needs a
semantic role. It is an operator heuristic, not a claim that a model is universally better. The
canonical role resolver still checks the exact backend, model identifier, provider, effort, account,
and current live catalog before dispatch. A name in this table is a candidate until that check
passes; an unavailable candidate remains unresolved and is never silently substituted.

## Harness and identifier rules

| Model family | Preferred CLI or harness | Current identifier to verify | Notes |
|---|---|---|---|
| GPT family | Codex | `gpt-5.6-sol`, `gpt-6-astra`, `gpt-5.6-luna` | Project snapshots; resolve exact IDs and effort through Codex. |
| Claude family | Claude Code | `claude-opus-5`, `claude-sonnet-5`, `claude-fable-5` | Current vendor names. The repository compatibility snapshot `claude-fable-5-1` remains a candidate until live resolution confirms that exact route. |
| GLM family | Claude Code | `glm-5.3`, `glm-5.3-flash` | Active candidates supplied by the operator; confirm the provider route at startup. Advisory until a Claude Code route is admitted by the resolver. |
| Kimi family | Claude Code | `k3` | Kimi Code's API identifier (display name: Kimi K3); advisory until a Claude Code route is admitted by the resolver. |
| DeepSeek family | DSH or Codex | `deepseek-flash` | DeepSeek currently documents this alias for V4.1 Flash. Advisory until a DSH/Codex route is admitted; `deepseek-v4.1` is not treated as a verified API ID. |
| Grok family | Configured provider or Cursor route | `grok-4.6` | Verify the current provider route; `grok-4.7` is a future candidate until an official ID is available in the live catalog. |

The project role file may use a relay or Cursor backend for a model family. Do not change that
backend merely because an operator prefers another CLI: add or activate a role only after the
identity map, validator, and live route all admit it. Effort labels are provider-local. `max` on
one backend is not evidence of equivalence to `max` on another.

When the supervisor is explicitly told that a third-party model is available, it may prepare
convenience aliases in the user's `~/.bashrc`; this is a user-level side effect and never a dispatch
mechanism. The bounded setup worker must require an already-set
`GENERAL_AGENT_ALLOW_USER_CONFIG_WRITE=1` opt-in (it must never set that variable itself), use one
idempotent, clearly delimited managed block, preserve every user-owned line, never write credentials,
and use the existing config-sync transaction primitives or leave a reviewable block for the human.
Derive the block only from models declared in the startup brief; do not prepopulate every provider.
Before writing, accept only model IDs matching `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`, alias names
matching `[A-Za-z_][A-Za-z0-9_]{0,63}`, and executable paths/arguments supplied as separately quoted
argv fields; reject shell metacharacters and unknown fields. Verify the exact command and model ID in
a disposable interactive shell. Since aliases do not affect non-interactive native launches, actual
dispatch must still use a fresh canonical route receipt and the selected harness. If the file, route,
or safe writer is unavailable, report that fact and leave the alias unresolved.

For a verified executable route, the generated form is one alias per declared model, with a
normalized name and a quoted command, for example:

```bash
alias ew-codex-gpt_5_6_sol='codex --model gpt-5.6-sol'
alias ew-claude-opus_5='claude --model claude-opus-5'
```

Only emit a line when the startup brief supplies the harness command, model ID, and provider
configuration needed by that command. A Claude Code alias for GLM or Kimi therefore requires a
working third-party provider route first; it must not imply that `claude --model` alone makes the
model available. Native workers still launch through the resolver, not through these interactive
aliases.

## Ordered role heuristics

The first entry is the user's cost-aware preference. The `project roles` column gives the closest
existing semantic role; it does not activate a candidate or override a fresh route receipt. Within
a row, select the first live-qualified candidate that satisfies the task contract and then record
an observable escalation trigger.

| Role | Use for | Preferred model order and effort | Project roles / escalation signal |
|---|---|---|---|
| Supervisor or sub-supervisor | Goal decomposition, dependency/topology choice, monitoring, conflict routing | `gpt-5.6-sol` (Max) → `gpt-6-astra` (Max) → `glm-5.3` (Max) → `glm-5.3-flash` (Max) | `slow` → `frontier_execute` → reviewed GLM candidate. Escalate only when decomposition or integration evidence is inadequate. |
| Planner and designer | Hypotheses, experiment/proof design, acceptance and falsifier | Fable (`claude-fable-5` or the project snapshot `claude-fable-5-1`) (Max) → `claude-opus-5` (Max) → `gpt-5.6-sol` (Max) → `glm-5.3`/`deepseek-flash` (Max or xhigh) | `frontier_advice_fable`/`frontier_execute_anthropic`/`slow`/candidate. Escalate when assumptions or falsifiers remain unresolved. |
| Analyst | Result interpretation, comparison, causal diagnosis | Same order as Planner | `frontier_advice_fable` or `frontier_execute_anthropic`/`slow`; use collection roles only for extraction, not consequential interpretation. |
| Frontier executor | Load-bearing implementation, difficult proof, high-risk experiment | `claude-opus-5` (Max) → `gpt-5.6-sol` (Max) → `deepseek-flash` (Max or xhigh) → `glm-5.3`/`glm-5.3-flash` (Max) → `k3` (Max) | `frontier_execute_anthropic`/`frontier_execute`/candidate routes. A candidate requires task evidence and independent review. |
| Research collector | Scoped retrieval, source extraction, citation formatting | `gpt-5.6-luna` (Max) → `deepseek-flash` (High or Medium) → `gpt-5.6-sol` (Medium) | `research_collect`; use `research_analyze` only when interpretation is required. |
| Reducer or integrator | Merge compatible outputs, preserve disagreement, form one scoped result | `claude-opus-5` (Max) → `gpt-5.6-sol` (Max) → `deepseek-flash` (Max or xhigh) | Qualified integration owner, never an automatic majority vote. Keep raw evidence and unresolved conflicts. |
| Reviewer | Independent code, proof, evidence, alignment, and reward-risk review | `claude-opus-5` (Max) → `deepseek-flash` (Max or xhigh) → `k3` (Max) | Use the exact opposite-provider reviewer pairing for terminal review. The fallback candidates require explicit reviewed activation. |
| Diversity reviewer | Named correlated blind spot or provider-specific failure mode | `claude-opus-5`, `gpt-5.6-sol`, `glm-5.3`/`glm-5.3-flash`, `deepseek-flash`, `k3`, then `grok-4.6` (all Max or xhigh where supported) | Dispatch one or more independent routes only for a named blind spot; compare evidence rather than voting. |
| Advice or RSI | New direction after a surprising result or genuine plateau | Fable (`claude-fable-5` or snapshot `claude-fable-5-1`) (Max) → `gpt-6-astra` (Max) | `frontier_advice_fable` / `frontier_advice_astra`; advice is non-terminal and scoped to one mechanism or decision. |
| Support worker | Mechanical edits, fixed-format transforms, predetermined runs, evidence packaging | `gpt-5.6-luna` (Max) | `smol`/`tiny`/`commit` or `research_collect` when semantic reading is needed. Escalate a new architectural question. |
| Vision | Image, chart, interface, or visual evidence extraction | `gpt-5.6-luna` (Medium) → `deepseek-flash` (Medium) | `vision` or a live-qualified multimodal candidate; preserve the original image evidence. |

### Dispatch algorithm

1. Classify the node by its required decision: deterministic, collection, analysis, planning,
   load-bearing execution, integration, verification, diversity, advice, support, or vision.
2. Select a row and walk its ordered candidates. Check task evidence, autonomy required,
   ambiguity, tool reliability, provider independence, effort support, and total delivery burden
   (briefing, calls, review, rework, integration, and time), not model price alone.
3. Resolve the first eligible candidate through the canonical resolver. Record `task_class`,
   `semantic_role`, exact model and effort, reason, and one observable escalation trigger in the
   dispatch brief. Never inherit the supervisor's model implicitly.
4. If capability is unknown, assign one small real and observable task before widening. A failure
   is diagnosed as missing context, tool/access, decomposition, or capability evidence before
   reassignment. Do not climb a fixed model-strength ladder.
5. Reserve mandatory reviewer and integrator capacity before increasing fan-out. Independent
   ready leaves may run concurrently; shared read-only evidence is not a dependency. Add a
   `resource_limit` serialization reason only for a real named bottleneck and a revisit condition.
6. If a candidate cannot satisfy the contract, return `NEEDS_ESCALATION` with evidence and open a
   new explicit dispatch. Do not silently switch model, CLI, provider, backend, effort, or role.

## Sources and update rule

The exact identifiers above were checked against vendor documentation on 2026-09-11. OpenAI's [model
documentation](https://developers.openai.com/api/docs/models) is the reference for the GPT family;
Anthropic's [model status](https://docs.anthropic.com/en/docs/about-claude/model-deprecations) lists
`claude-opus-5`, `claude-fable-5`, and `claude-sonnet-5`; the operator has confirmed `glm-5.3` and
`glm-5.3-flash` as active candidates; Kimi Code's [model list](https://www.kimi.com/code/docs/en/kimi-code/models.html)
uses `k3`; DeepSeek's [V4.1 Flash notice](https://www.deepseek.com/en/news/deepseek-v4-1-flash/)
instructs clients to use `deepseek-flash`; and xAI's [model documentation](https://docs.x.ai/developers/models)
names `grok-4.6`. These checks establish names and routing syntax, not comparative competence.
Recheck the live native catalog before each use and update this table when a model or alias changes.
Preserve the role schema and reviewer rules when updating it.
