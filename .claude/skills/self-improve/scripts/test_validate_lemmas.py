import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().with_name("validate_lemmas.py")
SPEC = importlib.util.spec_from_file_location("validate_lemmas", SCRIPT)
validate_lemmas = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = validate_lemmas
SPEC.loader.exec_module(validate_lemmas)


BODY = """# Claim

## Observation
Raw trace differs from the expected behavior.

## Problem
The cause of the discrepancy is not yet established.

## Hypothesis
The isolated control path causes the discrepancy.

## Prediction
Changing only that path will flip the observed result.

## Intervention
Run the declared isolated probe against treatment and control.

## Result
The declared raw artifacts record the observed outcome.

## Mechanism
The probe isolates the named path while holding the alternative constant.

## Boundaries and Falsifier
Limited to this runtime; a control with the same outcome would falsify the claim.

## Synthesis
The status reflects only the evidence declared in frontmatter.

## Next Probe
NONE
"""


def frontmatter(
    lemma_id: str,
    status: str,
    *,
    kind: str = "none",
    artifacts: tuple[str, ...] = (),
    runs: tuple[str, ...] = (),
    probe_type: str = "none",
    probe: str = "",
    independence: str = "",
    task_contexts: tuple[str, ...] = ("task://context-a", "task://context-b"),
    transfer_artifacts: tuple[str, ...] = (
        "artifact://context-a-observation",
        "artifact://context-b-observation",
    ),
    transfer_independence: str = "contexts use different projects and inputs",
    project_identifiers_removed: bool = True,
    applicability: str = "isolated test runtimes with the declared control path",
    counterexamples: str = "runtimes without that control path are out of scope",
    falsifier: str = "a matched control with the same outcome falsifies transfer",
    include_transfer: bool = True,
    derived: tuple[str, ...] = (),
    superseded_by: str = "",
) -> str:
    def yaml_list(values: tuple[str, ...]) -> str:
        return "[" + ", ".join(f'"{value}"' for value in values) + "]"

    transfer = ""
    if include_transfer:
        transfer = f"""transfer:
  task_contexts: {yaml_list(task_contexts)}
  artifacts: {yaml_list(transfer_artifacts)}
  independence: "{transfer_independence}"
  project_identifiers_removed: {str(project_identifiers_removed).lower()}
  applicability: "{applicability}"
  counterexamples: "{counterexamples}"
  falsifier: "{falsifier}"
"""

    return f"""---
id: {lemma_id}
status: {status}
skill: demo
scope: "isolated test runtime"
evidence:
  kind: {kind}
  artifacts: {yaml_list(artifacts)}
  runs: {yaml_list(runs)}
  probe_type: {probe_type}
  probe: "{probe}"
  independence: "{independence}"
{transfer}derived_changes: {yaml_list(derived)}
superseded_by: "{superseded_by}"
---

""" + BODY


def report(outcome: str, **overrides):
    payload = {
        "outcome": outcome,
        "skill": "",
        "path": "",
        "behaviorChanged": False,
        "hypothesis": "",
        "evidence": "",
        "artifacts": [],
        "prediction": "",
        "intervention": "",
        "observation": "",
        "mechanism": "",
        "boundaries": "",
        "reason": "status is warranted by the cited evidence",
        "nextProbe": "",
        "directDefect": "",
        "failingProbe": "",
        "passingProbe": "",
    }
    payload.update(overrides)
    return json.dumps(payload)


