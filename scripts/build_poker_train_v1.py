"""Build a grouped, training-only PokerBench suite from the pinned train sources."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import shutil
import re
import sqlite3
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

from kev.suite import digest, write_json, write_jsonl
try:
    from scripts import build_poker_v1 as poker
except ModuleNotFoundError:  # direct `python scripts/build_poker_train_v1.py`
    import build_poker_v1 as poker

DATASET = poker.DATASET
REVISION = poker.REVISION
FILES = {
    "preflop": "preflop_60k_train_set_prompt_and_label.json",
    "postflop": "postflop_500k_train_set_prompt_and_label.json",
}
SOURCE_SHA256 = {
    FILES["preflop"]: "dded3b40abf43a2db17f5c4ea721a7fffe272eb59b39f6b018fa101ffe391195",
    FILES["postflop"]: "89661dc905cbfd7f4610b8a2428b5744aa05ba80886f2c410e3c096930898123",
}
TOKENIZER_ID = "Qwen/Qwen3.5-0.8B-Base"
TOKENIZER_REVISION = "dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68"
SPLITS = ("train", "calibration", "development")
POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
CARD_RE = re.compile(r"\b(Ace|King|Queen|Jack|Ten|Nine|Eight|Seven|Six|Five|Four|Three|Two|[2-9]) of (Spade|Heart|Club|Diamond)s?\b", re.I)


def canonical_cards(cards: list[str]) -> tuple[str, ...]:
    """Canonicalize a whole visible card set under one global suit permutation."""
    normalized = []
    for card in cards:
        match = re.fullmatch(r"([2-9TJQKA])([SHDC])", card.upper())
        if not match:
            raise ValueError(f"invalid compact card: {card}")
        normalized.append(match.groups())
    suits = "CDHS"
    forms = []
    for permutation in itertools.permutations(suits):
        suit_map = dict(zip(suits, permutation))
        forms.append(tuple(rank + suit_map[suit] for rank, suit in normalized))
    return min(forms)


def card_tokens(instruction: str) -> list[str]:
    rank = {"ace": "A", "king": "K", "queen": "Q", "jack": "J", "ten": "T", "nine": "9",
            "eight": "8", "seven": "7", "six": "6", "five": "5", "four": "4", "three": "3", "two": "2"}
    suit = {"spade": "S", "heart": "H", "club": "C", "diamond": "D"}
    return [rank.get(r.casefold(), r) + suit[s.casefold()]
            for r, s in CARD_RE.findall(instruction)]


def stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _history_and_cards(instruction: str) -> tuple[str, list[tuple[str, list[dict]]], Decimal, list[str]]:
    if not re.search(r"small blind is 0\.5 chips and the big blind is 1(?:\.0)? chips", instruction, re.I):
        raise ValueError("unsupported blind template; expected 0.5/1 chips")
    if not re.search(r"Everyone started with 100(?:\.0)? chips", instruction, re.I):
        raise ValueError("unsupported stack template; expected 100 chips")
    positions = re.search(r"player positions involved in this game are\s+([^\n.]+)", instruction, re.I)
    if not positions or tuple(p.strip().upper() for p in positions.group(1).split(",")) != POSITIONS:
        raise ValueError("unsupported seat template; expected UTG,HJ,CO,BTN,SB,BB")
    hero, history, pot = poker.extract_history(instruction)
    holding = re.search(r"your holding is\s*\[(.*?)\]", instruction, re.I | re.S)
    if not holding:
        raise ValueError("missing hero holding")
    hole = card_tokens(holding.group(1))
    board = []
    board_streets = []
    for match in re.finditer(r"The\s+(flop|turn|river)\s+comes\s+(.*?),\s*then", instruction, re.I | re.S):
        street = match.group(1).casefold()
        dealt = card_tokens(match.group(2))
        expected = {"flop": 3, "turn": 1, "river": 1}[street]
        if len(dealt) != expected:
            raise ValueError(f"{street} must reveal {expected} cards; found {len(dealt)}")
        board_streets.append(street)
        board.extend(dealt)
    if board_streets not in ([], ["flop"], ["flop", "turn"], ["flop", "turn", "river"]):
        raise ValueError("community-card streets are missing or out of order")
    history_streets = [street for street, _ in history if street != "preflop"]
    if history_streets != board_streets:
        raise ValueError("action history streets do not match the revealed board")
    if len(hole) != 2:
        raise ValueError(f"hero holding must contain 2 cards; found {len(hole)}")
    cards = hole + board
    if len(set(cards)) != len(cards):
        raise ValueError("duplicate visible card")
    return hero, history, pot, cards


def full_state_key(instruction: str) -> tuple[str, str]:
    """Gold-blind complete visible-state key; label and candidate data are excluded."""
    hero, history, pot, cards = _history_and_cards(instruction)
    hole = cards[:2]
    board = cards[2:]
    # Keep action order and street boundaries; normalize bet/raise using the wager at action time.
    current = Decimal("1")
    actions = []
    for street, events in history:
        if street != "preflop":
            current = Decimal(0)
        street_actions = []
        for event in events:
            action, amount = event["action"], event["amount"]
            if action == "raise" and current == 0:
                action = "bet"
            elif action == "bet" and current > 0:
                action = "raise"
            if action in {"raise", "bet"}:
                current = amount
            street_actions.append((event["player"], action, poker.money(amount) if amount is not None else None))
        actions.append((street, street_actions))
    payload = {
        "hero": hero, "hole": hole, "board": board, "actions": actions,
        "pot": poker.money(pot), "starting_stack": "100", "blinds": ["0.5", "1"],
        "positions": list(POSITIONS),
    }
    # Apply each global suit mapping to hole and board together, and preserve hole/flop order rules.
    canonical_forms = []
    combined = hole + board
    suits = "CDHS"
    for permutation in itertools.permutations(suits):
        suit_map = dict(zip(suits, permutation))
        mapped = [rank + suit_map[suit] for rank, suit in map(lambda c: (c[0], c[1]), combined)]
        h = tuple(sorted(mapped[:2]))
        b = mapped[2:]
        flop = tuple(sorted(b[:3])) if len(b) >= 3 else tuple(b)
        board_key = flop + tuple(b[3:])
        canonical_forms.append((h, board_key))
    h, board_key = min(canonical_forms)
    payload["hole"] = h
    payload["board"] = board_key
    return stable_hash(payload), json.dumps(payload, sort_keys=True, separators=(",", ":"))


def split_group_key(stage: str, instruction: str) -> str:
    hero, history, _pot, cards = _history_and_cards(instruction)
    hole = cards[:2]
    # Canonical card encoding uses one global suit permutation for hole and flop.
    if stage == "preflop":
        ranks = sorted(c[0] for c in hole)
        suited = hole[0][1] == hole[1][1]
        hand = (tuple(ranks), "pair" if ranks[0] == ranks[1] else "suited" if suited else "offsuit")
        return stable_hash(("preflop", hero, hand))
    flop = cards[2:5]
    preflop = next((events for street, events in history if street == "preflop"), [])
    forms = []
    suits = "CDHS"
    for permutation in itertools.permutations(suits):
        suit_map = dict(zip(suits, permutation))
        mapped_hole = sorted(c[0] + suit_map[c[1]] for c in hole)
        mapped_flop = sorted(c[0] + suit_map[c[1]] for c in flop)
        forms.append((tuple(mapped_hole), tuple(mapped_flop)))
    h, f = min(forms)
    pre = [(e["player"], e["action"], poker.money(e["amount"]) if e["amount"] is not None else None)
           for e in preflop]
    return stable_hash(("postflop", hero, h, f, pre))


def allocate_groups(groups: list[tuple[str, int, str]], exposed: set[str] | None = None) -> dict[str, str]:
    """Deterministic largest-first 90/5/5 allocation with exposed groups train-bound."""
    exposed = exposed or set()
    assigned = {}
    for stage in sorted({stage for _, _, stage in groups}):
        rows = [row for row in groups if row[2] == stage]
        total = sum(size for _, size, _ in rows)
        targets = {"train": total * .90, "calibration": total * .05, "development": total * .05}
        counts = Counter()
        ordered = sorted(rows, key=lambda item: (-item[1], stable_hash(("poker-train-v1", stage, item[0]))))
        for group_id, size, _ in ordered:
            if group_id in exposed:
                split = "train"
            else:
                split = max(SPLITS, key=lambda name: ((targets[name] - counts[name]) / max(targets[name], 1), -SPLITS.index(name)))
            assigned[group_id] = split
            counts[split] += size
    return assigned


def iter_json_array(path: Path, chunk_size: int = 1024 * 1024):
    """Yield objects from a JSON array without retaining the complete source file."""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as stream:
        buffer = ""
        pos = 0
        started = False
        eof = False
        while True:
            if not eof and (pos >= len(buffer) - 1):
                buffer = buffer[pos:]
                pos = 0
                chunk = stream.read(chunk_size)
                eof = not chunk
                buffer += chunk
            while pos < len(buffer) and buffer[pos].isspace():
                pos += 1
            if not started:
                if pos >= len(buffer):
                    if eof:
                        raise ValueError("empty JSON source")
                    continue
                if buffer[pos] != "[":
                    raise ValueError("train source must be a JSON array")
                pos += 1
                started = True
                continue
            while pos < len(buffer) and buffer[pos] in ", \r\n\t":
                pos += 1
            if pos < len(buffer) and buffer[pos] == "]":
                return
            if pos >= len(buffer):
                if eof:
                    raise ValueError("truncated JSON array")
                continue
            try:
                value, end = decoder.raw_decode(buffer, pos)
            except json.JSONDecodeError:
                if eof:
                    raise
                buffer = buffer[pos:]
                pos = 0
                chunk = stream.read(chunk_size)
                eof = not chunk
                buffer += chunk
                continue
            yield value
            pos = end


def _create_index(db: sqlite3.Connection) -> None:
    db.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        CREATE TABLE test_keys (key TEXT PRIMARY KEY, raw TEXT NOT NULL);
        CREATE TABLE test_raw (raw TEXT PRIMARY KEY);
        CREATE TABLE rows (
            stage TEXT NOT NULL, row_num INTEGER NOT NULL, status TEXT NOT NULL,
            reason TEXT, full_key TEXT, group_key TEXT, label TEXT, original_output TEXT,
            correction TEXT, instruction_sha TEXT, raw_instruction_sha TEXT,
            exposed INTEGER NOT NULL DEFAULT 0, representative TEXT, split TEXT,
            PRIMARY KEY(stage, row_num)
        );
        CREATE INDEX rows_key_idx ON rows(full_key, stage, row_num);
        CREATE INDEX rows_group_idx ON rows(stage, group_key);
    """)


