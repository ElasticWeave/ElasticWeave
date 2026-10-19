---
name: write-subagent
description: "Author or revise reusable specialist agents for admitted workflows or repeated tasks; define trigger, least privilege, model role, and review route."
---
> **Model tier:** HIGH — authoring a specialist prompt is design work


# write-subagent — author a reusable task agent

Generate a complete, well-triggered canonical agent definition. The **#1 failure mode is a weak
`description`** (never auto-selected); **#2 is over-granting tools**; **#3 is a
model pin that silently resolves same-provider for a reviewer**. This skill hard-gates all three.

Read `references/model-capability-policy.md` when selecting, repinning, or activating a model role.
Read `../default-mode/references/heterogeneous-team-management.md` when choosing method autonomy,
support contracts, capability checks, or escalation for that role.

## Procedure
0. **Admission + reuse first.** Creating or modifying a single skill or agent definition is
   normally a bounded `native-subagent` task, not a workflow. A one-off bounded deliverable uses
   the active harness's native subagent with a task brief; it does not need a new file. For a repeated specialist or workflow
   node, check project `.omp/agents/`, this skill's `assets/project/.omp/agents/`, native built-ins, then
   `marketplace-search`. Author only if none fits.
1. **Scope to ONE specialty.** Two jobs → two agents.
2. **Name** — kebab-case, unique (first-seen-wins across discovery tiers — a clash silently
   shadows), descriptive; never `default`/`worker`/`agent`.
3. **Write a PUSHY description — the load-bearing field.** Third-person specialty + an EXPLICIT
   trigger clause ("Use proactively when …" / "MUST BE USED for …"). ALL when-to-use info goes
   HERE, not the body. REJECT a vague description.
4. **Tools: least-privilege portable names (lowercase).** reviewer/analyzer → `read, grep, glob, bash`;
   fixer → `+ edit`; author → `+ write`; researcher → `+ web_search`. Omit `tools` (full default
   set) ONLY for a genuine all-tool worker. Note: an explicit list gets `yield` auto-appended;
   `spawns: false` unless the agent genuinely dispatches (depth cap default 2).
5. **Choose intelligence class and semantic role before routing a model.** Read
   `config/workflows/models.json.reasoningPolicy`, inherit the issue's global posture, and choose
   an allowed role qualified by task evidence, minimizing total briefing, calls, review, rework,
   and integration burden. Role labels, catalog eligibility, and price are not capability evidence.
   Give experts outcome/interface constraints and method autonomy; give support members explicit
   inputs, outputs, checks, and an exception route. For unknown capability, use a small real,
   low-risk assignment or early sample with complete relevant context. Preserve user authority and
   independent review; deterministic scheduling may stay cheap while difficult semantic integration
   needs a qualified owner. Record `model-role: <role>` plus the observable escalation trigger in
   the body. Routine leaves must not inherit the authoring/coordinator model. A reusable agent does
   not silently upgrade itself; it reports `NEEDS_ESCALATION` with evidence distinguishing missing
   context/tools from a capability gap.
