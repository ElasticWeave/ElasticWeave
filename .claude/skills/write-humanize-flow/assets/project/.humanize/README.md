# Project Humanize flows

The local Humanize2 TUI discovers reviewed file and directory flows in `flows/`. Before opening the
scheduler, validate the installed native set without importing it:

```bash
python3 .claude/skills/write-humanize-flow/scripts/validate_project_flows.py \
  --root . --all-admitted
```

The scheduler directory carries one flow-owned `plateau-diagnosis` skill. Humanize mounts it into
the scheduler's agent sessions for the run. The validator covers the complete scheduler tree, so an
extra, missing, linked, or modified mounted file fails admission rather than silently changing the
prompt.

Then open the outer Codex scheduler from the project root through the supported repository
launcher. It applies all eleven model defaults automatically on every invocation:

```bash
./.humanize/run_scheduler.py
```

Verify the exact pinned CLI, flow, and ordered defaults without opening the TUI or making a model
call:

```bash
./.humanize/run_scheduler.py --check
```

For a PM issue, bind its immutable iteration snapshot instead of reading later project config:

```bash
./.humanize/run_scheduler.py \
  --pm-issue .pm/issues/<milestone>/<issue>.md
```

After a human-guided cycle returns an observation checkpoint, start the next invocation with the
same issue plus `--human-feedback '<observation>'`. The launcher accepts that feedback only when it
matches the exact pending checkpoint bytes. The Scheduler atomically records first consumption;
the same checkpoint cannot authorize another invocation.

The launcher executes the real `hmz` TUI with `local/scheduler` and these eleven
ordered `-a` values:

At the pinned revision, `/Flow` lists this project-owned entry as `local/scheduler`. That menu
name resolves to the reviewed `.humanize/flows/scheduler/__init__.py` directory entry.

| Position | Default agent |
|---|---|
| scheduler | `codex/gpt-6-astra:max` |
| executor | `claude/claude-sonnet-5:max` |
| reviewer | `codex/gpt-6-astra:max` |
| planner | `claude/claude-opus-5:max` |
| preflight | `claude/claude-opus-5:xhigh` |
| collector | `codex/gpt-5.6-luna:max` |
| counterevidence | `codex/gpt-6-astra:max` |
| reducer | `codex/gpt-6-astra:max` |
| knowledge | `codex/gpt-5.6-terra:max` |
| applicability | `claude/claude-opus-5:xhigh` |
| experimenter | `codex/gpt-6-astra:xhigh` |

Every launcher invocation reapplies these repository-versioned defaults, so no `.bashrc`, global
Humanize setting, or repeated manual setup is required. It does not name a
provider account or permission, so Humanize uses each backend's ambient account; in the pinned
revision, the omitted permission resolves to `bypass`. The ordinary `/agents` screen remains
available for changes during that TUI session. All eleven places default native goals off, which
disables Codex/Claude continuation and native-subagent tools unless the user deliberately enables
them in `/agents`.

The launcher sets `HUMANIZE_HOME` to the owner-only project path
`.humanize/scheduler/runtime`. This isolates saved model/UI choices and upstream
`fallbacks.json` retry/fallback rules from unrelated user-global Humanize state while leaving the
native Claude/Codex credential homes unchanged. The pinned upstream defaults to zero extra retries
and no fallback; the scheduler's explicit 5m/10m/30m/1h policy remains the only
repository-controlled retry path. A native model CLI may still apply its own internal backoff. The
child environment also sets `PYTHONDONTWRITEBYTECODE=1`, preventing a normal run from
dirtying the audited editable checkout and invalidating the next launch. Direct `hmz` bypasses this
project-state isolation and source-cleanliness guard.
Running `hmz -f local/scheduler` directly deliberately bypasses these automatic defaults and uses
Humanize's saved choices instead. Model identifiers are passed to the installed
backends as written; the corresponding model catalogs and credentials must be available when a
turn starts. These user-selected TUI defaults are separate from the audited semantic roles in
`config/workflows/models.json`; the launcher does not claim a fresh catalog receipt for them.

The launcher accepts only the audited source-local Humanize install selected by `HUMANIZE2_HOME` or
its standard data path. Before either `--check` or a TUI launch, the shared pinned-install validator
binds its Git checkout, wrapper, interpreter, editable package, imported source, and version. An
arbitrary executable at that path and every unrelated `hmz` on `PATH` are rejected; install or repair
the pin with `.claude/skills/humanize-workflow/scripts/ensure_humanize.py`.

