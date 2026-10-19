#!/usr/bin/env python3
"""Validate semantic model policy and refreshable native role snapshots."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from project_paths import project_root


REPO = project_root()
DEFAULT_PATH = REPO / "config" / "workflows" / "models.json"
REQUIRED_ROLES = frozenset(
    {
        "default", "smol", "slow", "vision", "plan", "designer", "commit", "tiny", "task",
        "frontier_execute", "frontier_execute_xhigh", "frontier_execute_anthropic",
        "frontier_execute_anthropic_xhigh", "frontier_advice", "frontier_advice_fable",
        "frontier_advice_astra",
        "research_collect", "research_collect_anthropic", "advisor", "codex_advisor",
        "diversity_glm", "diversity_kimi", "diversity_grok",
    }
)
POSTURES = frozenset({"economy", "balanced", "frontier"})
BACKEND_PROVIDERS = {"codex": "codex", "claude": "anthropic", "cursor": "cursor"}
DISPATCH_STATUSES = frozenset({"active", "catalog-candidate"})
RESOLUTION_STATUSES = frozenset({"bootstrap-unverified", "verified"})
TOP_EFFORTS = frozenset({"max", "xhigh"})
HIGHEST_AVAILABLE = "highest-available"
CATALOG_EVIDENCE_ASSURANCE = "caller-supplied-structural-evidence-not-live-authentication"
IDENTITY_MAP_ASSURANCE = "static-family-binding-not-live-availability-or-authentication"


class ValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def resolved_role_identities(roles: dict[str, Any], role_names: list[str]) -> list[dict[str, Any]]:
    fields = (
        "backend", "provider", "upstreamProviderFamily", "model", "modelFamily", "effort",
        "intelligenceClass", "dispatchStatus",
    )
    return [
        {"semanticRole": name, **{field: roles[name].get(field) for field in fields}}
        for name in sorted(role_names)
    ]


def _validate_identity_map(document: dict[str, Any], roles: dict[str, Any]) -> None:
    identity_map = document.get("auditedModelIdentities")
    require(isinstance(identity_map, dict), "auditedModelIdentities must be an object")
    require(
        identity_map.get("schema") == "workflow-model-identity-map/v1",
        "unsupported audited model identity schema",
    )
    require(
        identity_map.get("assurance") == IDENTITY_MAP_ASSURANCE,
        "audited model identity assurance must not claim live availability or authentication",
    )
    bindings = identity_map.get("bindings")
    require(isinstance(bindings, list) and bool(bindings), "auditedModelIdentities.bindings must be non-empty")
    fields = ("backend", "provider", "upstreamProviderFamily", "model", "modelFamily")
    observed: dict[tuple[str, str], tuple[str, str, str]] = {}
    for binding in bindings:
        require(isinstance(binding, dict), "audited model identity bindings must be objects")
        require(set(binding) == set(fields), "audited model identity binding fields are invalid")
        require(all(nonempty_string(binding.get(field)) for field in fields), "audited model identity fields must be non-empty")
        key = (binding["backend"], binding["model"])
        require(key not in observed, f"duplicate audited model identity for {key!r}")
        observed[key] = (
            binding["provider"], binding["upstreamProviderFamily"], binding["modelFamily"]
        )

    used_keys: set[tuple[str, str]] = set()
    for name, role in roles.items():
        key = (role.get("backend"), role.get("model"))
        expected = observed.get(key)
        actual = (
            role.get("provider"), role.get("upstreamProviderFamily"), role.get("modelFamily")
        )
        require(expected is not None, f"role {name} has no audited model identity")
        require(actual == expected, f"role {name} does not match its audited model identity")
        used_keys.add(key)
    require(set(observed) == used_keys, "audited model identities must exactly cover configured role models")


def _validate_catalog_resolution(document: dict[str, Any], roles: dict[str, Any]) -> None:
    resolution = document.get("catalogResolution")
    require(isinstance(resolution, dict), "catalogResolution must be an object")
    require(
        resolution.get("schema") == "workflow-model-catalog-resolution/v1",
        "unsupported catalog resolution schema",
    )
    status = resolution.get("status")
    require(status in RESOLUTION_STATUSES, "catalogResolution.status is invalid")
    require(
        resolution.get("source") == "authenticated-native-cli",
        "catalog resolution must use authenticated-native-cli",
    )
    for field in ("verifiedBackends", "verifiedRoles"):
        values = resolution.get(field)
        require(
            isinstance(values, list)
            and len(values) == len(set(values))
            and all(nonempty_string(value) for value in values),
            f"catalogResolution.{field} must be a unique string list",
        )
    if status == "bootstrap-unverified":
        require(
            resolution["verifiedBackends"] == [] and resolution["verifiedRoles"] == [],
            "bootstrap-unverified cannot claim verified backends or roles",
        )
        require("evidenceReceipt" not in resolution, "bootstrap-unverified cannot carry a catalog evidenceReceipt")
        return

    active_roles = {name for name, role in roles.items() if role.get("dispatchStatus") == "active"}
    active_backends = {roles[name].get("backend") for name in active_roles}
    require(
        set(resolution["verifiedRoles"]) == active_roles,
        "verifiedRoles must exactly cover every active role",
    )
    require(
        set(resolution["verifiedBackends"]) == active_backends,
        "verifiedBackends must exactly cover every active backend",
    )
    receipt = resolution.get("evidenceReceipt")
    require(isinstance(receipt, dict), "verified catalogResolution requires an evidenceReceipt")
    require(
        receipt.get("schema") == "workflow-model-catalog-evidence/v1",
        "unsupported catalog evidenceReceipt schema",
    )
    require(
        receipt.get("assurance") == CATALOG_EVIDENCE_ASSURANCE,
        "catalog evidenceReceipt must state that it is not live authentication",
    )
    backend_digests = receipt.get("backendCatalogSha256")
    require(
        isinstance(backend_digests, dict) and set(backend_digests) == active_backends,
        "catalog evidenceReceipt must bind every active backend exactly",
    )
    for backend, digest in backend_digests.items():
        require(
            isinstance(digest, str) and len(digest) == 71 and digest.startswith("sha256:")
            and all(char in "0123456789abcdef" for char in digest[7:]),
            f"catalog evidenceReceipt digest for {backend!r} must be sha256:<64hex>",
        )
    expected_role_digest = canonical_sha256(
        resolved_role_identities(roles, resolution["verifiedRoles"])
    )
    require(
        receipt.get("verifiedRoleIdentitiesSha256") == expected_role_digest,
        "catalog evidenceReceipt does not bind the verified role identities",
    )


def _validate_capability_policy(policy: Any, roles: dict[str, Any]) -> None:
    require(isinstance(policy, dict), "capabilityPolicy must be an object")
    require(policy.get("schema") == "workflow-capability-policy/v1", "unsupported capability policy schema")

    live = policy.get("liveResolution")
    require(isinstance(live, dict), "capabilityPolicy.liveResolution must be an object")
    for flag in ("requiredAtInit", "exactIdsAreRefreshableSnapshots", "candidateAvailabilityIsNotFact"):
        require(live.get(flag) is True, f"liveResolution.{flag} must be true")
    for failure in ("missing", "ambiguous", "unsupportedEffort"):
        require(live.get(failure) == "fail-closed", f"liveResolution.{failure} must fail closed")

    task_classes = policy.get("taskClasses")
    require(isinstance(task_classes, dict), "capabilityPolicy.taskClasses must be an object")
    top = task_classes.get("topIntelligence")
    require(isinstance(top, dict), "topIntelligence task class is required")
    require(top.get("preferredExecutorRole") == "frontier_execute", "Astra frontier_execute must be preferred")
    qualified_top = top.get("qualifiedRoles")
    require(
        isinstance(qualified_top, list)
        and set(qualified_top) == {
            "frontier_execute", "frontier_execute_xhigh",
            "frontier_execute_anthropic", "frontier_execute_anthropic_xhigh",
        },
        "topIntelligence must name the exact Astra/Opus qualified executor roles",
    )
    require(set(top.get("allowedEfforts", [])) == TOP_EFFORTS, "topIntelligence allows only max and xhigh")
    require(top.get("preferredAdviceRole") == "frontier_advice", "Opus frontier_advice must be preferred")
    require(
        top.get("qualifiedAdviceRoles") == ["frontier_advice", "frontier_advice_fable", "frontier_advice_astra"],
        "qualified advice must name exact active Opus, Fable 5.1, and Astra roles",
    )

    collection = task_classes.get("collection")
    require(isinstance(collection, dict), "collection task class is required")
    require(
        set(collection.get("qualifiedRoles", [])) == {"research_collect", "research_collect_anthropic"},
        "collection must use the Luna and Sonnet collection roles",
    )
    task_class_roles = {
        "mechanical": {"smol", "tiny", "commit"},
        "collection": {"research_collect", "research_collect_anthropic"},
        "analysis": {"research_analyze", "task", "default", "vision", "anthropic_task"},
        "planning": {"plan", "designer", "slow"},
        "topIntelligence": set(qualified_top),
        "advice": set(top.get("qualifiedAdviceRoles", [])) | set(top.get("candidateAdviceRoles", [])),
        "verification": {"advisor", "codex_advisor"},
        "diversityReview": {"diversity_kimi", "diversity_glm", "diversity_grok"},
    }
    declared_task_classes = policy.get("taskClassRoles")
    require(isinstance(declared_task_classes, dict), "capabilityPolicy.taskClassRoles must be an object")
    require(set(declared_task_classes) == set(task_class_roles), "capabilityPolicy.taskClassRoles is incomplete")
    for class_name, expected_roles in task_class_roles.items():
        require(
            set(declared_task_classes.get(class_name, [])) == expected_roles,
            f"taskClassRoles.{class_name} is inconsistent",
        )
    diversity = task_classes.get("diversityReview")
    require(isinstance(diversity, dict), "diversityReview task class is required")
    diversity_roles = {"diversity_kimi", "diversity_glm", "diversity_grok"}
    require(set(diversity.get("roles", [])) == diversity_roles, "diversityReview must name Kimi, GLM, and Grok")
    require(
        diversity.get("requiredEffort") == HIGHEST_AVAILABLE,
        "diversity review must require the highest available effort",
    )
    require(diversity.get("activeHarnesses") == ["subagent-scheduler"], "Cursor diversity is built-in-only")
    require(
        diversity.get("unsupportedHarnessBehavior") == "ineligible-no-substitution",
        "unsupported diversity harnesses must fail closed without substitution",
    )

    expected_pairs = {
        ("openai", "gpt-6-astra"): ("advisor", "anthropic", "claude-opus-5", "max"),
        ("anthropic", "claude-opus-5"): ("codex_advisor", "openai", "gpt-6-astra", "max"),
    }
    pairings = policy.get("reviewPairings")
    require(isinstance(pairings, list) and len(pairings) == 2, "exactly two reviewPairings are required")
    observed: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    for pair in pairings:
        require(isinstance(pair, dict), "reviewPairings entries must be objects")
        writer, reviewer = pair.get("writer"), pair.get("reviewer")
        require(isinstance(writer, dict) and isinstance(reviewer, dict), "review pairing needs writer and reviewer")
        require(set(writer.get("efforts", [])) == TOP_EFFORTS, "review pairing writer efforts must be max and xhigh")
        key = (writer.get("upstreamProviderFamily"), writer.get("modelFamily"))
        value = (
            reviewer.get("role"), reviewer.get("upstreamProviderFamily"),
            reviewer.get("modelFamily"), reviewer.get("effort"),
        )
        require(all(nonempty_string(item) for item in (*key, *value)), "review pairing fields must be non-empty")
        require(key not in observed, "duplicate review writer pairing")
        observed[key] = value
        reviewer_role = roles.get(value[0])
        require(isinstance(reviewer_role, dict), f"review pairing references unknown role {value[0]!r}")
        require(
            reviewer_role.get("upstreamProviderFamily") == value[1]
            and reviewer_role.get("modelFamily") == value[2]
            and reviewer_role.get("effort") == value[3]
            and reviewer_role.get("dispatchStatus") == "active",
            f"review pairing role {value[0]!r} does not match its required family and effort",
        )
    require(observed == expected_pairs, "reviewPairings must encode exact Astra-to-Opus and Opus-to-Astra Max review")

    defaults = policy.get("providerReviewerDefaults")
    require(isinstance(defaults, dict) and set(defaults) == {"openai", "anthropic"}, "reviewer defaults are incomplete")
    for provider, (role_name, _, _, effort) in {
        "openai": expected_pairs[("openai", "gpt-6-astra")],
        "anthropic": expected_pairs[("anthropic", "claude-opus-5")],
    }.items():
        require(
            defaults[provider] == {"reviewerRole": role_name, "requiredEffort": effort},
            f"providerReviewerDefaults.{provider} is inconsistent",
        )

    future = policy.get("futureCandidates")
    require(isinstance(future, list) and len(future) == 1, "futureCandidates must contain only DeepSeek")
    require(
        future[0] == {
            "upstreamProviderFamily": "deepseek", "modelFamily": "deepseek", "backend": "cursor",
            "requiredEffort": HIGHEST_AVAILABLE,
            "activation": "live-catalog-model-and-highest-effort-required",
        },
        "DeepSeek must remain a live-catalog highest-effort candidate",
    )
    excluded = policy.get("excludedFamilies")
    require(
        excluded == [{"modelFamily": "composer-2.5", "activation": "manual-policy-change-required"}],
        "Composer 2.5 must remain explicitly excluded",
    )


def validate(document: dict[str, Any]) -> None:
    require(document.get("schema") == "workflow-model-roles/v3", "unsupported role schema")
    roles = document.get("roles")
    require(isinstance(roles, dict) and bool(roles), "roles must be a non-empty object")
    missing = sorted(REQUIRED_ROLES - set(roles))
    require(not missing, f"missing required roles: {', '.join(missing)}")

    policy = document.get("reasoningPolicy")
    require(isinstance(policy, dict), "reasoningPolicy must be an object")
    require(policy.get("schema") == "workflow-reasoning-policy/v2", "unsupported reasoning policy schema")
    require(policy.get("selectionRule") == "lowest-cost-qualified-role", "unsupported role selection rule")
    require(
        policy.get("costMetric") == "project-relative-routing-rank-not-dollar-price",
        "reasoning policy must distinguish routing rank from dollar price",
    )
    require(policy.get("effortScope") == "global-dispatch-envelope", "effortScope must be global-dispatch-envelope")
    require(policy.get("crossFamilyEffortComparable") is False, "cross-family effort must not be marked comparable")
    require(
        type(policy.get("maxAutomaticEscalations")) is int
        and 0 <= policy["maxAutomaticEscalations"] <= 2,
        "maxAutomaticEscalations must be an integer in [0, 2]",
    )

    backend_efforts = policy.get("backendEffortLevels")
    require(isinstance(backend_efforts, dict) and bool(backend_efforts), "backendEffortLevels must be a non-empty object")
    for backend, levels in backend_efforts.items():
        require(nonempty_string(backend), "backendEffortLevels keys must be non-empty strings")
        require(
            isinstance(levels, list)
            and len(levels) == len(set(levels))
            and all(nonempty_string(level) for level in levels),
            f"backendEffortLevels.{backend} must be a unique string list",
        )
    require(
        backend_efforts.get("claude") == ["low", "medium", "high", "xhigh", "max"],
        "Claude backend effort levels must be low, medium, high, xhigh, max",
    )

    postures = policy.get("postures")
    require(isinstance(postures, dict) and set(postures) == POSTURES, "postures must be economy, balanced, and frontier")
    require(policy.get("defaultPosture") in POSTURES, "defaultPosture must name a declared posture")
    for name, spec in postures.items():
        require(isinstance(spec, dict), f"posture {name} must be an object")
        require(nonempty_string(spec.get("description")), f"posture {name} needs a description")
        allowed_roles = spec.get("allowedRoles")
        require(
            isinstance(allowed_roles, list) and bool(allowed_roles)
            and len(allowed_roles) == len(set(allowed_roles)),
            f"posture {name} allowedRoles must be a non-empty unique role list",
        )
        for role_name in allowed_roles:
            require(role_name in roles, f"posture {name} references unknown allowed role {role_name!r}")
            require(
                roles[role_name].get("dispatchStatus") == "active",
                f"posture {name} cannot allow non-active role {role_name!r}",
            )
        require(
            type(spec.get("maxAutomaticEscalations")) is int
            and 0 <= spec["maxAutomaticEscalations"] <= policy["maxAutomaticEscalations"],
            f"posture {name} maxAutomaticEscalations must be within the global cap",
        )
    require(
        set(postures["economy"]["allowedRoles"]) < set(postures["balanced"]["allowedRoles"])
        < set(postures["frontier"]["allowedRoles"]),
        "posture allowedRoles must form strict economy < balanced < frontier ceilings",
    )

    classes = policy.get("intelligenceClasses")
    require(isinstance(classes, dict) and bool(classes), "intelligenceClasses must be a non-empty object")
    role_classes: dict[str, str] = {}
    for class_name, role_names in classes.items():
        require(nonempty_string(class_name), "intelligence class names must be non-empty strings")
        require(
            isinstance(role_names, list) and bool(role_names) and len(role_names) == len(set(role_names)),
            f"intelligence class {class_name} must contain unique role names",
        )
        for role_name in role_names:
            require(role_name in roles, f"intelligence class {class_name} references unknown role {role_name!r}")
            require(role_name not in role_classes, f"role {role_name} belongs to multiple intelligence classes")
            role_classes[role_name] = class_name

    for name, spec in roles.items():
        require(isinstance(spec, dict), f"role {name} must be an object")
        for field in ("backend", "provider", "upstreamProviderFamily", "model", "modelFamily", "intelligenceClass"):
            require(nonempty_string(spec.get(field)), f"role {name} needs {field}")
        require(spec.get("dispatchStatus") in DISPATCH_STATUSES, f"role {name} has invalid dispatchStatus")
        require(spec["intelligenceClass"] in classes, f"role {name} has unknown intelligenceClass")
        require(role_classes.get(name) == spec["intelligenceClass"], f"role {name} class mapping is inconsistent")
        backend = spec["backend"]
        require(backend in backend_efforts, f"role {name} backend {backend!r} has no effort catalog")
        require(backend in BACKEND_PROVIDERS, f"role {name} uses unsupported audited backend {backend!r}")
        require(
            spec["provider"] == BACKEND_PROVIDERS.get(backend),
            f"role {name} provider {spec['provider']!r} does not match backend {backend!r}",
        )
        effort = spec.get("effort")
        effort_policy = spec.get("effortPolicy")
        allowed_efforts = backend_efforts[backend]
        if spec["dispatchStatus"] == "active":
            require(bool(allowed_efforts), f"active role {name} backend {backend} exposes no verified effort settings")
            require(effort in allowed_efforts, f"role {name} effort {effort!r} is invalid for backend {backend}")
            require("requiredEffort" not in spec, f"active role {name} must pin effort directly")
            if spec["intelligenceClass"] in {"advice", "diversity"}:
                if backend == "cursor":
                    require(
                        effort_policy == HIGHEST_AVAILABLE,
                        f"role {name} has an invalid highest-available effort policy",
                    )
                else:
                    require(
                        spec["intelligenceClass"] == "advice" and backend == "codex" and effort_policy is None,
                        f"role {name} has an invalid effort policy",
                    )
            else:
                require(
                    effort_policy is None,
                    f"role {name} may not declare a highest-available effort policy",
                )
        else:
            require(effort is None, f"catalog candidate role {name} must not pretend effort is pinned")
            require(
                spec.get("requiredEffort") == HIGHEST_AVAILABLE,
                f"catalog candidate role {name} must require highest-available effort",
            )
            require(effort_policy is None, f"catalog candidate role {name} cannot claim a resolved effort policy")
        require(
            type(spec.get("relativeCost")) is int and spec["relativeCost"] >= 1,
            f"role {name} needs a positive integer relativeCost",
        )

    _validate_identity_map(document, roles)

    require(
        roles["frontier_execute"].get("modelFamily") == "gpt-6-astra"
        and roles["frontier_execute"].get("effort") == "max",
        "frontier_execute must be GPT-6 Astra Max",
    )
    require(
        roles["frontier_execute_xhigh"].get("modelFamily") == "gpt-6-astra"
        and roles["frontier_execute_xhigh"].get("effort") == "xhigh"
        and roles["frontier_execute_xhigh"].get("intelligenceClass") == "topIntelligence",
        "frontier_execute_xhigh must be a GPT-6 Astra X-High top-intelligence role",
    )
    require(
        roles["frontier_execute_anthropic"].get("modelFamily") == "claude-opus-5"
        and roles["frontier_execute_anthropic"].get("effort") == "max",
        "frontier_execute_anthropic must be Opus 5 Max",
    )
    require(
        roles["frontier_execute_anthropic_xhigh"].get("modelFamily") == "claude-opus-5"
        and roles["frontier_execute_anthropic_xhigh"].get("effort") == "xhigh"
        and roles["frontier_execute_anthropic_xhigh"].get("intelligenceClass") == "topIntelligence",
        "frontier_execute_anthropic_xhigh must be an Opus 5 X-High top-intelligence role",
    )
    require(
        roles["frontier_advice"].get("modelFamily") == "claude-opus-5"
        and roles["frontier_advice"].get("effortPolicy") == HIGHEST_AVAILABLE,
        "frontier_advice must be highest-available Opus 5",
    )
    require(
        roles["frontier_advice_fable"].get("modelFamily") == "claude-fable-5-1"
        and roles["frontier_advice_fable"].get("effortPolicy") == HIGHEST_AVAILABLE,
        "frontier_advice_fable must be highest-available Fable 5.1",
    )
    require(
        roles["frontier_advice_astra"].get("modelFamily") == "gpt-6-astra"
        and roles["frontier_advice_astra"].get("dispatchStatus") == "active"
        and roles["frontier_advice_astra"].get("effort") == "max",
        "frontier_advice_astra must be active GPT-6 Astra Max",
    )
    require(
        roles["diversity_grok"].get("modelFamily") == "grok-4.6"
        and roles["diversity_grok"].get("dispatchStatus") == "active"
        and roles["diversity_grok"].get("effortPolicy") == HIGHEST_AVAILABLE,
        "diversity_grok must be active at the highest available Grok 4.6 effort",
    )
    require(
        roles["research_collect"].get("modelFamily") == "gpt-5.6-luna"
        and roles["research_collect"].get("effort") == "max",
        "research_collect must be Luna Max",
    )
    require(
        roles["research_collect_anthropic"].get("modelFamily") == "claude-sonnet-5"
        and roles["research_collect_anthropic"].get("effort") == "high",
        "research_collect_anthropic must be Sonnet 5 High",
    )
    require(
        roles["advisor"].get("backend") == "claude"
        and roles["advisor"].get("provider") == "anthropic"
        and roles["advisor"].get("upstreamProviderFamily") == "anthropic"
        and roles["advisor"].get("model") == "claude-opus-5"
        and roles["advisor"].get("modelFamily") == "claude-opus-5"
        and roles["advisor"].get("effort") == "max",
        "advisor must be native Claude Code with Anthropic Opus 5 Max",
    )

    writer_classes = {"mechanical", "collection", "analysis", "topIntelligence", "planning"}
    writer_upstreams = {
        roles[role_name]["upstreamProviderFamily"]
        for class_name in writer_classes
        for role_name in classes.get(class_name, [])
        if roles[role_name]["dispatchStatus"] == "active"
    }
    reviewer_upstreams = {
        roles[role_name]["upstreamProviderFamily"]
        for role_name in classes.get("verification", [])
        if roles[role_name]["dispatchStatus"] == "active"
    }
    for provider in writer_upstreams:
        require(
            any(reviewer != provider for reviewer in reviewer_upstreams),
            f"no cross-provider verification role exists for writer upstream {provider!r}",
        )

    rules = policy.get("rules")
    require(
        isinstance(rules, list) and bool(rules) and all(nonempty_string(item) for item in rules),
        "reasoning policy rules must be a non-empty string list",
    )
    _validate_capability_policy(document.get("capabilityPolicy"), roles)
    _validate_catalog_resolution(document, roles)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    path = Path(args[0]).resolve() if args else DEFAULT_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        require(isinstance(raw, dict), "role document must be a JSON object")
        validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(f"[validate-model-roles] {path}: {error}", file=sys.stderr)
        return 2
    print(f"[validate-model-roles] valid: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