def cross_task_reference(
    *,
    derived_from: tuple[str, ...],
    task_contexts: tuple[str, ...] = ("task://context-a", "task://context-b"),
    artifacts: tuple[str, ...] = (
        "artifact://context-a-observation",
        "artifact://context-b-observation",
    ),
    project_identifiers_removed: bool = True,
) -> str:
    def yaml_list(values: tuple[str, ...]) -> str:
        return "[" + ", ".join(f'"{value}"' for value in values) + "]"

    return f"""---
kind: cross-task-reference
source: self-evolve
status: supported
skill: demo
derived_from: {yaml_list(derived_from)}
transfer:
  task_contexts: {yaml_list(task_contexts)}
  artifacts: {yaml_list(artifacts)}
  independence: "contexts use different projects and inputs"
  project_identifiers_removed: {str(project_identifiers_removed).lower()}
  applicability: "isolated test runtimes with the declared control path"
  counterexamples: "runtimes without that path are out of scope"
  falsifier: "a matched control with the same outcome falsifies reuse"
---

# Learned guidance

Use the supported mechanism within its declared scope.
"""


class LemmaValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lemmas = self.root / ".claude" / "skills" / "demo" / "lemmas"
        self.lemmas.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name: str, text: str, *, indexed: bool = True) -> Path:
        path = self.lemmas / name
        path.write_text(text, encoding="utf-8")
        if indexed:
            index = self.lemmas / "INDEX.md"
            existing = index.read_text(encoding="utf-8") if index.exists() else "# Lemmas\n\n"
            index.write_text(existing + f"- [{name}]({name})\n", encoding="utf-8")
        return path

    def write_reference(self, name: str, text: str) -> Path:
        references = self.lemmas.parent / "references"
        references.mkdir(exist_ok=True)
        path = references / name
        path.write_text(text, encoding="utf-8")
        return path

    def problems(self):
        return validate_lemmas.validate_tree(self.root)

    def test_valid_deterministic_support(self):
        self.write(
            "deterministic.md",
            frontmatter(
                "deterministic-support",
                "supported",
                kind="deterministic",
                artifacts=("run://probe-1",),
                runs=("run://probe-1",),
                probe_type="counterfactual",
                probe="same input with only the target branch toggled",
                derived=(".claude/skills/demo/SKILL.md",),
            ),
        )
        self.assertEqual([], self.problems())
        result = subprocess.run(
            ["python3", "-S", str(SCRIPT), "--root", str(self.root)], capture_output=True, text=True
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_valid_stochastic_replicated_support(self):
        self.write(
            "stochastic.md",
            frontmatter(
                "stochastic-support",
                "supported",
                kind="stochastic",
                artifacts=("run://seed-1", "run://seed-2"),
                runs=("run://seed-1", "run://seed-2"),
                probe_type="statistical",
                probe="compare treatment and control across independent seeds",
                independence="independent seeds; effect exceeds measured run variance",
            ),
        )
        self.assertEqual([], self.problems())

    def test_lemma_without_transfer_declaration_is_rejected(self):
        self.write(
            "missing-transfer.md",
            frontmatter("missing-transfer", "conjecture", include_transfer=False),
        )
        self.assertTrue(any("transfer must be a mapping" in problem for problem in self.problems()))

    def test_one_task_context_cannot_be_promoted_as_a_lemma(self):
        self.write(
            "one-context.md",
            frontmatter(
                "one-context",
                "conjecture",
                task_contexts=("task://same-task",),
                transfer_artifacts=("artifact://run-one", "artifact://run-two"),
            ),
        )
        self.assertTrue(any("two distinct transfer.task_contexts" in problem for problem in self.problems()))

    def test_project_identifiers_must_be_declared_removed(self):
        self.write(
            "identified.md",
            frontmatter(
                "identified",
                "conjecture",
                project_identifiers_removed=False,
            ),
        )
        self.assertTrue(any("project_identifiers_removed must be true" in problem for problem in self.problems()))

    def test_transfer_requires_applicability_counterexample_and_falsifier(self):
        self.write(
            "unbounded.md",
            frontmatter(
                "unbounded",
                "conjecture",
                applicability="",
                counterexamples="",
                falsifier="",
            ),
        )
        problems = self.problems()
        self.assertTrue(any("transfer.applicability" in problem for problem in problems))
        self.assertTrue(any("transfer.counterexamples" in problem for problem in problems))
        self.assertTrue(any("transfer.falsifier" in problem for problem in problems))

    def test_transfer_placeholders_are_rejected(self):
        self.write(
            "placeholder.md",
            frontmatter(
                "placeholder",
                "conjecture",
                task_contexts=("task://one", "<second-task>"),
                transfer_artifacts=("artifact://one", "artifact://TODO"),
                transfer_independence="TBD",
            ),
        )
        problems = self.problems()
        self.assertTrue(any("task_contexts must be concrete" in problem for problem in problems))
        self.assertTrue(any("artifacts must be concrete" in problem for problem in problems))
        self.assertTrue(any("transfer.independence must be concrete" in problem for problem in problems))

    def test_learned_reference_requires_supported_same_skill_lemma(self):
        self.write("candidate.md", frontmatter("candidate", "conjecture"))
        self.write_reference(
            "learned-premature.md",
            cross_task_reference(derived_from=("candidate",)),
        )
        self.assertTrue(any("must resolve to a supported lemma" in problem for problem in self.problems()))

    def test_valid_learned_reference_from_supported_lemma(self):
        reference_path = ".claude/skills/demo/references/learned-supported.md"
        self.write(
            "supported.md",
            frontmatter(
                "supported-claim",
                "supported",
                kind="deterministic",
                artifacts=("run://probe",),
                runs=("run://probe",),
                probe_type="counterfactual",
                probe="toggle only the declared branch",
                derived=(reference_path,),
            ),
        )
        self.write_reference(
            "learned-supported.md",
            cross_task_reference(derived_from=("supported-claim",)),
        )
        self.assertEqual([], self.problems())

    def test_learned_reference_requires_reciprocal_derived_change_link(self):
        self.write(
            "supported.md",
            frontmatter(
                "supported-claim",
                "supported",
                kind="deterministic",
                artifacts=("run://probe",),
                runs=("run://probe",),
                probe_type="counterfactual",
                probe="toggle only the declared branch",
            ),
        )
        self.write_reference(
            "learned-unlinked.md",
            cross_task_reference(derived_from=("supported-claim",)),
        )
        self.assertTrue(any("must link this carrier in derived_changes" in problem for problem in self.problems()))

    def test_static_reference_is_not_treated_as_promoted_run_learning(self):
        self.write_reference("vendor-api.md", "# Upstream API\n\nStatic source documentation.\n")
        (self.lemmas / "INDEX.md").write_text("# Lemmas\n", encoding="utf-8")
        self.assertEqual([], self.problems())

    def test_legacy_project_memory_carrier_names_are_rejected(self):
        (self.lemmas / "INDEX.md").write_text("# Lemmas\n", encoding="utf-8")
        for name in ("lessons.md", "runtime-lessons.md", "standard-round-lessons.md"):
            with self.subTest(name=name):
                path = self.write_reference(name, "# Historical run lessons\n")
                problems = self.problems()
                self.assertTrue(
                    any("legacy project-memory carrier name" in problem for problem in problems),
                    problems,
                )
                path.unlink()

    def test_nested_legacy_project_memory_carrier_name_is_rejected(self):
        (self.lemmas / "INDEX.md").write_text("# Lemmas\n", encoding="utf-8")
        nested = self.lemmas.parent / "references" / "archive" / "lessons.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("# Archived run lessons\n", encoding="utf-8")
        problems = self.problems()
        self.assertTrue(any("legacy project-memory carrier name" in problem for problem in problems), problems)

    def test_nested_learned_reference_uses_the_owning_skill(self):
        reference_path = ".claude/skills/demo/references/topic/learned-supported.md"
        self.write(
            "supported.md",
            frontmatter(
                "supported-claim",
                "supported",
                kind="deterministic",
                artifacts=("run://probe",),
                runs=("run://probe",),
                probe_type="counterfactual",
                probe="toggle only the declared branch",
                derived=(reference_path,),
            ),
        )
        nested = self.lemmas.parent / "references" / "topic" / "learned-supported.md"
        nested.parent.mkdir(parents=True)
        nested.write_text(
            cross_task_reference(derived_from=("supported-claim",)),
            encoding="utf-8",
        )
        self.assertEqual([], self.problems())

    def test_learned_reference_rejects_dated_memory_log_entries(self):
        reference_path = ".claude/skills/demo/references/learned-supported.md"
        self.write(
            "supported.md",
            frontmatter(
                "supported-claim",
                "supported",
                kind="deterministic",
                artifacts=("run://probe",),
                runs=("run://probe",),
                probe_type="counterfactual",
                probe="toggle only the declared branch",
                derived=(reference_path,),
            ),
        )
        self.write_reference(
            "learned-supported.md",
            cross_task_reference(derived_from=("supported-claim",))
            + "\n- [2026-07-14] local run passed — why: machine-specific evidence\n",
        )
        problems = self.problems()
        self.assertTrue(any("project-memory dated entry syntax" in problem for problem in problems), problems)

    def test_static_upstream_reference_may_retain_version_and_date_provenance(self):
        self.write_reference(
            "vendor-changelog-2026.md",
            "# Vendor changelog\n\n"
            "Release date: 2026-07-14; API version: 2026-07.\n\n"
            "- [2026-07-14] Vendor published the documented API revision.\n",
        )
        (self.lemmas / "INDEX.md").write_text("# Lemmas\n", encoding="utf-8")
        self.assertEqual([], self.problems())

    def test_marked_self_evolve_reference_cannot_hide_under_static_name(self):
        reference_path = ".claude/skills/demo/references/innocent-looking.md"
        self.write(
            "supported.md",
            frontmatter(
                "supported-claim",
                "supported",
                kind="deterministic",
                artifacts=("run://probe",),
                runs=("run://probe",),
                probe_type="counterfactual",
                probe="toggle only the declared branch",
                derived=(reference_path,),
            ),
        )
        self.write_reference(
            "innocent-looking.md",
            cross_task_reference(derived_from=("supported-claim",)),
        )
        self.assertTrue(any("must use the learned-<slug>.md name" in problem for problem in self.problems()))

    def test_conjecture_with_no_feasible_experiment(self):
        self.write("conjecture.md", frontmatter("budget-conjecture", "conjecture"))
        self.assertEqual([], self.problems())

    def test_unsupported_durable_link_is_rejected(self):
        self.write(
            "weak.md",
            frontmatter(
                "weak-tested",
                "tested",
                kind="deterministic",
                artifacts=("run://one",),
                probe_type="discriminating",
                probe="single diagnostic probe",
                derived=(".claude/skills/demo/SKILL.md",),
            ),
        )
        self.assertTrue(any("cannot link durable" in problem for problem in self.problems()))

    def test_weak_stochastic_support_is_rejected(self):
        self.write(
            "weak-stochastic.md",
            frontmatter(
                "weak-stochastic",
                "supported",
                kind="stochastic",
                artifacts=("run://only-one",),
                probe_type="statistical",
                probe="one noisy run",
                independence="single seed",
            ),
        )
        self.assertTrue(any("two distinct" in problem for problem in self.problems()))

    def test_one_run_with_two_artifacts_is_not_independent_stochastic_support(self):
        self.write(
            "one-run.md",
            frontmatter(
                "one-run-two-artifacts",
                "supported",
                kind="stochastic",
                artifacts=("artifact://summary", "artifact://chart"),
                runs=("run://only-one",),
                probe_type="statistical",
                probe="one run rendered into two files",
                independence="the artifacts came from the same run",
            ),
        )
        self.assertTrue(any("two distinct evidence.runs" in problem for problem in self.problems()))

    def test_bad_status_enum_is_rejected(self):
        self.write("bad.md", frontmatter("bad-status", "promoted"))
        self.assertTrue(any("status must be one of" in problem for problem in self.problems()))

    def test_missing_index_coverage_is_rejected(self):
        (self.lemmas / "INDEX.md").write_text("# Lemmas\n", encoding="utf-8")
        self.write("unindexed.md", frontmatter("unindexed", "conjecture"), indexed=False)
        self.assertTrue(any("missing link" in problem for problem in self.problems()))

    def test_superseded_link_resolves_to_supported_replacement(self):
        self.write(
            "new.md",
            frontmatter(
                "replacement",
                "supported",
                kind="deterministic",
                artifacts=("run://replacement",),
                runs=("run://replacement",),
                probe_type="counterfactual",
                probe="replacement isolated against the old behavior",
            ),
        )
        self.write(
            "old.md",
            frontmatter(
                "old-claim",
                "superseded",
                derived=(".claude/skills/demo/SKILL.md",),
                superseded_by="replacement",
            ),
        )
        tree_problems, lemmas = validate_lemmas.inspect_tree(self.root)
        self.assertEqual([], tree_problems)
        raw = report(
            "superseded",
            skill="demo",
            path=".claude/skills/demo/lemmas/old.md",
            behaviorChanged=True,
            hypothesis="the supported replacement better explains the scoped behavior",
            evidence="the replacement passed its discriminating probe",
            artifacts=["run://replacement"],
            prediction="redirecting prior behavior will preserve the supported mechanism",
            intervention="replace only the old linked guidance",
            observation="the replacement is supported and the old claim is obsolete",
            mechanism="the new lemma isolates the causal path",
            boundaries="only prior behavior governed by the old claim; regression falsifies it",
        )
        report_problems, _, _ = validate_lemmas.validate_report(raw, self.root, lemmas)
        self.assertEqual([], report_problems)

    def test_superseded_behavior_change_requires_prior_derived_link(self):
        self.write(
            "new.md",
            frontmatter(
                "replacement",
                "supported",
                kind="deterministic",
                artifacts=("run://replacement",),
                runs=("run://replacement",),
                probe_type="counterfactual",
                probe="replacement isolated against the old behavior",
            ),
        )
        self.write(
            "old.md",
            frontmatter("old-claim", "superseded", superseded_by="replacement"),
        )
        tree_problems, lemmas = validate_lemmas.inspect_tree(self.root)
        self.assertEqual([], tree_problems)
        raw = report(
            "superseded",
            skill="demo",
            path=".claude/skills/demo/lemmas/old.md",
            behaviorChanged=True,
            hypothesis="the supported replacement better explains the scoped behavior",
            evidence="the replacement passed its discriminating probe",
            artifacts=["run://replacement"],
            prediction="redirecting prior behavior will preserve the supported mechanism",
            intervention="replace only the old linked guidance",
            observation="the replacement is supported and the old claim is obsolete",
            mechanism="the new lemma isolates the causal path",
            boundaries="only prior behavior governed by the old claim; regression falsifies it",
        )
        report_problems, _, _ = validate_lemmas.validate_report(raw, self.root, lemmas)
        self.assertTrue(any("requires prior linked behavior" in problem for problem in report_problems))

    def test_superseded_link_to_conjecture_is_rejected(self):
        self.write("new.md", frontmatter("replacement", "conjecture"))
        self.write(
            "old.md",
            frontmatter("old-claim", "superseded", superseded_by="replacement"),
        )
        self.assertTrue(any("must resolve to a supported lemma" in problem for problem in self.problems()))

    def test_repair_report_does_not_require_lemma_fields(self):
        (self.lemmas / "INDEX.md").write_text("# Lemmas\n", encoding="utf-8")
        raw = json.dumps(
            {
                "outcome": "REPAIR",
                "path": ".omp/agents/workflow-updater.md",
                "behaviorChanged": True,
                "artifacts": ["run://failing-probe", "run://passing-probe"],
                "reason": "a directly reproduced defect in a used artifact was repaired",
                "directDefect": "the used agent omitted required state",
                "failingProbe": "the focused probe reproduced the omission",
                "passingProbe": "the same probe passed after the repair",
            }
        )
        problems, _, _ = validate_lemmas.validate_report(raw, self.root, [])
        self.assertEqual([], problems)
        result = subprocess.run(
            [
                "python3",
                "-S",
                str(SCRIPT),
                "--root",
                str(self.root),
                "--json",
                "--report-json",
                raw,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])

    def test_none_allows_memory_only_residue_but_not_behavior_change(self):
        raw = json.dumps(
            {
                "outcome": "NONE",
                "skill": "demo",
                "path": ".memory/skills/demo.md",
                "behaviorChanged": False,
                "artifacts": ["run://raw-observation"],
                "reason": "only a terse source-layer observation was retained",
            }
        )
        problems, _, _ = validate_lemmas.validate_report(raw, self.root, [])
        self.assertEqual([], problems)
        changed = json.loads(raw)
        changed["behaviorChanged"] = True
        problems, _, _ = validate_lemmas.validate_report(json.dumps(changed), self.root, [])
        self.assertTrue(any("NONE cannot set behaviorChanged true" in problem for problem in problems))

    def test_report_outcome_must_match_on_disk_status(self):
        self.write("claim.md", frontmatter("claim", "conjecture"))
        tree_problems, lemmas = validate_lemmas.inspect_tree(self.root)
        self.assertEqual([], tree_problems)
        raw = report(
            "supported",
            skill="demo",
            path=".claude/skills/demo/lemmas/claim.md",
            hypothesis="the isolated path causes the discrepancy",
            evidence="a raw observation exists but no experiment ran",
            artifacts=["run://observation"],
            prediction="the intervention would flip the result",
            intervention="deferred because the probe exceeded this round",
            observation="the discrepancy was observed",
            mechanism="candidate mechanism only",
            boundaries="limited scope; a matching control falsifies it",
        )
        problems, _, checked = validate_lemmas.validate_report(raw, self.root, lemmas)
        self.assertEqual("conjecture", checked["status"])
        self.assertTrue(any("does not match on-disk" in problem for problem in problems))
        result = subprocess.run(
            [
                "python3",
                "-S",
                str(SCRIPT),
                "--root",
                str(self.root),
                "--json",
                "--report-json",
                raw,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        machine = json.loads(result.stdout)
        self.assertFalse(machine["ok"])
        self.assertTrue(any("does not match on-disk" in problem for problem in machine["problems"]))

    def test_refuted_lemma_is_valid_and_retained(self):
        path = self.write(
            "refuted.md",
            frontmatter(
                "refuted-claim",
                "refuted",
                kind="deterministic",
                artifacts=("run://counterexample",),
                runs=("run://counterexample",),
                probe_type="counterfactual",
                probe="control reproduces the same outcome",
                derived=(".claude/skills/demo/SKILL.md",),
            ),
        )
        tree_problems, lemmas = validate_lemmas.inspect_tree(self.root)
        self.assertEqual([], tree_problems)
        raw = report(
            "refuted",
            skill="demo",
            path=".claude/skills/demo/lemmas/refuted.md",
            behaviorChanged=True,
            hypothesis="the prior mechanism does not distinguish treatment from control",
            evidence="the counterfactual reproduced the same outcome",
            artifacts=["run://counterexample"],
            prediction="the control should differ if the old mechanism were causal",
            intervention="run the matched control",
            observation="the control produced the same result",
            mechanism="the old attributed cause was not discriminating",
            boundaries="remove only the linked guidance; a differing control would falsify refutation",
        )
        report_problems, _, _ = validate_lemmas.validate_report(raw, self.root, lemmas)
        self.assertEqual([], report_problems)
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
