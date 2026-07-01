"""Player-prop models: anytime scorer, score-or-assist, shots on target.

Shots-on-target props (the model's weakest family in live play — see the
2026-06-13 calibration analysis) are built on three data-driven facts measured
from StatsBomb tournament data + football-data club data:

1. International tournament play SUPPRESSES attacking output: team SOT/match is
   3.70 (tournament) vs 4.41 (club), a discount of 0.839. Club-sourced player
   SOT rates are scaled by this before use.
2. Conditional on a player's true per-match rate, SOT counts are POISSON
   (within-player var/mean ~= 1; the population overdispersion of 1.29 is
   entirely *between* players). So P(>=k) uses Poisson, not a heavier model.
3. Player rate estimates are noisy, so each is SHRUNK toward the tournament
   forward prior (0.72 SOT/match). Leave-one-tournament-out validation picked
   shrink strength k~=1 (Brier 0.177 for P(>=1), vs 0.25 coin-flip and the
   0.305 we scored live before this fix).

Scorer probability ties into the match's goal model: a player owning share s
of a team expected to score lam goals scores with p = 1 - exp(-s * lam).
The scorer family was already well-calibrated live (Brier ~0.10) and is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import exp
from pathlib import Path

import pandas as pd
import yaml

from models.counts import p_ge

ROOT = Path(__file__).resolve().parent.parent.parent
OVERRIDES_PATH = ROOT / "data" / "player_overrides.yaml"

# Scorer-family priors (unchanged — this family is well-calibrated)
PRIOR_GOAL_SHARE = 0.12      # share of own team's goals
PRIOR_INVOLVEMENT = 0.20     # share of goals scored-or-assisted
SHRINK_APPS = 6.0            # appearances of prior weight (goal shares)

# SOT-family constants (all measured; see module docstring)
PRIOR_SOT_RATE = 0.72        # tournament forward mean SOT/match
SOT_SHRINK_APPS = 1.0        # validated shrink strength toward the prior
INTL_DISCOUNT = 0.839        # club SOT/match -> tournament SOT/match
SOT_ALPHA = 0.05             # near-Poisson (within-player overdispersion ~0)
TOURNAMENT_TEAM_SOT = 4.11   # player-SOT tempo normaliser = team_sot_mean / this.
# Must track counts.TOURNAMENT_INTENSITY['sot']: when that went 0.90 -> 1.00
# (2026-06-24), every team_sot_mean rose x1/0.90, so this was raised 3.70 -> 4.11
# (=3.70/0.90) to keep player-SOT props INVARIANT (verified: Gakpo 0.445 -> 0.445).
# Otherwise the team/total-SOT threshold recalibration silently inflates the
# player_sot_ge family (our #1, +125 RBP) by ~11%.
MAX_TEMPO = 1.6              # cap on the per-match tempo multiplier


@dataclass
class PlayerRates:
    goal_share: float
    involvement_share: float
    sot_per_match: float       # tournament-context, before match tempo/minutes
    expected_minutes: float    # for the SOT minutes factor
    source: str


def _normalize_name(name: str) -> str:
    """Spelling-robust key: ascii-folded, lowercased, hyphens out, tokens
    sorted ('Son Heung-min' == 'Heung-Min Son' == StatsBomb's variants)."""
    import unicodedata
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(sorted(folded.lower().replace("-", " ").split()))


class PlayerModel:
    def __init__(self, statsbomb_players: pd.DataFrame,
                 match_minutes: dict[str, float] | None = None,
                 use_global_expected_minutes: bool = True):
        # match_minutes: confirmed expected_minutes pulled from the posted XI for
        # the SINGLE match being priced (scripts/pull_lineups.py). Takes precedence
        # over the global override's expected_minutes so a stale benching cannot
        # ride forward into a later fixture.
        self._match_minutes = match_minutes or {}
        self._use_global_expected_minutes = use_global_expected_minutes
        self._history = (
            statsbomb_players.groupby("player")
            .agg(apps=("match_id", "count"), goals=("goals", "sum"),
                 assists=("assists", "sum"), sot=("sot", "sum"),
                 team=("team", "last"))
        )
        self._name_index = {_normalize_name(n): n for n in self._history.index}
        # team goals across each player's tournaments, for share computation
        team_goals = statsbomb_players.groupby("team")["goals"].sum()
        team_apps = statsbomb_players.groupby(["team", "match_id"]).size().groupby("team").size()
        self._team_goals_per_match = (team_goals / team_apps).to_dict()
        self._overrides = (
            yaml.safe_load(OVERRIDES_PATH.read_text()) or {}
            if OVERRIDES_PATH.exists() else {}
        )

    SPARSE_APPS = 8  # below this, StatsBomb history is too thin to trust over current form

    def resolve(self, player: str) -> str | None:
        """Map a question-text name to the StatsBomb history index, tolerating
        spelling/order variants. None when the player has no tournament history."""
        if player in self._history.index:
            return player
        normalized = _normalize_name(player)
        exact = self._name_index.get(normalized)
        if exact is not None:
            return exact
        query_tokens = set(normalized.split())
        if not query_tokens:
            return None
        candidates = []
        for key, name in self._name_index.items():
            if query_tokens.issubset(set(key.split())):
                candidates.append(name)
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            # Prefer the shortest unambiguous-looking expansion, e.g.
            # "Kylian Mbappe" -> "Kylian Mbappe Lottin".
            candidates = sorted(candidates, key=lambda n: (len(_normalize_name(n).split()), n))
            shortest = len(_normalize_name(candidates[0]).split())
            if len(candidates) == 1 or len(_normalize_name(candidates[1]).split()) > shortest:
                return candidates[0]
        return None

    def is_sparse(self, player: str) -> bool:
        """True if the player has no override and only thin StatsBomb history,
        so a role change since 2018-2024 could leave the rate stale. Such
        players should be backed by current club form (update_player_rates)."""
        if self._overrides.get(player):
            return False
        resolved = self.resolve(player)
        if resolved is None:
            return True
        return self._history.loc[resolved, "apps"] < self.SPARSE_APPS

    def team_of(self, player: str) -> str | None:
        """The player's team, for match side-mapping: an explicit override
        `team:` wins, else the StatsBomb history team, else None. Needed for
        players whose national team is absent from the StatsBomb data (e.g.
        Iraq, Jordan, New Zealand) — without a team the pricer falls back to the
        match-average goal rate, which over-rates an underdog's players."""
        override = self._overrides.get(player) or {}
        if override.get("team"):
            return str(override["team"])
        resolved = self.resolve(player)
        if resolved is not None:
            return self._history.loc[resolved, "team"]
        return None

    def rates(self, player: str) -> PlayerRates:
        override = self._overrides.get(player, {})
        resolved = self.resolve(player)
        history = self._history.loc[resolved] if resolved is not None else None

        # --- goal shares (unchanged; well-calibrated family) ---
        if history is not None and history["apps"] > 0:
            apps = float(history["apps"])
            team_lam = self._team_goals_per_match.get(history["team"], 1.2)
            expected_team_goals = max(team_lam * apps, 1e-6)
            goal_share = ((history["goals"] + PRIOR_GOAL_SHARE * SHRINK_APPS)
                          / (expected_team_goals + SHRINK_APPS))
            involvement = ((history["goals"] + history["assists"]
                            + PRIOR_INVOLVEMENT * SHRINK_APPS)
                           / (expected_team_goals + SHRINK_APPS))
            source = f"statsbomb({int(apps)} apps)"
        else:
            goal_share, involvement = PRIOR_GOAL_SHARE, PRIOR_INVOLVEMENT
            source = "prior"

        sot_rate, sot_source = self._sot_rate(override, history)

        # expected_minutes precedence: confirmed pulled XI > optional manual
        # override > 90. Manual workflows disable the global override so a past
        # fixture benching cannot silently carry into a future match.
        if player in self._match_minutes:
            minutes = float(self._match_minutes[player])
        elif self._use_global_expected_minutes:
            minutes = float(override.get("expected_minutes", 90))
        else:
            minutes = 90.0

        return PlayerRates(
            goal_share=override.get("goal_share", goal_share),
            involvement_share=override.get("involvement_share", involvement),
            sot_per_match=sot_rate,
            expected_minutes=minutes,
            source=sot_source if not override else "override",
        )

    def _sot_rate(self, override: dict, history) -> tuple[float, str]:
        """Tournament-context SOT/match, shrunk toward the forward prior.

        Source preference: a tournament-context override (sot_per_match_intl,
        no discount) > a club override (sot_per_match, x INTL_DISCOUNT) >
        StatsBomb tournament history > the prior. Whatever the source, the
        rate is shrunk toward PRIOR_SOT_RATE; club rates are discounted first."""
        if "sot_per_match_intl" in override:
            raw, n = float(override["sot_per_match_intl"]), 4.0
            src = "override-intl"
        elif "sot_per_match" in override:
            raw, n = float(override["sot_per_match"]) * INTL_DISCOUNT, 4.0
            src = "override-club"
        elif history is not None and history["apps"] > 0:
            raw, n = history["sot"] / history["apps"], float(history["apps"])
            src = "statsbomb"
        else:
            return PRIOR_SOT_RATE, "prior"
        shrunk = (n * raw + SOT_SHRINK_APPS * PRIOR_SOT_RATE) / (n + SOT_SHRINK_APPS)
        return shrunk, src

    # --- market families ----------------------------------------------------

    def p_scores(self, player: str, team_lam: float,
                 prop_prob: float | None = None) -> float:
        """Anytime scorer. A de-margined prop-market probability wins outright
        (markets beat models). Otherwise the model price scales by
        expected_minutes/90, so a benched/sub player is reduced automatically from
        the pulled lineup and a ruled-out player (0 min) -> 0 — no manual
        goal_share cut needed (that hand-cut is what went stale on 2026-06-26)."""
        if prop_prob is not None:
            return prop_prob
        r = self.rates(player)
        return 1 - exp(-r.goal_share * team_lam * r.expected_minutes / 90.0)

    def p_scores_or_assists(self, player: str, team_lam: float) -> float:
        r = self.rates(player)
        return 1 - exp(-r.involvement_share * team_lam * r.expected_minutes / 90.0)

    def p_sot_ge(self, player: str, threshold: int, team_sot_mean: float,
                 period: str = "match", h2_share: float = 0.57) -> float:
        r = self.rates(player)
        rate = r.sot_per_match
        # Match tempo: how much this team is expected to shoot vs the
        # tournament average (capped so a heavy favourite can't explode it).
        rate *= min(team_sot_mean / TOURNAMENT_TEAM_SOT, MAX_TEMPO)
        rate *= r.expected_minutes / 90.0          # minutes on the pitch
        if period == "2h":
            rate *= h2_share
        return p_ge(rate, SOT_ALPHA, threshold)
