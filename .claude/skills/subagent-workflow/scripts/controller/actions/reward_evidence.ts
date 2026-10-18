#!/usr/bin/env bun
/**
 * No-follow adapter for the frozen reward-hacking evidence helper.
 *
 * The controller supplies the helper digest and owns the process deadline.  This
 * adapter deliberately has no watchdog of its own.  It binds every artifact path
 * to one canonical workflow-output/subagent run directory, scrubs delegated
 * authority, and translates helper results into WorkflowActivationOutput patches.
 *
 * This is pathname hardening, not an OS sandbox.  A concurrent malicious process
 * running as the same UID can still race filesystem operations.
 */

import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

type JsonObject = Record<string, unknown>;

const SHA256 = /^[0-9a-f]{64}$/;
const AMBIENT_ENV_ALLOWLIST = new Set([
	"HOME",
	"TMPDIR",
	"TMP",
	"TEMP",
	"LANG",
	"LANGUAGE",
	"TZ",
	"USER",
	"LOGNAME",
	"TERM",
	"COLORTERM",
	"NO_COLOR",
	"CI",
]);

// Command-scope config has higher precedence than repository-local config.  Preserve
// layout semantics such as core.worktree/core.bare, but neutralize every config key
// used by these read-only commands that can execute code or import hidden ignore /
// attribute state.  The helper uses rev-parse, ls-files, status, cat-file, and
// diff --name-status; none requires a pager, hooks, fsmonitor, or external diff.
const GIT_CONFIG_OVERRIDES: ReadonlyArray<readonly [string, string]> = [
	["core.fsmonitor", "false"],
	["core.hooksPath", "/dev/null"],
	["core.excludesFile", "/dev/null"],
	["core.attributesFile", "/dev/null"],
	["diff.external", ""],
	["core.pager", "cat"],
	["pager.rev-parse", "false"],
	["pager.ls-files", "false"],
	["pager.status", "false"],
	["pager.cat-file", "false"],
	["pager.diff", "false"],
	["color.ui", "false"],
];

export class RewardEvidenceActionError extends Error {
	constructor(message: string) {
		super(`reward evidence action: ${message}`);
		this.name = "RewardEvidenceActionError";
	}
}

