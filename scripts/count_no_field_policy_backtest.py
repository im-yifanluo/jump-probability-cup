"""No-field count-market policy backtest.

This read-only diagnostic tests what to do when the manual workflow has no
visible SportsPredict field average for count markets. It uses only prior
settled count rows plus the current row's submitted/model probability, so any
candidate here is deployable without field averages and without bot
predictions.

It does not read `/predictions` and does not submit. With `--sports-predict`,
SportsPredict is used only for GET `/events`, `/lobbies`, and `/results` to
settle older V1 history rows.

Usage:
    uv run --with requests python scripts/count_no_field_policy_backtest.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import (  # noqa: E402
    HistoryMarket,
    brier,
    clip_prob,
    official_delta_rbp,
)
from count_policy_backtest import load_count_rows  # noqa: E402


OUT = ROOT / "data" / "logs" / "count_no_field_policy_backtest.md"


@dataclass(frozen=True)
class ScoredPrediction:
    row: HistoryMarket
    predictions: dict[str, float]


def beta_mean(rows: list[HistoryMarket], *, prior: float = 0.5, strength: float = 4.0) -> float:
    """Smoothed empirical hit rate for chronological no-field baselines."""

    if strength < 0:
        raise ValueError("strength must be non-negative")
    hits = sum(row.outcome or 0 for row in rows)
    return clip_prob((hits + prior * strength) / (len(rows) + strength))


def grouped_bases(
    train: list[HistoryMarket],
    key_fn,
    *,
    fallback: float,
    strength: float,
) -> dict[str, float]:
    grouped: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in train:
        grouped[key_fn(row)].append(row)
    return {key: beta_mean(rows, prior=fallback, strength=strength) for key, rows in grouped.items()}


def is_strong_sot(row: HistoryMarket) -> bool:
    return row.stat == "sot" and row.family in {"team_count_ge", "team_more_count_than"}


def is_card_threshold(row: HistoryMarket) -> bool:
    """Card threshold rows are count-over/under style, not card head-to-head.

    This split is operationally distinct: total/team card thresholds are driven
    mostly by match card volume, while card head-to-head rows include side
    allocation noise.  It is still tested through the same chronological and
    bootstrap gates before promotion.
    """

    return row.stat == "cards" and row.family in {"total_count_ge", "team_count_ge", "both_teams_count_ge"}


def policy_predictions(
    row: HistoryMarket,
    *,
    global_base: float,
    stat_base: float,
    family_base: float,
) -> dict[str, float]:
    """Predeclared deployable policies for no-field count rows."""

    p = row.you
    raw_sot_or_offside = p if row.stat in {"sot", "offsides"} else stat_base
    return {
        "raw_model": p,
        "prior_global_base": global_base,
        "prior_stat_base": stat_base,
        "prior_family_base": family_base,
        "model75_stat25": 0.75 * p + 0.25 * stat_base,
        "model50_stat50": 0.50 * p + 0.50 * stat_base,
        "model25_stat75": 0.25 * p + 0.75 * stat_base,
        "model75_family25": 0.75 * p + 0.25 * family_base,
        "model50_family50": 0.50 * p + 0.50 * family_base,
        "model25_family75": 0.25 * p + 0.75 * family_base,
        "cards_stat_base_else_raw": stat_base if row.stat == "cards" else p,
        "cards_family_base_else_raw": family_base if row.stat == "cards" else p,
        "card_threshold_family_base_else_raw": family_base if is_card_threshold(row) else p,
        "card_threshold_stat_base_else_raw": stat_base if is_card_threshold(row) else p,
        "raw_strong_sot_else_stat_base": p if is_strong_sot(row) else stat_base,
        "raw_strong_sot_else_model50_stat50": p if is_strong_sot(row) else 0.50 * p + 0.50 * stat_base,
        "raw_sot_offsides_else_stat_base": raw_sot_or_offside,
        "raw_sot_offsides_else_model50_stat50": p if row.stat in {"sot", "offsides"} else 0.50 * p + 0.50 * stat_base,
    }


def chronological_predictions(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    global_strength: float,
    stat_strength: float,
    family_strength: float,
) -> list[ScoredPrediction]:
    scored: list[ScoredPrediction] = []
    for order in sorted({row.order for row in rows}):
        train = [row for row in rows if row.order < order]
        if len(train) < min_train:
            continue
        global_base = beta_mean(train, prior=0.5, strength=global_strength)
        stat_rates = grouped_bases(
            train,
            lambda row: row.stat or "none",
            fallback=global_base,
            strength=stat_strength,
        )
        family_rates = grouped_bases(
            train,
            lambda row: row.famkey,
            fallback=global_base,
            strength=family_strength,
        )
        for row in [row for row in rows if row.order == order]:
            stat_base = stat_rates.get(row.stat or "none", global_base)
            family_base = family_rates.get(row.famkey, stat_base)
            scored.append(
                ScoredPrediction(
                    row=row,
                    predictions={
                        name: clip_prob(value)
                        for name, value in policy_predictions(
                            row,
                            global_base=global_base,
                            stat_base=stat_base,
                            family_base=family_base,
                        ).items()
                    },
                )
            )
    return scored


def score_predictions(scored: list[ScoredPrediction], policy: str) -> dict[str, float]:
    if not scored:
        return {"n": 0, "delta_vs_raw": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    delta = br = mean_p = 0.0
    for item in scored:
        row = item.row
        p = clip_prob(item.predictions[policy])
        delta += official_delta_rbp(row, p) - official_delta_rbp(row, row.you)
        br += brier(p, row.outcome or 0)
        mean_p += p
    return {
        "n": len(scored),
        "delta_vs_raw": delta,
        "brier": br / len(scored),
        "mean_p": mean_p / len(scored),
    }


def score_field_context(scored: list[ScoredPrediction]) -> dict[str, float]:
    """Visible-field context only; not deployable without field averages."""

    if not scored:
        return {"n": 0, "delta_vs_raw": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    delta = br = mean_p = 0.0
    for item in scored:
        row = item.row
        delta += official_delta_rbp(row, row.crowd) - official_delta_rbp(row, row.you)
        br += brier(row.crowd, row.outcome or 0)
        mean_p += row.crowd
    return {
        "n": len(scored),
        "delta_vs_raw": delta,
        "brier": br / len(scored),
        "mean_p": mean_p / len(scored),
    }


def policy_summary(scored: list[ScoredPrediction]) -> list[dict[str, float | str]]:
    if not scored:
        return []
    rows: list[dict[str, float | str]] = []
    for name in scored[0].predictions:
        rows.append({"policy": name, **score_predictions(scored, name)})
    rows.append({"policy": "visible_field_context", **score_field_context(scored)})
    return sorted(rows, key=lambda row: float(row["delta_vs_raw"]), reverse=True)


def sensitivity_grid(
    rows: list[HistoryMarket],
    *,
    policy: str,
    min_trains: list[int],
    stat_strengths: list[float],
    global_strength: float,
    family_strength: float,
) -> list[dict[str, float | str]]:
    """Sensitivity of a fixed no-field count policy to training choices."""

    out: list[dict[str, float | str]] = []
    for min_train in min_trains:
        for stat_strength in stat_strengths:
            scored = chronological_predictions(
                rows,
                min_train=min_train,
                global_strength=global_strength,
                stat_strength=stat_strength,
                family_strength=family_strength,
            )
            if not scored or policy not in scored[0].predictions:
                continue
            target = score_predictions(scored, policy)
            deployable = [row for row in policy_summary(scored) if row["policy"] != "visible_field_context"]
            best = deployable[0] if deployable else {"policy": "", "delta_vs_raw": 0.0}
            rank = next(idx + 1 for idx, row in enumerate(deployable) if row["policy"] == policy)
            out.append(
                {
                    "min_train": min_train,
                    "stat_strength": stat_strength,
                    "holdout_rows": int(target["n"]),
                    "delta_vs_raw": float(target["delta_vs_raw"]),
                    "brier": float(target["brier"]),
                    "mean_p": float(target["mean_p"]),
                    "deployable_rank": rank,
                    "best_policy": str(best["policy"]),
                    "best_delta_vs_raw": float(best["delta_vs_raw"]),
                }
            )
    return sorted(out, key=lambda row: (int(row["min_train"]), float(row["stat_strength"])))


def sensitivity_summary(rows: list[dict[str, float | str]], policy: str) -> dict[str, float | str]:
    if not rows:
        return {
            "policy": policy,
            "configs": 0,
            "positive_configs": 0,
            "best_policy_configs": 0,
            "min_delta_vs_raw": 0.0,
            "median_delta_vs_raw": 0.0,
            "max_delta_vs_raw": 0.0,
        }
    deltas = sorted(float(row["delta_vs_raw"]) for row in rows)
    return {
        "policy": policy,
        "configs": len(rows),
        "positive_configs": sum(1 for row in rows if float(row["delta_vs_raw"]) > 0),
        "best_policy_configs": sum(1 for row in rows if str(row["best_policy"]) == policy),
        "min_delta_vs_raw": deltas[0],
        "median_delta_vs_raw": deltas[len(deltas) // 2],
        "max_delta_vs_raw": deltas[-1],
    }


def bootstrap_policy_diff(
    scored: list[ScoredPrediction],
    left: str,
    right: str,
    *,
    trials: int,
    seed: int,
) -> dict[str, float]:
    by_match: dict[str, list[ScoredPrediction]] = defaultdict(list)
    for item in scored:
        by_match[item.row.match].append(item)
    matches = sorted(by_match)
    if not matches:
        return {"ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(trials):
        delta = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for item in by_match[match]:
                row = item.row
                delta += official_delta_rbp(row, clip_prob(item.predictions[left]))
                if right == "visible_field_context":
                    delta -= official_delta_rbp(row, row.crowd)
                else:
                    delta -= official_delta_rbp(row, clip_prob(item.predictions[right]))
        draws.append(delta)
    draws.sort()
    return {
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "median": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(value > 0 for value in draws) / len(draws),
    }


def grouped_diagnostics(scored: list[ScoredPrediction], policy: str, key_fn) -> list[dict[str, float | str]]:
    grouped: dict[str, list[ScoredPrediction]] = defaultdict(list)
    for item in scored:
        grouped[key_fn(item.row)].append(item)
    out: list[dict[str, float | str]] = []
    for key, part in sorted(grouped.items()):
        delta = sum(
            official_delta_rbp(item.row, clip_prob(item.predictions[policy]))
            - official_delta_rbp(item.row, item.row.you)
            for item in part
        )
        out.append(
            {
                "group": key,
                "n": len(part),
                "delta_vs_raw": delta,
                "raw": sum(item.row.you for item in part) / len(part),
                "policy": sum(item.predictions[policy] for item in part) / len(part),
                "field": sum(item.row.crowd for item in part) / len(part),
                "hit": sum(item.row.outcome or 0 for item in part) / len(part),
            }
        )
    return sorted(out, key=lambda row: float(row["delta_vs_raw"]))


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render(
    rows: list[HistoryMarket],
    *,
    min_train: int,
    global_strength: float,
    stat_strength: float,
    family_strength: float,
    trials: int,
    seed: int,
) -> str:
    scored = chronological_predictions(
        rows,
        min_train=min_train,
        global_strength=global_strength,
        stat_strength=stat_strength,
        family_strength=family_strength,
    )
    summaries = policy_summary(scored)
    best = str(next(row["policy"] for row in summaries if row["policy"] != "visible_field_context"))
    direct = bootstrap_policy_diff(scored, best, "raw_model", trials=trials, seed=seed) if trials else None
    field_direct = (
        bootstrap_policy_diff(scored, "raw_model", "visible_field_context", trials=trials, seed=seed)
        if trials
        else None
    )
    sensitivity_rows = sensitivity_grid(
        rows,
        policy=best,
        min_trains=[30, 45, 60, 75, 90],
        stat_strengths=[0.0, 4.0, 8.0, 16.0, 32.0],
        global_strength=global_strength,
        family_strength=family_strength,
    )
    robustness = sensitivity_summary(sensitivity_rows, best)
    lines = [
        "# Count No-Field Policy Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- settled_count_rows: {len(rows)}",
        f"- matches: {len({row.match for row in rows})}",
        f"- chronological_holdout_rows: {len(scored)}",
        f"- min_train: {min_train}",
        f"- global_beta_strength: {global_strength:g}",
        f"- stat_beta_strength: {stat_strength:g}",
        f"- family_beta_strength: {family_strength:g}",
        "- scoring: each candidate is trained only on earlier settled count rows, then scored against the next match/order",
        "- deployable inputs: row model probability, row count family/stat, and earlier settled count outcomes only; no field average and no bot predictions",
        "",
        "## Chronological No-Field Policies",
        "",
    ]
    lines += md_table(
        ["policy", "n", "delta_vs_raw", "brier", "mean_p"],
        [
            [
                str(row["policy"]),
                f"{int(row['n'])}",
                f"{float(row['delta_vs_raw']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
            ]
            for row in summaries
        ],
    )
    lines += [
        "",
        "## Best No-Field Split By Stat",
        "",
        f"Best deployable no-field policy by chronological delta: `{best}`.",
        "",
    ]
    lines += md_table(
        ["stat", "n", "delta_vs_raw", "raw", best, "field_context", "hit"],
        [
            [
                str(row["group"]),
                f"{int(row['n'])}",
                f"{float(row['delta_vs_raw']):.2f}",
                f"{float(row['raw']):.3f}",
                f"{float(row['policy']):.3f}",
                f"{float(row['field']):.3f}",
                f"{float(row['hit']):.3f}",
            ]
            for row in grouped_diagnostics(scored, best, lambda row: row.stat or "none")
        ],
    )
    lines += [
        "",
        "## Best No-Field Split By Family",
        "",
    ]
    lines += md_table(
        ["family", "n", "delta_vs_raw", "raw", best, "field_context", "hit"],
        [
            [
                str(row["group"]),
                f"{int(row['n'])}",
                f"{float(row['delta_vs_raw']):.2f}",
                f"{float(row['raw']):.3f}",
                f"{float(row['policy']):.3f}",
                f"{float(row['field']):.3f}",
                f"{float(row['hit']):.3f}",
            ]
            for row in grouped_diagnostics(scored, best, lambda row: row.famkey)
        ],
    )
    lines += [
        "",
        "## Sensitivity Summary",
        "",
        f"- robustness_policy: {robustness['policy']}",
        f"- robustness_configs: {robustness['configs']}",
        f"- robustness_positive_configs: {robustness['positive_configs']}",
        f"- robustness_best_policy_configs: {robustness['best_policy_configs']}",
        f"- robustness_min_delta_vs_raw: {float(robustness['min_delta_vs_raw']):.2f}",
        f"- robustness_median_delta_vs_raw: {float(robustness['median_delta_vs_raw']):.2f}",
        f"- robustness_max_delta_vs_raw: {float(robustness['max_delta_vs_raw']):.2f}",
        "",
        "The grid varies `min_train` over 30, 45, 60, 75, 90 and stat beta strength over 0, 4, 8, 16, 32 while holding global/family strengths fixed. It is a robustness check for the current best no-field candidate, not a new tuning search.",
        "",
    ]
    lines += md_table(
        [
            "min_train",
            "stat_strength",
            "holdout_rows",
            "delta_vs_raw",
            "brier",
            "mean_p",
            "deployable_rank",
            "best_policy",
            "best_delta_vs_raw",
        ],
        [
            [
                f"{int(row['min_train'])}",
                f"{float(row['stat_strength']):g}",
                f"{int(row['holdout_rows'])}",
                f"{float(row['delta_vs_raw']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
                f"{int(row['deployable_rank'])}",
                str(row["best_policy"]),
                f"{float(row['best_delta_vs_raw']):.2f}",
            ]
            for row in sensitivity_rows
        ],
    )
    if trials:
        point = score_predictions(scored, best)["delta_vs_raw"]
        field_point = -score_field_context(scored)["delta_vs_raw"]
        lines += [
            "",
            "## Match-Level Bootstrap",
            "",
            f"Trials: {trials}; seed: {seed}. Bootstrap is over fixed walk-forward predictions on holdout rows.",
            "",
            "| comparison | point_delta | ci_2.5 | median | ci_97.5 | pr_gt_0 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
            (
                f"| {best}_minus_raw_model | {point:.2f} | {direct['ci_2.5']:.2f} | "
                f"{direct['median']:.2f} | {direct['ci_97.5']:.2f} | {direct['pr_gt_0']:.3f} |"
            ),
            (
                f"| raw_model_minus_visible_field_context | {field_point:.2f} | "
                f"{field_direct['ci_2.5']:.2f} | {field_direct['median']:.2f} | "
                f"{field_direct['ci_97.5']:.2f} | {field_direct['pr_gt_0']:.3f} |"
            ),
        ]
        if direct["ci_2.5"] > 0:
            lines += [
                "",
                (
                    f"- Decision gate: `{best}` clears the positive-bootstrap lower-bound check, "
                    "but promotion still requires a separate policy review before changing the manual workflow."
                ),
            ]
        else:
            lines += [
                "",
                (
                    f"- Decision gate: no no-field count fallback is promoted. `{best}` has a positive "
                    "chronological point estimate, but its match-bootstrap interval still crosses zero."
                ),
            ]
    lines += [
        "",
        "## Interpretation",
        "",
        "- This is a no-field fallback diagnostic for pre-lock manual entry. SportsPredict field averages are unavailable before prediction lock, so do not use a field-relative count policy pre-lock.",
        "- Promote a no-field fallback only if it beats `raw_model` chronologically and the match-bootstrap does not make the gain look like pure noise.",
        "- The card-threshold candidates deliberately exclude card head-to-head/allocation markets; those rows kept the raw model in this no-field diagnostic.",
        "- Pure empirical bases can be dangerous across heterogeneous thresholds; prefer mixed rules that retain raw model information unless the holdout/bootstrap evidence is strong.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=60)
    ap.add_argument("--global-strength", type=float, default=4.0)
    ap.add_argument("--stat-strength", type=float, default=8.0)
    ap.add_argument("--family-strength", type=float, default=12.0)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=37)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_count_rows(sports_predict=args.sports_predict)
    text = render(
        rows,
        min_train=args.min_train,
        global_strength=args.global_strength,
        stat_strength=args.stat_strength,
        family_strength=args.family_strength,
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
