#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const RESOURCE_ROOT = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(RESOURCE_ROOT, "../../..");
const BUNDLE_ROOT = path.resolve(PROJECT_ROOT, "../../../../..");
const DESIGN_RELATIVE = ".workflow/designs/standard-round.workflow.json";
const MAPPING_RELATIVE = ".subagent/flows/standard-round.json";
const MANIFEST_RELATIVE = ".workflow/resources/standard-round/resources.json";
const INPUT_RELATIVE = ".subagent/fixtures/standard-round.input.json";
const MODEL_POLICY_RELATIVE = ".claude/skills/default-mode/assets/project/config/workflows/models.json";
// The posture is the explicit role ceiling this workflow runs under; task, plan, and advisor are
// exactly the balanced ceiling, so the mapping declares it instead of letting the runner guess.
const REASONING_POSTURE = "balanced";

const REWARD_ACTION = ".claude/skills/subagent-workflow/scripts/controller/actions/reward_evidence.ts";
const LEMMA_ACTION = ".claude/skills/subagent-workflow/scripts/controller/actions/validate_lemmas.ts";
const EVIDENCE_HELPER = ".claude/skills/subagent-workflow/scripts/native/reward_hacking_evidence.py";
const LEMMA_VALIDATOR = ".claude/skills/self-improve/scripts/validate_lemmas.py";
const EXECUTABLES = {
  "controller-runtime": { source: "controller-runtime" },
  python: { source: "path", command: "python3" },
  git: { source: "path", command: "git" },
  "native-task": { source: "native-role", role: "task" },
  "native-plan": { source: "native-role", role: "plan" },
  "native-advisor": { source: "native-role", role: "advisor" },
};
const REVIEW_COMMITMENT_MARKER = "{{review.commitment}}";
const REVIEW_COMMITMENT_ANCHOR = "WORKSPACE STATE BINDING\n{{stateBinding}}\n\n";

const PROMPTS = {
  implement: ".workflow/resources/standard-round/prompts/implement.md",
  reward: ".workflow/resources/standard-round/prompts/reward-hacking-review.md",
  cross: ".workflow/resources/standard-round/prompts/cross-review.md",
  milestone: ".workflow/resources/standard-round/prompts/milestone-review.md",
  selfEvolve: ".workflow/resources/standard-round/prompts/self-evolve.md",
  evolveReview: ".workflow/resources/standard-round/prompts/evolve-review.md",
};

function absolute(relative) {
  const resolved = path.resolve(PROJECT_ROOT, relative);
  const within = path.relative(PROJECT_ROOT, resolved);
  if (!within || within.startsWith("..") || path.isAbsolute(within)) {
    throw new Error(`project resource escapes root: ${relative}`);
  }
  return resolved;
}

function bytes(relative) {
  const projectFile = absolute(relative);
  if (fs.existsSync(projectFile)) return fs.readFileSync(projectFile);
  const bundleFile = path.resolve(BUNDLE_ROOT, relative);
  const withinBundle = path.relative(BUNDLE_ROOT, bundleFile);
  if (!withinBundle || withinBundle.startsWith("..") || path.isAbsolute(withinBundle)) {
    throw new Error(`bundle resource escapes root: ${relative}`);
  }
  return fs.readFileSync(bundleFile);
}