function isPlainObject(value: unknown): value is JsonObject {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function scrubEnvironment(source: NodeJS.ProcessEnv, controlledPath?: string): NodeJS.ProcessEnv {
	const clean: NodeJS.ProcessEnv = {};
	for (const [key, value] of Object.entries(source)) {
		if (value === undefined) continue;
		if (AMBIENT_ENV_ALLOWLIST.has(key) || key.startsWith("LC_")) clean[key] = value;
	}
	if (controlledPath !== undefined) clean.PATH = controlledPath;
	clean.PYTHONNOUSERSITE = "1";
	clean.GIT_TERMINAL_PROMPT = "0";
	clean.GIT_CONFIG_NOSYSTEM = "1";
	clean.GIT_CONFIG_SYSTEM = "/dev/null";
	clean.GIT_CONFIG_GLOBAL = "/dev/null";
	clean.GIT_ATTR_NOSYSTEM = "1";
	clean.GIT_PAGER = "cat";
	clean.PAGER = "cat";
	clean.GIT_OPTIONAL_LOCKS = "0";
	clean.GIT_CONFIG_COUNT = String(GIT_CONFIG_OVERRIDES.length);
	for (const [index, [key, value]] of GIT_CONFIG_OVERRIDES.entries()) {
		clean[`GIT_CONFIG_KEY_${index}`] = key;
		clean[`GIT_CONFIG_VALUE_${index}`] = value;
	}
	return clean;
}

function sha256Fd(fd: number): string {
	const hash = createHash("sha256");
	const buffer = Buffer.allocUnsafe(1024 * 1024);
	let position = 0;
	while (true) {
		const count = fs.readSync(fd, buffer, 0, buffer.length, position);
		if (count === 0) break;
		hash.update(buffer.subarray(0, count));
		position += count;
	}
	return hash.digest("hex");
}

function openRegularNoFollow(file: string, label: string): number {
	let fd: number;
	try {
		fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
	} catch (error) {
		throw new RewardEvidenceActionError(`${label} is not a readable no-follow file: ${file}: ${String(error)}`);
	}
	const stat = fs.fstatSync(fd);
	if (!stat.isFile()) {
		fs.closeSync(fd);
		throw new RewardEvidenceActionError(`${label} is not a regular file: ${file}`);
	}
	return fd;
}

function assertPlainDirectory(directory: string, label: string): void {
	let stat: fs.Stats;
	try {
		stat = fs.lstatSync(directory);
	} catch (error) {
		throw new RewardEvidenceActionError(`${label} is unavailable: ${directory}: ${String(error)}`);
	}
	if (stat.isSymbolicLink() || !stat.isDirectory()) {
		throw new RewardEvidenceActionError(`${label} is not a plain directory: ${directory}`);
	}
}

function pathLexists(candidate: string): boolean {
	try {
		fs.lstatSync(candidate);
		return true;
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
		throw new RewardEvidenceActionError(`cannot inspect path: ${candidate}: ${String(error)}`);
	}
}

function assertPlainPathFrom(base: string, target: string, label: string): void {
	const relative = path.relative(base, target);
	if (relative === "" || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
		throw new RewardEvidenceActionError(`${label} is outside its required parent: ${target}`);
	}
	let cursor = base;
	for (const component of relative.split(path.sep)) {
		cursor = path.join(cursor, component);
		assertPlainDirectory(cursor, label);
	}
}

function canonicalProjectDirectory(raw: string): string {
	const resolved = path.resolve(raw);
	let canonical: string;
	try {
		canonical = fs.realpathSync.native(resolved);
	} catch (error) {
		throw new RewardEvidenceActionError(`project root is unavailable: ${resolved}: ${String(error)}`);
	}
	if (canonical !== resolved) throw new RewardEvidenceActionError(`project root is not canonical: ${resolved}`);
	assertPlainDirectory(canonical, "project root");
	return canonical;
}

function verifyFileDigest(file: string, expectedDigest: string, label: string): void {
	if (!SHA256.test(expectedDigest)) throw new RewardEvidenceActionError(`${label} SHA must be lowercase SHA-256`);
	const fd = openRegularNoFollow(file, label);
	try {
		if (sha256Fd(fd) !== expectedDigest) throw new RewardEvidenceActionError(`${label} SHA-256 drifted from the frozen digest`);
	} finally {
		fs.closeSync(fd);
	}
}

function trustedExecutable(projectRoot: string, raw: string, expectedDigest: string, label: string): string {
	if (!path.isAbsolute(raw) || path.resolve(raw) !== raw) {
		throw new RewardEvidenceActionError(`${label} must be an absolute canonical path`);
	}
	let canonical: string;
	try {
		canonical = fs.realpathSync.native(raw);
	} catch (error) {
		throw new RewardEvidenceActionError(`${label} is unavailable: ${raw}: ${String(error)}`);
	}
	if (canonical !== raw) throw new RewardEvidenceActionError(`${label} must not be a symlink: ${raw}`);
	const relative = path.relative(projectRoot, canonical);
	if (!relative || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))) {
		throw new RewardEvidenceActionError(`${label} must be outside the model-writable project root`);
	}
	fs.accessSync(canonical, fs.constants.X_OK);
	verifyFileDigest(canonical, expectedDigest, label);
	return canonical;
}

function controlledExecutablePath(python: string, git: string): string {
	const candidates = [path.dirname(git), path.dirname(python), "/bin", "/usr/bin"];
	const canonical = candidates.map(directory => fs.realpathSync.native(directory));
	for (const directory of canonical) assertPlainDirectory(directory, "trusted PATH entry");
	return [...new Set(canonical)].join(path.delimiter);
}

function assertGitTop(projectRoot: string, git: string, env: NodeJS.ProcessEnv): void {
	const probe = spawnSync(git, ["-C", projectRoot, "rev-parse", "--show-toplevel"], {
		encoding: "utf8",
		env,
	});
	if (probe.error || probe.status !== 0 || path.resolve(probe.stdout.trim()) !== projectRoot) {
		throw new RewardEvidenceActionError("project root must be the Git top level");
	}
}

function canonicalRunRoot(projectRoot: string, raw: string): string {
	const resolved = path.resolve(raw);
	const prefix = path.join(projectRoot, "workflow-output", "subagent");
	const relative = path.relative(prefix, resolved);
	if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
		throw new RewardEvidenceActionError(`run root must be below ${prefix}`);
	}
	assertPlainDirectory(prefix, "subagent output root");
	assertPlainPathFrom(prefix, resolved, "run root");
	const canonical = fs.realpathSync.native(resolved);
	if (canonical !== resolved) throw new RewardEvidenceActionError(`run root is not canonical: ${resolved}`);
	return canonical;
}

