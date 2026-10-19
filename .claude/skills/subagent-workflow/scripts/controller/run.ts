#!/usr/bin/env bun

/**
 * A small-model scheduler over a deterministic workflow controller.
 *
 * The scheduler may only choose from the controller-computed runnable frontier.
 * It never launches processes, mutates workflow state, changes budgets, or declares
 * success. Native model execution is delegated to the audited shared CLI core.
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { isDeepStrictEqual } from "node:util";
import {
	classifyNativeAttempt,
	classifyProvider,
	makeNativeRunner,
	materializeWorkflowSkills,
	nativeChildEnvironmentSha256,
	nativeInvocationPrefixSha256,
	normalizeNativeMap,
	normalizeReadOnlyArgs,
	osAccountHome,
	resolveNativeProviderIdentity,
	spawnCollect,
	type NativeLaunchAuthorization,
	verifyNativeExecutionClosure,
	verifyNativeLaunchAuthorization,
} from "../native/run";
import {
	bindSuccessfulTerminal,
	canonicalJson,
	canonicalMachineWorkflowProofRelative,
	captureProofBinding,
	captureEvidenceProducer,
	captureLatestMachineReview,
	captureWorkspaceEvidence,
	controllerFinalizeAuthorityFromEnvironment,
	createStateBoundCandidate,
	finalizeStateBoundCandidateOnly,
	freezeProofArtifacts,
	loadStateBoundCandidate,
	STATE_BOUND_PROOF_THREAT_BOUNDARY,
	type FrozenArtifactSet,
	type ProofBinding,
	type ResolvedExecutableBinding,
	type StateBoundCandidate,
	unprivilegedChildEnvironment,
	verifyEvidenceProducer,
	verifyFrozenProofArtifacts,
	verifyMachineReviewBinding,
	verifyProofBinding,
	verifyWorkspaceEvidence,
	writeStateBoundCandidate,
} from "./state_bound_proof";
import { captureCurrentState } from "./actions/reward_evidence";
import {
	type LaunchResolver,
	type LaunchSpecRequest,
	createBoundLaunchResolver,
} from "./launch_resolver";

type JsonObject = Record<string, unknown>;

const CONTROLLER_RELATIVE = ".claude/skills/subagent-workflow/scripts/controller/run.ts";
const LAUNCH_RESOLVER_RELATIVE = ".claude/skills/subagent-workflow/scripts/controller/launch_resolver.ts";
const LAUNCH_POLICY_RESOLVER_RELATIVE = ".claude/skills/update-platform-config/scripts/config-sync/resolve_launch_spec.py";
const LAUNCH_CATALOG_RELATIVE = ".claude/skills/update-platform-config/scripts/config-sync/live_catalog.py";
const LAUNCH_POLICY_VALIDATOR_RELATIVE = ".claude/skills/update-platform-config/scripts/config-sync/validate_model_roles.py";
const LAUNCH_PROJECT_PATHS_RELATIVE = ".claude/skills/update-platform-config/scripts/config-sync/project_paths.py";
const NATIVE_CORE_RELATIVE = ".claude/skills/subagent-workflow/scripts/native/run.ts";
const PROOF_MODULE_RELATIVE = ".claude/skills/subagent-workflow/scripts/controller/state_bound_proof.ts";
const NATIVE_MAP_RELATIVE = ".claude/skills/subagent-workflow/scripts/native/native-map.json";
const REWARD_ACTION_RELATIVE = ".claude/skills/subagent-workflow/scripts/controller/actions/reward_evidence.ts";

function resolveRepoPath(root: string, relative: string): string {
	const canonicalRoot = fs.realpathSync(root);
	const resolved = path.resolve(canonicalRoot, relative);
	const real = fs.realpathSync(resolved);
	const containment = path.relative(canonicalRoot, real);
	if (containment.startsWith("..") || path.isAbsolute(containment)) {
		throw new Error(`repository-owned path ${relative} escapes the project root`);
	}
	const expected = path.resolve(canonicalRoot, relative);
	if (path.normalize(real) !== path.normalize(expected) && !real.startsWith(expected + path.sep) && real !== expected) {
		throw new Error(`repository-owned path ${relative} resolved outside its declared location`);
	}
	return real;
}

// The resolver lives behind its own production module so that the launch
// authorization path has exactly one implementation and no injection hook: there is
// no exported override, no CLI flag, and no environment variable that can redirect
// it. Tests substitute the module at its boundary instead.

interface NativeMap {
	providers: Record<string, {
		bin: string;
		nativeProviderFamily?: string;
		args: string[];
		readArgs?: string[];
		readOnlyArgs?: string[];
		schedulerArgs?: string[];
		relayModelPrefixes?: string[];
		modelMap?: Record<string, string>;
		effort?: {
			values: string[];
			args?: string[];
			model?: string;
			encoding?: "catalog-model-id";
		};
		session?: {
			idSource: "assigned-uuid" | "create-stdout" | "jsonl-event";
			createArgs?: string[];
			start?: { position: "start" | "after"; anchor?: string; args: string[] };
			resume: { position: "start" | "after"; anchor?: string; args: string[] };
			eventType?: string;
			eventIdField?: string;
			output?: "text" | "jsonl-agent-message";
			continuePrompt: string;
			heartbeatSeconds?: number;
			stallSeconds?: number;
			backoffSeconds: number[];
		};
	}>;
	modelPrefixes?: Record<string, string>;
	upstreamModelFamilies?: Record<string, {
		upstreamProviderFamily: string;
		modelFamily: string;
		dispatchEligibility: "active" | "future-candidate" | "excluded";
		requiredEffort?: string;
		requiredEffortPolicy?: "highest-available";
	}>;
}

interface DesignNode {
	id: string;
	kind: "script" | "agent" | "decision" | "reducer" | "terminal";
	role?: string;
	workspaceAccess?: "read" | "write";
	reads?: string[];
	writes?: string[];
	waitFor?: string[];
	outcomes?: string[];
	reviews?: string[];
	timeoutSeconds?: number;
	outcome?: string;
	session?: "fresh" | string;
	skills?: { required?: string[]; onDemand?: string[] };
}

interface Transition {
	from: string;
	to: string;
	when?: Record<string, unknown>;
}

interface WorkflowDesign {
	schema: "workflow-design/v1";
	id: string;
	entry: string;
	governance?: { reviewPolicy?: string; completionProof?: string; malformedDecision?: string };
	budgets?: { maxModelCalls?: number; maxWallTimeSeconds?: number };
	requiredCapabilities?: string[];
	state?: Array<{
		name: string;
		type: string;
		maxItems?: number;
		itemType?: string;
		maxItemBytes?: number;
		uniqueItems?: boolean;
	}>;
	nodes: DesignNode[];
	transitions: Transition[];
	terminals: Record<string, string>;
}

type Handler =
	| { type: "input"; bindings: Record<string, string | { const: unknown }> }
	| { type: "agent"; prompt: string }
	| { type: "decision"; prompt: string }
	| { type: "increment"; field: string; by?: number; set?: Record<string, unknown> }
	| { type: "template"; values: Record<string, string> }
	| { type: "command"; argv: string[]; runWrites?: string[] }
	| { type: "terminal" };

type ExecutableSource =
	| { source: "controller-runtime" }
	| { source: "path"; command: string }
	| { source: "native-role"; role: string };

interface WorkflowMapping {
	schema: "subagent-workflow/v1";
	designPath: string;
	designSha256: string;
	inputPath: string;
	reasoningPosture: string;
	scheduler: {
		role: string;
		mode: "ambiguous-only" | "every-frontier" | "deterministic-all";
		maxCalls: number;
		timeoutSeconds?: number;
		reads?: string[];
		workspaceWriteScopes?: Record<string, string[]>;
	};
	proof?: {
		itemIdField: string;
		evidencePathField: string;
		evidenceDigestField?: string;
		helperPath: string;
		resourcesManifestPath: string;
		resources: Record<string, string>;
		finalReviewNodes: string[];
		evidenceProducerNodes: string[];
		currentStateTimeoutSeconds: number;
		actionPath: string;
		executables: Record<string, ExecutableSource | ResolvedExecutableBinding>;
	};
	handlers: Record<string, Handler>;
}

interface WorkflowProofInput extends JsonObject {
	proofBinding?: unknown;
}

interface RoleSpec {
	backend: string;
	provider: string;
	model: string;
	effort?: string | null;
	effortPolicy?: "highest-available";
	upstreamProviderFamily?: string;
	modelFamily?: string;
	dispatchStatus?: "active" | "catalog-candidate";
	intelligenceClass?: string;
}

interface RoleDocument extends JsonObject {
	schema?: unknown;
	roles?: unknown;
	capabilityPolicy?: unknown;
	catalogResolution?: unknown;
	reasoningPolicy?: unknown;
	auditedModelIdentities?: unknown;
}

interface FrozenObject<T extends JsonObject> {
	path: string;
	sha256: string;
	value: T;
}

interface ControllerState {
	version: 1;
	runId: string;
	designId: string;
	designSha256: string;
	mappingSha256: string;
	rolesSha256: string;
	nativeMapSha256: string;
	inputSha256: string;
	status: "running" | "success" | "unresolved" | "failure";
	completionPhase: "running" | "terminal" | "candidate" | "finalized";
	terminal: string;
	frontier: string[];
	pending: string[];
	completed: string[];
	inFlight: string;
	data: Record<string, unknown>;
	outputs: Record<string, JsonObject>;
	modelCalls: number;
	schedulerCalls: number;
	nodeCalls: number;
	forcedReason: string;
	candidateDigest: string;
	launchReceiptsDigest: string;
	proofPath: string;
	proofSha256: string;
	startedAt: string;
	updatedAt: string;
	elapsedMs: number;
}

interface ControllerOptions {
	root: string;
	mappingPath: string;
	rolesPath: string;
	nativeMapPath: string;
	inputPath?: string;
	outputRoot: string;
	resume?: string;
	finalizeOnly?: boolean;
	verifyProofOnly?: boolean;
	/** Optional outer-owner controls may narrow execution, never alter the design. */
	signal?: AbortSignal;
	parentBudget?: { maxModelCalls: number; maxWallTimeSeconds: number };
	parentAccess?: "read" | "write";
}

interface RunDirIdentity {
	path: string;
	dev: number;
	ino: number;
}

interface RunClock {
	baseElapsedMs: number;
	startedMonotonicMs: number;
	signal?: AbortSignal;
	parentWallTimeSeconds?: number;
}

interface FrozenRuntimeStat { label: string; dev: bigint; ino: bigint; ctimeNs: bigint; mtimeNs: bigint; size: bigint; mode: bigint }

function captureFrozenRuntimeStats(frozen: FrozenArtifactSet | undefined): FrozenRuntimeStat[] {
	if (!frozen) return [];
	verifyFrozenProofArtifacts(frozen);
	return frozen.files.map(file => {
		const stat = fs.statSync(path.join(frozen.root, file.relativePath), { bigint: true });
		return { label: file.label, dev: stat.dev, ino: stat.ino, ctimeNs: stat.ctimeNs, mtimeNs: stat.mtimeNs, size: stat.size, mode: stat.mode };
	});
}

function verifyFrozenRuntimeStats(frozen: FrozenArtifactSet | undefined, expected: FrozenRuntimeStat[]): void {
	if (!frozen) return;
	const actual = captureFrozenRuntimeStats(frozen);
	if (!isDeepStrictEqual(actual, expected)) throw new Error("frozen artifact identity changed during external activation");
}

class EvidencePathError extends Error {
	constructor(message: string, cause?: unknown) {
		super(`unsafe workflow evidence path: ${message}`, cause === undefined ? undefined : { cause });
		this.name = "EvidencePathError";
	}
}

/**
 * A launch was not authorized by the fresh canonical resolution.
 *
 * This is a contract violation, not a workflow outcome: recording it as an
 * `unresolved` run would bury the reason under a generic receipt-ledger mismatch
 * and let a caller retry into the same unauthorized launch. Like
 * `EvidencePathError` it propagates out of the run loop unchanged.
 */
class LaunchAuthorizationError extends Error {
	constructor(message: string, cause?: unknown) {
		super(`native launch not authorized: ${message}`, cause === undefined ? undefined : { cause });
		this.name = "LaunchAuthorizationError";
	}
}

class MalformedDecisionError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "MalformedDecisionError";
	}
}

function sha256Bytes(value: string | Buffer): string {
	return createHash("sha256").update(value).digest("hex");
}

function sha256File(file: string): string {
	return sha256Bytes(fs.readFileSync(file));
}

function readObject<T extends JsonObject>(file: string, label: string): T {
	let value: unknown;
	try {
		value = JSON.parse(fs.readFileSync(file, "utf8"));
	} catch (error) {
		throw new Error(`${label} is unreadable: ${(error as Error).message}`);
	}
	if (!value || typeof value !== "object" || Array.isArray(value)) {
		throw new Error(`${label} must be a JSON object`);
	}
	return value as T;
}

function freezeObject<T extends JsonObject>(root: string, value: string, label: string): FrozenObject<T> {
	const file = safeResolveExisting(root, value, label);
	let descriptor: number | undefined;
	let bytes: Buffer;
	try {
		descriptor = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		if (!fs.fstatSync(descriptor).isFile()) throw new Error(`${label} is not a regular file`);
		bytes = fs.readFileSync(descriptor);
	} catch (error) {
		throw new Error(`${label} is unreadable: ${(error as Error).message}`);
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
	let parsed: unknown;
	try {
		parsed = JSON.parse(bytes.toString("utf8"));
	} catch (error) {
		throw new Error(`${label} is unreadable: ${(error as Error).message}`);
	}
	if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error(`${label} must be a JSON object`);
	}
	return { path: file, sha256: sha256Bytes(bytes), value: parsed as T };
}

function safeResolve(root: string, value: string, label: string): string {
	const resolved = path.resolve(root, value);
	const relative = path.relative(root, resolved);
	if (relative.startsWith("..") || path.isAbsolute(relative)) {
		throw new Error(`${label} escapes the project root`);
	}
	return resolved;
}

function safeResolveExisting(root: string, value: string, label: string): string {
	const resolved = safeResolve(root, value, label);
	let realRoot: string;
	let realTarget: string;
	try {
		realRoot = fs.realpathSync(root);
		realTarget = fs.realpathSync(resolved);
	} catch (error) {
		throw new Error(`${label} is unavailable: ${(error as Error).message}`);
	}
	const relative = path.relative(realRoot, realTarget);
	if (relative.startsWith("..") || path.isAbsolute(relative)) {
		throw new Error(`${label} escapes the project root through a symlink`);
	}
	// Keep using the object that was checked instead of the symlink spelling. This
	// removes a later opportunity to retarget an otherwise valid runtime input.
	return realTarget;
}

function pathSegments(root: string, target: string): string[] {
	const relative = path.relative(root, target);
	if (!relative || relative === ".") return [];
	if (relative.startsWith("..") || path.isAbsolute(relative)) {
		throw new EvidencePathError(`${target} escapes ${root}`);
	}
	return relative.split(path.sep).filter(Boolean);
}

function requirePlainDirectory(directory: string, label: string): fs.Stats {
	let stat: fs.Stats;
	try {
		stat = fs.lstatSync(directory);
	} catch (error) {
		throw new EvidencePathError(`${label} is unavailable`, error);
	}
	if (stat.isSymbolicLink() || !stat.isDirectory()) {
		throw new EvidencePathError(`${label} is not a plain directory`);
	}
	return stat;
}

function ensureDirectoryTree(root: string, target: string, finalMustBeNew = false): void {
	requirePlainDirectory(root, "project root");
	let current = root;
	const segments = pathSegments(root, target);
	for (let index = 0; index < segments.length; index += 1) {
		current = path.join(current, segments[index]);
		if (fs.existsSync(current)) {
			if (finalMustBeNew && index === segments.length - 1) {
				throw new EvidencePathError("new run directory already exists");
			}
			requirePlainDirectory(current, current);
			continue;
		}
		try {
			fs.mkdirSync(current, { mode: 0o700 });
		} catch (error) {
			throw new EvidencePathError(`could not create directory ${current}`, error);
		}
		requirePlainDirectory(current, current);
	}
}

function captureRunDir(root: string, runDir: string, create: boolean): RunDirIdentity {
	if (create) ensureDirectoryTree(root, runDir, true);
	else {
		ensureDirectoryTree(root, path.dirname(runDir));
		requirePlainDirectory(runDir, "resume run directory");
	}
	const stat = requirePlainDirectory(runDir, "run directory");
	return { path: runDir, dev: stat.dev, ino: stat.ino };
}

function assertRunDirIdentity(identity: RunDirIdentity): void {
	const stat = requirePlainDirectory(identity.path, "run directory");
	if (stat.dev !== identity.dev || stat.ino !== identity.ino) {
		throw new EvidencePathError("run directory identity changed");
	}
}

function verifyEvidenceTree(identity: RunDirIdentity): void {
	assertRunDirIdentity(identity);
	const visit = (directory: string): void => {
		let entries: fs.Dirent[];
		try {
			entries = fs.readdirSync(directory, { withFileTypes: true });
		} catch (error) {
			throw new EvidencePathError(`could not inspect ${directory}`, error);
		}
		for (const entry of entries) {
			const item = path.join(directory, entry.name);
			if (entry.isSymbolicLink()) throw new EvidencePathError(`${item} is a symlink`);
			if (entry.isDirectory()) visit(item);
			else if (!entry.isFile()) throw new EvidencePathError(`${item} is not a regular file`);
		}
	};
	visit(identity.path);
}

interface EvidenceSnapshotEntry {
	kind: "directory" | "file";
	bytes?: Buffer;
}

function readRegularBytesNoFollow(file: string): Buffer {
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		if (!fs.fstatSync(descriptor).isFile()) throw new EvidencePathError(`${file} is not a regular file`);
		return fs.readFileSync(descriptor);
	} catch (error) {
		if (error instanceof EvidencePathError) throw error;
		throw new EvidencePathError(`could not snapshot ${file}`, error);
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
}

function readProjectFileNoFollow(root: string, relative: string, label: string): { path: string; bytes: Buffer } {
	const file = safeResolve(root, relative, label);
	let parent = root;
	for (const segment of pathSegments(root, path.dirname(file))) {
		parent = path.join(parent, segment);
		requirePlainDirectory(parent, `${label} parent`);
	}
	let stat: fs.Stats;
	try {
		stat = fs.lstatSync(file);
	} catch (error) {
		throw new EvidencePathError(`${label} is unavailable`, error);
	}
	if (stat.isSymbolicLink() || !stat.isFile()) throw new EvidencePathError(`${label} is not a plain file`);
	return { path: file, bytes: readRegularBytesNoFollow(file) };
}

function evidenceSnapshot(identity: RunDirIdentity): Map<string, EvidenceSnapshotEntry> {
	verifyEvidenceTree(identity);
	const snapshot = new Map<string, EvidenceSnapshotEntry>();
	const visit = (directory: string): void => {
		for (const entry of fs.readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
			const item = path.join(directory, entry.name);
			const relative = path.relative(identity.path, item);
			if (entry.isDirectory()) {
				snapshot.set(relative, { kind: "directory" });
				visit(item);
			} else if (entry.isFile()) {
				snapshot.set(relative, { kind: "file", bytes: readRegularBytesNoFollow(item) });
			} else throw new EvidencePathError(`${item} is not a regular evidence entry`);
		}
	};
	visit(identity.path);
	return snapshot;
}

function verifyExternalCallEvidence(
	identity: RunDirIdentity,
	before: Map<string, EvidenceSnapshotEntry>,
	expectedNativeCall?: number,
	allowedRunWrites: string[] = [],
): Array<{ path: string; sha256: string }> {
	const after = evidenceSnapshot(identity);
	const allowedFiles = new Set(allowedRunWrites);
	for (const relative of allowedFiles) {
		if (before.has(relative)) throw new EvidencePathError(`declared run write already exists before activation: ${relative}`);
	}
	const allowedDirectories = new Set<string>();
	for (const file of allowedFiles) {
		let parent = path.posix.dirname(file);
		while (parent !== ".") {
			allowedDirectories.add(parent);
			parent = path.posix.dirname(parent);
		}
	}
	for (const key of before.keys()) if (!after.has(key)) throw new EvidencePathError(`external node removed evidence ${key}`);
	for (const [relative, entry] of after) {
		if (before.has(relative)) continue;
		if (entry.kind === "directory" && allowedDirectories.has(relative)) continue;
		if (entry.kind === "file" && allowedFiles.has(relative)) continue;
		throw new EvidencePathError(`external node created undeclared evidence ${relative}`);
	}
	for (const [relative, oldEntry] of before) {
		const newEntry = after.get(relative)!;
		if (oldEntry.kind !== newEntry.kind) throw new EvidencePathError(`external node changed evidence type ${relative}`);
		if (oldEntry.kind === "directory") continue;
		const oldBytes = oldEntry.bytes!;
		const newBytes = newEntry.bytes!;
		if (allowedFiles.has(relative)) continue;
		const nativeJournals = new Set(["native-calls.jsonl", "native-attempts.jsonl", "native-heartbeats.jsonl"]);
		if (!nativeJournals.has(relative) || expectedNativeCall === undefined) {
			if (!oldBytes.equals(newBytes)) throw new EvidencePathError(`external node modified evidence file ${relative}`);
			continue;
		}
		if (oldBytes.equals(newBytes)) continue;
		if (newBytes.length <= oldBytes.length || !newBytes.subarray(0, oldBytes.length).equals(oldBytes)) {
			throw new EvidencePathError(`external node modified the existing ${relative} prefix`);
		}
		const suffix = newBytes.subarray(oldBytes.length).toString("utf8");
		const lines = suffix.split(/\r?\n/u).filter(line => line.trim());
		if (relative === "native-calls.jsonl" && lines.length !== 1) {
			throw new EvidencePathError("native runner did not append exactly one logical call record");
		}
		for (const line of lines) {
			let record: unknown;
			try {
				record = JSON.parse(line);
			} catch (error) {
				throw new EvidencePathError(`native runner appended a malformed ${relative} record`, error);
			}
			if (!record || typeof record !== "object" || Array.isArray(record) || (record as JsonObject).call !== expectedNativeCall) {
				throw new EvidencePathError(`native runner appended the wrong call number to ${relative}; expected ${expectedNativeCall}`);
			}
			const item = record as JsonObject;
			if (relative === "native-attempts.jsonl" &&
				(!Number.isInteger(item.attempt) || Number(item.attempt) < 0 ||
				 !["session-create", "start", "resume"].includes(String(item.mode)) ||
				 typeof item.classification !== "string" ||
				 !(item.sessionId === null || typeof item.sessionId === "string") ||
					 !nonemptyString(item.catalogModelId) || !nonemptyString(item.executable) ||
					 !path.isAbsolute(String(item.executable)) || !path.isAbsolute(String(item.processExecutable)) ||
					 ["argvSha256", "environmentSha256", "executableSha256", "executionClosureSha256",
						"invocationPrefixSha256", "launchReceiptSha256"]
						.some(key => typeof item[key] !== "string" || !/^[0-9a-f]{64}$/u.test(String(item[key]))))) {
				throw new EvidencePathError("native runner appended a malformed physical-attempt record");
			}
			if (relative === "native-heartbeats.jsonl" &&
				(!Number.isInteger(item.attempt) || Number(item.attempt) < 1 || item.processAlive !== true ||
				 typeof item.elapsedMs !== "number" || typeof item.idleMs !== "number" ||
				 !(item.sessionId === null || typeof item.sessionId === "string"))) {
				throw new EvidencePathError("native runner appended a malformed heartbeat record");
			}
		}
	}
	const bindings = [...allowedFiles].sort().map(relative => {
		const entry = after.get(relative);
		if (!entry || entry.kind !== "file") throw new EvidencePathError(`external node omitted declared run write ${relative}`);
		return { path: relative, sha256: sha256Bytes(entry.bytes!) };
	});
	return bindings;
}

function evidenceManifest(identity: RunDirIdentity): Array<{ path: string; sha256: string }> {
	const snapshot = evidenceSnapshot(identity);
	return [...snapshot.entries()]
		.filter(([relative, entry]) => entry.kind === "file" && relative !== "result.json")
		.map(([relative, entry]) => ({
			path: path.join(identity.path, relative),
			sha256: sha256Bytes(entry.bytes!),
		}))
		.sort((a, b) => a.path.localeCompare(b.path));
}

function ensureEvidenceDirectory(identity: RunDirIdentity, directory: string): void {
	assertRunDirIdentity(identity);
	let current = identity.path;
	for (const segment of pathSegments(identity.path, directory)) {
		current = path.join(current, segment);
		if (!fs.existsSync(current)) {
			try {
				fs.mkdirSync(current, { mode: 0o700 });
			} catch (error) {
				throw new EvidencePathError(`could not create ${current}`, error);
			}
		}
		requirePlainDirectory(current, current);
	}
}

function assertEvidenceFile(identity: RunDirIdentity, file: string, allowMissing: boolean): void {
	assertRunDirIdentity(identity);
	pathSegments(identity.path, file);
	ensureEvidenceDirectory(identity, path.dirname(file));
	let stat: fs.Stats;
	try {
		stat = fs.lstatSync(file);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT" && allowMissing) return;
		throw new EvidencePathError(`${file} is missing`, error);
	}
	if (stat.isSymbolicLink() || !stat.isFile()) throw new EvidencePathError(`${file} is not a plain file`);
}

function secureReadText(identity: RunDirIdentity, file: string): string {
	assertEvidenceFile(identity, file, false);
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		if (!fs.fstatSync(descriptor).isFile()) throw new EvidencePathError(`${file} is not a regular file`);
		return fs.readFileSync(descriptor, "utf8");
	} catch (error) {
		if (error instanceof EvidencePathError) throw error;
		throw new EvidencePathError(`could not read ${file} without following symlinks`, error);
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
}

function secureAtomicWrite(identity: RunDirIdentity, file: string, value: string): void {
	assertEvidenceFile(identity, file, true);
	const temporary = path.join(path.dirname(file), `.${path.basename(file)}.tmp-${process.pid}-${randomUUID()}`);
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(
			temporary,
			fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW,
			0o600,
		);
		fs.writeFileSync(descriptor, value);
		fs.fsyncSync(descriptor);
		fs.closeSync(descriptor);
		descriptor = undefined;
		assertEvidenceFile(identity, file, true);
		fs.renameSync(temporary, file);
		const parent = fs.openSync(path.dirname(file), fs.constants.O_RDONLY);
		try {
			fs.fsyncSync(parent);
		} finally {
			fs.closeSync(parent);
		}
	} catch (error) {
		if (descriptor !== undefined) fs.closeSync(descriptor);
		try {
			const stat = fs.lstatSync(temporary);
			if (stat.isFile() && !stat.isSymbolicLink()) fs.unlinkSync(temporary);
		} catch {
			// The temporary may already have been renamed or removed.
		}
		if (error instanceof EvidencePathError) throw error;
		throw new EvidencePathError(`could not atomically write ${file}`, error);
	}
}

function atomicJson(identity: RunDirIdentity, file: string, value: unknown): void {
	secureAtomicWrite(identity, file, `${JSON.stringify(value, null, 2)}\n`);
}

function appendEvent(identity: RunDirIdentity, file: string, event: JsonObject): void {
	assertEvidenceFile(identity, file, true);
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(
			file,
			fs.constants.O_WRONLY | fs.constants.O_APPEND | fs.constants.O_CREAT | fs.constants.O_NOFOLLOW,
			0o600,
		);
		if (!fs.fstatSync(descriptor).isFile()) throw new EvidencePathError(`${file} is not a regular file`);
		fs.writeSync(descriptor, `${JSON.stringify(event)}\n`);
		fs.fsyncSync(descriptor);
	} catch (error) {
		if (error instanceof EvidencePathError) throw error;
		throw new EvidencePathError(`could not append ${file} without following symlinks`, error);
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
}

function readEvidenceObject<T extends JsonObject>(identity: RunDirIdentity, file: string, label: string): T {
	let value: unknown;
	try {
		value = JSON.parse(secureReadText(identity, file));
	} catch (error) {
		if (error instanceof EvidencePathError) throw error;
		throw new Error(`${label} is unreadable: ${(error as Error).message}`);
	}
	if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} must be a JSON object`);
	return value as T;
}

function evidenceFileExists(identity: RunDirIdentity, file: string): boolean {
	assertRunDirIdentity(identity);
	pathSegments(identity.path, file);
	let stat: fs.Stats;
	try {
		stat = fs.lstatSync(file);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
		throw new EvidencePathError(`could not inspect ${file}`, error);
	}
	if (stat.isSymbolicLink() || !stat.isFile()) throw new EvidencePathError(`${file} is not a plain file`);
	return true;
}

function readEvidenceJsonLines(identity: RunDirIdentity, file: string, label: string): JsonObject[] {
	if (!evidenceFileExists(identity, file)) return [];
	const raw = secureReadText(identity, file);
	if (!raw.trim()) return [];
	return raw.split(/\r?\n/u).filter(line => line.trim()).map((line, index) => {
		let value: unknown;
		try {
			value = JSON.parse(line);
		} catch (error) {
			throw new Error(`${label} line ${index + 1} is malformed: ${(error as Error).message}`);
		}
		if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} line ${index + 1} is not an object`);
		return value as JsonObject;
	});
}

function normalizeArray(value: unknown, label: string): string[] {
	if (!Array.isArray(value) || !value.every(item => typeof item === "string" && item.length > 0)) {
		throw new Error(`${label} must be a string array`);
	}
	return [...new Set(value)].sort();
}

const SUBAGENT_V1_CAPABILITIES = new Set([
	"agent-invocation",
	"fresh-sessions",
	"interrupted-session-resume",
	"stateful-continuation",
	"node-skill-loading",
	"conditional-routing",
	"structured-output",
	"fanout-fanin",
	"durable-checkpoints",
	"observability",
	"machine-gated-review",
	"state-bound-proof",
]);

const APPROVAL_VERDICTS = new Set(["PASS", "APPROVE"]);
const TERMINAL_REVIEW_VERDICTS = new Set(["COMPLETE", "HUMAN_BLOCKED", "REVISE"]);
const HUMAN_REQUEST_KINDS = new Set(["authorization", "decision", "credential", "external-action"]);
const NON_HUMAN_BLOCKER =
	/\b(?:tests?|testing)\b.{0,32}\b(?:fail|failed|failing|failure|broken)\b|\b(?:fail|failed|failing|failure|broken)\b.{0,32}\b(?:tests?|testing)\b|\b(?:token|model[- ]?call|time|cost)\s+budget\b|\b(?:technical|implementation)\s+(?:difficulty|problem|issue)\b|\b(?:debug(?:ging)?|ordinary bug|code bug)\b|\u6d4b\u8bd5\u5931\u8d25|\u5931\u8d25\u7684\u6d4b\u8bd5|\u6280\u672f\u56f0\u96be|\u5b9e\u73b0\u56f0\u96be|\u8c03\u8bd5\u95ee\u9898|\u4ee3\u7801\u9519\u8bef|\u4ee4\u724c\u9884\u7b97|\u6a21\u578b\u8c03\u7528\u9884\u7b97|\u65f6\u95f4\u9884\u7b97/iu;
const SAFE_PROOF_LABEL = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const STATE_FIELD_NAME = /^[A-Za-z][A-Za-z0-9_-]*$/;
const SAFE_EXECUTABLE_COMMAND = /^[A-Za-z0-9][A-Za-z0-9._+-]*$/;

function resolvedExecutable(binding: ExecutableSource | ResolvedExecutableBinding): binding is ResolvedExecutableBinding {
	return Boolean(binding && typeof binding === "object" && !Array.isArray(binding) &&
		Object.keys(binding).sort().join(",") === "path,sha256" &&
		typeof (binding as ResolvedExecutableBinding).path === "string" &&
		typeof (binding as ResolvedExecutableBinding).sha256 === "string");
}

function requireResolvedExecutables(mapping: WorkflowMapping): Record<string, ResolvedExecutableBinding> {
	const bindings = mapping.proof?.executables ?? {};
	for (const [label, binding] of Object.entries(bindings)) {
		if (!resolvedExecutable(binding)) throw new Error(`portable executable ${label} has not been resolved`);
	}
	return bindings as Record<string, ResolvedExecutableBinding>;
}

function executableOnPath(command: string): string {
	if (!SAFE_EXECUTABLE_COMMAND.test(command) || command.includes("/") || command.includes("\\")) {
		throw new Error(`portable executable command is unsafe: ${JSON.stringify(command)}`);
	}
	for (const directory of (process.env.PATH ?? "").split(path.delimiter).filter(Boolean)) {
		const candidate = path.join(directory, command);
		try {
			fs.accessSync(candidate, fs.constants.X_OK);
			return fs.realpathSync(candidate);
		} catch {
			// Match ordinary PATH lookup: the first executable entry wins.
		}
	}
	throw new Error(`portable executable command is unavailable on PATH: ${command}`);
}

