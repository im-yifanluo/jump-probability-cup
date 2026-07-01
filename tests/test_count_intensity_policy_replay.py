import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import HistoryMarket  # noqa: E402
from count_intensity_policy_replay import apply_manual_policy, diff_vs_baseline  # noqa: E402
from count_intensity_replay import PricedRow  # noqa: E402


def _history(question, *, crowd=0.60, you=0.50, outcome=0):
    return HistoryMarket(
        version=2,
        order=1,
        match="RSA vs CAN",
        question=question,
        you=you,
        crowd=crowd,
        outcome=outcome,
        official_rbp=0.0,
    )


def _manual_probability(question, model, *, crowd=0.60):
    rows, skipped = apply_manual_policy(
        [PricedRow(row=_history(question, crowd=crowd), probability=model, candidate="candidate")]
    )
    assert skipped == []
    assert len(rows) == 1
    return rows[0].manual_probability, rows[0].policy


def test_apply_manual_policy_uses_field_for_card_counts():
    p, policy = _manual_probability(
        "Will there be 4 or more total cards shown?",
        0.20,
        crowd=0.55,
    )

    assert p == 0.55
    assert policy == "card_count_field"


def test_apply_manual_policy_keeps_strong_sot_model():
    p, policy = _manual_probability(
        "Will Canada have 5 or more shots on target?",
        0.37,
        crowd=0.58,
    )

    assert p == 0.37
    assert policy == "strong_sot_count_model"


def test_apply_manual_policy_caps_ordinary_count_below_field():
    p, policy = _manual_probability(
        "Will there be 9 or more total corner kicks?",
        0.20,
        crowd=0.60,
    )

    assert p == 0.48
    assert policy == "global_asym075_cap12"


def test_diff_vs_baseline_pairs_by_match_and_question():
    row = _history("Will there be 9 or more total corner kicks?", crowd=0.60, outcome=0)
    candidate, _ = apply_manual_policy([PricedRow(row=row, probability=0.20, candidate="candidate")])
    baseline, _ = apply_manual_policy([PricedRow(row=row, probability=0.60, candidate="baseline")])

    diff = diff_vs_baseline(candidate, baseline)

    assert diff["n"] == 1
    assert diff["delta"] > 0
