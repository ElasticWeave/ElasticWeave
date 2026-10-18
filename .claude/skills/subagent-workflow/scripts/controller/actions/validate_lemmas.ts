#!/usr/bin/env bun
/**
 * Deterministic adapter for the self-improve lemma validator.
 *
 * The workflow controller owns the process deadline. This adapter intentionally
 * does not install a watchdog. It authenticates the project-local validator and
 * the external Python interpreter before and after invocation, runs Python in
 * isolated mode with an allowlisted environment, and converts every validator
 * verdict (including invalid JSON and non-zero exits) into one conservative
 * `/lemmaValidation` state value for the evolve reviewer.
 *
 * Path/integrity/launch failures are adapter failures and exit non-zero. Lemma
 * violations are validation data and do not change the adapter exit status.
 */

import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";

type JsonObject = Record<string, unknown>;

const SHA256 = /^[0-9a-f]{64}$/;
const MAX_CAPTURE_BYTES = 16 * 1024 * 1024;
const VALIDATOR_RELATIVE = path.join(
	".claude", "skills", "self-improve", "scripts", "validate_lemmas.py",
);
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

export interface ValidateLemmasOptions {
	projectRoot: string;
	validator: string;
	validatorSha256: string;
	python: string;
	pythonSha256: string;
	lemmaReport: string;
}

export class ValidateLemmasActionError extends Error {
	constructor(message: string) {
		super(`validate lemmas action: ${message}`);
		this.name = "ValidateLemmasActionError";
	}
}

function isPlainObject(value: unknown): value is JsonObject {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function scrubEnvironment(source: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
	const clean: NodeJS.ProcessEnv = {};
	for (const [key, value] of Object.entries(source)) {
		if (value === undefined) continue;
		if (AMBIENT_ENV_ALLOWLIST.has(key) || key.startsWith("LC_")) clean[key] = value;
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
		throw new ValidateLemmasActionError(
			`${label} is not a readable no-follow file: ${file}: ${String(error)}`,
		);
	}
	const stat = fs.fstatSync(fd);
	if (!stat.isFile()) {
		fs.closeSync(fd);
		throw new ValidateLemmasActionError(`${label} is not a regular file: ${file}`);
	}
	return fd;
}

function assertPlainDirectory(directory: string, label: string): void {
	let stat: fs.Stats;
	try {
		stat = fs.lstatSync(directory);
	} catch (error) {
		throw new ValidateLemmasActionError(`${label} is unavailable: ${directory}: ${String(error)}`);
	}
	if (stat.isSymbolicLink() || !stat.isDirectory()) {
		throw new ValidateLemmasActionError(`${label} is not a plain directory: ${directory}`);
	}
}

function canonicalProjectDirectory(raw: string): string {
	if (!path.isAbsolute(raw) || path.resolve(raw) !== raw) {
		throw new ValidateLemmasActionError("project root must be an absolute canonical path");
	}
	let canonical: string;
	try {
		canonical = fs.realpathSync.native(raw);
	} catch (error) {
		throw new ValidateLemmasActionError(`project root is unavailable: ${raw}: ${String(error)}`);
	}
	if (canonical !== raw) throw new ValidateLemmasActionError(`project root is not canonical: ${raw}`);
	assertPlainDirectory(canonical, "project root");
	return canonical;
}

function verifyFileDigest(file: string, expectedDigest: string, label: string): void {
	if (!SHA256.test(expectedDigest)) {
		throw new ValidateLemmasActionError(`${label} SHA must be lowercase SHA-256`);
	}
	const fd = openRegularNoFollow(file, label);
	try {
		if (sha256Fd(fd) !== expectedDigest) {
			throw new ValidateLemmasActionError(`${label} SHA-256 drifted from the frozen digest`);
		}
	} finally {
		fs.closeSync(fd);
	}
}

function assertPlainParents(base: string, file: string, label: string): void {
	const parent = path.dirname(file);
	const relative = path.relative(base, parent);
	if (
		relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)
	) {
		throw new ValidateLemmasActionError(`${label} escapes the project root: ${file}`);
	}
	let cursor = base;
	if (relative && relative !== ".") {
		for (const component of relative.split(path.sep)) {
			cursor = path.join(cursor, component);
			assertPlainDirectory(cursor, `${label} parent`);
		}
	}
}

function trustedValidator(projectRoot: string, raw: string, digest: string): string {
	const expected = path.join(projectRoot, VALIDATOR_RELATIVE);
	if (!path.isAbsolute(raw) || path.resolve(raw) !== raw || raw !== expected) {
		throw new ValidateLemmasActionError(`validator must be ${expected}`);
	}
	assertPlainParents(projectRoot, raw, "validator");
	verifyFileDigest(raw, digest, "validator");
	return raw;
}

function trustedPython(projectRoot: string, raw: string, digest: string): string {
	if (!path.isAbsolute(raw) || path.resolve(raw) !== raw) {
		throw new ValidateLemmasActionError("Python interpreter must be an absolute canonical path");
	}
	let canonical: string;
	try {
		canonical = fs.realpathSync.native(raw);
	} catch (error) {
		throw new ValidateLemmasActionError(`Python interpreter is unavailable: ${raw}: ${String(error)}`);
	}
	if (canonical !== raw) {
		throw new ValidateLemmasActionError(`Python interpreter must not be a symlink: ${raw}`);
	}
	const relative = path.relative(projectRoot, canonical);
	if (!relative || (relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative))) {
		throw new ValidateLemmasActionError("Python interpreter must be outside the model-writable project root");
	}
	try {
		fs.accessSync(canonical, fs.constants.X_OK);
	} catch (error) {
		throw new ValidateLemmasActionError(`Python interpreter is not executable: ${String(error)}`);
	}
	verifyFileDigest(canonical, digest, "Python interpreter");
	return canonical;
}

