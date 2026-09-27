"""Build a readable PokerBench training/evaluation set and a decision-v7 mixed train file.

Usage:
    uv run python scripts/prepare_pokerbench.py

Only PokerBench's structured CSV files are fetched. The published prompt/label JSON files
are much larger and are not needed because the CSV contains legal moves and solver labels.
"""
import argparse
import ast
import hashlib
import heapq
import json
import re
from pathlib import Path

from kev.suite import ADMISSION_TOKENIZER, digest, load_split, read_json, write_json, write_jsonl
from pokerbench_state import transform_record


DATASET = "RZ412/PokerBench"
REVISION = "7ac61f961c81a50fc0f667820b2fb0e432dfec0d"
TRAIN_FILES = {
    "preflop": "preflop_60k_train_set_game_scenario_information.csv",
    "postflop": "postflop_500k_train_set_game_scenario_information.csv",
}
TEST_FILES = {
    "preflop": "preflop_1k_test_set_game_scenario_information.csv",
    "postflop": "postflop_10k_test_set_game_scenario_information.csv",
}
PROMPT_FILE_SHA256 = {
    "preflop_1k_test_set_prompt_and_label.json": "221027a6dace36d4ab13eb6c54431d4522f33c24cc9390b36eb31baf2eb825b4",
    "postflop_10k_test_set_prompt_and_label.json": "ba4fdbe4dbd0efa1f33b1a5c73fb4e194da3504bcb1a83e68d26392cd27f348c",
}


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def action_key(action):
    key = re.sub(r"[^a-z0-9]+", "_", action.casefold()).strip("_")
    if not key:
        raise ValueError(f"empty action key for {action!r}")
    return key


def actions(raw):
    values = ast.literal_eval(raw)
    if not isinstance(values, list) or not values or not all(isinstance(x, str) for x in values):
        raise ValueError(f"invalid available_moves: {raw!r}")
    return values


def record(stage, row, row_number, source_split):
    if stage == "preflop":
        state = {
            "game": "6-handed No Limit Texas Hold'em",
            "street": "preflop",
            "hero_position": row["hero_pos"],
            "hero_holding": row["hero_holding"],
            "action_history": row["prev_line"],
            "players_remaining": int(row["num_players"]),
            "actions_before_decision": int(row["num_bets"]),
            "pot_size": row["pot_size"],
        }
        answer = row["correct_decision"].strip()
    else:
        street = row["evaluation_at"].strip().casefold()
        if street not in {"flop", "turn", "river"}:
            raise ValueError(f"unknown postflop street: {row['evaluation_at']!r}")
        state = {
            "game": "6-handed No Limit Texas Hold'em",
            "street": street,
            "hero_position": row["hero_position"],
            "hero_holding": row["holding"],
            "board": {"flop": row["board_flop"]},
            "preflop_action": row["preflop_action"],
            "postflop_action_history": row["postflop_action"],
            "last_aggressor_position": row["aggressor_position"],
            "pot_size": row["pot_size"],
        }
        # Never include cards that had not been dealt at the decision point.
        if street in {"turn", "river"} and row.get("board_turn"):
            state["board"]["turn"] = row["board_turn"]
        if street == "river" and row.get("board_river"):
            state["board"]["river"] = row["board_river"]
        answer = row["correct_decision"].strip()

    moves = actions(row["available_moves"])
    keys = [action_key(move) for move in moves]
    if len(set(keys)) != len(keys):
        raise ValueError(f"action-key collision in {moves!r}")
    answer_key = next((key for move, key in zip(moves, keys) if move.casefold() == answer.casefold()), None)
    if answer_key is None:
        raise ValueError(f"answer {answer!r} is not in available_moves {moves!r}")
    state_json = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    state_hash = sha(state_json)
    identifier = f"pokerbench/{stage}/{source_split}/{row_number}"
    result = {
        "state": state,
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "Choose the solver-labeled optimal legal action for this game state.",
                "criteria": {key: move for key, move in zip(keys, moves)},
                "label": answer_key,
                "src": f"pokerbench_{stage}",
            }
        },
        "_meta": {
            "source": f"pokerbench_{stage}",
            "repo": DATASET,
            "revision": REVISION,
            "split": source_split,
            "id": identifier,
            "group_id": state_hash,
            "variant": "clean",
            "row": row_number,
            "text_sha256": state_hash,
            "row_sha256": sha(json.dumps({"state": state, "moves": moves, "label": answer_key}, sort_keys=True, ensure_ascii=False)),
        },
    }
    return transform_record(result)


def read_csv_records(path, stage, source_split):
    import csv
    records = []
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row_number, row in enumerate(csv.DictReader(stream)):
            records.append(record(stage, row, row_number, source_split))
    return records


