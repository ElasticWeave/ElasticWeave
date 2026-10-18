# Native humanize2 authoring contract

## API surface

- A flow is ordinary Python marked with `from hmz.flows import Agent, flow` and `@flow`.
- Its signature is `(agents, task)` or `(agents, task, config)`. `agents` must be a fixed-length
  `tuple[...]` or a `NamedTuple`; an open-ended tuple is rejected.
- A `NamedTuple` preserves semantic role names in the UI and trace. It does not choose models.
- The optional config type is a runtime-importable Pydantic model. `hmz exec` supplies `None`
  without `-c`, so the flow owns safe defaults.
- `agent.aturn(prompt, schema=Output)` creates a fresh session. `agent.new()` creates a persistent
  session whose later turns retain conversation history. Structured answers should use Pydantic
  models with extra fields forbidden where exactness matters.
- An async flow can await independent turns with `asyncio.gather` or `agent.abatch`. Turns in one
  persistent session are still sequential.

## Callable flows and reload boundary

`from hmz.flows import load` resolves a child by the same name accepted by `hmz exec -f`.
`load("slot")` validates that name immediately, and its returned callable rereads and validates
the child on every invocation before reusing the caller's existing agent objects. It does not
override model, effort, provider, machine, permission, or session settings.

The reload guarantee is narrow:

- invoking a previously saved callable after an atomic edit sees the new `slot.py` main-file bytes;
- evaluating a new `load("slot")` also validates the current child before returning;
- an in-flight call is unchanged;
- helpers imported normally may remain cached in `sys.modules` even on the next call.

Keep replaceable code in the child's main file or in a newly versioned helper module. Holding the
returned callable does not pin old main-file bytes, but the scheduler still resolves it inside each
dispatch so admission and execution share one explicit lifecycle.

The tracked `slot.py` is a controlled relative symlink to a digest-bound, single-link
`versions/slot-<sha256>.py` without group/world write. A collaborative Git checkout can restore a
tracked `100644` version as `0664`; only after a no-follow read proves that an effective-user-owned
target matches its filename digest does the reader clear those group/world write bits and revalidate.
The validator also records accepted bytes at `.approved/slot-<sha256>.py`, but that pathname is not
the production dispatch target. The shared launcher seals the approved default and slot bytes in
Linux memfds and passes only those descriptors to the pinned Humanize loader; the root calls the
slot descriptor named by `HUMANIZE_SLOT_FD`.
Editing the link, a version, an approved snapshot, or a parent directory cannot retarget that
already admitted dispatch. This remains a same-UID boundary,
not an OS sandbox or protection from a malicious process that can alter the credential store or
the already authenticated native executable before launch.

The bundled AST validator is fail closed for import/discovery-time statements, decorators,
annotations, defaults, class headers, and declarations. It intentionally permits trusted flow
function bodies to perform the task's declared work. It therefore does not prove that function-body
behavior is harmless or make an unreviewed flow safe to execute.

The validator has two explicit scopes. `--trusted-seed` checks `default.py` and `slot.py`, admits
only the byte-exact bundled `scheduler` tree when it is present, including every file Humanize
mounts from its flow-owned `skills/` directory, and returns other
discoverable direct Python files or directory entry points in `unvalidatedFlows`; it does not
import them. This covers Humanize's local discovery surface: direct `.py` children whose names do
not start with `_`, plus direct directories containing `__init__.py`; underscore-prefixed direct
Python files are helpers, not offered flows. An extensionless direct symlink is reported as
`<name>/__init__.py` so it cannot disappear from coverage accounting, but the validator never
follows or admits it. `--all-admitted` admits either that seed or the seed
plus the complete byte-exact bundled `deep_research.py`, `auto_research.py`, and
`_research_reference.py` set. Those files and the scheduler-mounted skill tree are compared with
controller-owned source and validator-owned approved digests without importing them. An extra,
missing, linked, or changed scheduler-tree entry fails admission.
Every other direct Python flow and every other direct underscore helper fails; underscore is not a
generic executable-helper allowance. This is coverage accounting, not a generic Python safety
proof. Other native flows require source review and may be used only under the explicitly partial
`--trusted-seed` result, not called all-admitted.

Neutral adapter generation uses `.humanize/generated-flows/`, never this native directory.
`default` and `slot` are reserved neutral IDs. Keep account/profile routing separate from model
identity: a Humanize provider account does not prove an upstream provider family.
Strong review/advice routes are Fable 5.1 (`claude-fable-5-1`) and GPT-6 Astra
(`gpt-6-astra`) after exact live-catalog resolution. Resolve the actual
writer execution before choosing a policy-qualified reviewer/adviser from the opposite upstream
provider. Every selected exact identity and effort requires fresh authenticated live-catalog
evidence. If the required direction is unavailable or unverifiable, report it unresolved and
never silently substitute a model, version, provider, backend, or effort.

