#!/usr/bin/env python3
"""Report frontmatter description sizes for context-budget planning."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


DESCRIPTION = re.compile(r"^description:\s*(.*)$", re.MULTILINE)


def description_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)
    if len(frontmatter) < 3:
        raise ValueError(f"{path}: missing frontmatter")
    match = DESCRIPTION.search(frontmatter[1])
    if match is None:
        raise ValueError(f"{path}: missing description")
    value = match.group(1).strip()
    if value in {"", ">-", "|"}:
        # Folded/block descriptions are retained as a reportable value without
        # attempting to duplicate a YAML parser in this lightweight lint.
        lines = frontmatter[1].splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("description:")) + 1
        value = " ".join(line.strip() for line in lines[start:] if line.strip())
    return value.strip('"\'')


def report(skills_root: Path, maximum: int) -> int:
    failures = 0
    for path in sorted(skills_root.glob("*/SKILL.md")):
        try:
            length = len(description_text(path))
        except (OSError, ValueError) as error:
            print(f"ERROR {error}", file=sys.stderr)
            failures += 1
            continue
        status = "OK" if length <= maximum else "OVER"
        print(f"{status:4} {length:4} {path.parent.name}")
        if status == "OVER":
            failures += 1
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--max", type=int, default=220, dest="maximum")
    args = parser.parse_args(argv)
    return report(args.skills_root, args.maximum)


if __name__ == "__main__":
    raise SystemExit(main())
