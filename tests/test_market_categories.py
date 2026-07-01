import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket
from count_policy_backtest import is_count
from manual.policy import is_special_market
from questions.parser import MarketSpec, parse
from strategy_research import live_category
from tournament_target_audit import _category
from manual_policy_backtest import category


def history_row(question: str) -> HistoryMarket:
    return HistoryMarket(
        version=2,
        order=1,
        match="BRA vs JPN",
        question=question,
        you=0.50,
        crowd=0.50,
        outcome=1,
        official_rbp=0.0,
    )


def test_total_shots_are_special_not_count_shots_in_reports():
    question = "Will there be 22 or more total shots (on and off target) in regulation (90 minutes + stoppage time)?"
    spec = parse(question, teams={"Brazil", "Japan"})

    assert spec.family == "total_count_ge"
    assert spec.stat == "shots"
    assert is_special_market(spec)
    assert _category(spec) == "special"
    assert category(history_row(question)) == "special"
    assert not is_count(history_row(question))
    assert live_category({"family": spec.family, "stat": spec.stat}) == "special_markets"


def test_corners_before_hydration_are_special_not_count_corners_in_reports():
    question = "Will 2 or more corner kicks be taken before the first hydration break?"
    spec = parse(question, teams={"Brazil", "Japan"})

    assert spec.family == "total_count_before_hydration"
    assert spec.stat == "corners"
    assert is_special_market(spec)
    assert _category(spec) == "special"
    assert category(history_row(question)) == "special"
    assert not is_count(history_row(question))
    assert live_category({"family": spec.family, "stat": spec.stat}) == "special_markets"


def test_regular_total_sot_remains_count_market():
    spec = MarketSpec("total_count_ge", stat="sot", threshold=8)

    assert not is_special_market(spec)
    assert _category(spec) == "count_sot"


def test_timed_offside_and_any_player_sot_are_not_count_backtest_rows():
    offside = history_row("Will either team be ruled offside before the first hydration break?")
    any_player = history_row(
        "Will any player record 2 or more shots on target in regulation (90 minutes + stoppage time)?"
    )

    assert not is_count(offside)
    assert not is_count(any_player)
