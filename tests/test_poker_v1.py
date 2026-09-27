from decimal import Decimal
import importlib.util
import json
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "build_poker_v1", Path(__file__).parents[1] / "scripts" / "build_poker_v1.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def state(*, street="flop", current="0", owed="0", stack="100", street_bet="0",
          min_to="1", max_to="100", pot="10", aggression=True, kind="bet", reopened=True):
    return {
        "street": street, "current_bet": Decimal(current), "owed": Decimal(owed),
        "stack": Decimal(stack), "street_bet": Decimal(street_bet), "min_to": Decimal(min_to),
        "max_to": Decimal(max_to), "pot": Decimal(pot), "aggression": aggression,
        "aggression_type": kind, "reopened": reopened,
    }


def test_positive_js_round_and_fractional_legal_boundaries():
    assert builder.js_round_positive(Decimal("2.5")) == 3
    assert builder.js_round_positive(Decimal("2.49")) == 2
    found = builder.baseline_amounts(state(min_to="70.7", max_to="86.5", pot="100"))
    assert found[0] == Decimal("70.7")
    assert found[-1] == Decimal("86.5")


def test_fold_remains_offered_when_check_is_available():
    question, _ = builder.candidates(state(), "check")
    assert {"fold", "check"} <= set(question["criteria"])
    assert question["label"] == "check"


def test_call_can_be_an_all_in_payment():
    s = state(current="30", owed="30", stack="12", street_bet="0", aggression=False, kind="raise")
    question, _ = builder.candidates(s, "call")
    assert question["label"] == "call"
    assert "paying 12 additional" in question["criteria"]["call"]
    assert "0 chips remain" in question["criteria"]["call"]
    assert "all-in call" in question["criteria"]["call"]


def test_short_all_in_raise_does_not_reopen_hero():
    history = [
        ("preflop", [
            {"player": "HJ", "action": "raise", "amount": Decimal("2"), "source_text": "HJ raise 2"},
            {"player": "BB", "action": "raise", "amount": Decimal("85"), "source_text": "BB raise 85"},
            {"player": "HJ", "action": "call", "amount": None, "source_text": "HJ call"},
        ]),
        ("flop", [
            {"player": "HJ", "action": "check", "amount": None, "source_text": "HJ check"},
            {"player": "BB", "action": "bet", "amount": Decimal("10"), "source_text": "BB bet 10"},
            {"player": "HJ", "action": "allin", "amount": None, "source_text": "HJ all-in"},
        ]),
    ]
    s = builder.replay("BB", history, Decimal("100"))
    assert s["current_bet"] == 15
    assert s["last_full_raise"] == 10
    assert s["reopened"] is False
    assert s["aggression"] is False


def test_only_all_in_opponent_cannot_be_raised():
    history = [("preflop", [
        {"player": "SB", "action": "allin", "amount": None, "source_text": "SB all-in"},
    ])]
    s = builder.replay("BB", history, Decimal("100"))
    assert s["owed"] == 99
    assert s["active_opponent"] is False
    assert s["aggression"] is False
    question, _ = builder.candidates(s, "call")
    assert "raise_to_" not in " ".join(question["criteria"])


def test_minimum_raise_and_normalized_label():
    s = state(current="10", owed="10", stack="90", street_bet="0", min_to="20",
              max_to="90", pot="30", kind="raise")
    question, info = builder.candidates(s, "bet 20")
    assert question["label"] == "raise_to_20"
    assert info["normalization_reason"] == "normalized_bet_to_raise_for_current_bet"
    assert "Raise to a total of 20 chips" in question["criteria"]["raise_to_20"]


def test_nearest_equal_distance_uses_lower_and_keeps_boundaries():
    s = state(min_to="2", max_to="20", pot="10")
    question, info = builder.candidates(s, "bet 5")
    assert info["replacement"]["from"] == "3"
    assert info["final_amounts"] == ["2", "5", "7", "10", "15", "20"]
    assert "bet_to_2" in question["criteria"] and "bet_to_20" in question["criteria"]


def test_gold_minimum_boundary_is_not_replaced():
    question, info = builder.candidates(state(min_to="3", max_to="20", pot="10"), "bet 3")
    assert info["replacement"] is None
    assert info["final_amounts"][0] == "3"
    assert question["label"] == "bet_to_3"


def test_exact_integer_truncated_all_in_is_corrected():
    s = state(current="65", owed="5", stack="10.7", street_bet="60", min_to="70.7",
              max_to="70.7", pot="100", kind="raise")
    question, info = builder.candidates(s, "raise 70")
    assert question["label"] == "raise_to_70_7"
    assert "70.7 chips remain in your stack" not in question["criteria"]["raise_to_70_7"]
    assert "Pay 10.7 additional chips now; 0 chips remain" in question["criteria"]["raise_to_70_7"]
    assert info["gold_correction"] == {
        "reason": "source_integer_truncated_exact_fractional_all_in", "from": "70", "to": "70.7"
    }


def test_allin_repair_requires_every_reviewed_condition():
    base = state(min_to="70.7", max_to="70.7", aggression=True, kind="raise")
    assert builder.truncated_allin_correction("raise", Decimal("70"), base)["to"] == "70.7"
    assert builder.truncated_allin_correction("raise", Decimal("70.2"), base) is None  # non-integer gold
    assert builder.truncated_allin_correction("raise", Decimal("69"), state(min_to="70.5", max_to="70.5")) is None  # gap 1.5
    assert builder.truncated_allin_correction("raise", Decimal("70"), state(min_to="70.2", max_to="70.2")) is None  # gap 0.2
    assert builder.truncated_allin_correction("raise", Decimal("70"), state(min_to="70", max_to="70.7")) is None  # min != max
    assert builder.truncated_allin_correction("raise", Decimal("70"), state(min_to="70.7", max_to="70.7", aggression=False)) is None
    assert builder.truncated_allin_correction("fold", Decimal("70"), base) is None


def test_unknown_output_is_quarantined_with_original_text():
    source = json.loads((Path(__file__).parents[1] / "evals/poker-v1/review/source.json").read_text(encoding="utf-8"))
    source["output"] = "jam it"
    record, quarantine = builder.convert("postflop", 2, source)
    assert record is None
    assert quarantine["instruction"] == source["instruction"]
    assert quarantine["output"] == source["output"]
    assert "unrecognized gold output" in quarantine["reason"]
