import { afterEach, describe, expect, setDefaultTimeout, test } from "bun:test";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { canonicalJson } from "../../scripts/controller/state_bound_proof";
import { scrubEnv } from "../../scripts/native/run";
import { canonicalModelPolicy, canonicalNativeMap } from "./fixtures/model_policy";
import "./fixtures/mock_launch_resolver";

const { parseCliArguments, runController } = await import("../../scripts/controller/run");

const sourceRoot = path.resolve(import.meta.dir, "../../../../..");
const cleanup: string[] = [];

// Portable proof runs hash the real controller and native executables on each
// start/resume.  That is deliberately more expensive than the old host-pinned
// fixture and can exceed Bun's generic five-second unit-test default.
setDefaultTimeout(30_000);

afterEach(() => {
	for (const target of cleanup.splice(0)) fs.rmSync(target, { recursive: true, force: true });
});

function sha256(file: string): string {
	return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function digestValue(value: unknown): string {
	return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

function write(root: string, relative: string, contents: string, mode?: number): string {
	const file = path.join(root, relative);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, contents, mode === undefined ? undefined : { mode });
	return file;
}

function copy(root: string, relative: string): string {
	const target = path.join(root, relative);
	fs.mkdirSync(path.dirname(target), { recursive: true });
	fs.copyFileSync(path.join(sourceRoot, relative), target);
	return target;
}

function canonicalExecutable(command: string): string {
	const located = execFileSync("/usr/bin/which", [command], { encoding: "utf8" }).trim();
	return fs.realpathSync(located);
}

type FixtureOptions = {
	probe?: "legal" | "outside" | "overwrite";
	tamperFrozen?: "action" | "helper" | "prompt";
	uiMode?: "file" | "interactive" | "linear";
};

function shellQuote(value: string): string {
	return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function writeSelfAuthoredLinearReceipt(root: string): string {
	const relative = ".pm/receipts/ui/issue-TEST-E2E.json";
	const uiContent = {
		schema: "linear-ui-mirror-content/v1",
		itemKind: "issue",
		itemId: "TEST-E2E",
		remoteKind: "issue",
		remoteId: "linear-TEST-E2E",
		targetState: "review",
		criteria: {
			path: ".pm/issues/e2e/TEST-E2E.md",
			sha256: sha256(path.join(root, ".pm/issues/e2e/TEST-E2E.md")),
		},
		report: {
			path: ".pm/reports/TEST-E2E.md",
			sha256: sha256(path.join(root, ".pm/reports/TEST-E2E.md")),
		},
	};
	const uiContentSha256 = `sha256:${digestValue(uiContent)}`;
	const uiMutation = {
		operation: "save_issue",
		remoteKind: "issue",
		remoteId: "linear-TEST-E2E",
		observedState: "review",
		contentSha256: uiContentSha256,
		providerRevision: "2026-08-14T01:02:03Z",
	};
	const uiReadback = {
		operation: "get_issue",
		remoteKind: "issue",
		remoteId: "linear-TEST-E2E",
		observedState: "review",
		contentSha256: uiContentSha256,
		providerRevision: "2026-08-14T01:02:03Z",
		observedAt: "2026-08-14T01:02:04Z",
	};
	const uiReceiptMaterial = {
		schema: "linear-ui-mirror-receipt/v1",
		producer: {
			kind: "delegated-linear-worker",
			provider: "linear",
			transport: "authenticated-native-mcp",
			invocationId: "linear-mirror-issue-TEST-E2E",
		},
		request: {
			operation: "mirror-item-state-and-report",
			itemKind: "issue",
			itemId: "TEST-E2E",
			remoteKind: "issue",
			remoteId: "linear-TEST-E2E",
			transition: { from: "in_progress", to: "review" },
			contentSha256: uiContentSha256,
		},
		mutation: uiMutation,
		mutationSha256: `sha256:${digestValue(uiMutation)}`,
		readback: uiReadback,
		readbackSha256: `sha256:${digestValue(uiReadback)}`,
	};
	write(root, relative, `${JSON.stringify({
		...uiReceiptMaterial,
		receiptSha256: `sha256:${digestValue(uiReceiptMaterial)}`,
	})}\n`);
	return relative;
}

function fixture(options: FixtureOptions = {}) {
	const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "state-bound-controller-repo-")));
	const external = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "state-bound-controller-native-")));
	cleanup.push(root, external);

	for (const relative of [
		".claude/skills/subagent-workflow/scripts/controller/run.ts",
		".claude/skills/subagent-workflow/scripts/controller/launch_resolver.ts",
		".claude/skills/subagent-workflow/scripts/controller/state_bound_proof.ts",
		".claude/skills/subagent-workflow/scripts/controller/actions/reward_evidence.ts",
		".claude/skills/subagent-workflow/scripts/native/run.ts",
		".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py",
		".claude/skills/update-platform-config/scripts/config-sync/project_paths.py",
		".claude/skills/update-platform-config/scripts/config-sync/validate_model_roles.py",
		".claude/skills/update-platform-config/scripts/config-sync/live_catalog.py",
		".claude/skills/update-platform-config/scripts/config-sync/resolve_launch_spec.py",
	]) copy(root, relative);

	const invocationLog = path.join(external, "native-invocations.log");
	const quotedLog = shellQuote(invocationLog);
	const frozenTarget = options.tamperFrozen === "action"
		? ".claude/skills/subagent-workflow/scripts/controller/actions/reward_evidence.ts"
		: options.tamperFrozen === "helper"
			? ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py"
			: options.tamperFrozen === "prompt"
				? ".workflow/resources/review-prompt.md"
				: "";
	const tamper = frozenTarget
		? `cp ${shellQuote(path.join(root, frozenTarget))} ${shellQuote(path.join(external, "saved-frozen"))}\nprintf 'temporary mutation\\n' >> ${shellQuote(path.join(root, frozenTarget))}\ncp ${shellQuote(path.join(external, "saved-frozen"))} ${shellQuote(path.join(root, frozenTarget))}\n`
		: "";
	const writer = write(external, "real/writer", `#!/bin/sh\nprintf 'writer\\n' >> ${quotedLog}\nprintf 'changed\\n' > work.txt\n${tamper}printf '%s\\n' '{"summary":"writer completed","statePatch":[{"op":"set","path":"/writerSummary","value":"changed"}]}'\n`, 0o755);
	const reviewer = write(external, "real/reviewer", `#!/bin/sh\nprintf 'reviewer\\n' >> ${quotedLog}\nprintf '%s\\n' '{"verdict":"PASS","summary":"reviewed evidence"}'\n`, 0o755);
	const probeBody = options.probe === "outside"
		? `printf 'undeclared\\n' > "$1/undeclared.txt"\n`
		: options.probe === "overwrite"
			? `printf 'forged trace\\n' > "$1/native-calls.jsonl"\n`
			: "";
	const probe = write(external, "real/probe", `#!/bin/sh\nmkdir -p "$1/probe"\nprintf 'owned\\n' > "$1/probe/owned.txt"\n${probeBody}printf '%s\\n' '{"summary":"probe completed","statePatch":[]}'\n`, 0o755);
	const bin = path.join(external, "bin");
	fs.mkdirSync(bin);
	fs.symlinkSync(writer, path.join(bin, "writer-native"));
	fs.symlinkSync(reviewer, path.join(bin, "reviewer-native"));
	fs.symlinkSync(probe, path.join(bin, "probe-native"));

	const python = canonicalExecutable("python3");
	const git = canonicalExecutable("git");
	const design = {
		schema: "workflow-design/v1",
		id: "state-bound-controller-e2e",
		entry: "seed",
		governance: { reviewPolicy: "machine-gated", completionProof: "state-bound", malformedDecision: "unresolved" },
		budgets: { maxModelCalls: 2, maxWallTimeSeconds: 120 },
		requiredCapabilities: ["machine-gated-review", "state-bound-proof"],
		state: [
			{ name: "itemId", type: "string" },
			{ name: "baselinePath", type: "string" },
			{ name: "baselineSummary", type: "string" },
			{ name: "baselineDigest", type: "string" },
			{ name: "writerSummary", type: "string" },
			{ name: "evidence", type: "string" },
			{ name: "evidencePath", type: "string" },
			{ name: "evidenceDigest", type: "string" },
			{ name: "stateBinding", type: "string" },
		],
		nodes: [
			{ id: "seed", kind: "script", reads: [], writes: ["itemId"] },
			{ id: "probe", kind: "script", reads: [], writes: [], timeoutSeconds: 20 },
			{ id: "baseline", kind: "script", reads: [], writes: ["baselinePath", "baselineSummary", "baselineDigest"], timeoutSeconds: 20 },
			{ id: "writer", kind: "agent", role: "task", session: "fresh", workspaceAccess: "write", reads: ["baselineDigest"], writes: ["writerSummary"], timeoutSeconds: 20 },
			{ id: "evidence", kind: "script", reads: ["baselinePath", "baselineDigest", "writerSummary"], writes: ["evidence", "evidencePath", "evidenceDigest", "stateBinding"], timeoutSeconds: 20 },
			{ id: "review", kind: "decision", role: "advisor", session: "fresh", workspaceAccess: "read", reads: ["writerSummary", "evidencePath", "evidenceDigest"], writes: [], outcomes: ["PASS", "BLOCK"], reviews: ["writer"], timeoutSeconds: 20 },
			{ id: "success", kind: "terminal", outcome: "success", reads: ["evidenceDigest"], writes: [] },
			{ id: "unresolved", kind: "terminal", outcome: "unresolved", reads: [], writes: [] },
		],
		transitions: [
			{ from: "seed", to: "probe" },
			{ from: "probe", to: "baseline" },
			{ from: "baseline", to: "writer" },
			{ from: "writer", to: "evidence" },
			{ from: "evidence", to: "review" },
			{ from: "review", to: "success", when: { field: "review.verdict", equals: "PASS" } },
			{ from: "review", to: "unresolved", when: { field: "review.verdict", equals: "BLOCK" } },
		],
		terminals: { success: "success", unresolved: "unresolved" },
	};
	const designPath = write(root, ".workflow/designs/state-bound-controller-e2e.workflow.json", `${JSON.stringify(design, null, 2)}\n`);
	write(root, "resources.json", '{"schema":"test-resources/v1"}\n');
	const reviewPrompt = "Review the final evidence commitment.";
	write(root, ".workflow/resources/review-prompt.md", `${reviewPrompt}\n`);
	const action = path.join(root, ".claude/skills/subagent-workflow/scripts/controller/actions/reward_evidence.ts");
	const helper = path.join(root, ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py");
	const common = [
		"--project-root", "{{controller.projectRoot}}",
		"--run-root", "{{controller.runRoot}}",
		"--helper", "{{controller.projectRoot}}/.claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py",
		"--helper-sha256", sha256(helper),
		"--python", "{{executable.python.path}}",
		"--python-sha256", "{{executable.python.sha256}}",
		"--git", "{{executable.git.path}}",
		"--git-sha256", "{{executable.git.sha256}}",
	];
	const mapping = {
		schema: "subagent-workflow/v1",
		designPath: ".workflow/designs/state-bound-controller-e2e.workflow.json",
		designSha256: sha256(designPath),
		inputPath: "input.json",
		reasoningPosture: "balanced",
		scheduler: {
			role: "smol",
			mode: "deterministic-all",
			maxCalls: 0,
			reads: [],
			workspaceWriteScopes: { writer: ["work.txt"] },
		},
		proof: {
			itemIdField: "itemId",
			evidencePathField: "evidencePath",
			evidenceDigestField: "evidenceDigest",
			helperPath: ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py",
			resourcesManifestPath: "resources.json",
			resources: { "review-prompt": ".workflow/resources/review-prompt.md" },
			finalReviewNodes: ["review"],
			evidenceProducerNodes: ["evidence"],
			currentStateTimeoutSeconds: 20,
			actionPath: ".claude/skills/subagent-workflow/scripts/controller/actions/reward_evidence.ts",
			executables: {
				"controller-runtime": { source: "controller-runtime" },
				python: { source: "path", command: "python3" },
				git: { source: "path", command: "git" },
				"native-task": { source: "native-role", role: "task" },
				"native-advisor": { source: "native-role", role: "advisor" },
				probe: { source: "path", command: "probe-native" },
			},
		},
		handlers: {
			seed: { type: "input", bindings: { itemId: "itemId" } },
			probe: {
				type: "command",
				argv: ["{{executable.probe.path}}", "{{controller.runRoot}}"],
				runWrites: ["probe/owned.txt"],
			},
			baseline: {
				type: "command",
				argv: ["{{executable.controller-runtime.path}}", ".claude/skills/subagent-workflow/scripts/controller/actions/reward_evidence.ts", "baseline", ...common, "--output", "baseline.json"],
				runWrites: ["baseline.json"],
			},
			writer: { type: "agent", prompt: "write the fixture after baseline {{baselineDigest}}" },
			evidence: {
				type: "command",
				argv: ["{{executable.controller-runtime.path}}", ".claude/skills/subagent-workflow/scripts/controller/actions/reward_evidence.ts", "evidence", ...common, "--baseline", "{{baselinePath}}", "--expected-baseline-digest", "{{baselineDigest}}", "--acceptance-argv-json", JSON.stringify([python, "-c", "from pathlib import Path; raise SystemExit(0 if Path('work.txt').read_text() == 'changed\\n' else 1)"]), "--acceptance-timeout", "5", "--output", "final-evidence.json"],
				runWrites: ["final-evidence.json"],
			},
			review: { type: "decision", prompt: `${reviewPrompt} {{writerSummary}} {{evidencePath}} {{evidenceDigest}}\n{{review.commitment}}` },
			success: { type: "terminal" },
			unresolved: { type: "terminal" },
		},
	};
	write(root, ".subagent/flows/state-bound-controller-e2e.json", `${JSON.stringify(mapping, null, 2)}\n`);
	const rolesPath = write(root, "config/workflows/models.json", `${JSON.stringify(canonicalModelPolicy(), null, 2)}\n`);
	const uiMode = options.uiMode ?? "file";
	write(root, ".pm/config.json", `${JSON.stringify({ debug: false, ui_mode: uiMode })}\n`);
	write(root, ".pm/goal.md", "# Goal Tracker\n\n## Ultimate Goal\nShip the fixture.\n\n## Acceptance Criteria\n- TEST-E2E passes.\n");
	write(root, ".pm/milestones/e2e.md", "---\nid: e2e\nstatus: review\n---\n\n## Acceptance Criteria\n\nFixture milestone is reviewable.\n");
	write(root, ".pm/issues/e2e/TEST-E2E.md", "---\nid: TEST-E2E\nmilestone: e2e\nlinear_id: linear-TEST-E2E\nstatus: review\n---\n\n## Acceptance Criteria\n\nState-bound workflow succeeds.\n");
	write(root, ".pm/reports/TEST-E2E.md", "# report: issue TEST-E2E\n\nFocused evidence is current.\n");
	const declared = (relative: string) => ({ path: relative, sha256: sha256(path.join(root, relative)) });
	const uiReceipts = uiMode === "linear" ? [declared(writeSelfAuthoredLinearReceipt(root))] : [];
	write(root, "input.json", `${JSON.stringify({
		itemId: "TEST-E2E",
		proofBinding: {
			schema: "subagent-proof-binding/v1",
			scope: "pm-item",
			itemKind: "issue",
			itemId: "TEST-E2E",
			milestoneId: "e2e",
			roleModelPolicyDigest: sha256(rolesPath),
			config: declared(".pm/config.json"),
			goal: declared(".pm/goal.md"),
			milestone: declared(".pm/milestones/e2e.md"),
			criteria: declared(".pm/issues/e2e/TEST-E2E.md"),
			report: declared(".pm/reports/TEST-E2E.md"),
			children: [],
			uiReceipts,
		},
	}, null, 2)}\n`);
	write(
		root,
		".claude/skills/subagent-workflow/scripts/native/native-map.json",
		`${JSON.stringify(canonicalNativeMap({ codex: writer, anthropic: reviewer, cursor: reviewer }), null, 2)}\n`,
	);
	write(root, "work.txt", "original\n");
	execFileSync(git, ["init", "-q"], { cwd: root });
	execFileSync(git, ["config", "user.email", "fixture@example.invalid"], { cwd: root });
	execFileSync(git, ["config", "user.name", "Fixture"], { cwd: root });
	execFileSync(git, ["add", "."], { cwd: root });
	execFileSync(git, ["commit", "-qm", "fixture"], { cwd: root });

	return {
		root,
		external,
		bin,
		invocationLog,
		writer,
		action,
		helper,
		prompt: path.join(root, ".workflow/resources/review-prompt.md"),
	};
}

