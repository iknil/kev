"""Render PokerBench source facts in plain English and add objective reading questions."""
import hashlib
import json
import re


POSITIONS = {
    "UTG": "under the gun (UTG)", "HJ": "hijack (HJ)", "CO": "cutoff (CO)",
    "BTN": "button (BTN)", "SB": "small blind (SB)", "BB": "big blind (BB)",
    "IP": "in position (IP)", "OOP": "out of position (OOP)",
}
RANKS = dict(zip("23456789TJQKA", ["Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Jack", "Queen", "King", "Ace"]))
SUITS = {"s": "spades", "h": "hearts", "d": "diamonds", "c": "clubs"}
STREET_DESCRIPTIONS = {
    "preflop": "Before any community cards are dealt",
    "flop": "The first three community cards are on the table",
    "turn": "The fourth community card is on the table",
    "river": "The fifth and final community card is on the table",
}
def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def card_name(card):
    match = re.fullmatch(r"([2-9TJQKA])([shdc])", card.strip())
    if not match:
        raise ValueError(f"invalid PokerBench card: {card!r}")
    rank, suit = match.groups()
    return f"{RANKS[rank]} of {SUITS[suit]}"


def card_list(cards):
    cards = cards.strip().replace(" ", "")
    if len(cards) % 2:
        raise ValueError(f"invalid compact PokerBench cards: {cards!r}")
    return [card_name(cards[i:i + 2]) for i in range(0, len(cards), 2)]


def position_name(position):
    try:
        return POSITIONS[position.strip().upper()]
    except KeyError as exc:
        raise ValueError(f"unknown PokerBench position: {position!r}") from exc


def preflop_actions(line):
    tokens = [part.strip() for part in line.split("/") if part.strip()]
    if len(tokens) % 2:
        raise ValueError(f"malformed preflop action line: {line!r}")
    result = []
    for position, action in zip(tokens[::2], tokens[1::2]):
        position_name(position)
        folded = action.casefold()
        amount = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*bb", action, re.IGNORECASE)
        if amount:
            result.append(f'{position.strip().upper()} raise {amount.group(1)} BB')
        elif folded == "call":
            result.append(f"{position.strip().upper()} call")
        elif folded == "fold":
            result.append(f"{position.strip().upper()} fold")
        elif folded in {"allin", "all-in"}:
            result.append(f"{position.strip().upper()} all-in")
        else:
            raise ValueError(f"unknown preflop action {action!r} in {line!r}")
    return result


def postflop_actions(line, board):
    tokens = [part.strip() for part in line.split("/") if part.strip()]
    result, dealt, street_index = [], [], 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.casefold() == "dealcards":
            if i + 1 >= len(tokens):
                raise ValueError(f"dealcards missing card in {line!r}")
            dealt.append(tokens[i + 1])
            street_index += 1
            i += 2
            continue
        match = re.fullmatch(r"(OOP|IP)_(CHECK|CALL|BET|RAISE)(?:_([0-9]+(?:\.[0-9]+)?))?", token, re.IGNORECASE)
        if not match:
            raise ValueError(f"unknown postflop action {token!r} in {line!r}")
        role, action, amount = match.groups()
        action = action.casefold()
        if action in {"bet", "raise"}:
            if amount is None:
                raise ValueError(f"{action} missing amount in {line!r}")
            verb = {"bet": "bet", "raise": "raised"}[action]
            result.append(("flop" if street_index == 0 else "turn" if street_index == 1 else "river",
                           role.upper(), action, amount,
                           f'{role.upper()} {verb} with recorded amount "{amount}"'))
        else:
            if amount is not None:
                raise ValueError(f"unexpected amount for {action} in {line!r}")
            verb = {"check": "check", "call": "call"}[action]
            result.append(("flop" if street_index == 0 else "turn" if street_index == 1 else "river",
                           role.upper(), action, None, f"{role.upper()} {verb}"))
        i += 1

    expected = []
    for street in ("turn", "river"):
        compact = board.get(street)
        if compact:
            cards = card_list(compact)
            if len(cards) != 1:
                raise ValueError(f"{street} must be one card, got {compact!r}")
            expected.append(compact.strip())
    if dealt != expected:
        raise ValueError(f"action history deals {dealt!r}, board fields give {expected!r}: {line!r}")
    return result


def source_state(record):
    meta = record.get("_meta", {})
    if "source_state" in meta:
        return meta["source_state"]
    state = record["state"]
    if not isinstance(state, dict):
        raise ValueError(f"expected original structured PokerBench state, got {type(state).__name__}")
    return state


