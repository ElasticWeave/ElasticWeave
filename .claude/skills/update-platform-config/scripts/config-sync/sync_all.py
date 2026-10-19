#!/usr/bin/env python3
"""Preflight and commit every enabled native surface as one transaction."""

from __future__ import annotations

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from agent_roles import AgentRoleError  # noqa: E402
from gen_claude_agents import build_claude_agent_plan  # noqa: E402
from project_paths import project_root, skills_root  # noqa: E402
from retire_legacy_hooks import (  # noqa: E402
    build_retired_hooks_plan,
    preflight_retired_hooks,
)
from sync_codex import build_codex_plan  # noqa: E402
from sync_kimi import build_kimi_plan  # noqa: E402
from sync_policy import (  # noqa: E402
    USER_CONFIG_WRITE_ENV,
    CanonicalInputs,
    SyncPlan,
    SyncTransactionError,
    commit_plan,
    load_canonical_inputs,
    user_config_write_enabled,
)
from sync_project_instructions import build_project_instruction_plan  # noqa: E402


def build_aggregate_plan(
    root: Path,
    installed_skills: Path,
    *,
    include_user_config: bool,
    canonical_inputs: CanonicalInputs | None = None,
) -> SyncPlan:
    """Build every enabled renderer before allowing the first filesystem mutation."""

    project = root.expanduser().absolute()
    skills = installed_skills.expanduser().absolute()
    inputs = canonical_inputs or load_canonical_inputs(
        project / "config" / "workflows" / "models.json",
        project / ".omp" / "agents",
    )

    # Retirement is preflighted first because an ambiguous legacy path must block all output.
    retirement = build_retired_hooks_plan(preflight_retired_hooks(project))
    instructions = build_project_instruction_plan(
        project,
        skills / "default-mode" / "references" / "project-session-contract.md",
    )
    claude = build_claude_agent_plan(
        inputs.role_document,
        canonical_inputs=inputs,
        source_root=project / ".omp" / "agents",
        destination_root=project / ".claude" / "agents",
    )

    aggregate = SyncPlan("all-platform-surfaces")
    aggregate.extend(instructions)
    if include_user_config:
        aggregate.extend(
            build_codex_plan(inputs.role_document, canonical_inputs=inputs)
        )
        aggregate.extend(
            build_kimi_plan(repo=project, skills=skills)
        )
    aggregate.extend(claude)
    # Recoverable legacy-artifact removals stay last in apply order, but were preflighted above.
    aggregate.extend(retirement)
    aggregate.metadata["user_config_enabled"] = include_user_config
    return aggregate


def _print_result(plan: SyncPlan) -> None:
    changed = plan.metadata.get("project_instructions_changed", ())
    print(
        "[sync-project-instructions] changed: "
        + (", ".join(changed) if changed else "none")
    )
    if plan.metadata.get("user_config_enabled"):
        skipped = plan.metadata.get("codex_skipped", ())
        if skipped:
            print(f"[sync-codex] role-incompatible agents omitted: {', '.join(skipped)}")
        print(
            f"[sync-codex] skills: {len(plan.metadata.get('codex_skills', ()))} symlinked; "
            f"agents: {len(plan.metadata.get('codex_agents', ()))} TOML-generated; "
            f"rules: {len(plan.metadata.get('codex_rules', ()))} copied "
            "(manifest-managed; personal entries untouched)"
        )
        print(
            "[sync-kimi] skills auto-discovered from .claude/skills (no sync); "
            f"hooks -> {plan.metadata.get('kimi_config')} "
            "(general-agent block; personal config preserved)"
        )

    skipped = plan.metadata.get("claude_agent_skipped", ())
    suffix = f"; role-incompatible agents omitted: {', '.join(skipped)}" if skipped else ""
    print(
        f"[gen-claude-agents] {plan.metadata.get('claude_agent_count', 0)} subagents: "
        f"{', '.join(plan.metadata.get('claude_agent_names', ()))}{suffix}"
    )
    retired = plan.metadata.get("retired_hooks", ())
    print(
        "[sync-all] retired legacy artifacts removed: "
        + (", ".join(retired) if retired else "none")
    )


def main() -> int:
    root = project_root()
    installed_skills = skills_root()
    include_user_config = user_config_write_enabled()
    if not include_user_config:
        print(
            "[sync-all] user-level Codex/Kimi config is unchanged; "
            f"set {USER_CONFIG_WRITE_ENV}=1 to opt in"
        )

    try:
        plan = build_aggregate_plan(
            root,
            installed_skills,
            include_user_config=include_user_config,
        )
    except (AgentRoleError, OSError, RuntimeError, UnicodeError) as error:
        print(
            f"[sync-all] aggregate preflight failed; no derived config was written: {error}",
            file=sys.stderr,
        )
        return 2

    try:
        commit_plan(plan)
    except SyncTransactionError as error:
        prefix = (
            "[sync-all] transaction failed with incomplete rollback"
            if error.rollback_incomplete
            else "[sync-all] transaction failed; all published changes were rolled back"
        )
        print(f"{prefix}: {error}", file=sys.stderr)
        return 3 if error.rollback_incomplete else 2

    _print_result(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
