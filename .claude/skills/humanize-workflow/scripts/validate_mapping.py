#!/usr/bin/env python3
"""Validate a humanize mapping without importing or executing its Python flow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from safe_mapping import MappingError, validate_full_mapping


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        manifest_path = args.manifest.expanduser()
        manifest = validate_full_mapping(root, manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"humanize-validate: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "valid", "artifact": manifest["artifact"], "revision": manifest["upstream"]["revision"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
