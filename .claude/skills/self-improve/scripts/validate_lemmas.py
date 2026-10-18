#!/usr/bin/env python3
"""Validate opt-in per-skill lemma and learned-reference declarations.

This checks the contract's strict schema-specific frontmatter subset and declared evidence
proportionality, including the transfer boundary between project observations and cross-task
carriers. It does not prove causality, true task independence, or complete de-identification.
Exit 0 is structurally clean; exit 2 reports violations.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


STATUSES = {"conjecture", "tested", "supported", "refuted", "superseded"}
EVIDENCE_KINDS = {"none", "deterministic", "stochastic"}
PROBE_TYPES = {"none", "discriminating", "counterfactual", "statistical"}
REPORT_OUTCOMES = {"NONE", "REPAIR", *STATUSES}
REPORT_OPTIONAL_STRING_FIELDS = (
    "skill",
    "path",
    "hypothesis",
    "evidence",
    "prediction",
    "intervention",
    "observation",
    "mechanism",
    "boundaries",
    "reason",
    "nextProbe",
    "directDefect",
    "failingProbe",
    "passingProbe",
)
REQUIRED_SECTIONS = (
    "Observation",
    "Problem",
    "Hypothesis",
    "Prediction",
    "Intervention",
    "Result",
    "Mechanism",
    "Boundaries and Falsifier",
    "Synthesis",
    "Next Probe",
)
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
INDEX_LINK = re.compile(r"\]\(([^)#]+\.md)(?:#[^)]+)?\)")
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|PLACEHOLDER)\b|<[^>]+>", re.IGNORECASE)
LEGACY_PROJECT_MEMORY_REFERENCE_NAMES = {
    "lessons.md",
    "runtime-lessons.md",
    "standard-round-lessons.md",
}
PROJECT_MEMORY_DATED_ENTRY = re.compile(
    r"^\s*-\s+\[\d{4}-\d{2}-\d{2}\](?:\s|$)", re.MULTILINE
)


@dataclass
class Lemma:
    path: Path
    skill: str
    data: dict[str, Any]
    sections: dict[str, str]


@dataclass
class LearnedReference:
    path: Path
    skill: str
    data: dict[str, Any]


def _scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value[0] in "['\"{" or value.startswith("["):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"invalid quoted/inline value {value!r}: {exc}") from exc
    return value


def _strict_frontmatter_subset(text: str) -> dict[str, Any]:
    """Parse the mappings/lists/scalars used by the lemma contract, nothing more."""

    tokens: list[tuple[int, str, int]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ValueError(f"line {line_number}: tabs are not supported")
        indent = len(raw) - len(raw.lstrip(" "))
        tokens.append((indent, raw.strip(), line_number))

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(tokens) or tokens[index][0] != indent:
            raise ValueError("invalid indentation")
        is_list = tokens[index][1].startswith("- ")
        container: Any = [] if is_list else {}
        while index < len(tokens):
            current_indent, content, line_number = tokens[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise ValueError(f"line {line_number}: unexpected indentation")
            if is_list:
                if not content.startswith("- "):
                    raise ValueError(f"line {line_number}: cannot mix list and mapping entries")
                item = content[2:].strip()
                if not item:
                    raise ValueError(f"line {line_number}: nested list items are not supported")
                container.append(_scalar(item))
                index += 1
                continue
            if content.startswith("- ") or ":" not in content:
                raise ValueError(f"line {line_number}: expected `key: value`")
            key, raw_value = content.split(":", 1)
            key = key.strip()
            if not key:
                raise ValueError(f"line {line_number}: empty key")
            if key in container:
                raise ValueError(f"line {line_number}: duplicate key {key!r}")
            index += 1
            if raw_value.strip():
                container[key] = _scalar(raw_value)
            elif index < len(tokens) and tokens[index][0] > indent:
                child_indent = tokens[index][0]
                container[key], index = parse_block(index, child_indent)
            else:
                container[key] = {}
        return container, index

    if not tokens:
        return {}
    if tokens[0][0] != 0:
        raise ValueError("frontmatter must start at indentation zero")
    result, consumed = parse_block(0, 0)
    if consumed != len(tokens) or not isinstance(result, dict):
        raise ValueError("frontmatter must be a mapping")
    return result


def _load_frontmatter(text: str) -> dict[str, Any]:
    return _strict_frontmatter_subset(text)


def _strings(value: Any, label: str, rel: str, problems: list[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        problems.append(f"{rel}: {label} must be a list of non-empty strings")
        return []
    return [item.strip() for item in value]


def _parse_sections(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip()] = body[match.end() : end].strip()
    return sections


def _nonempty_string(
    value: Any, label: str, rel: str, problems: list[str]
) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{rel}: {label} must be a non-empty string")
        return ""
    stripped = value.strip()
    if PLACEHOLDER.search(stripped):
        problems.append(f"{rel}: {label} must be concrete, not a placeholder")
    return stripped


def _validate_transfer(data: Any, rel: str, problems: list[str]) -> dict[str, Any]:
    """Validate declarations required before project evidence enters a cross-task carrier."""

    if not isinstance(data, dict):
        problems.append(f"{rel}: transfer must be a mapping")
        return {}

    contexts = _strings(data.get("task_contexts"), "transfer.task_contexts", rel, problems)
    if len(set(contexts)) < 2:
        problems.append(f"{rel}: cross-task carriers require at least two distinct transfer.task_contexts")
    elif any(PLACEHOLDER.search(item) for item in contexts):
        problems.append(f"{rel}: transfer.task_contexts must be concrete, not placeholders")

    artifacts = _strings(data.get("artifacts"), "transfer.artifacts", rel, problems)
    if len(set(artifacts)) < 2:
        problems.append(
            f"{rel}: cross-task carriers require at least two distinct transfer.artifacts pointers"
        )
    elif any(PLACEHOLDER.search(item) for item in artifacts):
        problems.append(f"{rel}: transfer.artifacts must be concrete, not placeholders")

    for field in ("independence", "applicability", "counterexamples", "falsifier"):
        _nonempty_string(data.get(field), f"transfer.{field}", rel, problems)

    if data.get("project_identifiers_removed") is not True:
        problems.append(f"{rel}: transfer.project_identifiers_removed must be true")
    return data


def _parse_lemma(path: Path, skill: str, root: Path, problems: list[str]) -> Lemma | None:
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        problems.append(f"{rel}: unreadable: {exc}")
        return None
    match = FRONTMATTER.match(text)
    if not match:
        problems.append(f"{rel}: missing `---` lemma frontmatter")
        return None
    try:
        data = _load_frontmatter(match.group(1))
    except ValueError as exc:
        problems.append(f"{rel}: invalid lemma frontmatter: {exc}")
        return None
    sections = _parse_sections(text[match.end() :])
    for heading in REQUIRED_SECTIONS:
        content = sections.get(heading, "").strip()
        if not content:
            problems.append(f"{rel}: missing or empty `## {heading}` section")
        elif content.upper() in {"TBD", "TODO"}:
            problems.append(f"{rel}: `## {heading}` cannot be a placeholder")
    return Lemma(path=path, skill=skill, data=data, sections=sections)


def _validate_lemma(lemma: Lemma, root: Path, problems: list[str]) -> None:
    rel = lemma.path.relative_to(root).as_posix()
    data = lemma.data
    lemma_id = data.get("id")
    if not isinstance(lemma_id, str) or not SLUG.fullmatch(lemma_id):
        problems.append(f"{rel}: id must be a lowercase slug")

    status = data.get("status")
    if status not in STATUSES:
        problems.append(f"{rel}: status must be one of {sorted(STATUSES)}, got {status!r}")

    declared_skill = data.get("skill")
    if declared_skill != lemma.skill:
        problems.append(f"{rel}: skill must match owning directory {lemma.skill!r}")
    if not isinstance(data.get("scope"), str) or not data["scope"].strip():
        problems.append(f"{rel}: scope must be a non-empty string")

    _validate_transfer(data.get("transfer"), rel, problems)

    evidence = data.get("evidence")
    if not isinstance(evidence, dict):
        problems.append(f"{rel}: evidence must be a mapping")
        evidence = {}
    kind = evidence.get("kind")
    if kind not in EVIDENCE_KINDS:
        problems.append(f"{rel}: evidence.kind must be one of {sorted(EVIDENCE_KINDS)}")
    artifacts = _strings(evidence.get("artifacts"), "evidence.artifacts", rel, problems)
    runs = _strings(evidence.get("runs"), "evidence.runs", rel, problems)
    probe_type = evidence.get("probe_type")
    if probe_type not in PROBE_TYPES:
        problems.append(f"{rel}: evidence.probe_type must be one of {sorted(PROBE_TYPES)}")
    probe = evidence.get("probe")
    if not isinstance(probe, str):
        problems.append(f"{rel}: evidence.probe must be a string")
        probe = ""
    independence = evidence.get("independence")
    if not isinstance(independence, str):
        problems.append(f"{rel}: evidence.independence must be a string")
        independence = ""

    derived = _strings(data.get("derived_changes"), "derived_changes", rel, problems)
    if status in {"conjecture", "tested"} and derived:
        problems.append(f"{rel}: {status} lemmas cannot link durable derived_changes")

    superseded_by = data.get("superseded_by")
    if not isinstance(superseded_by, str):
        problems.append(f"{rel}: superseded_by must be a string")
        superseded_by = ""
    if status == "superseded" and not superseded_by.strip():
        problems.append(f"{rel}: superseded lemmas require superseded_by")
    if status != "superseded" and superseded_by.strip():
        problems.append(f"{rel}: only superseded lemmas may set superseded_by")

    if status == "conjecture":
        if kind != "none" or probe_type != "none":
            problems.append(f"{rel}: conjecture must declare evidence.kind/probe_type as none")
    if status == "tested":
        if kind not in {"deterministic", "stochastic"} or not artifacts:
            problems.append(f"{rel}: tested lemmas require an experiment kind and raw artifact")
        if not probe.strip():
            problems.append(f"{rel}: tested lemmas require the probe that was run")
        if kind == "deterministic" and probe_type not in {"discriminating", "counterfactual"}:
            problems.append(f"{rel}: deterministic tested evidence requires a discriminating/counterfactual probe")
        if kind == "stochastic" and probe_type != "statistical":
            problems.append(f"{rel}: stochastic tested evidence requires probe_type statistical")

    if status in {"supported", "refuted"}:
        if kind not in {"deterministic", "stochastic"}:
            problems.append(f"{rel}: {status} lemmas require deterministic or stochastic evidence")
        if not artifacts:
            problems.append(f"{rel}: {status} lemmas require at least one raw artifact pointer")
        if not probe.strip():
            problems.append(f"{rel}: {status} lemmas require an explicit discriminating probe")
        if kind == "deterministic" and probe_type not in {"discriminating", "counterfactual"}:
            problems.append(
                f"{rel}: deterministic {status} requires probe_type discriminating or counterfactual"
            )
        if kind == "stochastic":
            if len(set(runs)) < 2:
                problems.append(
                    f"{rel}: stochastic {status} requires at least two distinct evidence.runs entries"
                )
            if probe_type != "statistical":
                problems.append(f"{rel}: stochastic {status} requires probe_type statistical")
            if not independence.strip():
                problems.append(f"{rel}: stochastic {status} must state repetition independence/noise handling")


def _parse_learned_reference(
    path: Path, skill: str, root: Path, problems: list[str]
) -> LearnedReference | None:
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        problems.append(f"{rel}: unreadable: {exc}")
        return None
    match = FRONTMATTER.match(text)
    if not match:
        problems.append(f"{rel}: learned reference requires `---` cross-task frontmatter")
        return None
    try:
        data = _load_frontmatter(match.group(1))
    except ValueError as exc:
        problems.append(f"{rel}: invalid learned-reference frontmatter: {exc}")
        return None
    if not text[match.end() :].strip():
        problems.append(f"{rel}: learned reference body must be non-empty")
    return LearnedReference(path=path, skill=skill, data=data)


def _self_evolve_reference_markers(path: Path) -> tuple[bool, bool, str | None]:
    """Return self-evolve and dated-memory markers, or a read error."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return False, False, str(exc)
    match = FRONTMATTER.match(text)
    if not match:
        marked = False
    else:
        header = match.group(1)
        marked = bool(
            re.search(r"^source:\s*['\"]?self-evolve['\"]?\s*$", header, re.MULTILINE)
            or re.search(r"^kind:\s*['\"]?cross-task-reference['\"]?\s*$", header, re.MULTILINE)
        )
    return marked, bool(PROJECT_MEMORY_DATED_ENTRY.search(text)), None