type Fixture = ReturnType<typeof fixture>;

function controllerOptions(item: Fixture) {
	return {
		root: item.root,
		mappingPath: ".subagent/flows/state-bound-controller-e2e.json",
		rolesPath: "config/workflows/models.json",
		nativeMapPath: ".claude/skills/subagent-workflow/scripts/native/native-map.json",
		outputRoot: "workflow-output/subagent",
	};
}

async function withControllerEnvironment<T>(item: Fixture, action: () => Promise<T>): Promise<T> {
	const previousPath = process.env.PATH;
	const previousAuthority = process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE;
	process.env.PATH = `${item.bin}${path.delimiter}${previousPath ?? ""}`;
	process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE = "1";
	try {
		return await action();
	} finally {
		if (previousPath === undefined) delete process.env.PATH;
		else process.env.PATH = previousPath;
		if (previousAuthority === undefined) delete process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE;
		else process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE = previousAuthority;
	}
}

function readJson(file: string): Record<string, any> {
	return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeJson(file: string, value: unknown): void {
	fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function fileIdentity(file: string): { sha256: string; mtimeNs: bigint; size: bigint } {
	const stat = fs.statSync(file, { bigint: true });
	return { sha256: sha256(file), mtimeNs: stat.mtimeNs, size: stat.size };
}

function proofAuditFiles(item: Fixture, runDir: string): string[] {
	return [
		path.join(runDir, "state.json"),
		path.join(runDir, "result.json"),
		path.join(runDir, "state-bound-candidate.json"),
		path.join(runDir, "frozen-artifacts.json"),
		path.join(runDir, "native-calls.jsonl"),
		path.join(runDir, "machine-reviews.jsonl"),
		path.join(runDir, "native-responses/review.result.json"),
		path.join(item.root, ".pm/workflow-reviews/TEST-E2E.md"),
	];
}

function restoreCrashWindow(item: Fixture, runDir: string, phase: "terminal" | "candidate"): void {
	fs.rmSync(path.join(runDir, "result.json"), { force: true });
	fs.rmSync(path.join(item.root, ".pm/workflow-reviews/TEST-E2E.md"), { force: true });
	const statePath = path.join(runDir, "state.json");
	const state = readJson(statePath);
	state.completionPhase = phase;
	state.proofPath = "";
	state.proofSha256 = "";
	if (phase === "terminal") {
		state.candidateDigest = "";
		fs.rmSync(path.join(runDir, "state-bound-candidate.json"), { force: true });
	}
	writeJson(statePath, state);
}

describe("state-bound controller end to end", () => {
	test("rejects a perfectly self-authored Linear receipt before capture, activation, or proof output", async () => {
		const item = fixture({ uiMode: "linear" });
		await withControllerEnvironment(item, async () => {
			await expect(runController(controllerOptions(item)))
				.rejects.toThrow("authenticated controller-owned Linear evidence is unavailable");
		});
		expect(fs.existsSync(item.invocationLog)).toBe(false);
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBe(false);
		expect(fs.existsSync(path.join(item.root, ".pm/workflow-reviews/TEST-E2E.md"))).toBe(false);
	});

	test("preflight rejects resolved host bindings in a tracked portable mapping before any activation", async () => {
		const item = fixture();
		const mappingPath = path.join(item.root, ".subagent/flows/state-bound-controller-e2e.json");
		const mapping = readJson(mappingPath);
		const runtime = fs.realpathSync(process.execPath);
		mapping.proof.executables.probe = { path: runtime, sha256: sha256(runtime) };
		writeJson(mappingPath, mapping);
		await withControllerEnvironment(item, async () => {
			await expect(runController(controllerOptions(item))).rejects.toThrow("must be a portable source declaration");
		});
		expect(fs.existsSync(item.invocationLog)).toBe(false);
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBe(false);
	});

	test("preflight rejects malformed and undeclared executable placeholders before any activation", async () => {
		for (const variant of ["malformed", "undeclared"] as const) {
			const item = fixture();
			const mappingPath = path.join(item.root, ".subagent/flows/state-bound-controller-e2e.json");
			const mapping = readJson(mappingPath);
			mapping.handlers.probe.argv[1] = variant === "malformed"
				? "{{executable.probe.digest}}"
				: "{{executable.missing.sha256}}";
			writeJson(mappingPath, mapping);
			await withControllerEnvironment(item, async () => {
				await expect(runController(controllerOptions(item))).rejects.toThrow(
					variant === "malformed" ? "references malformed executable value" : "references undeclared executable value",
				);
			});
			expect(fs.existsSync(item.invocationLog)).toBe(false);
			expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBe(false);
		}
	});

	test("preflight rejects an unavailable PATH executable before any command or model activation", async () => {
		const item = fixture();
		const mappingPath = path.join(item.root, ".subagent/flows/state-bound-controller-e2e.json");
		const mapping = readJson(mappingPath);
		mapping.proof.executables.probe = { source: "path", command: "portable-resolver-definitely-absent-7f3c" };
		writeJson(mappingPath, mapping);
		await withControllerEnvironment(item, async () => {
			await expect(runController(controllerOptions(item))).rejects.toThrow("portable executable command is unavailable on PATH");
		});
		expect(fs.existsSync(item.invocationLog)).toBe(false);
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBe(false);
	});

	test("proof Python and Git helpers ignore caller-PATH shadows", async () => {
		const item = fixture();
		const pythonMarker = path.join(item.root, "poison-python-ran");
		const gitMarker = path.join(item.root, "poison-git-ran");
		fs.writeFileSync(
			path.join(item.bin, "python3"),
			`#!/bin/sh\ntouch '${pythonMarker}'\nexec /usr/bin/python3 "$@"\n`,
			{ mode: 0o755 },
		);
		fs.writeFileSync(
			path.join(item.bin, "git"),
			`#!/bin/sh\ntouch '${gitMarker}'\nexec /usr/bin/git "$@"\n`,
			{ mode: 0o755 },
		);
		const result = await withControllerEnvironment(item, () => runController(controllerOptions(item)));
		expect(result.state.status).toBe("success");
		expect(fs.existsSync(pythonMarker)).toBeFalse();
		expect(fs.existsSync(gitMarker)).toBeFalse();
		const candidate = readJson(path.join(result.runDir, "state-bound-candidate.json"));
		const helpers = Object.fromEntries(
			candidate.frozen.executables
				.filter((entry: Record<string, string>) => ["python", "git"].includes(entry.label))
				.map((entry: Record<string, string>) => [entry.label, entry.path]),
		);
		expect(helpers.python).toBe(fs.realpathSync("/usr/bin/python3"));
		expect(helpers.git).toBe(fs.realpathSync("/usr/bin/git"));
	});

	test("preflight rejects a final review that omits the controller prompt commitment before any model call", async () => {
		const item = fixture();
		const mappingPath = path.join(item.root, ".subagent/flows/state-bound-controller-e2e.json");
		const mapping = readJson(mappingPath);
		mapping.handlers.review.prompt = "review declared state without including its evidence or state commitment";
		writeJson(mappingPath, mapping);
		await withControllerEnvironment(item, async () => {
			await expect(runController(controllerOptions(item))).rejects.toThrow("must contain exactly one {{review.commitment}} marker");
		});
		expect(fs.existsSync(item.invocationLog)).toBe(false);
		expect(fs.existsSync(path.join(item.root, "workflow-output/subagent/state-bound-controller-e2e"))).toBe(false);
	});

	test("preflight rejects a model or arbitrary command as the final evidence producer before any model call", async () => {
		for (const mode of ["model", "arbitrary-command"] as const) {
			const item = fixture();
			const mappingPath = path.join(item.root, ".subagent/flows/state-bound-controller-e2e.json");
			const mapping = readJson(mappingPath);
			if (mode === "model") mapping.proof.evidenceProducerNodes = ["writer"];
			else mapping.handlers.evidence.argv[1] = ".claude/skills/subagent-workflow/scripts/controller/actions/validate_lemmas.ts";
			writeJson(mappingPath, mapping);
			await withControllerEnvironment(item, async () => {
				await expect(runController(controllerOptions(item))).rejects.toThrow(/must be a deterministic script command|audited action\/helper\/output schema/u);
			});
			expect(fs.existsSync(item.invocationLog)).toBe(false);
		}
	});

	test("happy path finalizes proof and finalize-only replays no scheduler or model call", async () => {
		const item = fixture();
		await withControllerEnvironment(item, async () => {
			const poisoned = {
				OPENAI_API_KEY: "caller-openai-key-rejected",
				OPENAI_ORG_ID: "caller-openai-org-rejected",
				CODEX_API_KEY: "caller-codex-key-rejected",
				CODEX_SESSION: "caller-codex-session-rejected",
				CODEX_HOME: "/tmp/attacker-codex-home",
				CURSOR_API_KEY: "caller-cursor-key-rejected",
				CURSOR_AUTH_TOKEN: "caller-cursor-token-rejected",
				CURSOR_FUTURE_ACCOUNT_SELECTOR: "caller-cursor-future-rejected",
			};
			const previous = Object.fromEntries(
				Object.keys(poisoned).map(name => [name, process.env[name]]),
			) as Record<string, string | undefined>;
			for (const [name, value] of Object.entries(poisoned)) process.env[name] = value;
			try {
				const launchEnv = scrubEnv(process.env);
				expect(launchEnv.CURSOR_API_KEY).toBeUndefined();
				expect(launchEnv.CURSOR_AUTH_TOKEN).toBeUndefined();
				expect(launchEnv.CURSOR_FUTURE_ACCOUNT_SELECTOR).toBeUndefined();
				expect(Object.keys(launchEnv).filter(key => key.startsWith("CURSOR_"))).toEqual([]);
				expect(Object.keys(launchEnv).filter(key => key.startsWith("OPENAI_"))).toEqual([]);
				expect(Object.keys(launchEnv).filter(key => key.startsWith("CODEX_"))).toEqual(["CODEX_HOME"]);
				const options = controllerOptions(item);
				const first = await runController(options);
			if (first.state.status !== "success") {
				throw new Error(`unexpected terminal ${first.state.status}: ${first.state.forcedReason}`);
			}
			expect(first.state.status).toBe("success");
			expect(first.state.completionPhase).toBe("finalized");
			expect(first.state.schedulerCalls).toBe(0);
			expect(first.state.modelCalls).toBe(2);
			const traceRecords = fs.readFileSync(path.join(first.runDir, "native-calls.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line) as Record<string, any>);
			expect(traceRecords).toHaveLength(2);
			for (const trace of traceRecords) {
				const receiptPath = path.join(first.runDir, `launch-receipts/${trace.call}.json`);
				const receipt = readJson(receiptPath);
				const resolved = receipt.resolved;
				const command = receipt.policy.freshCatalogEvidence[resolved.backend].command;
				const prefix = command.invokedArgv.slice(
					0,
					command.invokedArgv.length - command.argv.length + 1,
				);
				expect(trace).toMatchObject({
					model: `${resolved.provider}/${resolved.catalogModelId}`,
					catalogModelId: resolved.catalogModelId,
					executable: command.executable,
					processExecutable: prefix[0],
					executableSha256: command.executableSha256.slice("sha256:".length),
					executionClosureSha256: command.executionClosureSha256.slice("sha256:".length),
					invocationPrefixSha256: createHash("sha256").update(JSON.stringify(prefix)).digest("hex"),
					launchReceiptSha256: sha256(receiptPath),
				});
				expect(trace.argvSha256).toMatch(/^[0-9a-f]{64}$/u);
				expect(trace.environmentSha256).toMatch(/^[0-9a-f]{64}$/u);
				if (resolved.backend === "claude") expect(trace.protectedPathStateSha256).toBeUndefined();
				else expect(trace.protectedPathStateSha256).toMatch(/^[0-9a-f]{64}$/u);
			}
			expect(fs.readFileSync(path.join(first.runDir, "probe/owned.txt"), "utf8")).toBe("owned\n");
			expect(readJson(path.join(first.runDir, "command-responses/probe.result.json")).runWrites)
				.toEqual([{ path: "probe/owned.txt", sha256: sha256(path.join(first.runDir, "probe/owned.txt")) }]);
				expect(fs.existsSync(path.join(item.root, ".pm/workflow-reviews/TEST-E2E.md"))).toBe(true);
				const proofPath = path.join(item.root, ".pm/workflow-reviews/TEST-E2E.md");
				const verified = execFileSync(canonicalExecutable("python3"), [
					item.helper, "verify-proof", "--root", item.root, "--item-id", "TEST-E2E", "--proof", proofPath,
				], { encoding: "utf8" });
				const verifiedProof = JSON.parse(verified);
				expect(verifiedProof.valid).toBe(true);
				expect(verifiedProof.itemKind).toBe("issue");
				expect(verifiedProof.itemId).toBe("TEST-E2E");
				expect(verifiedProof.proofBinding.scope).toBe("pm-item");
				expect(verifiedProof.proofBindingDigest).toBe(verifiedProof.proofBinding.digest);
				const traceBefore = fs.readFileSync(path.join(first.runDir, "native-calls.jsonl"), "utf8");
			const invocationsBefore = fs.readFileSync(item.invocationLog, "utf8");

			const finalized = await runController({ ...options, resume: first.runDir, finalizeOnly: true });
			expect(finalized.state.status).toBe("success");
			expect(finalized.state.completionPhase).toBe("finalized");
			expect(finalized.result.modelCallsReplayed).toBe(0);
			expect(finalized.state.schedulerCalls).toBe(0);
			expect(finalized.state.modelCalls).toBe(2);
			expect(fs.readFileSync(path.join(first.runDir, "native-calls.jsonl"), "utf8")).toBe(traceBefore);
			expect(fs.readFileSync(item.invocationLog, "utf8")).toBe(invocationsBefore);
			} finally {
				for (const [name, value] of Object.entries(previous)) {
					if (value === undefined) delete process.env[name];
					else process.env[name] = value;
				}
			}
		});
	});

	for (const bindingDrift of ["milestone", "criteria", "report"] as const) {
		test(`verify-proof rejects canonical PM ${bindingDrift} byte drift`, async () => {
			const item = fixture();
			await withControllerEnvironment(item, async () => {
				const options = controllerOptions(item);
				const completed = await runController(options);
				const relative = bindingDrift === "milestone"
					? ".pm/milestones/e2e.md"
					: bindingDrift === "criteria"
						? ".pm/issues/e2e/TEST-E2E.md"
						: ".pm/reports/TEST-E2E.md";
				fs.appendFileSync(path.join(item.root, relative), "\ndrift\n");
				await expect(runController({ ...options, resume: completed.runDir, verifyProofOnly: true }))
					.rejects.toThrow(/proof binding|bytes or mode changed|declared digest/u);
			});
		});
	}

	test("start rejects a wrong canonical item identity before creating a run", async () => {
		const item = fixture();
		const inputPath = path.join(item.root, "input.json");
		const input = readJson(inputPath);
		input.proofBinding.itemId = "OTHER";
		writeJson(inputPath, input);
		await withControllerEnvironment(item, async () => {
			await expect(runController(controllerOptions(item))).rejects.toThrow("item identity disagrees");
		});
		expect(fs.existsSync(path.join(item.root, "workflow-output"))).toBe(false);
	});

	test("verify-proof is a read-only full-chain audit and rejects tampered proof artifacts", async () => {
		const item = fixture();
		await withControllerEnvironment(item, async () => {
			const options = controllerOptions(item);
			const completed = await runController(options);
			const auditOptions = { ...options, resume: completed.runDir, verifyProofOnly: true };
			const files = proofAuditFiles(item, completed.runDir);
			const before = Object.fromEntries(files.map(file => [file, fileIdentity(file)]));
			const previousAuthority = process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE;
			delete process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE;
			try {
				const audited = await runController(auditOptions);
				expect(audited.result.terminal).toBe("success");
				expect(audited.result.reviewerTerminal).toBe("COMPLETE");
				expect(audited.result.evidenceCommitment).toBe(`sha256:${completed.state.candidateDigest}`);
				expect(audited.result.modelCallsReplayed).toBe(0);
			} finally {
				if (previousAuthority !== undefined) process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE = previousAuthority;
			}
			for (const file of files) expect(fileIdentity(file)).toEqual(before[file]);

			const tamperCases: Array<{ label: string; file: string; mutate: (bytes: Buffer) => Buffer }> = [
				{
					label: "proof",
					file: path.join(item.root, ".pm/workflow-reviews/TEST-E2E.md"),
					mutate: bytes => Buffer.from(bytes.toString("utf8").replace("TERMINAL: COMPLETE", "TERMINAL: REVISE")),
				},
				{
					label: "candidate",
					file: path.join(completed.runDir, "state-bound-candidate.json"),
					mutate: bytes => Buffer.from(bytes.toString("utf8").replace('"finalizer":"controller-only"', '"finalizer":"tampered"')),
				},
				{
					label: "review receipt",
					file: path.join(completed.runDir, "native-responses/review.result.json"),
					mutate: bytes => Buffer.from(bytes.toString("utf8").replace('"exitCode": 0', '"exitCode": 1')),
				},
				{
					label: "native trace",
					file: path.join(completed.runDir, "native-calls.jsonl"),
					mutate: bytes => Buffer.concat([bytes, Buffer.from(" ")]),
				},
				{
					label: "frozen manifest",
					file: path.join(completed.runDir, "frozen-artifacts.json"),
					mutate: bytes => Buffer.from(bytes.toString("utf8").replace('"digest": "', '"digest": "0')),
				},
			];
			for (const { label, file, mutate } of tamperCases) {
				const original = fs.readFileSync(file);
				const tampered = mutate(original);
				expect(tampered.equals(original)).toBeFalse();
				fs.writeFileSync(file, tampered);
				const tamperedIdentity = fileIdentity(file);
				await expect(runController(auditOptions)).rejects.toThrow();
				expect(fileIdentity(file)).toEqual(tamperedIdentity);
				fs.writeFileSync(file, original);
				expect((await runController(auditOptions)).result.modelCallsReplayed).toBe(0);
			}

			fs.appendFileSync(item.writer, "\n# tampered runtime\n");
			const runtimeTampered = fileIdentity(item.writer);
			await expect(runController(auditOptions)).rejects.toThrow(/executable|frozen artifact/u);
			expect(fileIdentity(item.writer)).toEqual(runtimeTampered);
		});
	});

	test("finalize-only rejects native-role executable byte drift without replaying any activation", async () => {
		const item = fixture();
		await withControllerEnvironment(item, async () => {
			const options = controllerOptions(item);
			const completed = await runController(options);
			const traceBefore = fs.readFileSync(path.join(completed.runDir, "native-calls.jsonl"), "utf8");
			const invocationsBefore = fs.readFileSync(item.invocationLog, "utf8");
			fs.appendFileSync(item.writer, "\n# executable drift\n");

			await expect(runController({ ...options, resume: completed.runDir, finalizeOnly: true }))
				.rejects.toThrow(/resolved executable native-task declared digest does not match its bytes|frozen artifact manifest disagrees/u);
			expect(fs.readFileSync(path.join(completed.runDir, "native-calls.jsonl"), "utf8")).toBe(traceBefore);
			expect(fs.readFileSync(item.invocationLog, "utf8")).toBe(invocationsBefore);
		});
	});

	test("terminal and candidate crash windows finalize without calls and repeated finalization is idempotent", async () => {
		const item = fixture();
		await withControllerEnvironment(item, async () => {
			const options = controllerOptions(item);
			const first = await runController(options);
			const traceBefore = fs.readFileSync(path.join(first.runDir, "native-calls.jsonl"), "utf8");
			const invocationsBefore = fs.readFileSync(item.invocationLog, "utf8");

			restoreCrashWindow(item, first.runDir, "terminal");
			const fromTerminal = await runController({ ...options, resume: first.runDir, finalizeOnly: true });
			expect(fromTerminal.state.completionPhase).toBe("finalized");
			expect(fromTerminal.result.modelCallsReplayed).toBe(0);
			expect(fs.readFileSync(path.join(first.runDir, "native-calls.jsonl"), "utf8")).toBe(traceBefore);

			restoreCrashWindow(item, first.runDir, "candidate");
			const fromCandidate = await runController({ ...options, resume: first.runDir, finalizeOnly: true });
			expect(fromCandidate.state.completionPhase).toBe("finalized");
			expect(fromCandidate.result.modelCallsReplayed).toBe(0);

			const repeated = await runController({ ...options, resume: first.runDir, finalizeOnly: true });
			expect(repeated.result.modelCallsReplayed).toBe(0);
			expect(fs.readFileSync(path.join(first.runDir, "native-calls.jsonl"), "utf8")).toBe(traceBefore);
			expect(fs.readFileSync(item.invocationLog, "utf8")).toBe(invocationsBefore);
		});
	});

	test("proof chain binds review inputs, trace, evidence, frozen mapping, and executable closure", async () => {
		const item = fixture();
		await withControllerEnvironment(item, async () => {
			const completed = await runController(controllerOptions(item));
			const candidate = readJson(path.join(completed.runDir, "state-bound-candidate.json"));
			const review = candidate.machineReview.latest;
			expect(candidate.proofBinding.scope).toBe("pm-item");
			expect(candidate.proofBinding.itemKind).toBe("issue");
			expect(candidate.proofBinding.artifacts.map((entry: Record<string, any>) => entry.relativePath)).toEqual([
				".pm/config.json",
				".pm/goal.md",
				".pm/milestones/e2e.md",
				".pm/issues/e2e/TEST-E2E.md",
				".pm/reports/TEST-E2E.md",
			]);
			expect(review.verdict).toBe("PASS");
			expect(review.promptSha256).toMatch(/^[0-9a-f]{64}$/);
			expect(review.promptCommitmentDigest).toMatch(/^[0-9a-f]{64}$/);
			expect(review.promptPath).toBe("native-responses/review.prompt");
			expect(sha256(path.join(completed.runDir, review.promptPath))).toBe(review.promptSha256);
			expect(fs.readFileSync(path.join(completed.runDir, review.promptPath), "utf8"))
				.toContain(`\"proofBinding\":${canonicalJson(candidate.proofBinding)}`);
			expect(review.reviewedInputsDigest).toMatch(/^[0-9a-f]{64}$/);
				expect(review.reviewedStateDigest).toBe(candidate.workspace.finalStateDigest);
				expect(review.evidenceDigest).toBe(candidate.workspace.evidenceDigest);
				const evidencePayload = readJson(path.join(completed.runDir, "final-evidence.json"));
				expect(candidate.workspace.baselineDigest).toBe(evidencePayload.evidence.baselineDigest);
			expect(candidate.machineReview.nativeTrace.sha256).toBe(sha256(path.join(completed.runDir, "native-calls.jsonl")));
			expect(candidate.workspace.evidenceFile.sha256).toBe(sha256(path.join(completed.runDir, "final-evidence.json")));
			expect(candidate.evidenceProducer.nodeId).toBe("evidence");
			expect(candidate.evidenceProducer.evidencePath).toBe("final-evidence.json");
			expect(candidate.evidenceProducer.evidenceSha256).toBe(candidate.workspace.evidenceFile.sha256);
			expect(candidate.evidenceProducer.receipt.sha256)
				.toBe(sha256(path.join(completed.runDir, "command-responses/evidence.result.json")));

			const mapping = readJson(path.join(item.root, ".subagent/flows/state-bound-controller-e2e.json"));
			expect(mapping.proof.executables["controller-runtime"]).toEqual({ source: "controller-runtime" });
			expect(mapping.proof.executables.python).toEqual({ source: "path", command: "python3" });
			expect(mapping.proof.executables.git).toEqual({ source: "path", command: "git" });
			expect(mapping.proof.executables["native-task"]).toEqual({ source: "native-role", role: "task" });
			expect(mapping.proof.executables["native-advisor"]).toEqual({ source: "native-role", role: "advisor" });
			expect(mapping.proof.executables.probe).toEqual({ source: "path", command: "probe-native" });
			expect(candidate.frozen.executables.map((binding: Record<string, any>) => binding.label)).toEqual([
				"controller-runtime", "git", "native-advisor", "native-task", "probe", "python",
			]);
			for (const binding of candidate.frozen.executables) {
				expect(fs.realpathSync(binding.path)).toBe(binding.path);
				expect(sha256(binding.path)).toBe(binding.sha256);
			}
			const frozenMapping = candidate.frozen.files.find((entry: Record<string, any>) => entry.label === "mapping");
			expect(frozenMapping.sha256).toBe(sha256(path.join(item.root, ".subagent/flows/state-bound-controller-e2e.json")));

			const proofPath = path.join(item.root, completed.state.proofPath);
			const proof = fs.readFileSync(proofPath, "utf8");
			expect(proof).toContain("TERMINAL: COMPLETE");
			expect(proof).toContain(`EVIDENCE-COMMITMENT: sha256:${candidate.candidateDigest}`);
			expect(proof).not.toContain("WORKFLOW: PASS");
			expect(proof).toContain(`FROZEN_INPUTS_DIGEST: ${candidate.frozen.digest}`);
			expect(proof).toContain(`PROOF_BINDING_DIGEST: ${candidate.proofBinding.digest}`);
			expect(proof).toContain(`ROLE_MODEL_POLICY_DIGEST: ${candidate.proofBinding.roleModelPolicyDigest}`);
			expect(proof).toContain(`MACHINE_REVIEW_PROMPT_SHA256: ${review.promptSha256}`);
				expect(proof).toContain(`MACHINE_REVIEWED_INPUTS_DIGEST: ${review.reviewedInputsDigest}`);
				expect(proof).toContain(`BASELINE_DIGEST: ${candidate.workspace.baselineDigest}`);
				expect(proof).toContain(`EVIDENCE_DIGEST: ${candidate.workspace.evidenceDigest}`);
			expect(proof).toContain(`EVIDENCE_PRODUCER_DIGEST: ${candidate.evidenceProducer.producerDigest}`);
			const result = readJson(path.join(completed.runDir, "result.json"));
			expect(result.terminal).toBe("success");
			expect(result.reviewerTerminal).toBe("COMPLETE");
			expect(result.evidenceCommitment).toBe(`sha256:${candidate.candidateDigest}`);
			expect(result.proofBinding).toEqual(candidate.proofBinding);
			expect(result.proofBindingDigest).toBe(candidate.proofBinding.digest);
			expect(result.itemKind).toBe("issue");
			expect(result.itemId).toBe("TEST-E2E");
			expect(result.proof.path).toBe(completed.state.proofPath);
			expect(result.proof.sha256).toBe(sha256(proofPath));
			expect(result.stateDigest).toBe(createHash("sha256").update(JSON.stringify(completed.state)).digest("hex"));
			for (const binding of result.evidence) {
				expect(path.relative(completed.runDir, binding.path).startsWith("..")).toBe(false);
				expect(sha256(binding.path)).toBe(binding.sha256);
			}
		});
	});

	for (const probe of ["outside", "overwrite"] as const) {
		test(`command runWrites rejects ${probe === "outside" ? "an undeclared output" : "an overwrite of controller evidence"}`, async () => {
			const item = fixture({ probe });
			await withControllerEnvironment(item, async () => {
				await expect(runController(controllerOptions(item))).rejects.toThrow("unsafe workflow evidence path");
			});
		});
	}

	test("resume rejects a tampered command runWrites receipt", async () => {
		const item = fixture();
		await withControllerEnvironment(item, async () => {
			const options = controllerOptions(item);
			const completed = await runController(options);
			expect(completed.state.forcedReason).toBe("");
			expect(completed.state.status).toBe("success");
			const receiptPath = path.join(completed.runDir, "command-responses/evidence.result.json");
			const receipt = readJson(receiptPath);
			receipt.runWrites[0].path = "baseline.json";
			writeJson(receiptPath, receipt);
			await expect(runController({ ...options, resume: completed.runDir, finalizeOnly: true }))
				.rejects.toThrow(/runWrites receipt disagrees with mapping|response receipt does not match/u);
		});
	});

	for (const drift of ["candidate", "evidence", "workspace"] as const) {
		test(`finalize-only rejects ${drift} drift`, async () => {
			const item = fixture();
			await withControllerEnvironment(item, async () => {
				const options = controllerOptions(item);
				const completed = await runController(options);
				if (drift === "candidate") {
					const candidatePath = path.join(completed.runDir, "state-bound-candidate.json");
					const candidate = readJson(candidatePath);
					candidate.authorization.finalizer = "tampered";
					writeJson(candidatePath, candidate);
				} else if (drift === "evidence") {
					fs.appendFileSync(path.join(completed.runDir, "final-evidence.json"), " ");
				} else {
					fs.writeFileSync(path.join(item.root, "work.txt"), "drifted\n");
				}
				await expect(runController({ ...options, resume: completed.runDir, finalizeOnly: true })).rejects.toThrow();
			});
		});
	}

	for (const tamperFrozen of ["action", "helper", "prompt"] as const) {
		test(`temporary ${tamperFrozen} mutation during a native activation fails closed`, async () => {
			const item = fixture({ tamperFrozen });
			await withControllerEnvironment(item, async () => {
				const result = await runController(controllerOptions(item));
				expect(result.state.status).toBe("unresolved");
				expect(result.state.forcedReason).toContain("frozen artifact identity changed during external activation");
				// A caught terminal error must leave a resumable, internally consistent terminal checkpoint.
				expect(result.state.inFlight).toBe("");
				expect(result.state.completionPhase).toBe("finalized");
			});
		});
	}
});
