import { createHash } from "node:crypto";

/**
 * Canonical `workflow-model-roles/v3` policy material for controller tests.
 *
 * The controller is required to enforce the same contract as
 * `update-platform-config/scripts/config-sync/validate_model_roles.py`, so every
 * controller fixture starts from a document that validator accepts and then
 * mutates exactly one field per attack case.
 */

export type Json = Record<string, any>;

function canonical(value: unknown): string {
	if (value === null) return "null";
	if (typeof value === "string" || typeof value === "boolean" || typeof value === "number") return JSON.stringify(value);
	if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
	if (value && typeof value === "object") {
		const object = value as Json;
		return `{${Object.keys(object).sort().map(key => `${JSON.stringify(key)}:${canonical(object[key])}`).join(",")}}`;
	}
	throw new Error(`canonical JSON rejects ${typeof value}`);
}

export function canonicalDigest(value: unknown): string {
	return `sha256:${createHash("sha256").update(canonical(value)).digest("hex")}`;
}

const IDENTITY_FIELDS = [
	"backend", "provider", "upstreamProviderFamily", "model", "modelFamily", "effort",
	"intelligenceClass", "dispatchStatus",
] as const;

export function resolvedRoleIdentities(roles: Json, roleNames: string[]): Json[] {
	return [...roleNames].sort().map(name => {
		const role = roles[name] ?? {};
		const identity: Json = { semanticRole: name };
		for (const field of IDENTITY_FIELDS) identity[field] = role[field] === undefined ? null : role[field];
		return identity;
	});
}

function role(
	backend: string,
	model: string,
	modelFamily: string,
	upstreamProviderFamily: string,
	effort: string | null,
	intelligenceClass: string,
	relativeCost: number,
	dispatchStatus: "active" | "catalog-candidate" = "active",
): Json {
	const provider = backend;
	return {
		backend,
		provider,
		upstreamProviderFamily,
		model,
		modelFamily,
		effort,
		...(dispatchStatus === "catalog-candidate" ? { requiredEffort: "highest-available" } : {}),
		dispatchStatus,
		intelligenceClass,
		relativeCost,
	};
}

function baseRoles(): Json {
	const luna = (effort: string, cls: string, cost: number) =>
		role("codex", "gpt-5.6-luna", "gpt-5.6-luna", "openai", effort, cls, cost);
	const sol = (effort: string, cls: string, cost: number) =>
		role("codex", "gpt-5.6-sol", "gpt-5.6-sol", "openai", effort, cls, cost);
	const astra = (effort: string, cls: string, cost: number) =>
		role("codex", "gpt-6-astra", "gpt-6-astra", "openai", effort, cls, cost);
	const sonnet = (cls: string, cost: number) =>
		role("cursor", "claude-sonnet-5", "claude-sonnet-5", "anthropic", "high", cls, cost);
	const opus = (effort: string, cls: string, cost: number) =>
		role("cursor", "claude-opus-5", "claude-opus-5", "anthropic", effort, cls, cost);
	const nativeOpus = (effort: string, cls: string, cost: number) => ({
		...role("claude", "claude-opus-5", "claude-opus-5", "anthropic", effort, cls, cost),
		provider: "anthropic",
	});
	return {
		smol: luna("low", "mechanical", 1),
		tiny: luna("low", "mechanical", 1),
		commit: luna("low", "mechanical", 1),
		research_collect: luna("max", "collection", 2),
		research_collect_anthropic: sonnet("collection", 2),
		research_analyze: luna("max", "analysis", 3),
		task: astra("max", "analysis", 7),
		frontier_execute: astra("max", "topIntelligence", 7),
		frontier_execute_xhigh: astra("xhigh", "topIntelligence", 6),
		default: astra("xhigh", "analysis", 6),
		vision: astra("xhigh", "analysis", 6),
		plan: astra("xhigh", "planning", 6),
		designer: astra("max", "planning", 7),
		slow: sol("xhigh", "planning", 6),
		anthropic_task: sonnet("analysis", 4),
		frontier_execute_anthropic: opus("max", "topIntelligence", 8),
		frontier_execute_anthropic_xhigh: opus("xhigh", "topIntelligence", 7),
		frontier_advice: { ...opus("max", "advice", 8), effortPolicy: "highest-available" },
		frontier_advice_fable: {
			...role("cursor", "claude-fable-5-1", "claude-fable-5-1", "anthropic", "max", "advice", 9),
			effortPolicy: "highest-available",
		},
		frontier_advice_astra: astra("max", "advice", 10),
		advisor: nativeOpus("max", "verification", 8),
		codex_advisor: astra("max", "verification", 7),
		diversity_glm: role("cursor", "glm-5.2-max", "glm-5.2", "zhipu", null, "diversity", 5, "catalog-candidate"),
		diversity_kimi: role("cursor", "kimi-k3-max", "kimi-k3", "moonshot", null, "diversity", 5, "catalog-candidate"),
		diversity_grok: {
			...role("cursor", "grok-4.6", "grok-4.6", "xai", "xhigh", "diversity", 5),
			effortPolicy: "highest-available",
		},
	};
}

