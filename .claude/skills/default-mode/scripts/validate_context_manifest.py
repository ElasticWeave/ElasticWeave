#!/usr/bin/env python3
"""Validate the mode-specific context routing manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


SCHEMA = "skill-context/v1"
MODES = ("default", "fast", "naive")
MODE_LISTS = ("bootstrap", "core")
AUDIENCES = {"supervisor", "supervisor-or-reviewer", "supervisor-or-domain-node"}
NAME = re.compile(r"^[a-z][a-z0-9-]*$")


class ContextManifestError(ValueError):
    """The context manifest cannot be safely used for routing."""


def _list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContextManifestError(f"{label} must be a list of strings")
    if len(value) != len(set(value)):
        raise ContextManifestError(f"{label} contains duplicates")
    return value


def _names(value: Any, label: str) -> list[str]:
    values = _list(value, label)
    bad = [item for item in values if NAME.fullmatch(item) is None]
    if bad:
        raise ContextManifestError(f"{label} contains unsafe names: {', '.join(bad)}")
    return values


def _check_acyclic(packs: dict[str, dict[str, Any]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ContextManifestError(f"pack dependency cycle includes {name}")
        if name in visited:
            return
        visiting.add(name)
        for dependency in _names(packs[name].get("dependsOn", []), f"packs.{name}.dependsOn"):
            if dependency not in packs:
                raise ContextManifestError(f"pack {name} depends on unknown pack {dependency}")
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in packs:
        visit(name)


def validate_manifest(document: Any, skills_root: Path) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ContextManifestError("manifest must be a JSON object")
    if document.get("schema") != SCHEMA:
        raise ContextManifestError(f"manifest schema must be {SCHEMA}")
    discovery = document.get("discovery")
    if not isinstance(discovery, dict) or discovery.get("skillsRoot") != ".claude/skills" or discovery.get("pattern") != "*/SKILL.md":
        raise ContextManifestError("discovery must preserve the .claude/skills/*/SKILL.md layout")
    if discovery.get("preserveTopLevel") is not True:
        raise ContextManifestError("discovery.preserveTopLevel must be true")

    discovered = {path.parent.name for path in skills_root.glob("*/SKILL.md")}
    if not discovered:
        raise ContextManifestError(f"no top-level skills found under {skills_root}")

    shared = document.get("shared")
    if not isinstance(shared, dict):
        raise ContextManifestError("shared must be an object")
    shared_skills = _names(shared.get("skills", []), "shared.skills")

    modes = document.get("modes")
    if not isinstance(modes, dict) or set(modes) != set(MODES):
        raise ContextManifestError(f"modes must contain exactly: {', '.join(MODES)}")
    mode_values: dict[str, dict[str, list[str]]] = {}
    for mode in MODES:
        value = modes[mode]
        if not isinstance(value, dict):
            raise ContextManifestError(f"modes.{mode} must be an object")
        mode_values[mode] = {
            key: _names(value.get(key, []), f"modes.{mode}.{key}")
            for key in MODE_LISTS
        }
        if value.get("nodeSkills") not in {"declared-per-node", "none"}:
            raise ContextManifestError(f"modes.{mode}.nodeSkills must declare a node context policy")
        mode_values[mode]["onDemandPacks"] = _names(
            value.get("onDemandPacks", []), f"modes.{mode}.onDemandPacks"
        )

    packs = document.get("packs")
    if not isinstance(packs, dict) or not packs:
        raise ContextManifestError("packs must be a non-empty object")
    pack_values: dict[str, dict[str, Any]] = {}
    for name, value in packs.items():
        if not isinstance(name, str) or NAME.fullmatch(name) is None:
            raise ContextManifestError(f"unsafe pack name: {name!r}")
        if not isinstance(value, dict):
            raise ContextManifestError(f"packs.{name} must be an object")
        skills = _names(value.get("skills", []), f"packs.{name}.skills")
        modes_used = _names(value.get("modes", []), f"packs.{name}.modes")
        if not modes_used or any(mode not in MODES for mode in modes_used):
            raise ContextManifestError(f"packs.{name}.modes must name at least one known mode")
        if value.get("load") != "on-demand":
            raise ContextManifestError(f"packs.{name}.load must be on-demand")
        if value.get("audience") not in AUDIENCES:
            raise ContextManifestError(f"packs.{name}.audience is not a supported context audience")
        if not isinstance(value.get("when"), str) or not value["when"].strip():
            raise ContextManifestError(f"packs.{name}.when must be a non-empty string")
        pack_values[name] = value
    _check_acyclic(pack_values)

    for mode, value in mode_values.items():
        unknown = sorted(set(value["onDemandPacks"]) - set(pack_values))
        if unknown:
            raise ContextManifestError(f"modes.{mode}.onDemandPacks references unknown packs: {', '.join(unknown)}")
        for pack in value["onDemandPacks"]:
            if mode not in pack_values[pack]["modes"]:
                raise ContextManifestError(f"pack {pack} is not enabled for mode {mode}")
        if mode == "naive":
            if value["core"] or value["onDemandPacks"] or value["bootstrap"] != ["naive-mode"]:
                raise ContextManifestError("naive mode may load only naive-mode")

    for name, value in pack_values.items():
        if not any(name in mode_values[mode]["onDemandPacks"] for mode in MODES):
            raise ContextManifestError(f"pack {name} is never selected by a mode")

    occurrences: dict[str, list[str]] = {name: [] for name in discovered}

    def account(names: list[str], source: str) -> None:
        for name in names:
            if name not in discovered:
                raise ContextManifestError(f"{source} references unknown skill {name}")
            occurrences.setdefault(name, []).append(source)

    account(shared_skills, "shared.skills")
    for mode, value in mode_values.items():
        for key in MODE_LISTS:
            account(value[key], f"modes.{mode}.{key}")
    for pack, value in pack_values.items():
        account(value["skills"], f"packs.{pack}.skills")

    missing = sorted(name for name, sources in occurrences.items() if not sources)
    duplicate = sorted(name for name, sources in occurrences.items() if len(sources) > 1)
    if missing:
        raise ContextManifestError(f"unaccounted top-level skills: {', '.join(missing)}")
    if duplicate:
        raise ContextManifestError(f"skills must be accounted for exactly once: {', '.join(duplicate)}")
    if shared_skills:
        raise ContextManifestError("shared.skills must be empty while naive mode is isolated")
    return document


def load_and_validate(manifest: Path, skills_root: Path) -> dict[str, Any]:
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContextManifestError(f"cannot read manifest {manifest}: {error}") from error
    return validate_manifest(document, skills_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("--skills-root", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[4]
    manifest = args.manifest or root / ".claude/skills/default-mode/references/context/manifest.json"
    skills_root = args.skills_root or root / ".claude/skills"
    try:
        load_and_validate(manifest, skills_root)
    except ContextManifestError as error:
        print(f"context manifest validation failed: {error}", file=sys.stderr)
        return 1
    print(f"context manifest validation passed: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