function safeRelative(raw: string, label: string): string {
	if (!raw || path.isAbsolute(raw) || raw.includes("\0")) {
		throw new RewardEvidenceActionError(`${label} must be a non-empty relative path`);
	}
	const normalized = path.normalize(raw);
	if (normalized !== raw || normalized === "." || normalized === ".." || normalized.startsWith(`..${path.sep}`)) {
		throw new RewardEvidenceActionError(`${label} is path-unsafe: ${JSON.stringify(raw)}`);
	}
	return normalized;
}

function ensureOutputParent(runRoot: string, relativeFile: string): string {
	const safe = safeRelative(relativeFile, "output");
	const output = path.join(runRoot, safe);
	const parent = path.dirname(output);
	const relativeParent = path.relative(runRoot, parent);
	let cursor = runRoot;
	if (relativeParent && relativeParent !== ".") {
		for (const component of relativeParent.split(path.sep)) {
			cursor = path.join(cursor, component);
			if (!pathLexists(cursor)) fs.mkdirSync(cursor, { mode: 0o700 });
			assertPlainDirectory(cursor, "output parent");
		}
	}
	if (pathLexists(output) || fs.lstatSync(parent).isSymbolicLink()) {
		throw new RewardEvidenceActionError(`output must not already exist: ${output}`);
	}
	return output;
}

function runInputFile(runRoot: string, raw: string, label: string): string {
	const candidate = path.isAbsolute(raw) ? path.resolve(raw) : path.join(runRoot, safeRelative(raw, label));
	const relative = path.relative(runRoot, candidate);
	if (!relative || relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
		throw new RewardEvidenceActionError(`${label} must be below the run root`);
	}
	const parent = path.dirname(candidate);
	if (parent !== runRoot) assertPlainPathFrom(runRoot, parent, `${label} parent`);
	const fd = openRegularNoFollow(candidate, label);
	fs.closeSync(fd);
	return candidate;
}

function validateHelper(projectRoot: string, helperRaw: string, expectedDigest: string): string {
	const expected = path.join(
		projectRoot,
		".claude", "skills", "subagent-workflow", "scripts", "native", "reward_hacking_evidence.py",
	);
	const helper = path.resolve(helperRaw);
	if (helper !== expected) throw new RewardEvidenceActionError(`helper must be ${expected}`);
	assertPlainPathFrom(projectRoot, path.dirname(helper), "helper parent");
	verifyFileDigest(helper, expectedDigest, "helper");
	return helper;
}

function parseHelperOutput(stdout: string): JsonObject {
	let value: unknown;
	try {
		value = JSON.parse(stdout.trim());
	} catch (error) {
		throw new RewardEvidenceActionError(`helper did not return one JSON object: ${String(error)}`);
	}
	if (!isPlainObject(value)) throw new RewardEvidenceActionError("helper result is not a JSON object");
	return value;
}

function executeHelper(
	helper: string,
	helperDigest: string,
	python: string,
	pythonDigest: string,
	git: string,
	gitDigest: string,
	env: NodeJS.ProcessEnv,
	argv: string[],
	timeoutMs?: number,
): JsonObject {
	const result = spawnSync(python, ["-I", helper, ...argv], {
		encoding: "utf8",
		env,
		maxBuffer: 128 * 1024 * 1024,
		...(timeoutMs === undefined ? {} : { timeout: timeoutMs, killSignal: "SIGTERM" as const }),
	});
	if ((result.error as NodeJS.ErrnoException | undefined)?.code === "ETIMEDOUT") {
		throw new RewardEvidenceActionError("helper deadline exceeded");
	}
	if (result.error) throw new RewardEvidenceActionError(`helper launch failed: ${String(result.error)}`);
	if (result.status !== 0) {
		throw new RewardEvidenceActionError(`helper failed with exit ${String(result.status)}: ${result.stderr.trim()}`);
	}
	verifyFileDigest(helper, helperDigest, "helper after invocation");
	verifyFileDigest(python, pythonDigest, "Python interpreter after invocation");
	verifyFileDigest(git, gitDigest, "Git executable after invocation");
	return parseHelperOutput(result.stdout);
}

function requireString(object: JsonObject, key: string): string {
	const value = object[key];
	if (typeof value !== "string" || value.length === 0) throw new RewardEvidenceActionError(`helper result has no ${key}`);
	return value;
}

function requireObject(object: JsonObject, key: string): JsonObject {
	const value = object[key];
	if (!isPlainObject(value)) throw new RewardEvidenceActionError(`helper result has no ${key} object`);
	return value;
}

function confirmOutput(output: string, helperPath: string, label: string): void {
	if (path.resolve(helperPath) !== output) throw new RewardEvidenceActionError(`${label} helper path does not match explicit output`);
	const fd = openRegularNoFollow(output, label);
	fs.closeSync(fd);
}

