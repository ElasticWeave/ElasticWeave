#!/usr/bin/env python3
"""Validate that the repository is a low-intrusion skill bundle."""

from __future__ import annotations

import hashlib
from pathlib import Path
import os
import re
import stat
import sys


ALLOWED_ROOTS = {
    ".agents",
    ".claude",
    ".codex",
    ".git",
    ".gitignore",
    ".humanize",
    ".kimi-code",
    "LICENSE",
    "VERSION",
}
FORBIDDEN_RUNTIME_ROOTS = {
    ".memory",
    ".omp",
    ".pm",
    ".subagent",
    ".workflow",
    "config",
    "tools",
}
FORBIDDEN_ROOT_FILES = {
    "agent.md",
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "setup_agent.sh",
}
OLD_PATHS = re.compile(
    r"tools/(?:config-sync|memory|native-workflow|subagent-workflow)|"
    r"(?:^|[\s`'\"])(?:agent\.md|setup_agent\.sh)(?:$|[\s`'\"),.:])"
)
OLD_PATH_FRAGMENT = re.compile(
    r"[\"']tools[\"']\s*,\s*[\"']"
    r"(?:config-sync|memory|native-workflow|subagent-workflow)[\"']"
)
TEXT_SUFFIXES = {
    ".c",
    ".h",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".rules",
    ".sh",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}
PYTHON_BYTECODE_SUFFIXES = frozenset({".pyc", ".pyo"})
REMOVED_HARNESS_MARKERS = re.compile(
    r"omh-workflow|flowjanus-workflow|\.omhflow\b|\.flowjanus(?:\b|/)", re.I
)
REMOVED_HARNESS_SKILL_DIRS = ("omh-workflow", "flowjanus-workflow")
RETIRED_STANDALONE_SKILL_DIRS = (
    "ask-user",
    "linear-sync",
    "notion-memory",
    "publish-management-artifacts",
    "resolve-pr",
)
RETIRED_STARTUP_SKILL = "in" + "it"
RETIRED_STARTUP_PATH = re.compile(
    rf"(?:\.claude/skills/|skills/){RETIRED_STARTUP_SKILL}(?:/|\b)|"
    rf"(?:^|[\s`'\"]){RETIRED_STARTUP_SKILL}/(?:assets|references|scripts)(?:/|\b)|"
    rf"/{RETIRED_STARTUP_SKILL}\b|"
    rf"/\s*[\"']{RETIRED_STARTUP_SKILL}[\"']\s*/\s*[\"'](?:assets|references|scripts)[\"']"
)
HUMANIZE_SLOT_LINK = Path("flows/slot.py")
HUMANIZE_SLOT_LINK_PATTERN = re.compile(r"^versions/slot-[0-9a-f]{64}\.py$")


def has_files(path: Path) -> bool:
    return path.is_file() or (path.is_dir() and any(item.is_file() for item in path.rglob("*")))


def has_python_cache_component(path: Path) -> bool:
    return any(part.casefold() == "__pycache__" for part in path.parts)


def iter_package_files(root: Path):
    skills = root / ".claude" / "skills"
    for current, directories, files in os.walk(skills, followlinks=False):
        directories[:] = [
            name for name in directories if name.casefold() != "__pycache__"
        ]
        base = Path(current)
        for name in files:
            path = base / name
            if not path.is_symlink():
                yield path


def has_removed_harness_reference(path: Path, text: str) -> bool:
    """Reject retired harness artifacts outside the opaque evidence archive and this rule."""
    if path.resolve() == Path(__file__).resolve():
        return False
    if path.name == "test_validate_bundle.py" and path.parent.resolve() == Path(__file__).resolve().parent:
        return False
    if path.name == "test_fresh_project_contracts.py" and path.parent.resolve() == Path(__file__).resolve().parent:
        return False
    if "tests/evidence/historical-workflow-evidence" in path.as_posix():
        return False
    return REMOVED_HARNESS_MARKERS.search(text) is not None