## Outer scheduler protocol

The bundled `.humanize/flows/scheduler` directory, whose sole entry point is `__init__.py`, is an
explicit local TUI flow with eleven fixed
positions: native Codex scheduler followed by executor, reviewer, planner, preflight, collector,
counterevidence, reducer, knowledge, applicability, and experimenter. A Slot declares an ordered
subset of the ten child roles and must include reviewer. The outer flow maps those names to the same
Agent objects configured when the TUI run started; a Slot update never changes their model,
account, effort, permission, or session settings. Every scheduler call is fresh and must atomically
write `.humanize/scheduler/dispatch.txt` as exact `false\n`, or as
`true\n.humanize/flows/slot.py\n`. False returns. The tracked `slot.py` is a controlled relative
symlink to `versions/slot-<sha256>.py`; publish a regular candidate only through the validator's
atomic installer, which creates a digest-bound single-link version without group/world write and
retargets that symlink. Git may restore group write under a collaborative umask; after the addressed
bytes and effective ownership verify, the reader clears only those bits and rechecks the digest and
inode.
Before the first scheduler turn, the outer flow
no-follow reads and sealed-memfd loads the validator once. True is valid only when that held
callable accepts the current slot bytes and returns only allowed role names. The outer flow resolves
the matching read-only digest snapshot through a no-follow descriptor and copies the verified bytes
into a sealed memfd. Because the pinned Humanize resolver rejects a memfd pseudo-name after
`realpath()`, the outer flow stages those sealed bytes as a randomized private read-only `slot.py`,
evaluates real `load()` on that path, passes the Slot-selected Agent subset, rechecks the staged
inode and bytes after return, removes it, and starts the next Codex turn. The flow resets the file
to invalid `pending` before each attempt and after every scheduler exception, rejects every other
target or malformed state, records bounded/redacted backend diagnostics, and fails explicitly at its
bounded round cap. `Config.max_rounds` defaults to 50 (range 1-64); each scheduler turn has one
initial attempt plus four retry rounds by default, with fixed waits of 5 minutes, 10 minutes,
30 minutes, and 1 hour. Explicit recoverable native-Codex rate limits, usage-limit windows,
transport errors, timeouts, temporary service errors, and otherwise-unclassified native process
exits are retried. Each native agent CLI turn
receives a 3600-second timeout; shorter bounds are reserved for deterministic probes and cleanup.
Insufficient credits or
billing/payment authority, authentication, permission, model, sandbox, and stopped app-server
errors fail immediately. It does not use or imply Flowverse resumability.
The typed Humanize `Unrecoverable` failure also fails immediately, regardless of transient words
in its diagnostic.

Before a model boundary, the outer flow resolves exactly one iteration source. A launch carrying
`--pm-issue .pm/issues/<milestone>/<issue>.md` seals that issue's validated snapshot and raw-byte
digest; every boundary revalidates those issue bytes and derives lease aging only from the frozen
expiry/fallback. It never substitutes a later `.pm/config.json`. An older issue with no snapshot
uses a marked conservative autonomous default, while partial or contradictory snapshot metadata
fails closed. A launch without `--pm-issue` is direct project-config mode and no-follow reads
`.pm/config.json` at each boundary. Legacy config with none of the mode fields means `autonomous`.
Persisted values are exactly `iteration_mode: autonomous | human_guided`, a human-guided-only
`human_checkin_lease_minutes` integer from 5 through 1440, a UTC
`human_checkin_lease_expires_at` ending in `Z`, and
`human_unavailable_fallback: pause | autonomous_review`. Partial, unknown, aliased (`Fast`),
contradictory, linked, special-file, or malformed state fails closed. An expired `pause` lease
writes the observation boundary and returns without loading the validator, starting the scheduler
model, or calling a child. `autonomous_review` makes the effective mode autonomous and explicitly
restores review-heavy idea/counterevidence work; it never silently keeps the human-guided label.