function patch(pathName: string, value: unknown): JsonObject {
	return { op: "set", path: `/${pathName}`, value };
}

interface CommonOptions {
	projectRoot: string;
	runRoot: string;
	helper: string;
	helperSha256: string;
	python: string;
	pythonSha256: string;
	git: string;
	gitSha256: string;
}

function commonContext(options: CommonOptions): {
	projectRoot: string;
	runRoot: string;
	helper: string;
	python: string;
	git: string;
	env: NodeJS.ProcessEnv;
} {
	const projectRoot = canonicalProjectDirectory(options.projectRoot);
	const python = trustedExecutable(projectRoot, options.python, options.pythonSha256, "Python interpreter");
	const git = trustedExecutable(projectRoot, options.git, options.gitSha256, "Git executable");
	const env = scrubEnvironment(process.env, controlledExecutablePath(python, git));
	assertGitTop(projectRoot, git, env);
	const runRoot = canonicalRunRoot(projectRoot, options.runRoot);
	const helper = validateHelper(projectRoot, options.helper, options.helperSha256);
	return { projectRoot, runRoot, helper, python, git, env };
}

export function captureBaseline(options: CommonOptions & { output: string }): JsonObject {
	const { projectRoot, runRoot, helper, python, git, env } = commonContext(options);
	const output = ensureOutputParent(runRoot, options.output);
	const outputRelative = path.relative(runRoot, output);
	const result = executeHelper(helper, options.helperSha256, python, options.pythonSha256, git, options.gitSha256, env, [
		"baseline", "--root", projectRoot, "--output", output,
	]);
	confirmOutput(output, requireString(result, "baselinePath"), "baseline output");
	const digest = requireString(result, "baselineDigest");
	if (!SHA256.test(digest)) throw new RewardEvidenceActionError("baseline digest is not SHA-256");
	const summary = requireObject(result, "baselineSummary");
	return {
		summary: `captured deterministic baseline ${digest}`,
		statePatch: [
			patch("baselinePath", outputRelative),
			patch("baselineSummary", JSON.stringify(summary)),
			patch("baselineDigest", digest),
		],
	};
}

export function captureEvidence(options: CommonOptions & {
	baseline: string;
	expectedBaselineDigest: string;
	acceptanceArgv: string[];
	acceptanceTimeout: number;
	output: string;
}): JsonObject {
	const { projectRoot, runRoot, helper, python, git, env } = commonContext(options);
	const baseline = runInputFile(runRoot, options.baseline, "baseline");
	if (!SHA256.test(options.expectedBaselineDigest)) throw new RewardEvidenceActionError("expected baseline digest is not SHA-256");
	if (!Array.isArray(options.acceptanceArgv) || options.acceptanceArgv.length === 0 ||
		options.acceptanceArgv.some(value => typeof value !== "string" || !value || value.includes("\0"))) {
		throw new RewardEvidenceActionError("acceptance argv must be a non-empty array of non-empty strings");
	}
	if (!Number.isSafeInteger(options.acceptanceTimeout) || options.acceptanceTimeout <= 0) {
		throw new RewardEvidenceActionError("acceptance timeout must be a positive integer");
	}
	const output = ensureOutputParent(runRoot, options.output);
	const outputRelative = path.relative(runRoot, output);
	const result = executeHelper(helper, options.helperSha256, python, options.pythonSha256, git, options.gitSha256, env, [
		"evidence",
		"--root", projectRoot,
		"--baseline", baseline,
		"--expected-baseline-digest", options.expectedBaselineDigest,
		"--acceptance-argv-json", JSON.stringify(options.acceptanceArgv),
		"--timeout", String(options.acceptanceTimeout),
		"--output", output,
	]);
	confirmOutput(output, requireString(result, "evidencePath"), "evidence output");
	const digest = requireString(result, "evidenceDigest");
	if (!SHA256.test(digest)) throw new RewardEvidenceActionError("evidence digest is not SHA-256");
	const evidence = requireObject(result, "evidence");
	const acceptance = requireObject(evidence, "acceptance");
	if (acceptance.timedOut !== false || acceptance.exitCode !== 0) {
		throw new RewardEvidenceActionError("acceptance check did not succeed; evidence cannot advance the workflow");
	}
	const binding = requireObject(result, "stateBinding");
	return {
		summary: `captured deterministic evidence ${digest}`,
		statePatch: [
			patch("evidence", JSON.stringify(evidence)),
			patch("evidencePath", outputRelative),
			patch("evidenceDigest", digest),
			patch("stateBinding", JSON.stringify(binding)),
		],
	};
}

