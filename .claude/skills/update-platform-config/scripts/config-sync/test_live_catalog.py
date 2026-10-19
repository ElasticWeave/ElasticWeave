from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("live_catalog", HERE / "live_catalog.py")
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

CatalogError = MODULE.CatalogError
produce = MODULE.produce
verify_role = MODULE.verify_role
normalize_cursor_catalog_id = MODULE.normalize_cursor_catalog_id
highest_available_effort = MODULE.highest_available_effort
_resolve_executable = MODULE._resolve_executable
_scrub_env = MODULE._scrub_env


def sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def codex_catalog_bytes(slug_levels: dict[str, list[str]]) -> bytes:
    return (
        json.dumps(
            {
                "models": [
                    {
                        "slug": slug,
                        "default_reasoning_level": levels[0] if levels else "low",
                        "supported_reasoning_levels": [{"effort": e} for e in levels],
                    }
                    for slug, levels in slug_levels.items()
                ]
            }
        ).encode("utf-8")
        + b"\n"
    )


def cursor_catalog_bytes(ids: list[str], *, with_tip: bool = True) -> bytes:
    """Build authentic-shaped cursor `models` stdout.

    The exact terminal Tip trailer is mandatory in production, so it is on by
    default here; `with_tip=False` builds the rejected shape.
    """
    body = "Available models\n\n" + "".join(f"{i} - {i.upper()}\n" for i in ids)
    if with_tip:
        body += MODULE.CURSOR_TIP_TRAILER + "\n"
    return body.encode("utf-8")


def claude_catalog_bytes(
    models: list[dict[str, object]], *, prefix: list[dict[str, object]] | None = None,
) -> bytes:
    messages = list(prefix or [])
    messages.append(
        {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "models",
                "response": {"models": models},
            },
        }
    )
    return b"".join(
        json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        for message in messages
    )


def runner_returning(returncode: int, stdout: bytes, stderr: bytes = b""):
    def runner(argv):
        return returncode, stdout, stderr
    return runner


class LiveCatalogCommandContractTest(unittest.TestCase):
    def test_fixed_argv_only_no_caller_override(self):
        self.assertEqual(("codex", "debug", "models"), MODULE.ALLOWED_COMMANDS["codex"])
        self.assertEqual(
            (
                "claude", "-p", "--input-format", "stream-json",
                "--output-format", "stream-json", "--verbose",
            ),
            MODULE.ALLOWED_COMMANDS["claude"],
        )
        self.assertEqual(("cursor-agent", "models"), MODULE.ALLOWED_COMMANDS["cursor"])
        self.assertEqual(
            b'{"type": "control_request", "request_id": "models", '
            b'"request": {"subtype": "list_models"}}\n',
            MODULE.CLAUDE_CONTROL_REQUEST,
        )

    def test_unknown_backend_fails_closed(self):
        with self.assertRaisesRegex(CatalogError, "no fixed catalog command"):
            produce("nonexistent")

    def test_cli_rejects_trailing_arguments_before_catalog_execution(self):
        with (
            mock.patch.object(MODULE, "produce") as produce_mock,
            mock.patch("builtins.print"),
        ):
            self.assertEqual(2, MODULE.main(["claude", "--input", "alternate-request"]))
        produce_mock.assert_not_called()


class ClaudeCatalogParseTest(unittest.TestCase):
    def test_parses_matching_success_and_strips_terminal_window_suffix(self):
        raw = claude_catalog_bytes(
            [
                {
                    "value": "opus",
                    "resolvedModel": "claude-opus-5[1m]",
                    "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
                },
                {
                    "value": "claude-sonnet-5[200k]",
                    "resolvedModel": "",
                    "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"],
                },
            ],
            prefix=[{"type": "system", "subtype": "init"}],
        )
        evidence = produce("claude", runner=runner_returning(0, raw))
        self.assertEqual(
            ["low", "medium", "high", "xhigh", "max"],
            evidence.coverage["claude-opus-5"],
        )
        self.assertIn("claude-sonnet-5", evidence.coverage)
        self.assertEqual(
            "claude-opus-5",
            verify_role("claude", "claude-opus-5", "max", evidence),
        )

    def test_rejects_duplicate_matching_control_responses(self):
        one = claude_catalog_bytes(
            [{"resolvedModel": "claude-opus-5", "supportedEffortLevels": ["max"]}]
        )
        with self.assertRaisesRegex(CatalogError, "duplicate matching control responses"):
            produce("claude", runner=runner_returning(0, one + one))

    def test_rejects_failed_matching_control_response(self):
        raw = (
            json.dumps(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "error",
                        "request_id": "models",
                        "error": "catalog unavailable",
                    },
                }
            ).encode("utf-8")
            + b"\n"
        )
        with self.assertRaisesRegex(CatalogError, "catalog unavailable"):
            produce("claude", runner=runner_returning(0, raw))

    def test_rejects_malformed_ndjson_even_before_matching_response(self):
        raw = b"not-json\n" + claude_catalog_bytes(
            [{"resolvedModel": "claude-opus-5", "supportedEffortLevels": ["max"]}]
        )
        with self.assertRaisesRegex(CatalogError, "line 1 is not valid JSON"):
            produce("claude", runner=runner_returning(0, raw))

    def test_omits_non_routable_models_without_effort_coverage(self):
        raw = claude_catalog_bytes(
            [
                {"resolvedModel": "claude-haiku-4-5-20251001"},
                {"resolvedModel": "claude-haiku-4-5", "supportedEffortLevels": []},
                {"resolvedModel": "claude-opus-5", "supportedEffortLevels": ["max"]},
            ]
        )
        evidence = produce("claude", runner=runner_returning(0, raw))
        self.assertEqual({"claude-opus-5": ["max"]}, evidence.coverage)
        with self.assertRaisesRegex(CatalogError, "does not list model"):
            verify_role("claude", "claude-haiku-4-5", "max", evidence)

    def test_rejects_catalog_without_any_effort_coverage(self):
        raw = claude_catalog_bytes(
            [{"resolvedModel": "claude-haiku-4-5", "supportedEffortLevels": []}]
        )
        with self.assertRaisesRegex(CatalogError, "no models with supported effort coverage"):
            produce("claude", runner=runner_returning(0, raw))

    def test_rejects_malformed_effort_coverage(self):
        raw = claude_catalog_bytes(
            [{"resolvedModel": "claude-opus-5", "supportedEffortLevels": "max"}]
        )
        with self.assertRaisesRegex(CatalogError, "malformed supportedEffortLevels"):
            produce("claude", runner=runner_returning(0, raw))

    def test_rejects_unknown_or_duplicate_effort(self):
        for efforts, message in ((["max", "turbo"], "unknown effort"), (["max", "max"], "duplicates effort")):
            with self.subTest(efforts=efforts):
                raw = claude_catalog_bytes(
                    [{"resolvedModel": "claude-opus-5", "supportedEffortLevels": efforts}]
                )
                with self.assertRaisesRegex(CatalogError, message):
                    produce("claude", runner=runner_returning(0, raw))

    def test_rejects_conflicting_duplicate_normalized_model(self):
        raw = claude_catalog_bytes(
            [
                {"resolvedModel": "claude-opus-5[1m]", "supportedEffortLevels": ["high", "max"]},
                {"resolvedModel": "claude-opus-5", "supportedEffortLevels": ["max"]},
            ]
        )
        with self.assertRaisesRegex(CatalogError, "conflicting duplicate model"):
            produce("claude", runner=runner_returning(0, raw))

    def test_uncovered_effort_fails_closed(self):
        raw = claude_catalog_bytes(
            [{"resolvedModel": "claude-opus-5", "supportedEffortLevels": ["high"]}]
        )
        evidence = produce("claude", runner=runner_returning(0, raw))
        with self.assertRaisesRegex(CatalogError, "does not support effort"):
            verify_role("claude", "claude-opus-5", "max", evidence)

    def test_real_subprocess_receives_exact_fixed_control_request(self):
        executable = MODULE._synthetic_executable(MODULE.ALLOWED_COMMANDS["claude"])
        completed = SimpleNamespace(returncode=0, stdout=b"reply", stderr=b"")
        with mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                (0, b"reply", b""),
                MODULE._run_command(executable, runner=None),
            )
        _, kwargs = run.call_args
        self.assertEqual(MODULE.CLAUDE_CONTROL_REQUEST, kwargs["input"])
        self.assertNotIn("stdin", kwargs)
        self.assertFalse(kwargs["shell"])


