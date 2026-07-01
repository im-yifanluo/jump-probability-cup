import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import (
    ambiguous_outcome_questions,
    official_alt_rbp,
    parse_history,
    result_outcome,
    settle_v1_rows,
    uninferable_result_questions,
    unique_outcomes_by_question,
)


def test_parse_history_v2_block_and_exact_delta():
    text = """## Version 2

### Spain vs Cabo Verde

RBP + 24.66

Q1
RESULT: YES
Will Cape Verde be caught offside 2 or more times?
YOU
36%
CROWD
39%
OUTCOME
100%
RBP
0.6
Beat crowd
"""
    rows = parse_history(text)
    assert len(rows) == 1
    row = rows[0]
    assert row.match == "Spain vs Cabo Verde"
    assert row.you == 0.36
    assert row.crowd == 0.39
    assert row.outcome == 1
    assert row.official_rbp == 0.6
    assert row.famkey == "team_count_ge/offsides"

    # Alternative p=0.39 changes official RBP only by current-vs-alt Brier.
    expected = 0.6 + 100 * ((0.36 - 1) ** 2 - (0.39 - 1) ** 2)
    assert official_alt_rbp(row, 0.39) == expected


def test_settle_v1_row_from_outcome_map():
    text = """## Version 1

### Qatar vs Switzerland is live. Your forecast is in — now sit back and see how the match plays out.

Question\tYou\tField\tIf Yes\tIf No\tSwing
Will Qatar score at least 1 goal?\t42%\t40%\t+6.80\t+3.09\t4
"""
    rows = parse_history(text)
    assert rows[0].outcome is None
    settled = settle_v1_rows(rows, {"Will Qatar score at least 1 goal?": 0})
    assert settled[0].outcome == 0
    assert settled[0].official_rbp == 3.09


def test_unique_outcomes_by_question_omits_ambiguous_repeated_questions():
    results = [
        {"question": "Will both teams score?", "probability_submitted": 60, "brier_score": 0.16},
        {"question": "Will both teams score?", "probability_submitted": 60, "brier_score": 0.36},
        {"question": "Will Qatar score?", "probability_submitted": 30, "brier_score": 0.09},
        {"question": "Will Qatar score?", "probability_submitted": 30, "brier_score": 0.09},
        {"question": "Will the match have 9+ corners?", "probability_submitted": 50, "brier_score": 0.25},
    ]

    assert unique_outcomes_by_question(results) == {"Will Qatar score?": 0}
    assert ambiguous_outcome_questions(results) == {"Will both teams score?": {0, 1}}
    assert uninferable_result_questions(results) == {"Will the match have 9+ corners?"}


def test_result_outcome_returns_none_when_brier_cannot_identify_side():
    assert result_outcome({"probability_submitted": 50, "brier_score": 0.25}) is None
    assert result_outcome({"probability_submitted": 60, "brier_score": 0.16}) == 1
    assert result_outcome({"probability_submitted": 60, "brier_score": 0.36}) == 0