def select_train_csv(path, stage, test_hashes, train_count, dev_count, calibration_count):
    """Stream a large source CSV while retaining only the deterministic sample we need."""
    import csv
    limits = {"train": train_count, "calibration": calibration_count, "development": dev_count}
    heaps = {split: [] for split in limits}
    seen = set()
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        for row_number, row in enumerate(csv.DictReader(stream)):
            item = record(stage, row, row_number, "train")
            state_hash = item["_meta"]["group_id"]
            if state_hash in test_hashes or state_hash in seen:
                continue
            seen.add(state_hash)
            bucket = int(state_hash[:8], 16) % 100
            split = "train" if bucket < 80 else "calibration" if bucket < 90 else "development"
            score = int(sha(f"select:{REVISION}:{stage}:{state_hash}"), 16)
            heap = heaps[split]
            if len(heap) < limits[split]:
                heapq.heappush(heap, (-score, item))
            elif score < -heap[0][0]:
                heapq.heapreplace(heap, (-score, item))
    selected = {}
    for split, heap in heaps.items():
        if len(heap) < limits[split]:
            raise ValueError(f"{stage} {split} has only {len(heap)} eligible rows; needs {limits[split]}")
        selected[split] = [item for _, item in sorted(heap, key=lambda pair: -pair[0])]
    return selected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="evals/poker")
    ap.add_argument("--decision-v7", default="evals/v7/decision-v7")
    ap.add_argument("--train-per-stage", type=int, default=6000)
    ap.add_argument("--development-per-stage", type=int, default=750)
    ap.add_argument("--calibration-per-stage", type=int, default=500)
    ap.add_argument("--overwrite", action="store_true", help="replace an existing Poker suite in place")
    ap.add_argument("--refresh-existing", action="store_true", help="reformat an existing suite from its stored PokerBench source fields")
    args = ap.parse_args()
    out = Path(args.out)
    if args.refresh_existing:
        if not args.overwrite:
            ap.error("--refresh-existing requires --overwrite")
        refresh_existing(out)
        return
    if (out / "manifest.json").exists() and not args.overwrite:
        raise FileExistsError(f"refusing to overwrite existing poker suite without --overwrite: {out}")
    from huggingface_hub import hf_hub_download

    out.mkdir(parents=True, exist_ok=True)
    poker = {split: [] for split in ("train", "calibration", "development", "test")}
    source_hashes = {}

    paths = {}
    for stage, filename in {**TRAIN_FILES, **{f"test_{k}": v for k, v in TEST_FILES.items()}}.items():
        paths[stage] = hf_hub_download(DATASET, filename, repo_type="dataset", revision=REVISION)
        source_hashes[filename] = digest(paths[stage])

    test_hashes = set()
    for stage in ("preflop", "postflop"):
        rows = read_csv_records(paths[f"test_{stage}"], stage, "test")
        poker["test"].extend(rows)
        test_hashes.update(r["_meta"]["group_id"] for r in rows)

    for stage in ("preflop", "postflop"):
        parts = select_train_csv(
            paths[stage], stage, test_hashes, args.train_per_stage,
            args.development_per_stage, args.calibration_per_stage,
        )
        for split, items in parts.items():
            poker[split].extend(items)

    # Keep ordering stable but mix streets within each partition.
    for split, records in poker.items():
        records.sort(key=lambda item: item["_meta"]["source_row_sha256"])
        write_jsonl(out / f"{split}.jsonl", records)

    v7_train = load_split(args.decision_v7, "train")
    mixed = v7_train + poker["train"]
    write_jsonl(out / "mixed_train.jsonl", mixed)
    parent_manifest = read_json(Path(args.decision_v7) / "manifest.json")
    files = {}
    for name in ("train.jsonl", "calibration.jsonl", "development.jsonl", "test.jsonl", "mixed_train.jsonl"):
        rows = [json.loads(line) for line in (out / name).read_text(encoding="utf-8").splitlines() if line]
        files[name] = {"sha256": digest(out / name), "records": len(rows), "questions": sum(len(r["questions"]) for r in rows)}
    manifest = {
        "version": 2,
        "dataset": DATASET,
        "dataset_revision": REVISION,
        "license": "apache-2.0",
        "base_revisions": {
            **parent_manifest.get("base_revisions", {}),
            "Qwen/Qwen3.5-0.8B-Base": "dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68",
            ADMISSION_TOKENIZER[0]: ADMISSION_TOKENIZER[1],
            "Qwen/Qwen3.5-9B-Base": "68c46c4b3498877f3ef123c856ecfde50c39f404",
        },
        "dataset_revisions": {DATASET: REVISION},
        "trainable_sources": ["pokerbench_preflop", "pokerbench_postflop"],
        "eval_only_sources": [],
        "holdout_sources": [],
        "context": parent_manifest["context"],
        "protocol": {
            "format": "Kev labelled request JSONL",
            "source_files_sha256": source_hashes,
            "source_prompt_files_sha256": PROMPT_FILE_SHA256,
            "train_selection": f"deterministic hash split; {args.train_per_stage} preflop and {args.train_per_stage} postflop rows selected by stable hash rank",
            "selected_per_stage": {"train": args.train_per_stage, "calibration": args.calibration_per_stage, "development": args.development_per_stage},
            "split_buckets": {"train": "hash % 100 in [0, 79]", "calibration": "[80, 89]", "development": "[90, 99]"},
            "test": "official PokerBench test rows, legal options, and solver labels are preserved; only state wording and independently derived reading questions are added",
            "mixed_train": "full decision-v7 train partition followed by poker train partition",
            "future_board_cards_excluded": True,
            "state_format": "plain-English source facts; action amounts retain source values without assuming total-versus-additional semantics",
            "reading_tasks": ["decision street", "hero position", "whether the turn card has been dealt", "current-street last bet or raise", "amount convention availability", "whether opponent cards are provided"],
            "source_consistency": "each PokerBench row retains and hashes its exact original structured state and action options/label; candidate keys, order, and answer key are unchanged; reading labels are derived from source fields or explicit omissions",
            "source_field_caveats": "num_players is reported as a source count, not asserted as the current active-player count; last_aggressor_position is retained as a source summary but rendered current-street aggressor is derived from ordered BET/RAISE events",
            "verified_global_facts": "six-handed table, SB 0.5 chip, BB 1 chip, and 100-chip starting stacks are stated in official prompt-and-label files pinned by source_prompt_files_sha256",
            "standard_coverage": "source-faithful but incomplete; stable seats, per-player stacks/contributions, call cost, made-hand category, and pot eligibility are unavailable",
            "hand_strength_and_side_pots": "TODO; source rows do not establish made-hand category or pot eligibility",
        },
        "parent_suite": {
            "directory": args.decision_v7,
            "train_manifest_sha256": parent_manifest["files"]["train.jsonl"]["sha256"],
        },
        "files": files,
    }
    write_json(out / "manifest.json", manifest)
    print(json.dumps({"output": str(out), "records": {k: len(v) for k, v in poker.items()}, "mixed_train": len(mixed)}, indent=2))