Every `true` dispatch must also carry `.humanize/scheduler/.cycle-decision.tmp`, a strict
newline-terminated `humanize-iteration-decision/v3` object. In addition to
`idea_review_disposition: performed | skipped_human_guided | triggered_by_risk` and a bounded,
duplicate-free risk-trigger list, it requires a duplicate-free `reward_hacking_risks` list of at
most five bounded task-specific hypotheses for the reviewer to test, plus non-empty bounded `expected_outcome` and
`expected_measurement` strings and one explicit strategy from `exploit | verify | substitute |
pivot | explore`. The runtime rejects human-guided-only skipping under an effective autonomous
mode. After a normal child return, one fresh scheduler session has evidence-only authority: it may
inspect, but not implement, repair, dispatch, or plan. It returns a strict
`humanize-child-assessment/v1` value with the observed outcome, separate `met | missed |
inconclusive` statuses for the expected outcome and expected measurement, a deviation, signals, and
one through six non-scheduler `workflow-output/humanize/...#sha256:<digest>` references. The runtime no-follow
reads each single-link regular file and verifies its digest. It then appends a bounded
`humanize-cycle-ledger/v1` history record containing the assessed outcome, a typed deviation
(`none | relevance | authority | accessibility | unexpected | contradiction | repeated_failure |
reward_hacking`), expectation statuses, the next strategy, digest-bound evidence references, and a
structural-review flag. `none` requires both statuses to be `met`; explicit contradiction and
reward-hacking markers in the referenced bytes override a false `none`. Only consecutive deviations
under the same expected outcome and measurement count toward repeated failure. Repeated deviations
and reward-hacking/jailbreak signals require `pivot`/`explore` plus a changed expected outcome or
measurement, or a clean stop. Strategy text alone is insufficient. These checks are guardrails, not
proof of semantic correctness, model tool confinement, or child authorship. It binds the resolved policy
and cycle ledger into both the fresh scheduler prompt and the child task. Safety/authority, code and
alignment review, experiment/proof validity, evidence integrity, deterministic reduction, and
terminal review remain mandatory in both modes.
Human-guided may omit only idea-level logic review after the minimum validity gate; contradiction,
ambiguity, plateau/repeated failure, irreversible/high-cost or safety-sensitive work, weak proxies,
and human/lease absence restore targeted review or the declared fallback.

After a human-guided child completes and its evidence assessment validates, the outer flow atomically writes the structured
`workflow-output/humanize/scheduler/observation-checkpoint.json`, sets dispatch to exact `false`, and returns
without another planning scheduler or child turn. The evidence-only assessment uses the same
one-plus-four recoverable native-Codex retry schedule and never reruns the child; missing/malformed
assessment, missing evidence, digest mismatch, or hard failure stops without a generic success
record. The checkpoint fields include hypothesis, actual operation, raw
observation, interpretation, alternate interpretation/anomaly, candidate next tasks, and an empty
human-feedback position plus a canonical checkpoint ID, task digest, and exact policy/issue
binding. The top/UI owner starts the next invocation through the launcher with the same PM issue
when applicable and `--human-feedback`. The launcher verifies the exact checkpoint bytes and
seals a self-digested feedback receipt; the flow refuses another human-guided planning turn until
that receipt matches the pending checkpoint. Its first accepted use atomically publishes
`workflow-output/humanize/scheduler/feedback-consumed/<checkpoint-sha256>.json`; later invocations
cannot consume the same checkpoint, while retries in the same invocation revalidate the exact
checkpoint and consumption bytes. This is an
external observation boundary, not an in-graph human node, completion proof, `HUMAN_BLOCKED`,
durable Humanize checkpoint, or automatic resume authority.

The executable project asset `.humanize/run_scheduler.py` is the supported versioned entry point.
It changes to the project root and executes the native `hmz` TUI with the scheduler flow plus one
ordered `-a` argument for every fixed role. `--check` prints the exact audited CLI, flow, command,
and eleven defaults without opening the TUI or making a model call. The short specs intentionally
omit provider and permission, so the
installed backends use their ambient account and pinned Humanize resolves permission to `bypass`.
The pinned `/Flow` picker and launcher both name the project-owned directory flow
`local/scheduler`, which resolves to `.humanize/flows/scheduler/__init__.py`.
For PM-managed work, `--pm-issue` is required and creates the sealed invocation context. For a
human-guided continuation, `--human-feedback` is accepted only when the current v2 observation
checkpoint is awaiting feedback; it never becomes an unbound prompt string. Direct `hmz -f
local/scheduler` cannot manufacture that receipt and therefore cannot bypass a pending checkpoint.
Each launcher invocation reapplies the repository defaults without a shell-startup or global
Humanize configuration. It sets `HUMANIZE_HOME` to the owner-only project path
`.humanize/scheduler/runtime`, so user-global settings and fallback/retry entries cannot alter this
run while native CLI credential homes remain unchanged. Every place declares
`AgentDefaults(goals=False)`; `/agents` remains available for a deliberate override. Direct `hmz -f
local/scheduler` bypasses these defaults and the project-state isolation. The launcher rejects
re-entry through that entry when a sealed scheduler context is inherited, and it holds one
nonblocking project-local owner lock across the `exec` so a second launcher cannot own the project.
Direct `hmz` bypasses these launcher guards as well as the defaults; the Scheduler prompt forbids it,
but Humanize does not tool-confine the Scheduler. This coordinates ordinary processes but does not
confine a malicious same-UID process. It also
sets `HUMANIZE_SENTRY=off` for this process, disabling telemetry and its first-run question without
editing saved settings, and disables Python bytecode writes so the audited editable checkout stays
clean across launches. Parsing a model
identifier is not live catalog validation; these user-selected TUI defaults do not claim the
audited semantic-role or fresh-receipt guarantees of `config/workflows/models.json`. Availability
is checked by the backend when used, but that check does not authorize a selected reviewer or
adviser. Resolve that identity from a fresh authenticated live catalog against the actual writer
before its turn, or leave it unresolved. The launcher accepts only the audited source-local pin selected
by `HUMANIZE2_HOME` or the standard data path. Before `--check` or launch, it invokes the shared
`humanize-workflow` validator to bind Git, wrapper, interpreter, editable-package metadata, imported
source, and version. An arbitrary executable at that path and every unrelated `hmz` on `PATH` fail
closed; install or repair the pinned source with `ensure_humanize.py`.

