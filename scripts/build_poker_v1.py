"""Build the instruction-faithful, eval-only PokerBench suite in evals/poker-v1.

This builder replays the published instruction text. It never joins the CSV files
by row number and never changes the source instruction stored in ``state``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path

from kev.suite import SERVING_CONTEXT, digest, write_json, write_jsonl


DATASET = "RZ412/PokerBench"
REVISION = "7ac61f961c81a50fc0f667820b2fb0e432dfec0d"
FILES = {
    "preflop": "preflop_1k_test_set_prompt_and_label.json",
    "postflop": "postflop_10k_test_set_prompt_and_label.json",
}
FILE_SHA256 = {
    FILES["preflop"]: "221027a6dace36d4ab13eb6c54431d4522f33c24cc9390b36eb31baf2eb825b4",
    FILES["postflop"]: "ba4fdbe4dbd0efa1f33b1a5c73fb4e194da3504bcb1a83e68d26392cd27f348c",
}
POSITIONS = ("UTG", "HJ", "CO", "BTN", "SB", "BB")
PREFLOP_ORDER = POSITIONS
ACTION_RE = re.compile(
    r"\b(UTG|HJ|CO|BTN|SB|BB)\s+(raise|bet|call|fold|check|all\s*[- ]?in)"
    r"(?:\s+([0-9]+(?:\.[0-9]+)?)(?:\s*chips?)?)?\b", re.IGNORECASE
)
GOLD_RE = re.compile(r"^(fold|check|call|bet|raise)(?:\s+([0-9]+(?:\.[0-9]+)?))?$", re.I)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(text: str) -> str:
    return sha_bytes(text.encode("utf-8"))


def money(value: Decimal | int | str) -> str:
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    s = format(d.normalize(), "f")
    return "0" if s in {"-0", ""} else s


def number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def js_round_positive(value: Decimal) -> Decimal:
    """JavaScript Math.round for the positive monetary values used here."""
    if value < 0:
        raise ValueError("candidate amount must be nonnegative")
    return (value + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR)


def parse_amount(text: str, where: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid amount in {where}: {text!r}") from exc
    if not value.is_finite() or value < 0:
        raise ValueError(f"invalid amount in {where}: {text!r}")
    return value


def parse_events(text: str, *, where: str) -> list[dict]:
    events = []
    cursor = 0
    for match in ACTION_RE.finditer(text):
        between = text[cursor:match.start()].strip(" ,;and\n\t")
        if between:
            raise ValueError(f"unparsed {where} text before action: {between!r}")
        player, action, amount = match.groups()
        action = re.sub(r"[ -]", "", action.casefold())
        if action in {"raise", "bet"} and amount is None:
            raise ValueError(f"{action} has no amount in {where}: {text!r}")
        if action not in {"raise", "bet"} and amount is not None:
            raise ValueError(f"unexpected amount for {action} in {where}: {text!r}")
        events.append({"player": player.upper(), "action": action,
                       "amount": parse_amount(amount, where) if amount is not None else None,
                       "source_text": match.group(0)})
        cursor = match.end()
    tail = text[cursor:].strip(" ,;and\n\t.")
    if tail:
        raise ValueError(f"unparsed {where} text after action: {tail!r}")
    return events


def extract_history(instruction: str) -> tuple[str, list[tuple[str, list[dict]]], Decimal]:
    pre = re.search(r"Before the flop,\s*(.*?)\.\s*Assume that all other players", instruction, re.S | re.I)
    pot = re.search(r"current pot size is\s+([0-9]+(?:\.[0-9]+)?)\s+chips", instruction, re.I)
    hero = re.search(r"your position is\s+(UTG|HJ|CO|BTN|SB|BB)\b", instruction, re.I)
    if not pre or not pot or not hero:
        raise ValueError("instruction is missing the expected preflop, pot, or hero-position sentence")
    pre_actions = pre.group(1).strip()
    if re.fullmatch(r"there has been no action yet", pre_actions, re.I):
        pre_events = []
    else:
        pre_events = parse_events(pre_actions, where="preflop history")
    streets = []
    pattern = re.compile(
        r"The\s+(flop|turn|river)\s+comes\s+.*?,\s*then\s+(.*?)(?=\nThe\s+(?:flop|turn|river)\s+comes|\n\nNow it is your turn|\Z)",
        re.I | re.S,
    )
    for match in pattern.finditer(instruction):
        street = match.group(1).casefold()
        raw = match.group(2).strip().rstrip(".")
        if raw.casefold() == "":
            events = []
        else:
            events = parse_events(raw, where=f"{street} history")
        streets.append((street, events))
    return hero.group(1).upper(), [("preflop", pre_events), *streets], parse_amount(pot.group(1), "pot")


def replay(hero: str, streets: list[tuple[str, list[dict]]], pot: Decimal) -> dict:
    final_street = streets[-1][0]
    participants = {hero}
    for _, events in streets:
        participants.update(event["player"] for event in events)
    future_preflop = []
    if final_street == "preflop":
        hero_index = PREFLOP_ORDER.index(hero)
        future_preflop = [p for p in PREFLOP_ORDER[hero_index + 1:] if p not in participants]
        participants.update(future_preflop)
    players = {p: {"stack": Decimal("100"), "committed": Decimal("0"), "street_bet": Decimal("0"),
                   "status": "active", "acted_at": None} for p in POSITIONS}
    players["SB"]["stack"] -= Decimal("0.5")
    players["SB"]["committed"] += Decimal("0.5")
    players["SB"]["street_bet"] += Decimal("0.5")
    players["BB"]["stack"] -= Decimal("1")
    players["BB"]["committed"] += Decimal("1")
    players["BB"]["street_bet"] += Decimal("1")
    for p in POSITIONS:
        if p not in participants:
            players[p]["status"] = "folded"
    current_bet, last_full_raise = Decimal("1"), Decimal("1")
    history_warnings = []
    for street, events in streets:
        if street != "preflop":
            for player in players.values():
                player["street_bet"] = Decimal("0")
                player["acted_at"] = None
            current_bet, last_full_raise = Decimal("0"), Decimal("1")
        for event in events:
            p, action, amount = event["player"], event["action"], event["amount"]
            player = players[p]
            if player["status"] != "active":
                raise ValueError(f"{p} acts after being {player['status']}: {event['source_text']}")
            old_bet = current_bet
            if action == "fold":
                if current_bet <= player["street_bet"]:
                    raise ValueError(f"fold while check is available: {event['source_text']}")
                player["status"] = "folded"
            elif action == "check":
                if current_bet > player["street_bet"]:
                    raise ValueError(f"check while owing chips: {event['source_text']}")
            elif action == "call":
                owed = max(Decimal("0"), current_bet - player["street_bet"])
                pay = min(owed, player["stack"])
                if pay <= 0:
                    raise ValueError(f"call with no amount owed: {event['source_text']}")
                player["stack"] -= pay
                player["street_bet"] += pay
                player["committed"] += pay
                if player["stack"] == 0:
                    player["status"] = "all-in"
            elif action == "allin":
                pay = player["stack"]
                if pay <= 0:
                    raise ValueError(f"all-in with empty stack: {event['source_text']}")
                target = player["street_bet"] + pay
                player["stack"] = Decimal("0")
                player["street_bet"] = target
                player["committed"] += pay
                player["status"] = "all-in"
                if target > current_bet:
                    increase = target - current_bet
                    current_bet = target
                    if increase >= last_full_raise:
                        last_full_raise = increase
            elif action in {"raise", "bet"}:
                # PokerBench's instruction numbers are street totals. The dataset
                # does not always match the textual pot, so pot is retained separately.
                history_action = action
                if action == "raise" and current_bet == 0:
                    history_action = "bet"
                    history_warnings.append(f"normalized_history_raise_to_bet:{street}:{p}:{money(amount)}")
                elif action == "bet" and current_bet > 0:
                    history_action = "raise"
                    history_warnings.append(f"normalized_history_bet_to_raise:{street}:{p}:{money(amount)}")
                if amount <= current_bet or amount <= player["street_bet"]:
                    raise ValueError(f"{history_action} target {money(amount)} does not exceed current wager {money(current_bet)}")
                pay = amount - player["street_bet"]
                if pay > player["stack"]:
                    raise ValueError(f"historical {history_action} exceeds remaining stack: {event['source_text']}")
                if history_action == "bet" and current_bet != 0:
                    raise ValueError(f"bet while a wager is open: {event['source_text']}")
                if history_action == "raise" and current_bet == 0:
                    raise ValueError(f"raise with no wager open: {event['source_text']}")
                increase = amount - current_bet
                required_full_raise = current_bet + last_full_raise
                all_in_target = player["street_bet"] + player["stack"]
                if history_action == "raise" and amount < required_full_raise and amount < all_in_target:
                    history_warnings.append(f"under_min_non_all_in_raise:{street}:{p}:{money(amount)}<{money(required_full_raise)}")
                player["stack"] -= pay
                player["street_bet"] = amount
                player["committed"] += pay
                current_bet = amount
                if increase >= last_full_raise:
                    last_full_raise = increase
                if player["stack"] == 0:
                    player["status"] = "all-in"
            else:
                raise ValueError(f"unknown action {action!r}")
            if player["status"] == "active":
                player["acted_at"] = current_bet
    h = players[hero]
    owed = max(Decimal("0"), current_bet - h["street_bet"])
    min_to = min(current_bet + last_full_raise, h["street_bet"] + h["stack"])
    max_to = h["street_bet"] + h["stack"]
    active_opponent = any(p != hero and row["status"] == "active" for p, row in players.items())
    acted_at = h["acted_at"]
    reopened = acted_at is None or acted_at == 0 or current_bet - acted_at >= last_full_raise
    aggression_type = "bet" if current_bet == 0 else "raise"
    aggression = active_opponent and reopened and max_to > current_bet
    if aggression:
        min_to = min(min_to, max_to)
    return {
        "hero": hero, "street": final_street, "pot": pot, "current_bet": current_bet,
        "last_full_raise": last_full_raise, "owed": owed, "stack": h["stack"],
        "street_bet": h["street_bet"], "min_to": min_to, "max_to": max_to,
        "aggression_type": aggression_type, "aggression": aggression,
        "players": players,
        "replayed_pot": sum((p["committed"] for p in players.values()), Decimal("0")),
        "active_opponent": active_opponent, "reopened": reopened,
        "history_warnings": history_warnings,
        "unacted_preflop_positions": future_preflop,
    }


def baseline_amounts(state: dict) -> list[Decimal]:
    if not state["aggression"]:
        return []
    low, high = state["min_to"], state["max_to"]
    if low > high:
        raise ValueError("min_to exceeds max_to")
    current, pot, owed = state["current_bet"], state["pot"], state["owed"]
    if state["street"] == "preflop":
        big_blind = Decimal("1")
        multiples = (Decimal("2"), Decimal("2.5"), Decimal("3")) if current == big_blind else (Decimal("2.5"), Decimal("3"), Decimal("4"))
        sizes = [m * big_blind if current == big_blind else m * current for m in multiples]
    elif current == 0:
        sizes = [f * pot for f in (Decimal(1) / 3, Decimal(2) / 3, Decimal(1), Decimal("1.5"))]
    else:
        sizes = [current + f * (pot + owed) for f in (Decimal("0.5"), Decimal("1"))]
    # Playground rounds positive formula sizes with Math.round, then clamps.
    # Real min/max values remain exact so half-chip stacks still include all-in.
    amounts = {low, high}
    for size in sizes:
        amounts.add(max(low, min(high, js_round_positive(size))))
    return sorted(amounts)


def truncated_allin_correction(action: str, amount: Decimal, state: dict) -> dict | None:
    """Recognize only the reviewed integer truncations of exact short all-ins."""
    if action not in {"bet", "raise"} or not state["aggression"]:
        return None
    if state["min_to"] != state["max_to"]:
        return None
    maximum = state["max_to"]
    difference = maximum - amount
    if amount != amount.to_integral_value():
        return None
    if amount != maximum.to_integral_value(rounding=ROUND_FLOOR):
        return None
    if difference not in {Decimal("0.5"), Decimal("0.7")}:
        return None
    return {
        "reason": "source_integer_truncated_exact_fractional_all_in",
        "from": money(amount),
        "to": money(maximum),
    }


def gold_action(output: str, state: dict) -> tuple[str, Decimal | None, str | None, dict | None]:
    match = GOLD_RE.fullmatch(output.strip())
    if not match:
        raise ValueError(f"unrecognized gold output: {output!r}")
    action, raw_amount = match.groups()
    action = action.casefold()
    amount = parse_amount(raw_amount, "gold output") if raw_amount else None
    if action in {"bet", "raise"} and amount is None:
        raise ValueError(f"gold {action} has no amount")
    if action not in {"bet", "raise"} and amount is not None:
        raise ValueError(f"unexpected amount in gold output {output!r}")
    reason = None
    correction = None
    if action in {"bet", "raise"}:
        expected = state["aggression_type"]
        if action != expected:
            reason = f"normalized_{action}_to_{expected}_for_current_bet"
            action = expected
        if not state["aggression"]:
            raise ValueError("gold aggressive action is unavailable in the replayed state")
        if amount < state["min_to"] or amount > state["max_to"]:
            correction = truncated_allin_correction(match.group(1).casefold(), amount, state)
            if correction is None:
                raise ValueError(
                    f"gold amount {money(amount)} outside legal {action} range "
                    f"[{money(state['min_to'])}, {money(state['max_to'])}]"
                )
            amount = state["max_to"]
    elif action == "call" and state["owed"] == 0:
        raise ValueError("gold call while check is available")
    elif action == "check" and state["owed"] > 0:
        raise ValueError("gold check while chips are owed")
    elif action == "fold" and state["owed"] == 0:
        reason = "fold_label_with_check_available"
    return action, amount, reason, correction


def candidates(state: dict, output: str) -> tuple[dict, dict]:
    action, gold_amount, normalization, correction = gold_action(output, state)
    criteria: dict[str, str] = {
        "fold": "Fold this hand. Pay 0 additional chips and give up any claim to the pot."
    }
    if state["owed"] == 0:
        criteria["check"] = (
            f"Check. Pay 0 additional chips. Keep {money(state['stack'])} chips in your stack and remain in the hand."
        )
    else:
        pay = min(state["owed"], state["stack"])
        remaining = state["stack"] - pay
        extra = " This is an all-in call." if pay == state["stack"] else ""
        criteria["call"] = (
            f"Call by paying {money(pay)} additional chips. Your total payment this street becomes "
            f"{money(state['street_bet'] + pay)} chips; {money(remaining)} chips remain in your stack.{extra}"
        )
    base = baseline_amounts(state)
    final = list(base)
    replacement = None
    if gold_amount is not None:
        if gold_amount not in final:
            interior = [x for x in final if x not in {state["min_to"], state["max_to"]}]
            if interior:
                replaced = min(interior, key=lambda x: (abs(x - gold_amount), x))
                final.remove(replaced)
                final.append(gold_amount)
                replacement = {"from": money(replaced), "to": money(gold_amount),
                               "reason": "nearest_same_action_amount; ties choose lower; min/max protected"}
            else:
                final.append(gold_amount)
                replacement = {"from": None, "to": money(gold_amount),
                               "reason": "no replaceable interior amount; gold added; min/max protected"}
        final = sorted(set(final))
    for to in final:
        pay = to - state["street_bet"]
        remaining = state["stack"] - pay
        if pay < 0 or remaining < 0:
            raise ValueError(f"invalid candidate chip arithmetic at total {money(to)}")
        kind = state["aggression_type"]
        key = f"{kind}_to_{money(to).replace('.', '_')}"
        allin = remaining == 0
        wording = "Bet" if kind == "bet" else "Raise"
        description = (
            f"{wording} to a total of {money(to)} chips paid this street. Pay {money(pay)} additional chips now; "
            f"{money(remaining)} chips remain in your stack."
        )
        if allin:
            description += " This puts you all-in."
        if key in criteria:
            raise ValueError(f"duplicate candidate key: {key}")
        criteria[key] = description
    label = "fold" if action == "fold" else "check" if action == "check" else "call" if action == "call" else f"{action}_to_{money(gold_amount).replace('.', '_')}"
    if label not in criteria:
        raise ValueError(f"gold label {label!r} not represented by candidates")
    return {"type": "choice", "instructions": "Choose one offered action. Wager amounts are totals paid on the current street.",
            "criteria": criteria, "label": label, "src": f"pokerbench_{state['street']}"}, {
                "baseline_amounts": [money(x) for x in base], "final_amounts": [money(x) for x in final],
                "replacement": replacement, "normalization_reason": normalization, "gold_correction": correction,
            }


def convert(
    stage: str,
    row: int,
    source: dict,
    *,
    source_name: str | None = None,
    source_file: str | None = None,
    source_file_sha256: str | None = None,
    split: str = "test",
    review_exposed: bool | None = None,
) -> tuple[dict | None, dict | None]:
    instruction, output = source.get("instruction"), source.get("output")
    if not isinstance(instruction, str) or not isinstance(output, str):
        return None, {"stage": stage, "row": row, "instruction": instruction, "output": output,
                      "reason": "instruction/output must both be strings"}
    try:
        hero, history, pot = extract_history(instruction)
        state = replay(hero, history, pot)
        question, candidate_info = candidates(state, output)
        if source_name:
            question["src"] = source_name
    except (ValueError, InvalidOperation, ArithmeticError) as exc:
        return None, {"stage": stage, "row": row, "instruction": instruction, "output": output,
                      "reason": str(exc)}
    record = {
        "state": instruction,
        "questions": {"action": question},
        "_meta": {
            "source": source_name or f"pokerbench_{stage}", "repo": DATASET, "revision": REVISION,
            "file": source_file or FILES[stage],
            "file_sha256": source_file_sha256 or FILE_SHA256[FILES[stage]], "split": split,
            "id": f"pokerbench/{stage}/{split}/{row}", "group_id": f"pokerbench/{stage}/{split}/{row}",
            "variant": "gold_amount_replacement", "row": row, "original_output": output,
            "review_exposed": (stage == "postflop" and row in {18, 20, 34}) if review_exposed is None else review_exposed,
            "instruction_sha256": sha_text(instruction),
            "candidate_policy": {"reference": "playground/src/lib/poker/candidates.ts",
                                 "street": state["street"], "pot": number(state["pot"]),
                                 "hero_stack": number(state["stack"]),
                                 "hero_street_bet": number(state["street_bet"]),
                                 "current_bet": number(state["current_bet"]),
                                 "min_to": number(state["min_to"]), "max_to": number(state["max_to"]),
                                 **candidate_info},
            "replay": {"replayed_pot": number(state["replayed_pot"]),
                       "source_pot_minus_replayed": number(state["pot"] - state["replayed_pot"]),
                       "pot_mismatch_recorded": state["pot"] != state["replayed_pot"],
                       "active_opponent": state["active_opponent"], "raise_reopened": state["reopened"],
                       "source_history_warnings": state["history_warnings"],
                       "unacted_preflop_positions": state["unacted_preflop_positions"]},
        },
    }
    return record, None


def load_source(root: Path, stage: str) -> list[dict]:
    path = root / FILES[stage]
    if digest(path) != FILE_SHA256[FILES[stage]]:
        raise ValueError(f"pinned source checksum mismatch: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"expected a JSON list in {path}")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--out", type=Path, default=Path("evals/poker-v1"))
    args = parser.parse_args()
    if (args.out / "manifest.json").exists():
        raise FileExistsError(f"refusing to overwrite frozen suite: {args.out / 'manifest.json'}")
    sources = {stage: load_source(args.source_dir, stage) for stage in FILES}
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    records, rejected, per_stage = [], [], {}
    for stage, rows in sources.items():
        accepted_before = len(records)
        rejected_before = len(rejected)
        for index, source in enumerate(rows):
            record, quarantine = convert(stage, index, source)
            (records if quarantine is None else rejected).append(record if quarantine is None else quarantine)
        per_stage[stage] = {"source": len(rows), "accepted": len(records) - accepted_before,
                            "quarantined": len(rejected) - rejected_before}
    records.sort(key=lambda r: (0 if r["_meta"]["source"] == "pokerbench_preflop" else 1, r["_meta"]["row"]))
    write_jsonl(out / "test.jsonl", records)
    for split in ("train", "calibration", "development"):
        write_jsonl(out / f"{split}.jsonl", [])
    write_jsonl(out / "quarantine.jsonl", rejected)
    if records:
        sample = next((r for r in records if r["_meta"]["source"] == "pokerbench_postflop" and r["_meta"]["row"] == 18), None)
        if sample:
            (out / "test").mkdir(parents=True, exist_ok=True)
            (out / "review").mkdir(parents=True, exist_ok=True)
            write_jsonl(out / "test/sample.jsonl", [sample])
            write_json(out / "review/sample.json", sample)
    stats = {
        "per_stage": per_stage, "accepted": len(records), "quarantined": len(rejected),
        "streets": dict(Counter(r["_meta"]["candidate_policy"]["street"] for r in records)),
        "outputs": dict(Counter(r["_meta"]["original_output"].split()[0].casefold() for r in records)),
        "normalizations": sum(bool(r["_meta"]["candidate_policy"]["normalization_reason"]) for r in records),
        "gold_replacements": sum(r["_meta"]["candidate_policy"]["replacement"] is not None for r in records),
        "pot_mismatches": sum(r["_meta"]["replay"]["pot_mismatch_recorded"] for r in records),
        "history_warning_rows": sum(bool(r["_meta"]["replay"]["source_history_warnings"]) for r in records),
        "history_warnings": dict(Counter(w.split(":", 1)[0] for r in records for w in r["_meta"]["replay"]["source_history_warnings"])),
        "gold_corrections": {
            "count": sum(bool(r["_meta"]["candidate_policy"]["gold_correction"]) for r in records),
            "difference_distribution": dict(Counter(
                money(Decimal(r["_meta"]["candidate_policy"]["gold_correction"]["to"]) -
                      Decimal(r["_meta"]["candidate_policy"]["gold_correction"]["from"]))
                for r in records if r["_meta"]["candidate_policy"]["gold_correction"])),
            "rows": [
                {"stage": r["_meta"]["source"].removeprefix("pokerbench_"), "row": r["_meta"]["row"],
                 **r["_meta"]["candidate_policy"]["gold_correction"]}
                for r in records if r["_meta"]["candidate_policy"]["gold_correction"]
            ],
        },
        "quarantine_reasons": dict(Counter(q["reason"] for q in rejected)),
    }
    write_json(out / "validation-report.json", stats)
    files = {}
    for name in ("train.jsonl", "calibration.jsonl", "development.jsonl", "test.jsonl", "quarantine.jsonl", "test/sample.jsonl"):
        path = out / name
        rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
        files[name] = {"sha256": digest(path), "records": len(rows)}
    candidate_path = Path("playground/src/lib/poker/candidates.ts")
    manifest = {
        "suite": "poker-v1", "version": 1, "dataset": DATASET, "dataset_revision": REVISION,
        "license": "apache-2.0", "eval_only": True, "trainable_sources": [], "eval_only_sources": list(dict.fromkeys(r["_meta"]["source"] for r in records)),
        "holdout_sources": [], "context": dict(SERVING_CONTEXT),
        "protocol": {
            "format": "Kev labelled request JSONL; exact upstream instruction string is the state",
            "source_files_sha256": FILE_SHA256,
        "candidate_source_sha256": digest(candidate_path),
        "builder_sha256": digest(Path(__file__)),
            "accepted_plus_quarantined_equals_source_rows": True,
            "source_pot_is_authoritative_for_candidate_size_formulas": True,
            "pot_history_differences_are_recorded_and_do_not_alone_quarantine": True,
            "candidate_rule": "Mirror candidates.ts formula amounts with JS Math.round on positive formula sizes, then clamp; preserve real Decimal minTo/maxTo exactly so fractional stack bounds include all-in.",
            "gold_rule": "Normalize bet/raise verb to current street state, require legal exact target, replace nearest interior same-action size; ties choose lower; protect min/max; add gold only if no interior amount can be replaced. With explicit authorization, correct only an integer gold equal to floor(max_to) when min_to=max_to, the exact all-in is 0.5 or 0.7 higher, and aggression is legal; retain original_output and correction metadata.",
            "fold_rule": "Always include fold, including when check is available, per requested evaluation protocol.",
            "replay_rule": "Replay posted 0.5/1 blinds and instruction actions as current-street total targets from 100-chip starting stacks. On postflop streets, unmentioned positions are folded. Preflop positions after the acting seat remain active until they have a turn; earlier unmentioned positions are folded. Never use CSV row indices.",
            "history_warnings": "Historical raises below minimum but not all-in, and verb/current-wager normalizations, are retained and listed per record; state text is never rewritten.",
            "amount_precision": "Historical and gold amounts use Decimal. Candidate formula sizes use positive JS Math.round then clamp against exact fractional legal bounds.",
            "review_exposed": True,
            "context_note": "Serving context limits only; this suite does not claim the 384-token training admission check.",
            "state_truncation": False,
        },
        "counts": stats, "files": files,
    }
    write_json(out / "manifest.json", manifest)
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
