"""Price every market family by counting outcomes over a Monte Carlo SimResult.

Mirrors MatchPricer's family routing, but each probability is the fraction of
simulated matches in which the market's condition held — so all markets are
mutually consistent and game-state correlations are captured. Player props are
not simulated (no player-level dynamics); they delegate to the analytic
PlayerModel, exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from probcup.models.players import PlayerModel
from simulator import SimResult
from probcup.questions.parser import MarketSpec


@dataclass
class SimMatchPricer:
    home_name: str
    away_name: str
    result: SimResult
    players: PlayerModel
    counts: "CountPricer"                       # for player-prop SOT tempo
    goals: "ScorelineModel"                     # for player-prop team lambda
    scorer_props: dict[str, float] = field(default_factory=dict)
    player_teams: dict[str, str] = field(default_factory=dict)

    def _side(self, team: str) -> str:
        if team == self.home_name:
            return "home"
        if team == self.away_name:
            return "away"
        raise KeyError(f"{team!r} not in {self.home_name} vs {self.away_name}")

    def _other(self, side: str) -> str:
        return "away" if side == "home" else "home"

    @staticmethod
    def _p(mask: np.ndarray) -> float:
        return float(np.mean(mask))

    def price(self, spec: MarketSpec) -> float:
        r = self.result
        f = spec.family
        if f in _PLAYER_FAMILIES:
            return self._price_player(spec)

        if f == "team_win":
            s = self._side(spec.team)
            return self._p(r.goals(s, "match") > r.goals(self._other(s), "match"))
        if f == "ht_tied":
            return self._p(r.goals("home", "1h") == r.goals("away", "1h"))
        if f == "ht_team_winning":
            s = self._side(spec.team)
            return self._p(r.goals(s, "1h") > r.goals(self._other(s), "1h"))
        if f == "total_goals":
            tot = r.goals("home", spec.period) + r.goals("away", spec.period)
            return self._p(tot >= spec.threshold if spec.direction == ">=" else tot <= spec.threshold)
        if f == "h2_more_goals_than_h1":
            h1 = r.goals("home", "1h") + r.goals("away", "1h")
            h2 = r.goals("home", "2h") + r.goals("away", "2h")
            return self._p(h2 > h1)
        if f == "btts_and_total_ge":
            h, a = r.goals("home", "match"), r.goals("away", "match")
            return self._p((h >= 1) & (a >= 1) & (h + a >= spec.threshold))
        if f == "team_goals_ge":
            return self._p(r.goals(self._side(spec.team), "match") >= spec.threshold)
        if f == "team_scores_in_half":
            return self._p(r.goals(self._side(spec.team), spec.period) >= 1)
        if f == "team_more_goals_2h":
            s = self._side(spec.team)
            return self._p(r.goals(s, "2h") > r.goals(self._other(s), "2h"))
        if f == "team_first_goal_2h":
            code = 1 if self._side(spec.team) == "home" else 2
            return self._p(r.first_scorer_2h == code)
        if f == "first_goal_and_other_scores_2h":
            s = self._side(spec.team)
            code = 1 if s == "home" else 2
            return self._p((r.first_scorer == code) & (r.goals(self._other(s), "2h") >= 1))
        if f == "penalty_awarded":
            return self._p(r.penalty)
        if f == "penalty_or_red":
            return self._p(r.penalty | r.red)
        if f == "total_count_ge":
            tot = r.count(spec.stat, "home", spec.period) + r.count(spec.stat, "away", spec.period)
            return self._p(tot >= spec.threshold)
        if f == "team_count_ge":
            return self._p(r.count(spec.stat, self._side(spec.team), spec.period) >= spec.threshold)
        if f == "both_teams_count_ge":
            return self._p((r.count(spec.stat, "home", spec.period) >= spec.threshold)
                           & (r.count(spec.stat, "away", spec.period) >= spec.threshold))
        if f == "team_more_count_than":
            s = self._side(spec.team)
            return self._p(r.count(spec.stat, s, spec.period) > r.count(spec.stat, self._other(s), spec.period))
        raise ValueError(f"sim pricer: no rule for family {spec.family!r}")

    # player props: delegate to the analytic model (unchanged) ------------
    def _player_side(self, player: str) -> str | None:
        team = self.player_teams.get(player)
        if team is None:
            resolved = self.players.resolve(player)
            if resolved is not None:
                team = self.players._history.loc[resolved, "team"]
        return self._side(team) if team in (self.home_name, self.away_name) else None

    def _team_lam(self, side: str | None) -> float:
        if side == "home":
            return self.goals.lam_home
        if side == "away":
            return self.goals.lam_away
        return (self.goals.lam_home + self.goals.lam_away) / 2

    def _price_player(self, spec: MarketSpec) -> float:
        side = self._player_side(spec.player)
        if spec.family == "player_scores":
            return self.players.p_scores(spec.player, self._team_lam(side),
                                         prop_prob=self.scorer_props.get(spec.player))
        if spec.family == "player_scores_or_assists":
            return self.players.p_scores_or_assists(spec.player, self._team_lam(side))
        # player_sot_ge
        team_sot = (self.counts.mean("sot", side, "match") if side
                    else (self.counts.mean("sot", "home", "match")
                          + self.counts.mean("sot", "away", "match")) / 2)
        h2 = self.counts.aggregates["stats"]["sot"]["h2_share"]
        return self.players.p_sot_ge(spec.player, spec.threshold, team_sot, spec.period, h2)


_PLAYER_FAMILIES = {"player_scores", "player_scores_or_assists", "player_sot_ge"}


def build_sim_pricer(match_name: str, kickoff, n_sims: int = 40000):
    """Monte Carlo pricer for one match — same odds-anchoring and count setup
    as build_pricer, but derived markets come from a game-state-aware match
    simulation (captures leading-team-cedes-possession dynamics)."""
    from probcup.models.counts import CountPricer, load_count_models, tournament_aggregates
    from probcup.models.goals import ScorelineModel
    from probcup.pipeline.build import get_anchors
    from probcup.pipeline.overrides import referee_card_factor, scorer_props
    from probcup.questions.teams import match_teams
    from probcup.sources.statsbomb import build_aggregates
    from simulator import MatchSimulator

    home_full, away_full = match_teams(match_name)
    p_home, p_draw, total = get_anchors(match_name, kickoff)
    params = load_count_models()
    team_halves, sb_players = build_aggregates()
    aggregates = tournament_aggregates(team_halves)
    h2_goals = aggregates["stats"]["goals"]["h2_share"]
    goals = ScorelineModel.from_market(p_home, p_draw, total, h2_share=h2_goals)
    counts = CountPricer(goals.lam_home, goals.lam_away, params, aggregates,
                         card_factor=referee_card_factor(match_name))
    result = MatchSimulator(goals, counts, n_sims=n_sims).run()
    return SimMatchPricer(home_full, away_full, result, PlayerModel(sb_players),
                          counts, goals, scorer_props=scorer_props())
