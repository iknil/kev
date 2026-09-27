import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from kev.suite import CONTEXT, SERVING_CONTEXT, digest, read_manifest, write_json
from scripts import build_poker_lite as lite


def _record(row: int, street: str, label: str) -> bytes:
    record = {"state": f"state {row}", "questions": {"action": {"label": label}},
              "_meta": {"id": f"row/{row}", "candidate_policy": {"street": street}, "group_id": f"group/{row}"}}
    return (json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _write_parent(path: Path, count: int = 37) -> list[bytes]:
    path.mkdir()
    rows = [_record(i, "preflop" if i % 2 else "flop", "fold" if i % 3 else "raise_to_10") for i in range(count)]
    files = {}
    for split in lite.SPLITS:
        split_rows = rows if split == "train" else rows[:13]
        raw = b"".join(split_rows)
        (path / f"{split}.jsonl").write_bytes(raw)
        files[f"{split}.jsonl"] = {"sha256": hashlib.sha256(raw).hexdigest(), "records": len(split_rows), "bytes": len(raw)}
    write_json(path / "manifest.json", {
        "suite": "poker-v1", "dataset": "RZ412/PokerBench", "dataset_revision": "rev",
        "license": "apache-2.0", "eval_only": False,
        "trainable_sources": ["pokerbench_preflop_train"], "eval_only_sources": ["pokerbench_calibration"],
        "context": SERVING_CONTEXT, "training_context": CONTEXT,
        "base_revisions": {"test/base": "sha"}, "files": files,
    })
    return rows


def test_rounding_and_largest_remainder_are_exact_and_deterministic():
    quotas, target = lite.allocate_quotas({"flop|fold": 1, "preflop|fold": 1, "turn|call": 1}, Decimal("0.5"))
    assert target == 2
    assert quotas == {"flop|fold": 1, "preflop|fold": 1, "turn|call": 0}
    assert lite.round_half_up(Decimal("2.5")) == 3
    assert lite.round_half_up(Decimal("2.4")) == 2


def test_label_action_type_and_street_form_strata():
    record = json.loads(_record(1, "river", "raise_to_16"))
    assert lite.stratum_for(record) == "river|raise"
    record["questions"]["action"]["label"] = "bet_to_100"
    assert lite.stratum_for(record) == "river|bet"


def test_sample_partition_is_reproducible_and_preserves_raw_lines(tmp_path):
    parent = tmp_path / "parent"
    rows = _write_parent(parent)
    manifest = read_manifest(parent)
    source = parent / "train.jsonl"
    original_hash = digest(source)
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    stats1 = lite.sample_partition(source, first, manifest["files"]["train.jsonl"], Decimal("0.2"), 42)
    stats2 = lite.sample_partition(source, second, manifest["files"]["train.jsonl"], Decimal("0.2"), 42)
    assert first.read_bytes() == second.read_bytes()
    output_lines = first.read_bytes().splitlines(keepends=True)
    assert all(line in rows for line in output_lines)
    assert [json.loads(line)["_meta"]["id"] for line in output_lines] == sorted(
        (json.loads(line)["_meta"]["id"] for line in output_lines), key=lambda value: int(value.split("/")[1])
    )
    assert stats1["selected_records"] == stats2["selected_records"] == lite.round_half_up(Decimal(len(rows)) * Decimal("0.2"))
    assert digest(source) == original_hash


def test_build_suite_preserves_policy_and_rejects_existing_output(tmp_path):
    parent = tmp_path / "parent"
    _write_parent(parent)
    out = tmp_path / "lite"
    manifest = lite.build_suite(parent, out, Decimal("0.25"), 42)
    assert manifest["suite"] == "poker-v1-lite"
    assert manifest["context"] == SERVING_CONTEXT
    assert manifest["training_context"] == CONTEXT
    assert manifest["parent_manifest_sha256"] == digest(parent / "manifest.json")
    assert manifest["protocol"]["test_is_non_independent_subset"] is True
    with pytest.raises(FileExistsError):
        lite.build_suite(parent, out, Decimal("0.25"), 42)
    assert len(read_manifest(out)["files"]) == 6


def test_source_hash_mismatch_refuses_to_build_partition(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_bytes(_record(0, "preflop", "fold"))
    with pytest.raises(ValueError, match="hash/count differs"):
        lite.sample_partition(source, tmp_path / "out.jsonl", {"sha256": "0" * 64, "records": 1}, Decimal("0.1"), 42)