export function captureCurrentState(options: CommonOptions & { timeoutSeconds: number }): JsonObject {
	const timeoutSeconds = options.timeoutSeconds;
	if (!Number.isSafeInteger(timeoutSeconds) || timeoutSeconds <= 0) {
		throw new RewardEvidenceActionError("current-state timeoutSeconds must be a positive integer");
	}
	const timeoutMs = timeoutSeconds * 1000;
	if (!Number.isSafeInteger(timeoutMs) || timeoutMs > 2_147_483_647) {
		throw new RewardEvidenceActionError("current-state timeoutSeconds is too large");
	}
	const { projectRoot, helper, python, git, env } = commonContext(options);
	const result = executeHelper(
		helper,
		options.helperSha256,
		python,
		options.pythonSha256,
		git,
		options.gitSha256,
		env,
		["current-state", "--root", projectRoot],
		timeoutMs,
	);
	return requireObject(result, "stateBinding");
}

function parseFlags(argv: string[]): { command: string; flags: Map<string, string> } {
	const command = argv[0] ?? "";
	if (!new Set(["baseline", "evidence", "current-state"]).has(command)) {
		throw new RewardEvidenceActionError("usage: reward_evidence.ts baseline|evidence|current-state [flags]");
	}
	const flags = new Map<string, string>();
	for (let index = 1; index < argv.length; index += 2) {
		const key = argv[index];
		const value = argv[index + 1];
		if (!key?.startsWith("--") || value === undefined || value.startsWith("--") || flags.has(key)) {
			throw new RewardEvidenceActionError(`invalid or duplicate flag near ${JSON.stringify(key)}`);
		}
		flags.set(key, value);
	}
	return { command, flags };
}

function exactFlags(flags: Map<string, string>, required: string[]): Record<string, string> {
	const allowed = new Set(required);
	for (const key of flags.keys()) if (!allowed.has(key)) throw new RewardEvidenceActionError(`unknown flag ${key}`);
	const output: Record<string, string> = {};
	for (const key of required) {
		const value = flags.get(key);
		if (value === undefined) throw new RewardEvidenceActionError(`missing required flag ${key}`);
		output[key] = value;
	}
	return output;
}

export function main(argv = process.argv.slice(2)): JsonObject {
	const { command, flags } = parseFlags(argv);
	const commonKeys = [
		"--project-root", "--run-root", "--helper", "--helper-sha256",
		"--python", "--python-sha256", "--git", "--git-sha256",
	];
	if (command === "baseline") {
		const values = exactFlags(flags, [...commonKeys, "--output"]);
		return captureBaseline({
			projectRoot: values["--project-root"], runRoot: values["--run-root"],
			helper: values["--helper"], helperSha256: values["--helper-sha256"], output: values["--output"],
			python: values["--python"], pythonSha256: values["--python-sha256"],
			git: values["--git"], gitSha256: values["--git-sha256"],
		});
	}
	if (command === "evidence") {
		const values = exactFlags(flags, [...commonKeys, "--baseline", "--expected-baseline-digest", "--acceptance-argv-json", "--acceptance-timeout", "--output"]);
		let acceptanceArgv: unknown;
		try {
			acceptanceArgv = JSON.parse(values["--acceptance-argv-json"]);
		} catch (error) {
			throw new RewardEvidenceActionError(`acceptance argv JSON is invalid: ${String(error)}`);
		}
		return captureEvidence({
			projectRoot: values["--project-root"], runRoot: values["--run-root"],
			helper: values["--helper"], helperSha256: values["--helper-sha256"],
			python: values["--python"], pythonSha256: values["--python-sha256"],
			git: values["--git"], gitSha256: values["--git-sha256"],
			baseline: values["--baseline"], expectedBaselineDigest: values["--expected-baseline-digest"],
			acceptanceArgv: acceptanceArgv as string[], acceptanceTimeout: Number(values["--acceptance-timeout"]), output: values["--output"],
		});
	}
	const values = exactFlags(flags, [...commonKeys, "--timeout-seconds"]);
	return captureCurrentState({
		projectRoot: values["--project-root"], runRoot: values["--run-root"],
		helper: values["--helper"], helperSha256: values["--helper-sha256"],
		python: values["--python"], pythonSha256: values["--python-sha256"],
		git: values["--git"], gitSha256: values["--git-sha256"],
		timeoutSeconds: Number(values["--timeout-seconds"]),
	});
}

if (import.meta.main) {
	try {
		process.stdout.write(`${JSON.stringify(main())}\n`);
	} catch (error) {
		process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
		process.exitCode = 1;
	}
}