def _validate_learned_reference(
    reference: LearnedReference,
    root: Path,
    lemmas_by_id: dict[str, Lemma],
    problems: list[str],
) -> None:
    rel = reference.path.relative_to(root).as_posix()
    data = reference.data
    if data.get("kind") != "cross-task-reference":
        problems.append(f"{rel}: kind must be 'cross-task-reference'")
    if data.get("source") != "self-evolve":
        problems.append(f"{rel}: source must be 'self-evolve'")
    if data.get("status") != "supported":
        problems.append(f"{rel}: status must be 'supported'; tentative learning stays in .memory")
    if data.get("skill") != reference.skill:
        problems.append(f"{rel}: skill must match owning directory {reference.skill!r}")
    _validate_transfer(data.get("transfer"), rel, problems)

    derived_from = _strings(data.get("derived_from"), "derived_from", rel, problems)
    if not derived_from:
        problems.append(f"{rel}: learned reference requires at least one derived_from lemma id")
    for lemma_id in set(derived_from):
        lemma = lemmas_by_id.get(lemma_id)
        if lemma is None or lemma.skill != reference.skill or lemma.data.get("status") != "supported":
            problems.append(
                f"{rel}: derived_from {lemma_id!r} must resolve to a supported lemma in the same skill"
            )
            continue
        derived_changes = lemma.data.get("derived_changes")
        if not isinstance(derived_changes, list) or rel not in derived_changes:
            problems.append(
                f"{rel}: source lemma {lemma_id!r} must link this carrier in derived_changes"
            )


