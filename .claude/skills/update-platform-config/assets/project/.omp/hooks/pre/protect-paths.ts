/**
 * protect-paths — the path-protection gate (port of hook_gate.py's protected-path check
 * + the constraint_gate successor).
 *
 * Layers:
 *   1. FRAMEWORK list: authoritative proof dirs, gates/flows, and control-plane
 *      source paths that require an explicit acknowledgement before direct edits.
 *   2. PROJECT list: .omp/hooks/protected-paths.json (constraint-derived during default-mode startup).
 *
 * Fires on the normalized input.path / input.paths (WRITE-family tools only — read/grep/glob are
 * never path-blocked; protecting a file must not stop reading it) and on bash commands (where
 * strict read/execute-only shapes like built-in validation are exempt).
 * Canonicalizes candidates first (collapse //, resolve ./ and ../, strip quotes/backslashes on
 * the bash surface) so quote/slash/dot evasion of protected paths
 * still matches. Native harnesses receive the corresponding hook/audit through platform sync or
 * the workflow runner.
 */
import * as fs from "node:fs";
import * as path from "node:path";

const CONTROL_PLANE_FILES = [
	".claude/hooks/protect_paths.py",
	".claude/skills/run-workflow/scripts/select_harness.py",
	".workflow/harnesses.json",
	"config/workflows/models.json",
	".claude/skills/subagent-workflow/scripts/native/native-map.json",
	".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py",
	".claude/skills/subagent-workflow/scripts/native/run.ts",
	".claude/skills/subagent-workflow/scripts/controller/run.ts",
	".claude/skills/subagent-workflow/scripts/controller/state_bound_proof.ts",
	".claude/skills/pm-dispatch/scripts/pm.py",
	"constraint.md",
];
const FRAMEWORK_PROTECTED = [
	".pm/reviews/",
	".pm/verdicts/",
	".pm/proofs/",
	".pm/workflow-reviews/", // pm.py's machine-gated workflow proof, disjoint from .pm/reviews/
	".omp/hooks/", // the gates protect themselves
	".workflow/designs/", // neutral workflow semantics are control-plane
	".subagent/flows/", // built-in adapter mappings are executable control-plane
	".claude/skills/subagent-workflow/scripts/controller/actions/", // deterministic workflow actions are frozen proof inputs
	...CONTROL_PLANE_FILES,
];
// NOTE: `.pm` is deliberately NOT a root. `.pm/` is a shared WORKING directory — the workflow's
// own signal files are created with bash (`touch .pm/cold_start_done`, `touch .pm/awaiting_human`,
// `printf … > .pm/round_input.json`), and blocking every command that merely mentions `.pm` made
// those files impossible to create, silently breaking startup completion, human-wait status,
// Reviews, verdicts, and workflow proofs are authoritative terminal artifacts. Direct path writes
// require a scoped acknowledgement, and a state-based post-bash audit reverts shell-obfuscated
// forgeries. Evidence and review briefs remain writable inputs and are commitment-checked later.
const PROOF_DIRS = [".pm/verdicts", ".pm/reviews", ".pm/proofs", ".pm/workflow-reviews"];

// ── Scoped acknowledgment override — protected files remain deliberately writable ──
// A file that NO agent session can ever write is dangerous: you end up "protecting" a file that never
// gets created. GENERAL_AGENT_ALLOW_PROTECTED_WRITE=1 is therefore a one-command acknowledgement
// used by reviewer/workflow writers. It is read only from the leading command assignment; a mere
// mention elsewhere in the command does not authorize anything.
const ACK_VAR = "GENERAL_AGENT_ALLOW_PROTECTED_WRITE";

/** Parse the LEADING env-assignment prefix of a shell command (`[env] VAR=val VAR2=val2 cmd …`).
 *  Handles single/double-quoted values (which may contain spaces) and bare values. */
