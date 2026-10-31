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

# Per-stat WC-2026 calibration multipliers on count MEANS, applied only when
# pricing tournament fixtures (CountPricer.tournament=True), never in the club
# backtest. REPLACES a former UNIFORM 0.90 "low-event" multiplier (2026-06-19)
# that was a blunt instrument: it suppressed every stat equally, but WC-2026
# needs OPPOSITE corrections per stat. Re-fit 2026-06-24 on 383 settled markets
# (objective: minimise our Brier vs realized outcomes = calibration, the
# RBP-optimal target) with chronological OOS validation — each value below
# improves RBP on BOTH halves of the tournament — and cross-checked against
# observed WC-2026 rates (goals 3.12/game vs 2.56 in 2022; fouls 24.4 vs 27.7;
# yellows ~2.17/game):
#   cards   0.78  WC-2026 is a low-card tournament; the StatsBomb-based card model
#                 over-predicted by ~30pts (we 0.62 vs 0.31 realized). The former
#                 uniform 0.90 was nowhere near enough.  (settled cards -57 -> -5 RBP)
#   sot     1.00  removes the erroneous suppression — the early period (pre-0.90)
#                 had SOT well-calibrated; the 0.90 broke it, esp. total-SOT
#                 (-54 RBP).  (settled sot +153 -> +186 RBP)
#   corners 1.05  corners run high in WC-2026 (we under-predicted); modest boost.
#                 (settled corners +29 -> +54 RBP)
#   offsides 0.90 UNCHANGED — 2nd-biggest edge via fading and the realized rate is
#                 too unstable across the split to re-fit without overfitting.
#   fouls   0.90  unchanged; only appears in head-to-head markets where the level
#                 cancels (so this value is inert).
# Each value is uniform WITHIN a stat, so it moves only THRESHOLD markets; head-to-
# head "more-than X" comparisons are unchanged (asymmetry cancels). Anything not
# listed falls back to 0.90. Re-estimate via scripts/parse_history_rbp.py + the
# calibration as KO-stage data settles (KO event rates may differ from the group
# stage). A between-team SOT correlation term for TOTAL-SOT is a flagged follow-up.
# sot stays 1.00 here. TEAM-level SOT (team_count_ge, both_teams, head-to-head, and player ">=1"
# via the normaliser) is well-calibrated (settled gaps ~-0.05). The under-prediction is TOTAL-
# SPECIFIC, so it is corrected by TOTAL_SOT_SCALE below — NOT by lifting the per-team mean.
# (2026-06-29: a flat sot=1.40 was tried and REVERTED — it fixed total SOT but OVER-corrected team
# SOT, leave-out Brier 0.203->0.230 on 31 settled team-SOT markets. The decoupled form keeps team
# SOT at calibrated v4 and boosts only the total.)
TOURNAMENT_INTENSITY = {"cards": 0.78, "sot": 1.00, "corners": 1.05,
                        "offsides": 0.90, "fouls": 0.90}
# TOTAL-SOT level correction — applied ONLY to the convolved total (p_total_count_ge for sot) and
# the total-derived squad "2+ SOT" (total_sot_pmf), never to team/player SOT. The platform realises
# ~1.4x StatsBomb's total SOT (settled total_count_ge/sot pred 0.54 vs hit 0.83, n=18; implied scale
# 1.49, CI 1.24-1.78 — 1.40 is the conservative end). Validated live (BRA/JPN "8+ total SOT" HIT:
# v4 0.31->Brier 0.476 vs boosted 0.65->0.122) and by leave-out sim (total-SOT Brier 0.245->0.142).
TOTAL_SOT_SCALE = 1.50  # 2026-07-02 PROMOTED 1.40->1.50: the bot-2 1.60 probe went 4/5 vs 1.40
                        # (net dBrier -0.39); implied scale 1.49 (CI 1.24-1.78) -> point estimate.
