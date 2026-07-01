"""Count-market manual policy backtest.

This read-only diagnostic isolates count markets from `docs/history.md` and
tests a small set of predeclared field-relative policies. It is meant to guide
manual entry for SOT/corners/cards/fouls/offsides thresholds and head-to-heads,
not to refit the underlying count model.

With `--sports-predict`, SportsPredict is used only for GET `/events`,
`/lobbies`, and `/results` to settle older V1 history rows. The script never
reads `/predictions` and never submits.

Usage:
    uv run --with requests python scripts/count_policy_backtest.py --sports-predict --write
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


OUT = ROOT / "data" / "logs" / "count_policy_backtest.md"
HISTORY = ROOT / "docs" / "history.md"
COUNT_FAMILIES = {
    "team_count_ge",
    "total_count_ge",
    "both_teams_count_ge",
    "team_more_count_than",
}
COUNT_STATS = {"cards", "corners", "sot", "fouls", "offsides"}


def _read_only_outcomes_from_sports_predict() -> dict[str, int]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    results = sp_get(f"/results?lobby_id={lobby['id']}", key)
    return unique_outcomes_by_question(results)


def is_count(row: HistoryMarket) -> bool:
    if row.family.startswith("player_"):
        return False
    if row.stat == "shots":
        return False
    return row.family in COUNT_FAMILIES and row.stat in COUNT_STATS


def load_count_rows(*, sports_predict: bool) -> list[HistoryMarket]:
    rows = load_history(HISTORY)
    if sports_predict:
        rows = settle_v1_rows(rows, _read_only_outcomes_from_sports_predict())
    return [r for r in exact_rows(rows) if is_count(r)]


GLOBAL_ASYM = asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.12)
ASYM_CAP10 = asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.10)


def _strong_sot(row: HistoryMarket) -> bool:
    return row.stat == "sot" and row.family in {"team_count_ge", "team_more_count_than"}


def policy_specs():
    """Small predeclared count-policy set.

    The mixed policies reflect prior evidence before this script: cards were
    weak live, team SOT/head-to-head SOT was strong, and the global asymmetric
    field policy was robust overall. They are tested here rather than assumed.
    """

    return [
        ("current", lambda r: r.you),
        ("field", crowd_strategy),
        ("shrink50", shrink_to_crowd(0.50)),
        ("global_asym075_cap12", GLOBAL_ASYM),
        ("asym075_cap10", ASYM_CAP10),
        ("min_model_field", lambda r: min(r.you, r.crowd)),
        ("cards_field_else_global", lambda r: r.crowd if r.stat == "cards" else GLOBAL_ASYM(r)),
        (
            "cards_total_sot_field_else_global",
            lambda r: r.crowd if r.stat == "cards" or (r.stat == "sot" and r.family == "total_count_ge") else GLOBAL_ASYM(r),
        ),
        (
            "strong_sot_current_cards_field_else_global",
            lambda r: r.you if _strong_sot(r) else (r.crowd if r.stat == "cards" else GLOBAL_ASYM(r)),
        ),
        (
            "strong_sot_current_else_global",
            lambda r: r.you if _strong_sot(r) else GLOBAL_ASYM(r),
        ),
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
    lines = [
        "# Count Policy Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- settled_count_rows: {len(rows)}",
        f"- matches: {len({r.match for r in rows})}",
        "- scoring: exact official-RBP deltas from displayed market RBP and realized Brier",
        "",
        "## By Stat",
        "",
    ]
    lines += md_table(
        ["stat", "n", "skill_vs_field", "brier_model", "brier_field", "model", "field", "hit"],
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
            for r in grouped_table(rows, lambda r: r.stat or "none")
        ],
    )
    lines += ["", "## By Family", ""]
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
        f"Fixed policies scored only after at least {min_train} prior settled count rows.",
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
    if trials > 0:
        lines += [
            "## Match-Level Bootstrap",
            "",
            f"Trials: {trials}; seed: {seed}. CI is for fixed-policy delta vs current on count rows.",
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
            (
                "promoted_minus_field",
                "strong_sot_current_cards_field_else_global",
                "field",
            ),
            (
                "promoted_minus_strong_sot_no_card_field",
                "strong_sot_current_cards_field_else_global",
                "strong_sot_current_else_global",
            ),
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
            "## Direct Count Policy Comparisons",
            "",
            "Direct left-minus-right checks for the promoted count policy.",
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
        "- Count evidence is not uniform: SOT team/head-to-head rows have been strong, while cards are weak.",
        "- Prefer simple predeclared policies. Do not promote family-mined thresholds unless they survive holdout and bootstrap checks.",
        "- This report controls manual field usage; it does not refit count-model means.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=60)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=23)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_count_rows(sports_predict=args.sports_predict)
    text = render(rows, min_train=args.min_train, trials=args.bootstrap, seed=args.seed)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
