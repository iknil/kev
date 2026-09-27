from scripts import build_poker_train_v1 as builder
import json
import sqlite3
from pathlib import Path

import pytest
from kev.model import training_context
from kev.suite import digest, read_manifest, write_json


PREFLOP = """You are a specialist. The small blind is 0.5 chips and the big blind is 1 chips. Everyone started with 100 chips.
The player positions involved in this game are UTG, HJ, CO, BTN, SB, BB.
In this hand, your position is CO, and your holding is [Ace of Spade and King of Heart].
Before the flop, UTG call. Assume that all other players that is not mentioned folded.
Now it is your turn. The current pot size is 2.5 chips, and your holding is [Ace of Spade and King of Heart]."""

POSTFLOP = """You are a specialist. The small blind is 0.5 chips and the big blind is 1 chips. Everyone started with 100 chips.
The player positions involved in this game are UTG, HJ, CO, BTN, SB, BB.
In this hand, your position is CO, and your holding is [Ace of Spade and King of Heart].
Before the flop, CO raise 2 chips, and BB call. Assume that all other players that is not mentioned folded.
The flop comes Two of Spade, Three of Heart, and Four of Club, then BB check, and CO check.
The turn comes Five of Diamond, then BB check.

Now it is your turn. The current pot size is 5 chips, and your holding is [Ace of Spade and King of Heart]."""


def test_full_key_ignores_label_and_global_suit_names():
    changed_label = PREFLOP.replace("your holding", "your holding") + "\nYour optimal action is: fold"
    swapped_suits = PREFLOP.replace("Spade", "TEMP").replace("Heart", "Spade").replace("TEMP", "Heart")
    assert builder.full_state_key(PREFLOP)[0] == builder.full_state_key(changed_label)[0]
    assert builder.full_state_key(PREFLOP)[0] == builder.full_state_key(swapped_suits)[0]


def test_preflop_group_uses_hand_class_not_suit_identity():
    swapped_suits = PREFLOP.replace("Spade", "TEMP").replace("Heart", "Spade").replace("TEMP", "Heart")
    assert builder.split_group_key("preflop", PREFLOP) == builder.split_group_key("preflop", swapped_suits)


def test_global_card_canonicalization_is_one_suit_permutation():
    assert builder.canonical_cards(["AS", "KH", "2D", "3C", "4S"]) == builder.canonical_cards(["AH", "KS", "2C", "3D", "4H"])


def test_group_allocator_is_deterministic_and_exposed_group_is_train():
    groups = [(f"g{i}", 1 + i % 3, "preflop") for i in range(40)]
    first = builder.allocate_groups(groups, {"g8"})
    assert first == builder.allocate_groups(groups, {"g8"})
    assert first["g8"] == "train"
    assert set(first.values()) <= {"train", "calibration", "development"}


def test_board_order_and_full_state_vs_postflop_group_scope():
    hole_and_flop_reordered = POSTFLOP.replace(
        "Ace of Spade and King of Heart", "King of Heart and Ace of Spade"
    ).replace("Two of Spade, Three of Heart, and Four of Club", "Four of Club, Two of Spade, and Three of Heart")
    assert builder.full_state_key(POSTFLOP)[0] == builder.full_state_key(hole_and_flop_reordered)[0]
    changed_turn = POSTFLOP.replace("Five of Diamond", "Six of Diamond")
    assert builder.full_state_key(POSTFLOP)[0] != builder.full_state_key(changed_turn)[0]
    assert builder.split_group_key("postflop", POSTFLOP) == builder.split_group_key("postflop", changed_turn)


def test_reader_handles_records_crossing_small_chunks(tmp_path):
    path = tmp_path / "rows.json"
    values = [{"i": i, "text": "x" * 55} for i in range(25)]
    path.write_text(json.dumps(values), encoding="utf-8")
    assert list(builder.iter_json_array(path, chunk_size=17)) == values


