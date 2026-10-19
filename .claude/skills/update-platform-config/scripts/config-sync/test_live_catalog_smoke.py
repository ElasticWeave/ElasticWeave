from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("live_catalog_smoke", HERE / "live_catalog.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

CatalogError = MODULE.CatalogError
produce = MODULE.produce


def _trusted_executable(resolved: str) -> bool:
    home = MODULE._home_root()
    for root in (home, "/usr/local", "/usr"):
        if resolved == root or resolved.startswith(root + os.sep):
            return True
    return False


# Each installed catalog is invoked exactly once for the whole smoke module. A
# per-test invocation would multiply real authenticated calls with no added
# coverage, and repeated calls could observe different catalog snapshots.
_ONCE: dict[str, object] = {}


def _catalog_once(backend: str):
    """Return (evidence, error) for `backend`, invoking the real command at most once.

    `error is None` means a genuine live pass; a non-None error is the exact
    reason the caller must report as a skip, so a skip can never be mistaken for
    a pass.
    """
    if backend not in _ONCE:
        try:
            started = time.perf_counter()
            evidence = produce(backend)
            _ONCE[backend] = (evidence, None, time.perf_counter() - started)
        except CatalogError as error:
            _ONCE[backend] = (None, error, None)
    return _ONCE[backend]  # type: ignore[return-value]


class LiveCatalogSmokeTest(unittest.TestCase):
    """Live production smoke for the canonical Claude/Codex/Cursor installs.

    These tests invoke the real authenticated native catalog commands through the
    fixed trusted install-location resolver, once per backend for the module.
    They prove the canonical symlink chains resolve to a regular executable under
    a trusted root, that the bound execution closure matches what is actually
    invoked, and that the parsed fresh catalog covers the configured roles. They
    fail closed (raise CatalogError, reported as an explicit skip with the exact
    reason) if the binary is not installed at a trusted location.

    Trust boundary: resolution accepts targets owned by this uid under this
    account home. A process already running as this uid can rewrite that tree;
    that is an accepted same-UID boundary, not a defended one. Post-exit rehash
    catches a persistent swap. A swap-and-restore during the running command is
    the residual OS trust window.
    """

    def _require(self, backend: str):
        evidence, error, _elapsed = _catalog_once(backend)
        if evidence is None:
            self.skipTest(f"{backend} not available at a trusted install location: {error}")
        return evidence

    def test_codex_canonical_symlink_resolves_and_covers_sol_and_luna(self):
        evidence = self._require("codex")
        record = evidence.record()
        self.assertEqual("codex", record["backend"])
        self.assertEqual(["codex", "debug", "models"], record["command"]["argv"])
        self.assertTrue(_trusted_executable(record["command"]["executable"]))
        self.assertTrue(record["command"]["executableSha256"].startswith("sha256:"))
        self.assertEqual(0, record["exitCode"])
        coverage = evidence.coverage
        self.assertIn("gpt-5.6-sol", coverage)
        self.assertIn("max", coverage["gpt-5.6-sol"])
        self.assertIn("gpt-5.6-luna", coverage)
        self.assertIn("max", coverage["gpt-5.6-luna"])
        # A static ELF entry point is executed directly, so the closure is the
        # entry point alone and invokedArgv starts with the bound target.
        self.assertEqual(["entrypoint"], [e["role"] for e in record["command"]["executionClosure"]])
        self.assertEqual(
            [record["command"]["executable"], "debug", "models"],
            record["command"]["invokedArgv"],
        )

    def test_cursor_canonical_symlink_resolves_and_parses_tip_trailer(self):
        evidence = self._require("cursor")
        record = evidence.record()
        self.assertEqual("cursor", record["backend"])
        self.assertEqual(["cursor-agent", "models"], record["command"]["argv"])
        self.assertTrue(_trusted_executable(record["command"]["executable"]))
        self.assertTrue(record["command"]["executableSha256"].startswith("sha256:"))
        self.assertEqual(0, record["exitCode"])
        ids = MODULE.cursor_catalog_ids(evidence)
        self.assertIn("claude-fable-5-1-thinking-max", ids)
        self.assertIn("claude-opus-5-thinking-max", ids)
        self.assertIn("claude-sonnet-5-thinking-high", ids)
        self.assertIn("cursor-grok-4.6-xhigh", ids)
        self.assertIn("gpt-5.6-sol-max", ids)
        self.assertIn("gpt-5.6-luna-max", ids)
        self.assertEqual(
            "max",
            MODULE.highest_available_effort("cursor", "claude-fable-5-1", evidence),
        )
        self.assertEqual(
            "xhigh",
            MODULE.highest_available_effort("cursor", "grok-4.6", evidence),
        )
        # The exact Tip trailer must have been parsed (the raw bytes contain it).
        self.assertIn(MODULE.CURSOR_TIP_TRAILER, evidence.raw_bytes.decode("utf-8", errors="replace"))

    def test_claude_control_catalog_covers_native_advisor(self):
        evidence = self._require("claude")
        record = evidence.record()
        self.assertEqual("claude", record["backend"])
        self.assertEqual(
            [
                "claude", "-p", "--input-format", "stream-json",
                "--output-format", "stream-json", "--verbose",
            ],
            record["command"]["argv"],
        )
        self.assertTrue(_trusted_executable(record["command"]["executable"]))
        self.assertEqual(0, record["exitCode"])
        self.assertEqual(
            "claude-opus-5",
            MODULE.verify_role("claude", "claude-opus-5", "max", evidence),
        )
        expected_efforts = ["low", "medium", "high", "xhigh", "max"]
        for model in ("claude-opus-5", "claude-sonnet-5"):
            with self.subTest(model=model):
                self.assertEqual(expected_efforts, evidence.coverage[model])

    def test_cursor_wrapper_closure_binds_interpreter_and_entry_point(self):
        evidence = self._require("cursor")
        record = evidence.record()
        closure = record["command"]["executionClosure"]
        roles = [entry["role"] for entry in closure]
        # The installed cursor-agent entry point is a bash wrapper. Catalog
        # launch skips that wrapper and invokes the bound absolute node plus
        # absolute index.js; the receipt still names the wrapper and the
        # complete install manifest, including dynamically loaded chunks.
        self.assertEqual(["entrypoint", "runtime", "bundle"], roles)
        self.assertEqual(record["command"]["executable"], closure[0]["path"])
        install = os.path.dirname(record["command"]["executable"])
        self.assertEqual(os.path.join(install, "node"), closure[1]["path"])
        self.assertEqual(os.path.join(install, "index.js"), closure[2]["path"])
        self.assertEqual(
            [closure[1]["path"], "--use-system-ca", closure[2]["path"], "models"],
            record["command"]["invokedArgv"],
        )
        for entry in closure:
            self.assertTrue(entry["sha256"].startswith("sha256:"))
        self.assertTrue(record["command"]["executionClosureSha256"].startswith("sha256:"))
        relative = [member.relative_path for member in evidence.executable.install_manifest]
        self.assertIn("node", relative)
        self.assertIn("index.js", relative)
        self.assertIn("cursor-agent", relative)
        self.assertTrue(any(name.endswith(".index.js") and name != "index.js" for name in relative))
        self.assertGreater(len(relative), 100)
        self.assertFalse(any(
            path == ".running" or path.startswith(".running/") for path in relative
        ))
        self.assertTrue(evidence.executable.install_manifest_sha256.startswith("sha256:"))
        _, _, elapsed = _catalog_once("cursor")
        self.assertIsNotNone(elapsed)
        # Pre-exec plus post-exit full-manifest hashing of the installed tree
        # (~hundreds of MB) must finish in a bounded window.
        self.assertLess(elapsed, 120.0)

    def test_cursor_normalize_covers_configured_anthropic_and_openai_roles(self):
        evidence = self._require("cursor")
        ids = set(MODULE.cursor_catalog_ids(evidence))
        for base, effort, expected in (
            ("claude-fable-5-1", "max", "claude-fable-5-1-thinking-max"),
            ("claude-opus-5", "max", "claude-opus-5-thinking-max"),
            ("claude-opus-5", "xhigh", "claude-opus-5-thinking-xhigh"),
            ("claude-sonnet-5", "high", "claude-sonnet-5-thinking-high"),
            ("grok-4.6", "xhigh", "cursor-grok-4.6-xhigh"),
            ("gpt-5.6-sol", "max", "gpt-5.6-sol-max"),
            ("gpt-5.6-luna", "max", "gpt-5.6-luna-max"),
        ):
            self.assertEqual(expected, MODULE.normalize_cursor_catalog_id(base, effort))
            self.assertIn(expected, ids)


if __name__ == "__main__":
    unittest.main()
