from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent

_MATERIALIZE_SPEC = importlib.util.spec_from_file_location(
    "default_mode_materialize", HERE / "materialize.py"
)
assert _MATERIALIZE_SPEC is not None and _MATERIALIZE_SPEC.loader is not None
materialize = importlib.util.module_from_spec(_MATERIALIZE_SPEC)
sys.modules[_MATERIALIZE_SPEC.name] = materialize
_MATERIALIZE_SPEC.loader.exec_module(materialize)

SKILLS = materialize.skills_root(HERE / "materialize.py")

VALIDATE_MODEL_ROLES = (
    SKILLS / "update-platform-config" / "scripts" / "config-sync" / "validate_model_roles.py"
)
VALIDATE_DESIGN = SKILLS / "build-workflow" / "scripts" / "validate_design.py"
SELECT_HARNESS = SKILLS / "run-workflow" / "scripts" / "select_harness.py"
SAFE_MAPPING = SKILLS / "humanize-workflow" / "scripts" / "safe_mapping.py"
VALIDATE_PROJECT_FLOWS = (
    SKILLS / "write-humanize-flow" / "scripts" / "validate_project_flows.py"
)
SUBAGENT_CONTROLLER = (
    SKILLS / "subagent-workflow" / "scripts" / "controller" / "run.ts"
)

REMOVED_HARNESS_NAME_RE = re.compile(r"(?:^|.)(?:omh-workflow|flowjanus-workflow)(?:$|.)", re.I)
REMOVED_HARNESS_FRAGMENT_RE = re.compile(r"\.omhflow\b|\.flowjanus(?:\b|/)", re.I)
REMOVED_HARNESS_NAME_PARTS = ("omh-workflow", "flowjanus-workflow", ".omhflow", ".flowjanus")
OWNED_SLOT_LINK = Path(
    "write-humanize-flow/assets/project/.humanize/flows/slot.py"
)


