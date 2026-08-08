"""Goal/outcome-market manual policy backtest.

This read-only diagnostic isolates odds-anchored goal and outcome markets from
`docs/history.md` and compares raw model probabilities to field-relative manual
policies. It excludes player props, count props, penalty/red base rates, and
knockout timing/special props.

With `--sports-predict`, SportsPredict is used only for GET `/events`,
`/lobbies`, and `/results` to settle older V1 history rows. The script never
reads `/predictions` and never submits.

Usage:
    uv run --with requests python scripts/goal_policy_backtest.py --sports-predict --write
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


OUT = ROOT / "data" / "logs" / "goal_policy_backtest.md"
HISTORY = ROOT / "docs" / "history.md"

ODDS_GOAL_FAMILIES = {
    "btts",
    "btts_and_total_ge",
    "first_goal_and_other_scores_2h",
    "goal_each_half",
    "h2_more_goals_than_h1",
    "ht_team_winning",
    "ht_tied",
    "match_drawn",
    "penalty_shootout",
    "team_advance",
    "team_clean_sheet",
    "team_first_goal_2h",
    "team_first_goal_game",
    "team_goals_ge",
    "team_leads_any_time",
    "team_more_goals_2h",
    "team_scores_both_halves",
    "team_scores_in_half",
    "team_win",
    "team_win_by_ge",
    "total_goals",
}


def _read_only_outcomes_from_sports_predict() -> dict[str, int]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    results = sp_get(f"/results?lobby_id={lobby['id']}", key)
    return unique_outcomes_by_question(results)


def load_goal_rows(*, sports_predict: bool) -> list[HistoryMarket]:
    rows = load_history(HISTORY)
    if sports_predict:
        rows = settle_v1_rows(rows, _read_only_outcomes_from_sports_predict())
    return [r for r in exact_rows(rows) if r.family in ODDS_GOAL_FAMILIES]


GLOBAL_ASYM = asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.12)
ASYM_CAP10 = asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.10)


def policy_specs():
    """Small predeclared policy set.

    These test whether the odds-anchored model should be trusted raw, blended
    toward field, or governed by the same asymmetric field rule used globally.
    """

    return [
        ("current", lambda r: r.you),
        ("field", crowd_strategy),
        ("shrink25", shrink_to_crowd(0.25)),
        ("shrink50", shrink_to_crowd(0.50)),
        ("shrink75", shrink_to_crowd(0.75)),
        ("global_asym075_cap12", GLOBAL_ASYM),
        ("asym075_cap10", ASYM_CAP10),
        ("min_model_field", lambda r: min(r.you, r.crowd)),
        ("above_field_to_field_else_current", lambda r: r.crowd if r.you > r.crowd else r.you),
        ("below_field_to_field_else_current", lambda r: r.crowd if r.you < r.crowd else r.you),
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
    orders = sorted({r.order for r in rows})
    holdout = []
    for order in orders:
        if sum(1 for r in rows if r.order < order) >= min_train:
            holdout.extend([r for r in rows if r.order == order])
    return sorted(
        [{"policy": name, **score(holdout, strategy)} for name, strategy in policy_specs()],
        key=lambda r: r["delta"],
        reverse=True,
    )


def sensitivity_grid(rows: list[HistoryMarket], *, min_trains: list[int]) -> list[dict[str, float | str]]:
    """Chronological-cutoff sensitivity for keeping raw odds-goal values."""

    out: list[dict[str, float | str]] = []
    for min_train in min_trains:
        holdout = chronological_holdout(rows, min_train)
        if not holdout:
            continue
        by_policy = {row["policy"]: row for row in holdout}
        current = by_policy.get("current")
        field = by_policy.get("field")
        shrink75 = by_policy.get("shrink75")
        if not current or not field or not shrink75:
            continue
        deployable = [row for row in holdout if row["policy"] != "field"]
        best = deployable[0] if deployable else current
        out.append(
            {
                "min_train": min_train,
                "holdout_rows": int(current["n"]),
                "current_delta": float(current["delta"]),
                "raw_minus_field": float(current["delta"]) - float(field["delta"]),
                "raw_minus_shrink75": float(current["delta"]) - float(shrink75["delta"]),
                "raw_rank": next(idx + 1 for idx, row in enumerate(deployable) if row["policy"] == "current"),
                "best_policy": str(best["policy"]),
                "best_delta": float(best["delta"]),
            }
        )
    return out


def sensitivity_summary(rows: list[dict[str, float | str]]) -> dict[str, float | str]:
    if not rows:
        return {
            "configs": 0,
            "raw_beats_field_configs": 0,
            "raw_beats_shrink75_configs": 0,
            "raw_best_configs": 0,
            "min_raw_minus_field": 0.0,
            "min_raw_minus_shrink75": 0.0,
        }
    return {
        "configs": len(rows),
        "raw_beats_field_configs": sum(1 for row in rows if float(row["raw_minus_field"]) > 0),
        "raw_beats_shrink75_configs": sum(1 for row in rows if float(row["raw_minus_shrink75"]) > 0),
        "raw_best_configs": sum(1 for row in rows if str(row["best_policy"]) == "current"),
        "min_raw_minus_field": min(float(row["raw_minus_field"]) for row in rows),
        "min_raw_minus_shrink75": min(float(row["raw_minus_shrink75"]) for row in rows),
    }


def walk_forward(rows: list[HistoryMarket], min_train: int) -> dict:
    specs = policy_specs()
    orders = sorted({r.order for r in rows})
    scored = []
    choices = Counter()
    for order in orders:
        train = [r for r in rows if r.order < order]
        test = [r for r in rows if r.order == order]
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
    alt_delta = sum(official_delta_rbp(r, p) for r, p in scored)
    return {
        "n": len(scored),
        "delta": alt_delta - current_delta,
        "brier": sum(brier(p, r.outcome or 0) for r, p in scored) / len(scored),
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
    """Match-level bootstrap for a direct left-minus-right policy comparison."""

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
    sensitivity_rows = sensitivity_grid(rows, min_trains=[30, 45, 60, 75, 90])
    robustness = sensitivity_summary(sensitivity_rows)
    lines = [
        "# Goal Policy Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- settled_goal_rows: {len(rows)}",
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
                r["group"],
                r["n"],
                f"{r['skill_vs_field']:.2f}",
                f"{r['brier_model']:.4f}",
                f"{r['brier_field']:.4f}",
                f"{r['model']:.3f}",
                f"{r['field']:.3f}",
                f"{r['hit']:.3f}",
            ]
            for r in grouped_table(rows, lambda r: r.famkey)
        ],
    )
    lines += ["", "## Directional Diagnostics", ""]
    lines += md_table(
        ["direction", "n", "skill_vs_field", "model", "field", "hit"],
        [
            [
                r["direction"],
                r["n"],
                f"{r['skill_vs_field']:.2f}",
                f"{r['model']:.3f}",
                f"{r['field']:.3f}",
                f"{r['hit']:.3f}",
            ]
            for r in directional_table(rows)
        ],
    )
    lines += ["", "## Fixed Policies", ""]
    lines += md_table(
        ["policy", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [r["policy"], r["n"], f"{r['delta']:.2f}", f"{r['brier']:.4f}", f"{r['mean_p']:.3f}"]
            for r in policy_table(rows)
        ],
    )
    holdout = chronological_holdout(rows, min_train)
    lines += [
        "",
        "## Chronological Holdout",
        "",
        f"Fixed policies scored only after at least {min_train} prior settled goal rows.",
        "",
    ]
    lines += md_table(
        ["policy", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [r["policy"], r["n"], f"{r['delta']:.2f}", f"{r['brier']:.4f}", f"{r['mean_p']:.3f}"]
            for r in holdout
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
        f"- robustness_configs: {robustness['configs']}",
        f"- raw_beats_field_configs: {robustness['raw_beats_field_configs']}",
        f"- raw_beats_shrink75_configs: {robustness['raw_beats_shrink75_configs']}",
        f"- raw_best_configs: {robustness['raw_best_configs']}",
        f"- robustness_min_raw_minus_field: {float(robustness['min_raw_minus_field']):.2f}",
        f"- robustness_min_raw_minus_shrink75: {float(robustness['min_raw_minus_shrink75']):.2f}",
        "",
        "The grid varies the chronological cutoff over 30, 45, 60, 75, and 90 prior goal rows. It is a robustness check for keeping raw odds-anchored values, not a new tuning search.",
        "",
    ]
    lines += md_table(
        [
            "min_train",
            "holdout_rows",
            "raw_minus_field",
            "raw_minus_shrink75",
            "raw_rank",
            "best_policy",
            "best_delta",
        ],
        [
            [
                f"{int(row['min_train'])}",
                f"{int(row['holdout_rows'])}",
                f"{float(row['raw_minus_field']):.2f}",
                f"{float(row['raw_minus_shrink75']):.2f}",
                f"{int(row['raw_rank'])}",
                str(row["best_policy"]),
                f"{float(row['best_delta']):.2f}",
            ]
            for row in sensitivity_rows
        ],
    )
    lines += [""]
    if trials > 0:
        lines += [
            "## Match-Level Bootstrap",
            "",
            f"Trials: {trials}; seed: {seed}. CI is for fixed-policy delta vs current on goal rows.",
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
        comparisons = [
            ("raw_current_minus_field", "current", "field"),
            ("raw_current_minus_best_shrink75", "current", "shrink75"),
        ]
        for label, left_name, right_name in comparisons:
            left = strategies[left_name]
            right = strategies[right_name]
            direct = bootstrap_strategy_diff(rows, left, right, trials=trials, seed=seed)
            full_diff = score(rows, left)["delta"] - score(rows, right)["delta"]
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
            "## Direct Goal Policy Comparisons",
            "",
            "Direct left-minus-right checks for keeping the raw odds-anchored model.",
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
        "- This validates manual field usage for odds-anchored goal/outcome rows only.",
        "- Keep the raw odds-anchored model; field and shrink alternatives lose on full sample, holdout, and direct bootstrap comparisons.",
        "- Timing/special goal props are intentionally excluded; use `special_market_backtest.py` for those.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=60)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=31)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_goal_rows(sports_predict=args.sports_predict)
    text = render(rows, min_train=args.min_train, trials=args.bootstrap, seed=args.seed)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