def inspect_tree(root: Path) -> tuple[list[str], list[Lemma]]:
    root = root.resolve()
    skills_root = root / ".claude" / "skills"
    problems: list[str] = []
    lemmas: list[Lemma] = []
    learned_references: list[LearnedReference] = []

    if not skills_root.is_dir():
        return problems, lemmas

    lemma_dirs = sorted(path for path in skills_root.glob("*/lemmas") if path.is_dir())
    for lemma_dir in lemma_dirs:
        skill = lemma_dir.parent.name
        index_path = lemma_dir / "INDEX.md"
        if not index_path.is_file():
            problems.append(f"{index_path.relative_to(root).as_posix()}: required for an adopted lemma directory")
            indexed: set[str] = set()
        else:
            try:
                index_text = index_path.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(f"{index_path.relative_to(root).as_posix()}: unreadable: {exc}")
                index_text = ""
            indexed = {Path(target).name for target in INDEX_LINK.findall(index_text)}

        lemma_files = sorted(path for path in lemma_dir.glob("*.md") if path.name != "INDEX.md")
        actual = {path.name for path in lemma_files}
        for missing in sorted(actual - indexed):
            problems.append(
                f"{index_path.relative_to(root).as_posix()}: missing link to {missing}"
            )
        for stale in sorted(indexed - actual):
            problems.append(
                f"{index_path.relative_to(root).as_posix()}: links missing lemma file {stale}"
            )
        for path in lemma_files:
            parsed = _parse_lemma(path, skill, root, problems)
            if parsed is not None:
                lemmas.append(parsed)

    reference_paths = sorted(skills_root.glob("*/references/**/*.md"))
    for reference_path in reference_paths:
        marked, has_dated_memory_entry, read_error = _self_evolve_reference_markers(reference_path)
        rel = reference_path.relative_to(root).as_posix()
        if read_error is not None:
            problems.append(f"{rel}: unreadable: {read_error}")
            continue
        if reference_path.name in LEGACY_PROJECT_MEMORY_REFERENCE_NAMES:
            problems.append(
                f"{rel}: legacy project-memory carrier name is forbidden; keep run lessons in .memory "
                "or use a reviewed learned-<slug>.md cross-task carrier"
            )
        if marked and not reference_path.name.startswith("learned-"):
            problems.append(
                f"{rel}: self-evolve cross-task references must use the learned-<slug>.md name"
            )
        if (marked or reference_path.name.startswith("learned-")) and has_dated_memory_entry:
            problems.append(
                f"{rel}: self-evolve learned references cannot contain project-memory dated entry syntax"
            )
        if not reference_path.name.startswith("learned-"):
            continue
        skill = reference_path.relative_to(skills_root).parts[0]
        parsed = _parse_learned_reference(reference_path, skill, root, problems)
        if parsed is not None:
            learned_references.append(parsed)

    seen_ids: dict[str, Lemma] = {}
    for lemma in lemmas:
        _validate_lemma(lemma, root, problems)
        lemma_id = lemma.data.get("id")
        if isinstance(lemma_id, str) and lemma_id:
            previous = seen_ids.get(lemma_id)
            if previous is not None:
                problems.append(
                    f"{lemma.path.relative_to(root).as_posix()}: duplicate id {lemma_id!r}; first declared in {previous.path.relative_to(root).as_posix()}"
                )
            else:
                seen_ids[lemma_id] = lemma

    for reference in learned_references:
        _validate_learned_reference(reference, root, seen_ids, problems)

    for lemma in lemmas:
        if lemma.data.get("status") != "superseded":
            continue
        replacement = lemma.data.get("superseded_by")
        if not isinstance(replacement, str) or not replacement:
            continue
        replacement_lemma = seen_ids.get(replacement)
        if replacement_lemma is None:
            problems.append(
                f"{lemma.path.relative_to(root).as_posix()}: superseded_by {replacement!r} does not resolve to a lemma id"
            )
        elif replacement_lemma.data.get("status") != "supported":
            problems.append(
                f"{lemma.path.relative_to(root).as_posix()}: superseded_by {replacement!r} must resolve to a supported lemma"
            )
    return problems, lemmas


