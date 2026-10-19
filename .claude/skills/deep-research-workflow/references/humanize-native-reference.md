# Humanize2 native research references

The skill seeds two trusted Python flows under
`assets/project/.humanize/flows/`: `deep_research.py` and `auto_research.py`. They are
bounded Humanize2-native examples, not Flowverse copies, generated adapter mappings, or the
canonical research specification. `SKILL.md`, `reference-design.md`, `falsification-gate.md`, and
`experimental-validation.md` continue to own platform-neutral intent.

Both references accept the canonical `autonomous | human_guided` iteration policy and bind it in
the strict config, launch receipt, and terminal artifact. Humanize has no durable in-graph human
resume checkpoint. Therefore a human-guided native run executes one finite research/experiment
cycle, reduces it, writes `human_observation_checkpoint` with `next_planning_allowed: false`, and
returns to the outer session/TUI. The next launch must carry renewed human feedback or an explicit
autonomous-review fallback. `Fast` is not a valid config value. Lease expiry never silently
continues human-guided behavior.

For PM-managed work, pass `--pm-issue .pm/issues/<milestone>/<issue>.md`. The launcher derives the
four iteration fields only from that issue's digest-bound snapshot and seals the issue path,
raw-byte digest, snapshot digest, and legacy-default marker into
`humanize-research-iteration-context/v1`. Later project config cannot change the run, and explicit
iteration fields in `--config` are rejected. An older issue with no snapshot defaults
conservatively to autonomous. Omitting `--pm-issue` is an explicit standalone launch, not PM
provenance.

An initial human-guided config leaves `human_feedback`,
`human_feedback_checkpoint_artifact`, and `human_feedback_checkpoint_sha256` null. A resumed run
must supply all three together. The artifact is the prior reviewed
`research-terminal/v1` under `.humanize/artifacts/`; its raw-byte SHA-256 and embedded checkpoint
commitment must verify, its recorded `taskSha256` and `iterationContext` must exactly match the new
task and PM-bound or standalone provenance, and the new run must use a different
`terminal_artifact`. The launcher revalidates those source bytes immediately before and after the
Humanize subprocess. The sealed launch receipt then binds the exact checkpoint, feedback, and next run through a self-digested
`humanize-human-feedback-receipt/v1`. A bare feedback string is rejected.

The bundle intentionally does not infer a neutral executable from these Python files. Use
`$build-workflow` when a project needs a canonical `workflow-design/v1` artifact and
`$run-workflow` when an admitted artifact must run through an eligible adapter.

## Supported launch path

Run these multi-role flows only through the dedicated fail-closed launcher:

```bash
python3 .claude/skills/deep-research-workflow/scripts/run_humanize_research.py \
  --root "$PWD" \
  --hmz /absolute/path/to/pinned-humanize2/.venv/bin/hmz \
  --flow deep-research \
  --pm-issue .pm/issues/<milestone>/<issue>.md \
  "the research task"
```

Use `--flow auto-research` for Auto Research. Omit `--pm-issue` only for a deliberate standalone
launch. An optional `--config` names a strict JSON object
with flow fields only. Model, effort, provider-account, permission, and role overrides are not
config fields. The launcher has no `--roles` option: it always consumes
`<root>/config/workflows/models.json`, so a caller cannot redirect one run to an alternate policy.

The generic two-role Humanize default launcher does not support these four- and five-role flows.
Direct `hmz exec`, TUI launch, and an ordinary `hmz.flows.load()` invocation lack the bound
launch receipt and therefore write `UNRESOLVED_INFRASTRUCTURE` before any model turn. A future
parent flow may call these references only after it implements the same multi-role receipt and
identity contract; next-call reload by itself is not authorization.

The launcher requires:

- a complete materialized copy of `deep_research.py`, `auto_research.py`, and
  `_research_reference.py` under the target project's `.humanize/flows/`, byte-exact against the
  bundle-owned approved source and launcher-owned SHA-256 values. This source admission happens
  before any live catalog command, Humanize invocation, or terminal-artifact write;
- a project-local `config/workflows/models.json` using `workflow-model-roles/v3` with a
  structurally valid catalog-resolution record, catalog evidence receipt, and audited model
  identity map; and
- `config/workflows/humanize-research-accounts.json`, described below.

It verifies the pinned `hmz` executable provenance, resolves the fixed semantic roles, and passes
every role to `hmz exec` with an explicit backend, model, effort, provider account, and permission.
The shared canonical model-role validator must accept the complete policy first. Each activation is
then resolved through the shared `workflow-native-launch-receipt/v1` launch-spec implementation
using the policy's declared default posture, the role's fixed task class, and exact-opposite review
for the terminal reviewer. A launch receipt binds its request, exact current policy bytes, fresh
catalog evidence, concrete catalog model ID, and self-digest. The persisted catalog-resolution
record is audit-only; it cannot authorize a route without the resolver's fresh evidence. The
research receipt retains the exact canonical policy-byte digest; the per-activation launch specs
are resolution inputs rather than a caller-provided policy lane.
It also passes the normalized flow config through `hmz exec -c`; the same config is committed in
an immutable, sealed, process-bound launch receipt. The receipt records the approved source
binding as evidence, but does not authorize source: the launcher independently validates all three
files and executes the approved bundle source rather than the mutable project copy. Replaced or
partial reference files fail before `hmz`. Custom reviewed native flows remain in the separate
`write-humanize-flow` authoring lane and are not accepted by this launcher.

