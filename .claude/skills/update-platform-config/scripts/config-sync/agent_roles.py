#!/usr/bin/env python3
"""Resolve reusable agent routes from the canonical workflow model-role document.

Agent frontmatter owns intent (``model-role`` and, for an independent reviewer,
exact ``writer-role`` or reusable ``writer-family``). ``config/workflows/models.json`` is the only owner of the current
backend, provider, exact model snapshot, and effort for those roles. Native
generators use this module so agent-local model copies cannot silently become a
routing source.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any, NamedTuple


from project_paths import project_root


REPO = project_root()
ROLE_PATH = REPO / "config" / "workflows" / "models.json"
AGENT_DIR = REPO / ".omp" / "agents"
REVIEW_CLASSES = frozenset({"verification"})
DISPATCHABLE_STATUS = "active"
VERIFIED_CATALOG_STATUS = "verified"


class AgentRoleError(ValueError):
    """An agent route cannot be proven from the canonical role document."""


class AgentRoute(NamedTuple):
    source: str
    name: str
    model_role: str
    role: dict[str, Any]
    writer_role: str | None
    writer: dict[str, Any] | None
    writer_family: str | None


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse the small YAML subset used by canonical agent definitions."""
    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        key_match = re.match(r"^(\w[\w-]*):\s*(.*)$", lines[index])
        if not key_match or lines[index].startswith(" "):
            index += 1
            continue
        key, raw = key_match.group(1), key_match.group(2).strip()
        if key == "model" and not raw:
            values: list[str] = []
            index += 1
            while index < len(lines):
                item = re.match(r"^\s+-\s+(\S+)", lines[index])
                if not item:
                    break
                values.append(item.group(1))
                index += 1
            meta[key] = values
            continue
        value = raw.split("#", 1)[0].strip()
        meta[key] = [value] if key == "model" and value else value.strip('"')
        index += 1
    return meta, match.group(2)


def expected_model_snapshot(role: dict[str, Any]) -> str:
    provider = role.get("provider")
    model = role.get("model")
    if not isinstance(provider, str) or not provider or not isinstance(model, str) or not model:
        raise AgentRoleError("selected model-role needs non-empty provider and model")
    return f"{provider}/{model}"


def _named_role(
    roles: dict[str, Any], role_name: Any, field: str, source: str
) -> tuple[str, dict[str, Any]]:
    if not isinstance(role_name, str) or not role_name.strip():
        raise AgentRoleError(f"{source}: missing {field}")
    role_name = role_name.strip()
    role = roles.get(role_name)
    if not isinstance(role, dict):
        raise AgentRoleError(f"{source}: unknown {field} {role_name!r}")
    for required in ("backend", "provider", "model"):
        if not isinstance(role.get(required), str) or not role[required].strip():
            raise AgentRoleError(f"{source}: role {role_name!r} needs {required}")
    return role_name, role


def resolve_agent(
    meta: dict[str, Any], role_document: dict[str, Any], source: str = "agent",
    *, require_catalog_verified: bool = False,
) -> AgentRoute:
    roles = role_document.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise AgentRoleError(f"{source}: canonical role document has no roles")

    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        raise AgentRoleError(f"{source}: missing name")
    model_role, role = _named_role(roles, meta.get("model-role"), "model-role", source)
    if role.get("dispatchStatus") != DISPATCHABLE_STATUS:
        raise AgentRoleError(
            f"{source}: model-role {model_role!r} is {role.get('dispatchStatus')!r}, not dispatchable"
        )
    if require_catalog_verified:
        resolution = role_document.get("catalogResolution")
        if not isinstance(resolution, dict) or resolution.get("status") != VERIFIED_CATALOG_STATUS:
            raise AgentRoleError(
                f"{source}: authenticated native catalog resolution is not verified"
            )
        verified_roles = resolution.get("verifiedRoles")
        if not isinstance(verified_roles, list) or model_role not in verified_roles:
            raise AgentRoleError(
                f"{source}: model-role {model_role!r} is absent from verifiedRoles"
            )

    if "model" in meta:
        raise AgentRoleError(
            f"{source}: model must be omitted; model-role {model_role!r} is resolved "
            "from the canonical role document"
        )

    cross_provider = str(meta.get("cross-provider", "false")).strip().lower() == "true"
    role_class = role.get("intelligenceClass")
    writer_role: str | None = None
    writer: dict[str, Any] | None = None
    writer_family: str | None = None
    if cross_provider:
        if role_class not in REVIEW_CLASSES:
            raise AgentRoleError(
                f"{source}: cross-provider agent role {model_role!r} is not a verification role"
            )
        declared_writer_role = meta.get("writer-role")
        declared_writer_family = meta.get("writer-family")
        if bool(declared_writer_role) == bool(declared_writer_family):
            raise AgentRoleError(
                f"{source}: cross-provider reviewer requires exactly one of writer-role "
                "or writer-family"
            )
        if declared_writer_role:
            writer_role, writer = _named_role(
                roles, declared_writer_role, "writer-role", source
            )
            if writer.get("dispatchStatus") != DISPATCHABLE_STATUS:
                raise AgentRoleError(
                    f"{source}: writer-role {writer_role!r} is "
                    f"{writer.get('dispatchStatus')!r}, not dispatchable"
                )
            writer_family = writer.get("upstreamProviderFamily")
            writers_to_validate = [(writer_role, writer)]
        else:
            if not nonempty_string(declared_writer_family):
                raise AgentRoleError(f"{source}: writer-family must be non-empty")
            writer_family = declared_writer_family.strip()
            writers_to_validate = [
                (candidate_name, candidate)
                for candidate_name, candidate in roles.items()
                if isinstance(candidate, dict)
                and candidate.get("dispatchStatus") == DISPATCHABLE_STATUS
                and candidate.get("upstreamProviderFamily") == writer_family
            ]
            if not writers_to_validate:
                raise AgentRoleError(
                    f"{source}: writer-family {writer_family!r} has no active semantic roles"
                )
        reviewer_upstream = role.get("upstreamProviderFamily")
        if not nonempty_string(writer_family) or not nonempty_string(reviewer_upstream):
            raise AgentRoleError(f"{source}: writer and reviewer need upstreamProviderFamily")
        if writer_family == reviewer_upstream:
            raise AgentRoleError(
                f"{source}: reviewer role {model_role!r} and writer role "
                f"{writer_role or writer_family!r} use the same upstream provider "
                f"{reviewer_upstream!r}"
            )
        if not name.strip().endswith(f"-{writer_family}-writer"):
            raise AgentRoleError(
                f"{source}: cross-provider reviewer name must be direction-qualified "
                f"with suffix '-{writer_family}-writer'"
            )
        for candidate_name, candidate in writers_to_validate:
            _validate_reviewer_pair(
                role_document, candidate_name, candidate, model_role, role, source
            )
    else:
        if meta.get("writer-role") or meta.get("writer-family"):
            raise AgentRoleError(
                f"{source}: writer-role/writer-family requires cross-provider: true"
            )
        if role_class in REVIEW_CLASSES:
            raise AgentRoleError(
                f"{source}: verification role {model_role!r} requires cross-provider: true"
            )

    return AgentRoute(
        source, name.strip(), model_role, role, writer_role, writer, writer_family
    )


