#!/usr/bin/env python3
from pathlib import Path
import json, sys
root = Path(__file__).resolve().parent
required = [
    'SKILL.md',
    'README.md',
    'INDEX.md',
    'test-prompts.json',
    'references/distilled-from-git-changes.md',
    'references/cleanup-rules.md',
    'references/readme-cleanup.md',
    'references/report-cleanup.md',
    'references/anti-slop-patterns.md',
    'references/fact-density-and-metaphor.md',
    'references/before-after-examples.md',
    'references/scorecard.md',
]
missing = [p for p in required if not (root / p).exists()]
if missing:
    print('Missing files:')
    for p in missing:
        print(' -', p)
    sys.exit(1)

skill = (root / 'SKILL.md').read_text(encoding='utf-8')
for needle in [
    'name: writeup-human-cleanup',
    'description:',
    'Output format',
    'Scoring',
    'Direct fact yield',
    'Metaphor cost',
    'Visual yield',
]:
    if needle not in skill:
        print(f'SKILL.md missing {needle!r}')
        sys.exit(1)

tests = json.loads((root / 'test-prompts.json').read_text(encoding='utf-8'))
if not isinstance(tests, dict) or set(tests) != {'schema_version', 'skill', 'tests'}:
    print('test-prompts.json has an invalid top-level shape')
    sys.exit(1)
if tests['schema_version'] != '1.0':
    print('test-prompts.json has an unsupported schema version')
    sys.exit(1)
if tests['skill'] != 'writeup-human-cleanup':
    print('test-prompts.json has wrong skill name')
    sys.exit(1)
if not isinstance(tests['tests'], list) or len(tests['tests']) < 10:
    print('Need at least 10 tests')
    sys.exit(1)

test_ids = []
required_test_keys = {'id', 'type', 'prompt', 'expected_behavior'}
allowed_test_types = {'should_trigger', 'should_not_trigger', 'edge_case'}
for index, test in enumerate(tests['tests']):
    if not isinstance(test, dict) or set(test) != required_test_keys:
        print(f'Test {index} has an invalid shape')
        sys.exit(1)
    if not isinstance(test['id'], str) or not test['id'].strip():
        print(f'Test {index} has an invalid id')
        sys.exit(1)
    if test['type'] not in allowed_test_types:
        print(f"Test {test['id']!r} has an invalid type")
        sys.exit(1)
    if not isinstance(test['prompt'], str) or not test['prompt'].strip():
        print(f"Test {test['id']!r} has an invalid prompt")
        sys.exit(1)
    expected = test['expected_behavior']
    if (
        not isinstance(expected, list)
        or not expected
        or any(not isinstance(item, str) or not item.strip() for item in expected)
    ):
        print(f"Test {test['id']!r} has invalid expected behavior")
        sys.exit(1)
    test_ids.append(test['id'])

if len(set(test_ids)) != len(test_ids):
    print('test-prompts.json has duplicate test ids')
    sys.exit(1)
required_test_ids = {
    'metaphor_density_review',
    'visual_fact_yield',
    'density_without_jargon_cramming',
}
if missing_test_ids := required_test_ids - set(test_ids):
    print('Missing fact-density tests:', ', '.join(sorted(missing_test_ids)))
    sys.exit(1)

print('writeup-human-cleanup: validation passed')
