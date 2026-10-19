#!/usr/bin/env python3
"""Fail closed unless every reusable agent resolves through canonical model roles."""

from __future__ import annotations

from pathlib import Path
import sys

from agent_roles import AGENT_DIR, ROLE_PATH, AgentRoleError, validate_canonical_agents


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    role_path = Path(args[0]).resolve() if args else ROLE_PATH
    agent_dir = Path(args[1]).resolve() if len(args) > 1 else AGENT_DIR
    try:
        routes = validate_canonical_agents(role_path, agent_dir)
    except AgentRoleError as error:
        print(f"[validate-agent-roles] {error}", file=sys.stderr)
        return 2
    print(f"[validate-agent-roles] valid: {len(routes)} agents from {role_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