class CodexCatalogParseTest(unittest.TestCase):
    def test_parses_slug_and_supported_efforts(self):
        raw = codex_catalog_bytes({"gpt-5.6-sol": ["low", "medium", "high", "xhigh", "max", "ultra"]})
        evidence = produce("codex", runner=runner_returning(0, raw))
        self.assertEqual("codex", evidence.backend)
        self.assertEqual(sha256(raw), evidence.bytes_sha256)
        self.assertEqual(
            ["low", "medium", "high", "xhigh", "max", "ultra"],
            evidence.coverage["gpt-5.6-sol"],
        )

    def test_parses_exact_astra_identity_when_the_catalog_exposes_it(self):
        raw = codex_catalog_bytes({"gpt-6-astra": ["low", "high", "max"]})
        evidence = produce("codex", runner=runner_returning(0, raw))
        self.assertEqual(
            "gpt-6-astra",
            verify_role("codex", "gpt-6-astra", "max", evidence),
        )
        self.assertEqual(
            "max",
            highest_available_effort("codex", "gpt-6-astra", evidence),
        )

    def test_rejects_non_zero_exit(self):
        with self.assertRaisesRegex(CatalogError, "exited 2"):
            produce("codex", runner=runner_returning(2, b"", b"auth error"))

    def test_rejects_empty_stdout(self):
        with self.assertRaisesRegex(CatalogError, "no stdout bytes"):
            produce("codex", runner=runner_returning(0, b""))

    def test_rejects_malformed_json(self):
        with self.assertRaisesRegex(CatalogError, "not valid UTF-8 JSON"):
            produce("codex", runner=runner_returning(0, b"not json"))

    def test_rejects_missing_models_list(self):
        with self.assertRaisesRegex(CatalogError, "models list"):
            produce("codex", runner=runner_returning(0, b"{}"))

    def test_rejects_entry_without_slug(self):
        raw = b'{"models": [{"supported_reasoning_levels": [{"effort": "low"}]}]}'
        with self.assertRaisesRegex(CatalogError, "string slug"):
            produce("codex", runner=runner_returning(0, raw))

    def test_rejects_unknown_effort_label(self):
        raw = codex_catalog_bytes({"gpt-5.6-sol": ["low", "turbo"]})
        with self.assertRaisesRegex(CatalogError, "unknown effort"):
            produce("codex", runner=runner_returning(0, raw))

    def test_rejects_duplicate_effort(self):
        raw = codex_catalog_bytes({"gpt-5.6-sol": ["low", "low"]})
        with self.assertRaisesRegex(CatalogError, "duplicates effort"):
            produce("codex", runner=runner_returning(0, raw))


class CursorCatalogParseTest(unittest.TestCase):
    def test_parses_concrete_ids(self):
        raw = cursor_catalog_bytes(
            ["claude-opus-5-thinking-max", "claude-sonnet-5-thinking-high", "gpt-5.6-sol-max"]
        )
        evidence = produce("cursor", runner=runner_returning(0, raw))
        self.assertEqual("cursor", evidence.backend)
        self.assertIn("claude-opus-5-thinking-max", MODULE.cursor_catalog_ids(evidence))

    def test_rejects_line_without_dash_display(self):
        raw = b"Available models\n\nclaude-opus-5-thinking-max\n"
        with self.assertRaisesRegex(CatalogError, "not '<id> - <display>'"):
            produce("cursor", runner=runner_returning(0, raw))

    def test_rejects_missing_tip_trailer(self):
        raw = cursor_catalog_bytes(["claude-opus-5-thinking-max"], with_tip=False)
        with self.assertRaisesRegex(CatalogError, "missing the exact terminal Tip trailer"):
            produce("cursor", runner=runner_returning(0, raw))

    def test_rejects_duplicate_id(self):
        raw = cursor_catalog_bytes(["claude-opus-5-thinking-max", "claude-opus-5-thinking-max"])
        with self.assertRaisesRegex(CatalogError, "duplicate id"):
            produce("cursor", runner=runner_returning(0, raw))


class CursorNormalizationTest(unittest.TestCase):
    def test_anthropic_fable_max_maps_to_thinking_variant(self):
        self.assertEqual(
            "claude-fable-5-1-thinking-max",
            normalize_cursor_catalog_id("claude-fable-5-1", "max"),
        )

    def test_stale_fable_5_family_has_no_normalization_route(self):
        with self.assertRaisesRegex(CatalogError, "no audited normalization rule"):
            normalize_cursor_catalog_id("claude-fable-5", "max")

    def test_anthropic_opus_max_maps_to_thinking_variant(self):
        self.assertEqual(
            "claude-opus-5-thinking-max",
            normalize_cursor_catalog_id("claude-opus-5", "max"),
        )

    def test_anthropic_sonnet_high_maps_to_thinking_variant(self):
        self.assertEqual(
            "claude-sonnet-5-thinking-high",
            normalize_cursor_catalog_id("claude-sonnet-5", "high"),
        )

    def test_openai_sol_max_maps_to_plain_variant(self):
        self.assertEqual(
            "gpt-5.6-sol-max",
            normalize_cursor_catalog_id("gpt-5.6-sol", "max"),
        )

    def test_openai_luna_max_maps_to_plain_variant(self):
        self.assertEqual(
            "gpt-5.6-luna-max",
            normalize_cursor_catalog_id("gpt-5.6-luna", "max"),
        )

    def test_grok_maps_to_audited_cursor_concrete_id(self):
        self.assertEqual(
            "cursor-grok-4.6-xhigh",
            normalize_cursor_catalog_id("grok-4.6", "xhigh"),
        )
        self.assertEqual(
            "cursor-grok-4.5-high",
            normalize_cursor_catalog_id("cursor-grok-4.5", "high"),
        )

    def test_unknown_family_fails_closed(self):
        with self.assertRaisesRegex(CatalogError, "no audited normalization rule"):
            normalize_cursor_catalog_id("gemini-3.7", "high")

    def test_unknown_effort_fails_closed(self):
        with self.assertRaisesRegex(CatalogError, "not a known reasoning level"):
            normalize_cursor_catalog_id("claude-opus-5", "turbo")


