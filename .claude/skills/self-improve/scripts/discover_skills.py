#!/usr/bin/env python3
"""discover_skills.py — before distilling a NEW skill, find an existing one to UPDATE instead.

Adopted from skillmaxxing's `findUpdateTarget` — the guard against `code-review` / `code-review-2` /
`review-code` proliferation: an EXACT name match, else a description token-Jaccard >= THRESHOLD => prefer
UPDATE over create. ONE tier (this user = this project): scans the project's `./.claude/skills` (the canonical,
git-tracked store where new skills are SAVED) plus any harness pre-installed `~/.claude/skills`; reads each
SKILL.md frontmatter.

Usage:
  discover_skills.py list
  discover_skills.py check "<candidate-name>" "<candidate-description>"
Advisory only (always exits 0 on a valid command): the self-improve skill reads EXISTS/SIMILAR/OK and acts.
"""
import glob
import os
import re
import sys

THRESHOLD = 0.6  # description-Jaccard at/above which "create" should become "update an existing skill"


def _roots():
    cands = [os.path.join(os.getcwd(), ".claude", "skills"), os.path.expanduser("~/.claude/skills")]
    out, seen = [], set()
    for c in cands:
        rc = os.path.realpath(c)
        if rc not in seen and os.path.isdir(c):
            seen.add(rc)
            out.append(c)
    return out


def _frontmatter(path):
    name = desc = ""
    try:
        s = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return name, desc
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---", s, re.S)
    block = m.group(1) if m else s[:600]
    for key in ("name", "description"):
        km = re.search(rf"^{key}:\s*(.+?)\s*$", block, re.M)
        if km:
            v = km.group(1).strip().strip('"').strip("'")
            if key == "name":
                name = v
            else:
                desc = v
    if not name:
        name = os.path.basename(os.path.dirname(path))
    return name, desc


def _skills():
    out = []
    for root in _roots():
        for f in sorted(glob.glob(os.path.join(root, "*", "SKILL.md"))):
            name, desc = _frontmatter(f)
            out.append((name, desc, f))
    return out


def _toks(s):
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "list"
    sk = _skills()
    if cmd == "list":
        if not sk:
            print("(no installed skills found)")
            return
        for name, desc, path in sk:
            print(f"{name}: {desc[:100]}  ({path})")
        return
    if cmd == "check":
        if len(args) < 3:
            sys.exit('usage: discover_skills.py check "<name>" "<description>"')
        name, desc = args[1], args[2]
        exact = [s for s in sk if s[0].lower() == name.lower()]
        if exact:
            print(f"EXISTS: a skill named '{exact[0][0]}' already exists ({exact[0][2]}) "
                  f"-> UPDATE it, do NOT create a duplicate.")
            return
        dt = _toks(desc)
        scored = sorted(((_jaccard(dt, _toks(s[1])), s) for s in sk), key=lambda x: x[0], reverse=True)
        if scored and scored[0][0] >= THRESHOLD:
            sim, s = scored[0]
            print(f"SIMILAR: '{s[0]}' is {sim * 100:.0f}% similar ({s[2]}) "
                  f"-> UPDATE/optimize it instead of creating a near-duplicate.")
            return
        closest = f" (closest: '{scored[0][1][0]}' {scored[0][0] * 100:.0f}%)" if scored else ""
        print(f"OK: no existing skill >= {int(THRESHOLD * 100)}% similar{closest} -> safe to create a new one.")
        return
    sys.exit(f"unknown command '{cmd}' (use: list | check)")


if __name__ == "__main__":
    main()
