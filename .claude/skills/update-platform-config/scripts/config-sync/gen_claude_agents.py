#!/usr/bin/env python3
"""gen_claude_agents.py — generate .claude/agents/*.md (Claude Code subagents) from .omp/agents/.

The V2 rule: single tasks a skill can describe go to NATIVE SUBAGENTS (better message passing +
session management than shelling to unrelated CLIs). Claude Code discovers subagents in the repo's
.claude/agents/ — this maps our canonical .omp/agents/*.md there (frontmatter translated; body
verbatim). Files are COMMITTED (they travel with the clone); re-run on any .omp/agents change.
Mapping: tools lowercase→CC names; ``model-role`` is resolved through models.json and a native
Claude model is rendered with its exact supported identity (legacy Opus/Sonnet families use their
short Claude Code aliases). An agent is emitted only when the canonical role has
``backend=claude`` and ``provider=anthropic``. Cursor-hosted ``cursor/claude-*`` roles are
omitted from ``.claude/agents`` and remain available only through Cursor/subagent surfaces; they
are never collapsed or relabeled onto native Claude aliases. For a cross-provider reviewer,
``writer-role`` must also resolve to an opposite provider.
"""
import os
from pathlib import Path
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from agent_roles import (
    AgentRoleError,
    expected_model_snapshot,
    load_role_document,
    parse_frontmatter,
    resolve_agent,
    validate_agent_directory,
)
from project_paths import project_root
from sync_policy import (
    CanonicalInputs,
    SyncPlan,
    SyncTransactionError,
    add_canonical_input_guards,
    add_role_document_guard,
    capture_path_state,
    commit_plan,
)

REPO = str(project_root())
SRC = os.path.join(REPO, ".omp", "agents")
DST = os.path.join(REPO, ".claude", "agents")

TOOL_MAP = {"read": "Read", "grep": "Grep", "glob": "Glob", "bash": "Bash",
            "web_search": "WebSearch", "ls": "Glob", "write": "Write", "edit": "Edit"}
GENERATED_MARKER = "GENERATED from .omp/agents"


def cc_model(chain: list[str]) -> str | None:
    for m in chain:
        mm = re.fullmatch(
            r"(?:([^/]+)/)?(claude-(opus|sonnet|haiku|fable)(?:-[a-z0-9]+)*)(?::[a-z0-9]+)?",
            m,
        )
        if not mm:
            continue
        if mm.group(1) == "cursor":
            continue
        # Fable has no safe Opus-equivalent alias. Preserve the exact identity so an admitted
        # Fable route either launches Fable or fails closed in the native resolver.
        if mm.group(3) == "fable":
            return mm.group(2)
        return mm.group(3)
    return None


def is_claude_model(spec: str) -> bool:
    return re.match(r"(?:[^/]+/)?claude-(opus|sonnet|haiku|fable)", spec) is not None


def parse(md: str) -> tuple[dict, str]:
    return parse_frontmatter(md)


def provider_compatible(meta: dict, role_document: dict | None = None) -> bool:
    """Whether Claude Code can host this agent as a native subagent.

    Native generation requires the canonical role ``backend == "claude"`` and
    ``provider == "anthropic"``. Cursor-hosted Claude-family roles stay on
    Cursor/subagent surfaces and are never collapsed to a native Claude alias.
    """
    document = role_document if role_document is not None else load_role_document()
    route = resolve_agent(meta, document)
    return (
        route.role.get("backend") == "claude"
        and route.role.get("provider") == "anthropic"
        and is_claude_model(expected_model_snapshot(route.role))
    )