# TOTAL-only offsides/shots level corrections — same total-specific pattern as SOT.
# DEFAULT 1.0 (bot 1 = v4, unchanged). The R32 our-data backtest shows total offsides
# (-28 RBP, pred 0.41 vs crowd 0.49, ~1.20x under) and total shots (-14 RBP, ~1.22x under)
# under-predicted, but on THIN samples (n=4 / n=1) that cannot be checked against StatsBomb
# (platform counting convention). So this is a BOT-2 EXPERIMENT ONLY until more settle —
# set via data/bot2_experiment.yaml (total_offsides_scale / total_shots_scale). Do NOT lift
# bot 1 off 1.0 without an OOS-validated win (same discipline as [[strategy-v4-doc]]).
TOTAL_OFFSIDES_SCALE = 1.10  # 2026-07-02 PROMOTED 1.0->1.10: total offsides -52 RBP cumulative
                             # (bias -0.32, n=7, direction consistent across ENG/COD + ESP/AUT);
                             # probe won at both notches on 2/3 settles. Mild level lift only.
TOTAL_SHOTS_SCALE = 1.0      # blanket shots probe is NET NEGATIVE (USA/BIH 22+ shots 91% vs NO,
                             # Brier 0.83) — dominance-concentrated, NOT promoted; bot-2 only.
# TOTAL-only card level correction — DEFAULT 1.0 (bot 1 unchanged). BOT-2 PROBE: cards are
# over-predicted even after the 0.78 per-stat intensity — direct calibration (2026-07-01, 724
# settled) shows mean card pred 0.481 vs realized 0.431 (+0.050), corroborated by the separate
# manual-branch policy backtest (+116 RBP from a card adjustment) and [[edge-analysis-and-fixes]]
# (total_count_ge/cards +0.14). A total-only reduction targets that without touching the calibrated
# card COMPARISONS (memory: card head-to-heads are winning/scale-invariant — never lift the per-team
# mean). Promote to bot 1 only on an OOS-validated win over its own card settles.
TOTAL_CARDS_SCALE = 0.85  # 2026-07-02 PROMOTED 1.0->0.85: cards over-predicted, corroborated 5
                          # ways — direct calib +0.05 (724 mkts), manual-branch backtest +116,
                          # edge memory +0.14, and 8/13 current-era live A/B wins with no real
                          # losses (ENG/COD, USA/BIH x2, BEL/SEN x3, ESP/AUT). Total-only.
# FAVOURITE-SOT boost — DEFAULT 0.0 (bot 1 = v4, OFF). BOT-2 PROBE. The club-fitted SOT
# elasticity (log_lam_team 0.797) is calibrated for EVEN matches (model 3.36 vs realized 3.33)
# but too FLAT for heavy favourites: our settled team-SOT predictions in the mid-high buckets
# under-predict by -0.12 to -0.18 (2026-07-01, n=32), and dominant teams rack up SOT (StatsBomb
# level 3.33 -> blowout 6.93, though that margin curve is ENDOGENOUS so we stay conservative).
# Supremacy-gated multiplier on the FAVOURITE's team-SOT mean (cascades to its players' SOT via
# tempo and to comparisons) — leaves even matches untouched. mu *= 1 + BOOST*min(lam_team/lam_opp-1, 2).
# Timing-window event scale. 2026-07-10 PROMOTED 1.0->0.85 (Yifan OK): timing_event
# over-predicts — realized 0.214 vs shipped 0.432 on n=28 settled. Three independent
# signals: live A/B probe 5/6, family calibration +0.146, and a walk-forward backtest
# (scripts/backtest_timing_scale.py, n=22 OOS) where 0.85 beat 1.0 by -1.83 Brier pts.
# 0.85 is a validated PARTIAL correction (backtest optimum is lower ~0.45-0.70; 0.70 now
# the bot-2 probe). Scales the windowed mean in p_total_in_window only.
TIMING_SCALE = 0.85
FAVORITE_SOT_BOOST = 0.06  # 2026-07-02 PROMOTED 0.0->0.06: the favourite-SOT under-prediction is
                           # bot 1's dominant recent leak — the 3 biggest losses in the last 4 games
                           # are ALL it (Bellingham 2+SOT -64, ENG 7+SOT -61, Spain 8+SOT -58).
                           # Validated: settled mid-high team-SOT bias -0.12..-0.18 (n=32), StatsBomb
                           # elasticity too flat for heavy favourites, and the live A/B won 4/4
                           # ESP/AUT divergents incl. the direct target (17->25 on a YES) and the
                           # player cascade (Yamal 39->44 YES). Supremacy-gated: even games untouched.
