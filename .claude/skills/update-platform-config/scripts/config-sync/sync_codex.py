#!/usr/bin/env python3
"""Safely synchronize explicitly managed Codex user configuration.

Skills are symlinked because Codex consumes the same format. Compatible agents and rules are
rendered as regular files. Existing targets are replaceable only when the manifest already owns
their exact name; opt-in permits a sync attempt but never transfers ownership of a collision.
Every target is preflighted before the first mutation and the manifest is published last.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from agent_roles import (
    AgentRoleError,
    load_role_document,
    parse_frontmatter,
    resolve_agent,
    route_matches_backend,
    validate_agent_directory,
)
from project_paths import project_root, skills_root
from sync_policy import (
    CanonicalInputs,
    PathState,
    USER_CONFIG_WRITE_ENV,
    SyncPlan,
    SyncTransactionError,
    add_canonical_input_guards,
    add_role_document_guard,
    capture_path_state,
    commit_plan,
    user_config_write_enabled,
)

REPO = str(project_root())
SKILLS_ROOT = str(skills_root())
CODEX = os.path.expanduser("~/.codex")
MANIFEST = os.path.join(CODEX, ".general-agent-sync.json")
MANIFEST_KEYS = ("skills", "agents", "rules")


def _empty_manifest() -> dict[str, list[str]]:
    return {key: [] for key in MANIFEST_KEYS}


def _safe_manifest_name(kind: str, name: object) -> bool:
    if not isinstance(name, str) or not name or name in {".", ".."}:
        return False
    if Path(name).name != name or "\x00" in name:
        return False
    if kind == "rules" and not name.endswith(".rules"):
        return False
    return True


def _validate_manifest(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) - set(MANIFEST_KEYS):
        raise RuntimeError("manifest must be an object containing only skills, agents, and rules")
    validated = _empty_manifest()
    for key in MANIFEST_KEYS:
        names = value.get(key, [])
        if not isinstance(names, list) or any(not _safe_manifest_name(key, name) for name in names):
            raise RuntimeError(f"manifest {key} must be a list of safe names")
        if len(set(names)) != len(names):
            raise RuntimeError(f"manifest {key} contains duplicate names")
        validated[key] = list(names)
    return validated


def load_manifest() -> dict[str, list[str]]:
    path = Path(MANIFEST)
    state = capture_path_state(path)
    if state.kind == "missing":
        return _empty_manifest()
    if state.kind != "regular file":
        raise RuntimeError(
            f"{path}: expected a regular file, found {state.kind}"
        )
    assert state.content is not None
    try:
        parsed = json.loads(state.content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{path}: invalid synchronization manifest: {error}") from error
    return _validate_manifest(parsed)


def toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def split_frontmatter(text: str) -> tuple[dict, str]:
    return parse_frontmatter(text)


def is_codex_model(spec: str) -> bool:
    base = spec.split(":", 1)[0]
    provider, _, model = base.partition("/")
    return provider in {"codex", "openai-codex"} or model.startswith("gpt") or base.startswith("gpt")


def provider_compatible(meta: dict, role_document: dict | None = None) -> bool:
    document = role_document if role_document is not None else load_role_document()
    route = resolve_agent(meta, document)
    return route_matches_backend(route, "codex")


def _preflight_desired_link(
    plan: SyncPlan,
    path: Path,
    target: str,
    owned: bool,
) -> None:
    current = capture_path_state(path)
    if current.kind == "missing":
        plan.add_symlink(path, target, before=current, label="managed Codex skill")
        return
    if not owned:
        raise RuntimeError(f"{path}: unowned name collision; personal bytes were not changed")
    if current.kind != "symbolic link":
        raise RuntimeError(
            f"{path}: expected a managed symbolic link, found {current.kind}"
        )
    plan.add_symlink(path, target, before=current, label="managed Codex skill")


def _preflight_desired_file(
    plan: SyncPlan,
    path: Path,
    content: bytes,
    default_mode: int,
    owned: bool,
) -> None:
    current = capture_path_state(path)
    if current.kind == "missing":
        plan.add_write(
            path,
            content,
            mode=default_mode,
            before=current,
            label="managed Codex file",
        )
        return
    if not owned:
        raise RuntimeError(f"{path}: unowned name collision; personal bytes were not changed")
    if current.kind != "regular file":
        raise RuntimeError(f"{path}: expected a managed regular file, found {current.kind}")
    assert current.snapshot is not None
    plan.add_write(
        path,
        content,
        mode=current.snapshot.mode,
        before=current,
        label="managed Codex file",
    )


def _preflight_stale(
    plan: SyncPlan,
    path: Path,
    allowed_type: str,
) -> None:
    current = capture_path_state(path)
    if current.kind == "missing":
        return
    valid = current.kind == ("symbolic link" if allowed_type == "link" else "regular file")
    if not valid:
        raise RuntimeError(
            f"{path}: stale managed name was replaced by a {current.kind}; refusing deletion"
        )
    plan.add_delete(
        path,
        before=current,
        allowed_kinds=frozenset({current.kind}),
        label="stale manifest-owned Codex target",
    )


def _plan_skills(manifest: dict[str, list[str]]) -> tuple[list[str], SyncPlan]:
    source_root = Path(SKILLS_ROOT)
    destination_root = Path(CODEX) / "skills"
    owned = set(manifest["skills"])
    managed: list[str] = []
    plan = SyncPlan("codex-skills")

    plan.add_directory_chain(source_root, mode=0o755)
    source_root_state = capture_path_state(source_root)
    if source_root_state.kind != "directory":
        raise RuntimeError(f"{source_root}: skills source is not a directory")
    plan.add_guard(source_root, source_root_state, label="skill directory membership")

    for source in sorted(source_root.iterdir(), key=lambda item: item.name):
        skill_file = source / "SKILL.md"
        source_state = capture_path_state(source)
        if source_state.kind != "directory":
            continue
        skill_state = capture_path_state(skill_file)
        if skill_state.kind != "regular file":
            continue
        plan.add_directory_chain(source, mode=0o755)
        plan.add_file_guard(
            skill_file,
            skill_state,
            label=f"skill marker {source.name}/SKILL.md",
        )
        name = source.name
        managed.append(name)

    if managed or owned:
        plan.add_directory_chain(destination_root, mode=0o700)
    for name in managed:
        source = str(source_root / name)
        _preflight_desired_link(plan, destination_root / name, source, name in owned)
    for name in sorted(owned - set(managed)):
        _preflight_stale(plan, destination_root / name, "link")
    return managed, plan


def _render_agents(
    role_document: dict,
    canonical_inputs: CanonicalInputs | None = None,
) -> tuple[dict[str, bytes], list[str], SyncPlan]:
    source_root = Path(REPO) / ".omp" / "agents"
    source_plan = SyncPlan("codex-agent-sources")
    records: list[tuple[Path, dict, str, object]] = []
    if canonical_inputs is not None:
        if canonical_inputs.role_document is not role_document:
            raise RuntimeError("Codex builder received a different canonical role document")
        add_canonical_input_guards(source_plan, canonical_inputs)
        records = [
            (agent.path, agent.metadata, agent.body, agent.route)
            for agent in canonical_inputs.agents
        ]
    else:
        validate_agent_directory(source_root, role_document)
        source_plan.add_directory_chain(source_root, mode=0o755)
        source_root_state = capture_path_state(source_root)
        source_plan.add_guard(
            source_root,
            source_root_state,
            label="canonical agent directory membership",
        )
        for source in sorted(source_root.iterdir(), key=lambda item: item.name):
            if source.suffix != ".md":
                continue
            state = source_plan.add_file_guard(
                source, label=f"canonical agent {source.name}"
            )
            assert state.content is not None
            meta, body = split_frontmatter(state.content.decode("utf-8"))
            route = resolve_agent(meta, role_document, str(source))
            records.append((source, meta, body, route))
        if capture_path_state(source_root) != source_root_state:
            raise RuntimeError(f"{source_root}: canonical agent directory changed while read")

    rendered: dict[str, bytes] = {}
    skipped: list[str] = []
    for source, meta, body, route in records:
        name = source.stem
        compatible = (
            route_matches_backend(route, "codex")
            if canonical_inputs is not None
            else provider_compatible(meta, role_document)
        )
        if not compatible:
            skipped.append(name)
            continue
        description = meta.get("description", "")
        instructions = body.strip().replace('"""', "'''")
        route_settings = f'model = "{toml_escape(route.role["model"])}"\n'
        if route.role.get("effort"):
            route_settings += f'reasoning_effort = "{toml_escape(route.role["effort"])}"\n'
        content = (
            f'name = "{toml_escape(meta.get("name", name))}"\n'
            f'description = "{toml_escape(description)}"\n'
            f"{route_settings}"
            f'developer_instructions = """\n{instructions}\n"""\n'
        )
        rendered[name] = content.encode("utf-8")
    return rendered, skipped, source_plan


