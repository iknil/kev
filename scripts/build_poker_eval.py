"""Build a balanced, reproducible test subset from the frozen Poker v1 suite."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from kev.suite import digest, read_manifest, write_json
from scripts.build_poker_lite import _scan_source, _select_lines

STREETS = ('preflop', 'flop', 'turn', 'river')


def balanced_quotas(capacities: dict[str, int], count: int) -> dict[str, int]:
    """Equal allocation, redistributing exhausted buckets in lexical order."""
    if count < 0 or count > sum(capacities.values()):
        raise ValueError('requested count exceeds available records')
    quotas = dict.fromkeys(sorted(capacities), 0)
    while count:
        for key in quotas:
            if count and quotas[key] < capacities[key]:
                quotas[key] += 1
                count -= 1
    return quotas


def build(parent: Path, out: Path, count: int = 1000, seed: int = 42) -> dict:
    parent, out = parent.resolve(), out.resolve()
    if count <= 0:
        raise ValueError('count must be positive')
    if out == parent or parent in out.parents or out.exists():
        raise ValueError('output must be a new directory outside the parent suite')
    manifest_hash = digest(parent / 'manifest.json')
    manifest = read_manifest(parent)
    if manifest['suite'] != 'poker-v1':
        raise ValueError('expected poker-v1 parent suite')
    source = parent / 'test.jsonl'
    counts, total, source_hash = _scan_source(source)
    expected = manifest['files']['test.jsonl']
    if source_hash != expected['sha256'] or total != expected['records']:
        raise ValueError('parent test hash/count does not match manifest')
    if {key.split('|')[0] for key in counts} != set(STREETS):
        raise ValueError('expected all four poker streets')
    street_counts = {street: sum(n for key, n in counts.items() if key.startswith(street + '|')) for street in STREETS}
    street_quotas = balanced_quotas(street_counts, count)
    quotas = {}
    for street in STREETS:
        quotas.update(balanced_quotas({k: n for k, n in counts.items() if k.startswith(street + '|')}, street_quotas[street]))
    selected = _select_lines(source, source_hash, total, quotas, seed)
    lines = sorted(row for rows in selected.values() for row in rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.poker-eval-', dir=out.parent))
    try:
        (staging / 'test.jsonl').write_bytes(b''.join(raw for _, raw in lines))
        report = {
            'parent_manifest_sha256': manifest_hash, 'parent_test_sha256': source_hash,
            'seed': seed, 'count': count,
            'algorithm': 'Equal street quotas, then equal label-action quotas within each street; capacity-limited redistribution with lexical ties. Lowest SHA256(seed:id) per stratum; raw lines in source order.',
            'strata': {key: {'available': counts[key], 'selected': quotas[key]} for key in sorted(counts)},
            'streets': street_quotas,
        }
        write_json(staging / 'selection-report.json', report)
        readme = f'''# Poker balanced evaluation

{count} test records selected from the frozen poker-v1 test partition. Streets receive equal quotas where capacity allows. Within each street, final label action types receive equal quotas. Remainders use lexical order. Preflop has no Bet labels. All-in labels retain the original Bet/Raise representation and amounts; they are not a separate stratum. Other attributes such as stack size and position are not explicitly balanced.

Seed {seed}; lowest SHA256(seed:id) wins within each stratum. Original state, options, labels, metadata and line bytes are unchanged. The manifest pins the source and output hashes. No timestamps or output paths enter the generated files. The builder refuses to overwrite an existing directory.

Rebuild from the local migrated original suite into a fresh directory:

```sh
uv run python -m scripts.build_poker_eval --parent evals/poker-v1 --out /tmp/poker-eval-rebuilt --count {count} --seed {seed}
```

This is a reselected balanced subset, not an independent test set or necessarily a superset of the earlier 550 records. Its accuracy weights streets and actions differently from the proportional lite test. Keep training/calibration/development in poker-v1-lite. Use this suite only for locked test evaluation with --allow-test. No model evaluation is performed by this builder.
'''
        (staging / 'README.md').write_text(readme, encoding='utf-8')
        output = {key: manifest[key] for key in ('dataset', 'dataset_revision', 'license', 'context', 'base_revisions') if key in manifest}
        output.update(suite=f'poker-v1-lite-eval-{count}', version=1, eval_only=True,
                      trainable_sources=[], eval_only_sources=manifest.get('eval_only_sources', []),
                      counts={'test': count}, selection=report,
                      files={name: {'sha256': digest(staging / name), 'bytes': (staging / name).stat().st_size,
                                    **({'records': count} if name.endswith('.jsonl') else {})}
                             for name in ('test.jsonl', 'selection-report.json', 'README.md')})
        write_json(staging / 'manifest.json', output)
        if digest(parent / 'manifest.json') != manifest_hash:
            raise ValueError('parent manifest changed during build')
        os.rename(staging, out)
        return output
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def update_suite(parent: Path, suite: Path, count: int = 1000, seed: int = 42) -> dict:
    """Replace only the test partition of an existing lite suite."""
    suite = suite.resolve()
    manifest = read_manifest(suite)
    if manifest.get('suite') != 'poker-v1-lite':
        raise ValueError('expected existing poker-v1-lite suite')
    for name, spec in manifest['files'].items():
        if digest(suite / name) != spec['sha256']:
            raise ValueError(f'existing suite checksum mismatch: {name}')
    with tempfile.TemporaryDirectory() as tmp:
        generated = Path(tmp) / 'evaluation'
        evaluation = build(parent, generated, count, seed)
        report = json.loads((suite / 'selection-report.json').read_text(encoding='utf-8'))
        selection = evaluation['selection']
        report['partitions']['test'] = {
            'source_records': sum(v['available'] for v in selection['strata'].values()),
            'selected_records': count,
            'source_sha256': selection['parent_test_sha256'],
            'output_sha256': evaluation['files']['test.jsonl']['sha256'],
            'strata': {k: {'source_records': v['available'], 'quota': v['selected']}
                       for k, v in selection['strata'].items()},
        }
        report['test_selection'] = selection
        report['fraction_applies_to'] = ['train', 'calibration', 'development']
        manifest['counts']['test'] = count
        manifest['selection']['fraction_applies_to'] = ['train', 'calibration', 'development']
        manifest['selection']['test'] = selection
        manifest['files']['test.jsonl'] = evaluation['files']['test.jsonl']
        readme = f"""# Poker v1 lite