function auditedModelIdentities(roles: Json): Json {
	const bindings = new Map<string, Json>();
	for (const spec of Object.values(roles) as Json[]) {
		const key = `${spec.backend}\u0000${spec.model}`;
		if (bindings.has(key)) continue;
		bindings.set(key, {
			backend: spec.backend,
			provider: spec.provider,
			upstreamProviderFamily: spec.upstreamProviderFamily,
			model: spec.model,
			modelFamily: spec.modelFamily,
		});
	}
	return {
		schema: "workflow-model-identity-map/v1",
		assurance: "static-family-binding-not-live-availability-or-authentication",
		bindings: [...bindings.values()],
	};
}

/** Rebuild the verified catalog receipt so it exactly covers the current active roles. */
export function refreshCatalogResolution(document: Json): Json {
	const roles = document.roles as Json;
	const activeRoles = Object.keys(roles).filter(name => roles[name].dispatchStatus === "active").sort();
	const activeBackends = [...new Set(activeRoles.map(name => roles[name].backend))].sort();
	document.catalogResolution = {
		schema: "workflow-model-catalog-resolution/v1",
		status: "verified",
		source: "authenticated-native-cli",
		verifiedBackends: activeBackends,
		verifiedRoles: activeRoles,
		evidenceReceipt: {
			schema: "workflow-model-catalog-evidence/v1",
			assurance: "caller-supplied-structural-evidence-not-live-authentication",
			backendCatalogSha256: Object.fromEntries(
				activeBackends.map(backend => [backend, `sha256:${createHash("sha256").update(backend).digest("hex")}`]),
			),
			verifiedRoleIdentitiesSha256: canonicalDigest(resolvedRoleIdentities(roles, activeRoles)),
		},
	};
	return document;
}

/** Rebuild the audited identity map so it exactly covers the current role models. */
export function refreshAuditedIdentities(document: Json): Json {
	document.auditedModelIdentities = auditedModelIdentities(document.roles as Json);
	return document;
}

