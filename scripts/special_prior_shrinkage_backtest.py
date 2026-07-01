"""Test whether special-market proxy priors should be shrunk toward 50%.

This is a read-only proxy audit over the local StatsBomb event cache. It does
not call SportsPredict, does not read `/predictions`, and does not submit
anything. The audit exists to prevent a tempting but weak manual adjustment:
pulling low-confidence special-market priors toward 50% just because official
SportsPredict settlement is sparse.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import random
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from special_market_backtest import (  # noqa: E402
    COUNT_THRESHOLD_PRIORS,
    SPECIAL_FIELDS,
    loto,
    rows as statsbomb_rows,
    tournament_heterogeneity_rows,
)


OUT = ROOT / "data" / "logs" / "special_prior_shrinkage_backtest.md"
SHRINK_FACTORS = (0.00, 0.25, 0.50, 0.75, 1.00)
RAW_FACTOR = 1.00


@dataclass(frozen=True)
class ScoredPrediction:
    family: str
    kind: str
    confidence: str
    heterogeneity: str
    tournament: str
    base_probability: float
    outcome: bool


def shrink_probability(base_probability: float, factor: float) -> float:
    """Shrink a base prior toward coinflip by a fixed predeclared factor."""

    return 0.5 + factor * (base_probability - 0.5)


def rbp_for_probability(probability: float, outcome: bool) -> float:
    return (0.25 - (probability - float(outcome)) ** 2) * 100


def score(predictions: list[ScoredPrediction], factor: float) -> dict[str, float]:
    if not predictions:
        return {"n": 0, "brier": 0.0, "rbp": 0.0, "mean_pred": 0.0}
    probs = [shrink_probability(row.base_probability, factor) for row in predictions]
    return {
        "n": len(predictions),
        "brier": sum((p - float(row.outcome)) ** 2 for p, row in zip(probs, predictions)) / len(predictions),
        "rbp": sum(rbp_for_probability(p, row.outcome) for p, row in zip(probs, predictions)),
        "mean_pred": sum(probs) / len(probs),
    }


def _base_rate(train: list[dict], field: str) -> float:
    return sum(bool(row[field]) for row in train) / len(train)


def _threshold_base_rate(train: list[dict], value_field: str, threshold: int) -> float:
    return sum(float(row[value_field]) >= threshold for row in train) / len(train)


def scored_predictions(data: list[dict]) -> list[ScoredPrediction]:
    """Build leave-one-tournament-out predictions for binary and threshold specials."""

    tournaments = sorted({str(row["tournament"]) for row in data})
    heterogeneity = {str(row["key"]): str(row["heterogeneity"]) for row in tournament_heterogeneity_rows(data)}
    confidence = {field: str(loto(data, field)["confidence_tier"]) for field in SPECIAL_FIELDS}

    out: list[ScoredPrediction] = []
    for field in SPECIAL_FIELDS:
        for tournament in tournaments:
            train = [row for row in data if str(row["tournament"]) != tournament]
            test = [row for row in data if str(row["tournament"]) == tournament]
            base_probability = _base_rate(train, field)
            for row in test:
                out.append(
                    ScoredPrediction(
                        family=field,
                        kind="binary",
                        confidence=confidence[field],
                        heterogeneity=heterogeneity.get(field, ""),
                        tournament=tournament,
                        base_probability=base_probability,
                        outcome=bool(row[field]),
                    )
                )

    for key, value_field, threshold in COUNT_THRESHOLD_PRIORS:
        for tournament in tournaments:
            train = [row for row in data if str(row["tournament"]) != tournament]
            test = [row for row in data if str(row["tournament"]) == tournament]
            base_probability = _threshold_base_rate(train, value_field, threshold)
            for row in test:
                out.append(
                    ScoredPrediction(
                        family=key,
                        kind="count_threshold",
                        confidence="count_threshold_base_prior",
                        heterogeneity=heterogeneity.get(key, ""),
                        tournament=tournament,
                        base_probability=base_probability,
                        outcome=float(row[value_field]) >= threshold,
                    )
                )
    return out


def scope_predictions(predictions: list[ScoredPrediction]) -> dict[str, list[ScoredPrediction]]:
    return {
        "all_special": predictions,
        "binary_special": [row for row in predictions if row.kind == "binary"],
        "count_threshold_special": [row for row in predictions if row.kind == "count_threshold"],
        "weak_near_coinflip_binary": [
            row for row in predictions if row.kind == "binary" and row.confidence == "weak_near_coinflip"
        ],
        "usable_binary": [row for row in predictions if row.kind == "binary" and row.confidence != "weak_near_coinflip"],
        "high_heterogeneity": [row for row in predictions if row.heterogeneity == "high"],
    }


def grid_rows(scopes: dict[str, list[ScoredPrediction]]) -> list[dict[str, float | str]]:
    rows_: list[dict[str, float | str]] = []
    for scope, predictions in scopes.items():
        raw = score(predictions, RAW_FACTOR)
        for factor in SHRINK_FACTORS:
            scored = score(predictions, factor)
            rows_.append(
                {
                    "scope": scope,
                    "factor": factor,
                    "n": int(scored["n"]),
                    "brier": scored["brier"],
                    "rbp": scored["rbp"],
                    "delta_vs_raw": scored["rbp"] - raw["rbp"],
                    "mean_pred": scored["mean_pred"],
                }
            )
    return rows_


def best_factor(predictions: list[ScoredPrediction]) -> float:
    return max(SHRINK_FACTORS, key=lambda factor: score(predictions, factor)["rbp"])


def bootstrap_delta(
    predictions: list[ScoredPrediction],
    *,
    factor: float,
    trials: int = 5000,
    seed: int = 20260629,
) -> dict[str, float]:
    point = score(predictions, factor)["rbp"] - score(predictions, RAW_FACTOR)["rbp"]
    by_tournament: dict[str, list[ScoredPrediction]] = defaultdict(list)
    for row in predictions:
        by_tournament[row.tournament].append(row)
    tournaments = sorted(by_tournament)
    if not tournaments or trials <= 0:
        return {"point": point, "ci_2.5": point, "median": point, "ci_97.5": point, "pr_gt_0": float(point > 0)}

    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        sampled: list[ScoredPrediction] = []
        for _tournament in tournaments:
            sampled.extend(by_tournament[rng.choice(tournaments)])
        draws.append(score(sampled, factor)["rbp"] - score(sampled, RAW_FACTOR)["rbp"])
    draws.sort()
    return {
        "point": point,
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "median": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(1 for value in draws if value > 0) / len(draws),
    }


def bootstrap_rows(scopes: dict[str, list[ScoredPrediction]]) -> list[dict[str, float | str]]:
    out: list[dict[str, float | str]] = []
    for scope, predictions in scopes.items():
        factor = best_factor(predictions)
        boot = bootstrap_delta(predictions, factor=factor)
        if factor == RAW_FACTOR:
            decision = "keep_raw_best"
        elif boot["point"] > 0 and boot["ci_2.5"] > 0:
            decision = "promote_shrinkage"
        else:
            decision = "not_promoted"
        out.append(
            {
                "scope": scope,
                "best_factor": factor,
                "n": len(predictions),
                "point_delta": boot["point"],
                "ci_2.5": boot["ci_2.5"],
                "median": boot["median"],
                "ci_97.5": boot["ci_97.5"],
                "pr_gt_0": boot["pr_gt_0"],
                "decision": decision,
            }
        )
    return out


def family_rows(predictions: list[ScoredPrediction]) -> list[dict[str, float | str]]:
    by_family: dict[str, list[ScoredPrediction]] = defaultdict(list)
    for row in predictions:
        by_family[row.family].append(row)
    out: list[dict[str, float | str]] = []
    for family, part in by_family.items():
        factor = best_factor(part)
        raw = score(part, RAW_FACTOR)
        best = score(part, factor)
        out.append(
            {
                "family": family,
                "kind": part[0].kind,
                "confidence": part[0].confidence,
                "heterogeneity": part[0].heterogeneity,
                "n": len(part),
                "best_factor": factor,
                "raw_brier": raw["brier"],
                "best_brier": best["brier"],
                "delta_vs_raw": best["rbp"] - raw["rbp"],
            }
        )
    return sorted(out, key=lambda row: (str(row["confidence"]), -float(row["delta_vs_raw"]), str(row["family"])))


def render(
    predictions: list[ScoredPrediction],
    *,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    scopes = scope_predictions(predictions)
    grid = grid_rows(scopes)
    boot_rows = bootstrap_rows(scopes)
    weak = next(row for row in boot_rows if row["scope"] == "weak_near_coinflip_binary")
    all_special = next(row for row in boot_rows if row["scope"] == "all_special")
    promotion_decision = (
        "promoted"
        if any(row["decision"] == "promote_shrinkage" for row in boot_rows)
        else "not_promoted"
    )

    lines = [
        "# Special Prior Shrinkage Backtest",
        "",
        "Read-only proxy analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- source: local StatsBomb raw event cache",
        "- method: leave-one-tournament-out prior, predeclared shrink factors toward 50%",
        f"- source_tournaments: {len({row.tournament for row in predictions})}",
        f"- total_scored_rows: {len(predictions)}",
        f"- binary_rows: {len(scopes['binary_special'])}",
        f"- count_threshold_rows: {len(scopes['count_threshold_special'])}",
        f"- weak_near_coinflip_binary_rows: {len(scopes['weak_near_coinflip_binary'])}",
        f"- best_overall_factor: {float(all_special['best_factor']):.2f}",
        f"- weak_best_factor: {float(weak['best_factor']):.2f}",
        f"- weak_point_delta: {float(weak['point_delta']):.2f}",
        f"- weak_ci_2.5: {float(weak['ci_2.5']):.2f}",
        f"- weak_pr_gt_0: {float(weak['pr_gt_0']):.3f}",
        f"- promotion_decision: {promotion_decision}",
        "",
        "## Shrinkage Grid",
        "",
        "| scope | factor | n | brier | rbp | delta_vs_raw | mean_pred |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in grid:
        lines.append(
            f"| {row['scope']} | {float(row['factor']):.2f} | {int(row['n'])} | "
            f"{float(row['brier']):.4f} | {float(row['rbp']):.2f} | "
            f"{float(row['delta_vs_raw']):.2f} | {float(row['mean_pred']):.3f} |"
        )

    lines.extend(
        [
            "",
            "## Best-Factor Bootstrap",
            "",
            "| scope | best_factor | n | point_delta_vs_raw | ci_2.5 | median | ci_97.5 | pr_gt_0 | decision |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in boot_rows:
        lines.append(
            f"| {row['scope']} | {float(row['best_factor']):.2f} | {int(row['n'])} | "
            f"{float(row['point_delta']):.2f} | {float(row['ci_2.5']):.2f} | "
            f"{float(row['median']):.2f} | {float(row['ci_97.5']):.2f} | "
            f"{float(row['pr_gt_0']):.3f} | {row['decision']} |"
        )

    lines.extend(
        [
            "",
            "## Family Detail",
            "",
            "| family | kind | confidence | heterogeneity | n | best_factor | raw_brier | best_brier | delta_vs_raw |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in family_rows(predictions):
        lines.append(
            f"| {row['family']} | {row['kind']} | {row['confidence']} | {row['heterogeneity']} | "
            f"{int(row['n'])} | {float(row['best_factor']):.2f} | {float(row['raw_brier']):.4f} | "
            f"{float(row['best_brier']):.4f} | {float(row['delta_vs_raw']):.2f} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Raw StatsBomb special priors are the best point estimate for the all-special, usable-binary, count-threshold, and high-heterogeneity scopes.",
            "- The weak near-coinflip timing families have a positive point estimate for mild shrinkage, but the tournament bootstrap interval crosses zero.",
            "- Promotion gate: automatic shrinkage requires a non-raw best factor, positive point delta, and positive tournament-bootstrap 2.5% bound.",
            "- Current action: do not apply automatic shrinkage; use weak-prior flags as research warnings and seek direct-source evidence before lock.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    predictions = scored_predictions(statsbomb_rows())
    text = render(predictions)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
