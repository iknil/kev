import json

import pytest

from kev.suite import digest, write_json
from scripts.build_poker_eval import STREETS, balanced_quotas, build


def test_capacity_redistribution_and_lexical_ties():
    assert balanced_quotas({'c': 8, 'b': 1, 'a': 8}, 8) == {'a': 4, 'b': 1, 'c': 3}
    with pytest.raises(ValueError):
        balanced_quotas({'a': 1}, 2)


def test_rebuild_preserves_bytes_and_rejects_source_changes(tmp_path):
    parent = tmp_path / 'parent'
    parent.mkdir()
    lines = []
    for street in STREETS:
        for action in ('fold', 'raise_to_6'):
            for i in range(4):
                lines.append((json.dumps({'state': 'untouched', 'questions': {'action': {'label': action}},
                    '_meta': {'id': f'{street}/{action}/{i}', 'candidate_policy': {'street': street}}}) + '\n').encode())
    source = parent / 'test.jsonl'
    source.write_bytes(b''.join(lines))
    write_json(parent / 'manifest.json', {'suite': 'poker-v1', 'files': {'test.jsonl': {'sha256': digest(source), 'records': 32}}})
    first, second = tmp_path / 'first', tmp_path / 'second'
    build(parent, first, 16)
    build(parent, second, 16)
    for path in first.iterdir():
        assert path.read_bytes() == (second / path.name).read_bytes()
    selected = (first / 'test.jsonl').read_bytes().splitlines(keepends=True)
    assert len(selected) == 16
    assert all(line in lines for line in selected)
    report = json.loads((first / 'selection-report.json').read_text(encoding='utf-8'))
    assert set(report['streets'].values()) == {4}
    assert {v['selected'] for v in report['strata'].values()} == {2}
    with pytest.raises(ValueError, match='new directory'):
        build(parent, first, 16)
    source.write_bytes(b''.join(lines[:-1]))
    with pytest.raises(ValueError, match='hash/count'):
        build(parent, tmp_path / 'bad', 16)
