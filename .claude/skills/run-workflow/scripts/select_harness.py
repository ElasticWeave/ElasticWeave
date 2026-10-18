#!/usr/bin/env python3
"""Select a capability-compatible workflow harness without importing or running it."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any

REGISTRY_SCHEMA = "workflow-harness-registry/v1"
DESIGN_SCHEMA = "workflow-design/v1"
HUMANIZE_MAPPING_SCHEMA = "humanize-workflow-mapping/v1"
HUMANIZE_ADAPTER_VERSION = 1
HUMANIZE_UPSTREAM_REPOSITORY = "https://github.com/humanfia/humanize2.git"
HUMANIZE_UPSTREAM_REVISION = "8fbb2b718d74b7ef1d3d0650a9768415ec489f26"
HUMANIZE_DISTRIBUTION = "hmz==0.1.0"
HARNESS_ID = re.compile(r"^[a-z][a-z0-9-]*$")
CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9-]*$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_STATUS = re.compile(
    r"^(?:native(?:-[a-z0-9-]+)*|adapter-[a-z0-9-]+|not-supported(?:-[a-z0-9-]+)*)$"
)
REVIEW_POLICIES = {"independent", "machine-gated"}
COMMAND_KEYS = {"check", "ensure", "validate", "run"}
OPTIONAL_COMMAND_KEYS = {"finalize"}
INSTALL_MODES = {"built-in", "lazy-pinned-isolated-venv"}
ENTRY_KEYS = {
    "enabled",
    "reviewPolicies",
    "skill",
    "artifact",
    "install",
    "commands",
    "capabilities",
}


def _load_design_contract():
    validator_path = (
        Path(__file__).resolve().parents[2]
        / "build-workflow"
        / "scripts"
        / "validate_design.py"
    )
    spec = importlib.util.spec_from_file_location(
        "workflow_design_contract", validator_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load workflow design contract: {validator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_humanize_mapping_contract():
    validator_path = (
        Path(__file__).resolve().parents[2]
        / "humanize-workflow"
        / "scripts"
        / "safe_mapping.py"
    )
    spec = importlib.util.spec_from_file_location(
        "humanize_mapping_contract", validator_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load humanize mapping contract: {validator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DESIGN_CONTRACT = _load_design_contract()
HUMANIZE_MAPPING_CONTRACT = _load_humanize_mapping_contract()
CANONICAL_CAPABILITIES = frozenset(DESIGN_CONTRACT.CANONICAL_CAPABILITIES)


def load_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_project_relative_path(
    value: object, label: str, *, placeholders: set[str] | None = None
) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty project-relative path")
    normalized = value
    for placeholder in placeholders or set():
        normalized = normalized.replace(placeholder, "placeholder")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or "." in pure.parts
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"{label} must stay inside the project root")
    unknown = re.findall(r"<[^>]+>", normalized)
    if unknown:
        raise ValueError(f"{label} contains unsupported placeholders {unknown}")
    return value


def classify(status: object) -> tuple[bool, int]:
    if not isinstance(status, str) or not SUPPORTED_STATUS.fullmatch(status):
        raise ValueError(f"unsupported capability status {status!r}")
    if status.startswith("not-supported"):
        return False, 0
    return True, int(status.startswith("adapter-"))


def validate_registry(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise ValueError(f"registry.schema must equal {REGISTRY_SCHEMA}")
    unknown_root = sorted(set(registry) - {"schema", "default", "harnesses"})
    if unknown_root:
        raise ValueError(f"registry has unsupported keys {unknown_root}")
    harnesses = registry.get("harnesses")
    if not isinstance(harnesses, dict) or not harnesses:
        raise ValueError("registry.harnesses must be a non-empty object")
    for name, spec in harnesses.items():
        if not isinstance(name, str) or not HARNESS_ID.fullmatch(name):
            raise ValueError(f"registry harness id {name!r} must be hyphen-case")
        if not isinstance(spec, dict):
            raise ValueError(f"registry harness {name} must be an object")
        unknown = sorted(set(spec) - ENTRY_KEYS)
        if unknown:
            raise ValueError(f"registry harness {name} has unsupported keys {unknown}")
        if not isinstance(spec.get("enabled", True), bool):
            raise ValueError(f"registry harness {name}.enabled must be boolean")
        review_policies = spec.get("reviewPolicies")
        if (
            not isinstance(review_policies, list)
            or not review_policies
            or not all(
                isinstance(policy, str) and policy in REVIEW_POLICIES
                for policy in review_policies
            )
            or len(set(review_policies)) != len(review_policies)
        ):
            raise ValueError(
                f"registry harness {name}.reviewPolicies must be a non-empty unique allowlist"
            )
        skill = spec.get("skill")
        if not isinstance(skill, str) or not HARNESS_ID.fullmatch(skill):
            raise ValueError(f"registry harness {name}.skill must be hyphen-case")
        validate_project_relative_path(
            spec.get("artifact"),
            f"registry harness {name}.artifact",
            placeholders={"<design-id>", "<design_id_snake_case>"},
        )
        install = spec.get("install")
        if install not in INSTALL_MODES:
            raise ValueError(
                f"registry harness {name}.install must be one of {sorted(INSTALL_MODES)}"
            )
        commands = spec.get("commands")
        if (
            not isinstance(commands, dict)
            or not COMMAND_KEYS.issubset(commands)
            or not set(commands).issubset(COMMAND_KEYS | OPTIONAL_COMMAND_KEYS)
            or not all(
                isinstance(command, str) and command.strip()
                for command in commands.values()
            )
        ):
            raise ValueError(
                f"registry harness {name}.commands must include non-empty "
                f"{sorted(COMMAND_KEYS)}"
            )
        if "<artifact>" not in commands["validate"] or "<artifact>" not in commands["run"]:
            raise ValueError(
                f"registry harness {name}.commands validate/run must reference <artifact>"
            )
        capabilities = spec.get("capabilities")
        if not isinstance(capabilities, dict):
            raise ValueError(f"registry harness {name}.capabilities must be an object")
        unknown_capabilities = sorted(set(capabilities) - CANONICAL_CAPABILITIES)
        if unknown_capabilities:
            raise ValueError(
                f"registry harness {name} has unknown capabilities {unknown_capabilities}"
            )
        missing_capabilities = sorted(CANONICAL_CAPABILITIES - set(capabilities))
        if missing_capabilities:
            raise ValueError(
                f"registry harness {name} omits explicit capability statuses "
                f"{missing_capabilities}"
            )
        for capability, status in capabilities.items():
            if not CAPABILITY_ID.fullmatch(str(capability)):
                raise ValueError(
                    f"registry harness {name} capability {capability!r} must be hyphen-case"
                )
            try:
                classify(status)
            except ValueError as error:
                raise ValueError(
                    f"registry harness {name} capability {capability} status is invalid: {error}"
                ) from error
    configured_default = registry.get("default")
    if configured_default != "auto" and configured_default not in harnesses:
        raise ValueError(f"unknown registry default harness: {configured_default}")
    return harnesses


def validate_design_for_selection(design: dict[str, Any]) -> None:
    if design.get("schema") != DESIGN_SCHEMA:
        raise ValueError(f"design.schema must equal {DESIGN_SCHEMA}")
    capabilities = design.get("requiredCapabilities")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(isinstance(item, str) and item for item in capabilities)
    ):
        raise ValueError("design.requiredCapabilities must be a non-empty string list")
    if len(capabilities) != len(set(capabilities)):
        raise ValueError("design.requiredCapabilities contains duplicates")
    unknown = sorted(set(capabilities) - CANONICAL_CAPABILITIES)
    if unknown:
        raise ValueError(
            f"design.requiredCapabilities contains non-canonical names {unknown}"
        )
    governance = design.get("governance")
    if not isinstance(governance, dict):
        raise ValueError("design.governance must be an object")
    if governance.get("reviewPolicy") not in REVIEW_POLICIES:
        raise ValueError("design.governance.reviewPolicy is unsupported")
    if governance.get("completionProof") not in {"none", "state-bound"}:
        raise ValueError("design.governance.completionProof is unsupported")
    derived = DESIGN_CONTRACT.derive_required_capabilities(design)
    missing = sorted(derived - set(capabilities))
    contradictory = sorted(
        (set(capabilities) & DESIGN_CONTRACT.STRUCTURE_BOUND_CAPABILITIES)
        - derived
    )
    if missing:
        raise ValueError(
            f"design.requiredCapabilities omits derived capabilities {missing}"
        )
    if contradictory:
        raise ValueError(
            "design.requiredCapabilities declares contradictory structure-bound "
            f"capabilities {contradictory}"
        )
    validation_errors = DESIGN_CONTRACT.validate(design)
    if validation_errors:
        raise ValueError(
            "design fails workflow-design/v1 validation: "
            + "; ".join(validation_errors)
        )


def required_capabilities(design: dict[str, Any]) -> list[str]:
    validate_design_for_selection(design)
    return list(dict.fromkeys(design["requiredCapabilities"]))


def governance_gap(spec: dict[str, Any], design: dict[str, Any]) -> str | None:
    governance = design["governance"]
    policy = governance["reviewPolicy"]
    return None if policy in spec["reviewPolicies"] else f"review-policy:{policy}"


def expand_artifact_path(pattern: str, design_id: str) -> str:
    return pattern.replace("<design-id>", design_id).replace(
        "<design_id_snake_case>", design_id.replace("-", "_")
    )


def project_file(root: Path, relative: object, label: str) -> Path:
    value = validate_project_relative_path(relative, label)
    root_resolved = root.resolve()
    candidate_lexical = root_resolved / value
    cursor = root_resolved
    for part in PurePosixPath(value).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink")
    candidate = candidate_lexical.resolve()
    if not candidate.is_relative_to(root_resolved):
        raise ValueError(f"{label} resolves outside the project root")
    return candidate


def report_path(root: Path, design_id: str, harness: str) -> Path:
    suffix = "subagent" if harness == "subagent-scheduler" else harness
    relative = f".workflow/mappings/{design_id}.{suffix}.md"
    return project_file(root, relative, f"{harness}.mapping-report")


def _report_issues(
    report: Path,
    *,
    design_relative: str,
    design_digest: str,
    artifact_relative: str,
    artifact_digest: str,
) -> list[str]:
    if not report.is_file():
        return ["mapping-report-missing"]
    text = report.read_text(encoding="utf-8")
    issues: list[str] = []
    matches = {
        "design": re.findall(
            r"^- (?:Design|Canonical design|\u8bbe\u8ba1)[\uFF1A:]\s*(.*)$",
            text,
            re.MULTILINE,
        ),
        "design_digest": re.findall(
            r"^- (?:Design SHA-256|Canonical design SHA-256|\u8bbe\u8ba1 SHA-256)[\uFF1A:]\s*(.*)$",
            text,
            re.MULTILINE,
        ),
        "artifact": re.findall(
            r"^- (?:Mapping|\u6620\u5c04)[\uFF1A:]\s*(.*)$", text, re.MULTILINE
        ),
        "artifact_digest": re.findall(
            r"^- (?:Mapping SHA-256|\u6620\u5c04 SHA-256)[\uFF1A:]\s*(.*)$",
            text,
            re.MULTILINE,
        ),
    }
    if any(len(found) > 1 for found in matches.values()):
        issues.append("mapping-report-duplicate-fields")
    values: dict[str, str | None] = {}
    for field, found in matches.items():
        parsed = re.fullmatch(r"`([^`]*)`", found[0].strip()) if len(found) == 1 else None
        values[field] = parsed.group(1) if parsed is not None else None
    if any(len(found) != 1 for found in matches.values()) or any(
        value is None for value in values.values()
    ):
        issues.append("mapping-report-schema")
    if values["design"] != design_relative:
        issues.append("mapping-report-design-path")
    if values["design_digest"] != design_digest:
        issues.append("mapping-report-design-digest")
    if values["artifact"] != artifact_relative:
        issues.append("mapping-report-artifact-path")
    if values["artifact_digest"] != artifact_digest:
        issues.append("mapping-report-artifact-digest")
    return issues


def _subagent_mapping_issues(
    artifact: Path,
    *,
    design_relative: str,
    design_digest: str,
) -> list[str]:
    try:
        mapping = load_object(artifact)
    except (OSError, ValueError, json.JSONDecodeError):
        return ["mapping-json"]
    issues: list[str] = []
    if set(mapping) - {
        "schema",
        "designPath",
        "designSha256",
        "inputPath",
        "reasoningPosture",
        "scheduler",
        "proof",
        "handlers",
    }:
        issues.append("mapping-unknown-keys")
    if mapping.get("schema") != "subagent-workflow/v1":
        issues.append("mapping-schema")
    if mapping.get("designPath") != design_relative:
        issues.append("mapping-design-path")
    if mapping.get("designSha256") != design_digest:
        issues.append("mapping-design-digest")
    posture = mapping.get("reasoningPosture")
    if not isinstance(posture, str) or not posture.strip():
        issues.append("mapping-reasoning-posture")
    if not isinstance(mapping.get("handlers"), dict):
        issues.append("mapping-handlers")
    scheduler = mapping.get("scheduler")
    try:
        validate_project_relative_path(mapping.get("inputPath"), "mapping.inputPath")
    except ValueError:
        issues.append("mapping-input-path")
    if (
        not isinstance(mapping.get("inputPath"), str)
        or not mapping.get("inputPath")
        or not isinstance(scheduler, dict)
        or not isinstance(scheduler.get("role"), str)
        or not scheduler.get("role")
        or scheduler.get("mode")
        not in {"ambiguous-only", "every-frontier", "deterministic-all"}
        or isinstance(scheduler.get("maxCalls"), bool)
        or not isinstance(scheduler.get("maxCalls"), int)
        or scheduler.get("maxCalls") < 0
    ):
        issues.append("mapping-scheduler")
    return issues


def _humanize_mapping_issues(
    root: Path,
    manifest_path: Path,
    *,
    design_relative: str,
    design_digest: str,
) -> list[str]:
    try:
        manifest = load_object(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return ["mapping-json"]
    issues: list[str] = []
    if manifest.get("schema") != HUMANIZE_MAPPING_SCHEMA:
        issues.append("mapping-schema")
    if manifest.get("adapterVersion") != HUMANIZE_ADAPTER_VERSION:
        issues.append("adapter-version")
    upstream = manifest.get("upstream")
    if (
        not isinstance(upstream, dict)
        or upstream.get("repository") != HUMANIZE_UPSTREAM_REPOSITORY
        or upstream.get("revision") != HUMANIZE_UPSTREAM_REVISION
        or upstream.get("distribution") != HUMANIZE_DISTRIBUTION
    ):
        issues.append("upstream-binding")
    if manifest.get("designPath") != design_relative:
        issues.append("mapping-design-path")
    if manifest.get("designSha256") != design_digest:
        issues.append("mapping-design-digest")
    for label, path_field, digest_field in (
        ("spec", "specPath", "specSha256"),
        ("artifact", "artifact", "artifactSha256"),
    ):
        try:
            source = project_file(root, manifest.get(path_field), path_field)
        except (OSError, ValueError):
            issues.append(f"{label}-path")
            continue
        expected = manifest.get(digest_field)
        if not isinstance(expected, str) or not DIGEST.fullmatch(expected):
            issues.append(f"{label}-digest-schema")
        elif not source.is_file() or sha256_file(source) != expected:
            issues.append(f"{label}-digest")
    design_id = Path(design_relative).name.removesuffix(".workflow.json")
    if manifest.get("artifact") != f".humanize/generated-flows/{design_id}.py":
        issues.append("artifact-namespace")
    terminal_protocol = manifest.get("terminalProtocol")
    node_agents = manifest.get("nodeAgents")
    valid_node_agents = isinstance(node_agents, list)
    seen_agent_nodes: set[str] = set()
    if valid_node_agents:
        for node_agent in node_agents:
            if (
                not isinstance(node_agent, dict)
                or set(node_agent) != {"node", "role", "workspaceAccess"}
                or not isinstance(node_agent.get("node"), str)
                or not HARNESS_ID.fullmatch(node_agent.get("node", ""))
                or node_agent.get("node") in seen_agent_nodes
                or not isinstance(node_agent.get("role"), str)
                or not node_agent.get("role")
                or node_agent.get("workspaceAccess") not in {"read", "write"}
            ):
                valid_node_agents = False
                break
            seen_agent_nodes.add(node_agent["node"])
    try:
        validate_project_relative_path(manifest.get("inputPath"), "manifest.inputPath")
    except ValueError:
        valid_input_path = False
    else:
        valid_input_path = True
    if (
        not valid_input_path
        or not valid_node_agents
        or not isinstance(terminal_protocol, dict)
        or terminal_protocol.get("field") != "TERMINAL"
        or terminal_protocol.get("values")
        != ["COMPLETE", "HUMAN_BLOCKED", "REVISE"]
        or terminal_protocol.get("humanRequestKinds")
        != ["authorization", "credential", "decision", "external-action"]
        or terminal_protocol.get("independentReviewRequired") is not True
    ):
        issues.append("manifest-execution-metadata")
    if issues:
        return issues
    try:
        HUMANIZE_MAPPING_CONTRACT.validate_full_mapping(root, manifest_path)
    except Exception as error:
        issues.append(
            f"full-mapping-validation: {type(error).__name__}: {error}"
        )
    return issues


def inspect_mappings(
    design: dict[str, Any],
    registry: dict[str, Any],
    root: Path,
    design_path: Path,
) -> dict[str, dict[str, Any]]:
    """Verify mapping evidence without importing a harness or generated Python."""
    harnesses = validate_registry(registry)
    design_id = design.get("id")
    if not isinstance(design_id, str) or not HARNESS_ID.fullmatch(design_id):
        raise ValueError("design.id must be lower-case hyphen-case")
    root = root.resolve()
    design_lexical = design_path.absolute()
    if not design_lexical.is_relative_to(root):
        raise ValueError("design path must be a file inside the project root")
    lexical_relative = design_lexical.relative_to(root)
    cursor = root
    for part in lexical_relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("design path must not traverse a symlink")
    design_path = design_lexical.resolve()
    if not design_path.is_file() or not design_path.is_relative_to(root):
        raise ValueError("design path must be a file inside the project root")
    design_relative = design_path.relative_to(root).as_posix()
    if load_object(design_path) != design:
        raise ValueError("design object differs from the canonical design file")
    design_digest = sha256_file(design_path)
    checks: dict[str, dict[str, Any]] = {}
    for name, spec in harnesses.items():
        artifact_relative = expand_artifact_path(spec["artifact"], design_id)
        try:
            artifact = project_file(root, artifact_relative, f"{name}.artifact")
        except ValueError as error:
            checks[name] = {"status": "invalid", "issues": [str(error)]}
            continue
        if not artifact.is_file():
            checks[name] = {
                "status": "missing",
                "issues": ["mapping-artifact-missing"],
                "artifact": artifact_relative,
            }
            continue
        if name == "subagent-scheduler" or spec.get("skill") == "subagent-workflow":
            issues = _subagent_mapping_issues(
                artifact,
                design_relative=design_relative,
                design_digest=design_digest,
            )
            try:
                report = report_path(root, design_id, name)
            except ValueError as error:
                issues.append(str(error))
            else:
                issues.extend(
                    _report_issues(
                        report,
                        design_relative=design_relative,
                        design_digest=design_digest,
                        artifact_relative=artifact_relative,
                        artifact_digest=sha256_file(artifact),
                    )
                )
        elif name == "humanize" or spec.get("skill") == "humanize-workflow":
            issues = _humanize_mapping_issues(
                root,
                artifact,
                design_relative=design_relative,
                design_digest=design_digest,
            )
        else:
            issues = ["mapping-verifier-unsupported"]
        checks[name] = {
            "status": "invalid" if issues else "valid",
            "issues": sorted(set(issues)),
            "artifact": artifact_relative,
        }
    return checks


def mapped_artifacts(
    design: dict[str, Any],
    registry: dict[str, Any],
    root: Path,
    design_path: Path | None = None,
) -> set[str]:
    """Compatibility helper returning only verified mapping names."""
    design_id = design.get("id")
    if design_path is None:
        if not isinstance(design_id, str):
            raise ValueError("design.id must be a non-empty string")
        design_path = root / ".workflow/designs" / f"{design_id}.workflow.json"
    return {
        name
        for name, check in inspect_mappings(
            design, registry, root, design_path
        ).items()
        if check["status"] == "valid"
    }


def select(
    design: dict[str, Any],
    registry: dict[str, Any],
    requested: str = "auto",
    mapping_checks: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    required = required_capabilities(design)
    harnesses = validate_registry(registry)
    if requested != "auto" and requested not in harnesses:
        raise ValueError(f"unknown harness: {requested}")

    configured_default = registry["default"]
    preferred = configured_default if requested == "auto" else requested
    names = [requested] if requested != "auto" else sorted(harnesses)
    eligible: list[tuple[int, int, str, dict[str, str]]] = []
    rejected: dict[str, list[str]] = {}
    for name in names:
        spec = harnesses[name]
        if spec.get("enabled", True) is not True:
            rejected[name] = ["disabled"]
            continue
        if gap := governance_gap(spec, design):
            rejected[name] = [gap]
            continue
        decisions: dict[str, str] = {}
        missing: list[str] = []
        adapter_count = 0
        for capability in required:
            status = spec["capabilities"].get(capability)
            if status is None:
                missing.append(capability)
                continue
            supported, adapter_owned = classify(status)
            if not supported:
                missing.append(capability)
                continue
            decisions[capability] = status
            adapter_count += adapter_owned
        if missing:
            rejected[name] = missing
        else:
            preference_rank = 0 if name == preferred else 1
            eligible.append((preference_rank, adapter_count, name, decisions))

    if not eligible:
        reason = "; ".join(
            f"{name}: {','.join(missing)}" for name, missing in sorted(rejected.items())
        )
        raise ValueError(f"no compatible harness ({reason})")

    _, adapter_count, name, decisions = min(
        eligible, key=lambda item: (item[0], item[1], item[2])
    )
    if mapping_checks is not None:
        mapping_verification = mapping_checks.get(
            name, {"status": "missing", "issues": ["mapping-not-inspected"]}
        )
        mapping_exists = mapping_verification.get("status") == "valid"
    else:
        mapping_exists = False
        mapping_verification = {
            "status": "not-inspected",
            "issues": ["mapping-not-inspected"],
        }
    spec = harnesses[name]
    return {
        "designId": design.get("id"),
        "harness": name,
        "skill": spec.get("skill"),
        "artifact": spec.get("artifact"),
        "install": spec.get("install"),
        "commands": spec.get("commands", {}),
        "adapterOwnedGuarantees": adapter_count,
        "capabilities": decisions,
        "configuredDefault": configured_default,
        "defaultApplied": requested == "auto" and name == configured_default,
        "mappingExists": mapping_exists,
        "mappingRequired": not mapping_exists,
        "mappingVerification": mapping_verification,
        "rejected": rejected,
    }


def find_project_root(design_path: Path) -> Path:
    absolute = design_path.absolute()
    for parent in (absolute.parent, *absolute.parents):
        if (parent / ".workflow").is_dir():
            return parent
    raise ValueError("design is not under a project .workflow directory")


def bundled_registry_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "subagent-workflow"
        / "assets"
        / "project"
        / ".workflow"
        / "harnesses.json"
    )


def resolve_registry_path(
    root: Path, explicit: Path | None
) -> tuple[Path, str]:
    if explicit is not None:
        path = explicit if explicit.is_absolute() else root / explicit
        return path.absolute(), "explicit"
    override = root / ".workflow/harnesses.json"
    if override.exists() or override.is_symlink():
        return override.absolute(), "project"
    return bundled_registry_path().absolute(), "bundled"


def reject_symlinked_file(path: Path, label: str) -> None:
    absolute = path.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} must not traverse a symlink")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("design", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--harness", default="auto")
    args = parser.parse_args()
    try:
        design_path = args.design.absolute()
        root = find_project_root(design_path)
        registry_path, registry_source = resolve_registry_path(root, args.registry)
        reject_symlinked_file(design_path, "design path")
        reject_symlinked_file(registry_path, "registry path")
        design = load_object(design_path)
        registry = load_object(registry_path)
        validate_design_for_selection(design)
        validate_registry(registry)
        checks = inspect_mappings(design, registry, root, design_path)
        result = select(
            design,
            registry,
            requested=args.harness,
            mapping_checks=checks,
        )
        result["registrySource"] = registry_source
        result["registryPath"] = str(registry_path)
        result["projectRoot"] = str(root)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"HARNESS: incompatible: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
