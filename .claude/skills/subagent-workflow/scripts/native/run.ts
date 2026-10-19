/**
 * Harness-neutral native-provider execution core for the built-in workflow controller.
 *
 * It owns provider classification, argv rendering, environment scrubbing, process lifecycle,
 * bounded recovery, session journals, and the final-line activation contract. Graph scheduling,
 * workflow state, terminal decisions, and result persistence remain controller responsibilities.
 */
import * as fs from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

/** Native agent turns must have enough time for long-running reasoning models such as Fable. */
export const NATIVE_AGENT_TIMEOUT_SECONDS = 3_600;
/** One initial call plus four bounded recovery waits. */
export const MAX_NATIVE_RECOVERY_RETRIES = 4;
export const NATIVE_RECOVERY_BACKOFF_SECONDS = [300, 600, 1_800, 3_600] as const;

export interface ProviderEntry {
	bin: string;
	nativeProviderFamily?: string; // semantic native harness family; executable identity remains authoritative
	args: string[]; // template tokens; {model} and {prompt} are substituted as WHOLE argv elements
	readArgs?: string[]; // canonical fail-closed read-only profile for reviewer/analysis calls
	readOnlyArgs?: string[]; // legacy/target alias; normalized to readArgs before native execution
	schedulerArgs?: string[]; // fail-closed, tool-free profile for bounded scheduling calls
	effort?: {
		values: string[];
		args?: string[]; // prepended CLI tokens; supports {effort}
		model?: string; // alternate model token; supports {model} and {effort}
		// `catalog-model-id` means the reasoning level is already part of the concrete
		// catalog model id the launch receipt resolved (Cursor's `-thinking-<effort>`
		// and `-<effort>` SKUs). The runtime must then add nothing: appending an argv
		// effort or rewriting the model token would launch a parameterization the
		// authenticated catalog never listed.
		encoding?: "catalog-model-id";
	};
	relayModelPrefixes?: string[]; // explicitly audited cross-family model prefixes (for relay CLIs)
	modelMap?: Record<string, string>;
	/** Inactivity/recovery policy for providers that do not expose resumable sessions. */
	timeoutSeconds?: number;
	backoffSeconds?: number[];
	session?: NativeSessionProfile;
}

export interface NativeSessionArgInsertion {
	position: "start" | "after";
	anchor?: string;
	args: string[];
}

export interface NativeSessionProfile {
	idSource: "assigned-uuid" | "create-stdout" | "jsonl-event";
	createArgs?: string[];
	start?: NativeSessionArgInsertion;
	resume: NativeSessionArgInsertion;
	eventType?: string;
	eventIdField?: string;
	output?: "text" | "jsonl-agent-message";
	continuePrompt: string;
	heartbeatSeconds?: number;
	stallSeconds?: number;
	backoffSeconds: number[];
}
export interface NativeMap {
	providers: Record<string, ProviderEntry>;
	// Relay operators rename providers freely in ~/.omp/agent/models.yml ("cloud code" ≠ anthropic).
	// When the provider NAME isn't in `providers`, classify by the BARE MODEL ID prefix instead —
	// the model id is what actually tells us which native CLI can serve it.
	modelPrefixes?: Record<string, string>; // prefix -> providers key, longest match wins
}

export interface NativeExecutionClosureEntry {
	role: "interpreter" | "entrypoint" | "runtime" | "bundle";
	path: string;
	sha256: string;
}

/** Exact executable closure authorized by one fresh native launch receipt. */
export interface NativeLaunchAuthorization {
	backend: string;
	provider: string;
	logicalExecutable: string;
	catalogModelId: string;
	executable: string;
	executableSha256: string;
	invocationPrefix: string[];
	executionClosure: NativeExecutionClosureEntry[];
	executionClosureSha256: string;
	launchEnvironment: Record<string, string>;
	launchEnvironmentSha256: string;
	launchReceiptSha256: string;
}

export interface NativeRunnerOptions {
	cwd: string;
	signal?: AbortSignal;
	env?: Record<string, string>;
	authorization: NativeLaunchAuthorization;
}

export interface NativeProviderIdentity {
	executionProviderFamily: string;
	executionHarness: string;
	upstreamProviderFamily: string;
	mappedModel: string;
}

function validateSessionInsertion(
	value: unknown,
	field: string,
	entry: ProviderEntry,
	required: boolean,
): NativeSessionArgInsertion | undefined {
	if (value === undefined && !required) return undefined;
	if (!value || typeof value !== "object" || Array.isArray(value)) {
		throw new Error(`native CLI '${entry.bin}' has a malformed ${field} session insertion`);
	}
	const insertion = value as NativeSessionArgInsertion;
	if (!["start", "after"].includes(insertion.position) ||
		!Array.isArray(insertion.args) || insertion.args.some(token => typeof token !== "string") ||
		(insertion.position === "after" && (typeof insertion.anchor !== "string" || !insertion.anchor)) ||
		(insertion.position === "start" && insertion.anchor !== undefined)) {
		throw new Error(`native CLI '${entry.bin}' has a malformed ${field} session insertion`);
	}
	return { ...insertion, args: [...insertion.args] };
}

function normalizeSessionProfile(entry: ProviderEntry): NativeSessionProfile | undefined {
	const value = entry.session;
	if (value === undefined) return undefined;
	if (!value || typeof value !== "object" || Array.isArray(value) ||
		!["assigned-uuid", "create-stdout", "jsonl-event"].includes(value.idSource)) {
		throw new Error(`native CLI '${entry.bin}' has a malformed session profile`);
	}
	const start = validateSessionInsertion(value.start, "start", entry, false);
	const resume = validateSessionInsertion(value.resume, "resume", entry, true)!;
	if (!resume.args.some(token => token.includes("{sessionId}"))) {
		throw new Error(`native CLI '${entry.bin}' resume session insertion must reference {sessionId}`);
	}
	if (value.idSource === "assigned-uuid" && !start?.args.some(token => token.includes("{sessionId}"))) {
		throw new Error(`native CLI '${entry.bin}' assigned session start must reference {sessionId}`);
	}
	if (value.idSource === "create-stdout" && !start?.args.some(token => token.includes("{sessionId}"))) {
		throw new Error(`native CLI '${entry.bin}' create-stdout session start must reference {sessionId}`);
	}
	if (value.idSource === "create-stdout" &&
		(!Array.isArray(value.createArgs) || value.createArgs.length === 0 || value.createArgs.some(token => typeof token !== "string"))) {
		throw new Error(`native CLI '${entry.bin}' create-stdout session must declare createArgs`);
	}
	if (value.idSource === "jsonl-event" &&
		(typeof value.eventType !== "string" || !value.eventType || typeof value.eventIdField !== "string" || !value.eventIdField)) {
		throw new Error(`native CLI '${entry.bin}' jsonl session must declare eventType and eventIdField`);
	}
	if (value.output !== undefined && !["text", "jsonl-agent-message"].includes(value.output)) {
		throw new Error(`native CLI '${entry.bin}' has an unsupported session output format`);
	}
	if (typeof value.continuePrompt !== "string" || !value.continuePrompt.trim()) {
		throw new Error(`native CLI '${entry.bin}' session continuePrompt must be non-empty`);
	}
	for (const [field, seconds] of [["heartbeatSeconds", value.heartbeatSeconds], ["stallSeconds", value.stallSeconds]] as const) {
		if (seconds !== undefined && (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0)) {
			throw new Error(`native CLI '${entry.bin}' session ${field} must be positive when declared`);
		}
	}
	if (value.stallSeconds !== undefined && value.stallSeconds > NATIVE_AGENT_TIMEOUT_SECONDS) {
		throw new Error(
			`native CLI '${entry.bin}' session stallSeconds must not exceed ${NATIVE_AGENT_TIMEOUT_SECONDS}`,
		);
	}
	if (!Array.isArray(value.backoffSeconds) || value.backoffSeconds.length === 0 ||
		value.backoffSeconds.some(seconds => typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0)) {
		throw new Error(`native CLI '${entry.bin}' session backoffSeconds must be a non-empty non-negative array`);
	}
	return {
		...value,
		createArgs: value.createArgs ? [...value.createArgs] : undefined,
		start,
		resume,
		backoffSeconds: [...value.backoffSeconds],
	};
}

function validateArgvProfile(value: unknown, field: string, entry: ProviderEntry): string[] | undefined {
	if (value === undefined) return undefined;
	if (!Array.isArray(value) || !value.every(token => typeof token === "string")) {
		throw new Error(`native CLI '${entry.bin}' has a malformed ${field} argv profile`);
	}
	return value;
}

function normalizeProviderRetryProfile(entry: ProviderEntry): Pick<ProviderEntry, "timeoutSeconds" | "backoffSeconds"> {
	if (entry.timeoutSeconds !== undefined &&
		(typeof entry.timeoutSeconds !== "number" || !Number.isFinite(entry.timeoutSeconds) || entry.timeoutSeconds <= 0)) {
		throw new Error(`native CLI '${entry.bin}' timeoutSeconds must be positive when declared`);
	}
	if (entry.timeoutSeconds !== undefined && entry.timeoutSeconds > NATIVE_AGENT_TIMEOUT_SECONDS) {
		throw new Error(
			`native CLI '${entry.bin}' timeoutSeconds must not exceed ${NATIVE_AGENT_TIMEOUT_SECONDS}`,
		);
	}
	if (entry.backoffSeconds !== undefined &&
		(!Array.isArray(entry.backoffSeconds) || entry.backoffSeconds.length === 0 ||
			entry.backoffSeconds.some(seconds => typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0))) {
		throw new Error(`native CLI '${entry.bin}' backoffSeconds must be a non-empty non-negative array when declared`);
	}
	return {
		...(entry.timeoutSeconds === undefined ? {} : { timeoutSeconds: entry.timeoutSeconds }),
		...(entry.backoffSeconds === undefined ? {} : { backoffSeconds: [...entry.backoffSeconds] }),
	};
}

/**
 * Resolve the canonical read-only argv profile without mutating the parsed provider map.
 * If both spellings are present they must contain the exact same ordered string bytes. This keeps
 * old target repositories readable while making contradictory authority profiles fail closed.
 */
export function normalizeReadOnlyArgs(entry: ProviderEntry): string[] | undefined {
	const canonical = validateArgvProfile(entry.readArgs, "readArgs", entry);
	const legacy = validateArgvProfile(entry.readOnlyArgs, "readOnlyArgs", entry);
	if (canonical && legacy) {
		const equivalent = Buffer.from(JSON.stringify(canonical), "utf8")
			.equals(Buffer.from(JSON.stringify(legacy), "utf8"));
		if (!equivalent) {
			throw new Error(
				`native CLI '${entry.bin}' declares conflicting readArgs and readOnlyArgs profiles`,
			);
		}
	}
	return canonical ?? legacy;
}

/**
 * Preflight and normalize a provider map for any native workflow controller. The returned map has
 * canonical `readArgs` on every provider that declared either accepted spelling. Write and
 * scheduler argv profiles are retained unchanged.
 */
export function normalizeNativeMap(map: NativeMap): NativeMap {
	if (
		!map
		|| typeof map !== "object"
		|| !map.providers
		|| typeof map.providers !== "object"
		|| Array.isArray(map.providers)
	) {
		throw new Error("native provider map must declare a providers object");
	}
	const providers: Record<string, ProviderEntry> = {};
	for (const [provider, entry] of Object.entries(map.providers)) {
		if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
			throw new Error(`native provider '${provider}' must be an object`);
		}
		const readArgs = normalizeReadOnlyArgs(entry);
		const retryProfile = normalizeProviderRetryProfile(entry);
		const session = normalizeSessionProfile(entry);
		providers[provider] = {
			...entry,
			...retryProfile,
			...(readArgs === undefined ? {} : { readArgs: [...readArgs] }),
			...(session === undefined ? {} : { session }),
		};
	}
	return { ...map, providers };
}

