from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sync_codex = load("sync_codex")
sync_kimi = load("sync_kimi")
sync_policy = load("sync_policy")


class UserConfigPolicyTest(unittest.TestCase):
    def make_skill_root(self, root: Path, names: tuple[str, ...] = ()) -> Path:
        model_path = root / "config" / "workflows" / "models.json"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_text("{}\n", encoding="utf-8")
        skills = root / ".claude" / "skills"
        for name in names:
            path = skills / name
            path.mkdir(parents=True, exist_ok=True)
            (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        rules = skills / "update-platform-config" / "assets" / "project" / ".codex" / "rules"
        rules.mkdir(parents=True, exist_ok=True)
        return skills

    def make_kimi_template(self, skills: Path) -> None:
        template = (
            skills
            / "update-platform-config"
            / "assets"
            / "project"
            / "config"
            / "kimi"
            / "hooks.toml"
        )
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text('command = "GENERAL_AGENT_REPO/hook.py"\n', encoding="utf-8")

    def test_codex_direct_sync_is_inert_without_opt_in(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sync_codex, "load_manifest") as load_manifest:
                self.assertEqual(0, sync_codex.main())
        load_manifest.assert_not_called()

    def test_kimi_direct_sync_is_inert_without_opt_in(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sync_kimi, "validate_canonical_agents") as validate:
                self.assertEqual(0, sync_kimi.main())
        validate.assert_not_called()

    def test_codex_unowned_skill_collision_fails_before_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root, ("alpha", "zeta"))
            agents = root / ".omp" / "agents"
            agents.mkdir(parents=True)
            codex = root / "home" / ".codex"
            private = codex / "skills" / "zeta" / "PRIVATE.txt"
            private.parent.mkdir(parents=True)
            private.write_bytes(b"personal bytes\n")

            with (
                mock.patch.object(sync_codex, "REPO", str(root)),
                mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_codex, "CODEX", str(codex)),
                mock.patch.object(sync_codex, "MANIFEST", str(codex / ".general-agent-sync.json")),
                mock.patch.object(sync_codex, "load_role_document", return_value={}),
                mock.patch.object(sync_codex, "validate_agent_directory"),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(2, sync_codex.main())

            self.assertEqual(b"personal bytes\n", private.read_bytes())
            self.assertFalse((codex / "skills" / "alpha").exists())
            self.assertFalse((codex / ".general-agent-sync.json").exists())

    def test_codex_unowned_agent_symlink_fails_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root)
            agents = root / ".omp" / "agents"
            agents.mkdir(parents=True)
            (agents / "personal.md").write_text(
                "---\nname: personal\ndescription: test\nmodel-role: task\n---\nbody\n",
                encoding="utf-8",
            )
            codex = root / "home" / ".codex"
            (codex / "agents").mkdir(parents=True)
            sentinel = root / "sentinel.toml"
            sentinel.write_bytes(b"do not replace\n")
            (codex / "agents" / "personal.toml").symlink_to(sentinel)
            route = SimpleNamespace(role={"model": "gpt-test", "effort": "max"})

            with (
                mock.patch.object(sync_codex, "REPO", str(root)),
                mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_codex, "CODEX", str(codex)),
                mock.patch.object(sync_codex, "MANIFEST", str(codex / ".general-agent-sync.json")),
                mock.patch.object(sync_codex, "load_role_document", return_value={}),
                mock.patch.object(sync_codex, "validate_agent_directory"),
                mock.patch.object(sync_codex, "provider_compatible", return_value=True),
                mock.patch.object(sync_codex, "resolve_agent", return_value=route),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(2, sync_codex.main())

            self.assertEqual(b"do not replace\n", sentinel.read_bytes())
            self.assertTrue((codex / "agents" / "personal.toml").is_symlink())
            self.assertFalse((codex / ".general-agent-sync.json").exists())

    def test_codex_unowned_rule_symlink_fails_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root, ("alpha",))
            source_rule = (
                skills
                / "update-platform-config"
                / "assets"
                / "project"
                / ".codex"
                / "rules"
                / "protect.rules"
            )
            source_rule.write_bytes(b"managed rule\n")
            agents = root / ".omp" / "agents"
            agents.mkdir(parents=True)
            codex = root / "home" / ".codex"
            (codex / "rules").mkdir(parents=True)
            sentinel = root / "sentinel.rules"
            sentinel.write_bytes(b"personal rule\n")
            (codex / "rules" / "protect.rules").symlink_to(sentinel)

            with (
                mock.patch.object(sync_codex, "REPO", str(root)),
                mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_codex, "CODEX", str(codex)),
                mock.patch.object(sync_codex, "MANIFEST", str(codex / ".general-agent-sync.json")),
                mock.patch.object(sync_codex, "load_role_document", return_value={}),
                mock.patch.object(sync_codex, "validate_agent_directory"),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(2, sync_codex.main())

            self.assertEqual(b"personal rule\n", sentinel.read_bytes())
            self.assertTrue((codex / "rules" / "protect.rules").is_symlink())
            self.assertFalse((codex / "skills" / "alpha").exists())

    def test_codex_late_agent_collision_does_not_publish_earlier_skill(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root, ("alpha",))
            agents = root / ".omp" / "agents"
            agents.mkdir(parents=True)
            (agents / "zeta.md").write_text(
                "---\nname: zeta\ndescription: test\nmodel-role: task\n---\nbody\n",
                encoding="utf-8",
            )
            codex = root / "home" / ".codex"
            personal = codex / "agents" / "zeta.toml"
            personal.parent.mkdir(parents=True)
            personal.write_bytes(b"personal agent\n")
            route = SimpleNamespace(role={"model": "gpt-test", "effort": "max"})

            with (
                mock.patch.object(sync_codex, "REPO", str(root)),
                mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_codex, "CODEX", str(codex)),
                mock.patch.object(sync_codex, "MANIFEST", str(codex / ".general-agent-sync.json")),
                mock.patch.object(sync_codex, "load_role_document", return_value={}),
                mock.patch.object(sync_codex, "validate_agent_directory"),
                mock.patch.object(sync_codex, "provider_compatible", return_value=True),
                mock.patch.object(sync_codex, "resolve_agent", return_value=route),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(2, sync_codex.main())

            self.assertEqual(b"personal agent\n", personal.read_bytes())
            self.assertFalse((codex / "skills" / "alpha").exists())
            self.assertFalse((codex / ".general-agent-sync.json").exists())

    def test_codex_manifest_owned_targets_update_and_manifest_is_written_last(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root, ("alpha",))
            source_rule = (
                skills
                / "update-platform-config"
                / "assets"
                / "project"
                / ".codex"
                / "rules"
                / "protect.rules"
            )
            source_rule.write_bytes(b"new rule\n")
            agents = root / ".omp" / "agents"
            agents.mkdir(parents=True)
            (agents / "worker.md").write_text(
                "---\nname: worker\ndescription: test\nmodel-role: task\n---\nnew body\n",
                encoding="utf-8",
            )
            codex = root / "home" / ".codex"
            (codex / "skills").mkdir(parents=True)
            old_skill = root / "old-skill"
            old_skill.mkdir()
            (codex / "skills" / "alpha").symlink_to(old_skill)
            (codex / "agents").mkdir()
            (codex / "agents" / "worker.toml").write_bytes(b"old agent\n")
            (codex / "rules").mkdir()
            (codex / "rules" / "protect.rules").write_bytes(b"old rule\n")
            manifest = codex / ".general-agent-sync.json"
            manifest.write_text(
                json.dumps(
                    {"skills": ["alpha"], "agents": ["worker"], "rules": ["protect.rules"]}
                ),
                encoding="utf-8",
            )
            route = SimpleNamespace(role={"model": "gpt-test", "effort": "max"})
            writes: list[Path] = []
            commits: list[list[Path]] = []
            real_commit = sync_codex.commit_plan

            def record_commit(plan):
                paths = [action.path for action in plan.actions]
                commits.append(paths)
                writes.extend(paths)
                return real_commit(plan)

            with (
                mock.patch.object(sync_codex, "REPO", str(root)),
                mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_codex, "CODEX", str(codex)),
                mock.patch.object(sync_codex, "MANIFEST", str(manifest)),
                mock.patch.object(sync_codex, "load_role_document", return_value={}),
                mock.patch.object(sync_codex, "validate_agent_directory"),
                mock.patch.object(sync_codex, "provider_compatible", return_value=True),
                mock.patch.object(sync_codex, "resolve_agent", return_value=route),
                mock.patch.object(sync_codex, "commit_plan", side_effect=record_commit),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(0, sync_codex.main())
                self.assertEqual(0, sync_codex.main())

            self.assertEqual(str(skills / "alpha"), os.readlink(codex / "skills" / "alpha"))
            self.assertIn(b'model = "gpt-test"', (codex / "agents" / "worker.toml").read_bytes())
            self.assertEqual(b"new rule\n", (codex / "rules" / "protect.rules").read_bytes())
            self.assertEqual(
                {"skills": ["alpha"], "agents": ["worker"], "rules": ["protect.rules"]},
                json.loads(manifest.read_text(encoding="utf-8")),
            )
            self.assertEqual(manifest, writes[-1])
            self.assertEqual([], commits[-1])

    def test_codex_interrupted_target_write_leaves_manifest_and_original_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root)
            source_rule = (
                skills
                / "update-platform-config"
                / "assets"
                / "project"
                / ".codex"
                / "rules"
                / "protect.rules"
            )
            source_rule.write_bytes(b"new rule\n")
            (root / ".omp" / "agents").mkdir(parents=True)
            codex = root / "home" / ".codex"
            target = codex / "rules" / "protect.rules"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old rule\n")
            manifest = codex / ".general-agent-sync.json"
            manifest_bytes = b'{"skills": [], "agents": [], "rules": ["protect.rules"]}\n'
            manifest.write_bytes(manifest_bytes)
            policy_globals = sync_codex.commit_plan.__globals__
            real_publish = policy_globals["_publish_action"]

            def interrupt_rule_publish(action, staged):
                if action.path == target:
                    raise OSError("interrupted target")
                return real_publish(action, staged)

            with (
                mock.patch.object(sync_codex, "REPO", str(root)),
                mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_codex, "CODEX", str(codex)),
                mock.patch.object(sync_codex, "MANIFEST", str(manifest)),
                mock.patch.object(sync_codex, "load_role_document", return_value={}),
                mock.patch.object(sync_codex, "validate_agent_directory"),
                mock.patch.dict(
                    policy_globals, {"_publish_action": interrupt_rule_publish}
                ),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(2, sync_codex.main())

            self.assertEqual(b"old rule\n", target.read_bytes())
            self.assertEqual(manifest_bytes, manifest.read_bytes())
            self.assertEqual([], list(target.parent.glob(".protect.rules.*")))

    def test_codex_final_manifest_failure_rolls_back_all_prior_targets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root, ("alpha",))
            source_rule = (
                skills
                / "update-platform-config"
                / "assets"
                / "project"
                / ".codex"
                / "rules"
                / "protect.rules"
            )
            source_rule.write_bytes(b"new rule\n")
            (root / ".omp" / "agents").mkdir(parents=True)
            codex = root / "home" / ".codex"
            (codex / "skills").mkdir(parents=True)
            old_skill = root / "old-skill"
            old_skill.mkdir()
            skill_target = codex / "skills" / "alpha"
            skill_target.symlink_to(old_skill)
            rule_target = codex / "rules" / "protect.rules"
            rule_target.parent.mkdir()
            rule_target.write_bytes(b"old rule\n")
            manifest = codex / ".general-agent-sync.json"
            manifest_bytes = json.dumps(
                {"skills": ["alpha"], "agents": [], "rules": ["protect.rules"]}
            ).encode("utf-8")
            manifest.write_bytes(manifest_bytes)

            policy_globals = sync_codex.commit_plan.__globals__
            real_publish = policy_globals["_publish_action"]

            def fail_manifest(action, staged):
                if action.path == manifest:
                    raise OSError("injected final manifest failure")
                return real_publish(action, staged)

            with (
                mock.patch.object(sync_codex, "REPO", str(root)),
                mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_codex, "CODEX", str(codex)),
                mock.patch.object(sync_codex, "MANIFEST", str(manifest)),
                mock.patch.object(sync_codex, "load_role_document", return_value={}),
                mock.patch.object(sync_codex, "validate_agent_directory"),
                mock.patch.dict(
                    os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True
                ),
                mock.patch.dict(
                    policy_globals, {"_publish_action": fail_manifest}
                ),
            ):
                self.assertEqual(2, sync_codex.main())

            self.assertEqual(str(old_skill), os.readlink(skill_target))
            self.assertEqual(b"old rule\n", rule_target.read_bytes())
            self.assertEqual(manifest_bytes, manifest.read_bytes())

    def test_codex_manifest_does_not_authorize_wrong_type_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root, ("alpha",))
            (root / ".omp" / "agents").mkdir(parents=True)
            codex = root / "home" / ".codex"
            private = codex / "skills" / "alpha" / "PRIVATE.txt"
            private.parent.mkdir(parents=True)
            private.write_bytes(b"personal bytes\n")
            manifest = codex / ".general-agent-sync.json"
            manifest.write_text(
                json.dumps({"skills": ["alpha"], "agents": [], "rules": []}),
                encoding="utf-8",
            )

            with (
                mock.patch.object(sync_codex, "REPO", str(root)),
                mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_codex, "CODEX", str(codex)),
                mock.patch.object(sync_codex, "MANIFEST", str(manifest)),
                mock.patch.object(sync_codex, "load_role_document", return_value={}),
                mock.patch.object(sync_codex, "validate_agent_directory"),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(2, sync_codex.main())

            self.assertEqual(b"personal bytes\n", private.read_bytes())

    def test_codex_malformed_or_symlink_manifest_fails_before_mutation(self) -> None:
        cases = ("malformed", "symlink")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    skills = self.make_skill_root(root, ("alpha",))
                    (root / ".omp" / "agents").mkdir(parents=True)
                    codex = root / "home" / ".codex"
                    codex.mkdir(parents=True)
                    manifest = codex / ".general-agent-sync.json"
                    if case == "malformed":
                        manifest.write_bytes(b"not json\n")
                    else:
                        sentinel = root / "personal-manifest.json"
                        sentinel.write_bytes(b'{"skills": [], "agents": [], "rules": []}\n')
                        manifest.symlink_to(sentinel)

                    with (
                        mock.patch.object(sync_codex, "REPO", str(root)),
                        mock.patch.object(sync_codex, "SKILLS_ROOT", str(skills)),
                        mock.patch.object(sync_codex, "CODEX", str(codex)),
                        mock.patch.object(sync_codex, "MANIFEST", str(manifest)),
                        mock.patch.object(sync_codex, "load_role_document", return_value={}),
                        mock.patch.object(sync_codex, "validate_agent_directory"),
                        mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
                    ):
                        self.assertEqual(2, sync_codex.main())
                    self.assertFalse((codex / "skills" / "alpha").exists())

    def test_kimi_rejects_config_symlink_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root)
            self.make_kimi_template(skills)
            kimi = root / ".kimi-code"
            kimi.mkdir()
            sentinel = root / "sentinel.toml"
            sentinel.write_bytes(b"personal config\n")
            config = kimi / "config.toml"
            config.symlink_to(sentinel)

            with (
                mock.patch.object(sync_kimi, "REPO", str(root)),
                mock.patch.object(sync_kimi, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_kimi, "KIMI", str(kimi)),
                mock.patch.object(sync_kimi, "CFG", str(config)),
                mock.patch.object(sync_kimi, "validate_canonical_agents"),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(2, sync_kimi.main())

            self.assertEqual(b"personal config\n", sentinel.read_bytes())
            self.assertTrue(config.is_symlink())

    def test_kimi_rejects_malformed_or_duplicate_markers_without_mutation(self) -> None:
        malformed = (
            b"personal\n# >>> general-agent hooks >>>\nunclosed\n",
            b"# >>> general-agent hooks >>>\na\n# >>> general-agent hooks >>>\nb\n"
            b"# <<< general-agent hooks <<<\n",
            b"# <<< general-agent hooks <<<\nfirst\n# >>> general-agent hooks >>>\n",
        )
        for original in malformed:
            with self.subTest(original=original):
                with tempfile.TemporaryDirectory() as raw:
                    root = Path(raw)
                    skills = self.make_skill_root(root)
                    self.make_kimi_template(skills)
                    kimi = root / ".kimi-code"
                    kimi.mkdir()
                    config = kimi / "config.toml"
                    config.write_bytes(original)

                    with (
                        mock.patch.object(sync_kimi, "REPO", str(root)),
                        mock.patch.object(sync_kimi, "SKILLS_ROOT", str(skills)),
                        mock.patch.object(sync_kimi, "KIMI", str(kimi)),
                        mock.patch.object(sync_kimi, "CFG", str(config)),
                        mock.patch.object(sync_kimi, "validate_canonical_agents"),
                        mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
                    ):
                        self.assertEqual(2, sync_kimi.main())
                    self.assertEqual(original, config.read_bytes())

    def test_kimi_interrupted_replace_preserves_original_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root)
            self.make_kimi_template(skills)
            kimi = root / ".kimi-code"
            kimi.mkdir()
            config = kimi / "config.toml"
            original = b"personal config without managed block\n"
            config.write_bytes(original)
            policy_globals = sync_kimi.commit_plan.__globals__
            real_publish = policy_globals["_publish_action"]

            def interrupt_config_publish(action, staged):
                if action.path == config:
                    raise OSError("interrupted")
                return real_publish(action, staged)

            with (
                mock.patch.object(sync_kimi, "REPO", str(root)),
                mock.patch.object(sync_kimi, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_kimi, "KIMI", str(kimi)),
                mock.patch.object(sync_kimi, "CFG", str(config)),
                mock.patch.object(sync_kimi, "validate_canonical_agents"),
                mock.patch.dict(
                    policy_globals, {"_publish_action": interrupt_config_publish}
                ),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            ):
                self.assertEqual(2, sync_kimi.main())
            self.assertEqual(original, config.read_bytes())
            self.assertEqual([], list(kimi.glob(".config.toml.*")))

    def test_kimi_preserves_personal_bytes_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skills = self.make_skill_root(root)
            self.make_kimi_template(skills)
            kimi = root / ".kimi-code"
            kimi.mkdir()
            config = kimi / "config.toml"
            prefix = b"# personal prefix\r\nsetting = true\r\n\r\n"
            suffix = b"\r\n# personal suffix\r\n"
            config.write_bytes(
                prefix
                + sync_kimi.BEGIN
                + b"\nold managed bytes\n"
                + sync_kimi.END
                + suffix
            )

            patches = (
                mock.patch.object(sync_kimi, "REPO", str(root)),
                mock.patch.object(sync_kimi, "SKILLS_ROOT", str(skills)),
                mock.patch.object(sync_kimi, "KIMI", str(kimi)),
                mock.patch.object(sync_kimi, "CFG", str(config)),
                mock.patch.object(sync_kimi, "validate_canonical_agents"),
                mock.patch.dict(os.environ, {sync_policy.USER_CONFIG_WRITE_ENV: "1"}, clear=True),
            )
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                self.assertEqual(0, sync_kimi.main())
                rendered = config.read_bytes()
                first_inode = config.stat().st_ino
                self.assertEqual(prefix, rendered[: rendered.index(sync_kimi.BEGIN)])
                end = rendered.index(sync_kimi.END) + len(sync_kimi.END)
                self.assertEqual(suffix, rendered[end:])
                self.assertEqual(0, sync_kimi.main())
                self.assertEqual(first_inode, config.stat().st_ino)
                self.assertEqual(rendered, config.read_bytes())


if __name__ == "__main__":
    unittest.main()
