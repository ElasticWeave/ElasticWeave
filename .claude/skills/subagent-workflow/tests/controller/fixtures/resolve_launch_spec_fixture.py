#!/usr/bin/env python3
"""Test-only driver that resolves a launch spec against canned catalog bytes.

The controller suite must exercise the *real* receipt construction — schema,
self-digest, identity, policy digests, fresh-evidence shape — without making one
authenticated native catalog call per activation. `resolve_launch_spec` already
exposes a private in-process seam for exactly this (`catalog_runner`), so this
driver supplies deterministic catalog stdout instead of spawning `codex` or
`cursor-agent`.

This file lives under tests/ and is never imported by production code. There is
deliberately no production CLI flag or environment variable that can reach the
`catalog_runner` seam.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

CONFIG_SYNC = (
    Path(__file__).resolve().parents[4]
    / "update-platform-config" / "scripts" / "config-sync"
)


def _load(name: str, filename: str):
    path = CONFIG_SYNC / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(CONFIG_SYNC))
LIVE_CATALOG = _load("_fixture_live_catalog", "live_catalog.py")
RESOLVER = _load("_fixture_resolve_launch_spec", "resolve_launch_spec.py")

# Fixed catalog snapshots that cover every role the controller fixtures configure.
# Efforts and concrete ids mirror the real catalogs so the receipt's catalogModelId
# is the same shape production would resolve.
CODEX_MODELS = {
    "gpt-5.6-sol": ["low", "medium", "high", "xhigh", "max", "ultra"],
    "gpt-6-astra": ["low", "medium", "high", "xhigh", "max", "ultra"],
    "gpt-5.6-luna": ["low", "medium", "high", "xhigh", "max", "ultra"],
    "gpt-5.6-terra": ["low", "medium", "high", "xhigh", "max"],
}

CLAUDE_MODELS = {
    "claude-opus-5": ["low", "medium", "high", "xhigh", "max"],
    "claude-sonnet-5": ["low", "medium", "high", "xhigh", "max"],
}

CURSOR_IDS = [
    "claude-opus-5-thinking-none",
    "claude-opus-5-thinking-low",
    "claude-opus-5-thinking-medium",
    "claude-opus-5-thinking-high",
    "claude-opus-5-thinking-xhigh",
    "claude-opus-5-thinking-max",
    "claude-sonnet-5-thinking-low",
    "claude-sonnet-5-thinking-medium",
    "claude-sonnet-5-thinking-high",
    "claude-sonnet-5-thinking-xhigh",
    "claude-sonnet-5-thinking-max",
    "gpt-5.6-sol-high",
    "gpt-5.6-sol-max",
    "gpt-5.6-luna-high",
    "gpt-5.6-luna-max",
]


def _codex_bytes() -> bytes:
    document = {
        "models": [
            {
                "slug": slug,
                "default_reasoning_level": levels[0],
                "supported_reasoning_levels": [{"effort": effort} for effort in levels],
            }
            for slug, levels in CODEX_MODELS.items()
        ]
    }
    return json.dumps(document).encode("utf-8") + b"\n"


def _claude_bytes() -> bytes:
    response = {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": "models",
            "response": {
                "models": [
                    {
                        "value": model,
                        "resolvedModel": model,
                        "supportedEffortLevels": efforts,
                    }
                    for model, efforts in CLAUDE_MODELS.items()
                ]
            },
        },
    }
    return json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"


def _cursor_bytes() -> bytes:
    body = "Available models\n\n"
    body += "".join(f"{identifier} - {identifier}\n" for identifier in CURSOR_IDS)
    body += LIVE_CATALOG.CURSOR_TIP_TRAILER + "\n"
    return body.encode("utf-8")


_CANNED = {
    tuple(LIVE_CATALOG.ALLOWED_COMMANDS["claude"]): _claude_bytes,
    ("codex", "debug", "models"): _codex_bytes,
    ("cursor-agent", "models"): _cursor_bytes,
}


def _runner(argv):
    builder = _CANNED.get(tuple(argv))
    if builder is None:
        raise SystemExit(f"fixture catalog runner has no canned output for {argv!r}")
    return 0, builder(), b""


def main(raw_args: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--task-class", required=True)
    parser.add_argument("--reasoning-posture", required=True)
    parser.add_argument("--semantic-role", required=True)
    parser.add_argument("--review-mode", choices=["none", "exact-opposite"], required=True)
    parser.add_argument("--writer-role")
    args = parser.parse_args(raw_args)
    try:
        receipt = RESOLVER.resolve_launch_spec(
            args.models.read_bytes(),
            task_class=args.task_class,
            reasoning_posture=args.reasoning_posture,
            semantic_role=args.semantic_role,
            review_mode=args.review_mode,
            writer_role=args.writer_role,
            catalog_runner=_runner,
        )
    except (OSError, RESOLVER.LaunchSpecError) as error:
        print(f"[fixture-resolve-launch-spec] {error}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
