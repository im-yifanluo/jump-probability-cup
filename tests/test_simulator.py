"""The Monte Carlo simulator must stay anchored to the odds and the analytic
model: simulated 1X2/totals reproduce the market, and derived-market prices
match the analytic pricer within Monte Carlo noise (the simulator only adds
game-state redistribution, it must not shift marginals)."""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from models.counts import CountPricer, load_count_models, tournament_aggregates
from models.goals import ScorelineModel
from models.players import PlayerModel
from models.pricer import MatchPricer
from models.sim_pricer import SimMatchPricer
from models.simulator import MatchSimulator
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES
from wcdata.statsbomb import build_aggregates

_th, _players_df = build_aggregates()
_agg = tournament_aggregates(_th)
_params = load_count_models()


def _pricers(p_home, p_draw, total):
    goals = ScorelineModel.from_market(p_home, p_draw, total)
    counts = CountPricer(goals.lam_home, goals.lam_away, _params, _agg)
    players = PlayerModel(_players_df)
    analytic = MatchPricer("Home", "Away", goals, counts, players)
    result = MatchSimulator(goals, counts, n_sims=40000, seed=1).run()
    sim = SimMatchPricer("Home", "Away", result, players, counts, goals)
    return analytic, sim, result


def test_marginals_match_odds():
    _, _, r = _pricers(0.55, 0.25, 2.6)
    p_home_win = np.mean(r.home_goals > r.away_goals)
    p_draw = np.mean(r.home_goals == r.away_goals)
    assert abs(p_home_win - 0.55) < 0.03, p_home_win
    assert abs(p_draw - 0.25) < 0.03, p_draw
    assert abs((r.home_goals + r.away_goals).mean() - 2.6) < 0.1


def test_sim_matches_analytic_within_noise():
    analytic, sim, _ = _pricers(0.50, 0.27, 2.7)
    families = [
        "Will Home win the match?",
        # NB: TOTAL count markets (SOT, cards, offsides) are intentionally EXCLUDED —
        # the analytic pricer applies total-only platform-level corrections
        # (counts.TOTAL_SOT_SCALE / TOTAL_CARDS_SCALE / TOTAL_OFFSIDES_SCALE, promoted
        # 2026-07-02) which the Monte-Carlo sim (a non-production cross-check) does not
        # model, so the two legitimately diverge on those totals.
        "At halftime, will the match be tied?",
        "Will both teams score AND the match have 3 or more total goals?",
    ]
    for q in families:
        spec = parse(q, teams=ALL_TEAM_NAMES | {"Home", "Away"})
        diff = abs(analytic.price(spec) - sim.price(spec))
        assert diff < 0.05, f"{q}: analytic vs sim differ by {diff:.3f}"
