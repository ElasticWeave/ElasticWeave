import { afterEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import {
	canonicalModelPolicy, canonicalNativeMap, refreshAuditedIdentities, refreshCatalogResolution,
} from "./fixtures/model_policy";
import "./fixtures/mock_launch_resolver";

const {
	canonicalLaunchReceipt, classifyTermination, maxModelActivations, parseCliArguments, runController,
} = await import("../../scripts/controller/run");

const roots: string[] = [];

afterEach(() => {
	for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

function digest(file: string): string {
	return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function write(root: string, relative: string, value: string, mode?: number): string {
	const file = path.join(root, relative);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, value, mode === undefined ? undefined : { mode });
	return file;
}

function fixture(options: {
	selection?: string[];
	illegalPatch?: boolean;
	wrongType?: boolean;
	duplicatePatch?: boolean;
	partialInvalidPatch?: boolean;
	slowA?: boolean;
	commandA?: boolean;
	commandBackground?: boolean;
	commandTimeout?: number;
	attackA?: "run-dir" | "subdir" | "response-file" | "event-file";
	tamperA?: "state" | "events" | "trace";
	driftInputA?: boolean;
	requiredSkillA?: boolean;
	roleEffort?: string;
	checkReservations?: boolean;
	wallTimeSeconds?: number | null;
	maxModelCalls?: number;
	governance?: object;
} = {}) {
	const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "subagent-workflow-test-")));
	roots.push(root);
	const design = {
		schema: "workflow-design/v1",
		id: "fixture",
		entry: "seed",
		governance: options.governance ?? { reviewPolicy: "independent", completionProof: "none" },
		budgets: {
			maxModelCalls: options.maxModelCalls ?? 4,
			...(options.wallTimeSeconds === null ? {} : { maxWallTimeSeconds: options.wallTimeSeconds ?? 60 }),
		},
		requiredCapabilities: options.requiredSkillA ? ["node-skill-loading"] : [],
		state: [
			{ name: "topic", type: "string" },
			{ name: "summaryA", type: "string" },
			{ name: "summaryB", type: "string" },
			{ name: "combined", type: "string" },
			{ name: "mergeStatus", type: "string" },
		],
		nodes: [
			{ id: "seed", kind: "script", reads: [], writes: ["topic"] },
			options.commandA
				? { id: "a", kind: "script", reads: ["topic"], writes: ["summaryA"], timeoutSeconds: options.commandTimeout ?? 5 }
				: {
					id: "a", kind: "agent", role: "smol", workspaceAccess: "read", reads: ["topic"], writes: ["summaryA"],
					timeoutSeconds: options.slowA ? 0.1 : 5,
					...(options.requiredSkillA ? { skills: { required: ["required-fixture"], onDemand: ["deferred-fixture"] } } : {}),
				},
			{ id: "b", kind: "agent", role: "smol", workspaceAccess: "read", reads: ["topic"], writes: ["summaryB"], timeoutSeconds: 5 },
			{ id: "merge", kind: "reducer", waitFor: ["a", "b"], reads: ["summaryA", "summaryB"], writes: ["combined", "mergeStatus"] },
			{ id: "success", kind: "terminal", outcome: "success", reads: ["combined"], writes: [] },
			{ id: "unresolved", kind: "terminal", outcome: "unresolved", reads: [], writes: [] },
		],
		transitions: [
			{ from: "seed", to: "a" },
			{ from: "seed", to: "b" },
			{ from: "a", to: "merge" },
			{ from: "b", to: "merge" },
			{ from: "merge", to: "success", when: { field: "mergeStatus", equals: "PASS" } },
			{ from: "merge", to: "unresolved", when: { field: "mergeStatus", equals: "BLOCK" } },
		],
		terminals: { success: "success", unresolved: "unresolved" },
	};
	const designPath = write(root, ".workflow/designs/fixture.workflow.json", `${JSON.stringify(design, null, 2)}\n`);
	const mapping = {
		schema: "subagent-workflow/v1",
		designPath: ".workflow/designs/fixture.workflow.json",
		designSha256: digest(designPath),
		inputPath: "input.json",
		reasoningPosture: "economy",
		scheduler: { role: "smol", mode: "ambiguous-only", maxCalls: 1, timeoutSeconds: 5, reads: [] },
		handlers: {
			seed: { type: "input", bindings: { topic: "topic" } },
			a: options.commandA ? { type: "command", argv: ["__STUB__", "command-a"] } : { type: "agent", prompt: "alpha {{topic}}" },
			b: { type: "agent", prompt: "beta {{topic}}" },
			merge: { type: "template", values: { combined: "{{summaryA}} + {{summaryB}}", mergeStatus: "PASS" } },
			success: { type: "terminal" },
			unresolved: { type: "terminal" },
		},
	};
	write(root, "input.json", '{"topic":"typed orchestration"}\n');
	const policy = canonicalModelPolicy();
	policy.roles.smol.effort = options.roleEffort ?? "low";
	refreshCatalogResolution(policy);
	write(root, "config/workflows/models.json", `${JSON.stringify(policy, null, 2)}\n`);
	if (options.requiredSkillA) {
		write(root, ".claude/skills/required-fixture/SKILL.md", "REQUIRED_FIXTURE_BODY_7f3c\n");
		write(root, ".claude/skills/deferred-fixture/SKILL.md", "DEFERRED_FIXTURE_BODY_SHOULD_NOT_LOAD\n");
	}
	const selection = JSON.stringify(options.selection ?? ["a", "b"]).replaceAll('"', '\\"');
	const aPatch = options.illegalPatch
		? '{"summary":"bad","statePatch":[{"op":"set","path":"/forbidden","value":"A"}]}'
		: options.wrongType
			? '{"summary":"bad type","statePatch":[{"op":"set","path":"/summaryA","value":42}]}'
			: options.duplicatePatch
				? '{"summary":"duplicate","statePatch":[{"op":"set","path":"/summaryA","value":"A"},{"op":"set","path":"/summaryA","value":"B"}]}'
				: options.partialInvalidPatch
					? '{"summary":"partial","statePatch":[{"op":"set","path":"/summaryA","value":"A"},{"op":"set","path":"/forbidden","value":"bad"}]}'
			: '{"summary":"a","statePatch":[{"op":"set","path":"/summaryA","value":"A"}]}';
	const slow = options.slowA ? "sleep 1; " : "";
	const quotedRoot = root.replaceAll("'", "'\"'\"'");
	const attack = options.attackA
		? [
			`run_dir=$(find '${quotedRoot}/workflow-output/subagent/fixture' -mindepth 1 -maxdepth 1 -type d | head -n 1)`,
			`outside='${quotedRoot}/outside-${options.attackA}'`,
			'mkdir -p "$outside"',
			options.attackA === "run-dir"
				? 'mv "$run_dir" "$run_dir-original"; ln -s "$outside" "$run_dir"'
				: options.attackA === "subdir"
					? 'ln -s "$outside" "$run_dir/native-responses"'
					: options.attackA === "response-file"
						? 'mkdir -p "$run_dir/native-responses"; printf sentinel > "$outside/target"; ln -s "$outside/target" "$run_dir/native-responses/a.stdout"'
						: 'printf sentinel > "$outside/target"; rm "$run_dir/events.jsonl"; ln -s "$outside/target" "$run_dir/events.jsonl"',
		].join("; ")
		: "";
	const schedulerReservationCheck = options.checkReservations
		? `state_file=$(find '${quotedRoot}/workflow-output/subagent/fixture' -name state.json | head -n 1); grep -q '\"inFlight\": \"scheduler\"' "$state_file" && grep -q '\"schedulerCalls\": 1' "$state_file" && grep -q '\"modelCalls\": 1' "$state_file" || exit 91; `
		: "";
	const leafReservationCheck = options.checkReservations
		? `state_file=$(find '${quotedRoot}/workflow-output/subagent/fixture' -name state.json | head -n 1); grep -q '\"inFlight\": \"a\"' "$state_file" && grep -q '\"nodeCalls\": 1' "$state_file" && grep -q '\"modelCalls\": 2' "$state_file" || exit 92; `
		: "";
	const tamper = options.tamperA
		? `run_dir=$(find '${quotedRoot}/workflow-output/subagent/fixture' -mindepth 1 -maxdepth 1 -type d | head -n 1); ${
			options.tamperA === "state"
				? 'printf tamper >> "$run_dir/state.json"'
				: options.tamperA === "events"
					? 'printf tamper >> "$run_dir/events.jsonl"'
					: 'printf forged > "$run_dir/native-calls.jsonl"'
		}; `
		: "";
	const driftInput = options.driftInputA ? `printf '{"topic":"drifted"}\\n' > '${quotedRoot}/input.json'; ` : "";
	const skillCheck = options.requiredSkillA
		? `printf '%s' "$prompt" | grep -q 'REQUIRED_FIXTURE_BODY_7f3c' || exit 93; printf '%s' "$prompt" | grep -q 'deferred-fixture/SKILL.md' || exit 94; if printf '%s' "$prompt" | grep -q 'DEFERRED_FIXTURE_BODY_SHOULD_NOT_LOAD'; then exit 95; fi; `
		: "";
	const commandBody = options.commandBackground
		? `(trap '' TERM; sleep 5) & echo '${aPatch}'; exit 0`
		: `sleep 1; if env | grep -q '^GENERAL_AGENT_'; then echo '{"summary":"leaked","statePatch":[{"op":"set","path":"/summaryA","value":"LEAKED"}]}'; else echo '${aPatch}'; fi; exit 0`;
	const stubScript = (expectedEffort: string) =>
		`#!/bin/sh\nif [ "$1" = "--effort" ]; then [ "$2" = "${expectedEffort}" ] || exit 96; shift 2; fi\nif [ "$1" = "command-a" ]; then ${commandBody}; fi\nprompt=$2\ncase "$prompt" in\n  *"workflow scheduler"*"typed orchestration"*) echo '{"verdict":"RUN","summary":"{\\"nodeIds\\":[\\"leaked-state\\"],\\"reason\\":\\"scheduler saw undeclared state\\"}"}' ;;\n  *"workflow scheduler"*) ${schedulerReservationCheck}echo '{"verdict":"RUN","summary":"{\\"nodeIds\\":${selection},\\"reason\\":\\"stub\\"}"}' ;;\n  *"alpha"*) ${leafReservationCheck}${skillCheck}${slow}${attack}${attack ? "; " : ""}${tamper}${driftInput}echo '${aPatch}' ;;\n  *"beta"*) echo '{"summary":"b","statePatch":[{"op":"set","path":"/summaryB","value":"B"}]}' ;;\n  *) echo '{"verdict":"BLOCK","summary":"unexpected prompt"}' ;;\nesac\n`;
	const stub = write(root, "stub.sh", stubScript(options.roleEffort ?? "low"), 0o755);
	const reviewerStub = write(root, "reviewer-stub.sh", stubScript("max"), 0o755);
	if (options.commandA) (mapping.handlers.a as { argv: string[] }).argv[0] = stub;
	const mappingPath = write(root, ".subagent/flows/fixture.json", `${JSON.stringify(mapping, null, 2)}\n`);
	write(
		root,
		"native-map.json",
		`${JSON.stringify(canonicalNativeMap({ codex: stub, anthropic: reviewerStub, cursor: reviewerStub }), null, 2)}\n`,
	);
	return {
		root, design, designPath, mapping, mappingPath, policy,
		rolesPath: path.join(root, "config/workflows/models.json"),
		nativeMapPath: path.join(root, "native-map.json"),
		stubPath: stub,
		reviewerStubPath: reviewerStub,
	};
}

