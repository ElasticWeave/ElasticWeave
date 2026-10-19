/**
 * Controller-owned state-bound completion proof primitives.
 *
 * This module performs no model or command invocation.  In particular, finalize-only
 * recovery is a pure verification + filesystem operation and therefore cannot replay a
 * model call.  A caller that obtains the current workspace binding from the frozen
 * reward-evidence helper must run that helper without GENERAL_AGENT_* authority.
 *
 * Threat boundary: these checks reject symlinks, non-regular files, path escapes, stale
 * bytes, and ordinary replacement mistakes.  They are practical same-process integrity
 * checks, not an OS sandbox.  A malicious process running concurrently as the same UID
 * can still race pathname operations between lstat/open/rename calls.
 */

import fs from "node:fs";
import path from "node:path";
import { createHash, randomUUID } from "node:crypto";
import { isDeepStrictEqual } from "node:util";

type JsonObject = Record<string, unknown>;

export const STATE_BOUND_PROOF_THREAT_BOUNDARY =
	"Practical no-follow byte binding; not an OS sandbox against a concurrent same-UID pathname attacker.";

export const REQUIRED_FROZEN_ARTIFACTS = [
	"controller",
	"native-core",
	"proof-module",
	"design",
	"mapping",
	"roles",
	"native-map",
	"input",
	"resources",
	"helper",
] as const;

const SHA256 = /^[0-9a-f]{64}$/;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const CANDIDATE_SCHEMA = "subagent-state-bound-candidate/v1" as const;
const FROZEN_SCHEMA = "subagent-frozen-artifacts/v2" as const;
const ALLOWED_FINAL_VERDICTS = new Set(["PASS", "APPROVE"]);
const PROOF_BINDING_SCHEMA = "subagent-proof-binding/v1" as const;
const LINEAR_UI_RECEIPT_SCHEMA = "linear-ui-mirror-receipt/v1" as const;
const LINEAR_UI_CONTENT_SCHEMA = "linear-ui-mirror-content/v1" as const;
const LINEAR_UI_OPERATION = "mirror-item-state-and-report" as const;
const LINEAR_UI_PRODUCER_KIND = "delegated-linear-worker" as const;
const LINEAR_UI_TRANSPORT = "authenticated-native-mcp" as const;
const LINEAR_AUTHENTICATED_EVIDENCE_UNAVAILABLE =
	"authenticated controller-owned Linear evidence is unavailable" as const;
const AUTHORIZATION_MODE = "controller-tier1-env" as const;
const AUTHORIZATION_SOURCE = "GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1" as const;
const issuedAuthorities = new WeakSet<object>();
const SHA256_TOKEN = /^sha256:[0-9a-f]{64}$/;
const OPAQUE_RECEIPT_ID = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/;
const UTC_INSTANT = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/;

export interface FileBinding {
	label: string;
	relativePath: string;
	sha256: string;
	bytes: number;
	mode: number;
}

export interface DeclaredFileBinding {
	path: string;
	sha256: string;
}

interface ProofBindingBase extends JsonObject {
	schema: typeof PROOF_BINDING_SCHEMA;
	itemId: string;
	roleModelPolicyDigest: string;
}

export interface PmItemProofBindingDeclaration extends ProofBindingBase {
	scope: "pm-item";
	itemKind: "issue" | "milestone";
	milestoneId?: string;
	milestone?: DeclaredFileBinding;
	config: DeclaredFileBinding;
	goal: DeclaredFileBinding;
	criteria: DeclaredFileBinding;
	report: DeclaredFileBinding;
	children: DeclaredFileBinding[];
	uiReceipts: DeclaredFileBinding[];
}

export interface CriteriaArtifactProofBindingDeclaration extends ProofBindingBase {
	scope: "criteria-artifact";
	criteria: DeclaredFileBinding;
}

export interface NoPmProofBindingDeclaration extends ProofBindingBase {
	scope: "no-pm";
	reason: string;
}

export type ProofBindingDeclaration =
	| PmItemProofBindingDeclaration
	| CriteriaArtifactProofBindingDeclaration
	| NoPmProofBindingDeclaration;

export interface ProofBinding {
	schema: typeof PROOF_BINDING_SCHEMA;
	scope: "pm-item" | "criteria-artifact" | "no-pm";
	itemKind: "issue" | "milestone" | "none";
	itemId: string;
	milestoneId: string;
	roleModelPolicyDigest: string;
	reason: string;
	artifacts: FileBinding[];
	digest: string;
}

/**
 * A native resolver's byte binding for an executable selected at run start.
 * The proof layer independently re-resolves and re-hashes the path; this is a
 * declaration to check, not trusted metadata.
 */
export interface ResolvedExecutableBinding {
	path: string;
	sha256: string;
}

/** A no-follow snapshot of an executable outside the project tree. */
export interface FrozenExecutableBinding {
	label: string;
	path: string;
	sha256: string;
	bytes: number;
	mode: number;
	dev: string;
	ino: string;
	ctimeNs: string;
	mtimeNs: string;
	size: string;
}

export interface FrozenArtifactSet {
	schema: typeof FROZEN_SCHEMA;
	root: string;
	rootDevice: number;
	rootInode: number;
	requiredLabels: string[];
	files: FileBinding[];
	executables: FrozenExecutableBinding[];
	digest: string;
}

export interface TerminalBinding {
	status: "success";
	terminal: string;
	completed: string[];
	frontier: [];
	pending: [];
	inFlight: "";
	modelCalls: number;
	schedulerCalls: number;
	nodeCalls: number;
	maxModelCalls: number;
	digest: string;
}

export interface MachineReviewRecord extends JsonObject {
	activation: number;
	call: number;
	nodeId: string;
	verdict: string;
	promptPath: string;
	promptSha256: string;
	promptCommitmentDigest: string;
	reviewedStateDigest: string;
	reviewedInputsDigest: string;
	evidenceDigest: string;
	nativeTracePath: string;
	traceSha256: string;
	responsePath: string;
	responseSha256: string;
	receiptPath: string;
	receiptSha256: string;
}

export interface MachineReviewBinding {
	ledger: FileBinding;
	nativeTrace: FileBinding;
	traceCalls: number;
	traceCall: JsonObject;
	prompt: FileBinding;
	response: FileBinding;
	receipt: FileBinding;
	latest: MachineReviewRecord;
	reviewDigest: string;
}

export interface EvidenceProducerBinding {
	nodeId: string;
	activation: number;
	argvSha256: string;
	evidencePath: string;
	evidenceSha256: string;
	stdout: FileBinding;
	stderr: FileBinding;
	receipt: FileBinding;
	producerDigest: string;
}

export interface WorkspaceEvidenceBinding {
	evidenceFile: FileBinding;
	baselineDigest: string;
	evidenceDigest: string;
	finalStateDigest: string;
	stateBinding: JsonObject;
}

export interface StateBoundCandidate {
	schema: typeof CANDIDATE_SCHEMA;
	projectRoot: string;
	runRoot: string;
	runDevice: number;
	runInode: number;
	itemId: string;
	runId: string;
	designId: string;
	proofBinding: ProofBinding;
	frozen: FrozenArtifactSet;
	terminal: TerminalBinding;
	machineReview: MachineReviewBinding;
	workspace: WorkspaceEvidenceBinding;
	evidenceProducer: EvidenceProducerBinding;
	authorization: {
		mode: typeof AUTHORIZATION_MODE;
		source: typeof AUTHORIZATION_SOURCE;
		finalizer: "controller-only";
	};
	createdAt: string;
	threatBoundary: typeof STATE_BOUND_PROOF_THREAT_BOUNDARY;
	candidateDigest: string;
}

export interface CreateCandidateOptions {
	projectRoot: string;
	runRoot: string;
	itemId: string;
	runId: string;
	designId: string;
	proofBinding: ProofBinding;
	frozen: FrozenArtifactSet;
	terminal: TerminalBinding;
	machineReview: MachineReviewBinding;
	workspace: WorkspaceEvidenceBinding;
	evidenceProducer: EvidenceProducerBinding;
	currentWorkspaceBinding: JsonObject;
	candidatePath?: string;
	now?: string;
}

export interface FinalizeOnlyOptions {
	projectRoot: string;
	runRoot: string;
	itemId: string;
	currentWorkspaceBinding: JsonObject;
	roleModelPolicyDigest: string;
	candidatePath?: string;
	/** Opaque token issued only after the controller proves its tier-1 environment. */
	authority: ControllerFinalizeAuthority;
}

export interface ControllerFinalizeAuthority {
	readonly mode: typeof AUTHORIZATION_MODE;
	readonly source: typeof AUTHORIZATION_SOURCE;
}

export interface FinalizeOnlyResult {
	proofPath: string;
	created: boolean;
	idempotent: boolean;
	candidateDigest: string;
	modelCallsReplayed: 0;
}

export class StateBoundProofError extends Error {
	constructor(message: string, cause?: unknown) {
		super(`state-bound proof: ${message}`, cause === undefined ? undefined : { cause });
		this.name = "StateBoundProofError";
	}
}

function sha256(value: Buffer | string): string {
	return createHash("sha256").update(value).digest("hex");
}

/** Stable JSON used only for controller-owned digests, not Python-helper digests. */
export function canonicalJson(value: unknown): string {
	if (value === null) return "null";
	if (typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
	if (typeof value === "number") {
		if (!Number.isFinite(value)) throw new StateBoundProofError("canonical JSON rejects non-finite numbers");
		return JSON.stringify(value);
	}
	if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
	if (value && typeof value === "object") {
		const object = value as Record<string, unknown>;
		return `{${Object.keys(object).sort().map(key => {
			if (object[key] === undefined) throw new StateBoundProofError(`canonical JSON rejects undefined field ${key}`);
			return `${JSON.stringify(key)}:${canonicalJson(object[key])}`;
		}).join(",")}}`;
	}
	throw new StateBoundProofError(`canonical JSON rejects ${typeof value}`);
}

function digestJson(value: unknown): string {
	return sha256(canonicalJson(value));
}

function asciiJsonString(value: string): string {
	const encoded = JSON.stringify(value);
	let ascii = "";
	// Iterate UTF-16 code units deliberately.  An astral character therefore becomes
	// two \uXXXX surrogate escapes, matching Python json.dumps(ensure_ascii=True).
	for (let index = 0; index < encoded.length; index += 1) {
		const codeUnit = encoded.charCodeAt(index);
		ascii += codeUnit >= 0x7f
			? `\\u${codeUnit.toString(16).padStart(4, "0")}`
			: encoded[index];
	}
	return ascii;
}

function compareUnicodeCodePoints(left: string, right: string): number {
	const a = Array.from(left, character => character.codePointAt(0)!);
	const b = Array.from(right, character => character.codePointAt(0)!);
	for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
		if (a[index] !== b[index]) return a[index] - b[index];
	}
	return a.length - b.length;
}

