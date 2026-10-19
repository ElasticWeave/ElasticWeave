#!/usr/bin/env python3
"""scan_session.py — the mechanical session-miner for the `session-import` skill.

Given a prior agent session (Claude Code `.jsonl` project, Codex rollout, or Cursor `state.vscdb`) for a task
the user has been working on, mine durable SETUP signals and emit them as evidence for `default-mode` to adopt.
It writes ONLY under --out (a `migration/` staging dir); it never writes canonical project state,
generates an executable acceptance gate, freezes a baseline, or commits. This is the mechanical half;
the agent then DISTILLS the buckets into drafts per `references/signal-map.md` (judgment, not this script).

Reuses `constraints-to-hooks/scripts/extract_constraints.py` (KW / _iter_jsonl_text / scan_sessions /
encode_claude_project / channel3_history / repo_root) — the tolerant Claude+Codex JSONL reader and the whole
constraint channel — instead of re-implementing them.

Usage:
  python3 scan_session.py --repo <target-repo-or-workdir> \
      [--claude-dir <dir> | --codex-file <f> | --cursor-workspace <dir>] [--out migration] [--max 400]

  With no explicit source flag it AUTO-DETECTS: Claude (~/.claude/projects/<enc(repo)>/*.jsonl) + Codex
  (~/.codex/sessions/**/*.jsonl whose session_meta.payload.cwd is under --repo). Output: <out>/signals.json
  (bucket -> [{text, src, confidence}]) + <out>/report.md (human-readable, [src]-tagged, ⚠ CONFIRM on the
  low-confidence + all alignment-critical buckets). Never crashes on a missing/malformed source; no source -> empty
  output + exit 0.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
from glob import glob
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "constraints-to-hooks", "scripts")))
import extract_constraints as ec  # noqa: E402  (KW, _iter_jsonl_text, scan_sessions, encode_claude_project, channel3_history, repo_root)

# --- secret redaction (applied to every text BEFORE extraction — transcripts routinely carry live tokens) ---
_SECRETS = [
    re.compile(r"lin_api_[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\\n\\r]*.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
    re.compile(r"xox[a-z]-[A-Za-z0-9-]{16,}"),
    re.compile(r"npm_[A-Za-z0-9]{20,}|//registry\.npmjs\.org/:_authToken=\S+"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]+", re.I),
    re.compile(r"(?<==)[A-Za-z0-9+/_-]{40,}"),  # generic KEY=<40+ char token> (keeps the key=, drops the value)
]


def redact(text):
    for pat in _SECRETS:
        text = pat.sub("‹REDACTED›", text)
    return text


# --- buckets: (compiled cue, confidence). Constraints go through the reused KW channel separately. ---
# These buckets determine what completion means or which evidence may be changed, so they always
# require confirmation even when the source cue is strong.
CONFIRM_REQUIRED = {
    "goal", "milestones", "acceptance_criteria", "graded_eval",
    "evaluation_fixtures", "off_limits",
}
BUCKETS = {
    "goal": (re.compile(r"\b(the goal is|we[’']?re trying to|objective is|aim to|the task is|trying to (make|get|reduce|improve|build))\b", re.I), "high"),
    "milestones": (re.compile(r"\b(milestone|\bM[1-9]\b|success (=|criteria|is)|target of|beat|reach)\b", re.I), "med"),
    "acceptance_criteria": (re.compile(r"\b(pytest|python3? -m \w|python3? tests/|tests? pass|done when|passes when|acceptance|verified?\b|verification command|check command|exit 0)\b", re.I), "high"),
    "graded_eval": (re.compile(r"\b(baseline|threshold|eval(uation)? set|benchmark|beat by|\bF1\b|accuracy|latency|throughput|metric)\b", re.I), "med"),
    "evaluation_fixtures": (re.compile(r"\b(fixture|golden (file|case|output)|reference output|held[- ]out|fixed (eval|test) (input|case)|eval(uation)? (input|case|corpus|dataset))\b", re.I), "med"),
    "off_limits": (re.compile(r"\b(don[’']?t (edit|modify|touch)|do not (edit|modify|touch)|immutable|frozen|read-?only|never (edit|modify|change)|off-limits|protected)\b|\u4e0d\u8981(\u6539|\u52a8|\u4fee\u6539)|\u7981\u6b62\u4fee\u6539", re.I), "high"),
    "target_repo": (re.compile(r"(?:git@|https?://)[\w./:-]+\.git|github\.com/[\w.-]+/[\w.-]+|\bgit clone\b|\bsubmodule\b", re.I), "high"),
    "project_remote": (re.compile(r"\b(push to|origin\s*[:=]|remote\s*[:=]|private repo)\b", re.I), "low"),
    "machine_constraints": (re.compile(r"\b(no gpu|cpu[- ]only|no sudo|disk quota|OOM|single[- ]core|N_CORES\s*=\s*1|read-?only (fs|filesystem))\b", re.I), "med"),
    "resources_compute": (re.compile(r"\b(ssh |srun|sbatch|slurm|salloc|gpu|allocate|quota|runs on|compute node|A100|H100|B200|node\d)\b", re.I), "med"),
    "machine_facts": (re.compile(r"\b(VLEN|per cycle|\d+/cycle|rate[- ]limit|ISA|SCRATCH|bandwidth|SIMD|VLIW|register|mod 2)\b", re.I), "med"),
    "key_decisions": (re.compile(r"\b(we decided|decided to|chose|decision:|the approach is|going with|opted for|strategy is)\b", re.I), "med"),
    "rejected_paths": (re.compile(r"\b(didn[’']?t work|did not work|reverted?|failed because|dead[- ]end|doesn[’']?t help|no improvement|regress(ed|ion)|abandon(ed)?)\b|\u4e0d\u884c|\u6ca1\u7528|\u56de\u9000|\u5931\u8d25", re.I), "high"),
    "reference_material": (re.compile(r"https?://[^\s)\]]+|\barxiv\b|\.pdf\b|reference implementation|see (the )?repo", re.I), "high"),
    "audience": (re.compile(r"\b(I[’']?m a|expert in|familiar with|new to|background in|PhD|it's for)\b", re.I), "low"),
    "debug": (re.compile(r"\b(debug mode|human review|review each (step|issue)|approve every|step[- ]by[- ]step approval)\b", re.I), "low"),
    "linear": (re.compile(r"\bLinear\b|linear team|@[\w-]+ (on|in) linear|assign(ee)? to", re.I), "low"),
}
_SENT = re.compile(r"(?<=[.!?\u3002\uFF01\uFF1F\n])")


# --- source adapters: each yields (text, src) records ---
@dataclass(frozen=True)
class SessionSource:
    path: Path
    text: str


@dataclass(frozen=True)
class FileIdentity:
    mode: int
    device: int
    inode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


def _identity(details):
    return FileIdentity(
        details.st_mode,
        details.st_dev,
        details.st_ino,
        details.st_nlink,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _absolute_lexical(path):
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _directory_flags(label):
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError(f"{label} requires no-follow file-descriptor support")
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_no_follow_directory(path, label):
    absolute = _absolute_lexical(path)
    flags = _directory_flags(label)
    descriptor = None
    try:
        descriptor = os.open(absolute.anchor, flags)
        for component in absolute.parts[1:]:
            before = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise ValueError(f"{label} must contain only non-symlink directories")
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            opened = os.fstat(next_descriptor)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                os.close(next_descriptor)
                raise ValueError(f"{label} changed while it was being opened")
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except ValueError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ValueError(
            f"{label} must resolve through existing non-symlink directories: {exc}"
        ) from exc


def _read_no_follow_regular(path, label, limit=None):
    absolute = _absolute_lexical(path)
    if not absolute.name:
        raise ValueError(f"{label} must name a regular non-symlink file")
    parent_descriptor = _open_no_follow_directory(absolute.parent, label)
    leaf_descriptor = None
    try:
        before = os.stat(absolute.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular non-symlink file")
        flags = (
            os.O_RDONLY
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOCTTY", 0)
        )
        leaf_descriptor = os.open(absolute.name, flags, dir_fd=parent_descriptor)
        opened = os.fstat(leaf_descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ValueError(f"{label} changed while it was being opened")
        chunks = []
        remaining = limit
        while remaining is None or remaining > 0:
            size = 1024 * 1024 if remaining is None else min(1024 * 1024, remaining)
            chunk = os.read(leaf_descriptor, size)
            if not chunk:
                break
            chunks.append(chunk)
            if remaining is not None:
                remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(leaf_descriptor)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(opened, field) != getattr(after, field) for field in stable_fields):
            raise ValueError(f"{label} changed while it was being read")
        if limit is None and len(raw) != after.st_size:
            raise ValueError(f"{label} changed while it was being read")
        return absolute, raw, after
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"{label} must be a regular non-symlink file: {exc}") from exc
    finally:
        if leaf_descriptor is not None:
            os.close(leaf_descriptor)
        os.close(parent_descriptor)


def _load_session_source(path, label):
    absolute, raw, _ = _read_no_follow_regular(path, label)
    return SessionSource(absolute, raw.decode("utf-8", errors="replace"))


def _codex_cwd(raw):
    try:
        meta = json.loads(raw.splitlines()[0])
        if not isinstance(meta, dict):
            return ""
        payload = meta.get("payload")
        if meta.get("type") != "session_meta" or not isinstance(payload, dict):
            return ""
        cwd = payload.get("cwd")
        return cwd if isinstance(cwd, str) and cwd else ""
    except (IndexError, TypeError, ValueError):
        return ""


def _contained(path, repo):
    try:
        return Path(path).resolve(strict=True).is_relative_to(Path(repo).resolve(strict=True))
    except (OSError, RuntimeError):
        return False


def _candidate_metadata(paths, label, fail_closed):
    candidates = []
    for path in paths:
        try:
            absolute, _, details = _read_no_follow_regular(path, label, limit=0)
            candidates.append((details.st_mtime_ns, absolute))
        except ValueError:
            if fail_closed:
                raise
    return candidates


def _load_recent(candidates, label, max_files, fail_closed):
    selected = sorted(candidates, reverse=True)[:max_files]
    sources = []
    for _, path in selected:
        try:
            sources.append(_load_session_source(path, label))
        except ValueError:
            if fail_closed:
                raise
    return sources


def _list_claude_sources(directory):
    descriptor = _open_no_follow_directory(directory, "Claude source directory")
    try:
        return [
            _absolute_lexical(directory) / name
            for name in os.listdir(descriptor)
            if name.endswith(".jsonl")
        ]
    except OSError as exc:
        raise ValueError(f"Claude source directory cannot be listed safely: {exc}") from exc
    finally:
        os.close(descriptor)


def resolve_sources(repo, claude_dir, codex_file, cursor_ws, out, max_files):
    """Return (claude_files, codex_files, cursor_texts, sources_read[]) — session files capped to the
    most-recent `max_files` (migration wants the CURRENT task's context, not the repo's whole history)."""
    claude_files, codex_files, cursor_texts, read = [], [], [], []
    # Claude
    if claude_dir:
        requested = _absolute_lexical(claude_dir)
        expected = _absolute_lexical(ec.encode_claude_project(repo))
        if requested != expected:
            raise ValueError(
                "explicit Claude source directory does not match the exact target repo"
            )
        candidates = _candidate_metadata(
            _list_claude_sources(requested), "Claude session source", True
        )
        claude_files = _load_recent(
            candidates, "Claude session source", max_files, True
        )
    elif not (codex_file or cursor_ws):  # auto-detect only when no explicit source given
        candidates = _candidate_metadata(
            glob(ec.encode_claude_project(repo) + "/*.jsonl"),
            "Claude session source",
            False,
        )
        claude_files = _load_recent(
            candidates, "Claude session source", max_files, False
        )
    # Codex
    if codex_file:
        source = _load_session_source(codex_file, "Codex session source")
        cwd = _codex_cwd(source.text)
        if not cwd:
            raise ValueError("explicit Codex session must declare session_meta.payload.cwd")
        if not _contained(cwd, repo):
            raise ValueError("explicit Codex session cwd is outside the exact target repo")
        codex_files = [source]
    elif not (claude_dir or cursor_ws):
        candidates = []
        for path in glob(os.path.expanduser("~/.codex/sessions/**/*.jsonl"), recursive=True):
            try:
                absolute, prefix, details = _read_no_follow_regular(
                    path, "Codex session source", limit=1024 * 1024
                )
            except ValueError:
                continue
            if _contained(_codex_cwd(prefix.decode("utf-8", errors="replace")), repo):
                candidates.append((details.st_mtime_ns, absolute))
        for source in _load_recent(candidates, "Codex session source", max_files, False):
            if _contained(_codex_cwd(source.text), repo):
                codex_files.append(source)
    # Cursor (best-effort; else paste fallback)
    if cursor_ws:
        cursor_texts, cur_read = read_cursor(cursor_ws, repo)
        read += cur_read
    paste = Path(out) / "cursor_paste.txt"
    try:
        paste.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError(f"Cursor paste source cannot be inspected safely: {exc}") from exc
    else:
        source = _load_session_source(paste, "Cursor paste source")
        cursor_texts.append((redact(source.text), "cursor_paste.txt"))
        read.append(str(source.path))
    read += [str(source.path) for source in claude_files + codex_files]
    return claude_files, codex_files, cursor_texts, read


def _workspace_folder(ws):
    workspace_json = Path(ws) / "workspace.json"
    try:
        source = _load_session_source(workspace_json, "Cursor workspace identity file")
        raw = json.loads(source.text)
        folder = raw.get("folder")
        parsed = urlparse(folder)
        if parsed.scheme != "file":
            raise ValueError
        return unquote(parsed.path)
    except Exception as exc:
        raise ValueError("Cursor workspace identity is invalid") from exc


def read_cursor(ws, repo):
    """Best-effort Cursor state.vscdb reader (read-only). Returns ([(text, src)], [files_read])."""
    if not _contained(_workspace_folder(ws), repo):
        raise ValueError("Cursor workspace identity does not match the exact target repo")
    texts, read = [], []
    # Workspace-local storage is identity-bound. Global storage mixes unrelated projects and is never mined.
    for db in (os.path.join(ws, "state.vscdb"),):
        db_path = Path(db)
        try:
            db_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(f"Cursor state database cannot be inspected safely: {exc}") from exc
        db_path, _, _ = _read_no_follow_regular(
            db_path, "Cursor state database", limit=0
        )
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            keys = ("composer.composerData", "aiService.prompts", "aiService.generations",
                    "workbench.panel.aichat.view.aichat.chatdata", "history.entries")
            q = "SELECT key, value FROM ItemTable WHERE key IN (%s)" % ",".join("?" * len(keys))
            for key, val in con.execute(q, keys).fetchall():
                texts.append((redact(_flatten_json_text(val)),
                              "cursor:" + str(db_path) + ":key:" + key))
            con.close()
            read.append(str(db_path))
        except Exception:
            continue  # schema drift / locked / absent -> fall back to paste
    return texts, read


def _flatten_json_text(val):
    """Pull all string values out of an arbitrary JSON blob (Cursor's schema varies by version)."""
    out = []
    try:
        obj = json.loads(val) if isinstance(val, (str, bytes)) else val
    except Exception:
        return str(val)[:5000]
    stack = [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, str):
            if len(x) >= 8:
                out.append(x)
        elif isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return "\n".join(out)


def _jsonl_text_with_lines(raw, repo_filter=None):
    """Yield extracted text with its exact source event line."""
    if repo_filter and repo_filter not in raw:
        return
    for line_no, line in enumerate(raw.splitlines(), 1):
        try:
            obj = json.loads(line)
        except Exception:
            continue
        msg = obj.get("message") or obj.get("payload") or obj
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
        elif isinstance(obj.get("text"), str):
            text = obj["text"]
        else:
            text = ""
        if text:
            yield text, line_no


def records(claude_files, codex_files, cursor_texts, repo, cap):
    """Yield (text, src) from every source, tolerant + capped."""
    n = 0
    for source in claude_files:
        for txt, line_no in _jsonl_text_with_lines(source.text):
            yield redact(txt), "claude:" + str(source.path) + f":line-{line_no}"
            n += 1
            if n >= cap:
                return
    for source in codex_files:
        for txt, line_no in _jsonl_text_with_lines(source.text):
            yield redact(txt), "codex:" + str(source.path) + f":line-{line_no}"
            n += 1
            if n >= cap:
                return
    for txt, src in cursor_texts:
        for line_no, line in enumerate(txt.splitlines(), 1):
            if line.strip():
                yield line, src + f":line-{line_no}"
                n += 1
                if n >= cap:
                    return


def _output_leaf_identity(path):
    try:
        details = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"migration output leaf cannot be inspected safely: {exc}") from exc
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("migration output files must be regular non-symlink files")
    if details.st_nlink != 1:
        raise ValueError("migration output files must be single-link regular files")
    return _identity(details)


