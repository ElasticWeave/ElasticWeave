import { afterEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import {
	canonicalModelPolicy,
	canonicalNativeMap,
	refreshAuditedIdentities,
	refreshCatalogResolution,
	type Json,
} from "./fixtures/model_policy";
import { resetTestLaunchResolver, setTestLaunchResolver } from "./fixtures/mock_launch_resolver";
import { scrubEnv } from "../../scripts/native/run";

const { canonicalLaunchReceipt, runController } = await import("../../scripts/controller/run");

const roots: string[] = [];

afterEach(() => {
	for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
	resetTestLaunchResolver();
	// Clear the receipt cache so a new test with a different models document
	// (or a tampered one written to the same path) re-resolves fresh.
	resolverCache.clear();
});

const DESIGN_ID = "receipt-fixture";
// A test-owned driver that calls the real `resolve_launch_spec` through its private
// in-process `catalog_runner` seam with canned catalog bytes. The receipt is
// therefore byte-identical in shape to production while the suite stays
// deterministic and never invokes an authenticated native catalog.
const LAUNCH_RESOLVER_FIXTURE = path.resolve(
	import.meta.dir,
	"./fixtures/resolve_launch_spec_fixture.py",
);
const LAUNCH_RECEIPT_SCHEMA = path.resolve(
	import.meta.dir,
	"../../../update-platform-config/scripts/config-sync/native_launch_receipt.schema.json",
);

function write(root: string, relative: string, value: string, mode?: number): string {
	const file = path.join(root, relative);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, value, mode === undefined ? undefined : { mode });
	return file;
}

function digest(file: string): string {
	return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function sha256Text(value: string): string {
	return createHash("sha256").update(value).digest("hex");
}

function resignReceipt(value: Json): Json {
	const { receiptSha256: _oldDigest, ...withoutDigest } = value;
	return {
		...withoutDigest,
		receiptSha256: `sha256:${sha256Text(canonical(withoutDigest))}`,
	};
}

function bindReceiptExecutables(receiptValue: Json, executables: Record<string, string>): Json {
	const receipt = structuredClone(receiptValue);
	for (const [backend, evidence] of Object.entries(receipt.policy.freshCatalogEvidence as Json)) {
		const executable = fs.realpathSync(executables[backend]);
		const interpreter = fs.realpathSync("/bin/sh");
		const command = (evidence as Json).command as Json;
		const closure = [
			{ role: "interpreter", path: interpreter, sha256: `sha256:${digest(interpreter)}` },
			{ role: "entrypoint", path: executable, sha256: `sha256:${digest(executable)}` },
		];
		command.executable = executable;
		command.executableSha256 = `sha256:${digest(executable)}`;
		command.invokedArgv = [interpreter, executable, ...(command.argv as string[]).slice(1)];
		command.executionClosure = closure;
		command.executionClosureSha256 = `sha256:${sha256Text(
			closure.map(item => `${item.role}\t${item.path}\t${item.sha256}`).join("\n"),
		)}`;
	}
	return resignReceipt(receipt);
}

function stub(root: string, name: string, body: string): string {
	return write(root, name, `#!/bin/sh\nif [ "$1" = "--effort" ]; then shift 2; fi\nprompt=$2\n${body}\n`, 0o755);
}

// Mirrors the controller's resolver-subprocess env scrubbing (run.ts
// RESOLVER_SCRUBBED_ENV). The test's comparison resolver must run under the same
// scrubbed env so its receipts match the controller's and so endpoint/proxy
// overrides in the ambient shell (e.g. ANTHROPIC_BASE_URL) cannot redirect the
// authenticated catalog probe.
function scrubbedResolverEnv(): Record<string, string> {
	return scrubEnv(process.env);
}

// Cache the fixture resolver's output per (models-document digest, request) so the
// controller's activations and the test's comparison share one canonical receipt per
// shape, and replay re-resolution is identical to the stored receipt (so tamper
// detection is the only thing that fails replay).
interface LaunchSpecRequestShape {
	taskClass: string;
	reasoningPosture: string;
	semanticRole: string;
	reviewMode: string;
	writerRole: string | null;
}
const resolverCache = new Map<string, Json>();
function resolverCacheKey(rolesPath: string, request: LaunchSpecRequestShape): string {
	const digest = createHash("sha256").update(fs.readFileSync(rolesPath)).digest("hex");
	return `${digest}|${request.taskClass}|${request.reasoningPosture}|${request.semanticRole}|${request.reviewMode}|${request.writerRole ?? ""}`;
}

interface Fixture {
	root: string;
	design: Json;
	designPath: string;
	mapping: Json;
	mappingPath: string;
	rolesPath: string;
	nativeMapPath: string;
	policy: Json;
}

function fixture(overrides: { posture?: string | null; policy?: (document: Json) => void } = {}): Fixture {
	const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "subagent-receipt-test-")));
	roots.push(root);
	const design = {
		schema: "workflow-design/v1",
		id: DESIGN_ID,
		entry: "seed",
		governance: { reviewPolicy: "machine-gated", completionProof: "none", malformedDecision: "unresolved" },
		budgets: { maxModelCalls: 6, maxWallTimeSeconds: 120 },
		requiredCapabilities: ["machine-gated-review"],
		state: [
			{ name: "topic", type: "string" },
			{ name: "summaryA", type: "string" },
			{ name: "summaryB", type: "string" },
			{ name: "combined", type: "string" },
		],
		nodes: [
			{ id: "seed", kind: "script", reads: [], writes: ["topic"] },
			{
				id: "a", kind: "agent", role: "frontier_execute", workspaceAccess: "write",
				reads: ["topic"], writes: ["summaryA"], timeoutSeconds: 20,
			},
			{
				id: "b", kind: "agent", role: "smol", workspaceAccess: "read",
				reads: ["topic"], writes: ["summaryB"], timeoutSeconds: 20,
			},
			{ id: "merge", kind: "reducer", waitFor: ["a", "b"], reads: ["summaryA", "summaryB"], writes: ["combined"] },
			{
				id: "review", kind: "decision", role: "advisor", session: "fresh", workspaceAccess: "read",
				reads: ["combined"], writes: [], outcomes: ["PASS", "BLOCK"], reviews: ["a"], timeoutSeconds: 20,
			},
			{ id: "success", kind: "terminal", outcome: "success", reads: ["combined"], writes: [] },
			{ id: "unresolved", kind: "terminal", outcome: "unresolved", reads: [], writes: [] },
		],
		transitions: [
			{ from: "seed", to: "a" },
			{ from: "seed", to: "b" },
			{ from: "a", to: "merge" },
			{ from: "b", to: "merge" },
			{ from: "merge", to: "review" },
			{ from: "review", to: "success", when: { field: "review.verdict", equals: "PASS" } },
			{ from: "review", to: "unresolved", when: { field: "review.verdict", equals: "BLOCK" } },
		],
		terminals: { success: "success", unresolved: "unresolved" },
	};
	const designPath = write(root, ".workflow/designs/fixture.workflow.json", `${JSON.stringify(design, null, 2)}\n`);

	const receiptProbe = [
		`[ -f workflow-output/subagent/${DESIGN_ID}/*/launch-receipts/1.json ] 2>/dev/null || exit 97`,
		`[ -f workflow-output/subagent/${DESIGN_ID}/*/launch-bindings/1.json ] 2>/dev/null || exit 98`,
	].join("; ");
	const codexStub = stub(root, "codex-stub.sh", [
		'case "$prompt" in',
		`  *"workflow scheduler"*) ${receiptProbe}; echo '{"verdict":"RUN","summary":"{\\"nodeIds\\":[\\"a\\",\\"b\\"],\\"reason\\":\\"stub\\"}"}' ;;`,
		`  *"alpha"*) echo '{"summary":"a","statePatch":[{"op":"set","path":"/summaryA","value":"A"}]}' ;;`,
		`  *"beta"*) echo '{"summary":"b","statePatch":[{"op":"set","path":"/summaryB","value":"B"}]}' ;;`,
		`  *) echo '{"verdict":"BLOCK","summary":"unexpected codex prompt"}' ;;`,
		"esac",
	].join("\n"));
	const anthropicStub = stub(root, "anthropic-stub.sh", [
		'case "$prompt" in',
		`  *"review"*) echo '{"verdict":"PASS","summary":"approved"}' ;;`,
		`  *) echo '{"verdict":"BLOCK","summary":"unexpected anthropic prompt"}' ;;`,
		"esac",
	].join("\n"));
	const cursorStub = stub(root, "cursor-stub.sh", [
		'case "$prompt" in',
		`  *"review"*) echo '{"verdict":"PASS","summary":"approved"}' ;;`,
		`  *"alpha"*) echo '{"summary":"a","statePatch":[{"op":"set","path":"/summaryA","value":"A"}]}' ;;`,
		`  *"beta"*) echo '{"summary":"b","statePatch":[{"op":"set","path":"/summaryB","value":"B"}]}' ;;`,
		`  *) echo '{"verdict":"BLOCK","summary":"unexpected cursor prompt"}' ;;`,
		"esac",
	].join("\n"));

	const mapping: Json = {
		schema: "subagent-workflow/v1",
		designPath: ".workflow/designs/fixture.workflow.json",
		designSha256: digest(designPath),
		inputPath: "input.json",
		...(overrides.posture === null ? {} : { reasoningPosture: overrides.posture ?? "frontier" }),
		scheduler: { role: "smol", mode: "ambiguous-only", maxCalls: 1, timeoutSeconds: 20, reads: [] },
		handlers: {
			seed: { type: "input", bindings: { topic: "topic" } },
			a: { type: "agent", prompt: "alpha {{topic}}" },
			b: { type: "agent", prompt: "beta {{topic}}" },
			merge: { type: "template", values: { combined: "{{summaryA}} + {{summaryB}}" } },
			review: { type: "decision", prompt: "review {{combined}}" },
			success: { type: "terminal" },
			unresolved: { type: "terminal" },
		},
	};
	const mappingPath = write(root, ".subagent/flows/fixture.json", `${JSON.stringify(mapping, null, 2)}\n`);
	write(root, "input.json", '{"topic":"typed orchestration"}\n');

	const policy = canonicalModelPolicy();
	overrides.policy?.(policy);
	const rolesPath = write(root, "config/workflows/models.json", `${JSON.stringify(policy, null, 2)}\n`);
	const nativeMapPath = write(
		root,
		"native-map.json",
		`${JSON.stringify(canonicalNativeMap({ codex: codexStub, anthropic: anthropicStub, cursor: cursorStub }), null, 2)}\n`,
	);
	const item = { root, design, designPath, mapping, mappingPath, rolesPath, nativeMapPath, policy };
	// Default every runController / canonicalLaunchReceipt call in this test to the
	// cached fixture resolver. Tests that need a different resolver (e.g. a rejecting
	// one) reassign activeResolver after fixture().
	setTestLaunchResolver(seamResolver(item));
	return item;
}

