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
# Must track counts.TOURNAMENT_INTENSITY['sot'] = 4.11 x sot_intensity. sot intensity is back to
# 1.00 (the 06-29 flat 1.40 was REVERTED — it over-corrected TEAM/player SOT; the total-SOT fix now
# lives in counts.TOTAL_SOT_SCALE, total-only), so this is 4.11 = 4.11 x 1.00. player ">=1 SOT" is
# thus exactly v4 again (the total-SOT correction does not touch it).
MAX_TEMPO = 1.6              # cap on the per-match tempo multiplier
# For the near-binary ">=1 SOT" threshold, team tempo barely moves whether a genuine
# shooter gets a shot ON TARGET (a starter shoots regardless of team strength). The
# linear tempo curve is too steep for this case: StatsBomb (314 matches, 234 shooter-
# matches) shows the model UNDER-rates underdog shooters (pred 0.23-0.44 vs realized
# 0.38-0.54) and OVER-rates favourites (0.71 vs 0.50). Compressing the tempo swing
# toward 1.0 lowers held-out Brier 0.275->0.255 at beta 0.5. Applies to >=1 SOT for a
# real shooter (rate>=SHOOTER_RATE) ONLY; 2+ SOT (volume) keeps the full tempo model,
# and calibrated low-rate defenders (realized 0.10) are left untouched. See
# scripts/backtest_sot1_tempo.py.
SOT1_TEMPO_BETA = 0.5        # 0=tempo-independent, 1=full tempo (old). 0.5 = conservative
SHOOTER_RATE = 0.5           # only compress for genuine shooters

# "Plays the entire match (regulation)" — starter full-90 share by position group,
# 5-sub-era tournaments only (wc2022/euro2024/copa2024/afcon2023, 199 matches,
# regulation-period subs only; scripts/derive_r16_families.py). Attackers are
# subbed most. Position comes from the player's override `position:` key; the
# conservative default for an unknown outfielder is MID.
FULL90_RATE = {"GK": 0.95, "DEF": 0.79, "MID": 0.575, "FWD": 0.282}


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
                 match_minutes: dict[str, float] | None = None):
        # match_minutes: confirmed expected_minutes pulled from the posted XI for
        # the SINGLE match being priced (scripts/pull_lineups.py). Takes precedence
        # over the global override's expected_minutes so a stale benching cannot
        # ride forward into a later fixture.
        self._match_minutes = match_minutes or {}
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
        key = _normalize_name(player)
        if key in self._name_index:
            return self._name_index[key]
        # Iberian/Portuguese full-legal-name fallback: the question's common name
        # ("Bernardo Silva", "Cristiano Ronaldo") is a strict token-subset of one
        # StatsBomb full name ("Bernardo Mota Veiga de Carvalho e Silva"). Accept
        # ONLY when exactly one history name contains every query token (unique)
        # and the query has >=2 tokens — otherwise stay on the prior rather than
        # risk a wrong match. Without this, elite scorers silently floor to 0.12.
        qtokens = set(key.split())
        if len(qtokens) >= 2:
            hits = [full for norm, full in self._name_index.items()
                    if qtokens <= set(norm.split())]
            if len(hits) == 1:
                return hits[0]
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

    def unrepresentative_scorer(self, player: str) -> bool:
        """is_sparse's blind spot: a player with ENOUGH apps to look trustworthy
        but ZERO goals in that history, so his goal_share collapses to the prior
        floor and a genuine scorer is silently under-rated (Cyle Larin: 8 apps,
        0 goals -> floored). Exempt only when goal_share is hand-set (then we
        trust the override). A research FLAG, not an auto-correction."""
        if "goal_share" in (self._overrides.get(player) or {}):
            return False
        resolved = self.resolve(player)
        if resolved is None:
            return False
        h = self._history.loc[resolved]
        return h["apps"] >= self.SPARSE_APPS and h["goals"] == 0

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

        # expected_minutes precedence: confirmed pulled XI > manual override > 90.
        # The pulled value wins so a stale benched override is auto-corrected the
        # moment the real lineup is read (the 06-26 Trezeguet failure mode).
        if player in self._match_minutes:
            minutes = float(self._match_minutes[player])
        else:
            minutes = float(override.get("expected_minutes", 90))

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
        tempo = min(team_sot_mean / TOURNAMENT_TEAM_SOT, MAX_TEMPO)
        # ">=1 SOT" saturates in tempo for a genuine shooter — compress the swing
        # toward 1.0 (calibration, not boldness; see SOT1_TEMPO_BETA above).
        if threshold == 1 and rate >= SHOOTER_RATE:
            tempo = 1.0 + SOT1_TEMPO_BETA * (tempo - 1.0)
        rate *= tempo
        rate *= r.expected_minutes / 90.0          # minutes on the pitch
        if period == "2h":
            rate *= h2_share
        return p_ge(rate, SOT_ALPHA, threshold)

    def p_plays_full(self, player: str) -> float:
        """P(player plays the ENTIRE match in regulation). Position-group base
        rate (FULL90_RATE, 5-sub era); a player who is not starting (confirmed
        or expected minutes < 90) cannot play the full match."""
        r = self.rates(player)
        if r.expected_minutes < 89:
            return 0.02
        pos = str((self._overrides.get(player) or {}).get("position", "MID")).upper()
        return FULL90_RATE.get(pos, FULL90_RATE["MID"])
