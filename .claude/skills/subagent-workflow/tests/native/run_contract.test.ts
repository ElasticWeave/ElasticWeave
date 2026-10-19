import { describe, expect, spyOn, test } from "bun:test";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { pathToFileURL } from "node:url";
import {
	classifyNativeAttempt,
	classifyProvider,
	makeNativeRunner as makeNativeRunnerCore,
	MAX_NATIVE_RECOVERY_RETRIES,
	materializeWorkflowSkills,
	NATIVE_AGENT_TIMEOUT_SECONDS,
	NATIVE_RECOVERY_BACKOFF_SECONDS,
	nativeChildEnvironmentSha256,
	normalizeNativeActivationStdout,
	normalizeNativeFailureStderr,
	normalizeNativeMap,
	parseWorkflowSkillContract,
	renderNativeArgv,
	resolveNativeProviderIdentity,
	scrubEnv,
	spawnCollect,
	verifyNativeExecutionClosure,
	type NativeLaunchAuthorization,
	type NativeMap,
} from "../../scripts/native/run";

const emptyMap: NativeMap = { providers: {} };
const opts = { cwd: process.cwd(), env: {} };
const projectGateAsset = path.join(
	import.meta.dir,
	"../../../update-platform-config/assets/project/.claude/hooks/protect_paths.py",
);

function testLaunchAuthorization(
	map: NativeMap,
	args: string[],
	options: { cwd: string; signal?: AbortSignal; env?: Record<string, string> },
): NativeLaunchAuthorization {
	const modelIndex = args.indexOf("--model");
	const model = modelIndex >= 0 ? args[modelIndex + 1] ?? "" : "";
	const provider = model.includes("/") ? model.split("/")[0] : "test";
	const modelTail = model.split("/").at(-1) ?? "";
	const separator = modelTail.lastIndexOf(":");
	const catalogModelId = separator >= 0 ? modelTail.slice(0, separator) : modelTail;
	const entry = classifyProvider(map, provider, catalogModelId);
	const configured = entry?.bin ?? "missing-native-route";
	let executable: string | undefined;
	if (path.isAbsolute(configured)) {
		try { executable = fs.realpathSync(configured); } catch { executable = path.resolve(configured); }
	} else {
		for (const directory of (options.env?.PATH ?? process.env.PATH ?? "").split(path.delimiter).filter(Boolean)) {
			const candidate = path.join(directory, configured);
			try {
				fs.accessSync(candidate, fs.constants.X_OK);
				executable = fs.realpathSync(candidate);
				break;
			} catch {
				// Continue through the test's explicit PATH.
			}
		}
	}
	executable ??= path.resolve("/nonexistent-native-test", configured);
	let executableSha256 = `sha256:${"0".repeat(64)}`;
	try {
		executableSha256 = `sha256:${createHash("sha256").update(fs.readFileSync(executable)).digest("hex")}`;
	} catch {
		// An unavailable executable is represented by a well-formed but unverifiable binding.
	}
	const executionClosure = [{ role: "entrypoint" as const, path: executable, sha256: executableSha256 }];
	const executionClosureSha256 = `sha256:${createHash("sha256").update(
		executionClosure.map(item => `${item.role}\t${item.path}\t${item.sha256}`).join("\n"),
	).digest("hex")}`;
	const launchEnvironment = scrubEnv({ ...process.env, ...(options.env ?? {}) });
	return {
		backend: provider,
		provider,
		logicalExecutable: path.isAbsolute(configured) ? path.basename(configured) : configured,
		catalogModelId,
		executable,
		executableSha256,
		invocationPrefix: [executable],
		executionClosure,
		executionClosureSha256,
		launchEnvironment,
		launchEnvironmentSha256: nativeChildEnvironmentSha256(launchEnvironment),
		launchReceiptSha256: createHash("sha256").update(
			JSON.stringify([provider, catalogModelId, executable, executableSha256]),
		).digest("hex"),
	};
}

function makeNativeRunner(
	map: NativeMap,
	maxModelCalls = Number.POSITIVE_INFINITY,
	tracePath?: string,
	initialModelCalls = 0,
) {
	const runner = makeNativeRunnerCore(map, maxModelCalls, tracePath, initialModelCalls);
	const authorized = async (
		args: string[],
		options: { cwd: string; signal?: AbortSignal; env?: Record<string, string> },
	) => runner(args, { ...options, authorization: testLaunchAuthorization(map, args, options) });
	return Object.assign(authorized, { getModelCalls: runner.getModelCalls });
}

interface CursorAttackFixture {
	root: string;
	install: string;
	bundle: string;
	runtime: string;
	marker: string;
	args: string[];
	map: NativeMap;
	authorization: NativeLaunchAuthorization;
}

