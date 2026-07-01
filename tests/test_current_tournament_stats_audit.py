import csv
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from current_tournament_stats_audit import (  # noqa: E402
    OpenMarket,
    TeamLine,
    aggregate_lines,
    current_open_count_rows,
    extract_team_lines,
    render,
    update_special_count_source_csv,
)


def _stat(name: str, value: str):
    return {"name": name, "displayValue": value}


def test_extract_team_lines_combines_yellow_and_red_cards():
    event = {
        "id": "e1",
        "date": "2026-06-20T17:00:00Z",
        "name": "Brazil at Japan",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Brazil"}},
                    {"homeAway": "away", "team": {"displayName": "Japan"}},
                ]
            }
        ],
    }
    summary = {
        "boxscore": {
            "teams": [
                {
                    "team": {"displayName": "Brazil"},
                    "statistics": [
                        _stat("totalShots", "18"),
                        _stat("shotsOnTarget", "7"),
                        _stat("wonCorners", "6"),
                        _stat("yellowCards", "2"),
                        _stat("redCards", "1"),
                    ],
                },
                {
                    "team": {"displayName": "Japan"},
                    "statistics": [
                        _stat("totalShots", "9"),
                        _stat("shotsOnTarget", "3"),
                        _stat("wonCorners", "2"),
                        _stat("yellowCards", "1"),
                        _stat("redCards", "0"),
                    ],
                },
            ]
        }
    }

    lines = extract_team_lines(event, summary)

    assert len(lines) == 2
    assert lines[0].stats["cards"] == 3
    assert lines[1].stats["cards"] == 1
    assert lines[0].stats["shots"] == 18


def test_current_open_count_rows_prices_total_shots_from_current_boxscores():
    kickoff = datetime(2026, 6, 29, 17, tzinfo=timezone.utc)
    lines = [
        TeamLine("b1", kickoff, "Brazil vs X", "Brazil", "X", {"shots": 20, "sot": 8, "corners": 7, "cards": 2, "red_cards": 0, "fouls": 10, "offsides": 1}),
        TeamLine("b1", kickoff, "Brazil vs X", "X", "Brazil", {"shots": 8, "sot": 2, "corners": 1, "cards": 1, "red_cards": 0, "fouls": 9, "offsides": 1}),
        TeamLine("j1", kickoff, "Japan vs Y", "Japan", "Y", {"shots": 12, "sot": 4, "corners": 4, "cards": 1, "red_cards": 0, "fouls": 8, "offsides": 2}),
        TeamLine("j1", kickoff, "Japan vs Y", "Y", "Japan", {"shots": 10, "sot": 3, "corners": 3, "cards": 2, "red_cards": 0, "fouls": 11, "offsides": 2}),
    ]
    markets = [
        OpenMarket(
            "m-shots",
            "BRA vs JPN",
            kickoff,
            "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
        )
    ]

    rows = current_open_count_rows(markets, aggregate_lines(lines))

    assert len(rows) == 1
    assert rows[0]["stat"] == "shots"
    assert rows[0]["mean"] > 20
    assert 0 < rows[0]["probability"] < 1
    assert rows[0]["method"] == "total_mean_poisson"


def test_update_special_count_source_csv_fills_only_first_blank_source(tmp_path):
    path = tmp_path / "special.csv"
    columns = [
        "market_id",
        "match",
        "kickoff_utc",
        "risk_state",
        "evidence_key",
        "model_entry",
        "base_hit_rate",
        "model_minus_base_pp",
        "question",
        "source_1",
        "source_1_probability",
        "source_1_count_mean",
        "source_2",
        "source_2_probability",
        "source_2_count_mean",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerow({"market_id": "m1", "question": "Will there be 22 or more total shots?"})

    changed = update_special_count_source_csv(
        path,
        [
            {
                "market_id": "m1",
                "probability": 0.44,
                "mean": 21.2,
                "method": "total_mean_poisson",
            }
        ],
    )

    with path.open(newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert changed == 1
    assert row["source_1"] == "ESPN current tournament boxscore shrink"
    assert row["source_1_probability"] == "0.440"
    assert row["source_1_count_mean"] == "21.20"
    assert row["source_2"] == ""


def test_render_exposes_current_tournament_stats_status():
    kickoff = datetime(2026, 6, 29, 17, tzinfo=timezone.utc)
    lines = [
        TeamLine("e1", kickoff, "Brazil vs Japan", "Brazil", "Japan", {"shots": 20, "sot": 8, "corners": 7, "cards": 2, "red_cards": 0, "fouls": 10, "offsides": 1}),
        TeamLine("e1", kickoff, "Brazil vs Japan", "Japan", "Brazil", {"shots": 8, "sot": 2, "corners": 1, "cards": 1, "red_cards": 0, "fouls": 9, "offsides": 1}),
    ]

    text = render(lines, aggregate_lines(lines), [], sports_predict=False, updated_csv_rows=0)

    assert "- current_tournament_stats_status: COMPLETE" in text
    assert "`/predictions` was not read" in text