def _atomic_write_text(path, payload, expected_identity):
    if _output_leaf_identity(path) != expected_identity:
        raise ValueError(f"migration output leaf changed before write: {path.name}")
    descriptor = None
    temporary = None
    temporary_identity = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ValueError("temporary migration output must be a single-link regular file")
        temporary_identity = _identity(details)
        mode = stat.S_IMODE(expected_identity.mode) if expected_identity else 0o600
        os.fchmod(descriptor, mode)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = None
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            written = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(written.st_mode)
                or written.st_nlink != 1
                or (written.st_dev, written.st_ino)
                != (temporary_identity.device, temporary_identity.inode)
            ):
                raise ValueError("temporary migration output changed while being written")
        if _output_leaf_identity(path) != expected_identity:
            raise ValueError(f"migration output leaf changed before replacement: {path.name}")
        os.replace(temporary, path)
        final_identity = _output_leaf_identity(path)
        if (
            final_identity is None
            or (final_identity.device, final_identity.inode)
            != (temporary_identity.device, temporary_identity.inode)
        ):
            raise ValueError(f"atomic migration output was not retained: {path.name}")
        directory_descriptor = _open_no_follow_directory(
            path.parent, "migration output directory"
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(f"migration output could not be written atomically: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                current = temporary.lstat()
            except FileNotFoundError:
                pass
            else:
                if temporary_identity is None or (
                    current.st_dev,
                    current.st_ino,
                ) == (temporary_identity.device, temporary_identity.inode):
                    temporary.unlink()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="the target repo / working dir the prior session was about")
    ap.add_argument("--claude-dir", help="explicit ~/.claude/projects/<enc> dir (else auto from --repo)")
    ap.add_argument("--codex-file", help="explicit ~/.codex/sessions rollout .jsonl")
    ap.add_argument("--cursor-workspace", help="explicit Cursor workspaceStorage/<hash> dir")
    ap.add_argument("--out", help="optional exact repo/migration path or child (default: <repo>/migration)")
    ap.add_argument("--max", type=int, default=400, help="max transcript lines to scan (bounds runtime)")
    ap.add_argument("--max-files", type=int, default=25, help="cap on most-recent source files mined")
    a = ap.parse_args()
    repo = os.path.abspath(os.path.expanduser(a.repo))
    repo_path = Path(repo).resolve(strict=True)
    if not repo_path.is_dir():
        ap.error("--repo must be an existing directory")
    repo = str(repo_path)
    migration = repo_path / "migration"
    requested_out = Path(a.out).expanduser() if a.out else migration
    if ".." in requested_out.parts:
        ap.error("--out must not contain '..'")
    out_path = requested_out.absolute()
    try:
        # Permit the migration root or a contained run-specific child; never follow a symlink parent.
        canonical_out = out_path.resolve(strict=False)
        if canonical_out != migration and not canonical_out.is_relative_to(migration):
            ap.error("--out must be the repository's migration/ directory or a child of it")
        parent = out_path
        while parent != repo_path:
            if parent.is_symlink():
                ap.error("--out must not traverse symlinks")
            parent = parent.parent
    except (OSError, RuntimeError, ValueError):
        ap.error("--out must be contained under repository migration/")
    os.makedirs(out_path, exist_ok=True)
    try:
        output_identities = {
            name: _output_leaf_identity(out_path / name)
            for name in ("signals.json", "report.md")
        }
    except ValueError as exc:
        ap.error(str(exc))
    a.out = str(out_path)

    try:
        claude_files, codex_files, cursor_texts, sources_read = resolve_sources(
            repo, a.claude_dir, a.codex_file, a.cursor_workspace, a.out, a.max_files)
    except ValueError as exc:
        ap.error(str(exc))

    # per-bucket regex extraction over the (redacted) record stream
    signals = {b: [] for b in BUCKETS}
    signals["constraints"] = []      # from the reused KW channel
    signals["current_state"] = []    # the last few substantive turns
    seen = {b: set() for b in signals}
    recent = []
    for text, src in records(claude_files, codex_files, cursor_texts, repo, a.max):
        if len(text) > 40:
            recent.append((text[:600], src))
        for sent in _SENT.split(text):
            sent = sent.strip()
            if not (12 <= len(sent) <= 400):
                continue
            for b, (cue, conf) in BUCKETS.items():
                if cue.search(sent):
                    key = re.sub(r"\s+", " ", sent.lower())[:120]
                    if key not in seen[b]:
                        seen[b].add(key)
                        signals[b].append({"text": sent, "src": src, "confidence": conf})
    # current state = last ~5 substantive turns (approx recency; sources are mtime/name ordered)
    for text, src in recent[-5:]:
        signals["current_state"].append({"text": text, "src": src, "confidence": "high"})
    # constraints via the reused channel (its own dedup + [src] tags)
    # Reuse the already-redacted, line-precise stream for constraints too; never re-read raw
    # transcript bytes through a helper that drops provenance or uses weaker redaction.
    for source_text, source_id in records(claude_files, codex_files, cursor_texts, repo, a.max):
        for sentence in _SENT.split(source_text):
            sentence = sentence.strip()
            if 12 <= len(sentence) <= 300 and ec.KW.search(sentence) and not ec.NOISE.match(sentence):
                key = re.sub(r"\s+", " ", sentence.lower())[:120]
                if key not in seen["constraints"]:
                    seen["constraints"].add(key)
                    signals["constraints"].append({"text": sentence, "src": source_id, "confidence": "med"})

    # write signals.json
    signals["_meta"] = {
        "repo": repo,
        "sources_read": [str(Path(s)) for s in sources_read],
        "source_count": len(sources_read),
        "reference_material_route": (
            "untrusted migration lead; keep project-local unless self-improve transfer admission, "
            "supported lemma, learned-reference validation, and independent review all pass"
        ),
    }
    try:
        _atomic_write_text(
            out_path / "signals.json",
            json.dumps(signals, ensure_ascii=False, indent=1),
            output_identities["signals.json"],
        )
    except ValueError as exc:
        ap.error(str(exc))

    # write report.md
    order = ["goal", "milestones", "acceptance_criteria", "graded_eval", "evaluation_fixtures",
             "off_limits", "target_repo",
             "project_remote", "constraints", "machine_constraints", "resources_compute", "machine_facts",
             "key_decisions", "rejected_paths", "reference_material", "audience", "debug", "linear",
             "current_state"]
    _srcs = signals["_meta"]["sources_read"]
    _srcshow = (", ".join(_srcs[:4]) + (f", … (+{len(_srcs) - 4} more)" if len(_srcs) > 4 else "")) or "(none found)"
    lines = [f"# session-import — mined signals for: {repo}",
             f"# sources ({len(_srcs)}): {_srcshow}",
             "# EVIDENCE ONLY — distill into drafts per references/signal-map.md; every alignment-critical item needs human confirm.\n"]
    total = 0
    for b in order:
        items = signals.get(b, [])
        if not items:
            continue
        total += len(items)
        lines.append(f"\n## {b}  ({len(items)})")
        for it in items:
            mark = "  ⚠ CONFIRM" if (b in CONFIRM_REQUIRED or it["confidence"] == "low") else ""
            lines.append(f"- [{it['src']}] {it['text']}{mark}")
    if not total:
        lines.append("\n(no signals found — check --repo / the source flags, or paste a transcript into "
                     "migration/cursor_paste.txt)")
    lines.append("\n# Next: distill each bucket into its migration/ draft (signal-map.md), then human-confirm, then default-mode adopts.")
    try:
        _atomic_write_text(
            out_path / "report.md",
            "\n".join(lines) + "\n",
            output_identities["report.md"],
        )
    except ValueError as exc:
        ap.error(str(exc))

    counts = " ".join(f"{b}={len(signals.get(b, []))}" for b in order if signals.get(b))
    print(f"[session-import] repo={repo} sources={len(sources_read)} signals: {counts or '(none)'} -> {a.out}/")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