class RoleVerificationTest(unittest.TestCase):
    def test_highest_available_cursor_effort_uses_provider_local_order(self):
        raw = cursor_catalog_bytes(
            [
                "cursor-grok-4.6-low",
                "cursor-grok-4.6-high",
                "cursor-grok-4.6-xhigh",
            ]
        )
        evidence = produce("cursor", runner=runner_returning(0, raw))
        self.assertEqual(
            "xhigh",
            highest_available_effort("cursor", "grok-4.6", evidence),
        )

    def test_highest_available_effort_rejects_absent_model_family(self):
        raw = cursor_catalog_bytes(["cursor-grok-4.5-xhigh"])
        evidence = produce("cursor", runner=runner_returning(0, raw))
        with self.assertRaisesRegex(CatalogError, "no supported effort"):
            highest_available_effort("cursor", "grok-4.6", evidence)

    def test_codex_role_with_supported_effort_verifies(self):
        raw = codex_catalog_bytes({"gpt-5.6-sol": ["low", "medium", "high", "xhigh", "max"]})
        evidence = produce("codex", runner=runner_returning(0, raw))
        self.assertEqual("gpt-5.6-sol", verify_role("codex", "gpt-5.6-sol", "max", evidence))

    def test_codex_role_with_unsupported_effort_fails_closed(self):
        raw = codex_catalog_bytes({"gpt-5.6-sol": ["low", "medium"]})
        evidence = produce("codex", runner=runner_returning(0, raw))
        with self.assertRaisesRegex(CatalogError, "does not support effort"):
            verify_role("codex", "gpt-5.6-sol", "max", evidence)

    def test_codex_role_with_absent_model_fails_closed(self):
        raw = codex_catalog_bytes({"gpt-5.6-luna": ["max"]})
        evidence = produce("codex", runner=runner_returning(0, raw))
        with self.assertRaisesRegex(CatalogError, "does not list model"):
            verify_role("codex", "gpt-5.6-sol", "max", evidence)

    def test_cursor_anthropic_role_verifies_against_thinking_id(self):
        raw = cursor_catalog_bytes(["claude-opus-5-thinking-max", "claude-opus-5-thinking-xhigh"])
        evidence = produce("cursor", runner=runner_returning(0, raw))
        self.assertEqual(
            "claude-opus-5-thinking-max",
            verify_role("cursor", "claude-opus-5", "max", evidence),
        )

    def test_cursor_grok_role_verifies_when_fresh_catalog_covers_effort(self):
        raw = cursor_catalog_bytes(["cursor-grok-4.6-xhigh"])
        evidence = produce("cursor", runner=runner_returning(0, raw))
        self.assertEqual(
            "cursor-grok-4.6-xhigh",
            verify_role("cursor", "grok-4.6", "xhigh", evidence),
        )

    def test_cursor_role_with_absent_concrete_id_fails_closed(self):
        raw = cursor_catalog_bytes(["claude-opus-5-thinking-xhigh"])
        evidence = produce("cursor", runner=runner_returning(0, raw))
        with self.assertRaisesRegex(CatalogError, "does not list concrete id"):
            verify_role("cursor", "claude-opus-5", "max", evidence)

    def test_backend_mismatch_fails_closed(self):
        raw = codex_catalog_bytes({"gpt-5.6-sol": ["max"]})
        evidence = produce("codex", runner=runner_returning(0, raw))
        with self.assertRaisesRegex(CatalogError, "does not match requested"):
            verify_role("cursor", "claude-opus-5", "max", evidence)


class EvidenceBindingTest(unittest.TestCase):
    def test_record_binds_command_executable_and_digest(self):
        raw = codex_catalog_bytes({"gpt-5.6-sol": ["max"]})
        evidence = produce("codex", runner=runner_returning(0, raw))
        record = evidence.record()
        self.assertEqual(["codex", "debug", "models"], record["command"]["argv"])
        self.assertEqual(sha256(raw), record["catalogBytesSha256"])
        self.assertIn("executableSha256", record["command"])
        self.assertEqual(0, record["exitCode"])


class CursorTipTrailerTest(unittest.TestCase):
    def test_accepts_exact_tip_trailer(self):
        raw = cursor_catalog_bytes(
            ["claude-opus-5-thinking-max", "gpt-5.6-sol-max"], with_tip=True,
        )
        evidence = produce("cursor", runner=runner_returning(0, raw))
        ids = MODULE.cursor_catalog_ids(evidence)
        self.assertIn("claude-opus-5-thinking-max", ids)
        self.assertIn("gpt-5.6-sol-max", ids)

    def test_rejects_malformed_tip_trailer(self):
        raw = (
            b"Available models\n\nclaude-opus-5-thinking-max - Opus\n"
            b"Tip: use --model <id> to switch (this is a different trailer).\n"
        )
        with self.assertRaisesRegex(CatalogError, "malformed Tip trailer"):
            produce("cursor", runner=runner_returning(0, raw))

    def test_rejects_duplicate_tip_trailer(self):
        raw = cursor_catalog_bytes(["claude-opus-5-thinking-max"], with_tip=True)
        raw += MODULE.CURSOR_TIP_TRAILER.encode("utf-8") + b"\n"
        with self.assertRaisesRegex(CatalogError, "duplicate Tip trailer"):
            produce("cursor", runner=runner_returning(0, raw))

    def test_rejects_model_line_after_tip_trailer(self):
        raw = cursor_catalog_bytes(["claude-opus-5-thinking-max"], with_tip=True)
        raw += b"claude-opus-5-thinking-xhigh - Smuggled\n"
        with self.assertRaisesRegex(CatalogError, "continues after the Tip trailer"):
            produce("cursor", runner=runner_returning(0, raw))

    def test_rejects_missing_header(self):
        raw = b"claude-opus-5-thinking-max - Opus\n"
        with self.assertRaisesRegex(CatalogError, "header is not 'Available models'"):
            produce("cursor", runner=runner_returning(0, raw))


