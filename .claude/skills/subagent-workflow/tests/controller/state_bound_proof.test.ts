import { afterEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import {
	bindSuccessfulTerminal,
	canonicalJson,
	captureProofBinding,
	captureEvidenceProducer,
	captureLatestMachineReview,
	captureWorkspaceEvidence,
	controllerFinalizeAuthorityFromEnvironment,
	canonicalMachineWorkflowProofRelative,
	createStateBoundCandidate,
	finalizeStateBoundCandidateOnly,
	freezeProofArtifacts,
	legacyMachineWorkflowProofRelative,
	loadStateBoundCandidate,
	unprivilegedChildEnvironment,
	verifyFrozenProofArtifacts,
	verifyProofBinding,
	writeStateBoundCandidate,
} from "../../scripts/controller/state_bound_proof";

const roots: string[] = [];

afterEach(() => {
	for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

function hash(value: string | Buffer): string {
	return createHash("sha256").update(value).digest("hex");
}

function write(root: string, relative: string, value: string, mode = 0o600): string {
	const file = path.join(root, relative);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.writeFileSync(file, value, { mode });
	return file;
}

function declaredFile(root: string, relativePath: string) {
	return { path: relativePath, sha256: hash(fs.readFileSync(path.join(root, relativePath))) };
}

function linearUiReceipt(
	root: string,
	itemKind: "issue" | "milestone",
	itemId: string,
	criteriaPath: string,
	reportPath: string,
) {
	const remoteKind = itemKind === "issue" ? "issue" : "project";
	const remoteId = `linear-${itemId}`;
	const targetState = "review";
	const content = {
		schema: "linear-ui-mirror-content/v1",
		itemKind,
		itemId,
		remoteKind,
		remoteId,
		targetState,
		criteria: { path: criteriaPath, sha256: hash(fs.readFileSync(path.join(root, criteriaPath))) },
		report: { path: reportPath, sha256: hash(fs.readFileSync(path.join(root, reportPath))) },
	};
	const contentSha256 = `sha256:${hash(canonicalJson(content))}`;
	const producer = {
		kind: "delegated-linear-worker",
		provider: "linear",
		transport: "authenticated-native-mcp",
		invocationId: `linear-mirror-${itemKind}-${itemId}`,
	};
	const request = {
		operation: "mirror-item-state-and-report",
		itemKind,
		itemId,
		remoteKind,
		remoteId,
		transition: { from: "in_progress", to: targetState },
		contentSha256,
	};
	const providerRevision = "2026-08-14T01:02:03Z";
	const mutation = {
		operation: itemKind === "issue" ? "save_issue" : "save_project",
		remoteKind,
		remoteId,
		observedState: targetState,
		contentSha256,
		providerRevision,
	};
	const readback = {
		operation: itemKind === "issue" ? "get_issue" : "get_project",
		remoteKind,
		remoteId,
		observedState: targetState,
		contentSha256,
		providerRevision,
		observedAt: "2026-08-14T01:02:04Z",
	};
	const material = {
		schema: "linear-ui-mirror-receipt/v1",
		producer,
		request,
		mutation,
		mutationSha256: `sha256:${hash(canonicalJson(mutation))}`,
		readback,
		readbackSha256: `sha256:${hash(canonicalJson(readback))}`,
	};
	return { ...material, receiptSha256: `sha256:${hash(canonicalJson(material))}` };
}

function makePmBindingFixture(
	kind: "issue" | "milestone" = "issue",
	uiMode: "file" | "interactive" | "linear" = "file",
) {
	const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "proof-binding-test-")));
	roots.push(root);
	write(root, ".pm/config.json", `${JSON.stringify({ ui_mode: uiMode })}\n`);
	write(root, ".pm/goal.md", "# Goal\n\nship the exact criteria\n");
	write(root, ".pm/milestones/m1.md", "---\nid: m1\nlinear_id: linear-m1\nstatus: review\n---\n\n## Acceptance Criteria\n\nall children complete\n");
	write(root, ".pm/issues/m1/i1.md", "---\nid: i1\nmilestone: m1\nlinear_id: linear-i1\nstatus: review\n---\n\n## Acceptance Criteria\n\nissue exact\n");
	write(root, ".pm/issues/m1/i2.md", "---\nid: i2\nmilestone: m1\nstatus: done\n---\n\n## Acceptance Criteria\n\nchild exact\n");
	const itemId = kind === "issue" ? "i1" : "m1";
	write(root, `.pm/reports/${itemId}.md`, `# report: ${kind} ${itemId}\n\ncomplete\n`);
	const criteriaPath = kind === "issue" ? ".pm/issues/m1/i1.md" : ".pm/milestones/m1.md";
	const reportPath = `.pm/reports/${itemId}.md`;
	if (uiMode === "linear") {
		write(root, `.pm/receipts/ui/${kind}-${itemId}.json`, `${JSON.stringify(
			linearUiReceipt(root, kind, itemId, criteriaPath, reportPath),
		)}\n`);
	}
	write(root, "config/workflows/models.json", '{"schema":"workflow-model-roles/v3","roles":{}}\n');
	const roleModelPolicyDigest = hash(fs.readFileSync(path.join(root, "config/workflows/models.json")));
	const declaration = kind === "issue" ? {
		schema: "subagent-proof-binding/v1",
		scope: "pm-item",
		itemKind: "issue",
		itemId,
		milestoneId: "m1",
		roleModelPolicyDigest,
		config: declaredFile(root, ".pm/config.json"),
		goal: declaredFile(root, ".pm/goal.md"),
		milestone: declaredFile(root, ".pm/milestones/m1.md"),
		criteria: declaredFile(root, ".pm/issues/m1/i1.md"),
		report: declaredFile(root, ".pm/reports/i1.md"),
		children: [],
		uiReceipts: uiMode === "linear" ? [declaredFile(root, ".pm/receipts/ui/issue-i1.json")] : [],
	} : {
		schema: "subagent-proof-binding/v1",
		scope: "pm-item",
		itemKind: "milestone",
		itemId,
		roleModelPolicyDigest,
		config: declaredFile(root, ".pm/config.json"),
		goal: declaredFile(root, ".pm/goal.md"),
		criteria: declaredFile(root, ".pm/milestones/m1.md"),
		report: declaredFile(root, ".pm/reports/m1.md"),
		children: [
			declaredFile(root, ".pm/issues/m1/i1.md"),
			declaredFile(root, ".pm/issues/m1/i2.md"),
		],
		uiReceipts: uiMode === "linear" ? [declaredFile(root, ".pm/receipts/ui/milestone-m1.json")] : [],
	};
	return { root, itemId, roleModelPolicyDigest, declaration };
}

function fileBinding(root: string, label: string, relativePath: string) {
	const bytes = fs.readFileSync(path.join(root, relativePath));
	const stat = fs.statSync(path.join(root, relativePath));
	return { label, relativePath, sha256: hash(bytes), bytes: bytes.length, mode: stat.mode & 0o7777 };
}

function selfAuthoredLinearBinding(item: ReturnType<typeof makePmBindingFixture>) {
	const material = {
		schema: "subagent-proof-binding/v1" as const,
		scope: "pm-item" as const,
		itemKind: "issue" as const,
		itemId: item.itemId,
		milestoneId: "m1",
		roleModelPolicyDigest: item.roleModelPolicyDigest,
		reason: "",
		artifacts: [
			fileBinding(item.root, "config", ".pm/config.json"),
			fileBinding(item.root, "goal", ".pm/goal.md"),
			fileBinding(item.root, "milestone", ".pm/milestones/m1.md"),
			fileBinding(item.root, "criteria", ".pm/issues/m1/i1.md"),
			fileBinding(item.root, "report", ".pm/reports/i1.md"),
			fileBinding(item.root, "ui-receipt-1", ".pm/receipts/ui/issue-i1.json"),
		],
	};
	return { ...material, digest: hash(canonicalJson(material)) };
}

