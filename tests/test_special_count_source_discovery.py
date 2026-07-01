import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from special_count_source_discovery import CountSourceRow, discover_sources, load_source_rows, render  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


def _event(title: str, markets: list[dict]) -> dict:
    return {"title": title, "markets": markets}


def _market(question: str, volume: float = 2_000, prices: list[float] | None = None) -> dict:
    return {
        "question": question,
        "outcomes": ["Over", "Under"],
        "outcomePrices": prices or [0.42, 0.58],
        "volume": volume,
    }


def _row(match: str, question: str, evidence_key: str = "total_shots_ge_22") -> CountSourceRow:
    return CountSourceRow(
        market_id=f"m-{evidence_key}",
        match_name=match,
        kickoff_utc="2026-07-02T00:00+00:00",
        risk_state="COUNT_THRESHOLD_HIGH_HETEROGENEITY",
        evidence_key=evidence_key,
        question=question,
        spec=parse(question, teams=ALL_TEAM_NAMES),
    )


def test_discovers_exact_usable_total_shots_market():
    row = _row(
        "USA vs BIH",
        "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?",
    )
    events = [
        _event(
            "United States vs. Bosnia and Herzegovina - Total Shots",
            [_market("United States vs. Bosnia and Herzegovina: O/U 21.5 Total Shots", volume=3_000)],
        )
    ]

    result = discover_sources([row], events, min_market_volume=1000)
    discovered = result.rows[0]

    assert result.decision == "DISCOVERY_HAS_USABLE_EXACT_SOURCES"
    assert discovered.status == "EXACT_USABLE"
    assert discovered.source_usable is True
    assert round(discovered.probability or 0, 3) == 0.420


def test_exact_total_shots_market_still_respects_volume_gate():
    row = _row("AUS vs EGY", "Will there be 20 or more total shots (on and off target)?", "total_shots_ge_20")
    events = [
        _event(
            "Australia vs. Egypt - Total Shots",
            [_market("Australia vs. Egypt: O/U 19.5 Total Shots", volume=999)],
        )
    ]

    result = discover_sources([row], events, min_market_volume=1000)

    assert result.decision == "DISCOVERY_COMPLETE_NO_EXACT_SOURCES"
    assert result.rows[0].status == "EXACT_BELOW_VOLUME"
    assert result.rows[0].source_usable is False


def test_first_half_corners_are_near_miss_not_hydration_source():
    row = _row(
        "USA vs BIH",
        "Will 2 or more corner kicks be taken before the first hydration break?",
        "early_corners_ge_2",
    )
    events = [
        _event(
            "United States vs. Bosnia and Herzegovina - Total Corners",
            [
                _market("United States vs. Bosnia and Herzegovina: 1st Half O/U 1.5 Total Corners", volume=5_000),
                _market("United States vs. Bosnia and Herzegovina: O/U 8.5 Total Corners", volume=8_000),
            ],
        )
    ]

    result = discover_sources([row], events, min_market_volume=1000)

    assert result.rows[0].status == "NEAR_PERIOD_MISMATCH"
    assert "not the first-hydration-break period" in result.rows[0].reason


def test_goal_only_market_confirms_match_without_count_source():
    row = _row("AUS vs EGY", "Will there be 20 or more total shots (on and off target)?", "total_shots_ge_20")
    events = [_event("Australia vs. Egypt - More Markets", [_market("Australia vs. Egypt: O/U 2.5", volume=10_000)])]

    result = discover_sources([row], events, min_market_volume=1000)
    text = render(result)

    assert result.rows[0].status == "GOAL_MARKET_ONLY"
    assert result.rows[0].source_usable is False
    assert "- exact_usable_rows: 0" in text
    assert "GOAL_MARKET_ONLY" in text


def test_load_source_rows_parses_current_template_columns(tmp_path):
    path = tmp_path / "special_count_sources_template.csv"
    path.write_text(
        "market_id,match,kickoff_utc,risk_state,evidence_key,model_entry,base_hit_rate,model_minus_base_pp,question,source_1,source_1_probability,source_1_count_mean,source_2,source_2_probability,source_2_count_mean,notes\n"
        "m1,USA vs BIH,2026-07-02T00:00+00:00,COUNT_THRESHOLD_HIGH_HETEROGENEITY,total_shots_ge_22,53%,61.8%,-9,Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?,,,,,,,\n",
        encoding="utf-8",
    )

    rows = load_source_rows(path)

    assert len(rows) == 1
    assert rows[0].market_id == "m1"
    assert rows[0].spec is not None
    assert rows[0].spec.stat == "shots"
