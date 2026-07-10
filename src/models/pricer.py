"""MatchPricer: routes every MarketSpec family to its model.

One MatchPricer per fixture. It owns the odds-anchored ScorelineModel, the
CountPricer and the PlayerModel, and knows which question-name team is the
"home" (first-listed) side. price(spec) returns the probability for any
family the parser can produce — guaranteeing full market coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.counts import (CountPricer, q_brace, p_sub_goal, SUB_BEFORE_HT_RATE,
                            SUB_AT_HT_RATE, p_goal_method, _OWN_GOAL, _HEADER_GOAL,
                            _OUTSIDE_GOAL)
from models.goals import ScorelineModel
from models.players import PlayerModel
from questions.parser import MarketSpec
from questions.teams import names_match


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

# NOTE (2026-06-14): a broad TOURNAMENT_WEIGHT_CAP shrinkage was tried and
# REVERTED. It was validated only on ~69 live markets, which were confounded
# with an unlucky favourite-underperformance run. The unbiased 314-match
# StatsBomb test showed shrinking the head-to-head / SOT families HURTS (they're
# calibrated), and cards already carry the right weight (0.40). Lesson: validate
# calibration changes on the large sample, not the recent live run.


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
        # Tolerant match: exact, alias (names_match), and the leading-article case
        # ("the Netherlands"/"the United States" vs "Netherlands"/"United States").
        def _strip(x: str) -> str:
            return x[4:] if x.lower().startswith("the ") else x
        t = _strip(team)
        for cand, side in ((self.home_name, "home"), (self.away_name, "away")):
            if team == cand or names_match(cand, team) or names_match(_strip(cand), t):
                return side
        raise KeyError(f"{team!r} is not in {self.home_name} vs {self.away_name}")

    def _team_lam(self, side: str) -> float:
        return self.goals.lam_home if side == "home" else self.goals.lam_away

    def _player_side(self, player: str) -> str | None:
        """Resolve a player's side: explicit MatchPricer mapping, then the
        PlayerModel's team (override `team:` or StatsBomb history). Returns None
        only when the team is genuinely unknown — callers then use match-average
        tempo. Team names are compared tolerantly so an override `team: Iraq`
        matches the question-space name without an exact-string requirement."""
        team = self.player_teams.get(player) or self.players.team_of(player)
        if team is None:
            return None
        for cand, side in ((self.home_name, "home"), (self.away_name, "away")):
            if team == cand or names_match(cand, team):
                return side
        return None

    def _player_team_lam(self, player: str) -> float:
        side = self._player_side(player)
        if side is None:
            return (self.goals.lam_home + self.goals.lam_away) / 2
        return self._team_lam(side)

    def _p_any_braces(self) -> float:
        """P(>=1 player scores 2+) = sum over the total-goals pmf of P(brace | G)."""
        return float(sum(p * q_brace(G) for G, p in enumerate(self.goals.total_goals_pmf())))

    def _p_substitute_scores(self) -> float:
        """P(>=1 substitute scores) = sum over the total-goals pmf of P(sub goal | G)."""
        return float(sum(p * p_sub_goal(G) for G, p in enumerate(self.goals.total_goals_pmf())))

    def _p_goal_method(self, table: tuple) -> float:
        """P(>=1 goal of a given method: own / header / outside-box) = sum over the
        total-goals pmf of P(>=1 method goal | G). Same machinery as substitute_scores."""
        return float(sum(p * p_goal_method(table, G)
                         for G, p in enumerate(self.goals.total_goals_pmf())))

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
            case "clean_sheet":
                return g.p_clean_sheet(self._side(spec.team))
            case "advance":
                return g.p_advance(self._side(spec.team))
            case "shootout_decided":
                return g.p_shootout()
            case "total_goals_exact":
                return g.p_total_goals_exact(spec.threshold)
            case "goal_each_half":
                return g.p_goal_each_half()
            case "hold_lead":
                return g.p_hold_lead(self._side(spec.team))
            case "cards_gt_goals":
                # P(total cards > total goals): joint over the two independent pmfs
                cards = c.total_cards_pmf()
                goals_pmf = g.total_goals_pmf()
                return float(sum(pg * cards[gl + 1:].sum()
                                 for gl, pg in enumerate(goals_pmf) if gl + 1 < len(cards)))
            case "plays_full_match":
                return self.players.p_plays_full(spec.player)
            case "extra_time":
                # a KO regulation draw sends the tie to extra time
                return g.p_draw()
            case "same_goals_halves":
                return g.p_same_goals_halves()
            case "total_goals_odd":
                return g.p_total_goals_odd()
            case "decided_by_one_goal":
                return g.p_margin_exact(1)
            case "first_goal_in_2h":
                return g.p_first_goal_in_2h()
            case "either_wins_both_halves":
                return g.p_either_wins_both_halves()
            case "card_each_half":
                return c.p_card_each_half()
            case "first_goal_not_players":
                # P(first goal by someone outside the named stars). Each excluded
                # player's scoring intensity λ = -ln(1 - anytime_prob), preferring
                # the de-margined market prop when we have one, else the model.
                from math import log
                lam_sum = 0.0
                for nm in spec.params.get("exclude", []):
                    pp = self.scorer_props.get(nm)
                    if pp is None:
                        pp = self.players.p_scores(nm, self._player_team_lam(nm))
                    pp = min(max(float(pp), 0.0), 0.99)
                    lam_sum += -log(1 - pp)
                return g.p_first_goal_by_other(lam_sum)
            case "first_card_before_goal":
                # race of two Poisson processes over one match: P(first card
                # precedes the first goal AND occurs). Uses the card-scaled totals.
                from models.counts import TOTAL_CARDS_SCALE
                lc = (c.mean("cards", "home", "match") + c.mean("cards", "away", "match")) * TOTAL_CARDS_SCALE
                lg = g.lam_home + g.lam_away
                import math
                return float(lc / (lc + lg) * (1 - math.exp(-(lc + lg))))
            case "gk_saves_ge":
                # keeper saves ~ opponent SOT minus opponent goals (NB tail)
                side = self._player_side(spec.player)
                if side is None:
                    mo = ((c.mean("sot", "home", "match") - g.lam_home)
                          + (c.mean("sot", "away", "match") - g.lam_away)) / 2
                else:
                    opp = "away" if side == "home" else "home"
                    mo = c.mean("sot", opp, "match") - (g.lam_away if opp == "away" else g.lam_home)
                from models.counts import p_ge
                return p_ge(max(mo, 0.3), c.alpha("sot"), spec.threshold)
            case "total_subs_ge":
                return c.p_total_subs_ge(spec.threshold)
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
            case "btts":
                return g.p_btts()
            case "match_drawn":
                return g.p_draw()
            case "team_first_goal_game":
                return g.p_first_goal_game(self._side(spec.team))
            case "team_win_by_ge":
                return g.p_win_by_ge(self._side(spec.team), spec.threshold)
            case "team_scores_both_halves":
                return g.p_team_scores_both_halves(self._side(spec.team))
            # events
            case "penalty_awarded":
                return c.p_penalty()
            case "penalty_or_red":
                return c.p_penalty_or_red()
            case "red_card":
                return c.p_red()
            case "timing_event":
                return c.p_total_in_window(spec.stat, spec.params["window"], spec.threshold)
            case "any_player_braces":
                return self._p_any_braces()
            case "any_player_sot_ge":
                return c.p_any_player_2sot()
            case "team_any_player_sot_ge":
                if spec.threshold != 2:
                    raise ValueError(f"team_any_player_sot_ge only models threshold 2, got {spec.threshold}")
                return c.p_team_any_player_2sot(self._side(spec.team))
            case "total_shots_ge":
                return c.p_total_shots_ge(spec.threshold)
            case "substitute_scores":
                return self._p_substitute_scores()
            case "own_goal":
                return self._p_goal_method(_OWN_GOAL)
            case "header_goal":
                return self._p_goal_method(_HEADER_GOAL)
            case "outside_box_goal":
                return self._p_goal_method(_OUTSIDE_GOAL)
            case "substitution_before_ht":
                return SUB_BEFORE_HT_RATE
            case "substitution_at_ht":
                return SUB_AT_HT_RATE
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