function makeFixture() {
	const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "state-bound-proof-test-")));
	roots.push(root);
	const executableRoot = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "state-bound-proof-executable-")));
	roots.push(executableRoot);
	const executablePath = write(executableRoot, "native-agent", "#!/bin/sh\nexit 0\n", 0o755);
	const resolvedExecutables = {
		builder: { path: executablePath, sha256: hash(fs.readFileSync(executablePath)) },
	};
	const runRoot = path.join(root, "workflow-output/subagent/fixture/run-1");
	fs.mkdirSync(runRoot, { recursive: true });
	const artifacts = {
		controller: ".claude/skills/subagent-workflow/scripts/controller/run.ts",
		"native-core": ".claude/skills/subagent-workflow/scripts/native/run.ts",
		"proof-module": ".claude/skills/subagent-workflow/scripts/controller/state_bound_proof.ts",
		design: ".workflow/designs/fixture.workflow.json",
		mapping: ".subagent/flows/fixture.json",
		roles: "config/workflows/models.json",
		"native-map": ".claude/skills/subagent-workflow/scripts/native/native-map.json",
		input: ".pm/round_input.json",
		resources: "resources.md",
		helper: ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py",
		"resource-closure": ".workflow/resources/fixture/closure.json",
		"action-wrapper": ".claude/skills/subagent-workflow/scripts/controller/action_wrapper.ts",
	};
	for (const [label, file] of Object.entries(artifacts)) write(root, file, `${label}\n`);
	const frozen = freezeProofArtifacts(
		root,
		artifacts,
		["resource-closure", "action-wrapper"],
		resolvedExecutables,
	);
	const stateBinding = {
		version: 1,
		finalStateDigest: "b".repeat(64),
		rootHead: "0123456789abcdef",
		workspaceHeads: { workspace: "fedcba9876543210" },
	};
	const baselineDigest = "a".repeat(64);
	const evidence = {
		baselineDigest,
		acceptance: {
			criteria: "The focused widget regression suite passes.",
			argv: ["python3", "-m", "unittest", "tests.test_widget"],
			exitCode: 0,
			timedOut: false,
		},
		stateBinding,
	};
	const evidenceDigest = hash(canonicalJson(evidence));
	const proofBinding = captureProofBinding(root, {
		schema: "subagent-proof-binding/v1",
		scope: "no-pm",
		itemId: "M1-I1",
		roleModelPolicyDigest: frozen.files.find(file => file.label === "roles")!.sha256,
		reason: "State-bound proof primitive unit fixture; no PM completion is authorized.",
	}, "M1-I1", frozen.files.find(file => file.label === "roles")!.sha256);
	const reviewedInputsDigest = hash(canonicalJson({
		evidenceDigest,
		reviewedStateDigest: stateBinding.finalStateDigest,
		proofBinding,
	}));
	write(
		runRoot,
		"final-evidence.json",
		`${JSON.stringify({ evidenceDigest, evidence })}\n`,
	);
	const commitment = {
		schema: "subagent-review-prompt-commitment/v1",
		nodeId: "final-review",
		declaredReads: { evidenceDigest, reviewedStateDigest: stateBinding.finalStateDigest },
		proofBinding,
		reviewedInputsDigest,
		reviewedStateDigest: stateBinding.finalStateDigest,
		evidenceDigest,
	};
	const promptCommitmentDigest = hash(canonicalJson(commitment));
	const reviewPrompt = `Review final state and evidence\n<workflow-review-commitment>${canonicalJson(commitment)}</workflow-review-commitment>`;
	const promptSha256 = hash(reviewPrompt);
	const promptPath = "native-responses/final-review.prompt";
	write(runRoot, promptPath, reviewPrompt);
	const trace = [
		{ call: 1, model: "anthropic/scheduler", effort: null },
		{ call: 2, model: "openai/builder", effort: "high" },
		{
			call: 3,
			model: "anthropic/reviewer",
			effort: "high",
			promptPath,
			promptSha256,
			promptCommitmentDigest,
			reviewedStateDigest: stateBinding.finalStateDigest,
			reviewedInputsDigest,
			evidenceDigest,
		},
		{
			call: 4,
			model: "anthropic/reviewer",
			effort: "high",
			promptPath,
			promptSha256,
			promptCommitmentDigest,
			reviewedStateDigest: stateBinding.finalStateDigest,
			reviewedInputsDigest,
			evidenceDigest,
		},
	].map(record => JSON.stringify(record)).join("\n") + "\n";
	write(runRoot, "native-calls.jsonl", trace);
	const response = '{"verdict":"PASS","reason":"reviewed final bytes"}\n';
	write(runRoot, "native-responses/final-review.stdout", response);
	const receipt = `${JSON.stringify({
		call: 4,
		exitCode: 0,
		terminationClass: "COMPLETED",
		stdoutSha256: hash(response),
		stderrSha256: hash(""),
		promptPath,
		promptSha256,
		promptCommitmentDigest,
		reviewedStateDigest: stateBinding.finalStateDigest,
		reviewedInputsDigest,
		evidenceDigest,
	})}\n`;
	write(runRoot, "native-responses/final-review.result.json", receipt);
	write(
		runRoot,
		"machine-reviews.jsonl",
		`${JSON.stringify({
			activation: 1,
			call: 3,
			nodeId: "earlier-review",
			verdict: "PASS",
			promptPath,
			promptSha256,
			promptCommitmentDigest,
			reviewedStateDigest: stateBinding.finalStateDigest,
			reviewedInputsDigest,
			evidenceDigest,
			nativeTracePath: "native-calls.jsonl",
			traceSha256: hash(trace),
			responsePath: "native-responses/final-review.stdout",
			responseSha256: hash(response),
			receiptPath: "native-responses/final-review.result.json",
			receiptSha256: hash(receipt),
		})}\n${JSON.stringify({
			activation: 2,
			call: 4,
			nodeId: "final-review",
		verdict: "PASS",
		promptPath,
		promptSha256,
		promptCommitmentDigest,
			reviewedStateDigest: stateBinding.finalStateDigest,
			reviewedInputsDigest,
			evidenceDigest,
			nativeTracePath: "native-calls.jsonl",
			traceSha256: hash(trace),
			responsePath: "native-responses/final-review.stdout",
			responseSha256: hash(response),
			receiptPath: "native-responses/final-review.result.json",
			receiptSha256: hash(receipt),
		})}\n`,
	);
	const machineReview = captureLatestMachineReview(runRoot, "machine-reviews.jsonl", "final-review");
	const workspace = captureWorkspaceEvidence(runRoot, "final-evidence.json");
	const producerStdout = `${JSON.stringify({ summary: "captured evidence", statePatch: [] })}\n`;
	const producerStderr = "";
	write(runRoot, "command-responses/evidence.stdout", producerStdout);
	write(runRoot, "command-responses/evidence.stderr", producerStderr);
	write(runRoot, "command-responses/evidence.result.json", `${JSON.stringify({
		nodeId: "evidence",
		activation: 3,
		argvSha256: hash(canonicalJson(["audited-action", "evidence"])),
		exitCode: 0,
		terminationClass: "COMPLETED",
		stdoutSha256: hash(producerStdout),
		stderrSha256: hash(producerStderr),
		runWrites: [{ path: "final-evidence.json", sha256: hash(fs.readFileSync(path.join(runRoot, "final-evidence.json"))) }],
	})}\n`);
	const evidenceProducer = captureEvidenceProducer(runRoot, "evidence", "final-evidence.json");
	const terminal = bindSuccessfulTerminal(
		{
			status: "success",
			terminal: "success",
			completed: ["seed", "final-review", "success"],
			frontier: [],
			pending: [],
			inFlight: "",
			modelCalls: 4,
			schedulerCalls: 1,
			nodeCalls: 3,
		},
		"success",
		8,
	);
	const candidate = createStateBoundCandidate({
		projectRoot: root,
		runRoot,
		itemId: "M1-I1",
		runId: "run-1",
		designId: "fixture",
		proofBinding,
		frozen,
		terminal,
		machineReview,
		workspace,
		evidenceProducer,
		currentWorkspaceBinding: stateBinding,
		now: "2026-08-02T00:00:00.000Z",
	});
	return {
		root, executableRoot, executablePath, resolvedExecutables, runRoot, artifacts, frozen, stateBinding,
		baselineDigest, evidenceDigest, reviewedInputsDigest, promptSha256,
		promptCommitmentDigest, machineReview, workspace, evidenceProducer, terminal, proofBinding, candidate,
	};
}

function authority() {
	const previous = process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE;
	process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE = "1";
	try {
		return controllerFinalizeAuthorityFromEnvironment();
	} finally {
		if (previous === undefined) delete process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE;
		else process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE = previous;
	}
}