function machineGatedFixture() {
	const item = fixture({
		governance: { reviewPolicy: "machine-gated", completionProof: "none", malformedDecision: "unresolved" },
		maxModelCalls: 5,
	});
	const design = item.design as any;
	design.requiredCapabilities = ["machine-gated-review"];
	design.nodes.find((node: { id: string }) => node.id === "a").workspaceAccess = "write";
	design.nodes.splice(design.nodes.length - 2, 0, {
		id: "review",
		kind: "decision",
		role: "advisor",
		session: "fresh",
		workspaceAccess: "read",
		reads: ["combined"],
		writes: [],
		outcomes: ["PASS", "BLOCK"],
		reviews: ["a"],
		timeoutSeconds: 5,
	});
	design.transitions = design.transitions.filter((edge: { from: string }) => edge.from !== "merge");
	design.transitions.push(
		{ from: "merge", to: "review" },
		{ from: "review", to: "success", when: { field: "review.verdict", equals: "PASS" } },
		{ from: "review", to: "unresolved", when: { field: "review.verdict", equals: "BLOCK" } },
	);
	fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);

	const mapping = item.mapping as any;
	mapping.handlers.review = { type: "decision", prompt: "review {{combined}}" };
	mapping.designSha256 = digest(item.designPath);
	fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);

	const approve = (file: string) => {
		const stub = fs.readFileSync(file, "utf8").replace(
			`  *) echo '{"verdict":"BLOCK","summary":"unexpected prompt"}' ;;`,
			`  *"review"*) echo '{"verdict":"PASS","summary":"approved"}' ;;\n  *) echo '{"verdict":"BLOCK","summary":"unexpected prompt"}' ;;`,
		);
		fs.writeFileSync(file, stub, { mode: 0o755 });
	};
	approve(item.stubPath);
	approve(item.reviewerStubPath);
	return { ...item, design, mapping };
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

function runtimeDigests(root: string) {
	return {
		rolesSha256: digest(path.join(root, "config/workflows/models.json")),
		nativeMapSha256: digest(path.join(root, "native-map.json")),
		inputSha256: digest(path.join(root, "input.json")),
	};
}

/**
 * Rebuild one crash-window native-call record together with the launch receipt the controller
 * would have written before that activation, so replay sees the same evidence a real run leaves.
 */
function nativeTrace(
	item: { root: string; designPath: string; mappingPath: string },
	runDir: string,
	call: number,
	activation: string,
	prompt = "fixture call",
): string {
	const normalizedPrompt = prompt === "scheduler"
		? "You are a workflow scheduler. You cannot execute work or change state."
		: prompt === "alpha fixture"
			? "alpha typed orchestration"
			: prompt === "beta fixture"
				? "beta typed orchestration"
				: prompt;
	const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
	const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
	const roles = JSON.parse(fs.readFileSync(path.join(item.root, "config/workflows/models.json"), "utf8"));
	const nativeMap = JSON.parse(fs.readFileSync(path.join(item.root, "native-map.json"), "utf8"));
	const node = design.nodes.find((entry: { id: string }) => entry.id === activation);
	const role = activation === "scheduler" ? mapping.scheduler.role : node.role ?? "task";
	const receipt = canonicalLaunchReceipt({
		design, mapping, roles, nativeMap,
		runId: path.basename(runDir),
		designSha256: digest(item.designPath),
		mappingSha256: digest(item.mappingPath),
		...runtimeDigests(item.root),
		call,
		activation,
		role,
		workspaceAccess: activation === "scheduler" ? "schedule" : node.workspaceAccess ?? "read",
		root: item.root,
		rolesPath: "config/workflows/models.json",
	});
	write(item.root, path.relative(item.root, path.join(runDir, receipt.path)), receipt.bytes);
	write(
		item.root,
		path.relative(item.root, path.join(runDir, receipt.controllerBindingPath)),
		receipt.controllerBindingBytes,
	);
	const resolved = receipt.receipt.resolved as Record<string, unknown>;
	const policy = receipt.receipt.policy as Record<string, unknown>;
	const evidence = policy.freshCatalogEvidence as Record<string, Record<string, unknown>>;
	const command = evidence[String(resolved.backend)].command as Record<string, unknown>;
	const invokedArgv = command.invokedArgv as string[];
	const catalogArgv = command.argv as string[];
	const invocationPrefix = invokedArgv.slice(0, invokedArgv.length - catalogArgv.length + 1);
	const execution = receipt.controllerBinding.execution as Record<string, string>;
	return `${JSON.stringify({
		call,
		model: `${resolved.provider}/${resolved.catalogModelId}`,
		effort: resolved.backend === "codex" || resolved.backend === "claude" ? resolved.effort ?? null : null,
		catalogModelId: resolved.catalogModelId,
		executable: command.executable,
		processExecutable: invocationPrefix[0],
		executableSha256: String(command.executableSha256).slice("sha256:".length),
		executionClosureSha256: String(command.executionClosureSha256).slice("sha256:".length),
		invocationPrefixSha256: hashText(JSON.stringify(invocationPrefix)),
		argvSha256: hashText(JSON.stringify(["fixture-crash-window-argv", call])),
		environmentSha256: execution.launchEnvironmentSha256,
		launchEnvironmentSha256: execution.launchEnvironmentSha256,
		launchReceiptSha256: receipt.sha256,
		prompt: normalizedPrompt,
		time: new Date().toISOString(),
		launchReceiptPath: receipt.path,
		controllerLaunchBindingPath: receipt.controllerBindingPath,
		controllerLaunchBindingSha256: receipt.controllerBindingSha256,
	})}\n`;
}

function hashText(value: string): string {
	return createHash("sha256").update(value).digest("hex");
}

const FIXTURE_COMMITMENT = `sha256:${"a".repeat(64)}`;