The launcher refuses re-entry through that entry when it inherits a sealed scheduler context. It
also holds one nonblocking project-local owner lock for the life of the TUI process; a second
launcher for the same project exits immediately, and process exit releases the lock automatically.
Direct `hmz` bypasses these launcher guards as well as the defaults; the Scheduler prompt forbids it,
but Humanize does not tool-confine the Scheduler. The launcher sets `HUMANIZE_SENTRY=off` only for
the launched process, so
the scheduler neither reports telemetry nor opens Humanize's first-run reporting question; it does
not change the user's saved global reporting preference. The guard coordinates ordinary processes;
it is not confinement against a malicious same-UID process.

A versioned Slot selects an ordered subset of the ten child positions, but keeps the same
configured Agent objects when the link changes. The bundled Seed Slot selects executor and
reviewer.

The outer `Config` defaults to 50 scheduler rounds. Each native Codex scheduler turn gets one
initial attempt plus four retry rounds for identified recoverable service, transport, timeout,
rate-limit, or usage-limit-window errors. The fixed waits are 5 minutes, 10 minutes, 30 minutes,
and 1 hour. Insufficient credits or billing/payment authority, authentication, permission, model,
sandbox, and stopped app-server errors stop immediately. Every failed attempt resets
`dispatch.txt` to `pending\n` and records a bounded,
redacted diagnostic before retrying or raising.

Only the outer scheduler decides which Slot to run; it does not execute task work or launch native
subagents. The Slot alone invokes executor, reviewer, planner, or the other child roles it declares.
Humanize's live `Turns` panel aggregates both layers into one transition graph, not an ordered or
nested transcript. `scheduler -> planner` and `planner -> scheduler` can mean child entry and
return; they do not imply a scheduler inside the Slot. The persisted cycle directory keeps the
exact boundary instead: its outer `cycle.jsonl` records `called`/`returned`, and each Slot call has
its own `cycle.<flow>_<id>.jsonl` record in that same directory.

The scheduler protocol and its only admitted child target are documented in
`scheduler/README.md`. `dispatch.txt` and `history.jsonl` are runtime state and are ignored by
Git. The tracked `flows/slot.py` is a relative symlink to a content-addressed
`flows/versions/slot-<sha256>.py` file. Do not edit that link or version file directly; validate a
regular candidate with the installer so the version is published and the link is atomically
retargeted. A child result or a `false` decision is not an independent completion verdict for the
outer agent framework.

## Concurrent flow calls and recovery

The `local/parallel` flow is the supervisor's fan-out boundary. It accepts up to twenty named
branches, limits active calls with `max_concurrency`, and calls the private `parallel_worker` flow
with a fresh clone of the executor and reviewer for every branch. Each branch is staged in a
disposable reflink-or-copy snapshot. Project-level `AGENTS.md`, `CLAUDE.md`, CLI settings, and all
ambient skill directories are removed from that snapshot; the worker receives only the skill names
listed for its executor and reviewer. A branch result and its changed paths are copied back to
`workflow-output/humanize/parallel/<run-id>/`, then the snapshot is removed unless
`keep_workspaces: true` is explicitly set.

Run it with the latest Humanize installation:

```bash
hmz exec -f .humanize/flows/parallel \
  -a executor=codex/gpt-6-astra:max \
  -a reviewer=claude/claude-opus-5:max \
  -c .humanize/parallel-config.example.yaml \
  "run the configured branches"
```

The meta-flow is marked `resumable=True`. Humanize persists its branch state in the epic; `/resume`
restarts the same flow and skips completed branches, while branches left `running`, `failed`, or
`interrupted` are retried. The native `/epics` view is the authoritative run list and flow tree.
For a scriptable equivalent of the requested `/flow-history` and `/status` views, use:

```bash
./.humanize/run_scheduler.py --flow-history
./.humanize/run_scheduler.py --status
```

These commands read only Humanize journals. They list each called flow, the agents it opened, and
the recorded call/session actions; they never start a model. `/status` intentionally shows the
flow-level view when there are multiple branches. With one branch, inspect that flow's sessions in
`/epics` to see the agent invocation chain.

For unattended machines, wrap a resumable run with the bounded OOM/killed-process watchdog:

```bash
python3 .humanize/auto_resume.py \
  --flow .humanize/flows/parallel \
  --agent executor=codex/gpt-6-astra:max \
  --agent reviewer=claude/claude-opus-5:max \
  --config .humanize/parallel-config.example.yaml \
  --task "run the configured branches" --max-retries 2
```

The watchdog runs each attempt in a child process, retries only a signal/OOM-shaped failure, and
requires a persisted resumable epic before retrying. It never turns an ordinary model error into a
fresh run and stops after the configured retry bound. A daemon or Slurm job may invoke this wrapper;
Humanize's own `/resume` remains the interactive path.