export function canonicalModelPolicy(): Json {
	const roles = baseRoles();
	const document: Json = {
		schema: "workflow-model-roles/v3",
		auditedModelIdentities: auditedModelIdentities(roles),
		capabilityPolicy: {
			schema: "workflow-capability-policy/v1",
			liveResolution: {
				requiredAtInit: true,
				exactIdsAreRefreshableSnapshots: true,
				candidateAvailabilityIsNotFact: true,
				missing: "fail-closed",
				ambiguous: "fail-closed",
				unsupportedEffort: "fail-closed",
			},
			taskClasses: {
				topIntelligence: {
					preferredExecutorRole: "frontier_execute",
					qualifiedRoles: [
						"frontier_execute", "frontier_execute_xhigh",
						"frontier_execute_anthropic", "frontier_execute_anthropic_xhigh",
					],
					allowedEfforts: ["max", "xhigh"],
					preferredAdviceRole: "frontier_advice",
					qualifiedAdviceRoles: ["frontier_advice", "frontier_advice_fable", "frontier_advice_astra"],
				},
				collection: {
					qualifiedRoles: ["research_collect_anthropic", "research_collect"],
					routingGoal: "cost-sensitive-evidence-collection",
				},
				diversityReview: {
					roles: ["diversity_kimi", "diversity_glm", "diversity_grok"],
					requiredEffort: "highest-available",
					activeHarnesses: ["subagent-scheduler"],
					unsupportedHarnessBehavior: "ineligible-no-substitution",
				},
			},
			taskClassRoles: {
				mechanical: ["smol", "tiny", "commit"],
				collection: ["research_collect", "research_collect_anthropic"],
				analysis: ["research_analyze", "task", "default", "vision", "anthropic_task"],
				planning: ["plan", "designer", "slow"],
				topIntelligence: [
					"frontier_execute", "frontier_execute_xhigh",
					"frontier_execute_anthropic", "frontier_execute_anthropic_xhigh",
				],
				advice: ["frontier_advice", "frontier_advice_fable", "frontier_advice_astra"],
				verification: ["advisor", "codex_advisor"],
				diversityReview: ["diversity_glm", "diversity_kimi", "diversity_grok"],
			},
			reviewPairings: [
				{
					writer: { upstreamProviderFamily: "openai", modelFamily: "gpt-6-astra", efforts: ["max", "xhigh"] },
					reviewer: { role: "advisor", upstreamProviderFamily: "anthropic", modelFamily: "claude-opus-5", effort: "max" },
				},
				{
					writer: { upstreamProviderFamily: "anthropic", modelFamily: "claude-opus-5", efforts: ["max", "xhigh"] },
					 reviewer: { role: "codex_advisor", upstreamProviderFamily: "openai", modelFamily: "gpt-6-astra", effort: "max" },
				},
			],
			providerReviewerDefaults: {
				openai: { reviewerRole: "advisor", requiredEffort: "max" },
				anthropic: { reviewerRole: "codex_advisor", requiredEffort: "max" },
			},
			futureCandidates: [{
				upstreamProviderFamily: "deepseek",
				modelFamily: "deepseek",
				backend: "cursor",
				requiredEffort: "highest-available",
				activation: "live-catalog-model-and-highest-effort-required",
			}],
			excludedFamilies: [{ modelFamily: "composer-2.5", activation: "manual-policy-change-required" }],
		},
		reasoningPolicy: {
			schema: "workflow-reasoning-policy/v2",
			defaultPosture: "balanced",
			selectionRule: "lowest-cost-qualified-role",
			costMetric: "project-relative-routing-rank-not-dollar-price",
			effortScope: "global-dispatch-envelope",
			crossFamilyEffortComparable: false,
			maxAutomaticEscalations: 0,
			backendEffortLevels: {
				claude: ["low", "medium", "high", "xhigh", "max"],
				codex: ["low", "medium", "high", "xhigh", "max", "ultra"],
				cursor: ["none", "low", "medium", "high", "xhigh", "max"],
			},
			postures: {
				economy: {
					description: "Bounded mechanical, collection, analysis, and verification routes only.",
					allowedRoles: [
						"smol", "tiny", "commit", "research_collect", "research_collect_anthropic",
						"research_analyze", "anthropic_task", "advisor", "codex_advisor",
					],
					maxAutomaticEscalations: 0,
				},
				balanced: {
					description: "Astra Max main executor, Opus Max review, cost-qualified collection.",
					allowedRoles: [
						"smol", "tiny", "commit", "research_collect", "research_collect_anthropic",
						"research_analyze", "anthropic_task", "default", "vision", "plan", "slow",
						"task", "designer", "frontier_execute", "frontier_advice", "frontier_advice_astra", "advisor", "codex_advisor",
					],
					maxAutomaticEscalations: 0,
				},
				frontier: {
					description: "Verified Astra or Opus top-intelligence routes with exact cross-provider Max review.",
					allowedRoles: [
						"smol", "tiny", "commit", "research_collect", "research_collect_anthropic",
						"research_analyze", "anthropic_task", "default", "vision", "plan", "slow",
						"task", "designer", "frontier_execute", "frontier_execute_xhigh",
						"frontier_execute_anthropic", "frontier_execute_anthropic_xhigh",
						"frontier_advice", "frontier_advice_fable", "frontier_advice_astra", "diversity_grok", "advisor", "codex_advisor",
					],
					maxAutomaticEscalations: 0,
				},
			},
			intelligenceClasses: {
				mechanical: ["smol", "tiny", "commit"],
				collection: ["research_collect", "research_collect_anthropic"],
				analysis: ["research_analyze", "task", "default", "vision", "anthropic_task"],
				topIntelligence: [
					"frontier_execute", "frontier_execute_xhigh",
					"frontier_execute_anthropic", "frontier_execute_anthropic_xhigh",
				],
				planning: ["plan", "designer", "slow"],
				advice: ["frontier_advice", "frontier_advice_fable", "frontier_advice_astra"],
				verification: ["advisor", "codex_advisor"],
				diversity: ["diversity_glm", "diversity_kimi", "diversity_grok"],
			},
			rules: [
				"Choose the global posture once per issue or workflow and include it in every delegated brief.",
				"Every ordinary native launch resolves one explicit task class, posture, semantic role, model, and effort into a policy-bound receipt.",
			],
		},
		roles,
	};
	return refreshCatalogResolution(document);
}

