"""Real overlapping harmless processes; no authenticated model calls in this suite.

The scheduler tests patch only build_command, with no production fixture flag. The
bridge integration test patches Bun's canonical resolver module and runs the real
native execution/retry/receipt machinery against a local harmless CLI fixture.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name("managed_dispatch.py")
SPEC = importlib.util.spec_from_file_location("managed_dispatch", SCRIPT)
md = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = md
SPEC.loader.exec_module(md)

FIXTURE = r'''
import hashlib,json,pathlib,sys,time
spec=json.loads(pathlib.Path(sys.argv[1]).read_text())
node=spec['node']; root=pathlib.Path(node['workspace']); d=pathlib.Path(spec['evidence_dir'])
settings=json.loads(node['brief'])
start=time.time()
(d/'started').write_text(str(start))
for i in range(3):
    print('progress '+str(i),flush=True)
    time.sleep(settings.get('delay',.12)/3)
if settings.get('exit'): sys.exit(settings['exit'])
artifact=root/(node['id']+'.txt'); artifact.write_text('work '+node['id'])
(d/'ended').write_text(str(time.time()))
result={'schema':'managed-worker-result/v1','request_id':spec['request_id'],'node_id':node['id'],
        'status':settings.get('status','complete'),'summary':'verified fixture work','evidence':[artifact.name]}
if node['envelope']['workerClass']=='terminal-reviewer':
    marker='Invocation and scope (cannot expand user authority):\n'
    contract=json.loads(spec['prompt'].split(marker)[1].split('\nProduce this scoped result')[0])
    result['verdict']=settings.get('verdict','COMPLETE')
    result['evidence_commitment']=contract['evidence_commitment']
print(json.dumps(result),flush=True)
'''


def envelope(invocation_id, *, root=None, reviewer=False):
    return {"schema": "skill-invocation-envelope/v1", "invocationId": invocation_id,
            "invocationMode": "root" if root is None else "child", "authority": "write" if root is None else "read_only",
            "terminalOwner": invocation_id if root is None else root,
            "workerClass": "terminal-reviewer" if reviewer else "execution", "activeSkillStack": [],
            "budgets": {"model_calls": 20 if root is None else 1, "wall_time_seconds": 40000},
            "requiredReceipts": [], "parentReceipts": [], "iterationPolicy": "autonomous", "humanFeedbackReceipt": None}


def node(root, name, deps=(), *, reviewer=False, delay=.12):
    return {"id": name, "owner": "bounded_worker", "action_class": "inspect", "workspace": str(root),
            "reads": [], "writes": [], "resources": [], "depends_on": list(deps),
            "brief": json.dumps({"delay": delay}), "envelope": envelope(name, root="root", reviewer=reviewer),
            "launch": {"kind": "native", "request": {"taskClass": "verification" if reviewer else "analysis",
                "reasoningPosture": "frontier", "semanticRole": "advisor" if reviewer else "task",
                "reviewMode": "exact-opposite" if reviewer else "none", "writerRole": "task" if reviewer else None}}}


def plan(root, workers=2):
    nodes = [node(root, f"worker{i}") for i in range(workers)]
    nodes.append(node(root, "review", [n['id'] for n in nodes], reviewer=True, delay=.02))
    return {"schema": md.SCHEMA, "request_id": "request", "envelope": envelope("root"),
            "nodes": nodes, "observation_seconds": .02}


class ManagedDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.workspace = self.directory / "workspace"
        self.workspace.mkdir()
        self.count = 0

    def controller(self, value=None):
        self.count += 1
        controller = md.Dispatcher(value or plan(self.workspace), self.directory / f"run-{self.count}")
        self.addCleanup(lambda: controller.cancel("test cleanup") if controller.handles else None)
        return controller

    @staticmethod
    def fixture_command(spec_path):
        return [sys.executable, "-c", FIXTURE, str(spec_path)]

    def run_fixture(self, controller):
        with mock.patch.object(md, "build_command", side_effect=self.fixture_command):
            return controller.run()

    def times(self, controller, name):
        directory = controller.run_dir / "nodes" / name
        return float((directory / "started").read_text()), float((directory / "ended").read_text())

    def test_real_parallel_overlap_join_and_terminal_lifecycle(self):
        controller = self.controller()
        result = self.run_fixture(controller)
        self.assertTrue(result["candidate_ready"])
        a, b, review = [self.times(controller, name) for name in ("worker0", "worker1", "review")]
        self.assertLess(max(a[0], b[0]), min(a[1], b[1]), "two real processes must overlap")
        self.assertGreaterEqual(review[0], max(a[1], b[1]))
        for name in controller.nodes:
            states = [e['event'] for e in controller.events if e['node_id'] == name and e['event'] in md.STATES]
            self.assertEqual(list(md.STATES), states)
        self.assertEqual("batch_result", controller.events[-1]['event'])
        self.assertEqual("delegated batch", controller.events[-1]['scope'])
        self.assertFalse(result['pending'])

    def test_progress_checkpoint_timeout_do_not_discharge_live_handles(self):
        controller = self.controller()
        with mock.patch.object(md, "build_command", side_effect=self.fixture_command):
            controller.tick()
            self.assertEqual(2, len(controller.handles))
            for kind in ("progress_update", "progress_update", "checkpoint", "observation_timeout"):
                controller.notify(kind, "awaiting authorized workers")
                self.assertFalse(controller.candidate_ready)
                self.assertEqual(2, len(controller.snapshot()['handles']))
                self.assertEqual(3, len(controller.snapshot()['pending']))
            self.assertTrue(controller.run()['candidate_ready'])
        self.assertIn("observation_timeout", [e['event'] for e in controller.events])

    def test_one_result_does_not_finish_while_peer_pending(self):
        value = plan(self.workspace)
        value['nodes'][0]['brief'] = json.dumps({'delay': .02})
        value['nodes'][1]['brief'] = json.dumps({'delay': .30})
        controller = self.controller(value)
        with mock.patch.object(md, "build_command", side_effect=self.fixture_command):
            deadline = time.monotonic() + 3
            while controller.states['worker0'] != 'complete' and time.monotonic() < deadline:
                controller.tick()
                time.sleep(.01)
            self.assertEqual('complete', controller.states['worker0'])
            self.assertEqual('running', controller.states['worker1'])
            self.assertFalse(controller.candidate_ready)
            self.assertNotIn('review', controller.handles)
            self.assertTrue(controller.run()['candidate_ready'])

    def test_failure_drains_unrelated_worker_without_top_fallback(self):
        value = plan(self.workspace)
        value['nodes'][0]['brief'] = json.dumps({'delay': .02, 'exit': 3})
        controller = self.controller(value)
        result = self.run_fixture(controller)
        self.assertFalse(result['candidate_ready'])
        self.assertEqual('failed', result['states']['worker0'])
        self.assertEqual('complete', result['states']['worker1'])
        self.assertEqual('planned', result['states']['review'])
        self.assertFalse(result['handles'])
        self.assertNotIn('final_return', [e['event'] for e in controller.events])
        self.assertEqual(2, len([e for e in controller.events if e['event'] == 'dispatched']))

    def test_pending_result_is_never_complete(self):
        value = plan(self.workspace, workers=1)
        value['nodes'][0]['brief'] = json.dumps({'status': 'pending'})
        result = self.run_fixture(self.controller(value))
        self.assertEqual('failed', result['states']['worker0'])
        self.assertFalse(result['candidate_ready'])

    def test_read_inspection_is_allowed_but_top_persistent_commands_rejected(self):
        md.action_preflight('inspect', 'top', [])
        md.action_preflight('disposable_verify', 'top', [])
        for cls, writes in [('mutation', ['source.py']), ('external_side_effect', []), ('disposable_verify', ['artifact.json'])]:
            with self.subTest(cls=cls), self.assertRaises(md.DispatchError):
                md.action_preflight(cls, 'top', writes)
        value = plan(self.workspace)
        value['nodes'][0]['owner'] = 'top'
        with self.assertRaises(md.DispatchError):
            self.controller(value)

    def test_disjoint_writers_overlap_but_parent_write_and_child_read_serialize(self):
        for collision in (False, True):
            value = plan(self.workspace)
            left, right = value['nodes'][:2]
            for worker in (left, right):
                worker['action_class'] = 'mutation'
                worker['envelope']['authority'] = 'write'
            left['writes'] = ['src/a']
            right['writes'] = ['src/b']
            if collision:
                left['writes'] = ['src']
                right['reads'] = ['src/a/file.py']
            controller = self.controller(value)
            self.assertTrue(self.run_fixture(controller)['candidate_ready'])
            a, b = self.times(controller, 'worker0'), self.times(controller, 'worker1')
            if collision:
                self.assertGreaterEqual(b[0], a[1])
                batches = [e for e in controller.events if e['event'] == 'dispatch_batch']
                self.assertTrue(batches[0]['serialization'][0]['serialization_reason'])
            else:
                self.assertLess(max(a[0], b[0]), min(a[1], b[1]))

    def test_resource_conflict_serializes_only_affected_pair(self):
        value = plan(self.workspace, workers=3)
        value['nodes'][0]['resources'] = ['gpu:0']
        value['nodes'][1]['resources'] = ['gpu:0']
        controller = self.controller(value)
        self.assertTrue(self.run_fixture(controller)['candidate_ready'])
        a, b, c = [self.times(controller, n) for n in ('worker0', 'worker1', 'worker2')]
        self.assertGreaterEqual(b[0], a[1])
        self.assertLess(max(a[0], c[0]), min(a[1], c[1]))

    def test_two_dependent_work_nodes_preserve_order(self):
        value = plan(self.workspace)
        value['nodes'][1]['depends_on'] = ['worker0']
        controller = self.controller(value)
        self.assertTrue(self.run_fixture(controller)['candidate_ready'])
        self.assertGreaterEqual(self.times(controller, 'worker1')[0], self.times(controller, 'worker0')[1])

    def test_single_leaf_does_not_invent_parallel_peer(self):
        controller = self.controller(plan(self.workspace, workers=1))
        self.assertTrue(self.run_fixture(controller)['candidate_ready'])
        self.assertEqual({'worker0', 'review'}, set(controller.nodes))
        batches = [e['dispatched_concurrently'] for e in controller.events if e['event'] == 'dispatch_batch']
        self.assertEqual([['worker0'], ['review']], batches)

    def test_unsupported_serial_choice_rejected_concrete_resource_cap_allowed(self):
        value = plan(self.workspace)
        value['max_concurrency'] = 1
        for reason in (None, {'kind': 'convenience', 'detail': 'I know the context and am faster'}):
            value['serialization_reason'] = reason
            with self.assertRaises(md.DispatchError):
                self.controller(value)
        value['serialization_reason'] = {'kind': 'resource_limit', 'detail': 'Account permits one active native call at a time'}
        controller = self.controller(value)
        self.assertTrue(self.run_fixture(controller)['candidate_ready'])
        self.assertGreaterEqual(self.times(controller, 'worker1')[0], self.times(controller, 'worker0')[1])

    def test_no_arbitrary_argv_or_model_override_surface(self):
        for field in ('argv', 'model', 'env', 'executable', 'receipt', 'shell'):
            value = plan(self.workspace)
            value['nodes'][0]['launch'][field] = '/bin/sh'
            with self.subTest(field=field), self.assertRaises(md.DispatchError):
                self.controller(value)

    def test_workflow_snapshot_conflicts_require_distinct_workspaces(self):
        value = plan(self.workspace)
        mapping = self.workspace / 'mapping.json'
        mapping.write_text('{}')
        first, second = value['nodes'][:2]
        first['owner'] = 'admitted_workflow'
        first['launch'] = {'kind': 'workflow', 'mapping': 'mapping.json'}
        self.assertTrue(md.conflicts(first, second))
        other = self.directory / 'worktree'
        other.mkdir()
        second['workspace'] = str(other)
        self.assertFalse(md.conflicts(first, second))
        second['owner'] = 'admitted_workflow'
        second['launch'] = {'kind': 'workflow', 'mapping': 'mapping.json'}
        (other / 'mapping.json').write_text('{}')
        controller = self.controller(value)
        self.assertTrue(self.run_fixture(controller)['candidate_ready'])
        a, b = self.times(controller, 'worker0'), self.times(controller, 'worker1')
        self.assertLess(max(a[0], b[0]), min(a[1], b[1]))

    def test_copy_on_write_workflow_returns_manifest_without_source_mutation(self):
        value = plan(self.workspace, workers=1)
        leaf = value['nodes'][0]
        leaf['owner'] = 'admitted_workflow'
        leaf['launch'] = {'kind': 'workflow', 'mapping': 'mapping.json'}
        leaf['workspace_mode'] = 'copy_on_write'
        (self.workspace / 'mapping.json').write_text(json.dumps({'designPath': 'design.json'}))
        (self.workspace / 'design.json').write_text(json.dumps({'nodes': []}))
        controller = self.controller(value)
        result = self.run_fixture(controller)
        self.assertTrue(result['candidate_ready'])
        event = next(e for e in controller.events if e['event'] == 'cow_workspace_finalized')
        cow_path = Path(event['workspace'])
        cow_result = json.loads((controller.run_dir / 'nodes/worker0/cow-result.json').read_text())
        self.assertIn('worker0.txt', cow_result['changed_paths'])
        self.assertFalse((self.workspace / 'worker0.txt').exists())
        self.assertTrue(any(binding['path'].endswith('cow-result.json') for binding in controller.results['worker0']['evidence_bindings']))
        md.cow_workspace.remove(cow_path)
        self.assertFalse(cow_path.exists())

    def test_scope_escape_symlinks_and_evidence_overlap_rejected(self):
        value = plan(self.workspace)
        worker = value['nodes'][0]
        worker['action_class'] = 'mutation'
        worker['envelope']['authority'] = 'write'
        for scope in ('../escape', '/tmp/escape'):
            worker['writes'] = [scope]
            with self.assertRaises(md.DispatchError):
                self.controller(value)
        (self.workspace / 'alias').symlink_to(self.directory, target_is_directory=True)
        worker['writes'] = ['alias/file']
        with self.assertRaises(md.DispatchError):
            self.controller(value)
        worker['writes'] = ['.']
        with self.assertRaises(md.DispatchError):
            md.Dispatcher(value, self.workspace / 'run')

    def test_dependency_cycle_and_unknown_dependency_rejected(self):
        value = plan(self.workspace)
        value['nodes'][0]['depends_on'] = ['worker1']
        value['nodes'][1]['depends_on'] = ['worker0']
        with self.assertRaises(md.DispatchError):
            self.controller(value)
        value = plan(self.workspace)
        value['nodes'][-1]['depends_on'] = ['unknown']
        with self.assertRaises(md.DispatchError):
            self.controller(value)

    def test_scope_and_review_receipt_mismatch_rejected(self):
        controller = self.controller(plan(self.workspace, workers=1))
        wrong = FIXTURE.replace("result['evidence_commitment']=contract['evidence_commitment']", "result['evidence_commitment']='sha256:wrong'")
        with mock.patch.object(md, 'build_command', side_effect=lambda path: [sys.executable, '-c', wrong, str(path)]):
            result = controller.run()
        self.assertFalse(result['candidate_ready'])
        self.assertEqual('failed', result['states']['review'])

    def test_reviewer_revise_routes_without_final_return(self):
        value = plan(self.workspace, workers=1)
        value['nodes'][-1]['brief'] = json.dumps({'verdict': 'REVISE'})
        controller = self.controller(value)
        result = self.run_fixture(controller)
        self.assertFalse(result['candidate_ready'])
        self.assertTrue(result['stopped'])
        self.assertFalse(result['handles'])
        self.assertNotIn('final_return', [e['event'] for e in controller.events])

    def test_child_cannot_expand_authority_budget_or_terminal_ownership(self):
        for field, change in [('authority', 'experiment'), ('terminalOwner', 'worker0'),
                              ('budgets', {'model_calls': 100, 'wall_time_seconds': 40000}),
                              ('budgets', {'model_calls': .5, 'wall_time_seconds': 40000})]:
            value = plan(self.workspace)
            value['nodes'][0]['envelope'][field] = change
            with self.subTest(field=field), self.assertRaises(md.DispatchError):
                self.controller(value)

    def test_invalid_or_expired_iteration_contract_is_not_an_authorization(self):
        value = plan(self.workspace)
        value['envelope']['iterationPolicy'] = 'human_guided'
        with self.assertRaises(md.DispatchError):
            self.controller(value)
        value['envelope']['iterationPolicy'] = {'iteration_mode_effective': 'human_guided'}
        with self.assertRaises(md.DispatchError):
            self.controller(value)

    def test_explicit_cancel_drains_processes_without_fake_completion(self):
        controller = self.controller()
        with mock.patch.object(md, 'build_command', side_effect=self.fixture_command):
            controller.tick()
            pids = [h['process'] for h in controller.handles.values()]
            controller.cancel('user cancelled this scoped request')
        self.assertTrue(all(p.poll() is not None for p in pids))
        self.assertFalse(controller.candidate_ready)
        self.assertFalse(controller.handles)

    def test_status_cli_reads_observable_live_handles(self):
        controller = self.controller()
        with mock.patch.object(md, 'build_command', side_effect=self.fixture_command):
            controller.tick()
            result = subprocess.run([sys.executable, str(SCRIPT), 'status', str(controller.run_dir)], capture_output=True, text=True, check=True)
            status = json.loads(result.stdout)
            self.assertEqual(2, len(status['handles']))
            self.assertFalse(status['candidate_ready'])
            controller.run()

    @unittest.skipUnless(shutil.which('bun'), 'Bun required for fixed native bridge fixture')
    def test_production_bridge_runs_real_native_runner_with_resolver_fixture(self):
        # Mock only fresh-catalog resolution; execute the real bridge, authorization
        # verifier, argv construction, native process, response normalization and trace.
        stub = self.directory / 'claude-fixture.py'
        marker = self.directory / 'actual-argv.json'
        stub.write_text('#!/usr/bin/python3\nimport json,pathlib,sys\n' +
                       f'pathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\n' +
                       'print(json.dumps({"summary":"fixture activation","statePatch":[{"op":"set","path":"/managedResult","value":{"schema":"managed-worker-result/v1","request_id":"request","node_id":"worker0","status":"complete","summary":"fixture result","evidence":[]}}]}))\n')
        stub.chmod(0o700)
        interpreter = Path(sys.executable).resolve()
        closure = [{'role': 'interpreter', 'path': str(interpreter), 'sha256': md.digest(interpreter.read_bytes())},
                   {'role': 'entrypoint', 'path': str(stub), 'sha256': md.digest(stub.read_bytes())}]
        closure_hash = md.digest('\n'.join(f"{e['role']}\t{e['path']}\t{e['sha256']}" for e in closure).encode())
        command = {'argv': ['claude', '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose'],
                   'executable': str(stub), 'executableSha256': md.digest(stub.read_bytes()),
                   'executionClosure': closure, 'executionClosureSha256': closure_hash}
        command['invokedArgv'] = [str(interpreter), str(stub)] + command['argv'][1:]
        receipt = {'schema': 'workflow-native-launch-receipt/v1', 'request': {}, 'writer': None,
                   'resolved': {'backend': 'claude', 'provider': 'anthropic', 'model': 'claude-opus-5',
                                'catalogModelId': 'claude-opus-5', 'effort': 'max'},
                   'policy': {'freshCatalogEvidence': {'claude': {'command': command}}}}
        receipt['receiptSha256'] = md.digest(md.canonical(receipt))
        directory = self.directory / 'bridge-evidence'
        directory.mkdir()
        models = self.workspace / 'config/workflows/models.json'
        models.parent.mkdir(parents=True)
        models.write_text('{}')
        leaf = node(self.workspace, 'worker0')
        launch = {'request_id': 'request', 'node': leaf, 'skills': str(md.SKILLS), 'evidence_dir': str(directory), 'prompt': 'Return the fixture result'}
        spec_file = directory / 'launch.json'
        spec_file.write_text(json.dumps(launch))
        bridge_file = self.directory / 'bridge.mjs'
        bridge_file.write_text(md.BRIDGE)
        resolver_path = md.SKILLS / 'subagent-workflow/scripts/controller/launch_resolver.ts'
        native_path = md.SKILLS / 'subagent-workflow/scripts/native/run.ts'
        # Native scrubEnv determines the canonical launch environment; retaining the
        # real function ensures the bridge cannot accidentally pass arbitrary env.
        driver = f'''import {{mock}} from "bun:test";
import {{scrubEnv}} from {json.dumps(str(native_path))};
const receipt={json.dumps(receipt)};
mock.module({json.dumps(str(resolver_path))},()=>({{createBoundLaunchResolver:()=>Object.assign(()=>receipt,{{launchEnvironment:scrubEnv(process.env)}})}}));
await import({json.dumps(str(bridge_file))});'''
        try:
            run = subprocess.run([shutil.which('bun'), '--eval', driver, str(spec_file)], capture_output=True, text=True, timeout=15)
        except subprocess.TimeoutExpired as exc:
            evidence = {p.name: p.read_text() for p in directory.iterdir() if p.is_file()}
            self.fail(f'bridge timeout: {exc.stdout!r} {exc.stderr!r}; evidence={evidence!r}')
        self.assertEqual(0, run.returncode, run.stderr)
        argv = json.loads(marker.read_text())
        self.assertIn('claude-opus-5', argv)
        self.assertIn('max', argv)
        self.assertIn('--safe-mode', argv)
        self.assertEqual('fixture result', json.loads(run.stdout)['summary'])
        self.assertTrue((directory / 'launch-receipt.json').is_file())
        self.assertTrue((directory / 'native-trace.jsonl').is_file())
        attempts = (directory / 'native-attempts.jsonl').read_text()
        self.assertIn('COMPLETED', attempts)


if __name__ == '__main__':
    unittest.main()
