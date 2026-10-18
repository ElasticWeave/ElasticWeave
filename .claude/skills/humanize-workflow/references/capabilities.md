# humanize2 adapter capability matrix

Audited against `humanfia/humanize2` revision
`8fbb2b718d74b7ef1d3d0650a9768415ec489f26` (`hmz==0.1.0`).

| Neutral capability | v1 status | Evidence / boundary |
|---|---|---|
| agent invocation | native | `hmz exec` constructs the named CLI agents |
| fresh sessions | native + generated mapping | every model node receives a distinct agent and calls `aturn` once |
| conditional routing | adapter | exact, finite decision-verdict routes generated from the neutral DAG |
| structured output | native + adapter | humanize passes Pydantic schemas; generated mapping rejects extra fields |
| typed state | adapter | input and node writes use generated Pydantic models and declared fields |
| observability | native + adapter | one cycle directory with outer and per-called-flow JSONL records, plus standardized adapter result JSON |
| backend permission rung | native | `read-only`/`workspace-write`; not an OS sandbox supplied by humanize |
| persistent sessions | unsupported | v1 allocates a distinct agent per node and requires `session: fresh` |
| interrupted session resume | unsupported | v1 does not opt into upstream resumable state; cycle records alone are not controller checkpoints |
| neutral node skill loading | unsupported | the `hmz exec -a` contract does not carry the neutral node skill contract; trusted directory flows may carry a separately admitted flow-owned skill tree |
| fan-out/fan-in | unsupported | upstream async flows can do it, but v1 does not generate or prove it |
| bounded loops | unsupported | arbitrary Python can loop; v1 accepts only an acyclic graph |
| durable checkpoints | unsupported | v1 writes no resumable state; a closed cycle is never reopened |
| stateful continuation | unsupported | in-process state is not a replay-safe checkpoint |
| machine-gated review | unsupported | v1 admits only graph-external independent terminal review |
| state-bound proof | unsupported | cycle/session records do not bind final workspace bytes and reviewer evidence |

Adapter-generated source lives only under `.humanize/generated-flows/`; trusted native Python
lives under `.humanize/flows/`. `default` and `slot` are reserved neutral design IDs so a mapping
cannot overwrite the native root or child.

The neutral adapter admits only one direct model predecessor per decision. At launch it binds each
role to a verified v3 catalog identity. The Humanize backend/account is an execution route, not an
upstream model-family claim: cross-provider review uses `upstreamProviderFamily` plus
`modelFamily`. Exact Sol/Opus pairings remain exact. Other writer families require the configured
provider reviewer default, a role in the `verification` intelligence class, and Max effort; absent
or ambiguous policy fails closed.

The pinned upstream also supports native `@flow` functions and `hmz.flows.load`. Those are not
capabilities of this adapter: v1 still generates only the finite single-successor shape above.
Use `write-humanize-flow` for trusted native composition. `load("child")` validates the name when
created, and the returned callable rereads the child's main file on every invocation. An in-flight
function remains bound to the bytes it started from; normally imported helpers may remain cached.

The audited Humanize revision was tested with its async, recursive-flow, reloading, and resume
suites. Native flows can therefore implement concurrent `load()` calls, durable
`@flow(resumable=True)` state, and per-session `Session.loads([...])` skill selection. Those
capabilities require a separately admitted native flow and do not change the neutral v1 matrix.

Before execution, the adapter verifies the canonical console wrapper, clean pinned checkout,
non-user-writable base interpreter, editable-install direct URL, imported `hmz`/`hmz.cli` source,
entry point, and version. It then uses the resolved non-user-writable interpreter and a fixed
`python -I -S -B -c` launcher with explicit pinned source and dependency paths; it validates but
does not execute the console wrapper.

The audited upstream profiles are Claude Code (`claude`), Antigravity CLI (`agy`), Codex (`codex`),
Cursor Agent (`cursor`), DeepSeek Harness (`dsh`), Grok Build (`grok`), Kimi Code CLI (`kimi`),
Pi (`pi`), Qwen Code (`qwen`), OpenCode (`opencode`), Mimo Code (`mimo`), and ZCode (`zcode`).
This neutral adapter still admits only its audited Codex and Claude execution routes. Cursor-only
Kimi/GLM/Grok diversity roles are unsupported here and must fail closed or run through the built-in
subagent scheduler. No backend or model may be silently substituted.

The pin also exposes flow callbacks as model tools, a human board, native-agent fleet events,
whole-run containers, and flow-owned skills mounted for the lifetime of an agent session. The
execution features are opt-in: this adapter and the bundled scheduler call no `offers()`, declare no
`Person`, select no Cursor role, and pass no `--container`. The bundled scheduler deliberately
carries one `plateau-diagnosis` skill. Its complete mounted tree is byte-compared with the canonical
asset by `validate_project_flows.py`; extra, missing, linked, or modified content fails admission.
Neutral mapped nodes still cannot declare skills. The scheduler's eleven places default `goals` off,
so native Codex/Claude continuation and subagent tools are disabled unless a user deliberately
changes that setting in `/agents`.

Upstream per-place retries and fallback routes are persisted in `$HUMANIZE_HOME/fallbacks.json`.
An unconfigured place has zero extra tries and no fallback. The repository scheduler launcher
overrides `HUMANIZE_HOME` with `.humanize/scheduler/runtime`, so unrelated user-global retry,
fallback, model, and UI settings do not alter a project run. Native CLI credential homes remain
unchanged. The launcher also disables Python bytecode writes so a normal TUI run cannot dirty the
audited editable checkout and invalidate the next launch. Direct `hmz` bypasses this isolation.

## Security findings

Humanize flows are ordinary Python. The local offer list can enumerate filenames without loading
them, but `hmz.flows.wanted()`/`loaded()` resolve and execute the selected entry through
`runpy.run_path`. Selection and validation therefore operate only on JSON plus source bytes/AST and
never ask Humanize to resolve an unvalidated flow.

At the audited revision, project-owned offers use stable `local/<name>` identifiers. The resolver
accepts that exact offer name and also restores a missing `.py` suffix for an explicit single-file
path, so every listed local flow can be selected again.

Humanize disables interactive permission prompts for unattended agents. Its remote export limits
which files a request may name, not which commands it may execute, and its container mode mounts the
workspace. Use only a workspace whose mutation is authorized and rely on the selected backend's
permission rung for its documented boundary.
