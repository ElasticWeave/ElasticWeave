"""Regression checks for boundaries found in independent dispatch review."""
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import time
import unittest
from unittest import mock

import test_managed_dispatch as fixtures

md = fixtures.md


class DispatchBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def test_batch_return_never_claims_parent_completion_or_invents_reviewer(self):
        value = fixtures.plan(self.workspace, workers=1)
        value.pop("terminal_review", None)
        value["nodes"] = value["nodes"][:1]
        dispatcher = md.Dispatcher(value, self.root / "run")
        with mock.patch.object(md, "build_command", side_effect=fixtures.ManagedDispatchTests.fixture_command):
            result = dispatcher.run()
        self.assertTrue(result["candidate_ready"])
        self.assertIs(result["terminal"], False)
        self.assertEqual(["worker0"], list(result["states"]))
        self.assertNotIn("final_return", [e["event"] for e in dispatcher.events])

    def test_nested_dispatch_preserves_outer_lifecycle_owner(self):
        value = fixtures.plan(self.workspace)
        value["envelope"]["invocationMode"] = "child"
        value["envelope"]["terminalOwner"] = "outer-session"
        for node in value["nodes"]:
            node["envelope"]["terminalOwner"] = "outer-session"
        self.assertEqual("outer-session", md.validate_plan(value)["envelope"]["terminalOwner"])

    def test_read_only_action_cannot_acquire_native_write_profile(self):
        value = fixtures.plan(self.workspace)
        value["nodes"][0]["envelope"]["authority"] = "write"
        with self.assertRaises(md.DispatchError):
            md.validate_plan(value)

    def test_dependency_chain_needs_no_artificial_serialization_reason(self):
        value = fixtures.plan(self.workspace)
        value["nodes"][1]["depends_on"] = ["worker0"]
        value["max_concurrency"] = 1
        dispatcher = md.Dispatcher(value, self.root / "run")
        with mock.patch.object(md, "build_command", side_effect=fixtures.ManagedDispatchTests.fixture_command):
            self.assertTrue(dispatcher.run()["candidate_ready"])

    def test_orchestration_error_drains_live_handles(self):
        dispatcher = md.Dispatcher(fixtures.plan(self.workspace), self.root / "run")
        with mock.patch.object(md, "build_command", side_effect=fixtures.ManagedDispatchTests.fixture_command):
            dispatcher.tick()
            processes = [handle["process"] for handle in dispatcher.handles.values()]
            with mock.patch.object(dispatcher, "tick", side_effect=RuntimeError("controller failed")):
                with self.assertRaises(RuntimeError):
                    dispatcher.run()
        self.assertFalse(dispatcher.handles)
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_changed_dependency_evidence_blocks_fanin(self):
        value = fixtures.plan(self.workspace)
        value["nodes"][0]["brief"] = json.dumps({"delay": .01})
        value["nodes"][1]["brief"] = json.dumps({"delay": .3})
        dispatcher = md.Dispatcher(value, self.root / "run")
        self.addCleanup(lambda: dispatcher.cancel("test cleanup") if dispatcher.handles else None)
        with mock.patch.object(md, "build_command", side_effect=fixtures.ManagedDispatchTests.fixture_command):
            deadline = time.monotonic() + 3
            while dispatcher.states["worker0"] != "complete" and time.monotonic() < deadline:
                dispatcher.tick()
                time.sleep(.01)
            (self.workspace / "worker0.txt").write_text("changed after result")
            result = dispatcher.run()
        self.assertFalse(result["candidate_ready"])
        self.assertEqual("failed", result["states"]["review"])
        self.assertEqual("complete", result["states"]["worker1"])

    @unittest.skipUnless(shutil.which("bun"), "Bun required for workflow bridge integration")
    def test_workflow_candidate_preserves_unresolved_independent_review(self):
        directory = self.root / "evidence"
        directory.mkdir()
        leaf = fixtures.node(self.workspace, "worker0")
        leaf["owner"] = "admitted_workflow"
        leaf["launch"] = {"kind": "workflow", "mapping": "mapping.json"}
        spec = {"request_id": "request", "node": leaf, "skills": str(md.SKILLS),
                "evidence_dir": str(directory), "prompt": "bounded work"}
        spec_path = directory / "launch.json"
        spec_path.write_text(json.dumps(spec))
        bridge_path = directory / "bridge.mjs"
        bridge_path.write_text(md.BRIDGE)
        controller = md.SKILLS / "subagent-workflow/scripts/controller/run.ts"
        driver = f'''import {{mock}} from "bun:test";
mock.module({json.dumps(str(controller))},()=>({{runController:async options=>{{
  if(options.parentAccess!=="read" || options.parentBudget.maxModelCalls!==1 || !options.signal)
    throw new Error("bridge lost parent authority, allocation or cancellation");
  return {{state:{{status:"success"}},result:{{terminal:"unresolved",nextAction:"external terminal reviewer required before COMPLETE"}},
    runDir:options.root+"/workflow-output/run"}};
}}}}));
await import({json.dumps(str(bridge_path))});'''
        result = subprocess.run([shutil.which("bun"), "--eval", driver, str(spec_path)],
                                capture_output=True, text=True, timeout=10, check=True)
        candidate = json.loads(result.stdout)
        self.assertEqual("complete", candidate["status"])
        self.assertEqual("unresolved", json.loads(candidate["summary"])["terminal"])


if __name__ == "__main__":
    unittest.main()