export interface WorkflowSkillContract {
	required: string[];
	onDemand: string[];
}

const WORKFLOW_SKILL_MARKER = /<!-- workflow-skills\/v1 ([^\r\n]+) -->/g;
const WORKFLOW_SKILL_NAME = /^[a-z][a-z0-9-]*$/;

function parseSkillNames(value: unknown, field: string): string[] {
	if (!Array.isArray(value) || !value.every(item => typeof item === "string" && WORKFLOW_SKILL_NAME.test(item))) {
		throw new Error(`workflow skill contract ${field} must be a hyphen-case string array`);
	}
	if (new Set(value).size !== value.length) {
		throw new Error(`workflow skill contract ${field} contains duplicates`);
	}
	return value;
}

export function parseWorkflowSkillContract(prompt: string): WorkflowSkillContract | undefined {
	const matches = [...prompt.matchAll(WORKFLOW_SKILL_MARKER)];
	if (matches.length === 0) return undefined;
	if (matches.length !== 1) throw new Error("workflow prompt must contain at most one workflow-skills/v1 marker");
	let payload: unknown;
	try {
		payload = JSON.parse(matches[0][1]);
	} catch (error) {
		throw new Error(`workflow skill contract is malformed JSON (${(error as Error).message})`);
	}
	if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
		throw new Error("workflow skill contract must be a JSON object");
	}
	const record = payload as Record<string, unknown>;
	const unknown = Object.keys(record).filter(key => !["required", "onDemand"].includes(key));
	if (unknown.length) throw new Error(`workflow skill contract has unsupported keys: ${unknown.sort().join(", ")}`);
	const required = parseSkillNames(record.required ?? [], "required");
	const onDemand = parseSkillNames(record.onDemand ?? [], "onDemand");
	if (required.length === 0 && onDemand.length === 0) {
		throw new Error("workflow skill contract must declare at least one skill");
	}
	const overlap = required.filter(name => onDemand.includes(name));
	if (overlap.length) {
		throw new Error(`workflow skill contract cannot require and defer the same skills: ${overlap.join(", ")}`);
	}
	return { required, onDemand };
}

export function materializeWorkflowSkills(prompt: string, cwd: string): string {
	const contract = parseWorkflowSkillContract(prompt);
	if (!contract) return prompt;
	const skillRoot = path.join(cwd, ".claude", "skills");
	let realSkillRoot: string;
	try {
		realSkillRoot = fs.realpathSync(skillRoot);
	} catch (error) {
		throw new Error(`workflow skill root is unavailable at ${skillRoot} (${(error as Error).message})`);
	}
	const sources = new Map<string, string>();
	for (const name of [...contract.required, ...contract.onDemand]) {
		const source = path.join(skillRoot, name, "SKILL.md");
		if (!fs.existsSync(source) || !fs.statSync(source).isFile()) {
			throw new Error(`workflow skill '${name}' is unavailable at ${source}`);
		}
		const realSource = fs.realpathSync(source);
		if (realSource !== realSkillRoot && !realSource.startsWith(realSkillRoot + path.sep)) {
			throw new Error(`workflow skill '${name}' resolves outside ${skillRoot}`);
		}
		sources.set(name, source);
	}
	const sections = ["=== WORKFLOW SKILL CONTRACT (materialized by the shared native runner) ==="];
	for (const name of contract.required) {
		const source = sources.get(name)!;
		sections.push(
			`REQUIRED SKILL \`${name}\` — loaded in full from ${source}`,
			fs.readFileSync(source, "utf8").trim(),
			`END REQUIRED SKILL \`${name}\``,
		);
	}
	if (contract.onDemand.length) {
		sections.push(
			"ON-DEMAND SKILLS — not loaded yet; when a trigger applies, read the named SKILL.md completely before acting:",
			...contract.onDemand.map(name => `- \`${name}\`: ${sources.get(name)}`),
			"Do not load on-demand skills speculatively.",
		);
	}
	sections.push("=== END WORKFLOW SKILL CONTRACT ===");
	return prompt.replace(WORKFLOW_SKILL_MARKER, sections.join("\n\n"));
}

interface NativeProtectionGate {
	source: string;
	bytes: Buffer;
}

interface RunnerProtectionGate extends NativeProtectionGate {
	projectRoot: string;
}

function readRegularFileNoFollow(file: string, label: string): Buffer {
	const noFollow = typeof fs.constants.O_NOFOLLOW === "number" ? fs.constants.O_NOFOLLOW : 0;
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(file, fs.constants.O_RDONLY | noFollow);
		if (!fs.fstatSync(descriptor).isFile()) throw new Error(`${label} is not a regular file: ${file}`);
		return fs.readFileSync(descriptor);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ELOOP") throw new Error(`${label} is a symlink: ${file}`);
		throw error;
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
}

const RECEIPT_SHA256 = /^sha256:[0-9a-f]{64}$/u;
const BARE_SHA256 = /^[0-9a-f]{64}$/u;

function digestBytes(bytes: Buffer | string): string {
	return createHash("sha256").update(bytes).digest("hex");
}

export function nativeInvocationPrefixSha256(prefix: string[]): string {
	return digestBytes(JSON.stringify(prefix));
}

const CURSOR_RUNTIME = "node";
const CURSOR_RUNTIME_STATE = ".running";
const CURSOR_ENTRY = "cursor-agent";
const STABLE_BIGINT_STAT_FIELDS = ["dev", "ino", "mode", "nlink", "size", "mtimeNs", "ctimeNs"] as const;

function lstatBigInt(file: string, label: string): fs.BigIntStats {
	try {
		return fs.lstatSync(file, { bigint: true });
	} catch (error) {
		throw new Error(`${label} cannot be inspected: ${file} (${(error as Error).message})`);
	}
}

function stableStatIdentityMatches(left: fs.BigIntStats, right: fs.BigIntStats): boolean {
	return STABLE_BIGINT_STAT_FIELDS.every(field => left[field] === right[field]);
}

function assertTrustedCursorOwner(file: string, stat: fs.BigIntStats, label: string): void {
	const uid = process.getuid?.();
	if (uid !== undefined && stat.uid !== 0n && stat.uid !== BigInt(uid)) {
		throw new Error(`authenticated Cursor ${label} is not owned by the current user or root: ${file}`);
	}
}

function assertTrustedCursorDirectory(file: string, stat: fs.BigIntStats): void {
	if (stat.isSymbolicLink()) throw new Error(`authenticated Cursor install contains a symlink: ${file}`);
	if (!stat.isDirectory()) throw new Error(`authenticated Cursor install path is not a directory: ${file}`);
	assertTrustedCursorOwner(file, stat, "install directory");
	if (process.platform !== "win32") {
		const worldWritable = (stat.mode & 0o002n) !== 0n;
		const sticky = (stat.mode & 0o1000n) !== 0n;
		if (worldWritable && !sticky) {
			throw new Error(`authenticated Cursor install directory is world-writable without sticky bit: ${file}`);
		}
		// Node exposes gid but no portable NSS membership proof equivalent to live_catalog.py.
		if ((stat.mode & 0o020n) !== 0n) {
			throw new Error(`authenticated Cursor install directory is group-writable: ${file}`);
		}
	}
}

function trustedCursorDirectoryChain(directory: string): fs.BigIntStats {
	if (!path.isAbsolute(directory) || path.resolve(directory) !== directory) {
		throw new Error(`authenticated Cursor install directory is not canonical: ${directory}`);
	}
	const parsed = path.parse(directory);
	let current = parsed.root;
	let currentStat = lstatBigInt(current, "authenticated Cursor install directory");
	assertTrustedCursorDirectory(current, currentStat);
	const relative = path.relative(parsed.root, directory);
	for (const component of relative.split(path.sep).filter(Boolean)) {
		current = path.join(current, component);
		currentStat = lstatBigInt(current, "authenticated Cursor install directory");
		assertTrustedCursorDirectory(current, currentStat);
	}
	return currentStat;
}

function assertTrustedCursorMember(
	file: string,
	stat: fs.BigIntStats,
	requireExecutable: boolean,
): void {
	if (stat.isSymbolicLink()) throw new Error(`authenticated Cursor install contains a symlink: ${file}`);
	if (!stat.isFile()) throw new Error(`authenticated Cursor install contains a special file: ${file}`);
	assertTrustedCursorOwner(file, stat, "install member");
	if (stat.nlink !== 1n) {
		throw new Error(`authenticated Cursor install member has multiple hard links: ${file}`);
	}
	if (process.platform !== "win32") {
		if ((stat.mode & 0o002n) !== 0n) {
			throw new Error(`authenticated Cursor install member is world-writable: ${file}`);
		}
		if ((stat.mode & 0o020n) !== 0n) {
			throw new Error(`authenticated Cursor install member is group-writable: ${file}`);
		}
		if (requireExecutable && (stat.mode & 0o100n) === 0n) {
			throw new Error(`authenticated Cursor install runtime is not user-executable: ${file}`);
		}
	}
}

function readStableCursorInstallMember(
	file: string,
	before: fs.BigIntStats,
	requireExecutable: boolean,
): Buffer {
	assertTrustedCursorMember(file, before, requireExecutable);
	if (typeof fs.constants.O_NOFOLLOW !== "number") {
		throw new Error("authenticated Cursor install verification requires O_NOFOLLOW");
	}
	const closeOnExec = typeof fs.constants.O_CLOEXEC === "number" ? fs.constants.O_CLOEXEC : 0;
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | closeOnExec);
		const opened = fs.fstatSync(descriptor, { bigint: true });
		if (!stableStatIdentityMatches(before, opened)) {
			throw new Error(`authenticated Cursor install member changed while it was being opened: ${file}`);
		}
		assertTrustedCursorMember(file, opened, requireExecutable);
		const bytes = fs.readFileSync(descriptor);
		const after = fs.fstatSync(descriptor, { bigint: true });
		const final = lstatBigInt(file, "authenticated Cursor install member");
		if (!stableStatIdentityMatches(opened, after) || !stableStatIdentityMatches(after, final) ||
			BigInt(bytes.byteLength) !== after.size) {
			throw new Error(`authenticated Cursor install member changed while it was being read: ${file}`);
		}
		return bytes;
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ELOOP") {
			throw new Error(`authenticated Cursor install contains a symlink: ${file}`);
		}
		throw error;
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
}

