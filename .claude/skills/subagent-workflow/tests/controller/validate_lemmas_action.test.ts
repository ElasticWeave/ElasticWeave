import { afterEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";

import {
	ValidateLemmasActionError,
	validateLemmas,
} from "../../scripts/controller/actions/validate_lemmas";

const sourceValidator = path.resolve(
	import.meta.dir,
	"../../../self-improve/scripts/validate_lemmas.py",
);
const actionCli = path.resolve(import.meta.dir, "../../scripts/controller/actions/validate_lemmas.ts");
const cleanups: string[] = [];

interface Fixture {
	root: string;
	validator: string;
	validatorSha256: string;
	python: string;
	pythonSha256: string;
}

function hashFile(file: string): string {
	return createHash("sha256").update(fs.readFileSync(file)).digest("hex");
}

function commandPath(name: string): string {
	return fs.realpathSync.native(execFileSync("which", [name], { encoding: "utf8" }).trim());
}

function fixture(validatorContent?: string): Fixture {
	const created = fs.mkdtempSync(path.join(os.tmpdir(), "validate-lemmas-action-"));
	const root = fs.realpathSync.native(created);
	cleanups.push(root);
	const validator = path.join(
		root, ".claude", "skills", "self-improve", "scripts", "validate_lemmas.py",
	);
	fs.mkdirSync(path.dirname(validator), { recursive: true });
	if (validatorContent === undefined) fs.copyFileSync(sourceValidator, validator);
	else fs.writeFileSync(validator, validatorContent);
	const python = commandPath("python3");
	return {
		root,
		validator,
		validatorSha256: hashFile(validator),
		python,
		pythonSha256: hashFile(python),
	};
}

function report(overrides: Record<string, unknown> = {}): string {
	return JSON.stringify({
		outcome: "NONE",
		reason: "\u6ca1\u6709\u53ef\u63a8\u5e7f\u7684\u673a\u5236\u66f4\u65b0",
		artifacts: [],
		behaviorChanged: false,
		...overrides,
	});
}

function validation(output: Record<string, unknown>): Record<string, unknown> {
	const patches = output.statePatch as Array<Record<string, unknown>>;
	expect(patches).toHaveLength(1);
	expect(patches[0]).toMatchObject({ op: "set", path: "/lemmaValidation" });
	expect(typeof patches[0].value).toBe("string");
	return JSON.parse(String(patches[0].value));
}

function cliArgs(f: Fixture, lemmaReport: string): string[] {
	return [
		actionCli,
		"--project-root", f.root,
		"--validator", f.validator,
		"--validator-sha256", f.validatorSha256,
		"--python", f.python,
		"--python-sha256", f.pythonSha256,
		"--lemma-report", lemmaReport,
	];
}

afterEach(() => {
	for (const directory of cleanups.splice(0)) fs.rmSync(directory, { recursive: true, force: true });
});

describe("validate lemmas action", () => {
	test("emits exactly one Unicode-safe lemmaValidation patch for a clean report", () => {
		const f = fixture();
		const output = validateLemmas({
			...f,
			projectRoot: f.root,
			lemmaReport: report({ reason: "\u8bc1\u636e\u8fb9\u754c\u660e\u786e\uFF1Aμ \u4e0e ✓" }),
		});
		const result = validation(output);
		expect(result).toMatchObject({
			schema: "lemma-validation/v1",
			ok: true,
			validatorExitCode: 0,
			validatorSignal: null,
			contractProblems: [],
		});
		const validatorOutput = result.validatorOutput as Record<string, unknown>;
		expect(validatorOutput.ok).toBeTrue();
		expect((validatorOutput.report as Record<string, unknown>).outcome).toBe("NONE");
	});

	test("keeps invalid lemmaReport and lemma violations as ok false review data", () => {
		const f = fixture();
		const invalidJson = validation(validateLemmas({
			...f,
			projectRoot: f.root,
			lemmaReport: "{\u4e0d\u662f JSON",
		}));
		expect(invalidJson.ok).toBeFalse();
		expect(invalidJson.validatorExitCode).toBe(2);
		expect(invalidJson.contractProblems).toEqual([]);
		expect(JSON.stringify(invalidJson.validatorOutput)).toContain("lemmaReport is not valid JSON");

		const invalidSchema = validation(validateLemmas({
			...f,
			projectRoot: f.root,
			lemmaReport: report({ behaviorChanged: true }),
		}));
		expect(invalidSchema.ok).toBeFalse();
		expect(invalidSchema.validatorExitCode).toBe(2);
		expect(JSON.stringify(invalidSchema.validatorOutput)).toContain("cannot set behaviorChanged true");
	});

	test("turns validator non-JSON and unexpected exit into structured review data with CLI exit zero", () => {
		const f = fixture(`
import sys
print("not-json-\u8bc1\u636e")
print("validator diagnostic", file=sys.stderr)
raise SystemExit(7)
`);
		const run = spawnSync(process.execPath, cliArgs(f, report()), { encoding: "utf8" });
		expect(run.status).toBe(0);
		const result = validation(JSON.parse(run.stdout));
		expect(result.ok).toBeFalse();
		expect(result.validatorExitCode).toBe(7);
		expect(result.validatorOutput).toBeNull();
		expect(result.stdout).toContain("not-json-\u8bc1\u636e");
		expect(result.stderr).toContain("validator diagnostic");
		expect(result.contractProblems).toEqual(expect.arrayContaining([
			expect.stringContaining("stdout was not JSON"),
			expect.stringContaining("unexpected status 7"),
		]));
	});

	test("uses an allowlist child environment and Python isolated mode", () => {
		const f = fixture(`
import json, os, sys
keys = [
  "GENERAL_AGENT_ALLOW_PROTECTED_WRITE", "GENERAL_AGENT_SECRET",
  "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
  "BASH_ENV", "ENV", "DYLD_INSERT_LIBRARIES", "LD_PRELOAD",
  "GIT_ASKPASS", "SSH_ASKPASS", "SSH_AUTH_SOCK", "PATH",
]
print(json.dumps({
  "ok": True,
  "problems": [],
  "lemmas": [],
  "report": {"environment": {key: os.environ.get(key) for key in keys}, "sysPath": sys.path},
}))
`);
		const marker = path.join(f.root, "pre-main-ran");
		const startup = path.join(os.tmpdir(), `lemma-startup-${process.pid}-${Date.now()}.py`);
		fs.writeFileSync(startup, `open(${JSON.stringify(marker)}, "w").write("bad")\n`);
		const injected = [
			"GENERAL_AGENT_ALLOW_PROTECTED_WRITE", "GENERAL_AGENT_SECRET",
			"NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "BASH_ENV", "ENV",
			"DYLD_INSERT_LIBRARIES", "LD_PRELOAD", "GIT_ASKPASS", "SSH_ASKPASS",
			"SSH_AUTH_SOCK", "PATH",
		];
		const saved = new Map<string, string | undefined>();
		for (const key of injected) {
			saved.set(key, process.env[key]);
			process.env[key] = "must-not-leak";
		}
		saved.set("PYTHONSTARTUP", process.env.PYTHONSTARTUP);
		process.env.PYTHONSTARTUP = startup;
		try {
			const result = validation(validateLemmas({
				...f,
				projectRoot: f.root,
				lemmaReport: report(),
			}));
			expect(result.ok).toBeTrue();
			const validatorOutput = result.validatorOutput as Record<string, unknown>;
			const validatorReport = validatorOutput.report as Record<string, unknown>;
			const environment = validatorReport.environment as Record<string, unknown>;
			for (const key of [...injected, "PYTHONSTARTUP"]) expect(environment[key]).toBeNull();
			expect(fs.existsSync(marker)).toBeFalse();
			expect((validatorReport.sysPath as string[]).some(item => item === "must-not-leak")).toBeFalse();
		} finally {
			for (const [key, value] of saved) {
				if (value === undefined) delete process.env[key];
				else process.env[key] = value;
			}
			fs.unlinkSync(startup);
		}
	});

	test("rejects digest drift before and after validator execution", () => {
		const f = fixture();
		expect(() => validateLemmas({
			...f,
			projectRoot: f.root,
			validatorSha256: "0".repeat(64),
			lemmaReport: report(),
		})).toThrow("validator SHA-256 drifted");

		const drifting = fixture(`
import json
from pathlib import Path
Path(__file__).write_text(Path(__file__).read_text(encoding="utf-8") + "# drift\\n", encoding="utf-8")
print(json.dumps({"ok": True, "problems": [], "lemmas": [], "report": {}}))
`);
		expect(() => validateLemmas({
			...drifting,
			projectRoot: drifting.root,
			lemmaReport: report(),
		})).toThrow("validator after invocation SHA-256 drifted");
	});

	test("rejects symlinks, validator path escape, and project-local Python", () => {
		const f = fixture();
		const outsideValidator = path.join(os.tmpdir(), `outside-validator-${process.pid}-${Date.now()}.py`);
		fs.copyFileSync(f.validator, outsideValidator);
		fs.unlinkSync(f.validator);
		fs.symlinkSync(outsideValidator, f.validator);
		try {
			expect(() => validateLemmas({
				...f,
				projectRoot: f.root,
				lemmaReport: report(),
			})).toThrow("no-follow file");
		} finally {
			fs.unlinkSync(outsideValidator);
		}

		const g = fixture();
		expect(() => validateLemmas({
			...g,
			projectRoot: g.root,
			validator: path.join(os.tmpdir(), "escaped-validator.py"),
			lemmaReport: report(),
		})).toThrow("validator must be");

		const localPython = path.join(g.root, "python3");
		fs.copyFileSync(g.python, localPython);
		fs.chmodSync(localPython, 0o700);
		expect(() => validateLemmas({
			...g,
			projectRoot: g.root,
			python: localPython,
			pythonSha256: hashFile(localPython),
			lemmaReport: report(),
		})).toThrow("outside the model-writable project root");
	});

	test("CLI fails nonzero only for adapter contract failures", () => {
		const f = fixture();
		const clean = spawnSync(process.execPath, cliArgs(f, report()), { encoding: "utf8" });
		expect(clean.status).toBe(0);
		expect(validation(JSON.parse(clean.stdout)).ok).toBeTrue();

		const badDigest = cliArgs(f, report());
		badDigest[badDigest.indexOf("--validator-sha256") + 1] = "f".repeat(64);
		const failed = spawnSync(process.execPath, badDigest, { encoding: "utf8" });
		expect(failed.status).toBe(1);
		expect(failed.stdout).toBe("");
		expect(failed.stderr).toContain("validator SHA-256 drifted");
	});
});
