import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from special_official_results_audit import (  # noqa: E402
    grouped,
    is_special_like_question,
    render,
    settled_special_results,
    special_result_coverage,
)


def _result(question: str, submitted_pct: int, outcome: int, *, status: str = "settled") -> dict:
    p = submitted_pct / 100
    return {
        "market_id": f"{question[:12]}-{outcome}",
        "question": question,
        "probability_submitted": submitted_pct,
        "brier_score": (p - outcome) ** 2,
        "market_status": status,
    }


def test_settled_special_results_infers_outcomes_and_ignores_non_special_rows():
    question = "Will a goal be scored before the first hydration break?"
    data = [
        {"tournament": "a", "goal_before_30": True},
        {"tournament": "a", "goal_before_30": False},
        {"tournament": "b", "goal_before_30": False},
    ]
    results = [
        _result(question, 60, 1),
        _result(question, 60, 0),
        _result(question, 50, 0),
        _result("Will Brazil win in regulation (90 minutes + stoppage time)?", 70, 1),
        _result(question, 60, 1, status="open"),
    ]

    rows, skipped = settled_special_results(results, data)

    assert skipped == [f"uninferable_outcome:{question}"]
    assert [row.outcome for row in rows] == [1, 0]
    assert {row.family for row in rows} == {"goal_before_hydration"}
    assert [row.prior for row in rows] == pytest.approx([1 / 3, 1 / 3])


def test_grouped_scores_statsbomb_prior_against_official_outcomes():
    question = "Will a goal be scored before the first hydration break?"
    data = [
        {"tournament": "a", "goal_before_30": True},
        {"tournament": "b", "goal_before_30": False},
        {"tournament": "b", "goal_before_30": False},
    ]
    rows, _ = settled_special_results(
        [
            _result(question, 60, 1),
            _result(question, 60, 0),
        ],
        data,
    )

    [summary] = grouped(rows, lambda row: row.famkey)

    assert summary["group"] == "goal_before_hydration/goals"
    assert summary["n"] == 2
    assert summary["successes"] == 1
    assert summary["hit"] == 0.5
    assert summary["prior_mean"] == pytest.approx(1 / 3)
    assert summary["brier_prior"] == pytest.approx(5 / 18)
    assert summary["brier_50"] == pytest.approx(0.25)
    assert summary["edge_vs_50"] == pytest.approx(-1 / 36)


def test_render_documents_audit_limitations():
    text = render([], [], [], [])

    assert "/predictions` was not read" in text
    assert "outcome-rate check, not a full manual-policy backtest" in text
    assert "settled_special_rows: 0" in text
    assert "Coverage Diagnostics" in text


def test_special_result_coverage_distinguishes_false_positive_wording():
    data = [
        {"tournament": "a", "goal_before_30": True},
        {"tournament": "b", "goal_before_30": False},
    ]
    special_question = "Will a goal be scored before the first hydration break?"
    ordinary_question = "Will Brazil be caught offside 2 or more times?"
    results = [
        _result(special_question, 60, 1),
        _result(ordinary_question, 60, 0),
    ]

    coverage, samples = special_result_coverage(results, data)

    assert is_special_like_question(special_question)
    assert not is_special_like_question(ordinary_question)
    assert coverage["settled_results_rows"] == 2
    assert coverage["special_like_settled_rows"] == 1
    assert coverage["parsed_special_settled_rows"] == 1
    assert coverage["usable_special_rows"] == 1
    assert coverage["special_like_non_special_rows"] == 0
    assert samples["special_like_non_special"] == []
