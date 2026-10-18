/**
 * grind — cross-platform anti-thrash gate (simplified from grind_gate.py).
 * Counts consecutive failures of the SAME bash command signature via
 * tool_result; once the threshold is hit, further identical attempts are
 * paused with the grind skill's escalation ladder (L1 narrow → L2 isolate →
 * L3 instrument → L4 reframe). After becoming aware, the agent may deliberately retry one
 * command with GENERAL_AGENT_REPEAT_FAILED_COMMAND=1.
 * State: .pm/.grind_state.json (runtime, gitignored).
 */
import * as fs from "node:fs";
import * as path from "node:path";

const THRESHOLD = 3;
const REPEAT_ACK = "GENERAL_AGENT_REPEAT_FAILED_COMMAND";

type GrindState = { sig: string; count: number };

function sigOf(cmd: string): string {
	// Peel common wrappers so retries-with-decoration still match.
	let c = cmd.trim();
	c = c.replace(/^(?:env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)+/, "");
	c = c.replace(/^(timeout\s+\S+\s+|nice\s+(-n\s*\d+\s+)?)/g, "");
	c = c.replace(/\s+/g, " ");
	return c.slice(0, 160);
}

function repeatAcknowledged(cmd: string): boolean {
	if (/^(?:\s*env\s+)?(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*GENERAL_AGENT_REPEAT_FAILED_COMMAND=(?:1|true|yes)\b/i.test(cmd)) {
		return true;
	}
	return /^(1|true|yes)$/i.test(process.env[REPEAT_ACK] ?? "");
}

function statePath(cwd: string): string {
	return path.join(cwd, ".pm", ".grind_state.json");
}

function readState(cwd: string): GrindState | null {
	try {
		return JSON.parse(fs.readFileSync(statePath(cwd), "utf8"));
	} catch {
		return null;
	}
}

function writeState(cwd: string, s: GrindState | null): void {
	try {
		fs.mkdirSync(path.join(cwd, ".pm"), { recursive: true });
		if (s === null) {
			fs.rmSync(statePath(cwd), { force: true });
		} else {
			fs.writeFileSync(statePath(cwd), JSON.stringify(s));
		}
	} catch {
		/* state is best-effort */
	}
}

export default function (pi: any) {
	pi.on("tool_call", (ev: any, ctx: any) => {
		if (ev?.toolName !== "bash") return undefined;
		const cmd = ev?.input?.command;
		if (typeof cmd !== "string") return undefined;
		const cwd: string = ctx?.cwd ?? process.cwd();
		const st = readState(cwd);
		if (st && st.sig === sigOf(cmd) && st.count >= THRESHOLD && !repeatAcknowledged(cmd)) {
			return {
				block: true,
					reason:
						`AWARENESS REQUIRED by the grind gate: this exact command has failed ${st.count} times in a row. ` +
						`Apply the grind ladder before deciding whether the same retry is justified: ` +
						`L1 narrow the repro; L2 isolate the failing unit in a scratch file; ` +
						`L3 instrument (add logging/asserts) and read the actual state; ` +
						`L4 reframe — question the assumption that made you expect this to work. If the evidence ` +
						`still supports an identical retry, rerun that one command with ` +
						`GENERAL_AGENT_REPEAT_FAILED_COMMAND=1. Changing the command or succeeding resets the counter.`,
			};
		}
		return undefined;
	});

	pi.on("tool_result", (ev: any, ctx: any) => {
		if (ev?.toolName !== "bash") return undefined;
		const cmd = ev?.input?.command;
		if (typeof cmd !== "string") return undefined;
		const cwd: string = ctx?.cwd ?? process.cwd();
		const sig = sigOf(cmd);
		const st = readState(cwd);
		if (ev?.isError) {
			const count = st && st.sig === sig ? st.count + 1 : 1;
			writeState(cwd, { sig, count });
		} else if (st && st.sig === sig) {
			writeState(cwd, null); // success clears the streak
		}
		return undefined;
	});
}