The outer scheduler itself only inspects state and evidence, designs or tunes the next Slot, and
decides whether to dispatch it. It is a team-leader/coordinator boundary: it must delegate every
implementation, research, task-level planning, experiment, reduction, and review turn to the
validated Slot/bounded subagent. It must not perform task work, invoke a configured child role,
schedule individual workflow nodes, or launch native Codex subagents. Every workflow execution turn
belongs to the validated Slot. Humanize's live `Turns` panel aggregates outer and called-flow turns
into one transition graph rather than an ordered or nested transcript, so a transition back to
`scheduler` can record child return rather than a scheduler role in the child; the child-role
validator rejects `scheduler`. The persisted cycle directory records the outer flow in
`cycle.jsonl` and each call in a separate `cycle.<flow>_<id>.jsonl`, with `called`/`returned` links.

## Execution and safety boundary

Humanize finds and describes flows with `runpy.run_path`; listing or reading a flow executes its
top level. Only inspect unknown flows as source bytes/AST. Flowverse is a pattern reference, not a
trusted execution source.

Humanize's cycle records calls and sessions but are not a replay checkpoint, state-bound proof, or
independent completion review. The root and child must emit or write their own terminal artifact
when the surrounding workflow requires one.

The upstream CLI profiles at the audited revision are Claude Code (`claude`), Antigravity CLI
(`agy`), Codex (`codex`), Cursor Agent (`cursor`), DeepSeek Harness (`dsh`), Grok Build (`grok`),
Kimi Code CLI (`kimi`), Pi (`pi`), Qwen Code (`qwen`), OpenCode (`opencode`), Mimo Code (`mimo`),
and ZCode (`zcode`). Cursor and ZCode are not admitted routes in the bundled default/research or
neutral-adapter launch contexts. Strong direction-qualified review/advice routes include Fable
5.1 (`claude-fable-5-1`) and GPT-6 Astra (`gpt-6-astra`). The launcher uses the
configured exact role pairing. Select the
reviewer/adviser opposite the actual writer execution and resolve its exact identity and effort from
a fresh live catalog. An unavailable or unverifiable route remains unresolved; a default-flow launch
must not relabel, substitute, or silently downgrade the activation. Cursor-specific models and
required Cursor diversity must use the built-in subagent scheduler or fail closed as unsupported.
A Slot reviewer may make one optional read-only `cursor-agent` CLI second-opinion attempt and
continue if unavailable; that advisory subprocess is not a configured Humanize role, a backend
substitution, or completion-review authority. Its optional status does not permit a selected
required review/advice identity to be skipped. Simplify -> Verify never requires Grok; Grok remains
optional general diversity for a named blind spot.

The default launcher derives its reasoning posture solely from
`config/workflows/models.json`'s canonical `reasoningPolicy.defaultPosture`; a flow spec, CLI
argument, or environment variable cannot override it. For an activation set that Humanize can
execute, it must validate the fresh resolver receipt's self-digest, exact request, current policy
digests, actual resolved writer identity, opposite-provider reviewer identity, and concrete
fresh-catalog coverage before sealing one per-activation receipt. Missing or unverifiable coverage
is unresolved and cannot trigger substitution. Unit tests may supply catalog bytes only through private in-process
callables; production launch inputs must not expose a catalog/backend/model injection seam.

The command `hmz` without arguments opens the interface and does not automatically execute a
project root. `hmz exec` requires `-f`; “default” is this bundle's explicit launcher convention.

## Pinned provenance

- Repository: `https://github.com/humanfia/humanize2.git`
- Audited revision: `8fbb2b718d74b7ef1d3d0650a9768415ec489f26`
- Distribution/CLI: `hmz==0.1.0`

The authoring contract depends on the revision above, especially `@flow`, `hmz.flows.load`, and
the Codex reconnect completion fix.
That revision exposes the flow-facing `Agent` protocol and `Unrecoverable` through `hmz.flows`, so
the bundled Scheduler does not depend on Humanize's internal agent module.
Re-audit before changing the pin.
