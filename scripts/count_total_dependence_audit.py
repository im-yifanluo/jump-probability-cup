"""Audit total-count dependence alternatives for count threshold markets.

The production count pricer gets team marginal means and dispersions, then
prices total-count thresholds by convolving independent home/away negative
binomial distributions. That is exact under the independence assumption, but
shared match tempo can make total counts more or less dispersed than the
independent-team convolution.

This audit tests that implementation risk on historical Football-Data club
matches with closing odds and observed home/away counts. It fits the marginal
team-count model in memory on prior seasons only, never writes the production
count-model cache, never calls SportsPredict, never reads `/predictions`, and
never submits anything.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import random
import sys
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "count_total_dependence_audit.md"

sys.path.insert(0, str(ROOT / "src"))

from backtest.implied import _de_margin_columns, _invert_total, _p_home_win  # noqa: E402
from models.counts import _moment_alpha  # noqa: E402


TEST_SEASONS = ("1718", "1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526")
COUNT_STATS = ("corners", "cards", "sot", "fouls")
TOTAL_THRESHOLDS = {
    # Predeclared around observed/live market lines. These are not tuned after
    # looking at the result table.
    "corners": (8, 9, 10, 11),
    "cards": (3, 4, 5),
    "sot": (6, 8, 10),
    "fouls": (20, 24, 28),
}
METHODS = (
    "current_independent_team_nb",
    "direct_total_poisson",
    "direct_total_nb",
    "empirical_threshold_prior",
)
BASELINE = "current_independent_team_nb"
MAX_COUNT = 80


@dataclass(frozen=True)
class MarginalParams:
    coef: dict[str, float]
    alpha_team: float
    alpha_total: float


@dataclass(frozen=True)
class ScoredRow:
    season: str
    match_key: str
    stat: str
    threshold: int
    method: str
    probability: float
    outcome: int
    brier: float


@dataclass(frozen=True)
class MatchArrays:
    season: np.ndarray
    date: np.ndarray
    home_team: np.ndarray
    away_team: np.ndarray
    lam_home: np.ndarray
    lam_away: np.ndarray
    home_corners: np.ndarray
    away_corners: np.ndarray
    home_cards: np.ndarray
    away_cards: np.ndarray
    home_sot: np.ndarray
    away_sot: np.ndarray
    home_fouls: np.ndarray
    away_fouls: np.ndarray


def _clip(p: np.ndarray | float) -> np.ndarray | float:
    return np.clip(p, 0.01, 0.99)


def _brier(probability: float, outcome: int) -> float:
    p = min(0.99, max(0.01, probability))
    return (p - float(outcome)) ** 2


def _nb_pmf_matrix(mean: np.ndarray, alpha: float, max_count: int = MAX_COUNT) -> np.ndarray:
    mean = np.maximum(np.asarray(mean, dtype=float), 1e-9)
    k = np.arange(max_count + 1)
    if alpha < 1e-9:
        pmf = stats.poisson.pmf(k[None, :], mean[:, None])
    else:
        size = 1.0 / alpha
        p = size / (size + mean)
        pmf = stats.nbinom.pmf(k[None, :], size, p[:, None])
    pmf_sum = pmf.sum(axis=1, keepdims=True)
    return pmf / np.maximum(pmf_sum, 1e-12)


def p_independent_sum_ge(
    home_mean: np.ndarray,
    away_mean: np.ndarray,
    alpha: float,
    threshold: int,
    *,
    max_count: int = MAX_COUNT,
) -> np.ndarray:
    """P(home + away >= threshold) for independent equal-alpha NB marginals."""

    home = _nb_pmf_matrix(home_mean, alpha, max_count)
    away = _nb_pmf_matrix(away_mean, alpha, max_count)
    away_cdf = np.cumsum(away, axis=1)
    prob = np.zeros(len(home_mean), dtype=float)
    for h in range(max_count + 1):
        cutoff = threshold - h
        if cutoff <= 0:
            away_tail = np.ones(len(home_mean), dtype=float)
        elif cutoff > max_count + 1:
            away_tail = np.zeros(len(home_mean), dtype=float)
        else:
            away_tail = 1.0 - away_cdf[:, cutoff - 1]
        prob += home[:, h] * away_tail
    return _clip(prob)


def p_count_ge(mean: np.ndarray, alpha: float, threshold: int) -> np.ndarray:
    mean = np.maximum(np.asarray(mean, dtype=float), 1e-9)
    if alpha < 1e-9:
        return _clip(stats.poisson.sf(threshold - 1, mean))
    size = 1.0 / alpha
    p = size / (size + mean)
    return _clip(stats.nbinom.sf(threshold - 1, size, p))


def load_source_matches() -> pd.DataFrame:
    """Load cleaned club matches without depending on hidden local state."""

    primary = ROOT / "data" / "club_matches.parquet"
    reference = ROOT / "data_reference" / "club_matches.parquet"
    if primary.exists():
        return pd.read_parquet(primary)
    if reference.exists():
        return pd.read_parquet(reference)
    from wcdata.football_data_uk import load_club_matches  # noqa: WPS433

    return load_club_matches()


def _finite_mask(df: pd.DataFrame, columns: Iterable[str]) -> np.ndarray:
    mask = np.ones(len(df), dtype=bool)
    for column in columns:
        values = pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=float)
        mask &= np.isfinite(values)
    return mask


def add_implied_lambdas(matches: pd.DataFrame) -> pd.DataFrame:
    """Local dropna-free version of the production implied-lambda helper."""

    odds_cols = ["odds_home", "odds_draw", "odds_away", "odds_over25", "odds_under25"]
    mask = _finite_mask(matches, odds_cols)
    for column in odds_cols:
        mask &= pd.to_numeric(matches[column], errors="coerce").to_numpy(dtype=float) > 1.0
    needed = [
        "season",
        "date",
        "home_team",
        "away_team",
        "home_corners",
        "away_corners",
        "home_sot",
        "away_sot",
        "home_cards",
        "away_cards",
        "home_fouls",
        "away_fouls",
        *odds_cols,
    ]
    df = pd.DataFrame({column: matches[column].to_numpy()[mask] for column in needed})
    p1x2 = _de_margin_columns(df, ["odds_home", "odds_draw", "odds_away"])
    p_ou = _de_margin_columns(df, ["odds_over25", "odds_under25"])
    df["p_home_market"], df["p_draw_market"], df["p_away_market"] = p1x2.T
    total = _invert_total(p_ou[:, 0])

    lo = np.full(len(df), 0.05)
    hi = total - 0.05
    target = df["p_home_market"].to_numpy()
    for _ in range(35):
        mid = (lo + hi) / 2
        too_low = _p_home_win(mid, total - mid) < target
        lo = np.where(too_low, mid, lo)
        hi = np.where(too_low, hi, mid)
    df["lam_home"] = (lo + hi) / 2
    df["lam_away"] = total - df["lam_home"]
    return df


def to_match_arrays(matches: pd.DataFrame) -> MatchArrays:
    return MatchArrays(
        season=matches["season"].to_numpy(),
        date=matches["date"].to_numpy(),
        home_team=matches["home_team"].to_numpy(),
        away_team=matches["away_team"].to_numpy(),
        lam_home=matches["lam_home"].to_numpy(dtype=float),
        lam_away=matches["lam_away"].to_numpy(dtype=float),
        home_corners=pd.to_numeric(matches["home_corners"], errors="coerce").to_numpy(dtype=float),
        away_corners=pd.to_numeric(matches["away_corners"], errors="coerce").to_numpy(dtype=float),
        home_cards=pd.to_numeric(matches["home_cards"], errors="coerce").to_numpy(dtype=float),
        away_cards=pd.to_numeric(matches["away_cards"], errors="coerce").to_numpy(dtype=float),
        home_sot=pd.to_numeric(matches["home_sot"], errors="coerce").to_numpy(dtype=float),
        away_sot=pd.to_numeric(matches["away_sot"], errors="coerce").to_numpy(dtype=float),
        home_fouls=pd.to_numeric(matches["home_fouls"], errors="coerce").to_numpy(dtype=float),
        away_fouls=pd.to_numeric(matches["away_fouls"], errors="coerce").to_numpy(dtype=float),
    )


def _home_values(arrays: MatchArrays, stat: str) -> np.ndarray:
    return getattr(arrays, f"home_{stat}")


def _away_values(arrays: MatchArrays, stat: str) -> np.ndarray:
    return getattr(arrays, f"away_{stat}")


def _valid_stat_indices(arrays: MatchArrays, indices: np.ndarray, stat: str) -> np.ndarray:
    home = _home_values(arrays, stat)[indices]
    away = _away_values(arrays, stat)[indices]
    mask = (
        np.isfinite(home)
        & np.isfinite(away)
        & np.isfinite(arrays.lam_home[indices])
        & np.isfinite(arrays.lam_away[indices])
    )
    return indices[mask]


def _predict_side_means_arrays(
    arrays: MatchArrays,
    indices: np.ndarray,
    params: MarginalParams,
) -> tuple[np.ndarray, np.ndarray]:
    c = params.coef
    lam_home = arrays.lam_home[indices]
    lam_away = arrays.lam_away[indices]
    home = np.exp(
        c["const"]
        + c["log_lam_team"] * np.log(lam_home)
        + c["log_lam_opp"] * np.log(lam_away)
        + c["is_home"]
    )
    away = np.exp(
        c["const"]
        + c["log_lam_team"] * np.log(lam_away)
        + c["log_lam_opp"] * np.log(lam_home)
    )
    return home, away


def fit_marginal_params_arrays(arrays: MatchArrays, train_indices: np.ndarray, stat: str) -> MarginalParams:
    import statsmodels.api as sm  # noqa: WPS433

    valid = _valid_stat_indices(arrays, train_indices, stat)
    lam_home = arrays.lam_home[valid]
    lam_away = arrays.lam_away[valid]
    home_y = _home_values(arrays, stat)[valid]
    away_y = _away_values(arrays, stat)[valid]
    x_home = np.column_stack(
        [
            np.ones(len(valid)),
            np.log(lam_home),
            np.log(lam_away),
            np.ones(len(valid)),
        ]
    )
    x_away = np.column_stack(
        [
            np.ones(len(valid)),
            np.log(lam_away),
            np.log(lam_home),
            np.zeros(len(valid)),
        ]
    )
    x = np.vstack([x_home, x_away])
    y = np.concatenate([home_y, away_y])
    glm = sm.GLM(y, x, family=sm.families.Poisson()).fit()
    coef = {
        "const": float(glm.params[0]),
        "log_lam_team": float(glm.params[1]),
        "log_lam_opp": float(glm.params[2]),
        "is_home": float(glm.params[3]),
    }
    fitted_team = glm.fittedvalues
    alpha_team = _moment_alpha(y, fitted_team)
    params = MarginalParams(coef=coef, alpha_team=alpha_team, alpha_total=0.0)
    home_mu, away_mu = _predict_side_means_arrays(arrays, valid, params)
    total_y = home_y + away_y
    alpha_total = _moment_alpha(total_y, home_mu + away_mu)
    return MarginalParams(coef=coef, alpha_team=alpha_team, alpha_total=alpha_total)


def _long_stat_frame(matches: pd.DataFrame, stat: str) -> pd.DataFrame:
    home_mask = _finite_mask(matches, ["lam_home", "lam_away", f"home_{stat}"])
    away_mask = _finite_mask(matches, ["lam_home", "lam_away", f"away_{stat}"])
    home_src = matches.loc[home_mask]
    away_src = matches.loc[away_mask]
    home = pd.DataFrame(
        {
            "log_lam_team": np.log(home_src["lam_home"].to_numpy(dtype=float)),
            "log_lam_opp": np.log(home_src["lam_away"].to_numpy(dtype=float)),
            "is_home": 1.0,
            "y": home_src[f"home_{stat}"].to_numpy(dtype=float),
        }
    )
    away = pd.DataFrame(
        {
            "log_lam_team": np.log(away_src["lam_away"].to_numpy(dtype=float)),
            "log_lam_opp": np.log(away_src["lam_home"].to_numpy(dtype=float)),
            "is_home": 0.0,
            "y": away_src[f"away_{stat}"].to_numpy(dtype=float),
        }
    )
    return pd.concat([home, away], ignore_index=True)


def _predict_side_means(matches: pd.DataFrame, params: MarginalParams) -> tuple[np.ndarray, np.ndarray]:
    c = params.coef
    home = np.exp(
        c["const"]
        + c["log_lam_team"] * np.log(matches["lam_home"].to_numpy())
        + c["log_lam_opp"] * np.log(matches["lam_away"].to_numpy())
        + c["is_home"]
    )
    away = np.exp(
        c["const"]
        + c["log_lam_team"] * np.log(matches["lam_away"].to_numpy())
        + c["log_lam_opp"] * np.log(matches["lam_home"].to_numpy())
    )
    return home, away


def fit_marginal_params(train: pd.DataFrame, stat: str) -> MarginalParams:
    """Fit the production-style marginal model without writing cache files."""

    import statsmodels.api as sm  # noqa: WPS433

    long = _long_stat_frame(train, stat)
    x = sm.add_constant(long[["log_lam_team", "log_lam_opp", "is_home"]], has_constant="add")
    glm = sm.GLM(long["y"], x, family=sm.families.Poisson()).fit()
    coef = {str(key): float(value) for key, value in glm.params.items()}
    fitted_team = glm.fittedvalues.to_numpy()
    alpha_team = _moment_alpha(long["y"].to_numpy(), fitted_team)

    usable_train = train.loc[_finite_mask(train, [f"home_{stat}", f"away_{stat}", "lam_home", "lam_away"])].copy()
    home_mu, away_mu = _predict_side_means(usable_train, MarginalParams(coef, alpha_team, 0.0))
    total_y = usable_train[f"home_{stat}"].to_numpy() + usable_train[f"away_{stat}"].to_numpy()
    alpha_total = _moment_alpha(total_y, home_mu + away_mu)
    return MarginalParams(coef=coef, alpha_team=alpha_team, alpha_total=alpha_total)


def _season_rank(season: str) -> int:
    return TEST_SEASONS.index(season) if season in TEST_SEASONS else 10_000


def _market_key(row: pd.Series | object) -> str:
    return f"{getattr(row, 'season')}:{getattr(row, 'date')}:{getattr(row, 'home_team')}:{getattr(row, 'away_team')}"


def _market_key_arrays(arrays: MatchArrays, idx: int) -> str:
    return f"{arrays.season[idx]}:{arrays.date[idx]}:{arrays.home_team[idx]}:{arrays.away_team[idx]}"


def score_test_arrays(
    arrays: MatchArrays,
    train_indices: np.ndarray,
    test_indices: np.ndarray,
    stat: str,
    params: MarginalParams,
) -> list[ScoredRow]:
    valid_test = _valid_stat_indices(arrays, test_indices, stat)
    if len(valid_test) == 0:
        return []
    valid_train = _valid_stat_indices(arrays, train_indices, stat)

    home_mu, away_mu = _predict_side_means_arrays(arrays, valid_test, params)
    total_mu = home_mu + away_mu
    total_actual = _home_values(arrays, stat)[valid_test] + _away_values(arrays, stat)[valid_test]
    train_total = _home_values(arrays, stat)[valid_train] + _away_values(arrays, stat)[valid_train]

    scored: list[ScoredRow] = []
    for threshold in TOTAL_THRESHOLDS[stat]:
        outcomes = (total_actual >= threshold).astype(int)
        empirical_prior = float((train_total >= threshold).mean())
        probabilities = {
            "current_independent_team_nb": p_independent_sum_ge(
                home_mu,
                away_mu,
                params.alpha_team,
                threshold,
            ),
            "direct_total_poisson": p_count_ge(total_mu, 0.0, threshold),
            "direct_total_nb": p_count_ge(total_mu, params.alpha_total, threshold),
            "empirical_threshold_prior": np.full(len(valid_test), empirical_prior),
        }
        for pos, idx in enumerate(valid_test):
            key = _market_key_arrays(arrays, int(idx))
            for method, probs in probabilities.items():
                probability = float(probs[pos])
                outcome = int(outcomes[pos])
                scored.append(
                    ScoredRow(
                        season=str(arrays.season[idx]),
                        match_key=key,
                        stat=stat,
                        threshold=threshold,
                        method=method,
                        probability=probability,
                        outcome=outcome,
                        brier=_brier(probability, outcome),
                    )
                )
    return scored


def score_test_matches(test: pd.DataFrame, stat: str, params: MarginalParams, train: pd.DataFrame) -> list[ScoredRow]:
    usable = test.loc[_finite_mask(test, [f"home_{stat}", f"away_{stat}", "lam_home", "lam_away"])].copy()
    if usable.empty:
        return []

    train_stat = train.loc[_finite_mask(train, [f"home_{stat}", f"away_{stat}"])]
    home_mu, away_mu = _predict_side_means(usable, params)
    total_mu = home_mu + away_mu
    total_actual = usable[f"home_{stat}"].to_numpy() + usable[f"away_{stat}"].to_numpy()

    scored: list[ScoredRow] = []
    for threshold in TOTAL_THRESHOLDS[stat]:
        outcomes = (total_actual >= threshold).astype(int)
        empirical_prior = float(
            (
                train_stat[f"home_{stat}"].to_numpy() + train_stat[f"away_{stat}"].to_numpy()
                >= threshold
            ).mean()
        )
        probabilities = {
            "current_independent_team_nb": p_independent_sum_ge(
                home_mu,
                away_mu,
                params.alpha_team,
                threshold,
            ),
            "direct_total_poisson": p_count_ge(total_mu, 0.0, threshold),
            "direct_total_nb": p_count_ge(total_mu, params.alpha_total, threshold),
            "empirical_threshold_prior": np.full(len(usable), empirical_prior),
        }
        for idx, row in enumerate(usable.itertuples(index=False)):
            key = _market_key(row)
            for method, probs in probabilities.items():
                probability = float(probs[idx])
                outcome = int(outcomes[idx])
                scored.append(
                    ScoredRow(
                        season=str(row.season),
                        match_key=key,
                        stat=stat,
                        threshold=threshold,
                        method=method,
                        probability=probability,
                        outcome=outcome,
                        brier=_brier(probability, outcome),
                    )
                )
    return scored


def replay(matches: pd.DataFrame, test_seasons: Iterable[str] = TEST_SEASONS) -> list[ScoredRow]:
    matches = add_implied_lambdas(matches)
    arrays = to_match_arrays(matches)
    ranks = np.array([_season_rank(str(season)) for season in arrays.season])
    seasons = tuple(test_seasons)
    scored: list[ScoredRow] = []
    for season in seasons:
        season_rank = _season_rank(season)
        train_indices = np.flatnonzero(ranks < season_rank)
        test_indices = np.flatnonzero(arrays.season.astype(str) == season)
        if len(train_indices) == 0 or len(test_indices) == 0:
            continue
        for stat in COUNT_STATS:
            if len(_valid_stat_indices(arrays, train_indices, stat)) == 0 or len(_valid_stat_indices(arrays, test_indices, stat)) == 0:
                continue
            params = fit_marginal_params_arrays(arrays, train_indices, stat)
            scored.extend(score_test_arrays(arrays, train_indices, test_indices, stat, params))
    return scored


def _unique_markets(scored: list[ScoredRow]) -> int:
    return len({(row.season, row.match_key, row.stat, row.threshold) for row in scored if row.method == BASELINE})


def method_table(scored: list[ScoredRow], *, rows: Iterable[ScoredRow] | None = None) -> list[dict[str, str]]:
    data = list(rows or scored)
    by_method: dict[str, list[ScoredRow]] = defaultdict(list)
    for row in data:
        by_method[row.method].append(row)
    base = by_method.get(BASELINE, [])
    base_brier = sum(row.brier for row in base) / len(base) if base else float("nan")
    out = []
    for method in METHODS:
        part = by_method.get(method, [])
        if not part:
            continue
        brier = sum(row.brier for row in part) / len(part)
        delta = (base_brier - brier) * len(part) * 100.0
        out.append(
            {
                "method": method,
                "n": str(len(part)),
                "brier": f"{brier:.6f}",
                "delta_vs_current_x100": f"{delta:.2f}",
                "mean_probability": f"{sum(row.probability for row in part) / len(part):.4f}",
                "hit_rate": f"{sum(row.outcome for row in part) / len(part):.4f}",
            }
        )
    return sorted(out, key=lambda row: float(row["delta_vs_current_x100"]), reverse=True)


def stat_table(scored: list[ScoredRow]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for stat in COUNT_STATS:
        part = [row for row in scored if row.stat == stat]
        if not part:
            continue
        for row in method_table(scored, rows=part):
            out.append({"stat": stat, **row})
    return out


def threshold_table(scored: list[ScoredRow]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    keys = sorted({(row.stat, row.threshold) for row in scored})
    for stat, threshold in keys:
        part = [row for row in scored if row.stat == stat and row.threshold == threshold]
        best = method_table(scored, rows=part)[0]
        current = next(row for row in method_table(scored, rows=part) if row["method"] == BASELINE)
        out.append(
            {
                "stat": stat,
                "threshold": str(threshold),
                "best_method": best["method"],
                "best_delta_vs_current_x100": best["delta_vs_current_x100"],
                "current_brier": current["brier"],
                "best_brier": best["brier"],
                "n": current["n"],
            }
        )
    return out


def paired_market_deltas(scored: list[ScoredRow], candidate: str, baseline: str = BASELINE) -> dict[str, float]:
    grouped: dict[tuple[str, str, str, int], dict[str, ScoredRow]] = defaultdict(dict)
    for row in scored:
        grouped[(row.season, row.match_key, row.stat, row.threshold)][row.method] = row
    deltas: dict[str, float] = defaultdict(float)
    for (_season, match_key, _stat, _threshold), methods in grouped.items():
        if candidate not in methods or baseline not in methods:
            continue
        deltas[match_key] += 100.0 * (methods[baseline].brier - methods[candidate].brier)
    return dict(deltas)


def bootstrap_comparison(
    scored: list[ScoredRow],
    candidate: str,
    *,
    baseline: str = BASELINE,
    n: int = 1000,
    seed: int = 29,
) -> dict[str, float]:
    deltas = paired_market_deltas(scored, candidate, baseline)
    values = list(deltas.values())
    if not values:
        return {"point_delta_x100": 0.0, "ci_2.5": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    point = sum(values)
    rng = random.Random(seed)
    draws = []
    for _ in range(n):
        draws.append(sum(rng.choice(values) for _ in values))
    draws.sort()
    return {
        "point_delta_x100": point,
        "ci_2.5": draws[int(0.025 * (n - 1))],
        "ci_97.5": draws[int(0.975 * (n - 1))],
        "pr_gt_0": sum(1 for value in draws if value > 0) / n,
    }


def _recent_rows(scored: list[ScoredRow], start: str = "2324") -> list[ScoredRow]:
    return [row for row in scored if _season_rank(row.season) >= _season_rank(start)]


def analyze(matches: pd.DataFrame | None = None, *, bootstrap_n: int = 1000) -> dict[str, object]:
    source = matches if matches is not None else load_source_matches()
    scored = replay(source)
    bootstraps = {
        method: bootstrap_comparison(scored, method, n=bootstrap_n)
        for method in METHODS
        if method != BASELINE
    }
    table = method_table(scored)
    best = table[0]["method"] if table else BASELINE
    direct_nb = bootstraps.get("direct_total_nb", {})
    recent_direct_nb = next(
        (
            row
            for row in method_table(scored, rows=_recent_rows(scored))
            if row["method"] == "direct_total_nb"
        ),
        {"delta_vs_current_x100": "0.00"},
    )
    if (
        best == "direct_total_nb"
        and direct_nb.get("point_delta_x100", 0.0) > 0
        and direct_nb.get("ci_2.5", 0.0) > 0
        and float(recent_direct_nb["delta_vs_current_x100"]) > 0
    ):
        decision = "proxy_candidate_direct_total_nb_positive_not_live_promoted"
    else:
        decision = "not_promoted_keep_independent_team_convolution"
    return {
        "run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decision": decision,
        "source_matches": int(len(add_implied_lambdas(source))),
        "scored_market_rows": _unique_markets(scored),
        "scored_prediction_rows": len(scored),
        "seasons": sorted({row.season for row in scored}, key=_season_rank),
        "method_table": table,
        "recent_method_table": method_table(scored, rows=_recent_rows(scored)),
        "stat_table": stat_table(scored),
        "threshold_table": threshold_table(scored),
        "bootstraps": bootstraps,
    }


def _format_float(value: float) -> str:
    return f"{value:.2f}"


def render(analysis: dict[str, object]) -> str:
    lines = [
        "# Count Total Dependence Audit",
        "",
        f"- run_utc: {analysis['run_utc']}",
        "- bot_predictions: not_read",
        "- sports_predict: not_called",
        "- purpose: test whether total-count thresholds need a direct-total/dependence correction",
        f"- decision: {analysis['decision']}",
        f"- source_matches: {analysis['source_matches']}",
        f"- scored_market_rows: {analysis['scored_market_rows']}",
        f"- scored_prediction_rows: {analysis['scored_prediction_rows']}",
        f"- seasons: {', '.join(analysis['seasons'])}",
        "",
        "## Method Replay",
        "",
        "| method | n | brier | delta_vs_current_x100 | mean_probability | hit_rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in analysis["method_table"]:
        lines.append(
            f"| {row['method']} | {row['n']} | {row['brier']} | {row['delta_vs_current_x100']} | "
            f"{row['mean_probability']} | {row['hit_rate']} |"
        )

    lines.extend(
        [
            "",
            "## Recent Holdout Since 2023",
            "",
            "| method | n | brier | delta_vs_current_x100 | mean_probability | hit_rate |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in analysis["recent_method_table"]:
        lines.append(
            f"| {row['method']} | {row['n']} | {row['brier']} | {row['delta_vs_current_x100']} | "
            f"{row['mean_probability']} | {row['hit_rate']} |"
        )

    lines.extend(
        [
            "",
            "## Match-Block Bootstraps Vs Current",
            "",
            "| comparison | point_delta_x100 | ci_2.5 | ci_97.5 | pr_gt_0 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for method, row in analysis["bootstraps"].items():
        lines.append(
            f"| {method}_minus_current | {_format_float(row['point_delta_x100'])} | "
            f"{_format_float(row['ci_2.5'])} | {_format_float(row['ci_97.5'])} | {row['pr_gt_0']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## By Stat",
            "",
            "| stat | method | n | brier | delta_vs_current_x100 | mean_probability | hit_rate |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in analysis["stat_table"]:
        lines.append(
            f"| {row['stat']} | {row['method']} | {row['n']} | {row['brier']} | "
            f"{row['delta_vs_current_x100']} | {row['mean_probability']} | {row['hit_rate']} |"
        )

    lines.extend(
        [
            "",
            "## By Threshold",
            "",
            "| stat | threshold | best_method | best_delta_vs_current_x100 | current_brier | best_brier | n |",
            "| --- | ---: | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in analysis["threshold_table"]:
        lines.append(
            f"| {row['stat']} | {row['threshold']} | {row['best_method']} | "
            f"{row['best_delta_vs_current_x100']} | {row['current_brier']} | {row['best_brier']} | {row['n']} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `current_independent_team_nb` is the production shape: independent home/away NB marginals convolved for the total.",
            "- `direct_total_nb` keeps the production marginal mean sum but estimates total-count overdispersion from train-only total residuals.",
            "- This is a club-history proxy for count-model shape. It is not official SportsPredict settlement and cannot override pre-lock no-field/current-source rules by itself.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument("--bootstrap-n", type=int, default=1000)
    args = ap.parse_args()

    analysis = analyze(bootstrap_n=args.bootstrap_n)
    text = render(analysis)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
