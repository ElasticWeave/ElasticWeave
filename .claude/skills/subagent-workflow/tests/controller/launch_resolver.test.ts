import { afterEach, describe, expect, test } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const resolverModule = path.resolve(import.meta.dir, "../../scripts/controller/launch_resolver.ts");
const temporary: string[] = [];

afterEach(() => {
	for (const root of temporary.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

function runModule(source: string, env: Record<string, string> = {}): { exitCode: number; stdout: string; stderr: string } {
	const result = Bun.spawnSync({
		cmd: [process.execPath, "-e", source],
		env: { ...process.env, ...env },
	});
	return {
		exitCode: result.exitCode,
		stdout: result.stdout.toString(),
		stderr: result.stderr.toString(),
	};
}

describe("bound launch resolver", () => {
	test("scrubs the exact resolver subprocess environment and pins account paths", () => {
		const result = runModule(
			`import { scrubbedResolverEnv } from ${JSON.stringify(resolverModule)}; console.log(JSON.stringify(scrubbedResolverEnv()));`,
			{
				HOME: "/tmp/attacker-home",
				PATH: "/tmp/attacker-bin",
				PYTHONPATH: "/tmp/attacker-python",
				BASH_ENV: "/tmp/attacker-bash-env",
				CURSOR_INVOKED_AS: "attacker",
				NODE_TLS_REJECT_UNAUTHORIZED: "0",
				OPENSSL_CONF: "/tmp/attacker-openssl",
				OPENAI_BASE_URL: "https://attacker.invalid",
				OPENAI_API_KEY: "attacker-openai-key",
				OPENAI_ORG_ID: "attacker-openai-org",
				OPENAI_FUTURE_ACCOUNT_SELECTOR: "attacker-openai-future",
				ANTHROPIC_API_KEY: "attacker-key",
				CLAUDE_CONFIG_DIR: "/tmp/attacker-claude-config",
				CODEX_HOME: "/tmp/attacker-codex-home",
				CODEX_API_KEY: "attacker-codex-key",
				CODEX_SESSION: "attacker-codex-session",
				CURSOR_API_KEY: "attacker-cursor-key",
				CURSOR_AUTH_TOKEN: "attacker-cursor-token",
				CURSOR_FUTURE_ACCOUNT_SELECTOR: "attacker-cursor-future",
				XDG_CONFIG_HOME: "/tmp/attacker-config",
				XDG_DATA_HOME: "/tmp/attacker-data",
				XDG_RUNTIME_DIR: "/tmp/attacker-runtime",
			},
		);
		expect(result.exitCode).toBe(0);
		const env = JSON.parse(result.stdout);
		expect(env.HOME).toBe(os.userInfo().homedir);
		expect(env.PATH).toBe([
			path.join(os.userInfo().homedir, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin",
		].join(path.delimiter));
		expect(env.PYTHONPATH).toBeUndefined();
		expect(env.BASH_ENV).toBeUndefined();
		expect(env.CURSOR_INVOKED_AS).toBeUndefined();
		expect(env.NODE_TLS_REJECT_UNAUTHORIZED).toBeUndefined();
		expect(env.OPENSSL_CONF).toBeUndefined();
		expect(env.OPENAI_BASE_URL).toBeUndefined();
		expect(env.OPENAI_API_KEY).toBeUndefined();
		expect(env.OPENAI_ORG_ID).toBeUndefined();
		expect(env.ANTHROPIC_API_KEY).toBeUndefined();
		expect(env.CLAUDE_CONFIG_DIR).toBeUndefined();
		expect(env.XDG_RUNTIME_DIR).toBeUndefined();
		expect(env.CODEX_HOME).toBe(path.join(os.userInfo().homedir, ".codex"));
		expect(env.CODEX_API_KEY).toBeUndefined();
		expect(env.CODEX_SESSION).toBeUndefined();
		expect(env.CURSOR_API_KEY).toBeUndefined();
		expect(env.CURSOR_AUTH_TOKEN).toBeUndefined();
		expect(env.CURSOR_FUTURE_ACCOUNT_SELECTOR).toBeUndefined();
		expect(Object.keys(env).filter(key => key.startsWith("OPENAI_"))).toEqual([]);
		expect(Object.keys(env).filter(key => key.startsWith("CODEX_"))).toEqual(["CODEX_HOME"]);
		expect(Object.keys(env).filter(key => key.startsWith("CURSOR_"))).toEqual([]);
		expect(env.XDG_CONFIG_HOME).toBe(path.join(os.userInfo().homedir, ".config"));
		expect(env.XDG_DATA_HOME).toBe(path.join(os.userInfo().homedir, ".local", "share"));
	});

	test("binds a fixed absolute Python interpreter independent of poisoned PATH", () => {
		const result = runModule(
			`import { boundPythonInterpreter } from ${JSON.stringify(resolverModule)}; console.log(JSON.stringify(boundPythonInterpreter()));`,
			{ PATH: "/tmp/attacker-bin", HOME: "/tmp/attacker-home" },
		);
		expect(result.exitCode).toBe(0);
		const binding = JSON.parse(result.stdout);
		expect(path.isAbsolute(binding.path)).toBeTrue();
		expect(binding.path).not.toContain("attacker");
		expect(binding.sha256).toMatch(/^[0-9a-f]{64}$/u);
	});

	test("detects interpreter, resolver, or policy byte drift through the shared no-follow binding", () => {
		const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "launch-resolver-binding-")));
		temporary.push(root);
		const file = path.join(root, "bound-input");
		fs.writeFileSync(file, "version-one\n");
		const result = runModule([
			`import fs from "node:fs"`,
			`import { bindFileBytes, verifyBoundFile } from ${JSON.stringify(resolverModule)}`,
			`const binding = bindFileBytes(${JSON.stringify(file)}, "resolver input").binding`,
			`fs.writeFileSync(${JSON.stringify(file)}, "version-two\\n")`,
			`try { verifyBoundFile(binding, "resolver input"); process.exit(9) }`,
			`catch (error) { console.error(String(error)); process.exit(0) }`,
		].join("\n"));
		expect(result.exitCode).toBe(0);
		expect(result.stderr).toContain("changed after it was bound");
	});

	test("boots the bound resolver in isolated Python mode without consulting a native catalog for a rejected role", () => {
		const projectRoot = path.resolve(import.meta.dir, "../../../../..");
		const models = path.join(projectRoot, ".claude/skills/default-mode/assets/project/config/workflows/models.json");
		const result = runModule([
			`import fs from "node:fs"`,
			`import { createHash } from "node:crypto"`,
			`import { createBoundLaunchResolver } from ${JSON.stringify(resolverModule)}`,
			`const models = ${JSON.stringify(models)}`,
			`const resolver = createBoundLaunchResolver(${JSON.stringify(projectRoot)}, { path: models, sha256: createHash("sha256").update(fs.readFileSync(models)).digest("hex") })`,
			`try { resolver({ taskClass: "mechanical", reasoningPosture: "frontier", semanticRole: "does-not-exist", reviewMode: "none", writerRole: null }); process.exit(9) }`,
			`catch (error) { console.error(String(error)); process.exit(0) }`,
		].join("\n"), { HOME: "/tmp/attacker-home", PATH: "/tmp/attacker-bin" });
		expect(result.exitCode).toBe(0);
		expect(result.stderr).toContain("unknown semantic role");
	});

	test("contains no production resolver override controlled by CLI, environment, or exported mutable state", () => {
		const source = fs.readFileSync(resolverModule, "utf8");
		expect(source).not.toMatch(/process\.env\.(?:LAUNCH|RESOLVER)|launchResolverOverride|LAUNCH_RESOLVER_TEST|test.?seam/iu);
		expect(source).toContain("verifyBoundFile(interpreter");
		expect(source).toContain("verifyBoundFile(resolver");
		expect(source).toContain("verifyBoundFile(models");
		expect(source).toContain('"-I"');
		const result = runModule(
			`const module = await import(${JSON.stringify(resolverModule)}); console.log(JSON.stringify(Object.keys(module).sort()));`,
		);
		expect(result.exitCode).toBe(0);
		expect(JSON.parse(result.stdout)).toEqual([
			"bindFileBytes", "boundPythonInterpreter", "createBoundLaunchResolver", "scrubbedResolverEnv", "verifyBoundFile",
		]);
	});
});