_DEFAULT_INTENSITY = 0.90

# Total shots (on+off target): anchor to the StatsBomb realized shots level
# (25.0/match, NB dispersion 0.044) and tempo-scale by the match's SOT relative to
# the COUNT MODEL's average-match SOT (6.956 at lam 1.3/1.3) — NOT the StatsBomb SOT
# (8.3), because the count model runs ~16% below it and would otherwise under-price.
TOURNAMENT_SHOTS_MEAN = 25.0
COUNT_AVG_SOT = 6.956
SHOTS_ALPHA = 0.044

# Squad-aggregate conditionals (StatsBomb 314 matches): P(any player brace |
# total goals G) and P(any player 2+ SOT | total SOT S). Integrated over the
# match's total-goals / total-SOT distribution so the price tracks tempo.
_Q_BRACE = (0.0, 0.0, 0.165, 0.203, 0.394, 0.368, 0.667)   # index = G; G>=6 -> last
_R_ANY2SOT_S = (1.0, 3.0, 5.0, 7.0, 9.0, 11.0)
_R_ANY2SOT_P = (0.05, 0.167, 0.353, 0.587, 0.924, 1.0)


def q_brace(total_goals: int) -> float:
    return _Q_BRACE[min(int(total_goals), len(_Q_BRACE) - 1)]


def r_any_2sot(total_sot: float) -> float:
    return float(np.interp(total_sot, _R_ANY2SOT_S, _R_ANY2SOT_P))


# Substitute scores: P(>=1 substitute goal | total goals G), from StatsBomb
# regulation goals on the 250 matches under the 5-SUB rule only (WC2022, Euro2020,
# Euro2024, Copa2024, AFCON2023). The 3-sub WC-2018 was DROPPED: it had the lowest
# sub-scoring rate of any tournament (0.172 vs the 5-sub mean 0.296) and biased the
# pooled table low at the modal G=2/3 cells. Tail (G>=4) isotonic-smoothed for
# monotonicity (thin samples). Marginal over the goal pmf = 0.296. Provenance +
# reproduction: scripts/derive_sub_scores.py (2026-06-28).
_SUB_GOAL = (0.0, 0.070, 0.264, 0.333, 0.565, 0.565, 0.900)   # index = G; G>=6 -> last
SUB_BEFORE_HT_RATE = 0.197
# P(>=1 team makes a substitution AT the halftime break) — StatsBomb 5-sub-era
# (wc2022/euro2024/copa2024/afcon2023, n=199): HT subs are recorded at period 2,
# minute 45. Coach-behaviour-driven and roughly match-independent -> flat base rate
# (derive: scripts/validate_first_goal_drift.py sibling one-off, 2026-07-10).
SUB_AT_HT_RATE = 0.497
# P(a NAMED team makes the match's first substitution) — StatsBomb: P(first-listed
# team subs first)=0.506 over n=314, i.e. symmetric ~0.50 (no pre-match tilt; who
# trails is unknown at pricing time). SF/KO template 2026-07-13.
SUB_FIRST_RATE = 0.50
# P(>=1 on-field VAR review at the pitchside monitor in reg+ET) — no clean StatsBomb
# event; reasoned estimate from recent-tournament on-field-review frequency (~0.3/game).
# ESTIMATE — refine if a better source appears. SF/KO template 2026-07-13.
VAR_REVIEW_RATE = 0.30
# Regulation penalty-kick conversion — StatsBomb 79/109 across the 6 tournaments
# (final template 2026-07-17). P(pen scored) = p_penalty x this.
PEN_CONVERSION = 0.725
# Share of matches' FIRST goals credited with an assist (pens/own goals have none) —
# StatsBomb n=285 first goals (final template 2026-07-17).
FIRST_GOAL_ASSISTED = 0.582
# P(>=5 DISTINCT players attempt a shot | team total shots s) — StatsBomb per-shot-
# count empirical curve (monotone-smoothed; n>=21 per point, 627 team-matches).
# Steep in s, so the family integrates over the team's shots pmf, never a flat rate.
_SHOOTERS_GE5_BY_SHOTS = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.30, 6: 0.33,
                          7: 0.71, 8: 0.84, 9: 0.88, 10: 0.97, 11: 0.97, 12: 0.98}
