#!/usr/bin/env python3
"""validate.py — mechanical governance for .memory/ (conventions bloat; validators don't).

Checks (exit 0 clean / 2 violations):
  1. MAIN.md caps: <=100 lines, <=8000 chars; every entry line matches `- [YYYY-MM-DD] ...`.
  2. Secret patterns nowhere outside the exact .memory/private/ subtree (sk-ant-, ntn_,
     github_pat_, lin_api_, OAUTH_TOKEN=..., BEGIN ... PRIVATE KEY). Prefix-similar siblings
     such as .memory/private-notes/ are not exempt.
  3. Skill-memory abstraction sync: each .memory/skills/<name>.md must START with
     `abstraction: <one line>`; if the matching .claude/skills/<name>/SKILL.md carries a cached
     `> **Memory abstraction:**` line, the text must MATCH (drift is the classic rot).
  4. inbox/new entries are single files (no directories), each with a `- [date]` first entry line.
  5. Project memory cannot contain a self-evolve cross-task-reference carrier. Such material must
     pass the transfer gate and live under a skill's references/ with its supported lemma link.

Run during default-mode startup, before promoting inbox entries, and in the commit-review checklist.
"""
import os
import re
import sys

ROOT = os.getcwd()
MEM = os.path.join(ROOT, ".memory")
problems: list[str] = []

ENTRY = re.compile(r"^- \[\d{4}-\d{2}-\d{2}\] \S")
SECRETS = re.compile(r"sk-ant-|ntn_[A-Za-z0-9]|github_pat_|lin_api_|OAUTH_TOKEN=\S|BEGIN [A-Z ]*PRIVATE KEY")
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def in_private_memory_subtree(dirpath: str) -> bool:
    """True only for `.memory/private` and its descendants.

    Relative path components, not string containment: `.memory/private-notes` must
    still be scanned.
    """
    rel = os.path.relpath(os.path.normpath(dirpath), os.path.normpath(MEM))
    if rel in (os.curdir, os.pardir) or rel.startswith(os.pardir + os.sep):
        return False
    return rel.split(os.sep)[0] == "private"


def check_main() -> None:
    p = os.path.join(MEM, "MAIN.md")
    if not os.path.exists(p):
        problems.append("MAIN.md missing — default-mode must seed it")
        return
    lines = open(p, encoding="utf-8").read().splitlines()
    if len(lines) > 100:
        problems.append(f"MAIN.md is {len(lines)} lines (cap 100) — triage/condense before adding more")
    if sum(len(l) for l in lines) > 8000:
        problems.append("MAIN.md exceeds 8000 chars (it IS injected context) — condense")
    in_entries = False
    for i, l in enumerate(lines, 1):
        if l.strip() == "## Entries":
            in_entries = True
            continue
        if in_entries and l.strip() and not l.startswith("  ") and not ENTRY.match(l):
            problems.append(f"MAIN.md:{i} entry not in `- [YYYY-MM-DD] …` form: {l[:60]!r}")


def check_secrets() -> None:
    for dirpath, dirnames, filenames in os.walk(MEM):
        if in_private_memory_subtree(dirpath):
            dirnames[:] = []
            continue
        for f in filenames:
            fp = os.path.join(dirpath, f)
            try:
                text = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            m = SECRETS.search(text)
            if m:
                problems.append(f"{os.path.relpath(fp, ROOT)}: secret-like pattern {m.group(0)[:12]!r} — memory NEVER holds secrets (use .memory/private/ if unavoidable, better: nowhere)")


def check_skill_abstractions() -> None:
    sdir = os.path.join(MEM, "skills")
    if not os.path.isdir(sdir):
        return
    for f in os.listdir(sdir):
        if not f.endswith(".md"):
            continue
        p = os.path.join(sdir, f)
        first = (open(p, encoding="utf-8").readline() or "").strip()
        m = re.match(r"^abstraction:\s*(\S.*)$", first)
        if not m:
            problems.append(f".memory/skills/{f}: first line must be `abstraction: <one line>` (canonical)")
            continue
        canonical = m.group(1).strip()
        skill_md = os.path.join(ROOT, ".claude", "skills", f[:-3], "SKILL.md")
        if os.path.exists(skill_md):
            for l in open(skill_md, encoding="utf-8"):
                cm = re.search(r"\*\*Memory abstraction:\*\*\s*(\S.*)$", l)
                if cm and cm.group(1).strip().rstrip(".") != canonical.rstrip("."):
                    problems.append(
                        f".claude/skills/{f[:-3]}/SKILL.md cached abstraction DRIFTED from .memory/skills/{f} — re-sync the header")


def check_inbox() -> None:
    new = os.path.join(MEM, "inbox", "new")
    if not os.path.isdir(new):
        problems.append(".memory/inbox/new/ missing")
        return
    for f in os.listdir(new):
        if f == ".gitkeep":
            continue
        fp = os.path.join(new, f)
        if os.path.isdir(fp):
            problems.append(f".memory/inbox/new/{f} is a directory — inbox entries are single files")
            continue
        first_entry = next((l for l in open(fp, encoding="utf-8") if l.startswith("- [")), None)
        if first_entry is None or not ENTRY.match(first_entry):
            problems.append(f".memory/inbox/new/{f}: needs at least one `- [YYYY-MM-DD] …` entry line")


def check_project_scope() -> None:
    for dirpath, dirnames, filenames in os.walk(MEM):
        if in_private_memory_subtree(dirpath):
            dirnames[:] = []
            continue
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            match = FRONTMATTER.match(text)
            if not match:
                continue
            header = match.group(1)
            if re.search(
                r"^kind:\s*['\"]?cross-task-reference['\"]?\s*$", header, re.MULTILINE
            ):
                rel = os.path.relpath(path, ROOT)
                problems.append(
                    f"{rel}: .memory is project-level; move self-evolve cross-task carriers to "
                    "the owning skill's references/ after transfer review"
                )


check_main()
check_secrets()
check_skill_abstractions()
check_inbox()
check_project_scope()

if problems:
    print("MEMORY VALIDATE: FAIL")
    for p in problems:
        print(f"  - {p}")
    sys.exit(2)
print("MEMORY VALIDATE: clean")