function leadingAssignments(cmd: string): Record<string, string> {
	const out: Record<string, string> = {};
	let rest = cmd.replace(/^\s+/, "").replace(/^env\s+/, "");
	const re = /^([A-Za-z_][A-Za-z0-9_]*)=(?:'([^']*)'|"([^"]*)"|([^'"\s;|&]*))\s+/;
	for (;;) {
		const m = rest.match(re);
		if (!m) break;
		out[m[1]] = m[2] ?? m[3] ?? m[4] ?? "";
		rest = rest.slice(m[0].length);
	}
	return out;
}
/** An AUTH grant must be a truthy LITERAL. A value containing shell expansion ($, backtick) is NOT
 *  a grant — `VAR=$(printf 0)` would lex here as the string "$(printf 0)" (truthy) while the shell
 *  resolves it to 0; refusing expansions entirely fails CLOSED on that ambiguity. */
function truthyLiteral(v: string | undefined): boolean {
	if (v === undefined) return false;
	if (v.includes("$") || v.includes("`")) return false; // expansion — never a grant
	const s = v.toLowerCase();
	return s !== "" && s !== "0" && s !== "false" && s !== "no";
}
function ackAuthorized(cmd: string): boolean {
	return truthyLiteral(leadingAssignments(cmd)[ACK_VAR]);
}
let preBashProof: Map<string, Buffer> | null = null;

class UnsafeProofPath extends Error {}

function errnoCode(error: unknown): string | undefined {
	return (error as NodeJS.ErrnoException).code;
}

function proofParts(relative: string): string[] {
	const normalized = relative.replace(/\\/g, "/");
	const parts = normalized.split("/");
	if (!normalized || normalized.startsWith("/") || parts.some((part) => ["", ".", ".."].includes(part))) {
		throw new UnsafeProofPath(`unsafe proof-relative path: ${JSON.stringify(relative)}`);
	}
	if (!PROOF_DIRS.some((root) => normalized.startsWith(root + "/"))) {
		throw new UnsafeProofPath(`path is outside authoritative proof roots: ${JSON.stringify(relative)}`);
	}
	return parts;
}

function safeDirectory(cwd: string, relative: string, create: boolean): string | null {
	let current = path.resolve(cwd);
	let root: fs.Stats;
	try {
		root = fs.lstatSync(current);
	} catch (error) {
		throw new UnsafeProofPath(`cannot lstat repository root: ${(error as Error).message}`);
	}
	if (root.isSymbolicLink() || !root.isDirectory()) {
		throw new UnsafeProofPath("repository root is not one ordinary directory");
	}
	const traversed: string[] = [];
	for (const component of relative.replace(/\\/g, "/").split("/")) {
		if (!component) continue;
		if ([".", ".."].includes(component)) {
			throw new UnsafeProofPath(`unsafe proof directory component: ${JSON.stringify(component)}`);
		}
		traversed.push(component);
		current = path.join(current, component);
		let metadata: fs.Stats;
		try {
			metadata = fs.lstatSync(current);
		} catch (error) {
			if (errnoCode(error) !== "ENOENT") {
				throw new UnsafeProofPath(
					`cannot lstat proof directory ${traversed.join("/")}: ${(error as Error).message}`,
				);
			}
			if (!create) return null;
			try {
				fs.mkdirSync(current, { mode: 0o700 });
				metadata = fs.lstatSync(current);
			} catch (mkdirError) {
				throw new UnsafeProofPath(
					`cannot safely create proof directory ${traversed.join("/")}: ${(mkdirError as Error).message}`,
				);
			}
		}
		if (metadata.isSymbolicLink() || !metadata.isDirectory()) {
			throw new UnsafeProofPath(`proof directory ${traversed.join("/")} is a symlink or non-directory`);
		}
	}
	return current;
}

function sameIdentity(left: fs.Stats, right: fs.Stats): boolean {
	return left.dev === right.dev && left.ino === right.ino;
}