def _copy_discoverable_skill_bundle(source_root: Path, project_root: Path) -> Path:
    """Copy the installed skill tree without following links or copying bytecode."""
    destination_root = project_root / ".claude" / "skills"
    destination_root.mkdir(parents=True, exist_ok=True)
    for current, directories, files in os.walk(source_root, topdown=True, followlinks=False):
        source_dir = Path(current)
        kept_directories: list[str] = []
        for name in directories:
            path = source_dir / name
            if path.is_symlink():
                raise AssertionError(f"skill bundle contains symlink: {path}")
            if materialize.has_python_cache_component(path):
                continue
            kept_directories.append(name)
        directories[:] = kept_directories
        for name in files:
            source = source_dir / name
            if source.is_symlink():
                relative = source.relative_to(source_root)
                target = os.readlink(source)
                if relative != OWNED_SLOT_LINK or materialize.SLOT_LINK_PATTERN.fullmatch(target) is None:
                    raise AssertionError(f"skill bundle contains unsupported symlink: {source}")
                destination = destination_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(target)
                continue
            if source.suffix.lower() in materialize.PYTHON_BYTECODE_SUFFIXES:
                continue
            relative = source.relative_to(source_root)
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return destination_root


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FreshProjectContractTests(unittest.TestCase):
    """End-to-end regression: a freshly materialized project satisfies every
    runtime contract the bundle ships.  Existing materialize tests only assert
    copied bytes; these checks mechanically run the same validators the runtime
    uses against the project-root files the materializer just created."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.project = Path(cls._tmp.name).resolve()
        created, _ = materialize.materialize(cls.project, SKILLS)
        cls.created = created
        cls.installed_skills = _copy_discoverable_skill_bundle(SKILLS, cls.project)
        cls.models_json = cls.project / "config" / "workflows" / "models.json"
        cls.designs_dir = cls.project / ".workflow" / "designs"
        cls.harnesses_json = cls.project / ".workflow" / "harnesses.json"
        cls.subagent_flows_dir = cls.project / ".subagent" / "flows"
        cls.humanize_flows_dir = cls.project / ".humanize" / "flows"
        cls.humanize_scheduler_launcher = cls.project / ".humanize" / "run_scheduler.py"
        cls.humanize_mappings_dir = cls.project / ".humanize" / "mappings"
        cls.validate_design_module = _load_module("default_mode_validate_design", VALIDATE_DESIGN)
        cls.select_harness_module = _load_module("default_mode_select_harness", SELECT_HARNESS)
        cls.safe_mapping_module = _load_module("default_mode_safe_mapping", SAFE_MAPPING)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _subprocess(self, argv: list[str], *, cwd: Path | None = None,
                    env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            text=True,
            capture_output=True,
            check=False,
            cwd=str(cwd or self.project),
            env={**os.environ, **(env or {})},
            timeout=60,
        )

    def test_project_root_models_json_validates_workflow_model_roles_v3(self) -> None:
        self.assertTrue(self.models_json.is_file(), "materializer must create config/workflows/models.json")
        env = {"GENERAL_AGENT_PROJECT_ROOT": str(self.project)}
        completed = self._subprocess(
            [sys.executable, str(VALIDATE_MODEL_ROLES), str(self.models_json)],
            cwd=self.project,
            env=env,
        )
        self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)
        self.assertIn("[validate-model-roles] valid:", completed.stdout)
        document = json.loads(self.models_json.read_text(encoding="utf-8"))
        self.assertEqual("workflow-model-roles/v3", document.get("schema"))
        self.assertEqual(
            {
                "backend": "claude",
                "provider": "anthropic",
                "upstreamProviderFamily": "anthropic",
                "model": "claude-opus-5",
                "modelFamily": "claude-opus-5",
                "effort": "max",
            },
            {
                key: document["roles"]["advisor"][key]
                for key in (
                    "backend", "provider", "upstreamProviderFamily",
                    "model", "modelFamily", "effort",
                )
            },
        )
        self.assertEqual(
            ["low", "medium", "high", "xhigh", "max"],
            document["reasoningPolicy"]["backendEffortLevels"]["claude"],
        )
        self.assertEqual(
            ("cursor", "cursor", "anthropic"),
            tuple(
                document["roles"]["anthropic_task"][key]
                for key in ("backend", "provider", "upstreamProviderFamily")
            ),
            "native advisor routing must not relabel Cursor-hosted Anthropic writers",
        )

    def test_every_bundled_design_validates_and_maps(self) -> None:
        designs = sorted(self.designs_dir.glob("*.workflow.json"))
        self.assertTrue(designs, "materializer must ship bundled workflow designs")
        registry = json.loads(self.harnesses_json.read_text(encoding="utf-8"))
        for design_path in designs:
            with self.subTest(design=design_path.name):
                completed = self._subprocess(
                    [sys.executable, str(VALIDATE_DESIGN), str(design_path)],
                    cwd=self.project,
                )
                self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)
                self.assertEqual("DESIGN: clean\n", completed.stdout)
                self.assertEqual("", completed.stderr)
                document = json.loads(design_path.read_text(encoding="utf-8"))
                self.assertEqual("workflow-design/v1", document.get("schema"))
                full_errors = self.validate_design_module.validate(document, SKILLS)
                self.assertEqual([], full_errors, full_errors)
                checks = self.select_harness_module.inspect_mappings(
                    document, registry, self.project, design_path
                )
                self.assertIn("subagent-scheduler", checks)
                subagent = checks["subagent-scheduler"]
                self.assertEqual(
                    "valid", subagent["status"],
                    f"subagent mapping for {design_path.name}: {subagent.get('issues')}",
                )
                self.assertEqual([], subagent.get("issues", []))
                humanize = checks.get("humanize")
                if humanize is not None:
                    self.assertIn(
                        humanize["status"], {"valid", "missing"},
                        f"humanize mapping for {design_path.name}: {humanize.get('issues')}",
                    )
                    if humanize["status"] == "valid":
                        self.assertEqual([], humanize.get("issues", []))
                    else:
                        self.assertEqual(["mapping-artifact-missing"], humanize.get("issues"))

    def test_subagent_mapping_validator_builder_check_where_applicable(self) -> None:
        bun = shutil.which("bun")
        if bun is None:
            self.skipTest("bun unavailable; subagent controller validate is not applicable here")
        mappings = sorted(self.subagent_flows_dir.glob("*.json"))
        self.assertTrue(mappings, "materializer must ship subagent flow fixtures")
        for mapping_path in mappings:
            with self.subTest(mapping=mapping_path.name):
                completed = self._subprocess(
                    [bun, str(SUBAGENT_CONTROLLER), "validate", str(mapping_path)],
                    cwd=self.project,
                )
                self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)

    def test_humanize_validate_full_mapping_canonical_path_available(self) -> None:
        self.assertTrue(callable(self.safe_mapping_module.validate_full_mapping))
        self.assertEqual(
            ".claude/skills/build-workflow/scripts/validate_design.py",
            str(self.safe_mapping_module.NEUTRAL_VALIDATOR),
        )
        self.assertEqual(
            "humanize-workflow-mapping/v1",
            self.safe_mapping_module.MAPPING_SCHEMA,
        )
        if self.humanize_mappings_dir.is_dir():
            manifests = sorted(self.humanize_mappings_dir.glob("*.json"))
        else:
            manifests = []
        for manifest_path in manifests:
            with self.subTest(manifest=manifest_path.name):
                self.safe_mapping_module.validate_full_mapping(self.project, manifest_path)

    def test_materialized_humanize_scheduler_launcher_exists(self) -> None:
        self.assertTrue(self.humanize_scheduler_launcher.is_file())

    def test_materialized_session_contract_exposes_conservative_iteration_default(self) -> None:
        for name in ("AGENTS.md", "CLAUDE.md"):
            text = (self.project / name).read_text(encoding="utf-8")
            self.assertIn("`autonomous` or `human_guided`", text)
            self.assertIn("Missing legacy fields mean `autonomous`", text)
            self.assertIn("external human observation checkpoint before replanning", text)
            self.assertIn("never waives authority", text)
            self.assertIn("You are the top scheduler and team", text)
            self.assertIn("dispatch bounded subagents", text)
            self.assertIn("do not over-focus on building a perfect test", text)
            self.assertIn("reward-hacking and jailbreak", text)
            self.assertIn("SIMPLIFY_VERIFY_DECISION: required|not_required", text)
            self.assertIn("SIMPLIFY_VERIFY_REASON: <one concrete sentence>", text)

    def test_validate_project_flows_all_admitted_on_project_humanize_files(self) -> None:
        completed = self._subprocess(
            [sys.executable, str(VALIDATE_PROJECT_FLOWS),
             "--root", str(self.project), "--all-admitted"],
            cwd=self.project,
        )
        self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertEqual("all-admitted", payload["scope"])
        self.assertEqual([], payload["unvalidatedFlows"])
        self.assertEqual([], payload["unvalidatedHelpers"])
        self.assertTrue(payload["defaultSha256"])
        self.assertTrue(payload["slotSha256"])
        self.assertEqual(
            payload["slotTarget"],
            f"versions/slot-{payload['slotSha256']}.py",
        )

    def test_removed_omh_flowjanus_residues_remain_absent(self) -> None:
        offenders: list[str] = []
        checked = 0
        for path in self.project.rglob("*"):
            if self.installed_skills in path.parents:
                continue
            if path.is_symlink() or not path.is_file():
                continue
            checked += 1
            relative = path.relative_to(self.project).as_posix()
            name = path.name.lower()
            if any(part in name for part in REMOVED_HARNESS_NAME_PARTS):
                offenders.append(f"name:{relative}")
                continue
            if REMOVED_HARNESS_NAME_RE.search(relative) or REMOVED_HARNESS_FRAGMENT_RE.search(relative):
                offenders.append(f"path:{relative}")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if REMOVED_HARNESS_NAME_RE.search(text) or REMOVED_HARNESS_FRAGMENT_RE.search(text):
                offenders.append(f"text:{relative}")
        self.assertGreater(checked, 0, "removed-harness hygiene must inspect materialized project assets")
        self.assertEqual([], offenders, f"removed harness residues materialized: {offenders}")

    def test_fresh_project_omits_python_bytecode(self) -> None:
        for path in self.project.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.project)
            self.assertFalse(
                materialize.has_python_cache_component(relative),
                f"python cache copied into fresh project: {relative}",
            )
            self.assertNotIn(
                path.suffix.lower(),
                materialize.PYTHON_BYTECODE_SUFFIXES,
                f"python bytecode copied into fresh project: {relative}",
            )


if __name__ == "__main__":
    unittest.main()
