/**
 * Production launch-resolver boundary for the subagent controller.
 *
 * The controller may not decide for itself which model a role is allowed to
 * launch: only the repository-owned canonical resolver, run against a live
 * authenticated native catalog, can. This module is the single place that spawns
 * that resolver, and it binds every input to exact bytes:
 *
 * - the Python interpreter is one of a fixed set of absolute paths, and its bytes
 *   are digested and re-verified before every resolution (PATH is never consulted,
 *   and root ownership alone is not accepted as proof the file is unchanged);
 * - the resolver script is the repository-owned module that ships beside this file,
 *   digested and re-verified the same way;
 * - the canonical models document is passed by its already-frozen path AND digest,
 *   and the returned receipt must claim exactly that digest, so the resolver cannot
 *   have read a different policy than the controller validated.
 *
 * Everything here is production code. Tests replace this module at its boundary
 * (Bun `mock.module`) rather than toggling any exported hook, so there is no
 * production-importable injection point.
 */

import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

// The launch resolver is a repository-owned Python module that ships alongside the
// controller. It is resolved relative to this module (not the per-project root) so
// a caller cannot redirect it by pointing the root at a temp tree, and so installed
// projects and the test harness share one canonical resolver.
const LAUNCH_RESOLVER_RELATIVE = "../../../update-platform-config/scripts/config-sync/resolve_launch_spec.py";
const LAUNCH_RESOLVER_PATH = path.resolve(import.meta.dir, LAUNCH_RESOLVER_RELATIVE);
const LAUNCH_RESOLVER_LOCAL_MODULES = [
	"project_paths.py",
	"validate_model_roles.py",
	"live_catalog.py",
];

export interface LaunchSpecRequest {
	taskClass: string;
	reasoningPosture: string;
	semanticRole: string;
	reviewMode: "none" | "exact-opposite";
	writerRole: string | null;
}

export type JsonObject = Record<string, unknown>;

export type LaunchResolver = ((request: LaunchSpecRequest) => JsonObject) & {
	readonly launchEnvironment: Readonly<Record<string, string>>;
};

export interface BoundFile {
	path: string;
	sha256: string;
}

// Environment variables that override endpoints, inject code, or reroute TLS /
// proxy trust. These are stripped from the resolver subprocess environment so a
// caller cannot redirect the authenticated catalog resolver at an alternate
// endpoint or preload code. Mirrors live_catalog._SCRUBBED_ENV_VARS.
const RESOLVER_SCRUBBED_ENV = new Set([
	"CURSOR_API_ENDPOINT", "CURSOR_API_KEY", "CURSOR_AUTH_TOKEN", "OPENAI_BASE_URL", "CODEX_API_BASE",
	"ANTHROPIC_BASE_URL", "ANTHROPIC_API_URL", "OPENAI_API_BASE", "ANTHROPIC_CUSTOM_HEADERS",
	"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN",
	"ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
	"ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
	"CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR",
	"CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_GATEWAY", "CLAUDE_CODE_USE_VERTEX",
	"CLAUDE_CODE_USE_BEDROCK",
	"NODE_OPTIONS", "NODE_TLS_REJECT_UNAUTHORIZED", "NODE_DEBUG", "NODE_DEBUG_NATIVE",
	"OPENSSL_CONF", "OPENSSL_ENGINES", "SSLKEYLOGFILE",
	"LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
	"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
	"http_proxy", "https_proxy", "all_proxy", "no_proxy",
	"SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
	"NODE_EXTRA_CA_CERTS", "GIT_SSL_NO_VERIFY",
	// Shell and runtime startup hooks. The resolver transitively spawns the Cursor
	// bash wrapper, so BASH_ENV/ENV/SHELLOPTS would run caller code inside the bound
	// catalog closure.
	"BASH_ENV", "ENV", "KSH_ENV", "SHELLOPTS", "BASHOPTS", "ZDOTDIR",
	"CDPATH", "IFS", "GLOBIGNORE", "PS4",
	"LD_LIBRARY_PATH", "LD_AUDIT", "LD_DEBUG",
	"PERL5OPT", "PERL5LIB", "RUBYOPT", "GEM_PATH",
	"NODE_PATH", "NODE_REPL_EXTERNAL_MODULE", "NODE_V8_COVERAGE",
	// The Cursor wrapper writes and reads a V8 compile cache derived from these. A
	// caller-chosen cache directory is executable input to the bound bundle.
	"NODE_COMPILE_CACHE", "XDG_CACHE_HOME", "AGENT_CLI_CREDENTIAL_STORE", "CURSOR_INVOKED_AS",
	"CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
	"CURSOR_CONFIG_DIR", "CURSOR_AGENT_CONFIG_DIR", "AGENT_CLI_CONFIG_DIR",
]);