function cursorInstallManifestLines(executable: string): string[] {
	const installRoot = path.dirname(executable);
	const lines: string[] = [];
	const visit = (
		directory: string,
		relativeDirectory: string,
		expectedDirectory: fs.BigIntStats,
	): void => {
		const directoryBefore = lstatBigInt(directory, "authenticated Cursor install directory");
		assertTrustedCursorDirectory(directory, directoryBefore);
		if (!stableStatIdentityMatches(expectedDirectory, directoryBefore)) {
			throw new Error(`authenticated Cursor install directory changed before traversal: ${directory}`);
		}
		const entries = fs.readdirSync(directory, { withFileTypes: true })
			.filter(entry => !(relativeDirectory === "" && entry.name === CURSOR_RUNTIME_STATE))
			.sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0);
		for (const entry of entries) {
			const absolute = path.join(directory, entry.name);
			const relative = relativeDirectory ? `${relativeDirectory}/${entry.name}` : entry.name;
			const stat = lstatBigInt(absolute, "authenticated Cursor install path");
			if (stat.isSymbolicLink()) throw new Error(`authenticated Cursor install contains a symlink: ${absolute}`);
			if (stat.isDirectory()) {
				assertTrustedCursorDirectory(absolute, stat);
				visit(absolute, relative, stat);
				continue;
			}
			if (!stat.isFile()) {
				throw new Error(`authenticated Cursor install contains a special file: ${absolute}`);
			}
			const bytes = readStableCursorInstallMember(absolute, stat, relative === CURSOR_RUNTIME);
			lines.push(`${relative}\tsha256:${digestBytes(bytes)}`);
		}
		const directoryAfter = lstatBigInt(directory, "authenticated Cursor install directory");
		if (!stableStatIdentityMatches(directoryBefore, directoryAfter)) {
			throw new Error(`authenticated Cursor install directory changed while it was traversed: ${directory}`);
		}
	};
	visit(installRoot, "", trustedCursorDirectoryChain(installRoot));
	return lines.sort();
}

export function verifyNativeExecutionClosure(binding: Pick<
	NativeLaunchAuthorization,
	"executable" | "executableSha256" | "invocationPrefix" | "executionClosure" | "executionClosureSha256"
>): void {
	if (!path.isAbsolute(binding.executable) || path.resolve(binding.executable) !== binding.executable ||
		!RECEIPT_SHA256.test(binding.executableSha256) || !RECEIPT_SHA256.test(binding.executionClosureSha256)) {
		throw new Error("authenticated native executable binding is malformed");
	}
	const cursorRuntimePrefix = Array.isArray(binding.invocationPrefix) &&
		binding.invocationPrefix.length === 3 &&
		path.isAbsolute(binding.invocationPrefix[0]) &&
		binding.invocationPrefix[1] === "--use-system-ca" &&
		path.isAbsolute(binding.invocationPrefix[2]);
	const directOrScriptPrefix = Array.isArray(binding.invocationPrefix) &&
		[1, 2].includes(binding.invocationPrefix.length) &&
		binding.invocationPrefix.every(item => path.isAbsolute(item) && path.resolve(item) === item);
	if ((!directOrScriptPrefix && !cursorRuntimePrefix) ||
		binding.invocationPrefix.some(item => typeof item !== "string" || !item)) {
		throw new Error("authenticated native invocation prefix is malformed");
	}
	if (!Array.isArray(binding.executionClosure) || binding.executionClosure.length === 0) {
		throw new Error("authenticated native execution closure is empty");
	}
	const identities = new Set<string>();
	let entrypointCount = 0;
	let interpreterCount = 0;
	let runtimePath: string | undefined;
	let bundlePath: string | undefined;
	const closureLines: string[] = [];
	for (const [index, entry] of binding.executionClosure.entries()) {
		if (!entry || typeof entry !== "object" || Array.isArray(entry) ||
			!(["interpreter", "entrypoint", "runtime", "bundle"] as string[]).includes(entry.role) ||
			!path.isAbsolute(entry.path) || path.resolve(entry.path) !== entry.path ||
			!RECEIPT_SHA256.test(entry.sha256)) {
			throw new Error(`authenticated native execution closure entry ${index + 1} is malformed`);
		}
		const identity = `${entry.role}\u0000${entry.path}`;
		if (identities.has(identity)) throw new Error("authenticated native execution closure contains a duplicate entry");
		identities.add(identity);
		closureLines.push(`${entry.role}\t${entry.path}\t${entry.sha256}`);
		if (entry.role === "entrypoint") {
			entrypointCount += 1;
			if (entry.path !== binding.executable || entry.sha256 !== binding.executableSha256) {
				throw new Error("authenticated native entrypoint disagrees with its executable binding");
			}
		}
		if (entry.role === "interpreter") {
			interpreterCount += 1;
		}
		if (entry.role === "runtime") {
			if (runtimePath !== undefined) throw new Error("authenticated native execution closure has multiple runtimes");
			runtimePath = entry.path;
		}
		if (entry.role === "bundle") {
			if (bundlePath !== undefined) throw new Error("authenticated native execution closure has multiple bundles");
			bundlePath = entry.path;
		}
	}
	const directOrScript = binding.invocationPrefix.at(-1) === binding.executable;
	const prefixBound = directOrScript
		? interpreterCount === binding.invocationPrefix.length - 1 &&
			(interpreterCount === 0 || binding.invocationPrefix[0] ===
				binding.executionClosure.find(entry => entry.role === "interpreter")?.path)
		: cursorRuntimePrefix && interpreterCount === 0 && runtimePath === binding.invocationPrefix[0] &&
			bundlePath === binding.invocationPrefix[2];
	if (entrypointCount !== 1 || !prefixBound) {
		throw new Error("authenticated native execution closure does not bind one exact entrypoint prefix");
	}
	const closurePayload = cursorRuntimePrefix
		? `${closureLines.join("\n")}\n--install-manifest--\n${cursorInstallManifestLines(binding.executable).join("\n")}`
		: closureLines.join("\n");
	const closureDigest = `sha256:${digestBytes(closurePayload)}`;
	if (closureDigest !== binding.executionClosureSha256) {
		throw new Error("authenticated native execution closure digest is invalid");
	}
	for (const entry of binding.executionClosure) {
		let real: string;
		try {
			real = fs.realpathSync(entry.path);
		} catch (error) {
			throw new Error(`authenticated native closure path is unavailable: ${entry.path} (${(error as Error).message})`);
		}
		if (real !== entry.path) throw new Error(`authenticated native closure path drifted: ${entry.path} -> ${real}`);
		let bytes: Buffer;
		try {
			bytes = readRegularFileNoFollow(entry.path, `authenticated native ${entry.role}`);
		} catch (error) {
			throw new Error(`authenticated native closure entry is unreadable: ${entry.path} (${(error as Error).message})`);
		}
		if (`sha256:${digestBytes(bytes)}` !== entry.sha256) {
			throw new Error(`authenticated native closure bytes changed: ${entry.path}`);
		}
		if ((entry.role === "entrypoint" || entry.role === "interpreter" || entry.role === "runtime") &&
			(fs.statSync(entry.path).mode & 0o111) === 0) {
			throw new Error(`authenticated native ${entry.role} is no longer executable: ${entry.path}`);
		}
	}
}

export function verifyNativeLaunchAuthorization(authorization: NativeLaunchAuthorization): void {
	if (!authorization || typeof authorization !== "object" || Array.isArray(authorization) ||
		![authorization.backend, authorization.provider, authorization.logicalExecutable, authorization.catalogModelId]
			.every(value => typeof value === "string" && value.length > 0) ||
		authorization.logicalExecutable.includes("/") || authorization.logicalExecutable.includes("\\") ||
		!BARE_SHA256.test(authorization.launchEnvironmentSha256) ||
		!BARE_SHA256.test(authorization.launchReceiptSha256)) {
		throw new Error("authenticated native launch authorization is malformed");
	}
	if (!authorization.launchEnvironment || typeof authorization.launchEnvironment !== "object" ||
		Array.isArray(authorization.launchEnvironment) ||
		nativeChildEnvironmentSha256(authorization.launchEnvironment) !== authorization.launchEnvironmentSha256 ||
		nativeChildEnvironmentSha256(scrubEnv(authorization.launchEnvironment)) !== authorization.launchEnvironmentSha256) {
		throw new Error("authenticated native launch environment is malformed or unsanitized");
	}
	if (authorization.backend !== "cursor" && authorization.invocationPrefix.at(-1) !== authorization.executable) {
		throw new Error("only the Cursor catalog may authorize a runtime-plus-bundle invocation prefix");
	}
	verifyNativeExecutionClosure(authorization);
}

function restoreFrozenRegularFile(file: string, bytes: Buffer, mode: number): void {
	try {
		try {
			const stat = fs.lstatSync(file);
			if (stat.isDirectory() && !stat.isSymbolicLink()) {
				throw new Error("refusing to replace a directory");
			}
			fs.unlinkSync(file);
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
		}
		fs.writeFileSync(file, bytes, { flag: "wx", mode });
	} catch (error) {
		throw new Error(`could not restore frozen native audit state ${file} (${(error as Error).message})`);
	}
}

interface FrozenDirectoryIdentity {
	path: string;
	device: number;
	inode: number;
}

function freezeDirectoryIdentity(directory: string): FrozenDirectoryIdentity {
	const stat = fs.lstatSync(directory);
	if (stat.isSymbolicLink() || !stat.isDirectory()) {
		throw new Error(`native audit temp path is not a real directory: ${directory}`);
	}
	// eslint-disable-next-line no-bitwise
	if (stat.uid !== process.getuid?.() || (stat.mode & 0o077) !== 0) {
		throw new Error(`native audit temp path is not account-private: ${directory}`);
	}
	return { path: directory, device: stat.dev, inode: stat.ino };
}

function directoryIdentityMatches(identity: FrozenDirectoryIdentity): boolean {
	try {
		const stat = fs.lstatSync(identity.path);
		return !stat.isSymbolicLink()
			&& stat.isDirectory()
			&& stat.dev === identity.device
			&& stat.ino === identity.inode;
	} catch {
		return false;
	}
}

function cleanFrozenDirectory(identity: FrozenDirectoryIdentity): string | undefined {
	let stat: fs.Stats;
	try {
		stat = fs.lstatSync(identity.path);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
		return `gate temp cleanup inspection failed (${(error as Error).message})`;
	}
	try {
		if (stat.isSymbolicLink() || !stat.isDirectory()) {
			fs.unlinkSync(identity.path);
			return "gate temp directory identity changed and the replacement was unlinked";
		}
		if (stat.dev !== identity.device || stat.ino !== identity.inode) {
			return "gate temp directory identity changed; refusing recursive cleanup of an unowned replacement";
		}
		fs.rmSync(identity.path, { recursive: true, force: true });
		return undefined;
	} catch (error) {
		return `gate temp cleanup failed (${(error as Error).message})`;
	}
}

interface GateCandidate {
	label: "project-installed" | "skill-owned";
	root: string;
	relative: string[];
}

function readGateCandidate(candidate: GateCandidate): NativeProtectionGate | undefined {
	let root: string;
	try {
		root = fs.realpathSync(candidate.root);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
		throw new Error(`${candidate.label} protected-path gate root is unavailable (${(error as Error).message})`);
	}
	if (!fs.statSync(root).isDirectory()) {
		throw new Error(`${candidate.label} protected-path gate root is not a directory: ${root}`);
	}

	let current = root;
	for (const [index, segment] of candidate.relative.entries()) {
		current = path.join(current, segment);
		let stat: fs.Stats;
		try {
			stat = fs.lstatSync(current);
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
			throw new Error(`${candidate.label} protected-path gate is unreadable (${(error as Error).message})`);
		}
		if (stat.isSymbolicLink()) {
			throw new Error(`${candidate.label} protected-path gate has a symlink component: ${current}`);
		}
		const final = index === candidate.relative.length - 1;
		if ((!final && !stat.isDirectory()) || (final && !stat.isFile())) {
			throw new Error(
				`${candidate.label} protected-path gate has an unsafe ${final ? "non-file" : "non-directory"} component: ${current}`,
			);
		}
	}

	const bytes = readRegularFileNoFollow(current, `${candidate.label} protected-path gate`);
	if (!bytes.includes(Buffer.from("node-pre")) || !bytes.includes(Buffer.from("node-post"))) {
		throw new Error(`${candidate.label} protected-path gate lacks the native node audit contract: ${current}`);
	}
	return { source: current, bytes };
}