def refresh_existing(out):
    """Replace the current suite with the plain-English state and verified reading labels."""
    manifest_path = out / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no existing poker suite at {out}")
    manifest = read_json(manifest_path)
    names = ("train.jsonl", "calibration.jsonl", "development.jsonl", "test.jsonl", "mixed_train.jsonl")
    for name in names:
        path = out / name
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        updated = [transform_record(row) if row.get("_meta", {}).get("source", "").startswith("pokerbench_") else row for row in records]
        write_jsonl(path, updated)
        manifest.setdefault("files", {})[name] = {
            "sha256": digest(path),
            "records": len(updated),
            "questions": sum(len(row.get("questions", {})) for row in updated),
        }
    manifest["version"] = 2
    manifest.setdefault("protocol", {})["source_prompt_files_sha256"] = PROMPT_FILE_SHA256
    manifest.setdefault("protocol", {}).update({
        "test": "official PokerBench test rows, legal options, and solver labels are preserved; only state wording and independently derived reading questions are added",
        "state_format": "plain-English source facts; action amounts retain source values without assuming total-versus-additional semantics",
        "reading_tasks": ["decision street", "hero position", "whether the turn card has been dealt", "current-street last bet or raise", "amount convention availability", "whether opponent cards are provided"],
        "source_consistency": "each PokerBench row retains and hashes its exact original structured state and action options/label; candidate keys, order, and answer key are unchanged; reading labels are derived from source fields or explicit omissions",
        "source_field_caveats": "num_players is reported as a source count, not asserted as the current active-player count; last_aggressor_position is retained as a source summary but rendered current-street aggressor is derived from ordered BET/RAISE events",
        "verified_global_facts": "six-handed table, SB 0.5 chip, BB 1 chip, and 100-chip starting stacks are stated in official prompt-and-label files pinned by source_prompt_files_sha256",
        "standard_coverage": "source-faithful but incomplete; stable seats, per-player stacks/contributions, call cost, made-hand category, and pot eligibility are unavailable",
        "hand_strength_and_side_pots": "TODO; source rows do not establish made-hand category or pot eligibility",
    })
    write_json(manifest_path, manifest)
    print(json.dumps({"output": str(out), "files": manifest["files"]}, indent=2))


if __name__ == "__main__":
    main()