function options(root: string) {
	return {
		root,
		mappingPath: ".subagent/flows/fixture.json",
		rolesPath: "config/workflows/models.json",
		nativeMapPath: "native-map.json",
		outputRoot: "workflow-output/subagent",
	};
}

function writePolicy(item: Fixture, mutate: (document: Json) => void): void {
	const document = JSON.parse(fs.readFileSync(item.rolesPath, "utf8"));
	mutate(document);
	fs.writeFileSync(item.rolesPath, `${JSON.stringify(document, null, 2)}\n`);
}

function readPolicy(item: Fixture): Json {
	return JSON.parse(fs.readFileSync(item.rolesPath, "utf8"));
}

function writeMapping(item: Fixture, mutate: (mapping: Json) => void): void {
	const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
	mutate(mapping);
	fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
}

function writeDesign(item: Fixture, mutate: (design: Json) => void): void {
	const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
	mutate(design);
	fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
	writeMapping(item, mapping => {
		mapping.designSha256 = digest(item.designPath);
	});
}

function traceRecords(runDir: string): Json[] {
	return fs.readFileSync(path.join(runDir, "native-calls.jsonl"), "utf8")
		.split(/\r?\n/u)
		.filter(Boolean)
		.map(line => JSON.parse(line));
}

function receipt(runDir: string, call: number): Json {
	return JSON.parse(fs.readFileSync(path.join(runDir, "launch-receipts", `${call}.json`), "utf8"));
}

function controllerBinding(runDir: string, call: number): Json {
	return JSON.parse(fs.readFileSync(path.join(runDir, "launch-bindings", `${call}.json`), "utf8"));
}

function receiptRequest(
	item: Fixture,
	policy: Json = JSON.parse(fs.readFileSync(item.rolesPath, "utf8")),
) {
	return {
		design: JSON.parse(fs.readFileSync(item.designPath, "utf8")),
		mapping: JSON.parse(fs.readFileSync(item.mappingPath, "utf8")),
		roles: policy,
		nativeMap: JSON.parse(fs.readFileSync(item.nativeMapPath, "utf8")),
		runId: "compatibility-run",
		designSha256: digest(item.designPath),
		mappingSha256: digest(item.mappingPath),
		rolesSha256: digest(item.rolesPath),
		nativeMapSha256: digest(item.nativeMapPath),
		inputSha256: digest(path.join(item.root, "input.json")),
		call: 1,
		activation: "scheduler",
		role: "smol",
		workspaceAccess: "schedule" as const,
		root: item.root,
		rolesPath: item.rolesPath,
	};
}

function resolvedReceipt(item: Fixture, request: {
	taskClass: string;
	reasoningPosture: string;
	semanticRole: string;
	reviewMode: "none" | "exact-opposite";
	writerRole?: string;
}): Json {
	const shape: LaunchSpecRequestShape = {
		taskClass: request.taskClass,
		reasoningPosture: request.reasoningPosture,
		semanticRole: request.semanticRole,
		reviewMode: request.reviewMode,
		writerRole: request.writerRole ?? null,
	};
	const key = resolverCacheKey(item.rolesPath, shape);
	const cached = resolverCache.get(key);
	if (cached) return cached;
	const command = [
		"python3", LAUNCH_RESOLVER_FIXTURE,
		"--models", item.rolesPath,
		"--task-class", request.taskClass,
		"--reasoning-posture", request.reasoningPosture,
		"--semantic-role", request.semanticRole,
		"--review-mode", request.reviewMode,
	];
	if (request.writerRole) command.push("--writer-role", request.writerRole);
	const process = Bun.spawnSync({ cmd: command, cwd: item.root, env: scrubbedResolverEnv() });
	if (process.exitCode !== 0) {
		const stderr = process.stderr.toString();
		const signal = process.signalCode ? ` (signal ${process.signalCode})` : "";
		throw new Error(`canonical launch resolver failed: ${stderr}${signal}`);
	}
	let receipt = JSON.parse(process.stdout.toString()) as Json;
	const nativeMap = JSON.parse(fs.readFileSync(item.nativeMapPath, "utf8")) as Json;
	const backends = Object.keys(receipt.policy.freshCatalogEvidence as Json);
	const executables = Object.fromEntries(backends.map(backend => [
		backend,
		nativeMap.providers?.[backend === "claude" ? "anthropic" : backend]?.bin,
	]));
	if (Object.values(executables).every(value => typeof value === "string" && path.isAbsolute(value))) {
		receipt = bindReceiptExecutables(receipt, executables as Record<string, string>);
	}
	resolverCache.set(key, receipt);
	return receipt;
}

