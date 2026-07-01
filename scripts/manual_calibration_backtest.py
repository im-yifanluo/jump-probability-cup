"""Manual-policy calibration audit.

This read-only diagnostic tests whether the promoted manual policy should get
an additional global calibration layer. It uses only `docs/history.md` plus
optional read-only SportsPredict `/results` settlement for older V1 rows.

It never reads `/predictions`, never reads stored bot values, and never
submits. Candidate calibration layers are intentionally simple: multiply the
distance from 50% by a fixed factor, then score exact official-RBP/Brier deltas.

Usage:
    uv run --with requests python scripts/manual_calibration_backtest.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, brier, clip_prob, official_delta_rbp  # noqa: E402
from manual_policy_backtest import category, combined_prob, load_rows  # noqa: E402


OUT = ROOT / "data" / "logs" / "manual_calibration_backtest.md"
CALIBRATION_FACTORS = (0.70, 0.80, 0.90, 1.00, 1.10, 1.20)


@dataclass(frozen=True)
class WalkForwardPrediction:
    row: HistoryMarket
    base: float
    calibrated: float
    factor: float


def calibrate_probability(p: float, factor: float) -> float:
    return clip_prob(0.5 + factor * (clip_prob(p) - 0.5))


def score_predictions(rows: list[HistoryMarket], probabilities: list[float]) -> dict[str, float]:
    if not rows:
        return {"n": 0, "delta_vs_current": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    return {
        "n": len(rows),
        "delta_vs_current": sum(official_delta_rbp(row, p) for row, p in zip(rows, probabilities)),
        "brier": sum(brier(p, row.outcome or 0) for row, p in zip(rows, probabilities)) / len(rows),
        "mean_p": sum(probabilities) / len(probabilities),
    }


def factor_score(rows: list[HistoryMarket], factor: float) -> dict[str, float]:
    return score_predictions(rows, [calibrate_probability(combined_prob(row), factor) for row in rows])


def best_factor(train_rows: list[HistoryMarket], factors: tuple[float, ...] = CALIBRATION_FACTORS) -> float:
    if not train_rows:
        return 1.0
    scored = []
    for factor in factors:
        probs = [calibrate_probability(combined_prob(row), factor) for row in train_rows]
        br = sum(brier(p, row.outcome or 0) for row, p in zip(train_rows, probs)) / len(train_rows)
        scored.append((br, abs(factor - 1.0), factor))
    return min(scored)[2]


def walk_forward_predictions(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    factors: tuple[float, ...] = CALIBRATION_FACTORS,
) -> list[WalkForwardPrediction]:
    out: list[WalkForwardPrediction] = []
    orders = sorted({row.order for row in rows})
    for order in orders:
        train = [row for row in rows if row.order < order]
        if len(train) < min_train:
            continue
        factor = best_factor(train, factors)
        for row in rows:
            if row.order != order:
                continue
            base = combined_prob(row)
            out.append(
                WalkForwardPrediction(
                    row=row,
                    base=clip_prob(base),
                    calibrated=calibrate_probability(base, factor),
                    factor=factor,
                )
            )
    return out


def paired_delta(predictions: list[WalkForwardPrediction]) -> float:
    return sum(
        100.0
        * (
            brier(pred.base, pred.row.outcome or 0)
            - brier(pred.calibrated, pred.row.outcome or 0)
        )
        for pred in predictions
    )


def bootstrap_paired_delta(
    predictions: list[WalkForwardPrediction],
    *,
    trials: int,
    seed: int,
) -> dict[str, float]:
    by_match: dict[str, list[WalkForwardPrediction]] = defaultdict(list)
    for pred in predictions:
        by_match[pred.row.match].append(pred)
    matches = sorted(by_match)
    if not matches or trials <= 0:
        return {"point": paired_delta(predictions), "ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        sample = []
        for match in (rng.choice(matches) for _ in matches):
            sample.extend(by_match[match])
        draws.append(paired_delta(sample))
    draws.sort()
    return {
        "point": paired_delta(predictions),
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "median": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(1 for value in draws if value > 0) / len(draws),
    }


def reliability_rows(rows: list[HistoryMarket], probabilities: list[float]) -> list[dict]:
    buckets = [
        ("0.00-0.20", 0.0, 0.20),
        ("0.20-0.35", 0.20, 0.35),
        ("0.35-0.45", 0.35, 0.45),
        ("0.45-0.55", 0.45, 0.55),
        ("0.55-0.65", 0.55, 0.65),
        ("0.65-0.80", 0.65, 0.80),
        ("0.80-1.00", 0.80, 1.01),
    ]
    paired = [(row, clip_prob(prob)) for row, prob in zip(rows, probabilities)]
    out = []
    for label, low, high in buckets:
        part = [(row, prob) for row, prob in paired if low <= prob < high]
        if not part:
            continue
        probs = [prob for _, prob in part]
        outcomes = [row.outcome or 0 for row, _ in part]
        out.append(
            {
                "bucket": label,
                "n": len(part),
                "mean_p": sum(probs) / len(probs),
                "hit": sum(outcomes) / len(outcomes),
                "brier": sum(brier(p, o) for p, o in zip(probs, outcomes)) / len(part),
            }
        )
    return out


def reliability_summary(rows: list[HistoryMarket], probabilities: list[float]) -> dict[str, float]:
    if not rows:
        return {"n": 0, "mean_p": float("nan"), "hit": float("nan"), "brier": float("nan"), "ece": float("nan")}
    probs = [clip_prob(prob) for prob in probabilities]
    outcomes = [row.outcome or 0 for row in rows]
    bins = reliability_rows(rows, probs)
    return {
        "n": len(rows),
        "mean_p": sum(probs) / len(probs),
        "hit": sum(outcomes) / len(outcomes),
        "brier": sum(brier(p, o) for p, o in zip(probs, outcomes)) / len(rows),
        "ece": sum(int(row["n"]) * abs(float(row["hit"]) - float(row["mean_p"])) for row in bins) / len(rows),
    }


def reliability_policy_rows(rows: list[HistoryMarket], policies: list[tuple[str, list[float]]]) -> list[dict]:
    out: list[dict] = []
    for policy, probabilities in policies:
        for row in reliability_rows(rows, probabilities):
            out.append({"policy": policy, **row})
    return out


def category_paired_rows(predictions: list[WalkForwardPrediction]) -> list[dict]:
    grouped: dict[str, list[WalkForwardPrediction]] = defaultdict(list)
    for pred in predictions:
        grouped[category(pred.row)].append(pred)
    out = []
    for cat, part in grouped.items():
        out.append(
            {
                "category": cat,
                "n": len(part),
                "paired_delta_vs_combined": paired_delta(part),
                "base_brier": sum(brier(pred.base, pred.row.outcome or 0) for pred in part) / len(part),
                "calibrated_brier": sum(brier(pred.calibrated, pred.row.outcome or 0) for pred in part) / len(part),
                "mean_base": sum(pred.base for pred in part) / len(part),
                "mean_calibrated": sum(pred.calibrated for pred in part) / len(part),
                "hit": sum(pred.row.outcome or 0 for pred in part) / len(part),
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


def render(rows: list[HistoryMarket], *, min_train: int, trials: int, seed: int) -> str:
    fixed = [{"factor": factor, **factor_score(rows, factor)} for factor in CALIBRATION_FACTORS]
    fixed.sort(key=lambda row: row["delta_vs_current"], reverse=True)
    wf = walk_forward_predictions(rows, min_train=min_train)
    base_rows = [pred.row for pred in wf]
    base_score = score_predictions(base_rows, [pred.base for pred in wf])
    cal_score = score_predictions(base_rows, [pred.calibrated for pred in wf])
    comparison = bootstrap_paired_delta(wf, trials=trials, seed=seed)
    factor_counts = Counter(pred.factor for pred in wf)
    full_reliability_policies = [
        ("current", [row.you for row in rows]),
        ("field", [row.crowd for row in rows]),
        ("combined_manual_policy", [combined_prob(row) for row in rows]),
    ]
    holdout_reliability_policies = [
        ("combined_manual_policy", [pred.base for pred in wf]),
        ("walkforward_calibrated", [pred.calibrated for pred in wf]),
    ]

    lines = [
        "# Manual Calibration Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- settled_rows: {len(rows)}",
        f"- matches: {len({row.match for row in rows})}",
        f"- calibration_factors: {', '.join(f'{factor:.2f}' for factor in CALIBRATION_FACTORS)}",
        "- candidate: p_calibrated = 0.5 + factor * (combined_manual_policy - 0.5), clipped to 1%-99%",
        "- promotion_gate: walk-forward paired delta must beat uncalibrated combined policy with positive match-bootstrap lower bound",
        "",
        "## Full-Sample Fixed Factors",
        "",
    ]
    lines += md_table(
        ["factor", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [
                f"{row['factor']:.2f}",
                f"{int(row['n'])}",
                f"{float(row['delta_vs_current']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
            ]
            for row in fixed
        ],
    )
    lines += [
        "",
        "## Full-Sample Reliability Summary",
        "",
    ]
    lines += md_table(
        ["policy", "n", "mean_p", "hit", "brier", "ece_7bin"],
        [
            [
                policy,
                f"{int(row['n'])}",
                f"{float(row['mean_p']):.3f}",
                f"{float(row['hit']):.3f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['ece']):.3f}",
            ]
            for policy, probabilities in full_reliability_policies
            for row in [reliability_summary(rows, probabilities)]
        ],
    )
    lines += [
        "",
        "## Full-Sample Reliability By Bucket",
        "",
    ]
    lines += md_table(
        ["policy", "bucket", "n", "mean_p", "hit", "abs_error", "brier"],
        [
            [
                row["policy"],
                row["bucket"],
                f"{int(row['n'])}",
                f"{float(row['mean_p']):.3f}",
                f"{float(row['hit']):.3f}",
                f"{abs(float(row['hit']) - float(row['mean_p'])):.3f}",
                f"{float(row['brier']):.4f}",
            ]
            for row in reliability_policy_rows(rows, full_reliability_policies)
        ],
    )
    lines += [
        "",
        "## Walk-Forward Calibration",
        "",
        f"- min_train_rows: {min_train}",
        f"- holdout_rows: {len(wf)}",
        f"- selected_factors: "
        + ", ".join(f"{factor:.2f}:{count}" for factor, count in sorted(factor_counts.items())),
        "",
    ]
    lines += md_table(
        ["policy", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [
                "combined_manual_policy",
                f"{int(base_score['n'])}",
                f"{base_score['delta_vs_current']:.2f}",
                f"{base_score['brier']:.4f}",
                f"{base_score['mean_p']:.3f}",
            ],
            [
                "walkforward_calibrated",
                f"{int(cal_score['n'])}",
                f"{cal_score['delta_vs_current']:.2f}",
                f"{cal_score['brier']:.4f}",
                f"{cal_score['mean_p']:.3f}",
            ],
        ],
    )
    lines += [
        "",
        "## Walk-Forward Reliability Summary",
        "",
    ]
    lines += md_table(
        ["policy", "n", "mean_p", "hit", "brier", "ece_7bin"],
        [
            [
                policy,
                f"{int(row['n'])}",
                f"{float(row['mean_p']):.3f}",
                f"{float(row['hit']):.3f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['ece']):.3f}",
            ]
            for policy, probabilities in holdout_reliability_policies
            for row in [reliability_summary(base_rows, probabilities)]
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
        "## Walk-Forward Category Contribution",
        "",
    ]
    lines += md_table(
        ["category", "n", "paired_delta", "base_brier", "calibrated_brier", "base", "calibrated", "hit"],
        [
            [
                row["category"],
                f"{int(row['n'])}",
                f"{float(row['paired_delta_vs_combined']):.2f}",
                f"{float(row['base_brier']):.4f}",
                f"{float(row['calibrated_brier']):.4f}",
                f"{float(row['mean_base']):.3f}",
                f"{float(row['mean_calibrated']):.3f}",
                f"{float(row['hit']):.3f}",
            ]
            for row in category_paired_rows(wf)
        ],
    )
    decision = (
        "promote"
        if comparison["point"] > 0 and comparison["ci_2.5"] > 0
        else "reject"
    )
    lines += [
        "",
        "## Interpretation",
        "",
        f"- decision: {decision} global calibration layer.",
        "- Full-sample factor ranking is diagnostic only; the deployment decision uses chronological walk-forward scoring.",
        "- If rejected, keep the promoted family-specific manual policy and keep monitoring calibration as more settled rows arrive.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=120)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=53)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_rows(sports_predict=args.sports_predict)
    text = render(rows, min_train=args.min_train, trials=args.bootstrap, seed=args.seed)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
