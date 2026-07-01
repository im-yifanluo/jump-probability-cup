import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from manual.player_prop_anchors import inspect_player_prop_anchor_csv  # noqa: E402
from manual.polymarket_direct import DirectMarketAnchor, PlayerPropCandidate  # noqa: E402
from player_prop_diagnostics import (  # noqa: E402
    OpenMarket,
    annotate_player_research_priority,
    anchor_csv_audit,
    anchor_csv_status,
    current_player_audit,
    current_prop_anchor_status,
    current_prop_anchor_summary,
    manual_entry_fields,
    render_polymarket_candidate_audit,
    render_anchor_csv_audit,
    selected_player_markets,
    write_anchor_template,
)
from questions.parser import parse  # noqa: E402


def test_write_anchor_template_outputs_exact_market_rows(tmp_path):
    path = tmp_path / "player_props.csv"
    rows = [
        {
            "market_id": "market-1",
            "match": "RSA vs CAN",
            "kickoff": datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
            "player": "Cyle Larin",
            "family": "player_scores",
            "probability": 15,
            "manual_entry": 15,
            "manual_policy": "player_no_field_raw_scores_else_global_base",
            "flags": "GLOBAL_MINUTES_UNCONFIRMED,LINEUP_NOT_POSTED",
            "question": "Will Cyle Larin score a goal?",
        }
    ]

    write_anchor_template(rows, path)

    with path.open(newline="", encoding="utf-8") as fh:
        out = list(csv.DictReader(fh))
    assert len(out) == 1
    assert out[0]["market_id"] == "market-1"
    assert out[0]["research_priority"] == ""
    assert out[0]["research_action"] == ""
    assert out[0]["match"] == "RSA vs CAN"
    assert out[0]["kickoff_utc"] == "2026-06-28T19:00+00:00"
    assert out[0]["model_probability"] == "15%"
    assert out[0]["manual_entry"] == "15%"
    assert out[0]["manual_policy"] == "player_no_field_raw_scores_else_global_base"
    assert out[0]["probability"] == ""
    assert out[0]["yes_american"] == ""
    assert out[0]["no_american"] == ""


def test_anchor_csv_audit_summarizes_usable_blank_invalid_and_unused(tmp_path):
    path = tmp_path / "player_props.csv"
    path.write_text(
        "market_id,probability,yes_american,no_american,prop_source,as_of\n"
        "m1,38%,,,DraftKings,2026-06-28T12:00Z\n"
        "m2,,,,,\n"
        "m3,,+120,,Book,2026-06-28T12:00Z\n"
        "unused,40%,,,Book,2026-06-28T12:00Z\n",
        encoding="utf-8",
    )
    current = [
        {
            "market_id": "m1",
            "match": "RSA vs CAN",
            "player": "Cyle Larin",
            "research_priority": "HIGH",
        },
        {
            "market_id": "m2",
            "match": "RSA vs CAN",
            "player": "Iqraam Rayners",
            "research_priority": "HIGH",
        },
        {
            "market_id": "m3",
            "match": "BRA vs JPN",
            "player": "Ayase Ueda",
            "research_priority": "MEDIUM",
        },
    ]
    inspection = inspect_player_prop_anchor_csv(str(path))

    summary = anchor_csv_audit(current, inspection)
    text = render_anchor_csv_audit(current, inspection)

    assert anchor_csv_status(summary) == "INCOMPLETE"
    assert summary["current_player_rows"] == 3
    assert summary["csv_rows"] == 4
    assert summary["usable"] == 2
    assert summary["blank"] == 1
    assert summary["invalid"] == 1
    assert summary["matched_usable"] == 1
    assert summary["current_missing_usable"] == 2
    assert summary["high_priority_missing_usable"] == 1
    assert summary["current_blank"] == 1
    assert summary["current_invalid"] == 1
    assert summary["unused_usable"] == 1
    assert "Current Prop Anchor CSV Audit" in text
    assert "player_prop_anchor_status: INCOMPLETE" in text
    assert "needs both yes_american and no_american" in text


def test_anchor_csv_status_distinguishes_partial_complete_and_unused():
    assert anchor_csv_status({}) == "NOT_SUPPLIED"
    assert anchor_csv_status({"current_player_rows": 0}) == "NO_CURRENT_PLAYER_PROPS"
    assert anchor_csv_status(
        {
            "current_player_rows": 2,
            "high_priority_missing_usable": 0,
            "current_blank": 0,
            "current_invalid": 0,
            "current_missing_usable": 1,
            "unused_usable": 0,
        }
    ) == "PARTIAL"
    assert anchor_csv_status(
        {
            "current_player_rows": 2,
            "high_priority_missing_usable": 0,
            "current_blank": 0,
            "current_invalid": 0,
            "current_missing_usable": 0,
            "unused_usable": 1,
        }
    ) == "COMPLETE_WITH_UNUSED_ROWS"
    assert anchor_csv_status(
        {
            "current_player_rows": 2,
            "high_priority_missing_usable": 0,
            "current_blank": 0,
            "current_invalid": 0,
            "current_missing_usable": 0,
            "unused_usable": 0,
        }
    ) == "COMPLETE"


