import { describe, expect, test } from "bun:test";
import { validateNativeRoutes } from "../../scripts/controller/run";
import {
	canonicalModelPolicy, canonicalNativeMap, refreshAuditedIdentities, refreshCatalogResolution,
} from "./fixtures/model_policy";

/** Distinct audited harnesses so execution independence never depends on the model name alone. */
function nativeMap() {
	return canonicalNativeMap({ codex: "/usr/bin/node", anthropic: "/usr/bin/env", cursor: "/bin/sh" });
}

function roles() {
	return canonicalModelPolicy();
}

/** Re-seal the policy after a route mutation so route checks are never masked by policy drift. */
function reseal(document: ReturnType<typeof roles>) {
	refreshAuditedIdentities(document);
	refreshCatalogResolution(document);
	return document;
}

function design(writerRole = "task", reviewerRole = "advisor") {
	return {
		schema: "workflow-design/v1",
		id: "model-policy",
		entry: "writer",
		governance: { reviewPolicy: "machine-gated", malformedDecision: "unresolved" },
		requiredCapabilities: ["machine-gated-review"],
		nodes: [
			{ id: "writer", kind: "agent", role: writerRole, workspaceAccess: "write", timeoutSeconds: 10 },
			{
				id: "review", kind: "decision", role: reviewerRole, workspaceAccess: "read", timeoutSeconds: 10,
				outcomes: ["PASS", "BLOCK"], reviews: ["writer"],
			},
			{ id: "success", kind: "terminal", outcome: "success" },
			{ id: "unresolved", kind: "terminal", outcome: "unresolved" },
		],
		transitions: [
			{ from: "writer", to: "review" },
			{ from: "review", to: "success", when: { field: "review.verdict", equals: "PASS" } },
			{ from: "review", to: "unresolved", when: { field: "review.verdict", equals: "BLOCK" } },
		],
		terminals: { success: "success", unresolved: "unresolved" },
	};
}

const mapping = { reasoningPosture: "frontier", scheduler: { role: "task", maxCalls: 0 } };

function validate(document: unknown, map: unknown, workflow = design()) {
	return () => validateNativeRoutes(workflow as never, mapping as never, document as never, map as never);
}