/**
 * Load one trusted native-node protection gate without following project or skill-asset symlinks.
 * Initialized projects own `.claude/hooks/protect_paths.py`; the distributable bundle itself uses
 * the source asset owned by `update-platform-config`. An unsafe installed candidate is never
 * hidden by falling back to the asset, while a genuinely absent installed hook may use that asset.
 */
export function resolveNativeProtectionGate(
	cwd: string,
	moduleUrl = import.meta.url,
): NativeProtectionGate {
	const projectRoot = path.resolve(cwd);
	const project = readGateCandidate({
		label: "project-installed",
		root: projectRoot,
		relative: [".claude", "hooks", "protect_paths.py"],
	});
	if (project) return project;

	const moduleDirectory = path.dirname(fileURLToPath(moduleUrl));
	const skillsRoot = path.resolve(moduleDirectory, "..", "..", "..");
	const skillOwned = readGateCandidate({
		label: "skill-owned",
		root: skillsRoot,
		relative: [
			"update-platform-config",
			"assets",
			"project",
			".claude",
			"hooks",
			"protect_paths.py",
		],
	});
	if (skillOwned) return skillOwned;
	throw new Error(
		`native protected-path gate is missing from ${path.join(projectRoot, ".claude", "hooks", "protect_paths.py")} `
		+ `and ${path.join(skillsRoot, "update-platform-config", "assets", "project", ".claude", "hooks", "protect_paths.py")}`,
	);
}

export function classifyProvider(map: NativeMap, provider: string | undefined, bareModel: string): ProviderEntry | undefined {
	const prefixes = Object.entries(map.modelPrefixes ?? {}).sort((a, b) => b[0].length - a[0].length);
	let familyEntry: ProviderEntry | undefined;
	for (const [prefix, key] of prefixes) {
		if (bareModel.startsWith(prefix) && map.providers[key]) {
			familyEntry = map.providers[key];
			break;
		}
	}
	const providerEntry = provider ? map.providers[provider] : undefined;
	// A recognized provider label and model-family prefix must resolve to the same native harness.
	// Fail closed on contradictory specs such as `anthropic/gpt-*`; otherwise the linter's provider
	// independence decision and the runtime's actual harness could disagree.
	if (providerEntry?.relayModelPrefixes && !providerEntry.relayModelPrefixes.some(prefix => bareModel.startsWith(prefix))) {
		return undefined;
	}
	const auditedRelay = providerEntry?.relayModelPrefixes?.some(prefix => bareModel.startsWith(prefix)) === true;
	if (providerEntry && familyEntry && providerEntry.bin !== familyEntry.bin && !auditedRelay) return undefined;
	return providerEntry ?? familyEntry;
}