The named-provider launcher uses the shared execution context. It ignores caller `HOME`,
`HUMANIZE_HOME`, `PATH`, endpoints, proxies, TLS settings, and configuration selectors; derives
the effective passwd home and canonical `<passwd-home>/.humanize`; then starts `hmz` in a scrubbed
environment with receipt-authenticated native executables staged under bare backend names. It
opens each provider record through no-follow descriptors, probes the exact `models.ask(backend,
provider)` catalog with the pinned Humanize provider redirect before `hmz`, and seals the flow,
helper, config, and outer receipt as memfds. Provider-record, policy/account, sealed receipt,
native executable, and pinned Humanize closure drift fail closed before or after dispatch.

The flow rechecks that receipt against the exact task, config, project root, live agent objects,
current model policy bytes, current account-binding bytes, and current Humanize provider records.
Each sealed role must carry its complete `workflow-native-launch-receipt/v1`. The flow validates
that nested receipt without importing or executing catalog tools: exact request and role, current
policy-byte digest, self-digest, `liveCatalogAuthenticatedByResolver`, fresh coverage of the
concrete catalog ID, writer/reviewer linkage, and equality with the sealed outer role. Persisted
`catalogResolution` `status`, `source`, `verifiedRoles`, or evidence cannot authorize. Changing an
agent's model, effort, provider account, backend, permission, or concrete catalog ID after launch
fails before a turn. The reviewer must be Max and must satisfy the canonical exact writer/reviewer
family, model-family, and effort pairing.

## Credential-account bindings

Humanize's `provider` setting is the name of one credential account. It is not an upstream model
family. Two different account aliases for the same upstream family do not constitute independent
cross-provider review.

Create `config/workflows/humanize-research-accounts.json` after configuring the accounts with the
pinned Humanize installation. Its complete schema is:

```json
{
  "schema": "humanize-research-account-bindings/v1",
  "flows": {
    "deep-research": {
      "preflight": {"providerAccount": "codex-main", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "openai"},
      "collector": {"providerAccount": "codex-main", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "openai"},
      "counterevidence": {"providerAccount": "codex-main", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "openai"},
      "reducer": {"providerAccount": "codex-main", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "openai"},
      "reviewer": {"providerAccount": "claude-review", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "anthropic"}
    },
    "auto-research": {
      "knowledge": {"providerAccount": "codex-main", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "openai"},
      "applicability": {"providerAccount": "codex-main", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "openai"},
      "experimenter": {"providerAccount": "codex-main", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "openai"},
      "reviewer": {"providerAccount": "claude-review", "providerRecordSha256": "sha256:...", "upstreamProviderFamily": "anthropic"}
    }
  }
}
```

Replace the examples with the accounts actually configured under the effective passwd account's
`<passwd-home>/.humanize/providers/<backend>/<account>/provider.json`. The launcher ignores a
caller-provided `HUMANIZE_HOME`. Compute
`providerRecordSha256` from the exact raw bytes of that file and retain only the digest in the
project binding. The launcher checks that the record is a regular non-symlink file and that its
`cli`, `name`, and digest still match. Never copy provider credentials into the project file or a
test log.

Each flow section must exactly cover its roles; unknown or missing roles fail closed. The declared
upstream family must equal the family of the verified semantic model role. Review independence is
then evaluated from that canonical family, never from account-name inequality.

## Fixed role and permission map

| Flow | Flow role | Semantic role | Permission |
| --- | --- | --- | --- |
| Deep | preflight | `plan` | `read-only` |
| Deep | collector | `research_collect` | `read-only` |
| Deep | counterevidence | `research_collect` | `read-only` |
| Deep | reducer | `frontier_execute` | `read-only` |
| Deep | reviewer | `advisor` | `read-only` |
| Auto | knowledge | `frontier_execute` | `read-only` |
| Auto | applicability | `plan` | `read-only` |
| Auto | experimenter | `task` | `workspace-write` |
| Auto | reviewer | `advisor` | `read-only` |