/**
 * Native route map whose audited upstream families cover the canonical policy.
 * `bins` lets a fixture point codex/anthropic/cursor at distinct stub executables.
 */
export function canonicalNativeMap(bins: { codex: string; anthropic: string; cursor?: string }): Json {
	const provider = (bin: string, family: string, extra: Json = {}): Json => ({
		bin,
		nativeProviderFamily: family,
		args: ["{model}", "{prompt}"],
		readArgs: ["{model}", "{prompt}"],
		readOnlyArgs: ["{model}", "{prompt}"],
		schedulerArgs: ["{model}", "{prompt}"],
		effort: { values: ["low", "medium", "high", "xhigh", "max", "ultra"], args: ["--effort", "{effort}"] },
		...extra,
	});
	return {
		providers: {
			codex: provider(bins.codex, "codex"),
			anthropic: provider(bins.anthropic, "anthropic"),
			cursor: provider(bins.cursor ?? bins.codex, "cursor", {
				relayModelPrefixes: ["gpt-", "claude-", "glm-", "kimi-", "grok-", "deepseek-", "composer-"],
			}),
		},
		modelPrefixes: {
			"gpt-": "codex",
			"claude-": "anthropic",
			"glm-": "cursor",
			"kimi-": "cursor",
			"grok-": "cursor",
			"deepseek-": "cursor",
			"composer-": "cursor",
		},
		upstreamModelFamilies: {
			"gpt-5.6-sol": { upstreamProviderFamily: "openai", modelFamily: "gpt-5.6-sol", dispatchEligibility: "active" },
			"gpt-5.6-luna": { upstreamProviderFamily: "openai", modelFamily: "gpt-5.6-luna", dispatchEligibility: "active" },
			"gpt-6-astra": {
				upstreamProviderFamily: "openai", modelFamily: "gpt-6-astra", dispatchEligibility: "active",
			},
			"claude-opus-5": { upstreamProviderFamily: "anthropic", modelFamily: "claude-opus-5", dispatchEligibility: "active" },
			"claude-fable-5-1": { upstreamProviderFamily: "anthropic", modelFamily: "claude-fable-5-1", dispatchEligibility: "active" },
			"claude-sonnet-5": { upstreamProviderFamily: "anthropic", modelFamily: "claude-sonnet-5", dispatchEligibility: "active" },
			"glm-5.2": {
				upstreamProviderFamily: "zhipu", modelFamily: "glm-5.2", dispatchEligibility: "active",
				requiredEffortPolicy: "highest-available",
			},
			"kimi-k3": {
				upstreamProviderFamily: "moonshot", modelFamily: "kimi-k3", dispatchEligibility: "active",
				requiredEffortPolicy: "highest-available",
			},
			"grok-4.6": {
				upstreamProviderFamily: "xai", modelFamily: "grok-4.6", dispatchEligibility: "active",
				requiredEffortPolicy: "highest-available",
			},
			deepseek: {
				upstreamProviderFamily: "deepseek", modelFamily: "deepseek", dispatchEligibility: "future-candidate",
				requiredEffortPolicy: "highest-available",
			},
			"composer-2.5": { upstreamProviderFamily: "cursor", modelFamily: "composer-2.5", dispatchEligibility: "excluded" },
		},
	};
}