def validate_skill(path: Path, errors: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if match is None:
        errors.append(f"{path}: missing YAML frontmatter")
        return
    frontmatter = match.group(1)
    if not re.search(r"^name:\s*\S+", frontmatter, re.M):
        errors.append(f"{path}: missing name")
    if not re.search(r"^description:\s*.+", frontmatter, re.M):
        errors.append(f"{path}: missing description")


def validate_root_humanize(root: Path, skills: Path, errors: list[str]) -> None:
    """Require the explicitly shipped root Humanize tree to equal its canonical owner."""
    canonical = skills / "write-humanize-flow/assets/project/.humanize"
    materialized = root / ".humanize"
    files: dict[str, dict[Path, Path]] = {}
    links: dict[str, dict[Path, str]] = {}
    for base, label in ((canonical, "canonical"), (materialized, "root")):
        if not base.is_dir() or base.is_symlink():
            errors.append(f"{label} .humanize must be a real directory")
            return
        regular: dict[Path, Path] = {}
        owned_links: dict[Path, str] = {}
        for path in base.rglob("*"):
            details = path.stat(follow_symlinks=False)
            if stat.S_ISLNK(details.st_mode):
                relative = path.relative_to(base)
                if relative != HUMANIZE_SLOT_LINK:
                    errors.append(f"{label} .humanize contains a link: {relative}")
                    continue
                target = os.readlink(path)
                if HUMANIZE_SLOT_LINK_PATTERN.fullmatch(target) is None:
                    errors.append(f"{label} slot link target is unsafe: {target}")
                    continue
                versions_directory = path.parent / "versions"
                try:
                    versions_status = versions_directory.stat(follow_symlinks=False)
                except OSError as error:
                    errors.append(f"{label} slot link parent is unsafe: {error}")
                    continue
                if not stat.S_ISDIR(versions_status.st_mode):
                    errors.append(f"{label} slot link parent is not a real versions directory")
                    continue
                version = path.parent / target
                try:
                    version_status = version.stat(follow_symlinks=False)
                except OSError as error:
                    errors.append(f"{label} slot link target is unsafe: {error}")
                    continue
                if (
                    not stat.S_ISREG(version_status.st_mode)
                    or version_status.st_nlink != 1
                ):
                    errors.append(
                        f"{label} slot link target must be a single-link regular file: {relative}"
                    )
                    continue
                if version_status.st_uid != os.geteuid():
                    errors.append(
                        f"{label} slot link target must be owned by the effective user"
                    )
                    continue
                expected_digest = target.removeprefix("versions/slot-").removesuffix(".py")
                if hashlib.sha256(version.read_bytes()).hexdigest() != expected_digest:
                    errors.append(f"{label} slot link target digest does not match its filename")
                    continue
                owned_links[relative] = target
            elif stat.S_ISDIR(details.st_mode):
                continue
            elif not stat.S_ISREG(details.st_mode):
                errors.append(
                    f"{label} .humanize contains a non-regular node: "
                    f"{path.relative_to(base)}"
                )
            else:
                if details.st_nlink != 1:
                    errors.append(
                        f"{label} .humanize file must be single-link: "
                        f"{path.relative_to(base)}"
                    )
                relative = path.relative_to(base)
                if len(relative.parts) == 3 and relative.parts[:2] == ("flows", "versions"):
                    version_match = re.fullmatch(
                        r"slot-([0-9a-f]{64})\.py", relative.name
                    )
                    if version_match is None:
                        errors.append(f"{label} contains an invalid slot version name: {relative}")
                    elif details.st_uid != os.geteuid():
                        errors.append(
                            f"{label} slot version must be owned by the effective user: {relative}"
                        )
                    else:
                        try:
                            if hashlib.sha256(path.read_bytes()).hexdigest() != version_match.group(1):
                                errors.append(f"{label} slot version digest does not match: {relative}")
                        except OSError as error:
                            errors.append(f"{label} slot version cannot be read: {error}")
                regular[relative] = path
        files[label] = regular
        links[label] = owned_links
    expected = files["canonical"]
    actual = files["root"]
    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)
    invalid_extra = {
        path
        for path in extra
        if not (
            len(path.parts) == 3
            and path.parts[:2] == ("flows", "versions")
            and re.fullmatch(r"slot-[0-9a-f]{64}\.py", path.name)
        )
    }
    if missing or invalid_extra:
        errors.append(
            "root .humanize file set differs from canonical owner "
            f"(missing={sorted(path.as_posix() for path in missing)}, "
            f"extra={sorted(path.as_posix() for path in invalid_extra)})"
        )
        return
    if set(links["root"]) != set(links["canonical"]):
        errors.append(
            "root .humanize slot link set differs from canonical owner "
            f"(canonical={sorted(links['canonical'])}, root={sorted(links['root'])})"
        )
    for relative, source in expected.items():
        target = actual[relative]
        if source.read_bytes() != target.read_bytes():
            errors.append(f"root .humanize bytes differ: {relative.as_posix()}")
        source_mode = source.stat().st_mode & 0o777
        target_mode = target.stat().st_mode & 0o777
        if (
            len(relative.parts) == 3
            and relative.parts[:2] == ("flows", "versions")
            and re.fullmatch(r"slot-[0-9a-f]{64}\.py", relative.name)
        ):
            # Git stores these as 100644 and a collaborative umask may restore
            # either mirror as 0664.  Runtime admission hashes first, then clears
            # 0o022 through a no-follow descriptor before executing the slot.
            source_mode &= ~0o022
            target_mode &= ~0o022
        if source_mode != target_mode:
            errors.append(f"root .humanize mode differs: {relative.as_posix()}")


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    for entry in root.iterdir():
        if entry.name not in ALLOWED_ROOTS and has_files(entry):
            errors.append(f"unexpected distributable root: {entry.name}")
    for name in FORBIDDEN_ROOT_FILES:
        if (root / name).exists() or (root / name).is_symlink():
            errors.append(f"forbidden root file: {name}")
    for name in FORBIDDEN_RUNTIME_ROOTS:
        if has_files(root / name):
            errors.append(f"project runtime state shipped at root: {name}")

    skills = root / ".claude" / "skills"
    if not skills.is_dir():
        errors.append("missing canonical .claude/skills directory")
        return errors
    validate_root_humanize(root, skills, errors)
    for platform in (".agents", ".codex", ".kimi-code"):
        link = root / platform / "skills"
        if not link.is_symlink() or os.readlink(link) != "../.claude/skills":
            errors.append(f"{platform}/skills must be ../.claude/skills symlink")

    skill_files = sorted(skills.glob("*/SKILL.md"))
    if not skill_files:
        errors.append("no skills found")
    for skill_file in skill_files:
        validate_skill(skill_file, errors)
    for name in REMOVED_HARNESS_SKILL_DIRS:
        path = skills / name
        if path.exists() or path.is_symlink():
            errors.append(f"removed harness skill still shipped: {path.relative_to(root)}")
    for name in RETIRED_STANDALONE_SKILL_DIRS:
        path = skills / name
        if path.exists() or path.is_symlink():
            errors.append(f"retired standalone skill still shipped: {path.relative_to(root)}")
    retired_startup = skills / RETIRED_STARTUP_SKILL
    if retired_startup.exists() or retired_startup.is_symlink():
        errors.append(
            f"retired startup skill still shipped: {retired_startup.relative_to(root)}"
        )
    for path in iter_package_files(root):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if path.resolve() != Path(__file__).resolve() and OLD_PATHS.search(text):
            errors.append(f"stale root-manual/tool reference: {path.relative_to(root)}")
        if path.resolve() != Path(__file__).resolve() and OLD_PATH_FRAGMENT.search(text):
            errors.append(f"stale fragmented root tool path: {path.relative_to(root)}")
        if path.resolve() != Path(__file__).resolve() and RETIRED_STARTUP_PATH.search(text):
            errors.append(f"retired startup path reference: {path.relative_to(root)}")
        host_markers = ("/Users/" + "muheng/", "/home/" + "muheng/")
        is_historical_evidence = "tests/evidence/historical-workflow-evidence" in path.as_posix()
        if has_removed_harness_reference(path, text):
            errors.append(f"removed harness reference: {path.relative_to(root)}")
        if not is_historical_evidence and any(marker in text for marker in host_markers):
            errors.append(f"host-specific absolute path: {path.relative_to(root)}")

    required = (
        skills / "default-mode" / "scripts" / "materialize.py",
        skills / "default-mode" / "references" / "project-session-contract.md",
        skills / "default-mode" / "assets" / "project" / "config" / "workflows" / "models.json",
        skills / "memory" / "assets" / "project" / ".memory" / "MAIN.md",
        skills / "subagent-workflow" / "scripts" / "controller" / "run.ts",
        skills / "subagent-workflow" / "scripts" / "native" / "native-map.json",
        skills / "update-platform-config" / "scripts" / "config-sync" / "sync_all.py",
        skills / "write-subagent" / "assets" / "project" / ".omp" / "agents",
        skills
        / "write-humanize-flow"
        / "assets/project/.humanize/flows/default.py",
        skills
        / "write-humanize-flow"
        / "assets/project/.humanize/flows/slot.py",
        skills
        / "write-humanize-flow"
        / "assets/project/.humanize/flows/versions/slot-e3bcbc9a3bad71d157aab519da780403e48729da13b80b9be5deafcc9f88aacf.py",
        skills
        / "write-humanize-flow"
        / "assets/project/.humanize/flows/scheduler/__init__.py",
        skills
        / "write-humanize-flow"
        / "assets/project/.humanize/run_scheduler.py",
        skills
        / "write-humanize-flow"
        / "assets/project/.humanize/README.md",
        skills
        / "write-humanize-flow"
        / "assets/project/.humanize/scheduler/.gitignore",
        skills
        / "write-humanize-flow"
        / "assets/project/.humanize/scheduler/README.md",
        skills
        / "deep-research-workflow"
        / "assets/project/.humanize/flows/_research_reference.py",
        skills
        / "deep-research-workflow"
        / "assets/project/.humanize/flows/deep_research.py",
        skills
        / "deep-research-workflow"
        / "assets/project/.humanize/flows/auto_research.py",
    )
    for path in required:
        if not path.exists():
            errors.append(f"missing owned package artifact: {path.relative_to(root)}")
    for skill_root in sorted(skills.iterdir()):
        assets = skill_root / "assets"
        for cache in sorted(assets.rglob("*")) if assets.is_dir() else ():
            if (
                has_python_cache_component(cache)
                or cache.suffix.lower() in PYTHON_BYTECODE_SUFFIXES
            ):
                errors.append(
                    f"runtime bytecode shipped in skill assets: {cache.relative_to(root)}"
                )
    for skill_root in sorted(skills.iterdir()):
        legacy_accept = skill_root / "assets" / "project" / ".pm" / "accept.sh"
        if legacy_accept.exists() or legacy_accept.is_symlink():
            errors.append(
                f"legacy global acceptance script shipped by project assets: {legacy_accept.relative_to(root)}"
            )
    contract = skills / "default-mode" / "references" / "project-session-contract.md"
    if contract.is_file():
        text = contract.read_text(encoding="utf-8")
        start = "<!-- general-agent:project-session-contract:start -->"
        end = "<!-- general-agent:project-session-contract:end -->"
        if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
            errors.append("default-mode managed project-session contract has invalid markers")
    constraint_template = skills / "default-mode" / "references" / "constraint.template.md"
    if constraint_template.is_file():
        constraint_text = constraint_template.read_text(encoding="utf-8")
        required_transfer_terms = (
            "Cross-project transcripts are unverified leads only",
            "self-improve",
            "de-identification",
            "applicability boundary",
            "falsifier",
            "owner review",
            "revalidated in this project",
        )
        missing_terms = [term for term in required_transfer_terms if term not in constraint_text]
        if missing_terms:
            errors.append(
                "default-mode constraint template bypasses cross-task transfer admission; missing: "
                + ", ".join(missing_terms)
            )
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[4]
    errors = validate(root)
    if errors:
        print("skill bundle validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("skill bundle validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