function shellQuote(value: string): string {
	return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function cursorAttackFixture(): CursorAttackFixture {
	const root = fs.mkdtempSync(path.join(os.homedir(), ".native-cursor-trust-"));
	const install = path.join(root, "cursor-version");
	fs.mkdirSync(install, { mode: 0o755 });
	const entrypoint = path.join(install, "cursor-agent");
	const bundle = path.join(install, "index.js");
	const runtime = path.join(install, "node");
	const marker = path.join(root, "launched");
	fs.writeFileSync(entrypoint, "#!/bin/sh\nexit 99\n", { mode: 0o755 });
	fs.writeFileSync(bundle, "console.log('{\"summary\":\"bound bundle\",\"statePatch\":[]}');\n", { mode: 0o644 });
	fs.writeFileSync(runtime, [
		"#!/bin/sh",
		`printf launched > ${shellQuote(marker)}`,
		'[ "$1" = --use-system-ca ] || exit 91',
		"shift",
		'exec /usr/bin/node "$@"',
	].join("\n"), { mode: 0o755 });
	const map: NativeMap = {
		providers: {
			test: { bin: entrypoint, nativeProviderFamily: "anthropic", args: ["{prompt}"] },
		},
	};
	const args = ["native-controller", "launch", "--model", "test/model-1", "-p", "do work"];
	const authorization = testLaunchAuthorization(map, args, { cwd: root, env: {} });
	authorization.backend = "cursor";
	authorization.invocationPrefix = [runtime, "--use-system-ca", bundle];
	authorization.executionClosure = [
		{
			role: "entrypoint",
			path: entrypoint,
			sha256: `sha256:${createHash("sha256").update(fs.readFileSync(entrypoint)).digest("hex")}`,
		},
		{
			role: "runtime",
			path: runtime,
			sha256: `sha256:${createHash("sha256").update(fs.readFileSync(runtime)).digest("hex")}`,
		},
		{
			role: "bundle",
			path: bundle,
			sha256: `sha256:${createHash("sha256").update(fs.readFileSync(bundle)).digest("hex")}`,
		},
	];
	const closureLines = authorization.executionClosure
		.map(item => `${item.role}\t${item.path}\t${item.sha256}`).join("\n");
	const manifestLines = ["cursor-agent", "index.js", "node"].sort().map(name =>
		`${name}\tsha256:${createHash("sha256").update(fs.readFileSync(path.join(install, name))).digest("hex")}`,
	).join("\n");
	authorization.executionClosureSha256 =
		`sha256:${createHash("sha256").update(`${closureLines}\n--install-manifest--\n${manifestLines}`).digest("hex")}`;
	return { root, install, bundle, runtime, marker, args, map, authorization };
}

async function expectCursorTrustAttackRejected(item: CursorAttackFixture, expected: string): Promise<void> {
	const tracePath = path.join(item.root, "native-calls.jsonl");
	const run = makeNativeRunnerCore(item.map, 1, tracePath);
	const result = await run(item.args, { cwd: item.root, env: {}, authorization: item.authorization });
	expect(result.exitCode).toBe(2);
	expect(result.stderr).toContain(expected);
	expect(run.getModelCalls()).toBe(0);
	expect(fs.existsSync(tracePath)).toBeFalse();
	expect(fs.existsSync(item.marker)).toBeFalse();
}

function statWithField(
	stat: fs.BigIntStats,
	field: "uid" | "ctimeNs",
	value: bigint,
): fs.BigIntStats {
	return new Proxy(stat, {
		get(target, property) {
			if (property === field) return value;
			const current = Reflect.get(target, property, target);
			return typeof current === "function" ? current.bind(target) : current;
		},
	});
}

describe("native workflow routing contract", () => {
	test("scrubs workflow authority variables before launching a child", () => {
		const home = os.userInfo().homedir;
		expect(scrubEnv({
			KEEP_ME: "yes",
			ANTHROPIC_API_KEY: "caller-auth-rejected",
			ANTHROPIC_BASE_URL: "https://attacker.invalid",
			CLAUDE_CONFIG_DIR: "/tmp/attacker-claude",
			OPENAI_API_KEY: "caller-openai-key-rejected",
			OPENAI_ORG_ID: "caller-openai-org-rejected",
			OPENAI_FUTURE_SELECTOR: "caller-openai-selector-rejected",
			CODEX_API_KEY: "caller-codex-key-rejected",
			CODEX_SESSION: "caller-codex-session-rejected",
			CODEX_FUTURE_SELECTOR: "caller-codex-selector-rejected",
			CODEX_HOME: "/tmp/attacker-codex-home",
			CURSOR_API_KEY: "caller-cursor-key-rejected",
			CURSOR_AUTH_TOKEN: "caller-cursor-token-rejected",
			CURSOR_FUTURE_SELECTOR: "caller-cursor-selector-rejected",
			HOME: "/tmp/native-home",
			PATH: "/tmp/attacker-bin",
			SSH_AUTH_SOCK: "/tmp/native-agent.sock",
			XDG_RUNTIME_DIR: "/tmp/attacker-runtime",
			GENERAL_AGENT_ALLOW_PROTECTED_WRITE: "1",
			BASH_ENV: "/tmp/bash-env",
			"BASH_FUNC_attack%%": "() { :; }",
			DENO_DIR: "/tmp/deno-cache",
			DYLD_INSERT_LIBRARIES: "/tmp/inject.dylib",
			LD_PRELOAD: "/tmp/inject.so",
			NODE_OPTIONS: "--require=/tmp/inject.js",
			PYTHONPATH: "/tmp/python-inject",
			GIT_ASKPASS: "/tmp/askpass",
			EMPTY: undefined,
		})).toEqual({
			KEEP_ME: "yes",
			HOME: home,
			PATH: [path.join(home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"].join(path.delimiter),
			CODEX_HOME: path.join(home, ".codex"),
			XDG_CONFIG_HOME: path.join(home, ".config"),
			XDG_DATA_HOME: path.join(home, ".local", "share"),
			SSH_AUTH_SOCK: "/tmp/native-agent.sock",
		});
	});

	test("abort terminates the whole process group before a grandchild can write", async () => {
		if (process.platform === "win32") return;
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-grandchild-"));
		const marker = path.join(cwd, "survived.txt");
		try {
			const controller = new AbortController();
			const pending = spawnCollect(
				[
					"/bin/sh",
					"-c",
					'(trap "" TERM; sleep 0.35; printf survived > "$1") >/dev/null 2>&1 & wait',
					"sh",
					marker,
				],
				{ cwd, signal: controller.signal, terminationGraceMs: 75 },
			);
			setTimeout(() => controller.abort(), 40);
			await pending;
			await Bun.sleep(400);
			expect(fs.existsSync(marker)).toBe(false);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("abort escalates to SIGKILL when the process group ignores SIGTERM", async () => {
		if (process.platform === "win32") return;
		const controller = new AbortController();
		const started = performance.now();
		const pending = spawnCollect(
			["/bin/sh", "-c", 'trap "" TERM; while :; do sleep 1; done'],
			{ cwd: process.cwd(), signal: controller.signal, terminationGraceMs: 75 },
		);
		setTimeout(() => controller.abort(), 40);
		const result = await pending;
		const elapsed = performance.now() - started;
		expect(result.exitCode).not.toBe(0);
		expect(elapsed).toBeGreaterThanOrEqual(70);
		expect(elapsed).toBeLessThan(1_000);
	});

	test("normal leader exit still cleans a detached grandchild before returning", async () => {
		if (process.platform === "win32") return;
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-orphan-"));
		const marker = path.join(cwd, "survived.txt");
		try {
			const result = await spawnCollect(
				[
					"/bin/sh",
					"-c",
					'(trap "" TERM; sleep 0.35; printf survived > "$1") >/dev/null 2>&1 & exit 0',
					"sh",
					marker,
				],
				{ cwd, terminationGraceMs: 75 },
			);
			expect(result.exitCode).toBe(0);
			await Bun.sleep(400);
			expect(fs.existsSync(marker)).toBe(false);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("post-exit cleanup probe failure preserves the native response and fails without resume", async () => {
		if (process.platform === "win32") return;
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-cleanup-probe-"));
		try {
			const cli = path.join(cwd, "fake-cleanup-cli.sh");
			fs.writeFileSync(cli, [
				"#!/bin/sh",
				"printf 'invoked\\n' >> invocations.txt",
				"printf 'native stdout survives\\n'",
				"printf 'native stderr survives\\n' >&2",
				"exit 0",
			].join("\n"), { mode: 0o700 });
			const trace = path.join(cwd, "native-calls.jsonl");
			const run = makeNativeRunner({
				providers: {
					test: {
						bin: cli,
						args: ["{prompt}"],
						session: {
							idSource: "assigned-uuid",
							start: { position: "start", args: ["--session-id", "{sessionId}"] },
							resume: { position: "start", args: ["--resume", "{sessionId}"] },
							continuePrompt: "continue",
							backoffSeconds: [0],
						},
					},
				},
			}, 1, trace);
			const kill = spyOn(process, "kill").mockImplementation(((pid: number, signal?: NodeJS.Signals | number) => {
				if (pid < 0 && signal === 0) {
					throw Object.assign(new Error("fault injected cleanup probe denial"), { code: "EPERM" });
				}
				return true;
			}) as typeof process.kill);
			let result: Awaited<ReturnType<typeof run>>;
			try {
				result = await run(
					["launch", "--model", "test/model", "-p", "do work"],
					{ cwd, env: {} },
				);
			} finally {
				kill.mockRestore();
			}

			expect(result.exitCode).not.toBe(0);
			expect(result.stdout).toBe("native stdout survives\n");
			expect(result.stderr).toContain("native stderr survives");
			expect(result.stderr).not.toContain("could not start");
			expect(fs.readFileSync(path.join(cwd, "invocations.txt"), "utf8").trim().split("\n")).toHaveLength(1);
			const attempts = fs.readFileSync(path.join(cwd, "native-attempts.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line));
			expect(attempts).toHaveLength(1);
			expect(attempts[0]).toMatchObject({
				classification: "PROCESS_CLEANUP_FAILED",
				exitCode: 0,
				cleanupFailure: {
					phase: "orphan-probe",
					code: "EPERM",
					cleanupVerified: false,
				},
			});
			expect(attempts[0].stdoutBytes).toBe(Buffer.byteLength("native stdout survives\n"));
			expect(attempts[0].stderrBytes).toBe(Buffer.byteLength("native stderr survives\n"));
			expect(attempts[0].stdoutSha256).toBe(
				createHash("sha256").update("native stdout survives\n").digest("hex"),
			);
			expect(attempts[0].stderrSha256).toBe(
				createHash("sha256").update("native stderr survives\n").digest("hex"),
			);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("native runner exposes actual invocation count including retries", async () => {
		const run = makeNativeRunner(
			{ providers: { test: { bin: "/usr/bin/true", args: [] } } },
			3,
		);
		const args = ["native-controller", "launch", "--model", "test/model-1", "-p", "do work"];
		expect((await run(args, opts)).exitCode).toBe(0);
		expect((await run(args, opts)).exitCode).toBe(0);
		expect(run.getModelCalls()).toBe(2);
	});

	test("native runner has no production path that launches without receipt authorization", async () => {
		const run = makeNativeRunnerCore(
			{ providers: { test: { bin: "/usr/bin/true", args: [] } } },
			1,
		);
		const result = await run(
			["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
			{ cwd: process.cwd(), env: {} } as Parameters<typeof run>[1],
		);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("launch authorization failed");
		expect(run.getModelCalls()).toBe(0);
	});

	test("launches an authenticated runtime-plus-bundle prefix and traces the actual argv", async () => {
		const cwd = fs.mkdtempSync(path.join(os.homedir(), ".native-runtime-bundle-"));
		try {
			const install = path.join(cwd, "cursor-version");
			fs.mkdirSync(install, { mode: 0o755 });
			const entrypoint = path.join(install, "cursor-agent");
			const bundle = path.join(install, "index.js");
			const runtime = path.join(install, "node");
			fs.writeFileSync(entrypoint, "#!/bin/sh\nexit 99\n", { mode: 0o755 });
			fs.writeFileSync(bundle, "console.log('{\"summary\":\"bound bundle\",\"statePatch\":[]}');\n", { mode: 0o644 });
			fs.writeFileSync(
				runtime,
				"#!/bin/sh\n[ \"$1\" = --use-system-ca ] || exit 91\nshift\nexec /usr/bin/node \"$@\"\n",
				{ mode: 0o755 },
			);
			const map: NativeMap = {
				providers: {
					test: {
						bin: entrypoint,
						nativeProviderFamily: "anthropic",
						args: ["{prompt}"],
					},
				},
			};
			const args = ["native-controller", "launch", "--model", "test/model-1", "-p", "do work"];
			const authorization = testLaunchAuthorization(map, args, { cwd, env: {} });
			authorization.backend = "cursor";
			authorization.invocationPrefix = [runtime, "--use-system-ca", bundle];
			authorization.executionClosure = [
				{
					role: "entrypoint",
					path: entrypoint,
					sha256: `sha256:${createHash("sha256").update(fs.readFileSync(entrypoint)).digest("hex")}`,
				},
				{
					role: "runtime",
					path: runtime,
					sha256: `sha256:${createHash("sha256").update(fs.readFileSync(runtime)).digest("hex")}`,
				},
				{
					role: "bundle",
					path: bundle,
					sha256: `sha256:${createHash("sha256").update(fs.readFileSync(bundle)).digest("hex")}`,
				},
			];
			const closureLines = authorization.executionClosure
				.map(item => `${item.role}\t${item.path}\t${item.sha256}`).join("\n");
			const manifestLines = ["cursor-agent", "index.js", "node"].sort().map(name =>
				`${name}\tsha256:${createHash("sha256").update(fs.readFileSync(path.join(install, name))).digest("hex")}`,
			).join("\n");
			authorization.executionClosureSha256 =
				`sha256:${createHash("sha256").update(`${closureLines}\n--install-manifest--\n${manifestLines}`).digest("hex")}`;
			const tracePath = path.join(cwd, "native-calls.jsonl");
			const run = makeNativeRunnerCore(map, 1, tracePath);
			const result = await run(args, { cwd, env: {}, authorization });
			expect(result.exitCode).toBe(0);
			expect(result.stdout).toContain('"summary":"bound bundle"');
			const trace = JSON.parse(fs.readFileSync(tracePath, "utf8"));
			const attempt = JSON.parse(fs.readFileSync(path.join(cwd, "native-attempts.jsonl"), "utf8"));
			expect(trace.argvSha256).toBe(attempt.argvSha256);
			expect(trace.executable).toBe(entrypoint);
			expect(trace.processExecutable).toBe(runtime);
			const running = path.join(install, ".running");
			fs.mkdirSync(running);
			fs.writeFileSync(path.join(running, "state"), "one\n");
			expect(() => verifyNativeExecutionClosure(authorization)).not.toThrow();
			fs.writeFileSync(path.join(running, "state"), "two\n");
			expect(() => verifyNativeExecutionClosure(authorization)).not.toThrow();
			fs.writeFileSync(
				path.join(install, "late-loaded-chunk.js"),
				"export const drift = true;\n",
				{ mode: 0o644 },
			);
			expect(() => verifyNativeExecutionClosure(authorization))
				.toThrow("execution closure digest is invalid");
			fs.rmSync(path.join(install, "late-loaded-chunk.js"));
			const nested = path.join(install, "nested");
			fs.mkdirSync(nested, { mode: 0o755 });
			fs.writeFileSync(path.join(nested, ".running"), "not runtime state\n", { mode: 0o644 });
			expect(() => verifyNativeExecutionClosure(authorization))
				.toThrow("execution closure digest is invalid");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("Cursor native child drops caller credential selectors and restores only the wrapper marker", async () => {
		const cwd = fs.mkdtempSync(path.join(os.homedir(), ".native-cursor-env-"));
		try {
			const install = path.join(cwd, "cursor-version");
			fs.mkdirSync(install, { mode: 0o755 });
			const entrypoint = path.join(install, "cursor-agent");
			const bundle = path.join(install, "index.js");
			const runtime = path.join(install, "node");
			fs.writeFileSync(entrypoint, "#!/bin/sh\nexit 99\n", { mode: 0o755 });
			fs.writeFileSync(bundle, [
				"const denied = [",
				"  'CURSOR_API_KEY',",
				"  'CURSOR_AUTH_TOKEN',",
				"  'CURSOR_FUTURE_ACCOUNT_SELECTOR',",
				"];",
				"console.log(JSON.stringify({",
				"  denied: Object.fromEntries(denied.map(name => [name, process.env[name] ?? null])),",
				"  invokedAs: process.env.CURSOR_INVOKED_AS ?? null,",
				"  cursorFamilyKeys: Object.keys(process.env).filter(name => name.startsWith('CURSOR_')).sort(),",
				"  summary: 'bound bundle',",
				"  statePatch: [],",
				"}));",
			].join("\n"), { mode: 0o644 });
			fs.writeFileSync(
				runtime,
				"#!/bin/sh\n[ \"$1\" = --use-system-ca ] || exit 91\nshift\nexec /usr/bin/node \"$@\"\n",
				{ mode: 0o755 },
			);
			const map: NativeMap = {
				providers: {
					test: { bin: entrypoint, nativeProviderFamily: "anthropic", args: ["{prompt}"] },
				},
			};
			const args = ["native-controller", "launch", "--model", "test/model-1", "-p", "do work"];
			const authorization = testLaunchAuthorization(map, args, {
				cwd,
				env: {
					CURSOR_API_KEY: "caller-cursor-key-rejected",
					CURSOR_AUTH_TOKEN: "caller-cursor-token-rejected",
					CURSOR_FUTURE_ACCOUNT_SELECTOR: "caller-cursor-future-rejected",
					CURSOR_INVOKED_AS: "forged",
				},
			});
			authorization.backend = "cursor";
			authorization.invocationPrefix = [runtime, "--use-system-ca", bundle];
			authorization.executionClosure = [
				{
					role: "entrypoint",
					path: entrypoint,
					sha256: `sha256:${createHash("sha256").update(fs.readFileSync(entrypoint)).digest("hex")}`,
				},
				{
					role: "runtime",
					path: runtime,
					sha256: `sha256:${createHash("sha256").update(fs.readFileSync(runtime)).digest("hex")}`,
				},
				{
					role: "bundle",
					path: bundle,
					sha256: `sha256:${createHash("sha256").update(fs.readFileSync(bundle)).digest("hex")}`,
				},
			];
			const closureLines = authorization.executionClosure
				.map(item => `${item.role}\t${item.path}\t${item.sha256}`).join("\n");
			const manifestLines = ["cursor-agent", "index.js", "node"].sort().map(name =>
				`${name}\tsha256:${createHash("sha256").update(fs.readFileSync(path.join(install, name))).digest("hex")}`,
			).join("\n");
			authorization.executionClosureSha256 =
				`sha256:${createHash("sha256").update(`${closureLines}\n--install-manifest--\n${manifestLines}`).digest("hex")}`;
			const result = await makeNativeRunnerCore(map, 1)(args, { cwd, env: {}, authorization });
			expect(result.exitCode).toBe(0);
			const observed = JSON.parse(result.stdout);
			expect(Object.values(observed.denied).every(value => value === null)).toBe(true);
			expect(observed.invokedAs).toBe("cursor-agent");
			expect(observed.cursorFamilyKeys).toEqual(["CURSOR_INVOKED_AS"]);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("Cursor chmod-only trust drift fails before process, counter, or trace writes", async () => {
		const attacks: Array<{
			target: keyof Pick<CursorAttackFixture, "root" | "bundle" | "install" | "runtime">;
			mode: number;
			error: string;
		}> = [
			{ target: "bundle", mode: 0o666, error: "world-writable" },
			{ target: "bundle", mode: 0o664, error: "group-writable" },
			{ target: "install", mode: 0o777, error: "world-writable" },
			{ target: "install", mode: 0o775, error: "group-writable" },
			{ target: "root", mode: 0o777, error: "world-writable" },
			{ target: "root", mode: 0o770, error: "group-writable" },
			{ target: "runtime", mode: 0o644, error: "runtime is not user-executable" },
		];
		for (const attack of attacks) {
			const item = cursorAttackFixture();
			try {
				fs.chmodSync(item[attack.target], attack.mode);
				await expectCursorTrustAttackRejected(item, attack.error);
			} finally {
				fs.rmSync(item.root, { recursive: true, force: true });
			}
		}
	});

	test("Cursor foreign-owned directories and members fail before process, counter, or trace writes", async () => {
		if (process.getuid === undefined) return;
		for (const target of ["root", "install", "bundle"] as const) {
			const item = cursorAttackFixture();
			const realLstat = fs.lstatSync;
			const foreignUid = BigInt(process.getuid() + 10_000);
			const lstat = spyOn(fs, "lstatSync").mockImplementation(((file: fs.PathLike, options?: unknown) => {
				const stat = realLstat(file, options as { bigint: true }) as fs.BigIntStats;
				return path.resolve(String(file)) === item[target]
					? statWithField(stat, "uid", foreignUid)
					: stat;
			}) as typeof fs.lstatSync);
			try {
				await expectCursorTrustAttackRejected(item, "not owned by the current user or root");
			} finally {
				lstat.mockRestore();
				fs.rmSync(item.root, { recursive: true, force: true });
			}
		}
	});

	test("Cursor hard links and read-time identity swaps fail before process, counter, or trace writes", async () => {
		const linked = cursorAttackFixture();
		try {
			fs.linkSync(linked.bundle, path.join(linked.root, "bundle-hardlink"));
			await expectCursorTrustAttackRejected(linked, "multiple hard links");
		} finally {
			fs.rmSync(linked.root, { recursive: true, force: true });
		}

		const swapped = cursorAttackFixture();
		const realLstat = fs.lstatSync;
		let bundleStats = 0;
		const lstat = spyOn(fs, "lstatSync").mockImplementation(((file: fs.PathLike, options?: unknown) => {
			const stat = realLstat(file, options as { bigint: true }) as fs.BigIntStats;
			if (path.resolve(String(file)) !== swapped.bundle || ++bundleStats < 2) return stat;
			return statWithField(stat, "ctimeNs", stat.ctimeNs + 1n);
		}) as typeof fs.lstatSync);
		try {
			await expectCursorTrustAttackRejected(swapped, "changed while it was being read");
		} finally {
			lstat.mockRestore();
			fs.rmSync(swapped.root, { recursive: true, force: true });
		}
	});

	test("entrypoint and transitive closure drift stop before trace, counter, or process launch", async () => {
		for (const attack of ["entrypoint", "bundle"] as const) {
			const cwd = fs.mkdtempSync(path.join(os.tmpdir(), `native-auth-${attack}-`));
			try {
				const marker = path.join(cwd, "launched");
				const executable = path.join(cwd, "native-test");
				fs.writeFileSync(executable, `#!/bin/sh\ntouch '${marker}'\nprintf '{}\\n'\n`, { mode: 0o755 });
				const map: NativeMap = { providers: { test: { bin: executable, args: ["{prompt}"] } } };
				const args = ["native-controller", "launch", "--model", "test/model-1", "-p", "do work"];
				const authorization = testLaunchAuthorization(map, args, { cwd, env: {} });
				if (attack === "entrypoint") {
					fs.appendFileSync(executable, "# changed after authentication\n");
				} else {
					const bundle = path.join(cwd, "native-bundle.js");
					fs.writeFileSync(bundle, "export const bound = true;\n");
					authorization.executionClosure.push({
						role: "bundle",
						path: bundle,
						sha256: `sha256:${createHash("sha256").update(fs.readFileSync(bundle)).digest("hex")}`,
					});
					authorization.executionClosureSha256 = `sha256:${createHash("sha256").update(
						authorization.executionClosure
							.map(item => `${item.role}\t${item.path}\t${item.sha256}`).join("\n"),
					).digest("hex")}`;
					fs.appendFileSync(bundle, "// changed after authentication\n");
				}
				const trace = path.join(cwd, "native-calls.jsonl");
				const run = makeNativeRunnerCore(map, 1, trace);
				const result = await run(args, { cwd, env: {}, authorization });
				expect(result.exitCode).toBe(2);
				expect(result.stderr).toContain("closure bytes changed");
				expect(run.getModelCalls()).toBe(0);
				expect(fs.existsSync(trace)).toBeFalse();
				expect(fs.existsSync(marker)).toBeFalse();
			} finally {
				fs.rmSync(cwd, { recursive: true, force: true });
			}
		}
	});

	test("native runner resumes from a validated prior call count", () => {
		const run = makeNativeRunner(emptyMap, 5, undefined, 3);
		expect(run.getModelCalls()).toBe(3);
		expect(() => makeNativeRunner(emptyMap, 2, undefined, 3)).toThrow("initial model-call count");
	});

	test("create-stdout session profiles must bind their created id into the start argv", () => {
		expect(() => makeNativeRunner({
			providers: {
				test: {
					bin: "test",
					args: ["{prompt}"],
					session: {
						idSource: "create-stdout",
						createArgs: ["create-chat"],
						start: { position: "start", args: ["--print"] },
						resume: { position: "start", args: ["--resume", "{sessionId}"] },
						continuePrompt: "continue",
						backoffSeconds: [0],
					},
				},
			},
		})).toThrow("create-stdout session start must reference");
	});

	test("create-stdout preflight waits for the real created session before activation", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-created-session-"));
		try {
			const cli = path.join(cwd, "cli.sh");
			fs.writeFileSync(cli, [
				"#!/bin/sh",
				'if [ "$1" = create-chat ]; then echo created-session; exit 0; fi',
				'[ "$1" = --resume ] && [ "$2" = created-session ] || exit 93',
				"echo '{\"summary\":\"created and activated\",\"statePatch\":[]}'",
			].join("\n"), { mode: 0o700 });
			const run = makeNativeRunner({ providers: { test: { bin: cli, args: ["{prompt}"], session: {
				idSource: "create-stdout", createArgs: ["create-chat"],
				start: { position: "start", args: ["--resume", "{sessionId}"] },
				resume: { position: "start", args: ["--resume", "{sessionId}"] },
				continuePrompt: "continue", backoffSeconds: [0],
			} } } }, 1, path.join(cwd, "native-calls.jsonl"));
			const result = await run(["launch", "--model", "test/model", "-p", "work"], { cwd, env: {} });
			expect(result.exitCode).toBe(0);
			expect(result.stdout).toContain("created and activated");
			const attempts = fs.readFileSync(path.join(cwd, "native-attempts.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line));
			expect(attempts.map(item => item.mode)).toEqual(["session-create", "start"]);
			expect(attempts[1].sessionId).toBe("created-session");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("retryable CLI failure resumes the same assigned session without spending another logical call", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-session-resume-"));
		try {
			const cli = path.join(cwd, "fake-session-cli.sh");
			fs.writeFileSync(cli, [
				"#!/bin/sh",
				"mode=$1",
				"session_id=$2",
				"prompt=$3",
				'if [ "$mode" = "--session-id" ]; then',
				'  printf "%s" "$session_id" > session-id.txt',
				'  printf "network error: connection reset by peer\\n" >&2',
				"  exit 1",
				"fi",
				'if [ "$mode" = "--resume" ] && [ "$(cat session-id.txt)" = "$session_id" ]; then',
				'  case "$prompt" in *continue*) ;; *) exit 92 ;; esac',
				'  printf \'{"summary":"resumed","statePatch":[]}\\n\'',
				"  exit 0",
				"fi",
				"exit 93",
			].join("\n"), { mode: 0o700 });
			const trace = path.join(cwd, "native-calls.jsonl");
			const run = makeNativeRunner({
				providers: {
					test: {
						bin: cli,
						args: ["{prompt}"],
						session: {
							idSource: "assigned-uuid",
							start: { position: "start", args: ["--session-id", "{sessionId}"] },
							resume: { position: "start", args: ["--resume", "{sessionId}"] },
							continuePrompt: "continue",
							backoffSeconds: [0],
						},
					},
				},
			}, 1, trace);
			const result = await run(
				["launch", "--model", "test/model", "-p", "do work"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(0);
			expect(result.stdout).toContain('"summary":"resumed"');
			expect(run.getModelCalls()).toBe(1);
			const attempts = fs.readFileSync(path.join(cwd, "native-attempts.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line));
			expect(attempts).toHaveLength(2);
			expect(attempts.map(item => item.mode)).toEqual(["start", "resume"]);
			expect(attempts[0].sessionId).toBe(attempts[1].sessionId);
			expect(attempts[0].classification).toBe("TRANSIENT_TRANSPORT");
			expect(attempts[1].classification).toBe("COMPLETED");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("unexpected native exits stop after the initial call plus four retries", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-bounded-retry-"));
		try {
			const cli = path.join(cwd, "always-exits.sh");
			fs.writeFileSync(cli, [
				"#!/bin/sh",
				"printf 'unexpected native process exit\\n' >&2",
				"exit 77",
			].join("\n"), { mode: 0o700 });
			const run = makeNativeRunner({
				providers: {
					test: {
						bin: cli,
						args: ["{prompt}"],
						session: {
							idSource: "assigned-uuid",
							start: { position: "start", args: ["--session-id", "{sessionId}"] },
							resume: { position: "start", args: ["--resume", "{sessionId}"] },
							continuePrompt: "continue",
							backoffSeconds: [0, 0, 0, 0, 0],
						},
					},
				},
			}, 1, path.join(cwd, "native-calls.jsonl"));
			const result = await run(
				["launch", "--model", "test/model", "-p", "do work"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(77);
			const attempts = fs.readFileSync(path.join(cwd, "native-attempts.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line));
			expect(attempts).toHaveLength(MAX_NATIVE_RECOVERY_RETRIES + 1);
			expect(attempts.every(item => item.classification === "TRANSIENT_PROCESS_EXIT")).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("JSONL session discovery resumes by thread id and returns the final agent message", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-jsonl-resume-"));
		try {
			const cli = path.join(cwd, "fake-jsonl-cli.sh");
			fs.writeFileSync(cli, [
				"#!/bin/sh",
				'if [ "$1" != "resume" ]; then',
				'  printf \'{"type":"thread.started","thread_id":"019feed0-0000-7000-8000-000000000001"}\\n\'',
				'  printf \'{"type":"error","message":"stream disconnected before completion"}\\n\'',
				"  exit 1",
				"fi",
				'if [ "$2" != "019feed0-0000-7000-8000-000000000001" ]; then exit 94; fi',
				'printf \'{"type":"thread.started","thread_id":"019feed0-0000-7000-8000-000000000001"}\\n\'',
				'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"agent_message","text":"{\\\"summary\\\":\\\"codex resumed\\\",\\\"statePatch\\\":[]}"}}\'',
			].join("\n"), { mode: 0o700 });
			const trace = path.join(cwd, "native-calls.jsonl");
			const run = makeNativeRunner({
				providers: {
					test: {
						bin: cli,
						args: ["{prompt}"],
						session: {
							idSource: "jsonl-event",
							resume: { position: "start", args: ["resume", "{sessionId}"] },
							eventType: "thread.started",
							eventIdField: "thread_id",
							output: "jsonl-agent-message",
							continuePrompt: "continue",
							backoffSeconds: [0],
						},
					},
				},
			}, 1, trace);
			const result = await run(
				["launch", "--model", "test/model", "-p", "do work"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(0);
			expect(result.stdout.trim()).toBe('{"summary":"codex resumed","statePatch":[]}');
			const attempts = fs.readFileSync(path.join(cwd, "native-attempts.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line));
			expect(attempts[0].sessionId).toBe("019feed0-0000-7000-8000-000000000001");
			expect(attempts[1].sessionId).toBe(attempts[0].sessionId);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("production Codex read-only profile remains valid after exec resume insertion", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-codex-read-resume-"));
		try {
			const cli = path.join(cwd, "fake-codex.sh");
			fs.writeFileSync(cli, [
				"#!/bin/sh",
				'if [ "$2" != "resume" ]; then',
				'  printf \'%s\\n\' "$@" > start-argv.txt',
				'  printf \'{"type":"thread.started","thread_id":"019feed0-0000-7000-8000-000000000002"}\\n\'',
				'  printf \'{"type":"error","message":"stream disconnected before completion"}\\n\'',
				"  exit 1",
				"fi",
				'printf \'%s\\n\' "$@" > resume-argv.txt',
				'if [ "$3" != "019feed0-0000-7000-8000-000000000002" ]; then exit 94; fi',
				'printf \'{"type":"thread.started","thread_id":"019feed0-0000-7000-8000-000000000002"}\\n\'',
				'printf \'%s\\n\' \'{"type":"item.completed","item":{"type":"agent_message","text":"{\\\"summary\\\":\\\"codex read resumed\\\",\\\"statePatch\\\":[]}"}}\'',
			].join("\n"), { mode: 0o700 });
			const productionMap: NativeMap = JSON.parse(
				fs.readFileSync(path.join(import.meta.dir, "../../scripts/native/native-map.json"), "utf8"),
			);
			const productionCodex = productionMap.providers.codex;
			const run = makeNativeRunner({
				providers: {
					codex: {
						...productionCodex,
						bin: cli,
						session: {
							...productionCodex.session!,
							backoffSeconds: [0],
						},
					},
				},
			}, 1);
			const result = await run(
				["launch", "--model", "codex/model-under-test", "-p", "inspect"],
				{ cwd, env: { GENERAL_AGENT_WORKSPACE_ACCESS: "read" } },
			);
			expect(result.exitCode).toBe(0);
			expect(result.stdout.trim()).toBe('{"summary":"codex read resumed","statePatch":[]}');
			for (const argvPath of ["start-argv.txt", "resume-argv.txt"]) {
				const argv = fs.readFileSync(path.join(cwd, argvPath), "utf8").trim().split("\n");
				expect(argv).not.toContain("--sandbox");
				expect(argv).toContain('sandbox_mode="read-only"');
			}
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("heartbeat detects a silent stalled attempt and resumes its assigned session", async () => {
		if (process.platform === "win32") return;
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-heartbeat-resume-"));
		try {
			const cli = path.join(cwd, "fake-stalled-cli.sh");
			fs.writeFileSync(cli, [
				"#!/bin/sh",
				'if [ "$1" = "--session-id" ]; then sleep 5; exit 1; fi',
				'if [ "$1" = "--resume" ]; then printf \'{"summary":"after stall","statePatch":[]}\\n\'; exit 0; fi',
				"exit 95",
			].join("\n"), { mode: 0o700 });
			const trace = path.join(cwd, "native-calls.jsonl");
			const run = makeNativeRunner({
				providers: {
					test: {
						bin: cli,
						args: ["{prompt}"],
						session: {
							idSource: "assigned-uuid",
							start: { position: "start", args: ["--session-id", "{sessionId}"] },
							resume: { position: "start", args: ["--resume", "{sessionId}"] },
							continuePrompt: "continue",
							heartbeatSeconds: 0.02,
							stallSeconds: 0.2,
							backoffSeconds: [0],
						},
					},
				},
			}, 1, trace);
			const result = await run(
				["launch", "--model", "test/model", "-p", "do work"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(0);
			const attempts = fs.readFileSync(path.join(cwd, "native-attempts.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line));
			expect(attempts.length).toBeGreaterThanOrEqual(2);
			expect(attempts.slice(0, -1).every(item => item.classification === "HEARTBEAT_STALL")).toBe(true);
			expect(attempts.at(-1)?.classification).toBe("COMPLETED");
			expect(attempts.every(item => item.sessionId === attempts[0].sessionId)).toBe(true);
			const heartbeats = fs.readFileSync(path.join(cwd, "native-heartbeats.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line));
			expect(heartbeats.length).toBeGreaterThan(0);
			expect(heartbeats.every(item => item.call === 1 && item.sessionId === attempts[0].sessionId)).toBe(true);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("reclassifies Cursor TLS disconnects as transient without masking real cancellation", () => {
		const stderr = "[aborted] Client network socket disconnected before secure TLS connection was established";
		expect(normalizeNativeFailureStderr(stderr, false)).toBe(
			"network error: Client network socket disconnected before secure TLS connection was established",
		);
		expect(normalizeNativeFailureStderr(stderr, true)).toBe(stderr);
		expect(normalizeNativeFailureStderr("[aborted] user cancelled", false)).toBe("[aborted] user cancelled");
	});

	test("normalizes prose-wrapped fenced activation JSON", () => {
		const raw = [
			"I completed the requested analysis.",
			"```json",
			'{"summary":"done","statePatch":[{"op":"set","path":"/report","value":{"ok":true}}]}',
			"```",
			"",
		].join("\n");
		expect(normalizeNativeActivationStdout(raw)).toBe(
			'{"summary":"done","statePatch":[{"op":"set","path":"/report","value":{"ok":true}}]}\n',
		);
	});

	test("normalizes the last valid review verdict object from noisy stdout", () => {
		const raw = [
			'{"diagnostic":"intermediate"}',
			"Review complete:",
			'{"verdict":"PASS","summary":"all checks passed"}',
		].join("\n");
		expect(normalizeNativeActivationStdout(raw)).toBe(
			'{"verdict":"PASS","summary":"all checks passed"}\n',
		);
	});

	test("leaves stdout unchanged when it has no workflow activation object", () => {
		const raw = 'plain text with {"unrelated":true}\n';
		expect(normalizeNativeActivationStdout(raw)).toBe(raw);
	});

	test("materializes required skills and defers on-demand skill contents", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-skills-"));
		try {
			for (const [name, content] of [
				["required-skill", "# Required sentinel\nfollow this"],
				["deferred-skill", "# Deferred secret sentinel\ndo not inline"],
			]) {
				const directory = path.join(cwd, ".claude", "skills", name);
				fs.mkdirSync(directory, { recursive: true });
				fs.writeFileSync(path.join(directory, "SKILL.md"), content);
			}
			const prompt =
				'<!-- workflow-skills/v1 {"required":["required-skill"],"onDemand":["deferred-skill"]} -->\ndo work';
			const contract = parseWorkflowSkillContract(prompt);
			expect(contract).toEqual({ required: ["required-skill"], onDemand: ["deferred-skill"] });
			const materialized = materializeWorkflowSkills(prompt, cwd);
			expect(materialized).toContain("# Required sentinel");
			expect(materialized).toContain("deferred-skill/SKILL.md");
			expect(materialized).not.toContain("# Deferred secret sentinel");
			expect(materialized).not.toContain("workflow-skills/v1");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("skill preflight fails closed before spending a model call", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-missing-skill-"));
		try {
			const run = makeNativeRunner(
				{ providers: { test: { bin: "/usr/bin/true", args: [] } } },
				1,
			);
			const missing = await run(
				[
					"native-controller",
					"launch",
					"--model",
					"test/model-1",
					"-p",
					'<!-- workflow-skills/v1 {"required":["missing-skill"],"onDemand":[]} -->',
				],
				{ cwd, env: {} },
			);
			expect(missing.exitCode).toBe(2);
			expect(missing.stderr).toContain("skill preflight failed");
			const successful = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "plain prompt"],
				{ cwd, env: {} },
			);
			expect(successful.exitCode).toBe(0);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("rejects malformed, overlapping, and path-traversal skill contracts", () => {
		expect(() =>
			parseWorkflowSkillContract('<!-- workflow-skills/v1 {"required":["../escape"]} -->'),
		).toThrow("hyphen-case");
		expect(() =>
			parseWorkflowSkillContract(
				'<!-- workflow-skills/v1 {"required":["grind"],"onDemand":["grind"]} -->',
			),
		).toThrow("same skills");
		expect(() => parseWorkflowSkillContract("<!-- workflow-skills/v1 { -->")).toThrow("malformed JSON");
	});

	test("rejects a declared skill whose SKILL.md symlink escapes the project skill root", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-skill-symlink-"));
		try {
			const outside = path.join(cwd, "outside.md");
			const skill = path.join(cwd, ".claude", "skills", "linked-skill");
			fs.mkdirSync(skill, { recursive: true });
			fs.writeFileSync(outside, "outside sentinel");
			fs.symlinkSync(outside, path.join(skill, "SKILL.md"));
			expect(() =>
				materializeWorkflowSkills(
					'<!-- workflow-skills/v1 {"required":["linked-skill"],"onDemand":[]} -->',
					cwd,
				),
			).toThrow("resolves outside");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("routes K3 model ids through the Kimi native harness", () => {
		const kimi = { bin: "kimi", args: [] };
		const map: NativeMap = {
			providers: { kimi },
			modelPrefixes: { k3: "kimi" },
		};
		expect(classifyProvider(map, "relay-label", "k3-max")).toBe(kimi);
	});

	test("default native routes grant coding agents non-interactive full access", () => {
		const map: NativeMap = JSON.parse(
			fs.readFileSync(path.join(import.meta.dir, "../../scripts/native/native-map.json"), "utf8"),
		);
		expect(map.providers.anthropic.args).toContain("--dangerously-skip-permissions");
		expect(map.providers["openai-codex"].args).toContain(
			"--dangerously-bypass-approvals-and-sandbox",
		);
		expect(map.providers.codex.args).toContain("--dangerously-bypass-approvals-and-sandbox");
		expect(map.providers.cursor.args).toContain("--force");
		expect(map.providers.kimi.args).toContain("-y");
		expect(map.providers.anthropic.session).toMatchObject({ idSource: "assigned-uuid" });
		expect(map.providers.codex.session).toMatchObject({
			idSource: "jsonl-event",
			eventType: "thread.started",
			eventIdField: "thread_id",
		});
		expect(map.providers["openai-codex"].session).toEqual(map.providers.codex.session);
		expect(map.providers.cursor.session).toMatchObject({ idSource: "create-stdout", createArgs: ["create-chat"] });
		expect(map.providers.kimi.session).toBeUndefined();
		expect(map.providers.kimi.timeoutSeconds).toBe(NATIVE_AGENT_TIMEOUT_SECONDS);
		expect(map.providers.kimi.backoffSeconds).toEqual([...NATIVE_RECOVERY_BACKOFF_SECONDS]);
		for (const provider of ["anthropic", "codex", "openai-codex", "cursor"]) {
			expect(map.providers[provider].session?.stallSeconds).toBe(NATIVE_AGENT_TIMEOUT_SECONDS);
			expect(map.providers[provider].session?.backoffSeconds).toEqual([...NATIVE_RECOVERY_BACKOFF_SECONDS]);
		}
	});

	test("a non-session provider retries fresh attempts under its bounded timeout policy", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-no-session-retry-"));
		try {
			const cli = path.join(cwd, "fake-kimi.sh");
			fs.writeFileSync(cli, [
				"#!/bin/sh",
				"count_file=attempt-count",
				"count=0",
				"[ -f $count_file ] && count=$(cat $count_file)",
				"count=$((count + 1))",
				"printf '%s' $count > $count_file",
				"if [ $count -lt 3 ]; then printf 'unexpected native process exit\\n' >&2; exit 77; fi",
				"printf '{\"summary\":\"fresh retry\",\"statePatch\":[]}\\n'",
			].join("\n"), { mode: 0o700 });
			const map: NativeMap = {
				providers: {
					test: {
						bin: cli,
						args: ["{prompt}"],
						timeoutSeconds: 3600,
						backoffSeconds: [0, 0, 0, 0],
					},
				},
			};
			const run = makeNativeRunner(map, 1, path.join(cwd, "native-calls.jsonl"));
			const result = await run(
				["launch", "--model", "test/model", "-p", "do work"],
				{ cwd, env: {}, authorization: testLaunchAuthorization(map, ["launch", "--model", "test/model", "-p", "do work"], { cwd, env: {} }) },
			);
			expect(result.exitCode).toBe(0);
			expect(result.stdout).toContain('"summary":"fresh retry"');
			const attempts = fs.readFileSync(path.join(cwd, "native-attempts.jsonl"), "utf8")
				.trim().split("\n").map(line => JSON.parse(line));
			expect(attempts).toHaveLength(3);
			expect(attempts.map(item => item.mode)).toEqual(["start", "start", "start"]);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("failure classification retries transient failures but stops on permanent failures", () => {
		expect(classifyNativeAttempt(1, "stream disconnected before completion")).toBe("TRANSIENT_TRANSPORT");
		expect(classifyNativeAttempt(1, "HTTP 503 service unavailable")).toBe("SERVICE_THROTTLED");
		expect(classifyNativeAttempt(1, "unexpected native process exit")).toBe("TRANSIENT_PROCESS_EXIT");
		expect(classifyNativeAttempt(1, "Not logged in. Please run /login")).toBe("AUTH_OR_PERMISSION");
		expect(classifyNativeAttempt(1, "quota exhausted: insufficient credits")).toBe("NATIVE_FAILURE");
		expect(classifyNativeAttempt(1, "unsupported model claude-fable-5-1")).toBe("NATIVE_FAILURE");
		expect(classifyNativeAttempt(1, "invalid output schema")).toBe("NATIVE_FAILURE");
		expect(MAX_NATIVE_RECOVERY_RETRIES).toBe(4);
	});

	test("Claude effort renders as the live --effort CLI flag", () => {
		const map: NativeMap = JSON.parse(
			fs.readFileSync(path.join(import.meta.dir, "../../scripts/native/native-map.json"), "utf8"),
		);
		expect(renderNativeArgv(map.providers.anthropic, "claude-fable-5-1", "PROMPT", "write", "xhigh"))
			.toEqual([
				"claude",
				"--effort",
				"xhigh",
				"--model",
				"claude-fable-5-1",
				"-p",
				"PROMPT",
				"--output-format",
				"text",
				"--dangerously-skip-permissions",
			]);
	});

	test("Codex effort renders as a strict model_reasoning_effort config override", () => {
		const map: NativeMap = JSON.parse(
			fs.readFileSync(path.join(import.meta.dir, "../../scripts/native/native-map.json"), "utf8"),
		);
		expect(renderNativeArgv(map.providers.codex, "gpt-5.6-sol", "PROMPT", "write", "high"))
			.toEqual([
				"codex",
				"-c",
				'model_reasoning_effort="high"',
				"exec",
				"-m",
				"gpt-5.6-sol",
				"--dangerously-bypass-approvals-and-sandbox",
				"PROMPT",
			]);
	});

	test("Cursor launches the concrete catalog model id and refuses a separate effort", () => {
		const map: NativeMap = JSON.parse(
			fs.readFileSync(path.join(import.meta.dir, "../../scripts/native/native-map.json"), "utf8"),
		);
		// The caller passes the concrete per-effort SKU the fresh catalog authenticated,
		// so the argv carries that id verbatim and nothing synthesizes a selector.
		expect(renderNativeArgv(map.providers.cursor, "gpt-5.6-sol-xhigh", "PROMPT", "write"))
			.toEqual([
				"cursor-agent",
				"--print",
				"--output-format",
				"text",
				"--model",
				"gpt-5.6-sol-xhigh",
				"--force",
				"--trust",
				"--disable-project-configs",
				"--disable-auto-update",
				"PROMPT",
			]);
		// Applying an effort on top of a concrete id would launch a parameterization the
		// catalog never listed, so it fails closed instead of being silently appended.
		expect(() => renderNativeArgv(map.providers.cursor, "gpt-5.6-sol-xhigh", "PROMPT", "write", "xhigh"))
			.toThrow(/encodes reasoning effort in the resolved catalog model id/iu);
		expect(map.providers.cursor.effort?.encoding).toBe("catalog-model-id");
		expect(classifyProvider(map, "cursor", "gpt-5.6-sol")).toBe(map.providers.cursor);
	});

	test("Cursor read-only argv keeps the audited hardening flags and never passes --force", () => {
		const map: NativeMap = JSON.parse(
			fs.readFileSync(path.join(import.meta.dir, "../../scripts/native/native-map.json"), "utf8"),
		);
		const argv = renderNativeArgv(
			map.providers.cursor, "claude-opus-5-thinking-max", "PROMPT", "read",
		);
		expect(argv).toEqual([
			"cursor-agent",
			"--print",
			"--output-format",
			"text",
			"--mode",
			"plan",
			"--model",
			"claude-opus-5-thinking-max",
			"--trust",
			"--disable-project-configs",
			"--disable-auto-update",
			"--disable-indexing",
			"--disable-codebase-ref",
			"PROMPT",
		]);
		expect(argv).not.toContain("--force");
		expect(argv).not.toContain("--yolo");
	});

	test("provider identity keeps native harness and mapped upstream family separate", () => {
		const map: NativeMap = JSON.parse(
			fs.readFileSync(path.join(import.meta.dir, "../../scripts/native/native-map.json"), "utf8"),
		);
		const cursorGpt = resolveNativeProviderIdentity(map, "cursor", "gpt-5.6-sol")!;
		const cursorClaude = resolveNativeProviderIdentity(map, "cursor", "claude-opus-5")!;
		const codex = resolveNativeProviderIdentity(map, "codex", "gpt-5.6-sol")!;
		const claude = resolveNativeProviderIdentity(map, "anthropic", "claude-opus-5")!;

		expect(cursorGpt.executionProviderFamily).toBe("cursor");
		expect(cursorGpt.executionHarness).toBe(cursorClaude.executionHarness);
		expect(cursorGpt.upstreamProviderFamily).toBe("codex");
		expect(cursorClaude.upstreamProviderFamily).toBe("anthropic");
		expect(codex.executionHarness).not.toBe(claude.executionHarness);
		expect(codex.upstreamProviderFamily).not.toBe(claude.upstreamProviderFamily);
	});

	test("provider identity applies modelMap before determining the upstream family", () => {
		const map: NativeMap = {
			providers: {
				codex: { bin: "/usr/bin/true", args: [] },
				relay: {
					bin: "/usr/bin/false",
					args: [],
					relayModelPrefixes: ["alias-"],
					modelMap: { "alias-review": "gpt-review" },
				},
			},
			modelPrefixes: { "gpt-": "codex" },
		};
		const identity = resolveNativeProviderIdentity(map, "relay", "alias-review")!;
		expect(identity.mappedModel).toBe("gpt-review");
		expect(identity.upstreamProviderFamily).toBe("codex");
	});

	test("effort fails closed before a model call on an unsupported provider", async () => {
		const run = makeNativeRunner({
			providers: { kimi: { bin: "/usr/bin/true", args: [] } },
		});
		const result = await run(
			["native-controller", "launch", "--model", "kimi/k3-max", "--effort", "high", "-p", "inspect"],
			opts,
		);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("does not support reasoning effort");
		expect(run.getModelCalls()).toBe(0);
	});

	test("effort outside a route's audited values fails closed", async () => {
		const run = makeNativeRunner({
			providers: {
				test: {
					bin: "/usr/bin/true",
					args: [],
					effort: { values: ["low"], args: ["--effort", "{effort}"] },
				},
			},
		});
		const result = await run(
			["native-controller", "launch", "--model", "test/model", "--effort", "max", "-p", "inspect"],
			opts,
		);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("unsupported reasoning effort 'max'");
		expect(run.getModelCalls()).toBe(0);
	});

	test("native model suffix effort reaches the same native argv contract", async () => {
		const run = makeNativeRunner({
			providers: {
				test: {
					bin: "/bin/echo",
					args: ["{model}"],
					effort: { values: ["low"], args: ["--effort", "{effort}"] },
				},
			},
		});
		const result = await run(
			["native-controller", "launch", "--model", "test/model:low", "-p", "inspect"],
			opts,
		);
		expect(result.exitCode).toBe(0);
		expect(result.stdout.trim()).toBe("--effort low model");
	});

	test("conflicting flag and model-suffix effort fails closed", async () => {
		const run = makeNativeRunner({
			providers: {
				test: {
					bin: "/usr/bin/true",
					args: [],
					effort: { values: ["low", "high"], args: ["--effort", "{effort}"] },
				},
			},
		});
		const result = await run(
			["native-controller", "launch", "--model", "test/model:low", "--effort", "high", "-p", "inspect"],
			opts,
		);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("conflicting reasoning effort");
		expect(run.getModelCalls()).toBe(0);
	});

	test("scheduler/reviewer profiles are explicitly read-only", () => {
		const map: NativeMap = JSON.parse(
			fs.readFileSync(path.join(import.meta.dir, "../../scripts/native/native-map.json"), "utf8"),
		);
		expect(map.providers.anthropic.readArgs).toContain("dontAsk");
		expect(map.providers.anthropic.readArgs).not.toContain("plan");
		expect(map.providers.anthropic.readArgs).toContain("--safe-mode");
		expect(map.providers.anthropic.readArgs).not.toContain("--no-session-persistence");
		expect(map.providers.anthropic.readArgs).not.toContain("--dangerously-skip-permissions");
		expect(map.providers.anthropic.schedulerArgs).toContain("--safe-mode");
		expect(map.providers.anthropic.schedulerArgs).not.toContain("--no-session-persistence");
		expect(map.providers.anthropic.schedulerArgs).toContain("");
		expect(map.providers.anthropic.schedulerArgs).not.toContain("--dangerously-skip-permissions");
		expect(map.providers.codex.readArgs).toContain('sandbox_mode="read-only"');
		expect(map.providers["openai-codex"].readArgs).toContain('sandbox_mode="read-only"');
		expect(map.providers.codex.readArgs).not.toContain("--dangerously-bypass-approvals-and-sandbox");
		expect(map.providers.cursor.readArgs).toContain("plan");
		expect(map.providers.cursor.readArgs).not.toContain("--force");
		expect(map.providers.codex.schedulerArgs).toEqual([
			"exec",
			"-m",
			"{model}",
			"--sandbox",
			"read-only",
			"--disable",
			"multi_agent",
			"--ephemeral",
			"--ignore-rules",
			"--color",
			"never",
			"{prompt}",
		]);
		for (const forbidden of ["--dangerously-bypass-approvals-and-sandbox", "--add-dir", "--worktree", "--worktree-base"]) {
			expect(map.providers.codex.schedulerArgs).not.toContain(forbidden);
		}
		expect(renderNativeArgv(
			map.providers.codex,
			"gpt-5.6-luna",
			"SCHEDULER PROMPT",
			"schedule",
			"low",
		)).toEqual([
			"codex",
			"-c",
			"model_reasoning_effort=\"low\"",
			"exec",
			"-m",
			"gpt-5.6-luna",
			"--sandbox",
			"read-only",
			"--disable",
			"multi_agent",
			"--ephemeral",
			"--ignore-rules",
			"--color",
			"never",
			"SCHEDULER PROMPT",
		]);
		expect(map.providers.cursor.schedulerArgs).toBeUndefined();
		expect(map.providers["openai-codex"].schedulerArgs).toBeUndefined();
	});

	test("normalizes legacy readOnlyArgs without changing write or scheduler argv", () => {
		const entry = {
			bin: "native-cli",
			args: ["write", "{model}", "{prompt}"],
			readOnlyArgs: ["read", "{model}", "{prompt}"],
			schedulerArgs: ["schedule", "{model}", "{prompt}"],
			effort: { values: ["high"], args: ["--effort", "{effort}"] },
		};
		const normalized = normalizeNativeMap({ providers: { test: entry } });
		expect(normalized.providers.test.readArgs).toEqual(entry.readOnlyArgs);
		expect(normalized.providers.test.args).toEqual(entry.args);
		expect(normalized.providers.test.schedulerArgs).toEqual(entry.schedulerArgs);
		expect(renderNativeArgv(entry, "model", "PROMPT", "read", "high")).toEqual([
			"native-cli", "--effort", "high", "read", "model", "PROMPT",
		]);
		expect(renderNativeArgv(entry, "model", "PROMPT", "write")).toEqual([
			"native-cli", "write", "model", "PROMPT",
		]);
		expect(renderNativeArgv(entry, "model", "PROMPT", "schedule")).toEqual([
			"native-cli", "schedule", "model", "PROMPT",
		]);
	});

	test("accepts byte-equivalent read profile spellings and rejects conflicts at preflight", () => {
		const equivalent = {
			bin: "/usr/bin/true",
			args: [],
			readArgs: ["--sandbox", "read-only", "{prompt}"],
			readOnlyArgs: ["--sandbox", "read-only", "{prompt}"],
		};
		expect(normalizeNativeMap({ providers: { test: equivalent } }).providers.test.readArgs)
			.toEqual(equivalent.readArgs);
		expect(() => makeNativeRunner({ providers: { test: equivalent } })).not.toThrow();

		const conflicting = {
			...equivalent,
			readOnlyArgs: ["--sandbox", "workspace-write", "{prompt}"],
		};
		expect(() => normalizeNativeMap({ providers: { test: conflicting } }))
			.toThrow("conflicting readArgs and readOnlyArgs");
		expect(() => makeNativeRunner({ providers: { test: conflicting } }))
			.toThrow("conflicting readArgs and readOnlyArgs");
		expect(() => renderNativeArgv(conflicting, "model", "PROMPT", "write"))
			.toThrow("conflicting readArgs and readOnlyArgs");
	});

	test("legacy readOnlyArgs reaches the actual read invocation after skill materialization and authority scrub", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-read-alias-"));
		try {
			const skillDir = path.join(cwd, ".claude", "skills", "required-skill");
			fs.mkdirSync(skillDir, { recursive: true });
			fs.writeFileSync(path.join(skillDir, "SKILL.md"), "# Required alias sentinel\n");
			const command = [
				"case \"$2\" in *'Required alias sentinel'*) materialized=yes ;; *) materialized=no ;; esac",
				"printf '%s|%s|%s|%s|%s\\n' \"$1\" \"$materialized\" \"${GENERAL_AGENT_ALLOW_PROTECTED_WRITE-unset}\" \"${GENERAL_AGENT_WORKSPACE_ACCESS-unset}\" \"$KEEP_ME\"",
			].join("; ");
			const run = makeNativeRunner({
				providers: {
					test: {
						bin: "/bin/sh",
						args: ["-c", "exit 91"],
						readOnlyArgs: ["-c", command, "read-profile", "{model}", "{prompt}"],
					},
				},
			}, 1);
			const result = await run(
				[
					"native-controller",
					"launch",
					"--model",
					"test/model-1",
					"-p",
					'<!-- workflow-skills/v1 {"required":["required-skill"],"onDemand":[]} -->\ninspect',
				],
				{
					cwd,
					env: {
						GENERAL_AGENT_WORKSPACE_ACCESS: "read",
						GENERAL_AGENT_ALLOW_PROTECTED_WRITE: "1",
						KEEP_ME: "kept",
					},
				},
			);
			expect(result.exitCode).toBe(0);
			expect(result.stdout.trim()).toBe("model-1|yes|unset|unset|kept");
			expect(run.getModelCalls()).toBe(1);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("read-only native calls fail closed when a provider has no audited profile", async () => {
		const run = makeNativeRunner({
			providers: { test: { bin: "/usr/bin/env", args: [] } },
		});
		const result = await run(
			["native-controller", "launch", "--model", "test/model", "-p", "inspect"],
			{ ...opts, env: { GENERAL_AGENT_WORKSPACE_ACCESS: "read" } },
		);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("no audited read-only argv profile");
	});

	test("scheduler native calls fail closed when a provider has no audited profile", async () => {
		const run = makeNativeRunner({
			providers: { test: { bin: "/usr/bin/env", args: [], readArgs: [] } },
		});
		const result = await run(
			["native-controller", "launch", "--model", "test/model", "-p", "schedule"],
			{ ...opts, env: { GENERAL_AGENT_WORKSPACE_ACCESS: "schedule" } },
		);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("no audited scheduler argv profile");
	});

	test("rejects a provider label that contradicts the model family", () => {
		const map: NativeMap = {
			providers: {
				anthropic: { bin: "claude", args: [] },
				codex: { bin: "codex", args: [] },
			},
			modelPrefixes: { claude: "anthropic", gpt: "codex" },
		};
		expect(classifyProvider(map, "anthropic", "gpt-5.6-sol")).toBeUndefined();
	});

	test("ignores unrelated environment model hints and requires an explicit model flag", async () => {
		const run = makeNativeRunner(emptyMap);
		const result = await run(["native-controller", "launch", "-p", "do work"], {
			...opts,
			env: { UNRELATED_MODEL_REQUEST: "{" },
		});
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("resolved no model spec");
		expect(result.stderr).toContain("No model fallback");
	});

	test("fails closed on an unmapped provider", async () => {
		const run = makeNativeRunner(emptyMap);
		const result = await run(
			["native-controller", "launch", "--model", "unknown/model-1", "-p", "do work"],
			opts,
		);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("no native CLI route");
		expect(result.stderr).toContain("unknown/model-1");
		expect(result.stderr).toContain("No model fallback");
	});

	test("fails closed when the engine provides no model", async () => {
		const run = makeNativeRunner(emptyMap);
		const result = await run(["native-controller", "launch", "-p", "do work"], opts);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("resolved no model spec");
	});

	test("rejects an unexpected headless invocation shape", async () => {
		const run = makeNativeRunner(emptyMap);
		const result = await run(["native-controller", "something-else"], opts);
		expect(result.exitCode).toBe(2);
		expect(result.stderr).toContain("no 'launch' subcommand");
	});

	test("enforces the declared model-call budget", async () => {
		const map: NativeMap = {
			providers: { test: { bin: "/usr/bin/true", args: [] } },
		};
		const run = makeNativeRunner(map, 1);
		const args = ["native-controller", "launch", "--model", "test/model-1", "-p", "do work"];
		expect((await run(args, opts)).exitCode).toBe(0);
		const exhausted = await run(args, opts);
		expect(exhausted.exitCode).toBe(2);
		expect(exhausted.stderr).toContain("maxModelCalls=1");
	});

	test("native-call trace refuses a symlink instead of writing through it", async () => {
		if (process.platform === "win32") return;
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-trace-symlink-"));
		const previousTemp = process.env.TMPDIR;
		try {
			const victim = path.join(cwd, "victim.txt");
			const marker = path.join(cwd, "activated.txt");
			const trace = path.join(cwd, "native-calls.jsonl");
			const temporaryRoot = path.join(cwd, "tmp");
			fs.mkdirSync(temporaryRoot);
			process.env.TMPDIR = temporaryRoot;
			fs.writeFileSync(victim, "unchanged\n");
			fs.symlinkSync(victim, trace);
			const run = makeNativeRunner(
				{
					providers: {
						test: { bin: "/bin/sh", args: ["-c", "printf activated > activated.txt"] },
					},
				},
				1,
				trace,
			);
			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(2);
			expect(result.stderr).toContain("native-call trace");
			expect(fs.readFileSync(victim, "utf8")).toBe("unchanged\n");
			expect(fs.existsSync(marker)).toBe(false);
			expect(run.getModelCalls()).toBe(0);
			expect(fs.readdirSync(temporaryRoot).filter(name => name.startsWith("general-agent-node-gate-")))
				.toHaveLength(0);
		} finally {
			if (previousTemp === undefined) delete process.env.TMPDIR;
			else process.env.TMPDIR = previousTemp;
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("native-call trace parent failure audits and cleans the gate before activation", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-trace-parent-"));
		const previousTemp = process.env.TMPDIR;
		try {
			const marker = path.join(cwd, "activated.txt");
			const traceParent = path.join(cwd, "trace-parent");
			const trace = path.join(traceParent, "native-calls.jsonl");
			const temporaryRoot = path.join(cwd, "tmp");
			fs.mkdirSync(temporaryRoot);
			fs.writeFileSync(traceParent, "ordinary file\n");
			process.env.TMPDIR = temporaryRoot;
			const run = makeNativeRunner(
				{
					providers: {
						test: { bin: "/bin/sh", args: ["-c", "printf activated > activated.txt"] },
					},
				},
				1,
				trace,
			);

			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(2);
			expect(result.stderr).toContain("native-call trace");
			expect(run.getModelCalls()).toBe(0);
			expect(fs.existsSync(marker)).toBe(false);
			expect(fs.readdirSync(temporaryRoot)).toHaveLength(0);
		} finally {
			if (previousTemp === undefined) delete process.env.TMPDIR;
			else process.env.TMPDIR = previousTemp;
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("binds a canonical account environment and removes endpoint config and pre-main injection variables", async () => {
		const marker = "/tmp/secret-frozen-reward-helper";
		const deniedNames = [
			"BASH_ENV",
			"BASH_FUNC_attack%%",
			"BASHOPTS",
			"CDPATH",
			"DENO_DIR",
			"DYLD_INSERT_LIBRARIES",
			"ENV",
			"GENERAL_AGENT_REWARD_HELPER",
			"GENERAL_AGENT_REWARD_HELPER_SHA256",
			"ANTHROPIC_API_KEY",
			"ANTHROPIC_BASE_URL",
			"CLAUDE_CONFIG_DIR",
			"OPENAI_API_KEY",
			"OPENAI_ORG_ID",
			"OPENAI_FUTURE_SELECTOR",
			"CODEX_API_KEY",
			"CODEX_SESSION",
			"CODEX_FUTURE_SELECTOR",
			"CURSOR_API_KEY",
			"CURSOR_AUTH_TOKEN",
			"CURSOR_FUTURE_SELECTOR",
			"CURSOR_API_ENDPOINT",
			"GIT_ASKPASS",
			"KSH_ENV",
			"LD_LIBRARY_PATH",
			"LD_PRELOAD",
			"NODE_OPTIONS",
			"PERL5OPT",
			"PYTHONHOME",
			"PYTHONPATH",
			"PYTHONSTARTUP",
			"RUBYOPT",
			"SHELLOPTS",
			"SSH_ASKPASS",
			"ZDOTDIR",
		];
		const inspectEnv = [
			"import json, os",
			`denied = ${JSON.stringify(deniedNames)}`,
			"print(json.dumps({",
			"  'denied': {name: os.environ.get(name) for name in denied},",
			"  'sshAuthSock': os.environ.get('SSH_AUTH_SOCK'),",
			"  'home': os.environ.get('HOME'),",
			"  'path': os.environ.get('PATH'),",
			"  'codexHome': os.environ.get('CODEX_HOME'),",
			"  'credentialFamilyKeys': sorted(name for name in os.environ if name.startswith(('OPENAI_', 'CODEX_', 'CURSOR_'))),",
			"  'xdgConfigHome': os.environ.get('XDG_CONFIG_HOME'),",
			"  'xdgDataHome': os.environ.get('XDG_DATA_HOME'),",
			"}, sort_keys=True))",
		].join("\n");
		const run = makeNativeRunner({
			providers: { test: { bin: "/usr/bin/python3", args: ["-c", inspectEnv] } },
		}, 1);
		const result = await run(
			["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
			{
				cwd: process.cwd(),
				env: {
					ANTHROPIC_API_KEY: "test-auth-is-retained",
					ANTHROPIC_BASE_URL: "https://attacker.invalid",
					CLAUDE_CONFIG_DIR: "/tmp/attacker-claude-config",
					OPENAI_API_KEY: "caller-openai-key-rejected",
					OPENAI_ORG_ID: "caller-openai-org-rejected",
					OPENAI_FUTURE_SELECTOR: "caller-openai-selector-rejected",
					CODEX_API_KEY: "caller-codex-key-rejected",
					CODEX_SESSION: "caller-codex-session-rejected",
					CODEX_FUTURE_SELECTOR: "caller-codex-selector-rejected",
					CODEX_HOME: "/tmp/attacker-codex-home",
					CURSOR_API_KEY: "caller-cursor-key-rejected",
					CURSOR_AUTH_TOKEN: "caller-cursor-token-rejected",
					CURSOR_FUTURE_SELECTOR: "caller-cursor-selector-rejected",
					CURSOR_API_ENDPOINT: "https://attacker.invalid",
					SSH_AUTH_SOCK: "/tmp/test-ssh-agent.sock",
					GENERAL_AGENT_REWARD_HELPER: marker,
					GENERAL_AGENT_REWARD_HELPER_SHA256: "f".repeat(64),
					BASH_ENV: "/tmp/bash-env-inject",
					"BASH_FUNC_attack%%": "() { printf injected; }",
					BASHOPTS: "extdebug",
					CDPATH: "/tmp/cdpath-inject",
					DENO_DIR: "/tmp/deno-inject",
					DYLD_INSERT_LIBRARIES: "/tmp/dyld-inject",
					ENV: "/tmp/sh-env-inject",
					GIT_ASKPASS: "/tmp/git-askpass-inject",
					KSH_ENV: "/tmp/ksh-env-inject",
					LD_LIBRARY_PATH: "/tmp/ld-library-inject",
					LD_PRELOAD: "/tmp/ld-preload-inject",
					NODE_OPTIONS: "--require=/tmp/node-inject.js",
					PERL5OPT: "-M/tmp/perl-inject.pm",
					PYTHONHOME: "/tmp/python-home-inject",
					PYTHONPATH: "/tmp/python-path-inject",
					PYTHONSTARTUP: "/tmp/python-startup-inject.py",
					RUBYOPT: "-r/tmp/ruby-inject.rb",
					SHELLOPTS: "extdebug",
					SSH_ASKPASS: "/tmp/ssh-askpass-inject",
					ZDOTDIR: "/tmp/zsh-inject",
				},
			},
		);
		expect(result.exitCode).toBe(0);
		const observed = JSON.parse(result.stdout);
		expect(Object.values(observed.denied).every(value => value === null)).toBe(true);
		expect(observed.sshAuthSock).toBe("/tmp/test-ssh-agent.sock");
		const canonical = scrubEnv({});
		expect(observed.home).toBe(canonical.HOME);
		expect(observed.path).toBe(canonical.PATH);
		expect(observed.codexHome).toBe(canonical.CODEX_HOME);
		expect(observed.credentialFamilyKeys).toEqual(["CODEX_HOME"]);
		expect(observed.xdgConfigHome).toBe(canonical.XDG_CONFIG_HOME);
		expect(observed.xdgDataHome).toBe(canonical.XDG_DATA_HOME);
	});

	test("non-Claude node audit leaves general control-plane edits for diff and review", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-integrity-"));
		const helper = path.join(cwd, ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py");
		const hook = path.join(cwd, ".claude/hooks/protect_paths.py");
		try {
			fs.mkdirSync(path.dirname(helper), { recursive: true });
			fs.mkdirSync(path.dirname(hook), { recursive: true });
			fs.writeFileSync(helper, "original helper\n");
			fs.copyFileSync(projectGateAsset, hook);
			const command = [
				"printf 'tampered helper\\n' > .claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py",
				"printf 'tampered hook\\n' > .claude/hooks/protect_paths.py",
				"printf '{\"summary\":\"done\"}\\n'",
			].join("; ");
			const run = makeNativeRunner({ providers: { test: { bin: "/bin/sh", args: ["-c", command] } } }, 1);
			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(0);
			expect(fs.readFileSync(helper, "utf8")).toBe("tampered helper\n");
			expect(fs.readFileSync(hook, "utf8")).toBe("tampered hook\n");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("non-Claude node fails before activation when both installed and skill-owned gates are missing", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-missing-gate-"));
		try {
			const isolatedModule = path.join(
				cwd,
				"bundle/.claude/skills/subagent-workflow/scripts/native/run.ts",
			);
			const project = path.join(cwd, "project");
			const marker = path.join(project, "activated.txt");
			fs.mkdirSync(path.dirname(isolatedModule), { recursive: true });
			fs.mkdirSync(project, { recursive: true });
			fs.copyFileSync(path.join(import.meta.dir, "../../scripts/native/run.ts"), isolatedModule);
			const isolated = await import(`${pathToFileURL(isolatedModule).href}?missing-gate=${Date.now()}`);
			const command = "printf activated > activated.txt; printf '{\"summary\":\"done\",\"statePatch\":[]}\\n'";
			const map = { providers: { test: { bin: "/bin/sh", args: ["-c", command] } } };
			const run = isolated.makeNativeRunner(map, 1);
			const args = ["native-controller", "launch", "--model", "test/model-1", "-p", "do work"];
			const runOptions = { cwd: project, env: {} };
			const result = await run(args, {
				...runOptions,
				authorization: testLaunchAuthorization(map, args, runOptions),
			});

			expect(result.exitCode).toBe(2);
			expect(result.stderr).toContain("protected-path gate");
			expect(result.stderr).toContain("missing");
			expect(run.getModelCalls()).toBe(0);
			expect(fs.existsSync(marker)).toBe(false);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("non-Claude node rejects a symlinked installed gate instead of falling back", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-symlink-gate-"));
		const hook = path.join(cwd, ".claude/hooks/protect_paths.py");
		const marker = path.join(cwd, "activated.txt");
		try {
			fs.mkdirSync(path.dirname(hook), { recursive: true });
			fs.symlinkSync(projectGateAsset, hook);
			const command = "printf activated > activated.txt; printf '{\"summary\":\"done\",\"statePatch\":[]}\\n'";
			const run = makeNativeRunner(
				{ providers: { test: { bin: "/bin/sh", args: ["-c", command] } } },
				1,
			);
			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);

			expect(result.exitCode).toBe(2);
			expect(result.stderr).toContain("protected-path gate");
			expect(result.stderr).toContain("symlink");
			expect(run.getModelCalls()).toBe(0);
			expect(fs.existsSync(marker)).toBe(false);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("Claude route keeps its project-hook distinction and does not require the runner gate", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-claude-hook-"));
		const temporaryRoot = path.join(cwd, "tmp");
		const runtimeRecord = path.join(cwd, "claude-xdg-runtime.txt");
		const previousTemp = process.env.TMPDIR;
		try {
			fs.mkdirSync(temporaryRoot);
			process.env.TMPDIR = temporaryRoot;
			const cli = path.join(cwd, "claude");
			fs.writeFileSync(
				cli,
				`#!/bin/sh\nprintf '%s' "$XDG_RUNTIME_DIR" > '${runtimeRecord}'\n`
					+ "printf '{\"summary\":\"claude\",\"statePatch\":[]}\\n'\n",
				{ mode: 0o700 },
			);
			const run = makeNativeRunner({ providers: { anthropic: { bin: "claude", args: [] } } }, 1);
			const result = await run(
				["native-controller", "launch", "--model", "anthropic/model-1", "-p", "do work"],
				{ cwd, env: { PATH: cwd } },
			);

			expect(result.exitCode).toBe(0);
			expect(result.stdout).toContain('"summary":"claude"');
			expect(run.getModelCalls()).toBe(1);
			const runtime = fs.readFileSync(runtimeRecord, "utf8");
			expect(path.dirname(runtime)).toBe(temporaryRoot);
			expect(path.basename(runtime).startsWith("general-agent-node-gate-")).toBeTrue();
			expect(fs.existsSync(runtime)).toBeFalse();
			expect(fs.readdirSync(temporaryRoot)).toHaveLength(0);
		} finally {
			if (previousTemp === undefined) delete process.env.TMPDIR;
			else process.env.TMPDIR = previousTemp;
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("Claude hook distinction follows the semantic native provider when the binary is wrapped", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-claude-wrapper-"));
		try {
			const cli = path.join(cwd, "wrapped-claude");
			fs.writeFileSync(cli, "#!/bin/sh\nprintf '{\"summary\":\"wrapped claude\",\"statePatch\":[]}\\n'\n", { mode: 0o700 });
			const run = makeNativeRunner({
				providers: { anthropic: { bin: cli, nativeProviderFamily: "anthropic", args: [] } },
			}, 1);
			const result = await run(
				["native-controller", "launch", "--model", "anthropic/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);

			expect(result.exitCode).toBe(0);
			expect(result.stdout).toContain('"summary":"wrapped claude"');
			expect(run.getModelCalls()).toBe(1);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("project-installed native gate restores protected create modify and delete while preserving ordinary edits", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-project-gate-"));
		const hook = path.join(cwd, ".claude/hooks/protect_paths.py");
		const review = path.join(cwd, ".pm/reviews/existing.md");
		const verdict = path.join(cwd, ".pm/verdicts/deleted.md");
		const proof = path.join(cwd, ".pm/proofs/created.json");
		const ordinary = path.join(cwd, "ordinary.txt");
		const trace = path.join(cwd, "native-calls.jsonl");
		const temporaryRoot = path.join(cwd, "tmp");
		const previousTemp = process.env.TMPDIR;
		try {
			fs.mkdirSync(path.dirname(hook), { recursive: true });
			fs.copyFileSync(projectGateAsset, hook);
			fs.mkdirSync(path.dirname(review), { recursive: true });
			fs.mkdirSync(path.dirname(verdict), { recursive: true });
			fs.mkdirSync(path.dirname(proof), { recursive: true });
			fs.writeFileSync(review, "trusted review\n");
			fs.writeFileSync(verdict, "trusted verdict\n");
			fs.writeFileSync(ordinary, "before\n");
			fs.mkdirSync(temporaryRoot);
			process.env.TMPDIR = temporaryRoot;
			const command = [
				"printf 'forged review\\n' > .pm/reviews/existing.md",
				"rm .pm/verdicts/deleted.md",
				"printf '{}\\n' > .pm/proofs/created.json",
				"printf 'after\\n' > ordinary.txt",
				"printf '{\"summary\":\"done\",\"statePatch\":[]}\\n'",
			].join("; ");
			const run = makeNativeRunner(
				{ providers: { test: { bin: "/bin/sh", args: ["-c", command] } } },
				1,
				trace,
			);
			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);

			expect(result.exitCode).toBe(2);
			expect(result.stdout).toContain('"summary":"done"');
			expect(result.stderr).toContain("unauthorized change to protected file(s) was reverted");
			expect(result.stderr).toContain(".pm/reviews/existing.md");
			expect(result.stderr).toContain(".pm/verdicts/deleted.md");
			expect(result.stderr).toContain(".pm/proofs/created.json");
			expect(fs.readFileSync(review, "utf8")).toBe("trusted review\n");
			expect(fs.readFileSync(verdict, "utf8")).toBe("trusted verdict\n");
			expect(fs.existsSync(proof)).toBe(false);
			expect(fs.readFileSync(ordinary, "utf8")).toBe("after\n");
			const traceRecord = JSON.parse(fs.readFileSync(trace, "utf8"));
			expect(traceRecord.protectedPathStateSha256).toMatch(/^[0-9a-f]{64}$/u);
			expect(fs.readdirSync(temporaryRoot)).toHaveLength(0);
		} finally {
			if (previousTemp === undefined) delete process.env.TMPDIR;
			else process.env.TMPDIR = previousTemp;
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("runner-frozen gate snapshot survives native-node deletion attempts", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-snapshot-tamper-"));
		const hook = path.join(cwd, ".claude/hooks/protect_paths.py");
		const review = path.join(cwd, ".pm/reviews/existing.md");
		const temporaryRoot = path.join(cwd, "tmp");
		const previousTemp = process.env.TMPDIR;
		try {
			fs.mkdirSync(path.dirname(hook), { recursive: true });
			fs.copyFileSync(projectGateAsset, hook);
			fs.mkdirSync(path.dirname(review), { recursive: true });
			fs.mkdirSync(temporaryRoot, { recursive: true });
			fs.writeFileSync(review, "trusted review\n");
			process.env.TMPDIR = temporaryRoot;
			const command = [
				"find \"$XDG_RUNTIME_DIR/general-agent-gate\" -maxdepth 1 -type f -name 'gate-node-*.json' -delete",
				"printf 'forged review\\n' > .pm/reviews/existing.md",
				"printf '{\"summary\":\"done\",\"statePatch\":[]}\\n'",
			].join("; ");
			const run = makeNativeRunner({ providers: { test: { bin: "/bin/sh", args: ["-c", command] } } }, 1);
			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);

			expect(result.exitCode).toBe(2);
			expect(result.stderr).toContain("frozen audit state was modified and restored");
			expect(fs.readFileSync(review, "utf8")).toBe("trusted review\n");
		} finally {
			if (previousTemp === undefined) delete process.env.TMPDIR;
			else process.env.TMPDIR = previousTemp;
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("frozen helper restoration never follows a node-created symlink", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-helper-symlink-"));
		const hook = path.join(cwd, ".claude/hooks/protect_paths.py");
		const target = path.join(cwd, "ordinary-target.txt");
		const previousTemp = process.env.TMPDIR;
		try {
			fs.mkdirSync(path.dirname(hook), { recursive: true });
			fs.copyFileSync(projectGateAsset, hook);
			fs.writeFileSync(target, "ordinary bytes\n");
			process.env.TMPDIR = path.join(cwd, "tmp");
			fs.mkdirSync(process.env.TMPDIR);
			const command = [
				"helper=\"$XDG_RUNTIME_DIR/protect_paths.py\"",
				"rm \"$helper\"",
				"ln -s \"$PWD/ordinary-target.txt\" \"$helper\"",
				"printf '{\"summary\":\"done\",\"statePatch\":[]}\\n'",
			].join("; ");
			const run = makeNativeRunner({ providers: { test: { bin: "/bin/sh", args: ["-c", command] } } }, 1);
			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);

			expect(result.exitCode).toBe(2);
			expect(result.stderr).toContain("frozen audit helper was modified and restored");
			expect(fs.readFileSync(target, "utf8")).toBe("ordinary bytes\n");
			expect(fs.readdirSync(process.env.TMPDIR)).toHaveLength(0);
		} finally {
			if (previousTemp === undefined) delete process.env.TMPDIR;
			else process.env.TMPDIR = previousTemp;
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("deleting the whole visible gate directory still restores protected bytes", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-gate-dir-delete-"));
		const hook = path.join(cwd, ".claude/hooks/protect_paths.py");
		const review = path.join(cwd, ".pm/reviews/existing.md");
		const previousTemp = process.env.TMPDIR;
		try {
			fs.mkdirSync(path.dirname(hook), { recursive: true });
			fs.copyFileSync(projectGateAsset, hook);
			fs.mkdirSync(path.dirname(review), { recursive: true });
			fs.writeFileSync(review, "trusted review\n");
			process.env.TMPDIR = path.join(cwd, "tmp");
			fs.mkdirSync(process.env.TMPDIR);
			const command = [
				"gate_dir=\"$XDG_RUNTIME_DIR\"",
				"find \"$gate_dir\" -depth -delete",
				"printf 'forged review\\n' > .pm/reviews/existing.md",
				"printf '{\"summary\":\"done\",\"statePatch\":[]}\\n'",
			].join("; ");
			const run = makeNativeRunner({ providers: { test: { bin: "/bin/sh", args: ["-c", command] } } }, 1);
			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);

			expect(result.exitCode).toBe(2);
			expect(result.stderr).toContain("frozen audit helper was modified and restored");
			expect(result.stderr).toContain("frozen audit state was modified and restored");
			expect(result.stderr).toContain("unauthorized change to protected file(s) was reverted");
			expect(fs.readFileSync(review, "utf8")).toBe("trusted review\n");
			expect(fs.readdirSync(process.env.TMPDIR)).toHaveLength(0);
		} finally {
			if (previousTemp === undefined) delete process.env.TMPDIR;
			else process.env.TMPDIR = previousTemp;
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("runner lifetime keeps the initially resolved gate after an earlier node tampers with the project hook", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-gate-lifetime-"));
		const hook = path.join(cwd, ".claude/hooks/protect_paths.py");
		const review = path.join(cwd, ".pm/reviews/existing.md");
		try {
			fs.mkdirSync(path.dirname(hook), { recursive: true });
			fs.copyFileSync(projectGateAsset, hook);
			fs.mkdirSync(path.dirname(review), { recursive: true });
			fs.writeFileSync(review, "trusted review\n");
			const command = [
				"case \"$1\" in",
				"  *first*) printf '# node-pre node-post\\n' > .claude/hooks/protect_paths.py; printf '{\"summary\":\"first\",\"statePatch\":[]}\\n' ;;",
				"  *) printf 'forged review\\n' > .pm/reviews/existing.md; printf '{\"summary\":\"second\",\"statePatch\":[]}\\n' ;;",
				"esac",
			].join("\n");
			const run = makeNativeRunner({
				providers: {
					test: {
						bin: "/bin/sh",
						args: ["-c", command, "sh", "{prompt}"],
					},
				},
			}, 2);
			expect((await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "first"],
				{ cwd, env: {} },
			)).exitCode).toBe(0);

			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "second"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(2);
			expect(fs.readFileSync(review, "utf8")).toBe("trusted review\n");
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});

	test("fails closed when the mapped native CLI is unavailable", async () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "native-runner-missing-cli-"));
		try {
			const run = makeNativeRunner(
				{ providers: { test: { bin: path.join(cwd, "does-not-exist"), args: [] } } },
				1,
			);
			const result = await run(
				["native-controller", "launch", "--model", "test/model-1", "-p", "do work"],
				{ cwd, env: {} },
			);
			expect(result.exitCode).toBe(2);
			expect(result.stderr).toContain("launch authorization failed");
			expect(result.stderr).toContain("closure path is unavailable");
			expect(result.stderr).toContain("No model fallback");
			expect(run.getModelCalls()).toBe(0);
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});
});
