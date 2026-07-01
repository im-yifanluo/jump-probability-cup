import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from full_manual_pricing import (  # noqa: E402
    FieldCoverage,
    MarketRow,
    PlayerNoFieldPolicy,
    _field_coverage_line,
    _player_no_field_policy_line,
    _player_prop_anchor_line,
    field_coverage,
    markdown_report,
    price_rows,
    read_field_csv,
)
import full_manual_pricing  # noqa: E402
from manual.player_prop_anchors import PlayerPropAnchor, PlayerPropAnchors  # noqa: E402


def _row(market_id: str, question: str = "Will both teams score?") -> MarketRow:
    return MarketRow(
        match_name="RSA vs CAN",
        kickoff=datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
        market_id=market_id,
        question=question,
    )


def test_read_field_csv_accepts_percent_and_decimal(tmp_path):
    path = tmp_path / "field.csv"
    path.write_text("market_id,field\nm1,47%\nm2,0.52\n", encoding="utf-8")

    assert read_field_csv(str(path)) == {"m1": 47.0, "m2": 0.52}


def test_read_field_csv_rejects_duplicate_market_ids(tmp_path):
    path = tmp_path / "field.csv"
    path.write_text("market_id,field\nm1,47\nm1,48\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        read_field_csv(str(path))


def test_read_field_csv_requires_id_and_field_columns(tmp_path):
    path = tmp_path / "field.csv"
    path.write_text("question,value\nq,47\n", encoding="utf-8")

    with pytest.raises(ValueError, match="market_id"):
        read_field_csv(str(path))


def test_field_coverage_reports_missing_and_unused_ids():
    rows = [_row("m1"), _row("m2", "Will Cyle Larin score?")]
    info = field_coverage(rows, {"m1": 47.0, "unused": 52.0})
    line = _field_coverage_line(info)

    assert info.supplied
    assert info.selected == 2
    assert info.supplied_rows == 2
    assert info.matched == 1
    assert [row.market_id for row in info.missing] == ["m2"]
    assert info.unused_ids == ("unused",)
    assert "missing=1" in line
    assert "unused=1" in line
    assert "Will Cyle Larin score?" in line


def test_player_prop_anchor_line_reports_selected_matches():
    rows = [
        {
            "market_id": "m1",
            "match_name": "RSA vs CAN",
            "question": "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        },
        {
            "market_id": "m2",
            "match_name": "RSA vs CAN",
            "question": "Will both teams score in regulation (90 minutes + stoppage time)?",
        },
    ]
    anchors = PlayerPropAnchors(
        by_market_id={"m1": PlayerPropAnchor(0.15, "DraftKings", "2026-06-28T12:00Z")},
        by_match_question={},
        row_count=1,
    )

    line = _player_prop_anchor_line(rows, anchors)

    assert "selected_player_rows=1" in line
    assert "matched=1" in line
    assert "unused=0" in line


def test_player_no_field_policy_line_reports_base_or_error():
    available = _player_no_field_policy_line(PlayerNoFieldPolicy(0.279, settled_rows=80))
    unavailable = _player_no_field_policy_line(PlayerNoFieldPolicy(None, error="network"))

    assert "base=28%" in available
    assert "settled_player_rows=80" in available
    assert "unavailable" in unavailable
    assert "network" in unavailable


def test_markdown_report_documents_world_cup_polymarket_series(monkeypatch):
    class FakeBaselines:
        source = "test"
        n_matches = 1

    monkeypatch.setattr(full_manual_pricing, "load_special_baselines", lambda: FakeBaselines())

    text = markdown_report(
        [
            {
                "match_name": "RSA vs CAN",
                "kickoff": datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
                "probability": 25,
                "manual_entry": "25%",
                "manual_policy": "raw_odds_goal_model",
                "flags": "",
                "field": None,
                "question": "Will regulation end in a tie?",
                "model": "match_drawn/goals",
                "reasoning": "test",
            }
        ],
        diagnostics={
            "RSA vs CAN": {
                "home": "South Africa",
                "away": "Canada",
                "lam_home": 0.75,
                "lam_away": 1.68,
                "referee": {"status": "no_override", "card_factor": 1.0},
            }
        },
        lineups={"RSA vs CAN": {"status": "not_posted"}},
        local_date="2026-06-28",
        tz=full_manual_pricing.ZoneInfo("America/Chicago"),
        now=datetime(2026, 6, 28, 12, tzinfo=timezone.utc),
        field_info=FieldCoverage(False, 1, 0, 0),
        player_prop_anchors=PlayerPropAnchors.empty(),
        player_no_field_policy=PlayerNoFieldPolicy(0.28, settled_rows=80),
    )

    assert "tag_slug=soccer" in text
    assert "series_slug=soccer-fifwc" in text


def test_price_rows_uses_exact_player_prop_anchor(monkeypatch):
    class FakeGoals:
        lam_home = 0.8
        lam_away = 1.6

    class FakePricer:
        home_name = "South Africa"
        away_name = "Canada"
        goals = FakeGoals()

        def price(self, spec):
            return 0.11

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(
        full_manual_pricing,
        "player_flags",
        lambda spec, pricer, lineup: ["PRIOR_ONLY", "LINEUP_NOT_POSTED"],
    )
    anchors = PlayerPropAnchors(
        by_market_id={"m1": PlayerPropAnchor(0.36, "DraftKings", "2026-06-28T12:00Z")},
        by_match_question={},
        row_count=1,
    )

    priced, _diagnostics = price_rows(
        [
            _row(
                "m1",
                "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"RSA vs CAN": {}},
        field_by_market={},
        player_prop_anchors=anchors,
    )

    assert priced[0]["probability"] == 36
    assert priced[0]["manual_policy"] == "player_no_field_researched_model"
    assert "CURRENT_PROP_MARKET_ANCHOR" in priced[0]["flags"]
    assert "PRIOR_ONLY" not in priced[0]["flags"]
    assert "LINEUP_NOT_POSTED" in priced[0]["flags"]
    assert priced[0]["model"].startswith("current_player_prop_anchor")


def test_price_rows_uses_exact_polymarket_player_prop_anchor(monkeypatch):
    class FakeRates:
        source = "prior"
        expected_minutes = 90

    class FakePlayers:
        def rates(self, player):
            return FakeRates()

        def is_sparse(self, player):
            return False

    class FakeGoals:
        lam_home = 1.7
        lam_away = 0.9

    class FakePricer:
        home_name = "Brazil"
        away_name = "Japan"
        goals = FakeGoals()
        players = FakePlayers()
        scorer_props = {}

        def price(self, spec):
            return 0.39

        def _player_side(self, player, team_hint=None):
            return "home"

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(
        full_manual_pricing,
        "player_flags",
        lambda spec, pricer, lineup: ["PRIOR_ONLY", "LINEUP_NOT_POSTED"],
    )
    events = [
        {
            "title": "Brazil vs. Japan - Player Props",
            "markets": [
                {
                    "question": "Matheus Cunha: 1+ goals",
                    "outcomes": ["Yes", "No"],
                    "outcomePrices": [0.315, 0.685],
                    "volume": 1500,
                }
            ],
        }
    ]

    priced, _diagnostics = price_rows(
        [
            MarketRow(
                match_name="BRA vs JPN",
                kickoff=datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
                market_id="m1",
                question="Will Matheus Cunha score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"BRA vs JPN": {}},
        field_by_market={},
        player_prop_anchors=PlayerPropAnchors.empty(),
        polymarket_events=events,
        polymarket_player_min_volume=1000,
    )

    assert priced[0]["probability"] == 32
    assert priced[0]["manual_entry"] == "32%"
    assert "CURRENT_PROP_MARKET_ANCHOR" in priced[0]["flags"]
    assert "PRIOR_ONLY" not in priced[0]["flags"]
    assert "LINEUP_NOT_POSTED" in priced[0]["flags"]
    assert priced[0]["model"].startswith("current_player_prop_anchor [Polymarket direct")
    assert priced[0]["polymarket_player_anchor"] is not None


def test_csv_player_prop_anchor_takes_precedence_over_polymarket(monkeypatch):
    class FakeGoals:
        lam_home = 1.7
        lam_away = 0.9

    class FakePricer:
        home_name = "Brazil"
        away_name = "Japan"
        goals = FakeGoals()

        def price(self, spec):
            return 0.39

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(full_manual_pricing, "player_flags", lambda spec, pricer, lineup: ["LINEUP_NOT_POSTED"])
    anchors = PlayerPropAnchors(
        by_market_id={"m1": PlayerPropAnchor(0.41, "DraftKings", "2026-06-28T13:00Z")},
        by_match_question={},
        row_count=1,
    )
    events = [
        {
            "title": "Brazil vs. Japan - Player Props",
            "markets": [
                {
                    "question": "Matheus Cunha: 1+ goals",
                    "outcomes": ["Yes", "No"],
                    "outcomePrices": [0.315, 0.685],
                    "volume": 1500,
                }
            ],
        }
    ]

    priced, _diagnostics = price_rows(
        [
            MarketRow(
                match_name="BRA vs JPN",
                kickoff=datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
                market_id="m1",
                question="Will Matheus Cunha score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"BRA vs JPN": {}},
        field_by_market={},
        player_prop_anchors=anchors,
        polymarket_events=events,
        polymarket_player_min_volume=1000,
    )

    assert priced[0]["probability"] == 41
    assert "DraftKings" in priced[0]["model"]
    assert priced[0]["polymarket_player_anchor"] is None


def test_price_rows_uses_exact_polymarket_direct_anchor_for_goal_market(monkeypatch):
    class FakeGoals:
        lam_home = 0.8
        lam_away = 1.6

    class FakePricer:
        home_name = "South Africa"
        away_name = "Canada"
        goals = FakeGoals()

        def price(self, spec):
            return 0.40

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(full_manual_pricing, "player_flags", lambda spec, pricer, lineup: [])
    events = [
        {
            "title": "South Africa vs. Canada - More Markets",
            "markets": [
                {
                    "question": "South Africa vs. Canada: O/U 2.5",
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": [0.55, 0.45],
                    "volume": 2500,
                }
            ],
        }
    ]

    priced, _diagnostics = price_rows(
        [
            _row(
                "m1",
                "Will the match have 3 or more total goals in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"RSA vs CAN": {}},
        field_by_market={},
        polymarket_events=events,
        polymarket_direct_min_volume=1000,
    )

    assert priced[0]["probability"] == 55
    assert priced[0]["manual_entry"] == "55%"
    assert priced[0]["model"].startswith("current_polymarket_direct")
    assert priced[0]["direct_market_anchor"] is not None


def test_price_rows_uses_exact_polymarket_count_anchor_for_corner_market(monkeypatch):
    class FakeGoals:
        lam_home = 0.8
        lam_away = 1.6

    class FakePricer:
        home_name = "South Africa"
        away_name = "Canada"
        goals = FakeGoals()

        def price(self, spec):
            return 0.49

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(full_manual_pricing, "player_flags", lambda spec, pricer, lineup: [])
    events = [
        {
            "title": "South Africa vs. Canada - Total Corners",
            "markets": [
                {
                    "question": "South Africa vs. Canada: O/U 8.5 Total Corners",
                    "outcomes": ["Over", "Under"],
                    "outcomePrices": [0.60, 0.40],
                    "volume": 30_000,
                }
            ],
        }
    ]

    priced, _diagnostics = price_rows(
        [
            _row(
                "m1",
                "Will there be 9 or more total corner kicks in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"RSA vs CAN": {}},
        field_by_market={},
        polymarket_events=events,
        polymarket_count_min_volume=5000,
    )

    assert priced[0]["probability"] == 60
    assert priced[0]["manual_entry"] == "60%"
    assert priced[0]["model"].startswith("current_polymarket_count_direct")
    assert priced[0]["polymarket_count_anchor"] is not None


def test_price_rows_applies_no_field_player_base_for_non_scorer_without_anchor(monkeypatch):
    class FakeGoals:
        lam_home = 0.8
        lam_away = 1.6

    class FakePricer:
        home_name = "South Africa"
        away_name = "Canada"
        goals = FakeGoals()

        def price(self, spec):
            return 0.36

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(
        full_manual_pricing,
        "player_flags",
        lambda spec, pricer, lineup: ["GLOBAL_MINUTES_UNCONFIRMED", "LINEUP_NOT_POSTED"],
    )
    monkeypatch.setattr(full_manual_pricing, "model_note", lambda pricer, spec: spec.family)

    priced, _diagnostics = price_rows(
        [
            _row(
                "m1",
                "Will Iqraam Rayners have at least 1 shot on target in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"RSA vs CAN": {}},
        field_by_market={},
        player_prop_anchors=PlayerPropAnchors.empty(),
        player_no_field_base=0.28,
    )

    assert priced[0]["probability"] == 36
    assert priced[0]["manual_entry"] == "28%"
    assert priced[0]["manual_policy"] == "player_no_field_raw_scores_else_global_base"


def test_price_rows_does_not_apply_no_field_base_to_unvalidated_player_threshold(monkeypatch):
    class FakeGoals:
        lam_home = 0.8
        lam_away = 1.6

    class FakePricer:
        home_name = "South Africa"
        away_name = "Canada"
        goals = FakeGoals()

        def price(self, spec):
            return 0.01

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(
        full_manual_pricing,
        "player_flags",
        lambda spec, pricer, lineup: ["LINEUP_NOT_POSTED"],
    )
    monkeypatch.setattr(full_manual_pricing, "model_note", lambda pricer, spec: spec.family)

    priced, _diagnostics = price_rows(
        [
            _row(
                "m1",
                "Will Nestory Irankunda (Australia) have 2 or more shots on target in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"RSA vs CAN": {}},
        field_by_market={},
        player_prop_anchors=PlayerPropAnchors.empty(),
        player_no_field_base=0.28,
    )

    assert priced[0]["probability"] == 1
    assert priced[0]["manual_entry"] == "1%"
    assert priced[0]["manual_policy"] == "player_no_field_raw_model_unvalidated_threshold"


def test_price_rows_keeps_prior_only_no_field_player_base_raw_source_unvalidated(monkeypatch):
    class FakeGoals:
        lam_home = 0.8
        lam_away = 1.6

    class FakePricer:
        home_name = "South Africa"
        away_name = "Canada"
        goals = FakeGoals()

        def price(self, spec):
            return 0.42

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(
        full_manual_pricing,
        "player_flags",
        lambda spec, pricer, lineup: ["PRIOR_ONLY", "LINEUP_NOT_POSTED"],
    )
    monkeypatch.setattr(full_manual_pricing, "model_note", lambda pricer, spec: spec.family)

    priced, _diagnostics = price_rows(
        [
            _row(
                "m1",
                "Will Amad Diallo (Ivory Coast) have 1 or more shots on target in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"RSA vs CAN": {}},
        field_by_market={},
        player_prop_anchors=PlayerPropAnchors.empty(),
        player_no_field_base=0.28,
    )

    assert priced[0]["probability"] == 42
    assert priced[0]["manual_entry"] == "42%"
    assert priced[0]["manual_policy"] == "research_required_no_field_source_unvalidated"


def test_price_rows_no_field_player_base_keeps_scorer_raw(monkeypatch):
    class FakeGoals:
        lam_home = 0.8
        lam_away = 1.6

    class FakePricer:
        home_name = "South Africa"
        away_name = "Canada"
        goals = FakeGoals()

        def price(self, spec):
            return 0.15

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(
        full_manual_pricing,
        "player_flags",
        lambda spec, pricer, lineup: ["GLOBAL_MINUTES_UNCONFIRMED", "LINEUP_NOT_POSTED"],
    )
    monkeypatch.setattr(full_manual_pricing, "model_note", lambda pricer, spec: spec.family)

    priced, _diagnostics = price_rows(
        [
            _row(
                "m1",
                "Will Cyle Larin score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"RSA vs CAN": {}},
        field_by_market={},
        player_prop_anchors=PlayerPropAnchors.empty(),
        player_no_field_base=0.28,
    )

    assert priced[0]["probability"] == 15
    assert priced[0]["manual_entry"] == "15%"
    assert priced[0]["manual_policy"] == "player_no_field_raw_scores_else_global_base"


def test_price_rows_ignores_global_scorer_prop_override_for_manual_sheet(monkeypatch, tmp_path):
    override_path = tmp_path / "player_overrides.yaml"
    override_path.write_text(
        "Folarin Balogun:\n"
        "  scorer_prop_prob: 0.10\n"
        "  expected_minutes: 20\n"
        "  goal_share: 0.30\n"
        "  involvement_share: 0.40\n"
        "  sot_per_match: 1.07\n"
        "  source: stale opponent-specific prop\n"
        "  team: United States\n",
        encoding="utf-8",
    )

    class FakeRates:
        source = "override"
        expected_minutes = 90

    class FakePlayers:
        def rates(self, player):
            return FakeRates()

        def is_sparse(self, player):
            return False

    class FakeGoals:
        lam_home = 1.4
        lam_away = 0.8

    class FakePricer:
        home_name = "United States"
        away_name = "Bosnia and Herzegovina"
        goals = FakeGoals()
        players = FakePlayers()
        scorer_props = {}

        def price(self, spec):
            return 0.37

        def _player_side(self, player, team_hint=None):
            return "home"

    seen_kwargs = {}

    def fake_build_pricer(match, kickoff, **kwargs):
        seen_kwargs.update(kwargs)
        return FakePricer()

    monkeypatch.setattr(full_manual_pricing, "PLAYER_OVERRIDES", override_path)
    monkeypatch.setattr(full_manual_pricing, "build_pricer", fake_build_pricer)
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})

    priced, _diagnostics = price_rows(
        [
            MarketRow(
                match_name="USA vs BIH",
                kickoff=datetime(2026, 7, 2, 0, tzinfo=timezone.utc),
                market_id="m1",
                question="Will Folarin Balogun score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"USA vs BIH": {"status": "not_posted"}},
        field_by_market={},
        player_prop_anchors=PlayerPropAnchors.empty(),
        player_no_field_base=0.28,
    )

    assert seen_kwargs["include_global_scorer_props"] is False
    assert seen_kwargs["include_global_expected_minutes"] is False
    assert priced[0]["probability"] == 37
    assert priced[0]["manual_entry"] == "37%"
    assert "GLOBAL_MINUTES_UNCONFIRMED" in priced[0]["flags"]
    assert "SCORER_PROP_RECHECK" in priced[0]["flags"]


def test_price_rows_keeps_confirmed_bench_player_model_without_field(monkeypatch):
    class FakeGoals:
        lam_home = 0.8
        lam_away = 1.6

    class FakePricer:
        home_name = "South Africa"
        away_name = "Canada"
        goals = FakeGoals()

        def price(self, spec):
            return 0.09

    monkeypatch.setattr(full_manual_pricing, "build_pricer", lambda match, kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr(full_manual_pricing, "referee_context", lambda match: {"status": "no_override", "card_factor": 1.0})
    monkeypatch.setattr(
        full_manual_pricing,
        "player_flags",
        lambda spec, pricer, lineup: ["CONFIRMED_BENCH"],
    )
    monkeypatch.setattr(full_manual_pricing, "model_note", lambda pricer, spec: spec.family)

    priced, _diagnostics = price_rows(
        [
            _row(
                "m1",
                "Will Iqraam Rayners have at least 1 shot on target in regulation (90 minutes + stoppage time)?",
            )
        ],
        lineups={"RSA vs CAN": {}},
        field_by_market={},
        player_prop_anchors=PlayerPropAnchors.empty(),
        player_no_field_base=0.28,
    )

    assert priced[0]["probability"] == 9
    assert priced[0]["manual_entry"] == "9%"
    assert priced[0]["manual_policy"] == "player_no_field_confirmed_bench_model"