def test_current_prop_anchor_status_summarizes_automatic_exact_coverage():
    rows = [
        {
            "market_id": "anchored",
            "research_priority": "MEDIUM",
            "current_prop_anchor": 40,
        },
        {
            "market_id": "high-missing",
            "research_priority": "HIGH",
            "current_prop_anchor": None,
        },
    ]

    summary = current_prop_anchor_summary(rows)

    assert summary["current_player_rows"] == 2
    assert summary["current_prop_anchor_rows"] == 1
    assert summary["current_prop_anchor_missing_rows"] == 1
    assert summary["high_priority_missing_current_prop_anchor"] == 1
    assert current_prop_anchor_status(summary, enabled=True) == "PARTIAL_HIGH_PRIORITY_MISSING"
    assert current_prop_anchor_status(summary, enabled=False) == "DISABLED"
    assert current_prop_anchor_status({"current_player_rows": 0}, enabled=True) == "NO_CURRENT_PLAYER_PROPS"
    assert current_prop_anchor_status(
        {
            "current_player_rows": 2,
            "current_prop_anchor_rows": 1,
            "current_prop_anchor_missing_rows": 1,
            "high_priority_missing_current_prop_anchor": 0,
        },
        enabled=True,
    ) == "PARTIAL_HIGH_PRIORITY_COVERED"


def test_manual_entry_fields_match_no_field_daily_policy():
    spec = parse(
        "Will Iqraam Rayners have at least 1 shot on target in regulation (90 minutes + stoppage time)?",
        teams={"South Africa", "Canada"},
    )

    fields = manual_entry_fields(
        spec,
        0.34,
        ["GLOBAL_MINUTES_UNCONFIRMED", "LINEUP_NOT_POSTED"],
        0.28,
    )

    assert fields["manual_entry"] == 28
    assert fields["manual_policy"] == "player_no_field_raw_scores_else_global_base"


def test_annotate_player_research_priority_uses_flags_and_family_leak():
    rows = [
        {
            "market_id": "high",
            "match": "RSA vs CAN",
            "kickoff": datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
            "question": "Will Cyle Larin score?",
            "famkey": "player_scores",
            "flags": "GLOBAL_MINUTES_UNCONFIRMED,LINEUP_NOT_POSTED",
        },
        {
            "market_id": "medium",
            "match": "RSA vs CAN",
            "kickoff": datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
            "question": "Will Iqraam Rayners have 1+ SOT?",
            "famkey": "player_sot_ge/sot",
            "flags": "LINEUP_NOT_POSTED",
        },
        {
            "market_id": "low",
            "match": "RSA vs CAN",
            "kickoff": datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
            "question": "Will Cyle Larin score?",
            "famkey": "player_scores",
            "flags": "OK",
        },
    ]
    family = [
        {"family": "player_sot_ge/sot", "n": 10, "skill_vs_field": -20.0},
        {"family": "player_scores", "n": 5, "skill_vs_field": 5.0},
    ]

    annotated = annotate_player_research_priority(rows, family)
    by_id = {row["market_id"]: row for row in annotated}

    assert by_id["high"]["research_priority"] == "HIGH"
    assert "lineup/minutes" in by_id["high"]["research_action"]
    assert by_id["medium"]["research_priority"] == "MEDIUM"
    assert by_id["medium"]["family_skill_vs_field_per_row"] == -2.0
    assert "family_skill_vs_field_per_row=-2.00" in by_id["medium"]["research_evidence"]
    assert by_id["low"]["research_priority"] == "LOW"


def test_selected_player_markets_excludes_started_rows_by_default():
    now = datetime(2026, 6, 28, 20, tzinfo=timezone.utc)
    rows = [
        OpenMarket(
            "RSA vs CAN",
            datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
            "started",
            "Will Cyle Larin score?",
        ),
        OpenMarket(
            "BRA vs JPN",
            datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
            "future",
            "Will Ayase Ueda have at least 1 shot on target?",
        ),
    ]

    selected = selected_player_markets(
        rows,
        now=now,
        tz=timezone.utc,
        local_date="2026-06-29",
    )

    assert [row.market_id for row in selected] == ["future"]