Train, calibration and development retain the original proportional 5% subsets.
Test contains {count} balanced records from poker-v1/test.jsonl: equal street quotas,
then equal final-label action quotas within each street, subject to availability.
Preflop has four action types; other streets have five. All-in keeps its original
Bet/Raise label. Position, stack depth and amount are not explicitly balanced.
State, options, labels and raw record bytes are unchanged.

| Partition | Records |
| --- | ---: |
"""
        for split in ('train', 'calibration', 'development', 'test'):
            readme += f"| {split} | {manifest['counts'][split]} |\n"
        readme += f"""
Rebuild only test in this existing suite:

```sh
uv run python -m scripts.build_poker_eval --parent evals/poker-v1 --suite evals/poker-v1-lite --count {count} --seed {seed}
```

To rebuild the entire suite at a fresh path, first run
`uv run python -m scripts.build_poker_lite --out /tmp/poker-v1-lite-rebuilt --fraction 0.05 --seed 42`,
then run the command above with `--suite /tmp/poker-v1-lite-rebuilt`.

Selection uses lowest SHA256(seed:id) within each stratum and preserves source order.
Source and output hashes are recorded. Repeated builds produce identical files.
The balanced test is not independent of the parent test and need not contain all
previous 550 rows. Earlier 550-row results refer to the old suite hash and are not
directly comparable. Test evaluation still requires --allow-test.

For training use --p_none 0 --p_none_distract 0 --p_distract 0 --p_none_pair 0.
"""
        write_json(generated / 'selection-report.json', report)
        (generated / 'README.md').write_text(readme, encoding='utf-8')
        for name in ('selection-report.json', 'README.md'):
            manifest['files'][name] = {'sha256': digest(generated / name), 'bytes': (generated / name).stat().st_size}
        write_json(generated / 'manifest.json', manifest)
        for name in ('test.jsonl', 'selection-report.json', 'README.md', 'manifest.json'):
            # Stage on the destination filesystem before publishing each file.
            staged = suite / ('.' + name + '.tmp')
            shutil.copyfile(generated / name, staged)
            os.replace(staged, suite / name)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, default=Path('evals/poker-v1'))
    parser.add_argument('--suite', type=Path, default=Path('evals/poker-v1-lite'))
    parser.add_argument('--count', type=int, default=1000)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    result = update_suite(args.parent, args.suite, args.count, args.seed)
    print(result['counts'])


if __name__ == '__main__':
    main()