// Credential-family variables are dropped along with the runtime families:
// PYTHONPATH/PYTHONHOME/PYTHONSTARTUP/PYTHONEXECUTABLE all change which code the
// bound interpreter imports and runs, while OPENAI_, CODEX_, and CURSOR_
// variables can select caller-supplied credentials or endpoints. CODEX_HOME is
// restored below from the passwd-derived account home after the family scrub.
// Cursor catalog probing restores only CURSOR_INVOKED_AS on the nested catalog
// child; this resolver process does not keep a caller Cursor credential.
const RESOLVER_SCRUBBED_ENV_PREFIXES = [
	"PYTHON", "BASH_FUNC_", "npm_", "NODE_", "OPENAI_", "CODEX_", "CURSOR_",
];

function osAccountHome(): string {
	const uid = process.getuid?.();
	if (!Number.isInteger(uid) || Number(uid) < 0) throw new Error("cannot determine the resolver process uid");
	let descriptor: number | undefined;
	let passwd: string;
	try {
		descriptor = fs.openSync("/etc/passwd", fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
		const stat = fs.fstatSync(descriptor);
		// eslint-disable-next-line no-bitwise
		if (!stat.isFile() || stat.uid !== 0 || (stat.mode & 0o022) !== 0) {
			throw new Error("/etc/passwd is not a root-owned, non-writable regular file");
		}
		passwd = fs.readFileSync(descriptor, "utf8");
	} finally {
		if (descriptor !== undefined) fs.closeSync(descriptor);
	}
	const entries = passwd.split(/\r?\n/u).filter(Boolean).map(line => line.split(":"));
	const matches = entries.filter(fields => fields.length === 7 && Number(fields[2]) === uid);
	if (matches.length !== 1 || !path.isAbsolute(matches[0][5])) {
		throw new Error(`cannot resolve one absolute OS account home for uid ${uid}`);
	}
	return fs.realpathSync(matches[0][5]);
}

export function scrubbedResolverEnv(): Record<string, string> {
	const env: Record<string, string> = {};
	for (const [key, value] of Object.entries(process.env)) {
		if (value === undefined) continue;
		if (RESOLVER_SCRUBBED_ENV.has(key)) continue;
		if (RESOLVER_SCRUBBED_ENV_PREFIXES.some(prefix => key.startsWith(prefix))) continue;
		env[key] = value;
	}
	// The resolver and the catalog binaries it invokes both derive credential and
	// install locations from HOME/PATH, so both are pinned to fixed account values
	// instead of inherited. The passwd lookup is performed directly because Bun's
	// os.userInfo().homedir follows a caller-supplied HOME, unlike Node's contract.
	const home = osAccountHome();
	env.HOME = home;
	env.PATH = [path.join(home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"].join(path.delimiter);
	env.CODEX_HOME = path.join(home, ".codex");
	env.XDG_CONFIG_HOME = path.join(home, ".config");
	env.XDG_DATA_HOME = path.join(home, ".local", "share");
	return env;
}

/**
 * Read a file through a no-follow descriptor and bind its digest.
 *
 * Every later use re-reads through this same function and compares digests, so a
 * path swap or a content rewrite after the initial bind fails closed instead of
 * silently changing which code runs.
 */
export function bindFileBytes(file: string, label: string): { binding: BoundFile; bytes: Buffer } {
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
	return {
		binding: { path: file, sha256: createHash("sha256").update(bytes).digest("hex") },
		bytes,
	};
}

export function verifyBoundFile(binding: BoundFile, label: string): void {
	const current = bindFileBytes(binding.path, label).binding;
	if (current.path !== binding.path || current.sha256 !== binding.sha256) {
		throw new Error(`${label} changed after it was bound; refusing to resolve a launch against swapped bytes`);
	}
}

// The fixed interpreters the repository-owned resolver may run under. PATH is never
// consulted: a caller-controlled `python3` earlier in PATH would otherwise execute
// arbitrary code with the resolver's authority.
const TRUSTED_PYTHON_INTERPRETERS = ["/usr/bin/python3", "/usr/local/bin/python3"];

/**
 * Bind the fixed Python interpreter to its exact bytes.
 *
 * Ownership and mode are checked because a writable or non-root interpreter is
 * already outside the trust boundary, but the binding itself is the digest: it is
 * re-verified before every resolution so an interpreter replaced mid-run (even by
 * another root-owned file) fails closed.
 */
export function boundPythonInterpreter(): BoundFile {
	const failures: string[] = [];
	for (const candidate of TRUSTED_PYTHON_INTERPRETERS) {
		let stats: fs.Stats;
		try {
			stats = fs.lstatSync(candidate);
		} catch {
			failures.push(`${candidate}: not installed`);
			continue;
		}
		// A symlinked interpreter is normal (python3 -> python3.12); resolve it and
		// validate the regular file the link lands on.
		const target = stats.isSymbolicLink() ? fs.realpathSync(candidate) : candidate;
		const targetStats = fs.statSync(target);
		if (!targetStats.isFile() || (targetStats.mode & 0o111) === 0) {
			failures.push(`${candidate}: not a regular file`);
			continue;
		}
		// eslint-disable-next-line no-bitwise
		if (targetStats.uid !== 0 || (targetStats.mode & 0o022) !== 0) {
			throw new Error(`trusted python interpreter ${target} is not a root-owned, non-writable file`);
		}
		return bindFileBytes(target, "trusted python interpreter").binding;
	}
	throw new Error(`no trusted python interpreter found; ${failures.join("; ")}`);
}

/**
 * Build the production launch resolver, bound to exact bytes.
 *
 * `models` is the path and digest the controller already froze. Interpreter,
 * resolver script, and models document are all re-verified immediately before
 * every resolution, and the receipt must claim the frozen models digest.
 */
export function createBoundLaunchResolver(root: string, models: BoundFile): LaunchResolver {
	const interpreter = boundPythonInterpreter();
	const resolverPath = fs.realpathSync(LAUNCH_RESOLVER_PATH);
	const resolver = bindFileBytes(resolverPath, "canonical launch resolver").binding;
	// resolve_launch_spec imports these repository-owned modules on every process
	// activation. Binding only its entrypoint would leave those transitive policy and
	// catalog implementations swappable between calls.
	const resolverModules = LAUNCH_RESOLVER_LOCAL_MODULES.map(name =>
		bindFileBytes(
			fs.realpathSync(path.join(path.dirname(resolverPath), name)),
			`canonical launch resolver dependency ${name}`,
		).binding,
	);
	const expectedModelsDigest = `sha256:${models.sha256}`;
	const launchEnvironment = Object.freeze(scrubbedResolverEnv());
	const resolve = (request: LaunchSpecRequest): JsonObject => {
		verifyBoundFile(interpreter, "trusted python interpreter");
		verifyBoundFile(resolver, "canonical launch resolver");
		for (const dependency of resolverModules) {
			verifyBoundFile(dependency, `canonical launch resolver dependency ${path.basename(dependency.path)}`);
		}
		verifyBoundFile(models, "canonical workflow role config");
		const argv = [
			interpreter.path,
			"-I",
			"-c",
			[
				"import runpy,sys",
				"sys.path.insert(0,sys.argv[1])",
				"sys.argv=sys.argv[2:]",
				"runpy.run_path(sys.argv[0],run_name='__main__')",
			].join(";"),
			path.dirname(resolver.path),
			resolver.path,
			"--models", models.path,
			"--task-class", request.taskClass,
			"--reasoning-posture", request.reasoningPosture,
			"--semantic-role", request.semanticRole,
			"--review-mode", request.reviewMode,
		];
		if (request.writerRole) argv.push("--writer-role", request.writerRole);
		const result = Bun.spawnSync({ cmd: argv, cwd: root, stdin: null, env: launchEnvironment });
		if (result.exitCode !== 0) {
			const stderr = result.stderr.toString().trim();
			throw new Error(`canonical launch resolver failed: ${stderr || `exit ${result.exitCode}`}`);
		}
		let receipt: JsonObject;
		try {
			receipt = JSON.parse(result.stdout.toString()) as JsonObject;
		} catch (error) {
			throw new Error(`canonical launch resolver emitted non-JSON output: ${(error as Error).message}`);
		}
		const policy = receipt.policy;
		const claimed = policy && typeof policy === "object" && !Array.isArray(policy)
			? (policy as JsonObject).modelsDocumentSha256
			: undefined;
		if (claimed !== expectedModelsDigest) {
			throw new Error(
				"canonical launch resolver read a models document that is not the frozen canonical policy "
				+ `(${String(claimed)} != ${expectedModelsDigest})`,
			);
		}
		return receipt;
	};
	return Object.assign(resolve, { launchEnvironment });
}
