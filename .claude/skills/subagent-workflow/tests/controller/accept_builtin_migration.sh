#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/../../../../.." && pwd -P)"
skills_root="${repo_root}/.claude/skills"
subagent_skill="${skills_root}/subagent-workflow"
project_assets="${subagent_skill}/assets/project"
temporary_project="$(mktemp -d)"
trap 'rm -rf "${temporary_project}"' EXIT

cd "${repo_root}"
git diff --check
python3 "${skills_root}/default-mode/scripts/validate_bundle.py"
python3 "${skills_root}/default-mode/scripts/test_validate_bundle.py"
python3 "${skills_root}/default-mode/scripts/test_materialize.py"
python3 -m unittest discover \
  -s "${skills_root}/update-platform-config/scripts/config-sync" -p 'test_*.py'

python3 "${skills_root}/build-workflow/scripts/test_validate_design.py"
python3 "${skills_root}/build-workflow/scripts/validate_design.py" \
  "${project_assets}/.workflow/designs/standard-round.workflow.json"
python3 "${skills_root}/build-workflow/scripts/validate_design.py" \
  "${project_assets}/.workflow/designs/review-gated-loop.workflow.json"
python3 "${skills_root}/run-workflow/scripts/test_select_harness.py"

python3 "${skills_root}/update-platform-config/tests/hooks/claude/test_protect_paths.py"
bun test "${skills_root}/update-platform-config/tests/hooks/omp/"*.test.ts

bun "${project_assets}/.workflow/resources/standard-round/build_mapping.mjs" --check
bun test "${subagent_skill}/tests/native/run_contract.test.ts"
bun test "${subagent_skill}/tests/controller/"*.test.ts
python3 "${script_dir}/verify_evidence.py"

python3 "${skills_root}/default-mode/scripts/materialize.py" \
  --project-root "${temporary_project}" --skills-root "${skills_root}"
mkdir -p "${temporary_project}/.claude"
cp -R "${skills_root}" "${temporary_project}/.claude/skills"
(
  cd "${temporary_project}"
  python3 "${skills_root}/memory/scripts/validate.py"
  bun "${subagent_skill}/scripts/controller/run.ts" validate \
    .subagent/flows/standard-round.json
  bun "${subagent_skill}/scripts/controller/run.ts" validate \
    .subagent/flows/review-gated-loop.json
)

printf '%s\n' 'PASS: skill-owned built-in workflow runtime and materialized project assets'
