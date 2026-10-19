import { describe, expect, test } from "bun:test";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import grind from "../../../assets/project/.omp/hooks/pre/grind";

function handlers(extension: (pi: any) => void) {
	const registered: Record<string, (event: any, context: any) => any> = {};
	extension({
		on: (name: string, handler: (event: any, context: any) => any) => {
			registered[name] = handler;
		},
	});
	return registered;
}

describe("awareness-based governance controls", () => {
	test("repeating a failed command requires but accepts a scoped acknowledgement", () => {
		const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "grind-awareness-"));
		try {
			const h = handlers(grind);
			const event = { toolName: "bash", input: { command: "false" }, isError: true };
			for (let i = 0; i < 3; i += 1) h.tool_result(event, { cwd });
			const blocked = h.tool_call({ toolName: "bash", input: { command: "false" } }, { cwd });
			expect(blocked?.block).toBe(true);
			expect(blocked?.reason).toContain("GENERAL_AGENT_REPEAT_FAILED_COMMAND=1");
			expect(
				h.tool_call(
					{
						toolName: "bash",
						input: { command: "GENERAL_AGENT_REPEAT_FAILED_COMMAND=1 false" },
					},
					{ cwd },
				),
			).toBeUndefined();
		} finally {
			fs.rmSync(cwd, { recursive: true, force: true });
		}
	});
});