# s >= 13 -> 1.0 (empirical: 134/134)


def p_sub_goal(total_goals: int) -> float:
    return _SUB_GOAL[min(int(total_goals), len(_SUB_GOAL) - 1)]


# 'Goal by method' families: P(>=1 [type] goal | total regulation goals G), from
# StatsBomb (314 matches): own goals = Own Goal Against events; header = Shot(Goal,
# body_part=Head); outside-box = Shot(Goal) whose location is outside the 18-yd box
# ([102,120]x[18,62] on the 120x80 pitch). Each is integrated over the goal pmf at
# price time (the substitute-scores pattern). PAV-monotone in G (more goals -> >= chance
# of any one type), marginals preserved (own 0.124 / header 0.331 / outside 0.242).
# LOTO-validated: conditioning on G beats the flat base rate OOS for all three
# (Brier own 0.109<0.111, header 0.196<0.222, outside 0.170<0.184). See
# scripts/derive_goal_methods.py (2026-06-30).
_OWN_GOAL = (0.0, 0.074, 0.074, 0.176, 0.176, 0.211, 0.400)        # index = G; G>=6 -> last
_HEADER_GOAL = (0.0, 0.224, 0.275, 0.377, 0.485, 0.706, 0.706)
_OUTSIDE_GOAL = (0.0, 0.069, 0.231, 0.333, 0.333, 0.474, 0.533)