function seamResolver(item: Fixture) {
	return (request: { taskClass: string; reasoningPosture: string; semanticRole: string; reviewMode: string; writerRole: string | null }): Json => {
		return resolvedReceipt(item, {
			taskClass: request.taskClass,
			reasoningPosture: request.reasoningPosture,
			semanticRole: request.semanticRole,
			reviewMode: request.reviewMode as "none" | "exact-opposite",
			writerRole: request.writerRole ?? undefined,
		});
	};
}

function validateCanonicalReceiptSchema(file: string): void {
	const process = Bun.spawnSync({
		cmd: [
			"python3",
			"-c",
			[
				"import json, jsonschema, pathlib, sys",
				"schema = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))",
				"receipt = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))",
				"jsonschema.Draft202012Validator.check_schema(schema)",
				"jsonschema.Draft202012Validator(schema).validate(receipt)",
			].join("; "),
			LAUNCH_RECEIPT_SCHEMA,
			file,
		],
	});
	if (process.exitCode !== 0) {
		throw new Error(`controller launch receipt is not canonical v1: ${process.stderr.toString()}`);
	}
}

async function expectRejectedBeforeAnyNativeProcess(item: Fixture, message: string | RegExp): Promise<void> {
	await expect(runController(options(item.root))).rejects.toThrow(message);
	expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
}

async function expectAuthorizationRejectedBeforeActivation(item: Fixture, message: string | RegExp): Promise<void> {
	await expect(runController(options(item.root))).rejects.toThrow(message);
	const outputBase = path.join(item.root, "workflow-output/subagent", DESIGN_ID);
	const runDir = path.join(outputBase, fs.readdirSync(outputBase)[0]);
	expect(fs.readFileSync(path.join(runDir, "native-calls.jsonl"), "utf8")).toBe("");
	expect(fs.readFileSync(path.join(runDir, "native-attempts.jsonl"), "utf8")).toBe("");
	expect(fs.readFileSync(path.join(runDir, "native-heartbeats.jsonl"), "utf8")).toBe("");
	expect(fs.existsSync(path.join(runDir, "launch-receipts"))).toBeFalse();
	expect(fs.existsSync(path.join(runDir, "launch-bindings"))).toBeFalse();
	const state = JSON.parse(fs.readFileSync(path.join(runDir, "state.json"), "utf8"));
	expect(state).toMatchObject({ modelCalls: 0, schedulerCalls: 0, nodeCalls: 0, inFlight: "" });
}

describe("subagent scheduler canonical model policy admission", () => {
	test("accepts only the canonical project-root policy path", async () => {
		const item = fixture();
		fs.copyFileSync(item.rolesPath, path.join(item.root, "other-models.json"));
		await expect(runController({ ...options(item.root), rolesPath: "other-models.json" }))
			.rejects.toThrow("config/workflows/models.json");
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("rejects workflow-model-roles/v1 and /v2 policies", async () => {
		for (const schema of ["workflow-model-roles/v1", "workflow-model-roles/v2"]) {
			const item = fixture();
			writePolicy(item, document => {
				document.schema = schema;
			});
			await expectRejectedBeforeAnyNativeProcess(item, "workflow-model-roles/v3");
		}
	});

	test("admits a bootstrap-unverified catalog resolution when the fresh live catalog covers the role", async () => {
		// Per the catalog-authenticity contract the persisted catalogResolution never
		// authorizes OR denies dispatch; the fresh resolver is the gate. A
		// bootstrap-unverified snapshot is structurally valid, so a fresh live
		// catalog that covers the role admits the run (the static ``validate``
		// advisory still reports dispatchAdmitted:false — see the structural test).
		const item = fixture();
		writePolicy(item, document => {
			document.catalogResolution = {
				schema: "workflow-model-catalog-resolution/v1",
				status: "bootstrap-unverified",
				source: "authenticated-native-cli",
				verifiedBackends: [],
				verifiedRoles: [],
			};
		});
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.state.modelCalls).toBe(4);
	});

	test("structural validation of a bootstrap policy never becomes permission to start", async () => {
		const item = fixture();
		writePolicy(item, document => {
			document.catalogResolution = {
				schema: "workflow-model-catalog-resolution/v1",
				status: "bootstrap-unverified",
				source: "authenticated-native-cli",
				verifiedBackends: [],
				verifiedRoles: [],
			};
		});
		// A project that has never run the authenticated catalog sync must still be able to check its
		// mapping, and that check must say out loud that a run would be refused.
		write(
			item.root,
			".claude/skills/subagent-workflow/scripts/native/native-map.json",
			fs.readFileSync(item.nativeMapPath, "utf8"),
		);
		const validated = Bun.spawnSync({
			cmd: [
				process.execPath,
				path.join(import.meta.dir, "../../scripts/controller/run.ts"),
				"validate",
				path.relative(item.root, item.mappingPath),
			],
			cwd: item.root,
		});
		expect(validated.stderr.toString()).not.toContain("REFUSING");
		expect(validated.exitCode).toBe(0);
		expect(JSON.parse(validated.stdout.toString())).toMatchObject({
			clean: true,
			designId: DESIGN_ID,
			dispatchAdmitted: false,
		});
		expect(validated.stderr.toString()).toContain("bootstrap-unverified");
		// The static structural pass reports dispatchAdmitted:false (no live evidence yet),
		// but the actual run admits because the fresh resolver — not the persisted snapshot
		// — is the gate. A clean structural pass is therefore never permission to start;
		// the run starts only on fresh live catalog authentication.
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.state.modelCalls).toBe(4);
	});

	test("static validation reports dispatchAdmitted:false even for a persisted verified snapshot", () => {
		// The persisted catalogResolution is caller-authorable audit data. A self-consistent
		// "verified" snapshot must therefore never let `validate` claim live dispatch
		// authorization; only the fresh resolver can, and only at start.
		const item = fixture();
		expect(readPolicy(item).catalogResolution.status).toBe("verified");
		write(
			item.root,
			".claude/skills/subagent-workflow/scripts/native/native-map.json",
			fs.readFileSync(item.nativeMapPath, "utf8"),
		);
		const validated = Bun.spawnSync({
			cmd: [
				process.execPath,
				path.join(import.meta.dir, "../../scripts/controller/run.ts"),
				"validate",
				path.relative(item.root, item.mappingPath),
			],
			cwd: item.root,
		});
		expect(validated.stderr.toString()).not.toContain("REFUSING");
		expect(validated.exitCode).toBe(0);
		const report = JSON.parse(validated.stdout.toString());
		expect(report.clean).toBeTrue();
		expect(report.dispatchAdmitted).toBeFalse();
		expect(report.structurallyDispatchEligible).toBeTrue();
		expect(report.dispatchBlocked).toContain("static validation never authorizes dispatch");
	});

	test("rejects forged catalog evidence digests", async () => {
		const item = fixture();
		writePolicy(item, document => {
			document.catalogResolution.evidenceReceipt.verifiedRoleIdentitiesSha256 = `sha256:${"0".repeat(64)}`;
		});
		await expectRejectedBeforeAnyNativeProcess(item, /evidenceReceipt|verified role identities/iu);
	});

	test("rejects partial catalog evidence that omits an active role", async () => {
		const item = fixture();
		writePolicy(item, document => {
			document.catalogResolution.verifiedRoles = document.catalogResolution.verifiedRoles
				.filter((name: string) => name !== "smol");
		});
		await expectRejectedBeforeAnyNativeProcess(item, /verifiedRoles|active role/iu);
	});

	test("rejects a catalog receipt whose backend digests do not cover every active backend", async () => {
		const item = fixture();
		writePolicy(item, document => {
			// The anthropic writer rides the cursor backend (cursor-agent routes to anthropic),
			// so the active backends are codex and cursor. Omitting cursor leaves an active
			// backend uncovered by the persisted evidence.
			delete document.catalogResolution.evidenceReceipt.backendCatalogSha256.cursor;
		});
		await expectRejectedBeforeAnyNativeProcess(item, /backend/iu);
	});

	test("rejects an inactive dispatch route for a role the workflow launches", async () => {
		const item = fixture();
		writeDesign(item, design => {
			design.nodes.find((node: Json) => node.id === "b").role = "diversity_glm";
		});
		await expectRejectedBeforeAnyNativeProcess(item, /catalog candidate/iu);
	});

	test("rejects a future-only model family even when the role claims to be active", async () => {
		const item = fixture();
		writeDesign(item, design => {
			design.nodes.find((node: Json) => node.id === "b").role = "research_analyze";
		});
		writePolicy(item, document => {
			// Route research_analyze at a future-only family (deepseek is declared
			// dispatchEligibility:future-candidate). The cursor backend already allows
			// max effort, so we must not narrow backendEffortLevels.cursor (that would
			// break the other cursor-backed roles); the future-only gate fires on its own.
			document.roles.research_analyze.model = "deepseek-v4";
			document.roles.research_analyze.modelFamily = "deepseek";
			document.roles.research_analyze.backend = "cursor";
			document.roles.research_analyze.provider = "cursor";
			document.roles.research_analyze.upstreamProviderFamily = "deepseek";
			document.roles.research_analyze.effort = "max";
			refreshAuditedIdentities(document);
			refreshCatalogResolution(document);
		});
		await expectRejectedBeforeAnyNativeProcess(item, /future-only/iu);
	});

	test("rejects an audited identity map that disagrees with a role", async () => {
		const item = fixture();
		writePolicy(item, document => {
			document.auditedModelIdentities.bindings = document.auditedModelIdentities.bindings
				.map((binding: Json) => binding.model === "gpt-5.6-luna"
					? { ...binding, upstreamProviderFamily: "anthropic" }
					: binding);
		});
		await expectRejectedBeforeAnyNativeProcess(item, /audited model identity/iu);
	});

	test("rejects a missing canonical role", async () => {
		const item = fixture();
		writePolicy(item, document => {
			delete document.roles.designer;
			delete document.reasoningPolicy.intelligenceClasses.planning[1];
			document.reasoningPolicy.intelligenceClasses.planning =
				document.reasoningPolicy.intelligenceClasses.planning.filter(Boolean);
			for (const posture of Object.values(document.reasoningPolicy.postures) as Json[]) {
				posture.allowedRoles = posture.allowedRoles.filter((name: string) => name !== "designer");
			}
			document.capabilityPolicy.taskClassRoles.planning =
				document.capabilityPolicy.taskClassRoles.planning.filter((name: string) => name !== "designer");
			refreshAuditedIdentities(document);
			refreshCatalogResolution(document);
		});
		await expectRejectedBeforeAnyNativeProcess(item, /missing required roles/iu);
	});

	test("rejects a review pairing that is not the exact opposite provider identity", async () => {
		const item = fixture();
		writePolicy(item, document => {
			document.capabilityPolicy.reviewPairings[0].reviewer.effort = "xhigh";
		});
		await expectRejectedBeforeAnyNativeProcess(item, /review pairing|reviewPairings/iu);
	});

	test("rejects postures that are not strictly ordered ceilings", async () => {
		const item = fixture();
		writePolicy(item, document => {
			document.reasoningPolicy.postures.economy.allowedRoles = [
				...document.reasoningPolicy.postures.balanced.allowedRoles,
			];
		});
		await expectRejectedBeforeAnyNativeProcess(item, /posture/iu);
	});

	test("rejects a capability policy that reopens an excluded family", async () => {
		const item = fixture();
		writePolicy(item, document => {
			document.capabilityPolicy.excludedFamilies = [];
		});
		await expectRejectedBeforeAnyNativeProcess(item, /Composer|excluded/iu);
	});

	test("rejects an unsupported automatic escalation budget", async () => {
		const item = fixture();
		writePolicy(item, document => {
			document.reasoningPolicy.maxAutomaticEscalations = 5;
		});
		await expectRejectedBeforeAnyNativeProcess(item, /maxAutomaticEscalations/iu);
	});
});

