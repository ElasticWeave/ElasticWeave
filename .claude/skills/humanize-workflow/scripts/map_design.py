#!/usr/bin/env python3
"""Map one eligible neutral design into deterministic humanize2 source bytes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from safe_mapping import (
    MappingError,
    build_manifest,
    canonical_json,
    canonical_reasoning_posture,
    generate_source,
    load_object,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("design", type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        design_path = args.design.resolve()
        spec_path = args.spec.resolve()
        for label, path in (("design", design_path), ("spec", spec_path)):
            if root != path and root not in path.parents:
                raise MappingError(f"{label} must be inside the project root")
            if path.is_symlink():
                raise MappingError(f"{label} must not be a symlink")
        design = load_object(design_path)
        spec = load_object(spec_path)
        design_id = design.get("id")
        # The spec may bind, but cannot choose, the project policy posture.
        canonical_reasoning_posture(root, spec)
        source = generate_source(design, spec)
        artifact = root / ".humanize" / "generated-flows" / f"{design_id}.py"
        manifest_path = root / ".humanize" / "mappings" / f"{design_id}.json"
        if artifact.is_symlink() or manifest_path.is_symlink():
            raise MappingError("refusing to overwrite a symlinked mapping target")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(source, encoding="utf-8")
        manifest = build_manifest(root, design_path, spec_path, artifact)
        manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"humanize-map: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"artifact": str(artifact), "manifest": str(manifest_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