/**
 * Matches the helper's json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)
 * on the helper evidence schema, whose numeric fields are safe integers.  Non-integer,
 * unsafe, and negative-zero numbers fail closed because JS cannot preserve Python's
 * original float spelling after JSON.parse.
 */
function helperCanonicalJson(value: unknown): string {
	if (value === null) return "null";
	if (typeof value === "string") return asciiJsonString(value);
	if (typeof value === "boolean") return value ? "true" : "false";
	if (typeof value === "number") {
		if (!Number.isSafeInteger(value) || Object.is(value, -0)) {
			throw new StateBoundProofError("helper evidence contains a number outside the safe-integer schema");
		}
		return JSON.stringify(value);
	}
	if (Array.isArray(value)) return `[${value.map(helperCanonicalJson).join(",")}]`;
	if (value && typeof value === "object") {
		const object = value as Record<string, unknown>;
		return `{${Object.keys(object).sort(compareUnicodeCodePoints).map(key => {
			if (object[key] === undefined) throw new StateBoundProofError(`helper evidence contains undefined field ${key}`);
			return `${asciiJsonString(key)}:${helperCanonicalJson(object[key])}`;
		}).join(",")}}`;
	}
	throw new StateBoundProofError(`helper evidence contains unsupported ${typeof value}`);
}

function isPlainObject(value: unknown): value is JsonObject {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function requireSafeId(value: string, label: string): void {
	if (!SAFE_ID.test(value)) throw new StateBoundProofError(`${label} is path-unsafe: ${JSON.stringify(value)}`);
}

/** Canonical machine-gated workflow proof: `.pm/workflow-reviews/<itemId>.md`. */
export function canonicalMachineWorkflowProofRelative(itemId: string): string {
	requireSafeId(itemId, "itemId");
	return path.join(".pm", "workflow-reviews", `${itemId}.md`);
}

/** Retired colliding path `.pm/reviews/workflow-<itemId>.md`. Never accepted as a machine proof. */
export function legacyMachineWorkflowProofRelative(itemId: string): string {
	requireSafeId(itemId, "itemId");
	return path.join(".pm", "reviews", `workflow-${itemId}.md`);
}

function requireDigest(value: unknown, label: string): asserts value is string {
	if (typeof value !== "string" || !SHA256.test(value)) throw new StateBoundProofError(`${label} is not SHA-256`);
}

function plainDirectory(directory: string, label: string): fs.Stats {
	let stat: fs.Stats;
	try {
		stat = fs.lstatSync(directory);
	} catch (error) {
		throw new StateBoundProofError(`${label} is unavailable`, error);
	}
	if (stat.isSymbolicLink() || !stat.isDirectory()) throw new StateBoundProofError(`${label} is not a plain directory`);
	return stat;
}

function normalizedRoot(root: string, label: string): string {
	const resolved = path.resolve(root);
	plainDirectory(resolved, label);
	let real: string;
	try {
		real = fs.realpathSync(resolved);
	} catch (error) {
		throw new StateBoundProofError(`${label} cannot be resolved`, error);
	}
	if (real !== resolved) throw new StateBoundProofError(`${label} must use its real, non-symlink path`);
	return real;
}

function safeRelative(value: string, label: string): string {
	if (!value || path.isAbsolute(value)) throw new StateBoundProofError(`${label} must be a non-empty relative path`);
	const normalized = path.normalize(value);
	if (normalized === "." || normalized === ".." || normalized.startsWith(`..${path.sep}`)) {
		throw new StateBoundProofError(`${label} escapes its root`);
	}
	return normalized;
}

function inspectParents(root: string, relative: string, create: boolean): string {
	const normalized = safeRelative(relative, "artifact path");
	let current = root;
	const parts = normalized.split(path.sep);
	for (const segment of parts.slice(0, -1)) {
		current = path.join(current, segment);
		let exists = true;
		try {
			fs.lstatSync(current);
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code === "ENOENT") exists = false;
			else throw new StateBoundProofError(`cannot inspect parent directory ${current}`, error);
		}
		if (!exists) {
			if (!create) throw new StateBoundProofError(`parent directory is missing: ${current}`);
			try {
				fs.mkdirSync(current, { mode: 0o700 });
			} catch (error) {
				throw new StateBoundProofError(`could not create parent directory ${current}`, error);
			}
		}
		plainDirectory(current, `parent directory ${current}`);
	}
	return path.join(root, normalized);
}

function existingEntry(file: string): fs.Stats | undefined {
	try {
		return fs.lstatSync(file);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
		throw new StateBoundProofError(`cannot inspect ${file}`, error);
	}
}

function readRegularNoFollow(root: string, relative: string): { bytes: Buffer; stat: fs.Stats } {
	const file = inspectParents(root, relative, false);
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		const stat = fs.fstatSync(descriptor);
		if (!stat.isFile()) throw new StateBoundProofError(`${relative} is not a regular file`);
		return { bytes: fs.readFileSync(descriptor), stat };
	} catch (error) {
		if (error instanceof StateBoundProofError) throw error;
		throw new StateBoundProofError(`cannot read ${relative} without following symlinks`, error);
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
}

function bindFile(root: string, relative: string, label: string): FileBinding {
	const normalized = safeRelative(relative, `${label} path`);
	const { bytes, stat } = readRegularNoFollow(root, normalized);
	return {
		label,
		relativePath: normalized,
		sha256: sha256(bytes),
		bytes: bytes.length,
		mode: stat.mode & 0o7777,
	};
}

function verifyFileBinding(root: string, expected: FileBinding): void {
	const actual = bindFile(root, expected.relativePath, expected.label);
	if (canonicalJson(actual) !== canonicalJson(expected)) {
		throw new StateBoundProofError(`${expected.label} bytes or mode changed at ${expected.relativePath}`);
	}
}

function exactKeys(value: JsonObject, expected: string[], label: string): void {
	if (!isDeepStrictEqual(Object.keys(value).sort(), [...expected].sort())) {
		throw new StateBoundProofError(`${label} has missing or unsupported fields`);
	}
}

function projectRelativePosix(value: unknown, label: string): string {
	if (typeof value !== "string" || !value || value.includes("\\") || path.posix.isAbsolute(value) ||
		path.posix.normalize(value) !== value || value === "." || value === ".." || value.startsWith("../")) {
		throw new StateBoundProofError(`${label} must be a normalized project-relative POSIX path`);
	}
	return value;
}

function declarationFile(
	root: string,
	value: unknown,
	expectedPath: string | undefined,
	label: string,
): FileBinding {
	if (!isPlainObject(value)) throw new StateBoundProofError(`${label} declaration must be an object`);
	exactKeys(value, ["path", "sha256"], `${label} declaration`);
	const relative = projectRelativePosix(value.path, `${label} declaration path`);
	if (expectedPath !== undefined && relative !== expectedPath) {
		throw new StateBoundProofError(`${label} declaration must use canonical path ${expectedPath}`);
	}
	requireDigest(value.sha256, `${label} declared digest`);
	const binding = bindFile(root, relative, label);
	if (binding.sha256 !== value.sha256) throw new StateBoundProofError(`${label} declared digest disagrees with current bytes`);
	return binding;
}

function sortedDeclarations(value: unknown, label: string): JsonObject[] {
	if (!Array.isArray(value) || value.some(item => !isPlainObject(item))) {
		throw new StateBoundProofError(`${label} declarations must be an array of objects`);
	}
	const declarations = value as JsonObject[];
	const paths = declarations.map((item, index) => {
		exactKeys(item, ["path", "sha256"], `${label} declaration ${index + 1}`);
		return projectRelativePosix(item.path, `${label} declaration ${index + 1} path`);
	});
	if (new Set(paths).size !== paths.length || !isDeepStrictEqual(paths, [...paths].sort())) {
		throw new StateBoundProofError(`${label} declarations must be unique and path-sorted`);
	}
	return declarations;
}

function canonicalMilestoneChildren(root: string, milestoneId: string): string[] {
	requireSafeId(milestoneId, "proof binding milestoneId");
	const relativeDirectory = path.posix.join(".pm", "issues", milestoneId);
	const directory = path.join(root, relativeDirectory);
	const info = existingEntry(directory);
	if (!info) return [];
	if (info.isSymbolicLink() || !info.isDirectory()) {
		throw new StateBoundProofError(`canonical issue directory is not a plain directory: ${relativeDirectory}`);
	}
	return fs.readdirSync(directory, { withFileTypes: true }).map(entry => {
		if (entry.isSymbolicLink() || !entry.isFile() || !entry.name.endsWith(".md")) {
			throw new StateBoundProofError(`canonical issue directory contains an ambiguous entry: ${entry.name}`);
		}
		return path.posix.join(relativeDirectory, entry.name);
	}).sort();
}

function canonicalIssuePath(root: string, itemId: string): string {
	requireSafeId(itemId, "proof binding issue itemId");
	const relativeRoot = path.posix.join(".pm", "issues");
	const directory = path.join(root, relativeRoot);
	const info = existingEntry(directory);
	if (!info || info.isSymbolicLink() || !info.isDirectory()) {
		throw new StateBoundProofError("canonical issue root is missing, symlinked, or not a directory");
	}
	const matches: string[] = [];
	for (const milestone of fs.readdirSync(directory, { withFileTypes: true })) {
		if (milestone.isSymbolicLink() || !milestone.isDirectory() || !SAFE_ID.test(milestone.name)) {
			throw new StateBoundProofError(`canonical issue root contains an ambiguous entry: ${milestone.name}`);
		}
		const relative = path.posix.join(relativeRoot, milestone.name, `${itemId}.md`);
		const child = existingEntry(path.join(root, relative));
		if (!child) continue;
		if (child.isSymbolicLink() || !child.isFile()) {
			throw new StateBoundProofError(`canonical issue item is ambiguous: ${relative}`);
		}
		matches.push(relative);
	}
	if (matches.length !== 1) {
		throw new StateBoundProofError(`canonical issue item identity has ${matches.length} matching paths`);
	}
	return matches[0];
}

