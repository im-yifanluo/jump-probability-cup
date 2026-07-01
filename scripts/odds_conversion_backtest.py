"""Backtest bookmaker overround conversion methods on historical football odds.

This is a local, read-only audit. It uses Football-Data CSVs only, never calls
SportsPredict, never reads `/predictions`, and never submits anything.

The live goal stack currently removes bookmaker margin proportionally. This
audit tests whether simple favorite-longshot-aware alternatives improve Brier
out of sample enough to justify changing that production conversion.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
from pathlib import Path
from typing import Callable

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "odds_conversion_backtest.md"
RAW_CANDIDATES = (ROOT / "data" / "raw" / "fduk", ROOT / "data_reference" / "raw" / "fduk")

ODDS_1X2 = (("PSH", "PSD", "PSA"), ("B365H", "B365D", "B365A"), ("AvgH", "AvgD", "AvgA"))
ODDS_OU = (("P>2.5", "P<2.5"), ("B365>2.5", "B365<2.5"), ("Avg>2.5", "Avg<2.5"))


@dataclass(frozen=True)
class OddsRecord:
    match_date: date
    season: str
    league: str
    home_goals: float
    away_goals: float
    odds_1x2: tuple[float, float, float] | None
    odds_ou25: tuple[float, float] | None


@dataclass(frozen=True)
class MarketSet:
    name: str
    odds: np.ndarray
    outcome: np.ndarray
    groups: tuple[str, ...]
    dates: tuple[date, ...]
    multiclass: bool


def _parse_float(value: str | None) -> float | None:
    try:
        parsed = float((value or "").strip())
    except ValueError:
        return None
    if not math.isfinite(parsed) or parsed <= 1.0:
        return None
    return parsed


def _parse_goal(value: str | None) -> float | None:
    try:
        parsed = float((value or "").strip())
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_date(value: str | None) -> date | None:
    text = (value or "").strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _pick_odds(row: dict[str, str], candidates: tuple[tuple[str, ...], ...]) -> tuple[float, ...] | None:
    for cols in candidates:
        values = tuple(_parse_float(row.get(col)) for col in cols)
        if all(value is not None for value in values):
            return tuple(float(value) for value in values if value is not None)
    return None


def default_raw_dir() -> Path:
    for path in RAW_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError("No Football-Data raw directory found under data/raw/fduk or data_reference/raw/fduk")


def load_records(raw_dir: Path | None = None) -> list[OddsRecord]:
    raw_dir = raw_dir or default_raw_dir()
    rows: list[OddsRecord] = []
    for path in sorted(raw_dir.glob("*.csv")):
        season = path.stem.split("_")[0]
        with path.open(encoding="latin-1", newline="") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                match_date = _parse_date(raw.get("Date"))
                home_goals = _parse_goal(raw.get("FTHG"))
                away_goals = _parse_goal(raw.get("FTAG"))
                if match_date is None or home_goals is None or away_goals is None:
                    continue
                rows.append(
                    OddsRecord(
                        match_date=match_date,
                        season=season,
                        league=raw.get("Div") or path.stem.split("_", 1)[-1],
                        home_goals=home_goals,
                        away_goals=away_goals,
                        odds_1x2=_pick_odds(raw, ODDS_1X2),  # type: ignore[arg-type]
                        odds_ou25=_pick_odds(raw, ODDS_OU),  # type: ignore[arg-type]
                    )
                )
    return sorted(rows, key=lambda row: (row.match_date, row.season, row.league))


def multiplicative_probs(odds: np.ndarray) -> np.ndarray:
    raw = 1.0 / odds
    return raw / raw.sum(axis=1, keepdims=True)


def additive_probs(odds: np.ndarray) -> np.ndarray:
    raw = 1.0 / odds
    margin = (raw.sum(axis=1, keepdims=True) - 1.0) / raw.shape[1]
    probs = np.maximum(raw - margin, 1e-9)
    return probs / probs.sum(axis=1, keepdims=True)


def power_probs(odds: np.ndarray) -> np.ndarray:
    raw = 1.0 / odds
    lo = np.full(len(raw), 0.01)
    hi = np.full(len(raw), 10.0)
    for _ in range(50):
        mid = (lo + hi) / 2.0
        total = (raw ** mid[:, None]).sum(axis=1)
        lo = np.where(total > 1.0, mid, lo)
        hi = np.where(total > 1.0, hi, mid)
    exponent = (lo + hi) / 2.0
    probs = raw ** exponent[:, None]
    return probs / probs.sum(axis=1, keepdims=True)


def odds_ratio_probs(odds: np.ndarray) -> np.ndarray:
    raw = 1.0 / odds
    lo = np.full(len(raw), 0.001)
    hi = np.full(len(raw), 1000.0)
    for _ in range(60):
        mid = (lo + hi) / 2.0
        probs = raw / (mid[:, None] + raw - mid[:, None] * raw)
        total = probs.sum(axis=1)
        lo = np.where(total > 1.0, mid, lo)
        hi = np.where(total > 1.0, hi, mid)
    c = (lo + hi) / 2.0
    probs = raw / (c[:, None] + raw - c[:, None] * raw)
    return probs / probs.sum(axis=1, keepdims=True)


def oo_epc_probs(odds: np.ndarray) -> np.ndarray:
    """Odds-Only Equal-Profitability-Confidence conversion.

    Goto, Takeishi, and Yairi's Algorithm 5 reduces each inverse odd by the
    same number of standard errors.  If that would make any probability
    negative, their fallback is to run multiplicative conversion.
    """

    raw = 1.0 / odds
    sigma = np.sqrt(np.maximum(1.0 - raw, 0.0))
    z = (raw.sum(axis=1, keepdims=True) - 1.0) / np.maximum(sigma.sum(axis=1, keepdims=True), 1e-12)
    candidate = raw - z * sigma
    valid = (candidate > 0.0).all(axis=1)
    probs = multiplicative_probs(odds)
    if bool(valid.any()):
        candidate_valid = candidate[valid]
        probs[valid] = candidate_valid / candidate_valid.sum(axis=1, keepdims=True)
    return probs


def shin_probs(odds: np.ndarray) -> np.ndarray:
    """Shin insider-trading overround removal.

    Formula follows the public `shin` reference implementation by Joseph
    Buchdahl's method family: solve z, then convert each inverse odd with the
    Shin probability equation.  The n=2 closed form avoids the singular
    fixed-point denominator.
    """

    raw = 1.0 / odds
    n = raw.shape[1]
    total = raw.sum(axis=1)
    if n == 2:
        diff = raw[:, 0] - raw[:, 1]
        z = ((total - 1.0) * (diff**2 - total)) / (total * (diff**2 - 1.0))
    else:
        z = np.zeros(len(raw), dtype=float)
        for _ in range(1000):
            previous = z.copy()
            z = (
                np.sqrt(z[:, None] ** 2 + 4.0 * (1.0 - z[:, None]) * raw**2 / total[:, None]).sum(axis=1)
                - 2.0
            ) / (n - 2.0)
            if float(np.max(np.abs(z - previous))) <= 1e-12:
                break
    probs = (np.sqrt(z[:, None] ** 2 + 4.0 * (1.0 - z[:, None]) * raw**2 / total[:, None]) - z[:, None]) / (
        2.0 * (1.0 - z[:, None])
    )
    return probs / probs.sum(axis=1, keepdims=True)


METHODS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "multiplicative": multiplicative_probs,
    "additive": additive_probs,
    "power": power_probs,
    "shin": shin_probs,
    "odds_ratio": odds_ratio_probs,
    "oo_epc": oo_epc_probs,
}

BOOTSTRAP_CANDIDATES = ("power", "oo_epc", "fl_glm_walkforward")
PROMOTION_CANDIDATES = BOOTSTRAP_CANDIDATES


def market_sets(records: list[OddsRecord]) -> dict[str, MarketSet]:
    rows_1x2 = [row for row in records if row.odds_1x2 is not None]
    odds_1x2 = np.array([row.odds_1x2 for row in rows_1x2], dtype=float)
    outcome_1x2 = np.zeros((len(rows_1x2), 3), dtype=float)
    for idx, row in enumerate(rows_1x2):
        if row.home_goals > row.away_goals:
            outcome_1x2[idx, 0] = 1.0
        elif row.home_goals == row.away_goals:
            outcome_1x2[idx, 1] = 1.0
        else:
            outcome_1x2[idx, 2] = 1.0

    rows_ou = [row for row in records if row.odds_ou25 is not None]
    odds_ou = np.array([row.odds_ou25 for row in rows_ou], dtype=float)
    outcome_ou = np.array([float(row.home_goals + row.away_goals > 2.5) for row in rows_ou])

    return {
        "1x2": MarketSet(
            "1x2",
            odds_1x2,
            outcome_1x2,
            tuple(f"{row.season}_{row.league}" for row in rows_1x2),
            tuple(row.match_date for row in rows_1x2),
            True,
        ),
        "over_under_2.5": MarketSet(
            "over_under_2.5",
            odds_ou,
            outcome_ou,
            tuple(f"{row.season}_{row.league}" for row in rows_ou),
            tuple(row.match_date for row in rows_ou),
            False,
        ),
    }


def brier_scores(market: MarketSet, probs: np.ndarray) -> np.ndarray:
    if market.multiclass:
        return ((probs - market.outcome) ** 2).sum(axis=1)
    return (probs[:, 0] - market.outcome) ** 2


def _outcome_matrix(market: MarketSet) -> np.ndarray:
    if market.multiclass:
        return np.asarray(market.outcome, dtype=float)
    outcome = np.asarray(market.outcome, dtype=float)
    return np.column_stack([outcome, 1.0 - outcome])


def fl_glm_probs(odds: np.ndarray, beta: float) -> np.ndarray:
    raw = 1.0 / odds
    # Work in log space so a wide beta search cannot underflow longshot odds.
    logits = beta * np.log(raw)
    logits -= logits.max(axis=1, keepdims=True)
    probs = np.exp(logits)
    return probs / probs.sum(axis=1, keepdims=True)


def _fl_glm_nll(odds: np.ndarray, outcome: np.ndarray, beta: float) -> float:
    probs = np.clip(fl_glm_probs(odds, beta), 1e-12, 1.0)
    return float(-(outcome * np.log(probs)).sum())


def fit_fl_glm_beta(odds: np.ndarray, outcome: np.ndarray, *, lo: float = 0.2, hi: float = 3.0) -> float:
    """Fit the one-parameter FL-GLM with deterministic golden-section search."""

    if len(odds) == 0:
        return 1.0
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    left = lo
    right = hi
    x1 = right - gr * (right - left)
    x2 = left + gr * (right - left)
    f1 = _fl_glm_nll(odds, outcome, x1)
    f2 = _fl_glm_nll(odds, outcome, x2)
    for _ in range(80):
        if f1 > f2:
            left = x1
            x1 = x2
            f1 = f2
            x2 = left + gr * (right - left)
            f2 = _fl_glm_nll(odds, outcome, x2)
        else:
            right = x2
            x2 = x1
            f2 = f1
            x1 = right - gr * (right - left)
            f1 = _fl_glm_nll(odds, outcome, x1)
        if right - left < 1e-6:
            break
    return float((left + right) / 2.0)


def fl_glm_walkforward(
    market: MarketSet,
    *,
    min_training_rows: int = 2000,
) -> tuple[np.ndarray, list[dict[str, str]]]:
    """Score a yearly expanding-window FL-GLM without future leakage."""

    outcome = _outcome_matrix(market)
    years = sorted({value.year for value in market.dates})
    probs = multiplicative_probs(market.odds)
    schedule: list[dict[str, str]] = []
    dates = np.array(market.dates, dtype=object)
    for year in years:
        train_mask = np.array([value.year < year for value in market.dates], dtype=bool)
        test_mask = np.array([value.year == year for value in market.dates], dtype=bool)
        train_rows = int(train_mask.sum())
        test_rows = int(test_mask.sum())
        if test_rows == 0:
            continue
        if train_rows < min_training_rows:
            beta = 1.0
            source = "multiplicative_fallback"
        else:
            beta = fit_fl_glm_beta(market.odds[train_mask], outcome[train_mask])
            probs[test_mask] = fl_glm_probs(market.odds[test_mask], beta)
            source = "trained_past_years"
        first_date = min(dates[test_mask]).isoformat()
        last_date = max(dates[test_mask]).isoformat()
        schedule.append(
            {
                "year": str(year),
                "train_rows": str(train_rows),
                "test_rows": str(test_rows),
                "beta": f"{beta:.6f}",
                "source": source,
                "date_range": f"{first_date} to {last_date}",
            }
        )
    return brier_scores(market, probs), schedule


def score_methods(market: MarketSet) -> dict[str, np.ndarray]:
    scores = {name: brier_scores(market, fn(market.odds)) for name, fn in METHODS.items()}
    fl_scores, _ = fl_glm_walkforward(market)
    scores["fl_glm_walkforward"] = fl_scores
    return scores


def _slice_by_date(market: MarketSet, start: date | None = None) -> np.ndarray:
    if start is None:
        return np.ones(len(market.dates), dtype=bool)
    return np.array([value >= start for value in market.dates], dtype=bool)


def summary_rows(market: MarketSet, scores: dict[str, np.ndarray], *, mask: np.ndarray | None = None) -> list[dict[str, str]]:
    mask = np.ones(len(market.dates), dtype=bool) if mask is None else mask
    base = scores["multiplicative"][mask]
    out = []
    for method, values_all in scores.items():
        values = values_all[mask]
        delta = base - values
        out.append(
            {
                "method": method,
                "n": str(int(mask.sum())),
                "brier": f"{values.mean():.6f}",
                "delta_vs_multiplicative_x100": f"{delta.sum() * 100:.2f}",
                "brier_points_per_1000": f"{delta.mean() * 100000:.3f}",
            }
        )
    return sorted(out, key=lambda row: float(row["brier"]))


def block_bootstrap_delta(
    market: MarketSet,
    scores: dict[str, np.ndarray],
    *,
    challenger: str,
    mask: np.ndarray | None = None,
    trials: int = 2000,
    seed: int = 20260629,
) -> dict[str, float | int]:
    mask = np.ones(len(market.dates), dtype=bool) if mask is None else mask
    deltas = scores["multiplicative"][mask] - scores[challenger][mask]
    groups = np.array(market.groups, dtype=object)[mask]
    labels = sorted(set(groups))
    group_sums = np.array([deltas[groups == label].sum() for label in labels], dtype=float)
    rng = np.random.default_rng(seed)
    sampled = group_sums[rng.integers(0, len(group_sums), size=(trials, len(group_sums)))].sum(axis=1) * 100.0
    point = float(deltas.sum() * 100.0)
    return {
        "groups": len(group_sums),
        "point_delta_x100": point,
        "ci_2.5": float(np.quantile(sampled, 0.025)),
        "ci_97.5": float(np.quantile(sampled, 0.975)),
        "pr_gt_0": float((sampled > 0).mean()),
    }


def _candidate_passes(analysis: dict[str, dict], method: str) -> bool:
    required = []
    for market_name in ("1x2", "over_under_2.5"):
        market = analysis[market_name]
        current = market["methods"][method]
        recent_2021 = market["recent_2021"][method]
        recent_2023 = market["recent_2023"][method]
        boot = market["bootstraps"][method]
        required.append(float(current["brier_points_per_1000"]) > 0.0)
        required.append(float(recent_2021["brier_points_per_1000"]) > 0.0)
        required.append(float(recent_2023["brier_points_per_1000"]) > 0.0)
        required.append(float(boot["ci_2.5"]) > 0.0)
    return all(required)


def decision(analysis: dict[str, dict]) -> str:
    passing = [method for method in PROMOTION_CANDIDATES if _candidate_passes(analysis, method)]
    if passing:
        best = max(
            passing,
            key=lambda method: sum(
                float(analysis[market_name]["methods"][method]["brier_points_per_1000"])
                for market_name in ("1x2", "over_under_2.5")
            ),
        )
        return f"promote_{best}_devig_structural_correction"
    return "not_promoted_keep_multiplicative_devig"


def analyze(records: list[OddsRecord]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, market in market_sets(records).items():
        scores = score_methods(market)
        _, fl_schedule = fl_glm_walkforward(market)
        all_rows = summary_rows(market, scores)
        by_method = {row["method"]: row for row in all_rows}
        recent_2021 = {row["method"]: row for row in summary_rows(market, scores, mask=_slice_by_date(market, date(2021, 1, 1)))}
        recent_2023 = {row["method"]: row for row in summary_rows(market, scores, mask=_slice_by_date(market, date(2023, 1, 1)))}
        bootstraps = {
            method: block_bootstrap_delta(market, scores, challenger=method)
            for method in BOOTSTRAP_CANDIDATES
        }
        out[name] = {
            "rows": len(market.dates),
            "date_min": min(market.dates).isoformat() if market.dates else "?",
            "date_max": max(market.dates).isoformat() if market.dates else "?",
            "methods": by_method,
            "method_rows": all_rows,
            "recent_2021": recent_2021,
            "recent_2023": recent_2023,
            "recent_2021_power": recent_2021["power"],
            "recent_2023_power": recent_2023["power"],
            "recent_2021_rows": list(recent_2021.values()),
            "recent_2023_rows": list(recent_2023.values()),
            "bootstrap_power": bootstraps["power"],
            "bootstraps": bootstraps,
            "fl_glm_schedule": fl_schedule,
        }
    out["decision"] = {"decision": decision(out)}
    return out


def _table(headers: list[str], rows: list[dict[str, str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return lines


def render(analysis: dict[str, dict], *, now: datetime | None = None, raw_dir: Path | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    raw_dir = raw_dir or default_raw_dir()
    lines = [
        "# Odds Conversion Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        f"- raw_data: {raw_dir}",
        "- purpose: test whether overround conversion should change from proportional de-margin to a favorite-longshot-aware method",
        f"- decision: {analysis['decision']['decision']}",
        f"- rows_1x2: {analysis['1x2']['rows']}",
        f"- rows_over_under_2.5: {analysis['over_under_2.5']['rows']}",
        f"- date_range: {analysis['1x2']['date_min']} to {analysis['1x2']['date_max']}",
        "",
        "## Method Definitions",
        "",
        "| method | definition |",
        "| --- | --- |",
        "| multiplicative | proportional overround removal, the current production baseline |",
        "| additive | subtract equal margin from each raw implied probability, then renormalize |",
        "| power | find exponent k so raw_implied_probability^k sums to 1; favorite-longshot-aware |",
        "| shin | solve Shin's insider-trading parameter z, then convert inverse odds to probabilities |",
        "| odds_ratio | solve a common odds-ratio correction so converted probabilities sum to 1 |",
        "| oo_epc | OO-EPC: reduce each inverse odd by the same number of standard errors; fallback to multiplicative if any probability would be negative |",
        "| fl_glm_walkforward | FL-GLM walk-forward: fit one favorite-longshot exponent on past calendar years only, then normalize raw_implied_probability^beta for the held-out year |",
        "",
    ]
    headers = ["method", "n", "brier", "delta_vs_multiplicative_x100", "brier_points_per_1000"]
    for market_name, heading in (("1x2", "1X2 Closing Odds"), ("over_under_2.5", "Over/Under 2.5 Closing Odds")):
        market = analysis[market_name]
        lines.extend([f"## {heading}", "", *_table(headers, market["method_rows"]), ""])
        lines.extend([f"## {heading} Recent Holdout Since 2021", "", *_table(headers, market["recent_2021_rows"]), ""])
        lines.extend([f"## {heading} Recent Holdout Since 2023", "", *_table(headers, market["recent_2023_rows"]), ""])
        lines.extend(
            [
                f"## {heading} FL-GLM Walk-Forward Schedule",
                "",
                *_table(["year", "train_rows", "test_rows", "beta", "source", "date_range"], market["fl_glm_schedule"]),
                "",
            ]
        )
        boot = market["bootstrap_power"]
        lines.extend(
            [
                f"## {heading} Power Bootstrap",
                "",
                "| comparison | groups | point_delta_x100 | ci_2.5 | ci_97.5 | pr_gt_0 |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
                (
                    "| power_minus_multiplicative | "
                    f"{boot['groups']} | {boot['point_delta_x100']:.2f} | {boot['ci_2.5']:.2f} | "
                    f"{boot['ci_97.5']:.2f} | {boot['pr_gt_0']:.3f} |"
                ),
                "",
            ]
        )
        rows = []
        for method in BOOTSTRAP_CANDIDATES:
            boot = market["bootstraps"][method]
            rows.append(
                {
                    "method": method,
                    "groups": str(boot["groups"]),
                    "point_delta_x100": f"{boot['point_delta_x100']:.2f}",
                    "ci_2.5": f"{boot['ci_2.5']:.2f}",
                    "ci_97.5": f"{boot['ci_97.5']:.2f}",
                    "pr_gt_0": f"{boot['pr_gt_0']:.3f}",
                }
            )
        lines.extend(
            [
                f"## {heading} Candidate Bootstrap Summary",
                "",
                *_table(["method", "groups", "point_delta_x100", "ci_2.5", "ci_97.5", "pr_gt_0"], rows),
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation",
            "",
            "- Power de-vig is tested as a small structural odds-conversion correction, not as a catch-up or extremizing layer.",
            "- The effect size is reported in Brier points per 1,000 markets because per-market gains are small.",
            "- A production change still needs source-quality checks on current World Cup odds; this audit only validates the generic conversion step on historical football closing odds.",
            "- If the evidence matrix marks this as promoted, the allowed action is to replace proportional de-margin with power de-vig for bookmaker-style odds only, not to alter field policy or direct-market semantic gates.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=None, help="Football-Data raw CSV directory")
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    raw_dir = args.raw_dir or default_raw_dir()
    analysis = analyze(load_records(raw_dir))
    text = render(analysis, raw_dir=raw_dir)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
