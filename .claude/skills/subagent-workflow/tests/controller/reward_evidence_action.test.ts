import { afterEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";

import {
	RewardEvidenceActionError,
	captureBaseline,
	captureCurrentState,
	captureEvidence,
} from "../../scripts/controller/actions/reward_evidence";

const sourceHelper = path.resolve(import.meta.dir, "../../scripts/native/reward_hacking_evidence.py");
const actionCli = path.resolve(import.meta.dir, "../../scripts/controller/actions/reward_evidence.ts");
const cleanups: string[] = [];

interface Fixture {
	root: string;
	runRoot: string;
	helper: string;
	helperSha256: string;
	python: string;
	pythonSha256: string;
	git: string;
	gitSha256: string;
}

function hashFile(file: string): string {
	return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function git(root: string, ...args: string[]): string {
	return execFileSync("git", ["-C", root, ...args], { encoding: "utf8" }).trim();
}

function commandPath(name: string): string {
	return fs.realpathSync.native(execFileSync("which", [name], { encoding: "utf8" }).trim());
}

function fixture(helperContent?: string): Fixture {
	const created = fs.mkdtempSync(path.join(os.tmpdir(), "reward-evidence-action-"));
	const root = fs.realpathSync.native(created);
	cleanups.push(root);
	fs.mkdirSync(path.join(root, ".claude", "skills", "subagent-workflow", "scripts", "native"), { recursive: true });
	const helper = path.join(root, ".claude", "skills", "subagent-workflow", "scripts", "native", "reward_hacking_evidence.py");
	if (helperContent === undefined) fs.copyFileSync(sourceHelper, helper);
	else fs.writeFileSync(helper, helperContent);
	fs.writeFileSync(path.join(root, "README.md"), "fixture\n");
	git(root, "init", "-q");
	git(root, "config", "user.name", "Evidence Test");
	git(root, "config", "user.email", "evidence@example.invalid");
	git(root, "add", ".");
	git(root, "-c", "commit.gpgsign=false", "commit", "-qm", "fixture");
	const runRoot = path.join(root, "workflow-output", "subagent", "standard-round", "run-1");
	fs.mkdirSync(runRoot, { recursive: true });
	const python = commandPath("python3");
	const gitExecutable = commandPath("git");
	return {
		root, runRoot, helper, helperSha256: hashFile(helper),
		python, pythonSha256: hashFile(python),
		git: gitExecutable, gitSha256: hashFile(gitExecutable),
	};
}

function patchValue(output: Record<string, unknown>, name: string): unknown {
	const patches = output.statePatch as Array<Record<string, unknown>>;
	return patches.find(item => item.path === `/${name}`)?.value;
}

afterEach(() => {
	for (const directory of cleanups.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});

describe("reward evidence action", () => {
	test("translates baseline and Unicode evidence without losing state binding", () => {
		const f = fixture();
		const baseline = captureBaseline({ ...f, projectRoot: f.root, output: "evidence/baseline.json" });
		expect(patchValue(baseline, "baselinePath")).toBe(path.join("evidence", "baseline.json"));
		expect(JSON.parse(String(patchValue(baseline, "baselineSummary"))).version).toBe(1);
		const baselineDigest = String(patchValue(baseline, "baselineDigest"));
		expect(baselineDigest).toMatch(/^[0-9a-f]{64}$/);

		const unicodePath = "\u8d44\u6599-μ.txt";
		fs.writeFileSync(path.join(f.root, unicodePath), "\u8bc1\u636e ✓\n");
		const evidence = captureEvidence({
			...f,
			projectRoot: f.root,
			baseline: String(patchValue(baseline, "baselinePath")),
			expectedBaselineDigest: baselineDigest,
			acceptanceArgv: ["test", "-f", unicodePath],
			acceptanceTimeout: 30,
			output: "evidence/final-\u8bc1\u636e.json",
		});
		expect(String(patchValue(evidence, "evidence"))).toContain(unicodePath);
		expect(patchValue(evidence, "evidencePath")).toBe(path.join("evidence", "final-\u8bc1\u636e.json"));
		const stateBinding = JSON.parse(String(patchValue(evidence, "stateBinding")));
		expect(stateBinding.finalStateDigest).toMatch(/^[0-9a-f]{64}$/);
		expect(captureCurrentState({ ...f, projectRoot: f.root, timeoutSeconds: 30 })).toEqual(stateBinding);
	});

	test("rejects helper drift before creating output", () => {
		const f = fixture();
		expect(() => captureBaseline({
			...f,
			projectRoot: f.root,
			helperSha256: "0".repeat(64),
			output: "baseline.json",
		})).toThrow("helper SHA-256 drifted");
		expect(fs.existsSync(path.join(f.runRoot, "baseline.json"))).toBeFalse();
	});

	test("rejects path escape, symlink output parents, and symlink inputs", () => {
		const f = fixture();
		expect(() => captureBaseline({ ...f, projectRoot: f.root, output: "../escape.json" })).toThrow("path-unsafe");

		const outside = fs.mkdtempSync(path.join(os.tmpdir(), "reward-evidence-outside-"));
		cleanups.push(fs.realpathSync.native(outside));
		fs.symlinkSync(outside, path.join(f.runRoot, "linked"));
		expect(() => captureBaseline({ ...f, projectRoot: f.root, output: "linked/baseline.json" })).toThrow("not a plain directory");
		fs.symlinkSync(path.join(outside, "missing.json"), path.join(f.runRoot, "broken-output.json"));
		expect(() => captureBaseline({ ...f, projectRoot: f.root, output: "broken-output.json" })).toThrow("must not already exist");

		const baseline = captureBaseline({ ...f, projectRoot: f.root, output: "evidence/baseline.json" });
		const digest = String(patchValue(baseline, "baselineDigest"));
		fs.symlinkSync(path.join(f.runRoot, String(patchValue(baseline, "baselinePath"))), path.join(f.runRoot, "baseline-link.json"));
		expect(() => captureEvidence({
			...f,
			projectRoot: f.root,
			baseline: "baseline-link.json",
			expectedBaselineDigest: digest,
			acceptanceArgv: ["true"],
			acceptanceTimeout: 30,
			output: "evidence/should-not-exist.json",
		})).toThrow("no-follow file");
	});

	test("rejects non-canonical roots, non-run directories, absolute outputs, and helper symlinks", () => {
		const f = fixture();
		expect(() => captureBaseline({
			...f,
			projectRoot: f.root,
			runRoot: path.join(f.root, "workflow-output", "subagent"),
			output: "baseline.json",
		})).toThrow("must be below");
		expect(() => captureBaseline({
			...f,
			projectRoot: f.root,
			runRoot: path.join(f.root, "not-a-run"),
			output: "baseline.json",
		})).toThrow("must be below");
		expect(() => captureBaseline({
			...f,
			projectRoot: f.root,
			output: path.join(f.runRoot, "absolute.json"),
		})).toThrow("relative path");

		const rootAlias = `${f.root}-alias`;
		fs.symlinkSync(f.root, rootAlias);
		cleanups.push(rootAlias);
		expect(() => captureBaseline({ ...f, projectRoot: rootAlias, output: "alias.json" })).toThrow("not canonical");

		const helperAlias = path.join(f.root, ".claude", "skills", "subagent-workflow", "scripts", "native", "helper-alias.py");
		fs.symlinkSync(f.helper, helperAlias);
		expect(() => captureBaseline({ ...f, projectRoot: f.root, helper: helperAlias, output: "helper-alias.json" })).toThrow("helper must be");
	});

	test("fails closed when mechanical acceptance fails", () => {
		const f = fixture();
		const baseline = captureBaseline({ ...f, projectRoot: f.root, output: "baseline.json" });
		expect(() => captureEvidence({
			...f,
			projectRoot: f.root,
			baseline: String(patchValue(baseline, "baselinePath")),
			expectedBaselineDigest: String(patchValue(baseline, "baselineDigest")),
			acceptanceArgv: ["false"],
			acceptanceTimeout: 30,
			output: "failed-evidence.json",
		})).toThrow("acceptance check did not succeed");
	});

	test("rejects an empty mechanical acceptance", () => {
		const f = fixture();
		const baseline = captureBaseline({ ...f, projectRoot: f.root, output: "baseline.json" });
		expect(() => captureEvidence({
			...f,
			projectRoot: f.root,
			baseline: String(patchValue(baseline, "baselinePath")),
			expectedBaselineDigest: String(patchValue(baseline, "baselineDigest")),
			acceptanceArgv: [],
			acceptanceTimeout: 30,
			output: "empty-evidence.json",
		})).toThrow("acceptance argv must be a non-empty array");
	});

	test("does not delegate authority or shell startup hooks", () => {
		const f = fixture();
		const baseline = captureBaseline({ ...f, projectRoot: f.root, output: "baseline.json" });
		const bashEnv = path.join(os.tmpdir(), `reward-evidence-bash-env-${process.pid}-${Date.now()}`);
		const marker = path.join(f.runRoot, "authority-leaked");
		fs.writeFileSync(bashEnv, `touch '${marker}'\n`);
		const saved = {
			GENERAL_AGENT_SECRET: process.env.GENERAL_AGENT_SECRET,
			BASH_ENV: process.env.BASH_ENV,
			ENV: process.env.ENV,
			bashFunction: process.env["BASH_FUNC_reward_probe%%"],
		};
		try {
			process.env.GENERAL_AGENT_SECRET = "must-not-leak";
			process.env.BASH_ENV = bashEnv;
			process.env.ENV = bashEnv;
			process.env["BASH_FUNC_reward_probe%%"] = "() { touch /tmp/should-not-run; }";
			captureEvidence({
				...f,
				projectRoot: f.root,
				baseline: String(patchValue(baseline, "baselinePath")),
				expectedBaselineDigest: String(patchValue(baseline, "baselineDigest")),
				acceptanceArgv: [
					f.python,
					"-c",
					"import os, pathlib, sys; sys.exit(0 if 'GENERAL_AGENT_SECRET' not in os.environ and not pathlib.Path(sys.argv[1]).exists() else 1)",
					marker,
				],
				acceptanceTimeout: 30,
				output: "evidence.json",
			});
			expect(fs.existsSync(marker)).toBeFalse();
		} finally {
			for (const [key, value] of [
				["GENERAL_AGENT_SECRET", saved.GENERAL_AGENT_SECRET],
				["BASH_ENV", saved.BASH_ENV],
				["ENV", saved.ENV],
				["BASH_FUNC_reward_probe%%", saved.bashFunction],
			] as Array<[string, string | undefined]>) {
				if (value === undefined) delete process.env[key];
				else process.env[key] = value;
			}
			fs.unlinkSync(bashEnv);
		}
	});

	test("scrubs authority from the helper process itself", () => {
		const helper = `
import json, os, pathlib, sys
output = sys.argv[sys.argv.index("--output") + 1]
pathlib.Path(output).write_text("{}\\n", encoding="utf-8")
keys = [
  "GENERAL_AGENT_SECRET", "BASH_ENV", "ENV", "BASH_FUNC_reward_probe%%",
  "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "NODE_OPTIONS",
  "DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "GIT_ASKPASS", "GIT_EXTERNAL_DIFF", "SSH_AUTH_SOCK",
  "PYTHONNOUSERSITE", "GIT_TERMINAL_PROMPT", "GIT_CONFIG_NOSYSTEM",
  "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "GIT_ATTR_NOSYSTEM",
  "GIT_PAGER", "PAGER", "GIT_OPTIONAL_LOCKS", "GIT_CONFIG_COUNT",
  "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "PATH",
]
print(json.dumps({
  "baselinePath": output,
  "baselineDigest": "a" * 64,
  "baselineSummary": {"environment": {key: os.environ.get(key) for key in keys}},
}))
`;
		const f = fixture(helper);
		const saved = new Map<string, string | undefined>();
		const blocked = [
			"GENERAL_AGENT_SECRET", "BASH_ENV", "ENV", "BASH_FUNC_reward_probe%%",
			"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "NODE_OPTIONS",
			"DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "GIT_ASKPASS", "GIT_EXTERNAL_DIFF", "SSH_AUTH_SOCK",
		];
		const overwritten = [
			"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "GIT_ATTR_NOSYSTEM",
			"GIT_PAGER", "PAGER", "GIT_OPTIONAL_LOCKS", "GIT_CONFIG_COUNT",
			"GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0",
		];
		for (const key of [...blocked, ...overwritten]) {
			saved.set(key, process.env[key]);
			process.env[key] = "must-not-leak";
		}
		const maliciousPath = path.join(f.runRoot, "malicious-bin");
		fs.mkdirSync(maliciousPath);
		saved.set("PATH", process.env.PATH);
		process.env.PATH = `${maliciousPath}${path.delimiter}${process.env.PATH ?? ""}`;
		try {
			const result = captureBaseline({ ...f, projectRoot: f.root, output: "environment.json" });
			const environment = JSON.parse(String(patchValue(result, "baselineSummary"))).environment;
			for (const key of blocked) expect(environment[key]).toBeNull();
			expect(environment.PYTHONNOUSERSITE).toBe("1");
			expect(environment.GIT_TERMINAL_PROMPT).toBe("0");
			expect(environment.GIT_CONFIG_NOSYSTEM).toBe("1");
			expect(environment.GIT_CONFIG_SYSTEM).toBe("/dev/null");
			expect(environment.GIT_CONFIG_GLOBAL).toBe("/dev/null");
			expect(environment.GIT_ATTR_NOSYSTEM).toBe("1");
			expect(environment.GIT_PAGER).toBe("cat");
			expect(environment.PAGER).toBe("cat");
			expect(environment.GIT_OPTIONAL_LOCKS).toBe("0");
			expect(Number(environment.GIT_CONFIG_COUNT)).toBeGreaterThan(0);
			expect(environment.GIT_CONFIG_KEY_0).toBe("core.fsmonitor");
			expect(environment.GIT_CONFIG_VALUE_0).toBe("false");
			expect(String(environment.PATH).split(path.delimiter)).not.toContain(maliciousPath);
			expect(String(environment.PATH).split(path.delimiter)[0]).toBe(path.dirname(f.git));
		} finally {
			for (const [key, value] of saved) {
				if (value === undefined) delete process.env[key];
				else process.env[key] = value;
			}
		}
	});

	test("neutralizes system, global, and local Git executable config without hiding files", () => {
		const f = fixture();
		const control = path.join(f.runRoot, "git-config-probe");
		fs.mkdirSync(control);
		const marker = path.join(control, "executed");
		const executable = path.join(control, "malicious-git-command.sh");
		fs.writeFileSync(executable, `#!/bin/sh\ntouch '${marker}'\nexit 0\n`, { mode: 0o700 });
		const excludes = path.join(control, "excludes");
		fs.writeFileSync(excludes, "hidden-by-config.txt\n");
		const globalConfig = path.join(control, "global.gitconfig");
		const systemConfig = path.join(control, "system.gitconfig");
		const configBody = [
			"[core]",
			`\tfsmonitor = ${executable}`,
			`\tpager = ${executable}`,
			`\texcludesFile = ${excludes}`,
			"[diff]",
			`\texternal = ${executable}`,
			"[pager]",
			`\tstatus = ${executable}`,
			`\tdiff = ${executable}`,
			"",
		].join("\n");
		fs.writeFileSync(globalConfig, configBody);
		fs.writeFileSync(systemConfig, configBody);
		git(f.root, "config", "core.fsmonitor", executable);
		git(f.root, "config", "core.pager", executable);
		git(f.root, "config", "core.excludesFile", excludes);
		git(f.root, "config", "diff.external", executable);
		git(f.root, "config", "pager.status", executable);
		git(f.root, "config", "pager.diff", executable);

		const saved = new Map<string, string | undefined>();
		for (const [key, value] of Object.entries({
			HOME: control,
			GIT_CONFIG_SYSTEM: systemConfig,
			GIT_CONFIG_GLOBAL: globalConfig,
			GIT_CONFIG_NOSYSTEM: "0",
			GIT_PAGER: executable,
			PAGER: executable,
			GIT_EXTERNAL_DIFF: executable,
		})) {
			saved.set(key, process.env[key]);
			process.env[key] = value;
		}
		try {
			const baseline = captureBaseline({ ...f, projectRoot: f.root, output: "git-config-baseline.json" });
			expect(fs.existsSync(marker)).toBeFalse();

			fs.appendFileSync(path.join(f.root, "README.md"), "new committed bytes\n");
			const safeGitArgs = [
				"-C", f.root,
				"-c", "core.fsmonitor=false",
				"-c", "core.hooksPath=/dev/null",
			];
			execFileSync(f.git, [...safeGitArgs, "add", "README.md"]);
			execFileSync(f.git, [...safeGitArgs, "-c", "commit.gpgsign=false", "commit", "-qm", "advance head"]);
			if (fs.existsSync(marker)) fs.unlinkSync(marker);
			fs.writeFileSync(path.join(f.root, "hidden-by-config.txt"), "must remain visible\n");

			const evidence = captureEvidence({
				...f,
				projectRoot: f.root,
				baseline: String(patchValue(baseline, "baselinePath")),
				expectedBaselineDigest: String(patchValue(baseline, "baselineDigest")),
				acceptanceArgv: ["true"],
				acceptanceTimeout: 30,
				output: "git-config-evidence.json",
			});
			expect(fs.existsSync(marker)).toBeFalse();
			expect(String(patchValue(evidence, "evidence"))).toContain("hidden-by-config.txt");
		} finally {
			for (const [key, value] of saved) {
				if (value === undefined) delete process.env[key];
				else process.env[key] = value;
			}
		}
	});

	test("CLI emits one command-handler activation object", () => {
		const f = fixture();
		const run = spawnSync(process.execPath, [
			actionCli, "baseline",
			"--project-root", f.root,
			"--run-root", f.runRoot,
			"--helper", f.helper,
			"--helper-sha256", f.helperSha256,
			"--python", f.python,
			"--python-sha256", f.pythonSha256,
			"--git", f.git,
			"--git-sha256", f.gitSha256,
			"--output", "cli-baseline.json",
		], { encoding: "utf8" });
		expect(run.status).toBe(0);
		const result = JSON.parse(run.stdout);
		expect(result.statePatch.map((item: Record<string, unknown>) => item.path)).toEqual([
			"/baselinePath", "/baselineSummary", "/baselineDigest",
		]);
		const current = spawnSync(process.execPath, [
			actionCli, "current-state",
			"--project-root", f.root,
			"--run-root", f.runRoot,
			"--helper", f.helper,
			"--helper-sha256", f.helperSha256,
			"--python", f.python,
			"--python-sha256", f.pythonSha256,
			"--git", f.git,
			"--git-sha256", f.gitSha256,
			"--timeout-seconds", "30",
		], { encoding: "utf8" });
		expect(current.status).toBe(0);
		const binding = JSON.parse(current.stdout);
		expect(binding.finalStateDigest).toMatch(/^[0-9a-f]{64}$/);
		expect(binding.statePatch).toBeUndefined();
	});

	test("current-state explicit deadline fails closed without late output", () => {
		const helper = `
import json, pathlib, sys, time
root = pathlib.Path(sys.argv[sys.argv.index("--root") + 1])
late = root / "workflow-output" / "subagent" / "standard-round" / "run-1" / "late-current-state.json"
time.sleep(3)
late.write_text("should not exist\\n", encoding="utf-8")
print(json.dumps({"stateBinding": {"finalStateDigest": "b" * 64}}))
`;
		const f = fixture(helper);
		const late = path.join(f.runRoot, "late-current-state.json");
		const started = performance.now();
		expect(() => captureCurrentState({
			...f,
			projectRoot: f.root,
			timeoutSeconds: 1,
		})).toThrow("helper deadline exceeded");
		expect(performance.now() - started).toBeLessThan(2500);
		expect(fs.existsSync(late)).toBeFalse();
		expect(() => captureCurrentState({ ...f, projectRoot: f.root, timeoutSeconds: 0 })).toThrow("positive integer");

		const current = spawnSync(process.execPath, [
			actionCli, "current-state",
			"--project-root", f.root,
			"--run-root", f.runRoot,
			"--helper", f.helper,
			"--helper-sha256", f.helperSha256,
			"--python", f.python,
			"--python-sha256", f.pythonSha256,
			"--git", f.git,
			"--git-sha256", f.gitSha256,
			"--timeout-seconds", "1",
		], { encoding: "utf8" });
		expect(current.status).not.toBe(0);
		expect(current.stdout).toBe("");
		expect(current.stderr).toContain("helper deadline exceeded");
		expect(fs.existsSync(late)).toBeFalse();
	});
});