describe("state-bound completion proof", () => {
	test("binds exact canonical issue and report bytes and rejects drift in file mode", () => {
		const item = makePmBindingFixture("issue");
		const binding = captureProofBinding(
			item.root, item.declaration, item.itemId, item.roleModelPolicyDigest,
		);
		expect(binding.scope).toBe("pm-item");
		expect(binding.itemKind).toBe("issue");
		expect(binding.artifacts.map(artifact => artifact.relativePath)).toEqual([
			".pm/config.json",
			".pm/goal.md",
			".pm/milestones/m1.md",
			".pm/issues/m1/i1.md",
			".pm/reports/i1.md",
		]);
		verifyProofBinding(item.root, binding, item.roleModelPolicyDigest);
		for (const relative of [
			".pm/milestones/m1.md",
			".pm/issues/m1/i1.md",
			".pm/reports/i1.md",
		]) {
			const original = fs.readFileSync(path.join(item.root, relative));
			fs.appendFileSync(path.join(item.root, relative), "drift\n");
			expect(() => verifyProofBinding(item.root, binding, item.roleModelPolicyDigest)).toThrow("bytes or mode changed");
			fs.writeFileSync(path.join(item.root, relative), original);
		}
	});

	test("rejects perfectly self-consistent self-authored Linear receipts before capture", () => {
		for (const kind of ["issue", "milestone"] as const) {
			const item = makePmBindingFixture(kind, "linear");
			expect(() => captureProofBinding(
				item.root, item.declaration, item.itemId, item.roleModelPolicyDigest,
			)).toThrow("authenticated controller-owned Linear evidence is unavailable");
		}
	});

	test("rejects a previously captured self-authored Linear binding during verification", () => {
		const item = makePmBindingFixture("issue", "linear");
		const binding = selfAuthoredLinearBinding(item);
		expect(() => verifyProofBinding(item.root, binding, item.roleModelPolicyDigest))
			.toThrow("authenticated controller-owned Linear evidence is unavailable");
	});

	test("keeps file and interactive PM workflows local and receipt-free", () => {
		for (const uiMode of ["file", "interactive"] as const) {
			const item = makePmBindingFixture("issue");
			write(item.root, ".pm/config.json", `${JSON.stringify({ ui_mode: uiMode })}\n`);
			(item.declaration as any).config = declaredFile(item.root, ".pm/config.json");
			(item.declaration as any).uiReceipts = [];
			const binding = captureProofBinding(
				item.root, item.declaration, item.itemId, item.roleModelPolicyDigest,
			);
			expect(binding.artifacts.some(artifact => artifact.label.startsWith("ui-receipt-"))).toBe(false);
			verifyProofBinding(item.root, binding, item.roleModelPolicyDigest);
		}
	});

	test("binds the exact milestone child set and rejects child drift or ambiguity", () => {
		const item = makePmBindingFixture("milestone");
		const binding = captureProofBinding(
			item.root, item.declaration, item.itemId, item.roleModelPolicyDigest,
		);
		expect(binding.artifacts.filter(artifact => artifact.label.startsWith("child-"))
			.map(artifact => artifact.relativePath)).toEqual([
				".pm/issues/m1/i1.md",
				".pm/issues/m1/i2.md",
			]);
		const child = path.join(item.root, ".pm/issues/m1/i2.md");
		const original = fs.readFileSync(child);
		fs.appendFileSync(child, "drift\n");
		expect(() => verifyProofBinding(item.root, binding, item.roleModelPolicyDigest)).toThrow("bytes or mode changed");
		fs.writeFileSync(child, original);
		write(item.root, ".pm/issues/m1/i3.md", "---\nid: i3\nmilestone: m1\nstatus: todo\n---\n");
		expect(() => verifyProofBinding(item.root, binding, item.roleModelPolicyDigest)).toThrow("canonical child set changed");
	});

	test("rejects wrong item identity and accepts explicit criteria-artifact/no-pm scopes", () => {
		const item = makePmBindingFixture("issue");
		expect(() => captureProofBinding(
			item.root, { ...item.declaration, itemId: "other" }, item.itemId, item.roleModelPolicyDigest,
		)).toThrow("item identity");
		write(item.root, "criteria/release.md", "release criteria\n");
		const criteria = captureProofBinding(item.root, {
			schema: "subagent-proof-binding/v1",
			scope: "criteria-artifact",
			itemId: "research-1",
			roleModelPolicyDigest: item.roleModelPolicyDigest,
			criteria: declaredFile(item.root, "criteria/release.md"),
		}, "research-1", item.roleModelPolicyDigest);
		expect(criteria.scope).toBe("criteria-artifact");
		const noPm = captureProofBinding(item.root, {
			schema: "subagent-proof-binding/v1",
			scope: "no-pm",
			itemId: "validation-only",
			roleModelPolicyDigest: item.roleModelPolicyDigest,
			reason: "Static mapping validation fixture; no PM completion is authorized.",
		}, "validation-only", item.roleModelPolicyDigest);
		expect(noPm.scope).toBe("no-pm");
		expect(noPm.artifacts).toEqual([]);
		expect(() => verifyProofBinding(item.root, noPm, "f".repeat(64)))
			.toThrow("role/model policy digest changed");
		expect(() => captureProofBinding(item.root, {
			schema: "subagent-proof-binding/v1",
			scope: "no-pm",
			itemId: "i1",
			roleModelPolicyDigest: item.roleModelPolicyDigest,
			reason: "incorrectly claims a canonical PM issue is not PM-owned",
		}, "i1", item.roleModelPolicyDigest)).toThrow("conflicts with an existing canonical PM item");
	});

	test("rejects canonical PM frontmatter, duplicate issue identity, report identity, and symlink ambiguity", () => {
		for (const mutation of ["frontmatter", "duplicate", "report", "symlink"] as const) {
			const item = makePmBindingFixture("issue");
			if (mutation === "frontmatter") {
				fs.writeFileSync(path.join(item.root, ".pm/issues/m1/i1.md"), "---\nid: other\nmilestone: m1\n---\n");
				(item.declaration as any).criteria = declaredFile(item.root, ".pm/issues/m1/i1.md");
			} else if (mutation === "duplicate") {
				write(item.root, ".pm/issues/other/i1.md", "---\nid: i1\nmilestone: other\n---\n");
			} else if (mutation === "report") {
				fs.writeFileSync(path.join(item.root, ".pm/reports/i1.md"), "# report: issue other\n");
				(item.declaration as any).report = declaredFile(item.root, ".pm/reports/i1.md");
			} else {
				const criteria = path.join(item.root, ".pm/issues/m1/i1.md");
				const outside = write(item.root, "outside-issue.md", fs.readFileSync(criteria, "utf8"));
				fs.unlinkSync(criteria);
				fs.symlinkSync(outside, criteria);
			}
			expect(() => captureProofBinding(
				item.root, item.declaration, item.itemId, item.roleModelPolicyDigest,
			)).toThrow(/identity|canonical issue item|symlink|without following/u);
		}
	});

	test("freezes controller, workflow, role, resource, and helper bytes and detects drift", () => {
		const item = makeFixture();
		expect(item.frozen.files.map(file => file.label)).toContainAllValues([
			"controller", "native-core", "proof-module", "design", "mapping", "roles", "native-map", "input", "resources", "helper",
			"resource-closure", "action-wrapper",
		]);
		expect(item.frozen.requiredLabels).toContain("resource-closure");
		expect(item.frozen.requiredLabels).toContain("action-wrapper");
		expect(item.frozen.executables.map(executable => executable.label)).toEqual(["builder"]);
		expect(item.frozen.executables[0].path).toBe(item.executablePath);
		verifyFrozenProofArtifacts(item.frozen);
		fs.writeFileSync(path.join(item.root, item.artifacts.resources), "changed\n");
		expect(() => verifyFrozenProofArtifacts(item.frozen)).toThrow("resources bytes or mode changed");
	});

	test("external executable bindings enter the frozen and candidate digests, and repeated role paths are allowed", () => {
		const item = makeFixture();
		const repeated = freezeProofArtifacts(
			item.root,
			item.artifacts,
			["resource-closure", "action-wrapper"],
			{
				builder: item.resolvedExecutables.builder,
				reviewer: item.resolvedExecutables.builder,
			},
		);
		expect(repeated.executables.map(executable => executable.label)).toEqual(["builder", "reviewer"]);
		expect(repeated.executables[0].path).toBe(repeated.executables[1].path);
		expect(repeated.digest).not.toBe(item.frozen.digest);

		const { schema: _schema, digest: _digest, ...frozenMaterial } = item.frozen;
		expect(item.frozen.digest).toBe(hash(canonicalJson(frozenMaterial)));
		expect(item.candidate.frozen.executables).toEqual(item.frozen.executables);
		const changed = structuredClone(item.candidate);
		changed.frozen.executables[0].label = "changed-role";
		const { candidateDigest: _candidateDigest, ...candidateMaterial } = changed;
		expect(hash(canonicalJson(candidateMaterial))).not.toBe(item.candidate.candidateDigest);
	});

	test("external executable capture rejects non-canonical, in-project, symlink, non-executable, and stale declarations", () => {
		const item = makeFixture();
		const nonCanonical = `${item.root}/../${path.basename(item.executableRoot)}/${path.basename(item.executablePath)}`;
		expect(() => freezeProofArtifacts(item.root, item.artifacts, [], {
			native: { path: nonCanonical, sha256: item.resolvedExecutables.builder.sha256 },
		})).toThrow("absolute and normalized");

		const inProject = write(item.root, "tools/in-project-agent", "#!/bin/sh\nexit 0\n", 0o755);
		expect(() => freezeProofArtifacts(item.root, item.artifacts, [], {
			native: { path: inProject, sha256: hash(fs.readFileSync(inProject)) },
		})).toThrow("outside the project root");

		const symlink = path.join(item.executableRoot, "agent-link");
		fs.symlinkSync(item.executablePath, symlink);
		expect(() => freezeProofArtifacts(item.root, item.artifacts, [], {
			native: { path: symlink, sha256: item.resolvedExecutables.builder.sha256 },
		})).toThrow("canonical real path");

		const nonExecutable = write(item.executableRoot, "non-executable", "plain\n", 0o644);
		expect(() => freezeProofArtifacts(item.root, item.artifacts, [], {
			native: { path: nonExecutable, sha256: hash(fs.readFileSync(nonExecutable)) },
		})).toThrow("is not executable");

		expect(() => freezeProofArtifacts(item.root, item.artifacts, [], {
			native: { path: item.executablePath, sha256: "f".repeat(64) },
		})).toThrow("declared digest does not match its bytes");
		expect(() => freezeProofArtifacts(item.root, item.artifacts, [], {
			"../native": item.resolvedExecutables.builder,
		})).toThrow("label is path-unsafe");
	});

	test("external executable verification fails closed on missing, symlink, byte, mode, identity, and manifest drift", () => {
		const missing = makeFixture();
		fs.unlinkSync(missing.executablePath);
		expect(() => verifyFrozenProofArtifacts(missing.frozen)).toThrow("is unavailable");

		const symlink = makeFixture();
		const replacement = write(symlink.executableRoot, "replacement", "#!/bin/sh\nexit 0\n", 0o755);
		fs.unlinkSync(symlink.executablePath);
		fs.symlinkSync(replacement, symlink.executablePath);
		expect(() => verifyFrozenProofArtifacts(symlink.frozen)).toThrow("canonical real path");

		const bytes = makeFixture();
		fs.writeFileSync(bytes.executablePath, "#!/bin/sh\nexit 1\n");
		expect(() => verifyFrozenProofArtifacts(bytes.frozen)).toThrow("declared digest does not match its bytes");

		const nonExecutable = makeFixture();
		fs.chmodSync(nonExecutable.executablePath, 0o644);
		expect(() => verifyFrozenProofArtifacts(nonExecutable.frozen)).toThrow("is not executable");

		const mode = makeFixture();
		fs.chmodSync(mode.executablePath, 0o700);
		expect(() => verifyFrozenProofArtifacts(mode.frozen)).toThrow("bytes, mode, or identity changed");

		const identity = makeFixture();
		const future = new Date(Date.now() + 60_000);
		fs.utimesSync(identity.executablePath, future, future);
		expect(() => verifyFrozenProofArtifacts(identity.frozen)).toThrow("bytes, mode, or identity changed");

		const manifest = makeFixture();
		manifest.frozen.executables[0].dev = "0";
		expect(() => verifyFrozenProofArtifacts(manifest.frozen)).toThrow("manifest digest is invalid");
	});

	test("frozen artifact capture rejects a symlink and a missing required artifact", () => {
		const item = makeFixture();
		const outside = write(item.root, "outside-helper.py", "outside\n");
		fs.rmSync(path.join(item.root, item.artifacts.helper));
		fs.symlinkSync(outside, path.join(item.root, item.artifacts.helper));
		expect(() => freezeProofArtifacts(item.root, item.artifacts)).toThrow("without following symlinks");
		const { helper: _helper, ...missing } = item.artifacts;
		expect(() => freezeProofArtifacts(item.root, missing)).toThrow("missing required frozen artifact helper");
		const { "action-wrapper": _wrapper, ...missingConditional } = item.artifacts;
		expect(() => freezeProofArtifacts(item.root, missingConditional, ["resource-closure", "action-wrapper"]))
			.toThrow("missing required frozen artifact action-wrapper");
	});

	test("success binding requires an empty frontier and fully reconciled bounded calls", () => {
		const base = {
			status: "success",
			terminal: "success",
			completed: ["success"],
			frontier: [],
			pending: [],
			inFlight: "",
			modelCalls: 2,
			schedulerCalls: 1,
			nodeCalls: 1,
		};
		expect(bindSuccessfulTerminal(base, "success", 2).modelCalls).toBe(2);
		expect(() => bindSuccessfulTerminal({ ...base, frontier: ["writer"] }, "success", 2)).toThrow("still has runnable");
		expect(() => bindSuccessfulTerminal({ ...base, modelCalls: 3 }, "success", 2)).toThrow("budget");
		expect(() => bindSuccessfulTerminal({ ...base, nodeCalls: 0 }, "success", 2)).toThrow("not fully reconciled");
		expect(() => bindSuccessfulTerminal({ ...base, terminal: "success\nTERMINAL: COMPLETE" }, "success\nTERMINAL: COMPLETE", 2))
			.toThrow("success terminal is path-unsafe");
	});

	test("binds the last machine review, not an earlier passing review", () => {
		const item = makeFixture();
		const revised = '{"verdict":"REVISE","reason":"late finding"}\n';
		write(item.runRoot, "native-responses/revise.stdout", revised);
		const receipt = `${JSON.stringify({
			exitCode: 0,
			terminationClass: "COMPLETED",
			stdoutSha256: hash(revised),
		})}\n`;
		write(item.runRoot, "native-responses/revise.result.json", receipt);
		fs.appendFileSync(
			path.join(item.runRoot, "machine-reviews.jsonl"),
			`${JSON.stringify({
				activation: 3,
				call: 4,
				nodeId: "final-review",
				verdict: "REVISE",
				promptPath: item.machineReview.latest.promptPath,
				promptSha256: item.promptSha256,
				promptCommitmentDigest: item.promptCommitmentDigest,
				reviewedStateDigest: item.stateBinding.finalStateDigest,
				reviewedInputsDigest: item.reviewedInputsDigest,
				evidenceDigest: item.evidenceDigest,
				nativeTracePath: "native-calls.jsonl",
				traceSha256: item.machineReview.nativeTrace.sha256,
				responsePath: "native-responses/revise.stdout",
				responseSha256: hash(revised),
				receiptPath: "native-responses/revise.result.json",
				receiptSha256: hash(receipt),
			})}\n`,
		);
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("not an explicit success verdict");
	});

	test("machine review binding includes the actual response bytes", () => {
		const item = makeFixture();
		fs.writeFileSync(path.join(item.runRoot, "native-responses/final-review.stdout"), "tampered\n");
		expect(() => createStateBoundCandidate({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			runId: "run-1",
			designId: "fixture",
			proofBinding: item.proofBinding,
			frozen: item.frozen,
			terminal: item.terminal,
			machineReview: item.machineReview,
			workspace: item.workspace,
			evidenceProducer: item.evidenceProducer,
			currentWorkspaceBinding: item.stateBinding,
		})).toThrow("response digest is stale");
	});

	test("machine review binding requires a completed receipt for the same stdout bytes", () => {
		const item = makeFixture();
		const receiptPath = path.join(item.runRoot, "native-responses/final-review.result.json");
		const receipt = `${JSON.stringify({
			exitCode: 1,
			terminationClass: "COMMAND_FAILURE",
			stdoutSha256: item.machineReview.response.sha256,
		})}\n`;
		fs.writeFileSync(receiptPath, receipt);
		const records = fs.readFileSync(path.join(item.runRoot, "machine-reviews.jsonl"), "utf8")
			.trim().split("\n").map(line => JSON.parse(line));
		for (const record of records) record.receiptSha256 = hash(receipt);
		fs.writeFileSync(
			path.join(item.runRoot, "machine-reviews.jsonl"),
			`${records.map(record => JSON.stringify(record)).join("\n")}\n`,
		);
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("does not attest the completed call");
	});

	test("machine review binding fails on native trace byte drift", () => {
		const item = makeFixture();
		fs.appendFileSync(path.join(item.runRoot, "native-calls.jsonl"), "\n");
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("native-call trace digest is stale");
	});

	test("machine review binding requires the canonical final native-calls trace", () => {
		const item = makeFixture();
		const alternate = fs.readFileSync(path.join(item.runRoot, "native-calls.jsonl"), "utf8");
		fs.writeFileSync(path.join(item.runRoot, "alternate-trace.jsonl"), alternate);
		const ledgerPath = path.join(item.runRoot, "machine-reviews.jsonl");
		const ledger = fs.readFileSync(ledgerPath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		for (const record of ledger) {
			record.nativeTracePath = "alternate-trace.jsonl";
			record.traceSha256 = hash(alternate);
		}
		fs.writeFileSync(ledgerPath, `${ledger.map(record => JSON.stringify(record)).join("\n")}\n`);
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("canonical final native-calls.jsonl");
	});

	test("machine review binding cross-checks prompt SHA against the selected native call", () => {
		const item = makeFixture();
		const tracePath = path.join(item.runRoot, "native-calls.jsonl");
		const trace = fs.readFileSync(tracePath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		trace[3].promptSha256 = hash("different final review prompt");
		const traceBytes = `${trace.map(record => JSON.stringify(record)).join("\n")}\n`;
		fs.writeFileSync(tracePath, traceBytes);
		const ledgerPath = path.join(item.runRoot, "machine-reviews.jsonl");
		const ledger = fs.readFileSync(ledgerPath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		for (const record of ledger) record.traceSha256 = hash(traceBytes);
		fs.writeFileSync(ledgerPath, `${ledger.map(record => JSON.stringify(record)).join("\n")}\n`);
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("native trace prompt digest disagrees");
	});

	test("machine review binding cross-checks receipt prompt SHA", () => {
		const item = makeFixture();
		const receiptPath = path.join(item.runRoot, "native-responses/final-review.result.json");
		const receipt = JSON.parse(fs.readFileSync(receiptPath, "utf8"));
		receipt.promptSha256 = hash("different receipt prompt");
		const receiptBytes = `${JSON.stringify(receipt)}\n`;
		fs.writeFileSync(receiptPath, receiptBytes);
		const ledgerPath = path.join(item.runRoot, "machine-reviews.jsonl");
		const ledger = fs.readFileSync(ledgerPath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		for (const record of ledger) record.receiptSha256 = hash(receiptBytes);
		fs.writeFileSync(ledgerPath, `${ledger.map(record => JSON.stringify(record)).join("\n")}\n`);
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("does not attest the completed call, prompt");
	});

	test("machine review binding rejects raw prompt byte tampering", () => {
		const item = makeFixture();
		fs.appendFileSync(path.join(item.runRoot, item.machineReview.latest.promptPath), "\ntampered\n");
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("prompt bytes disagree with its digest");
	});

	test("machine review binding rejects a prompt commitment mismatch even when the outer prompt digest is rebound", () => {
		const item = makeFixture();
		const promptPath = path.join(item.runRoot, item.machineReview.latest.promptPath);
		const prompt = fs.readFileSync(promptPath, "utf8");
		const changedCommitment = {
			schema: "subagent-review-prompt-commitment/v1",
			nodeId: "final-review",
			declaredReads: {
				evidenceDigest: "f".repeat(64),
				reviewedStateDigest: item.stateBinding.finalStateDigest,
			},
			reviewedInputsDigest: item.reviewedInputsDigest,
			reviewedStateDigest: item.stateBinding.finalStateDigest,
			evidenceDigest: "f".repeat(64),
		};
		const changedPrompt = prompt.replace(
			/<workflow-review-commitment>[^\n]*<\/workflow-review-commitment>/u,
			`<workflow-review-commitment>${canonicalJson(changedCommitment)}</workflow-review-commitment>`,
		);
		const changedPromptSha = hash(changedPrompt);
		fs.writeFileSync(promptPath, changedPrompt);

		const tracePath = path.join(item.runRoot, "native-calls.jsonl");
		const trace = fs.readFileSync(tracePath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		trace[3].promptSha256 = changedPromptSha;
		const traceBytes = `${trace.map(record => JSON.stringify(record)).join("\n")}\n`;
		fs.writeFileSync(tracePath, traceBytes);

		const receiptPath = path.join(item.runRoot, "native-responses/final-review.result.json");
		const receipt = JSON.parse(fs.readFileSync(receiptPath, "utf8"));
		receipt.promptSha256 = changedPromptSha;
		const receiptBytes = `${JSON.stringify(receipt)}\n`;
		fs.writeFileSync(receiptPath, receiptBytes);

		const ledgerPath = path.join(item.runRoot, "machine-reviews.jsonl");
		const ledger = fs.readFileSync(ledgerPath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		const latest = ledger.at(-1);
		latest.promptSha256 = changedPromptSha;
		latest.traceSha256 = hash(traceBytes);
		latest.receiptSha256 = hash(receiptBytes);
		fs.writeFileSync(ledgerPath, `${ledger.map(record => JSON.stringify(record)).join("\n")}\n`);

		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("prompt commitment does not bind the reviewed inputs");
	});

	test("machine review record fails closed when a review-input digest is missing", () => {
		const item = makeFixture();
		const ledgerPath = path.join(item.runRoot, "machine-reviews.jsonl");
		const ledger = fs.readFileSync(ledgerPath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		delete ledger.at(-1).evidenceDigest;
		fs.writeFileSync(ledgerPath, `${ledger.map(record => JSON.stringify(record)).join("\n")}\n`);
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("machine review evidence digest is not SHA-256");
	});

	test("reviewedInputsDigest is required and cross-checked against the native trace", () => {
		const missing = makeFixture();
		const missingLedgerPath = path.join(missing.runRoot, "machine-reviews.jsonl");
		const missingLedger = fs.readFileSync(missingLedgerPath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		delete missingLedger.at(-1).reviewedInputsDigest;
		fs.writeFileSync(missingLedgerPath, `${missingLedger.map(record => JSON.stringify(record)).join("\n")}\n`);
		expect(() => captureLatestMachineReview(missing.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("reviewed-inputs digest is not SHA-256");

		const tampered = makeFixture();
		const tamperedLedgerPath = path.join(tampered.runRoot, "machine-reviews.jsonl");
		const tamperedLedger = fs.readFileSync(tamperedLedgerPath, "utf8").trim().split("\n").map(line => JSON.parse(line));
		tamperedLedger.at(-1).reviewedInputsDigest = "f".repeat(64);
		fs.writeFileSync(tamperedLedgerPath, `${tamperedLedger.map(record => JSON.stringify(record)).join("\n")}\n`);
		expect(() => captureLatestMachineReview(tampered.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("native trace review-input digests disagree");
	});

	test("selected review trace call requires bound model and effort", () => {
		for (const missingField of ["model", "effort"] as const) {
			const item = makeFixture();
			const tracePath = path.join(item.runRoot, "native-calls.jsonl");
			const trace = fs.readFileSync(tracePath, "utf8").trim().split("\n").map(line => JSON.parse(line));
			delete trace[3][missingField];
			const traceBytes = `${trace.map(record => JSON.stringify(record)).join("\n")}\n`;
			fs.writeFileSync(tracePath, traceBytes);
			const ledgerPath = path.join(item.runRoot, "machine-reviews.jsonl");
			const ledger = fs.readFileSync(ledgerPath, "utf8").trim().split("\n").map(line => JSON.parse(line));
			for (const record of ledger) record.traceSha256 = hash(traceBytes);
			fs.writeFileSync(ledgerPath, `${ledger.map(record => JSON.stringify(record)).join("\n")}\n`);
			expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
				.toThrow(`native trace review ${missingField} is malformed`);
		}
	});

	test("a PASS ledger cannot relabel a bound REVISE response", () => {
		const item = makeFixture();
		const revise = '{"verdict":"REVISE","reason":"blocking"}\n';
		fs.writeFileSync(path.join(item.runRoot, "native-responses/final-review.stdout"), revise);
		const records = fs.readFileSync(path.join(item.runRoot, "machine-reviews.jsonl"), "utf8")
			.trim().split("\n").map(line => JSON.parse(line));
		for (const record of records) record.responseSha256 = hash(revise);
		fs.writeFileSync(
			path.join(item.runRoot, "machine-reviews.jsonl"),
			`${records.map(record => JSON.stringify(record)).join("\n")}\n`,
		);
		expect(() => captureLatestMachineReview(item.runRoot, "machine-reviews.jsonl", "final-review"))
			.toThrow("ledger verdict disagrees");
	});

	test("candidate creation checks the current final workspace binding", () => {
		const item = makeFixture();
		expect(() => createStateBoundCandidate({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			runId: "run-1",
			designId: "fixture",
			proofBinding: item.proofBinding,
			frozen: item.frozen,
			terminal: item.terminal,
			machineReview: item.machineReview,
			workspace: item.workspace,
			evidenceProducer: item.evidenceProducer,
			currentWorkspaceBinding: { ...item.stateBinding, finalStateDigest: "c".repeat(64) },
		})).toThrow("current workspace bytes no longer match");
	});

	test("candidate binds the final review call to terminal model-call accounting", () => {
		const item = makeFixture();
		const terminal = bindSuccessfulTerminal({
			status: "success",
			terminal: "success",
			completed: ["seed", "final-review", "success"],
			frontier: [],
			pending: [],
			inFlight: "",
			modelCalls: 3,
			schedulerCalls: 1,
			nodeCalls: 2,
		}, "success", 8);
		expect(() => createStateBoundCandidate({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			runId: "run-1",
			designId: "fixture",
			proofBinding: item.proofBinding,
			frozen: item.frozen,
			terminal,
			machineReview: item.machineReview,
			workspace: item.workspace,
			evidenceProducer: item.evidenceProducer,
			currentWorkspaceBinding: item.stateBinding,
		})).toThrow("final machine review call does not match");
	});

	test("a PASS review of old evidence cannot authorize a new final evidence file", () => {
		const item = makeFixture();
		const changedEvidence = {
			baselineDigest: item.baselineDigest,
			acceptance: {
				criteria: "The revised widget regression suite passes.",
				argv: ["python3", "-m", "unittest", "tests.test_widget_revised"],
				exitCode: 0,
				timedOut: false,
			},
			stateBinding: item.stateBinding,
		};
		const evidenceDigest = hash(canonicalJson(changedEvidence));
		fs.writeFileSync(
			path.join(item.runRoot, "final-evidence.json"),
			`${JSON.stringify({ evidenceDigest, evidence: changedEvidence })}\n`,
		);
		const workspace = captureWorkspaceEvidence(item.runRoot, "final-evidence.json");
		expect(() => createStateBoundCandidate({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			runId: "run-1",
			designId: "fixture",
			proofBinding: item.proofBinding,
			frozen: item.frozen,
			terminal: item.terminal,
			machineReview: item.machineReview,
			workspace,
			evidenceProducer: item.evidenceProducer,
			currentWorkspaceBinding: item.stateBinding,
		})).toThrow("evidence producer output bytes changed");
	});

	test("workspace evidence requires a valid helper digest and passing acceptance", () => {
		const item = makeFixture();
		const evidencePath = path.join(item.runRoot, "final-evidence.json");
		const payload = JSON.parse(fs.readFileSync(evidencePath, "utf8"));
		payload.evidenceDigest = "e".repeat(64);
		fs.writeFileSync(evidencePath, `${JSON.stringify(payload)}\n`);
		expect(() => captureWorkspaceEvidence(item.runRoot, "final-evidence.json")).toThrow("digest does not match");
		payload.evidence.acceptance.exitCode = 1;
		payload.evidenceDigest = hash(canonicalJson(payload.evidence));
		fs.writeFileSync(evidencePath, `${JSON.stringify(payload)}\n`);
		expect(() => captureWorkspaceEvidence(item.runRoot, "final-evidence.json")).toThrow("successful non-timeout acceptance");
	});

	test("workspace evidence requires the helper-produced baseline digest", () => {
		for (const baselineDigest of [undefined, "not-a-digest"] as const) {
			const item = makeFixture();
			const evidencePath = path.join(item.runRoot, "final-evidence.json");
			const payload = JSON.parse(fs.readFileSync(evidencePath, "utf8"));
			if (baselineDigest === undefined) delete payload.evidence.baselineDigest;
			else payload.evidence.baselineDigest = baselineDigest;
			payload.evidenceDigest = hash(canonicalJson(payload.evidence));
			fs.writeFileSync(evidencePath, `${JSON.stringify(payload)}\n`);
			expect(() => captureWorkspaceEvidence(item.runRoot, "final-evidence.json"))
				.toThrow("workspace evidence baseline digest is not SHA-256");
		}
	});

	test("evidence producer binding rejects receipt, stream, and owned-output tampering", () => {
		const receiptTamper = makeFixture();
		const receiptPath = path.join(receiptTamper.runRoot, "command-responses/evidence.result.json");
		const receipt = JSON.parse(fs.readFileSync(receiptPath, "utf8"));
		receipt.activation += 1;
		fs.writeFileSync(receiptPath, `${JSON.stringify(receipt)}\n`);
		expect(() => createStateBoundCandidate({
			projectRoot: receiptTamper.root,
			runRoot: receiptTamper.runRoot,
			itemId: "M1-I1",
			runId: "run-1",
			designId: "fixture",
			proofBinding: receiptTamper.proofBinding,
			frozen: receiptTamper.frozen,
			terminal: receiptTamper.terminal,
			machineReview: receiptTamper.machineReview,
			workspace: receiptTamper.workspace,
			evidenceProducer: receiptTamper.evidenceProducer,
			currentWorkspaceBinding: receiptTamper.stateBinding,
		})).toThrow("evidence producer command receipt or output binding changed");

		const streamTamper = makeFixture();
		fs.appendFileSync(path.join(streamTamper.runRoot, "command-responses/evidence.stdout"), "tampered\n");
		expect(() => captureEvidenceProducer(streamTamper.runRoot, "evidence", "final-evidence.json"))
			.toThrow("does not bind stdout and stderr bytes");

		const outputTamper = makeFixture();
		fs.appendFileSync(path.join(outputTamper.runRoot, "final-evidence.json"), "tampered\n");
		expect(() => captureEvidenceProducer(outputTamper.runRoot, "evidence", "final-evidence.json"))
			.toThrow("evidence producer output bytes changed");
	});

	test("helper digest canonicalization matches Python ensure_ascii for Chinese and emoji", () => {
		const item = makeFixture();
		const evidence = {
			"\u4e2d\u6587": "\u503c😀",
			acceptance: { command: "\u6d4b\u8bd5😀", exitCode: 0, timedOut: false },
			baselineDigest: "a".repeat(64),
			stateBinding: {
				finalStateDigest: "b".repeat(64),
				"\u8bf4\u660e": "\u5b8c\u6210✅",
			},
		};
		const pythonDigest = "4f54fe983a1ca138467fa524c7208007f9782fad847088027f69db7eab0f76d8";
		fs.writeFileSync(
			path.join(item.runRoot, "final-evidence.json"),
			`${JSON.stringify({ evidenceDigest: pythonDigest, evidence })}\n`,
		);
		expect(captureWorkspaceEvidence(item.runRoot, "final-evidence.json").evidenceDigest).toBe(pythonDigest);
	});

	test("helper digest sorts astral and BMP keys by Unicode code point like Python", () => {
		const item = makeFixture();
		const evidence = {
			"𐀀": "astral",
			"": "bmp",
			acceptance: { exitCode: 0, timedOut: false },
			baselineDigest: "a".repeat(64),
			stateBinding: { finalStateDigest: "b".repeat(64) },
		};
		const pythonDigest = "b05911fcbaa605a3de3817718992fbf79a21a35ddd96e0fdcea54da30903365f";
		fs.writeFileSync(
			path.join(item.runRoot, "final-evidence.json"),
			`${JSON.stringify({ evidenceDigest: pythonDigest, evidence })}\n`,
		);
		expect(captureWorkspaceEvidence(item.runRoot, "final-evidence.json").evidenceDigest).toBe(pythonDigest);
	});

	test("helper digest fails closed on floats whose Python spelling JS cannot preserve", () => {
		const item = makeFixture();
		const payload = JSON.parse(fs.readFileSync(path.join(item.runRoot, "final-evidence.json"), "utf8"));
		payload.evidence.rate = 1e-7;
		payload.evidenceDigest = "0".repeat(64);
		fs.writeFileSync(path.join(item.runRoot, "final-evidence.json"), `${JSON.stringify(payload)}\n`);
		expect(() => captureWorkspaceEvidence(item.runRoot, "final-evidence.json"))
			.toThrow("number outside the safe-integer schema");
	});

	test("candidate and protected proof are separate, then finalize-only is idempotent", () => {
		const item = makeFixture();
		const written = writeStateBoundCandidate(item.runRoot, item.candidate);
		expect(written.created).toBeTrue();
		expect(fs.existsSync(path.join(item.root, canonicalMachineWorkflowProofRelative("M1-I1")))).toBeFalse();
		expect(fs.existsSync(path.join(item.root, legacyMachineWorkflowProofRelative("M1-I1")))).toBeFalse();
		const first = finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		});
		expect(first.created).toBeTrue();
		expect(first.modelCallsReplayed).toBe(0);
		const second = finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		});
		expect(second.idempotent).toBeTrue();
		expect(second.modelCallsReplayed).toBe(0);
		const proof = fs.readFileSync(first.proofPath, "utf8");
		expect(proof.split("\n").filter(line => line.startsWith("TERMINAL:")))
			.toEqual(["TERMINAL: COMPLETE"]);
		expect(proof.split("\n").filter(line => line.startsWith("EVIDENCE-COMMITMENT:")))
			.toEqual([`EVIDENCE-COMMITMENT: sha256:${item.candidate.candidateDigest}`]);
		expect(proof).not.toContain("WORKFLOW: PASS");
		expect(proof).toContain(`CANDIDATE_DIGEST: ${item.candidate.candidateDigest}`);
		expect(proof).toContain(`MACHINE_REVIEW_DIGEST: ${item.machineReview.reviewDigest}`);
		expect(proof).toContain(`MACHINE_REVIEW_CALL: ${item.machineReview.latest.call}`);
		expect(proof).toContain(`MACHINE_REVIEW_PROMPT_SHA256: ${item.promptSha256}`);
		expect(proof).toContain(`MACHINE_REVIEW_EVIDENCE_DIGEST: ${item.evidenceDigest}`);
		expect(proof).toContain(`MACHINE_REVIEWED_INPUTS_DIGEST: ${item.reviewedInputsDigest}`);
		expect(proof).toContain(`NATIVE_TRACE_SHA256: ${item.machineReview.nativeTrace.sha256}`);
		expect(proof).toContain(`MACHINE_REVIEW_RECEIPT_SHA256: ${item.machineReview.receipt.sha256}`);
		expect(proof).toContain(`BASELINE_DIGEST: ${item.baselineDigest}`);
		expect(proof).toContain(`EVIDENCE_PRODUCER_DIGEST: ${item.evidenceProducer.producerDigest}`);
		expect(proof).toContain("AUTHORIZATION_MODE: controller-tier1-env");
	});

	test("finalize-only rejects a candidate baseline digest that disagrees with bound evidence", () => {
		const item = makeFixture();
		const candidate = structuredClone(item.candidate) as Record<string, any>;
		candidate.workspace.baselineDigest = "f".repeat(64);
		const { candidateDigest: _candidateDigest, ...material } = candidate;
		candidate.candidateDigest = hash(canonicalJson(material));
		fs.writeFileSync(
			path.join(item.runRoot, "state-bound-candidate.json"),
			`${canonicalJson(candidate)}\n`,
		);
		expect(() => finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		})).toThrow("final workspace evidence file or binding changed");
	});

	test("candidate binds the canonical in-project run path and matching run id", () => {
		const item = makeFixture();
		expect(() => createStateBoundCandidate({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			runId: "different-run",
			designId: "fixture",
			proofBinding: item.proofBinding,
			frozen: item.frozen,
			terminal: item.terminal,
			machineReview: item.machineReview,
			workspace: item.workspace,
			evidenceProducer: item.evidenceProducer,
			currentWorkspaceBinding: item.stateBinding,
		})).toThrow("not the canonical in-project path");
		const outsideRun = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "state-bound-external-run-")));
		roots.push(outsideRun);
		expect(() => createStateBoundCandidate({
			projectRoot: item.root,
			runRoot: outsideRun,
			itemId: "M1-I1",
			runId: path.basename(outsideRun),
			designId: "fixture",
			proofBinding: item.proofBinding,
			frozen: item.frozen,
			terminal: item.terminal,
			machineReview: item.machineReview,
			workspace: item.workspace,
			evidenceProducer: item.evidenceProducer,
			currentWorkspaceBinding: item.stateBinding,
		})).toThrow("not the canonical in-project path");
	});

	test("finalize-only always writes the canonical protected proof path", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		const result = finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
			proofPath: "unprotected.md",
		} as Parameters<typeof finalizeStateBoundCandidateOnly>[0] & { proofPath: string });
		expect(result.proofPath).toBe(path.join(item.root, canonicalMachineWorkflowProofRelative("M1-I1")));
		expect(fs.existsSync(path.join(item.root, "unprotected.md"))).toBeFalse();
	});

	test("finalize-only requires a module-issued token sourced from the controller tier-1 environment", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		const previous = process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE;
		delete process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE;
		try {
			expect(() => controllerFinalizeAuthorityFromEnvironment()).toThrow("GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1");
		} finally {
			if (previous !== undefined) process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE = previous;
		}
		expect(() => finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: { mode: "controller-tier1-env", source: "GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1" },
		})).toThrow("module-issued controller tier-1 authority token");
	});

	test("a different pre-existing canonical proof fails closed", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		write(item.root, canonicalMachineWorkflowProofRelative("M1-I1"), "TERMINAL: REVISE\nCANDIDATE_DIGEST: different\n");
		expect(() => finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		})).toThrow("refusing to replace a different existing file");
	});

	test("candidate tampering fails before finalization", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		const candidatePath = path.join(item.runRoot, "state-bound-candidate.json");
		const payload = JSON.parse(fs.readFileSync(candidatePath, "utf8"));
		payload.terminal.modelCalls = 0;
		fs.writeFileSync(candidatePath, `${JSON.stringify(payload)}\n`);
		expect(() => loadStateBoundCandidate(item.root, item.runRoot)).toThrow("candidate digest is invalid");
	});

	test("finalize-only rejects workspace or evidence drift after candidate creation", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		expect(() => finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: { ...item.stateBinding, finalStateDigest: "d".repeat(64) },
			authority: authority(),
		})).toThrow("current workspace bytes no longer match");
		fs.writeFileSync(path.join(item.runRoot, "final-evidence.json"), "{}\n");
		expect(() => finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		})).toThrow("no structured evidence");
	});

	test("proof target symlinks fail closed", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		const outside = write(item.root, "outside-proof", "sentinel\n");
		fs.mkdirSync(path.join(item.root, ".pm/workflow-reviews"), { recursive: true });
		fs.symlinkSync(outside, path.join(item.root, canonicalMachineWorkflowProofRelative("M1-I1")));
		expect(() => finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		})).toThrow("symlink target");
		expect(fs.readFileSync(outside, "utf8")).toBe("sentinel\n");
	});

	test("canonical machine proof path is disjoint from independent milestone workflow-x", () => {
		expect(legacyMachineWorkflowProofRelative("x")).toBe(".pm/reviews/workflow-x.md");
		expect(canonicalMachineWorkflowProofRelative("x")).toBe(".pm/workflow-reviews/x.md");
		expect(canonicalMachineWorkflowProofRelative("x")).not.toBe(".pm/reviews/workflow-x.md");
		expect(canonicalMachineWorkflowProofRelative("x")).not.toBe(".pm/reviews/x.md");
		expect(canonicalMachineWorkflowProofRelative("x")).not.toBe(".pm/verdicts/x.md");
		expect(() => canonicalMachineWorkflowProofRelative("../etc")).toThrow("path-unsafe");
	});

	test("milestone workflow-x revise unlink cannot delete the machine proof for issue x", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		const independent = write(item.root, legacyMachineWorkflowProofRelative("M1-I1"), "TERMINAL: REVISE\nindependent milestone\n");
		const first = finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		});
		expect(first.proofPath).toBe(path.join(item.root, canonicalMachineWorkflowProofRelative("M1-I1")));
		expect(fs.readFileSync(independent, "utf8")).toBe("TERMINAL: REVISE\nindependent milestone\n");
		const proofBytes = fs.readFileSync(first.proofPath);
		fs.unlinkSync(independent);
		expect(fs.existsSync(independent)).toBeFalse();
		expect(fs.readFileSync(first.proofPath).equals(proofBytes)).toBeTrue();
	});

	test("legacy-only colliding proof is not treated as the canonical machine proof", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		const first = finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		});
		const canonicalBytes = fs.readFileSync(first.proofPath);
		fs.mkdirSync(path.dirname(path.join(item.root, legacyMachineWorkflowProofRelative("M1-I1"))), { recursive: true });
		fs.renameSync(first.proofPath, path.join(item.root, legacyMachineWorkflowProofRelative("M1-I1")));
		expect(fs.existsSync(first.proofPath)).toBeFalse();
		const retry = finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		});
		expect(retry.created).toBeTrue();
		expect(retry.idempotent).toBeFalse();
		expect(fs.readFileSync(retry.proofPath).equals(canonicalBytes)).toBeTrue();
		expect(fs.readFileSync(path.join(item.root, legacyMachineWorkflowProofRelative("M1-I1")), "utf8"))
			.toBe(canonicalBytes.toString("utf8"));
	});

	test("proof parent directory symlink fails closed and does not write through the link", () => {
		const item = makeFixture();
		writeStateBoundCandidate(item.runRoot, item.candidate);
		const outside = fs.mkdtempSync(path.join(os.tmpdir(), "state-bound-proof-parent-"));
		roots.push(outside);
		const linked = path.join(item.root, ".pm", "workflow-reviews");
		fs.symlinkSync(outside, linked);
		expect(() => finalizeStateBoundCandidateOnly({
			projectRoot: item.root,
			runRoot: item.runRoot,
			itemId: "M1-I1",
			roleModelPolicyDigest: item.proofBinding.roleModelPolicyDigest,
			currentWorkspaceBinding: item.stateBinding,
			authority: authority(),
		})).toThrow(/plain directory|symlink/);
		expect(fs.readdirSync(outside)).toEqual([]);
		expect(fs.lstatSync(linked).isSymbolicLink()).toBeTrue();
	});

	test("candidate writes reject even a dangling symlink target", () => {
		const item = makeFixture();
		fs.symlinkSync(path.join(item.root, "missing-outside"), path.join(item.runRoot, "state-bound-candidate.json"));
		expect(() => writeStateBoundCandidate(item.runRoot, item.candidate)).toThrow("symlink target");
	});

	test("deterministic command environment is a strict runtime allowlist with no credentials", () => {
		const clean = unprivilegedChildEnvironment({
			PATH: "/usr/bin",
			HOME: "/tmp/home",
			LANG: "C.UTF-8",
			LC_ALL: "C.UTF-8",
			TMPDIR: "/tmp/commands",
			SSH_AUTH_SOCK: "/tmp/agent.sock",
			ANTHROPIC_API_KEY: "anthropic-key",
			OPENAI_API_KEY: "openai-key",
			CURSOR_API_KEY: "cursor-key",
			ARBITRARY_TOKEN: "token",
			PRIVATE_KEY: "private-key",
			LC_API_KEY: "locale-prefix-key",
			LC_TOKEN: "locale-prefix-token",
			LC_SSH_AUTH_SOCK: "/tmp/locale-prefix-agent.sock",
			GENERAL_AGENT_ALLOW_PROTECTED_WRITE: "1",
			GENERAL_AGENT_USER_APPROVED_WRITE: "1",
			BASH_ENV: "/tmp/attack",
			BASH_FUNC_payload: "() { :; }",
			ENV: "/tmp/sh-attack",
			SHELLOPTS: "xtrace",
			BASHOPTS: "extdebug",
			CDPATH: "/tmp",
			KSH_ENV: "/tmp/ksh-attack",
			ZDOTDIR: "/tmp/zsh-attack",
			NODE_OPTIONS: "--require=/tmp/node-attack.js",
			NODE_PATH: "/tmp/node-modules",
			NPM_CONFIG_NODE_OPTIONS: "--require=/tmp/npm-attack.js",
			PYTHONPATH: "/tmp/python-modules",
			PYTHONHOME: "/tmp/python-home",
			PYTHONSTARTUP: "/tmp/python-startup.py",
			PYTHONINSPECT: "1",
			PYTHONBREAKPOINT: "attack.breakpoint",
			RUBYOPT: "-r/tmp/ruby-attack.rb",
			RUBYLIB: "/tmp/ruby-lib",
			PERL5OPT: "-Mattack",
			PERL5LIB: "/tmp/perl-lib",
			DYLD_INSERT_LIBRARIES: "/tmp/attack.dylib",
			DYLD_LIBRARY_PATH: "/tmp/dylib",
			LD_PRELOAD: "/tmp/attack.so",
			LD_AUDIT: "/tmp/audit.so",
			GIT_ASKPASS: "/tmp/git-askpass",
			SSH_ASKPASS: "/tmp/ssh-askpass",
			SSH_ASKPASS_REQUIRE: "force",
			GIT_EXTERNAL_DIFF: "/tmp/git-diff",
			GIT_DIFF_OPTS: "--attack",
			GIT_EXEC_PATH: "/tmp/git-exec",
			GIT_SSH: "/tmp/git-ssh",
			GIT_SSH_COMMAND: "/tmp/git-ssh-command",
			DENO_DIR: "/tmp/deno-cache",
			JAVA_TOOL_OPTIONS: "-javaagent:/tmp/attack.jar",
			JDK_JAVA_OPTIONS: "-javaagent:/tmp/attack.jar",
			_JAVA_OPTIONS: "-javaagent:/tmp/attack.jar",
			CLASSPATH: "/tmp/classes",
			PROMPT_COMMAND: "/tmp/attack",
		});
		expect(clean).toEqual({
			PATH: "/usr/bin",
			HOME: "/tmp/home",
			LANG: "C.UTF-8",
			LC_ALL: "C.UTF-8",
			TMPDIR: "/tmp/commands",
		});
	});

	test("scrubbed command environment blocks Node pre-main NODE_OPTIONS injection", () => {
		const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "state-proof-node-env-")));
		roots.push(root);
		const marker = path.join(root, "node-injected");
		const injector = write(root, "inject.cjs", `require("node:fs").writeFileSync(${JSON.stringify(marker)}, "injected")\n`);
		const node = Bun.which("node");
		expect(node).not.toBeNull();

		const injected = Bun.spawnSync([node!, "-e", "void 0"], {
			env: { ...process.env, NODE_OPTIONS: `--require=${injector}` },
		});
		expect(injected.exitCode).toBe(0);
		expect(fs.readFileSync(marker, "utf8")).toBe("injected");
		fs.unlinkSync(marker);

		const blocked = Bun.spawnSync([node!, "-e", "void 0"], {
			env: unprivilegedChildEnvironment({ ...process.env, NODE_OPTIONS: `--require=${injector}` }),
		});
		expect(blocked.exitCode).toBe(0);
		expect(fs.existsSync(marker)).toBe(false);
	});

	test("scrubbed command environment blocks Python pre-main PYTHONPATH injection", () => {
		const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "state-proof-python-env-")));
		roots.push(root);
		const marker = path.join(root, "python-injected");
		const injectionRoot = path.join(root, "injection");
		write(root, "injection/sitecustomize.py", `from pathlib import Path\nPath(${JSON.stringify(marker)}).write_text("injected")\n`);
		const python = Bun.which("python3");
		expect(python).not.toBeNull();

		const injected = Bun.spawnSync([python!, "-c", "pass"], {
			env: { ...process.env, PYTHONPATH: injectionRoot },
		});
		expect(injected.exitCode).toBe(0);
		expect(fs.readFileSync(marker, "utf8")).toBe("injected");
		fs.unlinkSync(marker);

		const blocked = Bun.spawnSync([python!, "-c", "pass"], {
			env: unprivilegedChildEnvironment({ ...process.env, PYTHONPATH: injectionRoot }),
		});
		expect(blocked.exitCode).toBe(0);
		expect(fs.existsSync(marker)).toBe(false);
	});
});