function writeResumeEvidence(
	root: string,
	runDir: string,
	options: { schedulerSelection?: string[]; completedB?: boolean; inFlightA?: boolean } = {},
): { startedAt: string; updatedAt: string } {
	write(root, path.relative(root, path.join(runDir, "native-attempts.jsonl")), "");
	write(root, path.relative(root, path.join(runDir, "native-heartbeats.jsonl")), "");
	let tick = Date.now() - 10_000;
	let elapsed = 0;
	const time = () => new Date(tick += 10).toISOString();
	const timing = () => ({ time: time(), elapsedMs: ++elapsed });
	const startedAt = new Date(tick).toISOString();
	const seedOutput = {
		summary: "seeded workflow state",
		statePatch: [{ op: "set", path: "/topic", value: "typed orchestration" }],
	};
	const events: Record<string, unknown>[] = [
		{ ...timing(), event: "node_start", nodeId: "seed", modelCalls: 0 },
		{ ...timing(), event: "node_end", nodeId: "seed", outputDigest: hashText(JSON.stringify(seedOutput)), frontier: ["a", "b"] },
	];
	if (options.schedulerSelection) {
		const selected = [...options.schedulerSelection].sort();
		const stdout = JSON.stringify({
			verdict: "RUN",
			summary: JSON.stringify({ nodeIds: selected, reason: "fixture" }),
		});
		write(root, path.relative(root, path.join(runDir, "scheduler-responses/1.stdout")), stdout);
		write(root, path.relative(root, path.join(runDir, "scheduler-responses/1.stderr")), "");
		write(root, path.relative(root, path.join(runDir, "scheduler-responses/1.result.json")), `${JSON.stringify({
			model: "codex/gpt-5.6-luna",
			legalFrontier: ["a", "b"],
			visibleStateFields: [],
			exitCode: 0,
			terminationClass: "COMPLETED",
			stdoutSha256: hashText(stdout),
			stderrSha256: hashText(""),
		}, null, 2)}\n`);
		write(root, path.relative(root, path.join(runDir, "scheduler-decisions.jsonl")), `${JSON.stringify({
			...timing(),
			legalFrontier: ["a", "b"],
			selected,
			modelCalls: 1,
		})}\n`);
	}
	if (options.completedB) {
		const stdout = '{"summary":"b","statePatch":[{"op":"set","path":"/summaryB","value":"B"}]}';
		write(root, path.relative(root, path.join(runDir, "native-responses/b.stdout")), stdout);
		write(root, path.relative(root, path.join(runDir, "native-responses/b.stderr")), "");
		write(root, path.relative(root, path.join(runDir, "native-responses/b.result.json")), `${JSON.stringify({
			exitCode: 0,
			terminationClass: "COMPLETED",
			stdoutSha256: hashText(stdout),
			stderrSha256: hashText(""),
		}, null, 2)}\n`);
		const output = JSON.parse(stdout);
		events.push(
			{ ...timing(), event: "node_start", nodeId: "b", modelCalls: 2 },
			{ ...timing(), event: "node_end", nodeId: "b", outputDigest: hashText(JSON.stringify(output)), frontier: ["a"] },
		);
	}
	if (options.inFlightA) events.push({ ...timing(), event: "node_start", nodeId: "a", modelCalls: 2 });
	write(root, path.relative(root, path.join(runDir, "events.jsonl")), events.map(event => JSON.stringify(event)).join("\n") + "\n");
	return { startedAt, updatedAt: time() };
}

function rewindBeforeTerminal(runDir: string, state: Record<string, any>): Record<string, any> {
	const checkpoint = structuredClone(state);
	checkpoint.status = "running";
	checkpoint.completionPhase = "running";
	checkpoint.terminal = "";
	checkpoint.frontier = ["success"];
	checkpoint.pending = [];
	checkpoint.completed = checkpoint.completed.filter((nodeId: string) => nodeId !== "success");
	delete checkpoint.outputs.success;
	checkpoint.forcedReason = "";
	checkpoint.updatedAt = new Date().toISOString();
	fs.writeFileSync(path.join(runDir, "state.json"), `${JSON.stringify(checkpoint, null, 2)}\n`);
	const events = fs.readFileSync(path.join(runDir, "events.jsonl"), "utf8")
		.split(/\r?\n/u)
		.filter(Boolean)
		.map(line => JSON.parse(line))
		.filter(event => event.nodeId !== "success");
	fs.writeFileSync(path.join(runDir, "events.jsonl"), events.map(event => JSON.stringify(event)).join("\n") + "\n");
	fs.rmSync(path.join(runDir, "result.json"));
	return checkpoint;
}

