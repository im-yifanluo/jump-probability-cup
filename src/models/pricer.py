"""MatchPricer: routes every MarketSpec family to its model.

One MatchPricer per fixture. It owns the odds-anchored ScorelineModel, the
CountPricer and the PlayerModel, and knows which question-name team is the
"home" (first-listed) side. price(spec) returns the probability for any
family the parser can produce — guaranteeing full market coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.counts import CountPricer
from models.goals import ScorelineModel
from models.players import PlayerModel
from questions.parser import MarketSpec


# Backtest-derived weight on the model vs the structural baseline, keyed by
# (family, stat). Families absent here use the model fully (w=1). See
# data/family_blend.json + notebooks/02_backtest for provenance.
BLEND_WEIGHTS = {
    ("total_count_ge", "cards"): 0.40,
    ("team_count_ge", "cards"): 0.40,     # borrows the total-cards evidence
    ("total_count_ge", "corners"): 0.94,
    ("team_more_count_than", "fouls"): 0.82,
    ("team_more_count_than", "cards"): 0.95,
    ("team_more_count_than", "corners"): 0.97,
    ("h2_more_goals_than_h1", "goals"): 0.67,
}


@dataclass
class MatchPricer:
    home_name: str               # first-listed team, question-name space
    away_name: str
    goals: ScorelineModel
    counts: CountPricer
    players: PlayerModel
    scorer_props: dict[str, float] = field(default_factory=dict)  # de-margined market prices
    player_teams: dict[str, str] = field(default_factory=dict)    # player -> team name
    baseline: "MatchPricer | None" = None  # same models at tournament-average lambdas

    def _side(self, team: str) -> str:
        if team == self.home_name:
            return "home"
        if team == self.away_name:
            return "away"
        raise KeyError(f"{team!r} is not in {self.home_name} vs {self.away_name}")

    def _team_lam(self, side: str) -> float:
        return self.goals.lam_home if side == "home" else self.goals.lam_away

    def _player_side(self, player: str) -> str | None:
        """Resolve a player's side: explicit mapping, else StatsBomb history.
        Returns None when unknown — callers then use match-average tempo,
        which is the honest stance for an unmapped squad player."""
        team = self.player_teams.get(player)
        if team is None:
            resolved = self.players.resolve(player)
            if resolved is not None:
                team = self.players._history.loc[resolved, "team"]
        if team in (self.home_name, self.away_name):
            return self._side(team)
        return None

    def _player_team_lam(self, player: str) -> float:
        side = self._player_side(player)
        if side is None:
            return (self.goals.lam_home + self.goals.lam_away) / 2
        return self._team_lam(side)

    # ------------------------------------------------------------------ #

    def price(self, spec: MarketSpec) -> float:
        """Blended price: model output shrunk toward the structural baseline
        (same machinery at tournament-average goal rates) by the
        backtest-derived weight for this family/stat."""
        p = self._price_raw(spec)
        w = BLEND_WEIGHTS.get((spec.family, spec.stat), 1.0)
        if w >= 1.0 or self.baseline is None:
            return p
        p_base = self.baseline._price_raw(spec)
        return p_base + w * (p - p_base)

    def _price_raw(self, spec: MarketSpec) -> float:
        g, c = self.goals, self.counts
        match spec.family:
            # outcome / state
            case "team_win":
                return g.p_team_win(self._side(spec.team))
            case "ht_tied":
                return g.p_ht_tied()
            case "ht_team_winning":
                return g.p_ht_winning(self._side(spec.team))
            # goals
            case "total_goals":
                if spec.period == "match":
                    return g.p_total_goals(spec.threshold, spec.direction)
                return g.p_total_goals_half_ge(spec.threshold, spec.period)
            case "h2_more_goals_than_h1":
                return g.p_h2_more_goals_than_h1()
            case "btts_and_total_ge":
                return g.p_btts_and_total_ge(spec.threshold)
            case "team_goals_ge":
                return g.p_team_goals_ge(self._side(spec.team), spec.threshold)
            case "team_scores_in_half":
                return g.p_team_scores_in_half(self._side(spec.team), spec.period)
            case "team_more_goals_2h":
                return g.p_team_more_goals_2h(self._side(spec.team))
            case "team_first_goal_2h":
                return g.p_first_goal_2h(self._side(spec.team))
            case "first_goal_and_other_scores_2h":
                return g.p_first_goal_game_and_other_scores_2h(
                    self._side(spec.team), self._side(spec.opponent))
            # events
            case "penalty_awarded":
                return c.p_penalty()
            case "penalty_or_red":
                return c.p_penalty_or_red()
            # counts
            case "total_count_ge":
                return c.p_total_count_ge(spec.stat, spec.threshold, spec.period)
            case "team_count_ge":
                return c.p_team_count_ge(
                    spec.stat, self._side(spec.team), spec.threshold, spec.period)
            case "both_teams_count_ge":
                return c.p_both_teams_ge(spec.stat, spec.threshold, spec.period)
            case "team_more_count_than":
                return c.p_more_than(spec.stat, self._side(spec.team), spec.period)
            # player props
            case "player_scores":
                return self.players.p_scores(
                    spec.player, self._player_team_lam(spec.player),
                    prop_prob=self.scorer_props.get(spec.player))
            case "player_scores_or_assists":
                return self.players.p_scores_or_assists(
                    spec.player, self._player_team_lam(spec.player))
            case "player_sot_ge":
                side = self._player_side(spec.player)
                team_sot = (c.mean("sot", side, "match") if side is not None else
                            (c.mean("sot", "home", "match") + c.mean("sot", "away", "match")) / 2)
                h2 = c.aggregates["stats"]["sot"]["h2_share"]
                return self.players.p_sot_ge(
                    spec.player, spec.threshold, team_sot, spec.period, h2)
        raise ValueError(f"no pricing rule for family {spec.family!r}")