describe("subagent scheduler native launch receipts", () => {
	test("canonicalLaunchReceipt matches the canonical schema and resolver contract", () => {
		const item = fixture();
		const actual = canonicalLaunchReceipt(receiptRequest(item));
		const file = write(item.root, "controller-launch-receipt.json", actual.bytes);
		validateCanonicalReceiptSchema(file);
		expect(actual.receipt).toEqual(resolvedReceipt(item, {
			taskClass: "mechanical",
			reasoningPosture: "frontier",
			semanticRole: "smol",
			reviewMode: "none",
		}));
	});

	test("a multi-writer review keeps canonical v1 singular and binds every model pairing separately", () => {
		const item = fixture();
		writeDesign(item, design => {
			design.nodes.find((node: Json) => node.id === "b").workspaceAccess = "write";
			design.nodes.find((node: Json) => node.id === "review").reviews = ["a", "b"];
		});
		const actual = canonicalLaunchReceipt({
			...receiptRequest(item),
			call: 4,
			activation: "review",
			role: "advisor",
			workspaceAccess: "read",
		});
		expect(actual.receipt).toEqual(resolvedReceipt(item, {
			taskClass: "verification",
			reasoningPosture: "frontier",
			semanticRole: "advisor",
			reviewMode: "exact-opposite",
			writerRole: "frontier_execute",
		}));
		expect((actual.receipt.request as Json).writerRole).toBe("frontier_execute");
		expect((actual.controllerBinding.reviewPairings as Json[]).map(pair => pair.writerNodeId)).toEqual(["a", "b"]);
		expect((actual.controllerBinding.reviewPairings as Json[]).map(pair => pair.exactOppositePolicy)).toEqual([true, false]);
	});

	test("canonicalLaunchReceipt accepts a bootstrap-unverified catalog when the fresh live catalog covers the role", () => {
		const item = fixture();
		// Persist the bootstrap-unverified snapshot to the canonical roles path so the
		// resolver reads it fresh. The persisted status is audit-only; the fresh live
		// catalog is the gate. catalogResolution.schema is the resolution schema
		// (workflow-model-catalog-resolution/v1), distinct from the evidenceReceipt
		// schema (workflow-model-catalog-evidence/v1).
		writePolicy(item, document => {
			document.catalogResolution = {
				schema: "workflow-model-catalog-resolution/v1",
				status: "bootstrap-unverified",
				source: "authenticated-native-cli",
				verifiedBackends: [],
				verifiedRoles: [],
			};
		});
		const actual = canonicalLaunchReceipt(receiptRequest(item));
		expect(actual.receipt.policy.liveCatalogAuthenticatedByResolver).toBe(true);
		expect(actual.receipt.policy.catalogStatus).toBe("bootstrap-unverified");
	});

	test("canonicalLaunchReceipt fails closed when the fresh live catalog rejects the role, regardless of persisted verified status", () => {
		const item = fixture();
		// The persisted verified snapshot is left intact (a structurally consistent
		// verified document). Flipping verifiedRoles would break the document's
		// structural consistency and the validator would reject it for that reason;
		// instead we keep a valid verified document and inject a resolver that
		// simulates the fresh live catalog rejecting the role. This proves the
		// fresh resolver — not the persisted snapshot — is the gate.
		setTestLaunchResolver((): Json => {
			throw new Error("fresh live catalog does not cover role smol");
		});
		expect(() => canonicalLaunchReceipt(receiptRequest(item))).toThrow(/fresh live catalog does not cover role smol/iu);
	});

	test("canonicalLaunchReceipt continues to reject a catalog-candidate role", () => {
		const item = fixture();
		expect(() => canonicalLaunchReceipt({
			...receiptRequest(item),
			activation: "b",
			role: "diversity_glm",
			workspaceAccess: "read",
		})).toThrow(/catalog candidate/iu);
	});

	test("materializes one canonical receipt per native activation and binds it everywhere", async () => {
		const item = fixture();
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.state.modelCalls).toBe(4);

		const trace = traceRecords(result.runDir);
		expect(trace).toHaveLength(4);
		const receiptFiles = fs.readdirSync(path.join(result.runDir, "launch-receipts")).sort();
		expect(receiptFiles).toEqual(["1.json", "2.json", "3.json", "4.json"]);
		const bindingFiles = fs.readdirSync(path.join(result.runDir, "launch-bindings")).sort();
		expect(bindingFiles).toEqual(receiptFiles);

		for (const record of trace) {
			const call = record.call as number;
			expect(record.launchReceiptPath).toBe(`launch-receipts/${record.call}.json`);
			expect(record.launchReceiptSha256).toBe(
				digest(path.join(result.runDir, `launch-receipts/${record.call}.json`)),
			);
			expect(record.controllerLaunchBindingPath).toBe(`launch-bindings/${record.call}.json`);
			expect(record.controllerLaunchBindingSha256).toBe(
				digest(path.join(result.runDir, `launch-bindings/${record.call}.json`)),
			);
			const emitted = receipt(result.runDir, call);
			validateCanonicalReceiptSchema(path.join(result.runDir, `launch-receipts/${call}.json`));
			expect(emitted).toEqual(resolvedReceipt(item, emitted.request as {
				taskClass: string;
				reasoningPosture: string;
				semanticRole: string;
				reviewMode: "none" | "exact-opposite";
				writerRole?: string;
			}));
			const resolved = emitted.resolved as Json;
			const command = emitted.policy.freshCatalogEvidence[resolved.backend].command as Json;
			const invoked = command.invokedArgv as string[];
			const invocationPrefix = invoked.slice(0, invoked.length - (command.argv as string[]).length + 1);
			expect(record).toMatchObject({
				model: `${resolved.provider}/${resolved.catalogModelId}`,
				catalogModelId: resolved.catalogModelId,
				executable: command.executable,
				processExecutable: invocationPrefix[0],
				executableSha256: String(command.executableSha256).slice("sha256:".length),
				executionClosureSha256: String(command.executionClosureSha256).slice("sha256:".length),
				invocationPrefixSha256: sha256Text(JSON.stringify(invocationPrefix)),
			});
			expect(record.argvSha256).toMatch(/^[0-9a-f]{64}$/u);
			expect(record.environmentSha256).toMatch(/^[0-9a-f]{64}$/u);
			expect(record.launchEnvironmentSha256).toMatch(/^[0-9a-f]{64}$/u);
			expect(controllerBinding(result.runDir, call).execution).toMatchObject({
				executionHarness: command.executable,
				mappedModel: resolved.catalogModelId,
				launchedModelArgument: record.model,
				launchEnvironmentSha256: record.launchEnvironmentSha256,
			});
		}
		const attempts = fs.readFileSync(path.join(result.runDir, "native-attempts.jsonl"), "utf8")
			.trim().split("\n").map(line => JSON.parse(line) as Json);
		expect(attempts).toHaveLength(4);
		for (const attempt of attempts) {
			const matchingTrace = trace[(attempt.call as number) - 1];
			expect(attempt).toMatchObject({
				catalogModelId: matchingTrace.catalogModelId,
				executable: matchingTrace.executable,
				processExecutable: matchingTrace.processExecutable,
				executableSha256: matchingTrace.executableSha256,
				executionClosureSha256: matchingTrace.executionClosureSha256,
				invocationPrefixSha256: matchingTrace.invocationPrefixSha256,
				launchReceiptSha256: matchingTrace.launchReceiptSha256,
				environmentSha256: matchingTrace.environmentSha256,
			});
			expect(attempt.argvSha256).toMatch(/^[0-9a-f]{64}$/u);
		}

		const scheduler = receipt(result.runDir, 1);
		expect(Object.keys(scheduler).sort()).toEqual([
			"policy", "receiptSha256", "request", "resolved", "schema", "writer",
		]);
		expect(scheduler).toMatchObject({
			schema: "workflow-native-launch-receipt/v1",
			request: {
				semanticRole: "smol",
				taskClass: "mechanical",
				reasoningPosture: "frontier",
				writerRole: null,
				reviewMode: "none",
			},
			resolved: {
				semanticRole: "smol",
				provider: "codex",
				backend: "codex",
				model: "gpt-5.6-luna",
				modelFamily: "gpt-5.6-luna",
				upstreamProviderFamily: "openai",
				effort: "low",
				intelligenceClass: "mechanical",
				dispatchStatus: "active",
			},
			writer: null,
		});

		const byActivation = new Map(
			trace.map(record => {
				const call = record.call as number;
				const binding = controllerBinding(result.runDir, call);
				return [binding.activation as string, { receipt: receipt(result.runDir, call), binding }];
			}),
		);
		expect(byActivation.get("a")!.receipt).toMatchObject({
			request: { semanticRole: "frontier_execute", taskClass: "topIntelligence", reviewMode: "none" },
			resolved: { model: "gpt-6-astra", effort: "max", intelligenceClass: "topIntelligence" },
		});
		expect(byActivation.get("a")!.binding).toMatchObject({
			workspaceAccess: "write", reviewPairings: [], execution: { semanticRole: "frontier_execute" },
		});
		expect(byActivation.get("b")!.receipt).toMatchObject({
			request: { semanticRole: "smol", taskClass: "mechanical", reviewMode: "none" },
		});
		expect(byActivation.get("b")!.binding).toMatchObject({ workspaceAccess: "read" });
		const reviewReceipt = byActivation.get("review")!.receipt;
		expect(reviewReceipt).toMatchObject({
			request: {
				semanticRole: "advisor", taskClass: "verification", reviewMode: "exact-opposite",
				writerRole: "frontier_execute",
			},
			resolved: {
				provider: "anthropic", backend: "claude", model: "claude-opus-5",
				catalogModelId: "claude-opus-5",
				modelFamily: "claude-opus-5", upstreamProviderFamily: "anthropic",
				effort: "max", intelligenceClass: "verification",
			},
			writer: { semanticRole: "frontier_execute", upstreamProviderFamily: "openai" },
		});
		expect(byActivation.get("review")!.binding).toMatchObject({ workspaceAccess: "read" });
		expect(byActivation.get("review")!.binding.reviewPairings).toEqual([{
			exactOppositePolicy: true,
			writerNodeId: "a",
			writerRole: "frontier_execute",
			writerUpstreamProviderFamily: "openai",
			writerModelFamily: "gpt-6-astra",
			writerEffort: "max",
			reviewerRole: "advisor",
			reviewerUpstreamProviderFamily: "anthropic",
			reviewerModelFamily: "claude-opus-5",
			reviewerEffort: "max",
		}]);

		const policyDigests = scheduler.policy as Json;
		expect(policyDigests.modelsDocumentSha256).toBe(`sha256:${result.state.rolesSha256}`);
		const document = JSON.parse(fs.readFileSync(item.rolesPath, "utf8"));
		expect(policyDigests.capabilityPolicySha256).toBe(`sha256:${sha256Text(canonical(document.capabilityPolicy))}`);
		expect(policyDigests.reasoningPolicySha256).toBe(`sha256:${sha256Text(canonical(document.reasoningPolicy))}`);
		expect(policyDigests.catalogResolutionSha256).toBe(`sha256:${sha256Text(canonical(document.catalogResolution))}`);
		const schedulerBinding = controllerBinding(result.runDir, 1);
		expect(schedulerBinding).toMatchObject({
			schema: "workflow-controller-launch-binding/v1",
			runId: result.state.runId,
			designId: DESIGN_ID,
			call: 1,
			activation: "scheduler",
			workspaceAccess: "schedule",
			receipt: { path: "launch-receipts/1.json", sha256: digest(path.join(result.runDir, "launch-receipts/1.json")) },
			controller: {
				designSha256: result.state.designSha256,
				mappingSha256: result.state.mappingSha256,
				nativeMapSha256: result.state.nativeMapSha256,
				inputSha256: result.state.inputSha256,
			},
		});
		expect(schedulerBinding.bindingSha256).toMatch(/^sha256:[0-9a-f]{64}$/u);

		expect((result.state as unknown as Json).launchReceiptsDigest).toMatch(/^[0-9a-f]{64}$/u);
		const published = result.result.launchReceipts as Json[];
		expect(published.map(entry => entry.call)).toEqual([1, 2, 3, 4]);
		for (const entry of published) {
			expect(entry.path).toBe(`launch-receipts/${entry.call}.json`);
			expect(entry.sha256).toBe(digest(path.join(result.runDir, entry.path as string)));
			expect(entry.controllerBindingPath).toBe(`launch-bindings/${entry.call}.json`);
			expect(entry.controllerBindingSha256).toBe(
				digest(path.join(result.runDir, entry.controllerBindingPath as string)),
			);
		}
	});

	test("writes the receipt before the native process starts", async () => {
		const item = fixture();
		const result = await runController(options(item.root));
		// The scheduler stub exits 97/98 unless both pre-launch artifacts already exist.
		expect(result.state.status).toBe("success");
		expect(result.state.schedulerCalls).toBe(1);
	});

	test("launches the freshly authenticated CLI instead of a caller-PATH shadow", async () => {
		const item = fixture();
		const poisonDirectory = fs.realpathSync(fs.mkdtempSync(path.join(item.root, "poison-path-")));
		const poisonMarker = path.join(item.root, "poison-codex-ran");
		const poisonEnvironmentMarker = path.join(item.root, "poison-environment-reached-child");
		const authenticatedCodex = path.join(item.root, "codex-stub.sh");
		const authenticatedClaude = path.join(item.root, "anthropic-stub.sh");
		const poisonedCodex = path.join(poisonDirectory, "codex");
		fs.copyFileSync(authenticatedCodex, poisonedCodex);
		fs.chmodSync(poisonedCodex, 0o755);
		fs.writeFileSync(
			poisonedCodex,
			fs.readFileSync(poisonedCodex, "utf8").replace("#!/bin/sh\n", `#!/bin/sh\ntouch '${poisonMarker}'\n`),
			{ mode: 0o755 },
		);
		fs.writeFileSync(
			authenticatedCodex,
			fs.readFileSync(authenticatedCodex, "utf8").replace(
				"#!/bin/sh\n",
				`#!/bin/sh\nif [ -n "$ANTHROPIC_BASE_URL$CLAUDE_CONFIG_DIR$CURSOR_API_ENDPOINT" ] || `
					+ `[ "$XDG_CONFIG_HOME" = "/tmp/attacker-config" ]; then touch '${poisonEnvironmentMarker}'; fi\n`,
			),
			{ mode: 0o755 },
		);
		const nativeMap = JSON.parse(fs.readFileSync(item.nativeMapPath, "utf8"));
		nativeMap.providers.codex.bin = "codex";
		fs.writeFileSync(item.nativeMapPath, `${JSON.stringify(nativeMap, null, 2)}\n`);
		const inner = seamResolver(item);
		setTestLaunchResolver(request => bindReceiptExecutables(inner(request), {
			codex: authenticatedCodex,
			claude: authenticatedClaude,
		}));
		const priorPath = process.env.PATH;
		const priorPoison = Object.fromEntries(
			["ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "CURSOR_API_ENDPOINT", "XDG_CONFIG_HOME"]
				.map(name => [name, process.env[name]]),
		);
		process.env.PATH = [poisonDirectory, priorPath ?? ""].filter(Boolean).join(path.delimiter);
		process.env.ANTHROPIC_BASE_URL = "https://attacker.invalid";
		process.env.CLAUDE_CONFIG_DIR = "/tmp/attacker-claude-config";
		process.env.CURSOR_API_ENDPOINT = "https://attacker.invalid";
		process.env.XDG_CONFIG_HOME = "/tmp/attacker-config";
		try {
			const result = await runController(options(item.root));
			expect(result.state.status).toBe("success");
			expect(fs.existsSync(poisonMarker)).toBeFalse();
			expect(fs.existsSync(poisonEnvironmentMarker)).toBeFalse();
		} finally {
			if (priorPath === undefined) delete process.env.PATH;
			else process.env.PATH = priorPath;
			for (const [name, value] of Object.entries(priorPoison)) {
				if (value === undefined) delete process.env[name];
				else process.env[name] = value;
			}
		}
	});

	test("rejects a native-map entrypoint that disagrees with the freshly authenticated executable", async () => {
		const item = fixture();
		const authenticatedCodex = path.join(item.root, "codex-stub.sh");
		const authenticatedClaude = path.join(item.root, "anthropic-stub.sh");
		const unexpectedCodex = write(
			item.root,
			"unexpected-codex.sh",
			`#!/bin/sh\ntouch '${path.join(item.root, "unexpected-codex-ran")}'\nexit 99\n`,
			0o755,
		);
		const nativeMap = JSON.parse(fs.readFileSync(item.nativeMapPath, "utf8"));
		nativeMap.providers.codex.bin = unexpectedCodex;
		fs.writeFileSync(item.nativeMapPath, `${JSON.stringify(nativeMap, null, 2)}\n`);
		const inner = seamResolver(item);
		setTestLaunchResolver(request => bindReceiptExecutables(inner(request), {
			codex: authenticatedCodex,
			claude: authenticatedClaude,
		}));
		await expectAuthorizationRejectedBeforeActivation(item, /entrypoint .* disagrees with freshly authenticated executable/iu);
		expect(fs.existsSync(path.join(item.root, "unexpected-codex-ran"))).toBeFalse();
	});

	test("rejects authenticated entrypoint and transitive-closure byte drift before activation evidence", async () => {
		for (const attack of ["entrypoint", "bundle"] as const) {
			const item = fixture();
			const authenticatedCodex = path.join(item.root, "codex-stub.sh");
			const authenticatedClaude = path.join(item.root, "anthropic-stub.sh");
			const bundle = write(item.root, "authenticated-cli-bundle.js", "console.log('bound');\n");
			const inner = seamResolver(item);
			let attacked = false;
			setTestLaunchResolver(request => {
				let bound = bindReceiptExecutables(inner(request), {
					codex: authenticatedCodex,
					claude: authenticatedClaude,
				});
				if (!attacked) {
					if (attack === "bundle") {
						const changed = structuredClone(bound);
						const command = changed.policy.freshCatalogEvidence.codex.command as Json;
						(command.executionClosure as Json[]).push({
							role: "bundle",
							path: bundle,
							sha256: `sha256:${digest(bundle)}`,
						});
						command.executionClosureSha256 = `sha256:${sha256Text(
							(command.executionClosure as Json[])
								.map(entry => `${entry.role}\t${entry.path}\t${entry.sha256}`).join("\n"),
						)}`;
						bound = resignReceipt(changed);
						fs.appendFileSync(bundle, "// drift\n");
					} else {
						fs.appendFileSync(authenticatedCodex, "# drift\n");
					}
					attacked = true;
				}
				return bound;
			});
			await expectAuthorizationRejectedBeforeActivation(item, /executable closure is not live-bound.*closure bytes changed/iu);
		}
	});

	test("runController rejects non-schema resolver output and ignores any caller-supplied resolveLaunchSpec option", async () => {
		const item = fixture();
		const calls: { semanticRole: string; taskClass: string; reviewMode: string; writerRole: string | null }[] = [];
		// Deterministic catalog results are injected by substituting the resolver
		// module. An arbitrary extra property must fail the canonical v1 contract even
		// when every security-relevant nested field came from the real fixture resolver.
		const inner = seamResolver(item);
		setTestLaunchResolver((request): Json => {
			calls.push({
				semanticRole: request.semanticRole,
				taskClass: request.taskClass,
				reviewMode: request.reviewMode,
				writerRole: request.writerRole,
			});
			return { ...inner(request), __seamInjected: true } as Json;
		});
		// A caller attempts to inject a resolver through the options object. runController
		// must NOT honor it: no production option, flag, environment variable, or exported
		// binding on ControllerOptions can bypass or redirect the bound resolver.
		const sentinelOptions = {
			...options(item.root),
			resolveLaunchSpec: (): Json => {
				throw new Error("caller-supplied resolveLaunchSpec must never be invoked");
			},
		} as Parameters<typeof runController>[0];
		await expect(runController(sentinelOptions)).rejects.toThrow(/unsupported fields|malformed/iu);
		// Authorization stops at the first pending activation. No model process ran and
		// no call-specific receipt/trace ledger was created.
		expect(calls).toHaveLength(1);
		const outputBase = path.join(item.root, "workflow-output/subagent", DESIGN_ID);
		const runDirs = fs.existsSync(outputBase) ? fs.readdirSync(outputBase) : [];
		expect(runDirs).toHaveLength(1);
		const runDir = path.join(outputBase, runDirs[0]);
		expect(fs.readFileSync(path.join(runDir, "native-calls.jsonl"), "utf8")).toBe("");
		expect(fs.existsSync(path.join(runDir, "launch-receipts"))).toBeFalse();
		expect(fs.existsSync(path.join(runDir, "launch-bindings"))).toBeFalse();
		const checkpoint = JSON.parse(fs.readFileSync(path.join(runDir, "state.json"), "utf8"));
		expect(checkpoint.modelCalls).toBe(0);
		expect(checkpoint.schedulerCalls).toBe(0);
	});

	test("rejects a wrong request and a wrong receipt self-digest before launch", async () => {
		for (const attack of ["request", "digest"] as const) {
			const item = fixture();
			const inner = seamResolver(item);
			setTestLaunchResolver(request => {
				const original = inner(request);
				if (attack === "digest") return { ...original, receiptSha256: `sha256:${"0".repeat(64)}` };
				return resignReceipt({
					...original,
					request: { ...(original.request as Json), semanticRole: "frontier_execute" },
				});
			});
			await expect(runController(options(item.root))).rejects.toThrow(
				attack === "request" ? /request disagrees/iu : /self-digest/iu,
			);
			const outputBase = path.join(item.root, "workflow-output/subagent", DESIGN_ID);
			const runDir = path.join(outputBase, fs.readdirSync(outputBase)[0]);
			expect(fs.readFileSync(path.join(runDir, "native-calls.jsonl"), "utf8")).toBe("");
			expect(fs.existsSync(path.join(runDir, "launch-receipts"))).toBeFalse();
			expect(JSON.parse(fs.readFileSync(path.join(runDir, "state.json"), "utf8")).modelCalls).toBe(0);
		}
	});

	test("rejects arbitrary receipt JSON, frozen-policy drift, and malformed fresh execution evidence", async () => {
		const attacks: Array<{
			name: string;
			mutate: (receipt: Json) => Json;
			expected: RegExp;
		}> = [
			{
				name: "arbitrary JSON",
				mutate: () => ({ authorized: true }),
				expected: /missing or unsupported fields/iu,
			},
			{
				name: "wrong frozen policy digest",
				mutate: receiptValue => {
					const changed = structuredClone(receiptValue);
					changed.policy.modelsDocumentSha256 = `sha256:${"0".repeat(64)}`;
					return resignReceipt(changed);
				},
				expected: /modelsDocumentSha256 disagrees with the frozen policy/iu,
			},
			{
				name: "wrong execution closure digest",
				mutate: receiptValue => {
					const changed = structuredClone(receiptValue);
					changed.policy.freshCatalogEvidence.codex.command.executionClosureSha256 =
						`sha256:${"0".repeat(64)}`;
					return resignReceipt(changed);
				},
				expected: /execution closure digest is invalid/iu,
			},
			{
				name: "unsupported command evidence field",
				mutate: receiptValue => {
					const changed = structuredClone(receiptValue);
					changed.policy.freshCatalogEvidence.codex.command.attacker = true;
					return resignReceipt(changed);
				},
				expected: /catalog command has missing or unsupported fields/iu,
			},
			{
				name: "receipt command mismatch",
				mutate: receiptValue => {
					const changed = structuredClone(receiptValue);
					changed.policy.freshCatalogEvidence.codex.command.argv = ["codex", "models"];
					return resignReceipt(changed);
				},
				expected: /catalog command is not the canonical native probe/iu,
			},
		];
		for (const attack of attacks) {
			const item = fixture();
			const inner = seamResolver(item);
			setTestLaunchResolver(request => attack.mutate(inner(request)));
			await expectAuthorizationRejectedBeforeActivation(item, attack.expected);
		}
	});

	test("launches the advisor through native Claude at its concrete Opus id and Max effort", async () => {
		const item = fixture();
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		const trace = traceRecords(result.runDir);
		const claudeCalls = trace.filter(record => {
			const written = receipt(result.runDir, record.call as number);
			return written.resolved.backend === "claude";
		});
		expect(claudeCalls).toHaveLength(1);
		for (const record of claudeCalls) {
			const written = receipt(result.runDir, record.call as number);
			const resolved = written.resolved as Json;
			expect(resolved).toMatchObject({
				backend: "claude",
				provider: "anthropic",
				model: "claude-opus-5",
				catalogModelId: "claude-opus-5",
				effort: "max",
			});
			expect(record.model).toBe("anthropic/claude-opus-5");
			expect(record.effort).toBe("max");
		}
	});

	test("launches a codex role as the exact catalog slug plus its canonical effort", async () => {
		const item = fixture();
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		const trace = traceRecords(result.runDir);
		const codexCalls = trace.filter(record => String(record.model).startsWith("codex/"));
		expect(codexCalls.length).toBeGreaterThan(0);
		for (const record of codexCalls) {
			const written = receipt(result.runDir, record.call as number);
			const resolved = written.resolved as Json;
			// Codex normalization proves the base slug itself, and the reasoning level is a
			// separate provider-local parameter, so effort stays an explicit argument.
			expect(resolved.catalogModelId).toBe(resolved.model);
			expect(record.model).toBe(`codex/${resolved.catalogModelId}`);
			expect(record.effort).toBe(resolved.effort);
		}
	});

	test("refuses a receipt whose catalogModelId names a different base model family", async () => {
		const item = fixture();
		const inner = seamResolver(item);
		setTestLaunchResolver((request): Json => {
			const receiptValue = { ...inner(request) } as Json;
			const resolved = { ...(receiptValue.resolved as Json) };
			// A tampered or buggy resolver claims a concrete id from an unrelated family.
			resolved.catalogModelId = "gpt-5.6-luna-max";
			receiptValue.resolved = resolved;
			return resignReceipt(receiptValue);
		});
		await expect(runController(options(item.root)))
			.rejects.toThrow(/not the exact canonical slug/iu);
	});

	test("refuses a receipt whose catalogModelId encodes a different effort than the role", async () => {
		const item = fixture();
		const inner = seamResolver(item);
		setTestLaunchResolver((request): Json => {
			const receiptValue = { ...inner(request) } as Json;
			const resolved = { ...(receiptValue.resolved as Json) };
			// Silent effort escalation: same family, cheaper/pricier reasoning level.
			resolved.catalogModelId = `${String(resolved.model)}-thinking-low`;
			receiptValue.resolved = resolved;
			return resignReceipt(receiptValue);
		});
		await expect(runController(options(item.root)))
			.rejects.toThrow(/not the exact canonical slug|disagrees with role/iu);
	});

	test("refuses a receipt whose resolved identity disagrees with the canonical role", async () => {
		const item = fixture();
		const inner = seamResolver(item);
		setTestLaunchResolver((request): Json => {
			const receiptValue = { ...inner(request) } as Json;
			const resolved = { ...(receiptValue.resolved as Json) };
			resolved.effort = "low";
			receiptValue.resolved = resolved;
			return resignReceipt(receiptValue);
		});
		await expect(runController(options(item.root)))
			.rejects.toThrow(/receipt identity disagrees with role/iu);
	});

	test("refuses a receipt that carries no concrete catalogModelId", async () => {
		const item = fixture();
		const inner = seamResolver(item);
		setTestLaunchResolver((request): Json => {
			const receiptValue = { ...inner(request) } as Json;
			const resolved = { ...(receiptValue.resolved as Json) };
			delete resolved.catalogModelId;
			receiptValue.resolved = resolved;
			return resignReceipt(receiptValue);
		});
		await expect(runController(options(item.root)))
			.rejects.toThrow(/no concrete catalogModelId/iu);
	});

	test("a receipt whose catalogModelId was rewritten after the run fails closed on replay", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		const file = path.join(first.runDir, "launch-receipts/2.json");
		const tampered = JSON.parse(fs.readFileSync(file, "utf8"));
		tampered.resolved.catalogModelId = "gpt-5.6-luna-max";
		fs.writeFileSync(file, `${JSON.stringify(tampered, null, 2)}\n`);
		// The controller binding commits the receipt digest, so rewriting the launched
		// model id is caught as a binding/receipt divergence before replay even
		// re-derives the launch spec.
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/launch binding 2 disagrees with its canonical receipt bytes/iu);
	});

	test("resume rejects authenticated executable byte drift without replaying a native activation", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		const tracePath = path.join(first.runDir, "native-calls.jsonl");
		const attemptsPath = path.join(first.runDir, "native-attempts.jsonl");
		const statePath = path.join(first.runDir, "state.json");
		const before = {
			trace: fs.readFileSync(tracePath),
			attempts: fs.readFileSync(attemptsPath),
			state: fs.readFileSync(statePath),
		};
		fs.appendFileSync(path.join(item.root, "codex-stub.sh"), "# drift after completed run\n");
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/executable closure is not live-bound.*closure bytes changed/iu);
		expect(fs.readFileSync(tracePath)).toEqual(before.trace);
		expect(fs.readFileSync(attemptsPath)).toEqual(before.attempts);
		expect(fs.readFileSync(statePath)).toEqual(before.state);
	});

	test("a removed receipt fails closed on replay", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		fs.rmSync(path.join(first.runDir, "launch-receipts/2.json"));
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/launch receipt/iu);
	});

	test("a removed controller launch binding fails closed on replay", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		fs.rmSync(path.join(first.runDir, "launch-bindings/2.json"));
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/launch binding|call coverage/iu);
	});

	test("a modified receipt fails closed on replay", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		const file = path.join(first.runDir, "launch-receipts/2.json");
		const tampered = JSON.parse(fs.readFileSync(file, "utf8"));
		tampered.resolved.effort = "low";
		fs.writeFileSync(file, `${JSON.stringify(tampered, null, 2)}\n`);
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/launch.*receipt|canonical receipt/iu);
	});

	test("a modified controller launch binding fails closed on replay", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		const file = path.join(first.runDir, "launch-bindings/2.json");
		const tampered = JSON.parse(fs.readFileSync(file, "utf8"));
		tampered.activation = "other-node";
		fs.writeFileSync(file, `${JSON.stringify(tampered, null, 2)}\n`);
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/launch binding/iu);
	});

	test("swapped receipts fail closed on replay", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		const trace = traceRecords(first.runDir);
		const activations = trace.map(record => controllerBinding(first.runDir, record.call as number).activation);
		const left = activations.indexOf("a") + 1;
		const right = activations.indexOf("b") + 1;
		const leftFile = path.join(first.runDir, `launch-receipts/${left}.json`);
		const rightFile = path.join(first.runDir, `launch-receipts/${right}.json`);
		const leftBytes = fs.readFileSync(leftFile);
		fs.writeFileSync(leftFile, fs.readFileSync(rightFile));
		fs.writeFileSync(rightFile, leftBytes);
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/launch.*receipt|canonical receipt/iu);
	});

	test("an extra unexplained receipt fails closed on replay", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		fs.copyFileSync(
			path.join(first.runDir, "launch-receipts/1.json"),
			path.join(first.runDir, "launch-receipts/9.json"),
		);
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/launch receipt/iu);
	});

	test("a trace record whose receipt digest was re-pointed fails closed on replay", async () => {
		const item = fixture();
		const first = await runController(options(item.root));
		expect(first.state.status).toBe("success");
		const tracePath = path.join(first.runDir, "native-calls.jsonl");
		const records = traceRecords(first.runDir).map(record => record.call === 2
			? { ...record, launchReceiptSha256: digest(path.join(first.runDir, "launch-receipts/3.json")) }
			: record);
		fs.writeFileSync(tracePath, `${records.map(record => JSON.stringify(record)).join("\n")}\n`);
		await expect(runController({ ...options(item.root), resume: path.relative(item.root, first.runDir) }))
			.rejects.toThrow(/launch receipt/iu);
	});
});

describe("subagent mapping reasoning posture contract", () => {
	test("requires an explicit reasoning posture instead of a silent default", async () => {
		const item = fixture({ posture: null });
		await expectRejectedBeforeAnyNativeProcess(item, /reasoningPosture/iu);
	});

	test("rejects a posture that the canonical policy does not declare", async () => {
		const item = fixture({ posture: "yolo" });
		await expectRejectedBeforeAnyNativeProcess(item, /posture/iu);
	});

	test("rejects a launch whose role is outside the declared posture ceiling", async () => {
		const item = fixture({ posture: "economy" });
		await expectRejectedBeforeAnyNativeProcess(item, /frontier_execute/u);
	});

	test("binds the declared posture into every receipt", async () => {
		const item = fixture({ posture: "balanced" });
		writeMapping(item, mapping => {
			mapping.reasoningPosture = "balanced";
		});
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		for (const record of traceRecords(result.runDir)) {
			expect((receipt(result.runDir, record.call as number).request as Json).reasoningPosture).toBe("balanced");
		}
	});
});

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