describe("subagent workflow controller", () => {
	test("legacy none review policy fails with an actionable migration error", async () => {
		const item = fixture({ governance: { reviewPolicy: "none", completionProof: "none" } });
		await expect(runController(options(item.root))).rejects.toThrow(
			"reviewPolicy 'none' is no longer supported; migrate to independent or machine-gated",
		);
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("CLI grammar rejects control-plane overrides and duplicate options", () => {
		expect(parseCliArguments(["validate", ".subagent/flows/x.json"])).toEqual({
			command: "validate",
			mappingPath: ".subagent/flows/x.json",
		});
		expect(parseCliArguments(["start", ".subagent/flows/x.json", "--input", "input.json", "--resume", "workflow-output/subagent/x/run"])).toEqual({
			command: "start",
			mappingPath: ".subagent/flows/x.json",
			inputPath: "input.json",
			resume: "workflow-output/subagent/x/run",
		});
		expect(() => parseCliArguments(["start", ".subagent/flows/x.json", "--output-root", ".pm/reviews"]))
			.toThrow("unsupported start option --output-root");
		expect(() => parseCliArguments(["start", ".subagent/flows/x.json", "--input", "a", "--input", "b"]))
			.toThrow("duplicate start option --input");
		expect(() => parseCliArguments(["validate", ".subagent/flows/x.json", "--roles", "other.json"]))
			.toThrow("validate accepts only a mapping path");
		expect(parseCliArguments(["finalize-only", ".subagent/flows/x.json", "--resume", "workflow-output/subagent/x/run"]))
			.toEqual({ command: "finalize-only", mappingPath: ".subagent/flows/x.json", resume: "workflow-output/subagent/x/run" });
		expect(() => parseCliArguments(["finalize-only", ".subagent/flows/x.json", "--input", "input.json"]))
			.toThrow("requires exactly");
		expect(parseCliArguments(["verify-proof", ".subagent/flows/x.json", "--resume", "workflow-output/subagent/x/run"]))
			.toEqual({ command: "verify-proof", mappingPath: ".subagent/flows/x.json", resume: "workflow-output/subagent/x/run" });
		expect(() => parseCliArguments(["verify-proof", ".subagent/flows/x.json", "--input", "input.json"]))
			.toThrow("verify-proof requires exactly");
	});

	test("scheduler calls require an explicit deadline while a zero-call scheduler has no hidden default", async () => {
		const item = fixture();
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		delete mapping.scheduler.timeoutSeconds;
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("timeoutSeconds is required");
		mapping.scheduler.maxCalls = 0;
		mapping.scheduler.mode = "deterministic-all";
		mapping.scheduler.workspaceWriteScopes = {};
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.state.schedulerCalls).toBe(0);
	});

	test("classifies Claude SSE keepalive parse failures as transient transport", () => {
		const stderr = 'Could not parse message into JSON:\nFrom chunk: [ "event: content_block_delta", ": keepalive" ]';
		expect(classifyTermination(1, stderr)).toBe("TRANSIENT_TRANSPORT");
		expect(classifyTermination(1, stderr, true)).toBe("NODE_DEADLINE");
		expect(classifyTermination(1, "", false, "HTTP 503 service unavailable")).toBe("SERVICE_THROTTLED");
	});

	test("small scheduler can select only the legal frontier and the deterministic fan-in succeeds", async () => {
		const item = fixture();
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.result.terminal).toBe("unresolved");
		expect(result.result.reviewerTerminal).toBeUndefined();
		expect(result.result.nextAction).toBe("external terminal reviewer required before COMPLETE");
		expect(result.state.schedulerCalls).toBe(1);
		expect(result.state.nodeCalls).toBe(2);
		expect(result.state.modelCalls).toBe(3);
		expect(result.state.data.combined).toBe("A + B");
		expect(result.state.completed).toContain("success");
		expect(fs.existsSync(path.join(result.runDir, "scheduler-decisions.jsonl"))).toBeTrue();
		expect(fs.existsSync(path.join(result.runDir, "scheduler-responses/1.result.json"))).toBeTrue();
		expect(fs.existsSync(path.join(result.runDir, "state.json"))).toBeTrue();
		const evidence = result.result.evidence as Array<{ path: string; sha256: string }>;
		expect(evidence.length).toBeGreaterThan(3);
		expect(evidence.every(item => path.isAbsolute(item.path) && /^[0-9a-f]{64}$/.test(item.sha256))).toBeTrue();
	});

	test("scheduler and leaf model attempts are checkpointed before native invocation", async () => {
		const item = fixture({ checkReservations: true });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.state.modelCalls).toBe(3);
		expect(result.state.schedulerCalls).toBe(1);
		expect(result.state.nodeCalls).toBe(2);
	});

	test("deterministic-all executes the complete legal frontier without a scheduler model call", async () => {
		const item = fixture({ maxModelCalls: 2 });
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.scheduler = {
			role: "smol",
			mode: "deterministic-all",
			maxCalls: 0,
			reads: [],
			workspaceWriteScopes: {},
		};
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.state.schedulerCalls).toBe(0);
		expect(result.state.nodeCalls).toBe(2);
		expect(result.state.modelCalls).toBe(2);
		expect(fs.existsSync(path.join(result.runDir, "scheduler-decisions.jsonl"))).toBeFalse();
	});

	test("deterministic-all validates disjoint state and workspace write scopes", async () => {
		for (const variant of ["missing", "workspace-overlap", "state-overlap"] as const) {
			const item = fixture({ maxModelCalls: 2 });
			const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
			design.nodes.find((node: { id: string }) => node.id === "a").workspaceAccess = "write";
			design.nodes.find((node: { id: string }) => node.id === "b").workspaceAccess = "write";
			if (variant === "state-overlap") {
				design.nodes.find((node: { id: string }) => node.id === "b").writes = ["summaryA"];
			}
			fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
			const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
			mapping.designSha256 = digest(item.designPath);
			mapping.scheduler = {
				role: "unused-role",
				mode: "deterministic-all",
				maxCalls: 0,
				reads: [],
				workspaceWriteScopes: variant === "missing"
					? { a: ["research/a"] }
					: variant === "workspace-overlap"
						? { a: ["research/shared"], b: ["research/shared/child"] }
						: { a: ["research/a"], b: ["research/b"] },
			};
			fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
			await expect(runController(options(item.root))).rejects.toThrow(
				variant === "missing"
					? "workspace writer b has no declared write scope"
					: variant === "workspace-overlap"
						? "overlap workspace scope"
						: "overlap state write summaryA",
			);
			expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
		}
	});

	test("deterministic-all rejects a nonzero scheduler reservation", async () => {
		const item = fixture();
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.scheduler = {
			role: "smol", mode: "deterministic-all", maxCalls: 1, reads: [], workspaceWriteScopes: {},
		};
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("deterministic-all scheduler mode requires maxCalls=0");
	});

	test("model activation bound counts fan-out but excludes mutually exclusive machine-gated branches", () => {
		const fanout = {
			governance: { reviewPolicy: "independent" }, entry: "seed",
			nodes: [
				{ id: "seed", kind: "script" }, { id: "a", kind: "agent" }, { id: "b", kind: "agent" },
				{ id: "done", kind: "terminal" },
			],
			transitions: [
				{ from: "seed", to: "a" }, { from: "seed", to: "b" },
				{ from: "a", to: "done" }, { from: "b", to: "done" },
			],
		};
		expect(maxModelActivations(fanout as any)).toBe(2);
		const exclusive = {
			governance: { reviewPolicy: "machine-gated" }, entry: "choice",
			nodes: [
				{ id: "choice", kind: "decision", outcomes: ["LEFT", "RIGHT"] },
				{ id: "left", kind: "agent" }, { id: "right", kind: "agent" },
				{ id: "review", kind: "decision", outcomes: ["PASS", "BLOCK"] },
				{ id: "success", kind: "terminal" }, { id: "unresolved", kind: "terminal" },
			],
			transitions: [
				{ from: "choice", to: "left", when: { field: "choice.verdict", equals: "LEFT" } },
				{ from: "choice", to: "right", when: { field: "choice.verdict", equals: "RIGHT" } },
				{ from: "left", to: "review" }, { from: "right", to: "review" },
				{ from: "review", to: "success", when: { field: "review.verdict", equals: "PASS" } },
				{ from: "review", to: "unresolved", when: { field: "review.verdict", equals: "BLOCK" } },
			],
		};
		expect(maxModelActivations(exclusive as any)).toBe(3);
	});

	test("model activation DP handles over a million syntactic outcome assignments with a real path bound of 23", () => {
		const nodes: any[] = [];
		const transitions: any[] = [];
		for (let index = 0; index < 21; index += 1) {
			const id = `review-${index}`;
			const next = index === 20 ? "worker-a" : `review-${index + 1}`;
			nodes.push({ id, kind: "decision", outcomes: ["PASS", "BLOCK"] });
			transitions.push(
				{ from: id, to: next, when: { field: `${id}.verdict`, equals: "PASS" } },
				{ from: id, to: next, when: { field: `${id}.verdict`, equals: "BLOCK" } },
			);
		}
		nodes.push({ id: "worker-a", kind: "agent" }, { id: "worker-b", kind: "agent" }, { id: "done", kind: "terminal" });
		transitions.push({ from: "worker-a", to: "worker-b" }, { from: "worker-b", to: "done" });
		const design = { governance: { reviewPolicy: "machine-gated" }, entry: "review-0", nodes, transitions } as any;
		expect(maxModelActivations(design)).toBe(23);
		expect(maxModelActivations(design) + 1).toBe(24);
	});

	test("configured reasoning effort reaches scheduler and leaf native argv", async () => {
		const item = fixture({ roleEffort: "high" });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		const trace = fs.readFileSync(path.join(result.runDir, "native-calls.jsonl"), "utf8")
			.split(/\r?\n/u)
			.filter(Boolean)
			.map(line => JSON.parse(line));
		expect(trace).toHaveLength(3);
		expect(trace.every(record => record.effort === "high")).toBeTrue();
	});

	test("unsupported configured reasoning effort fails before creating a run", async () => {
		const item = fixture({ roleEffort: "high" });
		const nativeMap = JSON.parse(fs.readFileSync(item.nativeMapPath, "utf8"));
		nativeMap.providers.codex.effort.values = ["low"];
		fs.writeFileSync(item.nativeMapPath, `${JSON.stringify(nativeMap, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("does not support configured effort high");
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("scheduler state visibility is an explicit allowlist", async () => {
		const item = fixture();
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		const evidence = JSON.parse(fs.readFileSync(path.join(result.runDir, "scheduler-responses/1.result.json"), "utf8"));
		expect(evidence.visibleStateFields).toEqual([]);
	});

	test("required workflow skills are preflighted and materialized by the native runner", async () => {
		const item = fixture({ requiredSkillA: true });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.state.data.summaryA).toBe("A");
	});

	test("agent prompts cannot render controller metadata outside declared reads", async () => {
		const item = fixture();
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.handlers.a.prompt = "alpha {{topic}} {{completed}}";
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("not declared in reads");
	});

	test("agent prompts cannot read trusted controller runtime placeholders", async () => {
		const item = fixture();
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.handlers.a.prompt = "alpha {{controller.runRoot}}";
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("cannot read controller runtime values");
	});

	test("trusted command mappings can render the canonical controller runRoot", async () => {
		const item = fixture({ commandA: true });
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.handlers.a.argv.push("{{controller.runRoot}}");
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.runDir).toContain("workflow-output/subagent/fixture/subagent-");
	});

	test("state-mediated nodes remain fresh calls and transfer only declared checkpoint state", async () => {
		const item = fixture({ maxModelCalls: 3 });
		const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
		for (const node of design.nodes.filter((entry: { id: string }) => ["a", "b"].includes(entry.id))) node.session = "state-mediated";
		const b = design.nodes.find((node: { id: string }) => node.id === "b");
		b.reads = ["summaryA"];
		design.transitions = design.transitions.filter((edge: { from: string; to: string }) => !(edge.from === "seed" && edge.to === "b"));
		design.transitions.push({ from: "a", to: "b" });
		fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.designSha256 = digest(item.designPath);
		mapping.handlers.b.prompt = "beta {{summaryA}}";
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.state.nodeCalls).toBe(2);
		const trace = fs.readFileSync(path.join(result.runDir, "native-calls.jsonl"), "utf8");
		expect(trace).toContain("beta A");
	});

	test("command templates cannot render controller metadata outside declared reads", async () => {
		const item = fixture({ commandA: true });
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.handlers.a.argv.push("{{frontier}}");
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("not declared in reads");
	});

	test("deterministic templates cannot read undeclared workflow state", async () => {
		const item = fixture();
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.handlers.merge.values.combined = "{{summaryA}} + {{summaryB}} / {{topic}}";
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("not declared in reads");
	});

	test("scheduler selection outside the legal frontier fails closed", async () => {
		const item = fixture({ selection: ["unknown"] });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("outside the legal frontier");
		expect(result.state.completed).toEqual(["seed"]);
	});

	test("an agent cannot write an undeclared state field", async () => {
		const item = fixture({ illegalPatch: true });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("undeclared write forbidden");
		expect(result.state.data.forbidden).toBeUndefined();
	});

	test("model state patches must match the neutral design type", async () => {
		const item = fixture({ wrongType: true });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("does not match declared type string");
	});

	test("state patches reject duplicate paths", async () => {
		const item = fixture({ duplicatePatch: true });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("duplicate state path summaryA");
		expect(result.state.data.summaryA).toBeUndefined();
	});

	test("state patches commit transactionally only after every operation validates", async () => {
		const item = fixture({ partialInvalidPatch: true });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("undeclared write forbidden");
		expect(result.state.data.summaryA).toBeUndefined();
	});

	test("parent allocation rejects an over-budget workflow before launch", async () => {
		const item = fixture();
		await expect(runController({ ...options(item.root), parentBudget: {
			maxModelCalls: 1, maxWallTimeSeconds: 3600,
		} })).rejects.toThrow("parent model-call allocation");
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBe(false);
	});

	test("parent cancellation drains an active command and returns unresolved", async () => {
		const item = fixture({ commandA: true, commandTimeout: 30 });
		const marker = path.join(item.root, "command-started");
		fs.writeFileSync(item.stubPath, fs.readFileSync(item.stubPath, "utf8").replace(
			"sleep 1; if env", `touch '${marker}'; sleep 20; if env`,
		));
		const abort = new AbortController();
		const pending = runController({ ...options(item.root), signal: abort.signal });
		try {
			const deadline = performance.now() + 3000;
			while (!fs.existsSync(marker) && performance.now() < deadline) await Bun.sleep(10);
			expect(fs.existsSync(marker)).toBe(true);
		} finally {
			abort.abort();
		}
		const result = await pending;
		expect(result.state.status).toBe("unresolved");
		expect(result.state.data.summaryA).toBeUndefined();
		expect(result.state.completed).not.toContain("a");
	}, 10000);

	test("parent read-only authority refuses arbitrary workflow commands", async () => {
		const item = fixture({ commandA: true });
		await expect(runController({ ...options(item.root), parentAccess: "read" })).rejects.toThrow(
			"parent read-only authority",
		);
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBe(false);
	});

	test("node deadline closes unresolved without retrying the prompt", async () => {
		const item = fixture({ slowA: true });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("NODE_DEADLINE");
		expect(result.state.modelCalls).toBe(2);
		const trace = fs.readFileSync(path.join(result.runDir, "native-calls.jsonl"), "utf8").trim().split("\n");
		expect(trace).toHaveLength(2);
	});

	test("a command leader exit 0 is not accepted when a descendant holds the pipe to the deadline", async () => {
		const item = fixture({ commandA: true, commandBackground: true, commandTimeout: 0.1 });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("NODE_DEADLINE");
		expect(result.state.data.summaryA).toBeUndefined();
		const repeated = await runController({ ...options(item.root), resume: result.runDir });
		expect(repeated.result).toEqual(result.result);
		expect(repeated.state.completed).not.toContain("a");
	});

	test("the workflow wall-time cap also bounds deterministic command handlers", async () => {
		const item = fixture({ commandA: true, wallTimeSeconds: 0.15 });
		const runDir = path.join(item.root, "workflow-output/subagent/fixture/subagent-command-wall-cap");
		fs.mkdirSync(runDir, { recursive: true });
		const times = writeResumeEvidence(item.root, runDir, { schedulerSelection: ["b"], completedB: true });
		write(
			item.root,
			path.relative(item.root, path.join(runDir, "state.json")),
			`${JSON.stringify({
				version: 1,
				runId: "subagent-command-wall-cap",
				designId: "fixture",
				designSha256: digest(item.designPath),
				mappingSha256: digest(item.mappingPath),
				...runtimeDigests(item.root),
				status: "running",
				terminal: "",
				frontier: ["a"],
				pending: [],
				completed: ["b", "seed"],
				inFlight: "",
				data: { topic: "typed orchestration", summaryB: "B" },
				outputs: {
					seed: { summary: "seeded workflow state", statePatch: [{ op: "set", path: "/topic", value: "typed orchestration" }] },
					b: { summary: "b", statePatch: [{ op: "set", path: "/summaryB", value: "B" }] },
				},
				modelCalls: 2,
				schedulerCalls: 1,
				nodeCalls: 1,
				forcedReason: "",
				startedAt: times.startedAt,
				updatedAt: times.updatedAt,
				elapsedMs: 100,
			}, null, 2)}\n`,
		);
		write(item.root, path.relative(item.root, path.join(runDir, "native-calls.jsonl")), nativeTrace(item, runDir, 1, "scheduler", "scheduler") + nativeTrace(item, runDir, 2, "b", "beta fixture"));
		const result = await runController({ ...options(item.root), resume: runDir });
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("command node a failed (NODE_DEADLINE)");
		expect(result.state.completed).not.toContain("a");
		expect(result.state.data.summaryA).toBeUndefined();
		expect(result.state.modelCalls).toBe(2);
		expect(JSON.parse(fs.readFileSync(path.join(result.runDir, "command-responses/a.result.json"), "utf8")).terminationClass)
			.toBe("NODE_DEADLINE");
	});

	test("a workflow may omit the global wall-time cap while retaining per-node deadlines", async () => {
		const item = fixture({ wallTimeSeconds: null });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(item.design.budgets.maxWallTimeSeconds).toBeUndefined();
	});

	test("the mapping is rejected when the design cap omits scheduler calls", async () => {
		const item = fixture({ maxModelCalls: 2 });
		await expect(runController(options(item.root))).rejects.toThrow(
			"static model floor plus scheduler cap",
		);
	});

	test("resume reconciles a traced in-flight leaf and never replays it", async () => {
		const item = fixture();
		const runDir = path.join(item.root, "workflow-output/subagent/fixture/subagent-resume-test");
		fs.mkdirSync(runDir, { recursive: true });
		const times = writeResumeEvidence(item.root, runDir, { schedulerSelection: ["a", "b"], inFlightA: true });
		write(
			item.root,
			path.relative(item.root, path.join(runDir, "state.json")),
			`${JSON.stringify({
				version: 1,
				runId: "subagent-resume-test",
				designId: "fixture",
				designSha256: digest(item.designPath),
				mappingSha256: digest(item.mappingPath),
				...runtimeDigests(item.root),
				status: "running",
				terminal: "",
				frontier: ["a", "b"],
				pending: ["a", "b"],
				completed: ["seed"],
				inFlight: "a",
				data: { topic: "typed orchestration" },
				outputs: { seed: { summary: "seeded workflow state", statePatch: [{ op: "set", path: "/topic", value: "typed orchestration" }] } },
				modelCalls: 2,
				schedulerCalls: 1,
				nodeCalls: 1,
				forcedReason: "",
				startedAt: times.startedAt,
				updatedAt: times.updatedAt,
				elapsedMs: 20,
			}, null, 2)}\n`,
		);
		write(item.root, path.relative(item.root, path.join(runDir, "native-calls.jsonl")), nativeTrace(item, runDir, 1, "scheduler", "scheduler") + nativeTrace(item, runDir, 2, "a", "alpha fixture"));
		const result = await runController({ ...options(item.root), resume: runDir });
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("AMBIGUOUS_SIDE_EFFECT");
		expect(result.state.modelCalls).toBe(2);
		expect(fs.readFileSync(path.join(runDir, "native-calls.jsonl"), "utf8").trim().split("\n")).toHaveLength(2);
		const persisted = JSON.parse(fs.readFileSync(path.join(runDir, "state.json"), "utf8"));
		expect(persisted).toMatchObject({
			status: "unresolved",
			completionPhase: "finalized",
			inFlight: "",
			pending: [],
			frontier: [],
		});
		expect(JSON.parse(fs.readFileSync(path.join(runDir, "result.json"), "utf8")).stateDigest)
			.toBe(hashText(JSON.stringify(persisted)));
		const events = fs.readFileSync(path.join(runDir, "events.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line));
		expect(events.at(-1)).toMatchObject({ event: "resume_reconciled", inFlight: "a", status: "unresolved" });
		const repeated = await runController({ ...options(item.root), resume: runDir });
		expect(repeated.result).toEqual(result.result);
	});

	test("resume preserves a scheduler reservation and reconciles its native trace", async () => {
		const item = fixture();
		const runDir = path.join(item.root, "workflow-output/subagent/fixture/subagent-scheduler-crash");
		fs.mkdirSync(runDir, { recursive: true });
		const times = writeResumeEvidence(item.root, runDir);
		write(
			item.root,
			path.relative(item.root, path.join(runDir, "state.json")),
			`${JSON.stringify({
				version: 1,
				runId: "subagent-scheduler-crash",
				designId: "fixture",
				designSha256: digest(item.designPath),
				mappingSha256: digest(item.mappingPath),
				...runtimeDigests(item.root),
				status: "running",
				terminal: "",
				frontier: ["a", "b"],
				pending: [],
				completed: ["seed"],
				inFlight: "scheduler",
				data: { topic: "typed orchestration" },
				outputs: { seed: { summary: "seeded workflow state", statePatch: [{ op: "set", path: "/topic", value: "typed orchestration" }] } },
				modelCalls: 1,
				schedulerCalls: 1,
				nodeCalls: 0,
				forcedReason: "",
				startedAt: times.startedAt,
				updatedAt: times.updatedAt,
				elapsedMs: 20,
			}, null, 2)}\n`,
		);
		write(item.root, path.relative(item.root, path.join(runDir, "native-calls.jsonl")), nativeTrace(item, runDir, 1, "scheduler", "You are a workflow scheduler."));
		const result = await runController({ ...options(item.root), resume: runDir });
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("scheduler was in flight");
		expect(result.state.modelCalls).toBe(1);
		expect(result.state.schedulerCalls).toBe(1);
	});

	test("resume never lets the checkpoint undercount a valid native trace", async () => {
		const item = fixture();
		const runDir = path.join(item.root, "workflow-output/subagent/fixture/subagent-trace-ahead");
		fs.mkdirSync(runDir, { recursive: true });
		const now = new Date().toISOString();
		write(
			item.root,
			path.relative(item.root, path.join(runDir, "state.json")),
			`${JSON.stringify({
				version: 1,
				runId: "subagent-trace-ahead",
				designId: "fixture",
				designSha256: digest(item.designPath),
				mappingSha256: digest(item.mappingPath),
				...runtimeDigests(item.root),
				status: "running",
				terminal: "",
				frontier: ["seed"],
				pending: [],
				completed: [],
				inFlight: "",
				data: {},
				outputs: {},
				modelCalls: 0,
				schedulerCalls: 0,
				nodeCalls: 0,
				forcedReason: "",
				startedAt: now,
				updatedAt: now,
				elapsedMs: 0,
			}, null, 2)}\n`,
		);
		write(item.root, path.relative(item.root, path.join(runDir, "native-calls.jsonl")), nativeTrace(item, runDir, 1, "a", "alpha fixture"));
		const result = await runController({ ...options(item.root), resume: runDir });
		expect(result.state.status).toBe("unresolved");
		expect(result.state.forcedReason).toContain("trace records 1 model calls");
		expect(result.state.modelCalls).toBe(1);
		expect(result.state.nodeCalls).toBe(1);
		const persisted = JSON.parse(fs.readFileSync(path.join(runDir, "state.json"), "utf8"));
		expect(persisted).toMatchObject({
			status: "unresolved",
			completionPhase: "finalized",
			inFlight: "",
			pending: [],
			frontier: [],
		});
		const events = fs.readFileSync(path.join(runDir, "events.jsonl"), "utf8").trim().split("\n").map(line => JSON.parse(line));
		expect(events.at(-1)).toMatchObject({
			event: "resume_reconciled",
			inFlight: "",
			recordedModelCalls: 0,
			unattributedModelCalls: 1,
		});
		const repeated = await runController({ ...options(item.root), resume: runDir });
		expect(repeated.result).toEqual(result.result);
	});

	test("a single legal frontier is resumed mechanically without a scheduler call", async () => {
		const item = fixture();
		const runDir = path.join(item.root, "workflow-output/subagent/fixture/subagent-single-frontier");
		fs.mkdirSync(runDir, { recursive: true });
		const times = writeResumeEvidence(item.root, runDir, { schedulerSelection: ["b"], completedB: true });
		write(
			item.root,
			path.relative(item.root, path.join(runDir, "state.json")),
			`${JSON.stringify({
				version: 1,
				runId: "subagent-single-frontier",
				designId: "fixture",
				designSha256: digest(item.designPath),
				mappingSha256: digest(item.mappingPath),
				...runtimeDigests(item.root),
				status: "running",
				terminal: "",
				frontier: ["a"],
				pending: [],
				completed: ["b", "seed"],
				inFlight: "",
				data: { topic: "typed orchestration", summaryB: "B" },
				outputs: {
					seed: { summary: "seeded workflow state", statePatch: [{ op: "set", path: "/topic", value: "typed orchestration" }] },
					b: { summary: "b", statePatch: [{ op: "set", path: "/summaryB", value: "B" }] },
				},
				modelCalls: 2,
				schedulerCalls: 1,
				nodeCalls: 1,
				forcedReason: "",
				startedAt: times.startedAt,
				updatedAt: times.updatedAt,
				elapsedMs: 20,
			}, null, 2)}\n`,
		);
		write(item.root, path.relative(item.root, path.join(runDir, "native-calls.jsonl")), nativeTrace(item, runDir, 1, "scheduler", "scheduler") + nativeTrace(item, runDir, 2, "b", "beta fixture"));
		const result = await runController({ ...options(item.root), resume: runDir });
		expect(result.state.status).toBe("success");
		expect(result.state.schedulerCalls).toBe(1);
		expect(result.state.modelCalls).toBe(3);
		expect(result.state.data.combined).toBe("A + B");
	});

	test("command handlers receive no GENERAL_AGENT authority variables", async () => {
		const item = fixture({ commandA: true });
		process.env.GENERAL_AGENT_TEST_SECRET = "must-not-leak";
		try {
			const result = await runController(options(item.root));
			expect(result.state.status).toBe("success");
			expect(result.state.data.summaryA).toBe("A");
		} finally {
			delete process.env.GENERAL_AGENT_TEST_SECRET;
		}
	});

	for (const attack of ["run-dir", "subdir", "response-file", "event-file"] as const) {
		test(`evidence writes fail closed when an agent replaces ${attack} with a symlink`, async () => {
			const item = fixture({ attackA: attack });
			await expect(runController(options(item.root))).rejects.toThrow(/unsafe workflow evidence path|identity changed/);
			const outside = path.join(item.root, `outside-${attack}`);
			const target = path.join(outside, "target");
			if (fs.existsSync(target)) expect(fs.readFileSync(target, "utf8")).toBe("sentinel");
			expect(fs.existsSync(path.join(outside, "result.json"))).toBeFalse();
		});
	}

	for (const tamper of ["state", "events", "trace"] as const) {
		test(`write agents cannot rewrite existing ${tamper} evidence bytes`, async () => {
			const item = fixture({ tamperA: tamper });
			await expect(runController(options(item.root))).rejects.toThrow(/unsafe workflow evidence path/);
			const base = path.join(item.root, "workflow-output/subagent/fixture");
			const runDir = fs.readdirSync(base).map(name => path.join(base, name)).find(file => fs.lstatSync(file).isDirectory())!;
			expect(fs.existsSync(path.join(runDir, "result.json"))).toBeFalse();
		});
	}

	test("runtime input drift during a selected node prevents later nodes and result emission", async () => {
		const item = fixture({ driftInputA: true });
		await expect(runController(options(item.root))).rejects.toThrow("workflow runtime input drifted: input");
		const base = path.join(item.root, "workflow-output/subagent/fixture");
		const runDir = fs.readdirSync(base).map(name => path.join(base, name)).find(file => fs.lstatSync(file).isDirectory())!;
		expect(fs.existsSync(path.join(runDir, "result.json"))).toBeFalse();
		expect(fs.readFileSync(path.join(runDir, "native-calls.jsonl"), "utf8").trim().split("\n")).toHaveLength(2);
	});

	test("machine-gated DAG succeeds only after an explicit different-provider PASS review", async () => {
		const item = machineGatedFixture();
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("success");
		expect(result.result.terminal).toBe("success");
		expect(result.result.reviewerTerminal).toBe("COMPLETE");
		expect(result.state.outputs.review.verdict).toBe("PASS");
		expect(result.state.modelCalls).toBe(4);
	});

	test("machine-gated HUMAN_BLOCKED uses the exact request kind and request envelope", async () => {
		const item = machineGatedFixture();
		const response = JSON.stringify({
			verdict: "BLOCK",
			summary: `TERMINAL: HUMAN_BLOCKED\nEVIDENCE-COMMITMENT: ${FIXTURE_COMMITMENT}\nHUMAN-REQUEST-KIND: credential\nHUMAN-REQUEST: authenticate the reviewer CLI credential`,
		});
		const stub = fs.readFileSync(item.reviewerStubPath, "utf8").replace(
			`echo '{"verdict":"PASS","summary":"approved"}'`,
			`printf '%s\\n' '${response}'`,
		);
		fs.writeFileSync(item.reviewerStubPath, stub, { mode: 0o755 });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.result.terminal).toBe("unresolved");
		expect(result.result.reviewerTerminal).toBe("HUMAN_BLOCKED");
		expect(result.result.humanRequestKind).toBe("credential");
		expect(result.result.evidenceCommitment).toBe(FIXTURE_COMMITMENT);
		expect(result.result.humanRequest).toBe("authenticate the reviewer CLI credential");
	});

	test("machine-gated HUMAN_BLOCKED rejects missing, empty, or unsupported request fields", async () => {
		for (const summary of [
			`TERMINAL: HUMAN_BLOCKED\\nEVIDENCE-COMMITMENT: ${FIXTURE_COMMITMENT}\\nHUMAN-REQUEST-KIND: credential`,
			`TERMINAL: HUMAN_BLOCKED\\nEVIDENCE-COMMITMENT: ${FIXTURE_COMMITMENT}\\nHUMAN-REQUEST-KIND: tool-failure\\nHUMAN-REQUEST: retry the tool`,
			`TERMINAL: HUMAN_BLOCKED\\nEVIDENCE-COMMITMENT: ${FIXTURE_COMMITMENT}\\nHUMAN-REQUEST-KIND: decision\\nHUMAN-REQUEST: `,
			`TERMINAL: HUMAN_BLOCKED\\nEVIDENCE-COMMITMENT: ${FIXTURE_COMMITMENT}\\nHUMAN-REQUEST-KIND: external-action\\nHUMAN-REQUEST: tests are failing`,
			`TERMINAL: HUMAN_BLOCKED\\nEVIDENCE-COMMITMENT: ${FIXTURE_COMMITMENT}\\nHUMAN-REQUEST-KIND: authorization\\nHUMAN-REQUEST: \u6d4b\u8bd5\u5931\u8d25\uff0c\u8bf7\u6388\u6743\u91cd\u8bd5`,
		]) {
			const item = machineGatedFixture();
			const response = JSON.stringify({ verdict: "BLOCK", summary });
			const stub = fs.readFileSync(item.reviewerStubPath, "utf8").replace(
				`echo '{"verdict":"PASS","summary":"approved"}'`,
				`printf '%s\\n' '${response}'`,
			);
			fs.writeFileSync(item.reviewerStubPath, stub, { mode: 0o755 });
			await expect(runController(options(item.root))).rejects.toThrow(/HUMAN_BLOCKED/);
		}
	});

	test("state-bound completion fails closed without machine-gated review and a proof mapping", async () => {
		const item = fixture({ governance: { reviewPolicy: "independent", completionProof: "state-bound" } });
		await expect(runController(options(item.root))).rejects.toThrow("requires machine-gated review");
	});

	test("machine-gated decisions require exactly one conditional exit per declared outcome", async () => {
		const item = machineGatedFixture();
		item.design.transitions = item.design.transitions.filter(
			(edge: { from: string; when?: { equals?: string } }) => !(edge.from === "review" && edge.when?.equals === "BLOCK"),
		);
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("outcome BLOCK must have exactly one conditional exit");
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("machine-gated decisions reject duplicate, unconditional, and unknown verdict exits", async () => {
		for (const variant of ["duplicate", "unconditional", "unknown"] as const) {
			const item = machineGatedFixture();
			if (variant === "duplicate") {
				item.design.transitions.push({ from: "review", to: "unresolved", when: { field: "review.verdict", equals: "PASS" } });
			} else if (variant === "unconditional") {
				item.design.transitions.push({ from: "review", to: "success" });
			} else {
				item.design.transitions.push({ from: "review", to: "unresolved", when: { field: "review.verdict", equals: "MAYBE" } });
			}
			fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
			item.mapping.designSha256 = digest(item.designPath);
			fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
			await expect(runController(options(item.root))).rejects.toThrow(/machine-gated decision review/);
			expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
		}
	});

	test("machine-gated graph rejects a writer-to-success bypass around review", async () => {
		const item = machineGatedFixture();
		item.design.transitions.push({ from: "merge", to: "success" });
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("success path reaches success without a fresh PASS/APPROVE review");
	});

	test("an unrelated approval decision cannot clear a writer it does not explicitly review", async () => {
		const item = machineGatedFixture();
		item.design.nodes.find((node: { id: string }) => node.id === "b").workspaceAccess = "write";
		item.design.nodes.find((node: { id: string }) => node.id === "review").reviews = ["b"];
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("success path reaches success without a fresh PASS/APPROVE review");
	});

	test("review coverage must name an actual writer ancestor", async () => {
		const item = machineGatedFixture();
		item.design.nodes.find((node: { id: string }) => node.id === "review").reviews = ["success"];
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("references non-writer success");
	});

	test("machine-gated graph requires a fresh final review after the last writer", async () => {
		const item = machineGatedFixture();
		item.design.state.push({ name: "postWrite", type: "string" });
		item.design.nodes.splice(item.design.nodes.length - 2, 0, {
			id: "post-write", kind: "reducer", workspaceAccess: "write", reads: ["combined"], writes: ["postWrite"],
		});
		item.design.transitions = item.design.transitions.map((edge: any) =>
			edge.from === "review" && edge.when?.equals === "PASS" ? { ...edge, to: "post-write" } : edge,
		);
		item.design.transitions.push({ from: "post-write", to: "success" });
		item.mapping.handlers["post-write"] = { type: "template", values: { postWrite: "{{combined}}" } };
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("success path reaches success without a fresh PASS/APPROVE review");
	});

	test("a non-approval review outcome cannot fall through to success", async () => {
		const item = machineGatedFixture();
		item.design.transitions = item.design.transitions.map((edge: any) =>
			edge.from === "review" && edge.when?.equals === "BLOCK" ? { ...edge, to: "success" } : edge,
		);
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("success path reaches success without a fresh PASS/APPROVE review");
	});

	test("missing or malformed runtime verdict fails closed and never reaches success", async () => {
		const item = machineGatedFixture();
		const stub = fs.readFileSync(item.reviewerStubPath, "utf8").replace(
			`echo '{"verdict":"PASS","summary":"approved"}'`,
			`echo '{"summary":"missing verdict"}'`,
		);
		fs.writeFileSync(item.reviewerStubPath, stub, { mode: 0o755 });
		const result = await runController(options(item.root));
		expect(result.state.status).toBe("unresolved");
		expect(result.state.completed).not.toContain("success");
		expect(result.state.forcedReason).toContain("undeclared verdict");
	});

	test("a decision with no JSON follows malformedDecision=failure terminal semantics", async () => {
		const item = machineGatedFixture();
		item.design.governance.malformedDecision = "failure";
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		const stub = fs.readFileSync(item.reviewerStubPath, "utf8").replace(
			`echo '{"verdict":"PASS","summary":"approved"}'`,
			`echo 'review completed without a machine envelope'`,
		);
		fs.writeFileSync(item.reviewerStubPath, stub, { mode: 0o755 });

		const result = await runController(options(item.root));
		expect(result.state.status).toBe("failure");
		expect(result.state.completed).not.toContain("success");
		expect(result.state.forcedReason).toContain("returned no JSON object");
		const repeated = await runController({ ...options(item.root), resume: result.runDir });
		expect(repeated.result).toEqual(result.result);
		expect(repeated.state.completed).not.toContain("review");
	});

	test("machine-gated reviewer must resolve to a different actual native provider family", async () => {
		const item = machineGatedFixture();
		item.design.nodes.find((node: { id: string }) => node.id === "a").role = "anthropic_task";
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("same native execution provider anthropic");
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("machine-gated review cannot fake independence with two model families behind one relay harness", async () => {
		const item = machineGatedFixture();
		const nativeMap = JSON.parse(fs.readFileSync(item.nativeMapPath, "utf8"));
		nativeMap.providers.anthropic.bin = item.stubPath;
		fs.writeFileSync(item.nativeMapPath, `${JSON.stringify(nativeMap, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("same native execution provider anthropic");
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("machine-gated review compares the upstream provider after native model mapping", async () => {
		const item = machineGatedFixture();
		item.design.nodes.find((node: { id: string }) => node.id === "review").role = "codex_advisor";
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		const roles = JSON.parse(fs.readFileSync(item.rolesPath, "utf8"));
		roles.roles.codex_advisor = {
			...roles.roles.codex_advisor, backend: "cursor", provider: "cursor",
		};
		refreshAuditedIdentities(roles);
		refreshCatalogResolution(roles);
		fs.writeFileSync(item.rolesPath, `${JSON.stringify(roles, null, 2)}\n`);
		const nativeMap = JSON.parse(fs.readFileSync(item.nativeMapPath, "utf8"));
		nativeMap.providers.cursor.bin = item.reviewerStubPath;
		fs.writeFileSync(item.nativeMapPath, `${JSON.stringify(nativeMap, null, 2)}\n`);

		await expect(runController(options(item.root))).rejects.toThrow(
			"same upstream model provider openai after native model mapping",
		);
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("machine-gated reviewer must be read-only", async () => {
		const item = machineGatedFixture();
		item.design.nodes.find((node: { id: string }) => node.id === "review").workspaceAccess = "write";
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("approval decision review must be read-only");
	});

	test("machine-gated model-call budget covers all model nodes and scheduler reservations", async () => {
		const item = machineGatedFixture();
		item.design.budgets.maxModelCalls = 3;
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow(/cannot cover every model-node activation.*scheduler reservations/);
	});

	test("machine-gated budget admits mutually exclusive static branches without undercounting fan-out", async () => {
		const item = machineGatedFixture();
		item.design.budgets.maxModelCalls = 4;
		item.design.nodes.splice(1, 0, {
			id: "route", kind: "decision", role: "smol", workspaceAccess: "read", reads: ["topic"], writes: [],
			outcomes: ["LEFT", "RIGHT"], timeoutSeconds: 5,
		});
		const merge = item.design.nodes.find((node: { id: string }) => node.id === "merge");
		merge.waitFor = [];
		merge.reads = ["summaryA"];
		item.mapping.handlers.merge.values.combined = "{{summaryA}}";
		item.design.transitions = item.design.transitions.filter(
			(edge: { from: string; to: string }) => !(edge.from === "seed" && ["a", "b"].includes(edge.to)),
		);
		item.design.transitions.push(
			{ from: "seed", to: "route" },
			{ from: "route", to: "a", when: { field: "route.verdict", equals: "LEFT" } },
			{ from: "route", to: "b", when: { field: "route.verdict", equals: "RIGHT" } },
		);
		item.mapping.handlers.route = { type: "decision", prompt: "route {{topic}}" };
		fs.writeFileSync(item.designPath, `${JSON.stringify(item.design, null, 2)}\n`);
		item.mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(item.mapping, null, 2)}\n`);
		const stub = fs.readFileSync(item.stubPath, "utf8").replace(
			`  *"review"*) echo '{"verdict":"PASS","summary":"approved"}' ;;`,
			`  *"route"*) echo '{"verdict":"LEFT","summary":"left"}' ;;\n  *"review"*) echo '{"verdict":"PASS","summary":"approved"}' ;;`,
		);
		fs.writeFileSync(item.stubPath, stub, { mode: 0o755 });
		const result = await runController(options(item.root));
		expect(result.state.forcedReason).toBe("");
		expect(result.state.status).toBe("success");
		expect(result.state.modelCalls).toBe(3);
		expect(result.state.schedulerCalls).toBe(0);
	});

	test("direct start rejects unsupported required capabilities", async () => {
		const item = fixture();
		const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
		design.requiredCapabilities = ["agent-invocation", "bounded-loops"];
		fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("does not support required capability bounded-loops");
	});

	test("a workflow requiring interrupted session resume rejects a sessionless provider route", async () => {
		const item = fixture();
		const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
		design.requiredCapabilities = ["interrupted-session-resume"];
		fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow(
			"requires interrupted-session-resume but provider",
		);
	});

	test("direct start rejects persistent node sessions", async () => {
		const item = fixture();
		const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
		design.nodes.find((node: { id: string }) => node.id === "a").session = "persistent";
		fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("supports only fresh or state-mediated sessions");
	});

	test("v1 rejects cyclic graphs before any model call", async () => {
		const item = fixture();
		const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
		design.transitions.push({ from: "merge", to: "a" });
		fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("does not support cyclic graphs");
	});

	test("model and command nodes require an explicit deadline", async () => {
		const item = fixture();
		const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
		delete design.nodes.find((node: { id: string }) => node.id === "a").timeoutSeconds;
		fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("must declare a positive timeoutSeconds");
	});

	test("mapping digest drift is rejected before any model call", async () => {
		const item = fixture();
		fs.appendFileSync(item.designPath, "\n");
		await expect(runController(options(item.root))).rejects.toThrow("mapping design digest is stale");
	});

	test("malformed handlers are rejected before the output directory or any model call", async () => {
		const item = fixture();
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.handlers.a.prompt = 42;
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("agent handler a has no prompt");
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("path-unsafe node ids are rejected before response paths are created", async () => {
		const item = fixture();
		const design = JSON.parse(fs.readFileSync(item.designPath, "utf8"));
		design.nodes.find((node: { id: string }) => node.id === "a").id = "x/../a";
		design.transitions = design.transitions.map((edge: { from: string; to: string }) => ({
			...edge,
			from: edge.from === "a" ? "x/../a" : edge.from,
			to: edge.to === "a" ? "x/../a" : edge.to,
		}));
		fs.writeFileSync(item.designPath, `${JSON.stringify(design, null, 2)}\n`);
		const mapping = JSON.parse(fs.readFileSync(item.mappingPath, "utf8"));
		mapping.handlers["x/../a"] = mapping.handlers.a;
		delete mapping.handlers.a;
		mapping.designSha256 = digest(item.designPath);
		fs.writeFileSync(item.mappingPath, `${JSON.stringify(mapping, null, 2)}\n`);
		await expect(runController(options(item.root))).rejects.toThrow("path-unsafe node id");
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBeFalse();
	});

	test("existing runtime inputs cannot escape the project through a symlink", async () => {
		const item = fixture();
		const outsideRoot = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "subagent-workflow-outside-")));
		roots.push(outsideRoot);
		const outside = write(outsideRoot, "input.json", '{"topic":"outside"}\n');
		fs.rmSync(path.join(item.root, "input.json"));
		fs.symlinkSync(outside, path.join(item.root, "input.json"));
		await expect(runController(options(item.root))).rejects.toThrow("input path escapes the project root through a symlink");
	});

	test("controller output root is fixed and cannot target protected project paths", async () => {
		const item = fixture();
		await expect(runController({ ...options(item.root), outputRoot: ".pm/reviews" })).rejects.toThrow(
			"outputRoot is fixed at workflow-output/subagent",
		);
	});

	test("resume is restricted to the canonical design run directory", async () => {
		const item = fixture();
		await expect(runController({ ...options(item.root), resume: item.root })).rejects.toThrow(
			"resume directory must be an immediate child",
		);
	});

	test("resume binds roles, native routes, and workflow input bytes", async () => {
		const item = fixture();
		const completed = await runController(options(item.root));
		fs.writeFileSync(path.join(item.root, "input.json"), '{"topic":"drifted"}\n');
		await expect(
			runController({ ...options(item.root), resume: completed.runDir }),
		).rejects.toThrow("incompatible with the current design, mapping, roles, native map, or input");
	});

	test("resume rejects a zero-work checkpoint forged directly at the success frontier", async () => {
		const item = fixture();
		const runDir = path.join(item.root, "workflow-output/subagent/fixture/subagent-forged-success");
		fs.mkdirSync(runDir, { recursive: true });
		const now = new Date().toISOString();
		write(
			item.root,
			path.relative(item.root, path.join(runDir, "state.json")),
			`${JSON.stringify({
				version: 1,
				runId: "subagent-forged-success",
				designId: "fixture",
				designSha256: digest(item.designPath),
				mappingSha256: digest(item.mappingPath),
				...runtimeDigests(item.root),
				status: "running",
				terminal: "",
				frontier: ["success"],
				pending: [],
				completed: [],
				inFlight: "",
				data: { combined: "forged" },
				outputs: {},
				modelCalls: 0,
				schedulerCalls: 0,
				nodeCalls: 0,
				forcedReason: "",
				startedAt: now,
				updatedAt: now,
				elapsedMs: 0,
			}, null, 2)}\n`,
		);
		write(item.root, path.relative(item.root, path.join(runDir, "native-calls.jsonl")), "");
		await expect(runController({ ...options(item.root), resume: runDir })).rejects.toThrow(/replayed|legal DAG prefix/);
	});

	test("resume reconstructs agent outputs and rejects forged checkpoint data", async () => {
		const item = fixture();
		const completed = await runController(options(item.root));
		const checkpoint = rewindBeforeTerminal(completed.runDir, completed.state as unknown as Record<string, any>);
		checkpoint.data.summaryA = "FORGED";
		fs.writeFileSync(path.join(completed.runDir, "state.json"), `${JSON.stringify(checkpoint, null, 2)}\n`);
		await expect(runController({ ...options(item.root), resume: completed.runDir })).rejects.toThrow(/checkpoint data.*replayed/i);
	});

	test("resume rejects outputs attributed to a node that is not completed", async () => {
		const item = fixture();
		const completed = await runController(options(item.root));
		const checkpoint = rewindBeforeTerminal(completed.runDir, completed.state as unknown as Record<string, any>);
		checkpoint.outputs.success = { summary: "forged terminal" };
		fs.writeFileSync(path.join(completed.runDir, "state.json"), `${JSON.stringify(checkpoint, null, 2)}\n`);
		await expect(runController({ ...options(item.root), resume: completed.runDir })).rejects.toThrow(/outputs.*completed/i);
	});

	test("resume rejects future checkpoint timestamps that would expand the wall-time budget", async () => {
		const item = fixture();
		const completed = await runController(options(item.root));
		const checkpoint = rewindBeforeTerminal(completed.runDir, completed.state as unknown as Record<string, any>);
		checkpoint.startedAt = new Date(Date.now() + 60_000).toISOString();
		checkpoint.updatedAt = checkpoint.startedAt;
		fs.writeFileSync(path.join(completed.runDir, "state.json"), `${JSON.stringify(checkpoint, null, 2)}\n`);
		await expect(runController({ ...options(item.root), resume: completed.runDir })).rejects.toThrow("must not be in the future");
	});

	test("resume rejects a forged forcedReason that is absent from the event replay", async () => {
		const item = fixture();
		const completed = await runController(options(item.root));
		fs.rmSync(path.join(completed.runDir, "result.json"));
		const checkpoint = JSON.parse(fs.readFileSync(path.join(completed.runDir, "state.json"), "utf8"));
		checkpoint.forcedReason = "forged reason";
		fs.writeFileSync(path.join(completed.runDir, "state.json"), `${JSON.stringify(checkpoint, null, 2)}\n`);
		await expect(runController({ ...options(item.root), resume: completed.runDir })).rejects.toThrow(
			"forcedReason disagrees with replayed workflow state",
		);
	});

	test("resume rejects elapsedMs below the persisted event journal", async () => {
		const item = fixture();
		const completed = await runController(options(item.root));
		fs.rmSync(path.join(completed.runDir, "result.json"));
		const checkpoint = JSON.parse(fs.readFileSync(path.join(completed.runDir, "state.json"), "utf8"));
		checkpoint.elapsedMs = 0;
		fs.writeFileSync(path.join(completed.runDir, "state.json"), `${JSON.stringify(checkpoint, null, 2)}\n`);
		await expect(runController({ ...options(item.root), resume: completed.runDir })).rejects.toThrow(
			"invalid or non-monotonic elapsedMs",
		);
	});

	test("resume rejects native trace metadata that disagrees with the replayed invocation", async () => {
		const item = fixture();
		const completed = await runController(options(item.root));
		fs.rmSync(path.join(completed.runDir, "result.json"));
		const tracePath = path.join(completed.runDir, "native-calls.jsonl");
		const records = fs.readFileSync(tracePath, "utf8").trim().split(/\r?\n/u).map(line => JSON.parse(line));
		records[0].effort = "forged";
		fs.writeFileSync(tracePath, `${records.map(record => JSON.stringify(record)).join("\n")}\n`);
		await expect(runController({ ...options(item.root), resume: completed.runDir })).rejects.toThrow(
			"native call 1 effort disagrees with its authenticated launch receipt",
		);
	});

	test("a terminal checkpoint can deterministically rebuild a missing result without replaying nodes", async () => {
		const item = fixture();
		const completed = await runController(options(item.root));
		const originalTrace = fs.readFileSync(path.join(completed.runDir, "native-calls.jsonl"));
		const originalState = fs.readFileSync(path.join(completed.runDir, "state.json"));
		fs.rmSync(path.join(completed.runDir, "result.json"));
		const rebuilt = await runController({ ...options(item.root), resume: completed.runDir });
		expect(rebuilt.state.status).toBe("success");
		expect(fs.readFileSync(path.join(completed.runDir, "native-calls.jsonl"))).toEqual(originalTrace);
		expect(fs.readFileSync(path.join(completed.runDir, "state.json"))).toEqual(originalState);
		expect((rebuilt.result.evidence as unknown[]).length).toBeGreaterThan(3);
	});
});
