import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from odds_conversion_backtest import (  # noqa: E402
    analyze,
    fit_fl_glm_beta,
    fl_glm_probs,
    load_records,
    multiplicative_probs,
    oo_epc_probs,
    power_probs,
    render,
    shin_probs,
)


def test_power_probs_sum_to_one_and_tilt_favorites():
    odds = np.array([[1.50, 4.00, 7.00], [2.05, 3.40, 3.90]], dtype=float)

    mult = multiplicative_probs(odds)
    power = power_probs(odds)

    assert np.allclose(power.sum(axis=1), 1.0)
    assert power[0, 0] > mult[0, 0]
    assert power[0, 2] < mult[0, 2]


def test_shin_probs_match_reference_values():
    odds = np.array([[1.50, 4.00, 7.00], [2.05, 3.40, 3.90], [1.95, 1.95, 1000.0]], dtype=float)

    probs = shin_probs(odds)

    assert np.allclose(probs.sum(axis=1), 1.0)
    assert np.allclose(probs[0], [0.6422795621514281, 0.23152687539948089, 0.12619356244952404])
    assert np.allclose(probs[1], [0.47369008439207927, 0.28182550986073873, 0.24448440574765903])


def test_oo_epc_probs_sum_to_one_and_follow_algorithm_5():
    odds = np.array([[1.50, 4.00, 7.00], [2.05, 3.40, 3.90]], dtype=float)

    probs = oo_epc_probs(odds)
    raw = 1.0 / odds[0]
    sigma = np.sqrt(1.0 - raw)
    z = (raw.sum() - 1.0) / sigma.sum()
    expected = raw - z * sigma

    assert np.allclose(probs.sum(axis=1), 1.0)
    assert np.allclose(probs[0], expected)
    assert probs[0, 0] > multiplicative_probs(odds)[0, 0]


def test_binary_shin_probs_sum_to_one():
    odds = np.array([[1.95, 1.95], [1.65, 2.35]], dtype=float)

    probs = shin_probs(odds)

    assert np.allclose(probs.sum(axis=1), 1.0)
    assert np.allclose(probs[0], [0.5, 0.5])


def test_fl_glm_fits_single_favorite_longshot_parameter():
    odds = np.array(
        [
            [1.20, 7.00],
            [1.25, 6.00],
            [1.30, 5.50],
            [1.35, 5.00],
        ],
        dtype=float,
    )
    outcomes = np.array([[1.0, 0.0]] * len(odds), dtype=float)

    beta = fit_fl_glm_beta(odds, outcomes)
    probs = fl_glm_probs(odds, beta)

    assert beta > 1.0
    assert np.allclose(probs.sum(axis=1), 1.0)
    assert probs[0, 0] > multiplicative_probs(odds)[0, 0]


def test_load_records_from_raw_csv_fixture(tmp_path):
    raw_dir = tmp_path / "fduk"
    raw_dir.mkdir()
    path = raw_dir / "2526_E0.csv"
    with path.open("w", newline="", encoding="latin-1") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "Div",
                "Date",
                "FTHG",
                "FTAG",
                "PSH",
                "PSD",
                "PSA",
                "P>2.5",
                "P<2.5",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Div": "E0",
                "Date": "01/08/25",
                "FTHG": "2",
                "FTAG": "1",
                "PSH": "1.80",
                "PSD": "3.60",
                "PSA": "4.80",
                "P>2.5": "1.95",
                "P<2.5": "1.95",
            }
        )

    rows = load_records(raw_dir)

    assert len(rows) == 1
    assert rows[0].season == "2526"
    assert rows[0].odds_1x2 == (1.80, 3.60, 4.80)
    assert rows[0].odds_ou25 == (1.95, 1.95)


def test_real_odds_conversion_report_keeps_multiplicative_until_totals_clear_gate():
    analysis = analyze(load_records())
    text = render(analysis, now=datetime(2026, 6, 29, 3, tzinfo=timezone.utc))

    assert analysis["decision"]["decision"] == "not_promoted_keep_multiplicative_devig"
    assert float(analysis["1x2"]["bootstrap_power"]["ci_2.5"]) > 0
    assert float(analysis["over_under_2.5"]["bootstrap_power"]["ci_2.5"]) < 0
    assert "shin" in analysis["1x2"]["methods"]
    assert "oo_epc" in analysis["1x2"]["methods"]
    assert "fl_glm_walkforward" in analysis["1x2"]["methods"]
    assert "oo_epc" in analysis["1x2"]["bootstraps"]
    assert "fl_glm_walkforward" in analysis["over_under_2.5"]["bootstraps"]
    assert "Power de-vig is tested as a small structural odds-conversion correction" in text
    assert "OO-EPC" in text
    assert "FL-GLM Walk-Forward Schedule" in text
    assert "`/predictions` was not read" in text