function existingPmItemPaths(root: string, itemId: string): string[] {
	requireSafeId(itemId, "proof binding itemId");
	const matches: string[] = [];
	const milestone = path.posix.join(".pm", "milestones", `${itemId}.md`);
	const milestoneInfo = existingEntry(path.join(root, milestone));
	if (milestoneInfo) {
		if (milestoneInfo.isSymbolicLink() || !milestoneInfo.isFile()) {
			throw new StateBoundProofError(`canonical PM item is ambiguous: ${milestone}`);
		}
		matches.push(milestone);
	}
	const issueRoot = path.join(root, ".pm", "issues");
	const issueRootInfo = existingEntry(issueRoot);
	if (!issueRootInfo) return matches;
	if (issueRootInfo.isSymbolicLink() || !issueRootInfo.isDirectory()) {
		throw new StateBoundProofError("canonical issue root is symlinked or not a directory");
	}
	for (const entry of fs.readdirSync(issueRoot, { withFileTypes: true })) {
		if (entry.isSymbolicLink() || !entry.isDirectory() || !SAFE_ID.test(entry.name)) {
			throw new StateBoundProofError(`canonical issue root contains an ambiguous entry: ${entry.name}`);
		}
		const relative = path.posix.join(".pm", "issues", entry.name, `${itemId}.md`);
		const info = existingEntry(path.join(root, relative));
		if (!info) continue;
		if (info.isSymbolicLink() || !info.isFile()) {
			throw new StateBoundProofError(`canonical PM item is ambiguous: ${relative}`);
		}
		matches.push(relative);
	}
	return matches.sort();
}

function readJsonObjectNoFollow(root: string, relative: string, label: string): JsonObject {
	let value: unknown;
	try {
		value = JSON.parse(readRegularNoFollow(root, relative).bytes.toString("utf8"));
	} catch (error) {
		throw new StateBoundProofError(`${label} is not readable JSON`, error);
	}
	if (!isPlainObject(value)) throw new StateBoundProofError(`${label} is not a JSON object`);
	return value;
}

function markdownFrontmatter(root: string, relative: string, label: string): JsonObject {
	const text = readRegularNoFollow(root, relative).bytes.toString("utf8");
	if (!text.startsWith("---\n") && !text.startsWith("---\r\n")) {
		throw new StateBoundProofError(`${label} has no canonical frontmatter`);
	}
	const lines = text.split(/\r?\n/u);
	const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
	if (end < 0) throw new StateBoundProofError(`${label} frontmatter is unterminated`);
	const metadata: JsonObject = {};
	for (const line of lines.slice(1, end)) {
		if (!line.trim() || line.trimStart().startsWith("#")) continue;
		const separator = line.indexOf(":");
		if (separator < 1) continue;
		const key = line.slice(0, separator).trim();
		const value = line.slice(separator + 1).trim();
		if (metadata[key] !== undefined) throw new StateBoundProofError(`${label} frontmatter duplicates ${key}`);
		metadata[key] = value;
	}
	return metadata;
}

function verifyPmItemIdentity(
	root: string,
	relative: string,
	itemKind: "issue" | "milestone",
	itemId: string,
	milestoneId: string,
	label: string,
): void {
	const metadata = markdownFrontmatter(root, relative, label);
	if (metadata.id !== itemId) throw new StateBoundProofError(`${label} frontmatter item identity disagrees with its canonical path`);
	if (itemKind === "issue" && metadata.milestone !== milestoneId) {
		throw new StateBoundProofError(`${label} frontmatter milestone identity disagrees with its canonical path`);
	}
}

function verifyReportIdentity(root: string, relative: string, itemKind: "issue" | "milestone", itemId: string): void {
	const firstLine = readRegularNoFollow(root, relative).bytes.toString("utf8").split(/\r?\n/u)
		.find(line => line.trim())?.trim();
	if (firstLine !== `# report: ${itemKind} ${itemId}`) {
		throw new StateBoundProofError("report item identity disagrees with its canonical path");
	}
}

function requireSha256Token(value: unknown, label: string): asserts value is string {
	if (typeof value !== "string" || !SHA256_TOKEN.test(value)) {
		throw new StateBoundProofError(`${label} is not a sha256: digest`);
	}
}

function requireOpaqueReceiptId(value: unknown, label: string): asserts value is string {
	if (typeof value !== "string" || !OPAQUE_RECEIPT_ID.test(value)) {
		throw new StateBoundProofError(`${label} is missing or unsafe`);
	}
}

function requireUtcInstant(value: unknown, label: string): asserts value is string {
	if (typeof value !== "string" || !UTC_INSTANT.test(value) || !Number.isFinite(Date.parse(value))) {
		throw new StateBoundProofError(`${label} is not an ISO-8601 UTC instant`);
	}
}

function linearOperations(itemKind: "issue" | "milestone", remoteKind: unknown): {
	mutation: string;
	readback: string;
} {
	if (itemKind === "issue" && remoteKind === "issue") {
		return { mutation: "save_issue", readback: "get_issue" };
	}
	if (itemKind === "milestone" && remoteKind === "project") {
		return { mutation: "save_project", readback: "get_project" };
	}
	if (itemKind === "milestone" && remoteKind === "project-milestone") {
		return { mutation: "save_milestone", readback: "get_milestone" };
	}
	throw new StateBoundProofError("UI receipt remote kind disagrees with the PM item kind");
}

function linearMirrorContentDigest(
	itemKind: "issue" | "milestone",
	itemId: string,
	remoteKind: string,
	remoteId: string,
	targetState: string,
	criteria: FileBinding,
	report: FileBinding,
): string {
	return `sha256:${digestJson({
		schema: LINEAR_UI_CONTENT_SCHEMA,
		itemKind,
		itemId,
		remoteKind,
		remoteId,
		targetState,
		criteria: { path: criteria.relativePath, sha256: criteria.sha256 },
		report: { path: report.relativePath, sha256: report.sha256 },
	})}`;
}

/**
 * Future receipt schema contract only. Local JSON and self-digests do not authenticate a Linear
 * mutation, so every authorization path rejects linear mode before this validator can be reached.
 */
function validateLinearUiReceipt(
	root: string,
	receipt: FileBinding,
	itemKind: "issue" | "milestone",
	itemId: string,
	criteria: FileBinding,
	report: FileBinding,
): void {
	const value = readJsonObjectNoFollow(root, receipt.relativePath, "UI receipt");
	exactKeys(value, [
		"schema", "producer", "request", "mutation", "mutationSha256", "readback", "readbackSha256", "receiptSha256",
	], "UI receipt");
	if (value.schema !== LINEAR_UI_RECEIPT_SCHEMA) throw new StateBoundProofError("UI receipt schema is unsupported");
	if (!isPlainObject(value.producer)) throw new StateBoundProofError("UI receipt producer is not an object");
	exactKeys(value.producer, ["kind", "provider", "transport", "invocationId"], "UI receipt producer");
	if (
		value.producer.kind !== LINEAR_UI_PRODUCER_KIND || value.producer.provider !== "linear" ||
		value.producer.transport !== LINEAR_UI_TRANSPORT
	) {
		throw new StateBoundProofError("UI receipt does not name the authenticated delegated Linear producer");
	}
	requireOpaqueReceiptId(value.producer.invocationId, "UI receipt producer invocationId");

	if (!isPlainObject(value.request)) throw new StateBoundProofError("UI receipt request is not an object");
	exactKeys(value.request, [
		"operation", "itemKind", "itemId", "remoteKind", "remoteId", "transition", "contentSha256",
	], "UI receipt request");
	if (value.request.operation !== LINEAR_UI_OPERATION || value.request.itemKind !== itemKind || value.request.itemId !== itemId) {
		throw new StateBoundProofError("UI receipt request does not bind the PM item and mirror operation");
	}
	requireOpaqueReceiptId(value.request.remoteId, "UI receipt request remoteId");
	const operations = linearOperations(itemKind, value.request.remoteKind);
	if (!isPlainObject(value.request.transition)) throw new StateBoundProofError("UI receipt transition is not an object");
	exactKeys(value.request.transition, ["from", "to"], "UI receipt transition");
	if (
		typeof value.request.transition.from !== "string" || !value.request.transition.from.trim() ||
		typeof value.request.transition.to !== "string" || !value.request.transition.to.trim()
	) {
		throw new StateBoundProofError("UI receipt transition states must be non-empty strings");
	}
	const criteriaMetadata = markdownFrontmatter(root, criteria.relativePath, "criteria");
	if (criteriaMetadata.linear_id !== value.request.remoteId) {
		throw new StateBoundProofError("UI receipt remote identity disagrees with canonical PM linear_id");
	}
	if (criteriaMetadata.status !== value.request.transition.to) {
		throw new StateBoundProofError("UI receipt requested target state disagrees with canonical PM status");
	}
	const expectedContentSha256 = linearMirrorContentDigest(
		itemKind,
		itemId,
		value.request.remoteKind as string,
		value.request.remoteId,
		value.request.transition.to,
		criteria,
		report,
	);
	if (value.request.contentSha256 !== expectedContentSha256) {
		throw new StateBoundProofError("UI receipt request content digest does not bind the canonical criteria and report");
	}

	if (!isPlainObject(value.mutation)) throw new StateBoundProofError("UI receipt mutation evidence is not an object");
	exactKeys(value.mutation, [
		"operation", "remoteKind", "remoteId", "observedState", "contentSha256", "providerRevision",
	], "UI receipt mutation evidence");
	if (!isPlainObject(value.readback)) throw new StateBoundProofError("UI receipt readback evidence is not an object");
	exactKeys(value.readback, [
		"operation", "remoteKind", "remoteId", "observedState", "contentSha256", "providerRevision", "observedAt",
	], "UI receipt readback evidence");
	for (const [label, evidence, operation] of [
		["mutation", value.mutation, operations.mutation],
		["readback", value.readback, operations.readback],
	] as const) {
		if (
			evidence.operation !== operation || evidence.remoteKind !== value.request.remoteKind ||
			evidence.remoteId !== value.request.remoteId || evidence.observedState !== value.request.transition.to ||
			evidence.contentSha256 !== expectedContentSha256
		) {
			throw new StateBoundProofError(`UI receipt ${label} does not bind the requested remote identity, state, and content`);
		}
		requireUtcInstant(evidence.providerRevision, `UI receipt ${label} provider revision`);
	}
	if (value.mutation.providerRevision !== value.readback.providerRevision) {
		throw new StateBoundProofError("UI receipt mutation and readback revisions disagree");
	}
	requireUtcInstant(value.readback.observedAt, "UI receipt readback observedAt");
	if (Date.parse(value.readback.observedAt) < Date.parse(value.readback.providerRevision as string)) {
		throw new StateBoundProofError("UI receipt readback predates the observed provider revision");
	}
	requireSha256Token(value.mutationSha256, "UI receipt mutation digest");
	requireSha256Token(value.readbackSha256, "UI receipt readback digest");
	if (value.mutationSha256 !== `sha256:${digestJson(value.mutation)}`) {
		throw new StateBoundProofError("UI receipt mutation digest is invalid");
	}
	if (value.readbackSha256 !== `sha256:${digestJson(value.readback)}`) {
		throw new StateBoundProofError("UI receipt readback digest is invalid");
	}
	requireSha256Token(value.receiptSha256, "UI receipt self-digest");
	const { receiptSha256, ...material } = value;
	if (receiptSha256 !== `sha256:${digestJson(material)}`) {
		throw new StateBoundProofError("UI receipt self-digest is invalid");
	}
}

