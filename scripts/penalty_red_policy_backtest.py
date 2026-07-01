"""Penalty/red-card manual policy backtest.

This read-only diagnostic isolates low-event penalty/red markets from
`docs/history.md`. These rows are sparse, so the report is a guardrail around
field usage rather than a model refit.

With `--sports-predict`, SportsPredict is used only for GET `/events`,
`/lobbies`, and `/results` to settle older V1 history rows. The script never
reads `/predictions` and never submits.

Usage:
    uv run --with requests python scripts/penalty_red_policy_backtest.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import (  # noqa: E402
    HistoryMarket,
    asymmetric_field_policy,
    brier,
    clip_prob,
    crowd_strategy,
    exact_rows,
    load_history,
    official_delta_rbp,
    settle_v1_rows,
    shrink_to_crowd,
    unique_outcomes_by_question,
)
from manual.anchors import sp_get, sports_predict_key  # noqa: E402


OUT = ROOT / "data" / "logs" / "penalty_red_policy_backtest.md"
HISTORY = ROOT / "docs" / "history.md"
PENALTY_RED_FAMILIES = {"penalty_awarded", "penalty_or_red", "red_card"}

GLOBAL_ASYM = asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.12)
ASYM_CAP10 = asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.10)


def _read_only_outcomes_from_sports_predict() -> dict[str, int]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    results = sp_get(f"/results?lobby_id={lobby['id']}", key)
    return unique_outcomes_by_question(results)


def load_penalty_red_rows(*, sports_predict: bool) -> list[HistoryMarket]:
    rows = load_history(HISTORY)
    if sports_predict:
        rows = settle_v1_rows(rows, _read_only_outcomes_from_sports_predict())
    return [row for row in exact_rows(rows) if row.family in PENALTY_RED_FAMILIES]


def policy_specs():
    return [
        ("current", lambda r: r.you),
        ("field", crowd_strategy),
        ("shrink25", shrink_to_crowd(0.25)),
        ("shrink50", shrink_to_crowd(0.50)),
        ("shrink75", shrink_to_crowd(0.75)),
        ("global_asym075_cap12", GLOBAL_ASYM),
        ("asym075_cap10", ASYM_CAP10),
        ("min_model_field", lambda r: min(r.you, r.crowd)),
    ]


def score(rows: list[HistoryMarket], strategy) -> dict[str, float]:
    if not rows:
        return {"n": 0, "delta": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    preds = [clip_prob(strategy(r)) for r in rows]
    return {
        "n": len(rows),
        "delta": sum(official_delta_rbp(r, p) for r, p in zip(rows, preds)),
        "brier": sum(brier(p, r.outcome or 0) for r, p in zip(rows, preds)) / len(rows),
        "mean_p": sum(preds) / len(preds),
    }


def grouped_table(rows: list[HistoryMarket], key_fn) -> list[dict]:
    grouped: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        grouped[key_fn(row)].append(row)
    out = []
    for key, part in grouped.items():
        skill = sum(100 * (brier(r.crowd, r.outcome or 0) - brier(r.you, r.outcome or 0)) for r in part)
        out.append(
            {
                "group": key,
                "n": len(part),
                "skill_vs_field": skill,
                "brier_model": sum(brier(r.you, r.outcome or 0) for r in part) / len(part),
                "brier_field": sum(brier(r.crowd, r.outcome or 0) for r in part) / len(part),
                "model": sum(r.you for r in part) / len(part),
                "field": sum(r.crowd for r in part) / len(part),
                "hit": sum(r.outcome or 0 for r in part) / len(part),
            }
        )
    return sorted(out, key=lambda r: r["skill_vs_field"])


def directional_table(rows: list[HistoryMarket]) -> list[dict]:
    groups = [
        ("material_below_field", lambda r: r.you < r.crowd - 0.03),
        ("near_field", lambda r: abs(r.you - r.crowd) <= 0.03),
        ("material_above_field", lambda r: r.you > r.crowd + 0.03),
    ]
    out = []
    for label, pred in groups:
        part = [r for r in rows if pred(r)]
        if not part:
            continue
        skill = sum(100 * (brier(r.crowd, r.outcome or 0) - brier(r.you, r.outcome or 0)) for r in part)
        out.append(
            {
                "direction": label,
                "n": len(part),
                "skill_vs_field": skill,
                "model": sum(r.you for r in part) / len(part),
                "field": sum(r.crowd for r in part) / len(part),
                "hit": sum(r.outcome or 0 for r in part) / len(part),
            }
        )
    return out


def policy_table(rows: list[HistoryMarket]) -> list[dict]:
    return sorted(
        [{"policy": name, **score(rows, strategy)} for name, strategy in policy_specs()],
        key=lambda r: r["delta"],
        reverse=True,
    )


def chronological_holdout(rows: list[HistoryMarket], min_train: int) -> list[dict]:
    orders = sorted({row.order for row in rows})
    holdout = []
    for order in orders:
        if sum(1 for row in rows if row.order < order) >= min_train:
            holdout.extend([row for row in rows if row.order == order])
    return sorted(
        [{"policy": name, **score(holdout, strategy)} for name, strategy in policy_specs()],
        key=lambda r: r["delta"],
        reverse=True,
    )


def sensitivity_grid(
    rows: list[HistoryMarket],
    *,
    policy: str,
    min_trains: list[int],
) -> list[dict[str, float | str]]:
    """Check whether a fixed low-event policy survives training-cutoff choices."""

    out: list[dict[str, float | str]] = []
    for min_train in min_trains:
        holdout = chronological_holdout(rows, min_train)
        if not holdout:
            continue
        by_policy = {row["policy"]: row for row in holdout}
        if policy not in by_policy:
            continue
        target = by_policy[policy]
        best = holdout[0]
        out.append(
            {
                "min_train": min_train,
                "holdout_rows": int(target["n"]),
                "delta_vs_current": float(target["delta"]),
                "brier": float(target["brier"]),
                "mean_p": float(target["mean_p"]),
                "rank": next(idx + 1 for idx, row in enumerate(holdout) if row["policy"] == policy),
                "best_policy": str(best["policy"]),
                "best_delta_vs_current": float(best["delta"]),
            }
        )
    return out


def sensitivity_summary(rows: list[dict[str, float | str]], policy: str) -> dict[str, float | str]:
    if not rows:
        return {
            "policy": policy,
            "configs": 0,
            "positive_configs": 0,
            "best_policy_configs": 0,
            "min_delta_vs_current": 0.0,
            "median_delta_vs_current": 0.0,
            "max_delta_vs_current": 0.0,
        }
    deltas = sorted(float(row["delta_vs_current"]) for row in rows)
    return {
        "policy": policy,
        "configs": len(rows),
        "positive_configs": sum(1 for row in rows if float(row["delta_vs_current"]) > 0),
        "best_policy_configs": sum(1 for row in rows if str(row["best_policy"]) == policy),
        "min_delta_vs_current": deltas[0],
        "median_delta_vs_current": deltas[len(deltas) // 2],
        "max_delta_vs_current": deltas[-1],
    }


def walk_forward(rows: list[HistoryMarket], min_train: int) -> dict:
    specs = policy_specs()
    orders = sorted({row.order for row in rows})
    scored = []
    choices = Counter()
    for order in orders:
        train = [row for row in rows if row.order < order]
        test = [row for row in rows if row.order == order]
        if len(train) < min_train:
            name, strategy = "current", lambda r: r.you
        else:
            name, strategy, _ = max(
                ((name, strategy, score(train, strategy)["delta"]) for name, strategy in specs),
                key=lambda x: x[2],
            )
        for row in test:
            scored.append((row, clip_prob(strategy(row))))
            choices[name] += 1
    current_delta = score(rows, lambda r: r.you)["delta"]
    alt_delta = sum(official_delta_rbp(row, p) for row, p in scored)
    return {
        "n": len(scored),
        "delta": alt_delta - current_delta,
        "brier": sum(brier(p, row.outcome or 0) for row, p in scored) / len(scored),
        "choices": ", ".join(f"{k}:{v}" for k, v in choices.most_common()),
    }


def bootstrap(rows: list[HistoryMarket], strategy, *, trials: int, seed: int) -> tuple[float, float, float]:
    by_match: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        by_match[row.match].append(row)
    matches = sorted(by_match)
    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        delta = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for row in by_match[match]:
                delta += official_delta_rbp(row, clip_prob(strategy(row)))
        draws.append(delta)
    draws.sort()
    return (
        draws[int(0.025 * (trials - 1))],
        draws[int(0.500 * (trials - 1))],
        draws[int(0.975 * (trials - 1))],
    )


def bootstrap_strategy_diff(
    rows: list[HistoryMarket],
    left,
    right,
    *,
    trials: int,
    seed: int,
) -> dict[str, float]:
    by_match: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        by_match[row.match].append(row)
    matches = sorted(by_match)
    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        delta = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for row in by_match[match]:
                delta += official_delta_rbp(row, clip_prob(left(row)))
                delta -= official_delta_rbp(row, clip_prob(right(row)))
        draws.append(delta)
    draws.sort()
    return {
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "median": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(value > 0 for value in draws) / len(draws),
    }


def md_table(headers: list[str], rows: list[list]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def render(rows: list[HistoryMarket], *, min_train: int, trials: int, seed: int) -> str:
    sensitivity_rows = sensitivity_grid(
        rows,
        policy="global_asym075_cap12",
        min_trains=[4, 6, 8, 10, 12, 16],
    )
    robustness = sensitivity_summary(sensitivity_rows, "global_asym075_cap12")
    lines = [
        "# Penalty/Red Policy Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- settled_penalty_red_rows: {len(rows)}",
        f"- matches: {len({r.match for r in rows})}",
        "- scoring: exact official-RBP deltas from displayed market RBP and realized Brier",
        "",
        "## By Family",
        "",
    ]
    lines += md_table(
        ["family", "n", "skill_vs_field", "brier_model", "brier_field", "model", "field", "hit"],
        [
            [
                row["group"],
                row["n"],
                f"{row['skill_vs_field']:.2f}",
                f"{row['brier_model']:.4f}",
                f"{row['brier_field']:.4f}",
                f"{row['model']:.3f}",
                f"{row['field']:.3f}",
                f"{row['hit']:.3f}",
            ]
            for row in grouped_table(rows, lambda r: r.famkey)
        ],
    )
    lines += ["", "## Directional Diagnostics", ""]
    lines += md_table(
        ["direction", "n", "skill_vs_field", "model", "field", "hit"],
        [
            [
                row["direction"],
                row["n"],
                f"{row['skill_vs_field']:.2f}",
                f"{row['model']:.3f}",
                f"{row['field']:.3f}",
                f"{row['hit']:.3f}",
            ]
            for row in directional_table(rows)
        ],
    )
    lines += ["", "## Fixed Policies", ""]
    lines += md_table(
        ["policy", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [row["policy"], row["n"], f"{row['delta']:.2f}", f"{row['brier']:.4f}", f"{row['mean_p']:.3f}"]
            for row in policy_table(rows)
        ],
    )
    holdout = chronological_holdout(rows, min_train)
    lines += [
        "",
        "## Chronological Holdout",
        "",
        f"Fixed policies scored only after at least {min_train} prior settled penalty/red rows.",
        "",
    ]
    lines += md_table(
        ["policy", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [row["policy"], row["n"], f"{row['delta']:.2f}", f"{row['brier']:.4f}", f"{row['mean_p']:.3f}"]
            for row in holdout
        ],
    )
    wf = walk_forward(rows, min_train)
    lines += [
        "",
        "## Walk-Forward Policy Selection",
        "",
        f"- min_train: {min_train}",
        f"- n: {wf['n']}",
        f"- delta_vs_current: {wf['delta']:.2f}",
        f"- brier: {wf['brier']:.4f}",
            f"- choices: {wf['choices']}",
            "",
    ]
    lines += [
        "## Sensitivity Summary",
        "",
        f"- robustness_policy: {robustness['policy']}",
        f"- robustness_configs: {robustness['configs']}",
        f"- robustness_positive_configs: {robustness['positive_configs']}",
        f"- robustness_best_policy_configs: {robustness['best_policy_configs']}",
        f"- robustness_min_delta_vs_current: {float(robustness['min_delta_vs_current']):.2f}",
        f"- robustness_median_delta_vs_current: {float(robustness['median_delta_vs_current']):.2f}",
        f"- robustness_max_delta_vs_current: {float(robustness['max_delta_vs_current']):.2f}",
        "",
        "The grid varies the chronological cutoff over 4, 6, 8, 10, 12, and 16 prior penalty/red rows. It is a fragility check for the current guardrail, not a new tuning search.",
        "",
    ]
    lines += md_table(
        [
            "min_train",
            "holdout_rows",
            "delta_vs_current",
            "brier",
            "mean_p",
            "rank",
            "best_policy",
            "best_delta_vs_current",
        ],
        [
            [
                f"{int(row['min_train'])}",
                f"{int(row['holdout_rows'])}",
                f"{float(row['delta_vs_current']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
                f"{int(row['rank'])}",
                str(row["best_policy"]),
                f"{float(row['best_delta_vs_current']):.2f}",
            ]
            for row in sensitivity_rows
        ],
    )
    lines += [""]
    if trials > 0:
        lines += [
            "## Match-Level Bootstrap",
            "",
            f"Trials: {trials}; seed: {seed}. CI is for fixed-policy delta vs current on penalty/red rows.",
            "",
        ]
        boot_rows = []
        for name, strategy in policy_specs():
            if name == "current":
                continue
            point = score(rows, strategy)["delta"]
            lo, med, hi = bootstrap(rows, strategy, trials=trials, seed=seed)
            boot_rows.append([name, f"{point:.2f}", f"{lo:.2f}", f"{med:.2f}", f"{hi:.2f}"])
        lines += md_table(["policy", "point", "ci_2.5", "median", "ci_97.5"], boot_rows)
        lines += [""]
        strategies = {name: strategy for name, strategy in policy_specs()}
        holdout_by_policy = {row["policy"]: row for row in holdout}
        direct_rows = []
        for label, left_name, right_name in [
            ("global_asym_minus_field", "global_asym075_cap12", "field"),
            ("global_asym_minus_current", "global_asym075_cap12", "current"),
        ]:
            direct = bootstrap_strategy_diff(
                rows,
                strategies[left_name],
                strategies[right_name],
                trials=trials,
                seed=seed,
            )
            full_diff = score(rows, strategies[left_name])["delta"] - score(rows, strategies[right_name])["delta"]
            holdout_diff = holdout_by_policy[left_name]["delta"] - holdout_by_policy[right_name]["delta"]
            direct_rows.append(
                [
                    label,
                    f"{full_diff:.2f}",
                    f"{holdout_diff:.2f}",
                    f"{direct['ci_2.5']:.2f}",
                    f"{direct['median']:.2f}",
                    f"{direct['ci_97.5']:.2f}",
                    f"{direct['pr_gt_0']:.3f}",
                ]
            )
        lines += [
            "## Direct Penalty/Red Policy Comparisons",
            "",
            "Direct left-minus-right checks for the current global asymmetric policy on penalty/red rows.",
            "",
        ]
        lines += md_table(
            [
                "comparison",
                "full_sample_delta",
                "chronological_holdout_delta",
                "bootstrap_ci_2.5",
                "bootstrap_median",
                "bootstrap_ci_97.5",
                "bootstrap_pr_gt_0",
            ],
            direct_rows,
        )
        lines += [""]
    lines += [
        "## Interpretation",
        "",
        "- Penalty/red rows are sparse; treat this as a guardrail, not a robust family-specific promotion.",
        "- No referee-specific penalty/red transform is validated here.",
        "- If field is visible, use the global asymmetric policy unless a direct penalty/red market anchor is available.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=8)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=47)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_penalty_red_rows(sports_predict=args.sports_predict)
    text = render(rows, min_train=args.min_train, trials=args.bootstrap, seed=args.seed)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