def test_duplicate_representative_and_conflicts_are_resolved_as_groups(tmp_path):
    db = sqlite3.connect(tmp_path / "index.sqlite")
    builder._create_index(db)
    rows = [
        ("preflop", 3, "candidate", None, "conflict", "g1", "fold", "fold", None, "i", "i", 0, None, None),
        ("postflop", 0, "candidate", None, "conflict", "g2", "call", "call", None, "j", "j", 0, None, None),
        ("postflop", 4, "candidate", None, "duplicate", "g3", "fold", "fold", None, "k", "k", 0, None, None),
        ("preflop", 9, "candidate", None, "duplicate", "g3", "fold", "fold", None, "l", "l", 0, None, None),
    ]
    db.executemany("INSERT INTO rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    builder._resolve_duplicates_and_conflicts(db)
    assert db.execute("SELECT COUNT(*) FROM rows WHERE full_key='conflict' AND status='conflicting_label'").fetchone()[0] == 2
    assert db.execute("SELECT status,representative FROM rows WHERE stage='preflop' AND row_num=9").fetchone() == ("representative", "preflop:9")
    assert db.execute("SELECT status,representative FROM rows WHERE stage='postflop' AND row_num=4").fetchone() == ("duplicate", "preflop:9")


def _write_fixture_suite(path: Path, suite: str, partitions: dict[str, str], *, external_test_sha: str | None = None) -> None:
    path.mkdir(parents=True)
    files = {}
    for name, content in partitions.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        files[name] = {"sha256": digest(target), "records": sum(bool(line.strip()) for line in content.splitlines()), "bytes": target.stat().st_size}
    manifest = {
        "suite": suite, "files": files,
        "context": (builder.poker.SERVING_CONTEXT if suite == "poker-v1"
                    else {**training_context(), "truncate": False}),
        "trainable_sources": ["pokerbench_preflop_train"],
        "eval_only_sources": ["pokerbench_preflop_calibration", "pokerbench_preflop_development"],
        "base_revisions": {builder.TOKENIZER_ID: builder.TOKENIZER_REVISION},
        "external_test_suite": {"test_sha256": external_test_sha} if external_test_sha else {},
    }
    write_json(path / "manifest.json", manifest)
    write_json(path / "validation-report.json", {"disposition_counts": {"train": 1}})
    write_json(path / "validation.json", {"status": "checked"})
    (path / "README.md").write_text("fixture\n", encoding="utf-8")


def test_assemble_suite_checks_inputs_and_preserves_eval_test(tmp_path):
    train, evaluation, out = (tmp_path / name for name in ("train", "eval", "out"))
    test_text = '{"state":"locked test"}\n'
    sample_text = '{"state":"sample"}\n'
    _write_fixture_suite(train, "poker-train-v1", {
        "train.jsonl": '{"state":"train"}\n', "calibration.jsonl": '{"state":"cal"}\n',
        "development.jsonl": '{"state":"dev"}\n', "dispositions.jsonl": '{"row":1}\n',
        "review/sample.json": '{"state":"review"}\n',
    }, external_test_sha=digest_bytes(test_text.encode()))
    _write_fixture_suite(evaluation, "poker-v1", {
        "test.jsonl": test_text, "test/sample.jsonl": sample_text, "quarantine.jsonl": "",
    })
    # The assembler accepts only the fixed, already-reviewed source policy.
    manifest_path = train / "manifest.json"
    manifest = read_manifest(train)
    manifest["suite"] = "poker-train-v1"
    write_json(manifest_path, manifest)
    builder.assemble_suite(train, evaluation, out)
    assert (out / "test.jsonl").read_text(encoding="utf-8") == test_text
    assert (out / "test/sample.jsonl").read_text(encoding="utf-8") == sample_text
    combined = read_manifest(out)
    assert combined["files"]["test/sample.jsonl"]["sha256"] == digest(out / "test/sample.jsonl")
    assert combined["eval_only"] is False
    assert combined["context"]["max_state"] == 8192
    assert (out / "review/training/sample.json").exists()
    with pytest.raises(FileExistsError):
        builder.assemble_suite(train, evaluation, out)


def digest_bytes(value: bytes) -> str:
    import hashlib
    return hashlib.sha256(value).hexdigest()


def test_assemble_suite_rejects_different_dedup_test(tmp_path):
    train, evaluation, out = (tmp_path / name for name in ("train", "eval", "out"))
    _write_fixture_suite(train, "poker-train-v1", {
        "train.jsonl": "", "calibration.jsonl": "", "development.jsonl": "", "dispositions.jsonl": "",
    }, external_test_sha="0" * 64)
    _write_fixture_suite(evaluation, "poker-v1", {"test.jsonl": '{"state":"different"}\n', "quarantine.jsonl": ""})
    with pytest.raises(ValueError, match="different evaluation test hash"):
        builder.assemble_suite(train, evaluation, out)
    assert not (out / "manifest.json").exists()
