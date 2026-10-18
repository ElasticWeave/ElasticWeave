#!/usr/bin/env bash
# validate_delta.sh <summary-file> <bitlesson-file>
# Enforces the "## BitLesson Delta" contract on a subordinate's hand-up summary, so a
# −1 team-leader pushes a DISTILLED lesson (not raw memory). Exit 0 = valid; exit 2 +
# reason on stderr = BLOCK (wire into a Stop hook / the actual-writer-aware commit-review lane).
#
# Required block in the summary:
#   ## BitLesson Delta
#   - Action: none | add | update
#   - Lesson ID(s): <BL-YYYYMMDD-name[,...] | NONE>
#   - Notes: <what changed and why>
# Rules: Action=none ⇒ IDs=NONE. add/update ⇒ IDs match ^BL-[0-9]{8}-[A-Za-z0-9._-]+$,
#        each ID already EXISTS in the bitlesson file, Notes is non-empty, and every referenced
#        lesson declares >=2 task contexts, independence, de-identification, applicability, and a
#        counterexample/falsifier. These declarations are mechanically necessary, not proof.
set -uo pipefail
SUM="${1:?usage: validate_delta.sh <summary> <bitlesson>}"; KB="${2:?bitlesson file}"
fail(){ echo "BITLESSON DELTA INVALID: $*" >&2; exit 2; }
[ -f "$SUM" ] || fail "summary not found: $SUM"

block="$(awk '/^##[[:space:]]+BitLesson Delta/{f=1;next} /^##[[:space:]]/{if(f)exit} f' "$SUM")"
[ -n "$block" ] || fail "missing '## BitLesson Delta' section in the hand-up summary"

action="$(grep -ioE 'Action:[[:space:]]*(none|add|update)' <<<"$block" | head -1 | grep -ioE 'none|add|update' | tr '[:upper:]' '[:lower:]')"
ids="$(grep -iE 'Lesson ID' <<<"$block" | head -1 | sed -E 's/.*:[[:space:]]*//' | tr -d '[:space:]')"
notes="$(grep -iE 'Notes:' <<<"$block" | head -1 | sed -E 's/.*[Nn]otes:[[:space:]]*//')"

[ -n "$action" ] || fail "Action must be one of none|add|update"

if [ "$action" = none ]; then
  [ "$(printf '%s' "$ids" | tr '[:lower:]' '[:upper:]')" = NONE ] && exit 0 || fail "Action: none requires 'Lesson ID(s): NONE'"
fi

# add | update
{ [ -n "$ids" ] && [ "$(printf '%s' "$ids" | tr '[:lower:]' '[:upper:]')" != NONE ]; } || fail "Action: $action requires real BL- ID(s)"
[ -n "${notes//[[:space:]]/}" ] && ! grep -qiE 'placeholder|<.*>|TODO' <<<"$notes" || fail "Notes must be concrete (non-empty, no placeholders) for add/update"
[ -f "$KB" ] || fail "bitlesson file not found: $KB"
IFS=',' read -ra arr <<<"$ids"
for id in "${arr[@]}"; do
  id="$(printf '%s' "$id" | tr -d '[:space:]')"
  [[ "$id" =~ ^BL-[0-9]{8}-[A-Za-z0-9._-]+$ ]] || fail "bad ID format: '$id' (want BL-YYYYMMDD-short-name)"
  grep -qF "Lesson ID: $id" "$KB" || fail "ID not present in $KB: $id (add the entry to the BitLesson file first)"

  lesson_block="$(awk -v target="Lesson ID: $id" '
    /^## Lesson:/ {
      if (found) exit
      block = $0 ORS
      next
    }
    {
      block = block $0 ORS
      if ($0 == target) found = 1
    }
    END { if (found) printf "%s", block }
  ' "$KB")"
  [ -n "$lesson_block" ] || fail "could not isolate lesson block for $id"

  field(){
    awk -v key="$1:" 'index($0, key) == 1 { value = substr($0, length(key) + 1); sub(/^[[:space:]]+/, "", value); print value; exit }' <<<"$lesson_block"
  }
  concrete(){
    local label="$1" value="$2"
    [ -n "${value//[[:space:]]/}" ] || fail "$id requires non-empty '$label'"
    ! grep -qiE 'placeholder|TODO|TBD|<[^>]+>' <<<"$value" || fail "$id '$label' must be concrete"
  }

  contexts="$(field "Task Contexts")"
  concrete "Task Contexts" "$contexts"
  declare -A seen_contexts=()
  IFS=',' read -ra context_arr <<<"$contexts"
  for context in "${context_arr[@]}"; do
    context="$(sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' <<<"$context")"
    [ -n "$context" ] && seen_contexts["$context"]=1
  done
  [ "${#seen_contexts[@]}" -ge 2 ] || fail "$id requires at least two distinct Task Contexts; reruns of one task are one context"

  independence="$(field "Independence")"
  applicability="$(field "Applicability")"
  counterexample="$(field "Counterexamples / Falsifier")"
  concrete "Independence" "$independence"
  concrete "Applicability" "$applicability"
  concrete "Counterexamples / Falsifier" "$counterexample"

  evidence="$(field "Validation Evidence")"
  concrete "Validation Evidence" "$evidence"
  declare -A seen_evidence=()
  IFS=',' read -ra evidence_arr <<<"$evidence"
  for pointer in "${evidence_arr[@]}"; do
    pointer="$(sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' <<<"$pointer")"
    [ -n "$pointer" ] && seen_evidence["$pointer"]=1
  done
  [ "${#seen_evidence[@]}" -ge 2 ] || fail "$id requires at least two distinct Validation Evidence pointers"

  identifiers_removed="$(field "Project Identifiers Removed" | tr '[:upper:]' '[:lower:]')"
  [ "$identifiers_removed" = yes ] || fail "$id requires 'Project Identifiers Removed: yes'"
done
exit 0
