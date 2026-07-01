"""Audit special count-threshold priors and adaptation rules.

This is a local proxy research gate for special markets such as total shots
and early corners before hydration. It uses StatsBomb tournament event data
only, never calls SportsPredict, never reads `/predictions`, and never submits
anything.

The existing special-market backtest validates broad base priors but marks
count-threshold rows as weaker when live count-volume scaling is far from the
no-match-info base or cross-tournament heterogeneity is high. This audit
focuses on that boundary: direct threshold priors, count-distribution priors,
and chronological in-tournament adaptation.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from math import exp, lgamma, log
from pathlib import Path
import random
import sys


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "special_count_threshold_audit.md"

sys.path.insert(0, str(ROOT / "scripts"))

from special_market_backtest import COUNT_THRESHOLD_PRIORS, rows as statsbomb_rows  # noqa: E402


ADAPTATION_STRENGTHS = (16, 32, 64, 128)
METHODS = (
    "base_bernoulli",
    "poisson_count_prior",
    "nb_count_prior",
    "adaptive_bernoulli_s16",
    "adaptive_bernoulli_s32",
    "adaptive_bernoulli_s64",
    "adaptive_bernoulli_s128",
    "adaptive_nb_mean_s16",
    "adaptive_nb_mean_s32",
    "adaptive_nb_mean_s64",
    "adaptive_nb_mean_s128",
)


def _brier(probability: float, outcome: bool) -> float:
    p = min(0.99, max(0.01, probability))
    return (p - float(outcome)) ** 2


def _alpha(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / max(len(values) - 1, 1)
    return max(0.0, (variance - mean) / (mean * mean))


def _poisson_ge(mean: float, threshold: int) -> float:
    mean = max(mean, 1e-9)
    term = exp(-mean)
    cumulative = term
    for k in range(1, threshold):
        term *= mean / k
        cumulative += term
    return min(0.99, max(0.01, 1.0 - cumulative))


def _nb_pmf(k: int, mean: float, alpha: float) -> float:
    if alpha <= 1e-9:
        return exp(-mean + k * log(mean) - lgamma(k + 1))
    size = 1.0 / alpha
    p = size / (size + mean)
    return exp(lgamma(k + size) - lgamma(size) - lgamma(k + 1) + size * log(p) + k * log(1.0 - p))


def _nb_ge(mean: float, alpha: float, threshold: int) -> float:
    mean = max(mean, 1e-9)
    cumulative = sum(_nb_pmf(k, mean, alpha) for k in range(threshold))
    return min(0.99, max(0.01, 1.0 - cumulative))


def _ordered_batches(rows_: list[dict]) -> list[list[dict]]:
    ordered = sorted(
        rows_,
        key=lambda row: (
            row.get("match_date", "9999-12-31"),
            row.get("kick_off", "99:99:99.999"),
            row.get("match_id", 0),
        ),
    )
    batches: list[list[dict]] = []
    for row in ordered:
        key = (row.get("match_date"), row.get("kick_off"))
        if not batches or (batches[-1][0].get("match_date"), batches[-1][0].get("kick_off")) != key:
            batches.append([row])
        else:
            batches[-1].append(row)
    return batches


def score_thresholds(data: list[dict]) -> list[dict[str, object]]:
    scored: list[dict[str, object]] = []
    tournaments = sorted({str(row["tournament"]) for row in data})
    for key, value_field, threshold in COUNT_THRESHOLD_PRIORS:
        for tournament in tournaments:
            train = [row for row in data if str(row["tournament"]) != tournament]
            test = [row for row in data if str(row["tournament"]) == tournament]
            if not train or not test:
                continue
            train_values = [float(row[value_field]) for row in train]
            train_outcomes = [value >= threshold for value in train_values]
            base = sum(train_outcomes) / len(train_outcomes)
            mean = sum(train_values) / len(train_values)
            alpha = _alpha(train_values)
            prior_n = 0
            prior_successes = 0
            prior_value_sum = 0.0
            for batch in _ordered_batches(test):
                for row in batch:
                    value = float(row[value_field])
                    outcome = value >= threshold
                    predictions: dict[str, float] = {
                        "base_bernoulli": base,
                        "poisson_count_prior": _poisson_ge(mean, threshold),
                        "nb_count_prior": _nb_ge(mean, alpha, threshold),
                    }
                    for strength in ADAPTATION_STRENGTHS:
                        predictions[f"adaptive_bernoulli_s{strength}"] = (
                            strength * base + prior_successes
                        ) / max(strength + prior_n, 1e-9)
                        adapted_mean = (strength * mean + prior_value_sum) / max(strength + prior_n, 1e-9)
                        predictions[f"adaptive_nb_mean_s{strength}"] = _nb_ge(adapted_mean, alpha, threshold)
                    for method, probability in predictions.items():
                        scored.append(
                            {
                                "key": key,
                                "value_field": value_field,
                                "threshold": threshold,
                                "tournament": tournament,
                                "method": method,
                                "probability": probability,
                                "outcome": outcome,
                                "brier": _brier(probability, outcome),
                            }
                        )
                prior_n += len(batch)
                prior_successes += sum(float(row[value_field]) >= threshold for row in batch)
                prior_value_sum += sum(float(row[value_field]) for row in batch)
    return scored


def _summary_rows(scored: list[dict[str, object]], *, key_filter: str | None = None) -> list[dict[str, str]]:
    rows = [row for row in scored if key_filter is None or row["key"] == key_filter]
    by_method: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_method[str(row["method"])].append(row)
    base = by_method["base_bernoulli"]
    base_brier = sum(float(row["brier"]) for row in base) / len(base)
    out = []
    for method in METHODS:
        part = by_method.get(method, [])
        if not part:
            continue
        brier = sum(float(row["brier"]) for row in part) / len(part)
        delta = (base_brier - brier) * len(part) * 100.0
        out.append(
            {
                "method": method,
                "n": str(len(part)),
                "brier": f"{brier:.6f}",
                "delta_vs_base_bernoulli_x100": f"{delta:.2f}",
                "brier_points_per_1000": f"{(base_brier - brier) * 100000:.3f}",
            }
        )
    return sorted(out, key=lambda row: float(row["brier"]))


def _bootstrap(
    scored: list[dict[str, object]],
    *,
    challenger: str,
    key_filter: str | None = None,
    baseline: str = "base_bernoulli",
    trials: int = 5000,
    seed: int = 20260629,
) -> dict[str, float | int]:
    rows = [row for row in scored if key_filter is None or row["key"] == key_filter]
    by_tournament: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        by_tournament[str(row["tournament"])][str(row["method"])] += float(row["brier"])
    deltas = [
        values[baseline] - values[challenger]
        for values in by_tournament.values()
        if baseline in values and challenger in values
    ]
    if not deltas:
        return {"tournaments": 0, "point_delta_x100": 0.0, "ci_2.5": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    rng = random.Random(seed)
    sampled = []
    for _ in range(trials):
        sampled.append(sum(rng.choice(deltas) for _ in deltas) * 100.0)
    sampled.sort()
    return {
        "tournaments": len(deltas),
        "point_delta_x100": sum(deltas) * 100.0,
        "ci_2.5": sampled[int(0.025 * (trials - 1))],
        "ci_97.5": sampled[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(value > 0 for value in sampled) / len(sampled),
    }


def _live_count_threshold_rows(log_dir: Path = LOG_DIR) -> list[dict[str, str]]:
    path = log_dir / "special_market_live_audit.md"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == "## Live Special Rows":
            start = idx + 1
            break
    if start is None:
        return []
    while start < len(lines) and not lines[start].lstrip().startswith("|"):
        start += 1
    if start >= len(lines):
        return []
    headers = [part.strip() for part in lines[start].strip().strip("|").split("|")]
    out: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) == len(headers):
            row = dict(zip(headers, parts))
            if row.get("evidence_type") == "count_threshold_loto_base_prior":
                out.append(row)
    return out


def analyze(data: list[dict]) -> dict[str, object]:
    scored = score_thresholds(data)
    boots = {
        "nb_count_prior": _bootstrap(scored, challenger="nb_count_prior"),
        "adaptive_bernoulli_s64": _bootstrap(scored, challenger="adaptive_bernoulli_s64"),
        "adaptive_nb_mean_s64": _bootstrap(scored, challenger="adaptive_nb_mean_s64"),
    }
    if all(float(boots[name]["ci_2.5"]) > 0.0 for name in ("nb_count_prior", "adaptive_nb_mean_s64")):
        decision = "proxy_candidate_distribution_and_adaptation_positive_not_live_promoted"
    elif float(boots["nb_count_prior"]["ci_2.5"]) > 0.0:
        decision = "proxy_candidate_count_distribution_positive_not_live_promoted"
    elif float(boots["adaptive_nb_mean_s64"]["ci_2.5"]) > 0.0:
        decision = "proxy_candidate_count_threshold_adaptation_positive_not_live_promoted"
    else:
        decision = "not_promoted_keep_count_threshold_guardrails"
    return {
        "decision": decision,
        "matches": len(data),
        "tournaments": len({row["tournament"] for row in data}),
        "thresholds": len(COUNT_THRESHOLD_PRIORS),
        "scored_rows": len(scored) // len(METHODS),
        "overall": _summary_rows(scored),
        "by_key": {key: _summary_rows(scored, key_filter=key) for key, _field, _threshold in COUNT_THRESHOLD_PRIORS},
        "bootstraps": boots,
        "live_count_threshold_rows": _live_count_threshold_rows(),
    }


def _table(headers: list[str], rows: list[dict[str, str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return lines


def render(analysis: dict[str, object], *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = [
        "# Special Count Threshold Audit",
        "",
        "Read-only proxy analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- raw_data: StatsBomb tournament event cache",
        "- purpose: test count-threshold special priors, count-distribution conversion, and in-tournament adaptation",
        f"- decision: {analysis['decision']}",
        f"- matches: {analysis['matches']}",
        f"- tournaments: {analysis['tournaments']}",
        f"- threshold_families: {analysis['thresholds']}",
        f"- scored_rows: {analysis['scored_rows']}",
        "",
        "## Overall Method Comparison",
        "",
        *_table(["method", "n", "brier", "delta_vs_base_bernoulli_x100", "brier_points_per_1000"], analysis["overall"]),  # type: ignore[arg-type]
        "",
    ]
    by_key = analysis["by_key"]
    assert isinstance(by_key, dict)
    for key, _field, _threshold in COUNT_THRESHOLD_PRIORS:
        lines.extend(
            [
                f"## {key} Method Comparison",
                "",
                *_table(
                    ["method", "n", "brier", "delta_vs_base_bernoulli_x100", "brier_points_per_1000"],
                    by_key[key],  # type: ignore[index,arg-type]
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Candidate Bootstraps",
            "",
            "| comparison | tournaments | point_delta_x100 | ci_2.5 | ci_97.5 | pr_gt_0 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    boots = analysis["bootstraps"]
    assert isinstance(boots, dict)
    for comparison, boot in boots.items():
        assert isinstance(boot, dict)
        lines.append(
            f"| {comparison}_minus_base_bernoulli | {boot['tournaments']} | "
            f"{boot['point_delta_x100']:.2f} | {boot['ci_2.5']:.2f} | "
            f"{boot['ci_97.5']:.2f} | {boot['pr_gt_0']:.3f} |"
        )
    live_rows = analysis["live_count_threshold_rows"]
    assert isinstance(live_rows, list)
    lines.extend(
        [
            "",
            "## Live Count-Threshold Special Rows",
            "",
            "| match | entry | evidence_key | risk_state | scaled_shift_pp | question |",
            "| --- | ---: | --- | --- | ---: | --- |",
        ]
    )
    if not live_rows:
        lines.append("| - | - | - | - | - | no current live count-threshold rows found |")
    else:
        for row in live_rows:
            lines.append(
                f"| {row.get('match', '')} | {row.get('entry', '')} | {row.get('evidence_key', '')} | "
                f"{row.get('risk_state', '')} | {row.get('scaled_shift_pp', '')} | {row.get('question', '')} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is a StatsBomb proxy replay, not official SportsPredict validation.",
            "- `base_bernoulli` is the direct no-match-info threshold rate and remains the safest broad prior.",
            "- Count-distribution and in-tournament adaptation methods need positive tournament-block bootstrap support before they can change live special count-threshold probabilities.",
            "- Even a proxy-positive result must remain below exact current source or official SportsPredict replay evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()
    analysis = analyze(statsbomb_rows())
    text = render(analysis)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