def resolve_reviewer_assignment(
    meta: dict[str, Any], role_document: dict[str, Any], actual_writer_role: str,
    source: str = "reviewer agent", *, require_catalog_verified: bool = False,
) -> AgentRoute:
    """Resolve a reviewer and bind it to the writer role selected by the caller.

    A reusable reviewer is direction-specific.  Its declared ``writer-role`` is
    part of the agent identity, while ``actual_writer_role`` is the dispatch
    request.  Requiring equality prevents a generic agent name from silently
    reviewing a writer from the opposite provider family.
    """
    if not isinstance(actual_writer_role, str) or not actual_writer_role.strip():
        raise AgentRoleError(f"{source}: missing actual writer role")
    actual_writer_role = actual_writer_role.strip()
    roles = role_document.get("roles")
    if not isinstance(roles, dict):
        raise AgentRoleError(f"{source}: canonical role document has no roles")
    actual_name, actual_writer = _named_role(
        roles, actual_writer_role, "actual writer role", source
    )
    if actual_writer.get("dispatchStatus") != DISPATCHABLE_STATUS:
        raise AgentRoleError(
            f"{source}: actual writer role {actual_name!r} is not dispatchable"
        )
    if require_catalog_verified:
        resolution = role_document.get("catalogResolution")
        if not isinstance(resolution, dict) or resolution.get("status") != VERIFIED_CATALOG_STATUS:
            raise AgentRoleError(
                f"{source}: authenticated native catalog resolution is not verified"
            )
        verified_roles = resolution.get("verifiedRoles") if isinstance(resolution, dict) else None
        if not isinstance(verified_roles, list) or actual_name not in verified_roles:
            raise AgentRoleError(
                f"{source}: actual writer role {actual_name!r} is absent from verifiedRoles"
            )
    declared_role = meta.get("writer-role")
    declared_family = meta.get("writer-family")
    if declared_role and declared_role != actual_name:
        raise AgentRoleError(
            f"{source}: actual writer role {actual_name!r} is not declared "
            f"by this reviewer ({declared_role!r})"
        )
    actual_family = actual_writer.get("upstreamProviderFamily")
    if declared_family and declared_family != actual_family:
        raise AgentRoleError(
            f"{source}: actual writer role {actual_name!r} is not declared by this "
            f"direction-specific reviewer family ({declared_family!r})"
        )
    route = resolve_agent(
        meta, role_document, source, require_catalog_verified=require_catalog_verified
    )
    _validate_reviewer_pair(
        role_document, actual_name, actual_writer, route.model_role, route.role, source
    )
    return route._replace(
        writer_role=actual_name,
        writer=actual_writer,
        writer_family=actual_family,
    )


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_reviewer_pair(
    document: dict[str, Any], writer_name: str, writer: dict[str, Any],
    reviewer_name: str, reviewer: dict[str, Any], source: str,
) -> None:
    capability = document.get("capabilityPolicy")
    pairings = capability.get("reviewPairings") if isinstance(capability, dict) else None
    if not isinstance(pairings, list):
        raise AgentRoleError(f"{source}: canonical capabilityPolicy has no reviewPairings")
    writer_key = (writer.get("upstreamProviderFamily"), writer.get("modelFamily"))
    matches = []
    for pair in pairings:
        if not isinstance(pair, dict):
            continue
        pair_writer = pair.get("writer")
        if isinstance(pair_writer, dict) and (
            pair_writer.get("upstreamProviderFamily"), pair_writer.get("modelFamily")
        ) == writer_key:
            matches.append(pair)
    if not matches:
        defaults = capability.get("providerReviewerDefaults")
        default = defaults.get(writer_key[0]) if isinstance(defaults, dict) else None
        if not isinstance(default, dict):
            raise AgentRoleError(
                f"{source}: no reviewer default for writer role {writer_name!r} "
                f"upstream provider {writer_key[0]!r}"
            )
        if reviewer_name != default.get("reviewerRole") or reviewer.get("effort") != default.get("requiredEffort"):
            raise AgentRoleError(
                f"{source}: reviewer role {reviewer_name!r} does not match provider default "
                f"{default.get('reviewerRole')!r} at {default.get('requiredEffort')!r}"
            )
        return
    if len(matches) != 1:
        raise AgentRoleError(
            f"{source}: no unique reviewer pairing for writer role {writer_name!r} "
            f"family {writer_key!r}"
        )
    pair_writer = matches[0]["writer"]
    pair_reviewer = matches[0].get("reviewer")
    if not isinstance(pair_reviewer, dict):
        raise AgentRoleError(f"{source}: reviewer pairing is malformed")
    if writer.get("effort") not in pair_writer.get("efforts", []):
        raise AgentRoleError(
            f"{source}: writer role {writer_name!r} effort {writer.get('effort')!r} "
            "is outside the exact reviewer pairing"
        )
    expected = (
        pair_reviewer.get("role"), pair_reviewer.get("upstreamProviderFamily"),
        pair_reviewer.get("modelFamily"), pair_reviewer.get("effort"),
    )
    actual = (
        reviewer_name, reviewer.get("upstreamProviderFamily"),
        reviewer.get("modelFamily"), reviewer.get("effort"),
    )
    if actual != expected:
        raise AgentRoleError(
            f"{source}: reviewer route {actual!r} does not match exact pairing {expected!r}"
        )