function nativeExecutableInAccountPath(command: string): string {
	if (!SAFE_EXECUTABLE_COMMAND.test(command) || command.includes("/") || command.includes("\\")) {
		throw new Error(`native executable command is unsafe: ${JSON.stringify(command)}`);
	}
	for (const directory of [path.join(osAccountHome(), ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"]) {
		const candidate = path.join(directory, command);
		try {
			fs.accessSync(candidate, fs.constants.X_OK);
			return fs.realpathSync(candidate);
		} catch {
			// Continue through the fixed account install search order.
		}
	}
	throw new Error(`native executable command is unavailable in fixed account locations: ${command}`);
}

function fixedProofHelper(command: "python3" | "git"): string {
	const candidates = command === "python3"
		? ["/usr/bin/python3", "/bin/python3"]
		: ["/usr/bin/git", "/bin/git"];
	for (const candidate of candidates) {
		try {
			fs.accessSync(candidate, fs.constants.X_OK);
			return fs.realpathSync(candidate);
		} catch {
			// Continue through fixed system locations only.
		}
	}
	throw new Error(`fixed proof helper is unavailable: ${command}`);
}

function inspectResolvedExecutable(root: string, candidate: string, label: string): ResolvedExecutableBinding {
	let real: string;
	try {
		real = fs.realpathSync(candidate);
	} catch (error) {
		throw new Error(`portable executable ${label} is unavailable: ${(error as Error).message}`);
	}
	if (!path.isAbsolute(real) || path.resolve(real) !== real) {
		throw new Error(`portable executable ${label} did not resolve to a canonical absolute path`);
	}
	const relative = path.relative(root, real);
	if (!relative || (!relative.startsWith("..") && !path.isAbsolute(relative))) {
		throw new Error(`portable executable ${label} must resolve outside the model-writable project root`);
	}
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(real, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		const stat = fs.fstatSync(descriptor);
		if (!stat.isFile() || (stat.mode & 0o111) === 0) throw new Error("not an executable regular file");
		return { path: real, sha256: sha256Bytes(fs.readFileSync(descriptor)) };
	} catch (error) {
		throw new Error(`portable executable ${label} failed byte preflight: ${(error as Error).message}`);
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
}

function resolvePortableExecutables(
	root: string,
	sourceMapping: WorkflowMapping,
	roles: RoleDocument,
	nativeMap: NativeMap,
	frozenBindings?: Record<string, ResolvedExecutableBinding>,
): { mapping: WorkflowMapping; nativeMap: NativeMap; executables: Record<string, ResolvedExecutableBinding> } {
	if (!sourceMapping.proof) return { mapping: sourceMapping, nativeMap, executables: {} };
	const sources = sourceMapping.proof.executables;
	if (!sources || typeof sources !== "object" || Array.isArray(sources)) {
		throw new Error("state-bound proof executables must be portable source declarations");
	}
	const runtimeNativeMap = structuredClone(nativeMap);
	const executables: Record<string, ResolvedExecutableBinding> = {};
	if (frozenBindings && !isDeepStrictEqual(Object.keys(frozenBindings).sort(), Object.keys(sources).sort())) {
		throw new Error("frozen executable closure labels disagree with the proof mapping");
	}
	for (const [label, raw] of Object.entries(sources)) {
		if (!SAFE_PROOF_LABEL.test(label) || !raw || typeof raw !== "object" || Array.isArray(raw) || resolvedExecutable(raw)) {
			throw new Error(`state-bound proof executable ${label} must be a portable source declaration`);
		}
		const source = raw as ExecutableSource;
		const frozen = frozenBindings?.[label];
		let candidate: string;
		if (source.source === "controller-runtime" && isDeepStrictEqual(Object.keys(source).sort(), ["source"])) {
			candidate = frozen?.path ?? process.execPath;
		} else if (source.source === "path" && isDeepStrictEqual(Object.keys(source).sort(), ["command", "source"]) &&
			typeof source.command === "string") {
			if (!SAFE_EXECUTABLE_COMMAND.test(source.command) || source.command.includes("/") || source.command.includes("\\")) {
				throw new Error(`portable executable command is unsafe: ${JSON.stringify(source.command)}`);
			}
			if (label === "python" || label === "git") {
				const expected = label === "python" ? "python3" : "git";
				if (source.command !== expected) {
					throw new Error(`state-bound proof executable ${label} must use fixed command ${expected}`);
				}
				const fixed = fixedProofHelper(expected);
				if (frozen && frozen.path !== fixed) {
					throw new Error(`frozen proof helper ${label} is not the fixed system executable ${fixed}`);
				}
				candidate = fixed;
			} else {
				candidate = frozen?.path ?? executableOnPath(source.command);
			}
		} else if (source.source === "native-role" && isDeepStrictEqual(Object.keys(source).sort(), ["role", "source"]) &&
			typeof source.role === "string" && STATE_FIELD_NAME.test(source.role)) {
			const role = resolveRole(roles, source.role);
			const prefixedProvider = Object.entries(runtimeNativeMap.modelPrefixes ?? {})
				.sort(([left], [right]) => right.length - left.length)
				.find(([prefix, key]) => role.model.startsWith(prefix) && runtimeNativeMap.providers[key])?.[1];
			const providerKey = runtimeNativeMap.providers[role.backend]
				? role.backend
				: runtimeNativeMap.providers[role.provider] ? role.provider : prefixedProvider;
			const provider = providerKey ? runtimeNativeMap.providers[providerKey] : undefined;
			if (!providerKey || !provider) {
				throw new Error(`portable executable ${label} role ${source.role} has no native backend ${role.backend}/${role.provider}`);
			}
			candidate = path.isAbsolute(provider.bin) ? provider.bin : nativeExecutableInAccountPath(provider.bin);
			const resolved = inspectResolvedExecutable(root, candidate, label);
			if (frozen && (resolved.path !== frozen.path || resolved.sha256 !== frozen.sha256)) {
				throw new Error(`frozen executable ${label} disagrees with its fixed native route`);
			}
			runtimeNativeMap.providers[providerKey] = { ...provider, bin: resolved.path };
			executables[label] = resolved;
			continue;
		} else {
			throw new Error(`state-bound proof executable ${label} has a malformed portable source declaration`);
		}
		const resolved = inspectResolvedExecutable(root, candidate, label);
		if (frozen && resolved.sha256 !== frozen.sha256) {
			throw new Error(`frozen executable ${label} digest disagrees with its current bytes`);
		}
		executables[label] = resolved;
	}
	for (const required of ["controller-runtime", "python", "git"]) {
		if (!executables[required]) throw new Error(`state-bound proof executable source closure is missing ${required}`);
	}
	const mapping = structuredClone(sourceMapping);
	mapping.proof!.executables = executables;
	return { mapping, nativeMap: runtimeNativeMap, executables };
}

interface WriterReviewPair {
	writer: string;
	reviewer: string;
}

function machineGated(design: WorkflowDesign): boolean {
	return design.governance?.reviewPolicy === "machine-gated";
}

function independentReview(design: WorkflowDesign): boolean {
	return design.governance?.reviewPolicy === "independent";
}

function stateBound(design: WorkflowDesign): boolean {
	return design.governance?.completionProof === "state-bound";
}

function designReachable(design: WorkflowDesign, source: string, target: string): boolean {
	const successors = new Map<string, string[]>();
	for (const node of design.nodes) successors.set(node.id, []);
	for (const edge of design.transitions) successors.get(edge.from)?.push(edge.to);
	const seen = new Set<string>();
	const stack = [...(successors.get(source) ?? [])];
	while (stack.length > 0) {
		const nodeId = stack.pop()!;
		if (nodeId === target) return true;
		if (seen.has(nodeId)) continue;
		seen.add(nodeId);
		stack.push(...(successors.get(nodeId) ?? []));
	}
	return false;
}

function decisionOutcome(edge: Transition, nodeId: string): string | undefined {
	if (!edge.when || typeof edge.when !== "object" || Array.isArray(edge.when)) return undefined;
	const keys = Object.keys(edge.when).sort();
	if (keys.length !== 2 || keys[0] !== "equals" || keys[1] !== "field") return undefined;
	if (edge.when.field !== `${nodeId}.verdict` || typeof edge.when.equals !== "string") return undefined;
	return edge.when.equals;
}

/**
 * Prove the fail-closed review invariant over the static DAG. A write invalidates
 * any earlier approval. Only a read-only decision edge whose exact verdict is
 * PASS or APPROVE restores approval. Every reachable success path must therefore
 * end with a fresh approval and no unreviewed writer.
 */
function validateMachineGatedDesign(design: WorkflowDesign): WriterReviewPair[] {
	if (!machineGated(design)) return [];
	if (
		design.governance?.malformedDecision !== undefined &&
		!["failure", "unresolved"].includes(design.governance.malformedDecision)
	) throw new Error("machine-gated malformedDecision must fail or remain unresolved");

	const byId = new Map(design.nodes.map(node => [node.id, node]));
	const edgesBySource = new Map<string, Transition[]>();
	for (const node of design.nodes) edgesBySource.set(node.id, []);
	for (const edge of design.transitions) edgesBySource.get(edge.from)!.push(edge);
	const reachableFrom = (source: string, target: string): boolean => {
		const reached = new Set<string>();
		const stack = [...(edgesBySource.get(source) ?? []).map(edge => edge.to)];
		while (stack.length > 0) {
			const nodeId = stack.pop()!;
			if (nodeId === target) return true;
			if (reached.has(nodeId)) continue;
			reached.add(nodeId);
			stack.push(...(edgesBySource.get(nodeId) ?? []).map(edge => edge.to));
		}
		return false;
	};
	const pairs = new Map<string, WriterReviewPair>();

	for (const node of design.nodes.filter(item => item.kind === "decision")) {
		const outcomes = node.outcomes ?? [];
		const edges = edgesBySource.get(node.id) ?? [];
		for (const edge of edges) {
			const outcome = decisionOutcome(edge, node.id);
			if (outcome === undefined) {
				throw new Error(`machine-gated decision ${node.id} has an unconditional or malformed verdict exit`);
			}
			if (!outcomes.includes(outcome)) {
				throw new Error(`machine-gated decision ${node.id} has an exit for undeclared outcome ${outcome}`);
			}
		}
		for (const outcome of outcomes) {
			const count = edges.filter(edge => decisionOutcome(edge, node.id) === outcome).length;
			if (count !== 1) {
				throw new Error(`machine-gated decision ${node.id} outcome ${outcome} must have exactly one conditional exit`);
			}
		}
		if (edges.length !== outcomes.length) {
			throw new Error(`machine-gated decision ${node.id} has verdict exits outside its declared outcomes`);
		}
		const hasApproval = outcomes.some(outcome => APPROVAL_VERDICTS.has(outcome));
		if (hasApproval && node.workspaceAccess !== "read") {
			throw new Error(`machine-gated approval decision ${node.id} must be read-only`);
		}
		const reviews = normalizeArray(node.reviews ?? [], `machine-gated decision ${node.id} reviews`);
		if (reviews.length !== (node.reviews ?? []).length) {
			throw new Error(`machine-gated decision ${node.id} declares duplicate review coverage`);
		}
		if (hasApproval && reviews.length === 0) {
			throw new Error(`machine-gated approval decision ${node.id} must declare the writers it reviews`);
		}
		if (!hasApproval && reviews.length > 0) {
			throw new Error(`machine-gated non-approval decision ${node.id} cannot declare review coverage`);
		}
		for (const writer of reviews) {
			const writerNode = byId.get(writer);
			if (!writerNode || writerNode.workspaceAccess !== "write") {
				throw new Error(`machine-gated reviewer ${node.id} references non-writer ${writer}`);
			}
			if (!reachableFrom(writer, node.id)) {
				throw new Error(`machine-gated reviewer ${node.id} references writer ${writer}, which is not its ancestor`);
			}
			if (writerNode.kind === "agent" || writerNode.kind === "decision") {
				pairs.set(`${writer}\u0000${node.id}`, { writer, reviewer: node.id });
			}
		}
	}

	const controllerStates = new Set<string>();
	let reachedSuccess = false;
	const STATE_LIMIT = 1_000_000;
	const reachableFromFrontier = (frontier: Set<string>, target: string, completed: Set<string>): boolean => {
		const reached = new Set<string>();
		const stack = [...frontier];
		while (stack.length > 0) {
			const nodeId = stack.pop()!;
			if (nodeId === target) return true;
			if (reached.has(nodeId) || completed.has(nodeId)) continue;
			reached.add(nodeId);
			for (const edge of edgesBySource.get(nodeId) ?? []) stack.push(edge.to);
		}
		return false;
	};
	const inspectExecution = (
		completedList: string[],
		frontierList: string[],
		pendingList: string[],
		approved: boolean,
	): void => {
		const completed = [...completedList].sort();
		const frontier = [...frontierList].sort();
		const pending = [...pendingList].sort();
		const key = `${completed.join("\u0000")}\u0001${frontier.join("\u0000")}\u0001${pending.join("\u0000")}\u0001${approved ? "1" : "0"}`;
		if (controllerStates.has(key)) return;
		if (controllerStates.size >= STATE_LIMIT) {
			throw new Error(`machine-gated review analysis exceeded its fail-closed ${STATE_LIMIT}-state bound`);
		}
		controllerStates.add(key);
		for (const nodeId of frontier) {
			const node = byId.get(nodeId)!;
			const nextCompleted = new Set(completed);
			nextCompleted.add(nodeId);
			const baseCandidates = new Set(frontier.filter(item => item !== nodeId));
			const basePending = new Set(pending);
			let baseApproved = approved;
			if (node.workspaceAccess === "write") {
				basePending.add(node.id);
				baseApproved = false;
			}
			if (node.kind === "terminal") {
				if (node.outcome === "success" || design.terminals.success === node.id) {
					reachedSuccess = true;
					if (!baseApproved || basePending.size !== 0 || baseCandidates.size !== 0) {
						throw new Error(
							"machine-gated success path reaches success without a fresh PASS/APPROVE review " +
							"(unreviewed writers or concurrent frontier work)",
						);
					}
				}
				continue;
			}
			const variants: Array<{ targets: string[]; pending: Set<string>; approved: boolean }> = [];
			if (node.kind === "decision") {
				for (const outcome of node.outcomes ?? []) {
					const targets = (edgesBySource.get(node.id) ?? [])
						.filter(edge => decisionOutcome(edge, node.id) === outcome)
						.map(edge => edge.to);
					const outcomePending = new Set(basePending);
					let outcomeApproved = false;
					if (APPROVAL_VERDICTS.has(outcome)) {
						for (const writer of node.reviews ?? []) {
							if (!outcomePending.has(writer) && !nextCompleted.has(writer) &&
								reachableFromFrontier(baseCandidates, writer, nextCompleted)) {
								throw new Error(`machine-gated reviewer ${node.id} can approve before reviewed writer ${writer} is pending`);
							}
							outcomePending.delete(writer);
						}
						outcomeApproved = true;
					}
					variants.push({ targets, pending: outcomePending, approved: outcomeApproved });
				}
			} else {
				variants.push({
					targets: [...new Set((edgesBySource.get(node.id) ?? []).map(edge => edge.to))],
					pending: basePending,
					approved: baseApproved,
				});
			}
			for (const variant of variants) {
				const candidates = new Set(baseCandidates);
				for (const target of variant.targets) if (!nextCompleted.has(target)) candidates.add(target);
				const runnableFrontier = [...candidates].filter(target =>
					(byId.get(target)?.waitFor ?? []).every(parent => nextCompleted.has(parent)),
				).sort();
				inspectExecution([...nextCompleted], runnableFrontier, [...variant.pending], variant.approved);
			}
		}
	};
	inspectExecution([], [design.entry], [], false);
	if (!reachedSuccess) throw new Error("machine-gated workflow has no reachable success terminal");
	return [...pairs.values()].sort((a, b) => `${a.writer}/${a.reviewer}`.localeCompare(`${b.writer}/${b.reviewer}`));
}

/**
 * Exact controller-semantic upper bound for a static DAG.  The memoized state is
 * the pair (completed, runnable frontier), which preserves fan-out, shared joins,
 * early terminals, and ordering-sensitive waitFor activation without enumerating
 * the Cartesian product of every syntactic decision in the design.  A validated
 * machine-gated decision follows exactly one verdict edge; predicates on other
 * nodes are conservatively opened together.
 */
export function maxModelActivations(design: WorkflowDesign): number {
	const nodes = new Map(design.nodes.map(node => [node.id, node]));
	const edges = new Map<string, Transition[]>();
	for (const node of design.nodes) edges.set(node.id, []);
	for (const edge of design.transitions) edges.get(edge.from)?.push(edge);
	const memo = new Map<string, number>();
	const STATE_LIMIT = 1_000_000;
	const frontierAfter = (completed: Set<string>, candidates: Set<string>): string[] =>
		[...candidates].filter(nodeId => {
			if (completed.has(nodeId)) return false;
			const node = nodes.get(nodeId);
			return Boolean(node) && (node!.waitFor ?? []).every(parent => completed.has(parent));
		}).sort();
	const edgeVariants = (node: DesignNode): string[][] => {
		const outgoing = edges.get(node.id) ?? [];
		if (!machineGated(design) || node.kind !== "decision") {
			return [[...new Set(outgoing.map(edge => edge.to))].sort()];
		}
		return (node.outcomes ?? []).map(outcome =>
			[...new Set(outgoing.filter(edge => decisionOutcome(edge, node.id) === outcome).map(edge => edge.to))].sort(),
		);
	};
	const solve = (completedList: string[], frontierList: string[]): number => {
		const completed = [...completedList].sort();
		const frontier = [...frontierList].sort();
		const key = `${completed.join("\u0000")}\u0001${frontier.join("\u0000")}`;
		const cached = memo.get(key);
		if (cached !== undefined) return cached;
		if (memo.size >= STATE_LIMIT) {
			throw new Error(`model-activation analysis exceeded its fail-closed ${STATE_LIMIT}-state bound`);
		}
		memo.set(key, 0);
		let maximum = 0;
		for (const nodeId of frontier) {
			const node = nodes.get(nodeId);
			if (!node) throw new Error(`model-activation analysis reached unknown node ${nodeId}`);
			const weight = node.kind === "agent" || node.kind === "decision" ? 1 : 0;
			if (node.kind === "terminal") {
				maximum = Math.max(maximum, weight);
				continue;
			}
			for (const targets of edgeVariants(node)) {
				const nextCompleted = new Set(completed);
				nextCompleted.add(nodeId);
				const candidates = new Set(frontier.filter(item => item !== nodeId));
				for (const target of targets) if (!nextCompleted.has(target)) candidates.add(target);
				const nextFrontier = frontierAfter(nextCompleted, candidates);
				maximum = Math.max(maximum, weight + solve([...nextCompleted], nextFrontier));
			}
		}
		memo.set(key, maximum);
		return maximum;
	};
	return solve([], [design.entry]);
}

function validateDesign(design: WorkflowDesign): void {
	if (design.schema !== "workflow-design/v1" || !design.id || !design.entry) {
		throw new Error("unsupported or malformed workflow design");
	}
	const reviewPolicy = design.governance?.reviewPolicy;
	if (reviewPolicy === "none") {
		throw new Error(
			"workflow governance.reviewPolicy 'none' is no longer supported; migrate to independent or machine-gated",
		);
	}
	if (reviewPolicy !== "independent" && reviewPolicy !== "machine-gated") {
		throw new Error("workflow governance.reviewPolicy must be independent or machine-gated");
	}
	if (!Array.isArray(design.nodes) || !Array.isArray(design.transitions)) {
		throw new Error("workflow design needs nodes and transitions");
	}
	const requiredCapabilities = normalizeArray(design.requiredCapabilities ?? [], "requiredCapabilities");
	for (const capability of requiredCapabilities) {
		if (!SUBAGENT_V1_CAPABILITIES.has(capability)) {
			throw new Error(`subagent-workflow/v1 does not support required capability ${capability}`);
		}
	}
	const stateNames = new Set<string>();
	for (const declaration of design.state ?? []) {
		if (!declaration?.name || stateNames.has(declaration.name)) throw new Error(`duplicate or invalid state field ${declaration?.name}`);
		if (!["string", "number", "boolean", "array", "object"].includes(declaration.type)) {
			throw new Error(`state field ${declaration.name} has unsupported type ${declaration.type}`);
		}
		stateNames.add(declaration.name);
	}
	const ids = new Set<string>();
	for (const node of design.nodes) {
		if (!node?.id || !/^[a-z][a-z0-9-]*$/.test(node.id) || ids.has(node.id)) {
			throw new Error(`duplicate or path-unsafe node id: ${node?.id}`);
		}
		if (!["script", "agent", "decision", "reducer", "terminal"].includes(node.kind)) {
			throw new Error(`node ${node.id} has unsupported kind ${node.kind}`);
		}
		if (node.workspaceAccess !== undefined && !["read", "write"].includes(node.workspaceAccess)) {
			throw new Error(`node ${node.id} has unsupported workspace access ${node.workspaceAccess}`);
		}
		if (node.session !== undefined && !["fresh", "state-mediated"].includes(node.session)) {
			throw new Error(`subagent-workflow/v1 supports only fresh or state-mediated sessions; node ${node.id} declares ${node.session}`);
		}
		if (node.reads !== undefined) normalizeArray(node.reads, `node ${node.id} reads`);
		if (node.writes !== undefined) normalizeArray(node.writes, `node ${node.id} writes`);
		if (node.reviews !== undefined) {
			if (node.kind !== "decision") throw new Error(`non-decision node ${node.id} cannot declare review coverage`);
			const reviews = normalizeArray(node.reviews, `node ${node.id} reviews`);
			if (reviews.length !== node.reviews.length) throw new Error(`decision node ${node.id} declares duplicate review coverage`);
		}
		if (node.kind === "decision") {
			const outcomes = normalizeArray(node.outcomes ?? [], `decision node ${node.id} outcomes`);
			if (outcomes.length === 0) throw new Error(`decision node ${node.id} must declare at least one outcome`);
			if (outcomes.length !== node.outcomes!.length) throw new Error(`decision node ${node.id} declares duplicate outcomes`);
		}
		if (node.kind === "terminal" && !["success", "unresolved", "failure"].includes(node.outcome ?? "")) {
			throw new Error(`terminal node ${node.id} has unsupported outcome ${node.outcome}`);
		}
		if (node.skills !== undefined) {
			if (!node.skills || typeof node.skills !== "object" || Array.isArray(node.skills)) {
				throw new Error(`node ${node.id} has malformed skills`);
			}
			normalizeArray(node.skills.required ?? [], `node ${node.id} required skills`);
			normalizeArray(node.skills.onDemand ?? [], `node ${node.id} on-demand skills`);
		}
		for (const field of node.writes ?? []) {
			if (!stateNames.has(field)) throw new Error(`node ${node.id} writes undeclared state field ${field}`);
		}
		ids.add(node.id);
	}
	if (!ids.has(design.entry)) throw new Error("workflow entry node is missing");
	if (!design.terminals || typeof design.terminals !== "object" || Array.isArray(design.terminals) ||
		Object.keys(design.terminals).length === 0) {
		throw new Error("workflow design has no terminal mapping");
	}
	for (const [outcome, nodeId] of Object.entries(design.terminals)) {
		if (!["success", "unresolved", "failure"].includes(outcome)) {
			throw new Error(`workflow design has unsupported terminal outcome ${outcome}`);
		}
		const terminal = design.nodes.find(node => node.id === nodeId);
		if (!terminal || terminal.kind !== "terminal" || terminal.outcome !== outcome) {
			throw new Error(`terminal mapping ${outcome} disagrees with node ${nodeId}`);
		}
	}
	for (const transition of design.transitions) {
		if (!ids.has(transition.from) || !ids.has(transition.to)) {
			throw new Error(`transition names an unknown node: ${transition.from} -> ${transition.to}`);
		}
	}
	for (const node of design.nodes) {
		for (const field of node.reads ?? []) {
			if (!field.includes(".") && !stateNames.has(field)) throw new Error(`node ${node.id} reads undeclared state field ${field}`);
			if (field.includes(".")) {
				const [source, outputField, ...rest] = field.split(".");
				if (!source || !outputField || rest.length > 0 || !ids.has(source)) {
					throw new Error(`node ${node.id} has invalid output read ${field}`);
				}
			}
		}
		for (const dependency of node.waitFor ?? []) {
			if (!ids.has(dependency)) throw new Error(`node ${node.id} waits for unknown node ${dependency}`);
		}
	}
	const successors = new Map<string, string[]>();
	for (const nodeId of ids) successors.set(nodeId, []);
	for (const transition of design.transitions) successors.get(transition.from)!.push(transition.to);
	const visiting = new Set<string>();
	const visited = new Set<string>();
	const visit = (nodeId: string): void => {
		if (visiting.has(nodeId)) throw new Error("subagent-workflow/v1 does not support cyclic graphs");
		if (visited.has(nodeId)) return;
		visiting.add(nodeId);
		for (const target of successors.get(nodeId) ?? []) visit(target);
		visiting.delete(nodeId);
		visited.add(nodeId);
	};
	for (const nodeId of ids) visit(nodeId);
	validateMachineGatedDesign(design);
}

function validateDeterministicAllPolicy(mapping: WorkflowMapping, design: WorkflowDesign): void {
	const scopes = mapping.scheduler.workspaceWriteScopes;
	if (mapping.scheduler.mode !== "deterministic-all") {
		if (scopes !== undefined) throw new Error("workspaceWriteScopes is valid only for deterministic-all scheduling");
		return;
	}
	if (mapping.scheduler.maxCalls !== 0) throw new Error("deterministic-all scheduler mode requires maxCalls=0");
	if (!scopes || typeof scopes !== "object" || Array.isArray(scopes)) {
		throw new Error("deterministic-all scheduler mode requires workspaceWriteScopes");
	}
	const nodes = new Map(design.nodes.map(node => [node.id, node]));
	const normalizedScopes = new Map<string, string[]>();
	for (const [nodeId, raw] of Object.entries(scopes)) {
		const node = nodes.get(nodeId);
		if (!node || node.workspaceAccess !== "write") {
			throw new Error(`deterministic-all workspaceWriteScopes names non-writer ${nodeId}`);
		}
		const values = normalizeArray(raw, `workspaceWriteScopes.${nodeId}`);
		if (values.length !== raw.length || values.length === 0) {
			throw new Error(`workspaceWriteScopes.${nodeId} must be a non-empty duplicate-free array`);
		}
		for (const value of values) {
			if (value.includes("\\") || path.posix.isAbsolute(value) || path.posix.normalize(value) !== value || value === "." || value.startsWith("../")) {
				throw new Error(`workspaceWriteScopes.${nodeId} contains unsafe project-relative scope ${value}`);
			}
		}
		normalizedScopes.set(nodeId, values);
	}
	for (const node of design.nodes.filter(item => item.workspaceAccess === "write")) {
		if (!normalizedScopes.has(node.id)) throw new Error(`deterministic-all workspace writer ${node.id} has no declared write scope`);
	}

	const successors = new Map<string, string[]>();
	for (const node of design.nodes) successors.set(node.id, []);
	for (const edge of design.transitions) successors.get(edge.from)!.push(edge.to);
	const reachability = new Map<string, Set<string>>();
	const reachableFrom = (source: string): Set<string> => {
		const cached = reachability.get(source);
		if (cached) return cached;
		const reached = new Set<string>();
		const stack = [...(successors.get(source) ?? [])];
		while (stack.length > 0) {
			const target = stack.pop()!;
			if (reached.has(target)) continue;
			reached.add(target);
			stack.push(...(successors.get(target) ?? []));
		}
		reachability.set(source, reached);
		return reached;
	};
	const overlaps = (left: string, right: string): boolean =>
		left === right || left.startsWith(`${right}/`) || right.startsWith(`${left}/`);
	for (let leftIndex = 0; leftIndex < design.nodes.length; leftIndex += 1) {
		const left = design.nodes[leftIndex];
		for (let rightIndex = leftIndex + 1; rightIndex < design.nodes.length; rightIndex += 1) {
			const right = design.nodes[rightIndex];
			if (reachableFrom(left.id).has(right.id) || reachableFrom(right.id).has(left.id)) continue;
			const stateOverlap = (left.writes ?? []).find(field => (right.writes ?? []).includes(field));
			if (stateOverlap) {
				throw new Error(`deterministic-all incomparable nodes ${left.id} and ${right.id} overlap state write ${stateOverlap}`);
			}
			if (left.workspaceAccess !== "write" || right.workspaceAccess !== "write") continue;
			for (const leftScope of normalizedScopes.get(left.id)!) {
				for (const rightScope of normalizedScopes.get(right.id)!) {
					if (overlaps(leftScope, rightScope)) {
						throw new Error(
							`deterministic-all incomparable writers ${left.id} and ${right.id} overlap workspace scope ${leftScope}/${rightScope}`,
						);
					}
				}
			}
		}
	}
}

function validateProofMapping(mapping: WorkflowMapping, design: WorkflowDesign): void {
	const completionProof = design.governance?.completionProof ?? "none";
	if (completionProof !== "none" && completionProof !== "state-bound") {
		throw new Error(`subagent-workflow/v1 does not support completion proof ${completionProof}`);
	}
	if (completionProof === "none") {
		if (mapping.proof !== undefined) throw new Error("proof mapping is forbidden when completionProof is none");
		return;
	}
	if (mapping.inputPath === undefined) throw new Error("state-bound completion requires an explicit workflow input path");
	if (!machineGated(design)) throw new Error("state-bound completion requires machine-gated review");
	const proof = mapping.proof;
	if (!proof || typeof proof !== "object" || Array.isArray(proof)) {
		throw new Error("state-bound completion requires a complete proof mapping");
	}
	const requiredKeys = [
		"actionPath", "currentStateTimeoutSeconds", "evidencePathField", "evidenceProducerNodes", "executables", "finalReviewNodes", "helperPath",
		"itemIdField", "resources", "resourcesManifestPath",
	].sort();
	const actualKeys = Object.keys(proof).sort();
	if (requiredKeys.some(key => !actualKeys.includes(key)) || actualKeys.some(key => ![...requiredKeys, "evidenceDigestField"].includes(key))) {
		throw new Error("state-bound proof mapping has missing or unsupported fields");
	}
	if (!STATE_FIELD_NAME.test(proof.itemIdField) || !STATE_FIELD_NAME.test(proof.evidencePathField)) {
		throw new Error("state-bound proof itemIdField and evidencePathField must be safe state names");
	}
	const declarations = new Map((design.state ?? []).map(item => [item.name, item.type]));
	if (declarations.get(proof.itemIdField) !== "string" || declarations.get(proof.evidencePathField) !== "string") {
		throw new Error("state-bound proof item and evidence fields must be declared string state");
	}
	if (proof.evidenceDigestField !== undefined &&
		(!STATE_FIELD_NAME.test(proof.evidenceDigestField) || declarations.get(proof.evidenceDigestField) !== "string")) {
		throw new Error("state-bound proof evidenceDigestField must name declared string state");
	}
	for (const [label, value] of [
		["actionPath", proof.actionPath], ["helperPath", proof.helperPath], ["resourcesManifestPath", proof.resourcesManifestPath],
	] as const) {
		if (typeof value !== "string" || !value || path.isAbsolute(value)) {
			throw new Error(`state-bound proof ${label} must be a project-relative path`);
		}
	}
	if (!proof.executables || typeof proof.executables !== "object" || Array.isArray(proof.executables)) {
		throw new Error("state-bound proof executables must be a path and SHA-256 map");
	}
	for (const required of ["controller-runtime", "python", "git"]) {
		if (!proof.executables[required]) throw new Error(`state-bound proof executable closure is missing ${required}`);
	}
	for (const [label, binding] of Object.entries(proof.executables)) {
		if (!resolvedExecutable(binding) || !SAFE_PROOF_LABEL.test(label) || !binding || typeof binding !== "object" || Array.isArray(binding) ||
			!isDeepStrictEqual(Object.keys(binding).sort(), ["path", "sha256"]) ||
			typeof binding.path !== "string" || !path.isAbsolute(binding.path) || path.resolve(binding.path) !== binding.path ||
			typeof binding.sha256 !== "string" || !/^[0-9a-f]{64}$/.test(binding.sha256)) {
			throw new Error(`state-bound proof executable ${label} requires an absolute path and SHA-256`);
		}
	}
	if (!proof.resources || typeof proof.resources !== "object" || Array.isArray(proof.resources)) {
		throw new Error("state-bound proof resources must be a path map");
	}
	const reservedLabels = new Set([
		"controller", "launch-resolver", "launch-policy-resolver", "launch-catalog", "launch-policy-validator",
		"launch-project-paths", "native-core", "proof-module", "design", "mapping", "roles", "native-map", "input",
		"resources", "helper",
	]);
	for (const [label, resourcePath] of Object.entries(proof.resources)) {
		if (!SAFE_PROOF_LABEL.test(label) || reservedLabels.has(label) || label.startsWith("skill-")) {
			throw new Error(`state-bound proof resource label is unsafe or reserved: ${label}`);
		}
		if (typeof resourcePath !== "string" || !resourcePath || path.isAbsolute(resourcePath)) {
			throw new Error(`state-bound proof resource ${label} must be a project-relative path`);
		}
	}
	const reviews = normalizeArray(proof.finalReviewNodes, "state-bound proof finalReviewNodes");
	if (reviews.length === 0 || reviews.length !== proof.finalReviewNodes.length) {
		throw new Error("state-bound proof finalReviewNodes must be a non-empty duplicate-free array");
	}
	for (const nodeId of reviews) {
		const node = design.nodes.find(item => item.id === nodeId);
		if (!node || node.kind !== "decision" || !(node.outcomes ?? []).some(outcome => APPROVAL_VERDICTS.has(outcome))) {
			throw new Error(`state-bound proof final review ${nodeId} is not an approval decision`);
		}
		const handler = mapping.handlers[nodeId];
		const markers = handler?.type === "decision"
			? [...handler.prompt.matchAll(/\{\{review\.commitment\}\}/gu)]
			: [];
		if (markers.length !== 1) {
			throw new Error(`state-bound final review ${nodeId} prompt must contain exactly one {{review.commitment}} marker`);
		}
	}
	const producers = normalizeArray(proof.evidenceProducerNodes, "state-bound proof evidenceProducerNodes");
	if (producers.length === 0 || producers.length !== proof.evidenceProducerNodes.length) {
		throw new Error("state-bound proof evidenceProducerNodes must be a non-empty duplicate-free array");
	}
	for (const nodeId of producers) {
		const node = design.nodes.find(item => item.id === nodeId);
		const handler = mapping.handlers[nodeId];
		if (!node || handler?.type !== "command" || node.kind !== "script") {
			throw new Error(`state-bound evidence producer ${nodeId} must be a deterministic script command`);
		}
		const runWrites = handler.runWrites ?? [];
		const expectedHelper = `{{controller.projectRoot}}/${proof.helperPath}`;
		const optionValue = (flag: string): string | undefined => {
			const index = handler.argv.indexOf(flag);
			return index >= 0 ? handler.argv[index + 1] : undefined;
		};
		if (handler.argv[0] !== "{{executable.controller-runtime.path}}" || handler.argv[1] !== proof.actionPath ||
			handler.argv[2] !== "evidence" || optionValue("--project-root") !== "{{controller.projectRoot}}" ||
			optionValue("--run-root") !== "{{controller.runRoot}}" || optionValue("--helper") !== expectedHelper ||
			runWrites.length !== 1 || optionValue("--output") !== runWrites[0]) {
			throw new Error(`state-bound evidence producer ${nodeId} does not use the audited action/helper/output schema`);
		}
		if (!reviews.some(review => designReachable(design, nodeId, review))) {
			throw new Error(`state-bound evidence producer ${nodeId} is not an ancestor of any final review`);
		}
	}
	for (const review of reviews) {
		if (!producers.some(producer => designReachable(design, producer, review))) {
			throw new Error(`state-bound final review ${review} has no audited evidence producer ancestor`);
		}
	}
	if (!Number.isSafeInteger(proof.currentStateTimeoutSeconds) || proof.currentStateTimeoutSeconds <= 0) {
		throw new Error("state-bound proof currentStateTimeoutSeconds must be a positive integer");
	}
}

function validateProofExecutables(root: string, mapping: WorkflowMapping): void {
	if (!mapping.proof) return;
	const bindings = requireResolvedExecutables(mapping);
	const runtime = bindings["controller-runtime"];
	if (runtime.path !== fs.realpathSync(process.execPath)) {
		throw new Error("state-bound controller-runtime must equal the canonical current process executable");
	}
	for (const [label, binding] of Object.entries(bindings)) {
		const executable = binding.path;
		const digest = binding.sha256;
		let real: string;
		try {
			real = fs.realpathSync(executable);
		} catch (error) {
			throw new Error(`state-bound ${label} executable is unavailable: ${(error as Error).message}`);
		}
		if (real !== executable) throw new Error(`state-bound ${label} executable must be canonical and non-symlinked`);
		const relative = path.relative(root, real);
		if (!relative || (!relative.startsWith("..") && !path.isAbsolute(relative))) {
			throw new Error(`state-bound ${label} executable must be outside the model-writable project root`);
		}
		let descriptor: number | undefined;
		try {
			descriptor = fs.openSync(real, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
			const stat = fs.fstatSync(descriptor);
			if (!stat.isFile() || (stat.mode & 0o111) === 0) throw new Error("not an executable regular file");
			if (sha256Bytes(fs.readFileSync(descriptor)) !== digest) throw new Error("SHA-256 mismatch");
		} catch (error) {
			throw new Error(`state-bound ${label} executable failed byte preflight: ${(error as Error).message}`);
		} finally {
			if (descriptor !== undefined) fs.closeSync(descriptor);
		}
	}
}

function bindStateBoundNativeExecutables(mapping: WorkflowMapping, nativeMap: NativeMap): NativeMap {
	if (!mapping.proof) return nativeMap;
	const closure = [...new Set(Object.values(requireResolvedExecutables(mapping)).map(binding => binding.path))];
	const providers = Object.fromEntries(Object.entries(nativeMap.providers).map(([name, entry]) => {
		let candidates: string[];
		if (path.isAbsolute(entry.bin)) {
			let canonical = entry.bin;
			try { canonical = fs.realpathSync(entry.bin); } catch { /* route validation reports unavailable */ }
			candidates = closure.filter(executable => executable === canonical);
		} else {
			const discovered = new Set<string>();
			for (const directory of (process.env.PATH ?? "").split(path.delimiter).filter(Boolean)) {
				const candidate = path.join(directory, entry.bin);
				try {
					fs.accessSync(candidate, fs.constants.X_OK);
					const canonical = fs.realpathSync(candidate);
					if (closure.includes(canonical)) discovered.add(canonical);
				} catch {
					// Continue through PATH; actual spawn uses the canonical replacement below.
				}
			}
			candidates = [...discovered];
		}
		if (candidates.length > 1) throw new Error(`native CLI ${entry.bin} is ambiguous in the state-bound executable closure`);
		return [name, candidates.length === 1 ? { ...entry, bin: candidates[0] } : entry];
	}));
	return { ...nativeMap, providers };
}

function validateMapping(mapping: WorkflowMapping, design: WorkflowDesign): void {
	if (mapping.schema !== "subagent-workflow/v1") throw new Error("unsupported subagent mapping schema");
	if (!mapping.scheduler || !["ambiguous-only", "every-frontier", "deterministic-all"].includes(mapping.scheduler.mode)) {
		throw new Error("subagent mapping has no valid scheduler mode");
	}
	if (typeof mapping.designPath !== "string" || !mapping.designPath || !/^[0-9a-f]{64}$/.test(mapping.designSha256)) {
		throw new Error("subagent mapping has an invalid design path or digest");
	}
	if (typeof mapping.inputPath !== "string" || !mapping.inputPath || typeof mapping.scheduler.role !== "string" || !mapping.scheduler.role) {
		throw new Error("subagent mapping has an invalid input path or scheduler role");
	}
	if (typeof mapping.reasoningPosture !== "string" || !mapping.reasoningPosture.trim()) {
		throw new Error("subagent mapping must declare an explicit reasoningPosture");
	}
	if (!mapping.handlers || typeof mapping.handlers !== "object" || Array.isArray(mapping.handlers)) {
		throw new Error("subagent mapping handlers must be an object");
	}
	if (
		mapping.scheduler.timeoutSeconds !== undefined &&
		(typeof mapping.scheduler.timeoutSeconds !== "number" || !Number.isFinite(mapping.scheduler.timeoutSeconds) || mapping.scheduler.timeoutSeconds <= 0)
	) throw new Error("scheduler.timeoutSeconds must be positive when declared");
	if (!Number.isInteger(mapping.scheduler.maxCalls) || mapping.scheduler.maxCalls < 0) {
		throw new Error("scheduler.maxCalls must be a non-negative integer");
	}
	validateDeterministicAllPolicy(mapping, design);
	if (mapping.scheduler.maxCalls > 0 && mapping.scheduler.timeoutSeconds === undefined) {
		throw new Error("scheduler.timeoutSeconds is required when scheduler.maxCalls is positive");
	}
	validateProofMapping(mapping, design);
	if (stateBound(design)) {
		for (const [nodeId, handler] of Object.entries(mapping.handlers)) {
			if (handler.type !== "command") continue;
			const match = /^\{\{executable\.([A-Za-z0-9][A-Za-z0-9._-]*)\.path\}\}$/u.exec(handler.argv[0]);
			if (!match || !mapping.proof!.executables[match[1]]) {
				throw new Error(`state-bound command ${nodeId} executable is outside the proof executable closure`);
			}
		}
	}
	const declaredState = new Set((design.state ?? []).map(item => item.name));
	const schedulerReads = normalizeArray(mapping.scheduler.reads ?? [], "scheduler.reads");
	for (const field of schedulerReads) {
		if (!declaredState.has(field)) throw new Error(`scheduler reads undeclared state field ${field}`);
	}
	const nodeIds = new Set(design.nodes.map(node => node.id));
	const globallyOwnedRunWrites: string[] = [];
	for (const node of design.nodes) {
		const handler = mapping.handlers[node.id];
		if (!handler) throw new Error(`mapping has no handler for node ${node.id}`);
		if (handler.type === "input") {
			if (!handler.bindings || typeof handler.bindings !== "object" || Array.isArray(handler.bindings)) {
				throw new Error(`input handler ${node.id} has invalid bindings`);
			}
			for (const value of Object.values(handler.bindings)) {
				if (typeof value !== "string" && (!value || typeof value !== "object" || Array.isArray(value) || !("const" in value))) {
					throw new Error(`input handler ${node.id} has an invalid binding`);
				}
			}
		} else if (handler.type === "agent" || handler.type === "decision") {
			if (typeof handler.prompt !== "string" || !handler.prompt.trim()) throw new Error(`${handler.type} handler ${node.id} has no prompt`);
			if (/\{\{controller\./u.test(handler.prompt)) {
				throw new Error(`${handler.type} handler ${node.id} cannot read controller runtime values`);
			}
		} else if (handler.type === "command") {
			if (!Array.isArray(handler.argv) || handler.argv.length === 0 || handler.argv.some(value => typeof value !== "string" || !value)) {
				throw new Error(`command handler ${node.id} has invalid argv`);
			}
			const runWrites = normalizeArray(handler.runWrites ?? [], `command handler ${node.id} runWrites`);
			if (runWrites.length !== (handler.runWrites ?? []).length) throw new Error(`command handler ${node.id} runWrites contains duplicates`);
			const reserved = new Set([
				"state.json", "events.jsonl", "scheduler-decisions.jsonl", "machine-reviews.jsonl", "native-calls.jsonl",
				"native-attempts.jsonl", "native-heartbeats.jsonl",
				"frozen-artifacts.json", "state-bound-candidate.json", "result.json",
			]);
			for (const value of runWrites) {
				if (value.includes("\\") || path.posix.isAbsolute(value) || path.posix.normalize(value) !== value ||
					value === "." || value.startsWith("../") || reserved.has(value) ||
					["native-responses/", "command-responses/", "scheduler-responses/", "launch-receipts/", "launch-bindings/"]
						.some(prefix => value.startsWith(prefix))) {
					throw new Error(`command handler ${node.id} has unsafe or controller-reserved runWrite ${value}`);
				}
				for (const previous of globallyOwnedRunWrites) {
					if (value === previous || value.startsWith(`${previous}/`) || previous.startsWith(`${value}/`)) {
						throw new Error(`command handler ${node.id} runWrite overlaps another command output: ${value}/${previous}`);
					}
				}
				globallyOwnedRunWrites.push(value);
			}
		} else if (handler.type === "template") {
			if (!handler.values || typeof handler.values !== "object" || Array.isArray(handler.values) ||
				Object.values(handler.values).some(value => typeof value !== "string")) {
				throw new Error(`template handler ${node.id} has invalid values`);
			}
		} else if (handler.type === "increment") {
			if (typeof handler.field !== "string" || !handler.field ||
				(handler.by !== undefined && (typeof handler.by !== "number" || !Number.isFinite(handler.by))) ||
				(handler.set !== undefined && (!handler.set || typeof handler.set !== "object" || Array.isArray(handler.set)))) {
				throw new Error(`increment handler ${node.id} is malformed`);
			}
		} else if (handler.type !== "terminal") {
			throw new Error(`mapping handler ${node.id} has unsupported type`);
		}
		if (handler.type === "command" || handler.type === "template") {
			const templates = handler.type === "command" ? handler.argv : Object.values(handler.values);
			for (const template of templates) {
				for (const match of template.matchAll(/\{\{(executable\.[A-Za-z0-9_.-]+)\}\}/gu)) {
					if (!/^executable\.[A-Za-z0-9][A-Za-z0-9._-]*\.(?:path|sha256)$/u.test(match[1])) {
						throw new Error(`${handler.type} handler ${node.id} references malformed executable value ${match[1]}`);
					}
				}
				for (const match of template.matchAll(/\{\{(controller\.[A-Za-z0-9_.-]+)\}\}/g)) {
					if (!["controller.projectRoot", "controller.runRoot", "controller.runId"].includes(match[1])) {
						throw new Error(`${handler.type} handler ${node.id} references unsupported controller value ${match[1]}`);
					}
				}
				for (const match of template.matchAll(/\{\{(executable\.([A-Za-z0-9][A-Za-z0-9._-]*)\.(?:path|sha256))\}\}/gu)) {
					if (handler.type !== "command" || !mapping.proof?.executables[match[2]]) {
						throw new Error(`${handler.type} handler ${node.id} references undeclared executable value ${match[1]}`);
					}
				}
			}
		}
		if (["agent", "decision", "command"].includes(handler.type)) {
			if (typeof node.timeoutSeconds !== "number" || !Number.isFinite(node.timeoutSeconds) || node.timeoutSeconds <= 0) {
				throw new Error(`node ${node.id} must declare a positive timeoutSeconds`);
			}
		}
		if (node.kind === "agent" && handler.type !== "agent") {
			throw new Error(`agent node ${node.id} must retain an agent handler`);
		}
		if (node.kind === "decision" && handler.type !== "decision") {
			throw new Error(`decision node ${node.id} must retain a decision handler`);
		}
		if (node.kind === "terminal" && handler.type !== "terminal") {
			throw new Error(`terminal node ${node.id} must retain a terminal handler`);
		}
		if (["script", "reducer"].includes(node.kind) && ["agent", "decision", "terminal"].includes(handler.type)) {
			throw new Error(`deterministic node ${node.id} has an incompatible ${handler.type} handler`);
		}
	}
	for (const mapped of Object.keys(mapping.handlers)) {
		if (!nodeIds.has(mapped)) throw new Error(`mapping contains unknown node ${mapped}`);
	}
	const activationBound = maxModelActivations(design);
	const maxModelCalls = Number(design.budgets?.maxModelCalls ?? 0);
	if (activationBound + mapping.scheduler.maxCalls > maxModelCalls) {
		throw new Error(
			`design maxModelCalls cannot cover every model-node activation on all possible paths plus scheduler reservations ` +
			`(${activationBound} activation bound + ${mapping.scheduler.maxCalls} scheduler reservations; static model floor plus scheduler cap)`,
		);
	}
}

function resolveRole(document: RoleDocument, role: string): RoleSpec {
	if (document.schema !== "workflow-model-roles/v3" || !document.roles || typeof document.roles !== "object") {
		throw new Error("unsupported workflow role config; workflow-model-roles/v3 is required");
	}
	const stored = (document.roles as Record<string, unknown>)[role];
	if (!stored || typeof stored !== "object" || Array.isArray(stored)) throw new Error(`unknown workflow role ${role}`);
	const item = stored as Record<string, unknown>;
	const backend = String(item.backend ?? "");
	const provider = String(item.provider ?? (backend === "claude" ? "anthropic" : backend));
	const model = String(item.model ?? "");
	if (!backend || !provider || !model) throw new Error(`workflow role ${role} has no native route`);
	const upstreamProviderFamily = typeof item.upstreamProviderFamily === "string" && item.upstreamProviderFamily
		? item.upstreamProviderFamily
		: undefined;
	const modelFamily = typeof item.modelFamily === "string" && item.modelFamily ? item.modelFamily : undefined;
	const dispatchStatus = item.dispatchStatus === "active" || item.dispatchStatus === "catalog-candidate"
		? item.dispatchStatus
		: undefined;
	if (!upstreamProviderFamily || !modelFamily || !dispatchStatus) {
		throw new Error(`workflow role ${role} has incomplete v3 model identity or dispatch status`);
	}
	const intelligenceClass = typeof item.intelligenceClass === "string" && item.intelligenceClass
		? item.intelligenceClass
		: undefined;
	if (!intelligenceClass) throw new Error(`workflow role ${role} has no declared intelligence class`);
	return {
		backend,
		provider,
		model,
		effort: typeof item.effort === "string" ? item.effort : null,
		effortPolicy: item.effortPolicy === "highest-available" ? item.effortPolicy : undefined,
		upstreamProviderFamily,
		modelFamily,
		dispatchStatus,
		intelligenceClass,
	};
}

interface UpstreamModelIdentity {
	upstreamProviderFamily: string;
	modelFamily: string;
	dispatchEligibility: "active" | "future-candidate" | "excluded";
	requiredEffort?: string;
	requiredEffortPolicy?: "highest-available";
}

interface ResolvedRoleIdentity extends UpstreamModelIdentity {
	executionProviderFamily: string;
	executionHarness: string;
	mappedModel: string;
}

function upstreamIdentityForModel(nativeMap: NativeMap, model: string): UpstreamModelIdentity {
	const bare = model.split("/").slice(-1)[0].split(":", 1)[0];
	const matches = Object.entries(nativeMap.upstreamModelFamilies ?? {})
		.filter(([prefix]) => bare.startsWith(prefix))
		.sort((left, right) => right[0].length - left[0].length);
	if (matches.length === 0) {
		throw new Error(`cannot derive an audited upstream model family for ${model}`);
	}
	const longest = matches[0][0].length;
	const winners = matches.filter(([prefix]) => prefix.length === longest);
	if (winners.length !== 1) throw new Error(`ambiguous upstream model-family mapping for ${model}`);
	const identity = winners[0][1];
	if (!identity || typeof identity !== "object" ||
		!identity.upstreamProviderFamily || !identity.modelFamily ||
		!["active", "future-candidate", "excluded"].includes(identity.dispatchEligibility) ||
		(identity.requiredEffort !== undefined && !identity.requiredEffort) ||
		(identity.requiredEffortPolicy !== undefined && identity.requiredEffortPolicy !== "highest-available")) {
		throw new Error(`malformed upstream model-family mapping for prefix ${winners[0][0]}`);
	}
	return identity;
}

function actualNativeProviderIdentity(
	nativeMap: NativeMap,
	role: RoleSpec,
	requireAuditedUpstream = false,
): ResolvedRoleIdentity {
	const bareModel = role.model.split(":", 1)[0];
	const nativeIdentity = resolveNativeProviderIdentity(nativeMap, role.provider, bareModel);
	if (!nativeIdentity) throw new Error(`cannot establish native execution identity for ${role.provider}/${role.model}`);
	let upstreamIdentity: UpstreamModelIdentity;
	try {
		upstreamIdentity = upstreamIdentityForModel(nativeMap, nativeIdentity.mappedModel);
	} catch (error) {
		if (requireAuditedUpstream) throw error;
		upstreamIdentity = {
			upstreamProviderFamily: nativeIdentity.upstreamProviderFamily,
			modelFamily: nativeIdentity.mappedModel,
			dispatchEligibility: "active",
		};
	}
	if (role.upstreamProviderFamily && role.upstreamProviderFamily !== upstreamIdentity.upstreamProviderFamily) {
		throw new Error(
			`role upstream provider ${role.upstreamProviderFamily} disagrees with audited model mapping ` +
			`${upstreamIdentity.upstreamProviderFamily} for ${role.model}`,
		);
	}
	if (role.modelFamily && role.modelFamily !== upstreamIdentity.modelFamily) {
		throw new Error(
			`role model family ${role.modelFamily} disagrees with audited model mapping ` +
			`${upstreamIdentity.modelFamily} for ${role.model}`,
		);
	}
	return { ...nativeIdentity, ...upstreamIdentity };
}

interface ReviewPairing {
	writer: {
		upstreamProviderFamily: string;
		modelFamily: string;
		efforts: string[];
	};
	reviewer: {
		role: string;
		upstreamProviderFamily: string;
		modelFamily: string;
		effort: string;
	};
}

/**
 * The built-in scheduler admits exactly one model policy: the canonical project-root
 * `config/workflows/models.json` workflow-model-roles/v3 document. This is a byte-for-byte
 * port of the contract in `update-platform-config/scripts/config-sync/validate_model_roles.py`;
 * both sides must reject the same documents, so the two implementations stay in lockstep and
 * a policy that the shared validator refuses can never reach a native process from here.
 */
const CANONICAL_ROLES_RELATIVE = "config/workflows/models.json";
const REQUIRED_ROLES = [
	"default", "smol", "slow", "vision", "plan", "designer", "commit", "tiny", "task",
	"frontier_execute", "frontier_execute_xhigh", "frontier_execute_anthropic",
	"frontier_execute_anthropic_xhigh", "frontier_advice", "frontier_advice_fable",
	"frontier_advice_astra",
	"research_collect", "research_collect_anthropic", "advisor", "codex_advisor",
	"diversity_glm", "diversity_kimi", "diversity_grok",
];
const POLICY_POSTURES = ["balanced", "economy", "frontier"];
const BACKEND_PROVIDERS: Record<string, string> = { codex: "codex", claude: "anthropic", cursor: "cursor" };
const DISPATCH_STATUSES = ["active", "catalog-candidate"];
const RESOLUTION_STATUSES = ["bootstrap-unverified", "verified"];
const TOP_EFFORTS = ["max", "xhigh"];
const HIGHEST_AVAILABLE = "highest-available";
const CATALOG_EVIDENCE_ASSURANCE = "caller-supplied-structural-evidence-not-live-authentication";
const IDENTITY_MAP_ASSURANCE = "static-family-binding-not-live-availability-or-authentication";
const ROLE_IDENTITY_FIELDS = [
	"backend", "provider", "upstreamProviderFamily", "model", "modelFamily", "effort",
	"intelligenceClass", "dispatchStatus",
];

function policyRequire(condition: boolean, message: string): void {
	if (!condition) throw new Error(`workflow model policy: ${message}`);
}

function plainObject(value: unknown): Record<string, unknown> | undefined {
	return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function nonemptyString(value: unknown): boolean {
	return typeof value === "string" && value.trim().length > 0;
}

function uniqueStringList(value: unknown): string[] | undefined {
	if (!Array.isArray(value) || !value.every(item => nonemptyString(item))) return undefined;
	return new Set(value as string[]).size === value.length ? value as string[] : undefined;
}

function sameSet(left: Iterable<string>, right: Iterable<string>): boolean {
	return isDeepStrictEqual([...new Set(left)].sort(), [...new Set(right)].sort());
}

function canonicalPolicyDigest(value: unknown): string {
	return sha256Bytes(canonicalJson(value));
}

function resolvedRoleIdentities(roles: Record<string, unknown>, roleNames: string[]): JsonObject[] {
	return [...roleNames].sort().map(name => {
		const role = plainObject(roles[name]) ?? {};
		const identity: JsonObject = { semanticRole: name };
		for (const field of ROLE_IDENTITY_FIELDS) identity[field] = role[field] === undefined ? null : role[field];
		return identity;
	});
}

function validateIdentityMap(document: RoleDocument, roles: Record<string, unknown>): void {
	const map = plainObject(document.auditedModelIdentities);
	policyRequire(map !== undefined, "auditedModelIdentities must be an object");
	policyRequire(map!.schema === "workflow-model-identity-map/v1", "unsupported audited model identity schema");
	policyRequire(
		map!.assurance === IDENTITY_MAP_ASSURANCE,
		"audited model identity assurance must not claim live availability or authentication",
	);
	const bindings = map!.bindings;
	policyRequire(Array.isArray(bindings) && bindings.length > 0, "auditedModelIdentities.bindings must be non-empty");
	const fields = ["backend", "provider", "upstreamProviderFamily", "model", "modelFamily"];
	const observed = new Map<string, string>();
	for (const raw of bindings as unknown[]) {
		const binding = plainObject(raw);
		policyRequire(binding !== undefined, "audited model identity bindings must be objects");
		policyRequire(sameSet(Object.keys(binding!), fields), "audited model identity binding fields are invalid");
		policyRequire(fields.every(field => nonemptyString(binding![field])), "audited model identity fields must be non-empty");
		const key = canonicalJson([binding!.backend, binding!.model]);
		policyRequire(!observed.has(key), `duplicate audited model identity for ${key}`);
		observed.set(key, canonicalJson([binding!.provider, binding!.upstreamProviderFamily, binding!.modelFamily]));
	}
	const used = new Set<string>();
	for (const [name, raw] of Object.entries(roles)) {
		const role = plainObject(raw) ?? {};
		const key = canonicalJson([role.backend ?? null, role.model ?? null]);
		policyRequire(observed.has(key), `role ${name} has no audited model identity`);
		policyRequire(
			observed.get(key) === canonicalJson([role.provider ?? null, role.upstreamProviderFamily ?? null, role.modelFamily ?? null]),
			`role ${name} does not match its audited model identity`,
		);
		used.add(key);
	}
	policyRequire(sameSet(observed.keys(), used), "audited model identities must exactly cover configured role models");
}

function validateCatalogResolution(document: RoleDocument, roles: Record<string, unknown>): void {
	const resolution = plainObject(document.catalogResolution);
	policyRequire(resolution !== undefined, "catalogResolution must be an object");
	policyRequire(resolution!.schema === "workflow-model-catalog-resolution/v1", "unsupported catalog resolution schema");
	policyRequire(RESOLUTION_STATUSES.includes(resolution!.status as string), "catalogResolution.status is invalid");
	policyRequire(resolution!.source === "authenticated-native-cli", "catalog resolution must use authenticated-native-cli");
	const verifiedBackends = uniqueStringList(resolution!.verifiedBackends) ??
		(Array.isArray(resolution!.verifiedBackends) && resolution!.verifiedBackends.length === 0 ? [] : undefined);
	const verifiedRoles = uniqueStringList(resolution!.verifiedRoles) ??
		(Array.isArray(resolution!.verifiedRoles) && resolution!.verifiedRoles.length === 0 ? [] : undefined);
	policyRequire(verifiedBackends !== undefined, "catalogResolution.verifiedBackends must be a unique string list");
	policyRequire(verifiedRoles !== undefined, "catalogResolution.verifiedRoles must be a unique string list");
	if (resolution!.status === "bootstrap-unverified") {
		// Structurally legal, exactly as validate_model_roles.py accepts it. Refusing to launch on
		// this document is a dispatch decision and belongs to the admission check below, so a project
		// can still validate its mapping before the authenticated catalog sync has ever run.
		policyRequire(
			verifiedBackends!.length === 0 && verifiedRoles!.length === 0,
			"bootstrap-unverified cannot claim verified backends or roles",
		);
		policyRequire(!("evidenceReceipt" in resolution!), "bootstrap-unverified cannot carry a catalog evidenceReceipt");
		return;
	}
	const activeRoles = Object.keys(roles).filter(name => (plainObject(roles[name]) ?? {}).dispatchStatus === "active");
	const activeBackends = activeRoles.map(name => String((plainObject(roles[name]) ?? {}).backend));
	policyRequire(sameSet(verifiedRoles!, activeRoles), "verifiedRoles must exactly cover every active role");
	policyRequire(sameSet(verifiedBackends!, activeBackends), "verifiedBackends must exactly cover every active backend");
	const receipt = plainObject(resolution!.evidenceReceipt);
	policyRequire(receipt !== undefined, "verified catalogResolution requires an evidenceReceipt");
	policyRequire(receipt!.schema === "workflow-model-catalog-evidence/v1", "unsupported catalog evidenceReceipt schema");
	policyRequire(
		receipt!.assurance === CATALOG_EVIDENCE_ASSURANCE,
		"catalog evidenceReceipt must state that it is not live authentication",
	);
	const backendDigests = plainObject(receipt!.backendCatalogSha256);
	policyRequire(
		backendDigests !== undefined && sameSet(Object.keys(backendDigests), activeBackends),
		"catalog evidenceReceipt must bind every active backend exactly",
	);
	for (const [backend, digest] of Object.entries(backendDigests!)) {
		policyRequire(
			typeof digest === "string" && /^sha256:[0-9a-f]{64}$/u.test(digest),
			`catalog evidenceReceipt digest for ${backend} must be sha256:<64hex>`,
		);
	}
	policyRequire(
		receipt!.verifiedRoleIdentitiesSha256 === `sha256:${canonicalPolicyDigest(resolvedRoleIdentities(roles, verifiedRoles!))}`,
		"catalog evidenceReceipt does not bind the verified role identities",
	);
}

function validateCapabilityPolicy(document: RoleDocument, roles: Record<string, unknown>): void {
	const policy = plainObject(document.capabilityPolicy);
	policyRequire(policy !== undefined, "capabilityPolicy must be an object");
	policyRequire(policy!.schema === "workflow-capability-policy/v1", "unsupported capability policy schema");

	const live = plainObject(policy!.liveResolution);
	policyRequire(live !== undefined, "capabilityPolicy.liveResolution must be an object");
	for (const flag of ["requiredAtInit", "exactIdsAreRefreshableSnapshots", "candidateAvailabilityIsNotFact"]) {
		policyRequire(live![flag] === true, `liveResolution.${flag} must be true`);
	}
	for (const failure of ["missing", "ambiguous", "unsupportedEffort"]) {
		policyRequire(live![failure] === "fail-closed", `liveResolution.${failure} must fail closed`);
	}

	const taskClasses = plainObject(policy!.taskClasses);
	policyRequire(taskClasses !== undefined, "capabilityPolicy.taskClasses must be an object");
	const top = plainObject(taskClasses!.topIntelligence);
	policyRequire(top !== undefined, "topIntelligence task class is required");
	policyRequire(top!.preferredExecutorRole === "frontier_execute", "Astra frontier_execute must be preferred");
	const qualifiedTop = Array.isArray(top!.qualifiedRoles) ? top!.qualifiedRoles as string[] : [];
	policyRequire(
		sameSet(qualifiedTop, [
			"frontier_execute", "frontier_execute_xhigh",
			"frontier_execute_anthropic", "frontier_execute_anthropic_xhigh",
		]),
		"topIntelligence must name the exact Astra/Opus qualified executor roles",
	);
	policyRequire(sameSet((top!.allowedEfforts ?? []) as string[], TOP_EFFORTS), "topIntelligence allows only max and xhigh");
	policyRequire(top!.preferredAdviceRole === "frontier_advice", "Opus frontier_advice must be preferred");
	policyRequire(
		isDeepStrictEqual(top!.qualifiedAdviceRoles, ["frontier_advice", "frontier_advice_fable", "frontier_advice_astra"]),
		"qualified advice must name exact active Opus, Fable 5.1, and Astra roles",
	);

	const collection = plainObject(taskClasses!.collection);
	policyRequire(collection !== undefined, "collection task class is required");
	policyRequire(
		sameSet((collection!.qualifiedRoles ?? []) as string[], ["research_collect", "research_collect_anthropic"]),
		"collection must use the Luna and Sonnet collection roles",
	);
	const expectedTaskClassRoles: Record<string, string[]> = {
		mechanical: ["smol", "tiny", "commit"],
		collection: ["research_collect", "research_collect_anthropic"],
		analysis: ["research_analyze", "task", "default", "vision", "anthropic_task"],
		planning: ["plan", "designer", "slow"],
		topIntelligence: qualifiedTop,
		advice: [
			...((top!.qualifiedAdviceRoles ?? []) as string[]),
			...((top!.candidateAdviceRoles ?? []) as string[]),
		],
		verification: ["advisor", "codex_advisor"],
		diversityReview: ["diversity_kimi", "diversity_glm", "diversity_grok"],
	};
	const declaredTaskClasses = plainObject(policy!.taskClassRoles);
	policyRequire(declaredTaskClasses !== undefined, "capabilityPolicy.taskClassRoles must be an object");
	policyRequire(
		sameSet(Object.keys(declaredTaskClasses!), Object.keys(expectedTaskClassRoles)),
		"capabilityPolicy.taskClassRoles is incomplete",
	);
	for (const [className, expected] of Object.entries(expectedTaskClassRoles)) {
		policyRequire(
			sameSet((declaredTaskClasses![className] ?? []) as string[], expected),
			`taskClassRoles.${className} is inconsistent`,
		);
	}
	const diversity = plainObject(taskClasses!.diversityReview);
	policyRequire(diversity !== undefined, "diversityReview task class is required");
	policyRequire(
		sameSet((diversity!.roles ?? []) as string[], ["diversity_kimi", "diversity_glm", "diversity_grok"]),
		"diversityReview must name Kimi, GLM, and Grok",
	);
	policyRequire(
		diversity!.requiredEffort === HIGHEST_AVAILABLE,
		"diversity review must require the highest available effort",
	);
	policyRequire(
		isDeepStrictEqual(diversity!.activeHarnesses, ["subagent-scheduler"]),
		"Cursor diversity is built-in-only",
	);
	policyRequire(
		diversity!.unsupportedHarnessBehavior === "ineligible-no-substitution",
		"unsupported diversity harnesses must fail closed without substitution",
	);

	const expectedPairs: Record<string, string> = {
		[canonicalJson(["openai", "gpt-6-astra"])]: canonicalJson(["advisor", "anthropic", "claude-opus-5", "max"]),
		[canonicalJson(["anthropic", "claude-opus-5"])]: canonicalJson(["codex_advisor", "openai", "gpt-6-astra", "max"]),
	};
	const pairings = policy!.reviewPairings;
	policyRequire(Array.isArray(pairings) && pairings.length === 2, "exactly two reviewPairings are required");
	const observedPairs: Record<string, string> = {};
	for (const raw of pairings as unknown[]) {
		const pair = plainObject(raw);
		policyRequire(pair !== undefined, "reviewPairings entries must be objects");
		const writer = plainObject(pair!.writer);
		const reviewer = plainObject(pair!.reviewer);
		policyRequire(writer !== undefined && reviewer !== undefined, "review pairing needs writer and reviewer");
		policyRequire(sameSet((writer!.efforts ?? []) as string[], TOP_EFFORTS), "review pairing writer efforts must be max and xhigh");
		const key = canonicalJson([writer!.upstreamProviderFamily ?? null, writer!.modelFamily ?? null]);
		const value = canonicalJson([
			reviewer!.role ?? null, reviewer!.upstreamProviderFamily ?? null,
			reviewer!.modelFamily ?? null, reviewer!.effort ?? null,
		]);
		policyRequire(
			[writer!.upstreamProviderFamily, writer!.modelFamily, reviewer!.role, reviewer!.upstreamProviderFamily,
				reviewer!.modelFamily, reviewer!.effort].every(item => nonemptyString(item)),
			"review pairing fields must be non-empty",
		);
		policyRequire(!(key in observedPairs), "duplicate review writer pairing");
		observedPairs[key] = value;
		const reviewerRole = plainObject(roles[String(reviewer!.role)]);
		policyRequire(reviewerRole !== undefined, `review pairing references unknown role ${String(reviewer!.role)}`);
		policyRequire(
			reviewerRole!.upstreamProviderFamily === reviewer!.upstreamProviderFamily &&
			reviewerRole!.modelFamily === reviewer!.modelFamily &&
			reviewerRole!.effort === reviewer!.effort &&
			reviewerRole!.dispatchStatus === "active",
			`review pairing role ${String(reviewer!.role)} does not match its required family and effort`,
		);
	}
	policyRequire(
		canonicalJson(observedPairs) === canonicalJson(expectedPairs),
		"reviewPairings must encode exact Astra-to-Opus and Opus-to-Astra Max review",
	);

	const defaults = plainObject(policy!.providerReviewerDefaults);
	policyRequire(defaults !== undefined && sameSet(Object.keys(defaults), ["openai", "anthropic"]), "reviewer defaults are incomplete");
	policyRequire(
		canonicalJson(defaults!.openai) === canonicalJson({ reviewerRole: "advisor", requiredEffort: "max" }),
		"providerReviewerDefaults.openai is inconsistent",
	);
	policyRequire(
		canonicalJson(defaults!.anthropic) === canonicalJson({ reviewerRole: "codex_advisor", requiredEffort: "max" }),
		"providerReviewerDefaults.anthropic is inconsistent",
	);

	const future = policy!.futureCandidates;
	policyRequire(Array.isArray(future) && future.length === 1, "futureCandidates must contain only DeepSeek");
	policyRequire(
		canonicalJson((future as unknown[])[0]) === canonicalJson({
			upstreamProviderFamily: "deepseek", modelFamily: "deepseek", backend: "cursor",
			requiredEffort: HIGHEST_AVAILABLE,
			activation: "live-catalog-model-and-highest-effort-required",
		}),
		"DeepSeek must remain a live-catalog highest-effort candidate",
	);
	policyRequire(
		canonicalJson(policy!.excludedFamilies) ===
			canonicalJson([{ modelFamily: "composer-2.5", activation: "manual-policy-change-required" }]),
		"Composer 2.5 must remain explicitly excluded",
	);
}

export function validateModelRolePolicy(document: RoleDocument): void {
	policyRequire(document.schema === "workflow-model-roles/v3", "unsupported role schema; workflow-model-roles/v3 is required");
	const roles = plainObject(document.roles);
	policyRequire(roles !== undefined && Object.keys(roles).length > 0, "roles must be a non-empty object");
	const missing = REQUIRED_ROLES.filter(name => !(name in roles!)).sort();
	policyRequire(missing.length === 0, `missing required roles: ${missing.join(", ")}`);

	const policy = plainObject(document.reasoningPolicy);
	policyRequire(policy !== undefined, "reasoningPolicy must be an object");
	policyRequire(policy!.schema === "workflow-reasoning-policy/v2", "unsupported reasoning policy schema");
	policyRequire(policy!.selectionRule === "lowest-cost-qualified-role", "unsupported role selection rule");
	policyRequire(
		policy!.costMetric === "project-relative-routing-rank-not-dollar-price",
		"reasoning policy must distinguish routing rank from dollar price",
	);
	policyRequire(policy!.effortScope === "global-dispatch-envelope", "effortScope must be global-dispatch-envelope");
	policyRequire(policy!.crossFamilyEffortComparable === false, "cross-family effort must not be marked comparable");
	const globalEscalations = policy!.maxAutomaticEscalations;
	policyRequire(
		Number.isInteger(globalEscalations) && (globalEscalations as number) >= 0 && (globalEscalations as number) <= 2,
		"maxAutomaticEscalations must be an integer in [0, 2]",
	);

	const backendEfforts = plainObject(policy!.backendEffortLevels);
	policyRequire(
		backendEfforts !== undefined && Object.keys(backendEfforts).length > 0,
		"backendEffortLevels must be a non-empty object",
	);
	for (const [backend, levels] of Object.entries(backendEfforts!)) {
		policyRequire(nonemptyString(backend), "backendEffortLevels keys must be non-empty strings");
		policyRequire(
			Array.isArray(levels) && (levels.length === 0 || uniqueStringList(levels) !== undefined),
			`backendEffortLevels.${backend} must be a unique string list`,
		);
	}
	policyRequire(
		isDeepStrictEqual(backendEfforts!.claude, ["low", "medium", "high", "xhigh", "max"]),
		"Claude backend effort levels must be low, medium, high, xhigh, max",
	);

	const postures = plainObject(policy!.postures);
	policyRequire(postures !== undefined && sameSet(Object.keys(postures), POLICY_POSTURES), "postures must be economy, balanced, and frontier");
	policyRequire(POLICY_POSTURES.includes(policy!.defaultPosture as string), "defaultPosture must name a declared posture");
	for (const [name, raw] of Object.entries(postures!)) {
		const spec = plainObject(raw);
		policyRequire(spec !== undefined, `posture ${name} must be an object`);
		policyRequire(nonemptyString(spec!.description), `posture ${name} needs a description`);
		const allowedRoles = uniqueStringList(spec!.allowedRoles);
		policyRequire(
			allowedRoles !== undefined && allowedRoles.length > 0,
			`posture ${name} allowedRoles must be a non-empty unique role list`,
		);
		for (const roleName of allowedRoles!) {
			policyRequire(roleName in roles!, `posture ${name} references unknown allowed role ${roleName}`);
			policyRequire(
				(plainObject(roles![roleName]) ?? {}).dispatchStatus === "active",
				`posture ${name} cannot allow non-active role ${roleName}`,
			);
		}
		const escalations = spec!.maxAutomaticEscalations;
		policyRequire(
			Number.isInteger(escalations) && (escalations as number) >= 0 && (escalations as number) <= (globalEscalations as number),
			`posture ${name} maxAutomaticEscalations must be within the global cap`,
		);
	}
	const ceiling = (name: string): Set<string> =>
		new Set((plainObject(postures![name])!.allowedRoles as string[]));
	const strictSubset = (left: Set<string>, right: Set<string>): boolean =>
		left.size < right.size && [...left].every(item => right.has(item));
	policyRequire(
		strictSubset(ceiling("economy"), ceiling("balanced")) && strictSubset(ceiling("balanced"), ceiling("frontier")),
		"posture allowedRoles must form strict economy < balanced < frontier ceilings",
	);

	const classes = plainObject(policy!.intelligenceClasses);
	policyRequire(classes !== undefined && Object.keys(classes).length > 0, "intelligenceClasses must be a non-empty object");
	const roleClasses = new Map<string, string>();
	for (const [className, roleNames] of Object.entries(classes!)) {
		policyRequire(nonemptyString(className), "intelligence class names must be non-empty strings");
		const members = uniqueStringList(roleNames);
		policyRequire(members !== undefined && members.length > 0, `intelligence class ${className} must contain unique role names`);
		for (const roleName of members!) {
			policyRequire(roleName in roles!, `intelligence class ${className} references unknown role ${roleName}`);
			policyRequire(!roleClasses.has(roleName), `role ${roleName} belongs to multiple intelligence classes`);
			roleClasses.set(roleName, className);
		}
	}

	for (const [name, raw] of Object.entries(roles!)) {
		const spec = plainObject(raw);
		policyRequire(spec !== undefined, `role ${name} must be an object`);
		for (const field of ["backend", "provider", "upstreamProviderFamily", "model", "modelFamily", "intelligenceClass"]) {
			policyRequire(nonemptyString(spec![field]), `role ${name} needs ${field}`);
		}
		policyRequire(DISPATCH_STATUSES.includes(spec!.dispatchStatus as string), `role ${name} has invalid dispatchStatus`);
		policyRequire(String(spec!.intelligenceClass) in classes!, `role ${name} has unknown intelligenceClass`);
		policyRequire(roleClasses.get(name) === spec!.intelligenceClass, `role ${name} class mapping is inconsistent`);
		const backend = String(spec!.backend);
		policyRequire(backend in backendEfforts!, `role ${name} backend ${backend} has no effort catalog`);
		policyRequire(backend in BACKEND_PROVIDERS, `role ${name} uses unsupported audited backend ${backend}`);
		policyRequire(
			spec!.provider === BACKEND_PROVIDERS[backend],
			`role ${name} provider ${String(spec!.provider)} does not match backend ${backend}`,
		);
		const allowedEfforts = backendEfforts![backend] as string[];
		const effortPolicy = spec!.effortPolicy;
		if (spec!.dispatchStatus === "active") {
			policyRequire(allowedEfforts.length > 0, `active role ${name} backend ${backend} exposes no verified effort settings`);
			policyRequire(
				allowedEfforts.includes(spec!.effort as string),
				`role ${name} effort ${String(spec!.effort)} is invalid for backend ${backend}`,
			);
			policyRequire(!("requiredEffort" in spec!), `active role ${name} must pin effort directly`);
			if (["advice", "diversity"].includes(String(spec!.intelligenceClass))) {
				if (backend === "cursor") {
					policyRequire(
						effortPolicy === HIGHEST_AVAILABLE,
					`role ${name} has an invalid highest-available effort policy`,
					);
				} else {
					policyRequire(
						String(spec!.intelligenceClass) === "advice" && backend === "codex" && effortPolicy === undefined,
						`role ${name} has an invalid effort policy`,
					);
				}
			} else {
				policyRequire(
					effortPolicy === undefined,
					`role ${name} may not declare a highest-available effort policy`,
				);
			}
		} else {
			policyRequire(spec!.effort === null || spec!.effort === undefined, `catalog candidate role ${name} must not pretend effort is pinned`);
			policyRequire(
				spec!.requiredEffort === HIGHEST_AVAILABLE,
				`catalog candidate role ${name} must require highest-available effort`,
			);
			policyRequire(effortPolicy === undefined, `catalog candidate role ${name} cannot claim a resolved effort policy`);
		}
		policyRequire(
			Number.isInteger(spec!.relativeCost) && (spec!.relativeCost as number) >= 1,
			`role ${name} needs a positive integer relativeCost`,
		);
	}

	validateIdentityMap(document, roles!);

	const pinned: Array<[string, string, string | undefined, string]> = [
		["frontier_execute", "gpt-6-astra", "max", "frontier_execute must be GPT-6 Astra Max"],
		["frontier_execute_xhigh", "gpt-6-astra", "xhigh", "frontier_execute_xhigh must be a GPT-6 Astra X-High top-intelligence role"],
		["frontier_execute_anthropic", "claude-opus-5", "max", "frontier_execute_anthropic must be Opus 5 Max"],
		["frontier_execute_anthropic_xhigh", "claude-opus-5", "xhigh", "frontier_execute_anthropic_xhigh must be an Opus 5 X-High top-intelligence role"],
		["research_collect", "gpt-5.6-luna", "max", "research_collect must be Luna Max"],
		["research_collect_anthropic", "claude-sonnet-5", "high", "research_collect_anthropic must be Sonnet 5 High"],
	];
	for (const [name, modelFamily, effort, message] of pinned) {
		const spec = plainObject(roles![name])!;
		policyRequire(spec.modelFamily === modelFamily && spec.effort === effort, message);
	}
	const opusAdvice = plainObject(roles!.frontier_advice)!;
	policyRequire(
		opusAdvice.modelFamily === "claude-opus-5" && opusAdvice.effortPolicy === HIGHEST_AVAILABLE,
		"frontier_advice must be highest-available Opus 5",
	);
	const fableAdvice = plainObject(roles!.frontier_advice_fable)!;
	policyRequire(
		fableAdvice.modelFamily === "claude-fable-5-1" && fableAdvice.effortPolicy === HIGHEST_AVAILABLE,
		"frontier_advice_fable must be highest-available Fable 5.1",
	);
	const astraAdvice = plainObject(roles!.frontier_advice_astra)!;
	policyRequire(
		astraAdvice.modelFamily === "gpt-6-astra" && astraAdvice.dispatchStatus === "active" &&
		astraAdvice.effort === "max",
		"frontier_advice_astra must be active GPT-6 Astra Max",
	);
	const grokAdvice = plainObject(roles!.diversity_grok)!;
	policyRequire(
		grokAdvice.modelFamily === "grok-4.6" && grokAdvice.dispatchStatus === "active" &&
		grokAdvice.effortPolicy === HIGHEST_AVAILABLE,
		"diversity_grok must be active at the highest available Grok 4.6 effort",
	);
	const advisor = plainObject(roles!.advisor)!;
	policyRequire(
		advisor.backend === "claude" && advisor.provider === "anthropic" &&
		advisor.upstreamProviderFamily === "anthropic" && advisor.model === "claude-opus-5" &&
		advisor.modelFamily === "claude-opus-5" && advisor.effort === "max",
		"advisor must be native Claude Code with Anthropic Opus 5 Max",
	);
	for (const name of ["frontier_execute_xhigh", "frontier_execute_anthropic_xhigh"]) {
		policyRequire(
			plainObject(roles![name])!.intelligenceClass === "topIntelligence",
			`${name} must be a top-intelligence role`,
		);
	}

	const activeUpstream = (className: string): string[] =>
		((classes![className] ?? []) as string[])
			.filter(roleName => (plainObject(roles![roleName]) ?? {}).dispatchStatus === "active")
			.map(roleName => String((plainObject(roles![roleName]) ?? {}).upstreamProviderFamily));
	const writerUpstreams = new Set(
		["mechanical", "collection", "analysis", "topIntelligence", "planning"].flatMap(activeUpstream),
	);
	const reviewerUpstreams = new Set(activeUpstream("verification"));
	for (const provider of writerUpstreams) {
		policyRequire(
			[...reviewerUpstreams].some(reviewer => reviewer !== provider),
			`no cross-provider verification role exists for writer upstream ${provider}`,
		);
	}

	const rules = uniqueStringList(policy!.rules) ?? (Array.isArray(policy!.rules) ? policy!.rules as string[] : undefined);
	policyRequire(
		rules !== undefined && rules.length > 0 && rules.every(item => nonemptyString(item)),
		"reasoning policy rules must be a non-empty string list",
	);
	validateCapabilityPolicy(document, roles!);
	validateCatalogResolution(document, roles!);
}

function reviewPairings(document: RoleDocument): ReviewPairing[] {
	const policy = document.capabilityPolicy;
	if (!policy || typeof policy !== "object" || Array.isArray(policy)) {
		throw new Error("workflow role policy requires capabilityPolicy");
	}
	const raw = (policy as Record<string, unknown>).reviewPairings;
	if (!Array.isArray(raw) || raw.length === 0) throw new Error("v3 capabilityPolicy requires reviewPairings");
	return raw.map((value, index) => {
		if (!value || typeof value !== "object" || Array.isArray(value)) {
			throw new Error(`reviewPairings[${index}] must be an object`);
		}
		const writer = (value as Record<string, unknown>).writer;
		const reviewer = (value as Record<string, unknown>).reviewer;
		if (!writer || typeof writer !== "object" || Array.isArray(writer) ||
			!reviewer || typeof reviewer !== "object" || Array.isArray(reviewer)) {
			throw new Error(`reviewPairings[${index}] must declare writer and reviewer objects`);
		}
		const writerItem = writer as Record<string, unknown>;
		const reviewerItem = reviewer as Record<string, unknown>;
		if (![writerItem.upstreamProviderFamily, writerItem.modelFamily,
			reviewerItem.role, reviewerItem.upstreamProviderFamily, reviewerItem.modelFamily, reviewerItem.effort]
			.every(value => typeof value === "string" && value.length > 0) ||
			!Array.isArray(writerItem.efforts) || writerItem.efforts.length === 0 ||
			!writerItem.efforts.every(value => typeof value === "string" && value.length > 0)) {
			throw new Error(`reviewPairings[${index}] is malformed`);
		}
		return {
			writer: {
				upstreamProviderFamily: writerItem.upstreamProviderFamily as string,
				modelFamily: writerItem.modelFamily as string,
				efforts: writerItem.efforts as string[],
			},
			reviewer: {
				role: reviewerItem.role as string,
				upstreamProviderFamily: reviewerItem.upstreamProviderFamily as string,
				modelFamily: reviewerItem.modelFamily as string,
				effort: reviewerItem.effort as string,
			},
		};
	});
}

function assertRoleDispatchEligible(
	document: RoleDocument,
	roleName: string,
	role: RoleSpec,
	identity: ResolvedRoleIdentity,
	admission: RouteAdmission,
): void {
	if (role.dispatchStatus === "catalog-candidate") {
		throw new Error(`workflow role ${roleName} is a catalog candidate and cannot be actively dispatched`);
	}
	if (identity.dispatchEligibility === "excluded") {
		throw new Error(`workflow role ${roleName} model family ${identity.modelFamily} is excluded from active dispatch`);
	}
	if (identity.dispatchEligibility === "future-candidate") {
		throw new Error(`workflow role ${roleName} model family ${identity.modelFamily} is future-only and cannot be actively dispatched`);
	}
	if (identity.requiredEffort && role.effort !== identity.requiredEffort) {
		throw new Error(
			`workflow role ${roleName} model family ${identity.modelFamily} requires effort ${identity.requiredEffort}`,
		);
	}
	if (identity.requiredEffortPolicy && role.effortPolicy !== identity.requiredEffortPolicy) {
		throw new Error(
			`workflow role ${roleName} model family ${identity.modelFamily} requires effort policy ${identity.requiredEffortPolicy}`,
		);
	}
}

function assertExactReviewPair(
	document: RoleDocument,
	pair: { writer: string; reviewer: string },
	reviewerRoleName: string,
	writerRole: RoleSpec,
	reviewerRole: RoleSpec,
	writerIdentity: ResolvedRoleIdentity,
	reviewerIdentity: ResolvedRoleIdentity,
): boolean {
	const candidates = reviewPairings(document).filter(candidate =>
		candidate.writer.upstreamProviderFamily === writerIdentity.upstreamProviderFamily &&
		candidate.writer.modelFamily === writerIdentity.modelFamily &&
		candidate.writer.efforts.includes(writerRole.effort ?? ""),
	);
	if (candidates.length > 1) {
		throw new Error(`machine-gated writer ${pair.writer} has ambiguous exact reviewer pairing policy`);
	}
	let expected: ReviewPairing["reviewer"];
	if (candidates.length === 1) {
		expected = candidates[0].reviewer;
	} else {
		const defaults = plainObject(plainObject(document.capabilityPolicy)?.providerReviewerDefaults);
		const fallback = plainObject(defaults?.[writerIdentity.upstreamProviderFamily]);
		const fallbackRoleName = fallback?.reviewerRole;
		const fallbackRole = typeof fallbackRoleName === "string"
			? plainObject((document.roles as Record<string, unknown>)[fallbackRoleName])
			: undefined;
		if (!fallback || typeof fallbackRoleName !== "string" || !fallbackRole ||
			fallbackRole.upstreamProviderFamily === writerIdentity.upstreamProviderFamily ||
			fallbackRole.effort !== fallback.requiredEffort) {
			throw new Error(`machine-gated writer ${pair.writer} has no valid exact opposite-provider reviewer default`);
		}
		expected = {
			role: fallbackRoleName,
			upstreamProviderFamily: String(fallbackRole.upstreamProviderFamily),
			modelFamily: String(fallbackRole.modelFamily),
			effort: String(fallbackRole.effort),
		};
	}
	if (reviewerRoleName !== expected.role || reviewerRole.modelFamily !== expected.modelFamily ||
		reviewerIdentity.upstreamProviderFamily !== expected.upstreamProviderFamily ||
		reviewerIdentity.modelFamily !== expected.modelFamily || reviewerRole.effort !== expected.effort) {
		throw new Error(
			`machine-gated reviewer ${pair.reviewer} must use role ${expected.role} ` +
			`${expected.upstreamProviderFamily}/` +
			`${expected.modelFamily} effort ${expected.effort} for ${writerIdentity.modelFamily} writer ${pair.writer}`,
		);
	}
	return candidates.length === 1;
}

function dispatchRoutes(
	design: WorkflowDesign,
	mapping: WorkflowMapping,
): Array<{ role: string; access: "read" | "write" | "schedule"; node: string }> {
	const routes: Array<{ role: string; access: "read" | "write" | "schedule"; node: string }> = [];
	if (mapping.scheduler.maxCalls > 0) {
		routes.push({ role: mapping.scheduler.role, access: "schedule", node: "scheduler" });
	}
	for (const node of design.nodes) {
		if (node.kind === "agent" || node.kind === "decision") {
			routes.push({ role: node.role ?? "task", access: node.workspaceAccess ?? "read", node: node.id });
		}
	}
	return routes;
}

/**
 * The reasoning posture is an explicit mapping declaration. It is never inferred from a role,
 * a node name, or a default, because the posture is exactly the ceiling that decides which
 * roles this workflow is allowed to activate at all.
 */
function declaredPosture(mapping: WorkflowMapping, roles: RoleDocument): string {
	const posture = mapping.reasoningPosture;
	if (typeof posture !== "string" || !posture.trim()) {
		throw new Error("subagent mapping must declare an explicit reasoningPosture");
	}
	const postures = plainObject(plainObject(roles.reasoningPolicy)?.postures);
	if (!postures || !(posture in postures)) {
		throw new Error(`subagent mapping reasoningPosture ${posture} is not a declared reasoning posture`);
	}
	return posture;
}

function postureAllowedRoles(roles: RoleDocument, posture: string): string[] {
	return (plainObject(plainObject(plainObject(roles.reasoningPolicy)?.postures)?.[posture])?.allowedRoles ?? []) as string[];
}

function policyTaskClass(roles: RoleDocument, roleName: string): string {
	const taskClassRoles = plainObject(plainObject(roles.capabilityPolicy)?.taskClassRoles) ?? {};
	const matches = Object.entries(taskClassRoles)
		.filter(([, members]) => Array.isArray(members) && (members as string[]).includes(roleName))
		.map(([taskClass]) => taskClass);
	if (matches.length !== 1) {
		throw new Error(
			`workflow role ${roleName} resolves to ${matches.length} declared task classes; exactly one explicit task class is required`,
		);
	}
	return matches[0];
}

/**
 * `dispatch` is the gate every command that may launch a native process uses: the policy must be
 * structurally canonical AND the live catalog receipt must actually cover the routes. `structure`
 * checks only what a document can prove about itself, so a freshly materialized project can still
 * validate its mapping before the authenticated catalog sync exists. No native process runs under
 * `structure`.
 */
export type RouteAdmission = "dispatch" | "structure";

export function validateNativeRoutes(
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	roles: RoleDocument,
	nativeMap: NativeMap,
	admission: RouteAdmission = "dispatch",
): void {
	validateModelRolePolicy(roles);
	const posture = declaredPosture(mapping, roles);
	const allowedRoles = postureAllowedRoles(roles, posture);
	const requiresInterruptedSessionResume = design.requiredCapabilities.includes("interrupted-session-resume");
	const routes = dispatchRoutes(design, mapping);
	if (mapping.scheduler.maxCalls === 0) {
		// Preserve schema validation without requiring a route/profile that can never launch.
		resolveRole(roles, mapping.scheduler.role);
	}
	for (const route of routes) {
		const role = resolveRole(roles, route.role);
		const bare = role.model.split(":", 1)[0];
		const provider = classifyProvider(nativeMap, role.provider, bare);
		if (!provider) throw new Error(`${route.node} has no native CLI route for ${role.provider}/${role.model}`);
		const identity = actualNativeProviderIdentity(nativeMap, role, true);
		assertRoleDispatchEligible(roles, route.role, role, identity, admission);
		if (!allowedRoles.includes(route.role)) {
			throw new Error(`${route.node} role ${route.role} is outside the declared ${posture} reasoning posture ceiling`);
		}
		policyTaskClass(roles, route.role);
		if (requiresInterruptedSessionResume && !provider.session) {
			throw new Error(
				`${route.node} requires interrupted-session-resume but provider ${provider.bin} has no audited session profile`,
			);
		}
		if (role.effort && (!provider.effort || !provider.effort.values.includes(role.effort))) {
			throw new Error(`${route.node} provider ${provider.bin} does not support configured effort ${role.effort}`);
		}
		if (route.access === "read" && !normalizeReadOnlyArgs(provider)) {
			throw new Error(`${route.node} provider ${provider.bin} has no audited read-only profile`);
		}
		if (route.access === "schedule" && !provider.schedulerArgs) {
			throw new Error(`${route.node} provider ${provider.bin} has no audited scheduler profile`);
		}
		if (stateBound(design)) {
			const closed = new Set(Object.values(requireResolvedExecutables(mapping)).map(binding => binding.path));
			if (!path.isAbsolute(provider.bin) || !closed.has(provider.bin)) {
				throw new Error(`${route.node} native CLI ${provider.bin} is outside the state-bound executable closure`);
			}
		}
	}
	for (const pair of validateMachineGatedDesign(design)) {
		const writerNode = design.nodes.find(node => node.id === pair.writer)!;
		const reviewerNode = design.nodes.find(node => node.id === pair.reviewer)!;
		const writerRole = resolveRole(roles, writerNode.role ?? "task");
		const reviewerRoleName = reviewerNode.role ?? "task";
		const reviewerRole = resolveRole(roles, reviewerRoleName);
		const writerIdentity = actualNativeProviderIdentity(nativeMap, writerRole, true);
		const reviewerIdentity = actualNativeProviderIdentity(nativeMap, reviewerRole, true);
		if (writerIdentity.executionHarness === reviewerIdentity.executionHarness) {
			throw new Error(
				`machine-gated reviewer ${pair.reviewer} and writer ${pair.writer} resolve to the same native execution provider ` +
				`${reviewerIdentity.executionProviderFamily} (${reviewerIdentity.executionHarness})`,
			);
		}
		if (writerIdentity.upstreamProviderFamily === reviewerIdentity.upstreamProviderFamily) {
			throw new Error(
				`machine-gated reviewer ${pair.reviewer} and writer ${pair.writer} resolve to the same upstream model provider ` +
				`${reviewerIdentity.upstreamProviderFamily} after native model mapping`,
			);
		}
		assertExactReviewPair(
			roles,
			pair,
			reviewerRoleName,
			writerRole,
			reviewerRole,
			writerIdentity,
			reviewerIdentity,
		);
	}
}

function render(template: string, values: Record<string, unknown>): string {
	return template.replace(/\{\{([A-Za-z][A-Za-z0-9_.-]*)\}\}/g, (_match, key: string) => {
		if (!(key in values)) throw new Error(`prompt/template references missing value ${key}`);
		const value = values[key];
		return typeof value === "string" ? value : JSON.stringify(value);
	});
}

function lastJsonObject(text: string, label: string): JsonObject {
	const lines = text.split(/\r?\n/u).map(line => line.trim()).filter(Boolean);
	for (let index = lines.length - 1; index >= 0; index -= 1) {
		try {
			const value: unknown = JSON.parse(lines[index]);
			if (value && typeof value === "object" && !Array.isArray(value)) return value as JsonObject;
		} catch {
			// Continue looking for the final machine object.
		}
	}
	throw new Error(`${label} returned no JSON object`);
}

function stateValue(state: ControllerState, field: string): unknown {
	if (field.includes(".")) {
		const [nodeId, outputField] = field.split(".", 2);
		return state.outputs[nodeId]?.[outputField];
	}
	return state.data[field];
}

function matchesWhen(state: ControllerState, when: Record<string, unknown> | undefined): boolean {
	if (!when) return true;
	const field = typeof when.field === "string" ? when.field : "";
	if (!field) return false;
	const actual = stateValue(state, field);
	if ("equals" in when) return actual === when.equals;
	if ("lessThan" in when) return typeof actual === "number" && typeof when.lessThan === "number" && actual < when.lessThan;
	if ("atLeast" in when) return typeof actual === "number" && typeof when.atLeast === "number" && actual >= when.atLeast;
	return false;
}

function outgoing(design: WorkflowDesign, state: ControllerState, nodeId: string): string[] {
	return design.transitions
		.filter(edge => edge.from === nodeId && matchesWhen(state, edge.when))
		.map(edge => edge.to);
}

function enforcedOutgoing(design: WorkflowDesign, state: ControllerState, node: DesignNode): string[] {
	const targets = outgoing(design, state, node.id);
	if (machineGated(design) && node.kind === "decision" && targets.length !== 1) {
		throw new MalformedDecisionError(
			`machine-gated decision ${node.id} selected ${targets.length} exits instead of exactly one`,
		);
	}
	return targets;
}

function runnable(design: WorkflowDesign, state: ControllerState, candidates: string[]): string[] {
	const completed = new Set(state.completed);
	return [...new Set(candidates)].filter(nodeId => {
		if (completed.has(nodeId)) return false;
		const node = design.nodes.find(item => item.id === nodeId);
		if (!node) return false;
		return (node.waitFor ?? []).every(parent => completed.has(parent));
	}).sort();
}

export function assertStateType(design: WorkflowDesign, field: string, value: unknown): void {
	const declaration = design.state?.find(item => item.name === field);
	if (!declaration) throw new Error(`state field ${field} is not declared by the design`);
	const valid = declaration.type === "string"
		? typeof value === "string"
		: declaration.type === "number"
			? typeof value === "number" && Number.isFinite(value)
			: declaration.type === "boolean"
				? typeof value === "boolean"
				: declaration.type === "array"
					? Array.isArray(value)
					: declaration.type === "object"
						? Boolean(value) && typeof value === "object" && !Array.isArray(value)
						: false;
	if (!valid) throw new Error(`state field ${field} does not match declared type ${declaration.type}`);
	if (declaration.type === "array") {
		const arrayValue = value as unknown[];
		if (declaration.maxItems !== undefined && (declaration.maxItems < 0 || arrayValue.length > declaration.maxItems)) {
			throw new Error(`state field ${field} exceeds maxItems`);
		}
		if (declaration.itemType === "string" && arrayValue.some(item => typeof item !== "string")) {
			throw new Error(`state field ${field} items must be strings`);
		}
		if (declaration.maxItemBytes !== undefined && arrayValue.some(item =>
			typeof item === "string" && Buffer.byteLength(item, "utf8") > declaration.maxItemBytes!)) {
			throw new Error(`state field ${field} item exceeds maxItemBytes`);
		}
		if (declaration.uniqueItems && new Set(arrayValue).size !== arrayValue.length) {
			throw new Error(`state field ${field} items must be unique`);
		}
	}
}

function applyPatch(design: WorkflowDesign, node: DesignNode, state: ControllerState, output: JsonObject): void {
	const writes = new Set(node.writes ?? []);
	const patches = output.statePatch;
	if (!Array.isArray(patches)) throw new Error(`node ${node.id} returned no statePatch array`);
	const seen = new Set<string>();
	const staged = new Map<string, unknown>();
	for (const raw of patches) {
		if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error(`node ${node.id} returned malformed state patch`);
		const patch = raw as Record<string, unknown>;
		if (patch.op !== "set" || typeof patch.path !== "string" || !/^\/[A-Za-z][A-Za-z0-9_-]*$/.test(patch.path)) {
			throw new Error(`node ${node.id} returned unsupported state patch`);
		}
		const field = patch.path.slice(1);
		if (!writes.has(field)) throw new Error(`node ${node.id} attempted undeclared write ${field}`);
		if (seen.has(field)) throw new Error(`node ${node.id} wrote duplicate state path ${field}`);
		assertStateType(design, field, patch.value);
		seen.add(field);
		staged.set(field, patch.value);
	}
	for (const field of writes) {
		if (!seen.has(field)) throw new Error(`node ${node.id} omitted declared write ${field}`);
	}
	for (const [field, value] of staged) state.data[field] = value;
}

function declaredReadValues(node: DesignNode, state: ControllerState): Record<string, unknown> {
	const values: Record<string, unknown> = {};
	for (const field of node.reads ?? []) {
		const value = stateValue(state, field);
		if (value === undefined) throw new Error(`node ${node.id} declared read ${field}, but it is unavailable`);
		values[field] = value;
	}
	return values;
}

function controllerRenderValues(root: string, identity: RunDirIdentity, state: ControllerState): Record<string, string> {
	return {
		"controller.projectRoot": root,
		"controller.runRoot": identity.path,
		"controller.runId": state.runId,
	};
}

function executableRenderValues(mapping: WorkflowMapping): Record<string, string> {
	const values: Record<string, string> = {};
	for (const [label, binding] of Object.entries(mapping.proof?.executables ?? {})) {
		if (!resolvedExecutable(binding)) throw new Error(`portable executable ${label} was not resolved before rendering`);
		values[`executable.${label}.path`] = binding.path;
		values[`executable.${label}.sha256`] = binding.sha256;
	}
	return values;
}

function renderForNode(
	template: string,
	node: DesignNode,
	state: ControllerState,
	controllerValues?: Record<string, string>,
): string {
	const reads = new Set(node.reads ?? []);
	for (const match of template.matchAll(/\{\{([A-Za-z][A-Za-z0-9_.-]*)\}\}/g)) {
		if (match[1].startsWith("controller.") || match[1].startsWith("executable.")) {
			if (!controllerValues || !(match[1] in controllerValues)) {
				throw new Error(`node ${node.id} template references forbidden trusted value ${match[1]}`);
			}
			continue;
		}
		if (!reads.has(match[1])) {
			throw new Error(`node ${node.id} template placeholder ${match[1]} is not declared in reads`);
		}
	}
	return render(template, { ...declaredReadValues(node, state), ...(controllerValues ?? {}) });
}

function handlerTimeout(node: DesignNode): number {
	const value = node.timeoutSeconds;
	if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
		throw new Error(`node ${node.id} has no positive timeoutSeconds`);
	}
	return Math.floor(value * 1000);
}

function elapsedMs(clock: RunClock): number {
	return Math.max(clock.baseElapsedMs, clock.baseElapsedMs + (performance.now() - clock.startedMonotonicMs));
}

function remainingWallTime(design: WorkflowDesign, clock: RunClock): number {
	if (clock.signal?.aborted) throw new Error("workflow cancelled by outer owner");
	const declared = design.budgets?.maxWallTimeSeconds;
	const cap = clock.parentWallTimeSeconds === undefined ? declared
		: Math.min(declared ?? Number.POSITIVE_INFINITY, clock.parentWallTimeSeconds);
	if (cap === undefined) return Number.MAX_SAFE_INTEGER;
	if (typeof cap !== "number" || !Number.isFinite(cap) || cap <= 0) throw new Error("workflow has an invalid wall-time cap");
	const remaining = Math.floor(cap * 1000 - elapsedMs(clock));
	if (remaining <= 0) throw new Error("workflow wall-time cap reached");
	return remaining;
}

async function commandCall(
	argv: string[],
	cwd: string,
	timeoutMs: number,
	parentSignal?: AbortSignal,
): Promise<{ stdout: string; stderr: string; exitCode: number; terminationClass: string }> {
	const controller = new AbortController();
	const signal = parentSignal ? AbortSignal.any([controller.signal, parentSignal]) : controller.signal;
	const timer = setTimeout(() => controller.abort(new Error("command deadline exceeded")), timeoutMs);
	try {
		const { stdout, stderr, exitCode } = await spawnCollect(argv, {
			cwd,
			env: unprivilegedChildEnvironment(process.env) as Record<string, string>,
			signal,
		});
		return {
			stdout: stdout.trim(),
			stderr: stderr.trim(),
			exitCode,
			terminationClass: signal.aborted ? "NODE_DEADLINE" : exitCode === 0 ? "COMPLETED" : "COMMAND_FAILURE",
		};
	} finally {
		clearTimeout(timer);
	}
}

/**
 * The exact model argv the receipt authorizes for one activation.
 *
 * The launched model is the receipt's concrete `catalogModelId` — the id a fresh
 * authenticated catalog actually listed — not the role's configured base model.
 * Two shapes are legal, and each is proven by fresh catalog normalization:
 *
 * - the concrete id already encodes the reasoning level (Cursor's
 *   `<family>-thinking-<effort>` and `<family>-<effort>` SKUs). Appending a
 *   synthetic `--effort` there would launch a parameterization the catalog never
 *   listed, so no effort argument is passed.
 * - the concrete id IS the base model (native Codex and Claude slugs), where the
 *   reasoning level is a separate provider-local parameter the catalog verified.
 *
 * Anything else — a different base family, a different effort, a missing id —
 * fails closed, so a tampered receipt cannot widen the launched argv.
 */
function receiptLaunchSpec(role: RoleSpec, receipt: JsonObject): { modelArgument: string; effortArgs: string[] } {
	const resolved = plainObject(receipt.resolved);
	const catalogModelId = resolved?.catalogModelId;
	if (!nonemptyString(catalogModelId)) {
		throw new LaunchAuthorizationError("native launch receipt carries no concrete catalogModelId");
	}
	if (resolved?.model !== role.model || resolved?.effort !== role.effort ||
		resolved?.provider !== role.provider || resolved?.backend !== role.backend) {
		throw new LaunchAuthorizationError(
			`native launch receipt identity disagrees with role ${role.provider}/${role.model} effort ${role.effort}`,
		);
	}
	if ((role.backend === "codex" || role.backend === "claude") && catalogModelId === role.model) {
		// The catalog proved the base slug itself, so the reasoning level stays a
		// separate provider-local argument (Codex `--effort`).
		return {
			modelArgument: `${role.provider}/${catalogModelId}`,
			effortArgs: role.effort ? ["--effort", role.effort] : [],
		};
	}
	// Otherwise the catalog proved a concrete per-effort SKU. Only the audited
	// normalizations of this exact role at this exact effort are acceptable, and the
	// effort must not be re-applied as an argument on top of the id.
	if (role.backend !== "cursor") {
		throw new LaunchAuthorizationError(
			`native launch receipt catalogModelId ${catalogModelId} is not the exact canonical slug ${role.model} for backend ${role.backend}`,
		);
	}
	const audited = role.effort
		? [`${role.model}-thinking-${role.effort}`, `${role.model}-${role.effort}`]
		: [];
	if (!audited.includes(catalogModelId)) {
		throw new LaunchAuthorizationError(
			`native launch receipt catalogModelId ${catalogModelId} is not an audited normalization of `
			+ `${role.model} at effort ${role.effort}`,
		);
	}
	return { modelArgument: `${role.provider}/${catalogModelId}`, effortArgs: [] };
}

const LAUNCH_RECEIPT_TOP_KEYS = ["policy", "receiptSha256", "request", "resolved", "schema", "writer"];
const LAUNCH_RECEIPT_REQUEST_KEYS = ["reasoningPosture", "reviewMode", "semanticRole", "taskClass", "writerRole"];
const LAUNCH_RECEIPT_IDENTITY_KEYS = [
	"backend", "catalogModelId", "dispatchStatus", "effort", "intelligenceClass", "model", "modelFamily",
	"provider", "semanticRole", "upstreamProviderFamily",
];
const LAUNCH_RECEIPT_POLICY_KEYS = [
	"capabilityPolicySha256", "catalogEvidenceReceipt", "catalogResolutionSha256", "catalogSource",
	"catalogStatus", "freshCatalogEvidence", "liveCatalogAuthenticatedByResolver", "modelsDocumentSha256",
	"modelsSchema", "reasoningPolicySha256",
];
const CATALOG_EVIDENCE_KEYS = ["backend", "catalogBytesSha256", "command", "coverage", "exitCode", "stderrSha256"];
const CATALOG_COMMAND_KEYS = [
	"argv", "executable", "executableSha256", "executionClosure", "executionClosureSha256", "invokedArgv",
];
const CATALOG_CLOSURE_KEYS = ["path", "role", "sha256"];
const SHA256_RECEIPT = /^sha256:[0-9a-f]{64}$/u;

function requireExactKeys(value: JsonObject, expected: string[], label: string): void {
	const actual = Object.keys(value).sort();
	if (!isDeepStrictEqual(actual, [...expected].sort())) {
		throw new LaunchAuthorizationError(`${label} has missing or unsupported fields`);
	}
}

function prefixedCanonicalSha256(value: unknown): string {
	return `sha256:${sha256Bytes(canonicalJson(value))}`;
}

function validateReceiptIdentity(
	value: unknown,
	roleName: string,
	role: RoleSpec,
	label: string,
): JsonObject {
	const identity = plainObject(value);
	if (!identity) throw new LaunchAuthorizationError(`${label} identity is malformed`);
	if (!Object.hasOwn(identity, "catalogModelId") || !nonemptyString(identity.catalogModelId)) {
		throw new LaunchAuthorizationError(`${label} identity carries no concrete catalogModelId`);
	}
	requireExactKeys(identity, LAUNCH_RECEIPT_IDENTITY_KEYS, `${label} identity`);
	const { catalogModelId: _catalogModelId, ...baseIdentity } = identity;
	if (!isDeepStrictEqual(baseIdentity, canonicalRoleIdentity(roleName, role))) {
		throw new LaunchAuthorizationError(
			`${label} receipt identity disagrees with role ${role.provider}/${role.model} effort ${role.effort}`,
		);
	}
	// Reuse the same backend-specific concrete-id rule used to construct the actual
	// argv. This also proves a review writer's catalog id, even though it is not the
	// process launched by this activation.
	receiptLaunchSpec(role, { resolved: identity });
	return identity;
}

function expectedCatalogArgv(backend: string): string[] | undefined {
	return backend === "claude"
		? ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
		: backend === "codex" ? ["codex", "debug", "models"]
		: backend === "cursor" ? ["cursor-agent", "models"] : undefined;
}

function catalogInvocationPrefix(command: JsonObject, backend: string): string[] {
	const expected = expectedCatalogArgv(backend);
	const invoked = command.invokedArgv;
	if (!expected || !Array.isArray(invoked)) {
		throw new LaunchAuthorizationError(`fresh ${backend} catalog command has no canonical invocation prefix`);
	}
	const tail = expected.slice(1);
	const prefixLength = invoked.length - tail.length;
	const prefix = invoked.slice(0, prefixLength);
	const directOrScript = [1, 2].includes(prefix.length) && prefix.every(value => nonemptyString(value)) &&
		prefix.at(-1) === command.executable;
	const cursorRuntime = backend === "cursor" && prefix.length === 3 &&
		nonemptyString(prefix[0]) && prefix[1] === "--use-system-ca" && nonemptyString(prefix[2]);
	if ((!directOrScript && !cursorRuntime) || !isDeepStrictEqual(invoked.slice(prefixLength), tail) ||
		prefix.some(value => !nonemptyString(value)) ||
		(backend !== "cursor" && prefix.at(-1) !== command.executable)) {
		throw new LaunchAuthorizationError(`fresh ${backend} catalog command has a malformed fixed invocation prefix`);
	}
	return prefix as string[];
}

function receiptExecutionClosure(command: JsonObject, backend: string): Pick<
	NativeLaunchAuthorization,
	"executable" | "executableSha256" | "invocationPrefix" | "executionClosure" | "executionClosureSha256"
> {
	return {
		executable: String(command.executable),
		executableSha256: String(command.executableSha256),
		invocationPrefix: catalogInvocationPrefix(command, backend),
		executionClosure: command.executionClosure as NativeLaunchAuthorization["executionClosure"],
		executionClosureSha256: String(command.executionClosureSha256),
	};
}

function validateCatalogEvidence(
	value: unknown,
	backend: string,
	identities: JsonObject[],
): void {
	const evidence = plainObject(value);
	if (!evidence) throw new LaunchAuthorizationError(`fresh ${backend} catalog evidence is malformed`);
	requireExactKeys(evidence, CATALOG_EVIDENCE_KEYS, `fresh ${backend} catalog evidence`);
	if (evidence.backend !== backend || evidence.exitCode !== 0 ||
		!SHA256_RECEIPT.test(String(evidence.catalogBytesSha256)) ||
		!SHA256_RECEIPT.test(String(evidence.stderrSha256))) {
		throw new LaunchAuthorizationError(`fresh ${backend} catalog evidence has malformed provenance`);
	}
	const command = plainObject(evidence.command);
	if (!command) throw new LaunchAuthorizationError(`fresh ${backend} catalog command is malformed`);
	requireExactKeys(command, CATALOG_COMMAND_KEYS, `fresh ${backend} catalog command`);
	const expectedArgv = expectedCatalogArgv(backend);
	if (!expectedArgv || !isDeepStrictEqual(command.argv, expectedArgv) ||
		!Array.isArray(command.invokedArgv) || command.invokedArgv.length < expectedArgv.length ||
		command.invokedArgv.some(value => !nonemptyString(value)) ||
		!isDeepStrictEqual(command.invokedArgv.slice(-expectedArgv.length + 1), expectedArgv.slice(1)) ||
		!nonemptyString(command.executable) || !SHA256_RECEIPT.test(String(command.executableSha256))) {
		throw new LaunchAuthorizationError(`fresh ${backend} catalog command is not the canonical native probe`);
	}
	const closure = command.executionClosure;
	if (!Array.isArray(closure) || closure.length === 0) {
		throw new LaunchAuthorizationError(`fresh ${backend} catalog execution closure is empty`);
	}
	const closureIdentities = new Set<string>();
	let entrypointBound = false;
	for (const [index, raw] of closure.entries()) {
		const entry = plainObject(raw);
		if (!entry) throw new LaunchAuthorizationError(`fresh ${backend} catalog closure entry ${index + 1} is malformed`);
		requireExactKeys(entry, CATALOG_CLOSURE_KEYS, `fresh ${backend} catalog closure entry ${index + 1}`);
		if (!["interpreter", "entrypoint", "runtime", "bundle"].includes(String(entry.role)) ||
			!nonemptyString(entry.path) || !SHA256_RECEIPT.test(String(entry.sha256))) {
			throw new LaunchAuthorizationError(`fresh ${backend} catalog closure entry ${index + 1} is malformed`);
		}
		const unique = `${String(entry.role)}\u0000${String(entry.path)}`;
		if (closureIdentities.has(unique)) {
			throw new LaunchAuthorizationError(`fresh ${backend} catalog execution closure contains a duplicate entry`);
		}
		closureIdentities.add(unique);
		if (entry.role === "entrypoint" && entry.path === command.executable &&
			entry.sha256 === command.executableSha256) entrypointBound = true;
	}
	if (!entrypointBound) {
		throw new LaunchAuthorizationError(`fresh ${backend} catalog execution closure does not bind its entrypoint`);
	}
	try {
		verifyNativeExecutionClosure(receiptExecutionClosure(command, backend));
	} catch (error) {
		throw new LaunchAuthorizationError(
			`fresh ${backend} catalog executable closure is not live-bound: ${(error as Error).message}`,
			error,
		);
	}
	const coverage = plainObject(evidence.coverage);
	if (!coverage || Object.values(coverage).some(item =>
		!Array.isArray(item) || item.length === 0 || item.some(value => !nonemptyString(value)) ||
		new Set(item as string[]).size !== item.length)) {
		throw new LaunchAuthorizationError(`fresh ${backend} catalog coverage is malformed`);
	}
	for (const identity of identities) {
		const model = String(identity.model);
		const effort = String(identity.effort);
		const catalogModelId = String(identity.catalogModelId);
		const covered = backend === "codex" || backend === "claude"
			? Array.isArray(coverage[model]) && (coverage[model] as unknown[]).includes(effort) && catalogModelId === model
			: backend === "cursor"
				? Array.isArray(coverage.__cursor_ids__) && (coverage.__cursor_ids__ as unknown[]).includes(catalogModelId)
				: false;
		if (!covered) {
			throw new LaunchAuthorizationError(
				`fresh ${backend} catalog evidence does not cover ${model} at effort ${effort} as ${catalogModelId}`,
			);
		}
	}
}

function validateLaunchReceipt(
	receipt: JsonObject,
	request: LaunchSpecRequest,
	roles: RoleDocument,
	roleName: string,
	role: RoleSpec,
	modelsSha256: string,
): void {
	requireExactKeys(receipt, LAUNCH_RECEIPT_TOP_KEYS, "native launch receipt");
	if (receipt.schema !== LAUNCH_RECEIPT_SCHEMA) {
		throw new LaunchAuthorizationError("native launch receipt has an unsupported schema");
	}
	const requestValue = plainObject(receipt.request);
	if (!requestValue) throw new LaunchAuthorizationError("native launch receipt request is malformed");
	requireExactKeys(requestValue, LAUNCH_RECEIPT_REQUEST_KEYS, "native launch receipt request");
	if (!isDeepStrictEqual(requestValue, request)) {
		throw new LaunchAuthorizationError("native launch receipt request disagrees with the pending activation");
	}
	const resolved = validateReceiptIdentity(receipt.resolved, roleName, role, "resolved");
	let writer: JsonObject | null = null;
	if (request.writerRole === null) {
		if (receipt.writer !== null) throw new LaunchAuthorizationError("native launch receipt has an unexpected writer identity");
	} else {
		const writerRole = resolveRole(roles, request.writerRole);
		writer = validateReceiptIdentity(receipt.writer, request.writerRole, writerRole, "writer");
	}
	const policy = plainObject(receipt.policy);
	if (!policy) throw new LaunchAuthorizationError("native launch receipt policy is malformed");
	requireExactKeys(policy, LAUNCH_RECEIPT_POLICY_KEYS, "native launch receipt policy");
	const expectedPolicy = {
		modelsSchema: roles.schema,
		modelsDocumentSha256: `sha256:${modelsSha256}`,
		catalogResolutionSha256: prefixedCanonicalSha256(roles.catalogResolution),
		capabilityPolicySha256: prefixedCanonicalSha256(roles.capabilityPolicy),
		reasoningPolicySha256: prefixedCanonicalSha256(roles.reasoningPolicy),
		catalogStatus: plainObject(roles.catalogResolution)?.status,
		catalogSource: "authenticated-native-cli",
		catalogEvidenceReceipt: plainObject(roles.catalogResolution)?.evidenceReceipt ?? null,
		liveCatalogAuthenticatedByResolver: true,
	};
	for (const [key, expected] of Object.entries(expectedPolicy)) {
		if (!isDeepStrictEqual(policy[key], expected)) {
			throw new LaunchAuthorizationError(`native launch receipt policy ${key} disagrees with the frozen policy`);
		}
	}
	const fresh = plainObject(policy.freshCatalogEvidence);
	const identities = [resolved, ...(writer ? [writer] : [])];
	const expectedBackends = [...new Set(identities.map(identity => String(identity.backend)))].sort();
	if (!fresh || !isDeepStrictEqual(Object.keys(fresh).sort(), expectedBackends)) {
		throw new LaunchAuthorizationError("native launch receipt fresh evidence does not cover the exact activation backends");
	}
	for (const backend of expectedBackends) {
		validateCatalogEvidence(fresh[backend], backend, identities.filter(identity => identity.backend === backend));
	}
	if (!SHA256_RECEIPT.test(String(receipt.receiptSha256))) {
		throw new LaunchAuthorizationError("native launch receipt self-digest is malformed");
	}
	const { receiptSha256: _claimedDigest, ...withoutDigest } = receipt;
	if (receipt.receiptSha256 !== prefixedCanonicalSha256(withoutDigest)) {
		throw new LaunchAuthorizationError("native launch receipt self-digest is invalid");
	}
}

function receiptLaunchAuthorization(
	role: RoleSpec,
	receipt: JsonObject,
	nativeMap: NativeMap,
	receiptBytes: string,
	launchEnvironment: Readonly<Record<string, string>>,
): NativeLaunchAuthorization {
	const resolved = plainObject(receipt.resolved)!;
	const backend = String(resolved.backend);
	const provider = String(resolved.provider);
	const catalogModelId = String(resolved.catalogModelId);
	const expectedArgv = expectedCatalogArgv(backend);
	const fresh = plainObject(plainObject(receipt.policy)!.freshCatalogEvidence)!;
	const evidence = plainObject(fresh[backend]);
	const command = plainObject(evidence?.command);
	if (!expectedArgv || !command) {
		throw new LaunchAuthorizationError(`resolved backend ${backend} has no authenticated native command`);
	}
	const route = classifyProvider(nativeMap, role.provider, catalogModelId);
	if (!route) {
		throw new LaunchAuthorizationError(
			`native map has no route for authenticated ${provider}/${catalogModelId}`,
		);
	}
	const authenticatedExecutable = String(command.executable);
	if (path.isAbsolute(route.bin)) {
		let configured: string;
		try {
			configured = fs.realpathSync(route.bin);
		} catch (error) {
			throw new LaunchAuthorizationError(
				`configured native entrypoint is unavailable: ${(error as Error).message}`,
				error,
			);
		}
		if (configured !== authenticatedExecutable) {
			throw new LaunchAuthorizationError(
				`configured native entrypoint ${configured} disagrees with freshly authenticated executable ${authenticatedExecutable}`,
			);
		}
	} else if (route.bin !== expectedArgv[0]) {
		throw new LaunchAuthorizationError(
			`native map command ${route.bin} disagrees with authenticated ${backend} entrypoint ${expectedArgv[0]}`,
		);
	}
	const mapped = route.modelMap?.[catalogModelId] ?? catalogModelId;
	if (mapped !== catalogModelId) {
		throw new LaunchAuthorizationError(
			`native map rewrites authenticated catalog id ${catalogModelId} as ${mapped}`,
		);
	}
	const authorization: NativeLaunchAuthorization = {
		backend,
		provider,
		logicalExecutable: expectedArgv[0],
		catalogModelId,
		...receiptExecutionClosure(command, backend),
		launchEnvironment: { ...launchEnvironment },
		launchEnvironmentSha256: nativeChildEnvironmentSha256(launchEnvironment as Record<string, string>),
		launchReceiptSha256: sha256Bytes(receiptBytes),
	};
	try {
		verifyNativeLaunchAuthorization(authorization);
	} catch (error) {
		throw new LaunchAuthorizationError(
			`fresh ${backend} executable authorization is invalid: ${(error as Error).message}`,
			error,
		);
	}
	return authorization;
}

function revalidatePendingLaunch(authorization: NativeLaunchAuthorization): void {
	try {
		verifyNativeLaunchAuthorization(authorization);
	} catch (error) {
		throw new LaunchAuthorizationError(
			`authenticated executable closure drifted before activation reservation: ${(error as Error).message}`,
			error,
		);
	}
}

async function nativeCall(
	runner: ReturnType<typeof makeNativeRunner>,
	launch: { modelArgument: string; effortArgs: string[]; authorization: NativeLaunchAuthorization },
	prompt: string,
	cwd: string,
	timeoutMs: number,
	workspaceAccess: "read" | "write" | "schedule",
	parentSignal?: AbortSignal,
): Promise<{ stdout: string; stderr: string; exitCode: number; terminationClass: string }> {
	const controller = new AbortController();
	const signal = parentSignal ? AbortSignal.any([controller.signal, parentSignal]) : controller.signal;
	const timer = setTimeout(() => controller.abort(new Error("node deadline exceeded")), timeoutMs);
	try {
		const result = await runner(
			["launch", "--model", launch.modelArgument, ...launch.effortArgs, "-p", prompt],
			{
				cwd,
				signal,
				env: { GENERAL_AGENT_WORKSPACE_ACCESS: workspaceAccess },
				authorization: launch.authorization,
			},
		);
		const terminationClass = classifyTermination(
			result.exitCode,
			result.stderr,
			signal.aborted,
			result.stdout,
		);
		return { ...result, terminationClass };
	} finally {
		clearTimeout(timer);
	}
}

export function classifyTermination(exitCode: number, stderr: string, aborted = false, stdout = ""): string {
	if (aborted) return "NODE_DEADLINE";
	return classifyNativeAttempt(exitCode, `${stderr}\n${stdout}`);
}

function checkpoint(identity: RunDirIdentity, state: ControllerState, clock: RunClock): void {
	state.elapsedMs = Math.max(state.elapsedMs, elapsedMs(clock));
	state.updatedAt = new Date().toISOString();
	atomicJson(identity, path.join(identity.path, "state.json"), state);
}

function assertResultReady(state: ControllerState): void {
	if (state.status === "running") throw new Error("workflow result requires a terminal status");
	if (state.completionPhase !== "finalized") {
		throw new Error(`${state.status} result requires finalized completion, not ${state.completionPhase}`);
	}
	if (state.inFlight || state.pending.length > 0 || state.frontier.length > 0) {
		throw new Error("workflow result refuses unresolved in-flight, pending, or frontier work");
	}
}

function buildResult(
	root: string,
	identity: RunDirIdentity,
	design: WorkflowDesign,
	state: ControllerState,
): JsonObject {
	assertResultReady(state);
	const evidence = evidenceManifest(identity);
	const launchReceipts = verifyLaunchReceiptLedger(identity, nativeTraceRecords(identity));
	if (launchReceipts.length !== state.modelCalls) {
		throw new Error(
			`workflow result requires one native launch receipt per model call (${launchReceipts.length} receipts, ${state.modelCalls} calls)`,
		);
	}
	if (state.launchReceiptsDigest !== launchReceiptsDigest(identity)) {
		throw new Error("workflow result launch receipt ledger disagrees with the checkpoint digest");
	}
	const reviewer = finalMachineReviewerVerdict(identity, design, state);
	if (state.status === "success" && reviewer && reviewer.reviewerTerminal !== "COMPLETE") {
		throw new Error(`success terminal cannot publish reviewerTerminal ${reviewer.reviewerTerminal}`);
	}
	if (reviewer?.reviewerTerminal === "HUMAN_BLOCKED" && state.completionPhase !== "finalized") {
		throw new Error("HUMAN_BLOCKED cannot publish before a finalized unresolved terminal");
	}
	if (stateBound(design) && state.status === "success") {
		if (!state.candidateDigest || reviewer?.evidenceCommitment !== `sha256:${state.candidateDigest}`) {
			throw new Error("state-bound success reviewer commitment disagrees with candidateDigest");
		}
		const proofPath = safeResolveExisting(root, state.proofPath, "workflow proof");
		const proofText = fs.readFileSync(proofPath, "utf8");
		const proofCommitments = proofText.split(/\r?\n/u)
			.filter(line => line.startsWith("EVIDENCE-COMMITMENT:"));
		if (!isDeepStrictEqual(proofCommitments, [`EVIDENCE-COMMITMENT: sha256:${state.candidateDigest}`])) {
			throw new Error("state-bound success proof commitment disagrees with candidateDigest");
		}
	}
	const proofBinding = stateBound(design) && state.status === "success"
		? loadStateBoundCandidate(root, identity.path).proofBinding
		: undefined;
	const runtimeTerminal = independentReview(design) && state.status === "success"
		? "unresolved"
		: reviewer?.reviewerTerminal === "HUMAN_BLOCKED" || reviewer?.reviewerTerminal === "REVISE"
			? "unresolved"
			: state.status;
	const nextAction = independentReview(design) && state.status === "success"
		? "external terminal reviewer required before COMPLETE"
		: reviewer?.reviewerTerminal === "HUMAN_BLOCKED"
			? reviewer.humanRequest!
			: reviewer?.reviewerTerminal === "REVISE"
				? "continue or re-dispatch workflow revision"
				: state.status === "unresolved" ? state.forcedReason : "";
	const result = {
		designId: state.designId,
		harness: "subagent-scheduler",
		runId: state.runId,
		terminal: runtimeTerminal,
		...(reviewer ? { reviewerTerminal: reviewer.reviewerTerminal } : {}),
		...(reviewer?.evidenceCommitment ? { evidenceCommitment: reviewer.evidenceCommitment } : {}),
		...(proofBinding ? {
			proofBinding,
			proofBindingDigest: proofBinding.digest,
			itemKind: proofBinding.itemKind,
			itemId: proofBinding.itemId,
		} : {}),
		...(reviewer?.humanRequestKind ? {
			humanRequestKind: reviewer.humanRequestKind,
			humanRequest: reviewer.humanRequest,
		} : {}),
		evidence,
		launchReceipts,
		launchReceiptsDigest: state.launchReceiptsDigest,
		metrics: {
			modelCalls: state.modelCalls,
			schedulerCalls: state.schedulerCalls,
			nodeCalls: state.nodeCalls,
			inputTokens: null,
			outputTokens: null,
			wallSeconds: Math.round(state.elapsedMs / 10) / 100,
		},
		nextAction,
		stateDigest: sha256Bytes(JSON.stringify(state)),
		...(state.proofPath ? { proof: { path: state.proofPath, sha256: state.proofSha256 } } : {}),
	};
	return result;
}

function writeResult(
	root: string,
	identity: RunDirIdentity,
	design: WorkflowDesign,
	state: ControllerState,
	clock: RunClock,
): JsonObject {
	void clock;
	const result = buildResult(root, identity, design, state);
	atomicJson(identity, path.join(identity.path, "result.json"), result);
	return result;
}

function initializeState(
	design: WorkflowDesign,
	designSha256: string,
	mappingSha256: string,
	rolesSha256: string,
	nativeMapSha256: string,
	inputSha256: string,
): ControllerState {
	const now = new Date().toISOString();
	return {
		version: 1,
		runId: `subagent-${Date.now()}-${process.pid}`,
		designId: design.id,
		designSha256,
		mappingSha256,
		rolesSha256,
		nativeMapSha256,
		inputSha256,
		status: "running",
		completionPhase: "running",
		terminal: "",
		frontier: [design.entry],
		pending: [],
		completed: [],
		inFlight: "",
		data: {},
		outputs: {},
		modelCalls: 0,
		schedulerCalls: 0,
		nodeCalls: 0,
		forcedReason: "",
		candidateDigest: "",
		launchReceiptsDigest: "",
		proofPath: "",
		proofSha256: "",
		startedAt: now,
		updatedAt: now,
		elapsedMs: 0,
	};
}

function validateCheckpoint(state: ControllerState, design: WorkflowDesign): void {
	if (state.version !== 1 || !/^subagent-[A-Za-z0-9-]+$/.test(state.runId)) {
		throw new Error("resume checkpoint has an invalid identity");
	}
	if (!["running", "success", "unresolved", "failure"].includes(state.status)) {
		throw new Error("resume checkpoint has an invalid status");
	}
	if (!(state as Partial<ControllerState>).completionPhase) {
		state.completionPhase = state.status === "running" ? "running" : "finalized";
	}
	if (!["running", "terminal", "candidate", "finalized"].includes(state.completionPhase)) {
		throw new Error("resume checkpoint has an invalid completion phase");
	}
	if (state.status === "running" && state.completionPhase !== "running") {
		throw new Error("running checkpoint has a non-running completion phase");
	}
	if (state.status !== "running" && state.status !== "success" && state.completionPhase !== "finalized") {
		throw new Error("non-success terminal checkpoint must be finalized");
	}
	for (const field of ["candidateDigest", "launchReceiptsDigest", "proofPath", "proofSha256"] as const) {
		if ((state as Partial<ControllerState>)[field] === undefined) state[field] = "";
		if (typeof state[field] !== "string") throw new Error(`resume checkpoint ${field} is invalid`);
	}
	if (state.candidateDigest && !/^[0-9a-f]{64}$/.test(state.candidateDigest)) {
		throw new Error("resume checkpoint candidateDigest is invalid");
	}
	if (state.launchReceiptsDigest && !/^[0-9a-f]{64}$/.test(state.launchReceiptsDigest)) {
		throw new Error("resume checkpoint launchReceiptsDigest is invalid");
	}
	if (state.proofSha256 && !/^[0-9a-f]{64}$/.test(state.proofSha256)) {
		throw new Error("resume checkpoint proofSha256 is invalid");
	}
	for (const field of ["designSha256", "mappingSha256", "rolesSha256", "nativeMapSha256", "inputSha256"] as const) {
		if (!/^[0-9a-f]{64}$/.test(state[field])) throw new Error(`resume checkpoint ${field} is invalid`);
	}
	const nodeIds = new Set(design.nodes.map(node => node.id));
	for (const [label, value] of [["frontier", state.frontier], ["completed", state.completed]] as const) {
		if (!Array.isArray(value) || value.some(item => typeof item !== "string" || !nodeIds.has(item))) {
			throw new Error(`resume checkpoint ${label} contains an unknown node`);
		}
	}
	if (state.inFlight && state.inFlight !== "scheduler" && !nodeIds.has(state.inFlight)) {
		throw new Error("resume checkpoint inFlight node is unknown");
	}
	if (state.pending === undefined) state.pending = [];
	if (!Array.isArray(state.pending) || state.pending.some(item => typeof item !== "string" || !nodeIds.has(item))) {
		throw new Error("resume checkpoint pending contains an unknown node");
	}
	if (!state.data || typeof state.data !== "object" || Array.isArray(state.data)) throw new Error("resume checkpoint data is malformed");
	if (!state.outputs || typeof state.outputs !== "object" || Array.isArray(state.outputs)) throw new Error("resume checkpoint outputs are malformed");
	for (const field of ["modelCalls", "schedulerCalls", "nodeCalls"] as const) {
		if (!Number.isInteger(state[field]) || state[field] < 0) throw new Error(`resume checkpoint ${field} is invalid`);
	}
	if (state.modelCalls !== state.schedulerCalls + state.nodeCalls) {
		throw new Error("resume checkpoint model-call counters are inconsistent");
	}
	const startedMs = Date.parse(state.startedAt);
	const updatedMs = Date.parse(state.updatedAt);
	if (
		!Number.isFinite(startedMs) || new Date(startedMs).toISOString() !== state.startedAt ||
		!Number.isFinite(updatedMs) || new Date(updatedMs).toISOString() !== state.updatedAt
	) {
		throw new Error("resume checkpoint timestamps must be canonical ISO-8601 instants");
	}
	const now = Date.now();
	if (startedMs > now || updatedMs > now) throw new Error("resume checkpoint timestamps must not be in the future");
	if (updatedMs < startedMs) throw new Error("resume checkpoint updatedAt precedes startedAt");
	if (typeof state.elapsedMs !== "number" || !Number.isFinite(state.elapsedMs) || state.elapsedMs < 0) {
		throw new Error("resume checkpoint elapsedMs is invalid");
	}
	const wallCap = design.budgets?.maxWallTimeSeconds;
	if (wallCap !== undefined) {
		const wallCapMs = Number(wallCap) * 1000;
		if (!Number.isFinite(wallCapMs) || wallCapMs <= 0 || state.elapsedMs > wallCapMs) {
			throw new Error("resume checkpoint elapsedMs exceeds the workflow wall-time cap");
		}
	}
	for (const [field, value] of Object.entries(state.data)) assertStateType(design, field, value);
}

function loadResume(
	identity: RunDirIdentity,
	design: WorkflowDesign,
	designSha256: string,
	mappingSha256: string,
	rolesSha256: string,
	nativeMapSha256: string,
	inputSha256: string,
): ControllerState {
	const state = readEvidenceObject<ControllerState>(identity, path.join(identity.path, "state.json"), "subagent checkpoint");
	validateCheckpoint(state, design);
	if (
		state.version !== 1 || state.designId !== design.id || state.designSha256 !== designSha256 ||
		state.mappingSha256 !== mappingSha256 || state.rolesSha256 !== rolesSha256 ||
		state.nativeMapSha256 !== nativeMapSha256 || state.inputSha256 !== inputSha256
	) {
		throw new Error("resume checkpoint is incompatible with the current design, mapping, roles, native map, or input");
	}
	if (state.status !== "running" && (!state.terminal || !state.completed.includes(state.terminal))) {
		const forcedTerminal = (state.status === "unresolved" || state.status === "failure") && Boolean(state.forcedReason) &&
			state.completionPhase === "finalized" && !state.inFlight && state.pending.length === 0 && state.frontier.length === 0;
		if (!forcedTerminal) throw new Error(`resume checkpoint is terminal without a completed terminal node: ${state.status}`);
	}
	return state;
}

function nativeTraceRecords(identity: RunDirIdentity): JsonObject[] {
	const tracePath = path.join(identity.path, "native-calls.jsonl");
	const raw = secureReadText(identity, tracePath);
	if (!raw.trim()) return [];
	const lines = raw.split(/\r?\n/u).filter(line => line.trim());
	const records: JsonObject[] = [];
	for (let index = 0; index < lines.length; index += 1) {
		let item: unknown;
		try {
			item = JSON.parse(lines[index]);
		} catch (error) {
			throw new Error(`native call trace line ${index + 1} is malformed: ${(error as Error).message}`);
		}
		if (!item || typeof item !== "object" || Array.isArray(item) || (item as JsonObject).call !== index + 1) {
			throw new Error(`native call trace line ${index + 1} has a non-contiguous call number`);
		}
		const record = item as JsonObject;
		const baseKeys = ["call", "effort", "model", "prompt", "time"];
		const executableKeys = [
			"argvSha256", "catalogModelId", "environmentSha256", "executable", "executableSha256",
			"executionClosureSha256", "invocationPrefixSha256", "launchEnvironmentSha256", "processExecutable",
		];
		const digestKeys = [
			"argvSha256", "controllerLaunchBindingSha256", "environmentSha256", "evidenceDigest",
			"executableSha256", "executionClosureSha256", "invocationPrefixSha256",
			"launchEnvironmentSha256", "launchReceiptSha256", "promptCommitmentDigest", "promptSha256",
			"protectedPathStateSha256", "reviewedInputsDigest", "reviewedStateDigest",
		];
		const pathKeys = ["controllerLaunchBindingPath", "launchReceiptPath", "promptPath"];
		const optionalKeys = [...digestKeys, ...pathKeys, ...executableKeys, "resumable", "sessionId"];
		const keys = Object.keys(record).sort();
		if (baseKeys.some(key => !keys.includes(key)) || keys.some(key => !baseKeys.includes(key) && !optionalKeys.includes(key)) ||
			typeof record.model !== "string" || !record.model ||
			(executableKeys.some(key => key in record) && executableKeys.some(key => !(key in record))) ||
			(record.catalogModelId !== undefined && !nonemptyString(record.catalogModelId)) ||
			(record.executable !== undefined && (!nonemptyString(record.executable) || !path.isAbsolute(String(record.executable)))) ||
			(record.processExecutable !== undefined &&
				(!nonemptyString(record.processExecutable) || !path.isAbsolute(String(record.processExecutable)))) ||
			(record.effort !== null && typeof record.effort !== "string") ||
			(record.resumable !== undefined && typeof record.resumable !== "boolean") ||
			(record.sessionId !== undefined && record.sessionId !== null && typeof record.sessionId !== "string") ||
			typeof record.prompt !== "string" || typeof record.time !== "string") {
			throw new Error(`native call trace line ${index + 1} has malformed metadata`);
		}
		for (const key of digestKeys) {
			if (key in record && (typeof record[key] !== "string" || !/^[0-9a-f]{64}$/.test(record[key] as string))) {
				throw new Error(`native call trace line ${index + 1} has malformed ${key}`);
			}
		}
		for (const key of pathKeys) {
			const value = record[key];
			if (key in record && (typeof value !== "string" || !value ||
				path.posix.normalize(value) !== value || path.posix.isAbsolute(value) || value.startsWith("../"))) {
				throw new Error(`native call trace line ${index + 1} has malformed ${key}`);
			}
		}
		if ("launchReceiptPath" in record && record.launchReceiptPath !== launchReceiptRelativePath(index + 1)) {
			throw new Error(`native call trace line ${index + 1} binds a non-canonical launch receipt path`);
		}
		if ("controllerLaunchBindingPath" in record &&
			record.controllerLaunchBindingPath !== controllerLaunchBindingRelativePath(index + 1)) {
			throw new Error(`native call trace line ${index + 1} binds a non-canonical controller launch binding path`);
		}
		const time = Date.parse(record.time);
		if (!Number.isFinite(time) || new Date(time).toISOString() !== record.time || time > Date.now()) {
			throw new Error(`native call trace line ${index + 1} has an invalid timestamp`);
		}
		records.push(record);
	}
	return records;
}

function nativeTraceCount(identity: RunDirIdentity): number {
	return nativeTraceRecords(identity).length;
}

function ambiguousInFlightReason(inFlight: string): string {
	return inFlight === "scheduler"
		? "AMBIGUOUS_SIDE_EFFECT: controller stopped while scheduler was in flight"
		: `AMBIGUOUS_SIDE_EFFECT: controller stopped while ${inFlight} was in flight`;
}

function reconcileResumeCalls(identity: RunDirIdentity, state: ControllerState, maxModelCalls: number): string {
	const traced = nativeTraceCount(identity);
	if (traced > maxModelCalls) throw new Error(`native call trace exceeds workflow model-call cap (${traced} > ${maxModelCalls})`);
	if (state.modelCalls > maxModelCalls) {
		throw new Error(`resume checkpoint exceeds workflow model-call cap (${state.modelCalls} > ${maxModelCalls})`);
	}
	if (traced > state.modelCalls) {
		const recorded = state.modelCalls;
		state.modelCalls = traced;
		state.status = "unresolved";
		state.forcedReason = `AMBIGUOUS_SIDE_EFFECT: trace records ${traced} model calls but checkpoint records ${recorded}`;
		return "";
	}
	if (state.modelCalls - traced > (state.inFlight ? 1 : 0)) {
		throw new Error(
			`resume checkpoint reserves ${state.modelCalls} model calls but trace records ${traced} without a single explainable in-flight reservation`,
		);
	}
	if (!state.inFlight && traced !== state.modelCalls) {
		throw new Error(`resume checkpoint modelCalls=${state.modelCalls} disagrees with native trace count ${traced}`);
	}
	if (state.inFlight) {
		state.status = "unresolved";
		state.forcedReason = ambiguousInFlightReason(state.inFlight);
		return state.inFlight;
	}
	return "";
}

function readCompletedResponse(
	identity: RunDirIdentity,
	directory: string,
	stem: string,
	label: string,
): JsonObject {
	const responseDir = path.join(identity.path, directory);
	const stdout = secureReadText(identity, path.join(responseDir, `${stem}.stdout`));
	const stderr = secureReadText(identity, path.join(responseDir, `${stem}.stderr`));
	const receipt = readEvidenceObject<JsonObject>(identity, path.join(responseDir, `${stem}.result.json`), `${label} receipt`);
	if (
		receipt.exitCode !== 0 || receipt.terminationClass !== "COMPLETED" ||
		receipt.stdoutSha256 !== sha256Bytes(stdout) || receipt.stderrSha256 !== sha256Bytes(stderr)
	) throw new Error(`${label} response receipt does not match completed response bytes`);
	return lastJsonObject(stdout, label);
}

interface TerminalReviewerVerdict {
	reviewerTerminal: "COMPLETE" | "HUMAN_BLOCKED" | "REVISE";
	evidenceCommitment?: string;
	humanRequestKind?: "authorization" | "decision" | "credential" | "external-action";
	humanRequest?: string;
}

function parseTerminalReviewerVerdict(text: string): TerminalReviewerVerdict | undefined {
	const lines = text.split(/\r?\n/u).map(line => line.trim()).filter(Boolean);
	const terminalLines = lines.filter(line => line.startsWith("TERMINAL:"));
	if (terminalLines.length === 0) return undefined;
	if (terminalLines.length !== 1) throw new Error("terminal reviewer returned multiple TERMINAL lines");
	const reviewerTerminal = terminalLines[0].slice("TERMINAL:".length).trim();
	if (!TERMINAL_REVIEW_VERDICTS.has(reviewerTerminal)) {
		throw new Error(`terminal reviewer returned unsupported TERMINAL verdict ${reviewerTerminal || "<empty>"}`);
	}
	const commitmentLines = lines.filter(line => line.startsWith("EVIDENCE-COMMITMENT:"));
	if (commitmentLines.length !== 1) {
		throw new Error("terminal reviewer requires exactly one EVIDENCE-COMMITMENT line");
	}
	const evidenceCommitment = commitmentLines[0].slice("EVIDENCE-COMMITMENT:".length).trim();
	if (!/^sha256:[0-9a-f]{64}$/u.test(evidenceCommitment)) {
		throw new Error("terminal reviewer EVIDENCE-COMMITMENT must be sha256:<64hex>");
	}
	const kindLines = lines.filter(line => line.startsWith("HUMAN-REQUEST-KIND:"));
	const requestLines = lines.filter(line => line.startsWith("HUMAN-REQUEST:"));
	if (reviewerTerminal !== "HUMAN_BLOCKED") {
		if (kindLines.length > 0 || requestLines.length > 0) {
			throw new Error("terminal reviewer may declare HUMAN-REQUEST fields only with HUMAN_BLOCKED");
		}
		return { reviewerTerminal: reviewerTerminal as "COMPLETE" | "REVISE", evidenceCommitment };
	}
	if (kindLines.length !== 1 || requestLines.length !== 1) {
		throw new Error("HUMAN_BLOCKED requires exactly one HUMAN-REQUEST-KIND and HUMAN-REQUEST line");
	}
	const humanRequestKind = kindLines[0].slice("HUMAN-REQUEST-KIND:".length).trim();
	const humanRequest = requestLines[0].slice("HUMAN-REQUEST:".length).trim();
	if (!HUMAN_REQUEST_KINDS.has(humanRequestKind)) {
		throw new Error(`HUMAN_BLOCKED has unsupported HUMAN-REQUEST-KIND ${humanRequestKind || "<empty>"}`);
	}
	if (!humanRequest) throw new Error("HUMAN_BLOCKED requires a non-empty HUMAN-REQUEST");
	if (NON_HUMAN_BLOCKER.test(humanRequest)) {
		throw new Error("test failure, budgets, tool/model trouble, and unfinished technical work are REVISE, not HUMAN_BLOCKED");
	}
	return {
		reviewerTerminal: "HUMAN_BLOCKED",
		evidenceCommitment,
		humanRequestKind: humanRequestKind as TerminalReviewerVerdict["humanRequestKind"],
		humanRequest,
	};
}

function finalMachineReviewerVerdict(
	identity: RunDirIdentity,
	design: WorkflowDesign,
	state: ControllerState,
): TerminalReviewerVerdict | undefined {
	if (!machineGated(design)) return undefined;
	const reviews = readEvidenceJsonLines(
		identity,
		path.join(identity.path, "machine-reviews.jsonl"),
		"machine review ledger",
	);
	const latest = reviews.at(-1);
	if (!latest || typeof latest.nodeId !== "string" || typeof latest.verdict !== "string") return undefined;
	const stdout = secureReadText(identity, path.join(identity.path, "native-responses", `${latest.nodeId}.stdout`));
	const output = readCompletedResponse(identity, "native-responses", latest.nodeId, `decision node ${latest.nodeId}`);
	const explicit = parseTerminalReviewerVerdict(stdout) ??
		(typeof output.summary === "string" ? parseTerminalReviewerVerdict(output.summary) : undefined);
	if (explicit) return explicit;
	if (state.status === "success" && APPROVAL_VERDICTS.has(latest.verdict)) {
		return {
			reviewerTerminal: "COMPLETE",
			...(state.candidateDigest ? { evidenceCommitment: `sha256:${state.candidateDigest}` } : {}),
		};
	}
	return undefined;
}

function verifyCommandRunWriteReceipt(
	identity: RunDirIdentity,
	nodeId: string,
	expectedPaths: string[],
): void {
	const receipt = readEvidenceObject<JsonObject>(
		identity,
		path.join(identity.path, "command-responses", `${nodeId}.result.json`),
		`command node ${nodeId} receipt`,
	);
	if (!Array.isArray(receipt.runWrites)) throw new Error(`command node ${nodeId} receipt has no runWrites binding`);
	const actual = receipt.runWrites.map((raw, index) => {
		if (!raw || typeof raw !== "object" || Array.isArray(raw) ||
			!isDeepStrictEqual(Object.keys(raw as JsonObject).sort(), ["path", "sha256"]) ||
			typeof (raw as JsonObject).path !== "string" || typeof (raw as JsonObject).sha256 !== "string") {
			throw new Error(`command node ${nodeId} runWrites receipt entry ${index + 1} is malformed`);
		}
		return raw as { path: string; sha256: string };
	});
	if (!isDeepStrictEqual(actual.map(item => item.path), [...expectedPaths].sort())) {
		throw new Error(`command node ${nodeId} runWrites receipt disagrees with mapping`);
	}
	for (const binding of actual) {
		const bytes = readRegularBytesNoFollow(path.join(identity.path, binding.path));
		if (sha256Bytes(bytes) !== binding.sha256) throw new Error(`command node ${nodeId} runWrite bytes changed: ${binding.path}`);
	}
}

const LAUNCH_RECEIPT_SCHEMA = "workflow-native-launch-receipt/v1";
const LAUNCH_RECEIPT_DIRECTORY = "launch-receipts";
const CONTROLLER_LAUNCH_BINDING_SCHEMA = "workflow-controller-launch-binding/v1";
const CONTROLLER_LAUNCH_BINDING_DIRECTORY = "launch-bindings";

interface LaunchReceiptBinding {
	call: number;
	path: string;
	sha256: string;
	controllerBindingPath: string;
	controllerBindingSha256: string;
}

interface BuiltLaunchArtifacts {
	receipt: JsonObject;
	receiptBytes: string;
	controllerBinding: JsonObject;
	controllerBindingBytes: string;
	launch: { modelArgument: string; effortArgs: string[]; authorization: NativeLaunchAuthorization };
}

function canonicalRoleIdentity(roleName: string, role: RoleSpec): JsonObject {
	return {
		semanticRole: roleName,
		backend: role.backend,
		provider: role.provider,
		upstreamProviderFamily: role.upstreamProviderFamily!,
		model: role.model,
		modelFamily: role.modelFamily!,
		effort: role.effort!,
		intelligenceClass: role.intelligenceClass!,
		dispatchStatus: role.dispatchStatus!,
	};
}

function controllerReviewBindings(
	design: WorkflowDesign,
	roles: RoleDocument,
	nativeMap: NativeMap,
	activation: string,
	roleName: string,
	role: RoleSpec,
	reviewerIdentity: ResolvedRoleIdentity,
): JsonObject[] {
	return validateMachineGatedDesign(design)
		.filter(pair => pair.reviewer === activation)
		.sort((left, right) => left.writer.localeCompare(right.writer))
		.map(pair => {
			const writerNode = design.nodes.find(node => node.id === pair.writer)!;
			const writerRoleName = writerNode.role ?? "task";
			const writerRole = resolveRole(roles, writerRoleName);
			const writerIdentity = actualNativeProviderIdentity(nativeMap, writerRole, true);
			assertRoleDispatchEligible(roles, writerRoleName, writerRole, writerIdentity, "dispatch");
			const exactOppositePolicy = assertExactReviewPair(
				roles, pair, roleName, writerRole, role, writerIdentity, reviewerIdentity,
			);
			return {
				writerNodeId: pair.writer,
				writerRole: writerRoleName,
				writerUpstreamProviderFamily: writerIdentity.upstreamProviderFamily,
				writerModelFamily: writerIdentity.modelFamily,
				writerEffort: writerRole.effort!,
				exactOppositePolicy,
				reviewerRole: roleName,
				reviewerUpstreamProviderFamily: reviewerIdentity.upstreamProviderFamily,
				reviewerModelFamily: reviewerIdentity.modelFamily,
				reviewerEffort: role.effort!,
			};
		});
}

/** Resolver-equivalent workflow-native-launch-receipt/v1 construction. */
function buildLaunchArtifacts(
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	roles: RoleDocument,
	nativeMap: NativeMap,
	state: ControllerState,
	activation: string,
	roleName: string,
	workspaceAccess: "read" | "write" | "schedule",
	rolesPath: string,
	resolveLaunchSpec: LaunchResolver,
	// The 1-based model call this launch will become. Callers that authorize before
	// consuming budget pass the pending number; replay passes the recorded one.
	call: number = state.modelCalls,
	// Replay validates the exact persisted receipt and reconstructs its controller
	// binding without performing a new catalog query. This is internal evidence
	// verification, never a caller-facing launch authorization seam.
	replayedReceipt?: JsonObject,
): BuiltLaunchArtifacts {
	validateModelRolePolicy(roles);
	const role = resolveRole(roles, roleName);
	const nativeIdentity = actualNativeProviderIdentity(nativeMap, role, true);
	assertRoleDispatchEligible(roles, roleName, role, nativeIdentity, "dispatch");
	const reviewBindings = controllerReviewBindings(
		design, roles, nativeMap, activation, roleName, role, nativeIdentity,
	);
	let writerRoleName: string | null = null;
	let reviewMode: "none" | "exact-opposite" = "none";
	if (role.intelligenceClass === "verification") {
		if (reviewBindings.length === 0) {
			throw new Error(`verification role ${roleName} requires an exact-opposite reviewed writer`);
		}
		writerRoleName = String(reviewBindings[0].writerRole);
		reviewMode = "exact-opposite";
	} else if (reviewBindings.length > 0) {
		throw new Error(`non-verification role ${roleName} cannot carry exact-opposite review bindings`);
	}
	const resolverRequest: LaunchSpecRequest = {
		taskClass: policyTaskClass(roles, roleName),
		reasoningPosture: declaredPosture(mapping, roles),
		semanticRole: roleName,
		reviewMode,
		writerRole: writerRoleName,
	};
	let receipt: JsonObject;
	if (replayedReceipt) {
		receipt = replayedReceipt;
	} else {
		try {
			receipt = resolveLaunchSpec(resolverRequest);
		} catch (error) {
			if (error instanceof LaunchAuthorizationError) throw error;
			throw new LaunchAuthorizationError(
				`canonical fresh-catalog resolver failed: ${(error as Error).message}`,
				error,
			);
		}
	}
	validateLaunchReceipt(receipt, resolverRequest, roles, roleName, role, state.rolesSha256);
	const receiptBytes = `${canonicalJson(receipt)}\n`;
	// Derive the launch argv here, at the same point the receipt is committed, so
	// the binding, the trace, and the process all reference one model id.
	const modelLaunch = receiptLaunchSpec(role, receipt);
	const authorization = receiptLaunchAuthorization(
		role,
		receipt,
		nativeMap,
		receiptBytes,
		resolveLaunchSpec.launchEnvironment,
	);
	const launch = { ...modelLaunch, authorization };
	const controllerWithoutDigest: JsonObject = {
		schema: CONTROLLER_LAUNCH_BINDING_SCHEMA,
		runId: state.runId,
		designId: state.designId,
		call,
		activation,
		workspaceAccess,
		receipt: { path: launchReceiptRelativePath(call), sha256: sha256Bytes(receiptBytes) },
		execution: {
			semanticRole: roleName,
			dispatchEligibility: nativeIdentity.dispatchEligibility,
			executionHarness: authorization.executable,
			processExecutable: authorization.invocationPrefix[0],
			executionProviderFamily: nativeIdentity.executionProviderFamily,
			mappedModel: authorization.catalogModelId,
			launchedModelArgument: launch.modelArgument,
			launchedEffortArgs: launch.effortArgs,
			executableSha256: authorization.executableSha256.slice("sha256:".length),
			executionClosureSha256: authorization.executionClosureSha256.slice("sha256:".length),
			invocationPrefixSha256: nativeInvocationPrefixSha256(authorization.invocationPrefix),
			launchEnvironmentSha256: authorization.launchEnvironmentSha256,
		},
		controller: {
			designSha256: state.designSha256,
			mappingSha256: state.mappingSha256,
			nativeMapSha256: state.nativeMapSha256,
			inputSha256: state.inputSha256,
		},
		reviewPairings: reviewBindings,
	};
	const controllerBinding = {
		...controllerWithoutDigest,
		bindingSha256: `sha256:${sha256Bytes(canonicalJson(controllerWithoutDigest))}`,
	};
	return {
		receipt,
		receiptBytes,
		controllerBinding,
		controllerBindingBytes: `${canonicalJson(controllerBinding)}\n`,
		launch,
	};
}

function launchReceiptRelativePath(call: number): string {
	return path.posix.join(LAUNCH_RECEIPT_DIRECTORY, `${call}.json`);
}

function controllerLaunchBindingRelativePath(call: number): string {
	return path.posix.join(CONTROLLER_LAUNCH_BINDING_DIRECTORY, `${call}.json`);
}

/**
 * The canonical receipt bytes for one activation, exposed so that evidence reconstruction
 * outside the run loop derives the receipt from this single definition instead of restating
 * the schema. The controller writes exactly these bytes before it launches the process.
 */
export function canonicalLaunchReceipt(request: {
	design: WorkflowDesign;
	mapping: WorkflowMapping;
	roles: RoleDocument;
	nativeMap: NativeMap;
	runId: string;
	designSha256: string;
	mappingSha256: string;
	rolesSha256: string;
	nativeMapSha256: string;
	inputSha256: string;
	call: number;
	activation: string;
	role: string;
	workspaceAccess: "read" | "write" | "schedule";
	root: string;
	rolesPath: string;
}): {
	path: string;
	sha256: string;
	bytes: string;
	receipt: JsonObject;
	controllerBindingPath: string;
	controllerBindingSha256: string;
	controllerBindingBytes: string;
	controllerBinding: JsonObject;
} {
	const state = initializeState(
		request.design,
		request.designSha256,
		request.mappingSha256,
		request.rolesSha256,
		request.nativeMapSha256,
		request.inputSha256,
	);
	state.runId = request.runId;
	state.modelCalls = request.call;
	const resolver = createBoundLaunchResolver(
		request.root,
		{ path: resolveRepoPath(request.root, request.rolesPath), sha256: request.rolesSha256 },
	);
	const built = buildLaunchArtifacts(
		request.design, request.mapping, request.roles, request.nativeMap, state,
		request.activation, request.role, request.workspaceAccess,
		request.rolesPath, resolver,
	);
	return {
		path: launchReceiptRelativePath(request.call),
		sha256: sha256Bytes(built.receiptBytes),
		bytes: built.receiptBytes,
		receipt: built.receipt,
		controllerBindingPath: controllerLaunchBindingRelativePath(request.call),
		controllerBindingSha256: sha256Bytes(built.controllerBindingBytes),
		controllerBindingBytes: built.controllerBindingBytes,
		controllerBinding: built.controllerBinding,
	};
}

function numberedLaunchArtifactNames(identity: RunDirIdentity, directoryName: string, label: string): string[] {
	const directory = path.join(identity.path, directoryName);
	if (!fs.existsSync(directory)) return [];
	ensureEvidenceDirectory(identity, directory);
	const names = fs.readdirSync(directory).sort();
	for (const name of names) {
		if (!/^[1-9][0-9]*\.json$/u.test(name)) {
			throw new Error(`${label} directory contains an unsupported entry ${name}`);
		}
	}
	return names;
}

function validateControllerLaunchBinding(binding: JsonObject, call: number): void {
	const expectedKeys = [
		"activation", "bindingSha256", "call", "controller", "designId", "execution", "receipt",
		"reviewPairings", "runId", "schema", "workspaceAccess",
	];
	if (!isDeepStrictEqual(Object.keys(binding).sort(), expectedKeys) ||
		binding.schema !== CONTROLLER_LAUNCH_BINDING_SCHEMA || binding.call !== call ||
		!nonemptyString(binding.runId) || !nonemptyString(binding.designId) || !nonemptyString(binding.activation) ||
		!["read", "write", "schedule"].includes(String(binding.workspaceAccess)) ||
		!Array.isArray(binding.reviewPairings)) {
		throw new Error(`controller launch binding ${call} is malformed`);
	}
	const receipt = plainObject(binding.receipt);
	const execution = plainObject(binding.execution);
	const controller = plainObject(binding.controller);
	if (!receipt || !isDeepStrictEqual(Object.keys(receipt).sort(), ["path", "sha256"]) ||
		receipt.path !== launchReceiptRelativePath(call) ||
		typeof receipt.sha256 !== "string" || !/^[0-9a-f]{64}$/u.test(receipt.sha256) ||
		!execution || !isDeepStrictEqual(Object.keys(execution).sort(), [
			"dispatchEligibility", "executableSha256", "executionClosureSha256", "executionHarness",
			"executionProviderFamily", "invocationPrefixSha256", "launchEnvironmentSha256",
			"launchedEffortArgs", "launchedModelArgument", "mappedModel", "processExecutable", "semanticRole",
		]) ||
		!Array.isArray(execution.launchedEffortArgs) ||
		execution.launchedEffortArgs.some(value => !nonemptyString(value)) ||
		Object.entries(execution).some(([key, value]) => key !== "launchedEffortArgs" && !nonemptyString(value)) ||
		!path.isAbsolute(String(execution.processExecutable)) ||
		["executableSha256", "executionClosureSha256", "invocationPrefixSha256", "launchEnvironmentSha256"]
			.some(key => !/^[0-9a-f]{64}$/u.test(String(execution[key]))) ||
		!controller || !isDeepStrictEqual(Object.keys(controller).sort(), [
			"designSha256", "inputSha256", "mappingSha256", "nativeMapSha256",
		]) || Object.values(controller).some(value => typeof value !== "string" || !/^[0-9a-f]{64}$/u.test(value))) {
		throw new Error(`controller launch binding ${call} has malformed provenance`);
	}
	const claimed = binding.bindingSha256;
	const { bindingSha256: _bindingSha256, ...withoutDigest } = binding;
	if (claimed !== `sha256:${sha256Bytes(canonicalJson(withoutDigest))}`) {
		throw new Error(`controller launch binding ${call} self-digest is invalid`);
	}
}

function readControllerLaunchBinding(identity: RunDirIdentity, call: number): JsonObject {
	const binding = readEvidenceObject<JsonObject>(
		identity,
		path.join(identity.path, controllerLaunchBindingRelativePath(call)),
		`controller launch binding ${call}`,
	);
	validateControllerLaunchBinding(binding, call);
	return binding;
}

function launchReceiptBindings(identity: RunDirIdentity): LaunchReceiptBinding[] {
	const receiptNames = numberedLaunchArtifactNames(identity, LAUNCH_RECEIPT_DIRECTORY, "native launch receipt");
	const controllerNames = numberedLaunchArtifactNames(
		identity, CONTROLLER_LAUNCH_BINDING_DIRECTORY, "controller launch binding",
	);
	if (!isDeepStrictEqual(receiptNames, controllerNames)) {
		throw new Error("native launch receipts and controller launch bindings do not have identical call coverage");
	}
	const bindings = receiptNames.map(name => {
		const call = Number(name.slice(0, -".json".length));
		const relative = launchReceiptRelativePath(call);
		const sha256 = sha256Bytes(secureReadText(identity, path.join(identity.path, relative)));
		const controllerBinding = readControllerLaunchBinding(identity, call);
		const receiptBinding = plainObject(controllerBinding.receipt)!;
		if (receiptBinding.path !== relative || receiptBinding.sha256 !== sha256) {
			throw new Error(`controller launch binding ${call} disagrees with its canonical receipt bytes`);
		}
		const controllerBindingPath = controllerLaunchBindingRelativePath(call);
		return {
			call,
			path: relative,
			sha256,
			controllerBindingPath,
			controllerBindingSha256: sha256Bytes(secureReadText(identity, path.join(identity.path, controllerBindingPath))),
		};
	});
	bindings.sort((left, right) => left.call - right.call);
	for (let index = 0; index < bindings.length; index += 1) {
		if (bindings[index].call !== index + 1) {
			throw new Error(`native launch receipt numbering is not contiguous at ${bindings[index].call}`);
		}
	}
	return bindings;
}

function launchReceiptsDigest(identity: RunDirIdentity): string {
	return sha256Bytes(canonicalJson(launchReceiptBindings(identity)));
}

function writeLaunchArtifacts(identity: RunDirIdentity, call: number, built: BuiltLaunchArtifacts): void {
	ensureEvidenceDirectory(identity, path.join(identity.path, LAUNCH_RECEIPT_DIRECTORY));
	ensureEvidenceDirectory(identity, path.join(identity.path, CONTROLLER_LAUNCH_BINDING_DIRECTORY));
	const receiptFile = path.join(identity.path, launchReceiptRelativePath(call));
	const controllerBindingFile = path.join(identity.path, controllerLaunchBindingRelativePath(call));
	if (evidenceFileExists(identity, receiptFile) || evidenceFileExists(identity, controllerBindingFile)) {
		throw new Error(`native launch evidence ${call} already exists before its activation`);
	}
	secureAtomicWrite(identity, receiptFile, built.receiptBytes);
	secureAtomicWrite(identity, controllerBindingFile, built.controllerBindingBytes);
}

function readLaunchReceipt(identity: RunDirIdentity, call: number): JsonObject {
	return readEvidenceObject<JsonObject>(
		identity,
		path.join(identity.path, launchReceiptRelativePath(call)),
		`native launch receipt ${call}`,
	);
}

function receiptLaunchTraceBinding(receipt: JsonObject, receiptSha256: string): JsonObject {
	const resolved = plainObject(receipt.resolved);
	const backend = String(resolved?.backend ?? "");
	const fresh = plainObject(plainObject(receipt.policy)?.freshCatalogEvidence);
	const evidence = plainObject(fresh?.[backend]);
	const command = plainObject(evidence?.command);
	if (!resolved || !command) throw new Error("native launch receipt has no traceable executable authorization");
	const closure = receiptExecutionClosure(command, backend);
	return {
		model: `${String(resolved.provider)}/${String(resolved.catalogModelId)}`,
		effort: backend === "codex" || backend === "claude" ? resolved.effort : null,
		catalogModelId: resolved.catalogModelId,
		executable: closure.executable,
		processExecutable: closure.invocationPrefix[0],
		executableSha256: closure.executableSha256.slice("sha256:".length),
		executionClosureSha256: closure.executionClosureSha256.slice("sha256:".length),
		invocationPrefixSha256: nativeInvocationPrefixSha256(closure.invocationPrefix),
		launchReceiptSha256: receiptSha256,
	};
}

function verifyNativeAttemptLaunchBindings(
	identity: RunDirIdentity,
	bindings: LaunchReceiptBinding[],
	trace: JsonObject[],
): void {
	const attempts = readEvidenceJsonLines(
		identity,
		path.join(identity.path, "native-attempts.jsonl"),
		"native physical-attempt ledger",
	);
	const firstAttemptByCall = new Set<number>();
	for (const [index, attempt] of attempts.entries()) {
		const call = attempt.call;
		if (!Number.isInteger(call) || Number(call) < 1 || Number(call) > bindings.length) {
			throw new Error(`native physical-attempt line ${index + 1} has no launch receipt`);
		}
		const binding = bindings[Number(call) - 1];
		const expected = receiptLaunchTraceBinding(readLaunchReceipt(identity, Number(call)), binding.sha256);
		for (const key of [
			"catalogModelId", "executable", "processExecutable", "executableSha256", "executionClosureSha256",
			"invocationPrefixSha256", "launchReceiptSha256",
		]) {
			if (attempt[key] !== expected[key]) {
				throw new Error(`native physical-attempt line ${index + 1} ${key} disagrees with its launch receipt`);
			}
		}
		if (typeof attempt.argvSha256 !== "string" || !/^[0-9a-f]{64}$/u.test(attempt.argvSha256)) {
			throw new Error(`native physical-attempt line ${index + 1} has no actual argv digest`);
		}
		const traceRecord = trace[Number(call) - 1];
		if (attempt.environmentSha256 !== traceRecord.environmentSha256) {
			throw new Error(`native physical-attempt line ${index + 1} environmentSha256 disagrees with its native trace`);
		}
		if (!firstAttemptByCall.has(Number(call))) {
			if (attempt.argvSha256 !== traceRecord.argvSha256) {
				throw new Error(`native physical-attempt line ${index + 1} argvSha256 disagrees with its native trace`);
			}
			firstAttemptByCall.add(Number(call));
		}
	}
}

/**
 * Bind the receipt ledger to the native trace. The receipt is written before the runner appends
 * its trace line, so the only tolerated asymmetry is one trailing receipt for an activation that
 * never reached the trace.
 */
function verifyLaunchReceiptLedger(identity: RunDirIdentity, trace: JsonObject[]): LaunchReceiptBinding[] {
	const bindings = launchReceiptBindings(identity);
	if (bindings.length !== trace.length && bindings.length !== trace.length + 1) {
		throw new Error(
			`native launch receipt ledger records ${bindings.length} activations for ${trace.length} native calls`,
		);
	}
	for (let index = 0; index < trace.length; index += 1) {
		const record = trace[index];
		const binding = bindings[index];
		if (!binding) throw new Error(`native call ${index + 1} has no launch receipt`);
		const expectedLaunch = receiptLaunchTraceBinding(readLaunchReceipt(identity, index + 1), binding.sha256);
		for (const [key, expected] of Object.entries(expectedLaunch)) {
			if (record[key] !== expected) {
				throw new Error(`native call ${index + 1} ${key} disagrees with its authenticated launch receipt`);
			}
		}
		const controllerBinding = JSON.parse(
			readRegularBytesNoFollow(path.join(identity.path, binding.controllerBindingPath)).toString("utf8"),
		) as JsonObject;
		const execution = plainObject(controllerBinding.execution);
		if (!execution || record.launchEnvironmentSha256 !== execution.launchEnvironmentSha256) {
			throw new Error(`native call ${index + 1} launch environment disagrees with its controller binding`);
		}
		// The native runner always records launchReceiptSha256 before starting the process.
		// An activation that aborts after the process but before the controller annotation
		// therefore has that digest without the three controller-owned binding fields.
		// The digest was checked against the receipt above; require the complete controller
		// annotation only when any controller-owned field is present.
		const controllerAnnotated = record.launchReceiptPath !== undefined ||
			record.controllerLaunchBindingPath !== undefined || record.controllerLaunchBindingSha256 !== undefined;
		if (controllerAnnotated && (record.launchReceiptPath !== binding.path ||
			record.controllerLaunchBindingPath !== binding.controllerBindingPath ||
			record.controllerLaunchBindingSha256 !== binding.controllerBindingSha256)) {
			throw new Error(`native call ${index + 1} launch receipt binding disagrees with the persisted receipt`);
		}
	}
	verifyNativeAttemptLaunchBindings(identity, bindings, trace);
	return bindings;
}

interface ReviewInputBinding {
	promptSha256: string;
	promptPath: string;
	promptCommitmentDigest: string;
	reviewedInputsDigest: string;
	reviewedStateDigest: string;
	evidenceDigest: string;
}

type ReviewDigestBinding = Pick<
	ReviewInputBinding,
	"reviewedInputsDigest" | "reviewedStateDigest" | "evidenceDigest"
> & { proofBinding?: ProofBinding };

function annotateNativeTrace(
	identity: RunDirIdentity,
	call: number,
	metadata: Partial<ReviewInputBinding> & {
		launchReceiptPath?: string;
		launchReceiptSha256?: string;
		controllerLaunchBindingPath?: string;
		controllerLaunchBindingSha256?: string;
	},
): string {
	const tracePath = path.join(identity.path, "native-calls.jsonl");
	const records = readEvidenceJsonLines(identity, tracePath, "native call trace");
	if (records.length !== call || records[call - 1]?.call !== call) {
		throw new Error(`cannot bind metadata to native call ${call}`);
	}
	for (const key of Object.keys(metadata)) {
		if (records[call - 1][key] !== undefined && records[call - 1][key] !== metadata[key as keyof typeof metadata]) {
			throw new Error(`native call ${call} already has conflicting ${key}`);
		}
	}
	records[call - 1] = { ...records[call - 1], ...metadata };
	secureAtomicWrite(identity, tracePath, `${records.map(record => JSON.stringify(record)).join("\n")}\n`);
	return sha256File(tracePath);
}

function bindDecisionInputs(
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	node: DesignNode,
	state: ControllerState,
	identity: RunDirIdentity,
	root: string,
	workflowInput: WorkflowProofInput,
	frozenProof?: FrozenArtifactSet,
): ReviewDigestBinding {
	let reviewedInputs: JsonObject = declaredReadValues(node, state);
	let reviewedStateDigest = "";
	let evidenceDigest = "";
	if (stateBound(design)) {
		const proof = mapping.proof!;
		const itemId = state.data[proof.itemIdField];
		if (typeof itemId !== "string" || !frozenProof) throw new Error(`state-bound decision ${node.id} has no proof binding context`);
		const proofBinding = captureRunProofBinding(root, workflowInput, itemId, frozenProof);
		reviewedInputs = { ...reviewedInputs, proofBinding };
		const evidencePath = state.data[proof.evidencePathField];
		if (typeof evidencePath !== "string" || !evidencePath) {
			throw new Error(`state-bound decision ${node.id} has no final evidence path in ${proof.evidencePathField}`);
		}
		const workspace = captureWorkspaceEvidence(identity.path, evidencePath);
		evidenceDigest = workspace.evidenceDigest;
		reviewedStateDigest = workspace.finalStateDigest;
		if (proof.evidenceDigestField) {
			const declared = state.data[proof.evidenceDigestField];
			if (declared !== evidenceDigest) throw new Error(`state-bound decision ${node.id} evidence digest state is stale`);
		}
	}
	const reviewedInputsDigest = sha256Bytes(canonicalJson(reviewedInputs));
	if (!reviewedStateDigest || !evidenceDigest) {
		reviewedStateDigest = reviewedInputsDigest;
		evidenceDigest = reviewedInputsDigest;
	}
	return {
		reviewedInputsDigest,
		reviewedStateDigest,
		evidenceDigest,
		...(stateBound(design) ? { proofBinding: (reviewedInputs as JsonObject).proofBinding as ProofBinding } : {}),
	};
}

function reviewPromptCommitment(node: DesignNode, state: ControllerState, input: ReviewDigestBinding): JsonObject {
	const { proofBinding, ...digests } = input;
	return {
		schema: "subagent-review-prompt-commitment/v1",
		nodeId: node.id,
		declaredReads: declaredReadValues(node, state),
		...(proofBinding ? { proofBinding } : {}),
		...digests,
	};
}

function appendMachineReviewRecord(
	identity: RunDirIdentity,
	node: DesignNode,
	output: JsonObject,
	activation: number,
	call: number,
	input: ReviewInputBinding,
	traceSha256: string,
): void {
	if (node.kind !== "decision" || typeof output.verdict !== "string") return;
	const responsePath = path.posix.join("native-responses", `${node.id}.stdout`);
	const receiptPath = path.posix.join("native-responses", `${node.id}.result.json`);
	const response = secureReadText(identity, path.join(identity.path, responsePath));
	const receipt = secureReadText(identity, path.join(identity.path, receiptPath));
	appendEvent(identity, path.join(identity.path, "machine-reviews.jsonl"), {
		activation,
		call,
		nodeId: node.id,
		verdict: output.verdict,
		...input,
		nativeTracePath: "native-calls.jsonl",
		traceSha256,
		responsePath,
		responseSha256: sha256Bytes(response),
		receiptPath,
		receiptSha256: sha256Bytes(receipt),
	});
}

function validateMachineReviewLedger(identity: RunDirIdentity, design: WorkflowDesign): void {
	const events = readEvidenceJsonLines(identity, path.join(identity.path, "events.jsonl"), "event journal");
	const starts = new Map<string, JsonObject>();
	const completedDecisions: string[] = [];
	for (const event of events) {
		if (event.event === "node_start" && typeof event.nodeId === "string") starts.set(event.nodeId, event);
		if (event.event === "node_end" && typeof event.nodeId === "string") {
			const node = design.nodes.find(item => item.id === event.nodeId);
			if (node?.kind === "decision") completedDecisions.push(node.id);
		}
	}
	const records = readEvidenceJsonLines(
		identity,
		path.join(identity.path, "machine-reviews.jsonl"),
		"machine review ledger",
	);
	if (records.length !== completedDecisions.length) {
		throw new Error(`machine review ledger has ${records.length} records for ${completedDecisions.length} completed decisions`);
	}
	let previousActivation = -1;
	for (let index = 0; index < records.length; index += 1) {
		const nodeId = completedDecisions[index];
		const record = records[index];
		const expectedKeys = [
			"activation", "call", "evidenceDigest", "nativeTracePath", "nodeId", "promptSha256",
			"promptCommitmentDigest", "promptPath", "receiptPath", "receiptSha256", "responsePath", "responseSha256", "reviewedInputsDigest",
			"reviewedStateDigest", "traceSha256", "verdict",
		].sort();
		if (!isDeepStrictEqual(Object.keys(record).sort(), expectedKeys)) {
			throw new Error(`machine review record ${index + 1} has malformed fields`);
		}
		const start = starts.get(nodeId);
		if (!start || !Number.isInteger(start.modelCalls) || record.activation !== start.modelCalls ||
			(record.activation as number) <= previousActivation) {
			throw new Error(`machine review record ${index + 1} has an invalid activation`);
		}
		previousActivation = record.activation as number;
		if (record.call !== start.modelCalls) throw new Error(`machine review record ${index + 1} binds the wrong native call`);
		if (record.nodeId !== nodeId) throw new Error(`machine review record ${index + 1} names the wrong decision`);
		const responsePath = path.posix.join("native-responses", `${nodeId}.stdout`);
		const promptPath = path.posix.join("native-responses", `${nodeId}.prompt`);
		const receiptPath = path.posix.join("native-responses", `${nodeId}.result.json`);
		if (record.promptPath !== promptPath || record.responsePath !== responsePath || record.receiptPath !== receiptPath) {
			throw new Error(`machine review record ${index + 1} has non-canonical response paths`);
		}
		if (record.nativeTracePath !== "native-calls.jsonl") throw new Error(`machine review record ${index + 1} has a non-canonical trace path`);
		for (const field of ["promptSha256", "promptCommitmentDigest", "reviewedInputsDigest", "reviewedStateDigest", "evidenceDigest", "traceSha256"] as const) {
			if (typeof record[field] !== "string" || !/^[0-9a-f]{64}$/.test(record[field] as string)) {
				throw new Error(`machine review record ${index + 1} has malformed ${field}`);
			}
		}
		const traceRecords = nativeTraceRecords(identity);
		const traceCall = traceRecords[(record.call as number) - 1];
		const tracePrefix = `${traceRecords.slice(0, record.call as number).map(item => JSON.stringify(item)).join("\n")}\n`;
		if (record.traceSha256 !== sha256Bytes(tracePrefix)) throw new Error(`machine review record ${index + 1} has a stale trace-prefix digest`);
		if (!traceCall || traceCall.promptSha256 !== record.promptSha256 ||
			traceCall.promptPath !== record.promptPath || traceCall.promptCommitmentDigest !== record.promptCommitmentDigest ||
			traceCall.reviewedInputsDigest !== record.reviewedInputsDigest ||
			traceCall.reviewedStateDigest !== record.reviewedStateDigest || traceCall.evidenceDigest !== record.evidenceDigest) {
			throw new Error(`machine review record ${index + 1} disagrees with native trace review inputs`);
		}
		const response = secureReadText(identity, path.join(identity.path, responsePath));
		const prompt = secureReadText(identity, path.join(identity.path, promptPath));
		const receipt = secureReadText(identity, path.join(identity.path, receiptPath));
		if (record.promptSha256 !== sha256Bytes(prompt) || record.responseSha256 !== sha256Bytes(response) || record.receiptSha256 !== sha256Bytes(receipt)) {
			throw new Error(`machine review record ${index + 1} has stale response or receipt digests`);
		}
		const output = readCompletedResponse(identity, "native-responses", nodeId, `decision node ${nodeId}`);
		if (record.verdict !== output.verdict) throw new Error(`machine review record ${index + 1} verdict disagrees with response`);
		const receiptObject = readEvidenceObject<JsonObject>(
			identity,
			path.join(identity.path, receiptPath),
			`decision node ${nodeId} receipt`,
		);
		for (const field of ["call", "promptPath", "promptSha256", "promptCommitmentDigest", "reviewedInputsDigest", "reviewedStateDigest", "evidenceDigest"] as const) {
			if (receiptObject[field] !== record[field]) throw new Error(`machine review record ${index + 1} ${field} disagrees with receipt`);
		}
	}
}

function deterministicReplayOutput(
	identity: RunDirIdentity,
	root: string,
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	node: DesignNode,
	state: ControllerState,
	input: JsonObject,
): JsonObject {
	const handler = mapping.handlers[node.id];
	declaredReadValues(node, state);
	if (handler.type === "input") {
		const patches: JsonObject[] = [];
		for (const [field, source] of Object.entries(handler.bindings)) {
			const value = typeof source === "string" ? input[source] : source.const;
			if (value === undefined) throw new Error(`workflow input has no value for ${field}`);
			patches.push({ op: "set", path: `/${field}`, value });
		}
		return { summary: "seeded workflow state", statePatch: patches };
	}
	if (handler.type === "increment") {
		const current = state.data[handler.field];
		if (typeof current !== "number") throw new Error(`increment field ${handler.field} is not numeric`);
		const patches: JsonObject[] = [{ op: "set", path: `/${handler.field}`, value: current + (handler.by ?? 1) }];
		for (const [field, value] of Object.entries(handler.set ?? {})) patches.push({ op: "set", path: `/${field}`, value });
		return { summary: `incremented ${handler.field}`, statePatch: patches };
	}
	if (handler.type === "template") {
		return {
			summary: "rendered deterministic state",
			statePatch: Object.entries(handler.values).map(([field, template]) => ({
				op: "set",
				path: `/${field}`,
				value: renderForNode(template, node, state, controllerRenderValues(root, identity, state)),
			})),
		};
	}
	if (handler.type === "command") {
		const output = readCompletedResponse(identity, "command-responses", node.id, `command node ${node.id}`);
		verifyCommandRunWriteReceipt(identity, node.id, handler.runWrites ?? []);
		return output;
	}
	if (handler.type === "agent" || handler.type === "decision") {
		return readCompletedResponse(identity, "native-responses", node.id, `node ${node.id}`);
	}
	if (handler.type === "terminal") return { summary: `${node.id} completed` };
	throw new Error(`unsupported replay handler for ${node.id}`);
}

function applyReplayedOutput(
	design: WorkflowDesign,
	node: DesignNode,
	handler: Handler,
	state: ControllerState,
	output: JsonObject,
): void {
	if (["input", "increment", "template", "command", "agent"].includes(handler.type)) {
		applyPatch(design, node, state, output);
	} else if (handler.type === "decision") {
		const verdict = output.verdict;
		if (typeof verdict !== "string" || !(node.outcomes ?? []).includes(verdict)) {
			throw new MalformedDecisionError(`decision node ${node.id} returned an undeclared verdict`);
		}
		const writes = node.writes ?? [];
		if (writes.length > 1) throw new Error(`decision node ${node.id} may write at most one reason field in v1`);
		if (writes.length === 1) {
			const value = String(output.summary ?? output.reason ?? "");
			assertStateType(design, writes[0], value);
			state.data[writes[0]] = value;
		}
	}
	state.outputs[node.id] = output;
	state.completed = [...new Set([...state.completed, node.id])].sort();
	state.frontier = state.frontier.filter(item => item !== node.id);
	for (const target of enforcedOutgoing(design, state, node)) {
		if (!state.completed.includes(target) && !state.frontier.includes(target)) state.frontier.push(target);
	}
	state.frontier = runnable(design, state, state.frontier);
	state.pending = state.pending.filter(item => item !== node.id);
	if (node.kind === "terminal") {
		state.terminal = node.id;
		state.status = node.outcome === "success" || design.terminals.success === node.id
			? "success"
			: node.outcome === "failure" || design.terminals.failure === node.id
				? "failure"
				: "unresolved";
		state.completionPhase = state.status === "success" && stateBound(design) ? "terminal" : "finalized";
	}
}

function validateResumeProvenance(
	identity: RunDirIdentity,
	state: ControllerState,
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	input: JsonObject,
	roles: RoleDocument,
	nativeMap: NativeMap,
	root: string,
	rolesPath: string,
	resolveLaunchSpec: LaunchResolver,
	frozenProof?: FrozenArtifactSet,
): void {
	const replay = initializeState(
		design,
		state.designSha256,
		state.mappingSha256,
		state.rolesSha256,
		state.nativeMapSha256,
		state.inputSha256,
	);
	replay.runId = state.runId;
	replay.startedAt = state.startedAt;
	replay.updatedAt = state.updatedAt;
	replay.elapsedMs = state.elapsedMs;
	const events = readEvidenceJsonLines(identity, path.join(identity.path, "events.jsonl"), "event journal");
	const decisions = readEvidenceJsonLines(
		identity,
		path.join(identity.path, "scheduler-decisions.jsonl"),
		"scheduler decision journal",
	);
	const trace = nativeTraceRecords(identity);
	const receiptBindings = verifyLaunchReceiptLedger(identity, trace);
	const replayedReceiptCalls = new Set<number>();
	// The replayed receipt is what authorizes the launched model id, so the trace
	// check reads the launch spec recomputed during receipt replay rather than
	// re-deriving it from the role's configured base model.
	const replayedLaunchSpecs = new Map<number, { modelArgument: string; effortArgs: string[] }>();
	const validateTraceIntent = (call: number, roleName: string, prompt: string, label: string): void => {
		const record = trace[call - 1];
		if (!record) return;
		const launch = replayedLaunchSpecs.get(call);
		if (!launch) throw new Error(`${label} native-call trace has no replayed launch authorization`);
		const expectedEffort = launch.effortArgs.length === 2 ? launch.effortArgs[1] : null;
		const materialized = materializeWorkflowSkills(prompt, root);
		const expectedPrompt = materialized.split(/\r?\n/u).find(line => line.trim())?.trim().slice(0, 240) ?? "";
		if (record.model !== launch.modelArgument || record.effort !== expectedEffort || record.prompt !== expectedPrompt ||
			(record.promptSha256 !== undefined && record.promptSha256 !== sha256Bytes(materialized))) {
			throw new Error(`${label} native-call trace metadata disagrees with the replayed invocation`);
		}
	};
	const validateLaunchReceiptIntent = (
		call: number,
		activation: string,
		roleName: string,
		workspaceAccess: "read" | "write" | "schedule",
		label: string,
	): void => {
		if (!receiptBindings[call - 1]) throw new Error(`${label} has no persisted native launch receipt`);
		const replayState = { ...replay, modelCalls: call, runId: state.runId, designId: state.designId };
		const stored = readLaunchReceipt(identity, call);
		const expected = buildLaunchArtifacts(
			design, mapping, roles, nativeMap, replayState as ControllerState, activation, roleName, workspaceAccess,
			rolesPath, resolveLaunchSpec, call, stored,
		);
		const storedControllerBinding = readControllerLaunchBinding(identity, call);
		if (canonicalJson(stored) !== canonicalJson(expected.receipt) ||
			canonicalJson(storedControllerBinding) !== canonicalJson(expected.controllerBinding)) {
			throw new Error(`${label} native launch receipt disagrees with the replayed activation`);
		}
		replayedReceiptCalls.add(call);
		replayedLaunchSpecs.set(call, expected.launch);
	};
	let decisionIndex = 0;
	let modelNodeStarts = 0;
	let openNode = "";
	let terminalAttempt = "";
	let terminalEventSeen = false;
	let reconciledUnattributedCalls = 0;
	let lastEventMs = Date.parse(state.startedAt);
	let lastElapsedMs = 0;
	const validateTime = (event: JsonObject, label: string): void => {
		if (typeof event.time !== "string") throw new Error(`${label} has no timestamp`);
		const time = Date.parse(event.time);
		if (!Number.isFinite(time) || new Date(time).toISOString() !== event.time || time < lastEventMs || time > Date.now()) {
			throw new Error(`${label} has an invalid or non-monotonic timestamp`);
		}
		if (typeof event.elapsedMs !== "number" || !Number.isFinite(event.elapsedMs) ||
			event.elapsedMs < lastElapsedMs || event.elapsedMs > state.elapsedMs) {
			throw new Error(`${label} has invalid or non-monotonic elapsedMs`);
		}
		lastEventMs = time;
		lastElapsedMs = event.elapsedMs;
	};
	const primePending = (): void => {
		if (replay.pending.length > 0) return;
		const legal = runnable(design, replay, replay.frontier);
		if (legal.length === 0) throw new Error("replayed workflow has no legal runnable frontier");
		if (mapping.scheduler.mode === "deterministic-all") {
			replay.pending = legal;
			return;
		}
		const needsScheduler = mapping.scheduler.mode === "every-frontier" || legal.length > 1;
		if (!needsScheduler) {
			replay.pending = legal;
			return;
		}
		const decision = decisions[decisionIndex];
		if (!decision) throw new Error("event journal requires a missing scheduler decision");
		validateTime(decision, `scheduler decision ${decisionIndex + 1}`);
		if (!isDeepStrictEqual(decision.legalFrontier, legal)) throw new Error("scheduler decision legal frontier does not match replayed frontier");
		const selected = normalizeArray(decision.selected, "scheduler decision selected");
		if (selected.length === 0 || selected.some(nodeId => !legal.includes(nodeId))) {
			throw new Error("scheduler decision selected outside the replayed legal frontier");
		}
		const response = readCompletedResponse(
			identity,
			"scheduler-responses",
			String(decisionIndex + 1),
			`scheduler ${decisionIndex + 1}`,
		);
		if (response.verdict !== "RUN" || typeof response.summary !== "string") throw new Error("scheduler replay response is not RUN");
		let body: unknown;
		try {
			body = JSON.parse(response.summary);
		} catch (error) {
			throw new Error(`scheduler replay summary is malformed: ${(error as Error).message}`);
		}
		const responseSelected = normalizeArray((body as JsonObject)?.nodeIds, "scheduler replay nodeIds");
		if (!isDeepStrictEqual(responseSelected, selected)) throw new Error("scheduler response disagrees with decision journal");
		decisionIndex += 1;
		if (decision.modelCalls !== decisionIndex + modelNodeStarts) throw new Error("scheduler decision model-call counter is inconsistent");
		validateLaunchReceiptIntent(
			decision.modelCalls as number,
			"scheduler",
			mapping.scheduler.role,
			"schedule",
			`scheduler ${decisionIndex}`,
		);
		validateTraceIntent(
			decision.modelCalls as number,
			mapping.scheduler.role,
			[
				"You are a workflow scheduler. You cannot execute work or change state.",
			].join("\n"),
			`scheduler ${decisionIndex}`,
		);
		replay.pending = selected;
	};
	const validateSchedulerInFlight = (): void => {
		if (openNode || replay.pending.length > 0) throw new Error("scheduler inFlight conflicts with replayed node selection");
		const legal = runnable(design, replay, replay.frontier);
		if (mapping.scheduler.mode === "deterministic-all") throw new Error("deterministic-all workflow cannot have a scheduler in flight");
		if (mapping.scheduler.mode === "ambiguous-only" && legal.length <= 1) throw new Error("scheduler inFlight is not required by the replayed frontier");
	};

	for (let index = 0; index < events.length; index += 1) {
		const event = events[index];
		if (event.event === "node_start") {
			if (openNode) throw new Error("event journal starts a node while another node is in flight");
			if (typeof event.nodeId !== "string") throw new Error("node_start has no nodeId");
			primePending();
			validateTime(event, `event ${index + 1}`);
			if (replay.pending[0] !== event.nodeId || !replay.frontier.includes(event.nodeId)) {
				throw new Error(`node ${event.nodeId} is not the next node in a legal DAG prefix`);
			}
			const node = design.nodes.find(item => item.id === event.nodeId)!;
			const handler = mapping.handlers[node.id];
			if (handler.type === "agent" || handler.type === "decision") {
				modelNodeStarts += 1;
				const built = buildModelPrompt(node, handler, design, mapping, replay, root, identity, input, frozenProof);
				validateLaunchReceiptIntent(
					event.modelCalls as number,
					node.id,
					node.role ?? "task",
					node.workspaceAccess ?? "read",
					`node ${node.id}`,
				);
				validateTraceIntent(
					event.modelCalls as number,
					node.role ?? "task",
					built.prompt,
					`node ${node.id}`,
				);
				const traceRecord = trace[(event.modelCalls as number) - 1];
				if (stateBound(design) || traceRecord?.promptPath !== undefined) {
					const promptBytes = secureReadText(identity, path.join(identity.path, built.promptPath));
					if (promptBytes !== built.materializedPrompt || traceRecord?.promptPath !== built.promptPath ||
						traceRecord?.promptSha256 !== built.promptSha256) {
						throw new Error(`node ${node.id} stored prompt bytes disagree with the replayed invocation`);
					}
				}
				if (built.reviewInput && (stateBound(design) || traceRecord?.promptCommitmentDigest !== undefined) &&
					(traceRecord?.promptCommitmentDigest !== built.reviewInput.promptCommitmentDigest ||
					traceRecord?.reviewedInputsDigest !== built.reviewInput.reviewedInputsDigest ||
					traceRecord?.reviewedStateDigest !== built.reviewInput.reviewedStateDigest ||
					traceRecord?.evidenceDigest !== built.reviewInput.evidenceDigest)) {
					throw new Error(`node ${node.id} prompt commitment disagrees with replayed review inputs`);
				}
			}
			if (event.modelCalls !== decisionIndex + modelNodeStarts) throw new Error(`node_start ${node.id} has inconsistent modelCalls`);
			openNode = node.id;
			continue;
		}
		validateTime(event, `event ${index + 1}`);
		if (event.event === "failure" || event.event === "resume_reconciled") {
			if (index !== events.length - 1) throw new Error(`${event.event} must be the last event journal record`);
			const legacyFailure = event.event === "failure" && event.inFlight === undefined && event.status === undefined;
			const eventInFlight = legacyFailure
				? openNode || (state.schedulerCalls === decisionIndex + 1 ? "scheduler" : "")
				: event.inFlight;
			const eventStatus = legacyFailure ? state.status : event.status;
			if (typeof eventInFlight !== "string") throw new Error(`${event.event} has no inFlight binding`);
			if (legacyFailure && (state.status === "running" || state.completionPhase !== "finalized" || state.inFlight ||
				state.pending.length > 0 || state.frontier.length > 0 || event.reason !== state.forcedReason)) {
				throw new Error("legacy failure event disagrees with the finalized checkpoint");
			}
			if (eventInFlight === "scheduler") validateSchedulerInFlight();
			else if (eventInFlight) {
				if (openNode !== eventInFlight) throw new Error(`${event.event} does not match the replayed open node`);
			} else if (openNode) throw new Error(`${event.event} omits the replayed open node`);
			if (event.event === "resume_reconciled") {
				const recordedModelCalls = event.recordedModelCalls;
				const unattributedModelCalls = event.unattributedModelCalls;
				if (!Number.isInteger(recordedModelCalls) || (recordedModelCalls as number) < 0 ||
					!Number.isInteger(unattributedModelCalls) || (unattributedModelCalls as number) < 0 ||
					(recordedModelCalls as number) + (unattributedModelCalls as number) !== trace.length) {
					throw new Error("resume_reconciled has invalid model-call counters");
				}
				const expectedReason = eventInFlight
					? ambiguousInFlightReason(eventInFlight)
					: `AMBIGUOUS_SIDE_EFFECT: trace records ${trace.length} model calls but checkpoint records ${recordedModelCalls}`;
				if (eventStatus !== "unresolved" || event.reason !== expectedReason ||
					(eventInFlight ? unattributedModelCalls !== 0 : (unattributedModelCalls as number) < 1)) {
					throw new Error("resume_reconciled has an invalid forced-unresolved binding");
				}
				reconciledUnattributedCalls = unattributedModelCalls as number;
			} else if ((eventStatus !== "unresolved" && eventStatus !== "failure") ||
				typeof event.reason !== "string" || !event.reason) {
				throw new Error("failure event has an invalid terminal binding");
			}
			terminalAttempt = eventInFlight;
			terminalEventSeen = true;
			replay.status = eventStatus as "unresolved" | "failure";
			replay.forcedReason = event.reason as string;
			replay.inFlight = "";
			replay.pending = [];
			replay.frontier = [];
			replay.completionPhase = "finalized";
			openNode = "";
			continue;
		}
		if (event.event !== "node_end" || typeof event.nodeId !== "string") throw new Error(`event ${index + 1} has unsupported shape`);
		if (!openNode || openNode !== event.nodeId) throw new Error(`node_end ${event.nodeId} has no matching node_start`);
		const node = design.nodes.find(item => item.id === openNode)!;
		const output = deterministicReplayOutput(identity, root, design, mapping, node, replay, input);
		if (event.outputDigest !== sha256Bytes(JSON.stringify(output))) throw new Error(`node_end ${node.id} output digest disagrees with source evidence`);
		applyReplayedOutput(design, node, mapping.handlers[node.id], replay, output);
		if (!isDeepStrictEqual(event.frontier, replay.frontier)) throw new Error(`node_end ${node.id} frontier disagrees with replayed frontier`);
		openNode = "";
	}

	if (!terminalEventSeen && !openNode && state.pending.length > 0) primePending();
	if (terminalEventSeen) {
		if (state.inFlight) throw new Error("terminally reconciled checkpoint retains inFlight work");
	} else if (state.inFlight === "scheduler") validateSchedulerInFlight();
	else if (state.inFlight !== openNode) {
		throw new Error("checkpoint inFlight disagrees with the replayed event journal");
	}
	if (decisionIndex !== decisions.length) throw new Error("scheduler decision journal has unused decisions");
	const expectedSchedulerCalls = decisionIndex + (terminalAttempt === "scheduler" || state.inFlight === "scheduler" ? 1 : 0);
	if (state.schedulerCalls !== expectedSchedulerCalls || state.nodeCalls !== modelNodeStarts + reconciledUnattributedCalls ||
		state.modelCalls !== state.schedulerCalls + state.nodeCalls) {
		throw new Error("checkpoint call counters disagree with replayed attempts");
	}
	if (!isDeepStrictEqual(state.completed, replay.completed)) throw new Error("checkpoint completed nodes disagree with replayed legal DAG prefix");
	if (!isDeepStrictEqual(state.frontier, replay.frontier)) throw new Error("checkpoint frontier disagrees with replayed legal DAG prefix");
	if (!isDeepStrictEqual(state.pending, replay.pending)) throw new Error("checkpoint pending selection disagrees with replayed scheduler state");
	const unexpectedOutputs = Object.keys(state.outputs).filter(nodeId => !state.completed.includes(nodeId));
	if (unexpectedOutputs.length > 0) throw new Error(`checkpoint outputs include nodes that are not completed: ${unexpectedOutputs.join(", ")}`);
	if (!isDeepStrictEqual(state.outputs, replay.outputs)) throw new Error("checkpoint outputs disagree with replayed source evidence");
	if (!isDeepStrictEqual(state.data, replay.data)) throw new Error("checkpoint data disagrees with replayed outputs and deterministic handlers");
	if (state.terminal !== replay.terminal || state.status !== replay.status) throw new Error("checkpoint terminal status disagrees with replayed DAG prefix");
	if (state.completionPhase === "running" || state.completionPhase === "terminal") {
		if (state.completionPhase !== replay.completionPhase) throw new Error("checkpoint completion phase disagrees with replayed DAG prefix");
	} else if (replay.completionPhase !== "terminal" && state.completionPhase !== replay.completionPhase) {
		throw new Error("checkpoint finalized phase disagrees with replayed DAG prefix");
	}
	if (state.forcedReason !== replay.forcedReason) throw new Error("checkpoint forcedReason disagrees with replayed workflow state");
	// Every persisted receipt must belong to a replayed activation. The tolerated exceptions are the
	// receipt written immediately before an activation the checkpoint still reports as in flight, and
	// the receipts of native calls the replay cannot attribute at all - both are crash windows the
	// receipt exists to expose, and both already force the run unresolved.
	const unattributedCalls = Math.max(reconciledUnattributedCalls, trace.length - state.modelCalls, 0);
	const inFlightReceipts = (state.inFlight || terminalAttempt ? 1 : 0) + unattributedCalls;
	const explained = [...replayedReceiptCalls].sort((left, right) => left - right);
	if (explained.length + inFlightReceipts < receiptBindings.length) {
		throw new Error(
			`native launch receipt ledger records ${receiptBindings.length} activations but replay explains ${explained.length}`,
		);
	}
	for (let index = 0; index < explained.length; index += 1) {
		if (explained[index] !== index + 1) throw new Error("native launch receipt replay coverage is not contiguous");
	}
	if (state.launchReceiptsDigest && state.launchReceiptsDigest !== launchReceiptsDigest(identity)) {
		throw new Error("checkpoint launch receipt digest disagrees with the persisted receipt ledger");
	}
	// The ledger itself is proven against the replay above, so the digest the run publishes is
	// re-derived from the proven receipts instead of trusted from the checkpoint.
	state.launchReceiptsDigest = launchReceiptsDigest(identity);
}

function assertRuntimeInputsStable(
	state: ControllerState,
	paths: { design: string; mapping: string; roles: string; nativeMap: string; input: string },
): void {
	const expected: Record<keyof typeof paths, string> = {
		design: state.designSha256,
		mapping: state.mappingSha256,
		roles: state.rolesSha256,
		nativeMap: state.nativeMapSha256,
		input: state.inputSha256,
	};
	for (const [label, file] of Object.entries(paths) as Array<[keyof typeof paths, string]>) {
		if (sha256File(file) !== expected[label]) throw new Error(`workflow runtime input drifted: ${label}`);
	}
}

async function chooseNodes(
	mapping: WorkflowMapping,
	design: WorkflowDesign,
	state: ControllerState,
	runner: ReturnType<typeof makeNativeRunner>,
	roles: RoleDocument,
	nativeMap: NativeMap,
	root: string,
	identity: RunDirIdentity,
	clock: RunClock,
	assertRuntimeStable: () => void,
	remainingMs: number,
	rolesPath: string,
	resolveLaunchSpec: LaunchResolver,
): Promise<string[]> {
	const legal = runnable(design, state, state.frontier);
	if (legal.length === 0) throw new Error("workflow has no runnable frontier");
	if (mapping.scheduler.mode === "deterministic-all") return legal;
	if (mapping.scheduler.mode === "ambiguous-only" && legal.length === 1) return legal;
	if (state.schedulerCalls >= mapping.scheduler.maxCalls) throw new Error("scheduler call cap reached");
	if (state.modelCalls >= Number(design.budgets?.maxModelCalls ?? 0)) {
		throw new Error("workflow model-call cap reached before scheduler");
	}
	const visibleState = Object.fromEntries(
		(mapping.scheduler.reads ?? [])
			.filter(field => Object.hasOwn(state.data, field))
			.map(field => [field, state.data[field]]),
	);
	const prompt = [
		"You are a workflow scheduler. You cannot execute work or change state.",
		"Choose one or more node IDs only from the legal frontier. Prefer all mutually independent nodes.",
		"Return exactly one review-style JSON object on the final line:",
		'{"verdict":"RUN","summary":"{\\"nodeIds\\":[\\"node-id\\"],\\"reason\\":\\"short reason\\"}"}',
		`Design: ${design.id}`,
		`Legal frontier: ${JSON.stringify(legal)}`,
		`Completed: ${JSON.stringify(state.completed)}`,
		`Permitted state projection: ${JSON.stringify(visibleState)}`,
	].join("\n\n");
	const callNumber = state.schedulerCalls + 1;
	if (runner.getModelCalls() !== state.modelCalls) throw new Error("native runner and controller model-call counters diverged");
	// Resolve and validate the complete receipt before reserving budget or mutating
	// activation state. Authorization errors are fatal and leave no partial call
	// ledger behind.
	const pendingCall = state.modelCalls + 1;
	const launchArtifacts = buildLaunchArtifacts(
		design, mapping, roles, nativeMap, state, "scheduler", mapping.scheduler.role, "schedule",
		rolesPath, resolveLaunchSpec, pendingCall,
	);
	revalidatePendingLaunch(launchArtifacts.launch.authorization);
	state.inFlight = "scheduler";
	state.schedulerCalls = callNumber;
	state.modelCalls = pendingCall;
	writeLaunchArtifacts(identity, state.modelCalls, launchArtifacts);
	state.launchReceiptsDigest = launchReceiptsDigest(identity);
	checkpoint(identity, state, clock);
	verifyEvidenceTree(identity);
	assertEvidenceFile(identity, path.join(identity.path, "native-calls.jsonl"), false);
	const beforeCall = evidenceSnapshot(identity);
	const result = await nativeCall(
		runner,
		launchArtifacts.launch,
		prompt,
		root,
		Math.min(Math.floor(mapping.scheduler.timeoutSeconds! * 1000), remainingMs),
		"schedule",
		clock.signal,
	);
	verifyExternalCallEvidence(identity, beforeCall, state.modelCalls);
	if (runner.getModelCalls() !== state.modelCalls) {
		throw new Error(`scheduler native runner refused before activation: ${result.stderr}`);
	}
	annotateNativeTrace(identity, state.modelCalls, {
		launchReceiptPath: launchReceiptRelativePath(state.modelCalls),
		launchReceiptSha256: sha256Bytes(launchArtifacts.receiptBytes),
		controllerLaunchBindingPath: controllerLaunchBindingRelativePath(state.modelCalls),
		controllerLaunchBindingSha256: sha256Bytes(launchArtifacts.controllerBindingBytes),
	});
	assertRuntimeStable();
	verifyEvidenceTree(identity);
	const responseDir = path.join(identity.path, "scheduler-responses");
	ensureEvidenceDirectory(identity, responseDir);
	secureAtomicWrite(identity, path.join(responseDir, `${callNumber}.stdout`), result.stdout);
	secureAtomicWrite(identity, path.join(responseDir, `${callNumber}.stderr`), result.stderr);
	atomicJson(identity, path.join(responseDir, `${callNumber}.result.json`), {
		model: launchArtifacts.launch.modelArgument,
		legalFrontier: legal,
		visibleStateFields: mapping.scheduler.reads ?? [],
		exitCode: result.exitCode,
		terminationClass: result.terminationClass,
		stdoutSha256: sha256Bytes(result.stdout),
		stderrSha256: sha256Bytes(result.stderr),
	});
	if (result.terminationClass !== "COMPLETED") throw new Error(`scheduler failed (${result.terminationClass}): ${result.stderr}`);
	const envelope = lastJsonObject(result.stdout, "scheduler");
	if (envelope.verdict !== "RUN" || typeof envelope.summary !== "string") {
		throw new Error("scheduler returned an invalid RUN envelope");
	}
	let decision: unknown;
	try {
		decision = JSON.parse(envelope.summary);
	} catch (error) {
		throw new Error(`scheduler summary is not JSON: ${(error as Error).message}`);
	}
	if (!decision || typeof decision !== "object" || Array.isArray(decision)) throw new Error("scheduler decision is not an object");
	const selected = normalizeArray((decision as JsonObject).nodeIds, "scheduler nodeIds");
	if (selected.length === 0 || selected.some(nodeId => !legal.includes(nodeId))) {
		throw new Error("scheduler selected a node outside the legal frontier");
	}
	appendEvent(identity, path.join(identity.path, "scheduler-decisions.jsonl"), {
		time: new Date().toISOString(),
		elapsedMs: elapsedMs(clock),
		legalFrontier: legal,
		selected,
		modelCalls: state.modelCalls,
	});
	state.pending = selected;
	state.inFlight = "";
	checkpoint(identity, state, clock);
	return selected;
}

function statePatchPrompt(node: DesignNode): string {
	return [
		`Declared reads: ${JSON.stringify(node.reads ?? [])}`,
		`Declared writes: ${JSON.stringify(node.writes ?? [])}`,
		"End with a WorkflowActivationOutput JSON object. Set every declared write exactly once and no other field.",
	].join("\n");
}

function materializationMarker(node: DesignNode): string {
	const required = node.skills?.required ?? [];
	const onDemand = node.skills?.onDemand ?? [];
	if (required.length === 0 && onDemand.length === 0) return "";
	return `<!-- workflow-skills/v1 ${JSON.stringify({ required, onDemand })} -->\n`;
}

function preflightWorkflowSkills(design: WorkflowDesign, root: string): void {
	for (const node of design.nodes) {
		const marker = materializationMarker(node);
		if (!marker) continue;
		// Reuse the native runner's materializer so preflight and invocation have
		// exactly the same path, schema, overlap, and symlink-containment rules.
		materializeWorkflowSkills(marker, root);
	}
}

interface BuiltModelPrompt {
	prompt: string;
	materializedPrompt: string;
	promptSha256: string;
	promptPath: string;
	reviewInput?: ReviewInputBinding;
}

function buildModelPrompt(
	node: DesignNode,
	handler: Extract<Handler, { type: "agent" | "decision" }>,
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	state: ControllerState,
	root: string,
	identity: RunDirIdentity,
	workflowInput: WorkflowProofInput,
	frozenProof?: FrozenArtifactSet,
): BuiltModelPrompt {
	const reviewDigests = handler.type === "decision"
		? bindDecisionInputs(design, mapping, node, state, identity, root, workflowInput, frozenProof)
		: undefined;
	let promptTemplate = handler.prompt;
	let promptCommitmentDigest: string | undefined;
	if (reviewDigests) {
		const commitment = reviewPromptCommitment(node, state, reviewDigests);
		const commitmentJson = canonicalJson(commitment);
		promptCommitmentDigest = sha256Bytes(commitmentJson);
		const tagged = `<workflow-review-commitment>${commitmentJson}</workflow-review-commitment>`;
		if (promptTemplate.includes("{{review.commitment}}")) {
			promptTemplate = promptTemplate.replace("{{review.commitment}}", tagged);
		} else {
			promptTemplate += `\n\n${tagged}`;
		}
	}
	let prompt = materializationMarker(node) + renderForNode(promptTemplate, node, state);
	if (handler.type === "agent") prompt += `\n\n${statePatchPrompt(node)}`;
	else prompt += `\n\nAllowed verdicts: ${JSON.stringify(node.outcomes ?? [])}. Return one verdict JSON object.`;
	const materializedPrompt = materializeWorkflowSkills(prompt, root);
	const promptSha256 = sha256Bytes(materializedPrompt);
	const promptPath = path.posix.join("native-responses", `${node.id}.prompt`);
	const { proofBinding: _proofBinding, ...reviewDigestFields } = reviewDigests ?? {};
	return {
		prompt,
		materializedPrompt,
		promptSha256,
		promptPath,
		reviewInput: reviewDigests ? {
			promptSha256,
			promptPath,
			promptCommitmentDigest: promptCommitmentDigest!,
			...reviewDigestFields,
		} : undefined,
	};
}

async function executeNode(
	node: DesignNode,
	handler: Handler,
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	state: ControllerState,
	runner: ReturnType<typeof makeNativeRunner>,
	roles: RoleDocument,
	nativeMap: NativeMap,
	root: string,
	input: JsonObject,
	identity: RunDirIdentity,
	clock: RunClock,
	assertRuntimeStable: () => void,
	remainingMs: number,
	rolesPath: string,
	resolveLaunchSpec: LaunchResolver,
	frozenProof?: FrozenArtifactSet,
): Promise<void> {
	assertRuntimeStable();
	const activationFrozenStats = captureFrozenRuntimeStats(frozenProof);
	const eventPath = path.join(identity.path, "events.jsonl");
	const modelNode = handler.type === "agent" || handler.type === "decision";
	let launchArtifacts: BuiltLaunchArtifacts | undefined;
	if (modelNode) {
		if (state.modelCalls >= Number(design.budgets?.maxModelCalls ?? 0)) {
			throw new Error(`workflow model-call cap reached before ${node.id}`);
		}
		if (runner.getModelCalls() !== state.modelCalls) throw new Error("native runner and controller model-call counters diverged");
		// Authorize first, then count. A launch the fresh resolution refuses must not
		// consume model-call budget, and must not leave the receipt ledger one short of
		// the counter — which would report an authorization failure as a ledger mismatch.
		const pendingCall = state.modelCalls + 1;
		launchArtifacts = buildLaunchArtifacts(
			design, mapping, roles, nativeMap, state, node.id, node.role ?? "task", node.workspaceAccess ?? "read",
			rolesPath, resolveLaunchSpec, pendingCall,
		);
		revalidatePendingLaunch(launchArtifacts.launch.authorization);
		state.nodeCalls += 1;
		state.modelCalls = pendingCall;
		writeLaunchArtifacts(identity, state.modelCalls, launchArtifacts);
		state.launchReceiptsDigest = launchReceiptsDigest(identity);
	}
	state.inFlight = node.id;
	checkpoint(identity, state, clock);
	appendEvent(identity, eventPath, {
		time: new Date().toISOString(),
		elapsedMs: state.elapsedMs,
		event: "node_start",
		nodeId: node.id,
		modelCalls: state.modelCalls,
	});
	let output: JsonObject = { summary: `${node.id} completed` };

	if (handler.type === "input") {
		const patches: JsonObject[] = [];
		for (const [field, source] of Object.entries(handler.bindings)) {
			const value = typeof source === "string" ? input[source] : source.const;
			if (value === undefined) throw new Error(`workflow input has no value for ${field}`);
			patches.push({ op: "set", path: `/${field}`, value });
		}
		output = { summary: "seeded workflow state", statePatch: patches };
		applyPatch(design, node, state, output);
	} else if (handler.type === "increment") {
		const current = state.data[handler.field];
		if (typeof current !== "number") throw new Error(`increment field ${handler.field} is not numeric`);
		const patches: JsonObject[] = [{ op: "set", path: `/${handler.field}`, value: current + (handler.by ?? 1) }];
		for (const [field, value] of Object.entries(handler.set ?? {})) patches.push({ op: "set", path: `/${field}`, value });
		output = { summary: `incremented ${handler.field}`, statePatch: patches };
		applyPatch(design, node, state, output);
	} else if (handler.type === "template") {
		const patches = Object.entries(handler.values).map(([field, template]) => ({
			op: "set",
			path: `/${field}`,
			value: renderForNode(template, node, state, controllerRenderValues(root, identity, state)),
		}));
		output = { summary: "rendered deterministic state", statePatch: patches };
		applyPatch(design, node, state, output);
	} else if (handler.type === "command") {
		const argv = handler.argv.map(value => renderForNode(value, node, state, {
			...controllerRenderValues(root, identity, state),
			...executableRenderValues(mapping),
		}));
		const activation = state.completed.length + 1;
		verifyFrozenRuntimeStats(frozenProof, activationFrozenStats);
		if (argv.length === 0) throw new Error(`command handler ${node.id} has empty argv`);
		verifyEvidenceTree(identity);
		const beforeCall = evidenceSnapshot(identity);
		const processResult = await commandCall(argv, root, Math.min(handlerTimeout(node), remainingMs), clock.signal);
		const runWriteBindings = verifyExternalCallEvidence(identity, beforeCall, undefined, handler.runWrites ?? []);
		verifyFrozenRuntimeStats(frozenProof, activationFrozenStats);
		assertRuntimeStable();
		verifyEvidenceTree(identity);
		const responseDir = path.join(identity.path, "command-responses");
		ensureEvidenceDirectory(identity, responseDir);
		secureAtomicWrite(identity, path.join(responseDir, `${node.id}.stdout`), processResult.stdout);
		secureAtomicWrite(identity, path.join(responseDir, `${node.id}.stderr`), processResult.stderr);
		atomicJson(identity, path.join(responseDir, `${node.id}.result.json`), {
			nodeId: node.id,
			activation,
			argvSha256: sha256Bytes(canonicalJson(argv)),
			exitCode: processResult.exitCode,
			terminationClass: processResult.terminationClass,
			stdoutSha256: sha256Bytes(processResult.stdout),
			stderrSha256: sha256Bytes(processResult.stderr),
			runWrites: runWriteBindings,
		});
		if (processResult.terminationClass !== "COMPLETED") {
			throw new Error(`command node ${node.id} failed (${processResult.terminationClass}): ${processResult.stderr || processResult.stdout}`);
		}
		output = lastJsonObject(processResult.stdout, `command node ${node.id}`);
		applyPatch(design, node, state, output);
	} else if (handler.type === "agent" || handler.type === "decision") {
		const role = resolveRole(roles, node.role ?? "task");
		const built = buildModelPrompt(node, handler, design, mapping, state, root, identity, input, frozenProof);
		const { prompt, materializedPrompt, promptSha256, promptPath, reviewInput } = built;
		const responseDir = path.join(identity.path, "native-responses");
		ensureEvidenceDirectory(identity, responseDir);
		secureAtomicWrite(identity, path.join(identity.path, promptPath), materializedPrompt);
		verifyFrozenRuntimeStats(frozenProof, activationFrozenStats);
		verifyEvidenceTree(identity);
		assertEvidenceFile(identity, path.join(identity.path, "native-calls.jsonl"), false);
		const beforeCall = evidenceSnapshot(identity);
		const result = await nativeCall(
			runner,
			launchArtifacts!.launch,
			prompt,
			root,
			Math.min(handlerTimeout(node), remainingMs),
			node.workspaceAccess ?? "read",
			clock.signal,
		);
		verifyExternalCallEvidence(identity, beforeCall, state.modelCalls);
		if (runner.getModelCalls() !== state.modelCalls) {
			throw new Error(`node ${node.id} native runner refused before activation: ${result.stderr}`);
		}
		verifyFrozenRuntimeStats(frozenProof, activationFrozenStats);
		const traceSha256 = annotateNativeTrace(identity, state.modelCalls, {
			...(reviewInput ?? { promptSha256, promptPath }),
			launchReceiptPath: launchReceiptRelativePath(state.modelCalls),
			launchReceiptSha256: sha256Bytes(launchArtifacts!.receiptBytes),
			controllerLaunchBindingPath: controllerLaunchBindingRelativePath(state.modelCalls),
			controllerLaunchBindingSha256: sha256Bytes(launchArtifacts!.controllerBindingBytes),
		});
		assertRuntimeStable();
		verifyEvidenceTree(identity);
		secureAtomicWrite(identity, path.join(responseDir, `${node.id}.stdout`), result.stdout);
		secureAtomicWrite(identity, path.join(responseDir, `${node.id}.stderr`), result.stderr);
		atomicJson(identity, path.join(responseDir, `${node.id}.result.json`), {
			call: state.modelCalls,
			promptPath,
			promptSha256,
			...(reviewInput ? {
				promptCommitmentDigest: reviewInput.promptCommitmentDigest,
				reviewedInputsDigest: reviewInput.reviewedInputsDigest,
				reviewedStateDigest: reviewInput.reviewedStateDigest,
				evidenceDigest: reviewInput.evidenceDigest,
			} : {}),
			exitCode: result.exitCode,
			terminationClass: result.terminationClass,
			stdoutSha256: sha256Bytes(result.stdout),
			stderrSha256: sha256Bytes(result.stderr),
		});
		if (result.terminationClass !== "COMPLETED") throw new Error(`node ${node.id} failed (${result.terminationClass}): ${result.stderr}`);
		try {
			output = lastJsonObject(result.stdout, `node ${node.id}`);
		} catch (error) {
			if (handler.type === "decision") throw new MalformedDecisionError((error as Error).message);
			throw error;
		}
		if (handler.type === "agent") applyPatch(design, node, state, output);
		else {
			const verdict = output.verdict;
			if (typeof verdict !== "string" || !(node.outcomes ?? []).includes(verdict)) {
				throw new MalformedDecisionError(`decision node ${node.id} returned an undeclared verdict`);
			}
			const writes = node.writes ?? [];
			if (writes.length > 1) throw new Error(`decision node ${node.id} may write at most one reason field in v1`);
			if (writes.length === 1) {
				const value = String(output.summary ?? output.reason ?? "");
				assertStateType(design, writes[0], value);
				state.data[writes[0]] = value;
			}
			appendMachineReviewRecord(identity, node, output, state.modelCalls, state.modelCalls, reviewInput!, traceSha256);
		}
	} else if (handler.type === "terminal") {
		if (node.kind !== "terminal") throw new Error(`terminal handler attached to non-terminal ${node.id}`);
		declaredReadValues(node, state);
	} else {
		throw new Error(`unsupported handler for ${node.id}`);
	}

	state.outputs[node.id] = output;
	state.completed = [...new Set([...state.completed, node.id])].sort();
	state.frontier = state.frontier.filter(item => item !== node.id);
	for (const target of enforcedOutgoing(design, state, node)) {
		if (!state.completed.includes(target) && !state.frontier.includes(target)) state.frontier.push(target);
	}
	state.frontier = runnable(design, state, state.frontier);
	state.pending = state.pending.filter(item => item !== node.id);
	state.inFlight = "";
	if (node.kind === "terminal") {
		state.terminal = node.id;
		state.status = node.outcome === "success" || design.terminals.success === node.id
			? "success"
			: node.outcome === "failure" || design.terminals.failure === node.id
				? "failure"
				: "unresolved";
		state.completionPhase = state.status === "success" && stateBound(design) ? "terminal" : "finalized";
	}
	appendEvent(identity, eventPath, {
		time: new Date().toISOString(),
		elapsedMs: elapsedMs(clock),
		event: "node_end",
		nodeId: node.id,
		outputDigest: sha256Bytes(JSON.stringify(output)),
		frontier: state.frontier,
	});
	assertRuntimeStable();
	checkpoint(identity, state, clock);
}

function projectRelative(root: string, absolute: string, label: string): string {
	const relative = path.relative(root, absolute);
	if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
		throw new Error(`${label} is not a distinct file below the project root`);
	}
	return relative;
}

function proofArtifactPaths(
	root: string,
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	paths: { design: string; mapping: string; roles: string; nativeMap: string; input: string },
): { artifacts: Record<string, string>; additional: string[] } {
	if (!mapping.proof) throw new Error("state-bound proof mapping is unavailable");
	const artifacts: Record<string, string> = {
		controller: CONTROLLER_RELATIVE,
		"launch-resolver": LAUNCH_RESOLVER_RELATIVE,
		"launch-policy-resolver": LAUNCH_POLICY_RESOLVER_RELATIVE,
		"launch-catalog": LAUNCH_CATALOG_RELATIVE,
		"launch-policy-validator": LAUNCH_POLICY_VALIDATOR_RELATIVE,
		"launch-project-paths": LAUNCH_PROJECT_PATHS_RELATIVE,
		"native-core": NATIVE_CORE_RELATIVE,
		"proof-module": PROOF_MODULE_RELATIVE,
		design: projectRelative(root, paths.design, "design"),
		mapping: projectRelative(root, paths.mapping, "mapping"),
		roles: projectRelative(root, paths.roles, "roles"),
		"native-map": projectRelative(root, paths.nativeMap, "native map"),
		input: projectRelative(root, paths.input, "input"),
		resources: mapping.proof.resourcesManifestPath,
		helper: mapping.proof.helperPath,
		"proof-action": mapping.proof.actionPath,
	};
	const additional: string[] = ["proof-action"];
	for (const [label, resourcePath] of Object.entries(mapping.proof.resources)) {
		artifacts[label] = resourcePath;
		additional.push(label);
	}
	const skillNames = new Set<string>();
	for (const node of design.nodes) {
		for (const name of [...(node.skills?.required ?? []), ...(node.skills?.onDemand ?? [])]) skillNames.add(name);
	}
	for (const name of [...skillNames].sort()) {
		const label = `skill-${name}`;
		if (!SAFE_PROOF_LABEL.test(label) || artifacts[label]) throw new Error(`state-bound proof skill label is invalid: ${label}`);
		artifacts[label] = path.join(".claude", "skills", name, "SKILL.md");
		additional.push(label);
	}
	return { artifacts, additional: additional.sort() };
}

function writeOrVerifyFrozenManifest(
	root: string,
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	paths: { design: string; mapping: string; roles: string; nativeMap: string; input: string },
	identity: RunDirIdentity,
	resume: boolean,
	resolvedExecutables: Record<string, ResolvedExecutableBinding>,
): FrozenArtifactSet | undefined {
	if (!stateBound(design)) return undefined;
	const { artifacts, additional } = proofArtifactPaths(root, design, mapping, paths);
	const current = freezeProofArtifacts(root, artifacts, additional, resolvedExecutables);
	const manifestPath = path.join(identity.path, "frozen-artifacts.json");
	if (!resume) {
		atomicJson(identity, manifestPath, current);
		return current;
	}
	const stored = readEvidenceObject<FrozenArtifactSet & JsonObject>(identity, manifestPath, "frozen artifact manifest");
	verifyFrozenProofArtifacts(stored);
	if (canonicalJson(stored) !== canonicalJson(current)) {
		const storedLabels = (stored.files ?? []).map(file => file.label).sort();
		const currentLabels = (current.files ?? []).map(file => file.label).sort();
		const storedExec = (stored.executables ?? []).map(item => item.label).sort();
		const currentExec = (current.executables ?? []).map(item => item.label).sort();
		const fileDiffs = current.files.flatMap(file => {
			const previous = stored.files.find(item => item.label === file.label);
			if (!previous) return [`file +${file.label} path=${file.relativePath} sha256=${file.sha256}`];
			if (canonicalJson(previous) === canonicalJson(file)) return [];
			return [`file ~${file.label} stored=${canonicalJson(previous)} current=${canonicalJson(file)}`];
		});
		const missingFiles = stored.files
			.filter(file => !current.files.some(item => item.label === file.label))
			.map(file => `file -${file.label} path=${file.relativePath} sha256=${file.sha256}`);
		const execDiffs = current.executables.flatMap(item => {
			const previous = stored.executables.find(entry => entry.label === item.label);
			if (!previous) return [`exec +${item.label} path=${item.path}`];
			if (canonicalJson(previous) === canonicalJson(item)) return [];
			const keys = [...new Set([...Object.keys(previous), ...Object.keys(item)])].sort();
			const changed = keys.filter(key => canonicalJson((previous as Record<string, unknown>)[key]) !== canonicalJson((item as Record<string, unknown>)[key]));
			return [`exec ~${item.label} changed=${changed.join(",")} stored=${canonicalJson(previous)} current=${canonicalJson(item)}`];
		});
		const missingExec = stored.executables
			.filter(item => !current.executables.some(entry => entry.label === item.label))
			.map(item => `exec -${item.label} path=${item.path}`);
		throw new Error(
			"frozen artifact manifest disagrees with current controller inputs: "
			+ JSON.stringify({
				requiredLabels: { stored: stored.requiredLabels, current: current.requiredLabels },
				fileLabels: { stored: storedLabels, current: currentLabels },
				execLabels: { stored: storedExec, current: currentExec },
				root: { stored: stored.root, current: current.root, storedDev: stored.rootDevice, currentDev: current.rootDevice, storedIno: stored.rootInode, currentIno: current.rootInode },
				diffs: [...missingFiles, ...fileDiffs, ...missingExec, ...execDiffs],
			}),
		);
	}
	return stored;
}

function activeRoleModelPolicyDigest(frozen: FrozenArtifactSet): string {
	const roles = frozen.files.find(file => file.label === "roles");
	if (!roles) throw new Error("state-bound frozen artifact manifest has no role/model policy binding");
	return roles.sha256;
}

function captureRunProofBinding(
	root: string,
	input: WorkflowProofInput,
	itemId: string,
	frozen: FrozenArtifactSet,
): ProofBinding {
	return captureProofBinding(root, input.proofBinding, itemId, activeRoleModelPolicyDigest(frozen));
}

function proofInputItemId(mapping: WorkflowMapping, input: WorkflowProofInput): string {
	if (!mapping.proof) throw new Error("state-bound proof mapping is unavailable");
	const matches = Object.values(mapping.handlers).filter(handler =>
		handler.type === "input" && Object.prototype.hasOwnProperty.call(handler.bindings, mapping.proof!.itemIdField),
	) as Array<Extract<Handler, { type: "input" }>>;
	if (matches.length !== 1) throw new Error("state-bound itemIdField must have exactly one input binding");
	const source = matches[0].bindings[mapping.proof.itemIdField];
	const value = typeof source === "string" ? input[source] : source.const;
	if (typeof value !== "string" || !SAFE_PROOF_LABEL.test(value)) {
		throw new Error("state-bound workflow input must expose a safe canonical itemId");
	}
	return value;
}

function latestAllowedReviewNode(
	identity: RunDirIdentity,
	allowed: string[],
): string {
	const records = readEvidenceJsonLines(
		identity,
		path.join(identity.path, "machine-reviews.jsonl"),
		"machine review ledger",
	);
	const latest = records.at(-1);
	if (!latest || typeof latest.nodeId !== "string" || !allowed.includes(latest.nodeId)) {
		throw new Error(`latest machine review is not one of the allowed final review nodes: ${allowed.join(", ")}`);
	}
	return latest.nodeId;
}

async function currentWorkspaceBinding(
	root: string,
	identity: RunDirIdentity,
	frozen: FrozenArtifactSet,
	proof: NonNullable<WorkflowMapping["proof"]>,
	timeoutSeconds: number,
): Promise<JsonObject> {
	verifyFrozenProofArtifacts(frozen);
	const helper = safeResolveExisting(root, proof.helperPath, "state-bound current-state helper");
	const action = safeResolveExisting(root, proof.actionPath, "state-bound current-state action");
	if (action !== path.join(root, REWARD_ACTION_RELATIVE)) {
		throw new Error("state-bound current-state action must use the audited project wrapper");
	}
	const before = evidenceSnapshot(identity);
	const executables = requireResolvedExecutables({ proof } as WorkflowMapping);
	const result = captureCurrentState({
		projectRoot: root,
		runRoot: identity.path,
		helper,
		helperSha256: sha256File(helper),
		python: executables.python.path,
		pythonSha256: executables.python.sha256,
		git: executables.git.path,
		gitSha256: executables.git.sha256,
		timeoutSeconds,
	});
	verifyExternalCallEvidence(identity, before);
	verifyFrozenProofArtifacts(frozen);
	if (!result || typeof result !== "object" || Array.isArray(result)) {
		throw new Error("state-bound current-state action returned no stateBinding object");
	}
	return result;
}

function loadExistingResult(
	root: string,
	identity: RunDirIdentity,
	design: WorkflowDesign,
	state: ControllerState,
): JsonObject {
	assertResultReady(state);
	const result = readEvidenceObject<JsonObject>(identity, path.join(identity.path, "result.json"), "workflow result");
	const reviewer = finalMachineReviewerVerdict(identity, design, state);
	if (state.status === "success" && reviewer && reviewer.reviewerTerminal !== "COMPLETE") {
		throw new Error(`existing success terminal cannot publish reviewerTerminal ${reviewer.reviewerTerminal}`);
	}
	const expectedTerminal = independentReview(design) && state.status === "success"
		? "unresolved"
		: reviewer?.reviewerTerminal === "HUMAN_BLOCKED" || reviewer?.reviewerTerminal === "REVISE"
			? "unresolved"
			: state.status;
	if (
		result.designId !== state.designId || result.runId !== state.runId || result.terminal !== expectedTerminal ||
		result.stateDigest !== sha256Bytes(JSON.stringify(state))
	) throw new Error("existing workflow result disagrees with the finalized checkpoint");
	if (stateBound(design) && state.status === "success") {
		if (
			result.reviewerTerminal !== "COMPLETE" ||
			result.evidenceCommitment !== `sha256:${state.candidateDigest}` ||
			result.humanRequestKind !== undefined || result.humanRequest !== undefined
		) {
			throw new Error("existing state-bound result does not publish the canonical COMPLETE commitment");
		}
	}
	if (state.proofPath || state.proofSha256) {
		const proof = result.proof;
		if (!proof || typeof proof !== "object" || Array.isArray(proof) ||
			(proof as JsonObject).path !== state.proofPath || (proof as JsonObject).sha256 !== state.proofSha256) {
			throw new Error("existing workflow result disagrees with the finalized proof binding");
		}
		const proofFile = safeResolveExisting(root, state.proofPath, "workflow proof");
		if (sha256File(proofFile) !== state.proofSha256) throw new Error("existing workflow proof bytes changed");
	} else if (result.proof !== undefined) {
		throw new Error("existing workflow result has an unexpected proof binding");
	}
	if (canonicalJson(result) !== canonicalJson(buildResult(root, identity, design, state))) {
		throw new Error("existing workflow result envelope disagrees with canonical runtime evidence");
	}
	return result;
}

async function completeStateBoundProof(
	root: string,
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	state: ControllerState,
	identity: RunDirIdentity,
	clock: RunClock,
	frozen: FrozenArtifactSet,
	input: WorkflowProofInput,
): Promise<void> {
	const proof = mapping.proof!;
	if (state.status !== "success") throw new Error("state-bound completion requires a success terminal checkpoint");
	verifyFrozenProofArtifacts(frozen);
	validateMachineReviewLedger(identity, design);
	const itemId = state.data[proof.itemIdField];
	const evidencePath = state.data[proof.evidencePathField];
	if (typeof itemId !== "string" || !SAFE_PROOF_LABEL.test(itemId)) throw new Error("state-bound itemId state is missing or unsafe");
	const proofBinding = captureRunProofBinding(root, input, itemId, frozen);
	if (typeof evidencePath !== "string" || !evidencePath || path.isAbsolute(evidencePath) ||
		path.normalize(evidencePath) !== evidencePath || evidencePath === ".." || evidencePath.startsWith(`..${path.sep}`)) {
		throw new Error("state-bound evidence path must be run-root-relative and normalized");
	}
	const terminal = bindSuccessfulTerminal(state as unknown as JsonObject, design.terminals.success, Number(design.budgets?.maxModelCalls ?? 0));
	const finalReviewNode = latestAllowedReviewNode(identity, proof.finalReviewNodes);
	const machineReview = captureLatestMachineReview(identity.path, "machine-reviews.jsonl", finalReviewNode);
	const workspace = captureWorkspaceEvidence(identity.path, evidencePath);
	const producerNodes = proof.evidenceProducerNodes.filter(nodeId => {
		const handler = mapping.handlers[nodeId];
		return state.completed.includes(nodeId) && handler.type === "command" &&
			(handler.runWrites ?? []).includes(evidencePath) && designReachable(design, nodeId, finalReviewNode);
	});
	if (producerNodes.length !== 1) {
		throw new Error(`final evidence path must have exactly one completed audited producer ancestor; found ${producerNodes.length}`);
	}
	const evidenceProducer = captureEvidenceProducer(identity.path, producerNodes[0], evidencePath);
	const boundedCurrentStateTimeout = (): number => {
		const wallSeconds = Math.floor(remainingWallTime(design, clock) / 1000);
		if (wallSeconds < 1) throw new Error("workflow wall-time budget cannot cover current-state proof audit");
		return Math.min(proof.currentStateTimeoutSeconds, wallSeconds);
	};
	let current = await currentWorkspaceBinding(root, identity, frozen, proof, boundedCurrentStateTimeout());
	let candidate: StateBoundCandidate;
	const candidatePath = path.join(identity.path, "state-bound-candidate.json");
	if (evidenceFileExists(identity, candidatePath)) {
		candidate = loadStateBoundCandidate(root, identity.path);
		if (
			candidate.itemId !== itemId || candidate.runId !== state.runId || candidate.designId !== state.designId ||
			canonicalJson(candidate.proofBinding) !== canonicalJson(proofBinding) ||
			canonicalJson(candidate.frozen) !== canonicalJson(frozen) ||
			canonicalJson(candidate.terminal) !== canonicalJson(terminal) ||
			canonicalJson(candidate.machineReview) !== canonicalJson(machineReview) ||
			canonicalJson(candidate.workspace) !== canonicalJson(workspace) ||
			canonicalJson(candidate.evidenceProducer) !== canonicalJson(evidenceProducer)
		) throw new Error("existing state-bound candidate disagrees with the recovered terminal binding");
		verifyMachineReviewBinding(identity.path, candidate.machineReview);
		verifyWorkspaceEvidence(identity.path, candidate.workspace, current);
	} else {
		candidate = createStateBoundCandidate({
			projectRoot: root,
			runRoot: identity.path,
			itemId,
			runId: state.runId,
			designId: state.designId,
			proofBinding,
			frozen,
			terminal,
			machineReview,
			workspace,
			evidenceProducer,
			currentWorkspaceBinding: current,
		});
		writeStateBoundCandidate(identity.path, candidate);
	}
	if (state.candidateDigest && state.candidateDigest !== candidate.candidateDigest) {
		throw new Error("checkpoint candidate digest disagrees with state-bound candidate");
	}
	state.candidateDigest = candidate.candidateDigest;
	state.completionPhase = "candidate";
	checkpoint(identity, state, clock);

	// Re-audit after candidate durability.  This second current-state read closes the
	// ordinary drift window between candidate construction and protected finalization.
	verifyFrozenProofArtifacts(frozen);
	validateMachineReviewLedger(identity, design);
	verifyMachineReviewBinding(identity.path, candidate.machineReview);
	current = await currentWorkspaceBinding(root, identity, frozen, proof, boundedCurrentStateTimeout());
	verifyWorkspaceEvidence(identity.path, candidate.workspace, current);
	const authority = controllerFinalizeAuthorityFromEnvironment();
	const finalized = finalizeStateBoundCandidateOnly({
		projectRoot: root,
		runRoot: identity.path,
		itemId,
		currentWorkspaceBinding: current,
		roleModelPolicyDigest: activeRoleModelPolicyDigest(frozen),
		authority,
	});
	state.proofPath = projectRelative(root, finalized.proofPath, "state-bound proof");
	state.proofSha256 = sha256File(finalized.proofPath);
	state.completionPhase = "finalized";
	checkpoint(identity, state, clock);
}

async function auditFinalizedStateBoundProof(
	root: string,
	design: WorkflowDesign,
	mapping: WorkflowMapping,
	state: ControllerState,
	identity: RunDirIdentity,
	clock: RunClock,
	frozen: FrozenArtifactSet,
	input: WorkflowProofInput,
): Promise<void> {
	if (state.status !== "success" || state.completionPhase !== "finalized") {
		throw new Error("finalized proof audit requires a finalized success checkpoint");
	}
	verifyFrozenProofArtifacts(frozen);
	validateMachineReviewLedger(identity, design);
	const candidate = loadStateBoundCandidate(root, identity.path);
	const proof = mapping.proof!;
	const itemId = state.data[proof.itemIdField];
	const evidencePath = state.data[proof.evidencePathField];
	if (typeof itemId !== "string") throw new Error("finalized state-bound itemId is missing");
	const proofBinding = captureRunProofBinding(root, input, itemId, frozen);
	const terminal = bindSuccessfulTerminal(
		state as unknown as JsonObject,
		design.terminals.success,
		Number(design.budgets?.maxModelCalls ?? 0),
	);
	if (
		candidate.candidateDigest !== state.candidateDigest ||
		candidate.itemId !== itemId || candidate.runId !== state.runId || candidate.designId !== state.designId ||
		canonicalJson(candidate.proofBinding) !== canonicalJson(proofBinding) ||
		canonicalJson(candidate.frozen) !== canonicalJson(frozen) ||
		canonicalJson(candidate.terminal) !== canonicalJson(terminal) ||
		candidate.evidenceProducer.evidencePath !== evidencePath
	) {
		throw new Error("finalized state-bound candidate disagrees with the checkpoint and frozen run binding");
	}
	if (!proof.finalReviewNodes.includes(candidate.machineReview.latest.nodeId)) {
		throw new Error("finalized candidate machine review is not an allowed final review node");
	}
	if (!proof.evidenceProducerNodes.includes(candidate.evidenceProducer.nodeId)) {
		throw new Error("finalized candidate evidence producer is not allowed by the proof mapping");
	}
	const producerHandler = mapping.handlers[candidate.evidenceProducer.nodeId];
	if (
		producerHandler?.type !== "command" ||
		!(producerHandler.runWrites ?? []).includes(candidate.evidenceProducer.evidencePath) ||
		!state.completed.includes(candidate.evidenceProducer.nodeId) ||
		!designReachable(design, candidate.evidenceProducer.nodeId, candidate.machineReview.latest.nodeId)
	) {
		throw new Error("finalized candidate evidence producer is not the completed audited producer ancestor");
	}
	verifyMachineReviewBinding(identity.path, candidate.machineReview);
	verifyProofBinding(root, candidate.proofBinding, activeRoleModelPolicyDigest(frozen));
	verifyEvidenceProducer(identity.path, candidate.evidenceProducer);
	const wallSeconds = Math.floor(remainingWallTime(design, clock) / 1000);
	if (wallSeconds < 1) throw new Error("workflow wall-time budget cannot cover finalized proof audit");
	const current = await currentWorkspaceBinding(
		root,
		identity,
		frozen,
		proof,
		Math.min(proof.currentStateTimeoutSeconds, wallSeconds),
	);
	verifyWorkspaceEvidence(identity.path, candidate.workspace, current);
	const proofRelative = canonicalMachineWorkflowProofRelative(candidate.itemId);
	if (state.proofPath !== proofRelative) {
		throw new Error("finalized protected proof path disagrees with the canonical candidate path");
	}
	const { bytes: proofBytes } = readProjectFileNoFollow(root, proofRelative, "state-bound proof");
	const expectedProof = Buffer.from([
		"TERMINAL: COMPLETE",
		`EVIDENCE-COMMITMENT: sha256:${candidate.candidateDigest}`,
		"REVIEW_POLICY: machine-gated",
		"COMPLETION_PROOF: state-bound",
		`ITEM_ID: ${candidate.itemId}`,
		`ITEM_KIND: ${candidate.proofBinding.itemKind}`,
		`PROOF_SCOPE: ${candidate.proofBinding.scope}`,
		`PROOF_BINDING_DIGEST: ${candidate.proofBinding.digest}`,
		`ROLE_MODEL_POLICY_DIGEST: ${candidate.proofBinding.roleModelPolicyDigest}`,
		`PROOF_BINDING_JSON: ${canonicalJson(candidate.proofBinding)}`,
		`DESIGN_ID: ${candidate.designId}`,
		`RUN_ID: ${candidate.runId}`,
		`SUCCESS_TERMINAL: ${candidate.terminal.terminal}`,
		`MODEL_CALLS: ${candidate.terminal.modelCalls}`,
		`MAX_MODEL_CALLS: ${candidate.terminal.maxModelCalls}`,
		`FROZEN_INPUTS_DIGEST: ${candidate.frozen.digest}`,
		`MACHINE_REVIEW_DIGEST: ${candidate.machineReview.reviewDigest}`,
		`MACHINE_REVIEW_CALL: ${candidate.machineReview.latest.call}`,
		`MACHINE_REVIEW_PROMPT_SHA256: ${candidate.machineReview.latest.promptSha256}`,
		`MACHINE_REVIEWED_STATE_DIGEST: ${candidate.machineReview.latest.reviewedStateDigest}`,
		`MACHINE_REVIEWED_INPUTS_DIGEST: ${candidate.machineReview.latest.reviewedInputsDigest}`,
		`MACHINE_REVIEW_EVIDENCE_DIGEST: ${candidate.machineReview.latest.evidenceDigest}`,
		`NATIVE_TRACE_SHA256: ${candidate.machineReview.nativeTrace.sha256}`,
		`NATIVE_TRACE_CALLS: ${candidate.machineReview.traceCalls}`,
		`MACHINE_REVIEW_RESPONSE_SHA256: ${candidate.machineReview.response.sha256}`,
		`MACHINE_REVIEW_RECEIPT_SHA256: ${candidate.machineReview.receipt.sha256}`,
		`MACHINE_REVIEW_PROMPT_BYTES_SHA256: ${candidate.machineReview.prompt.sha256}`,
		`BASELINE_DIGEST: ${candidate.workspace.baselineDigest}`,
		`EVIDENCE_DIGEST: ${candidate.workspace.evidenceDigest}`,
		`EVIDENCE_PRODUCER_NODE: ${candidate.evidenceProducer.nodeId}`,
		`EVIDENCE_PRODUCER_ACTIVATION: ${candidate.evidenceProducer.activation}`,
		`EVIDENCE_PRODUCER_DIGEST: ${candidate.evidenceProducer.producerDigest}`,
		`EVIDENCE_PRODUCER_RECEIPT_SHA256: ${candidate.evidenceProducer.receipt.sha256}`,
		`FINAL_STATE_DIGEST: ${candidate.workspace.finalStateDigest}`,
		`STATE_BINDING_JSON: ${canonicalJson(candidate.workspace.stateBinding)}`,
		`CANDIDATE_DIGEST: ${candidate.candidateDigest}`,
		`AUTHORIZATION_MODE: ${candidate.authorization.mode}`,
		`AUTHORIZATION_SOURCE: ${candidate.authorization.source}`,
		`THREAT_BOUNDARY: ${STATE_BOUND_PROOF_THREAT_BOUNDARY}`,
		"",
	].join("\n"), "utf8");
	if (!proofBytes.equals(expectedProof) || sha256Bytes(proofBytes) !== state.proofSha256) {
		throw new Error("finalized protected proof disagrees with checkpoint binding");
	}
}

export async function runController(options: ControllerOptions): Promise<{ state: ControllerState; result: JsonObject; runDir: string }> {
	if (options.signal?.aborted) throw new Error("workflow cancelled before launch");
	let root: string;
	try {
		root = fs.realpathSync(path.resolve(options.root));
	} catch (error) {
		throw new Error(`project root is unavailable: ${(error as Error).message}`);
	}
	if (path.normalize(options.outputRoot) !== path.normalize("workflow-output/subagent")) {
		throw new Error("subagent-workflow outputRoot is fixed at workflow-output/subagent");
	}
	if (path.normalize(options.rolesPath) !== path.normalize(CANONICAL_ROLES_RELATIVE)) {
		throw new Error(`subagent-scheduler model policy is fixed at ${CANONICAL_ROLES_RELATIVE}`);
	}
	const frozenMapping = freezeObject<WorkflowMapping>(root, options.mappingPath, "subagent workflow mapping");
	const mappingPath = frozenMapping.path;
	const mappingSha256 = frozenMapping.sha256;
	const sourceMapping = frozenMapping.value;
	const frozenDesign = freezeObject<WorkflowDesign>(root, sourceMapping.designPath, "workflow design");
	const designPath = frozenDesign.path;
	const designSha256 = frozenDesign.sha256;
	if (designSha256 !== sourceMapping.designSha256) throw new Error("mapping design digest is stale");
	const design = frozenDesign.value;
	validateDesign(design);
	if (options.parentAccess === "read" && (
		design.nodes.some(node => node.workspaceAccess === "write") ||
		Object.values(sourceMapping.handlers).some(handler => handler.type === "command")
	)) throw new Error("workflow exceeds parent read-only authority");
	if (options.parentBudget) {
		const allocation = options.parentBudget;
		if (!Number.isInteger(allocation.maxModelCalls) || allocation.maxModelCalls < 1 ||
			!Number.isFinite(allocation.maxWallTimeSeconds) || allocation.maxWallTimeSeconds <= 0) {
			throw new Error("invalid parent workflow allocation");
		}
		if (Number(design.budgets?.maxModelCalls) > allocation.maxModelCalls) {
			throw new Error("workflow exceeds parent model-call allocation");
		}
	}
	const outputBase = safeResolve(root, path.join("workflow-output/subagent", design.id), "output base");
	const frozenRoles = freezeObject<RoleDocument>(root, options.rolesPath, "workflow role config");
	// Admit the canonical policy before anything else touches a native route, a run directory,
	// or a process. A policy the shared validator would reject never reaches an activation.
	validateModelRolePolicy(frozenRoles.value);
	const frozenNativeMap = freezeObject<NativeMap>(root, options.nativeMapPath, "native model map");
	const frozenInput = freezeObject<JsonObject>(root, options.inputPath ?? sourceMapping.inputPath, "workflow input path");
	const requestedResume = options.resume ? safeResolveExisting(root, options.resume, "resume directory") : undefined;
	if (options.verifyProofOnly && options.resume && path.resolve(root, options.resume) !== requestedResume) {
		throw new Error("verify-proof --resume must name the canonical run directory without symlink traversal");
	}
	if (requestedResume && path.dirname(requestedResume) !== outputBase) {
		throw new Error(`resume directory must be an immediate child of workflow-output/subagent/${design.id}`);
	}
	let verifyProofBindings: Record<string, ResolvedExecutableBinding> | undefined;
	if (options.verifyProofOnly) {
		if (!requestedResume) throw new Error("verify-proof requires --resume");
		const verifyIdentity = captureRunDir(root, requestedResume, false);
		const manifest = readEvidenceObject<FrozenArtifactSet & JsonObject>(
			verifyIdentity,
			path.join(requestedResume, "frozen-artifacts.json"),
			"frozen artifact manifest",
		);
		verifyFrozenProofArtifacts(manifest);
		verifyProofBindings = Object.fromEntries(
			manifest.executables.map(binding => [binding.label, { path: binding.path, sha256: binding.sha256 }]),
		);
	}
	const resolvedRuntime = resolvePortableExecutables(
		root,
		sourceMapping,
		frozenRoles.value,
		normalizeNativeMap(frozenNativeMap.value) as NativeMap,
		verifyProofBindings,
	);
	const mapping = resolvedRuntime.mapping;
	validateMapping(mapping, design);
	validateProofExecutables(root, mapping);
	preflightWorkflowSkills(design, root);
	const maxModelCalls = design.budgets?.maxModelCalls;
	if (!Number.isInteger(maxModelCalls) || (maxModelCalls as number) < 1) throw new Error("design has no positive maxModelCalls");
	if (mapping.scheduler.maxCalls > (maxModelCalls as number)) throw new Error("scheduler cap exceeds design model-call cap");
	const rolesPath = frozenRoles.path;
	const nativeMapPath = frozenNativeMap.path;
	const inputPath = frozenInput.path;
	const nativeMap = bindStateBoundNativeExecutables(
		mapping,
		resolvedRuntime.nativeMap,
	);
	validateNativeRoutes(design, mapping, frozenRoles.value, nativeMap);
	const rolesSha256 = frozenRoles.sha256;
	// The resolver is bound to the already-frozen models path AND digest, so it can
	// neither read a different file nor observe a rewrite between freeze and launch.
	const resolveLaunchSpec = createBoundLaunchResolver(root, { path: rolesPath, sha256: rolesSha256 });
	const nativeMapSha256 = frozenNativeMap.sha256;
	const inputSha256 = frozenInput.sha256;
	if (stateBound(design)) {
		captureProofBinding(
			root,
			(frozenInput.value as WorkflowProofInput).proofBinding,
			proofInputItemId(mapping, frozenInput.value as WorkflowProofInput),
			rolesSha256,
		);
	}
	const resumeDir = requestedResume;
	if (options.finalizeOnly && options.verifyProofOnly) throw new Error("finalize-only and verify-proof are mutually exclusive");
	if (options.finalizeOnly && !resumeDir) throw new Error("finalize-only requires --resume");
	if (options.finalizeOnly && !stateBound(design)) throw new Error("finalize-only requires state-bound completion");
	if (options.verifyProofOnly && !resumeDir) throw new Error("verify-proof requires --resume");
	if (options.verifyProofOnly && !stateBound(design)) throw new Error("verify-proof requires state-bound completion");
	let state: ControllerState;
	let runDir: string;
	let identity: RunDirIdentity;
	let resumeReconciledInFlight = "";
	let resumeUnattributedModelCalls = 0;
	if (resumeDir) {
		runDir = resumeDir;
		identity = captureRunDir(root, runDir, false);
		state = loadResume(identity, design, designSha256, mappingSha256, rolesSha256, nativeMapSha256, inputSha256);
		if (path.basename(runDir) !== state.runId) throw new Error("resume directory name does not match checkpoint runId");
		// Full replay validation happens after the frozen proof manifest is loaded, so every
		// historical final-review prompt is reconstructed with the same canonical PM binding.
	} else {
		state = initializeState(design, designSha256, mappingSha256, rolesSha256, nativeMapSha256, inputSha256);
		runDir = safeResolve(root, path.join("workflow-output/subagent", design.id, state.runId), "output directory");
		identity = captureRunDir(root, runDir, true);
		secureAtomicWrite(identity, path.join(runDir, "native-calls.jsonl"), "");
		secureAtomicWrite(identity, path.join(runDir, "native-attempts.jsonl"), "");
		secureAtomicWrite(identity, path.join(runDir, "native-heartbeats.jsonl"), "");
	}
	const runtimePaths = {
		design: designPath,
		mapping: mappingPath,
		roles: rolesPath,
		nativeMap: nativeMapPath,
		input: inputPath,
	};
	const frozenProof = writeOrVerifyFrozenManifest(
		root, design, mapping, runtimePaths, identity, Boolean(resumeDir), resolvedRuntime.executables,
	);
	if (resumeDir) {
		validateResumeProvenance(
			identity, state, design, mapping, frozenInput.value, frozenRoles.value, nativeMap, root,
			rolesPath, resolveLaunchSpec, frozenProof,
		);
		const checkpointModelCalls = state.modelCalls;
		resumeReconciledInFlight = reconcileResumeCalls(identity, state, maxModelCalls as number);
		resumeUnattributedModelCalls = state.modelCalls - checkpointModelCalls;
		state.nodeCalls += resumeUnattributedModelCalls;
		validateMachineReviewLedger(identity, design);
	}
	const clock: RunClock = { baseElapsedMs: state.elapsedMs, startedMonotonicMs: performance.now(),
		signal: options.signal, parentWallTimeSeconds: options.parentBudget?.maxWallTimeSeconds };
	const assertRuntimeStable = (): void => {
		assertRuntimeInputsStable(state, runtimePaths);
		if (frozenProof) {
			verifyFrozenProofArtifacts(frozenProof);
			validateProofExecutables(root, mapping);
		}
	};
	if (options.verifyProofOnly) {
		assertRuntimeStable();
		if (resumeReconciledInFlight || resumeUnattributedModelCalls > 0) {
			throw new Error("verify-proof refuses a checkpoint whose native-call accounting requires reconciliation");
		}
		if (!evidenceFileExists(identity, path.join(runDir, "result.json"))) {
			throw new Error("verify-proof requires an existing canonical result.json");
		}
		await auditFinalizedStateBoundProof(root, design, mapping, state, identity, clock, frozenProof!, frozenInput.value);
		const storedResult = loadExistingResult(root, identity, design, state);
		return { state, result: { ...storedResult, modelCallsReplayed: 0 }, runDir };
	}
	if (resumeReconciledInFlight || resumeUnattributedModelCalls > 0) {
		assertRuntimeStable();
		const recordedModelCalls = state.modelCalls - resumeUnattributedModelCalls;
		const expectedReason = resumeReconciledInFlight
			? ambiguousInFlightReason(resumeReconciledInFlight)
			: `AMBIGUOUS_SIDE_EFFECT: trace records ${state.modelCalls} model calls but checkpoint records ${recordedModelCalls}`;
		if (state.status !== "unresolved" || state.inFlight !== resumeReconciledInFlight || state.forcedReason !== expectedReason) {
			throw new Error("resume reconciliation state changed before checkpoint");
		}
		state.inFlight = "";
		state.pending = [];
		state.frontier = [];
		state.completionPhase = "finalized";
		appendEvent(identity, path.join(runDir, "events.jsonl"), {
			time: new Date().toISOString(),
			elapsedMs: elapsedMs(clock),
			event: "resume_reconciled",
			inFlight: resumeReconciledInFlight,
			status: state.status,
			reason: state.forcedReason,
			recordedModelCalls,
			unattributedModelCalls: resumeUnattributedModelCalls,
		});
		checkpoint(identity, state, clock);
	}
	const tracePath = path.join(runDir, "native-calls.jsonl");
	const runner = makeNativeRunner(nativeMap, maxModelCalls as number, tracePath, state.modelCalls);
	if (resumeDir && evidenceFileExists(identity, path.join(runDir, "result.json"))) {
		assertRuntimeStable();
		assertResultReady(state);
		if (options.finalizeOnly && state.status !== "success") {
			throw new Error("finalize-only requires a successful state-bound terminal");
		}
		if (stateBound(design) && state.status === "success") {
			await auditFinalizedStateBoundProof(root, design, mapping, state, identity, clock, frozenProof!, frozenInput.value);
		}
		const storedResult = loadExistingResult(root, identity, design, state);
		const result = options.finalizeOnly ? { ...storedResult, modelCallsReplayed: 0 } : storedResult;
		return { state, result, runDir };
	}
	if (state.status !== "running") {
		assertRuntimeStable();
		if (stateBound(design) && state.status === "success") {
			await completeStateBoundProof(root, design, mapping, state, identity, clock, frozenProof!, frozenInput.value);
		} else if (options.finalizeOnly) {
			throw new Error("finalize-only requires a successful state-bound terminal");
		}
		const result = writeResult(root, identity, design, state, clock);
		if (options.finalizeOnly) result.modelCallsReplayed = 0;
		return { state, result, runDir };
	}
	if (options.finalizeOnly) throw new Error("finalize-only refuses a running workflow checkpoint");
	checkpoint(identity, state, clock);

	try {
		while (state.status === "running") {
			assertRuntimeStable();
			let selected = state.pending.filter(nodeId => !state.completed.includes(nodeId));
			if (selected.length === 0) {
				selected = await chooseNodes(
					mapping,
					design,
					state,
					runner,
					frozenRoles.value,
					nativeMap,
					root,
					identity,
					clock,
					assertRuntimeStable,
					remainingWallTime(design, clock),
					rolesPath,
					resolveLaunchSpec,
				);
				if (state.pending.length === 0) {
					state.pending = selected;
					checkpoint(identity, state, clock);
				}
			}
			assertRuntimeStable();
			if (selected.some(nodeId => !state.frontier.includes(nodeId))) {
				throw new Error("persisted scheduler selection is no longer a subset of the legal frontier");
			}
			for (const nodeId of selected) {
				if (state.status !== "running") break;
				assertRuntimeStable();
				const remainingMs = remainingWallTime(design, clock);
				if (!state.frontier.includes(nodeId)) throw new Error(`selected node ${nodeId} is no longer runnable`);
				const node = design.nodes.find(item => item.id === nodeId);
				if (!node) throw new Error(`selected node ${nodeId} disappeared from design`);
				await executeNode(
					node,
					mapping.handlers[nodeId],
					design,
					mapping,
					state,
					runner,
					frozenRoles.value,
					nativeMap,
					root,
					frozenInput.value,
					identity,
					clock,
					assertRuntimeStable,
					remainingMs,
					rolesPath,
					resolveLaunchSpec,
					frozenProof,
				);
				assertRuntimeStable();
				if (state.status === "success") remainingWallTime(design, clock);
			}
			assertRuntimeStable();
		}
	} catch (error) {
		if (error instanceof EvidencePathError || error instanceof LaunchAuthorizationError) throw error;
		verifyEvidenceTree(identity);
		state.status = error instanceof MalformedDecisionError && design.governance?.malformedDecision === "failure"
			? "failure"
			: "unresolved";
		state.forcedReason = (error as Error).message;
		state.modelCalls = Math.max(state.modelCalls, runner.getModelCalls());
		state.inFlight = "";
		state.pending = [];
		state.frontier = [];
		state.completionPhase = "finalized";
		appendEvent(identity, path.join(runDir, "events.jsonl"), {
			time: new Date().toISOString(),
			elapsedMs: elapsedMs(clock),
			event: "failure",
			reason: state.forcedReason,
		});
		checkpoint(identity, state, clock);
	}
	assertRuntimeStable();
	if (state.status === "success") remainingWallTime(design, clock);
	checkpoint(identity, state, clock);
	if (stateBound(design) && state.status === "success") {
		await completeStateBoundProof(root, design, mapping, state, identity, clock, frozenProof!, frozenInput.value);
	}
	const result = writeResult(root, identity, design, state, clock);
	return { state, result, runDir };
}

export function parseCliArguments(argv: string[]): {
	command: "start" | "validate" | "finalize-only" | "verify-proof";
	mappingPath: string;
	inputPath?: string;
	resume?: string;
} {
	const command = argv[0];
	if ((command !== "start" && command !== "validate" && command !== "finalize-only" && command !== "verify-proof") ||
		!argv[1] || argv[1].startsWith("-")) {
		throw new Error("usage: <validate|start|finalize-only|verify-proof> <mapping.json> [--input file] [--resume run-dir]");
	}
	if (command === "validate") {
		if (argv.length !== 2) throw new Error("validate accepts only a mapping path");
		return { command, mappingPath: argv[1] };
	}
	if (command === "finalize-only" || command === "verify-proof") {
		if (argv.length !== 4 || argv[2] !== "--resume" || !argv[3] || argv[3].startsWith("-")) {
			throw new Error(`${command} requires exactly <mapping.json> --resume <canonical-run-dir>`);
		}
		return { command, mappingPath: argv[1], resume: argv[3] };
	}
	const result: { command: "start"; mappingPath: string; inputPath?: string; resume?: string } = {
		command,
		mappingPath: argv[1],
	};
	const seen = new Set<string>();
	for (let index = 2; index < argv.length; index += 2) {
		const flag = argv[index];
		const value = argv[index + 1];
		if (flag !== "--input" && flag !== "--resume") throw new Error(`unsupported start option ${flag ?? "<missing>"}`);
		if (seen.has(flag)) throw new Error(`duplicate start option ${flag}`);
		if (!value || value.startsWith("-")) throw new Error(`start option ${flag} requires a value`);
		seen.add(flag);
		if (flag === "--input") result.inputPath = value;
		else result.resume = value;
	}
	return result;
}

async function main(): Promise<void> {
	const argv = process.argv.slice(2);
	const root = process.cwd();
	try {
		const parsed = parseCliArguments(argv);
		if (parsed.command === "validate") {
			const mappingPath = safeResolveExisting(root, parsed.mappingPath, "mapping path");
			const sourceMapping = readObject<WorkflowMapping>(mappingPath, "subagent workflow mapping");
			const designPath = safeResolveExisting(root, sourceMapping.designPath, "design path");
			const designDigest = sha256File(designPath);
			if (designDigest !== sourceMapping.designSha256) throw new Error("mapping design digest is stale");
			const design = readObject<WorkflowDesign>(designPath, "workflow design");
			validateDesign(design);
			const rolesPath = safeResolveExisting(root, "config/workflows/models.json", "role config path");
			const nativeMapPath = safeResolveExisting(root, NATIVE_MAP_RELATIVE, "native map path");
			const roles = readObject<RoleDocument>(rolesPath, "workflow role config");
			const resolvedRuntime = resolvePortableExecutables(
				root,
				sourceMapping,
				roles,
				normalizeNativeMap(readObject<NativeMap>(nativeMapPath, "native model map")) as NativeMap,
			);
			const mapping = resolvedRuntime.mapping;
			validateMapping(mapping, design);
			validateProofExecutables(root, mapping);
			const inputPath = safeResolveExisting(root, mapping.inputPath, "workflow input path");
			const input = readObject<JsonObject>(inputPath, "workflow input");
			for (const [nodeId, handler] of Object.entries(mapping.handlers)) {
				if (handler.type !== "input") continue;
				for (const source of Object.values(handler.bindings)) {
					if (typeof source === "string" && input[source] === undefined) {
						throw new Error(`input handler ${nodeId} references missing input field ${source}`);
					}
				}
			}
			preflightWorkflowSkills(design, root);
		if (stateBound(design)) {
			const itemId = proofInputItemId(mapping, input as WorkflowProofInput);
			const { artifacts, additional } = proofArtifactPaths(root, design, mapping, {
					design: designPath, mapping: mappingPath, roles: rolesPath, nativeMap: nativeMapPath, input: inputPath,
				});
			const frozen = freezeProofArtifacts(root, artifacts, additional, resolvedRuntime.executables);
			verifyFrozenProofArtifacts(frozen);
			captureRunProofBinding(root, input as WorkflowProofInput, itemId, frozen);
		}
			const routeMap = bindStateBoundNativeExecutables(mapping, resolvedRuntime.nativeMap);
			validateNativeRoutes(design, mapping, roles, routeMap, "structure");
			// `validate` proves the static contract without launching anything, so it can never
			// authorize dispatch: only the fresh native launch resolver, invoked at start against a
			// live authenticated catalog, can. `dispatchAdmitted` is therefore unconditionally false
			// here — including when the persisted catalogResolution says "verified", which is
			// caller-authorable audit data. `structurallyDispatchEligible` carries the only thing this
			// command actually proved.
			let structurallyBlocked: string | undefined;
			try {
				validateNativeRoutes(design, mapping, roles, routeMap, "dispatch");
			} catch (error) {
				structurallyBlocked = (error as Error).message;
			}
			const dispatchBlocked = structurallyBlocked
				?? "static validation never authorizes dispatch; start must authenticate the live catalog "
				+ "through the fresh native launch resolver";
			console.error(`[subagent-workflow] structural pass only, start is not yet authorized: ${dispatchBlocked}`);
			if (roles.catalogResolution?.status === "bootstrap-unverified") {
				console.error(
					"[subagent-workflow] persisted catalog resolution is bootstrap-unverified (audit data only); "
					+ "the fresh resolver decides admission at start",
				);
			}
			console.log(JSON.stringify({
				clean: true,
				designId: design.id,
				designSha256: designDigest,
				dispatchAdmitted: false,
				structurallyDispatchEligible: structurallyBlocked === undefined,
				dispatchBlocked,
			}));
			return;
		}
		const { result, runDir } = await runController({
			root,
			mappingPath: parsed.mappingPath,
			rolesPath: "config/workflows/models.json",
			nativeMapPath: NATIVE_MAP_RELATIVE,
			inputPath: parsed.inputPath,
			outputRoot: "workflow-output/subagent",
			resume: parsed.resume,
			finalizeOnly: parsed.command === "finalize-only",
			verifyProofOnly: parsed.command === "verify-proof",
		});
		console.log(JSON.stringify({ ...result, runDir }));
		if (result.terminal !== "success") process.exitCode = 2;
	} catch (error) {
		console.error(`[subagent-workflow] REFUSING to start: ${(error as Error).message}`);
		process.exitCode = 2;
	}
}

if (import.meta.main) await main();