def p_goal_method(table: tuple, total_goals: int) -> float:
    """P(>=1 goal of this method | total regulation goals) from a conditional table."""
    return table[min(int(total_goals), len(table) - 1)]


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

    # Timing-market shares: fraction of each stat's events in each within-match
    # window (from StatsBomb minute timestamps). Missing columns (older cache)
    # degrade to {} so the timing pricer can fall back / stay flagged.
    from wcdata.statsbomb import TIMING_STATS, TIMING_WINDOWS
    timing = {}
    for s in TIMING_STATS:
        if s in team_halves and all(f"{s}_{w}" in team_halves for w in TIMING_WINDOWS):
            tot = team_halves[s].sum()
            if tot:
                timing[s] = {w: float(team_halves[f"{s}_{w}"].sum() / tot)
                             for w in TIMING_WINDOWS}
    return {
        "stats": per_stat,
        "p_penalty": float(match_flags["penalty"].mean()),
        "p_red": float((match_flags["reds"] > 0).mean()),
        "p_penalty_or_red": float(pen_or_red),
        "timing": timing,
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
        if self.tournament:
            mu *= TOURNAMENT_INTENSITY.get(stat, _DEFAULT_INTENSITY)  # WC-2026 per-stat calibration (OOS-validated)
        if stat == "sot" and FAVORITE_SOT_BOOST and lam_team > lam_opp:
            # Steepen SOT for the FAVOURITE only (supremacy-gated) — the flat elasticity
            # under-prices dominant teams' shot volume. Off by default (bot 1); bot-2 probe.
            mu *= 1.0 + FAVORITE_SOT_BOOST * min(lam_team / lam_opp - 1.0, 2.0)
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
        # TOTAL SOT carries a total-only level correction (TOTAL_SOT_SCALE) that
        # never touches the per-team SOT markets — see the constant's note.
        scale = {"sot": TOTAL_SOT_SCALE, "offsides": TOTAL_OFFSIDES_SCALE,
                 "cards": TOTAL_CARDS_SCALE}.get(stat, 1.0)
        home = nb_pmf_grid(self.mean(stat, "home", period) * scale, self.alpha(stat))
        away = nb_pmf_grid(self.mean(stat, "away", period) * scale, self.alpha(stat))
        total = np.convolve(home, away)
        return float(total[threshold:].sum())

    def p_team_distinct_shooters_ge(self, side: str, threshold: int) -> float:
        """P(>= `threshold` DIFFERENT players of `side` attempt a shot) — only the
        5-threshold is modelled (the empirical curve is >=5-specific). Integrates
        P(>=5 distinct | s) over the team's NB shots pmf; the curve is steep in s,
        so pricing at the mean would bias (final template, 2026-07-17)."""
        if threshold != 5:
            raise ValueError(f"distinct-shooters only models threshold 5, got {threshold}")
        # Team shots mean via the same SOT-tempo derivation as p_total_shots_ge
        # (shots are not a per-team fitted stat; the /2 cancels in the ratio).
        team_shots = TOURNAMENT_SHOTS_MEAN * self.mean("sot", side, "match") / COUNT_AVG_SOT
        pmf = nb_pmf_grid(team_shots, SHOTS_ALPHA)
        return float(sum(p * (_SHOOTERS_GE5_BY_SHOTS.get(s, 1.0) if s < 13 else 1.0)
                         for s, p in enumerate(pmf)))

    def p_card_each_half(self) -> float:
        """P(>=1 card shown in EACH half of regulation) — independent halves, the
        card sibling of goals.p_goal_each_half. Each half is the convolved two-team
        NB tail at >=1, using cards' StatsBomb h2_share (via mean(..,period)) and
        the TOTAL_CARDS_SCALE level, exactly as p_total_count_ge (KO, 2026-07-07)."""
        def half_ge1(period: str) -> float:
            h = nb_pmf_grid(self.mean("cards", "home", period) * TOTAL_CARDS_SCALE, self.alpha("cards"))
            a = nb_pmf_grid(self.mean("cards", "away", period) * TOTAL_CARDS_SCALE, self.alpha("cards"))
            return float(np.convolve(h, a)[1:].sum())
        return half_ge1("1h") * half_ge1("2h")

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

    def p_red(self) -> float:
        return self.aggregates["p_red"]

    def p_total_in_window(self, stat: str, window: str, threshold: int = 1) -> float:
        """P(>=threshold of `stat` by both teams within a within-match time window)
        = match rate x the empirical window share (aggregates['timing']), Poisson
        for the small windowed count. Raises if no timing share exists for the stat."""
        if window == "mid30_75":
            # Between the two hydration breaks (~30'..~75') = the middle share left
            # after removing the before-30 and after-75 windows.
            t = self.aggregates.get("timing", {}).get(stat, {})
            share = max(1.0 - (t.get("before30") or 0.0) - (t.get("after75") or 0.0), 0.0)
        elif window == "stoppage":
            # A card in EITHER half's stoppage time = the union of the two disjoint
            # stoppage windows; disjoint => their shares (hence windowed means) add.
            t = self.aggregates.get("timing", {}).get(stat, {})
            share = (t.get("h1stop") or 0.0) + (t.get("h2stop") or 0.0)
        elif window == "h1_after30":
            # First half AFTER the ~30' hydration break = the whole-first-half share
            # minus the before-30 window (the two partition the first half). Derived
            # from the stored before30 share + the stat's h2 split, so no separate
            # StatsBomb window is needed.
            from models.goals import H2_GOAL_SHARE
            before30 = self.aggregates.get("timing", {}).get(stat, {}).get("before30", 0.0)
            h2 = self.aggregates.get("stats", {}).get(stat, {}).get("h2_share") or H2_GOAL_SHARE
            share = max((1 - h2) - before30, 0.0)
        else:
            share = self.aggregates.get("timing", {}).get(stat, {}).get(window)
        if not share:
            raise ValueError(f"no timing share for {stat}/{window}")
        if stat == "goals":
            wmean = (self.lam_home + self.lam_away) * share
        else:
            wmean = (self.mean(stat, "home", "match") + self.mean(stat, "away", "match")) * share
        return float(1 - stats.poisson.cdf(threshold - 1, wmean * TIMING_SCALE))

    def total_sot_pmf(self) -> np.ndarray:
        # Total-only SOT correction (TOTAL_SOT_SCALE): this pmf feeds the squad
        # "2+ SOT" market (p_any_player_2sot), which is total-derived, not per-team.
        home = nb_pmf_grid(self.mean("sot", "home", "match") * TOTAL_SOT_SCALE, self.alpha("sot"))
        away = nb_pmf_grid(self.mean("sot", "away", "match") * TOTAL_SOT_SCALE, self.alpha("sot"))
        return np.convolve(home, away)

    def p_any_player_2sot(self) -> float:
        """P(>=1 player records 2+ SOT) = sum over the total-SOT pmf of the
        empirical P(any 2+ SOT | total SOT)."""
        return float(sum(p * r_any_2sot(s) for s, p in enumerate(self.total_sot_pmf())))

    # P(total regulation substitutions >= k), 5-sub-era StatsBomb (199 matches,
    # mean 8.82; scripts run 2026-07-06). Near-mechanical: teams use their windows.
    SUBS_TAIL = {6: 0.97, 7: 0.92, 8: 0.84, 9: 0.72, 10: 0.41, 11: 0.01}

    def p_total_subs_ge(self, threshold: int) -> float:
        if threshold <= 5:
            return 0.99
        return float(self.SUBS_TAIL.get(threshold, 0.005))

    # P(>=1 player OF ONE TEAM records 2+ SOT | that team's SOT total) — StatsBomb
    # 628 team-matches (derived 2026-07-05; near-deterministic rising curve). The
    # team-scoped sibling of r_any_2sot (which conditions on the MATCH total).
    _TEAM_ANY2SOT = (0.0, 0.0, 0.12, 0.31, 0.56, 0.64, 0.84, 0.98, 0.99)

    def p_team_any_player_2sot(self, side: str) -> float:
        """P(any single player of `side` records 2+ SOT) = team-SOT pmf x the
        empirical conditional. Uses the team's OWN mean (per-team market — no
        TOTAL-level scale, same discipline as team_count_ge)."""
        pmf = nb_pmf_grid(self.mean("sot", side, "match"), self.alpha("sot"))
        t = self._TEAM_ANY2SOT
        return float(sum(p * (t[min(s, len(t) - 1)]) for s, p in enumerate(pmf)))

    def total_cards_pmf(self) -> np.ndarray:
        """Convolved total-cards pmf at the TOTAL level (card_factor + intensity
        via mean(), then TOTAL_CARDS_SCALE like p_total_count_ge) — feeds the
        cross-stat 'more cards than goals' market."""
        home = nb_pmf_grid(self.mean("cards", "home", "match") * TOTAL_CARDS_SCALE, self.alpha("cards"))
        away = nb_pmf_grid(self.mean("cards", "away", "match") * TOTAL_CARDS_SCALE, self.alpha("cards"))
        return np.convolve(home, away)

    def p_total_shots_ge(self, threshold: int) -> float:
        """Total shots (on+off target): anchored to the realized shots level and
        tempo-scaled by this match's SOT vs the count model's average SOT."""
        match_sot = self.mean("sot", "home", "match") + self.mean("sot", "away", "match")
        mean = TOURNAMENT_SHOTS_MEAN * match_sot / COUNT_AVG_SOT * TOTAL_SHOTS_SCALE
        return p_ge(mean, SHOTS_ALPHA, threshold)