def route_matches_backend(route: AgentRoute, backend: str) -> bool:
    """Whether a native harness can honor the selected semantic role."""
    return route.role["backend"] == backend


def validate_agent_directory(
    agent_dir: Path, role_document: dict[str, Any]
) -> list[AgentRoute]:
    if not agent_dir.is_dir():
        raise AgentRoleError(f"missing canonical agent directory: {agent_dir}")
    routes: list[AgentRoute] = []
    for path in sorted(agent_dir.glob("*.md")):
        meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        routes.append(resolve_agent(meta, role_document, str(path)))
    if not routes:
        raise AgentRoleError(f"no canonical agent definitions in {agent_dir}")
    names = [route.name for route in routes]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise AgentRoleError(f"duplicate canonical agent names: {', '.join(duplicates)}")
    return routes


def _validate_role_document(document: dict[str, Any]) -> None:
    validator_path = Path(__file__).with_name("validate_model_roles.py")
    spec = importlib.util.spec_from_file_location("_canonical_model_role_validator", validator_path)
    if spec is None or spec.loader is None:
        raise AgentRoleError(f"cannot load canonical role validator: {validator_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    try:
        module.validate(document)
    except module.ValidationError as error:
        raise AgentRoleError(f"invalid canonical model-role document: {error}") from error


def load_role_document(path: Path = ROLE_PATH) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentRoleError(f"cannot load canonical model roles from {path}: {error}") from error
    if not isinstance(document, dict):
        raise AgentRoleError(f"canonical model roles at {path} must be a JSON object")
    _validate_role_document(document)
    resolution = document.get("catalogResolution")
    if not isinstance(resolution, dict) or resolution.get("status") != VERIFIED_CATALOG_STATUS:
        raise AgentRoleError(
            "canonical model roles are bootstrap-unverified; resolve every active role through "
            "authenticated native catalogs before runtime sync"
        )
    verified_roles = resolution.get("verifiedRoles")
    if not isinstance(verified_roles, list):
        raise AgentRoleError("canonical catalog resolution has no verifiedRoles list")
    active_roles = {
        name for name, role in document.get("roles", {}).items()
        if isinstance(role, dict) and role.get("dispatchStatus") == DISPATCHABLE_STATUS
    }
    if set(verified_roles) != active_roles:
        raise AgentRoleError("canonical verifiedRoles does not exactly cover active roles")
    return document


def validate_canonical_agents(
    role_path: Path = ROLE_PATH, agent_dir: Path = AGENT_DIR
) -> list[AgentRoute]:
    return validate_agent_directory(agent_dir, load_role_document(role_path))