class TrustedExecutableResolutionTest(unittest.TestCase):
    """The production resolver must bind a fixed trusted install location and
    never consult the caller PATH. These tests build a disposable install tree
    via the test-only ``candidate_roots`` seam and exercise the symlink-chain
    resolver directly, so they never touch the real install or PATH."""

    def _make_tree(self) -> tuple[str, str]:
        tmp = tempfile.mkdtemp(prefix="live-catalog-trusted-", dir=MODULE._home_root())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir, exist_ok=True)
        self._tree_root = Path(tmp)
        self._secure_tree_directories(bindir)
        return tmp, bindir

    def _secure_tree_directories(self, directory: str) -> None:
        root = self._tree_root
        target = Path(directory)
        if not target.is_relative_to(root):
            return
        os.chmod(root, 0o755)
        current = root
        for part in target.relative_to(root).parts:
            current /= part
            os.chmod(current, 0o755)

    def _write_exec(self, path: str, *, mode: int = 0o755) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._secure_tree_directories(os.path.dirname(path))
        with open(path, "wb") as fh:
            fh.write(b"#!/bin/sh\necho '{\"models\":[]}'\n")
        os.chmod(path, mode)

    def test_canonical_symlink_chain_resolves_to_regular_target(self):
        root, bindir = self._make_tree()
        target_dir = os.path.join(root, "releases", "v1", "bin")
        self._write_exec(os.path.join(target_dir, "codex"))
        current = os.path.join(root, "current")
        os.symlink(os.path.join(root, "releases", "v1"), current)
        entry = os.path.join(bindir, "codex")
        os.symlink(os.path.join(current, "bin", "codex"), entry)
        resolved = _resolve_executable(
            ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self.assertTrue(resolved.executable.endswith("releases/v1/bin/codex"))
        self.assertEqual(("codex", "debug", "models"), resolved.argv)
        self.assertTrue(resolved.executable_sha256.startswith("sha256:"))

    def test_arbitrary_path_substitution_is_refused(self):
        # Poison PATH with a temp binary; the resolver must ignore PATH and use
        # the fixed candidate roots only.
        root, bindir = self._make_tree()
        evil_dir = os.path.join(root, "evil")
        os.makedirs(evil_dir, exist_ok=True)
        self._write_exec(os.path.join(evil_dir, "codex"))
        entry = os.path.join(bindir, "codex")
        self._write_exec(entry)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = evil_dir + os.pathsep + old_path
        try:
            resolved = _resolve_executable(
                ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )
        finally:
            os.environ["PATH"] = old_path
        self.assertEqual(entry, resolved.executable)
        self.assertNotIn("evil", resolved.executable)

    def test_missing_entry_point_fails_closed(self):
        root, bindir = self._make_tree()
        with self.assertRaisesRegex(CatalogError, "no trusted install location"):
            _resolve_executable(("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir])

    def test_group_writable_target_with_shared_primary_group_rejected(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write_exec(entry, mode=0o775)
        gid = os.lstat(entry).st_gid
        account = SimpleNamespace(pw_name="current", pw_uid=os.geteuid(), pw_gid=gid)
        peer = SimpleNamespace(pw_name="peer", pw_uid=os.geteuid() + 1, pw_gid=gid)
        group = SimpleNamespace(gr_gid=gid, gr_mem=[])
        with (
            mock.patch.object(MODULE.pwd, "getpwuid", return_value=account),
            mock.patch.object(MODULE.pwd, "getpwall", return_value=[account, peer]),
            mock.patch.object(MODULE.grp, "getgrgid", return_value=group),
            self.assertRaisesRegex(CatalogError, "group-writable"),
        ):
            _resolve_executable(("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir])

    def test_world_writable_target_rejected(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write_exec(entry, mode=0o777)
        with self.assertRaisesRegex(CatalogError, "group/world-writable"):
            _resolve_executable(("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir])

    def test_target_outside_trusted_roots_rejected(self):
        root, bindir = self._make_tree()
        outside = tempfile.mkdtemp(prefix="live-catalog-outside-", dir=MODULE._home_root())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        target = os.path.join(outside, "codex")
        self._write_exec(target)
        entry = os.path.join(bindir, "codex")
        os.symlink(target, entry)
        with self.assertRaisesRegex(CatalogError, "outside the trusted install roots"):
            _resolve_executable(("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir])

    def test_symlink_loop_rejected(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        loop_a = os.path.join(bindir, "loop-a")
        loop_b = os.path.join(bindir, "loop-b")
        os.symlink(loop_b, loop_a)
        os.symlink(loop_a, loop_b)
        os.symlink(loop_a, entry)
        with self.assertRaisesRegex(CatalogError, "loops"):
            _resolve_executable(("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir])

    def test_world_writable_parent_rejected(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write_exec(entry)
        # Make bindir world-writable WITHOUT the sticky bit so it is unsafe.
        os.chmod(bindir, 0o777)
        try:
            with self.assertRaisesRegex(CatalogError, "world-writable without sticky bit"):
                _resolve_executable(("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir])
        finally:
            os.chmod(bindir, 0o755)

    def test_group_writable_intermediate_parent_with_shared_primary_group_rejected(self):
        root, _ = self._make_tree()
        intermediate = os.path.join(root, "releases")
        bindir = os.path.join(intermediate, "v1", "bin")
        self._write_exec(os.path.join(bindir, "codex"))
        os.chmod(intermediate, 0o775)
        try:
            gid = os.lstat(intermediate).st_gid
            account = SimpleNamespace(pw_name="current", pw_uid=os.geteuid(), pw_gid=gid)
            peer = SimpleNamespace(pw_name="peer", pw_uid=os.geteuid() + 1, pw_gid=gid)
            group = SimpleNamespace(gr_gid=gid, gr_mem=[])
            with (
                mock.patch.object(MODULE.pwd, "getpwuid", return_value=account),
                mock.patch.object(MODULE.pwd, "getpwall", return_value=[account, peer]),
                mock.patch.object(MODULE.grp, "getgrgid", return_value=group),
                self.assertRaisesRegex(CatalogError, "group-writable"),
            ):
                _resolve_executable(
                    ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
                )
        finally:
            os.chmod(intermediate, 0o755)

    def test_group_writable_parent_with_shared_explicit_member_rejected(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write_exec(entry)
        os.chmod(bindir, 0o775)
        try:
            gid = os.lstat(bindir).st_gid
            account = SimpleNamespace(pw_name="current", pw_uid=os.geteuid(), pw_gid=gid)
            group = SimpleNamespace(gr_gid=gid, gr_mem=["current", "peer"])
            with (
                mock.patch.object(MODULE.pwd, "getpwuid", return_value=account),
                mock.patch.object(MODULE.pwd, "getpwall", return_value=[account]),
                mock.patch.object(MODULE.grp, "getgrgid", return_value=group),
                self.assertRaisesRegex(CatalogError, "group-writable"),
            ):
                _resolve_executable(
                    ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
                )
        finally:
            os.chmod(bindir, 0o755)

    def test_group_writable_parent_with_private_group_is_accepted(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write_exec(entry)
        os.chmod(bindir, 0o775)
        try:
            gid = os.lstat(bindir).st_gid
            account = SimpleNamespace(pw_name="current", pw_uid=os.geteuid(), pw_gid=gid)
            group = SimpleNamespace(gr_gid=gid, gr_mem=[])
            with (
                mock.patch.object(MODULE.pwd, "getpwuid", return_value=account),
                mock.patch.object(MODULE.pwd, "getpwall", return_value=[account]),
                mock.patch.object(MODULE.grp, "getgrgid", return_value=group),
            ):
                resolved = _resolve_executable(
                    ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
                )
            self.assertEqual(entry, resolved.executable)
        finally:
            os.chmod(bindir, 0o755)

    def test_foreign_owned_intermediate_parent_rejected(self):
        root, _ = self._make_tree()
        foreign_parent = os.path.join(root, "foreign-owner")
        bindir = os.path.join(foreign_parent, "bin")
        self._write_exec(os.path.join(bindir, "codex"))
        real_lstat = os.lstat
        foreign_uid = 65534 if os.geteuid() != 65534 else 65533

        def lstat_with_foreign_component(path: str):
            metadata = real_lstat(path)
            if os.fspath(path) == foreign_parent:
                return SimpleNamespace(st_mode=metadata.st_mode, st_uid=foreign_uid)
            return metadata

        with mock.patch.object(MODULE.os, "lstat", side_effect=lstat_with_foreign_component):
            with self.assertRaisesRegex(CatalogError, "not owned by the current user or root"):
                _resolve_executable(
                    ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
                )

    def test_non_regular_target_rejected(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        os.symlink(os.path.join(root, "not-a-file"), entry)
        Path(os.path.join(root, "not-a-file")).mkdir()
        with self.assertRaisesRegex(CatalogError, "neither a symlink, directory, nor regular file|resolved to a directory"):
            _resolve_executable(("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir])


class EndpointEnvScrubTest(unittest.TestCase):
    def test_scrub_removes_endpoint_and_injection_overrides(self):
        poisoned = {
            "CURSOR_API_ENDPOINT": "https://evil.example/api",
            "OPENAI_BASE_URL": "https://evil.example/v1",
            "CODEX_API_BASE": "https://evil.example/codex",
            "ANTHROPIC_BASE_URL": "https://evil.example/anthropic",
            "NODE_OPTIONS": "--require /tmp/evil.js",
            "NODE_TLS_REJECT_UNAUTHORIZED": "0",
            "NODE_COMPILE_CACHE": "/tmp/evil-cache",
            "CURSOR_INVOKED_AS": "forged",
            "LD_PRELOAD": "/tmp/evil.so",
            "LD_LIBRARY_PATH": "/tmp/evil",
            "PYTHONPATH": "/tmp/evil",
            "PYTHONHOME": "/tmp/evil",
            "PYTHONSTARTUP": "/tmp/evil.py",
            "BASH_ENV": "/tmp/evil.sh",
            "ENV": "/tmp/evil.sh",
            "SHELLOPTS": "xtrace",
            "HTTP_PROXY": "http://evil.proxy",
            "HTTPS_PROXY": "http://evil.proxy",
            "SSL_CERT_FILE": "/tmp/evil-ca.pem",
            "REQUESTS_CA_BUNDLE": "/tmp/evil-ca.pem",
            "CURSOR_API_KEY": "secret-key",
            "CURSOR_AUTH_TOKEN": "secret-token",
            "CURSOR_FUTURE_ACCOUNT_SELECTOR": "secret-future",
            "OPENAI_API_KEY": "secret-key",
            "CODEX_API_KEY": "alternate-codex-account-key",
            "ANTHROPIC_API_KEY": "alternate-account-key",
            "ANTHROPIC_AUTH_TOKEN": "alternate-account-token",
            "CLAUDE_CODE_OAUTH_TOKEN": "alternate-oauth-token",
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }
        scrubbed = _scrub_env(poisoned)
        for banned in (
            "CURSOR_API_ENDPOINT", "OPENAI_BASE_URL", "CODEX_API_BASE", "ANTHROPIC_BASE_URL",
            "NODE_OPTIONS", "NODE_TLS_REJECT_UNAUTHORIZED", "NODE_COMPILE_CACHE",
            "CURSOR_INVOKED_AS", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME",
            "PYTHONSTARTUP", "BASH_ENV", "ENV", "SHELLOPTS", "HTTP_PROXY", "HTTPS_PROXY",
            "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY",
            "CURSOR_API_KEY", "CURSOR_AUTH_TOKEN", "CURSOR_FUTURE_ACCOUNT_SELECTOR",
            "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
        ):
            self.assertNotIn(banned, scrubbed)

    def test_scrub_removes_complete_openai_codex_and_cursor_selector_families(self):
        poisoned = {
            "OPENAI_API_KEY": "sentinel-openai-key",
            "OPENAI_ORGANIZATION": "sentinel-openai-org",
            "OPENAI_PROJECT": "sentinel-openai-project",
            "OPENAI_FUTURE_ACCOUNT_SELECTOR": "sentinel-openai-future",
            "CODEX_API_KEY": "sentinel-codex-key",
            "CODEX_AUTH_TOKEN": "sentinel-codex-token",
            "CODEX_PROFILE": "sentinel-codex-profile",
            "CODEX_FUTURE_CREDENTIAL_SELECTOR": "sentinel-codex-future",
            "CODEX_HOME": "/tmp/sentinel-codex-home",
            "CURSOR_API_KEY": "sentinel-cursor-key",
            "CURSOR_AUTH_TOKEN": "sentinel-cursor-token",
            "CURSOR_FUTURE_ACCOUNT_SELECTOR": "sentinel-cursor-future",
        }
        scrubbed = _scrub_env(poisoned)
        for selector in poisoned:
            if selector == "CODEX_HOME":
                continue
            self.assertNotIn(selector, scrubbed)
        self.assertEqual(
            os.path.join(MODULE._home_root(), ".codex"),
            scrubbed["CODEX_HOME"],
        )
        self.assertEqual(
            [],
            sorted(key for key in scrubbed if key.startswith(("OPENAI_", "CODEX_", "CURSOR_")) and key != "CODEX_HOME"),
        )
        self.assertIn("OPENAI_", MODULE._SCRUBBED_ENV_PREFIXES)
        self.assertIn("CODEX_", MODULE._SCRUBBED_ENV_PREFIXES)
        self.assertIn("CURSOR_", MODULE._SCRUBBED_ENV_PREFIXES)

    def test_scrub_replaces_config_and_credential_roots_with_canonical_account_paths(self):
        poisoned = {
            "CODEX_HOME": "/tmp/alternate-codex",
            "CLAUDE_CONFIG_DIR": "/tmp/alternate-claude",
            "CURSOR_CONFIG_DIR": "/tmp/alternate-cursor",
            "CURSOR_AGENT_CONFIG_DIR": "/tmp/alternate-cursor-agent",
            "AGENT_CLI_CONFIG_DIR": "/tmp/alternate-agent-cli",
            "XDG_CONFIG_HOME": "/tmp/alternate-xdg-config",
            "XDG_DATA_HOME": "/tmp/alternate-xdg-data",
        }
        scrubbed = _scrub_env(poisoned)
        home = MODULE._home_root()
        self.assertEqual(os.path.join(home, ".codex"), scrubbed["CODEX_HOME"])
        self.assertEqual(os.path.join(home, ".config"), scrubbed["XDG_CONFIG_HOME"])
        self.assertEqual(
            os.path.join(home, ".local", "share"), scrubbed["XDG_DATA_HOME"],
        )
        for selector in (
            "CLAUDE_CONFIG_DIR",
            "CURSOR_CONFIG_DIR",
            "CURSOR_AGENT_CONFIG_DIR",
            "AGENT_CLI_CONFIG_DIR",
        ):
            self.assertNotIn(selector, scrubbed)
        for selector, alternate in poisoned.items():
            self.assertNotEqual(alternate, scrubbed.get(selector))

    def test_scrub_removes_cursor_openai_and_codex_auth(self):
        env = {
            "CURSOR_API_KEY": "cursor-key",
            "CURSOR_AUTH_TOKEN": "cursor-token",
            "OPENAI_API_KEY": "alternate-openai-account-key",
            "CODEX_API_KEY": "alternate-codex-account-key",
        }
        scrubbed = _scrub_env(env)
        self.assertNotIn("CURSOR_API_KEY", scrubbed)
        self.assertNotIn("CURSOR_AUTH_TOKEN", scrubbed)
        self.assertNotIn("OPENAI_API_KEY", scrubbed)
        self.assertNotIn("CODEX_API_KEY", scrubbed)

    def test_codex_subprocess_receives_no_caller_account_selector(self):
        executable = MODULE._synthetic_executable(MODULE.ALLOWED_COMMANDS["codex"])
        completed = SimpleNamespace(returncode=0, stdout=b"reply", stderr=b"")
        poisoned = {
            "OPENAI_API_KEY": "sentinel-openai-key",
            "OPENAI_ORGANIZATION": "sentinel-openai-org",
            "CODEX_API_KEY": "sentinel-codex-key",
            "CODEX_PROFILE": "sentinel-codex-profile",
            "CODEX_HOME": "/tmp/sentinel-codex-home",
            "CURSOR_API_KEY": "sentinel-cursor-key",
            "CURSOR_FUTURE_ACCOUNT_SELECTOR": "sentinel-cursor-future",
        }
        with (
            mock.patch.dict(MODULE.os.environ, poisoned, clear=False),
            mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual((0, b"reply", b""), MODULE._run_command(executable, runner=None))
        child_env = run.call_args.kwargs["env"]
        for selector in (
            "OPENAI_API_KEY", "OPENAI_ORGANIZATION", "CODEX_API_KEY", "CODEX_PROFILE",
            "CURSOR_API_KEY", "CURSOR_FUTURE_ACCOUNT_SELECTOR",
        ):
            self.assertNotIn(selector, child_env)
        self.assertEqual(
            [],
            sorted(
                key
                for key in child_env
                if key.startswith(("OPENAI_", "CODEX_", "CURSOR_")) and key != "CODEX_HOME"
            ),
        )
        self.assertEqual(
            os.path.join(MODULE._home_root(), ".codex"),
            child_env["CODEX_HOME"],
        )

    def test_cursor_subprocess_receives_no_caller_account_selector(self):
        executable = MODULE._synthetic_executable(MODULE.ALLOWED_COMMANDS["cursor"])
        completed = SimpleNamespace(returncode=0, stdout=b"reply", stderr=b"")
        poisoned = {
            "CURSOR_API_KEY": "sentinel-cursor-key",
            "CURSOR_AUTH_TOKEN": "sentinel-cursor-token",
            "CURSOR_FUTURE_ACCOUNT_SELECTOR": "sentinel-cursor-future",
            "CURSOR_INVOKED_AS": "forged",
            "OPENAI_API_KEY": "sentinel-openai-key",
            "CODEX_API_KEY": "sentinel-codex-key",
        }
        with (
            mock.patch.dict(MODULE.os.environ, poisoned, clear=False),
            mock.patch.object(MODULE.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual((0, b"reply", b""), MODULE._run_command(executable, runner=None))
        child_env = run.call_args.kwargs["env"]
        for selector in (
            "CURSOR_API_KEY", "CURSOR_AUTH_TOKEN", "CURSOR_FUTURE_ACCOUNT_SELECTOR",
            "OPENAI_API_KEY", "CODEX_API_KEY",
        ):
            self.assertNotIn(selector, child_env)
        self.assertEqual(
            ["CURSOR_INVOKED_AS"],
            sorted(key for key in child_env if key.startswith("CURSOR_")),
        )
        self.assertEqual("cursor-agent", child_env["CURSOR_INVOKED_AS"])

    def test_scrub_pins_home_and_path_to_account_values(self):
        scrubbed = _scrub_env({"HOME": "/tmp/attacker-home", "PATH": "/tmp/attacker-bin"})
        account_home = MODULE._home_root()
        self.assertEqual(account_home, scrubbed["HOME"])
        self.assertNotIn("/tmp/attacker-bin", scrubbed["PATH"])
        self.assertEqual(
            os.pathsep.join(
                (os.path.join(account_home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"),
            ),
            scrubbed["PATH"],
        )


class AccountHomeDerivationTest(unittest.TestCase):
    """`_home_root` is the anchor for both the install search order and the
    subprocess HOME, so it must come from the passwd database for the running
    uid, never from a caller-controlled HOME. The residual trust boundary is
    explicitly same-UID: a process already running as this uid can write its own
    home, and that is accepted and documented, not defended against."""

    def test_home_root_is_passwd_entry_not_caller_home(self):
        import pwd as pwd_module

        expected = os.path.realpath(pwd_module.getpwuid(os.getuid()).pw_dir)
        previous = os.environ.get("HOME")
        os.environ["HOME"] = "/tmp/attacker-home"
        try:
            self.assertEqual(expected, MODULE._home_root())
        finally:
            if previous is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = previous

    def test_entry_dirs_anchor_on_account_home(self):
        expected = os.path.join(MODULE._home_root(), ".local", "bin")
        self.assertEqual(expected, MODULE._entry_dirs(None)[0])


class ExecutionClosureBindingTest(unittest.TestCase):
    """Cursor installs its entry point as a bash wrapper, so the kernel would
    actually execute bash. The receipt must therefore bind the interpreter as
    well as the wrapper, and the wrapper must be run through a fixed trusted
    interpreter rather than via `#!` PATH lookup."""

    def _make_tree(self) -> tuple[str, str]:
        tmp = tempfile.mkdtemp(prefix="live-catalog-closure-", dir=MODULE._home_root())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        bindir = os.path.join(tmp, "bin")
        os.makedirs(bindir, exist_ok=True)
        self._tree_root = Path(tmp)
        self._secure_tree_directories(bindir)
        return tmp, bindir

    def _secure_tree_directories(self, directory: str) -> None:
        root = self._tree_root
        target = Path(directory)
        if not target.is_relative_to(root):
            return
        os.chmod(root, 0o755)
        current = root
        for part in target.relative_to(root).parts:
            current /= part
            os.chmod(current, 0o755)

    def _write(self, path: str, payload: bytes, *, mode: int = 0o755) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._secure_tree_directories(os.path.dirname(path))
        with open(path, "wb") as fh:
            fh.write(payload)
        os.chmod(path, mode)

    def test_binary_entry_point_binds_only_entrypoint(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write(entry, b"\x7fELF\x02\x01\x01\x00binary-body")
        resolved = _resolve_executable(
            ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self.assertEqual((entry, "debug", "models"), resolved.invoked_argv)
        self.assertEqual(["entrypoint"], [e.role for e in resolved.closure])
        self.assertEqual(resolved.executable_sha256, resolved.closure[0].sha256)

    def _write_cursor_install(
        self,
        bindir: str,
        *,
        extra_files: dict[str, bytes] | None = None,
        wrapper: bytes | None = None,
    ) -> None:
        self._write(
            os.path.join(bindir, "cursor-agent"),
            wrapper or b"#!/usr/bin/env bash\nexec node index.js \"$@\"\n",
        )
        self._write(os.path.join(bindir, "node"), b"\x7fELF-node-runtime")
        self._write(os.path.join(bindir, "index.js"), b"// bundle v1\n", mode=0o644)
        files = {"1234.index.js": b"// dynamically loaded chunk\n"}
        files.update(extra_files or {})
        for name, payload in files.items():
            self._write(os.path.join(bindir, name), payload, mode=0o644)

    def test_non_cursor_bash_wrapper_is_invoked_through_fixed_interpreter(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write(entry, b"#!/usr/bin/env bash\nexec real-agent \"$@\"\n")
        resolved = _resolve_executable(
            ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self.assertEqual(["interpreter", "entrypoint"], [e.role for e in resolved.closure])
        interpreter = resolved.closure[0].path
        self.assertIn(interpreter, ("/usr/bin/bash", "/bin/bash"))
        self.assertEqual((interpreter, entry, "debug", "models"), resolved.invoked_argv)
        self.assertTrue(resolved.closure[0].sha256.startswith("sha256:"))
        self.assertNotEqual(resolved.closure[0].sha256, resolved.closure[1].sha256)

    def test_cursor_invokes_bound_node_and_index_directly(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        resolved = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        node = os.path.join(bindir, "node")
        bundle = os.path.join(bindir, "index.js")
        wrapper = os.path.join(bindir, "cursor-agent")
        self.assertEqual(["entrypoint", "runtime", "bundle"], [e.role for e in resolved.closure])
        self.assertEqual(wrapper, resolved.closure[0].path)
        self.assertEqual(node, resolved.closure[1].path)
        self.assertEqual(bundle, resolved.closure[2].path)
        self.assertEqual((node, "--use-system-ca", bundle, "models"), resolved.invoked_argv)
        self.assertEqual(wrapper, resolved.executable)

    def test_cursor_missing_node_fails_closed(self):
        root, bindir = self._make_tree()
        self._write(os.path.join(bindir, "cursor-agent"), b"#!/usr/bin/env bash\nexec node \"$@\"\n")
        self._write(os.path.join(bindir, "index.js"), b"// bundle\n", mode=0o644)
        with self.assertRaisesRegex(CatalogError, "missing required member 'node'"):
            _resolve_executable(
                ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )

    def test_cursor_missing_index_fails_closed(self):
        root, bindir = self._make_tree()
        self._write(os.path.join(bindir, "cursor-agent"), b"#!/usr/bin/env bash\nexec node \"$@\"\n")
        self._write(os.path.join(bindir, "node"), b"\x7fELF-node-runtime")
        with self.assertRaisesRegex(CatalogError, "missing required member 'index.js'"):
            _resolve_executable(
                ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )

    def test_cursor_symlink_chunk_fails_closed(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        chunk = os.path.join(bindir, "1234.index.js")
        os.remove(chunk)
        os.symlink("/tmp/forged-chunk.js", chunk)
        with self.assertRaisesRegex(CatalogError, "unexpected symlink"):
            _resolve_executable(
                ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )

    def test_dynamically_loaded_chunk_is_bound_and_drift_changes_manifest(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(
            bindir,
            extra_files={
                "1234.index.js": b"// chunk v1\n",
                os.path.join("node_modules", "piscina", "dist", "worker.js"): b"worker v1\n",
            },
        )
        first = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        relative = [member.relative_path for member in first.install_manifest]
        self.assertIn("1234.index.js", relative)
        self.assertIn("node_modules/piscina/dist/worker.js", relative)
        self.assertIn("node", relative)
        self.assertIn("index.js", relative)
        self.assertIn("cursor-agent", relative)
        self._write(os.path.join(bindir, "1234.index.js"), b"// chunk v2 forged\n", mode=0o644)
        second = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self.assertEqual(first.executable_sha256, second.executable_sha256)
        self.assertNotEqual(first.install_manifest_sha256, second.install_manifest_sha256)

    def test_executable_drift_changes_runtime_digest(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        first = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self._write(os.path.join(bindir, "node"), b"\x7fELF-forged-runtime")
        second = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self.assertEqual(first.executable_sha256, second.executable_sha256)
        self.assertNotEqual(first.closure[1].sha256, second.closure[1].sha256)
        self.assertNotEqual(first.install_manifest_sha256, second.install_manifest_sha256)

    def test_unsafe_manifest_member_fails_closed(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        os.chmod(os.path.join(bindir, "1234.index.js"), 0o666)
        with self.assertRaisesRegex(CatalogError, "install member"):
            _resolve_executable(
                ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )

    def test_running_runtime_state_is_excluded_from_manifest(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        running = os.path.join(bindir, ".running", "state")
        self._write(running, b"state-1\n", mode=0o644)
        first = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        relative = [member.relative_path for member in first.install_manifest]
        self.assertFalse(any(path == ".running/state" or path.startswith(".running/") for path in relative))
        self._write(running, b"state-2 swapped\n", mode=0o644)
        second = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self.assertEqual(first.install_manifest_sha256, second.install_manifest_sha256)

    def test_closure_digest_changes_when_bundle_bytes_change(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        first = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self._write(os.path.join(bindir, "index.js"), b"// bundle v2 exfiltrates\n", mode=0o644)
        second = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self.assertEqual(first.executable_sha256, second.executable_sha256)
        self.assertNotEqual(first.closure_sha256, second.closure_sha256)

    def test_closure_digest_changes_when_wrapper_bytes_change(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        first = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self._write(
            os.path.join(bindir, "cursor-agent"),
            b"#!/usr/bin/env bash\nexec evil-agent \"$@\"\n",
        )
        second = _resolve_executable(
            ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
        )
        self.assertNotEqual(first.closure_sha256, second.closure_sha256)

    def test_group_writable_bundle_fails_closed(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        os.chmod(os.path.join(bindir, "index.js"), 0o666)
        with self.assertRaisesRegex(CatalogError, "install member"):
            _resolve_executable(
                ("cursor-agent", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )

    def test_untrusted_shebang_interpreter_fails_closed(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write(entry, b"#!/tmp/evil-interpreter\nnoop\n")
        with self.assertRaisesRegex(CatalogError, "untrusted interpreter"):
            _resolve_executable(
                ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )

    def test_shebang_with_extra_interpreter_arguments_fails_closed(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write(entry, b"#!/bin/bash -x -c evil\nnoop\n")
        with self.assertRaisesRegex(CatalogError, "unsupported interpreter arguments"):
            _resolve_executable(
                ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )

    def test_env_shebang_with_extra_arguments_fails_closed(self):
        root, bindir = self._make_tree()
        entry = os.path.join(bindir, "codex")
        self._write(entry, b"#!/usr/bin/env -S bash -x\nnoop\n")
        with self.assertRaisesRegex(CatalogError, "env with unsupported extra arguments"):
            _resolve_executable(
                ("codex", "debug", "models"), candidate_roots=[root], entry_dirs=[bindir],
            )

    def test_read_bound_bytes_refuses_symlink(self):
        root, bindir = self._make_tree()
        target = os.path.join(bindir, "regular")
        self._write(target, b"payload\n", mode=0o644)
        link = os.path.join(bindir, "link")
        os.symlink(target, link)
        with self.assertRaisesRegex(CatalogError, "not a regular file"):
            MODULE._read_bound_bytes(link)

    def test_direct_argv_and_scrubbed_env_launch_node_and_index(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        captured: dict[str, object] = {}
        stdout = cursor_catalog_bytes(["claude-opus-5-thinking-max"])

        def fake_run(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = dict(kwargs["env"])
            captured["shell"] = kwargs["shell"]
            return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")

        poisoned = {
            "PATH": "/tmp/attacker-bin",
            "NODE_OPTIONS": "--require /tmp/evil.js",
            "NODE_COMPILE_CACHE": "/tmp/evil-cache",
            "NODE_TLS_REJECT_UNAUTHORIZED": "0",
            "NODE_PATH": "/tmp/evil-modules",
            "BASH_ENV": "/tmp/evil.sh",
            "CURSOR_API_ENDPOINT": "https://evil.example/api",
            "CURSOR_API_KEY": "sentinel-cursor-key",
            "CURSOR_AUTH_TOKEN": "sentinel-cursor-token",
            "CURSOR_FUTURE_ACCOUNT_SELECTOR": "sentinel-cursor-future",
            "CURSOR_INVOKED_AS": "forged",
            "SSL_CERT_FILE": "/tmp/evil-ca.pem",
        }
        with mock.patch.object(MODULE.subprocess, "run", side_effect=fake_run):
            with mock.patch.dict(os.environ, poisoned, clear=False):
                produce("cursor", candidate_roots=[root], entry_dirs=[bindir])
        node = os.path.join(bindir, "node")
        bundle = os.path.join(bindir, "index.js")
        self.assertEqual([node, "--use-system-ca", bundle, "models"], captured["cmd"])
        self.assertFalse(captured["shell"])
        env = captured["env"]
        assert isinstance(env, dict)
        for banned in (
            "NODE_OPTIONS", "NODE_COMPILE_CACHE", "NODE_TLS_REJECT_UNAUTHORIZED",
            "NODE_PATH", "BASH_ENV", "CURSOR_API_ENDPOINT", "SSL_CERT_FILE",
            "CURSOR_API_KEY", "CURSOR_AUTH_TOKEN", "CURSOR_FUTURE_ACCOUNT_SELECTOR",
        ):
            self.assertNotIn(banned, env)
        self.assertEqual("cursor-agent", env["CURSOR_INVOKED_AS"])
        self.assertEqual(
            ["CURSOR_INVOKED_AS"],
            sorted(key for key in env if key.startswith("CURSOR_")),
        )
        self.assertNotIn("/tmp/attacker-bin", env["PATH"])

    def test_post_exit_rebind_detects_persistent_chunk_swap(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        chunk = os.path.join(bindir, "1234.index.js")
        stdout = cursor_catalog_bytes(["claude-opus-5-thinking-max"])

        def swapped_run(cmd, **kwargs):
            with open(chunk, "wb") as handle:
                handle.write(b"// swapped after bind\n")
            os.chmod(chunk, 0o644)
            return SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")

        with mock.patch.object(MODULE.subprocess, "run", side_effect=swapped_run):
            with self.assertRaisesRegex(CatalogError, "changed after the catalog command exited"):
                produce("cursor", candidate_roots=[root], entry_dirs=[bindir])

    def test_post_exit_rebind_runs_after_nonzero_exit(self):
        root, bindir = self._make_tree()
        self._write_cursor_install(bindir)
        chunk = os.path.join(bindir, "1234.index.js")

        def swapped_run(cmd, **kwargs):
            with open(chunk, "wb") as handle:
                handle.write(b"// swapped on failure\n")
            os.chmod(chunk, 0o644)
            return SimpleNamespace(returncode=2, stdout=b"", stderr=b"auth error")

        with mock.patch.object(MODULE.subprocess, "run", side_effect=swapped_run):
            with self.assertRaisesRegex(CatalogError, "changed after the catalog command exited"):
                produce("cursor", candidate_roots=[root], entry_dirs=[bindir])

    def test_record_exposes_invoked_argv_and_closure(self):
        raw = codex_catalog_bytes({"gpt-5.6-sol": ["max"]})
        evidence = produce("codex", runner=runner_returning(0, raw))
        record = evidence.record()
        self.assertEqual(["codex", "debug", "models"], record["command"]["invokedArgv"])
        self.assertEqual(
            [{"role": "entrypoint", "path": "test-runner:codex",
              "sha256": record["command"]["executableSha256"]}],
            record["command"]["executionClosure"],
        )
        self.assertTrue(record["command"]["executionClosureSha256"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