function readProofFileNoFollow(absolute: string, expected: fs.Stats, relative: string): Buffer {
	const noFollow = fs.constants.O_NOFOLLOW ?? 0;
	let descriptor: number;
	try {
		descriptor = fs.openSync(absolute, fs.constants.O_RDONLY | noFollow);
	} catch (error) {
		throw new UnsafeProofPath(
			`cannot open proof file ${relative} without following links: ${(error as Error).message}`,
		);
	}
	try {
		const opened = fs.fstatSync(descriptor);
		if (!opened.isFile() || !sameIdentity(opened, expected)) {
			throw new UnsafeProofPath(`proof file ${relative} changed while opening`);
		}
		const content = fs.readFileSync(descriptor);
		const after = fs.lstatSync(absolute);
		if (
			!sameIdentity(after, expected) ||
			after.mode !== expected.mode ||
			after.size !== expected.size ||
			after.mtimeMs !== expected.mtimeMs
		) {
			throw new UnsafeProofPath(`proof file ${relative} changed while reading`);
		}
		return content;
	} finally {
		fs.closeSync(descriptor);
	}
}

function snapshotProof(cwd: string): Map<string, Buffer> {
	const snap = new Map<string, Buffer>();
	function walk(relative: string): void {
		const directory = safeDirectory(cwd, relative, false);
		if (directory === null) return;
		let names: string[];
		try {
			names = fs.readdirSync(directory).sort();
		} catch (error) {
			throw new UnsafeProofPath(`cannot inspect proof directory ${relative}: ${(error as Error).message}`);
		}
		for (const name of names) {
			const child = relative + "/" + name;
			const absolute = path.join(directory, name);
			let metadata: fs.Stats;
			try {
				metadata = fs.lstatSync(absolute);
			} catch (error) {
				throw new UnsafeProofPath(`cannot lstat proof path ${child}: ${(error as Error).message}`);
			}
			if (metadata.isSymbolicLink()) throw new UnsafeProofPath(`proof path ${child} is a symbolic link`);
			if (metadata.isDirectory()) walk(child);
			else if (metadata.isFile()) snap.set(child, readProofFileNoFollow(absolute, metadata, child));
			else throw new UnsafeProofPath(`proof path ${child} is not a regular file`);
		}
	}
	for (const dir of PROOF_DIRS) walk(dir);
	return snap;
}

let restoreCounter = 0;

function safeAtomicRestore(cwd: string, relative: string, content: Buffer): void {
	const parts = proofParts(relative);
	const parentRelative = parts.slice(0, -1).join("/");
	const parent = safeDirectory(cwd, parentRelative, true);
	if (parent === null) throw new UnsafeProofPath(`cannot open proof parent for ${relative}`);
	const parentBefore = fs.lstatSync(parent);
	const destination = path.join(parent, parts.at(-1)!);
	let current: fs.Stats | null;
	try {
		current = fs.lstatSync(destination);
	} catch (error) {
		if (errnoCode(error) !== "ENOENT") throw error;
		current = null;
	}
	if (current && !(current.isFile() || current.isSymbolicLink())) {
		throw new UnsafeProofPath(`proof leaf ${relative} is not replaceable as one file`);
	}
	const temporary = path.join(
		parent,
		`.${path.basename(destination)}.ga-restore-${process.pid}-${Date.now()}-${restoreCounter++}`,
	);
	let temporaryCreated = false;
	let cleanupIsSafe = true;
	try {
		const noFollow = fs.constants.O_NOFOLLOW ?? 0;
		const descriptor = fs.openSync(
			temporary,
			fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | noFollow,
			0o600,
		);
		temporaryCreated = true;
		try {
			fs.writeFileSync(descriptor, content);
			fs.fsyncSync(descriptor);
		} finally {
			fs.closeSync(descriptor);
		}
		const checkedParent = safeDirectory(cwd, parentRelative, false);
		if (checkedParent === null || checkedParent !== parent) {
			cleanupIsSafe = false;
			throw new UnsafeProofPath(`proof parent for ${relative} changed before replacement`);
		}
		const parentAfter = fs.lstatSync(checkedParent);
		if (!sameIdentity(parentBefore, parentAfter)) {
			cleanupIsSafe = false;
			throw new UnsafeProofPath(`proof parent for ${relative} changed before replacement`);
		}
		fs.renameSync(temporary, destination);
		temporaryCreated = false;
		try {
			const parentDescriptor = fs.openSync(parent, fs.constants.O_RDONLY | (fs.constants.O_DIRECTORY ?? 0));
			try {
				fs.fsyncSync(parentDescriptor);
			} finally {
				fs.closeSync(parentDescriptor);
			}
		} catch {
			// Replacement is already complete on platforms without directory fsync support.
		}
	} finally {
		if (temporaryCreated && cleanupIsSafe) {
			try {
				const temporaryMetadata = fs.lstatSync(temporary);
				if (temporaryMetadata.isFile()) fs.unlinkSync(temporary);
			} catch (error) {
				if (errnoCode(error) !== "ENOENT") throw error;
			}
		}
	}
}