def test_selected_player_markets_can_include_started_for_audit():
    now = datetime(2026, 6, 28, 20, tzinfo=timezone.utc)
    rows = [
        OpenMarket(
            "RSA vs CAN",
            datetime(2026, 6, 28, 19, tzinfo=timezone.utc),
            "started",
            "Will Cyle Larin score?",
        )
    ]

    selected = selected_player_markets(
        rows,
        now=now,
        tz=timezone.utc,
        local_date="2026-06-28",
        include_started=True,
    )

    assert [row.market_id for row in selected] == ["started"]


def test_selected_player_markets_respects_all_open_and_match_filter():
    now = datetime(2026, 6, 28, 18, tzinfo=timezone.utc)
    rows = [
        OpenMarket(
            "BRA vs JPN",
            datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
            "brazil",
            "Will Ayase Ueda have at least 1 shot on target?",
        ),
        OpenMarket(
            "GER vs PAR",
            datetime(2026, 6, 29, 20, 30, tzinfo=timezone.utc),
            "germany",
            "Will Kai Havertz score?",
        ),
    ]

    selected = selected_player_markets(
        rows,
        now=now,
        tz=timezone.utc,
        local_date="2026-06-28",
        all_open=True,
        matches={"GER vs PAR"},
    )

    assert [row.market_id for row in selected] == ["germany"]


def test_current_player_audit_applies_exact_polymarket_anchor_and_clears_source_flags(monkeypatch):
    class FakeRates:
        source = "prior"
        expected_minutes = 90

    class FakePlayers:
        def rates(self, _player):
            return FakeRates()

        def resolve(self, player):
            return player

        def team_of(self, _player):
            return ""

        def is_sparse(self, _player):
            return False

    class FakePricer:
        players = FakePlayers()
        scorer_props = set()

        def _player_side(self, _player, _team_hint):
            return None

        def price(self, _spec):
            return 0.42

    def fake_anchor(_match_name, _spec, _events, *, min_volume):
        assert min_volume == 1000
        return DirectMarketAnchor(
            probability=0.27,
            source="Polymarket direct",
            event_title="Brazil vs. Japan",
            market_question="Matheus Cunha: 1+ goals",
            market_volume=1500,
            outcome="Yes",
        )

    monkeypatch.setattr("player_prop_diagnostics.build_pricer", lambda _match, _kickoff, **_kwargs: FakePricer())
    monkeypatch.setattr("player_prop_diagnostics.pull_lineup", lambda *_args, **_kwargs: {"status": "not_posted"})
    monkeypatch.setattr("player_prop_diagnostics.player_prop_anchor_for_market", fake_anchor)

    rows = [
        OpenMarket(
            "BRA vs JPN",
            datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
            "market-1",
            "Will Matheus Cunha score a goal (excluding own goals) in regulation (90 minutes + stoppage time)?",
        )
    ]

    audit = current_player_audit(rows, player_no_field_base=0.28, polymarket_events=[{"id": "event"}])

    assert audit[0]["raw_model_probability"] == 42
    assert audit[0]["probability"] == 27
    assert audit[0]["current_prop_anchor"] == 27
    assert "CURRENT_PROP_MARKET_ANCHOR" in audit[0]["flags"]
    assert "PRIOR_ONLY" not in audit[0]["flags"]
    assert "TEAM_UNKNOWN" not in audit[0]["flags"]


def test_render_polymarket_candidate_audit_lists_non_promoted_near_misses():
    rows = [
        {
            "kickoff": datetime(2026, 6, 29, 17, tzinfo=timezone.utc),
            "match": "BRA vs JPN",
            "market_id": "market-1",
            "player": "Ayase Ueda",
            "polymarket_prop_candidates": [
                PlayerPropCandidate(
                    status="below_volume",
                    reason="matching prop but volume $999 < $1,000",
                    probability=0.295,
                    event_title="Brazil vs. Japan - Player Props",
                    market_question="Ayase Ueda: 1+ shots on target",
                    market_volume=999,
                    expected_prop="1+ shots on target",
                    actual_prop="1+ shots on target",
                ),
                PlayerPropCandidate(
                    status="prop_mismatch",
                    reason="expected 1+ shots on target; saw 2+ shots on target",
                    probability=0.135,
                    event_title="Brazil vs. Japan - Player Props",
                    market_question="Ayase Ueda: 2+ shots on target",
                    market_volume=5000,
                    expected_prop="1+ shots on target",
                    actual_prop="2+ shots on target",
                ),
            ],
        }
    ]

    text = "\n".join(render_polymarket_candidate_audit(rows))

    assert "Polymarket Player Prop Candidate Audit" in text
    assert "same_player_candidates: 2" in text
    assert "below_volume=1" in text
    assert "prop_mismatch=1" in text
    assert "non-eligible candidates are logged for source review only" in text
    assert "Ayase Ueda: 1+ shots on target" in text
