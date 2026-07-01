"""Brier reliability/resolution decomposition for manual policies.

This read-only diagnostic separates Brier score into calibration/reliability,
resolution, and uncertainty components using predeclared probability buckets.
It does not promote a probability transform; it explains whether weak
calibration buckets are actual reliability problems or noisy low-resolution
slices.

Usage:
    uv run --with requests python scripts/brier_decomposition_audit.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, brier, clip_prob  # noqa: E402
from manual_policy_backtest import category, combined_prob, load_rows  # noqa: E402


OUT = ROOT / "data" / "logs" / "brier_decomposition_audit.md"
BUCKETS = (
    ("0.00-0.20", 0.0, 0.20),
    ("0.20-0.35", 0.20, 0.35),
    ("0.35-0.45", 0.35, 0.45),
    ("0.45-0.55", 0.45, 0.55),
    ("0.55-0.65", 0.55, 0.65),
    ("0.65-0.80", 0.65, 0.80),
    ("0.80-1.00", 0.80, 1.01),
)


@dataclass(frozen=True)
class BucketRow:
    bucket: str
    n: int
    mean_p: float
    hit: float
    brier: float
    reliability_component: float
    resolution_component: float


def _outcome(row: HistoryMarket) -> int:
    return int(row.outcome or 0)


def _bucket(probability: float) -> tuple[str, float, float]:
    p = clip_prob(probability)
    for label, low, high in BUCKETS:
        if low <= p < high:
            return label, low, high
    return BUCKETS[-1]


def bucket_rows(rows: list[HistoryMarket], probabilities: list[float]) -> list[BucketRow]:
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have the same length")
    if not rows:
        return []

    overall_hit = sum(_outcome(row) for row in rows) / len(rows)
    grouped: dict[str, list[tuple[HistoryMarket, float]]] = defaultdict(list)
    for row, probability in zip(rows, probabilities):
        label, _low, _high = _bucket(probability)
        grouped[label].append((row, clip_prob(probability)))

    out: list[BucketRow] = []
    for label, _low, _high in BUCKETS:
        part = grouped.get(label, [])
        if not part:
            continue
        probs = [prob for _row, prob in part]
        outcomes = [_outcome(row) for row, _prob in part]
        mean_p = sum(probs) / len(probs)
        hit = sum(outcomes) / len(outcomes)
        weight = len(part) / len(rows)
        out.append(
            BucketRow(
                bucket=label,
                n=len(part),
                mean_p=mean_p,
                hit=hit,
                brier=sum(brier(prob, outcome) for prob, outcome in zip(probs, outcomes)) / len(part),
                reliability_component=weight * (mean_p - hit) ** 2,
                resolution_component=weight * (hit - overall_hit) ** 2,
            )
        )
    return out


def decomposition(rows: list[HistoryMarket], probabilities: list[float]) -> dict[str, float]:
    if len(rows) != len(probabilities):
        raise ValueError("rows and probabilities must have the same length")
    if not rows:
        return {
            "n": 0,
            "mean_p": float("nan"),
            "hit": float("nan"),
            "brier": float("nan"),
            "uncertainty": float("nan"),
            "reliability": float("nan"),
            "resolution": float("nan"),
            "decomposed_brier": float("nan"),
            "residual": float("nan"),
            "ece": float("nan"),
            "max_bucket_abs_error": float("nan"),
            "bucket_count": 0,
        }

    probs = [clip_prob(probability) for probability in probabilities]
    outcomes = [_outcome(row) for row in rows]
    hit = sum(outcomes) / len(outcomes)
    buckets = bucket_rows(rows, probs)
    reliability = sum(row.reliability_component for row in buckets)
    resolution = sum(row.resolution_component for row in buckets)
    uncertainty = hit * (1 - hit)
    actual_brier = sum(brier(prob, outcome) for prob, outcome in zip(probs, outcomes)) / len(rows)
    decomposed = reliability - resolution + uncertainty
    ece = sum(row.n * abs(row.mean_p - row.hit) for row in buckets) / len(rows)
    max_abs_error = max((abs(row.mean_p - row.hit) for row in buckets), default=0.0)
    return {
        "n": len(rows),
        "mean_p": sum(probs) / len(probs),
        "hit": hit,
        "brier": actual_brier,
        "uncertainty": uncertainty,
        "reliability": reliability,
        "resolution": resolution,
        "decomposed_brier": decomposed,
        "residual": actual_brier - decomposed,
        "ece": ece,
        "max_bucket_abs_error": max_abs_error,
        "bucket_count": len(buckets),
    }


def policy_probabilities(rows: list[HistoryMarket]) -> list[tuple[str, list[float]]]:
    return [
        ("current", [row.you for row in rows]),
        ("field", [row.crowd for row in rows]),
        ("combined_manual_policy", [combined_prob(row) for row in rows]),
    ]


def policy_decompositions(rows: list[HistoryMarket]) -> list[dict[str, float | str]]:
    out: list[dict[str, float | str]] = []
    for policy, probabilities in policy_probabilities(rows):
        out.append({"policy": policy, **decomposition(rows, probabilities)})
    return out


def category_decompositions(rows: list[HistoryMarket], *, min_rows: int) -> list[dict[str, float | str]]:
    grouped: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        grouped[category(row)].append(row)
    out: list[dict[str, float | str]] = []
    for cat, part in grouped.items():
        if len(part) < min_rows:
            continue
        out.append({"category": cat, **decomposition(part, [combined_prob(row) for row in part])})
    return sorted(out, key=lambda row: (float(row["reliability"]), int(row["n"])), reverse=True)


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _fmt(value: float | int | str, digits: int = 4) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return value


def render(rows: list[HistoryMarket], *, min_category_rows: int, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    policy_rows = policy_decompositions(rows)
    combined = next(row for row in policy_rows if row["policy"] == "combined_manual_policy")
    category_rows = category_decompositions(rows, min_rows=min_category_rows)
    bucket_table = bucket_rows(rows, [combined_prob(row) for row in rows])
    worst_bucket = max(bucket_table, key=lambda row: abs(row.mean_p - row.hit), default=None)
    worst_category = category_rows[0] if category_rows else {}

    lines = [
        "# Brier Decomposition Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: read_only_results_when_requested",
        "- bot_predictions: not_read",
        "- decision: diagnostic_only_no_probability_transform",
        f"- settled_rows: {len(rows)}",
        f"- matches: {len({row.match for row in rows})}",
        f"- bucket_scheme: {len(BUCKETS)} fixed probability buckets",
        f"- min_category_rows: {min_category_rows}",
        f"- combined_brier: {_fmt(float(combined['brier']))}",
        f"- combined_reliability: {_fmt(float(combined['reliability']))}",
        f"- combined_resolution: {_fmt(float(combined['resolution']))}",
        f"- combined_uncertainty: {_fmt(float(combined['uncertainty']))}",
        f"- combined_residual: {_fmt(float(combined['residual']))}",
        f"- combined_ece: {_fmt(float(combined['ece']))}",
        f"- worst_bucket_abs_error: {_fmt(abs(worst_bucket.mean_p - worst_bucket.hit) if worst_bucket else 0.0)}",
        f"- worst_bucket: {worst_bucket.bucket if worst_bucket else '-'}",
        f"- worst_category_by_reliability: {worst_category.get('category', '-')}",
        "",
        "## Policy Decomposition",
        "",
    ]
    lines += md_table(
        [
            "policy",
            "n",
            "mean_p",
            "hit",
            "brier",
            "reliability",
            "resolution",
            "uncertainty",
            "residual",
            "ece",
            "max_bucket_abs_error",
        ],
        [
            [
                str(row["policy"]),
                _fmt(int(row["n"]), 0),
                _fmt(float(row["mean_p"]), 3),
                _fmt(float(row["hit"]), 3),
                _fmt(float(row["brier"]), 4),
                _fmt(float(row["reliability"]), 4),
                _fmt(float(row["resolution"]), 4),
                _fmt(float(row["uncertainty"]), 4),
                _fmt(float(row["residual"]), 4),
                _fmt(float(row["ece"]), 4),
                _fmt(float(row["max_bucket_abs_error"]), 4),
            ]
            for row in policy_rows
        ],
    )
    lines += [
        "",
        "## Combined Policy By Category",
        "",
    ]
    lines += md_table(
        ["category", "n", "brier", "reliability", "resolution", "uncertainty", "residual", "ece", "max_bucket_abs_error"],
        [
            [
                str(row["category"]),
                _fmt(int(row["n"]), 0),
                _fmt(float(row["brier"]), 4),
                _fmt(float(row["reliability"]), 4),
                _fmt(float(row["resolution"]), 4),
                _fmt(float(row["uncertainty"]), 4),
                _fmt(float(row["residual"]), 4),
                _fmt(float(row["ece"]), 4),
                _fmt(float(row["max_bucket_abs_error"]), 4),
            ]
            for row in category_rows
        ],
    )
    lines += [
        "",
        "## Combined Policy Reliability Buckets",
        "",
    ]
    lines += md_table(
        ["bucket", "n", "mean_p", "hit", "brier", "reliability_component", "resolution_component"],
        [
            [
                row.bucket,
                str(row.n),
                _fmt(row.mean_p, 3),
                _fmt(row.hit, 3),
                _fmt(row.brier, 4),
                _fmt(row.reliability_component, 4),
                _fmt(row.resolution_component, 4),
            ]
            for row in bucket_table
        ],
    )
    lines += [
        "",
        "## Interpretation",
        "",
        "- Lower reliability is better; it measures bucket-level miscalibration.",
        "- Higher resolution is better; it measures separation from the base rate.",
        "- The residual is expected because fixed buckets group non-identical probabilities.",
        "- This report is diagnostic only. It does not authorize a calibration multiplier or bucket-level probability transform.",
        "- Use high-reliability-error buckets as source, lineup, and wording recheck priorities before lock.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sports-predict", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--min-category-rows", type=int, default=20)
    args = parser.parse_args()

    rows = load_rows(sports_predict=args.sports_predict)
    text = render(rows, min_category_rows=args.min_category_rows)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