function parseValidatorJson(stdout: string): { value: JsonObject | null; problem: string | null } {
	const trimmed = stdout.trim();
	if (!trimmed) return { value: null, problem: "validator stdout was empty" };
	let parsed: unknown;
	try {
		parsed = JSON.parse(trimmed);
	} catch (error) {
		return { value: null, problem: `validator stdout was not JSON: ${String(error)}` };
	}
	if (!isPlainObject(parsed)) {
		return { value: null, problem: "validator stdout JSON was not an object" };
	}
	return { value: parsed, problem: null };
}

function normalizedValidation(
	status: number | null,
	signal: NodeJS.Signals | null,
	stdout: string,
	stderr: string,
): JsonObject {
	const parsed = parseValidatorJson(stdout);
	const contractProblems: string[] = [];
	if (parsed.problem) contractProblems.push(parsed.problem);
	if (status !== 0 && status !== 2) {
		contractProblems.push(`validator exited with unexpected status ${String(status)}`);
	}
	if (signal !== null) contractProblems.push(`validator terminated by signal ${signal}`);
	if (parsed.value !== null && typeof parsed.value.ok !== "boolean") {
		contractProblems.push("validator JSON has no boolean ok field");
	}
	if (
		parsed.value !== null
		&& (!Array.isArray(parsed.value.problems)
			|| parsed.value.problems.some(item => typeof item !== "string"))
	) {
		contractProblems.push("validator JSON has no string-array problems field");
	}
	const validatorOk = parsed.value?.ok === true;
	const ok = status === 0 && signal === null && validatorOk && contractProblems.length === 0;
	return {
		schema: "lemma-validation/v1",
		ok,
		validatorExitCode: status,
		validatorSignal: signal,
		contractProblems,
		validatorOutput: parsed.value,
		stdout: parsed.value === null ? stdout : "",
		stderr,
	};
}

function patch(value: unknown): JsonObject {
	return { op: "set", path: "/lemmaValidation", value };
}

export function validateLemmas(options: ValidateLemmasOptions): JsonObject {
	const projectRoot = canonicalProjectDirectory(options.projectRoot);
	const validator = trustedValidator(projectRoot, options.validator, options.validatorSha256);
	const python = trustedPython(projectRoot, options.python, options.pythonSha256);
	const env = scrubEnvironment(process.env);
	const result = spawnSync(python, [
		"-I",
		validator,
		"--root", projectRoot,
		"--json",
		"--report-json", options.lemmaReport,
	], {
		cwd: projectRoot,
		encoding: "utf8",
		env,
		maxBuffer: MAX_CAPTURE_BYTES,
	});
	if (result.error) {
		throw new ValidateLemmasActionError(`validator launch failed: ${String(result.error)}`);
	}

	verifyFileDigest(validator, options.validatorSha256, "validator after invocation");
	verifyFileDigest(python, options.pythonSha256, "Python interpreter after invocation");

	const validation = normalizedValidation(result.status, result.signal, result.stdout, result.stderr);
	const serialized = JSON.stringify(validation);
	if (typeof serialized !== "string") {
		throw new ValidateLemmasActionError("could not serialize the validation summary");
	}
	return {
		summary: validation.ok === true ? "lemma validation passed" : "lemma validation requires evolve review",
		statePatch: [patch(serialized)],
	};
}

function parseFlags(argv: string[]): Map<string, string> {
	const flags = new Map<string, string>();
	for (let index = 0; index < argv.length; index += 2) {
		const key = argv[index];
		const value = argv[index + 1];
		if (!key?.startsWith("--") || value === undefined || value.startsWith("--") || flags.has(key)) {
			throw new ValidateLemmasActionError(`invalid or duplicate flag near ${JSON.stringify(key)}`);
		}
		flags.set(key, value);
	}
	return flags;
}

function exactFlags(flags: Map<string, string>): Record<string, string> {
	const required = [
		"--project-root",
		"--validator",
		"--validator-sha256",
		"--python",
		"--python-sha256",
		"--lemma-report",
	];
	const allowed = new Set(required);
	for (const key of flags.keys()) {
		if (!allowed.has(key)) throw new ValidateLemmasActionError(`unknown flag ${key}`);
	}
	const output: Record<string, string> = {};
	for (const key of required) {
		const value = flags.get(key);
		if (value === undefined) throw new ValidateLemmasActionError(`missing required flag ${key}`);
		output[key] = value;
	}
	return output;
}

export function main(argv = process.argv.slice(2)): JsonObject {
	const values = exactFlags(parseFlags(argv));
	return validateLemmas({
		projectRoot: values["--project-root"],
		validator: values["--validator"],
		validatorSha256: values["--validator-sha256"],
		python: values["--python"],
		pythonSha256: values["--python-sha256"],
		lemmaReport: values["--lemma-report"],
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
