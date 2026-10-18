import { describe, expect, test } from "bun:test";
import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

const root = path.resolve(import.meta.dir, "../../assets/project");
const designPath = path.join(root, ".workflow/designs/standard-round.workflow.json");
const mappingPath = path.join(root, ".subagent/flows/standard-round.json");
const manifestPath = path.join(root, ".workflow/resources/standard-round/resources.json");
const fixturePath = path.join(root, ".subagent/fixtures/standard-round.input.json");
const builderPath = path.join(root, ".workflow/resources/standard-round/build_mapping.mjs");
const bundleRoot = path.resolve(import.meta.dir, "../../../../..");

const read = (file: string): any => JSON.parse(fs.readFileSync(file, "utf8"));
const sha256 = (file: string): string => createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const resourceFile = (relative: string): string => {
	const projectFile = path.join(root, relative);
	return fs.existsSync(projectFile) ? projectFile : path.join(bundleRoot, relative);
};
const { assertStateType } = await import("../../scripts/controller/run");

describe("standard-round built-in subagent mapping", () => {
	test("is reproduced exactly by the canonical builder", () => {
		const result = Bun.spawnSync({
			cmd: [process.execPath, builderPath, "--check"],
			cwd: root,
			stdout: "pipe",
			stderr: "pipe",
		});
		expect(new TextDecoder().decode(result.stderr)).toBe("");
		expect(result.exitCode).toBe(0);
		const output = JSON.parse(new TextDecoder().decode(result.stdout));
		expect(output).toMatchObject({ ok: true, workflow: "standard-round", nodes: 122 });
	});

	test("two canonical writes are byte-for-byte stable", () => {
		const write = () => Bun.spawnSync({
			cmd: [process.execPath, builderPath, "--write"],
			cwd: root,
			stdout: "pipe",
			stderr: "pipe",
		});
		const first = write();
		expect(first.exitCode).toBe(0);
		expect(new TextDecoder().decode(first.stderr)).toBe("");
		const firstMapping = fs.readFileSync(mappingPath);
		const firstManifest = fs.readFileSync(manifestPath);
		const firstFixture = fs.readFileSync(fixturePath);
		const second = write();
		expect(second.exitCode).toBe(0);
		expect(new TextDecoder().decode(second.stderr)).toBe("");
		expect(fs.readFileSync(mappingPath)).toEqual(firstMapping);
		expect(fs.readFileSync(manifestPath)).toEqual(firstManifest);
		expect(fs.readFileSync(fixturePath)).toEqual(firstFixture);
	});

	test("derives the no-PM fixture proof binding from the canonical role policy", () => {
		const fixture = read(fixturePath);
		expect(fixture).toEqual({
			itemId: "fixture-standard-round",
			task: "Fixture only: validate the standard-round mapping without starting model work.",
			acceptanceArgv: ["/usr/bin/true"],
			rewardHackingRisks: [],
			proofBinding: {
				schema: "subagent-proof-binding/v1",
				scope: "no-pm",
				itemId: "fixture-standard-round",
				roleModelPolicyDigest: sha256(path.join(
					bundleRoot,
					".claude/skills/default-mode/assets/project/config/workflows/models.json",
				)),
				reason: "Bundled mapping validation fixture; no PM completion or status transition is authorized.",
			},
		});
	});

	test("bounds task-specific reward-hacking hypotheses at the state boundary", () => {
		const design = read(designPath);
		const declaration = design.state.find((field: any) => field.name === "rewardHackingRisks");
		expect(declaration).toMatchObject({
			type: "array",
			maxItems: 5,
			itemType: "string",
			maxItemBytes: 2048,
			uniqueItems: true,
		});
		assertStateType(design, "rewardHackingRisks", ["hardcode expected outputs"]);
		expect(() => assertStateType(design, "rewardHackingRisks", Array.from({ length: 6 }, (_, i) => `risk-${i}`)))
			.toThrow("exceeds maxItems");
		expect(() => assertStateType(design, "rewardHackingRisks", ["x".repeat(2049)]))
			.toThrow("item exceeds maxItemBytes");
		expect(() => assertStateType(design, "rewardHackingRisks", ["duplicate", "duplicate"]))
			.toThrow("items must be unique");
	});

	test("binds the exact design and covers every node without fallback", () => {
		const design = read(designPath);
		const mapping = read(mappingPath);
		expect(mapping.designSha256).toBe(sha256(designPath));
		expect(Object.keys(mapping.handlers)).toEqual(design.nodes.map((node: any) => node.id));
		for (const node of design.nodes) {
			const handler = mapping.handlers[node.id];
			expect(handler).toBeDefined();
			if (node.kind === "agent") expect(handler.type).toBe("agent");
			if (node.kind === "decision") expect(handler.type).toBe("decision");
			if (node.kind === "terminal") expect(handler.type).toBe("terminal");
		}
	});

	test("uses no model scheduler calls and only canonical execution roles", () => {
		const design = read(designPath);
		const mapping = read(mappingPath);
		expect(mapping.scheduler).toEqual({
			role: "task",
			mode: "ambiguous-only",
			maxCalls: 0,
			timeoutSeconds: 7200,
			reads: [],
		});
		const roles = new Set(
			design.nodes.filter((node: any) => node.role).map((node: any) => node.role),
		);
		expect([...roles].sort()).toEqual(["advisor", "plan", "task"]);
	});

	test("uses two-hour ordinary and five-hour long-node deadlines without a global wall cap", () => {
		const design = read(designPath);
		for (const node of design.nodes.filter((item: any) => item.kind === "agent" || item.kind === "decision")) {
			expect([7200, 18000]).toContain(node.timeoutSeconds);
		}
		for (const node of design.nodes.filter((item: any) => mappingCommand(read(mappingPath), item.id))) {
			expect([7200, 18000]).toContain(node.timeoutSeconds);
		}
		expect(design.budgets.maxWallTimeSeconds).toBeUndefined();
	});

	test("binds every final success review and all neutral resources", () => {
		const design = read(designPath);
		const mapping = read(mappingPath);
		const manifest = read(manifestPath);
		const expectedFinalReviews = design.transitions
			.filter((edge: any) => edge.to === "archive-proof" && edge.when?.equals === "PASS")
			.map((edge: any) => edge.from);
		expect(mapping.proof.finalReviewNodes).toEqual(expectedFinalReviews);
		expect(mapping.proof.evidenceProducerNodes).toEqual(
			expectedFinalReviews.map((nodeId: string) =>
				nodeId.replace("final-reward-hack-review-", "capture-final-evidence-"),
			),
		);
		const rewardReviewNodes = design.nodes
			.map((node: any) => node.id)
			.filter((nodeId: string) =>
				nodeId.startsWith("reward-hack-review-") || nodeId.startsWith("final-reward-hack-review-"),
			);
		for (const nodeId of rewardReviewNodes) {
			const markers = mapping.handlers[nodeId].prompt.match(/\{\{review\.commitment\}\}/gu) ?? [];
			expect(markers).toHaveLength(1);
			expect(mapping.handlers[nodeId].prompt).toContain("{{rewardHackingRisks}}");
			expect(design.nodes.find((node: any) => node.id === nodeId)?.reads).toContain("rewardHackingRisks");
		}
		const manifestByPath = new Map(manifest.files.map((entry: any) => [entry.path, entry]));
		for (const resourcePath of Object.values(mapping.proof.resources) as string[]) {
			const entry: any = manifestByPath.get(resourcePath);
			expect(entry).toBeDefined();
			expect(entry.sha256).toBe(sha256(resourceFile(resourcePath)));
		}
		expect(manifestByPath.get(".workflow/resources/standard-round/build_mapping.mjs")?.sha256)
			.toBe(sha256(builderPath));
	});

	test("declares portable executable sources and exact run-owned artifacts", () => {
		const mapping = read(mappingPath);
		const manifest = read(manifestPath);
		const expectedExecutables = {
			"controller-runtime": { source: "controller-runtime" },
			python: { source: "path", command: "python3" },
			git: { source: "path", command: "git" },
			"native-task": { source: "native-role", role: "task" },
			"native-plan": { source: "native-role", role: "plan" },
			"native-advisor": { source: "native-role", role: "advisor" },
		};
		expect(mapping.proof.executables).toEqual(expectedExecutables);
		expect(manifest.externalExecutables).toEqual(
			Object.entries(expectedExecutables).map(([label, source]) => ({ label, ...source })),
		);
		const writes = new Set<string>();
		for (const [nodeId, handler] of Object.entries(mapping.handlers) as Array<[string, any]>) {
			if (handler.type !== "command") continue;
			expect(handler.argv[0]).toBe("{{executable.controller-runtime.path}}");
			expect(handler.argv).toContain("{{executable.python.path}}");
			expect(handler.argv).toContain("{{executable.python.sha256}}");
			if (nodeId.startsWith("capture-")) {
				expect(handler.argv).toContain("{{executable.git.path}}");
				expect(handler.argv).toContain("{{executable.git.sha256}}");
				if (nodeId !== "capture-baseline") {
					expect(handler.argv).toContain("--acceptance-argv-json");
					expect(handler.argv).toContain("{{acceptanceArgv}}");
					expect(handler.argv).not.toContain("--acceptance");
				}
			}
			for (const token of handler.argv as string[]) {
				if (token.includes("{{executable.")) {
					expect(token).toMatch(/^\{\{executable\.[a-z0-9-]+\.(?:path|sha256)\}\}$/u);
				}
				for (const match of token.matchAll(/\{\{executable\.([a-z0-9-]+)\.(?:path|sha256)\}\}/gu)) {
					expect(expectedExecutables).toHaveProperty(match[1]);
				}
			}
			for (const runWrite of handler.runWrites) {
				expect(writes.has(runWrite)).toBe(false);
				writes.add(runWrite);
				if (nodeId === "capture-baseline") expect(runWrite).toBe("artifacts/baseline.json");
				else expect(runWrite).toBe(`artifacts/${nodeId}.json`);
			}
		}
		expect(writes.size).toBe(14);
	});

	test("active portable sources contain no host-specific installation path", () => {
		const activeFiles = [mappingPath, manifestPath, builderPath];
		const bytes = activeFiles.map(file => fs.readFileSync(file, "utf8")).join("\n");
		expect(bytes).not.toMatch(/\/Users\//u);
		expect(bytes).not.toContain(["/opt/", "homebrew"].join(""));
		if (process.env.USER) expect(bytes).not.toContain(process.env.USER);
	});

	test("contains no active legacy runtime dependency", () => {
		const mappingText = fs.readFileSync(mappingPath, "utf8");
		const resourceText = fs.readFileSync(path.join(root, ".workflow/resources/standard-round/prompts/self-evolve.md"), "utf8");
		expect(mappingText).not.toContain(".omp/");
		expect(mappingText).not.toContain("completedActivations");
		expect(resourceText).not.toContain("completedActivations");
		expect(resourceText).toContain("Do not use another harness's runtime state");
	});
});

function mappingCommand(mapping: any, nodeId: string): boolean {
	return mapping.handlers[nodeId]?.type === "command";
}
