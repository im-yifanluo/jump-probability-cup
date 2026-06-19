"""Count models: corners, cards, shots on target, fouls, offsides.

Approach (transferable by construction): a team's expected count in a match is
regressed on the *odds-implied goal rates* (its own attacking rate, the
opponent's rate, home flag) using ~15 years of club matches:

    log E[count] = b0 + b1·log(lam_team) + b2·log(lam_opp) + b3·is_home

Because the features come from the betting market rather than team identity,
the fitted model applies directly to any World Cup fixture once we have its
odds. Overdispersion is captured with a negative binomial (moment-estimated
alpha); head-to-head comparisons enumerate the two teams' NB distributions
with ties resolving to "No".

Stats absent from club data (offsides) and event flags (penalty, red card) use
international-tournament rates measured from StatsBomb event data, as do the
second-half shares of each stat.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent.parent
PARAMS_CACHE = ROOT / "data" / "count_models.json"

REGRESSION_STATS = ("corners", "cards", "sot", "fouls")
MAX_COUNT = 40  # NB grid truncation for enumerations
OFFSIDE_TEMPO_EXPONENT = 0.18  # measured offside-vs-attack elasticity (StatsBomb)

# Count regressions are fit on CLUB data; international tournaments have
# systematically different event rates. These are tournament_mean / club_mean
# measured on 116k club + 314 StatsBomb tournament team-matches. Applied only
# when pricing tournament fixtures (CountPricer.tournament=True), never in the
# club backtest. Fouls > 1: tournament play is more cagey/physical.
COUNT_INTL_DISCOUNT = {"cards": 0.849, "corners": 0.872, "sot": 0.839, "fouls": 1.104}


def nb_pmf_grid(mean: float, alpha: float, n: int = MAX_COUNT) -> np.ndarray:
    """Negative binomial pmf over 0..n with Var = mean(1 + alpha*mean).
    alpha -> 0 degrades gracefully to Poisson."""
    if alpha < 1e-6:
        pmf = stats.poisson.pmf(np.arange(n + 1), mean)
    else:
        size = 1.0 / alpha
        p = size / (size + mean)
        pmf = stats.nbinom.pmf(np.arange(n + 1), size, p)
    return pmf / pmf.sum()


def p_ge(mean: float, alpha: float, threshold: int) -> float:
    return float(nb_pmf_grid(mean, alpha)[threshold:].sum())


def p_strictly_more(mean_a: float, mean_b: float, alpha: float) -> float:
    """P(A > B) for independent NB counts — ties count as 'No'."""
    a, b = nb_pmf_grid(mean_a, alpha), nb_pmf_grid(mean_b, alpha)
    return float(sum(a[i] * b[:i].sum() for i in range(1, len(a))))


# --------------------------------------------------------------------------- #
# Fitting on club data
# --------------------------------------------------------------------------- #

def _moment_alpha(y: np.ndarray, mu: np.ndarray) -> float:
    """Cameron-Trivedi moment estimator for NB2 dispersion."""
    num = ((y - mu) ** 2 - mu).sum()
    den = (mu ** 2).sum()
    return max(0.0, num / den)


def fit_count_models(matches: pd.DataFrame) -> dict:
    """Fit per-stat Poisson GLMs on club matches that carry odds-implied
    lambdas (columns lam_home/lam_away, added by the backtest data prep).
    Returns {stat: {coef: [...], alpha: float}} and caches to disk."""
    rows = []
    for side, opp in (("home", "away"), ("away", "home")):
        side_df = pd.DataFrame({
            "log_lam_team": np.log(matches[f"lam_{side}"]),
            "log_lam_opp": np.log(matches[f"lam_{opp}"]),
            "is_home": 1.0 if side == "home" else 0.0,
        })
        for stat in REGRESSION_STATS:
            side_df[stat] = matches[f"{side}_{stat}"].to_numpy()
        rows.append(side_df)
    long = pd.concat(rows, ignore_index=True).dropna()

    X = sm.add_constant(long[["log_lam_team", "log_lam_opp", "is_home"]])
    params = {}
    for stat in REGRESSION_STATS:
        glm = sm.GLM(long[stat], X, family=sm.families.Poisson()).fit()
        mu = glm.fittedvalues.to_numpy()
        params[stat] = {
            "coef": dict(glm.params),
            "alpha": _moment_alpha(long[stat].to_numpy(), mu),
        }
    PARAMS_CACHE.write_text(json.dumps(params, indent=2))
    return params


def load_count_models() -> dict:
    return json.loads(PARAMS_CACHE.read_text())


# --------------------------------------------------------------------------- #
# Tournament aggregates from StatsBomb (half shares, offsides, penalty/red)
# --------------------------------------------------------------------------- #

def tournament_aggregates(team_halves: pd.DataFrame) -> dict:
    """Second-half share per stat + offside/penalty/red base rates, measured
    on international tournaments (WC 2018/2022, Euros, Copa, AFCON)."""
    per_stat = {}
    for stat in ("goals", "corners", "cards", "sot", "fouls", "offsides"):
        by_half = team_halves.groupby("half")[stat].sum()
        per_stat[stat] = {
            "h2_share": float(by_half[2] / (by_half[1] + by_half[2])),
            "team_match_mean": float(
                team_halves.groupby(["match_id", "team"])[stat].sum().mean()),
        }
    # offside dispersion across team-matches (regression stats get alpha from clubs)
    offsides = team_halves.groupby(["match_id", "team"])["offsides"].sum()
    per_stat["offsides"]["alpha"] = _moment_alpha(
        offsides.to_numpy(), np.full(len(offsides), offsides.mean()))

    match_flags = team_halves.groupby("match_id").agg(
        penalty=("penalty_awarded", "max"), reds=("reds", "sum"))
    pen_or_red = (match_flags["penalty"] | (match_flags["reds"] > 0)).mean()
    return {
        "stats": per_stat,
        "p_penalty": float(match_flags["penalty"].mean()),
        "p_red": float((match_flags["reds"] > 0).mean()),
        "p_penalty_or_red": float(pen_or_red),
        "matches": int(team_halves["match_id"].nunique()),
    }


# --------------------------------------------------------------------------- #
# Pricing context for one fixture
# --------------------------------------------------------------------------- #

@dataclass
class CountPricer:
    """Prices all count markets for one match, given odds-implied goal rates."""

    lam_home: float
    lam_away: float
    params: dict          # fit_count_models output
    aggregates: dict      # tournament_aggregates output
    neutral_venue: bool = True
    tournament: bool = True   # apply club->tournament discounts (False in the club backtest)
    card_factor: float = 1.0  # referee strictness: (ref cards/game) / tournament avg (3.46)

    def mean(self, stat: str, side: str, period: str = "match") -> float:
        lam_team = self.lam_home if side == "home" else self.lam_away
        lam_opp = self.lam_away if side == "home" else self.lam_home
        if stat == "offsides":
            # No club coverage. Offsides are driven by defensive-line tactics,
            # NOT attacking strength — measured elasticity vs attacking output is
            # ~0.18 on StatsBomb (corr 0.08), far below the 0.5 once assumed here,
            # which overpriced favourites' offsides (e.g. SUI 0.59 vs true ~0.44).
            base = self.aggregates["stats"]["offsides"]["team_match_mean"]
            tempo = lam_team / 1.30  # 1.30 = average tournament goal rate
            mu = base * tempo ** OFFSIDE_TEMPO_EXPONENT
        else:
            c = self.params[stat]["coef"]
            is_home = 0.5 if self.neutral_venue else (1.0 if side == "home" else 0.0)
            mu = np.exp(c["const"] + c["log_lam_team"] * np.log(lam_team)
                        + c["log_lam_opp"] * np.log(lam_opp) + c["is_home"] * is_home)
            if self.tournament:
                mu *= COUNT_INTL_DISCOUNT.get(stat, 1.0)
            if stat == "cards":
                # Referee strictness is the dominant driver of card counts
                # (StatsBomb: a strict vs lenient ref swings P(4+ cards) ~0.24->0.66).
                mu *= self.card_factor
        if period != "match":
            share = self.aggregates["stats"][stat]["h2_share"]
            mu *= share if period == "2h" else 1 - share
        return float(mu)

    def alpha(self, stat: str) -> float:
        if stat == "offsides":
            return self.aggregates["stats"]["offsides"]["alpha"]
        return self.params[stat]["alpha"]

    # --- market families ---------------------------------------------------

    def p_team_count_ge(self, stat: str, side: str, threshold: int, period: str) -> float:
        return p_ge(self.mean(stat, side, period), self.alpha(stat), threshold)

    def p_total_count_ge(self, stat: str, threshold: int, period: str) -> float:
        # Exact: convolve the two teams' NB distributions (a sum of NBs with
        # unequal means is not NB; an alpha shortcut misprices uneven matches).
        home = nb_pmf_grid(self.mean(stat, "home", period), self.alpha(stat))
        away = nb_pmf_grid(self.mean(stat, "away", period), self.alpha(stat))
        total = np.convolve(home, away)
        return float(total[threshold:].sum())

    def p_both_teams_ge(self, stat: str, threshold: int, period: str) -> float:
        return (p_ge(self.mean(stat, "home", period), self.alpha(stat), threshold)
                * p_ge(self.mean(stat, "away", period), self.alpha(stat), threshold))

    def p_more_than(self, stat: str, side: str, period: str) -> float:
        other = "away" if side == "home" else "home"
        return p_strictly_more(self.mean(stat, side, period),
                               self.mean(stat, other, period), self.alpha(stat))

    def p_penalty(self) -> float:
        return self.aggregates["p_penalty"]

    def p_penalty_or_red(self) -> float:
        return self.aggregates["p_penalty_or_red"]
