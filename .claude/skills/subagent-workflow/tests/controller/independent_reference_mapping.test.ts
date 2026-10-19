import { describe, expect, test } from "bun:test";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const projectRoot = path.resolve(import.meta.dir, "../../assets/project");

function readJson(relative: string): Record<string, any> {
	return JSON.parse(fs.readFileSync(path.join(projectRoot, relative), "utf8"));
}

function sha256(relative: string): string {
	return createHash("sha256")
		.update(fs.readFileSync(path.join(projectRoot, relative)))
		.digest("hex");
}

describe("independent reference mappings", () => {
	for (const id of ["subagent-scheduler-smoke", "subagent-long-node-probe"]) {
		test(`${id} binds its migrated independent-review design`, () => {
			const designRelative = `.workflow/designs/${id}.workflow.json`;
			const mappingRelative = `.subagent/flows/${id}.json`;
			const design = readJson(designRelative);
			const mapping = readJson(mappingRelative);

			expect(design.governance).toMatchObject({
				reviewPolicy: "independent",
				completionProof: "none",
			});
			expect(mapping.designPath).toBe(designRelative);
			expect(mapping.designSha256).toBe(sha256(designRelative));

			const report = fs.readFileSync(
				path.join(projectRoot, `.workflow/mappings/${id}.subagent.md`),
				"utf8",
			);
			expect(report).toContain(mapping.designSha256);
			expect(report).toContain("independent");
			expect(report).toMatch(/predate(?:s)?\s+the\s+migration/u);
		});
	}
});