function text(relative) {
  return bytes(relative).toString("utf8");
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function json(relative) {
  return JSON.parse(text(relative));
}

function pretty(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function rewardCommand(nodeId, designSha, helperSha, rewardActionSha) {
  void designSha;
  void rewardActionSha;
  const common = [
    "{{executable.controller-runtime.path}}",
    REWARD_ACTION,
    "--project-root", "{{controller.projectRoot}}",
    "--run-root", "{{controller.runRoot}}",
    "--helper", `{{controller.projectRoot}}/${EVIDENCE_HELPER}`,
    "--helper-sha256", helperSha,
    "--python", "{{executable.python.path}}",
    "--python-sha256", "{{executable.python.sha256}}",
    "--git", "{{executable.git.path}}",
    "--git-sha256", "{{executable.git.sha256}}",
  ];
  if (nodeId === "capture-baseline") {
    return {
      type: "command",
      argv: [common[0], common[1], "baseline", ...common.slice(2), "--output", "artifacts/baseline.json"],
      runWrites: ["artifacts/baseline.json"],
    };
  }
  return {
    type: "command",
    argv: [
      common[0], common[1], "evidence", ...common.slice(2),
      "--baseline", "{{baselinePath}}",
      "--expected-baseline-digest", "{{baselineDigest}}",
      "--acceptance-argv-json", "{{acceptanceArgv}}",
      "--acceptance-timeout", "17400",
      "--output", `artifacts/${nodeId}.json`,
    ],
    runWrites: [`artifacts/${nodeId}.json`],
  };
}

function lemmaCommand(validatorSha) {
  return {
    type: "command",
    argv: [
      "{{executable.controller-runtime.path}}",
      LEMMA_ACTION,
      "--project-root", "{{controller.projectRoot}}",
      "--validator", `{{controller.projectRoot}}/${LEMMA_VALIDATOR}`,
      "--validator-sha256", validatorSha,
      "--python", "{{executable.python.path}}",
      "--python-sha256", "{{executable.python.sha256}}",
      "--lemma-report", "{{lemmaReport}}",
    ],
    runWrites: [],
  };
}

function withReviewCommitment(prompt) {
  const markerCount = prompt.split(REVIEW_COMMITMENT_MARKER).length - 1;
  if (markerCount === 1) return prompt;
  if (markerCount > 1) throw new Error("reward-hacking review prompt has duplicate review commitment markers");
  const anchorCount = prompt.split(REVIEW_COMMITMENT_ANCHOR).length - 1;
  if (anchorCount !== 1) {
    throw new Error("reward-hacking review prompt must contain one workspace-state binding anchor");
  }
  return prompt.replace(
    REVIEW_COMMITMENT_ANCHOR,
    `${REVIEW_COMMITMENT_ANCHOR}CONTROLLER REVIEW COMMITMENT\n${REVIEW_COMMITMENT_MARKER}\n\n`,
  );
}

function promptHandler(nodeId) {
  if (nodeId.startsWith("implement-")) return { type: "agent", prompt: text(PROMPTS.implement) };
  if (nodeId.startsWith("reward-hack-review-") || nodeId.startsWith("final-reward-hack-review-")) {
    return { type: "decision", prompt: withReviewCommitment(text(PROMPTS.reward)) };
  }
  if (nodeId.startsWith("cross-review-")) return { type: "decision", prompt: text(PROMPTS.cross) };
  if (nodeId.startsWith("milestone-review-")) return { type: "decision", prompt: text(PROMPTS.milestone) };
  if (nodeId.startsWith("self-evolve-")) return { type: "agent", prompt: text(PROMPTS.selfEvolve) };
  if (nodeId.startsWith("evolve-review-")) return { type: "decision", prompt: text(PROMPTS.evolveReview) };
  return null;
}

function handlerFor(node, digests) {
  if (node.id === "seed") {
    return {
      type: "input",
      bindings: {
        itemId: "itemId",
        task: "task",
        acceptanceArgv: "acceptanceArgv",
        rewardHackingRisks: "rewardHackingRisks",
        report: { const: "" },
        reviseCount: { const: 0 },
        evolveCount: { const: 0 },
        evolutionRetryUsed: { const: false },
        reviewFeedback: { const: "" },
        lastReviewNode: { const: "" },
        forcedReason: { const: "" },
      },
    };
  }
  if (node.id === "capture-baseline" || node.id.startsWith("capture-evidence-") || node.id.startsWith("capture-final-evidence-")) {
    return rewardCommand(node.id, digests.design, digests.helper, digests.rewardAction);
  }
  if (node.id.startsWith("validate-lemmas-")) return lemmaCommand(digests.lemmaValidator);
  const model = promptHandler(node.id);
  if (model) return model;
  if (node.id.startsWith("record-")) {
    return { type: "template", values: { lastReviewNode: node.id.slice("record-".length) } };
  }
  if (node.id.startsWith("advance-revision-")) return { type: "increment", field: "reviseCount", by: 1 };
  if (node.id.startsWith("exhaust-revision-")) {
    return {
      type: "increment",
      field: "reviseCount",
      by: 1,
      set: { forcedReason: `REVISION_CAP_REACHED:${node.id}` },
    };
  }
  if (node.id.startsWith("consume-evolution-retry-")) {
    return { type: "increment", field: "evolveCount", by: 1, set: { evolutionRetryUsed: true } };
  }
  if (node.id.startsWith("exhaust-evolution-")) {
    return {
      type: "increment",
      field: "evolveCount",
      by: 1,
      set: { forcedReason: `EVOLUTION_CAP_REACHED:${node.id}` },
    };
  }
  if (node.id === "archive-proof") {
    return { type: "template", values: { proofCandidatePath: "state-bound-candidate.json" } };
  }
  if (node.kind === "terminal") return { type: "terminal" };
  throw new Error(`no explicit handler policy for ${node.id}`);
}

function resourceManifest(design, digests) {
  const resources = [
    ["mapping-builder", ".workflow/resources/standard-round/build_mapping.mjs", "mapping construction and semantic check"],
    ["prompt-implement", PROMPTS.implement, "implementation nodes"],
    ["prompt-reward", PROMPTS.reward, "reward-hacking review nodes"],
    ["prompt-cross", PROMPTS.cross, "cross-review nodes"],
    ["prompt-milestone", PROMPTS.milestone, "milestone review nodes"],
    ["prompt-self-evolve", PROMPTS.selfEvolve, "self-evolution nodes"],
    ["prompt-evolve-review", PROMPTS.evolveReview, "self-evolution review nodes"],
    ["reward-action", REWARD_ACTION, "baseline and evidence command handler"],
    ["lemma-action", LEMMA_ACTION, "lemma validation command handler"],
    ["evidence-helper", EVIDENCE_HELPER, "mechanical baseline/evidence/current-state implementation"],
    ["lemma-validator", LEMMA_VALIDATOR, "deterministic lemma schema validator"],
  ].map(([label, resourcePath, purpose]) => ({
    label,
    path: resourcePath,
    sha256: sha256(bytes(resourcePath)),
    purpose,
  }));
  return {
    schema: "workflow-resource-closure/v1",
    workflow: design.id,
    designPath: DESIGN_RELATIVE,
    designSha256: digests.design,
    files: resources,
    externalExecutables: Object.entries(EXECUTABLES).map(([label, source]) => ({ label, ...source })),
  };
}

function inputFixture() {
  return {
    itemId: "fixture-standard-round",
    task: "Fixture only: validate the standard-round mapping without starting model work.",
    acceptanceArgv: ["/usr/bin/true"],
    rewardHackingRisks: [],
    proofBinding: {
      schema: "subagent-proof-binding/v1",
      scope: "no-pm",
      itemId: "fixture-standard-round",
      roleModelPolicyDigest: sha256(bytes(MODEL_POLICY_RELATIVE)),
      reason: "Bundled mapping validation fixture; no PM completion or status transition is authorized.",
    },
  };
}

function build() {
  const design = json(DESIGN_RELATIVE);
  const digests = {
    design: sha256(bytes(DESIGN_RELATIVE)),
    helper: sha256(bytes(EVIDENCE_HELPER)),
    rewardAction: sha256(bytes(REWARD_ACTION)),
    lemmaAction: sha256(bytes(LEMMA_ACTION)),
    lemmaValidator: sha256(bytes(LEMMA_VALIDATOR)),
  };
  const handlers = Object.fromEntries(design.nodes.map(node => [node.id, handlerFor(node, digests)]));
  const finalReviewNodes = design.transitions
    .filter(edge => edge.to === "archive-proof" && edge.when?.equals === "PASS")
    .map(edge => edge.from);
  const evidenceProducerNodes = finalReviewNodes.map(reviewNodeId => {
    const producers = design.transitions
      .filter(edge => edge.to === reviewNodeId && edge.from.startsWith("capture-final-evidence-"))
      .map(edge => edge.from);
    if (producers.length !== 1) {
      throw new Error(`final review ${reviewNodeId} must have exactly one direct final-evidence producer`);
    }
    return producers[0];
  });
  const proofResources = {
    "mapping-builder": ".workflow/resources/standard-round/build_mapping.mjs",
    "prompt-implement": PROMPTS.implement,
    "prompt-reward": PROMPTS.reward,
    "prompt-cross": PROMPTS.cross,
    "prompt-milestone": PROMPTS.milestone,
    "prompt-self-evolve": PROMPTS.selfEvolve,
    "prompt-evolve-review": PROMPTS.evolveReview,
    "lemma-action": LEMMA_ACTION,
    "lemma-validator": LEMMA_VALIDATOR,
  };
  const mapping = {
    schema: "subagent-workflow/v1",
    designPath: DESIGN_RELATIVE,
    designSha256: digests.design,
    inputPath: INPUT_RELATIVE,
    reasoningPosture: REASONING_POSTURE,
    scheduler: {
      role: "task",
      mode: "ambiguous-only",
      maxCalls: 0,
      timeoutSeconds: 7200,
      reads: [],
    },
    proof: {
      itemIdField: "itemId",
      evidencePathField: "evidencePath",
      evidenceDigestField: "evidenceDigest",
      helperPath: EVIDENCE_HELPER,
      resourcesManifestPath: MANIFEST_RELATIVE,
      resources: proofResources,
      finalReviewNodes,
      evidenceProducerNodes,
      currentStateTimeoutSeconds: 7200,
      actionPath: REWARD_ACTION,
      executables: EXECUTABLES,
    },
    handlers,
  };
  return { design, mapping, manifest: resourceManifest(design, digests), input: inputFixture(), digests };
}

function compare(relative, expected) {
  const actual = text(relative);
  if (actual !== expected) throw new Error(`${relative} is stale; run build_mapping.mjs --write`);
}

function semanticChecks({ design, mapping, manifest, input, digests }) {
  if (mapping.designSha256 !== digests.design) throw new Error("mapping design digest is stale");
  if (mapping.scheduler.mode !== "ambiguous-only" || mapping.scheduler.maxCalls !== 0) {
    throw new Error("standard-round scheduler must be ambiguous-only with zero calls");
  }
  const expectedExecutables = JSON.stringify(EXECUTABLES);
  if (JSON.stringify(mapping.proof.executables) !== expectedExecutables) {
    throw new Error("proof executable sources differ from the portable runtime contract");
  }
  if (JSON.stringify(manifest.externalExecutables) !== JSON.stringify(
    Object.entries(EXECUTABLES).map(([label, source]) => ({ label, ...source })),
  )) {
    throw new Error("resource manifest executable sources differ from the portable runtime contract");
  }
  const roles = new Set(design.nodes.filter(node => node.role).map(node => node.role));
  if ([...roles].some(role => !["task", "plan", "advisor"].includes(role))) {
    throw new Error(`mapping design uses an unexpected role: ${[...roles].join(",")}`);
  }
  if (mapping.reasoningPosture !== REASONING_POSTURE) {
    throw new Error(`mapping must declare the ${REASONING_POSTURE} reasoning posture`);
  }
  const designIds = design.nodes.map(node => node.id);
  if (Object.keys(mapping.handlers).join("\n") !== designIds.join("\n")) {
    throw new Error("mapping handlers do not cover design nodes exactly and in order");
  }
  for (const node of design.nodes.filter(node => ["agent", "decision"].includes(node.kind))) {
    if (!Number.isFinite(node.timeoutSeconds) || node.timeoutSeconds <= 600) {
      throw new Error(`model node ${node.id} must have an explicit timeout above 600 seconds`);
    }
  }
  for (const node of design.nodes) {
    const handler = mapping.handlers[node.id];
    if (node.kind === "agent" && handler.type !== "agent") throw new Error(`${node.id} lost agent semantics`);
    if (node.kind === "decision" && handler.type !== "decision") throw new Error(`${node.id} lost decision semantics`);
    if (node.kind === "terminal" && handler.type !== "terminal") throw new Error(`${node.id} lost terminal semantics`);
  }
  for (const [nodeId, handler] of Object.entries(mapping.handlers)) {
    if (handler.type !== "command") continue;
    if (handler.argv[0] !== "{{executable.controller-runtime.path}}") {
      throw new Error(`command ${nodeId} does not use the resolved controller runtime`);
    }
    const argvText = handler.argv.join("\n");
    for (const placeholder of [
      "{{executable.python.path}}",
      "{{executable.python.sha256}}",
    ]) {
      if (!argvText.includes(placeholder)) throw new Error(`command ${nodeId} omitted ${placeholder}`);
    }
    if (nodeId.startsWith("capture-")) {
      for (const placeholder of ["{{executable.git.path}}", "{{executable.git.sha256}}"] ) {
        if (!argvText.includes(placeholder)) throw new Error(`command ${nodeId} omitted ${placeholder}`);
      }
      if (nodeId !== "capture-baseline") {
        if (!handler.argv.includes("--acceptance-argv-json") || !handler.argv.includes("{{acceptanceArgv}}")) {
          throw new Error(`command ${nodeId} omitted the explicit acceptance argv contract`);
        }
        if (handler.argv.includes("--acceptance")) {
          throw new Error(`command ${nodeId} retained the legacy shell acceptance flag`);
        }
      }
    }
  }
  const manifestPaths = new Set(manifest.files.map(file => file.path));
  for (const [label, resourcePath] of Object.entries(mapping.proof.resources)) {
    if (!manifestPaths.has(resourcePath)) throw new Error(`proof resource ${label} is absent from resource manifest`);
  }
  for (const file of manifest.files) {
    if (sha256(bytes(file.path)) !== file.sha256) throw new Error(`resource manifest digest is stale for ${file.path}`);
  }
  const finalReviewNodes = new Set(mapping.proof.finalReviewNodes);
  for (const edge of design.transitions.filter(edge => edge.to === "archive-proof" && edge.when?.equals === "PASS")) {
    if (!finalReviewNodes.has(edge.from)) throw new Error(`final review allowlist omitted ${edge.from}`);
  }
  if (finalReviewNodes.size !== mapping.proof.finalReviewNodes.length) throw new Error("final review allowlist has duplicates");
  const evidenceProducerNodes = new Set(mapping.proof.evidenceProducerNodes);
  if (evidenceProducerNodes.size !== mapping.proof.evidenceProducerNodes.length) {
    throw new Error("final evidence producer allowlist has duplicates");
  }
  for (const reviewNodeId of mapping.proof.finalReviewNodes) {
    const handler = mapping.handlers[reviewNodeId];
    const markerCount = handler.type === "decision"
      ? handler.prompt.split(REVIEW_COMMITMENT_MARKER).length - 1
      : 0;
    if (markerCount !== 1) {
      throw new Error(`final review ${reviewNodeId} must contain exactly one review commitment marker`);
    }
    const producers = design.transitions
      .filter(edge => edge.to === reviewNodeId && edge.from.startsWith("capture-final-evidence-"))
      .map(edge => edge.from);
    if (producers.length !== 1 || !evidenceProducerNodes.has(producers[0])) {
      throw new Error(`final review ${reviewNodeId} is not bound to its direct final-evidence producer`);
    }
  }
  if (evidenceProducerNodes.size !== mapping.proof.finalReviewNodes.length) {
    throw new Error("final evidence producer allowlist does not match final review coverage");
  }
  if (input.proofBinding.roleModelPolicyDigest !== sha256(bytes(MODEL_POLICY_RELATIVE))) {
    throw new Error("standard-round proof fixture role/model policy digest is stale");
  }
  if (!Array.isArray(input.rewardHackingRisks) || input.rewardHackingRisks.length > 5 ||
      input.rewardHackingRisks.some(value => typeof value !== "string" || !value.trim() || Buffer.byteLength(value, "utf8") > 2048) ||
      new Set(input.rewardHackingRisks).size !== input.rewardHackingRisks.length) {
    throw new Error("standard-round rewardHackingRisks fixture must be a bounded unique string list");
  }
}

function main(argv) {
  if (argv.length !== 1 || !["--write", "--check"].includes(argv[0])) {
    throw new Error("usage: build_mapping.mjs --write|--check");
  }
  const built = build();
  semanticChecks(built);
  if (argv[0] === "--write") {
    fs.mkdirSync(path.dirname(absolute(MAPPING_RELATIVE)), { recursive: true });
    fs.writeFileSync(absolute(MAPPING_RELATIVE), pretty(built.mapping));
    fs.writeFileSync(absolute(MANIFEST_RELATIVE), pretty(built.manifest));
    fs.writeFileSync(absolute(INPUT_RELATIVE), pretty(built.input));
  } else {
    compare(MAPPING_RELATIVE, pretty(built.mapping));
    compare(MANIFEST_RELATIVE, pretty(built.manifest));
    compare(INPUT_RELATIVE, pretty(built.input));
  }
  process.stdout.write(`${JSON.stringify({ ok: true, workflow: built.design.id, nodes: built.design.nodes.length, designSha256: built.digests.design })}\n`);
}

try {
  main(process.argv.slice(2));
} catch (error) {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
}