6. **Resolve the model FRESH from `model-role`; do not copy a model snapshot into the agent.**
   `update-platform-config` owns role resolution. Agent frontmatter declares `model-role` and omits
   `model`; generators derive the exact provider/model/effort by resolving that role from
   `config/workflows/models.json`. Before default-mode startup use an
   existing native user config only as bootstrap input. Confirm the exact id and required effort
   through the selected backend's authenticated live catalog and current vendor source at every
   selected activation or dispatch. The shipped file is deliberately `bootstrap-unverified`;
   resolve only routes needed by the current dispatch, using fresh authenticated per-dispatch
   receipts, and resolve additional roles on demand. Partial coverage leaves `catalogResolution`
   `bootstrap-unverified`; `verified` requires exact coverage of all active roles/backends and never
   substitutes for launch authorization. Missing, ambiguous, unavailable, or effort-inexpressible
   routes remain unresolved and must not dispatch. Never silently substitute a model, version, provider,
   backend, or effort. Exact model ids are refreshable snapshots in the role document, never a
   second source of truth in reusable agent files.
   **A reviewer/verifier agent MUST
   select a different upstream provider family from the actual writer execution, set
   `cross-provider: true`, and
   MUST NOT include a same-provider fallback.** A role-specific workflow reviewer declares one
   exact `writer-role`. A reusable native reviewer that can cover several semantic roles from the
   same family declares one `writer-family` and has a direction-qualified name such as
   `cross-review-openai-writer`; create the opposite-family asset separately. The caller MUST pass
   the actual writer semantic role and its concrete resolved execution identity, and
   `agent_roles.resolve_reviewer_assignment` must prove it belongs to the declared family and exact
   canonical reviewer pairing. Never use an unqualified generic reviewer name or select direction
   from the top session's model. Resolve both roles from the canonical model-role document and
   fresh live catalogs, then compare the actual writer and reviewer `upstreamProviderFamily`
   values; a planned role, different CLI, or relay name alone does not prove independence.
   Platform sync omits this agent from an incompatible native
   harness; launch it through the configured provider's CLI instead. If unavailable, fail closed.
   Strong review and advice candidates are Fable 5.1 (`claude-fable-5-1`) and GPT-6 Astra
   (`gpt-6-astra`), but neither name activates a route or replaces the configured exact terminal
   pairing. The shipped `advisor` reviews OpenAI Astra writers with Anthropic Opus, and
   `codex_advisor` reviews Anthropic Opus writers with OpenAI Astra. A qualified review/advice role
   may select Fable or Astra only after explicit reviewed policy activation, fresh live-catalog
   availability, and proof that it is opposite the actual writer. Neither substitutes for the preferred executor or terminal reviewer, and
   an unavailable or unverifiable selection remains unresolved. `diversity_glm` and
   `diversity_kimi` remain Cursor catalog candidates; `diversity_grok` is active only at the highest
   effort proven for its exact Cursor model. Every adaptive Cursor advice or diversity role pins
   that effort under `effortPolicy: highest-available`; literal `max` wins when present. Humanize
   cannot substitute another backend. DeepSeek stays a future highest-available Cursor candidate,
   and Composer 2.5 is excluded until an explicit policy change. Simplify -> Verify never requires
   Grok; Grok remains optional general diversity for a named blind spot.
7. **Structured output when a flow consumes it:** add an `output:` schema — review nodes and
   statePatch contracts parse mechanically, not by mood. A portable workflow node declares
   `skills.required` or `skills.onDemand` in its neutral design and lets the selected adapter
   materialize that contract; reusable agent frontmatter does not own workflow skill loading.
   A terminal completion reviewer emits exactly `COMPLETE | HUMAN_BLOCKED | REVISE`, binds its
   verdict to current acceptance evidence with an evidence commitment, and remains read-only.
   `HUMAN_BLOCKED` must name a
   permission, decision, credential, or external action only a human can provide after safe
   agent-side alternatives are exhausted. Test failure, difficulty, disagreement, tool failure,
   elapsed time, or budget is `REVISE`, not a human block. A reviewer whose sole assignment is this
   terminal verdict must not recursively invoke another terminal reviewer.
8. **Body** — `# Purpose` → `## Instructions` (numbered "When invoked: 1…") → `## Best
   Practices` → `## Report` (the exact structured output). **For an implementing worker, END
   with the grind persistence block** (`.claude/skills/grind/SKILL.md` § "The dispatch block"),
   including its fresh-evidence and terminal-review requirement.
   Any non-review worker that returns a terminal assignment result must likewise route its current
   artifact and focused evidence to a separate reviewer whose upstream provider is opposite the
   actual writer before yielding.
   A pure reviewer returns its own verdict directly and is the sole recursion exemption.
9. **Choose the destination deliberately.** A project specialist goes to `.omp/agents/<name>.md`.
   A reusable default shipped by this bundle goes to `assets/project/.omp/agents/<name>.md` and is
   materialized by `default-mode`. Then run
   `python3 .claude/skills/update-platform-config/scripts/config-sync/sync_all.py` to render
   compatible native platform formats.
10. *(optional)* Probe triggering with a few should/should-not prompts; tighten the description.

## File shape
```markdown
---
name: <kebab-name>
description: "<third-person specialty>. Use proactively when <trigger>."
tools: read, grep, glob, bash        # least-privilege portable names
spawns: false
# cross-provider: true               # required for reviewer/verifier agents only
# writer-role: task                  # direction guard; actual resolved writer evidence also required
# writer-family: openai              # family guard; verify against the actual writer execution
model-role: task                     # semantic role selected under the global reasoning posture
thinking-level: medium               # optional
---
# Purpose
<one line>
## Instructions
When invoked:
1. …
## Best Practices
- …
## Report
<the exact structured output returned to the caller / yielded to the flow>
```

Used by **`build-workflow`** to author reusable specialists referenced by neutral workflow designs;
platform adapters render any harness-specific packaging.
