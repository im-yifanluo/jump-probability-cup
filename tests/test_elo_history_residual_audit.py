import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from elo_history_residual_audit import analyze, load_matches, probs_from_expected, render  # noqa: E402


def test_probs_from_expected_are_normalized_and_ordered():
    probs = probs_from_expected(0.75, diff=190.0, draw_base=0.27)

    assert np.allclose(probs.sum(), 1.0)
    assert probs[0] > probs[2]
    assert 0.02 <= probs[1] <= 0.54


def test_load_matches_from_fixture(tmp_path):
    path = tmp_path / "results.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "date",
                "home_team",
                "away_team",
                "home_score",
                "away_score",
                "tournament",
                "city",
                "country",
                "neutral",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "date": "2026-06-01",
                "home_team": "Brazil",
                "away_team": "Japan",
                "home_score": "2",
                "away_score": "1",
                "tournament": "FIFA World Cup",
                "city": "Dallas",
                "country": "United States",
                "neutral": "TRUE",
            }
        )

    rows = load_matches(path)

    assert len(rows) == 1
    assert rows[0].outcome_index == 0
    assert rows[0].neutral is True


def test_real_elo_history_audit_is_proxy_only_not_odds_override():
    analysis = analyze(load_matches())
    text = render(analysis, now=datetime(2026, 6, 29, 4, tzinfo=timezone.utc))

    assert analysis["decision"] == "candidate_history_feature_positive_unpriced_only"
    assert analysis["best_history"] == "elo_history_w0.75"
    assert float(analysis["history_vs_elo_bootstrap"]["ci_2.5"]) > 0
    assert "not a live override for priced goal/outcome markets" in text
    assert "`/predictions` was not read" in text
