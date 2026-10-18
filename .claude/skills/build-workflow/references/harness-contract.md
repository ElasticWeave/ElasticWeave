# Workflow design and harness adapter contract

## Ownership boundary

The canonical design owns:

- objective and explicit acceptance criteria, including any deterministic argv/result evidence;
- node responsibilities and write scopes;
- per-node required and on-demand skill dependencies;
- typed shared state and session-continuity requirements;
- allowed decision outcomes and every route;
- success, unresolved, and failure meanings;
- loop, call, token, time, and external-compute budgets;
- evidence, review, side-effect, replay, and recovery requirements;
- required runtime capabilities.

An active adapter owns:

- runtime/version checks and, where external, a pinned least-invasive install;
- target syntax, file layout, imports, and CLI commands;
- model-role resolution into current provider/model identifiers;
- implementation of declared routes, counters, state, resource packaging, and observability;
- preflight and prompt/session materialization of declared node skills;
- harness-specific lint, smoke tests, and run monitoring;
- a mapping report that discloses adaptations and gaps.

An adapter must not change acceptance, delete an outcome, convert unresolved to success, weaken
write isolation, or replace a required persistent session with summary state without recording and
testing the semantic change.

Acceptance is owned by the issue/design. It is not a bundle-wide Bash gate created by default-mode. An
adapter may implement deterministic criteria with an argv command handler, but it must preserve the
declared argv, result, output digests, and candidate identity as reviewer evidence.

## Node skill contract

A model node may declare `skills.required` and `skills.onDemand`. The canonical names resolve to
`.claude/skills/<name>/SKILL.md`; they are not harness agent metadata. Before spending a model call,
an adapter must validate names, reject duplicates/overlap, prove that every declared file exists,
and reject a symlink that resolves outside the canonical skill root.

- `required`: load the complete skill body into the first prompt of that node session. Fresh sessions
  receive it on every activation. A proven persistent session may use a short reminder on later turns.
- `onDemand`: provide the exact skill path and the instruction to read it when its trigger applies;
  do not inline its body speculatively.

The portable prompt marker is a mapping detail, not canonical design syntax:

```markdown
<!-- workflow-skills/v1 {"required":["grind"],"onDemand":["systematic-debugging"]} -->
```

The built-in subagent controller replaces this marker before invoking a model. Missing or malformed
contracts route to failure/unresolved rather than silently running an under-instructed node.

## Capability negotiation

The active vocabulary is the closed `CANONICAL_CAPABILITIES` set exported by
`scripts/validate_design.py`. The same module derives graph-visible requirements, and
`run-workflow/scripts/select_harness.py` imports that contract rather than maintaining a second
list. A registry uses schema `workflow-harness-registry/v1`; every enabled or disabled adapter
declares a review-policy allowlist and one status for every active capability:

- `native[-detail]`: the harness enforces it directly;
- `adapter-<tested-mechanism>`: focused adapter code enforces it;
- `not-supported[-detail]`: the adapter is ineligible when required.

No other status string is affirmative. Unknown capabilities, missing statuses, missing review
allowlists, unsafe artifact paths, and malformed command entries invalidate the registry. A
project override takes precedence only by presence: if present but invalid, fail closed instead of
falling through to the bundled registry.

Classify every requirement as:

- `native`: enforced by the harness;
- `implemented-by-mapping`: supplied by adapter code and covered by focused tests;
- `unsupported`: the harness is ineligible;
- `adapted`: semantics differ, the difference is explicit, and the user/project acceptance allows it.

Select an enabled adapter only when every required capability is `native` or safely
`implemented-by-mapping`, and its explicit review-policy allowlist includes the design policy.
Disabled legacy adapters are not fallbacks, even if installed or mapped. Arbitrary Python flow
flexibility is not a capability claim: the humanize adapter may select only the finite subset its
generated source, AST validator, and tests enforce.

Before ranking adapters, compare declared design requirements with deterministic structure:

- agent/decision nodes derive invocation and structured-output support;
- `fresh` sessions derive fresh-session support;
- `state-mediated` sessions derive stateful continuation;
- decisions or conditional edges derive conditional routing;
- reducers, any nonempty `waitFor`, and non-decision fan-out derive fan-out/fan-in;
- node skill declarations derive node-skill loading;
- a transition cycle derives bounded-loop support;
- governance derives machine-gated review and state-bound proof.

Missing or contradictory structure-bound declarations are design errors, not mapper freedom.

## Branch contract

For each decision node, the canonical design declares:

- exact outcome enum;
- evidence used to decide;
- one outgoing route for every outcome;
- missing/malformed behavior;
- any loop counter and cap exit.

The built-in controller requires one exact edge per declared outcome, rejects a success fallback,
and fails closed on malformed verdict output. Machine-gated designs also bind fresh cross-provider
review receipts and final evidence into controller-finalized state-bound proof.

For `reviewPolicy: independent`, controller `success` means candidate-ready. The top session must
dispatch a fresh external terminal reviewer before stopping. For `reviewPolicy: machine-gated`, a
passing final in-graph review can supply `TERMINAL: COMPLETE`; a non-reviewed or technically
unresolved stop still requires terminal review. The terminal artifact always carries an
`EVIDENCE-COMMITMENT`. `HUMAN_BLOCKED` is valid only with a structured human-exclusive request;
otherwise the terminal is `REVISE`.

## Mapping report

Store `.workflow/mappings/<design>.<adapter-suffix>.md` with:

The canonical adapter suffixes are:

| Adapter | Mapping report suffix |
|---|---|
| `subagent-scheduler` | `subagent` |
| `humanize` | `humanize` |

The built-in controller's report is therefore `.workflow/mappings/<design>.subagent.md`,
not a filename containing the full `subagent-scheduler` adapter id. Humanize's machine-readable
manifest remains `.humanize/mappings/<design>.json`; its workflow report uses the `humanize` suffix.

Each report contains:

1. canonical design path and digest;
2. target built-in artifact and controller/native-core digests;
3. capability classification table;
4. node and route mapping;
5. deliberate adaptations and unresolved gaps;
6. validate, test, tier-1 start, and state-bound finalize-only commands;
7. output, evidence, checkpoint, and recovery locations.

The selector reports `mappingExists=true` only after static evidence validation. For the built-in
adapter, require a `subagent-workflow/v1` artifact whose `designPath` and `designSha256` match the
canonical file plus a report with parseable design-path, design-digest, mapping-path, and
mapping-digest fields.
For Humanize, require a `humanize-workflow-mapping/v1` manifest whose design, spec, and generated
artifact paths remain project-contained and non-symlinked, whose artifact stays in the isolated
generated-flow namespace, and whose recorded digests and execution metadata match current bytes.
Do not import generated Python during this check. Missing or stale evidence yields
`mappingVerification.status=missing|invalid` and therefore `mappingRequired=true`; it is not an
adapter-selection failure, but it is a hard start gate.