describe("native upstream-model identity policy", () => {
	test("accepts the exact Astra Max -> Opus Max machine-review pairing", () => {
		expect(validate(roles(), nativeMap())).not.toThrow();
	});

	test("uses the native Claude backend for the canonical Opus reviewer without relabeling Cursor", () => {
		const document = roles();
		expect(document.roles.advisor.backend).toBe("claude");
		expect(document.roles.advisor.provider).toBe("anthropic");
		expect(document.roles.advisor.upstreamProviderFamily).toBe("anthropic");
		expect(document.roles.task.backend).toBe("codex");
		expect(document.roles.task.upstreamProviderFamily).toBe("openai");
		expect(validate(reseal(document), nativeMap())).not.toThrow();
	});

	test("rejects relabeling the canonical advisor as Cursor-hosted Opus", () => {
		const document = roles();
		document.roles.advisor.backend = "cursor";
		document.roles.advisor.provider = "cursor";
		expect(validate(reseal(document), nativeMap()))
			.toThrow("advisor must be native Claude Code with Anthropic Opus 5 Max");
	});

	test("sharing one executable between Codex and Claude collapses execution independence", () => {
		const document = roles();
		const map = nativeMap();
		map.providers.codex.bin = map.providers.anthropic.bin;
		expect(validate(reseal(document), map))
			.toThrow("resolve to the same native execution provider anthropic");
	});

	test("rejects a role identity that contradicts the audited model prefix", () => {
		const document = roles();
		// A model id only this role uses, so the contradiction cannot be blamed on a shared binding.
		document.roles.task.model = "gpt-5.6-sol-preview";
		document.roles.task.upstreamProviderFamily = "anthropic";
		expect(validate(reseal(document), nativeMap()))
			.toThrow("role upstream provider anthropic disagrees with audited model mapping openai");
	});

	test("requires the exact paired reviewer role and Max effort", () => {
		expect(validate(roles(), nativeMap(), design("frontier_execute_anthropic", "research_collect")))
			.toThrow("must use role codex_advisor openai/gpt-6-astra effort max");
	});

	test("requires explicit highest-available policy for active Cursor diversity", () => {
		const accepted = roles();
		expect(accepted.roles.diversity_grok.effort).toBe("xhigh");
		expect(accepted.roles.diversity_grok.effortPolicy).toBe("highest-available");
		expect(validate(reseal(accepted), nativeMap())).not.toThrow();

		const missingPolicy = roles();
		delete missingPolicy.roles.diversity_grok.effortPolicy;
		expect(validate(reseal(missingPolicy), nativeMap()))
			.toThrow("invalid highest-available effort policy");
	});

	test("pins the Claude backend effort list like the Python validator", () => {
		const document = roles();
		document.reasoningPolicy.backendEffortLevels.claude.push("ultra");
		expect(validate(document, nativeMap())).toThrow("Claude backend effort levels");
	});

	test("pins Fable 5.1 and rejects the stale Fable 5 family after resealing", () => {
		const accepted = roles();
		expect(accepted.roles.frontier_advice_fable.modelFamily).toBe("claude-fable-5-1");
		expect(validate(reseal(accepted), nativeMap())).not.toThrow();

		const stale = roles();
		stale.roles.frontier_advice_fable.model = "claude-fable-5";
		stale.roles.frontier_advice_fable.modelFamily = "claude-fable-5";
		expect(validate(reseal(stale), nativeMap())).toThrow("Fable 5.1");
	});

	test("family policy rejects an extra analysis role that omits highest-available effort", () => {
		for (const [model, modelFamily, upstreamProviderFamily] of [
			["kimi-k3-xhigh", "kimi-k3", "moonshot"],
			["glm-5.2-xhigh", "glm-5.2", "zhipu"],
			["grok-4.6", "grok-4.6", "xai"],
			["deepseek-r1", "deepseek", "deepseek"],
		] as const) {
			const roleName = `untrusted_${modelFamily.replaceAll(".", "_").replaceAll("-", "_")}_analysis`;
			const document = roles();
			document.roles[roleName] = {
				backend: "cursor",
				provider: "cursor",
				upstreamProviderFamily,
				model,
				modelFamily,
				effort: "xhigh",
				dispatchStatus: "active",
				intelligenceClass: "analysis",
				relativeCost: 1,
			};
			document.reasoningPolicy.intelligenceClasses.analysis.push(roleName);
			document.reasoningPolicy.postures.frontier.allowedRoles.push(roleName);

			const map = nativeMap();
			if (modelFamily === "deepseek") {
				map.upstreamModelFamilies.deepseek.dispatchEligibility = "active";
			}
			expect(validate(reseal(document), map, design(roleName)))
				.toThrow(`model family ${modelFamily} requires effort policy highest-available`);
		}
	});

	test("retains literal effort requirements for exact-effort identities", () => {
		const map = nativeMap();
		delete map.upstreamModelFamilies["grok-4.6"].requiredEffortPolicy;
		map.upstreamModelFamilies["grok-4.6"].requiredEffort = "max";
		expect(validate(roles(), map, design("diversity_grok")))
			.toThrow("model family grok-4.6 requires effort max");
	});

	test("fails closed when a v3 role has no unique audited upstream family", () => {
		const document = roles();
		document.roles.task.model = "gpt-future-unknown";
		document.roles.task.modelFamily = "gpt-future-unknown";
		expect(validate(reseal(document), nativeMap())).toThrow("cannot derive an audited upstream model family");
	});

	test("does not dispatch remaining catalog candidates, future DeepSeek, or excluded Composer", () => {
		const astra = roles().roles.frontier_advice_astra;
		expect(astra).toMatchObject({ backend: "codex", model: "gpt-6-astra", modelFamily: "gpt-6-astra", dispatchStatus: "active", effort: "max" });
		expect(validate(roles(), nativeMap(), design("diversity_glm"))).toThrow("catalog candidate");
		expect(validate(roles(), nativeMap(), design("frontier_advice_astra"))).not.toThrow();
		for (const [model, family, provider, message] of [
			["deepseek-r1", "deepseek", "deepseek", "future-only"],
			["composer-2.5", "composer-2.5", "cursor", "excluded"],
		] as const) {
			const document = roles();
			document.roles.diversity_glm = {
				...document.roles.diversity_glm,
				model, modelFamily: family, upstreamProviderFamily: provider,
				dispatchStatus: "active", effort: "max", effortPolicy: "highest-available",
			};
			delete document.roles.diversity_glm.requiredEffort;
			expect(validate(reseal(document), nativeMap(), design("diversity_glm"))).toThrow(message);
		}
	});

	test("treats the persisted catalog resolution as audit data that neither grants nor vetoes dispatch", () => {
		// Static route validation is structural only. A bootstrap-unverified snapshot is
		// therefore not a veto — a fresh authenticated resolution can still authorize the
		// role — and, symmetrically, a persisted verified snapshot is not a grant. Live
		// authority comes solely from canonicalLaunchReceipt.
		const bootstrap = roles();
		bootstrap.catalogResolution = {
			schema: "workflow-model-catalog-resolution/v1",
			status: "bootstrap-unverified",
			source: "authenticated-native-cli",
			verifiedBackends: [],
			verifiedRoles: [],
		};
		expect(validate(bootstrap, nativeMap())).not.toThrow();
	});

	test("rejects a bootstrap snapshot that claims verified coverage it cannot have", () => {
		for (const [mutate, message] of [
			[(document: Record<string, unknown>) => {
				(document.catalogResolution as Record<string, unknown>).verifiedRoles = ["task"];
			}, "bootstrap-unverified cannot claim verified backends or roles"],
			[(document: Record<string, unknown>) => {
				(document.catalogResolution as Record<string, unknown>).evidenceReceipt = {
					schema: "workflow-model-catalog-evidence/v1",
				};
			}, "bootstrap-unverified cannot carry a catalog evidenceReceipt"],
		] as const) {
			const document = roles();
			document.catalogResolution = {
				schema: "workflow-model-catalog-resolution/v1",
				status: "bootstrap-unverified",
				source: "authenticated-native-cli",
				verifiedBackends: [],
				verifiedRoles: [],
			};
			mutate(document as unknown as Record<string, unknown>);
			expect(validate(document, nativeMap())).toThrow(message);
		}
	});
});