def render_preflop(source):
    position = source.get("hero_pos", source.get("hero_position"))
    cards = card_list(source.get("hero_holding", source.get("holding", "")))
    line = source.get("action_history", source.get("prev_line", ""))
    history = preflop_actions(line)
    count = source.get("players_remaining", source.get("num_players"))
    prior_count = source.get("actions_before_decision", source.get("num_bets"))
    pot = source.get("pot_size")
    if len(cards) != 2 or count is None or prior_count is None or pot is None:
        raise ValueError(f"missing preflop source facts: {source!r}")
    actions = "; ".join(history) or "none"
    used_positions = {token.strip().upper() for token in line.split("/")[::2] if token.strip()}
    used_positions.add(position.strip().upper())
    legend = ", ".join(f"{code}={POSITIONS[code]}" for code in sorted(used_positions))
    return (
        "PokerBench no-limit Texas Hold'em at a six-seat table. Blinds: small blind (SB) 0.5 chip, big blind (BB) 1 chip (1 BB = 1 chip). Starting stack: 100 chips each.\n"
        f"Decision: preflop, before any community cards are dealt. It is your turn. Your position: {position_name(position)}. Your cards: {cards[0]}, {cards[1]}.\n"
        f"Player count reported by source: {count}; current active-player count is not independently verified. Prior action count reported by source: {prior_count}.\n"
        f"Pot size reported by source: {pot} chips. Opponent cards are not provided. Prior actions in order: {actions}.\n"
        "Current stacks, contributions, and exact call payment are not provided. "
        "Numeric preflop sizes are source raise sizes in BB; the source does not define total versus additional. "
        f"Position codes: {legend}."
    ), position


def render_postflop(source, postflop):
    street = source["street"].casefold()
    if street not in {"flop", "turn", "river"}:
        raise ValueError(f"unknown postflop street: {street!r}")
    position = source["hero_position"].strip().upper()
    cards = card_list(source["hero_holding"])
    board = source["board"]
    if len(cards) != 2:
        raise ValueError(f"expected two private cards: {cards!r}")
    flop = card_list(board["flop"])
    if len(flop) != 3:
        raise ValueError(f"expected three flop cards: {board.get('flop')!r}")
    if (street in {"turn", "river"}) != ("turn" in board):
        raise ValueError(f"turn card does not match decision street {street!r}: {board!r}")
    if (street == "river") != ("river" in board):
        raise ValueError(f"river card does not match decision street {street!r}: {board!r}")
    preflop = preflop_actions(source["preflop_action"])
    board_text = f"Flop: {', '.join(flop)}; turn: {card_list(board['turn'])[0] if 'turn' in board else 'not dealt'}; river: {card_list(board['river'])[0] if 'river' in board else 'not dealt'}."
    postflop_by_street = {name: [] for name in ("flop", "turn", "river")}
    for action_street, _, _, _, text in postflop:
        postflop_by_street[action_street].append(text)
    postflop_text = "; ".join(
        f"{street}: {', '.join(postflop_by_street[street])}"
        for street in ("flop", "turn", "river") if postflop_by_street[street]
    ) or "none"
    current_street_actions = [item for item in postflop if item[0] == street]
    no_current_action_note = f" No actions are recorded on the {street} yet." if not current_street_actions else ""
    preflop_text = "; ".join(preflop) or "none"
    preflop_codes = {token.strip().upper() for token in source["preflop_action"].split("/")[::2] if token.strip()}
    preflop_codes.add(position)
    legend = ", ".join(f"{code}={POSITIONS[code]}" for code in sorted(preflop_codes))
    aggressors = [role for action_street, role, action, _, _ in postflop
                  if action_street == street and action in {"bet", "raise"}]
    last_aggressor = aggressors[-1] if aggressors else None
    return (
        "PokerBench no-limit Texas Hold'em at a six-seat table. Blinds: small blind (SB) 0.5 chip, big blind (BB) 1 chip (1 BB = 1 chip). Starting stack: 100 chips each.\n"
        f"Decision: {street}. It is your turn. Your position: {position_name(position)}. Your cards: {cards[0]}, {cards[1]}.\n"
        f"Board: {board_text}\nPot size reported by source: {source['pot_size']} chips. Opponent cards are not provided.\n"
        "Current stacks, contributions, and exact call payment are not provided.\n"
        f"Preflop action entries in order: {preflop_text}.\n"
        f"Postflop actions in order: {postflop_text}.{no_current_action_note}\n"
        f"Most recent bet or raise on this street: {last_aggressor or 'none recorded'}.\n"
        f"OOP means out of position and acts before IP after the flop; IP means in position. "
        f"Postflop sizes are chips; total-versus-additional is unspecified. Position codes: {legend}."
    ), position