def validate_tree(root: Path) -> list[str]:
    return inspect_tree(root)[0]


def _lemma_metadata(lemma: Lemma, root: Path) -> dict[str, Any]:
    derived = lemma.data.get("derived_changes")
    return {
        "path": lemma.path.relative_to(root).as_posix(),
        "id": lemma.data.get("id") if isinstance(lemma.data.get("id"), str) else None,
        "status": lemma.data.get("status") if isinstance(lemma.data.get("status"), str) else None,
        "skill": lemma.skill,
        "superseded_by": (
            lemma.data.get("superseded_by") if isinstance(lemma.data.get("superseded_by"), str) else None
        ),
        "derived_changes": derived if isinstance(derived, list) else [],
    }


def validate_report(
    raw: str, root: Path, lemmas: list[Lemma]
) -> tuple[list[str], dict[str, Any] | None, dict[str, Any] | None]:
    root = root.resolve()
    problems: list[str] = []
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"lemmaReport is not valid JSON: {exc}"], None, None
    if not isinstance(report, dict):
        return ["lemmaReport must decode to an object"], None, None

    if not isinstance(report.get("outcome"), str):
        problems.append("lemmaReport.outcome must be a string")
    for key in REPORT_OPTIONAL_STRING_FIELDS:
        if key in report and not isinstance(report.get(key), str):
            problems.append(f"lemmaReport.{key} must be a string")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, list) or any(
        not isinstance(item, str) or not item.strip() for item in artifacts
    ):
        problems.append("lemmaReport.artifacts must be a list of non-empty strings")
        artifacts = []
    behavior_changed = report.get("behaviorChanged")
    if not isinstance(behavior_changed, bool):
        problems.append("lemmaReport.behaviorChanged must be boolean")

    outcome = report.get("outcome")
    if outcome not in REPORT_OUTCOMES:
        problems.append("lemmaReport.outcome is invalid")

    def nonempty(key: str) -> None:
        value = report.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"lemmaReport.{key} must be a non-empty string")

    nonempty("reason")
    if outcome in {"NONE", "conjecture", "tested"} and behavior_changed is not False:
        problems.append(f"{outcome} cannot set behaviorChanged true")

    checked: dict[str, Any] | None = None
    if outcome == "REPAIR":
        for key in ("path", "directDefect", "failingProbe", "passingProbe"):
            nonempty(key)
        path = report.get("path")
        if isinstance(path, str) and (Path(path).is_absolute() or ".." in Path(path).parts):
            problems.append("REPAIR path must be project-relative and cannot traverse upward")
        if len(set(artifacts)) < 2:
            problems.append("REPAIR requires at least two distinct failing/passing artifact strings")

    if outcome in STATUSES:
        for key in (
            "skill",
            "path",
            "hypothesis",
            "evidence",
            "prediction",
            "intervention",
            "observation",
            "mechanism",
            "boundaries",
        ):
            nonempty(key)
        if not artifacts:
            problems.append(f"{outcome} requires at least one raw artifact pointer")
        if outcome == "conjecture":
            nonempty("nextProbe")

        report_path = report.get("path")
        skill = report.get("skill")
        expected_prefix = f".claude/skills/{skill}/lemmas/" if isinstance(skill, str) else ""
        if (
            not isinstance(skill, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", skill)
            or not isinstance(report_path, str)
            or Path(report_path).is_absolute()
            or ".." in Path(report_path).parts
            or not report_path.startswith(expected_prefix)
            or not report_path.endswith(".md")
            or report_path.endswith("/INDEX.md")
        ):
            problems.append(
                "lemma outcome path must name one lemma file under the reported kebab-case skill's lemmas directory"
            )
        else:
            by_path = {_lemma_metadata(lemma, root)["path"]: lemma for lemma in lemmas}
            lemma = by_path.get(report_path)
            if lemma is None:
                problems.append(f"reported lemma file does not exist or could not be parsed: {report_path}")
            else:
                checked = _lemma_metadata(lemma, root)
                if checked["status"] != outcome:
                    problems.append(
                        f"reported outcome {outcome!r} does not match on-disk lemma status {checked['status']!r}"
                    )
                if checked["skill"] != skill:
                    problems.append("reported skill does not match the on-disk lemma owner")

                if outcome == "refuted" and behavior_changed is True and not checked["derived_changes"]:
                    problems.append(
                        "refuted behaviorChanged true requires prior linked behavior in derived_changes"
                    )
                if outcome == "superseded" and behavior_changed is True:
                    if not checked["derived_changes"]:
                        problems.append(
                            "superseded behaviorChanged true requires prior linked behavior in derived_changes"
                        )
                    replacement_id = checked.get("superseded_by")
                    by_id = {
                        metadata["id"]: metadata
                        for metadata in (_lemma_metadata(item, root) for item in lemmas)
                        if metadata["id"]
                    }
                    replacement = by_id.get(replacement_id)
                    if replacement is None or replacement.get("status") != "supported":
                        problems.append(
                            "superseded behaviorChanged true requires an existing supported replacement lemma"
                        )
    return problems, report, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default: cwd)")
    parser.add_argument("--json", action="store_true", help="emit machine-readable validation metadata")
    parser.add_argument("--report-json", help="also validate a standard-round lemmaReport JSON string")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    problems, lemmas = inspect_tree(root)
    report = None
    checked = None
    if args.report_json is not None:
        report_problems, report, checked = validate_report(args.report_json, root, lemmas)
        problems.extend(report_problems)
    if args.json:
        payload = {
            "ok": not problems,
            "problems": problems,
            "lemmas": [_lemma_metadata(lemma, root) for lemma in lemmas],
            "report": {
                "outcome": report.get("outcome") if isinstance(report, dict) else None,
                "path": report.get("path") if isinstance(report, dict) else None,
                "checkedLemma": checked,
            },
        }
        print(json.dumps(payload, sort_keys=True))
        return 0 if not problems else 2
    if problems:
        print("LEMMA VALIDATE: FAIL")
        for problem in problems:
            print(f"  - {problem}")
        return 2
    print("LEMMA VALIDATE: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