function canonicalNativeHarness(bin: string): string {
	const candidates = path.isAbsolute(bin)
		? [bin]
		: [path.join(osAccountHome(), ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"]
			.map(directory => path.join(directory, bin));
	for (const candidate of candidates) {
		try {
			fs.accessSync(candidate, fs.constants.X_OK);
			return fs.realpathSync(candidate);
		} catch {
			// Native route validation reports an unavailable executable separately. Identity still
			// needs a stable fail-closed token when validating a synthetic or unavailable map.
		}
	}
	return path.isAbsolute(bin) ? path.normalize(bin) : `command:${bin}`;
}

function upstreamFamilyForModel(map: NativeMap, model: string): string | undefined {
	const bare = model.split("/").slice(-1)[0].split(":", 1)[0];
	for (const [prefix, family] of Object.entries(map.modelPrefixes ?? {}).sort((a, b) => b[0].length - a[0].length)) {
		if (bare.startsWith(prefix) && map.providers[family]) return family;
	}
	return undefined;
}

/**
 * Bind both identities involved in an ostensibly independent native call.
 *
 * `executionHarness` is the canonical executable actually launched. `upstreamProviderFamily`
 * is derived only after the selected route's modelMap is applied. A relay can therefore expose
 * GPT and Claude models without being mistaken for two independent native providers, while two
 * different wrappers around the same upstream family are not mistaken for independent upstreams.
 */
export function resolveNativeProviderIdentity(
	map: NativeMap,
	provider: string | undefined,
	bareModel: string,
): NativeProviderIdentity | undefined {
	const route = classifyProvider(map, provider, bareModel);
	if (!route) return undefined;
	const mappedModel = route.modelMap?.[bareModel] ?? bareModel;
	const upstreamProviderFamily = upstreamFamilyForModel(map, mappedModel)
		?? (route.relayModelPrefixes ? undefined : provider ?? route.nativeProviderFamily);
	if (!upstreamProviderFamily) return undefined;
	const executionHarness = canonicalNativeHarness(route.bin);
	let executionProviderFamily = route.nativeProviderFamily;
	if (!executionProviderFamily) {
		const matching = Object.entries(map.providers).find(([, entry]) =>
			canonicalNativeHarness(entry.bin) === executionHarness,
		);
		executionProviderFamily = matching?.[0] ?? executionHarness;
	}
	return { executionProviderFamily, executionHarness, upstreamProviderFamily, mappedModel };
}

const NATIVE_CONTRACT_SUFFIX = `

=== NATIVE-RUNTIME OUTPUT CONTRACT (this OVERRIDES any "yield tool" instruction above) ===
You are running in a native CLI harness: there is NO \`yield\` tool. To complete this workflow
node, END your reply with EXACTLY ONE JSON object on the FINAL LINE (nothing after it):
- implement/agent node: a WorkflowActivationOutput object, e.g.
  {"summary": "<one line>", "statePatch": [{"op": "set", "path": "/<declared-write>", "value": <value>}]}
  covering every declared write of this node. Every patch value MUST match the declared state
  schema; when the node asks for a string, emit a JSON string rather than an object or array.
- review node: {"verdict": "<EXACTLY one of the declared gates>", "summary": "<reasons>"}.`;

function isWorkflowActivationObject(value: unknown): value is Record<string, unknown> {
	if (!value || typeof value !== "object" || Array.isArray(value)) return false;
	const object = value as Record<string, unknown>;
	if (typeof object.summary !== "string") return false;
	return Array.isArray(object.statePatch) || (typeof object.verdict === "string" && object.verdict.trim().length > 0);
}

/**
 * Native CLIs occasionally wrap the requested activation JSON in prose or a Markdown fence.
 * Workflow controllers consume stdout as a machine contract, so normalize the last schema-shaped
 * JSON object at this shared boundary. This extracts model-provided state/verdict data; it never
 * invents either.
 */
export function normalizeNativeActivationStdout(stdout: string): string {
	let start = -1;
	let depth = 0;
	let inString = false;
	let escaped = false;
	let lastActivation: Record<string, unknown> | undefined;

	for (let index = 0; index < stdout.length; index += 1) {
		const char = stdout[index];
		if (start < 0) {
			if (char === "{") {
				start = index;
				depth = 1;
				inString = false;
				escaped = false;
			}
			continue;
		}

		if (inString) {
			if (escaped) escaped = false;
			else if (char === "\\") escaped = true;
			else if (char === '"') inString = false;
			continue;
		}
		if (char === '"') {
			inString = true;
			continue;
		}
		if (char === "{") depth += 1;
		else if (char === "}") {
			depth -= 1;
			if (depth === 0) {
				try {
					const parsed: unknown = JSON.parse(stdout.slice(start, index + 1));
					if (isWorkflowActivationObject(parsed)) lastActivation = parsed;
				} catch {
					// A balanced prose fragment is not a workflow activation object; keep scanning.
				}
				start = -1;
			}
		}
	}

	return lastActivation ? `${JSON.stringify(lastActivation)}\n` : stdout;
}

export function normalizeNativeFailureStderr(stderr: string, signalAborted: boolean): string {
	if (signalAborted) return stderr;
	const cursorTlsDisconnect = /client network socket disconnected before secure tls connection was established/iu;
	if (!cursorTlsDisconnect.test(stderr)) return stderr;
	const detail = stderr.replace(/^\s*\[?aborted\]?\s*:?[ \t]*/iu, "").trim();
	return `network error: ${detail}`;
}

function argAfter(args: string[], flag: string, from: number): string | undefined {
	const i = args.indexOf(flag, from);
	return i >= 0 && i + 1 < args.length ? args[i + 1] : undefined;
}

function optionalUniqueArgBefore(
	args: string[],
	flag: string,
	from: number,
	before: number,
): { value?: string; error?: string } {
	const matches: number[] = [];
	for (let index = from; index < before; index += 1) {
		if (args[index] === flag) matches.push(index);
	}
	if (matches.length > 1) return { error: `headless agent invocation has duplicate '${flag}' flags` };
	if (matches.length === 0) return {};
	const value = args[matches[0] + 1];
	if (!value || value.startsWith("-")) {
		return { error: `headless agent invocation has no value after ${flag}` };
	}
	return { value };
}

export function renderNativeArgv(
	entry: ProviderEntry,
	model: string,
	prompt: string,
	workspaceAccess?: string,
	effort?: string,
): string[] {
	const readArgs = normalizeReadOnlyArgs(entry);
	if (workspaceAccess === "read" && !readArgs) {
		throw new Error(`native CLI '${entry.bin}' has no audited read-only argv profile`);
	}
	if (workspaceAccess === "schedule" && !entry.schedulerArgs) {
		throw new Error(`native CLI '${entry.bin}' has no audited scheduler argv profile`);
	}
	if (workspaceAccess && !["read", "write", "schedule"].includes(workspaceAccess)) {
		throw new Error(`unsupported workspace access profile '${workspaceAccess}'`);
	}

	let resolvedModel = model;
	let effortArgs: string[] = [];
	if (effort !== undefined) {
		const mapping = entry.effort;
		if (!mapping) throw new Error(`native CLI '${entry.bin}' does not support reasoning effort`);
		if (!Array.isArray(mapping.values) || !mapping.values.includes(effort)) {
			throw new Error(`native CLI '${entry.bin}' received unsupported reasoning effort '${effort}'`);
		}
		if (mapping.encoding === "catalog-model-id") {
			// The caller already had a concrete catalog id in hand; a separate effort
			// argument here means the launched parameterization would no longer be the
			// one the fresh catalog authenticated.
			throw new Error(
				`native CLI '${entry.bin}' encodes reasoning effort in the resolved catalog model id; `
				+ `a separate effort '${effort}' cannot be applied`,
			);
		}
		if (!mapping.args && !mapping.model) {
			throw new Error(`native CLI '${entry.bin}' has an invalid reasoning-effort mapping`);
		}
		if (mapping.model) {
			if (!mapping.model.includes("{model}") || !mapping.model.includes("{effort}")) {
				throw new Error(`native CLI '${entry.bin}' has an invalid reasoning-effort model template`);
			}
			if (model.includes("[") || model.includes("]")) {
				throw new Error(`native CLI '${entry.bin}' cannot safely add effort to an already parameterized model`);
			}
			resolvedModel = mapping.model.replaceAll("{model}", model).replaceAll("{effort}", effort);
		}
		if (mapping.args) {
			if (!mapping.args.some(token => token.includes("{effort}"))) {
				throw new Error(`native CLI '${entry.bin}' has an invalid reasoning-effort argv template`);
			}
			effortArgs = mapping.args.map(token => token.replaceAll("{effort}", effort));
		}
	}

	const argumentTemplate = workspaceAccess === "schedule"
		? entry.schedulerArgs!
		: workspaceAccess === "read"
			? readArgs!
			: entry.args;
	return [
		entry.bin,
		...effortArgs,
		...argumentTemplate.map(token => token.replaceAll("{model}", resolvedModel).replaceAll("{prompt}", prompt)),
	];
}

function authorizedNativeArgv(rendered: string[], authorization: NativeLaunchAuthorization): string[] {
	if (rendered.length === 0 || rendered[0] !== authorization.executable) {
		throw new Error("rendered native argv disagrees with the authenticated executable");
	}
	return [...authorization.invocationPrefix, ...rendered.slice(1)];
}

const NATIVE_CHILD_ENV_DENYLIST = new Set([
	"AGENT_CLI_CONFIG_DIR",
	"AGENT_CLI_CREDENTIAL_STORE",
	"ALL_PROXY",
	"ANTHROPIC_API_KEY",
	"ANTHROPIC_API_URL",
	"ANTHROPIC_AUTH_TOKEN",
	"ANTHROPIC_BASE_URL",
	"ANTHROPIC_CUSTOM_HEADERS",
	"ANTHROPIC_DEFAULT_HAIKU_MODEL",
	"ANTHROPIC_DEFAULT_OPUS_MODEL",
	"ANTHROPIC_DEFAULT_SONNET_MODEL",
	"ANTHROPIC_MODEL",
	"ANTHROPIC_OAUTH_TOKEN",
	"BASH_ENV",
	"BASHOPTS",
	"CDPATH",
	"CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
	"CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
	"CLAUDE_CODE_OAUTH_TOKEN",
	"CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
	"CLAUDE_CODE_USE_BEDROCK",
	"CLAUDE_CODE_USE_FOUNDRY",
	"CLAUDE_CODE_USE_GATEWAY",
	"CLAUDE_CODE_USE_VERTEX",
	"CLAUDE_CONFIG_DIR",
	"CODEX_API_BASE",
	"CODEX_HOME",
	"CURL_CA_BUNDLE",
	"CURSOR_AGENT_CONFIG_DIR",
	"CURSOR_API_ENDPOINT",
	"CURSOR_API_KEY",
	"CURSOR_AUTH_TOKEN",
	"CURSOR_CONFIG_DIR",
	"CURSOR_INVOKED_AS",
	"DENO_DIR",
	"ENV",
	"GEM_PATH",
	"GIT_ASKPASS",
	"GIT_SSL_NO_VERIFY",
	"GLOBIGNORE",
	"HTTP_PROXY",
	"HTTPS_PROXY",
	"IFS",
	"KSH_ENV",
	"LD_AUDIT",
	"LD_DEBUG",
	"LD_LIBRARY_PATH",
	"LD_PRELOAD",
	"NODE_COMPILE_CACHE",
	"NODE_EXTRA_CA_CERTS",
	"NODE_DEBUG",
	"NODE_DEBUG_NATIVE",
	"NODE_OPTIONS",
	"NODE_PATH",
	"NODE_REPL_EXTERNAL_MODULE",
	"NODE_V8_COVERAGE",
	"NO_PROXY",
	"OPENAI_API_BASE",
	"OPENAI_BASE_URL",
	"OPENSSL_CONF",
	"OPENSSL_ENGINES",
	"PERL5LIB",
	"PERL5OPT",
	"PS4",
	"PYTHONHOME",
	"PYTHONPATH",
	"PYTHONSTARTUP",
	"REQUESTS_CA_BUNDLE",
	"RUBYOPT",
	"SHELLOPTS",
	"SSL_CERT_DIR",
	"SSL_CERT_FILE",
	"SSLKEYLOGFILE",
	"SSH_ASKPASS",
	"XDG_CACHE_HOME",
	"XDG_CONFIG_HOME",
	"XDG_DATA_HOME",
	"XDG_RUNTIME_DIR",
	"ZDOTDIR",
	"all_proxy",
	"http_proxy",
	"https_proxy",
	"no_proxy",
]);

function isNativeChildInjectionEnv(name: string): boolean {
	return name.startsWith("GENERAL_AGENT_")
		|| name.startsWith("BASH_FUNC_")
		|| name.startsWith("DYLD_")
		|| name.startsWith("NODE_")
		|| name.startsWith("OPENAI_")
		|| name.startsWith("CODEX_")
		|| name.startsWith("CURSOR_")
		|| name.startsWith("PYTHON")
		|| name.startsWith("npm_")
		|| NATIVE_CHILD_ENV_DENYLIST.has(name);
}

export function osAccountHome(): string {
	const uid = process.getuid?.();
	if (!Number.isInteger(uid) || Number(uid) < 0) throw new Error("cannot determine the native process uid");
	let descriptor: number | undefined;
	let passwd: string;
	try {
		descriptor = fs.openSync("/etc/passwd", fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		const info = fs.fstatSync(descriptor);
		// eslint-disable-next-line no-bitwise
		if (!info.isFile() || info.uid !== 0 || (info.mode & 0o022) !== 0) {
			throw new Error("/etc/passwd is not a root-owned, non-writable regular file");
		}
		passwd = fs.readFileSync(descriptor, "utf8");
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
	const matches = passwd.split(/\r?\n/u).filter(Boolean).map(line => line.split(":"))
		.filter(fields => fields.length === 7 && Number(fields[2]) === uid);
	if (matches.length !== 1 || !path.isAbsolute(matches[0][5])) {
		throw new Error(`cannot resolve one absolute OS account home for uid ${uid}`);
	}
	return fs.realpathSync(matches[0][5]);
}

export function scrubEnv(env: Record<string, string | undefined>): Record<string, string> {
	const out: Record<string, string> = {};
	for (const [k, v] of Object.entries(env)) {
		if (v === undefined) continue;
		if (isNativeChildInjectionEnv(k)) continue;
		out[k] = v;
	}
	const home = osAccountHome();
	out.HOME = home;
	out.PATH = [path.join(home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"].join(path.delimiter);
	out.CODEX_HOME = path.join(home, ".codex");
	out.XDG_CONFIG_HOME = path.join(home, ".config");
	out.XDG_DATA_HOME = path.join(home, ".local", "share");
	return out;
}

function nativeChildLaunchEnvironment(
	authorization: NativeLaunchAuthorization,
	extra: Record<string, string> = {},
): Record<string, string> {
	const env = { ...authorization.launchEnvironment, ...extra };
	if (authorization.backend === "cursor") env.CURSOR_INVOKED_AS = CURSOR_ENTRY;
	return env;
}

export function nativeChildEnvironmentSha256(env: Record<string, string>): string {
	return digestBytes(JSON.stringify(Object.fromEntries(Object.entries(env).sort(([left], [right]) =>
		left.localeCompare(right)))));
}

export interface SpawnCollectOptions {
	cwd: string;
	signal?: AbortSignal;
	env?: Record<string, string>;
	/** Time allowed for the process group to exit after SIGTERM before SIGKILL. */
	terminationGraceMs?: number;
	heartbeatMs?: number;
	inactivityTimeoutMs?: number;
	onHeartbeat?: (heartbeat: {
		pid: number;
		elapsedMs: number;
		idleMs: number;
		stdoutBytes: number;
		stderrBytes: number;
	}) => void;
}

export interface SpawnCollectResult {
	stdout: string;
	stderr: string;
	exitCode: number;
	terminationReason?: "signal" | "inactivity" | "orphan-cleanup";
	cleanupFailure?: ProcessCleanupFailure;
}

export interface ProcessCleanupFailure {
	phase: "orphan-probe" | "sigterm" | "sigkill";
	code: string;
	message: string;
	cleanupVerified: false;
}

function signalProcessTree(pid: number, signal: "SIGTERM" | "SIGKILL"): void {
	if (process.platform === "win32") return;
	try {
		// A detached POSIX child is the leader of a new process group. A negative PID targets
		// every descendant that retained that group, including grandchildren after the leader exits.
		process.kill(-pid, signal);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
	}
}

function processGroupExists(pid: number): boolean {
	if (process.platform === "win32") return false;
	try {
		process.kill(-pid, 0);
		return true;
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ESRCH") return false;
		throw error;
	}
}

export async function spawnCollect(
	argv: string[],
	opts: SpawnCollectOptions,
): Promise<SpawnCollectResult> {
	const terminationGraceMs = opts.terminationGraceMs ?? 1_000;
	if (!Number.isFinite(terminationGraceMs) || terminationGraceMs < 0) {
		throw new Error("terminationGraceMs must be a non-negative finite number");
	}
	for (const [field, value] of [["heartbeatMs", opts.heartbeatMs], ["inactivityTimeoutMs", opts.inactivityTimeoutMs]] as const) {
		if (value !== undefined && (!Number.isFinite(value) || value <= 0)) {
			throw new Error(`${field} must be a positive finite number when declared`);
		}
	}
	const child = Bun.spawn(argv, {
		cwd: opts.cwd,
		stdout: "pipe",
		stderr: "pipe",
		detached: process.platform !== "win32",
		env: opts.env,
	});
	let terminationTimer: ReturnType<typeof setTimeout> | undefined;
	let heartbeatTimer: ReturnType<typeof setInterval> | undefined;
	let terminationStarted = false;
	let terminationReason: SpawnCollectResult["terminationReason"];
	let cleanupFailure: ProcessCleanupFailure | undefined;
	let escalation: Promise<void> | undefined;
	const recordCleanupFailure = (phase: ProcessCleanupFailure["phase"], error: unknown): void => {
		if (cleanupFailure) return;
		const cause = error as NodeJS.ErrnoException;
		cleanupFailure = {
			phase,
			code: typeof cause.code === "string" && cause.code ? cause.code : "UNKNOWN",
			message: cause instanceof Error ? cause.message : String(error),
			cleanupVerified: false,
		};
	};
	const terminate = (reason: NonNullable<SpawnCollectResult["terminationReason"]>) => {
		if (terminationStarted) return;
		terminationStarted = true;
		terminationReason = reason;
		try {
			if (process.platform === "win32") {
				child.kill("SIGTERM");
			} else {
				signalProcessTree(child.pid, "SIGTERM");
			}
		} catch (error) {
			recordCleanupFailure("sigterm", error);
		}
		escalation = new Promise(resolve => {
			terminationTimer = setTimeout(() => {
				try {
					if (process.platform === "win32") child.kill("SIGKILL");
					else signalProcessTree(child.pid, "SIGKILL");
				} catch (error) {
					recordCleanupFailure("sigkill", error);
				} finally {
					resolve();
				}
			}, terminationGraceMs);
		});
	};
	const abort = () => terminate("signal");
	opts.signal?.addEventListener("abort", abort, { once: true });
	if (opts.signal?.aborted) abort();
	const startedAt = performance.now();
	let lastActivityAt = startedAt;
	let lastHeartbeatAt = startedAt;
	let stdoutBytes = 0;
	let stderrBytes = 0;
	const collect = async (stream: ReadableStream<Uint8Array>, channel: "stdout" | "stderr"): Promise<string> => {
		const chunks: Uint8Array[] = [];
		const reader = stream.getReader();
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			chunks.push(value);
			if (channel === "stdout") stdoutBytes += value.byteLength;
			else stderrBytes += value.byteLength;
			lastActivityAt = performance.now();
		}
		return new TextDecoder().decode(Buffer.concat(chunks.map(chunk => Buffer.from(chunk))));
	};
	const timerPeriod = Math.min(
		opts.heartbeatMs ?? Number.POSITIVE_INFINITY,
		opts.inactivityTimeoutMs ? Math.max(10, Math.min(opts.inactivityTimeoutMs / 4, 1_000)) : Number.POSITIVE_INFINITY,
	);
	if (Number.isFinite(timerPeriod)) {
		heartbeatTimer = setInterval(() => {
			const now = performance.now();
			const heartbeat = {
				pid: child.pid,
				elapsedMs: Math.floor(now - startedAt),
				idleMs: Math.floor(now - lastActivityAt),
				stdoutBytes,
				stderrBytes,
			};
			if (opts.heartbeatMs !== undefined && now - lastHeartbeatAt >= opts.heartbeatMs) {
				lastHeartbeatAt = now;
				opts.onHeartbeat?.(heartbeat);
			}
			if (opts.inactivityTimeoutMs !== undefined && heartbeat.idleMs >= opts.inactivityTimeoutMs) {
				terminate("inactivity");
			}
		}, timerPeriod);
	}
	try {
		const [stdout, stderr, exitCode] = await Promise.all([
			collect(child.stdout, "stdout"),
			collect(child.stderr, "stderr"),
			child.exited,
		]);
		// A native CLI can exit successfully after detaching background writers. The workflow
		// contract is node-scoped, so a completed leader must not leave any member of its process
		// group running into the next node.
		if (!terminationStarted && process.platform !== "win32") {
			try {
				if (processGroupExists(child.pid)) terminate("orphan-cleanup");
			} catch (error) {
				recordCleanupFailure("orphan-probe", error);
			}
		}
		// The leader may exit on SIGTERM while an ignoring grandchild remains. Do not clear the
		// escalation timer until the process group itself is gone.
		if (terminationStarted && process.platform !== "win32") {
			try {
				if (processGroupExists(child.pid)) await escalation;
			} catch (error) {
				recordCleanupFailure("orphan-probe", error);
			}
		}
		return { stdout, stderr, exitCode, terminationReason, cleanupFailure };
	} finally {
		opts.signal?.removeEventListener("abort", abort);
		if (terminationTimer) clearTimeout(terminationTimer);
		if (heartbeatTimer) clearInterval(heartbeatTimer);
	}
}

function writeRegularFileNoFollow(file: string, content: string, truncate = false): void {
	const noFollow = fs.constants.O_NOFOLLOW ?? 0;
	if (noFollow === 0 && fs.existsSync(file) && fs.lstatSync(file).isSymbolicLink()) {
		throw new Error("refusing symbolic-link target");
	}
	const flags = fs.constants.O_WRONLY
		| fs.constants.O_CREAT
		| (truncate ? fs.constants.O_TRUNC : fs.constants.O_APPEND)
		| noFollow;
	const fd = fs.openSync(file, flags, 0o600);
	try {
		if (!fs.fstatSync(fd).isFile()) throw new Error("target is not a regular file");
		fs.writeSync(fd, content);
	} finally {
		fs.closeSync(fd);
	}
}

function routeFailure(reason: string): { stdout: string; stderr: string; exitCode: number } {
	return {
		stdout: "",
		stderr: `[native-workflow] FAILED: ${reason}. No model fallback was attempted.`,
		exitCode: 2,
	};
}

function insertSessionArgs(
	argv: string[],
	insertion: NativeSessionArgInsertion | undefined,
	sessionId: string | undefined,
): string[] {
	if (!insertion) return argv;
	const tokens = insertion.args.map(token => token.replaceAll("{sessionId}", sessionId ?? ""));
	if (tokens.some(token => token.includes("{sessionId}")) ||
		insertion.args.some(token => token.includes("{sessionId}")) && !sessionId) {
		throw new Error("native session argv requires an unavailable session id");
	}
	if (insertion.position === "start") return [argv[0], ...tokens, ...argv.slice(1)];
	const anchorIndex = argv.indexOf(insertion.anchor!, 1);
	if (anchorIndex < 0) throw new Error(`native session argv anchor '${insertion.anchor}' is unavailable`);
	return [...argv.slice(0, anchorIndex + 1), ...tokens, ...argv.slice(anchorIndex + 1)];
}

function jsonlObjects(stdout: string): Array<Record<string, unknown>> {
	const objects: Array<Record<string, unknown>> = [];
	for (const line of stdout.split(/\r?\n/u)) {
		if (!line.trim()) continue;
		try {
			const value: unknown = JSON.parse(line);
			if (value && typeof value === "object" && !Array.isArray(value)) {
				objects.push(value as Record<string, unknown>);
			}
		} catch {
			// Keep the raw transcript for evidence; only schema-shaped JSONL participates in recovery.
		}
	}
	return objects;
}

export function discoverSessionId(profile: NativeSessionProfile, stdout: string): string | undefined {
	if (profile.idSource !== "jsonl-event") return undefined;
	for (const event of jsonlObjects(stdout)) {
		if (event.type !== profile.eventType) continue;
		const id = event[profile.eventIdField!];
		if (typeof id === "string" && id.trim()) return id.trim();
	}
	return undefined;
}

export function normalizeSessionOutput(profile: NativeSessionProfile | undefined, stdout: string): string {
	if (profile?.output !== "jsonl-agent-message") return stdout;
	let message: string | undefined;
	for (const event of jsonlObjects(stdout)) {
		const item = event.item;
		if (!item || typeof item !== "object" || Array.isArray(item)) continue;
		const record = item as Record<string, unknown>;
		if (record.type === "agent_message" && typeof record.text === "string") message = record.text;
	}
	return message === undefined ? stdout : `${message}\n`;
}

export type NativeAttemptClassification =
	| "COMPLETED"
	| "PROCESS_CLEANUP_FAILED"
	| "TRANSIENT_TRANSPORT"
	| "TRANSIENT_PROCESS_EXIT"
	| "SERVICE_THROTTLED"
	| "HEARTBEAT_STALL"
	| "NODE_CANCELLED"
	| "AUTH_OR_PERMISSION"
	| "NATIVE_FAILURE";

export function classifyNativeAttempt(
	exitCode: number,
	stderr: string,
	terminationReason?: SpawnCollectResult["terminationReason"],
	cleanupFailure?: ProcessCleanupFailure,
): NativeAttemptClassification {
	if (cleanupFailure) return "PROCESS_CLEANUP_FAILED";
	if (terminationReason === "inactivity") return "HEARTBEAT_STALL";
	if (terminationReason === "signal") return "NODE_CANCELLED";
	if (exitCode === 0) return "COMPLETED";
	if (/(?:not logged in|authentication|unauthorized|forbidden|permission denied|please run \/login|invalid api key)/iu.test(stderr)) {
		return "AUTH_OR_PERMISSION";
	}
	if (/(?:quota|insufficient (?:credits|funds|balance)|credit limit|billing|payment required|out of credits)/iu.test(stderr)) {
		return "NATIVE_FAILURE";
	}
	if (/(?:invalid request|invalid argument|unsupported model|unknown model|model .* not found|policy refusal|policy violation|invalid output|malformed|structured output|schema)/iu.test(stderr)) {
		return "NATIVE_FAILURE";
	}
	if (/(?:rate.?limit|too many requests|\b429\b|service unavailable|\b5\d{2}\b|overloaded|api error|internal server error)/iu.test(stderr)) {
		return "SERVICE_THROTTLED";
	}
	if (
		/(?:network error|connection (?:reset|closed|refused)|stream disconnected|socket disconnected|secure tls|timed? ?out|temporary failure|fetch failed|econnreset|etimedout|eai_again|enetunreach|broken pipe)/iu.test(stderr)
		|| (/Could not parse message into JSON/iu.test(stderr) && /(?:content_block_delta|:\s*keepalive)/iu.test(stderr))
	) return "TRANSIENT_TRANSPORT";
	// A non-zero exit without a known permanent diagnosis is an unexpected native
	// process failure. It is safe to retry a bounded number of times while keeping
	// malformed successful output and explicit policy failures fail-closed.
	return exitCode !== 0 ? "TRANSIENT_PROCESS_EXIT" : "NATIVE_FAILURE";
}

function recoveryEligible(classification: NativeAttemptClassification): boolean {
	return [
		"TRANSIENT_TRANSPORT",
		"TRANSIENT_PROCESS_EXIT",
		"SERVICE_THROTTLED",
		"HEARTBEAT_STALL",
	].includes(classification);
}

function siblingJournal(tracePath: string | undefined, name: string, cwd: string): string | undefined {
	if (!tracePath) return undefined;
	const resolvedTrace = path.isAbsolute(tracePath) ? tracePath : path.join(cwd, tracePath);
	return path.join(path.dirname(resolvedTrace), name);
}

function appendJournal(file: string | undefined, record: Record<string, unknown>): void {
	if (!file) return;
	fs.mkdirSync(path.dirname(file), { recursive: true });
	writeRegularFileNoFollow(file, `${JSON.stringify(record)}\n`);
}

async function waitForRecovery(seconds: number, signal?: AbortSignal): Promise<boolean> {
	if (signal?.aborted) return false;
	if (seconds === 0) return true;
	return await new Promise<boolean>(resolve => {
		let timer: ReturnType<typeof setTimeout> | undefined;
		const aborted = () => {
			if (timer) clearTimeout(timer);
			signal?.removeEventListener("abort", aborted);
			resolve(false);
		};
		timer = setTimeout(() => {
			signal?.removeEventListener("abort", aborted);
			resolve(true);
		}, seconds * 1_000);
		signal?.addEventListener("abort", aborted, { once: true });
	});
}

export function makeNativeRunner(
	map: NativeMap,
	maxModelCalls = Number.POSITIVE_INFINITY,
	tracePath?: string,
	initialModelCalls = 0,
) {
	const normalizedMap = normalizeNativeMap(map);
	if (!Number.isInteger(initialModelCalls) || initialModelCalls < 0 || initialModelCalls > maxModelCalls) {
		throw new Error("native runner initial model-call count is invalid");
	}
	let modelCalls = initialModelCalls;
	let frozenProtectionGate: RunnerProtectionGate | undefined;
	const runner = async (
		args: string[],
		opts: NativeRunnerOptions,
	): Promise<{ stdout: string; stderr: string; exitCode: number }> => {
		const workspaceAccess = opts.env?.GENERAL_AGENT_WORKSPACE_ACCESS;
		const li = args.indexOf("launch");
		if (li < 0) return routeFailure("headless agent invocation has no 'launch' subcommand");

		const model = argAfter(args, "--model", li);
		if (!model) return routeFailure("workflow node resolved no model spec");

		const promptFlagIndex = args.indexOf("-p", li);
		const prompt = argAfter(args, "-p", li);
		if (!prompt) return routeFailure("headless agent invocation has no prompt after -p");
		const parsedEffort = optionalUniqueArgBefore(args, "--effort", li + 1, promptFlagIndex);
		if (parsedEffort.error) return routeFailure(parsedEffort.error);
		const provider = model?.includes("/") ? model.split("/")[0] : undefined;
		const modelTail = model ? model.split("/").slice(-1)[0] : "";
		const effortSeparator = modelTail.lastIndexOf(":");
		const suffixEffort = effortSeparator >= 0 ? modelTail.slice(effortSeparator + 1) : undefined;
		if (suffixEffort === "") return routeFailure(`model spec '${model}' has an empty reasoning-effort suffix`);
		if (parsedEffort.value && suffixEffort && parsedEffort.value !== suffixEffort) {
			return routeFailure(
				`model spec '${model}' and --effort '${parsedEffort.value}' declare conflicting reasoning effort`,
			);
		}
		const effort = parsedEffort.value ?? suffixEffort;
		const bareEarly = effortSeparator >= 0 ? modelTail.slice(0, effortSeparator) : modelTail;
		if (!bareEarly) return routeFailure(`model spec '${model}' has no native model id`);
		const entry = model ? classifyProvider(normalizedMap, provider, bareEarly) : undefined;

		if (!entry) return routeFailure(`no native CLI route for model '${model}'`);
		const authorization = opts.authorization;
		try {
			verifyNativeLaunchAuthorization(authorization);
			if (authorization.catalogModelId !== bareEarly) {
				throw new Error(
					`authenticated catalog model ${authorization.catalogModelId} disagrees with requested model ${bareEarly}`,
				);
			}
			if (provider && authorization.provider !== provider) {
				throw new Error(
					`authenticated provider ${authorization.provider} disagrees with requested provider ${provider}`,
				);
			}
			if (path.isAbsolute(entry.bin)) {
				let configured: string;
				try {
					configured = fs.realpathSync(entry.bin);
				} catch (error) {
					throw new Error(`configured native entrypoint is unavailable (${(error as Error).message})`);
				}
				if (configured !== authorization.executable) {
					throw new Error(
						`configured native entrypoint ${configured} disagrees with authenticated executable ${authorization.executable}`,
					);
				}
			} else if (entry.bin !== authorization.logicalExecutable) {
				throw new Error(
					`configured native command ${entry.bin} disagrees with authenticated command ${authorization.logicalExecutable}`,
				);
			}
		} catch (error) {
			return routeFailure(`native launch authorization failed (${(error as Error).message})`);
		}
		let env = nativeChildLaunchEnvironment(authorization);
		let materializedPrompt: string;
		try {
			materializedPrompt = materializeWorkflowSkills(prompt, opts.cwd);
		} catch (error) {
			return routeFailure(`workflow skill preflight failed (${(error as Error).message})`);
		}
		const bare = bareEarly; // provider + :thinking stripped (works for bare ids too)
		const mapped = entry.modelMap?.[bare] ?? bare;
		if (mapped !== authorization.catalogModelId) {
			return routeFailure(
				`native model map rewrites authenticated catalog id '${authorization.catalogModelId}' as '${mapped}'`,
			);
		}
		const boundEntry = { ...entry, bin: authorization.executable };
		const finalPrompt = materializedPrompt + NATIVE_CONTRACT_SUFFIX;
		const session = boundEntry.session;
		const timeoutSeconds = session?.stallSeconds ?? boundEntry.timeoutSeconds;
		const backoffSeconds = session?.backoffSeconds ?? boundEntry.backoffSeconds;
		// A session may intentionally omit an inactivity bound in a fixture or legacy map, but
		// its declared backoff still authorizes bounded recovery. Production profiles bind both.
		const retryProfileAvailable = backoffSeconds !== undefined;
		let sessionId = session?.idSource === "assigned-uuid" ? randomUUID() : undefined;
		const preflightSessionId = sessionId ?? (session?.idSource === "create-stdout" ? "session-preflight" : undefined);
		let initialArgvSha256 = "";
		try {
			const preview = authorizedNativeArgv(
				insertSessionArgs(renderNativeArgv(boundEntry, mapped, finalPrompt, workspaceAccess, effort), session?.start, preflightSessionId),
				authorization,
			);
			const firstPhysicalArgv = session?.idSource === "create-stdout"
				? [...authorization.invocationPrefix, ...session.createArgs!]
				: preview;
			initialArgvSha256 = digestBytes(JSON.stringify(firstPhysicalArgv));
			if (session) {
				authorizedNativeArgv(
					insertSessionArgs(renderNativeArgv(
						boundEntry,
						mapped,
						session.continuePrompt + NATIVE_CONTRACT_SUFFIX,
						workspaceAccess,
						effort,
					), session.resume, sessionId ?? "session-preflight"),
					authorization,
				);
			}
		} catch (error) {
			return routeFailure((error as Error).message);
		}
		if (modelCalls >= maxModelCalls) {
			return routeFailure(
				`workflow exceeded dispatch.maxModelCalls=${maxModelCalls} before native activation ${modelCalls + 1}`,
			);
		}

		// Claude Code loads the installed project hook itself. Native CLIs without that hook system
		// must be wrapped before their first activation, and a missing/unsafe gate is a hard failure.
		const usesClaudeProjectHook = boundEntry.nativeProviderFamily === "anthropic" ||
			authorization.logicalExecutable === "claude";
		const requiresRunnerGate = !usesClaudeProjectHook;
		const nodeKey = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
		let gate: string | undefined;
		let gateTempDir: string | undefined;
		let gateTempIdentity: FrozenDirectoryIdentity | undefined;
		let gateStateIdentity: FrozenDirectoryIdentity | undefined;
		let gateBytes: Buffer | undefined;
		let gateSnapshot: string | undefined;
		let gateSnapshotBytes: Buffer | undefined;
		try {
			gateTempDir = fs.mkdtempSync(path.join(os.tmpdir(), "general-agent-node-gate-"));
			fs.chmodSync(gateTempDir, 0o700);
			gateTempIdentity = freezeDirectoryIdentity(gateTempDir);
			env = nativeChildLaunchEnvironment(authorization, { XDG_RUNTIME_DIR: gateTempDir });
			if (requiresRunnerGate) {
				const projectRoot = fs.realpathSync(opts.cwd);
				if (frozenProtectionGate && frozenProtectionGate.projectRoot !== projectRoot) {
					throw new Error(
						`native runner is bound to project ${frozenProtectionGate.projectRoot}, not ${projectRoot}`,
					);
				}
				const source = frozenProtectionGate ?? resolveNativeProtectionGate(projectRoot);
				frozenProtectionGate ??= {
					projectRoot,
					source: source.source,
					bytes: Buffer.from(source.bytes),
				};
				gateBytes = source.bytes;
				gate = path.join(gateTempDir, "protect_paths.py");
				fs.writeFileSync(gate, gateBytes, { flag: "wx", mode: 0o500 });
				const before = Bun.spawnSync(
					["/usr/bin/python3", gate, "node-pre", nodeKey, opts.cwd],
					{ env },
				);
				if (before.exitCode !== 0) {
					const detail = new TextDecoder().decode(before.stderr).trim();
					throw new Error(`snapshot failed${detail ? `: ${detail}` : ""}`);
				}
				const stateDirectory = path.join(gateTempDir, "general-agent-gate");
				gateStateIdentity = freezeDirectoryIdentity(stateDirectory);
				const snapshotName = `gate-node-${nodeKey}.json`;
				const stateEntries = fs.readdirSync(stateDirectory).sort();
				if (stateEntries.length !== 1 || stateEntries[0] !== snapshotName) {
					throw new Error(`snapshot produced unexpected private audit state: ${stateEntries.join(",")}`);
				}
				gateSnapshot = path.join(stateDirectory, snapshotName);
				gateSnapshotBytes = readRegularFileNoFollow(gateSnapshot, "native protected-path audit state");
			}
		} catch (error) {
			if (gateTempIdentity) cleanFrozenDirectory(gateTempIdentity);
			return routeFailure(`native private runtime preflight failed (${(error as Error).message})`);
		}
		const auditGate = (): { result?: ReturnType<typeof Bun.spawnSync>; tampered: boolean; snapshotTampered: boolean } => {
			if (!requiresRunnerGate || !gate || !gateBytes) {
				return { tampered: false, snapshotTampered: false };
			}
			let tampered = false;
			let snapshotTampered = false;
			const originalDirectoryIntact = gateTempIdentity !== undefined
				&& directoryIdentityMatches(gateTempIdentity)
				&& gateStateIdentity !== undefined
				&& directoryIdentityMatches(gateStateIdentity);
			let auditDirectory = gateTempDir;
			let recoveryIdentity: FrozenDirectoryIdentity | undefined;
			if (!originalDirectoryIntact) {
				tampered = true;
				snapshotTampered = true;
				auditDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "general-agent-node-gate-recovery-"));
				recoveryIdentity = freezeDirectoryIdentity(auditDirectory);
				gate = path.join(auditDirectory, "protect_paths.py");
				const recoveryStateDirectory = path.join(auditDirectory, "general-agent-gate");
				fs.mkdirSync(recoveryStateDirectory, { mode: 0o700 });
				gateSnapshot = path.join(
					recoveryStateDirectory,
					`gate-node-${nodeKey}.json`,
				);
				fs.writeFileSync(gate, gateBytes, { flag: "wx", mode: 0o500 });
				if (!gateSnapshotBytes) throw new Error("native protected-path audit state was not frozen");
				fs.writeFileSync(gateSnapshot, gateSnapshotBytes, { flag: "wx", mode: 0o600 });
			}
			try {
				tampered ||= !readRegularFileNoFollow(gate, "native protected-path helper").equals(gateBytes);
			} catch {
				tampered = true;
			}
			if (tampered) {
				restoreFrozenRegularFile(gate, gateBytes, 0o500);
			}
			if (gateSnapshot && gateSnapshotBytes) {
				try {
					snapshotTampered ||= !readRegularFileNoFollow(
						gateSnapshot,
						"native protected-path audit state",
					).equals(gateSnapshotBytes);
				} catch {
					snapshotTampered = true;
				}
				if (snapshotTampered) restoreFrozenRegularFile(gateSnapshot, gateSnapshotBytes, 0o600);
			}
			try {
				return {
					result: Bun.spawnSync(
						["/usr/bin/python3", gate, "node-post", nodeKey, opts.cwd],
						{ env: nativeChildLaunchEnvironment(authorization, { XDG_RUNTIME_DIR: auditDirectory! }) },
					),
					tampered,
					snapshotTampered,
				};
			} finally {
				if (recoveryIdentity) cleanFrozenDirectory(recoveryIdentity);
			}
		};
		const closeGate = (): {
			result?: ReturnType<typeof Bun.spawnSync>;
			tampered: boolean;
			snapshotTampered: boolean;
			failure?: string;
		} => {
			let audit: ReturnType<typeof auditGate> = { tampered: false, snapshotTampered: false };
			let failure: string | undefined;
			try {
				audit = auditGate();
			} catch (error) {
				failure = `protected-path audit crashed (${(error as Error).message})`;
			}
			if (gateTempIdentity) {
				const cleanupFailure = cleanFrozenDirectory(gateTempIdentity);
				if (cleanupFailure) failure = [failure, cleanupFailure].filter(Boolean).join("; ");
				gateTempDir = undefined;
				gateTempIdentity = undefined;
				gateStateIdentity = undefined;
			}
			return { ...audit, failure };
		};
		const failBeforeActivation = (reason: string): ReturnType<typeof routeFailure> => {
			const { result: audit, tampered, snapshotTampered, failure } = closeGate();
			const auditMessage = audit ? new TextDecoder().decode(audit.stderr).trim() : "";
			const auditFailed = failure !== undefined ||
				(requiresRunnerGate && (tampered || snapshotTampered || !audit || audit.exitCode !== 0));
			return routeFailure(
				auditFailed
					? `${reason}; protected-path audit failed${failure ? ` (${failure})` : auditMessage ? ` (${auditMessage})` : ""}`
					: reason,
			);
		};
		const callNumber = modelCalls + 1;
		try {
			verifyNativeLaunchAuthorization(authorization);
		} catch (error) {
			return failBeforeActivation(`native launch authorization drifted (${(error as Error).message})`);
		}
		if (tracePath) {
			const resolvedTrace = path.isAbsolute(tracePath) ? tracePath : path.join(opts.cwd, tracePath);
			try {
				fs.mkdirSync(path.dirname(resolvedTrace), { recursive: true });
				writeRegularFileNoFollow(
					resolvedTrace,
					`${JSON.stringify({
						call: callNumber,
						model,
						effort: effort ?? null,
						catalogModelId: authorization.catalogModelId,
						executable: authorization.executable,
						processExecutable: authorization.invocationPrefix[0],
						executableSha256: authorization.executableSha256.slice("sha256:".length),
						executionClosureSha256: authorization.executionClosureSha256.slice("sha256:".length),
						invocationPrefixSha256: nativeInvocationPrefixSha256(authorization.invocationPrefix),
						argvSha256: initialArgvSha256,
						environmentSha256: nativeChildEnvironmentSha256(env),
						launchEnvironmentSha256: authorization.launchEnvironmentSha256,
						...(gateSnapshotBytes
							? { protectedPathStateSha256: digestBytes(gateSnapshotBytes) }
							: {}),
						launchReceiptSha256: authorization.launchReceiptSha256,
						sessionId: sessionId ?? null,
						resumable: session !== undefined,
						prompt: materializedPrompt.split(/\r?\n/u).find(line => line.trim())?.trim().slice(0, 240) ?? "",
						time: new Date().toISOString(),
					})}\n`,
				);
			} catch (error) {
				return failBeforeActivation(`native-call trace is unsafe or unwritable (${(error as Error).message})`);
			}
		}

		const attemptJournal = siblingJournal(tracePath, "native-attempts.jsonl", opts.cwd);
		const heartbeatJournal = siblingJournal(tracePath, "native-heartbeats.jsonl", opts.cwd);
		try {
			if (attemptJournal) writeRegularFileNoFollow(attemptJournal, "");
			if (heartbeatJournal) writeRegularFileNoFollow(heartbeatJournal, "");
		} catch (error) {
			return failBeforeActivation(`native session journal is unsafe or unwritable (${(error as Error).message})`);
		}
		modelCalls = callNumber;
		const { authorization: _boundAuthorization, ...processOptions } = opts;
		let res: SpawnCollectResult;
		try {
			if (session?.idSource === "create-stdout") {
				verifyNativeLaunchAuthorization(authorization);
				const setupArgv = [...authorization.invocationPrefix, ...session.createArgs!];
				const setupStarted = performance.now();
				let setup = await spawnCollect(setupArgv, { ...processOptions, env });
				try {
					verifyNativeLaunchAuthorization(authorization);
				} catch (error) {
					setup = {
						...setup,
						exitCode: setup.exitCode || 2,
						stderr: [setup.stderr, `native launch authorization drifted after session creation (${(error as Error).message})`]
							.filter(Boolean).join("\n"),
					};
				}
				const classification = classifyNativeAttempt(
					setup.exitCode,
					setup.stderr,
					setup.terminationReason,
					setup.cleanupFailure,
				);
				if (setup.exitCode === 0 && !setup.cleanupFailure) {
					const candidate = setup.stdout.trim().split(/\r?\n/u).find(Boolean)?.trim();
					if (candidate && /^[A-Za-z0-9][A-Za-z0-9._:-]*$/u.test(candidate)) sessionId = candidate;
				}
				appendJournal(attemptJournal, {
					call: callNumber,
					attempt: 0,
					mode: "session-create",
					catalogModelId: authorization.catalogModelId,
					executable: authorization.executable,
					processExecutable: authorization.invocationPrefix[0],
					executableSha256: authorization.executableSha256.slice("sha256:".length),
					executionClosureSha256: authorization.executionClosureSha256.slice("sha256:".length),
					invocationPrefixSha256: nativeInvocationPrefixSha256(authorization.invocationPrefix),
					environmentSha256: nativeChildEnvironmentSha256(env),
					launchReceiptSha256: authorization.launchReceiptSha256,
					argvSha256: digestBytes(JSON.stringify(setupArgv)),
					sessionId: sessionId ?? null,
					classification: setup.cleanupFailure
						? classification
						: sessionId ? "COMPLETED" : setup.exitCode === 0 ? "NATIVE_FAILURE" : classification,
					exitCode: setup.exitCode,
					terminationReason: setup.terminationReason ?? null,
					cleanupFailure: setup.cleanupFailure ?? null,
					durationMs: Math.floor(performance.now() - setupStarted),
					stdoutSha256: createHash("sha256").update(setup.stdout).digest("hex"),
					stderrSha256: createHash("sha256").update(setup.stderr).digest("hex"),
					time: new Date().toISOString(),
				});
				if (!sessionId) {
					res = {
						...setup,
						stderr: setup.stderr || "native CLI did not return a usable session id",
						exitCode: setup.exitCode || 2,
					};
				} else {
					res = { stdout: "", stderr: "", exitCode: 0 };
				}
			} else {
				res = { stdout: "", stderr: "", exitCode: 0 };
			}

			let attempt = 0;
			while (res.exitCode === 0) {
				attempt += 1;
				const mode = attempt === 1 || !session ? "start" : "resume";
				const attemptPrompt = attempt === 1 || !session
					? finalPrompt
					: session!.continuePrompt + NATIVE_CONTRACT_SUFFIX;
				// Session arguments belong to the CLI, after its authenticated
				// interpreter/bundle prefix. Insert into logical argv before expansion.
				const argv = authorizedNativeArgv(
					insertSessionArgs(renderNativeArgv(boundEntry, mapped, attemptPrompt, workspaceAccess, effort),
						mode === "start" ? session?.start : session?.resume, sessionId),
					authorization,
				);
				verifyNativeLaunchAuthorization(authorization);
				const attemptStarted = performance.now();
				let heartbeatError: Error | undefined;
				res = await spawnCollect(argv, {
					...processOptions,
					env,
					heartbeatMs: session?.heartbeatSeconds === undefined ? undefined : session.heartbeatSeconds * 1_000,
					inactivityTimeoutMs: timeoutSeconds === undefined ? undefined : timeoutSeconds * 1_000,
					onHeartbeat: heartbeat => {
						try {
							appendJournal(heartbeatJournal, {
								call: callNumber,
								attempt,
								mode,
								sessionId: sessionId ?? null,
								processAlive: true,
								...heartbeat,
								time: new Date().toISOString(),
							});
						} catch (error) {
							heartbeatError = error as Error;
						}
					},
				});
				if (heartbeatError) throw new Error(`native heartbeat journal failed (${heartbeatError.message})`);
				try {
					verifyNativeLaunchAuthorization(authorization);
				} catch (error) {
					res = {
						...res,
						exitCode: res.exitCode || 2,
						stderr: [res.stderr, `native launch authorization drifted after ${mode} (${(error as Error).message})`]
							.filter(Boolean).join("\n"),
					};
				}
				if (session?.idSource === "jsonl-event" && !sessionId) {
					sessionId = discoverSessionId(session, res.stdout);
				}
				if (res.exitCode !== 0) {
					res = { ...res, stderr: normalizeNativeFailureStderr(res.stderr, res.terminationReason === "signal") };
				}
				// JSONL CLIs can report transport/auth failures as structured stdout events while
				// leaving stderr empty. Classification is regex-only and does not parse or trust the
				// model response, so inspect both captured channels and keep unknown failures closed.
				const classification = classifyNativeAttempt(
					res.exitCode,
					`${res.stderr}\n${res.stdout}`,
					res.terminationReason,
					res.cleanupFailure,
				);
				const nativeExitCode = res.exitCode;
				appendJournal(attemptJournal, {
					call: callNumber,
					attempt,
					mode,
					catalogModelId: authorization.catalogModelId,
					executable: authorization.executable,
					processExecutable: authorization.invocationPrefix[0],
					executableSha256: authorization.executableSha256.slice("sha256:".length),
					executionClosureSha256: authorization.executionClosureSha256.slice("sha256:".length),
					invocationPrefixSha256: nativeInvocationPrefixSha256(authorization.invocationPrefix),
					environmentSha256: nativeChildEnvironmentSha256(env),
					launchReceiptSha256: authorization.launchReceiptSha256,
					argvSha256: digestBytes(JSON.stringify(argv)),
					sessionId: sessionId ?? null,
					classification,
					exitCode: nativeExitCode,
					terminationReason: res.terminationReason ?? null,
					cleanupFailure: res.cleanupFailure ?? null,
					durationMs: Math.floor(performance.now() - attemptStarted),
					stdoutBytes: Buffer.byteLength(res.stdout),
					stderrBytes: Buffer.byteLength(res.stderr),
					stdoutSha256: createHash("sha256").update(res.stdout).digest("hex"),
					stderrSha256: createHash("sha256").update(res.stderr).digest("hex"),
					time: new Date().toISOString(),
				});
				if (classification !== "COMPLETED" && nativeExitCode === 0) {
					res = { ...res, exitCode: 2 };
				}
				if (classification === "COMPLETED") break;
				if (
					!retryProfileAvailable
					|| (session !== undefined && !sessionId)
					|| !recoveryEligible(classification)
					|| attempt > MAX_NATIVE_RECOVERY_RETRIES
					|| opts.signal?.aborted
				) break;
				const backoff = backoffSeconds![Math.min(attempt - 1, backoffSeconds!.length - 1)];
				if (!await waitForRecovery(backoff, opts.signal)) break;
				res = { stdout: "", stderr: "", exitCode: 0 };
			}
		} catch (error) {
			const { result: audit, tampered, snapshotTampered, failure } = closeGate();
			if (failure !== undefined ||
				(requiresRunnerGate && (tampered || snapshotTampered || !audit || audit.exitCode !== 0))) {
					const auditMessage = audit
						? new TextDecoder().decode(audit.stderr).trim()
						: "native protected-path audit did not run";
					return routeFailure(
						`native CLI '${entry.bin}' failed and protected-path audit also failed`
						+ `${failure ? ` (${failure})` : auditMessage ? ` (${auditMessage})` : ""}`,
					);
			}
			return routeFailure(
				`native CLI '${entry.bin}' could not start for model '${model}' (${(error as Error).message})`,
			);
		}
		if (res.cleanupFailure) {
			const failure = res.cleanupFailure;
			const diagnostic = `[native-workflow] process cleanup failed during ${failure.phase} (${failure.code}: ${failure.message}); cleanup was not verified`;
			res = {
				...res,
				stderr: [res.stderr, diagnostic].filter(Boolean).join("\n"),
				exitCode: res.exitCode || 2,
			};
		}
		if (res.exitCode === 0) {
			res = { ...res, stdout: normalizeNativeActivationStdout(normalizeSessionOutput(session, res.stdout)) };
		}
		{
			const { result: audit, tampered, snapshotTampered, failure } = closeGate();
			if (failure !== undefined ||
				(requiresRunnerGate && (tampered || snapshotTampered || !audit || audit.exitCode !== 0))) {
				const msg = audit ? new TextDecoder().decode(audit.stderr) : "native protected-path audit did not run";
				const gateMsg = [
					tampered ? "native-node frozen audit helper was modified and restored" : "",
					snapshotTampered ? "native-node frozen audit state was modified and restored" : "",
					failure ?? "",
				].filter(Boolean).join("\n");
				return {
					stdout: res.stdout,
					stderr: [res.stderr, gateMsg, msg].filter(Boolean).join("\n").trim(),
					exitCode: res.exitCode || 2,
				};
			}
		}
		try {
			verifyNativeLaunchAuthorization(authorization);
		} catch (error) {
			return {
				stdout: res.stdout,
				stderr: [res.stderr, `native launch authorization drifted before result handoff (${(error as Error).message})`]
					.filter(Boolean).join("\n"),
				exitCode: res.exitCode || 2,
			};
		}
		return res;
	};
	return Object.assign(runner, { getModelCalls: () => modelCalls });
}
