#!/usr/bin/env bash
# init.sh — create <dir>/<relpath> bitlesson.md from the template if missing. Prints the path.
# Usage: init.sh [project-dir=.] [relpath=bitlesson.md]
set -euo pipefail
ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$ROOT" ] || ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"   # fallback if not a git checkout
DIR="${1:-.}"; REL="${2:-bitlesson.md}"
TEMPLATE="$(dirname "$0")/../references/bitlesson.template.md"
[ -f "$TEMPLATE" ] || { echo "template missing: $TEMPLATE" >&2; exit 1; }
case "$REL" in /*|*..*) echo "relpath must be relative, no '..'" >&2; exit 1;; esac
out="$DIR/$REL"
[ -f "$out" ] || { mkdir -p "$(dirname "$out")"; cp "$TEMPLATE" "$out"; }
printf '%s\n' "$out"
