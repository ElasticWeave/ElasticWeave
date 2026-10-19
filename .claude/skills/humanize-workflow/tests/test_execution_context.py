from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/execution_context.py"


def load_module():
    spec = importlib.util.spec_from_file_location("humanize_execution_context", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class HumanizeExecutionContextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.context_module = load_module()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.humanize_root = self.root / "humanize"
        source = self.humanize_root / "src/hmz"
        packages = self.humanize_root / ".venv/lib/python3.12/site-packages"
        source.mkdir(parents=True)
        packages.mkdir(parents=True)
        (source / "__init__.py").write_text("# pinned source\n", encoding="utf-8")
        (packages / "dependency.py").write_text("# pinned dependency\n", encoding="utf-8")
        interpreter = self.humanize_root / "python"
        interpreter.write_bytes(b"fake-python")
        interpreter.chmod(0o500)
        wrapper = self.humanize_root / ".venv/bin/hmz"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_bytes(b"fake-hmz")
        wrapper.chmod(0o500)
        self.pinned = {
            "argvPrefix": [
                str(interpreter), "-I", "-S", "-B", "-c", "fixed-code",
                str(self.humanize_root / "src"), str(packages),
            ],
            "binary": str(wrapper),
            "interpreter": str(interpreter),
            "root": str(self.humanize_root),
            "revision": "8fbb2b718d74b7ef1d3d0650a9768415ec489f26",
            "repository": "https://github.com/humanfia/humanize2.git",
            "version": "hmz 0.1.0",
        }
        self.account = self.context_module.AccountBinding(
            uid=os.geteuid(),
            gid=os.getegid(),
            name=pwd.getpwuid(os.geteuid()).pw_name,
            home=str(self.home),
            humanize_home=str(self.home / ".humanize"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _native(self, backend: str, marker: str) -> tuple[dict[str, Path], dict[str, object]]:
        folder = self.root / f"native-{backend}"
        folder.mkdir(exist_ok=True)
        executable = folder / backend
        payload = f"#!/bin/sh\nprintf '%s\\n' '{marker}'\n".encode()
        if executable.exists():
            executable.chmod(0o700)
        executable.write_bytes(payload)
        executable.chmod(0o500)
        sources = {backend: executable}
        closure = [{"role": "entrypoint", "path": str(executable), "sha256": digest(payload)}]
        if backend == "codex":
            companion = folder / "codex-code-mode-host"
            if companion.exists():
                companion.chmod(0o700)
            companion.write_bytes(b"codex companion")
            companion.chmod(0o500)
            sources["codex-code-mode-host"] = companion
        command = {
            "argv": [backend, "catalog"],
            "executable": str(executable),
            "executableSha256": digest(payload),
            "invokedArgv": [str(executable), "catalog"],
            "executionClosure": closure,
            "executionClosureSha256": digest(
                "\n".join(
                    f"{item['role']}\t{item['path']}\t{item['sha256']}" for item in closure
                ).encode()
            ),
        }
        return sources, command

    def _route(self, backend: str, command: dict[str, object], *, model: str = "model", effort: str = "max") -> dict[str, object]:
        return {
            "backend": backend,
            "catalogModelId": model,
            "model": model,
            "effort": effort,
            "launchReceipt": {
                "policy": {
                    "freshCatalogEvidence": {
                        backend: {
                            "backend": backend,
                            "command": command,
                            "coverage": {model: [effort]},
                        }
                    }
                }
            },
        }

    def _context(self, *, backend: str = "claude", provider_bindings=(), provider_probe=None):
        sources, command = self._native(backend, f"staged-{backend}")
        context = self.context_module.build_execution_context(
            pinned=self.pinned,
            routes=[self._route(backend, command)],
            sources={"main": b"print('sealed main')\n"},
            account_mode="named" if provider_bindings else "ambient",
            provider_bindings=list(provider_bindings),
            launcher_environment={"HUMANIZE_TEST_RECEIPT": "bound"},
            account_override=self.account,
            native_source_override={backend: sources},
            provider_probe=provider_probe,
        )
        return context, sources

    def test_poisoned_caller_environment_is_not_inherited(self) -> None:
        poisoned = {
            "HOME": "/attacker/home",
            "PATH": "/attacker/bin",
            "BASH_ENV": "/attacker/bashrc",
            "ENV": "/attacker/shrc",
            "PYTHONPATH": "/attacker/python",
            "NODE_OPTIONS": "--require=/attacker/node.js",
            "LD_PRELOAD": "/attacker/preload.so",
            "HTTPS_PROXY": "http://attacker",
            "SSL_CERT_FILE": "/attacker/ca.pem",
            "CODEX_HOME": "/attacker/codex",
            "CLAUDE_CONFIG_DIR": "/attacker/claude",
            "GENERAL_AGENT_MODEL": "attacker",
            "HUMANIZE_HOME": "/attacker/humanize",
            "HMZ_FLOW": "/attacker/flow.py",
        }
        with mock.patch.dict(os.environ, poisoned, clear=False):
            context, _ = self._context()
        try:
            self.assertEqual(context.environment["HOME"], str(self.home))
            self.assertEqual(context.environment["HUMANIZE_HOME"], str(self.home / ".humanize"))
            self.assertEqual(context.environment["PATH"].split(os.pathsep)[0], str(context.bin_dir))
            for name, value in poisoned.items():
                self.assertNotEqual(context.environment.get(name), value, name)
            self.assertEqual(context.environment["HUMANIZE_TEST_RECEIPT"], "bound")
            self.assertEqual(context.receipt["account"]["mode"], "ambient")
        finally:
            context.cleanup()

    def test_bare_native_name_executes_private_staged_bytes(self) -> None:
        context, sources = self._context()
        source = sources["claude"]
        try:
            source.chmod(0o700)
            source.write_text("#!/bin/sh\nprintf '%s\\n' 'mutated-source'\n", encoding="utf-8")
            source.chmod(0o500)
            completed = subprocess.run(
                ["claude"],
                text=True,
                capture_output=True,
                check=False,
                env=context.environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "staged-claude\n")
            self.assertEqual(stat.S_IMODE(context.bin_dir.stat().st_mode), 0o700)
        finally:
            context.cleanup()

    def test_codex_companion_is_required_and_staged(self) -> None:
        context, sources = self._context(backend="codex")
        try:
            self.assertTrue((context.bin_dir / "codex").is_file())
            self.assertTrue((context.bin_dir / "codex-code-mode-host").is_file())
            context.revalidate()
        finally:
            context.cleanup()

        sources, command = self._native("codex", "missing-companion")
        sources.pop("codex-code-mode-host").unlink()
        with self.assertRaisesRegex(self.context_module.ExecutionContextError, "companion"):
            self.context_module.build_execution_context(
                pinned=self.pinned,
                routes=[self._route("codex", command)],
                sources={"main": b"pass\n"},
                account_mode="ambient",
                account_override=self.account,
                native_source_override={"codex": sources},
            )

    def test_provider_record_injection_and_ambient_catalog_fail_closed(self) -> None:
        provider_dir = self.home / ".humanize/providers/claude/review"
        provider_dir.mkdir(parents=True)
        record = {
            "cli": "claude",
            "name": "review",
            "way": "env",
            "env": {"PYTHONPATH": "/attacker"},
            "args": [],
            "made": "test",
        }
        raw = (json.dumps(record, sort_keys=True) + "\n").encode()
        record_path = provider_dir / "provider.json"
        record_path.write_bytes(raw)
        binding = {
            "backend": "claude",
            "providerAccount": "review",
            "providerRecordSha256": digest(raw),
            "providerRecordPath": str(record_path),
            "catalogModelId": "model",
            "effort": "max",
        }
        with self.assertRaisesRegex(self.context_module.ExecutionContextError, "provider environment"):
            self._context(provider_bindings=[binding], provider_probe=lambda *_: (0, b'[]\n', b''))

        record["env"] = {"ANTHROPIC_API_KEY": "secret"}
        raw = (json.dumps(record, sort_keys=True) + "\n").encode()
        record_path.write_bytes(raw)
        binding["providerRecordSha256"] = digest(raw)
        with self.assertRaisesRegex(self.context_module.ExecutionContextError, "exact model"):
            self._context(provider_bindings=[binding], provider_probe=lambda *_: (0, b'{"models":[]}\n', b''))
        record_path.unlink()
        with self.assertRaisesRegex(self.context_module.ExecutionContextError, "provider record"):
            self._context(
                provider_bindings=[binding],
                provider_probe=lambda *_: (0, b'{"models":[{"name":"model","efforts":["max"]}]}\n', b""),
            )

    def test_provider_record_rejects_a_symlinked_parent_directory(self) -> None:
        providers = self.home / ".humanize/providers"
        outside = self.root / "outside-provider-root"
        record_path = providers / "claude/review/provider.json"
        actual = outside / "review/provider.json"
        actual.parent.mkdir(parents=True)
        providers.mkdir(parents=True)
        (providers / "claude").symlink_to(outside, target_is_directory=True)
        record = {
            "cli": "claude",
            "name": "review",
            "way": "key",
            "env": {"ANTHROPIC_API_KEY": "secret"},
            "args": [],
            "made": "test",
        }
        raw = (json.dumps(record, sort_keys=True) + "\n").encode()
        actual.write_bytes(raw)
        binding = {
            "backend": "claude",
            "providerAccount": "review",
            "providerRecordSha256": digest(raw),
            "providerRecordPath": str(record_path),
            "catalogModelId": "model",
            "effort": "max",
        }
        with self.assertRaisesRegex(
            self.context_module.ExecutionContextError, "cannot be opened without following links"
        ):
            self._context(
                provider_bindings=[binding],
                provider_probe=lambda *_: (0, b'{"models":[{"name":"model","efforts":["max"]}]}\n', b""),
            )

    def test_provider_probe_receipt_and_mutation_revalidation(self) -> None:
        provider_dir = self.home / ".humanize/providers/claude/review"
        provider_dir.mkdir(parents=True)
        record = {
            "cli": "claude",
            "name": "review",
            "way": "key",
            "env": {"ANTHROPIC_API_KEY": "secret"},
            "args": [],
            "made": "test",
        }
        raw = (json.dumps(record, sort_keys=True) + "\n").encode()
        record_path = provider_dir / "provider.json"
        record_path.write_bytes(raw)
        binding = {
            "backend": "claude",
            "providerAccount": "review",
            "providerRecordSha256": digest(raw),
            "providerRecordPath": str(record_path),
            "catalogModelId": "model",
            "effort": "max",
        }

        def probe(backend, account, environment):
            self.assertEqual((backend, account), ("claude", "review"))
            staged = Path(environment["PATH"].split(os.pathsep)[0])
            self.assertEqual(staged.name, "bin")
            self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o700)
            return 0, b'{"models":[{"name":"model","efforts":["max"]}]}\n', b""

        active, _ = self._context(provider_bindings=[binding], provider_probe=probe)
        try:
            provider_receipt = active.receipt["providerCatalogs"][0]
            self.assertEqual(provider_receipt["account"], "review")
            self.assertEqual(provider_receipt["catalogModelId"], "model")
            self.assertNotIn("secret", json.dumps(active.receipt))
            record_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(self.context_module.ExecutionContextError, "provider record"):
                active.revalidate()
        finally:
            active.cleanup()

    def test_sealed_sources_detect_mutation_and_cleanup_always(self) -> None:
        context, _ = self._context()
        run_root = context.run_root
        sealed = context.sources["main"]
        try:
            self.assertTrue(context.is_sealed(sealed.fd))
            with self.assertRaises(OSError):
                os.write(sealed.fd, b"mutate")
            staged = context.bin_dir / "claude"
            staged.chmod(0o700)
            staged.write_text("mutated", encoding="utf-8")
            with self.assertRaisesRegex(self.context_module.ExecutionContextError, "staged native"):
                context.revalidate()
        finally:
            context.cleanup()
        self.assertFalse(run_root.exists())


if __name__ == "__main__":
    unittest.main()
