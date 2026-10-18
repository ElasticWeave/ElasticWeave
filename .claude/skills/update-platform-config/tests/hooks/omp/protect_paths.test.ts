import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import protectPaths from "../../../assets/project/.omp/hooks/pre/protect-paths";

function handlers() {
	const registered: Record<string, (event: any, context: any) => any> = {};
	protectPaths({ on: (name: string, handler: (event: any, context: any) => any) => {
		registered[name] = handler;
	} });
	return registered;
}

describe("protect-paths acknowledgement surface", () => {
	test("absent project config remains optional at cold start", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-no-project-config-"));
		try {
			const h = handlers();
			expect(h.tool_call({ toolName: "write", input: { path: "ordinary.txt" } }, { cwd })).toBeUndefined();
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("non-bash command-shaped input cannot bypass protected path checks", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-command-shaped-write-"));
		try {
			const h = handlers();
			const result = h.tool_call(
				{
					toolName: "write",
					input: {
						path: "constraint.md",
						command: "bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate .subagent/flows/example.json",
					},
				},
				{ cwd },
			);
			expect(result?.block).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("malformed configured project protection fails closed", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-bad-project-config-"));
		try {
			fs.mkdirSync(path.join(cwd, ".omp/hooks"), { recursive: true });
			fs.writeFileSync(path.join(cwd, ".omp/hooks/protected-paths.json"), "{ malformed");
			const h = handlers();
			const result = h.tool_call({ toolName: "write", input: { path: "ordinary.txt" } }, { cwd });
			expect(result?.block).toBe(true);
			expect(result?.reason).toContain("malformed project protection config");
			expect(h.tool_call({ toolName: "read", input: { path: ".omp/hooks/protected-paths.json" } }, { cwd })).toBeUndefined();
			expect(
				h.tool_call(
					{ toolName: "bash", input: { command: "GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 python3 .claude/skills/constraints-to-hooks/scripts/constraint_gate.py install-protected-paths" } },
					{ cwd },
				),
			).toBeUndefined();
			expect(
				h.tool_call(
					{ toolName: "bash", input: { command: "GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 touch ordinary.txt" } },
					{ cwd },
				)?.block,
			).toBe(true);
			expect(
				h.tool_call(
					{ toolName: "bash", input: { command: "GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json" } },
					{ cwd },
				)?.block,
			).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("non-string project protection entry fails closed", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-bad-project-entry-"));
		try {
			fs.mkdirSync(path.join(cwd, ".omp/hooks"), { recursive: true });
			fs.writeFileSync(path.join(cwd, ".omp/hooks/protected-paths.json"), JSON.stringify(["fixtures/", 7]));
			const h = handlers();
			const result = h.tool_call({ toolName: "bash", input: { command: "true" } }, { cwd });
			expect(result?.block).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("broken symlink project protection config fails closed", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-broken-config-link-"));
		try {
			fs.mkdirSync(path.join(cwd, ".omp/hooks"), { recursive: true });
			fs.symlinkSync(path.join(cwd, "missing-policy.json"), path.join(cwd, ".omp/hooks/protected-paths.json"));
			const h = handlers();
			const result = h.tool_call({ toolName: "write", input: { path: "ordinary.txt" } }, { cwd });
			expect(result?.block).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("old gates and reproducible evidence are writable while terminal artifacts are protected", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-no-accept-gate-"));
		try {
			const h = handlers();
			for (const target of [
				".pm/accept.sh",
				".pm/immutable/case.json",
				".pm/verify_base",
				".pm/evidence/i1.md",
				".pm/review-briefs/i1.md",
			]) {
				expect(h.tool_call({ toolName: "write", input: { path: target } }, { cwd })).toBeUndefined();
			}
			for (const target of [
				".pm/reviews/m1.md",
				".pm/verdicts/i1.md",
				".pm/proofs/workflow/run-1/result.json",
			]) {
				expect(
					h.tool_call({ toolName: "write", input: { path: target } }, { cwd })?.block,
				).toBe(true);
			}
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("protects the machine-gated workflow review proof directory on write", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-workflow-review-write-"));
		try {
			const h = handlers();
			expect(
				h.tool_call({ toolName: "write", input: { path: ".pm/workflow-reviews/i1.md" } }, { cwd })?.block,
			).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("recursively restores an obfuscated workflow review proof edit", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-workflow-review-audit-"));
		const proof = path.join(cwd, ".pm/workflow-reviews/i1.md");
		try {
			fs.mkdirSync(path.dirname(proof), { recursive: true });
			fs.writeFileSync(proof, "TERMINAL: REVISE\n");
			const h = handlers();
			expect(
				h.tool_call({ toolName: "bash", input: { command: "python3 -c pass" } }, { cwd }),
			).toBeUndefined();
			fs.writeFileSync(proof, "TERMINAL: COMPLETE\n");
			const result = h.tool_result({ toolName: "bash" }, { cwd });
			expect(result?.isError).toBe(true);
			expect(result?.content).toContain("PROOF reverted");
			expect(fs.readFileSync(proof, "utf8")).toBe("TERMINAL: REVISE\n");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("allows normal PM CLI execution while protecting the verifier source", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-pm-"));
		try {
			const h = handlers();
			const result = h.tool_call(
				{
					toolName: "bash",
					input: { command: "python3 .claude/skills/pm-dispatch/scripts/pm.py list issues" },
				},
				{ cwd },
			);
			expect(result).toBeUndefined();
			h.tool_result({ toolName: "bash" }, { cwd });
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("does not silently restore general control-plane source after an unobserved edit", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-integrity-"));
		const helper = path.join(cwd, ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py");
		try {
			fs.mkdirSync(path.dirname(helper), { recursive: true });
			fs.writeFileSync(helper, "original\n", { mode: 0o640 });
			const h = handlers();
			expect(h.tool_call({ toolName: "bash", input: { command: "python3 -c pass" } }, { cwd })).toBeUndefined();
			fs.writeFileSync(helper, "tampered\n", { mode: 0o600 });
			const result = h.tool_result({ toolName: "bash" }, { cwd });
			expect(result).toBeUndefined();
			expect(fs.readFileSync(helper, "utf8")).toBe("tampered\n");
			expect(fs.statSync(helper).mode & 0o777).toBe(0o640);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("recursively restores an obfuscated authoritative proof edit", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-proof-audit-"));
		const proof = path.join(cwd, ".pm/proofs/workflow/run-1/result.json");
		try {
			fs.mkdirSync(path.dirname(proof), { recursive: true });
			fs.writeFileSync(proof, "REVISE\n");
			const h = handlers();
			expect(
				h.tool_call({ toolName: "bash", input: { command: "python3 -c pass" } }, { cwd }),
			).toBeUndefined();
			fs.writeFileSync(proof, "COMPLETE\n");
			const result = h.tool_result({ toolName: "bash" }, { cwd });
			expect(result?.isError).toBe(true);
			expect(result?.content).toContain("PROOF reverted");
			expect(fs.readFileSync(proof, "utf8")).toBe("REVISE\n");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("replaces a proof symlink without writing through it", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-proof-symlink-"));
		const proof = path.join(cwd, ".pm/verdicts/i1.md");
		const sentinel = path.join(cwd, "outside.txt");
		try {
			fs.mkdirSync(path.dirname(proof), { recursive: true });
			fs.writeFileSync(proof, "VERDICT: REVISE\n");
			fs.writeFileSync(sentinel, "outside\n");
			const h = handlers();
			expect(
				h.tool_call({ toolName: "bash", input: { command: "python3 -c pass" } }, { cwd }),
			).toBeUndefined();
			fs.unlinkSync(proof);
			fs.symlinkSync(sentinel, proof);

			const result = h.tool_result({ toolName: "bash" }, { cwd });

			expect(result?.isError).toBe(true);
			expect(fs.readFileSync(sentinel, "utf8")).toBe("outside\n");
			expect(fs.lstatSync(proof).isSymbolicLink()).toBe(false);
			expect(fs.readFileSync(proof, "utf8")).toBe("VERDICT: REVISE\n");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("rejects a symlinked proof parent without writing through it", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-proof-parent-symlink-"));
		const runDir = path.join(cwd, ".pm/proofs/workflow/run-1");
		const proof = path.join(runDir, "result.json");
		const outside = path.join(cwd, "outside");
		const sentinel = path.join(outside, "result.json");
		try {
			fs.mkdirSync(runDir, { recursive: true });
			fs.mkdirSync(outside);
			fs.writeFileSync(proof, '{"terminal":"REVISE"}\n');
			fs.writeFileSync(sentinel, "outside\n");
			const h = handlers();
			expect(
				h.tool_call({ toolName: "bash", input: { command: "python3 -c pass" } }, { cwd }),
			).toBeUndefined();
			fs.unlinkSync(proof);
			fs.rmdirSync(runDir);
			fs.symlinkSync(outside, runDir, "dir");

			const result = h.tool_result({ toolName: "bash" }, { cwd });

			expect(result?.isError).toBe(true);
			expect(result?.content).toContain("COULD NOT RESTORE");
			expect(fs.readFileSync(sentinel, "utf8")).toBe("outside\n");
			expect(fs.lstatSync(runDir).isSymbolicLink()).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("directory protection matches path boundaries, not similarly named siblings", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-boundary-"));
		try {
			const h = handlers();
			expect(
				h.tool_call({ toolName: "write", input: { path: ".omp/hooks-extra/file.ts" } }, { cwd }),
			).toBeUndefined();
			const protectedResult = h.tool_call(
				{ toolName: "write", input: { path: ".omp/hooks/file.ts" } },
				{ cwd },
			);
			expect(protectedResult?.block).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("absolute in-repo root policy path remains protected", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-absolute-policy-"));
		try {
			const h = handlers();
			expect(
				h.tool_call({ toolName: "bash", input: { command: "touch docs/constraint.md" } }, { cwd }),
			).toBeUndefined();
			h.tool_result({ toolName: "bash" }, { cwd });
			expect(
				h.tool_call({ toolName: "bash", input: { command: "touch constraint.md" } }, { cwd })?.block,
			).toBe(true);
			expect(
				h.tool_call({ toolName: "bash", input: { command: `touch ${path.join(cwd, "constraint.md")}` } }, { cwd })?.block,
			).toBe(true);
			expect(
				h.tool_call({ toolName: "write", input: { path: path.join(cwd, "constraint.md") } }, { cwd })?.block,
			).toBe(true);
			expect(
				h.tool_call({ toolName: "write", input: { path: path.join(cwd, "docs/constraint.md") } }, { cwd }),
			).toBeUndefined();
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("protects neutral designs and subagent mappings while allowing the exact runner", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-subagent-"));
		try {
			const h = handlers();
			for (const target of [".workflow/designs/example.workflow.json", ".subagent/flows/example.json"]) {
				const result = h.tool_call({ toolName: "write", input: { path: target } }, { cwd });
				expect(result?.block).toBe(true);
			}
			const legal = [
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate .subagent/flows/example.json",
				"GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json",
				"GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun ./.claude/skills/subagent-workflow/scripts/controller/run.ts start ./.subagent/flows/example.json --input .subagent/fixtures/example.input.json",
				"GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --resume workflow-output/subagent/example/subagent-123",
				"GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --resume workflow-output/subagent/example/subagent-123 --input .subagent/fixtures/example.input.json",
				"GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 bun .claude/skills/subagent-workflow/scripts/controller/run.ts finalize-only .subagent/flows/example.json --resume workflow-output/subagent/example/subagent-123",
			];
			for (const command of legal) {
				const run = h.tool_call({ toolName: "bash", input: { command } }, { cwd });
				expect(run).toBeUndefined();
				h.tool_result({ toolName: "bash" }, { cwd });
			}
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("subagent runner rejects protected output and resume overrides", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-subagent-output-"));
		try {
			const h = handlers();
			const illegal = [
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts finalize-only .subagent/flows/example.json --resume workflow-output/subagent/example/run-1",
				"GENERAL_AGENT_ALLOW_PROTECTED_WRITE=true bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --output-root .pm/reviews",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --output-root .workflow/designs",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --resume .pm/reviews/forged",
			];
			for (const command of illegal) {
				const result = h.tool_call({ toolName: "bash", input: { command } }, { cwd });
				expect(result?.block).toBe(true);
			}
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("subagent runner rejects unknown, duplicate, and malformed arguments", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "protect-paths-subagent-argv-"));
		try {
			const h = handlers();
			const illegal = [
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --unknown value",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --input one.json --input two.json",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --resume workflow-output/subagent/example/run-1 --resume workflow-output/subagent/example/run-2",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate .subagent/flows/example.json --input example.json",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json extra",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/../example.json",
				"bun .claude/skills/subagent-workflow/scripts/controller/run.ts start .subagent/flows/example.json --input ../outside.json",
			];
			for (const command of illegal) {
				const result = h.tool_call({ toolName: "bash", input: { command } }, { cwd });
				expect(result?.block).toBe(true);
			}
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});
});
