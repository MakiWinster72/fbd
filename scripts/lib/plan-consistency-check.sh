#!/bin/sh
set -eu
uv run --offline --with pyyaml python - "$1" <<'PY'
import pathlib, sys, yaml
root = pathlib.Path(sys.argv[1])
errors = []
epic = yaml.safe_load((root / 'epic.yaml').read_text())
for story in epic['stories']:
    spec = root / story['spec']
    task_file = root / story['tasks']
    data = yaml.safe_load(task_file.read_text())
    if data['story_id'] != story['id']:
        errors.append('story_id mismatch')
    if data['bcps'] != story['bcps']:
        errors.append('BCP mismatch')
    text = spec.read_text()
    for i in range(1, 21):
        if f'## {i}. ' not in text:
            errors.append(f'missing section {i}')
    if '**Before:**' not in text or '**After:**' not in text:
        errors.append('missing requirement before/after')
    ids = []
    severity = {'P0': 'critical', 'P1': 'high', 'P2': 'normal', 'P3': 'minor'}
    for task in data['tasks']:
        ids.append(task['id'])
        if not task.get('verify') or task['risk'] not in severity:
            errors.append('missing verification or invalid risk')
        if task['status'] not in ('failing', 'passing'):
            errors.append('invalid task status')
        if task['allure']['severity'] != severity[task['risk']]:
            errors.append('severity mismatch')
        if task['verify'] not in text:
            errors.append(f'verify command absent from story: {task["id"]}')
    if len(ids) != len(set(ids)):
        errors.append('duplicate task IDs')
for error in errors:
    print('HIGH:', error)
print(f'CRITICAL: 0; HIGH: {len(errors)}; MED: 0')
sys.exit(bool(errors))
PY
