# source-adapters — where each agent stores a session, and how to read it

The skill is a thin **source-adapter layer → shared extractors**: only "turn a session into a `(role, text)`
stream" differs per source; every downstream bucket/extractor (`references/signal-map.md`) is identical. The
receiver is fixed (`default-mode`), so there is NO per-*target* adaptation — only these three per-*source* readers.

## Claude Code  (fully supported; verified)
- **Location:** `~/.claude/projects/<enc>/<uuid>.jsonl`, where `<enc>` = the working-dir path with every `/`
  replaced by `-` (identical to `extract_constraints.encode_claude_project`).
- **Format:** one JSON event per line; user/assistant turns are `{"type":"user"|"assistant","message":{"content":…}}`
  where `content` is a **str OR a list of `{"text":…}`** parts. Other line types (queue-operation, summary,
  isCompactSummary, …) are skipped by the text reader.
- **Locate:** `encode_claude_project(repo)` → glob `*.jsonl`, rank by mtime, take the most recent RELEVANT.
- **Reuse:** `extract_constraints._iter_jsonl_text(fp)` reads it unchanged.

## Codex  (fully supported; verified)
- **Location:** `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`.
- **Format:** first line `{"type":"session_meta","payload":{"cwd":…,"id":…,"timestamp":…,"cli_version":…}}`;
  subsequent lines carry text under `payload`/`message`/`text` (the same `_iter_jsonl_text` fallbacks handle it).
- **Locate:** glob all rollouts, keep those whose first-line `payload.cwd` **is (under) the target repo** — a
  cwd-precise filter, tighter than extract_constraints' whole-file text match. Rank by timestamp.

## Cursor  (best-effort / EXPERIMENTAL — not validated on a real install)
- **Location:** `~/.config/Cursor/User/workspaceStorage/<md5>/state.vscdb`. Never read
  `globalStorage/state.vscdb`: it mixes unrelated workspaces and cannot provide project provenance.
  a **SQLite** DB, table `ItemTable(key, value)` with JSON values under version-dependent keys
  (`composer.composerData`, `aiService.prompts` / `aiService.generations`,
  `workbench.panel.aichat.view.aichat.chatdata`, `history.entries`).
- **Locate:** the `workspaceStorage/<hash>/workspace.json` whose `folder` file-URI matches the target repo.
- **Read:** open the DB **read-only**, pull the known keys, flatten to the shared `(role, text)` stream.
- **Degrade gracefully:** on schema drift or a missing DB, print what was tried and emit the exact
  candidate request to paste the transcript into `migration/cursor_paste.txt`; the adapter then
  treats it as one plain-text session. Outside the cold-start launching conversation, only a fresh
  typed terminal `HUMAN_BLOCKED` may authorize routing that exact request through the controlling
  session or configured UI channel; `REVISE` continues or redispatches
  adapter work. The cold-start launching conversation alone may use the narrow alignment
  exception. Cursor must never turn adapter failure itself into a human block—the paste request
  must genuinely require a human-only external action.

## Redaction (all sources, before ANY write)
Strip secrets from every extracted line: `lin_api_…`, `ghp_…` / `github_pat_…`, `sk-…` / `sk-ant-…`,
Slack `xox…`, npm tokens, JWTs, `AKIA…`, the complete `-----BEGIN … PRIVATE KEY-----` block,
`Authorization: Bearer …`, and generic `<KEY>=<40+ char hex/b64>`.
Transcripts routinely contain live tokens; `resources.md` is pointers-only.
