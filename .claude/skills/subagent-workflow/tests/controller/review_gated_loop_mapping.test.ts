import { describe, expect, test } from "bun:test";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { assertStateType } from "../../scripts/controller/run";

type JsonObject = Record<string, unknown>;

const root = path.resolve(import.meta.dir, "../../assets/project");
const flowPath = path.join(root, ".subagent/flows/review-gated-loop.json");
const designPath = path.join(root, ".workflow/designs/review-gated-loop.workflow.json");
const fixturePath = path.join(root, ".subagent/fixtures/review-gated-loop.input.json");
const manifestPath = path.join(root, ".workflow/resources/review-gated-loop/manifest.json");
const bundleRoot = path.resolve(import.meta.dir, "../../../../..");

function json(file: string): JsonObject {
	return JSON.parse(fs.readFileSync(file, "utf8")) as JsonObject;
}

function sha256(file: string): string {
	return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function resourceFile(relative: string): string {
	const projectFile = path.join(root, relative);
	return fs.existsSync(projectFile) ? projectFile : path.join(bundleRoot, relative);
}

describe("review-gated-loop built-in mapping", () => {
	test("binds the exact design, every node, and a scheduler-free singleton frontier", () => {
		const flow = json(flowPath);
		const design = json(designPath);
		const nodes = design.nodes as JsonObject[];
		const transitions = design.transitions as JsonObject[];
		const handlers = flow.handlers as Record<string, JsonObject>;

		expect(flow.designSha256).toBe(sha256(designPath));
		expect(Object.keys(handlers).sort()).toEqual(nodes.map(node => String(node.id)).sort());
		expect(flow.scheduler).toEqual({ role: "task", mode: "ambiguous-only", maxCalls: 0, reads: [] });

		for (const node of nodes) {
			if (node.kind !== "terminal") expect([7200, 18000]).toContain(Number(node.timeoutSeconds));
			const outgoing = transitions.filter(edge => edge.from === node.id);
			if (node.kind === "decision") {
				expect(outgoing).toHaveLength((node.outcomes as string[]).length);
				expect(new Set(outgoing.map(edge => JSON.stringify(edge.when))).size).toBe(outgoing.length);
			} else if (node.kind !== "terminal") {
				expect(outgoing).toHaveLength(1);
			}
		}
		expect((design.budgets as JsonObject).maxWallTimeSeconds).toBeUndefined();
	});

	test("declares portable executable placeholders and exact disjoint run artifacts", () => {
		const flow = json(flowPath);
		const handlers = flow.handlers as Record<string, JsonObject>;
		const expected = new Map([
			["capture-baseline", "artifacts/baseline.json"],
			["capture-evidence-r0", "artifacts/evidence-r0.json"],
			["capture-evidence-r1", "artifacts/evidence-r1.json"],
			["capture-evidence-r2", "artifacts/evidence-r2.json"],
		]);
		const seen = new Set<string>();
			for (const [nodeId, output] of expected) {
			const handler = handlers[nodeId];
			const argv = handler.argv as string[];
			expect(handler.type).toBe("command");
			expect(argv[0]).toBe("{{executable.controller-runtime.path}}");
			expect(argv).toContain("{{executable.python.path}}");
			expect(argv).toContain("{{executable.python.sha256}}");
			expect(argv).toContain("{{executable.git.path}}");
			expect(argv).toContain("{{executable.git.sha256}}");
				expect(handler.runWrites).toEqual([output]);
				expect(argv[argv.indexOf("--output") + 1]).toBe(output);
				if (nodeId !== "capture-baseline") {
					expect(argv).toContain("--acceptance-argv-json");
					expect(argv).toContain("{{acceptanceArgv}}");
					expect(argv).not.toContain("--acceptance");
				}
			expect(seen.has(output)).toBeFalse();
			seen.add(output);
		}
	});

	test("binds project resources and declares exact portable executable sources", () => {
		const flow = json(flowPath);
		const proof = flow.proof as JsonObject;
		const resources = proof.resources as Record<string, string>;
		const manifest = json(manifestPath);
		const described = [
			...(manifest.resources as JsonObject[]),
			...(manifest.deterministicDependencies as JsonObject[]),
		];
		for (const item of described) {
			const file = resourceFile(String(item.path));
			expect(sha256(file)).toBe(item.sha256);
		}
		expect(new Set(Object.values(resources))).toEqual(new Set((manifest.resources as JsonObject[]).map(item => String(item.path))));

		const expectedExecutables = {
			"controller-runtime": { source: "controller-runtime" },
			python: { source: "path", command: "python3" },
			git: { source: "path", command: "git" },
			"native-task": { source: "native-role", role: "task" },
			"native-advisor": { source: "native-role", role: "advisor" },
		};
		expect(proof.executables).toEqual(expectedExecutables);
		expect(manifest.externalExecutables).toEqual(
			Object.entries(expectedExecutables).map(([label, source]) => ({ label, ...source })),
		);
		for (const handler of Object.values(flow.handlers as Record<string, JsonObject>)) {
			if (handler.type !== "command") continue;
			for (const token of handler.argv as string[]) {
				if (token.includes("{{executable.")) {
					expect(token).toMatch(/^\{\{executable\.[a-z0-9-]+\.(?:path|sha256)\}\}$/u);
				}
				for (const match of token.matchAll(/\{\{executable\.([a-z0-9-]+)\.(?:path|sha256)\}\}/gu)) {
					expect(expectedExecutables).toHaveProperty(match[1]);
				}
			}
		}
	});

	test("active mapping and resource manifest contain no host-specific installation path", () => {
		const activeBytes = [flowPath, manifestPath]
			.map(file => fs.readFileSync(file, "utf8"))
			.join("\n");
		expect(activeBytes).not.toMatch(/\/Users\//u);
		expect(activeBytes).not.toContain(["/opt/", "homebrew"].join(""));
		if (process.env.USER) expect(activeBytes).not.toContain(process.env.USER);
	});

	test("passes actual evidence to every fresh reviewer and keeps legacy harnesses inactive", () => {
		const flow = json(flowPath);
		const handlers = flow.handlers as Record<string, JsonObject>;
		const proof = flow.proof as JsonObject;
		expect(proof.finalReviewNodes).toEqual(["review-r0", "review-r1", "review-r2"]);
		const design = json(designPath);
		for (const nodeId of proof.finalReviewNodes as string[]) {
			expect((design.nodes as JsonObject[]).find(node => node.id === nodeId)?.reads).toContain("rewardHackingRisks");
		}
		for (const nodeId of proof.finalReviewNodes as string[]) {
			const prompt = String(handlers[nodeId].prompt);
			for (const field of ["{{evidence}}", "{{evidencePath}}", "{{evidenceDigest}}", "{{stateBinding}}", "{{rewardHackingRisks}}"] ) {
				expect(prompt).toContain(field);
			}
			expect(prompt).toContain(".workflow/resources/review-gated-loop/review.md");
		}
		const activeBytes = [fs.readFileSync(flowPath, "utf8"), fs.readFileSync(manifestPath, "utf8")].join("\n").toLowerCase();
		expect(activeBytes).not.toContain("external harness runtime");
		 expect(activeBytes).not.toContain("disabled adapter");
	});

	test("bounds task-specific reward-hacking hypotheses at the state boundary", () => {
		const design = json(designPath);
		const declaration = (design.state as JsonObject[]).find(field => field.name === "rewardHackingRisks");
		expect(declaration).toMatchObject({
			type: "array",
			maxItems: 5,
			itemType: "string",
			maxItemBytes: 2048,
			uniqueItems: true,
		});
		assertStateType(design as any, "rewardHackingRisks", ["hardcode expected outputs"]);
		expect(() => assertStateType(design as any, "rewardHackingRisks", Array.from({ length: 6 }, (_, i) => `risk-${i}`)))
			.toThrow("exceeds maxItems");
		expect(() => assertStateType(design as any, "rewardHackingRisks", ["x".repeat(2049)]))
			.toThrow("item exceeds maxItemBytes");
		expect(() => assertStateType(design as any, "rewardHackingRisks", ["duplicate", "duplicate"]))
			.toThrow("items must be unique");
	});

	test("fixture supplies task inputs plus a typed no-PM binding and cannot assert a verdict or terminal proof", () => {
		const fixture = json(fixturePath);
		expect(Object.keys(fixture).sort()).toEqual(["acceptanceArgv", "itemId", "proofBinding", "rewardHackingRisks", "task"]);
		expect(fixture.acceptanceArgv).toBeArray();
		expect(fixture.rewardHackingRisks).toEqual([]);
		expect(fixture.proofBinding).toEqual({
			schema: "subagent-proof-binding/v1",
			scope: "no-pm",
			itemId: "MAPPING-FIXTURE",
			roleModelPolicyDigest: sha256(path.join(bundleRoot, ".claude/skills/default-mode/assets/project/config/workflows/models.json")),
			reason: "Bundled mapping validation fixture; no PM completion or status transition is authorized.",
		});
		expect(fixture).not.toHaveProperty("verdict");
		expect(fixture).not.toHaveProperty("status");
		expect(fixture).not.toHaveProperty("proof");
	});
});