function safeRemoveProofFile(cwd: string, relative: string): void {
	const parts = proofParts(relative);
	const parent = safeDirectory(cwd, parts.slice(0, -1).join("/"), false);
	if (parent === null) return;
	const destination = path.join(parent, parts.at(-1)!);
	let metadata: fs.Stats;
	try {
		metadata = fs.lstatSync(destination);
	} catch (error) {
		if (errnoCode(error) === "ENOENT") return;
		throw error;
	}
	if (!(metadata.isFile() || metadata.isSymbolicLink())) {
		throw new UnsafeProofPath(`forged proof leaf ${relative} is not one removable file`);
	}
	fs.unlinkSync(destination);
}

class ProjectProtectionConfigError extends Error {}

function projectProtected(cwd: string): string[] {
	const cfg = path.join(cwd, ".omp", "hooks", "protected-paths.json");
	let lst: fs.Stats;
	try {
		lst = fs.lstatSync(cfg);
	} catch (error) {
		if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
		throw new ProjectProtectionConfigError(
			`cannot inspect project protection config ${cfg}: ${(error as Error).message}`,
		);
	}
	try {
		if (lst.isSymbolicLink() || !lst.isFile()) throw new Error("config must be one ordinary file");
		const raw = JSON.parse(fs.readFileSync(cfg, "utf8"));
		if (!Array.isArray(raw)) throw new Error("config must be a JSON array");
		const list: string[] = [];
		for (const [index, value] of raw.entries()) {
			if (typeof value !== "string" || !value.trim() || /[\0\n\r]/.test(value)) {
				throw new Error(`entry ${index} must be one non-empty string`);
			}
			const normalized = value.replace(/\\/g, "/");
			if (normalized.startsWith("/") || normalized.split("/").includes("..")) {
				throw new Error(`entry ${index} must stay inside the repository`);
			}
			if ([".", "./"].includes(normalized) || /[*?[]/.test(normalized)) {
				throw new Error(`entry ${index} must be one compiled non-root prefix`);
			}
			list.push(value);
		}
		return list;
	} catch (error) {
		throw new ProjectProtectionConfigError(
			`malformed project protection config ${cfg}: ${(error as Error).message}`,
		);
	}
}

/** Canonicalize a path: forward slashes, collapse //, resolve . and .. segments, strip leading ./ */
function canonicalize(raw: string): string {
	let p = raw.replace(/\\/g, "/");
	const isAbs = p.startsWith("/");
	const out: string[] = [];
	for (const seg of p.split("/")) {
		if (seg === "" || seg === ".") continue;
		if (seg === "..") {
			if (out.length && out[out.length - 1] !== "..") out.pop();
			else if (!isAbs) out.push("..");
			continue;
		}
		out.push(seg);
	}
	return (isAbs ? "/" : "") + out.join("/");
}

function hitsList(candidate: string, prot: string[]): string | null {
	const c = canonicalize(candidate);
	for (const entry of prot) {
		const e = canonicalize(entry);
		// directory entry (trailing slash originally) → prefix match on a path boundary
		const dir = entry.endsWith("/");
		if (c === e) return entry;
		if (dir && (c === e || c.startsWith(e + "/"))) return entry;
		if (!dir && (c === e || c.startsWith(e + "/"))) return entry;
		// also catch a nested occurrence of a protected path
		if (e.includes("/") && (c.includes("/" + e + "/") || c.endsWith("/" + e))) return entry;
	}
	return null;
}

/** READ/EXECUTE-ONLY command shapes may MENTION protected paths freely — protecting a flow must
 *  not block validating it. Without this, built-in mapping validation and ordinary read-only
 *  inspection were blocked for merely naming the path, pushing agents into
 *  acknowledgment-laundering workarounds.
 *  Safety: the exemption applies ONLY when (a) the WHOLE command is free of shell metacharacters
 *  AND newlines that could smuggle a write or a second command (; & | < > ` $ ( ) # \n \r \0),
 *  and (b) it STARTS with an allowlisted read/execute-only program — NO env-assignment prefixes
 *  (LD_PRELOAD/BASH_ENV/PYTHONPATH poisoning would turn any listed program into arbitrary code).
 *  With no redirects, pipes, chaining, substitution, or env injection possible, none of these
 *  programs can modify the mentioned path. */
// Metachars that could smuggle a second command or a write: shell operators AND newlines
// (newline chains a second command without any listed operator — Codex-found bypass).
const RO_METACHAR = /[;&|<>`$()#\n\r\0]/;
// NO env-assignment prefixes in the exemption: LD_PRELOAD/BASH_ENV/PATH/GIT_EXTERNAL_DIFF/
// PYTHONPATH poisoning turns any allowlisted program into arbitrary code (Codex-found). A prefixed
// command simply doesn't get the exemption — run it without the prefix, or use the ack.
// Program list is minimal + audited for exec/output-taking flags: rg (--pre executes) and git
// (--output writes, -c/pager execute) are deliberately ABSENT. Built-in starts/finalization are
// handled by the exact grammar below and require a literal tier-1 prefix.
const RO_CMD = new RegExp(
		"^\\s*(?:python3?\\s+(?:\\./)?\\.claude/skills/pm-dispatch/scripts/pm\\.py" +
		"|cat|grep|ls|head|tail|wc|diff|file|stat)(?:\\s|$)",
);

const SAFE_SUBAGENT_MAPPING = /^(?:\.\/)?\.subagent\/flows\/([A-Za-z0-9][A-Za-z0-9_-]*)\.json$/;
const SAFE_SUBAGENT_RESUME =
	/^workflow-output\/subagent\/[A-Za-z0-9][A-Za-z0-9_-]*\/[A-Za-z0-9][A-Za-z0-9_-]*$/;
const SAFE_PATH_SEGMENT = /^[A-Za-z0-9._-]+$/;

function safeInputPath(raw: string): boolean {
	if (!raw || raw.startsWith("/") || raw.startsWith("-") || raw.includes("\\")) return false;
	const value = raw.startsWith("./") ? raw.slice(2) : raw;
	const parts = value.split("/");
	return (
		parts.length > 0 &&
		parts.every((part) => part !== "" && part !== "." && part !== ".." && SAFE_PATH_SEGMENT.test(part))
	);
}

type SubagentInvocation = { action: "validate" | "start" | "finalize-only"; authorized: boolean };

/** Exact grammar for the built-in controller. Validate is read-only and unprefixed. Start and
 *  finalize-only may create a state-bound proof and therefore require the one literal tier-1
 *  assignment shown in the registry. Unknown env, shell syntax, options, and paths fail closed. */
function parseSubagentRunnerShape(command: string): SubagentInvocation | null {
	if (RO_METACHAR.test(command)) return null;
	const words = command.trim().split(/\s+/);
	let authorized = false;
	if (words[0] === `${ACK_VAR}=1`) {
		authorized = true;
		words.shift();
	}
	if (words.length < 4 || words[0] !== "bun") return null;
	if (![".claude/skills/subagent-workflow/scripts/controller/run.ts", "./.claude/skills/subagent-workflow/scripts/controller/run.ts"].includes(words[1])) return null;
	const action = words[2];
	if (!["validate", "start", "finalize-only"].includes(action) || !SAFE_SUBAGENT_MAPPING.test(words[3])) return null;
	if (action === "validate") return words.length === 4 && !authorized ? { action, authorized } : null;
	if (!authorized) return null;
	if (action === "finalize-only") {
		return words.length === 6 && words[4] === "--resume" && SAFE_SUBAGENT_RESUME.test(words[5])
			? { action, authorized }
			: null;
	}

	const seen = new Set<string>();
	for (let index = 4; index < words.length; index += 2) {
		const flag = words[index];
		const value = words[index + 1];
		if (!["--input", "--resume"].includes(flag) || seen.has(flag) || value === undefined) return null;
		if (flag === "--input" && !safeInputPath(value)) return null;
		if (flag === "--resume" && !SAFE_SUBAGENT_RESUME.test(value)) return null;
		seen.add(flag);
	}
	return { action: "start", authorized };
}
function isSubagentRunnerShape(command: string): boolean {
	return parseSubagentRunnerShape(command) !== null;
}
function isReadOnlyShape(cmd: string): boolean {
	return !RO_METACHAR.test(cmd) && (parseSubagentRunnerShape(cmd)?.action === "validate" || RO_CMD.test(cmd));
}

function isProjectConfigRepairShape(command: string): boolean {
	if (RO_METACHAR.test(command)) return false;
	const words = command.trim().split(/\s+/);
	return (
		words.length === 4 &&
		words[0] === `${ACK_VAR}=1` &&
		["python", "python3"].includes(words[1]) &&
		words[2] === ".claude/skills/constraints-to-hooks/scripts/constraint_gate.py" &&
		words[3] === "install-protected-paths"
	);
}

function mentionsPath(text: string, target: string): boolean {
	let offset = 0;
	while (offset <= text.length) {
		const index = text.indexOf(target, offset);
		if (index < 0) return false;
		const before = index === 0 ? "" : text[index - 1];
		const afterIndex = index + target.length;
		const after = afterIndex >= text.length ? "" : text[afterIndex];
		const leftBoundary = !before || !/[A-Za-z0-9_.-]/.test(before);
		const rightBoundary = !after || after === "/" || !/[A-Za-z0-9_.-]/.test(after);
		if (leftBoundary && rightBoundary) return true;
		offset = index + 1;
	}
	return false;
}

/** Bash surface: strip quotes/backslashes, canonicalize whitespace, then fail closed on any
 *  protected root or entry mention — EXCEPT strict read/execute-only shapes (above). Other reads
 *  of these paths via bash are collateral (use the read tool) — safety over convenience. */
function bashHit(command: string, prot: string[], cwd?: string): string | null {
	if (isReadOnlyShape(command)) return null;
	const stripped = command.replace(/['"\\]/g, "");
	// FIXPOINT collapse of /./ // and /seg/../ so repeated-dot / .. evasion of a protected path
	// (.omp/././hooks, .omp/foo/../hooks) still matches the literal scan (Codex-found).
	let canon = stripped.replace(/\s+/g, " ");
	for (let prev = ""; prev !== canon; ) {
		prev = canon;
		canon = canon.replace(/\/\.\//g, "/").replace(/\/{2,}/g, "/").replace(/\/[^/]+\/\.\.\//g, "/");
	}
	for (const entry of prot) {
		if (entry === "constraint.md") {
			if (mentionsRootPolicy(canon, cwd)) return entry;
			continue;
		}
		const e = canonicalize(entry);
		if (mentionsPath(canon, e)) return entry;
	}
	return null;
}

function mentionsRootPolicy(text: string, cwd?: string): boolean {
	if (cwd && mentionsPath(text, canonicalize(path.resolve(cwd)) + "/constraint.md")) return true;
	for (const spelling of ["constraint.md", "./constraint.md"]) {
		let offset = 0;
		while (offset <= text.length) {
			const index = text.indexOf(spelling, offset);
			if (index < 0) break;
			const before = index === 0 ? "" : text[index - 1];
			const afterIndex = index + spelling.length;
			const after = afterIndex >= text.length ? "" : text[afterIndex];
			if ((!before || !/[A-Za-z0-9_./-]/.test(before)) && (!after || !/[A-Za-z0-9_./-]/.test(after))) {
				return true;
			}
			offset = index + 1;
		}
	}
	return false;
}

export default function (pi: any) {
	pi.on("tool_call", (ev: any, ctx: any) => {
		const input = ev?.input ?? {};
		const cwd: string = ctx?.cwd ?? process.cwd();
		const toolName = String(ev?.toolName ?? "").toLowerCase();
		const readFamily = new Set(["read", "grep", "glob", "ls", "web_search"]);
		if (readFamily.has(toolName)) return undefined;
		const isBash = toolName === "bash" && typeof input.command === "string";
		let controllerInvocation: SubagentInvocation | null = null;
		if (isBash && input.command.includes(".claude/skills/subagent-workflow/scripts/controller/run.ts")) {
			const invocation = parseSubagentRunnerShape(input.command);
			if (!invocation) {
				return {
					block: true,
					reason:
						`BLOCKED by general_agent governance: malformed or unauthorized built-in workflow command. ` +
						`Use plain \`bun .claude/skills/subagent-workflow/scripts/controller/run.ts validate .subagent/flows/<name>.json\`, or prefix ` +
						`an exact start/finalize-only command with \`${ACK_VAR}=1\`.`,
				};
			}
			if (invocation.action === "validate") return undefined;
			controllerInvocation = invocation;
		}
		let prot: string[];
		try {
			prot = [...FRAMEWORK_PROTECTED, ...projectProtected(cwd)];
		} catch (error) {
			if (typeof input.command === "string" && isProjectConfigRepairShape(input.command)) {
				preBashProof = null;
				return undefined;
			}
			return {
				block: true,
				reason:
					`BLOCKED by general_agent governance: ${(error as Error).message}. ` +
					`Repair or atomically recompile .omp/hooks/protected-paths.json before any tool action.`,
			};
		}
		if (controllerInvocation !== null) return undefined;
		if (typeof input.command === "string" && ackAuthorized(input.command)) {
			preBashProof = null;
			return undefined;
		}

		// Write/Edit surface (input.path/paths). READ-family tools are NEVER path-blocked —
		// protecting a file must not stop the agent from READING it. Only write-capable
		// tools hit the gate; they carry no command line, so they can't present the inline ack —
		// the block message points at the bash+ack route.
		// ast_grep/lsp are excluded because both can apply rewrites in some configurations
		// (sg --rewrite, LSP rename) — fail closed on protected paths.
		const pathCandidates: string[] = [];
		if (typeof input.path === "string") pathCandidates.push(projectRelativeCandidate(cwd, input.path));
		if (Array.isArray(input.paths)) {
			for (const p of input.paths) if (typeof p === "string") pathCandidates.push(projectRelativeCandidate(cwd, p));
		}
		for (const c of pathCandidates) {
			const hit = hitsList(c, prot);
			if (hit) return blocked(hit, /*viaTool=*/ true);
		}
			if (typeof input.command === "string") {
				const hit = bashHit(input.command, prot, cwd);
				if (hit) return blocked(hit);
				// Shell-obfuscated authoritative proof writes are detected by the post-command snapshot.
				try {
					preBashProof = snapshotProof(cwd);
				} catch (error) {
					preBashProof = null;
					return {
						block: true,
						reason:
							`BLOCKED by general_agent governance: cannot safely snapshot authoritative proof paths: ` +
							`${(error as Error).message}.`,
					};
				}
			}
		return undefined;
	});

	// Post-command audit: restore unauthorized authoritative proof changes.
	pi.on("tool_result", (ev: any, ctx: any) => {
		if (ev?.toolName !== "bash" || !preBashProof) return undefined;
		const proofSnap = preBashProof;
		preBashProof = null;
		const cwd: string = ctx?.cwd ?? process.cwd();
		const failed: string[] = [];
		const forged: string[] = [];
			let after: Map<string, Buffer>;
			try {
				after = snapshotProof(cwd);
			} catch (error) {
				after = new Map();
				failed.push(`unsafe proof tree (${(error as Error).message})`);
			}
			for (const [rel, now] of after) {
				const before = proofSnap.get(rel);
				if (before && before.equals(now)) continue;
				try {
					if (before) safeAtomicRestore(cwd, rel, before);
					else safeRemoveProofFile(cwd, rel);
					forged.push(rel);
			} catch (e) {
				failed.push(`${rel} (${(e as Error).message})`);
			}
		}
			for (const [rel, before] of proofSnap) {
				if (after.has(rel)) continue;
				try {
					safeAtomicRestore(cwd, rel, before);
					forged.push(rel);
			} catch (e) {
				failed.push(`${rel} (${(e as Error).message})`);
			}
		}

		if (forged.length || failed.length) {
			const parts = [`general_agent governance: an unauthorized change to protected file(s) was reverted.`];
			if (forged.length)
				parts.push(
					`PROOF reverted: ${forged.join(", ")} — verdicts, reviews, and workflow proofs come from ` +
						`their authorized writers (run them with \`${ACK_VAR}=1\`), not hand-authored.`,
				);
			if (failed.length)
				parts.push(
					`COULD NOT RESTORE (fail closed — treat as compromised, restore from git/backup): ${failed.join(", ")}.`,
				);
			// `content` is surfaced by both the legacy and extensions tool wrappers; `isError` is
			// additionally honored (thrown) by the extensions runner our gates load under.
			return { isError: true, content: parts.join(" ") };
		}
		return undefined;
	});
}

function projectRelativeCandidate(cwd: string, candidate: string): string {
	if (!path.isAbsolute(candidate)) return candidate;
	const relative = path.relative(cwd, candidate);
	if (relative !== ".." && !relative.startsWith(".." + path.sep) && !path.isAbsolute(relative)) {
		return relative.replace(/\\/g, "/");
	}
	return candidate;
}

function blocked(hit: string, viaTool = false) {
	// NOT a hard wall: a protected write is allowed when a deliberate reviewer/workflow command
	// carries the scoped acknowledgement. The message always teaches that route.
	const ackHint = viaTool
		? `To WRITE it deliberately, run a bash command prefixed with the authorization env var ` +
			`(the Write/Edit tools can't carry it; e.g. \`${ACK_VAR}=1 tee ${hit} <<'EOF' … EOF\`).`
		: `If the change is genuinely intended, re-run WITH the authorization env var prefixed: ` +
			`\`${ACK_VAR}=1 <your command>\` (you are asserting you know it normally must not change). ` +
			`If you were only READING it, use the read tool instead of a shell command.`;
	return {
		block: true,
		reason:
			`BLOCKED by general_agent governance: ${hit} is protected ` +
			`(authoritative review proof or self-protected control-plane). ${ackHint}`,
	};
}
