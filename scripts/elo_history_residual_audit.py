"""Audit whether recent Elo history adds signal for national-team outcomes.

This is a local, read-only research script. It uses the bundled international
results CSV only, never calls SportsPredict, never reads `/predictions`, and
never submits anything.

The live manual goal stack is market-anchored, so this script is not allowed to
override bookmaker/Polymarket prices. Its purpose is narrower: test whether a
recent Elo-trajectory feature is good enough to keep as an unpriced-market
fallback candidate or as a future residual feature once archived odds joins are
available.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import math
from typing import Iterable

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data_reference" / "raw" / "internationals" / "results.csv"
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "elo_history_residual_audit.md"

MAJOR_TOURNAMENTS = {
    "FIFA World Cup",
    "FIFA World Cup qualification",
    "UEFA Euro",
    "UEFA Euro qualification",
    "Copa América",
    "Gold Cup",
    "African Cup of Nations",
    "AFC Asian Cup",
    "CONCACAF Nations League",
    "UEFA Nations League",
}


@dataclass(frozen=True)
class InternationalMatch:
    match_date: date
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    tournament: str
    neutral: bool

    @property
    def year(self) -> int:
        return self.match_date.year

    @property
    def outcome_index(self) -> int:
        if self.home_score > self.away_score:
            return 0
        if self.home_score == self.away_score:
            return 1
        return 2

    @property
    def result_score(self) -> float:
        if self.home_score > self.away_score:
            return 1.0
        if self.home_score == self.away_score:
            return 0.5
        return 0.0


def load_matches(path: Path = RAW) -> list[InternationalMatch]:
    rows: list[InternationalMatch] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            try:
                match_date = datetime.strptime(raw["date"], "%Y-%m-%d").date()
                home_score = int(raw["home_score"])
                away_score = int(raw["away_score"])
            except (KeyError, ValueError):
                continue
            rows.append(
                InternationalMatch(
                    match_date=match_date,
                    home_team=raw["home_team"].strip(),
                    away_team=raw["away_team"].strip(),
                    home_score=home_score,
                    away_score=away_score,
                    tournament=raw["tournament"].strip(),
                    neutral=raw["neutral"].strip().upper() == "TRUE",
                )
            )
    return sorted(rows, key=lambda row: (row.match_date, row.home_team, row.away_team))


def _expected_score(diff: float, *, scale: float = 400.0) -> float:
    return 1.0 / (1.0 + 10.0 ** (-diff / scale))


def probs_from_expected(expected: float, *, diff: float, draw_base: float, draw_scale: float = 650.0) -> np.ndarray:
    max_draw = max(0.0, 2.0 * min(expected, 1.0 - expected) - 1e-9)
    draw = min(max_draw, max(0.02, draw_base * math.exp(-abs(diff) / draw_scale)))
    home = max(1e-9, expected - 0.5 * draw)
    away = max(1e-9, 1.0 - expected - 0.5 * draw)
    probs = np.array([home, draw, away], dtype=float)
    return probs / probs.sum()


def brier_multiclass(probs: np.ndarray, outcome_index: int) -> float:
    target = np.zeros(3, dtype=float)
    target[outcome_index] = 1.0
    return float(((probs - target) ** 2).sum())


def _k_factor(tournament: str) -> float:
    if tournament == "Friendly":
        return 10.0
    if tournament in {"FIFA World Cup", "UEFA Euro", "Copa América"}:
        return 30.0
    return 20.0


def _history_delta(history: deque[float], current_rating: float, lookback: int) -> float:
    if len(history) < lookback:
        return 0.0
    return current_rating - list(history)[-lookback]


def _base_probs(counts: Counter[int]) -> np.ndarray:
    total = sum(counts.values()) + 3.0
    return np.array(
        [
            (counts.get(0, 0) + 1.0) / total,
            (counts.get(1, 0) + 1.0) / total,
            (counts.get(2, 0) + 1.0) / total,
        ],
        dtype=float,
    )


def replay(
    matches: list[InternationalMatch],
    *,
    start_year: int = 1994,
    lookback: int = 10,
    momentum_weights: tuple[float, ...] = (-0.5, 0.0, 0.25, 0.5, 0.75),
    home_advantage: float = 65.0,
) -> dict[str, list[dict[str, float | int | str]]]:
    ratings: defaultdict[str, float] = defaultdict(lambda: 1500.0)
    histories: defaultdict[str, deque[float]] = defaultdict(lambda: deque(maxlen=max(lookback + 1, 2)))
    outcome_counts: Counter[int] = Counter()
    model_scores: dict[str, list[dict[str, float | int | str]]] = defaultdict(list)

    for match in matches:
        for team in (match.home_team, match.away_team):
            if not histories[team]:
                histories[team].append(ratings[team])

        scored = match.year >= start_year and sum(outcome_counts.values()) >= 100
        draw_base = (outcome_counts.get(1, 0) + 1.0) / (sum(outcome_counts.values()) + 3.0)
        home_edge = 0.0 if match.neutral else home_advantage
        home_rating = ratings[match.home_team]
        away_rating = ratings[match.away_team]
        home_delta = _history_delta(histories[match.home_team], home_rating, lookback)
        away_delta = _history_delta(histories[match.away_team], away_rating, lookback)

        if scored:
            base = _base_probs(outcome_counts)
            model_scores["rolling_base"].append(
                {
                    "year": match.year,
                    "tournament": match.tournament,
                    "brier": brier_multiclass(base, match.outcome_index),
                }
            )
            for weight in momentum_weights:
                adjusted_home = home_rating + weight * home_delta
                adjusted_away = away_rating + weight * away_delta
                diff = adjusted_home + home_edge - adjusted_away
                probs = probs_from_expected(_expected_score(diff), diff=diff, draw_base=draw_base)
                model_scores[f"elo_history_w{weight:g}"].append(
                    {
                        "year": match.year,
                        "tournament": match.tournament,
                        "brier": brier_multiclass(probs, match.outcome_index),
                    }
                )

        diff_for_update = home_rating + home_edge - away_rating
        expected = _expected_score(diff_for_update)
        change = _k_factor(match.tournament) * (match.result_score - expected)
        ratings[match.home_team] += change
        ratings[match.away_team] -= change
        histories[match.home_team].append(ratings[match.home_team])
        histories[match.away_team].append(ratings[match.away_team])
        outcome_counts[match.outcome_index] += 1

    return model_scores


def _summarize(rows: list[dict[str, float | int | str]], *, mask: Iterable[bool] | None = None) -> dict[str, float | int | str]:
    selected = rows if mask is None else [row for row, keep in zip(rows, mask) if keep]
    if not selected:
        return {"n": 0, "brier": float("nan")}
    return {"n": len(selected), "brier": float(np.mean([float(row["brier"]) for row in selected]))}


def _summary_table(scores: dict[str, list[dict[str, float | int | str]]], *, base_model: str = "rolling_base") -> list[dict[str, str]]:
    base_brier = float(_summarize(scores[base_model])["brier"])
    rows = []
    for model, scored in scores.items():
        summary = _summarize(scored)
        delta = (base_brier - float(summary["brier"])) * int(summary["n"]) * 100.0
        rows.append(
            {
                "model": model,
                "n": str(summary["n"]),
                "brier": f"{summary['brier']:.6f}",
                "delta_vs_rolling_base_x100": f"{delta:.2f}",
                "brier_points_per_1000": f"{(base_brier - float(summary['brier'])) * 100000:.3f}",
            }
        )
    return sorted(rows, key=lambda row: float(row["brier"]))


def _filtered_table(
    scores: dict[str, list[dict[str, float | int | str]]],
    predicate,
    *,
    base_model: str = "rolling_base",
) -> list[dict[str, str]]:
    filtered = {model: [row for row in rows if predicate(row)] for model, rows in scores.items()}
    return _summary_table(filtered, base_model=base_model)


def _bootstrap_delta(
    challenger_rows: list[dict[str, float | int | str]],
    base_rows: list[dict[str, float | int | str]],
    *,
    trials: int = 2000,
    seed: int = 20260629,
) -> dict[str, float | int]:
    by_year: dict[int, float] = defaultdict(float)
    for challenger, base in zip(challenger_rows, base_rows):
        by_year[int(challenger["year"])] += (float(base["brier"]) - float(challenger["brier"]))
    years = sorted(by_year)
    values = np.array([by_year[year] for year in years], dtype=float)
    rng = np.random.default_rng(seed)
    sampled = values[rng.integers(0, len(values), size=(trials, len(values)))].sum(axis=1) * 100.0
    return {
        "years": len(years),
        "point_delta_x100": float(values.sum() * 100.0),
        "ci_2.5": float(np.quantile(sampled, 0.025)),
        "ci_97.5": float(np.quantile(sampled, 0.975)),
        "pr_gt_0": float((sampled > 0).mean()),
    }


def analyze(matches: list[InternationalMatch]) -> dict[str, object]:
    scores = replay(matches)
    all_rows = _summary_table(scores)
    modern_recent = _filtered_table(scores, lambda row: int(row["year"]) >= 2021)
    major_rows = _filtered_table(scores, lambda row: str(row["tournament"]) in MAJOR_TOURNAMENTS)
    elo_base = next(row for row in all_rows if row["model"] == "elo_history_w0")
    best_history = min((row for row in all_rows if row["model"].startswith("elo_history_w")), key=lambda row: float(row["brier"]))
    boot = _bootstrap_delta(scores[best_history["model"]], scores["elo_history_w0"])
    recent_best = next((row for row in modern_recent if row["model"] == best_history["model"]), {})
    major_best = next((row for row in major_rows if row["model"] == best_history["model"]), {})
    recent_base = next((row for row in modern_recent if row["model"] == "elo_history_w0"), {})
    major_base = next((row for row in major_rows if row["model"] == "elo_history_w0"), {})
    history_improves = (
        best_history["model"] != "elo_history_w0"
        and float(best_history["brier"]) < float(elo_base["brier"])
        and float(recent_best.get("brier", "inf")) < float(recent_base.get("brier", "inf"))
        and float(major_best.get("brier", "inf")) < float(major_base.get("brier", "inf"))
        and float(boot["ci_2.5"]) > 0.0
    )
    decision = (
        "candidate_history_feature_positive_unpriced_only"
        if history_improves
        else "not_promoted_keep_market_anchored_goal_stack"
    )
    return {
        "rows": len(matches),
        "scored_rows": len(scores["rolling_base"]),
        "date_min": matches[0].match_date.isoformat() if matches else "?",
        "date_max": matches[-1].match_date.isoformat() if matches else "?",
        "all_rows": all_rows,
        "recent_rows": modern_recent,
        "major_rows": major_rows,
        "best_history": best_history["model"],
        "history_vs_elo_bootstrap": boot,
        "decision": decision,
    }


def _table(headers: list[str], rows: list[dict[str, str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return lines


def render(analysis: dict[str, object], *, now: datetime | None = None, raw_path: Path = RAW) -> str:
    now = now or datetime.now(timezone.utc)
    boot = analysis["history_vs_elo_bootstrap"]
    assert isinstance(boot, dict)
    lines = [
        "# Elo History Residual Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        f"- raw_data: {raw_path}",
        "- purpose: test whether recent Elo trajectory improves national-team 1X2 probabilities before considering it as a residual/fallback feature",
        f"- decision: {analysis['decision']}",
        f"- source_rows: {analysis['rows']}",
        f"- scored_rows: {analysis['scored_rows']}",
        f"- date_range: {analysis['date_min']} to {analysis['date_max']}",
        f"- best_history_model: {analysis['best_history']}",
        "",
        "## Model Definitions",
        "",
        "| model | definition |",
        "| --- | --- |",
        "| rolling_base | chronological global 1X2 base rates with Dirichlet smoothing |",
        "| elo_history_w0 | ordinary chronological Elo with no recent-history adjustment |",
        "| elo_history_wX | ordinary Elo plus X times each team's rating change over its previous 10 matches |",
        "",
        "## All Scored Matches Since 1994",
        "",
        *_table(
            ["model", "n", "brier", "delta_vs_rolling_base_x100", "brier_points_per_1000"],
            analysis["all_rows"],  # type: ignore[arg-type]
        ),
        "",
        "## Recent Holdout Since 2021",
        "",
        *_table(
            ["model", "n", "brier", "delta_vs_rolling_base_x100", "brier_points_per_1000"],
            analysis["recent_rows"],  # type: ignore[arg-type]
        ),
        "",
        "## Major Tournament And Qualifier Rows",
        "",
        *_table(
            ["model", "n", "brier", "delta_vs_rolling_base_x100", "brier_points_per_1000"],
            analysis["major_rows"],  # type: ignore[arg-type]
        ),
        "",
        "## Best History Model Vs Plain Elo Year Bootstrap",
        "",
        "| comparison | years | point_delta_x100 | ci_2.5 | ci_97.5 | pr_gt_0 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {analysis['best_history']}_minus_elo_history_w0 | {boot['years']} | "
            f"{boot['point_delta_x100']:.2f} | {boot['ci_2.5']:.2f} | "
            f"{boot['ci_97.5']:.2f} | {boot['pr_gt_0']:.3f} |"
        ),
        "",
        "## Interpretation",
        "",
        "- This is not a live override for priced goal/outcome markets; public odds remain the primary anchor.",
        "- A recent-Elo feature can only be promoted for live manual entries after it beats the market-anchored goal stack on an archived odds-residual replay.",
        "- If the decision is not promoted, use this report only as evidence that the Elo-history candidate was tested rather than assumed.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW, help="international results CSV")
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    analysis = analyze(load_matches(args.raw))
    text = render(analysis, raw_path=args.raw)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