def build_claude_agent_plan(
    role_document: dict,
    *,
    canonical_inputs: CanonicalInputs | None = None,
    source_root: Path | None = None,
    destination_root: Path | None = None,
) -> SyncPlan:
    """Render native-Claude-compatible agents into a read-only transaction plan."""

    source_directory = Path(SRC) if source_root is None else source_root
    destination = Path(DST) if destination_root is None else destination_root
    plan = SyncPlan("claude-agents")
    records: list[tuple[Path, dict, str, object]] = []

    if canonical_inputs is not None:
        if canonical_inputs.role_document is not role_document:
            raise RuntimeError("Claude builder received a different canonical role document")
        add_canonical_input_guards(plan, canonical_inputs)
        records = [
            (agent.path, agent.metadata, agent.body, agent.route)
            for agent in canonical_inputs.agents
        ]
    else:
        plan.add_directory_chain(source_directory, mode=0o755)
        source_directory_state = capture_path_state(source_directory)
        if source_directory_state.kind != "directory":
            raise RuntimeError(
                f"canonical agent source is not a directory: {source_directory}"
            )
        plan.add_guard(
            source_directory,
            source_directory_state,
            label="canonical agent directory membership",
        )
        for source in sorted(source_directory.iterdir(), key=lambda item: item.name):
            if source.suffix != ".md":
                continue
            state = plan.add_file_guard(source, label=f"canonical agent {source.name}")
            assert state.content is not None
            meta, body = parse(state.content.decode("utf-8"))
            route = resolve_agent(meta, role_document, str(source))
            records.append((source, meta, body, route))
        if capture_path_state(source_directory) != source_directory_state:
            raise RuntimeError(
                f"{source_directory}: canonical agent directory changed while read"
            )

    rendered: dict[str, bytes] = {}
    made_names: list[str] = []
    skipped: list[str] = []
    for source, meta, body, route in records:
        filename = source.name
        name = meta.get("name", source.stem)
        tools = ", ".join(dict.fromkeys(TOOL_MAP[t.strip()] for t in meta.get("tools", "").split(",")
                                        if t.strip() in TOOL_MAP)) if meta.get("tools") else ""
        model = cc_model([expected_model_snapshot(route.role)])
        compatible = (
            route.role.get("backend") == "claude"
            and route.role.get("provider") == "anthropic"
            and is_claude_model(expected_model_snapshot(route.role))
        )
        if not compatible:
            skipped.append(name)
            continue
        fm = [f"name: {name}", f"description: \"{meta.get('description', '')[:1000]}\""]
        if tools:
            fm.append(f"tools: {tools}")
        if model:
            fm.append(f"model: {model}")
        out = ("---\n" + "\n".join(fm) + "\n---\n"
               "<!-- " + GENERATED_MARKER + "/" + filename + " by gen_claude_agents.py — edit THERE, then re-run (sync_all.py). -->\n\n"
               + body.strip() + "\n")
        rendered[filename] = out.encode("utf-8")
        made_names.append(name)

    plan.add_directory_chain(destination, mode=0o755)
    conflicts: list[str] = []
    for filename, content in rendered.items():
        target = destination / filename
        current = capture_path_state(target)
        if current.kind not in {"missing", "regular file"}:
            conflicts.append(str(target))
            continue
        if (
            current.kind == "regular file"
            and current.content is not None
            and GENERATED_MARKER.encode("utf-8") not in current.content
        ):
            conflicts.append(str(target))
            continue
        mode = (
            current.snapshot.mode
            if current.snapshot is not None
            else 0o644
        )
        plan.add_write(
            target,
            content,
            mode=mode,
            before=current,
            label=f"generated Claude agent {filename}",
        )

    destination_state = capture_path_state(destination)
    if destination_state.kind == "directory":
        for target in sorted(destination.iterdir(), key=lambda item: item.name):
            if target.suffix != ".md" or target.name in rendered:
                continue
            current = capture_path_state(target)
            if current.kind != "regular file":
                conflicts.append(str(target))
                continue
            assert current.content is not None
            if GENERATED_MARKER.encode("utf-8") in current.content:
                plan.add_delete(
                    target,
                    before=current,
                    allowed_kinds=frozenset({"regular file"}),
                    label=f"stale generated Claude agent {target.name}",
                )
    if conflicts:
        raise RuntimeError(
            "refusing to overwrite non-generated destination(s): "
            + ", ".join(conflicts)
        )

    plan.metadata.update(
        {
            "claude_agent_count": len(rendered),
            "claude_agent_names": tuple(made_names),
            "claude_agent_skipped": tuple(skipped),
        }
    )
    return plan


def main() -> int:
    try:
        model_path = Path(REPO) / "config" / "workflows" / "models.json"
        role_document = load_role_document(model_path)
        validate_agent_directory(Path(SRC), role_document)
        plan = build_claude_agent_plan(role_document)
        add_role_document_guard(plan, model_path, role_document)
        commit_plan(plan)
    except (AgentRoleError, OSError, RuntimeError, SyncTransactionError, UnicodeError) as error:
        print(f"[gen-claude-agents] {error}", file=sys.stderr)
        return 2
    made_names = list(plan.metadata["claude_agent_names"])
    skipped = list(plan.metadata["claude_agent_skipped"])
    suffix = f"; role-incompatible agents omitted: {', '.join(skipped)}" if skipped else ""
    print(
        f"[gen-claude-agents] {plan.metadata['claude_agent_count']} subagents: "
        f"{', '.join(made_names)}{suffix}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