function requireAuthorizablePmUiMode(value: unknown): asserts value is "file" | "interactive" {
	if (value === "linear") throw new StateBoundProofError(LINEAR_AUTHENTICATED_EVIDENCE_UNAVAILABLE);
	if (value !== "interactive" && value !== "file") throw new StateBoundProofError("PM config ui_mode is invalid");
}

function proofBindingMaterial(binding: Omit<ProofBinding, "digest">): string {
	return digestJson(binding);
}

export function captureProofBinding(
	projectRoot: string,
	declarationValue: unknown,
	expectedItemId: string,
	roleModelPolicyDigest: string,
): ProofBinding {
	const root = normalizedRoot(projectRoot, "project root");
	requireSafeId(expectedItemId, "proof binding expected itemId");
	requireDigest(roleModelPolicyDigest, "current role/model policy digest");
	if (!isPlainObject(declarationValue)) throw new StateBoundProofError("proof binding declaration must be an object");
	const declaration = declarationValue as JsonObject;
	if (declaration.schema !== PROOF_BINDING_SCHEMA) throw new StateBoundProofError("proof binding schema is unsupported");
	if (declaration.itemId !== expectedItemId) throw new StateBoundProofError("proof binding item identity disagrees with workflow state");
	if (declaration.roleModelPolicyDigest !== roleModelPolicyDigest) {
		throw new StateBoundProofError("proof binding role/model policy digest disagrees with the active role file");
	}
	const scope = declaration.scope;
	if (scope === "no-pm") {
		exactKeys(declaration, ["schema", "scope", "itemId", "roleModelPolicyDigest", "reason"], "no-pm proof binding");
		if (typeof declaration.reason !== "string" || !declaration.reason.trim()) {
			throw new StateBoundProofError("no-pm proof binding requires a non-empty reason");
		}
		if (existingPmItemPaths(root, expectedItemId).length > 0) {
			throw new StateBoundProofError("no-pm proof binding conflicts with an existing canonical PM item");
		}
		const material: Omit<ProofBinding, "digest"> = {
			schema: PROOF_BINDING_SCHEMA,
			scope,
			itemKind: "none",
			itemId: expectedItemId,
			milestoneId: "",
			roleModelPolicyDigest,
			reason: declaration.reason.trim(),
			artifacts: [],
		};
		return { ...material, digest: proofBindingMaterial(material) };
	}
	if (scope === "criteria-artifact") {
		exactKeys(declaration, ["schema", "scope", "itemId", "roleModelPolicyDigest", "criteria"], "criteria-artifact proof binding");
		const criteria = declarationFile(root, declaration.criteria, undefined, "criteria");
		if (criteria.relativePath === ".pm" || criteria.relativePath.startsWith(".pm/")) {
			throw new StateBoundProofError("criteria-artifact scope cannot bind a .pm path; use pm-item scope");
		}
		if (existingPmItemPaths(root, expectedItemId).length > 0) {
			throw new StateBoundProofError("criteria-artifact proof binding conflicts with an existing canonical PM item");
		}
		const material: Omit<ProofBinding, "digest"> = {
			schema: PROOF_BINDING_SCHEMA,
			scope,
			itemKind: "none",
			itemId: expectedItemId,
			milestoneId: "",
			roleModelPolicyDigest,
			reason: "",
			artifacts: [criteria],
		};
		return { ...material, digest: proofBindingMaterial(material) };
	}
	if (scope !== "pm-item") throw new StateBoundProofError("proof binding scope must be pm-item, criteria-artifact, or no-pm");
	exactKeys(declaration, [
		"schema", "scope", "itemKind", "itemId", "roleModelPolicyDigest", "config", "goal", "criteria", "report", "children", "uiReceipts",
		...(declaration.itemKind === "issue" ? ["milestoneId", "milestone"] : []),
	], "PM proof binding");
	const itemKind = declaration.itemKind;
	if (itemKind !== "issue" && itemKind !== "milestone") throw new StateBoundProofError("PM proof binding itemKind is invalid");
	const milestoneId = itemKind === "issue" && typeof declaration.milestoneId === "string" ? declaration.milestoneId : expectedItemId;
	requireSafeId(milestoneId, "proof binding milestoneId");
	const config = declarationFile(root, declaration.config, ".pm/config.json", "config");
	const uiMode = readJsonObjectNoFollow(root, config.relativePath, "PM config").ui_mode;
	requireAuthorizablePmUiMode(uiMode);
	const goal = declarationFile(root, declaration.goal, ".pm/goal.md", "goal");
	const milestone = itemKind === "issue"
		? declarationFile(root, declaration.milestone, path.posix.join(".pm", "milestones", `${milestoneId}.md`), "milestone")
		: undefined;
	if (milestone) verifyPmItemIdentity(root, milestone.relativePath, "milestone", milestoneId, milestoneId, "milestone");
	const criteriaPath = itemKind === "issue"
		? canonicalIssuePath(root, expectedItemId)
		: path.posix.join(".pm", "milestones", `${expectedItemId}.md`);
	if (itemKind === "issue" && criteriaPath !== path.posix.join(".pm", "issues", milestoneId, `${expectedItemId}.md`)) {
		throw new StateBoundProofError("proof binding milestoneId disagrees with the unique canonical issue path");
	}
	const criteria = declarationFile(root, declaration.criteria, criteriaPath, "criteria");
	verifyPmItemIdentity(root, criteria.relativePath, itemKind, expectedItemId, milestoneId, "criteria");
	const report = declarationFile(root, declaration.report, path.posix.join(".pm", "reports", `${expectedItemId}.md`), "report");
	verifyReportIdentity(root, report.relativePath, itemKind, expectedItemId);
	const childDeclarations = sortedDeclarations(declaration.children, "child");
	const expectedChildren = itemKind === "milestone" ? canonicalMilestoneChildren(root, expectedItemId) : [];
	const declaredChildren = childDeclarations.map(item => item.path as string);
	if (!isDeepStrictEqual(declaredChildren, expectedChildren)) throw new StateBoundProofError("canonical child set changed or is ambiguous");
	const children = childDeclarations.map((item, index) => declarationFile(root, item, expectedChildren[index], `child-${index + 1}`));
	for (const child of children) {
		const childId = path.posix.basename(child.relativePath, ".md");
		verifyPmItemIdentity(root, child.relativePath, "issue", childId, expectedItemId, child.label);
	}
	const uiDeclarations = sortedDeclarations(declaration.uiReceipts, "UI receipt");
	if (uiDeclarations.length !== 0) {
		throw new StateBoundProofError("UI receipt set disagrees with canonical ui_mode requirements");
	}
	const material: Omit<ProofBinding, "digest"> = {
		schema: PROOF_BINDING_SCHEMA,
		scope,
		itemKind,
		itemId: expectedItemId,
		milestoneId,
		roleModelPolicyDigest,
		reason: "",
		artifacts: [config, goal, ...(milestone ? [milestone] : []), criteria, report, ...children],
	};
	return { ...material, digest: proofBindingMaterial(material) };
}

export function verifyProofBinding(
	projectRoot: string,
	expected: ProofBinding,
	roleModelPolicyDigest: string,
): void {
	const root = normalizedRoot(projectRoot, "project root");
	requireDigest(expected.digest, "proof binding digest");
	requireDigest(roleModelPolicyDigest, "current role/model policy digest");
	const { digest, ...material } = expected;
	if (proofBindingMaterial(material) !== digest) throw new StateBoundProofError("proof binding digest is invalid");
	if (expected.roleModelPolicyDigest !== roleModelPolicyDigest) {
		throw new StateBoundProofError("current role/model policy digest changed");
	}
	if (expected.scope === "pm-item") {
		const config = expected.artifacts.find(artifact => artifact.label === "config");
		if (!config || config.relativePath !== ".pm/config.json") {
			throw new StateBoundProofError("proof binding is missing canonical PM config");
		}
		verifyFileBinding(root, config);
		const uiMode = readJsonObjectNoFollow(root, config.relativePath, "PM config").ui_mode;
		requireAuthorizablePmUiMode(uiMode);
	}
	for (const artifact of expected.artifacts) verifyFileBinding(root, artifact);
	if (expected.scope === "pm-item") {
		const expectedChildren = expected.itemKind === "milestone" ? canonicalMilestoneChildren(root, expected.itemId) : [];
		const actualChildren = expected.artifacts.filter(artifact => artifact.label.startsWith("child-"))
			.map(artifact => artifact.relativePath);
		if (!isDeepStrictEqual(actualChildren, expectedChildren)) throw new StateBoundProofError("canonical child set changed or is ambiguous");
		const actualUi = expected.artifacts.filter(artifact => artifact.label.startsWith("ui-receipt-"))
			.map(artifact => artifact.relativePath);
		if (actualUi.length !== 0) throw new StateBoundProofError("canonical UI receipt set changed");
		const criteria = expected.artifacts.find(artifact => artifact.label === "criteria");
		if (!criteria) throw new StateBoundProofError("proof binding is missing PM criteria");
		verifyPmItemIdentity(root, criteria.relativePath, expected.itemKind, expected.itemId, expected.milestoneId, "criteria");
		if (expected.itemKind === "issue" && canonicalIssuePath(root, expected.itemId) !== criteria.relativePath) {
			throw new StateBoundProofError("canonical issue path changed or became ambiguous");
		}
		const milestone = expected.artifacts.find(artifact => artifact.label === "milestone");
		if (expected.itemKind === "issue") {
			if (!milestone || milestone.relativePath !== path.posix.join(".pm", "milestones", `${expected.milestoneId}.md`)) {
				throw new StateBoundProofError("proof binding is missing the canonical parent milestone");
			}
			verifyPmItemIdentity(root, milestone.relativePath, "milestone", expected.milestoneId, expected.milestoneId, "milestone");
		} else if (milestone) {
			throw new StateBoundProofError("milestone proof binding has an unexpected parent milestone artifact");
		}
		const report = expected.artifacts.find(artifact => artifact.label === "report");
		if (!report) throw new StateBoundProofError("proof binding is missing PM report");
		verifyReportIdentity(root, report.relativePath, expected.itemKind, expected.itemId);
		for (const child of expected.artifacts.filter(artifact => artifact.label.startsWith("child-"))) {
			verifyPmItemIdentity(
				root,
				child.relativePath,
				"issue",
				path.posix.basename(child.relativePath, ".md"),
				expected.itemId,
				child.label,
			);
		}
	}
}

function externalIdentity(stat: fs.BigIntStats): Pick<
	FrozenExecutableBinding,
	"dev" | "ino" | "ctimeNs" | "mtimeNs" | "size"
