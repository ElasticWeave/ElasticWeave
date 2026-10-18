#!/usr/bin/env python3
"""sync_kimi.py — Kimi Code sync. Skills need NO sync (kimi auto-discovers .claude/skills/ at
project level). Only HOOKS need mapping: our protect_paths.py wired into ~/.kimi-code/config.toml's
[[hooks]] between BEGIN/END markers (the user's own config is preserved verbatim outside them)."""
import os
import sys
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from agent_roles import AgentRoleError, validate_canonical_agents
from project_paths import project_root, skills_root
from sync_policy import (
    USER_CONFIG_WRITE_ENV,
    SyncPlan,
    SyncTransactionError,
    capture_path_state,
    commit_plan,
    render_managed_block,
    user_config_write_enabled,
)

REPO = str(project_root())
SKILLS_ROOT = str(skills_root())
KIMI = os.path.expanduser(os.environ.get("KIMI_CODE_HOME", "~/.kimi-code"))
CFG = os.path.join(KIMI, "config.toml")
BEGIN = b"# >>> general-agent hooks >>>"
END = b"# <<< general-agent hooks <<<"


def build_kimi_plan(
    repo: Path | None = None,
    skills: Path | None = None,
    kimi_home: Path | None = None,
    config: Path | None = None,
) -> SyncPlan:
    """Render the one managed Kimi block without changing user configuration."""

    repository = Path(REPO) if repo is None else repo
    skills_root_path = Path(SKILLS_ROOT) if skills is None else skills
    kimi_path = Path(KIMI) if kimi_home is None else kimi_home
    config_path = Path(CFG) if config is None else config
    template_path = (
        skills_root_path
        / "update-platform-config"
        / "assets"
        / "project"
        / "config"
        / "kimi"
        / "hooks.toml"
    )

    plan = SyncPlan("kimi")
    plan.add_directory_chain(template_path.parent, mode=0o755)
    template_state = plan.add_file_guard(template_path, label="Kimi hook template")
    assert template_state.content is not None
    template = template_state.content.decode("utf-8")
    block = (
        BEGIN
        + b"\n"
        + template.replace("GENERAL_AGENT_REPO", str(repository)).strip().encode("utf-8")
        + b"\n"
        + END
        + b"\n"
    )

    plan.add_directory_chain(kimi_path, mode=0o700)
    current = capture_path_state(config_path)
    if current.kind not in {"missing", "regular file"}:
        raise RuntimeError(
            f"{config_path}: expected a regular file, found {current.kind}"
        )
    existing = current.content or b""
    rendered = render_managed_block(existing, block, BEGIN, END, config_path)
    mode = (
        current.snapshot.mode
        if current.snapshot is not None
        else 0o600
    )
    changed = plan.add_write(
        config_path,
        rendered,
        mode=mode,
        before=current,
        label="Kimi managed hook block",
    )
    plan.metadata.update(
        {
            "kimi_config": str(config_path),
            "kimi_changed": changed,
        }
    )
    return plan


def main() -> int:
    if not user_config_write_enabled():
        print(
            f"[sync-kimi] skipped: set {USER_CONFIG_WRITE_ENV}=1 to allow user-config writes"
        )
        return 0
    try:
        validate_canonical_agents()
    except AgentRoleError as error:
        print(f"[sync-kimi] invalid canonical agent routing: {error}", file=sys.stderr)
        return 2
    try:
        plan = build_kimi_plan()
        commit_plan(plan)
    except (OSError, RuntimeError, SyncTransactionError, UnicodeError) as error:
        print(f"[sync-kimi] refusing unsafe config update: {error}", file=sys.stderr)
        return 2
    print(f"[sync-kimi] skills auto-discovered from .claude/skills (no sync); hooks → {CFG} "
          f"(general-agent block; personal config preserved)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