The table fixes semantic roles and permissions, not stale model IDs. The launcher resolves the
exact fresh catalog model ID and effort from the current v3 policy through the canonical resolver.
Because these fixed flows use OpenAI-family writers, their shipped `advisor` resolves through
native Claude Code to the opposite-provider Opus model at Max effort. Fable 5.1
(`claude-fable-5-1`) remains scoped advice and GPT-6 Astra (`gpt-6-astra`) is the active
OpenAI review/advice route for Anthropic-family writers after fresh live-catalog admission; neither silently replaces the fixed role. The launcher binds the actual writer
identity and requires exact fresh live-catalog coverage for the selected reviewer. An unavailable
or unverifiable identity produces `UNRESOLVED_INFRASTRUCTURE`; it never falls back to a
same-provider reviewer or another silent substitute. Cursor-only diversity remains unsupported on
this Humanize mapping; a required diversity run emits
`UNSUPPORTED_HUMANIZE_CAPABILITY` with `route_to: subagent-scheduler` and never substitutes or
relabels a backend.

Humanize permission levels are backend translations, not portable operating-system sandboxes.
Treat the flows as trusted project source, inspect them before execution, and retain external
infrastructure isolation where required. Flow discovery executes Python.

## Deep Research behavior

`deep_research.py` uses one static bounded path:

1. Preflight locks the core problem and returns a ranked kill set with falsifiers and minimum
   valid checks.
2. A validated plan declares one to four non-overlapping branches.
3. Fresh branch sessions run concurrently. The first branch uses the counterevidence role, and
   the controller reserves the cumulative call budget before dispatch.
4. Controller code sorts evidence, rejects duplicate evidence IDs, and builds the claim ledger
   mechanically. The reducer must reproduce it exactly.
5. A fresh different-upstream reviewer judges the current complete artifact before one atomic
   terminal write.

A non-`CONTINUE` preflight does not fan out. There is no refill loop or runtime role escalation in
this reference.

## Auto Research behavior

`auto_research.py` is the bounded native experimental-validation branch:

1. A read-only role produces the knowledge synthesis before applicability reasoning.
2. A separate read-only role classifies every claim exactly once.
3. Controller code computes `READY | NOT_READY | INVALID` only from the explicit Pydantic resource
   declaration; ambient hardware and credentials never count.
4. `NOT_READY` makes no experimenter turn and records
   `NOT_EXECUTED_NOT_EXPERIMENTALLY_VALIDATED` with a complete validation plan.
5. Only `READY` may run the bounded trial contract. `INVALID` or `STOP_BUDGET` stops the sequence.
6. A fresh different-upstream reviewer checks the current complete artifact before the atomic
   terminal write.

The experimenter may mutate only the project-local workspace needed for an authorized trial.
External writes, resource acquisition, credential use, publication, and deployment remain out of
scope. Add deterministic hooks or infrastructure isolation for trial-specific path and command
restrictions.

## Terminal and recovery contract

Every controlled launcher or flow failure after trusted source admission writes an atomic
`research-terminal/v1` artifact with:

- `terminal` and `domain_terminal` set to `UNRESOLVED_INFRASTRUCTURE`;
- an explicit `failure_category` and bounded reason;
- `human_dependency: null`; and
- `terminal_review: null` when no valid review completed.

This includes launch validation, Humanize process failure, success without the required artifact,
budget exhaustion, malformed structured answers, and flow infrastructure errors. A source-admission
failure is earlier: it writes no terminal artifact, so a tampered project copy cannot cause even an
output write before its hash is rejected. These failures are not `HUMAN_BLOCKED`. If the project
root or artifact directory itself is unavailable, the launcher reports the write failure on stderr
because no local atomic destination exists.

The terminal path is lexical and project-relative. The launcher and reference controller open each
parent with Linux no-follow directory descriptors, reject linked or non-regular leaves, revalidate
the held parent and leaf around an atomic rename, and never accept the project root as an artifact.
A linked or concurrently replaced destination is an infrastructure failure; it is never followed
to overwrite another in-project file.

Humanize traces sessions and calls but are not durable workflow checkpoints. Resume or replay must
bind the task, normalized config, role receipt, provider-account records, and terminal artifact at
the project workflow layer.

## Zero-paid-call verification

The default repository test entry is deterministic under plain system Python:

```bash
python3 -m unittest discover \
  -s .claude/skills/deep-research-workflow/tests -p 'test_*.py'
```

This runs launcher and structural/security contracts under plain system Python. It skips the two
Humanize-dependent runtime test classes when `hmz` is unavailable; path, source-binding, budget,
validator, and no-follow tests still execute. The native file has the same behavior when run
directly: structural/security tests run and only the runtime-dependent classes skip.

```bash
python3 .claude/skills/deep-research-workflow/tests/test_humanize_native_references.py
```

Run the native integration with the pinned checkout's environment:

```bash
/absolute/path/to/pinned-humanize2/.venv/bin/python \
  .claude/skills/deep-research-workflow/tests/test_humanize_native_references.py
```

The pinned suite copies and loads the real flows through Humanize's loader, then uses scripted fake
agents. It makes no real model calls. Tests cover bounded calls, deterministic reduction, resource
gating, role/effort/account binding, direct-invocation refusal, diversity routing, malformed
structured answers, budget failure, explicit terminal artifacts, and no-turn launch failures.
