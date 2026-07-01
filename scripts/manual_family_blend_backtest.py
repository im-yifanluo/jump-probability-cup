"""Category-wise model/field linear-pool backtest.

Forecast-combination research supports simple linear pools as useful
probability-forecast candidates, but they are easy to overfit on small sports
samples. This diagnostic tests a predeclared adaptive pool:

    p = weight_on_model * model + (1 - weight_on_model) * field

Weights are selected only from earlier settled rows, by broad manual category
when enough category history exists and otherwise from the global settled
history. The deployment gate is intentionally strict: the walk-forward
category pool must beat the promoted combined manual policy with a positive
match-bootstrap lower bound.

With `--sports-predict`, SportsPredict is used only for GET `/events`,
`/lobbies`, and `/results` to settle older V1 history rows. The script never
reads `/predictions` and never submits.

Usage:
    uv run --with requests python scripts/manual_family_blend_backtest.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, brier, clip_prob, official_delta_rbp  # noqa: E402
from manual_policy_backtest import category, combined_prob, load_rows, score  # noqa: E402


OUT = ROOT / "data" / "logs" / "manual_family_blend_backtest.md"
BLEND_WEIGHTS = (0.0, 0.25, 0.50, 0.75, 1.0)


@dataclass(frozen=True)
class BlendPrediction:
    row: HistoryMarket
    category: str
    base: float
    blended: float
    weight_on_model: float
    source: str


def blend_model_field(row: HistoryMarket, weight_on_model: float) -> float:
    return clip_prob(weight_on_model * row.you + (1.0 - weight_on_model) * row.crowd)


def best_weight(rows: list[HistoryMarket], weights: tuple[float, ...] = BLEND_WEIGHTS) -> float:
    """Choose the best model/field weight by Brier, tied toward equal blend."""

    if not rows:
        return 0.50
    scored = []
    for weight in weights:
        br = sum(brier(blend_model_field(row, weight), row.outcome or 0) for row in rows) / len(rows)
        scored.append((br, abs(weight - 0.50), weight))
    return min(scored)[2]


def walk_forward_predictions(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    min_category_train: int,
    weights: tuple[float, ...] = BLEND_WEIGHTS,
) -> list[BlendPrediction]:
    out: list[BlendPrediction] = []
    orders = sorted({row.order for row in rows})
    for order in orders:
        train = [row for row in rows if row.order < order]
        if len(train) < min_train:
            continue
        global_weight = best_weight(train, weights)
        by_category: dict[str, list[HistoryMarket]] = defaultdict(list)
        for row in train:
            by_category[category(row)].append(row)
        category_weights = {
            cat: best_weight(part, weights)
            for cat, part in by_category.items()
            if len(part) >= min_category_train
        }
        for row in rows:
            if row.order != order:
                continue
            cat = category(row)
            if cat in category_weights:
                weight = category_weights[cat]
                source = "category"
            else:
                weight = global_weight
                source = "global"
            out.append(
                BlendPrediction(
                    row=row,
                    category=cat,
                    base=clip_prob(combined_prob(row)),
                    blended=blend_model_field(row, weight),
                    weight_on_model=weight,
                    source=source,
                )
            )
    return out


def score_predictions(rows: list[HistoryMarket], probabilities: list[float]) -> dict[str, float]:
    if not rows:
        return {"n": 0, "delta_vs_current": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    probs = [clip_prob(p) for p in probabilities]
    return {
        "n": len(rows),
        "delta_vs_current": sum(official_delta_rbp(row, p) for row, p in zip(rows, probs)),
        "brier": sum(brier(p, row.outcome or 0) for row, p in zip(rows, probs)) / len(rows),
        "mean_p": sum(probs) / len(probs),
    }


def paired_delta(predictions: list[BlendPrediction]) -> float:
    return sum(
        100.0
        * (
            brier(pred.base, pred.row.outcome or 0)
            - brier(pred.blended, pred.row.outcome or 0)
        )
        for pred in predictions
    )


def bootstrap_paired_delta(
    predictions: list[BlendPrediction],
    *,
    trials: int,
    seed: int,
) -> dict[str, float]:
    by_match: dict[str, list[BlendPrediction]] = defaultdict(list)
    for pred in predictions:
        by_match[pred.row.match].append(pred)
    matches = sorted(by_match)
    point = paired_delta(predictions)
    if not matches or trials <= 0:
        return {"point": point, "ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        sample = []
        for match in (rng.choice(matches) for _ in matches):
            sample.extend(by_match[match])
        draws.append(paired_delta(sample))
    draws.sort()
    return {
        "point": point,
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "median": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(1 for value in draws if value > 0) / len(draws),
    }


def fixed_global_table(rows: list[HistoryMarket]) -> list[dict]:
    table = []
    for weight in BLEND_WEIGHTS:
        table.append(
            {
                "policy": f"global_linear_pool_w{weight:.2f}",
                "weight_on_model": weight,
                **score_predictions(rows, [blend_model_field(row, weight) for row in rows]),
            }
        )
    combined = score(rows, combined_prob)
    table.append(
        {
            "policy": "combined_manual_policy",
            "weight_on_model": float("nan"),
            "n": combined["n"],
            "delta_vs_current": combined["delta"],
            "brier": combined["brier"],
            "mean_p": combined["mean_p"],
        }
    )
    return sorted(table, key=lambda row: row["delta_vs_current"], reverse=True)


def full_sample_category_weights(rows: list[HistoryMarket]) -> list[dict]:
    grouped: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        grouped[category(row)].append(row)
    out = []
    for cat, part in grouped.items():
        weight = best_weight(part)
        out.append(
            {
                "category": cat,
                "n": len(part),
                "weight_on_model": weight,
                "linear_pool_brier": score_predictions(part, [blend_model_field(row, weight) for row in part])["brier"],
                "combined_brier": score_predictions(part, [combined_prob(row) for row in part])["brier"],
                "paired_delta_vs_combined": sum(
                    100.0
                    * (
                        brier(combined_prob(row), row.outcome or 0)
                        - brier(blend_model_field(row, weight), row.outcome or 0)
                    )
                    for row in part
                ),
            }
        )
    return sorted(out, key=lambda row: row["paired_delta_vs_combined"], reverse=True)


def category_paired_rows(predictions: list[BlendPrediction]) -> list[dict]:
    grouped: dict[str, list[BlendPrediction]] = defaultdict(list)
    for pred in predictions:
        grouped[pred.category].append(pred)
    out = []
    for cat, part in grouped.items():
        out.append(
            {
                "category": cat,
                "n": len(part),
                "paired_delta_vs_combined": paired_delta(part),
                "combined_brier": sum(brier(pred.base, pred.row.outcome or 0) for pred in part) / len(part),
                "linear_pool_brier": sum(brier(pred.blended, pred.row.outcome or 0) for pred in part) / len(part),
                "mean_weight": sum(pred.weight_on_model for pred in part) / len(part),
                "category_source_rows": sum(1 for pred in part if pred.source == "category"),
                "global_source_rows": sum(1 for pred in part if pred.source == "global"),
            }
        )
    return sorted(out, key=lambda row: row["paired_delta_vs_combined"], reverse=True)


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _fmt_weight(weight: float) -> str:
    return "n/a" if weight != weight else f"{weight:.2f}"


def render(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    min_category_train: int,
    trials: int,
    seed: int,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    predictions = walk_forward_predictions(
        rows,
        min_train=min_train,
        min_category_train=min_category_train,
    )
    holdout_rows = [pred.row for pred in predictions]
    combined_score = score_predictions(holdout_rows, [pred.base for pred in predictions])
    blend_score = score_predictions(holdout_rows, [pred.blended for pred in predictions])
    comparison = bootstrap_paired_delta(predictions, trials=trials, seed=seed)
    source_counts = Counter(pred.source for pred in predictions)
    weight_counts = Counter((pred.source, pred.weight_on_model) for pred in predictions)
    decision = (
        "promoted"
        if comparison["point"] > 0 and comparison["ci_2.5"] > 0
        else "not_promoted"
    )

    lines = [
        "# Manual Family Blend Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- settled_rows: {len(rows)}",
        f"- matches: {len({row.match for row in rows})}",
        f"- blend_weights: {', '.join(f'{weight:.2f}' for weight in BLEND_WEIGHTS)}",
        f"- min_train_rows: {min_train}",
        f"- min_category_train_rows: {min_category_train}",
        "- candidate: one-match-ahead category-wise linear pool of submitted model probability and field average",
        "- fallback: use a global walk-forward blend weight when a category lacks enough prior rows",
        "- promotion_gate: paired walk-forward delta versus combined manual policy must have positive match-bootstrap lower bound",
        f"- decision: {decision}",
        "",
        "## Full-Sample Global Linear Pools",
        "",
    ]
    lines += md_table(
        ["policy", "weight_on_model", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [
                str(row["policy"]),
                _fmt_weight(float(row["weight_on_model"])),
                str(row["n"]),
                f"{float(row['delta_vs_current']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
            ]
            for row in fixed_global_table(rows)
        ],
    )
    lines += [
        "",
        "## Full-Sample Category Oracle Diagnostic",
        "",
        "This table is diagnostic only; these weights are fit on the same rows they score.",
        "",
    ]
    lines += md_table(
        ["category", "n", "weight_on_model", "paired_delta_vs_combined", "combined_brier", "linear_pool_brier"],
        [
            [
                str(row["category"]),
                str(row["n"]),
                f"{float(row['weight_on_model']):.2f}",
                f"{float(row['paired_delta_vs_combined']):.2f}",
                f"{float(row['combined_brier']):.4f}",
                f"{float(row['linear_pool_brier']):.4f}",
            ]
            for row in full_sample_category_weights(rows)
        ],
    )
    lines += [
        "",
        "## Walk-Forward Result",
        "",
    ]
    lines += md_table(
        ["policy", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [
                "combined_manual_policy",
                str(combined_score["n"]),
                f"{combined_score['delta_vs_current']:.2f}",
                f"{combined_score['brier']:.4f}",
                f"{combined_score['mean_p']:.3f}",
            ],
            [
                "walkforward_category_linear_pool",
                str(blend_score["n"]),
                f"{blend_score['delta_vs_current']:.2f}",
                f"{blend_score['brier']:.4f}",
                f"{blend_score['mean_p']:.3f}",
            ],
        ],
    )
    lines += [
        "",
        "## Paired Delta Versus Combined Policy",
        "",
        f"- point_delta: {comparison['point']:.2f}",
        f"- ci_2.5: {comparison['ci_2.5']:.2f}",
        f"- median: {comparison['median']:.2f}",
        f"- ci_97.5: {comparison['ci_97.5']:.2f}",
        f"- pr_gt_0: {comparison['pr_gt_0']:.3f}",
        "",
        "## Weight Usage",
        "",
    ]
    lines += md_table(
        ["source", "weight_on_model", "rows"],
        [
            [source, f"{weight:.2f}", str(count)]
            for (source, weight), count in sorted(weight_counts.items(), key=lambda item: (item[0][0], item[0][1]))
        ],
    )
    lines += [
        "",
        "## Category Contribution",
        "",
    ]
    lines += md_table(
        [
            "category",
            "n",
            "paired_delta_vs_combined",
            "combined_brier",
            "linear_pool_brier",
            "mean_weight",
            "category_source",
            "global_source",
        ],
        [
            [
                str(row["category"]),
                str(row["n"]),
                f"{float(row['paired_delta_vs_combined']):.2f}",
                f"{float(row['combined_brier']):.4f}",
                f"{float(row['linear_pool_brier']):.4f}",
                f"{float(row['mean_weight']):.2f}",
                str(row["category_source_rows"]),
                str(row["global_source_rows"]),
            ]
            for row in category_paired_rows(predictions)
        ],
    )
    lines += [
        "",
        "## Interpretation",
        "",
        f"- source_counts: {dict(source_counts)}",
        "- A full-sample category oracle can identify research leads, but only the walk-forward section is deployable evidence.",
        "- If not promoted, keep the existing family-specific manual policy and use this report as evidence against replacing it with adaptive category linear pooling.",
        "- This report tests field/model combination only. It does not replace fresh odds, lineup, prop-market, referee, or current-source execution checks.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=120)
    ap.add_argument("--min-category-train", type=int, default=20)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=47)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_rows(sports_predict=args.sports_predict)
    text = render(
        rows,
        min_train=args.min_train,
        min_category_train=args.min_category_train,
        trials=args.bootstrap,
        seed=args.seed,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
