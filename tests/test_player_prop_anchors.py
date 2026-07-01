import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from manual.player_prop_anchors import (  # noqa: E402
    american_to_probability,
    devig_two_way,
    inspect_player_prop_anchor_csv,
    read_player_prop_anchor_csv,
)


def test_read_player_prop_anchor_csv_probability_by_market_id(tmp_path):
    path = tmp_path / "props.csv"
    path.write_text(
        "market_id,probability,source,as_of\nm1,38%,DraftKings,2026-06-28T12:00Z\n",
        encoding="utf-8",
    )

    anchors = read_player_prop_anchor_csv(str(path))

    assert anchors.row_count == 1
    assert math.isclose(anchors.by_market_id["m1"].probability, 0.38)
    assert anchors.by_market_id["m1"].source == "DraftKings"
    assert anchors.by_market_id["m1"].as_of == "2026-06-28T12:00Z"


def test_read_player_prop_anchor_csv_devigs_two_way_american_odds(tmp_path):
    path = tmp_path / "props.csv"
    path.write_text(
        "match,question,yes_american,no_american\n"
        "RSA vs CAN,Will Cyle Larin score?," "+100,-120\n",
        encoding="utf-8",
    )

    anchors = read_player_prop_anchor_csv(str(path))
    anchor = anchors.by_match_question[("RSA vs CAN", "Will Cyle Larin score?")]

    expected = american_to_probability(100) / (
        american_to_probability(100) + american_to_probability(-120)
    )
    assert math.isclose(anchor.probability, expected)
    assert math.isclose(anchor.probability, devig_two_way(100, -120))


def test_read_player_prop_anchor_csv_requires_exact_key(tmp_path):
    path = tmp_path / "props.csv"
    path.write_text("probability\n45\n", encoding="utf-8")

    with pytest.raises(ValueError, match="market_id or match\\+question"):
        read_player_prop_anchor_csv(str(path))


def test_read_player_prop_anchor_csv_rejects_duplicate_market_id(tmp_path):
    path = tmp_path / "props.csv"
    path.write_text("market_id,probability\nm1,45\nm1,46\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        read_player_prop_anchor_csv(str(path))


def test_inspect_player_prop_anchor_csv_allows_blank_template_rows(tmp_path):
    path = tmp_path / "props.csv"
    path.write_text(
        "market_id,match,question,probability,yes_american,no_american\n"
        "m1,RSA vs CAN,Will Cyle Larin score?,,,\n",
        encoding="utf-8",
    )

    inspection = inspect_player_prop_anchor_csv(str(path))

    assert len(inspection.rows) == 1
    assert inspection.rows[0].status == "blank"
    assert len(inspection.blank) == 1
    assert len(inspection.usable) == 0


def test_strict_reader_rejects_blank_template_rows(tmp_path):
    path = tmp_path / "props.csv"
    path.write_text(
        "market_id,probability,yes_american,no_american\nm1,,,\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="blank"):
        read_player_prop_anchor_csv(str(path))


def test_inspect_player_prop_anchor_csv_marks_one_sided_odds_invalid(tmp_path):
    path = tmp_path / "props.csv"
    path.write_text(
        "market_id,yes_american,no_american\nm1,+120,\n",
        encoding="utf-8",
    )

    inspection = inspect_player_prop_anchor_csv(str(path))

    assert len(inspection.invalid) == 1
    assert "both yes_american and no_american" in inspection.invalid[0].error
