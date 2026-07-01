import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import parse_history  # noqa: E402
from backtest_integrity_audit import classify_v1  # noqa: E402


def test_classify_v1_separates_unique_ambiguous_and_missing_results():
    text = """## Version 1

### Qatar vs Switzerland is live. Your forecast is in — now sit back and see how the match plays out.

Question\tYou\tField\tIf Yes\tIf No\tSwing
Will Qatar score?\t42%\t40%\t+6.80\t+3.09\t4
Will both teams score?\t52%\t50%\t+1.00\t+2.00\t1
Will the match have 9+ corners?\t50%\t50%\t+1.00\t+1.00\t1
Will Switzerland score?\t40%\t45%\t+3.00\t+4.00\t1
"""
    rows = parse_history(text)
    results = [
        {"question": "Will Qatar score?", "probability_submitted": 30, "brier_score": 0.09},
        {"question": "Will both teams score?", "probability_submitted": 60, "brier_score": 0.16},
        {"question": "Will both teams score?", "probability_submitted": 60, "brier_score": 0.36},
        {"question": "Will the match have 9+ corners?", "probability_submitted": 50, "brier_score": 0.25},
    ]

    buckets = classify_v1(rows, results)

    assert [row.question for row in buckets["safe_unique"]] == ["Will Qatar score?"]
    assert [row.question for row in buckets["ambiguous"]] == ["Will both teams score?"]
    assert [row.question for row in buckets["uninferable"]] == ["Will the match have 9+ corners?"]
    assert [row.question for row in buckets["missing"]] == ["Will Switzerland score?"]