def comprehension_questions(street, position):
    street_label = street.casefold()
    position_label = position.strip().upper()
    position_options = dict(POSITIONS)
    return {
        "reading_street": {
            "type": "choice",
            "instructions": "Which street is the decision on?",
            "criteria": {key: value for key, value in STREET_DESCRIPTIONS.items()},
            "label": street_label,
            "src": "pokerbench_state_reading",
        },
        "reading_position": {
            "type": "choice",
            "instructions": "What position or relative position does the source assign to you?",
            "criteria": position_options,
            "label": position_label,
            "src": "pokerbench_state_reading",
        },
        "reading_turn_card": {
            "type": "noul",
            "instructions": "Has the turn card been dealt at this decision point?",
            "criteria": {"true": "A turn card is shown in the state", "false": "No turn card is shown in the state"},
            "label": street_label in {"turn", "river"},
            "src": "pokerbench_state_reading",
        },
        "reading_amount_semantics": {
            "type": "noul",
            "instructions": "Does the source specify whether a numeric action size is a total or an additional payment?",
            "criteria": {"true": "The source specifies the convention", "false": "The source does not specify the convention"},
            "label": False,
            "src": "pokerbench_state_reading",
        },
        "reading_opponent_cards": {
            "type": "noul",
            "instructions": "Are an opponent's private cards included in this state?",
            "criteria": {"true": "Opponent private cards are included", "false": "Opponent private cards are not included"},
            "label": False,
            "src": "pokerbench_state_reading",
        },
    }


def describe_action(value):
    """Add only the ambiguity supplied by the source; keep candidate keys and order intact."""
    match = re.fullmatch(r"(bet|raise)\s+([0-9]+(?:\.[0-9]+)?)", value.strip(), re.IGNORECASE)
    if match:
        verb, amount = match.groups()
        mechanism = "increases the current wager" if verb.casefold() == "raise" else "starts wagering on this street"
        return f"{verb.title()}, which {mechanism}, with recorded size {amount} chips; total-versus-additional convention unspecified"
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?\s*bb", value.strip(), re.IGNORECASE):
        return f"Raise, which increases the current wager, with source size {value.strip()}; total-versus-additional convention unspecified"
    return {
        "fold": "Fold and forfeit this hand",
        "call": "Pay to match the current wager, capped by your remaining chips; exact payment is not known",
        "check": "Check without adding chips",
        "allin": "Move all remaining chips in",
        "all-in": "Move all remaining chips in",
    }.get(value.strip().casefold(), value)


def transform_record(record):
    meta = record.setdefault("_meta", {})
    source = source_state(record)
    source_digest = sha(json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    expected_digest = meta.get("source_state_sha256", meta.get("text_sha256"))
    if expected_digest and expected_digest != source_digest:
        raise ValueError(f"stored source-state hash does not match the preserved source fields: {meta.get('id')}")
    if meta.get("group_id") and meta["group_id"] != source_digest:
        raise ValueError(f"source group id does not match the preserved source fields: {meta.get('id')}")
    original_action = record["questions"].get("action")
    if original_action is None:
        raise ValueError(f"PokerBench record has no action question: {meta.get('id')}")
    if isinstance(source, dict):
        source_candidates = meta.get("source_action_criteria")
        if source_candidates is None:
            source_candidates = [
                {"key": key, "value": value}
                for key, value in original_action.get("criteria", {}).items()
            ]
            meta["source_action_criteria"] = source_candidates
        moves = [entry["value"] for entry in source_candidates]
        original_row_digest = sha(json.dumps({"state": source, "moves": moves, "label": original_action.get("label")}, sort_keys=True, ensure_ascii=False))
        expected_row_digest = meta.get("source_row_sha256", meta.get("row_sha256"))
        if expected_row_digest and expected_row_digest != original_row_digest:
            raise ValueError(f"stored solver label or options do not match the original source row: {meta.get('id')}")
        meta["source_row_sha256"] = original_row_digest
        original_action["criteria"] = {
            entry["key"]: describe_action(entry["value"])
            for entry in source_candidates
        }
    if source.get("street") == "preflop":
        state, position = render_preflop(source)
    else:
        postflop = postflop_actions(source["postflop_action_history"], source["board"])
        state, position = render_postflop(source, postflop)
        # PokerBench's aggressor summary can disagree with the ordered action history.
        # Keep the source value in source_state, but use the current-street events above.
        meta["derived_current_street_aggressor"] = next(
            (role for street, role, action, _, _ in reversed(postflop)
             if street == source["street"].casefold() and action in {"bet", "raise"}),
            None,
        )
    questions = {key: value for key, value in record["questions"].items() if not key.startswith("reading_")}
    questions.update(comprehension_questions(source.get("street", "preflop"), position))
    if source.get("street") != "preflop":
        source_street = source["street"].casefold()
        recent = next((role for name, role, action, _, _ in reversed(postflop)
                       if name == source_street and action in {"bet", "raise"}), "none")
        questions["reading_last_aggressor"] = {
            "type": "choice",
            "instructions": "Which relative position most recently bet or raised on this street?",
            "criteria": {"IP": "In position (IP)", "OOP": "Out of position (OOP)", "none": "No bet or raise is recorded on this street"},
            "label": recent,
            "src": "pokerbench_state_reading",
        }
    meta.setdefault("source_state", source)
    meta["source_state_sha256"] = source_digest
    meta["variant"] = "english_state_with_reading_questions_v1"
    meta["text_sha256"] = sha(state)
    record["state"] = state
    record["questions"] = questions
    content = {key: record[key] for key in ("state", "questions")}
    meta["row_sha256"] = sha(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    return record