> {
	return {
		dev: stat.dev.toString(10),
		ino: stat.ino.toString(10),
		ctimeNs: stat.ctimeNs.toString(10),
		mtimeNs: stat.mtimeNs.toString(10),
		size: stat.size.toString(10),
	};
}

function executablePathOutsideProject(projectRoot: string, executablePath: string): boolean {
	const relative = path.relative(projectRoot, executablePath);
	return relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative);
}

function inspectResolvedExecutable(
	projectRoot: string,
	label: string,
	declared: ResolvedExecutableBinding,
): FrozenExecutableBinding {
	requireSafeId(label, "resolved executable label");
	if (!isPlainObject(declared) || typeof declared.path !== "string") {
		throw new StateBoundProofError(`resolved executable ${label} is malformed`);
	}
	requireDigest(declared.sha256, `resolved executable ${label} declared digest`);
	if (!path.isAbsolute(declared.path) || path.normalize(declared.path) !== declared.path || path.resolve(declared.path) !== declared.path) {
		throw new StateBoundProofError(`resolved executable ${label} path must be absolute and normalized`);
	}
	let real: string;
	try {
		real = fs.realpathSync(declared.path);
	} catch (error) {
		throw new StateBoundProofError(`resolved executable ${label} is unavailable`, error);
	}
	if (real !== declared.path) {
		throw new StateBoundProofError(`resolved executable ${label} path must equal its canonical real path`);
	}
	if (!executablePathOutsideProject(projectRoot, real)) {
		throw new StateBoundProofError(`resolved executable ${label} must be outside the project root`);
	}

	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(real, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		const before = fs.fstatSync(descriptor, { bigint: true });
		if (!before.isFile()) throw new StateBoundProofError(`resolved executable ${label} is not a regular file`);
		const mode = Number(before.mode & 0o7777n);
		if ((mode & 0o111) === 0) throw new StateBoundProofError(`resolved executable ${label} is not executable`);
		const bytes = fs.readFileSync(descriptor);
		const after = fs.fstatSync(descriptor, { bigint: true });
		if (canonicalJson(externalIdentity(before)) !== canonicalJson(externalIdentity(after))) {
			throw new StateBoundProofError(`resolved executable ${label} identity changed while reading`);
		}
		if (BigInt(bytes.length) !== after.size) {
			throw new StateBoundProofError(`resolved executable ${label} size changed while reading`);
		}
		const actualSha256 = sha256(bytes);
		if (actualSha256 !== declared.sha256) {
			throw new StateBoundProofError(`resolved executable ${label} declared digest does not match its bytes`);
		}
		return {
			label,
			path: real,
			sha256: actualSha256,
			bytes: bytes.length,
			mode,
			...externalIdentity(after),
		};
	} catch (error) {
		if (error instanceof StateBoundProofError) throw error;
		throw new StateBoundProofError(`cannot read resolved executable ${label} without following symlinks`, error);
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
}

function verifyFrozenExecutable(projectRoot: string, expected: FrozenExecutableBinding): void {
	if (!isPlainObject(expected)) throw new StateBoundProofError("frozen executable binding is malformed");
	const actual = inspectResolvedExecutable(projectRoot, expected.label, {
		path: expected.path,
		sha256: expected.sha256,
	});
	if (canonicalJson(actual) !== canonicalJson(expected)) {
		throw new StateBoundProofError(`resolved executable ${expected.label} bytes, mode, or identity changed at ${expected.path}`);
	}
}

function atomicWriteNoFollow(root: string, relative: string, content: Buffer): { created: boolean; idempotent: boolean } {
	const file = inspectParents(root, relative, true);
	const initial = existingEntry(file);
	if (initial) {
		if (initial.isSymbolicLink()) throw new StateBoundProofError(`refusing symlink target: ${relative}`);
		const existing = readRegularNoFollow(root, relative).bytes;
		if (existing.equals(content)) return { created: false, idempotent: true };
		throw new StateBoundProofError(`refusing to replace a different existing file: ${relative}`);
	}
	const temporary = path.join(path.dirname(file), `.${path.basename(file)}.tmp-${process.pid}-${randomUUID()}`);
	let descriptor: number | undefined;
	try {
		descriptor = fs.openSync(
			temporary,
			fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW,
			0o600,
		);
		fs.writeFileSync(descriptor, content);
		fs.fsyncSync(descriptor);
		fs.closeSync(descriptor);
		descriptor = undefined;
		const appeared = existingEntry(file);
		if (appeared) {
			if (appeared.isSymbolicLink()) throw new StateBoundProofError(`target appeared as a symlink: ${relative}`);
			const existing = readRegularNoFollow(root, relative).bytes;
			if (existing.equals(content)) {
				fs.unlinkSync(temporary);
				return { created: false, idempotent: true };
			}
			throw new StateBoundProofError(`target appeared with different bytes during finalization: ${relative}`);
		}
		fs.renameSync(temporary, file);
		const parent = fs.openSync(path.dirname(file), fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		try {
			fs.fsyncSync(parent);
		} finally {
			fs.closeSync(parent);
		}
		return { created: true, idempotent: false };
	} catch (error) {
		if (descriptor !== undefined) fs.closeSync(descriptor);
		try {
			const stat = fs.lstatSync(temporary);
			if (stat.isFile() && !stat.isSymbolicLink()) fs.unlinkSync(temporary);
		} catch {
			// The temporary may already have been renamed or removed.
		}
		if (error instanceof StateBoundProofError) throw error;
		throw new StateBoundProofError(`atomic no-follow write failed for ${relative}`, error);
	}
}

export function freezeProofArtifacts(
	projectRoot: string,
	artifacts: Record<string, string>,
	requiredAdditionalArtifacts: string[] = [],
	resolvedExecutables: Record<string, ResolvedExecutableBinding> = {},
): FrozenArtifactSet {
	// Extra labels are retained in the manifest.  A mapping makes conditional inputs
	// mandatory by passing their labels in requiredAdditionalArtifacts.
	const root = normalizedRoot(projectRoot, "project root");
	if (!Array.isArray(requiredAdditionalArtifacts) ||
		requiredAdditionalArtifacts.some(label => typeof label !== "string" || !SAFE_ID.test(label))) {
		throw new StateBoundProofError("required additional frozen artifact labels are malformed");
	}
	const requiredLabels = [...new Set([...REQUIRED_FROZEN_ARTIFACTS, ...requiredAdditionalArtifacts])].sort();
	for (const required of requiredLabels) {
		if (typeof artifacts[required] !== "string" || !artifacts[required]) {
			throw new StateBoundProofError(`missing required frozen artifact ${required}`);
		}
	}
	const labels = Object.keys(artifacts).sort();
	if (new Set(Object.values(artifacts).map(value => safeRelative(value, "frozen artifact path"))).size !== labels.length) {
		throw new StateBoundProofError("frozen artifacts must use distinct paths");
	}
	const rootStat = plainDirectory(root, "project root");
	const files = labels.map(label => bindFile(root, artifacts[label], label));
	if (!isPlainObject(resolvedExecutables)) throw new StateBoundProofError("resolved executable set is malformed");
	const executables = Object.keys(resolvedExecutables).sort()
		.map(label => inspectResolvedExecutable(root, label, resolvedExecutables[label]));
	const material = { root, rootDevice: rootStat.dev, rootInode: rootStat.ino, requiredLabels, files, executables };
	return { schema: FROZEN_SCHEMA, ...material, digest: digestJson(material) };
}

export function verifyFrozenProofArtifacts(frozen: FrozenArtifactSet): void {
	if (frozen.schema !== FROZEN_SCHEMA) throw new StateBoundProofError("unsupported frozen artifact schema");
	const root = normalizedRoot(frozen.root, "frozen project root");
	const stat = plainDirectory(root, "frozen project root");
	if (stat.dev !== frozen.rootDevice || stat.ino !== frozen.rootInode) {
		throw new StateBoundProofError("frozen project root identity changed");
	}
	const labels = new Set(frozen.files.map(file => file.label));
	if (!Array.isArray(frozen.requiredLabels) ||
		frozen.requiredLabels.some(label => typeof label !== "string" || !SAFE_ID.test(label)) ||
		new Set(frozen.requiredLabels).size !== frozen.requiredLabels.length) {
		throw new StateBoundProofError("frozen required-label contract is malformed");
	}
	for (const base of REQUIRED_FROZEN_ARTIFACTS) {
		if (!frozen.requiredLabels.includes(base)) throw new StateBoundProofError(`frozen required-label contract lost ${base}`);
	}
	for (const required of frozen.requiredLabels) {
		if (!labels.has(required)) throw new StateBoundProofError(`frozen artifact set lost ${required}`);
	}
	if (labels.size !== frozen.files.length) throw new StateBoundProofError("frozen artifact labels are duplicated");
	if (!Array.isArray(frozen.executables)) throw new StateBoundProofError("frozen executable set is malformed");
	const executableLabels = frozen.executables.map(executable => executable?.label);
	if (executableLabels.some(label => typeof label !== "string" || !SAFE_ID.test(label)) ||
		new Set(executableLabels).size !== executableLabels.length) {
		throw new StateBoundProofError("frozen executable labels are malformed or duplicated");
	}
	const material = {
		root: frozen.root,
		rootDevice: frozen.rootDevice,
		rootInode: frozen.rootInode,
		requiredLabels: frozen.requiredLabels,
		files: frozen.files,
		executables: frozen.executables,
	};
	if (digestJson(material) !== frozen.digest) throw new StateBoundProofError("frozen artifact manifest digest is invalid");
	for (const file of frozen.files) verifyFileBinding(root, file);
	for (const executable of frozen.executables) verifyFrozenExecutable(root, executable);
}

function requireStringArray(value: unknown, label: string): string[] {
	if (!Array.isArray(value) || !value.every(item => typeof item === "string" && item)) {
		throw new StateBoundProofError(`${label} must be a non-empty-string array`);
	}
	if (new Set(value).size !== value.length) throw new StateBoundProofError(`${label} contains duplicates`);
	return [...value];
}

function requireCounter(value: unknown, label: string): number {
	if (!Number.isInteger(value) || (value as number) < 0) throw new StateBoundProofError(`${label} must be a non-negative integer`);
	return value as number;
}

export function bindSuccessfulTerminal(
	state: JsonObject,
	successTerminal: string,
	maxModelCalls: number,
): TerminalBinding {
	requireSafeId(successTerminal, "success terminal");
	if (state.status !== "success" || state.terminal !== successTerminal) {
		throw new StateBoundProofError(`controller is not at expected success terminal ${successTerminal}`);
	}
	const completed = requireStringArray(state.completed, "completed nodes").sort();
	if (!completed.includes(successTerminal)) throw new StateBoundProofError("success terminal is not completed");
	const frontier = requireStringArray(state.frontier, "frontier");
	const pending = requireStringArray(state.pending, "pending");
	if (frontier.length !== 0 || pending.length !== 0 || state.inFlight !== "") {
		throw new StateBoundProofError("success terminal still has runnable, pending, or in-flight work");
	}
	const modelCalls = requireCounter(state.modelCalls, "modelCalls");
	const schedulerCalls = requireCounter(state.schedulerCalls, "schedulerCalls");
	const nodeCalls = requireCounter(state.nodeCalls, "nodeCalls");
	if (!Number.isInteger(maxModelCalls) || maxModelCalls < 0 || modelCalls > maxModelCalls) {
		throw new StateBoundProofError(`model-call budget is invalid or exceeded (${modelCalls}/${maxModelCalls})`);
	}
	if (schedulerCalls + nodeCalls !== modelCalls) {
		throw new StateBoundProofError("success model-call accounting is not fully reconciled");
	}
	const material = {
		status: "success" as const,
		terminal: successTerminal,
		completed,
		frontier: [] as [],
		pending: [] as [],
		inFlight: "" as const,
		modelCalls,
		schedulerCalls,
		nodeCalls,
		maxModelCalls,
	};
	return { ...material, digest: digestJson(material) };
}

function parseJsonLines(bytes: Buffer, label: string): JsonObject[] {
	const lines = bytes.toString("utf8").split(/\r?\n/u).filter(line => line.trim());
	if (lines.length === 0) throw new StateBoundProofError(`${label} is empty`);
	return lines.map((line, index) => {
		let value: unknown;
		try {
			value = JSON.parse(line);
		} catch (error) {
			throw new StateBoundProofError(`${label} line ${index + 1} is malformed`, error);
		}
		if (!isPlainObject(value)) throw new StateBoundProofError(`${label} line ${index + 1} is not an object`);
		return value;
	});
}

function normalizeReviewRecord(value: JsonObject, previousActivation: number): MachineReviewRecord {
	const activation = requireCounter(value.activation, "review activation");
	if (activation <= previousActivation) throw new StateBoundProofError("machine review activations are not strictly increasing");
	const call = requireCounter(value.call, "machine review native call");
	if (call < 1) throw new StateBoundProofError("machine review native call must be positive");
	const nodeId = typeof value.nodeId === "string" ? value.nodeId : "";
	const verdict = typeof value.verdict === "string" ? value.verdict : "";
	const promptPath = typeof value.promptPath === "string" ? value.promptPath : "";
	const promptSha256 = value.promptSha256;
	const promptCommitmentDigest = value.promptCommitmentDigest;
	const reviewedStateDigest = value.reviewedStateDigest;
	const reviewedInputsDigest = value.reviewedInputsDigest;
	const evidenceDigest = value.evidenceDigest;
	const nativeTracePath = typeof value.nativeTracePath === "string" ? value.nativeTracePath : "";
	const traceSha256 = value.traceSha256;
	const responsePath = typeof value.responsePath === "string" ? value.responsePath : "";
	const responseSha256 = value.responseSha256;
	const receiptPath = typeof value.receiptPath === "string" ? value.receiptPath : "";
	const receiptSha256 = value.receiptSha256;
	if (!nodeId || !verdict || !promptPath || !nativeTracePath || !responsePath || !receiptPath) {
		throw new StateBoundProofError("machine review record is incomplete");
	}
	requireDigest(promptSha256, "machine review prompt digest");
	requireDigest(promptCommitmentDigest, "machine review prompt-commitment digest");
	requireDigest(reviewedStateDigest, "machine review reviewed-state digest");
	requireDigest(reviewedInputsDigest, "machine review reviewed-inputs digest");
	requireDigest(evidenceDigest, "machine review evidence digest");
	requireDigest(traceSha256, "machine review native-trace digest");
	requireDigest(responseSha256, "machine review response digest");
	requireDigest(receiptSha256, "machine review receipt digest");
	return {
		...value,
		activation,
		call,
		nodeId,
		verdict,
		promptPath,
		promptSha256,
		promptCommitmentDigest,
		reviewedStateDigest,
		reviewedInputsDigest,
		evidenceDigest,
		nativeTracePath,
		traceSha256,
		responsePath,
		responseSha256,
		receiptPath,
		receiptSha256,
	} as MachineReviewRecord;
}

function finalResponseObject(bytes: Buffer): JsonObject {
	const lines = bytes.toString("utf8").split(/\r?\n/u).map(line => line.trim()).filter(Boolean);
	for (let index = lines.length - 1; index >= 0; index -= 1) {
		try {
			const value: unknown = JSON.parse(lines[index]);
			if (isPlainObject(value)) return value;
		} catch {
			// Native CLIs may emit prose before the final machine object.
		}
	}
	throw new StateBoundProofError("latest machine review response has no final JSON object");
}

function promptCommitmentObject(bytes: Buffer): JsonObject {
	const text = bytes.toString("utf8");
	const matches = [...text.matchAll(/<workflow-review-commitment>([^\n]*)<\/workflow-review-commitment>/gu)];
	if (matches.length !== 1) {
		throw new StateBoundProofError("latest machine review prompt must contain exactly one review commitment");
	}
	let value: unknown;
	try {
		value = JSON.parse(matches[0][1]);
	} catch (error) {
		throw new StateBoundProofError("latest machine review prompt commitment is malformed JSON", error);
	}
	if (!isPlainObject(value) || value.schema !== "subagent-review-prompt-commitment/v1") {
		throw new StateBoundProofError("latest machine review prompt commitment schema is unsupported");
	}
	return value;
}

export function captureLatestMachineReview(
	runRootValue: string,
	ledgerPath: string,
	expectedFinalReviewNode: string,
): MachineReviewBinding {
	const runRoot = normalizedRoot(runRootValue, "run root");
	const ledger = bindFile(runRoot, ledgerPath, "machine-review-ledger");
	const records = parseJsonLines(readRegularNoFollow(runRoot, ledger.relativePath).bytes, "machine review ledger");
	let previous = -1;
	const normalized = records.map(record => {
		const item = normalizeReviewRecord(record, previous);
		previous = item.activation;
		return item;
	});
	const latest = normalized.at(-1)!;
	if (latest.nodeId !== expectedFinalReviewNode) {
		throw new StateBoundProofError(`latest machine review is ${latest.nodeId}, expected ${expectedFinalReviewNode}`);
	}
	if (!ALLOWED_FINAL_VERDICTS.has(latest.verdict)) {
		throw new StateBoundProofError(`latest machine review verdict ${latest.verdict} is not an explicit success verdict`);
	}
	if (path.normalize(latest.nativeTracePath) !== "native-calls.jsonl") {
		throw new StateBoundProofError("machine review must bind the canonical final native-calls.jsonl trace");
	}
	const nativeTrace = bindFile(runRoot, latest.nativeTracePath, "final-native-call-trace");
	if (nativeTrace.sha256 !== latest.traceSha256) throw new StateBoundProofError("final native-call trace digest is stale");
	const traceRecords = parseJsonLines(
		readRegularNoFollow(runRoot, nativeTrace.relativePath).bytes,
		"final native-call trace",
	);
	let traceCall: JsonObject | undefined;
	for (let index = 0; index < traceRecords.length; index += 1) {
		const record = traceRecords[index];
		if (record.call !== index + 1) throw new StateBoundProofError("final native-call trace has non-contiguous call numbers");
		if (record.call === latest.call) traceCall = record;
	}
	if (!traceCall) throw new StateBoundProofError(`final native-call trace has no review call ${latest.call}`);
	if (traceCall.promptSha256 !== latest.promptSha256) {
		throw new StateBoundProofError("native trace prompt digest disagrees with the machine review input");
	}
	if (traceCall.promptCommitmentDigest !== latest.promptCommitmentDigest) {
		throw new StateBoundProofError("native trace prompt-commitment digest disagrees with the machine review input");
	}
	if (
		traceCall.reviewedStateDigest !== latest.reviewedStateDigest ||
		traceCall.reviewedInputsDigest !== latest.reviewedInputsDigest ||
		traceCall.evidenceDigest !== latest.evidenceDigest
	) {
		throw new StateBoundProofError("native trace review-input digests disagree with the machine review ledger");
	}
	if (typeof traceCall.model !== "string" || !traceCall.model) {
		throw new StateBoundProofError("native trace review model is malformed");
	}
	if (traceCall.effort !== null && typeof traceCall.effort !== "string") {
		throw new StateBoundProofError("native trace review effort is malformed");
	}
	const prompt = bindFile(runRoot, latest.promptPath, "latest-machine-review-prompt");
	if (prompt.sha256 !== latest.promptSha256) {
		throw new StateBoundProofError("latest machine review prompt bytes disagree with its digest");
	}
	const commitment = promptCommitmentObject(readRegularNoFollow(runRoot, prompt.relativePath).bytes);
	if (digestJson(commitment) !== latest.promptCommitmentDigest || commitment.nodeId !== latest.nodeId ||
		commitment.reviewedStateDigest !== latest.reviewedStateDigest ||
		commitment.reviewedInputsDigest !== latest.reviewedInputsDigest ||
		commitment.evidenceDigest !== latest.evidenceDigest) {
		throw new StateBoundProofError("latest machine review prompt commitment does not bind the reviewed inputs");
	}
	const response = bindFile(runRoot, latest.responsePath, "latest-machine-review-response");
	if (response.sha256 !== latest.responseSha256) throw new StateBoundProofError("latest machine review response digest is stale");
	const responseObject = finalResponseObject(readRegularNoFollow(runRoot, response.relativePath).bytes);
	if (responseObject.verdict !== latest.verdict) {
		throw new StateBoundProofError("latest machine review ledger verdict disagrees with the bound response");
	}
	const receipt = bindFile(runRoot, latest.receiptPath, "latest-machine-review-receipt");
	if (receipt.sha256 !== latest.receiptSha256) throw new StateBoundProofError("latest machine review receipt digest is stale");
	let receiptObject: unknown;
	try {
		receiptObject = JSON.parse(readRegularNoFollow(runRoot, receipt.relativePath).bytes.toString("utf8"));
	} catch (error) {
		throw new StateBoundProofError("latest machine review receipt is not readable JSON", error);
	}
	if (!isPlainObject(receiptObject)) throw new StateBoundProofError("latest machine review receipt is not an object");
	if (
		receiptObject.exitCode !== 0 || receiptObject.terminationClass !== "COMPLETED" ||
		receiptObject.stdoutSha256 !== response.sha256 || receiptObject.call !== latest.call ||
		receiptObject.promptSha256 !== latest.promptSha256 ||
		receiptObject.promptPath !== latest.promptPath ||
		receiptObject.promptCommitmentDigest !== latest.promptCommitmentDigest ||
		receiptObject.reviewedStateDigest !== latest.reviewedStateDigest ||
		receiptObject.reviewedInputsDigest !== latest.reviewedInputsDigest ||
		receiptObject.evidenceDigest !== latest.evidenceDigest
	) {
		throw new StateBoundProofError("latest machine review receipt does not attest the completed call, prompt, review inputs, and stdout bytes");
	}
	return {
		ledger,
		nativeTrace,
		traceCalls: traceRecords.length,
		traceCall,
		prompt,
		response,
		receipt,
		latest,
		reviewDigest: digestJson(latest),
	};
}

export function verifyMachineReviewBinding(runRoot: string, expected: MachineReviewBinding): void {
	const actual = captureLatestMachineReview(runRoot, expected.ledger.relativePath, expected.latest.nodeId);
	if (canonicalJson(actual) !== canonicalJson(expected)) {
		throw new StateBoundProofError("machine review ledger, response, or latest-review digest changed");
	}
}

export function captureWorkspaceEvidence(
	runRootValue: string,
	evidencePath: string,
): WorkspaceEvidenceBinding {
	const runRoot = normalizedRoot(runRootValue, "run root");
	const evidenceFile = bindFile(runRoot, evidencePath, "final-workspace-evidence");
	let payload: unknown;
	try {
		payload = JSON.parse(readRegularNoFollow(runRoot, evidenceFile.relativePath).bytes.toString("utf8"));
	} catch (error) {
		throw new StateBoundProofError("final workspace evidence is not readable JSON", error);
	}
	if (!isPlainObject(payload) || !isPlainObject(payload.evidence)) {
		throw new StateBoundProofError("final workspace evidence has no structured evidence object");
	}
	requireDigest(payload.evidenceDigest, "workspace evidence digest");
	if (sha256(helperCanonicalJson(payload.evidence)) !== payload.evidenceDigest) {
		throw new StateBoundProofError("workspace evidence digest does not match the structured evidence bytes");
	}
	const baselineDigest = payload.evidence.baselineDigest;
	requireDigest(baselineDigest, "workspace evidence baseline digest");
	const acceptance = payload.evidence.acceptance;
	if (!isPlainObject(acceptance) || acceptance.exitCode !== 0 || acceptance.timedOut !== false) {
		throw new StateBoundProofError("final workspace evidence does not contain a successful non-timeout acceptance result");
	}
	const stateBinding = payload.evidence.stateBinding;
	if (!isPlainObject(stateBinding)) throw new StateBoundProofError("workspace evidence has no state binding");
	requireDigest(stateBinding.finalStateDigest, "final workspace state digest");
	return {
		evidenceFile,
		baselineDigest,
		evidenceDigest: payload.evidenceDigest,
		finalStateDigest: stateBinding.finalStateDigest,
		stateBinding,
	};
}

export function captureEvidenceProducer(
	runRootValue: string,
	nodeId: string,
	evidencePath: string,
): EvidenceProducerBinding {
	const runRoot = normalizedRoot(runRootValue, "run root");
	requireSafeId(nodeId, "evidence producer nodeId");
	const relativeEvidence = safeRelative(evidencePath, "evidence producer output path");
	const receipt = bindFile(runRoot, path.posix.join("command-responses", `${nodeId}.result.json`), "evidence-producer-receipt");
	const stdout = bindFile(runRoot, path.posix.join("command-responses", `${nodeId}.stdout`), "evidence-producer-stdout");
	const stderr = bindFile(runRoot, path.posix.join("command-responses", `${nodeId}.stderr`), "evidence-producer-stderr");
	let value: unknown;
	try {
		value = JSON.parse(readRegularNoFollow(runRoot, receipt.relativePath).bytes.toString("utf8"));
	} catch (error) {
		throw new StateBoundProofError("evidence producer receipt is not readable JSON", error);
	}
	if (!isPlainObject(value) || value.nodeId !== nodeId || value.exitCode !== 0 || value.terminationClass !== "COMPLETED") {
		throw new StateBoundProofError("evidence producer receipt does not attest a completed deterministic command");
	}
	const activation = requireCounter(value.activation, "evidence producer activation");
	if (activation < 1) throw new StateBoundProofError("evidence producer activation must be positive");
	requireDigest(value.argvSha256, "evidence producer argv digest");
	if (value.stdoutSha256 !== stdout.sha256 || value.stderrSha256 !== stderr.sha256) {
		throw new StateBoundProofError("evidence producer receipt does not bind stdout and stderr bytes");
	}
	if (!Array.isArray(value.runWrites)) throw new StateBoundProofError("evidence producer receipt has no runWrite evidence");
	const binding = value.runWrites.find(raw => isPlainObject(raw) && raw.path === relativeEvidence);
	if (!isPlainObject(binding) || !isDeepStrictEqual(Object.keys(binding).sort(), ["path", "sha256"]) ||
		typeof binding.sha256 !== "string") {
		throw new StateBoundProofError("evidence producer receipt does not own the final evidence path");
	}
	requireDigest(binding.sha256, "evidence producer output digest");
	const evidence = bindFile(runRoot, relativeEvidence, "evidence-producer-output");
	if (evidence.sha256 !== binding.sha256) throw new StateBoundProofError("evidence producer output bytes changed");
	const material = {
		nodeId,
		activation,
		argvSha256: value.argvSha256,
		evidencePath: relativeEvidence,
		evidenceSha256: evidence.sha256,
		stdout,
		stderr,
		receipt,
	};
	return { ...material, producerDigest: digestJson(material) };
}

export function verifyEvidenceProducer(runRoot: string, expected: EvidenceProducerBinding): void {
	const actual = captureEvidenceProducer(runRoot, expected.nodeId, expected.evidencePath);
	if (canonicalJson(actual) !== canonicalJson(expected)) {
		throw new StateBoundProofError("evidence producer command receipt or output binding changed");
	}
}

export function verifyWorkspaceEvidence(
	runRoot: string,
	expected: WorkspaceEvidenceBinding,
	currentWorkspaceBinding: JsonObject,
): void {
	const actual = captureWorkspaceEvidence(runRoot, expected.evidenceFile.relativePath);
	if (canonicalJson(actual) !== canonicalJson(expected)) {
		throw new StateBoundProofError("final workspace evidence file or binding changed");
	}
	if (canonicalJson(currentWorkspaceBinding) !== canonicalJson(expected.stateBinding)) {
		throw new StateBoundProofError("current workspace bytes no longer match the reviewed final state");
	}
}

function verifyReviewSawWorkspace(
	machineReview: MachineReviewBinding,
	workspace: WorkspaceEvidenceBinding,
): void {
	if (machineReview.latest.evidenceDigest !== workspace.evidenceDigest) {
		throw new StateBoundProofError("latest machine review did not receive the final workspace evidence digest");
	}
	if (machineReview.latest.reviewedStateDigest !== workspace.finalStateDigest) {
		throw new StateBoundProofError("latest machine review did not receive the final reviewed-state digest");
	}
}

function verifyReviewSawProofBinding(
	runRoot: string,
	machineReview: MachineReviewBinding,
	proofBinding: ProofBinding,
): void {
	const commitment = promptCommitmentObject(
		readRegularNoFollow(runRoot, machineReview.prompt.relativePath).bytes,
	);
	if (canonicalJson(commitment.proofBinding) !== canonicalJson(proofBinding)) {
		throw new StateBoundProofError("latest machine review did not receive the canonical proof binding");
	}
	if (!isPlainObject(commitment.declaredReads)) {
		throw new StateBoundProofError("latest machine review prompt commitment has malformed declared reads");
	}
	const reviewedInputs = { ...commitment.declaredReads, proofBinding };
	if (digestJson(reviewedInputs) !== machineReview.latest.reviewedInputsDigest) {
		throw new StateBoundProofError("latest machine review proof binding does not match reviewed-inputs digest");
	}
}

function verifyReviewCallAccounting(
	machineReview: MachineReviewBinding,
	terminal: TerminalBinding,
): void {
	if (machineReview.traceCalls !== terminal.modelCalls || machineReview.latest.call !== terminal.modelCalls) {
		throw new StateBoundProofError("final machine review call does not match reconciled terminal model-call accounting");
	}
}

function candidateMaterial(candidate: Omit<StateBoundCandidate, "candidateDigest">): string {
	return digestJson(candidate);
}

function candidateRelative(value = "state-bound-candidate.json"): string {
	return safeRelative(value, "candidate path");
}

function requireCanonicalRunRoot(projectRoot: string, runRoot: string, designId: string, runId: string): void {
	const expected = path.join(projectRoot, "workflow-output", "subagent", designId, runId);
	if (runRoot !== expected) {
		throw new StateBoundProofError(
			`run root is not the canonical in-project path for ${designId}/${runId}`,
		);
	}
}

function validateTerminalBinding(binding: TerminalBinding): void {
	const reconstructed = bindSuccessfulTerminal(
		{
			status: binding.status,
			terminal: binding.terminal,
			completed: binding.completed,
			frontier: binding.frontier,
			pending: binding.pending,
			inFlight: binding.inFlight,
			modelCalls: binding.modelCalls,
			schedulerCalls: binding.schedulerCalls,
			nodeCalls: binding.nodeCalls,
		},
		binding.terminal,
		binding.maxModelCalls,
	);
	if (canonicalJson(reconstructed) !== canonicalJson(binding)) throw new StateBoundProofError("terminal binding digest is invalid");
}

export function createStateBoundCandidate(options: CreateCandidateOptions): StateBoundCandidate {
	const projectRoot = normalizedRoot(options.projectRoot, "project root");
	const runRoot = normalizedRoot(options.runRoot, "run root");
	requireSafeId(options.itemId, "itemId");
	requireSafeId(options.runId, "runId");
	requireSafeId(options.designId, "designId");
	if (options.proofBinding.itemId !== options.itemId) throw new StateBoundProofError("proof binding item identity disagrees with candidate");
	requireCanonicalRunRoot(projectRoot, runRoot, options.designId, options.runId);
	if (options.frozen.root !== projectRoot) throw new StateBoundProofError("frozen artifacts belong to another project root");
	verifyFrozenProofArtifacts(options.frozen);
	verifyProofBinding(projectRoot, options.proofBinding, options.proofBinding.roleModelPolicyDigest);
	const roles = options.frozen.files.find(file => file.label === "roles");
	if (!roles || roles.sha256 !== options.proofBinding.roleModelPolicyDigest) {
		throw new StateBoundProofError("proof binding role/model policy digest is not the frozen roles artifact");
	}
	validateTerminalBinding(options.terminal);
	verifyMachineReviewBinding(runRoot, options.machineReview);
	verifyWorkspaceEvidence(runRoot, options.workspace, options.currentWorkspaceBinding);
	verifyEvidenceProducer(runRoot, options.evidenceProducer);
	if (options.evidenceProducer.evidencePath !== options.workspace.evidenceFile.relativePath ||
		options.evidenceProducer.evidenceSha256 !== options.workspace.evidenceFile.sha256) {
		throw new StateBoundProofError("deterministic evidence producer does not bind the final workspace evidence bytes");
	}
	verifyReviewSawWorkspace(options.machineReview, options.workspace);
	verifyReviewSawProofBinding(runRoot, options.machineReview, options.proofBinding);
	verifyReviewCallAccounting(options.machineReview, options.terminal);
	const runStat = plainDirectory(runRoot, "run root");
	const createdAt = options.now ?? new Date().toISOString();
	if (!Number.isFinite(Date.parse(createdAt))) throw new StateBoundProofError("candidate timestamp is invalid");
	const material: Omit<StateBoundCandidate, "candidateDigest"> = {
		schema: CANDIDATE_SCHEMA,
		projectRoot,
		runRoot,
		runDevice: runStat.dev,
		runInode: runStat.ino,
		itemId: options.itemId,
		runId: options.runId,
		designId: options.designId,
		proofBinding: options.proofBinding,
		frozen: options.frozen,
		terminal: options.terminal,
		machineReview: options.machineReview,
		workspace: options.workspace,
		evidenceProducer: options.evidenceProducer,
		authorization: {
			mode: AUTHORIZATION_MODE,
			source: AUTHORIZATION_SOURCE,
			finalizer: "controller-only",
		},
		createdAt,
		threatBoundary: STATE_BOUND_PROOF_THREAT_BOUNDARY,
	};
	return { ...material, candidateDigest: candidateMaterial(material) };
}

export function writeStateBoundCandidate(
	runRootValue: string,
	candidate: StateBoundCandidate,
	relativePath = "state-bound-candidate.json",
): { path: string; created: boolean; idempotent: boolean } {
	const runRoot = normalizedRoot(runRootValue, "run root");
	if (candidate.runRoot !== runRoot) throw new StateBoundProofError("candidate belongs to another run root");
	validateCandidate(candidate, candidate.projectRoot, runRoot);
	const relative = candidateRelative(relativePath);
	const outcome = atomicWriteNoFollow(runRoot, relative, Buffer.from(`${canonicalJson(candidate)}\n`, "utf8"));
	return { path: path.join(runRoot, relative), ...outcome };
}

function validateCandidate(value: unknown, projectRootValue: string, runRootValue: string): asserts value is StateBoundCandidate {
	if (!isPlainObject(value) || value.schema !== CANDIDATE_SCHEMA) throw new StateBoundProofError("candidate schema is unsupported");
	const candidate = value as unknown as StateBoundCandidate;
	const projectRoot = normalizedRoot(projectRootValue, "project root");
	const runRoot = normalizedRoot(runRootValue, "run root");
	if (candidate.projectRoot !== projectRoot || candidate.runRoot !== runRoot) {
		throw new StateBoundProofError("candidate root binding does not match this run");
	}
	requireSafeId(candidate.itemId, "candidate itemId");
	requireSafeId(candidate.runId, "candidate runId");
	requireSafeId(candidate.designId, "candidate designId");
	if (candidate.proofBinding?.itemId !== candidate.itemId) throw new StateBoundProofError("candidate proof binding item identity changed");
	verifyProofBinding(projectRoot, candidate.proofBinding, candidate.proofBinding.roleModelPolicyDigest);
	const roles = candidate.frozen?.files?.find(file => file.label === "roles");
	if (!roles || roles.sha256 !== candidate.proofBinding.roleModelPolicyDigest) {
		throw new StateBoundProofError("candidate proof binding role/model policy digest is not the frozen roles artifact");
	}
	requireCanonicalRunRoot(projectRoot, runRoot, candidate.designId, candidate.runId);
	requireDigest(candidate.candidateDigest, "candidate digest");
	const { candidateDigest, ...material } = candidate;
	if (candidateMaterial(material) !== candidateDigest) throw new StateBoundProofError("candidate digest is invalid");
	if (candidate.threatBoundary !== STATE_BOUND_PROOF_THREAT_BOUNDARY) throw new StateBoundProofError("candidate threat boundary changed");
	if (
		candidate.authorization?.mode !== AUTHORIZATION_MODE ||
		candidate.authorization?.source !== AUTHORIZATION_SOURCE ||
		candidate.authorization?.finalizer !== "controller-only"
	) throw new StateBoundProofError("candidate authorization mode is invalid");
	const runStat = plainDirectory(runRoot, "run root");
	if (runStat.dev !== candidate.runDevice || runStat.ino !== candidate.runInode) {
		throw new StateBoundProofError("candidate run-root identity changed");
	}
	validateTerminalBinding(candidate.terminal);
	if (!ALLOWED_FINAL_VERDICTS.has(candidate.machineReview.latest.verdict)) {
		throw new StateBoundProofError("candidate does not contain a passing latest machine review");
	}
	requireDigest(candidate.machineReview.reviewDigest, "candidate machine-review digest");
	requireDigest(candidate.workspace.baselineDigest, "candidate baseline digest");
	requireDigest(candidate.workspace.finalStateDigest, "candidate final-state digest");
	requireDigest(candidate.evidenceProducer?.producerDigest, "candidate evidence-producer digest");
	if (candidate.evidenceProducer.evidencePath !== candidate.workspace.evidenceFile.relativePath ||
		candidate.evidenceProducer.evidenceSha256 !== candidate.workspace.evidenceFile.sha256) {
		throw new StateBoundProofError("candidate evidence producer does not own the final evidence bytes");
	}
	verifyReviewSawWorkspace(candidate.machineReview, candidate.workspace);
	verifyReviewSawProofBinding(runRoot, candidate.machineReview, candidate.proofBinding);
	verifyReviewCallAccounting(candidate.machineReview, candidate.terminal);
}

export function loadStateBoundCandidate(
	projectRoot: string,
	runRoot: string,
	relativePath = "state-bound-candidate.json",
): StateBoundCandidate {
	const normalizedRunRoot = normalizedRoot(runRoot, "run root");
	const bytes = readRegularNoFollow(normalizedRunRoot, candidateRelative(relativePath)).bytes;
	let value: unknown;
	try {
		value = JSON.parse(bytes.toString("utf8"));
	} catch (error) {
		throw new StateBoundProofError("candidate is unreadable JSON", error);
	}
	validateCandidate(value, projectRoot, normalizedRunRoot);
	return value;
}

function proofBody(candidate: StateBoundCandidate): Buffer {
	const lines = [
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
	];
	return Buffer.from(lines.join("\n"), "utf8");
}

/**
 * Idempotent recovery entrypoint.  It verifies an already-created candidate and writes
 * only the protected proof.  It never invokes a model, command, helper, or scheduler.
 */
export function finalizeStateBoundCandidateOnly(options: FinalizeOnlyOptions): FinalizeOnlyResult {
	if (!options.authority || !issuedAuthorities.has(options.authority) ||
		options.authority.mode !== AUTHORIZATION_MODE || options.authority.source !== AUTHORIZATION_SOURCE) {
		throw new StateBoundProofError("a module-issued controller tier-1 authority token is required for protected proof finalization");
	}
	const projectRoot = normalizedRoot(options.projectRoot, "project root");
	const runRoot = normalizedRoot(options.runRoot, "run root");
	const candidate = loadStateBoundCandidate(projectRoot, runRoot, options.candidatePath);
	if (candidate.itemId !== options.itemId) throw new StateBoundProofError("requested itemId does not match candidate");
	verifyProofBinding(projectRoot, candidate.proofBinding, options.roleModelPolicyDigest);
	verifyFrozenProofArtifacts(candidate.frozen);
	validateTerminalBinding(candidate.terminal);
	verifyMachineReviewBinding(runRoot, candidate.machineReview);
	verifyWorkspaceEvidence(runRoot, candidate.workspace, options.currentWorkspaceBinding);
	verifyEvidenceProducer(runRoot, candidate.evidenceProducer);
	verifyReviewSawWorkspace(candidate.machineReview, candidate.workspace);
	verifyReviewSawProofBinding(runRoot, candidate.machineReview, candidate.proofBinding);
	verifyReviewCallAccounting(candidate.machineReview, candidate.terminal);
	const proofRelative = canonicalMachineWorkflowProofRelative(candidate.itemId);
	const outcome = atomicWriteNoFollow(projectRoot, proofRelative, proofBody(candidate));
	return {
		proofPath: path.join(projectRoot, safeRelative(proofRelative, "proof path")),
		created: outcome.created,
		idempotent: outcome.idempotent,
		candidateDigest: candidate.candidateDigest,
		modelCallsReplayed: 0,
	};
}

/**
 * Issue an in-process, non-serializable authority token after checking the controller's
 * tier-1 environment.  The token is never placed in a child environment or candidate.
 */
export function controllerFinalizeAuthorityFromEnvironment(): ControllerFinalizeAuthority {
	if (process.env.GENERAL_AGENT_ALLOW_PROTECTED_WRITE !== "1") {
		throw new StateBoundProofError(`protected finalization requires ${AUTHORIZATION_SOURCE}`);
	}
	const authority = Object.freeze({ mode: AUTHORIZATION_MODE, source: AUTHORIZATION_SOURCE });
	issuedAuthorities.add(authority);
	return authority;
}

const DETERMINISTIC_COMMAND_ENV_ALLOWLIST = new Set([
	"PATH",
	"HOME",
	"TMPDIR",
	"TMP",
	"TEMP",
	"LANG",
	"LANGUAGE",
	"LC_ALL",
	"LC_COLLATE",
	"LC_CTYPE",
	"LC_MESSAGES",
	"LC_MONETARY",
	"LC_NUMERIC",
	"LC_TIME",
	"LC_PAPER",
	"LC_NAME",
	"LC_ADDRESS",
	"LC_TELEPHONE",
	"LC_MEASUREMENT",
	"LC_IDENTIFICATION",
	"TZ",
	"USER",
	"LOGNAME",
	"TERM",
	"COLORTERM",
	"NO_COLOR",
	"CI",
]);

/**
 * Construct the minimal ambient environment for deterministic command/action
 * processes.  Everything not explicitly required for ordinary process startup
 * is absent, including controller authority, API credentials, askpass/agent
 * sockets, and shell/runtime pre-main injection variables.
 *
 * This is deliberately command-specific.  Native model dispatch does not use
 * this function; it builds and scrubs its own authenticated child environment.
 */
export function unprivilegedChildEnvironment(base: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
	const out: NodeJS.ProcessEnv = {};
	for (const [key, value] of Object.entries(base)) {
		if (value === undefined) continue;
		if (DETERMINISTIC_COMMAND_ENV_ALLOWLIST.has(key)) out[key] = value;
	}
	return out;
}