def _plan_agents(
    manifest: dict[str, list[str]],
    role_document: dict,
    canonical_inputs: CanonicalInputs | None = None,
) -> tuple[list[str], list[str], SyncPlan]:
    destination_root = Path(CODEX) / "agents"
    owned = set(manifest["agents"])
    rendered, skipped, source_plan = _render_agents(role_document, canonical_inputs)
    managed = sorted(rendered)
    plan = SyncPlan("codex-agents")
    plan.extend(source_plan)
    if managed or owned:
        plan.add_directory_chain(destination_root, mode=0o700)
    for name, content in rendered.items():
        _preflight_desired_file(
            plan,
            destination_root / f"{name}.toml",
            content,
            0o600,
            name in owned,
        )
    for name in sorted(owned - set(managed)):
        _preflight_stale(plan, destination_root / f"{name}.toml", "file")
    return managed, skipped, plan


def _plan_rules(manifest: dict[str, list[str]]) -> tuple[list[str], SyncPlan]:
    source_root = (
        Path(SKILLS_ROOT)
        / "update-platform-config"
        / "assets"
        / "project"
        / ".codex"
        / "rules"
    )
    destination_root = Path(CODEX) / "rules"
    owned = set(manifest["rules"])
    rendered: dict[str, tuple[bytes, int]] = {}
    plan = SyncPlan("codex-rules")
    source_parent_state = capture_path_state(source_root.parent)
    if source_parent_state.kind == "directory":
        plan.add_directory_chain(source_root.parent, mode=0o755)
    elif source_parent_state.kind != "missing":
        raise RuntimeError(
            f"{source_root.parent}: rules source parent is not a directory"
        )
    source_root_state = capture_path_state(source_root)
    plan.add_guard(
        source_root,
        source_root_state,
        label="Codex rules source membership",
    )
    if source_root_state.kind != "missing":
        if source_root_state.kind != "directory":
            raise RuntimeError(f"{source_root}: rules source is not a directory")
        plan.add_directory_chain(source_root, mode=0o755)
        for source in sorted(source_root.iterdir(), key=lambda item: item.name):
            if source.suffix != ".rules":
                continue
            state = plan.add_file_guard(source, label=f"Codex rule {source.name}")
            assert state.content is not None and state.snapshot is not None
            rendered[source.name] = (state.content, state.snapshot.mode)

    managed = sorted(rendered)
    if managed or owned:
        plan.add_directory_chain(destination_root, mode=0o700)
    for name, (content, mode) in rendered.items():
        _preflight_desired_file(plan, destination_root / name, content, mode, name in owned)
    for name in sorted(owned - set(managed)):
        _preflight_stale(plan, destination_root / name, "file")
    return managed, plan


