"""Walk-forward backtest of the production pricing stack on club history.

For every test-season match (with odds), we rebuild exactly what the live
pricer does — odds-anchored ScorelineModel + train-fitted CountPricer — and
price a fixed battery of synthetic questions mirroring the World Cup market
families. Each prediction is scored (Brier) against what actually happened.

Baselines per family:
  brier_50        always predict 0.5 (the naive crowd guess)
  brier_baserate  predict the family's frequency in seasons before the test one
  (for team_win, our prediction *is* the market — matching it is success)

Club data has no half-level corners/cards/SOT, so half-based count questions
are validated indirectly: the half machinery is exercised on goals (real
HT/FT splits) and the count half-shares come from StatsBomb tournaments.
Player props are not backtestable here (no player columns) — their rates are
validated against tournament aggregates and market props instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.counts import CountPricer, fit_count_models, tournament_aggregates
from models.goals import ScorelineModel
from wcdata.football_data_uk import load_club_matches
from wcdata.statsbomb import build_aggregates
from backtest.implied import add_implied_lambdas

TEST_SEASONS = ["1718", "1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526"]


def _rho_linearized(lam_h: float, lam_a: float, p_draw_market: float,
                    grid_model: ScorelineModel) -> float:
    """One-step linear solve for the DC rho that matches the market draw prob."""
    p00 = grid_model.grid[0, 0]
    p11 = grid_model.grid[1, 1]
    d0 = grid_model.p_draw()
    denom = p00 * lam_h * lam_a + p11
    if denom < 1e-9:
        return 0.0
    rho = (d0 - p_draw_market) / denom
    lo = max(-1.0 / max(lam_h, 1e-6), -1.0 / max(lam_a, 1e-6)) + 1e-3
    hi = min(1.0 / max(lam_h * lam_a, 1e-6), 1.0) - 1e-3
    return float(np.clip(rho, lo, hi))


def _question_rows(row, goals: ScorelineModel, counts: CountPricer) -> list[tuple[str, float, int]]:
    """(family_label, prediction, outcome) triples for one historical match.
    Every question is gated on its *own* inputs being present — a missing
    stat must mean "skip", never "score the outcome as False"."""
    hg, ag = row.home_goals, row.away_goals
    total = hg + ag

    out = [
        ("team_win",        goals.p_home_win(),               hg > ag),
        ("total_goals_ge3", goals.p_total_goals(3, ">="),     total >= 3),
        ("btts_and_ge3",    goals.p_btts_and_total_ge(3),     hg >= 1 and ag >= 1 and total >= 3),
        ("team_goals_ge2",  goals.p_team_goals_ge("home", 2), hg >= 2),
    ]

    if np.isfinite(row.home_goals_1h) and np.isfinite(row.away_goals_1h):
        h1h, h1a = row.home_goals_1h, row.away_goals_1h
        h2h, h2a = hg - h1h, ag - h1a
        ht_total = h1h + h1a
        out += [
            ("ht_tied",            goals.p_ht_tied(),                         h1h == h1a),
            ("ht_team_winning",    goals.p_ht_winning("home"),                h1h > h1a),
            ("team_scores_2h",     goals.p_team_scores_in_half("home", "2h"), h2h >= 1),
            ("h2_more_than_h1",    goals.p_h2_more_goals_than_h1(),           (h2h + h2a) > ht_total),
            ("team_more_goals_2h", goals.p_team_more_goals_2h("home"),        h2h > h2a),
            ("total_goals_2h_ge2", goals.p_total_goals_half_ge(2, "2h"),      (h2h + h2a) >= 2),
        ]

    per_stat = {
        "corners": [("total_corners_ge10", "total", 10), ("team_corners_ge5", "team", 5),
                    ("more_corners", "more", None)],
        "cards":   [("total_cards_ge4", "total", 4), ("more_cards", "more", None)],
        "sot":     [("total_sot_ge8", "total", 8), ("team_sot_ge4", "team", 4),
                    ("more_sot", "more", None)],
        "fouls":   [("more_fouls", "more", None)],
    }
    for stat, questions in per_stat.items():
        home_v, away_v = getattr(row, f"home_{stat}"), getattr(row, f"away_{stat}")
        if not (np.isfinite(home_v) and np.isfinite(away_v)):
            continue
        for family, kind, threshold in questions:
            if kind == "total":
                out.append((family, counts.p_total_count_ge(stat, threshold, "match"),
                            home_v + away_v >= threshold))
            elif kind == "team":
                out.append((family, counts.p_team_count_ge(stat, "home", threshold, "match"),
                            home_v >= threshold))
            else:
                out.append((family, counts.p_more_than(stat, "home", "match"),
                            home_v > away_v))
    return [(family, p, int(outcome)) for family, p, outcome in out]


def run_backtest() -> pd.DataFrame:
    matches = add_implied_lambdas(load_club_matches())
    team_halves, _ = build_aggregates()
    aggregates = tournament_aggregates(team_halves)

    records = []
    for season in TEST_SEASONS:
        train = matches[matches["season"] < season]
        test = matches[matches["season"] == season]
        if train.empty or test.empty:
            continue
        params = fit_count_models(train.dropna(subset=["home_corners", "home_cards", "home_sot"]))
        h1_goals = (train["home_goals_1h"] + train["away_goals_1h"]).sum()
        h2_share = 1 - h1_goals / (train["home_goals"] + train["away_goals"]).sum()

        for row in tqdm(test.itertuples(), total=len(test), desc=f"season {season}"):
            base = ScorelineModel(row.lam_home, row.lam_away, 0.0, h2_share)
            rho = _rho_linearized(row.lam_home, row.lam_away, row.p_draw_market, base)
            goals = ScorelineModel(row.lam_home, row.lam_away, rho, h2_share)
            counts = CountPricer(row.lam_home, row.lam_away, params, aggregates,
                                 neutral_venue=False)
            for family, p, outcome in _question_rows(row, goals, counts):
                records.append((season, family, p, outcome))

    return pd.DataFrame(records, columns=["season", "family", "p", "outcome"])


def report(results: pd.DataFrame) -> pd.DataFrame:
    """Per-family Brier vs baselines. base-rate baseline uses prior seasons."""
    rows = []
    for family, df in results.groupby("family"):
        brier = ((df["p"] - df["outcome"]) ** 2).mean()
        brier_50 = ((0.5 - df["outcome"]) ** 2).mean()
        rate_briers = []
        for season in df["season"].unique():
            prior = df[df["season"] < season]["outcome"]
            if len(prior) >= 200:
                cur = df[df["season"] == season]
                rate_briers.append(((prior.mean() - cur["outcome"]) ** 2).mean())
        rows.append({
            "family": family, "n": len(df),
            "brier_model": brier, "brier_50": brier_50,
            "brier_baserate": np.mean(rate_briers) if rate_briers else np.nan,
            "edge_vs_50": brier_50 - brier,
            "edge_vs_baserate": (np.mean(rate_briers) - brier) if rate_briers else np.nan,
        })
    return pd.DataFrame(rows).sort_values("edge_vs_baserate", ascending=False)
