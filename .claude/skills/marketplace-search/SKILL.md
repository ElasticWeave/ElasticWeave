---
name: marketplace-search
description: "Perform read-only local-first discovery for reusable skills, agents, plugins, workflows, or mappings before authoring one; adoption requires separate authorization."
---
> **Model tier:** MEDIUM — search is mechanical but VETTING needs judgment


# marketplace-search — reuse before you build

Search-and-adapt beats greenfield. Run this before authoring any **skill / agent / flow**.
Helper: `scripts/find.py --root <project-root> "<domain keywords>"` automates steps 1–2 without
cloning, installing, copying, enabling, or mutating anything.

## Where to look (best first)
1. **Already project-local (zero cost):** installed `.claude/skills/`, their `assets/`,
   plus any project-generated `.omp/agents/`, `.workflow/designs/`, `.subagent/flows/`, and
   `.humanize/flows/`.
2. **Curated GitHub collections** — `hesreallyhim/awesome-claude-code`,
   `rohitg00/awesome-claude-code-toolkit`, `ComposioHQ/awesome-claude-skills`, and high-signal
   workflow-engine repositories. Inspect metadata and source before cloning.
3. **Generic SKILL.md puller** (any repo, any dir): sparse-checkout
   (`git clone --depth 1 --filter=blob:none --sparse <r> && git sparse-checkout set <dir>`)
   or `gh api repos/<r>/contents/<dir>` → read the `download_url` — inspect without cloning.
4. **MCP tool servers** — `registry.modelcontextprotocol.io` first, then Smithery / mcp.so;
   wire into project `.omp/mcp.json`.

Workflows have **no universal marketplace**. Extract reusable intent into a neutral
`workflow-design/v1` artifact with `build-workflow`; treat external harness examples as mapping
references rather than canonical workflow definitions.

## Procedure (`scripts/find.py` does 1–2)
1. **Local first** — enumerate the tiers above; it may already be installed.
2. **Enumerate** via 3 merged query families only when `gh` is present and authenticated:
   `gh search repos "<domain> claude code" --sort=stars`,
   `gh search repos "<domain> subagent|skill" --sort=stars`, `gh search repos "awesome claude"` (mine the index).
   Distinguish `unavailable`, `unauthenticated`, command failure, invalid JSON, and a genuine empty
   result. Never reinterpret a tool/auth failure as “no reusable candidate exists.”
3. **Verify each**: `gh repo view <r> --json nameWithOwner,stargazerCount,pushedAt,licenseInfo,description` —
   drop stale (>~3 months) or unlicensed. Never trust topic-search star counts; `gh search code
   --filename=SKILL.md` is noise.
4. **Inspect without adoption** using read-only API/source views. Select at most one candidate.
5. **VET before enabling (mandatory):** read the SKILL.md + any scripts/hooks/network calls +
   maintainer + permissions; score domain-fit + quality. A vague/over-broad skill DILUTES the
   team — reject it.
6. **Obtain separate adoption authorization**, binding the exact source revision, destination,
   requested permissions/network/hooks, and selected adapter. Discovery grants no install, clone,
   copy, enable, activation, or config-mutation authority.
7. **ADAPT, don't fork blindly:** after authorization, copy in, rewrite the frontmatter `description` (its trigger)
   + body to our domain + invariants, and strip tools you don't grant. The bounded marketplace
   owner records its own terse source residue in `.memory/skills/marketplace-search.md`, describing
   what was adopted and how it was adapted. A top session only authorizes and inspects that diff;
   it never appends memory or performs the adaptation itself.

## When to run
- **At default-mode startup:** domain-expert skills + base agents for the work area (default-mode step 8).
- **At dispatch:** a base design/skill for the sub-task — if a neutral design already fits, map it
  through an active capability-compatible adapter; if only an external platform example fits,
  extract its intent into a neutral design first. Build
  greenfield **only when the search comes up genuinely empty**.