def sync_skills(manifest: dict) -> list[str]:
    validated = _validate_manifest(manifest)
    managed, plan = _plan_skills(validated)
    commit_plan(plan)
    return managed


def sync_agents(manifest: dict, role_document: dict | None = None) -> list[str]:
    validated = _validate_manifest(manifest)
    model_path = Path(REPO) / "config" / "workflows" / "models.json"
    document = (
        role_document
        if role_document is not None
        else load_role_document(model_path)
    )
    managed, skipped, plan = _plan_agents(validated, document)
    add_role_document_guard(plan, model_path, document)
    commit_plan(plan)
    if skipped:
        print(f"[sync-codex] role-incompatible agents omitted: {', '.join(skipped)}")
    return managed


def sync_rules(manifest: dict) -> list[str]:
    validated = _validate_manifest(manifest)
    managed, plan = _plan_rules(validated)
    commit_plan(plan)
    return managed


def _manifest_from_state(path: Path, state: PathState) -> dict[str, list[str]]:
    if state.kind == "missing":
        return _empty_manifest()
    if state.kind != "regular file":
        raise RuntimeError(f"{path}: expected a regular file, found {state.kind}")
    assert state.content is not None
    try:
        parsed = json.loads(state.content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{path}: invalid synchronization manifest: {error}") from error
    return _validate_manifest(parsed)


def build_codex_plan(
    role_document: dict,
    *,
    canonical_inputs: CanonicalInputs | None = None,
) -> SyncPlan:
    """Build the complete manifest-owned Codex update without mutating user state."""

    codex_root = Path(CODEX)
    manifest_path = Path(MANIFEST)
    plan = SyncPlan("codex")
    plan.add_directory_chain(codex_root, mode=0o700)
    manifest_state = capture_path_state(manifest_path)
    manifest = _manifest_from_state(manifest_path, manifest_state)

    skills, skill_plan = _plan_skills(manifest)
    agents, skipped, agent_plan = _plan_agents(
        manifest, role_document, canonical_inputs
    )
    rules, rule_plan = _plan_rules(manifest)
    plan.extend(skill_plan)
    plan.extend(agent_plan)
    plan.extend(rule_plan)

    new_manifest = {"skills": skills, "agents": agents, "rules": rules}
    manifest_content = (
        json.dumps(new_manifest, indent=1, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_mode = (
        manifest_state.snapshot.mode
        if manifest_state.snapshot is not None
        else 0o600
    )
    plan.add_write(
        manifest_path,
        manifest_content,
        mode=manifest_mode,
        before=manifest_state,
        label="Codex synchronization manifest",
    )
    plan.metadata.update(
        {
            "codex_skills": tuple(skills),
            "codex_agents": tuple(agents),
            "codex_rules": tuple(rules),
            "codex_skipped": tuple(skipped),
        }
    )
    return plan


def main() -> int:
    if not user_config_write_enabled():
        print(f"[sync-codex] skipped: set {USER_CONFIG_WRITE_ENV}=1 to allow user-config writes")
        return 0
    try:
        model_path = Path(REPO) / "config" / "workflows" / "models.json"
        role_document = load_role_document(model_path)
        validate_agent_directory(Path(REPO) / ".omp" / "agents", role_document)
        plan = build_codex_plan(role_document)
        add_role_document_guard(plan, model_path, role_document)
        commit_plan(plan)
        skills = list(plan.metadata["codex_skills"])
        agents = list(plan.metadata["codex_agents"])
        rules = list(plan.metadata["codex_rules"])
        skipped = list(plan.metadata["codex_skipped"])
    except (AgentRoleError, OSError, RuntimeError, SyncTransactionError, UnicodeError) as error:
        print(f"[sync-codex] refusing unsafe user-config update: {error}", file=sys.stderr)
        return 2

    if skipped:
        print(f"[sync-codex] role-incompatible agents omitted: {', '.join(skipped)}")
    print(
        f"[sync-codex] skills: {len(skills)} symlinked; agents: {len(agents)} TOML-generated; "
        f"rules: {len(rules)} copied (manifest-managed; personal entries untouched)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
