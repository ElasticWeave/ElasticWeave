import { mock } from "bun:test";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { scrubEnv } from "../../../scripts/native/run";

export type TestJson = Record<string, any>;

export interface TestLaunchRequest {
	taskClass: string;
	reasoningPosture: string;
	semanticRole: string;
	reviewMode: string;
	writerRole: string | null;
}

type TestResolver = (request: TestLaunchRequest) => TestJson;

let activeResolver: TestResolver | null = null;

function canonical(value: unknown): string {
	if (value === null) return "null";
	if (typeof value === "string" || typeof value === "boolean" || typeof value === "number") return JSON.stringify(value);
	if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
	if (value && typeof value === "object") {
		const object = value as TestJson;
		return `{${Object.keys(object).sort().map(key => `${JSON.stringify(key)}:${canonical(object[key])}`).join(",")}}`;
	}
	throw new Error(`canonical JSON rejects ${typeof value}`);
}

function sha256(value: string | Buffer): string {
	return createHash("sha256").update(value).digest("hex");
}

function sealReceipt(value: TestJson): TestJson {
	const { receiptSha256: _oldDigest, ...withoutDigest } = value;
	return { ...withoutDigest, receiptSha256: `sha256:${sha256(canonical(withoutDigest))}` };
}

function testNativeExecutable(root: string, backend: string, logicalExecutable: string): string {
	const candidates = [
		path.join(root, "native-map.json"),
		path.join(root, ".claude/skills/subagent-workflow/scripts/native/native-map.json"),
	];
	for (const candidate of candidates) {
		if (!fs.existsSync(candidate)) continue;
		const nativeMap = JSON.parse(fs.readFileSync(candidate, "utf8")) as TestJson;
		const route = backend === "claude" ? "anthropic" : backend;
		const configured = nativeMap.providers?.[route]?.bin;
		if (typeof configured !== "string" || !configured) continue;
		if (path.isAbsolute(configured)) return fs.realpathSync(configured);
		for (const directory of (process.env.PATH ?? "").split(path.delimiter).filter(Boolean)) {
			const executable = path.join(directory, configured);
			try {
				fs.accessSync(executable, fs.constants.X_OK);
				return fs.realpathSync(executable);
			} catch {
				// Continue through the test process PATH.
			}
		}
	}
	throw new Error(`test launch resolver cannot bind ${backend} executable ${logicalExecutable}`);
}

function deterministicResolver(root: string, modelsPath: string): TestResolver {
	return request => {
		const raw = fs.readFileSync(modelsPath);
		const document = JSON.parse(raw.toString("utf8")) as TestJson;
		const roles = document.roles as TestJson;
		const identity = (name: string): TestJson => {
			const role = roles[name] as TestJson;
			const catalogModelId = role.backend === "codex" || role.backend === "claude"
				? role.model
				: String(role.model).startsWith("claude-")
					? `${role.model}-thinking-${role.effort}`
					: `${role.model}-${role.effort}`;
			return {
				semanticRole: name,
				backend: role.backend,
				provider: role.provider,
				upstreamProviderFamily: role.upstreamProviderFamily,
				model: role.model,
				modelFamily: role.modelFamily,
				effort: role.effort,
				intelligenceClass: role.intelligenceClass,
				dispatchStatus: role.dispatchStatus,
				catalogModelId,
			};
		};
		const resolved = identity(request.semanticRole);
		const writer = request.writerRole ? identity(request.writerRole) : null;
		const identities = [resolved, ...(writer ? [writer] : [])];
		const evidence = Object.fromEntries([...new Set(identities.map(item => item.backend))].sort().map(backend => {
			const argv = backend === "claude"
				? ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
				: backend === "codex" ? ["codex", "debug", "models"] : ["cursor-agent", "models"];
			const executable = testNativeExecutable(root, backend, argv[0]);
			const executableSha256 = `sha256:${sha256(fs.readFileSync(executable))}`;
			const scripted = fs.readFileSync(executable).subarray(0, 2).toString() === "#!";
			const interpreter = scripted ? fs.realpathSync("/bin/sh") : undefined;
			const closure = [
				...(interpreter ? [{
					role: "interpreter", path: interpreter,
					sha256: `sha256:${sha256(fs.readFileSync(interpreter))}`,
				}] : []),
				{ role: "entrypoint", path: executable, sha256: executableSha256 },
			];
			const covered = identities.filter(item => item.backend === backend);
			const coverage: TestJson = backend === "cursor" ? { __cursor_ids__: [] } : {};
			for (const item of covered) {
				if (backend === "codex" || backend === "claude") {
					const efforts = new Set<string>(coverage[item.model] ?? []);
					efforts.add(item.effort);
					coverage[item.model] = [...efforts];
				} else {
					coverage.__cursor_ids__.push(item.catalogModelId);
				}
			}
			if (backend === "cursor") coverage.__cursor_ids__ = [...new Set(coverage.__cursor_ids__)];
			return [backend, {
				backend,
				command: {
					argv,
					executable,
					executableSha256,
					invokedArgv: [...(interpreter ? [interpreter] : []), executable, ...argv.slice(1)],
					executionClosure: closure,
					executionClosureSha256: `sha256:${sha256(
						closure.map(item => `${item.role}\t${item.path}\t${item.sha256}`).join("\n"),
					)}`,
				},
				catalogBytesSha256: `sha256:${sha256(`${backend}:catalog`)}`,
				exitCode: 0,
				stderrSha256: `sha256:${sha256("")}`,
				coverage,
			}];
		}));
		return sealReceipt({
			schema: "workflow-native-launch-receipt/v1",
			request: {
				taskClass: request.taskClass,
				reasoningPosture: request.reasoningPosture,
				semanticRole: request.semanticRole,
				writerRole: request.writerRole,
				reviewMode: request.reviewMode,
			},
			resolved,
			writer,
			policy: {
				modelsSchema: document.schema,
				modelsDocumentSha256: `sha256:${sha256(raw)}`,
				catalogResolutionSha256: `sha256:${sha256(canonical(document.catalogResolution))}`,
				capabilityPolicySha256: `sha256:${sha256(canonical(document.capabilityPolicy))}`,
				reasoningPolicySha256: `sha256:${sha256(canonical(document.reasoningPolicy))}`,
				catalogStatus: document.catalogResolution.status,
				catalogSource: "authenticated-native-cli",
				catalogEvidenceReceipt: document.catalogResolution.evidenceReceipt ?? null,
				liveCatalogAuthenticatedByResolver: true,
				freshCatalogEvidence: evidence,
			},
		});
	};
}

mock.module(path.resolve(import.meta.dir, "../../../scripts/controller/launch_resolver.ts"), () => ({
	createBoundLaunchResolver: (_root: string, models: { path: string }) => {
		const resolver = (request: TestLaunchRequest): TestJson =>
			(activeResolver ?? deterministicResolver(_root, models.path))(request);
		return Object.assign(resolver, {
			launchEnvironment: Object.freeze(scrubEnv(process.env)),
		});
	},
}));

export function setTestLaunchResolver(resolver: TestResolver): void {
	activeResolver = resolver;
}

export function resetTestLaunchResolver(): void {
	activeResolver = null;
}
