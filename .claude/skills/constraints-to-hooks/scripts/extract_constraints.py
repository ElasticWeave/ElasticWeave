#!/usr/bin/env python3
"""extract_constraints.py — surface CANDIDATE constraints at cold start, from the
three default-mode startup channels, for an agent to distill into constraint.md.

It does NOT write constraint.md (that needs judgment) — it gathers evidence:

  Channel 1  current-repo sessions   Claude transcripts for THIS repo
                                      (~/.claude/projects/<encoded-repo>/) + Codex
                                      rollouts (~/.codex/sessions/**) mentioning the repo.
  Channel 2  cross-project sessions   the SAME stores for OTHER repos under $HOME —
                                      UNVERIFIED TRANSFER LEADS only. They cannot enter
                                      constraint.md or deterministic §3 without self-improve
                                      transfer review and validation in this project.
  Channel 3  project history & rules  git log/PR style + in-repo convention files
                                      (CONTRIBUTING, CODEOWNERS, .github/, lint configs).

Usage:
  python3 .claude/skills/constraints-to-hooks/scripts/extract_constraints.py [--repo PATH] [--home PATH] [--max N]

Output: a grouped, de-duplicated report (source-tagged) on stdout. Channel 1/3 evidence may be
distilled into project constraints by judgment. Channel 2 remains quarantined transfer input.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from glob import glob

# Lines that smell like a stated constraint (EN + ZH).
KW = re.compile(
    r"\b(do not|don't|must not|never|forbidden|not allowed|off-limits|read-?only|"
    r"immutable|protected|out of scope|only edit|do NOT|cannot)\b"
    r"|\u4e0d\u8981|\u4e0d\u80fd|\u4e0d\u53ef\u4ee5|\u7981\u6b62|\u53ea\u80fd|\u4e0d\u51c6|\u5207\u52ff|\u7ea6\u675f|\u9650\u5236|\u4e0d\u5f97|\u4e25\u7981",
    re.I,
)
NOISE = re.compile(r"^\s*[\}\]\)]+\s*$")


def _run(cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def repo_root(start=None):
    r = _run(["git", "rev-parse", "--show-toplevel"], cwd=start or os.getcwd())
    return r.stdout.strip() if r.returncode == 0 else os.path.abspath(start or os.getcwd())


def encode_claude_project(path):
    # ~/.claude/projects/-home-user-myrepo  <- /home/user/myrepo
    return os.path.expanduser("~/.claude/projects/" + path.replace("/", "-"))


def _iter_jsonl_text(fp, repo_filter=None):
    """Yield user/assistant text lines from a session jsonl; optionally require the
    repo path to appear somewhere in the file (cheap relevance filter)."""
    try:
        raw = open(fp, encoding="utf-8", errors="replace").read()
    except OSError:
        return
    if repo_filter and repo_filter not in raw:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line or '"' not in line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        # extract any human-ish text fields
        txt = ""
        msg = o.get("message") or o.get("payload") or o
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str):
            txt = content
        elif isinstance(content, list):
            txt = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        elif isinstance(o.get("text"), str):
            txt = o["text"]
        if txt:
            yield txt


def scan_sessions(claude_glob, codex_files, repo_filter, max_hits):
    hits = []
    seen = set()
    def add(src, text):
        for ln in re.split(r"(?<=[.!?\u3002\uFF01\uFF1F\n])", text):
            ln = ln.strip()
            if 12 <= len(ln) <= 300 and KW.search(ln) and not NOISE.match(ln):
                key = re.sub(r"\s+", " ", ln.lower())[:120]
                if key not in seen:
                    seen.add(key); hits.append((src, ln))
    for fp in claude_glob:
        for txt in _iter_jsonl_text(fp):
            add("claude:" + os.path.basename(fp)[:8], txt)
            if len(hits) >= max_hits: return hits
    for fp in codex_files:
        for txt in _iter_jsonl_text(fp, repo_filter=repo_filter):
            add("codex:" + os.path.basename(fp)[:18], txt)
            if len(hits) >= max_hits: return hits
    return hits


def channel3_history(repo):
    out = []
    log = _run(["git", "-C", repo, "log", "--oneline", "-n", "40"]).stdout.strip()
    if log:
        out.append(("git-log (last 40 — PR/commit style)", log))
    merges = _run(["git", "-C", repo, "log", "--merges", "--oneline", "-n", "10"]).stdout.strip()
    if merges:
        out.append(("merge/PR pattern", merges))
    # convention files
    conv = []
    for pat in ["CONTRIBUTING*", "CODEOWNERS", ".github/CODEOWNERS", "PULL_REQUEST_TEMPLATE*",
                ".github/PULL_REQUEST_TEMPLATE*", ".github/workflows/*", ".editorconfig",
                ".pre-commit-config.yaml", "ruff.toml", ".flake8", ".eslintrc*", "CLAUDE.md", "AGENTS.md"]:
        conv += glob(os.path.join(repo, pat))
    if conv:
        out.append(("in-repo convention files (READ these)", "\n".join(sorted(set(conv)))))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--max", type=int, default=60)
    a = ap.parse_args()
    repo = repo_root(a.repo)
    codex_files = sorted(glob(os.path.expanduser("~/.codex/sessions/**/*.jsonl"), recursive=True))

    print(f"# Candidate constraints for: {repo}\n# (evidence only — distill into constraint.md)\n")

    print("=" * 70)
    print("CHANNEL 1 — current-repo sessions")
    print("=" * 70)
    c1 = scan_sessions(sorted(glob(encode_claude_project(repo) + "/*.jsonl")),
                       codex_files, repo_filter=repo, max_hits=a.max)
    for src, ln in c1: print(f"  [{src}] {ln}")
    if not c1: print("  (none found)")

    print("\n" + "=" * 70)
    print("CHANNEL 2 — UNVERIFIED CROSS-PROJECT LEADS (quarantined transfer input)")
    print("=" * 70)
    print("  DO NOT distill these directly into constraint.md or deterministic §3.")
    print("  Admit only through self-improve transfer review plus validation in THIS project.")
    other = [g for g in glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
             if encode_claude_project(repo) not in g]
    c2 = scan_sessions(other, [], repo_filter=None, max_hits=a.max // 2)
    for src, ln in c2: print(f"  [{src}] {ln}")
    if not c2: print("  (none found)")

    print("\n" + "=" * 70)
    print("CHANNEL 3 — project history & conventions")
    print("=" * 70)
    for title, body in channel3_history(repo):
        print(f"\n-- {title} --\n{body}")

    print("\n# Next: distill project-supported Channel 1/3 evidence into constraint.md, then")
    print("#       run constraints-to-hooks to compile §3. Keep Channel 2 quarantined until")
    print("#       self-improve transfer review and this-project validation both succeed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