def _test_keys(db: sqlite3.Connection, test_path: Path) -> int:
    count = 0
    with test_path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            record = json.loads(line)
            key, _ = full_state_key(record["state"])
            raw = poker.sha_text(record["state"])
            db.execute("INSERT OR IGNORE INTO test_keys VALUES (?, ?)", (key, raw))
            db.execute("INSERT OR IGNORE INTO test_raw VALUES (?)", (raw,))
            count += 1
    db.commit()
    return count


def _index_sources(db: sqlite3.Connection, source_dir: Path) -> tuple[dict, Counter]:
    source_counts, reasons = {}, Counter()
    for stage, filename in FILES.items():
        path = source_dir / filename
        actual_sha = digest(path)
        if actual_sha != SOURCE_SHA256[filename]:
            raise ValueError(f"pinned source checksum mismatch for {path}: {actual_sha}")
        count = 0
        batch = []
        for count, source in enumerate(iter_json_array(path), start=1):
            instruction, output = source.get("instruction"), source.get("output")
            if not isinstance(instruction, str) or not isinstance(output, str):
                batch.append((stage, count - 1, "invalid", "instruction/output must both be strings", None, None,
                              None, str(output), None, None, None, 0, None, None))
                reasons["instruction/output must both be strings"] += 1
            else:
                try:
                    _hero, history, _pot, cards = _history_and_cards(instruction)
                    has_board = len(cards) > 2
                    if stage == "preflop" and has_board or stage == "postflop" and not has_board:
                        raise ValueError(f"{stage} source row has the wrong community-card template")
                    full_key, _ = full_state_key(instruction)
                    group_key = split_group_key(stage, instruction)
                    raw_sha = poker.sha_text(instruction)
                    overlap = db.execute("SELECT raw FROM test_keys WHERE key=?", (full_key,)).fetchone()
                    if overlap:
                        status = "test_overlap"
                        exact = db.execute("SELECT 1 FROM test_raw WHERE raw=?", (raw_sha,)).fetchone()
                        reason = "exact_instruction_test_overlap" if exact else "suitomorphic_test_overlap"
                        label, correction = None, None
                    else:
                        record, rejected = poker.convert(stage, count - 1, source)
                        if rejected is not None:
                            raise ValueError(rejected["reason"])
                        status, reason = "candidate", None
                        label = record["questions"]["action"]["label"]
                        correction = record["_meta"]["candidate_policy"].get("gold_correction")
                    batch.append((stage, count - 1, status, reason, full_key, group_key, label, output,
                                  json.dumps(correction, sort_keys=True) if correction else None,
                                  poker.sha_text(instruction), raw_sha, int(stage == "preflop" and count - 1 == 2), None, None))
                    if overlap:
                        reasons["test_overlap"] += 1
                except (ValueError, ArithmeticError) as exc:
                    why = str(exc)
                    batch.append((stage, count - 1, "invalid", why, None, None, None, output,
                                  None, poker.sha_text(instruction), poker.sha_text(instruction), 0, None, None))
                    reasons[why] += 1
            if len(batch) >= 2000:
                db.executemany("INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
                db.commit()
                batch.clear()
            if count % 25000 == 0:
                print(f"indexed {stage}: {count} source rows", flush=True)
        if batch:
            db.executemany("INSERT INTO rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", batch)
            db.commit()
        source_counts[stage] = {"file": filename, "rows": count, "sha256": actual_sha, "bytes": path.stat().st_size}
    return source_counts, reasons


def _resolve_duplicates_and_conflicts(db: sqlite3.Connection) -> Counter:
    outcomes = Counter()
    updates = []
    current_key, items = None, []

    def resolve(group):
        if not group:
            return
        labels = {item[3] for item in group}
        if len(labels) > 1:
            for _key, stage, row_num, _label in group:
                updates.append(("conflicting_label", "same full visible state has different gold labels", None, stage, row_num))
                outcomes["conflicting_label"] += 1
            return
        rep_stage, rep_row = group[0][1:3]
        representative = f"{rep_stage}:{rep_row}"
        updates.append(("representative", None, representative, rep_stage, rep_row))
        outcomes["representative"] += 1
        for _key, stage, row_num, _label in group[1:]:
            updates.append(("duplicate", "same state and gold as deterministic representative", representative, stage, row_num))
            outcomes["duplicate"] += 1

    query = "SELECT full_key,stage,row_num,label FROM rows WHERE status='candidate' ORDER BY full_key,CASE stage WHEN 'preflop' THEN 0 ELSE 1 END,row_num"
    for item in db.execute(query):
        if current_key is not None and item[0] != current_key:
            resolve(items)
            items = []
        current_key = item[0]
        items.append(item)
        if len(updates) >= 2000:
            db.executemany("UPDATE rows SET status=?,reason=?,representative=? WHERE stage=? AND row_num=?", updates)
            db.commit()
            updates.clear()
    resolve(items)
    if updates:
        db.executemany("UPDATE rows SET status=?,reason=?,representative=? WHERE stage=? AND row_num=?", updates)
    db.commit()
    return outcomes


def _admit_representatives(db: sqlite3.Connection, source_dir: Path) -> tuple[Counter, dict]:
    from kev.data import materialize
    from kev.model import encode, load_tokenizer, training_context

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    tokenizer_path = Path.home() / ".cache/huggingface/hub/models--Qwen--Qwen3.5-0.8B-Base/snapshots" / TOKENIZER_REVISION
    if not tokenizer_path.exists():
        raise FileNotFoundError(f"required pinned local tokenizer cache not found: {tokenizer_path}")
    tokenizer = load_tokenizer(str(tokenizer_path), revision=None)
    context = {**training_context(), "truncate": False}
    counts = Counter()
    max_state_tokens = max_branch_tokens = max_packed_tokens = 0
    for stage, filename in FILES.items():
        path = source_dir / filename
        for row_num, source in enumerate(iter_json_array(path)):
            row = db.execute("SELECT status FROM rows WHERE stage=? AND row_num=?", (stage, row_num)).fetchone()
            if not row or row[0] != "representative":
                continue
            record, rejected = poker.convert(stage, row_num, source)
            if rejected:
                raise RuntimeError(f"source changed after index at {stage}:{row_num}")
            internal = materialize(record)
            try:
                packed = encode(tokenizer, internal, max_state=context["max_state"],
                                max_branch=context["max_branch"], strict=True)
                state_tokens = sum(1 for seg in packed["seg"] if seg == 0)
                branch_tokens = len(packed["ids"]) - state_tokens
                packed_tokens = len(packed["ids"])
                fits_context = packed_tokens <= context["max_packed"]
            except ValueError:
                state_tokens = branch_tokens = packed_tokens = -1
                fits_context = False
            if not fits_context:
                db.execute("UPDATE rows SET status='context_limit', reason='does not fit pinned Qwen3.5-0.8B training_context without truncation' WHERE full_key=? AND status IN ('representative','duplicate')", (db.execute("SELECT full_key FROM rows WHERE stage=? AND row_num=?", (stage, row_num)).fetchone()[0],))
                counts["context_limit"] += 1
                continue
            db.execute("UPDATE rows SET status='eligible' WHERE stage=? AND row_num=?", (stage, row_num))
            counts["eligible"] += 1
            max_state_tokens = max(max_state_tokens, state_tokens)
            max_branch_tokens = max(max_branch_tokens, branch_tokens)
            max_packed_tokens = max(max_packed_tokens, packed_tokens)
            if counts["eligible"] % 5000 == 0:
                print(f"admitted representatives: {counts['eligible']}", flush=True)
        db.commit()
    return counts, {"max_state_tokens": max_state_tokens, "max_branch_tokens": max_branch_tokens,
                    "max_packed_tokens": max_packed_tokens, "tokenizer": TOKENIZER_ID,
                    "tokenizer_revision": TOKENIZER_REVISION, "context": context}


def _assign_partitions(db: sqlite3.Connection) -> dict:
    assignments = {}
    group_stages = {}
    exposed_groups = set()
    for stage in FILES:
        exposed = db.execute("SELECT group_key FROM rows WHERE stage=? AND exposed=1 LIMIT 1", (stage,)).fetchone()
        groups = db.execute("SELECT group_key,COUNT(*) FROM rows WHERE stage=? AND status='eligible' GROUP BY group_key", (stage,)).fetchall()
        group_ids = {group for group, _ in groups}
        group_stages.update({group: stage for group, _ in groups})
        if exposed and exposed[0] in group_ids:
            exposed_groups.add(exposed[0])
        assignments.update(allocate_groups([(group, size, stage) for group, size in groups], exposed_groups))
    for group_key, split in assignments.items():
        stage = group_stages[group_key]
        db.execute("UPDATE rows SET status=?, split=? WHERE stage=? AND group_key=? AND status='eligible'",
                   (split, split, stage, group_key))
    db.execute("""UPDATE rows AS d SET split=(
        SELECT r.split FROM rows AS r
        WHERE r.stage=substr(d.representative,1,instr(d.representative,':')-1)
          AND r.row_num=CAST(substr(d.representative,instr(d.representative,':')+1) AS INTEGER)
    ) WHERE d.status='duplicate' AND d.representative IS NOT NULL""")
    db.commit()
    return {"assignments": assignments, "review_exposed_group_overrides": sorted(exposed_groups)}


def _export_partitions(db: sqlite3.Connection, source_dir: Path, stage_dir: Path) -> tuple[Counter, Counter, list]:
    from kev.data import SystemOneRequest, api_request, materialize
    from kev.suite import validate_training

    counts, action_counts, street_counts, corrections = Counter(), Counter(), Counter(), []
    for split in (*SPLITS, "test"):
        (stage_dir / f"{split}.jsonl").write_text("", encoding="utf-8")
    streams = {split: (stage_dir / f"{split}.jsonl").open("w", encoding="utf-8", newline="\n") for split in SPLITS}
    trainable = ["pokerbench_preflop_train", "pokerbench_postflop_train"]
    try:
        for stage, filename in FILES.items():
            for row_num, source in enumerate(iter_json_array(source_dir / filename)):
                indexed = db.execute("SELECT status,split,full_key,group_key FROM rows WHERE stage=? AND row_num=?", (stage, row_num)).fetchone()
                if not indexed or indexed[0] not in SPLITS:
                    continue
                split, full_key, group_key = indexed[1], indexed[2], indexed[3]
                source_name = f"pokerbench_{stage}_{split}"
                record, rejected = poker.convert(
                    stage, row_num, source, source_name=source_name, source_file=filename,
                    source_file_sha256=SOURCE_SHA256[filename], split=split,
                    review_exposed=bool(db.execute("SELECT 1 FROM rows WHERE stage=? AND group_key=? AND exposed=1", (stage, group_key)).fetchone()),
                )
                if rejected:
                    raise RuntimeError(f"source changed after indexing at {stage}:{row_num}: {rejected['reason']}")
                meta = record["_meta"]
                meta.update({
                    "source": source_name, "split": split, "original_source_split": "train",
                    "id": f"pokerbench/{stage}/{split}/{row_num}", "row": row_num, "source_row": row_num,
                    "source_file_sha256": SOURCE_SHA256[filename], "state_sha256": poker.sha_text(record["state"]),
                    "full_state_key_sha256": full_key, "group_id": group_key,
                })
                record["questions"]["action"]["src"] = source_name
                SystemOneRequest.model_validate(api_request(record))
                materialize(record)
                if split == "train":
                    validate_training([record], {"trainable_sources": trainable})
                streams[split].write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                counts[split] += 1
                action_counts[record["questions"]["action"]["label"].split("_to_", 1)[0]] += 1
                street_counts[meta["candidate_policy"]["street"]] += 1
                correction = meta["candidate_policy"].get("gold_correction")
                if correction:
                    corrections.append({"stage": stage, "row": row_num, **correction})
    finally:
        for stream in streams.values():
            stream.close()
    summary = Counter({**{f"action:{k}": v for k, v in action_counts.items()},
                       **{f"street:{k}": v for k, v in street_counts.items()}})
    return counts, summary, corrections


def _write_dispositions(db: sqlite3.Connection, path: Path) -> Counter:
    counts = Counter()
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        query = "SELECT stage,row_num,status,reason,split,representative,group_key FROM rows ORDER BY CASE stage WHEN 'preflop' THEN 0 ELSE 1 END,row_num"
        for stage, row_num, status, reason, split, representative, group_key in db.execute(query):
            record = {"source": f"pokerbench_{stage}_train", "source_file": FILES[stage], "row": row_num,
                      "status": status, "reason": reason, "split": split, "group_id": group_key,
                      "duplicate_of": representative}
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            counts[status] += 1
    return counts


def _existing_test_provenance(test_path: Path) -> dict:
    from kev.suite import read_manifest
    manifest_path = test_path.parent / "manifest.json"
    manifest = read_manifest(test_path.parent)
    expected = manifest["files"][test_path.name]["sha256"]
    if digest(test_path) != expected:
        raise ValueError("existing evals/poker-v1 test hash differs from its manifest")
    if manifest.get("evaluation_origin"):
        return dict(manifest["evaluation_origin"])
    return {"suite": "evals/poker-v1", "manifest_sha256": digest(manifest_path),
            "test_sha256": expected, "rows": manifest["files"][test_path.name]["records"]}


def assemble_suite(training_suite: Path, evaluation_suite: Path, out: Path, *, allow_existing: bool = False) -> dict:
    """Combine an already-built train suite with a locked evaluation suite into one Kev suite."""
    from kev.suite import read_json, write_json

    training_suite = training_suite.resolve()
    evaluation_suite = evaluation_suite.resolve()
    out = out.resolve()
    if out == evaluation_suite:
        raise ValueError("output must be fresh and separate from --eval-suite")
    if (out / "manifest.json").exists() and not allow_existing:
        raise FileExistsError(f"refusing to overwrite frozen suite: {out / 'manifest.json'}")
    train_manifest_path = training_suite / "manifest.json"
    eval_manifest_path = evaluation_suite / "manifest.json"
    train_manifest, eval_manifest = read_json(train_manifest_path), read_json(eval_manifest_path)
    if train_manifest.get("suite") != "poker-train-v1":
        raise ValueError("--training-suite must contain a poker-train-v1 source manifest")
    if not (evaluation_suite / "test.jsonl").exists():
        raise FileNotFoundError(f"evaluation test split missing: {evaluation_suite / 'test.jsonl'}")

    def verify_partition(source_dir: Path, manifest: dict, filename: str) -> tuple[str, int]:
        expected = manifest.get("files", {}).get(filename)
        path = source_dir / filename
        if expected is None or not path.is_file():
            raise ValueError(f"{source_dir}: manifest or file missing {filename}")
        actual_hash = digest(path)
        actual_records = sum(1 for line in path.open(encoding="utf-8") if line.strip())
        if actual_hash != expected.get("sha256") or actual_records != expected.get("records"):
            raise ValueError(f"{source_dir}: {filename} differs from manifest hash/count")
        return actual_hash, actual_records

    for split in ("train", "calibration", "development"):
        verify_partition(training_suite, train_manifest, f"{split}.jsonl")
    test_hash, _test_records = verify_partition(evaluation_suite, eval_manifest, "test.jsonl")
    if "test/sample.jsonl" in eval_manifest.get("files", {}):
        verify_partition(evaluation_suite, eval_manifest, "test/sample.jsonl")
    pinned_test = train_manifest.get("external_test_suite", {}).get("test_sha256")
    if not pinned_test or pinned_test != test_hash:
        raise ValueError("training source manifest was de-duplicated against a different evaluation test hash")

    # Preserve the first eval-only manifest when the input is already a merged suite.
    if (evaluation_suite / "audit/evaluation/manifest.json").exists():
        eval_history = evaluation_suite / "audit/evaluation"
        evaluation_origin = eval_manifest.get("evaluation_origin", {})
    else:
        eval_history = evaluation_suite
        evaluation_origin = {
            "suite": eval_manifest.get("suite", "poker-v1"),
            "manifest_path": "audit/evaluation/manifest.json",
            "manifest_sha256": digest(eval_manifest_path),
            "test_sha256": digest(evaluation_suite / "test.jsonl"),
            "test_records": eval_manifest["files"]["test.jsonl"]["records"],
        }

    out.mkdir(parents=True, exist_ok=True)
    (out / "audit/evaluation").mkdir(parents=True, exist_ok=True)
    (out / "audit/training").mkdir(parents=True, exist_ok=True)
    (out / "review/training").mkdir(parents=True, exist_ok=True)
    for split in ("train", "calibration", "development"):
        shutil.copyfile(training_suite / f"{split}.jsonl", out / f"{split}.jsonl")
        (out / split).mkdir(exist_ok=True)
        (out / split / ".gitkeep").touch()
    shutil.copyfile(evaluation_suite / "test.jsonl", out / "test.jsonl")
    (out / "test").mkdir(exist_ok=True)
    if (evaluation_suite / "test/sample.jsonl").exists():
        shutil.copyfile(evaluation_suite / "test/sample.jsonl", out / "test/sample.jsonl")
    shutil.copyfile(training_suite / "dispositions.jsonl", out / "dispositions.jsonl")
    if (evaluation_suite / "quarantine.jsonl").exists():
        shutil.copyfile(evaluation_suite / "quarantine.jsonl", out / "quarantine.jsonl")
    else:
        (out / "quarantine.jsonl").write_text("", encoding="utf-8")

    # Keep original review rows at their current locations and put exposed training review under review/training.
    if (evaluation_suite / "review").exists():
        for item in (evaluation_suite / "review").iterdir():
            if item.name == "training":
                continue
            target = out / "review" / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copyfile(item, target)
    training_review = training_suite / "review"
    if not training_review.exists():
        training_review = evaluation_suite / "review/training"
    if training_review.exists():
        shutil.copytree(training_review, out / "review/training", dirs_exist_ok=True)

    eval_names = ("manifest.json", "README.md", "validation-report.json", "validation.json")
    if eval_history == evaluation_suite:
        for name in eval_names:
            if (eval_history / name).exists():
                target_name = "manifest.json" if name == "manifest.json" else name
                shutil.copyfile(eval_history / name, out / "audit/evaluation" / target_name)
    else:
        for name in eval_names:
            if (eval_history / name).exists():
                shutil.copyfile(eval_history / name, out / "audit/evaluation" / name)
    for source, destination in (("README.md", "README.md"), ("manifest.json", "source-manifest.json"),
                                ("validation-report.json", "validation-report.json"), ("validation.json", "validation.json")):
        if (training_suite / source).exists():
            shutil.copyfile(training_suite / source, out / "audit/training" / destination)

    training_report = read_json(training_suite / "validation-report.json")
    training_validation = read_json(training_suite / "validation.json") if (training_suite / "validation.json").exists() else {}
    if eval_history == evaluation_suite:
        evaluation_report = read_json(evaluation_suite / "validation-report.json") if (evaluation_suite / "validation-report.json").exists() else {}
        evaluation_validation = read_json(evaluation_suite / "validation.json") if (evaluation_suite / "validation.json").exists() else {}
    else:
        evaluation_report = read_json(eval_history / "validation-report.json") if (eval_history / "validation-report.json").exists() else {}
        evaluation_validation = read_json(eval_history / "validation.json") if (eval_history / "validation.json").exists() else {}

    counts = {}
    file_info = {}
    file_names = ["train.jsonl", "calibration.jsonl", "development.jsonl", "test.jsonl", "test/sample.jsonl", "dispositions.jsonl", "quarantine.jsonl"]
    file_names += [f"audit/evaluation/{name}" for name in eval_names if (out / "audit/evaluation" / name).exists()]
    file_names += [f"audit/training/{name}" for name in ("source-manifest.json", "README.md", "validation-report.json", "validation.json") if (out / "audit/training" / name).exists()]
    for filename in file_names:
        path = out / filename
        info = {"sha256": digest(path), "bytes": path.stat().st_size}
        if filename.endswith(".jsonl"):
            info["records"] = sum(1 for line in path.open(encoding="utf-8") if line.strip())
        file_info[filename] = info
    for split in ("train", "calibration", "development", "test"):
        counts[split] = file_info[f"{split}.jsonl"]["records"]

    train_context = train_manifest["context"]
    eval_context = eval_manifest.get("context", poker.SERVING_CONTEXT)
    combined_report = {
        "suite": "poker-v1", "partitions": counts,
        "training": training_report, "evaluation": evaluation_report,
        "source_dispositions": training_report.get("disposition_counts", {}),
        "model_evaluation_run": False, "training_run": False,
    }
    write_json(out / "validation-report.json", combined_report)
    combined_validation = {
        "status": "merged", "evaluation_history": evaluation_validation,
        "training_migration": training_validation,
        "combined_partition_counts": counts,
        "evaluation_test_sha256": digest(out / "test.jsonl"),
        "training_partition_hashes": {split: file_info[f"{split}.jsonl"]["sha256"] for split in ("train", "calibration", "development")},
        "external_suite_self_reference": False, "model_evaluation_run": False, "training_run": False,
    }
    write_json(out / "validation.json", combined_validation)

    readme = f"""# PokerBench v1

This suite combines Kev's existing locked PokerBench test with grouped training data from `RZ412/PokerBench` at revision `{REVISION}`. The instruction remains the state verbatim. Training labels use the reviewed action replay and candidate policy; bet/raise options include the gold amount, so this measures choice among a gold-inclusive candidate set rather than live candidate generation without answer access.

| Partition | Records | Role |
| --- | ---: | --- |
| train | {counts['train']:,} | Trainable |
| calibration | {counts['calibration']:,} | Eval-only |
| development | {counts['development']:,} | Eval-only |
| test | {counts['test']:,} | Locked evaluation |

Training admission was checked only with Qwen3.5-0.8B-Base at `{train_manifest['base_revisions'][TOKENIZER_ID]}` and strict `training_context` limits (384 state, 1,024 branch, 2,048 packed). Other bases require an explicit pinned tokenizer revision and a new admission pass. The root manifest retains the original serving context for the locked test and lists the training context separately. `eval_only=false` exposes the train partition to training tools, so benchmark overlong-record filtering follows the unified manifest policy.

Run the locked evaluation explicitly with:

```sh
uv run python -m kev.benchmark --run runs/<run> --suite evals/poker-v1 --out runs/poker-v1 --allow-test
```

The source files must be named `preflop_60k_train_set_prompt_and_label.json` and `postflop_500k_train_set_prompt_and_label.json` in one directory. Their pinned hashes, candidate/eval helper hashes, and row dispositions are recorded in the root manifest and `dispositions.jsonl`.

```sh
uv run python scripts/build_poker_train_v1.py \\
  --source-dir /path/to/pinned-pokerbench-train-files \\
  --eval-suite evals/poker-v1 \\
  --out /tmp/poker-v1-rebuilt
```

To assemble an already-built `poker-train-v1` directory with an evaluation suite, add `--assemble-existing --training-suite /path/to/poker-train-v1`. Builders refuse to overwrite an output directory with a manifest. Historical evaluation and training manifests, reports, and README files are under `audit/evaluation/` and `audit/training/`; the exposed training example is under `review/training/`.

Future training must set `--p_none 0 --p_none_distract 0 --p_distract 0 --p_none_pair 0` to preserve the reviewed candidate set. These flags are not changed in Kev's CLI defaults. This migration ran no model evaluation or training.
"""
    (out / "README.md").write_text(readme, encoding="utf-8")

    all_info = dict(file_info)
    for name in ("README.md", "validation-report.json", "validation.json"):
        path = out / name
        all_info[name] = {"sha256": digest(path), "bytes": path.stat().st_size}
    eval_source_names = list(train_manifest.get("eval_only_sources", []))
    root_manifest = {
        "suite": "poker-v1", "version": 1, "dataset": DATASET, "dataset_revision": REVISION,
        "license": "apache-2.0", "eval_only": False,
        "trainable_sources": train_manifest["trainable_sources"],
        "eval_only_sources": eval_source_names, "holdout_sources": [],
        "context": eval_context, "training_context": train_context,
        "partition_context": {"train": train_context, "calibration": train_context,
                              "development": train_context, "test": eval_context},
        "base_revisions": train_manifest.get("base_revisions", {}),
        "evaluation_origin": evaluation_origin,
        "source_files": train_manifest.get("source_files", {}),
        "source_rows": train_manifest.get("source_rows", {}),
        "split_seed": "poker-train-v1",
        "assembly_builder_sha256": digest(Path(__file__).resolve()),
        "protocol": {"merged_suite": True, "external_test_suite_self_reference": False,
                     "training_context_checked": train_context, "test_context_preserved_from_evaluation_manifest": True,
                     "model_evaluation_run": False, "training_run": False,
                     "training_manifest_path": "audit/training/source-manifest.json",
                     "evaluation_manifest_path": "audit/evaluation/manifest.json"},
        "counts": {"partitions": counts, "training": training_report, "evaluation": evaluation_report},
        "files": all_info,
    }
    write_json(out / "manifest.json", root_manifest)
    return root_manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--eval-suite", type=Path, default=Path("evals/poker-v1"))
    parser.add_argument("--training-suite", type=Path, default=Path("evals/poker-train-v1"))
    parser.add_argument("--assemble-existing", action="store_true", help="assemble an existing training suite without rebuilding source data")
    parser.add_argument("--out", type=Path, default=Path("evals/poker-v1"))
    args = parser.parse_args()
    out = args.out.resolve()
    eval_suite = args.eval_suite.resolve()
    if args.assemble_existing:
        assemble_suite(args.training_suite, eval_suite, out)
        print(f"assembled unified PokerBench suite at {out}", flush=True)
        return
    if (out / "manifest.json").exists():
        raise FileExistsError(f"refusing to overwrite frozen suite: {out / 'manifest.json'}")
    if out == eval_suite:
        raise ValueError("--out must be fresh and separate from --eval-suite")
    out.mkdir(parents=True, exist_ok=True)
    eval_test = eval_suite / "test.jsonl"
    external_test = _existing_test_provenance(eval_test)
    with tempfile.TemporaryDirectory(prefix="poker-train-v1-") as temporary:
        work = Path(temporary)
        stage_dir = work / "stage"
        stage_dir.mkdir()
        db = sqlite3.connect(work / "index.sqlite")
        _create_index(db)
        test_rows = _test_keys(db, eval_test)
        source_counts, parse_reasons = _index_sources(db, args.source_dir)
        print(f"source index complete: {source_counts['preflop']['rows']} preflop + {source_counts['postflop']['rows']} postflop; {test_rows} locked-test rows", flush=True)
        dup_stats = _resolve_duplicates_and_conflicts(db)
        print(f"duplicate/conflict resolution complete: {dict(dup_stats)}", flush=True)
        context_stats, token_stats = _admit_representatives(db, args.source_dir)
        print(f"strict context admission complete: {dict(context_stats)}", flush=True)
        split_stats = _assign_partitions(db)
        print(f"group allocation complete: {len(split_stats['assignments'])} groups", flush=True)
        partition_counts, action_street_counts, corrections = _export_partitions(db, args.source_dir, stage_dir)
        print(f"partition export complete: {dict(partition_counts)}", flush=True)
        disposition_counts = _write_dispositions(db, stage_dir / "dispositions.jsonl")

        actual_source_rows = {stage: db.execute("SELECT COUNT(*) FROM rows WHERE stage=?", (stage,)).fetchone()[0] for stage in FILES}
        final_partition_rows = {split: partition_counts[split] for split in SPLITS}
        if sum(actual_source_rows.values()) != sum(disposition_counts.values()):
            raise AssertionError("source disposition ledger does not conserve all source rows")
        if sum(final_partition_rows.values()) != sum(disposition_counts[s] for s in SPLITS):
            raise AssertionError("partition records differ from final train/calibration/development dispositions")

        status_counts = dict(db.execute("SELECT status,COUNT(*) FROM rows GROUP BY status"))
        overlap_counts = dict(db.execute("SELECT reason,COUNT(*) FROM rows WHERE status='test_overlap' GROUP BY reason"))
        group_stats = {}
        for stage in FILES:
            group_stats[stage] = {split: db.execute(
                "SELECT COUNT(DISTINCT group_key),COUNT(*) FROM rows WHERE stage=? AND status=?", (stage, split)
            ).fetchone() for split in SPLITS}
        report = {
            "source_rows": actual_source_rows, "source_total": sum(actual_source_rows.values()),
            "source_files": source_counts, "disposition_counts": dict(disposition_counts),
            "final_status_counts": status_counts, "test_overlap": overlap_counts,
            "duplicate_conflict": dict(dup_stats), "context_admission": {**dict(context_stats), **token_stats},
            "split_allocation": {"algorithm": "largest-first relative deficit, fixed hash tie break; stratified by source stage",
                                 "target_ratio": {"train": 0.90, "calibration": 0.05, "development": 0.05},
                                 "review_exposed_group_overrides": split_stats["review_exposed_group_overrides"],
                                 "groups": {stage: {split: {"groups": vals[0], "records": vals[1]}
                                                    for split, vals in group_stats[stage].items()}
                                            for stage in FILES}},
            "partition_counts": final_partition_rows, "action_and_street_counts": dict(action_street_counts),
            "gold_corrections": {"count": len(corrections), "difference_distribution": dict(Counter(
                poker.money(Decimal(c["to"]) - Decimal(c["from"])) for c in corrections)), "rows": corrections},
            "parse_and_gold_reasons": dict(parse_reasons), "external_test": external_test,
            "suite_loading_validation": "partition hashes/counts and request schema/materialize checked by streaming every exported record; Kev load_split checked on the empty test partition to avoid loading large training data into RAM",
            "model_evaluation_run": False, "training_run": False,
        }
        write_json(stage_dir / "validation-report.json", report)
        README = """# PokerBench train source data

This suite adapts the pinned `RZ412/PokerBench` training files at revision `7ac61f961c81a50fc0f667820b2fb0e432dfec0d` into Kev choice records. The source instruction is retained byte-for-byte as `state`. Bet/raise candidates include the gold amount, so this data measures choice among a gold-inclusive candidate set and does not establish how a live candidate generator performs without answer access.

| Partition | Records | Role |
| --- | ---: | --- |
| train | 466,704 | Trainable |
| calibration | 25,929 | Eval-only |
| development | 25,928 | Eval-only |
| test | 0 | Empty; external locked test is `evals/poker-v1` |

The 0.8B Qwen3.5 tokenizer at revision `dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68` was used for strict, no-truncation admission under `kev.model.training_context()` (384 state, 1,024 branch, 2,048 packed tokens). This is the only tokenizer/base admission checked; any other base or tokenizer needs its own pinned revision and a new admission pass. The preflop and postflop source SHA256 values, candidate helper hash, eval helper hash, partition hashes and counts are in `manifest.json`.

To build the unified suite, place both pinned source objects in one directory with their original filenames:

```sh
mkdir -p /tmp/pokerbench-train-sources
# Put preflop_60k_train_set_prompt_and_label.json and postflop_500k_train_set_prompt_and_label.json here.
uv run python scripts/build_poker_train_v1.py \\
  --source-dir /tmp/pokerbench-train-sources \\
  --eval-suite evals/poker-v1 \\
  --out /tmp/poker-v1-rebuilt
```

To assemble existing training partitions without reading the large source files again, use `--assemble-existing --training-suite /path/to/poker-train-v1 --eval-suite evals/poker-v1 --out /tmp/poker-v1-rebuilt`.

The builder verifies both pinned source hashes and refuses to overwrite an output directory that already has a manifest. It also requires the pinned tokenizer to be available locally. `dispositions.jsonl` records one outcome for each of the 563,200 source rows. `validation-report.json` includes exclusions and correction details.

The `review/` directory contains one previously exposed example; it is part of the train group assignment and is not an additional partition record. Calibration and development use role-specific eval-only sources. States overlapping the locked test set, duplicate states, conflicting labels, unsupported templates, invalid golds, or context overflow are handled by the recorded disposition policy.

Any future training run must set `--p_none 0 --p_none_distract 0 --p_distract 0 --p_none_pair 0` to preserve the reviewed candidate set; this manifest does not change Kev's CLI defaults. No model evaluation or training was run to build this suite.
"""
        (stage_dir / "README.md").write_text(README, encoding="utf-8")

        file_info = {}
        for filename in ("train.jsonl", "calibration.jsonl", "development.jsonl", "test.jsonl", "dispositions.jsonl", "validation-report.json", "README.md"):
            path = stage_dir / filename
            count = sum(1 for line in path.open(encoding="utf-8") if line.strip()) if filename.endswith(".jsonl") else None
            file_info[filename] = {"sha256": digest(path), "bytes": path.stat().st_size}
            if count is not None:
                file_info[filename]["records"] = count
        manifest = {
            "suite": "poker-train-v1", "version": 1, "dataset": DATASET, "dataset_revision": REVISION,
            "license": "apache-2.0", "eval_only": False,
            "trainable_sources": ["pokerbench_preflop_train", "pokerbench_postflop_train"],
            "eval_only_sources": [f"pokerbench_{stage}_{split}" for stage in FILES for split in ("calibration", "development")]
                                + ["pokerbench_preflop", "pokerbench_postflop"],
            "holdout_sources": [], "context": token_stats["context"],
            "base_revisions": {TOKENIZER_ID: TOKENIZER_REVISION},
            "source_files": source_counts, "source_rows": actual_source_rows,
            "external_test_suite": external_test,
            "protocol": {
                "train_context_tokenizer": TOKENIZER_ID, "train_context_tokenizer_revision": TOKENIZER_REVISION,
                "candidate_source_sha256": digest(Path("playground/src/lib/poker/candidates.ts")),
                "eval_builder_sha256": digest(Path("scripts/build_poker_v1.py")),
                "train_builder_sha256": digest(Path(__file__)),
                "source_split": "train", "partition_ratios": {"train": .90, "calibration": .05, "development": .05},
                "split_algorithm": "stage-stratified largest-first relative target deficit; ties train/calibration/development; stable hash order",
                "group_keys": {"preflop": "hero position plus unordered ranks and pair/suited/offsuit class",
                               "postflop": "hero position, global-suit-canonical unordered hole/flop, ordered preflop actions; turn/river and later actions omitted"},
                "dedup_key": "complete visible state, gold blind, exact amounts, global suit permutation; locked test overlap excluded",
                "state_preserved": True, "state_truncation": False, "p_none": False,
                "p_none_distract": False, "p_distract": False, "p_none_pair": False,
                "review_exposed_is_partition_data": False, "model_evaluation_run": False, "training_run": False,
                "future_training_augmentation_flags": ["--p_none 0", "--p_none_distract 0", "--p_distract 0", "--p_none_pair 0"],
            },
            "counts": report, "files": file_info,
        }
        write_json(stage_dir / "manifest.json", manifest)

        # Validate counts, hashes, and source policy without materializing a huge training partition in RAM.
        from kev.suite import load_split, validate_training
        empty_test = load_split(stage_dir, "test", allow_test=True)
        if empty_test:
            raise AssertionError("training suite test partition must remain empty")
        for split in SPLITS:
            path = stage_dir / f"{split}.jsonl"
            if digest(path) != file_info[path.name]["sha256"]:
                raise AssertionError(f"partition checksum mismatch for {split}")
            line_count = sum(1 for line in path.open(encoding="utf-8") if line.strip())
            if line_count != file_info[path.name]["records"]:
                raise AssertionError(f"partition row count mismatch for {split}")
            sample = next((json.loads(line) for line in path.open(encoding="utf-8") if line.strip()), None)
            if sample and split == "train":
                validate_training([sample], manifest)
            if sample and split in {"calibration", "development"}:
                try:
                    validate_training([sample], manifest)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"{split} unexpectedly passed training-source validation")

        db.close()
        combined = assemble_suite(stage_dir, eval_suite, out)
        print(json.dumps({"source_rows": actual_source_rows, "partitions": combined["counts"]["partitions"],
                          "dispositions": dict(disposition_counts), "report": str(out / "validation-report.json")},
                         indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
