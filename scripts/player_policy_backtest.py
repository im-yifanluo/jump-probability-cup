"""Player-prop manual policy backtest.

This is a read-only strategy diagnostic for the manual entry. It uses the same
history parser/scorer as `scripts/history_backtest.py`, filters settled player
props, and evaluates a small predeclared set of field-relative policies.

It does not read `/predictions` and does not submit anything. With
`--sports-predict`, SportsPredict is used only for GET `/events`, `/lobbies`,
and `/results` to settle older V1 history rows.

Usage:
    uv run --with pandas --with pyyaml --with requests python scripts/player_policy_backtest.py --sports-predict --write
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


OUT = ROOT / "data" / "logs" / "player_policy_backtest.md"
HISTORY = ROOT / "docs" / "history.md"


def _read_only_outcomes_from_sports_predict() -> dict[str, int]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    results = sp_get(f"/results?lobby_id={lobby['id']}", key)
    return unique_outcomes_by_question(results)


def load_player_rows(*, sports_predict: bool) -> list[HistoryMarket]:
    rows = load_history(HISTORY)
    if sports_predict:
        rows = settle_v1_rows(rows, _read_only_outcomes_from_sports_predict())
    return [r for r in exact_rows(rows) if r.family.startswith("player_")]


def policy_specs():
    """Small predeclared policy set for player props.

    The names describe how much of the independent model-vs-field deviation is
    retained. `asym` variants never go above the field, matching the global
    promoted manual policy family.
    """

    return [
        ("current", lambda r: r.you),
        ("field", crowd_strategy),
        ("shrink25", shrink_to_crowd(0.25)),
        ("shrink50", shrink_to_crowd(0.50)),
        ("shrink75", shrink_to_crowd(0.75)),
        ("global_asym075_cap12", asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.12)),
        ("asym025_cap10", asymmetric_field_policy(above_k=0.0, below_k=0.25, max_abs_dev=0.10)),
        ("asym050_cap10", asymmetric_field_policy(above_k=0.0, below_k=0.50, max_abs_dev=0.10)),
        ("asym075_cap10", asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.10)),
        ("min_model_field", lambda r: min(r.you, r.crowd)),
    ]


def score(rows: list[HistoryMarket], strategy) -> dict[str, float]:
    if not rows:
        return {"n": 0, "delta": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    preds = [clip_prob(strategy(r)) for r in rows]
    delta = sum(official_delta_rbp(r, p) for r, p in zip(rows, preds))
    br = sum(brier(p, r.outcome or 0) for r, p in zip(rows, preds)) / len(rows)
    return {
        "n": len(rows),
        "delta": delta,
        "brier": br,
        "mean_p": sum(preds) / len(preds),
    }


def summary_by_policy(rows: list[HistoryMarket]) -> list[dict]:
    out = []
    for name, strategy in policy_specs():
        out.append({"policy": name, **score(rows, strategy)})
    return sorted(out, key=lambda r: r["delta"], reverse=True)


def family_table(rows: list[HistoryMarket]) -> list[dict]:
    grouped: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        grouped[row.famkey].append(row)
    out = []
    for fam, part in grouped.items():
        skill = sum(100 * (brier(r.crowd, r.outcome or 0) - brier(r.you, r.outcome or 0)) for r in part)
        out.append(
            {
                "family": fam,
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


def chronological_holdout(rows: list[HistoryMarket], min_train: int) -> list[dict]:
    orders = sorted({r.order for r in rows})
    holdout = []
    for order in orders:
        train_n = sum(1 for r in rows if r.order < order)
        if train_n >= min_train:
            holdout.extend([r for r in rows if r.order == order])
    return [{"policy": name, **score(holdout, strategy)} for name, strategy in policy_specs()]


def walk_forward(rows: list[HistoryMarket], min_train: int) -> dict:
    """One-match-ahead policy selection on player rows only.

    This is not promoted as a deployable adaptive policy unless it beats the
    simple fixed policy. It is a guardrail against in-sample policy mining.
    """

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
            best = max(
                ((name, strategy, score(train, strategy)["delta"]) for name, strategy in specs),
                key=lambda x: x[2],
            )
            name, strategy = best[0], best[1]
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
        "# Player Policy Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- settled_player_rows: {len(rows)}",
        f"- matches: {len({r.match for r in rows})}",
        "- scoring: exact official-RBP deltas from displayed market RBP and realized Brier",
        "",
        "## Family Diagnostics",
        "",
    ]
    lines += md_table(
        ["family", "n", "skill_vs_field", "brier_model", "brier_field", "model", "field", "hit"],
        [
            [
                r["family"],
                r["n"],
                f"{r['skill_vs_field']:.2f}",
                f"{r['brier_model']:.4f}",
                f"{r['brier_field']:.4f}",
                f"{r['model']:.3f}",
                f"{r['field']:.3f}",
                f"{r['hit']:.3f}",
            ]
            for r in family_table(rows)
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
            for r in summary_by_policy(rows)
        ],
    )
    holdout = chronological_holdout(rows, min_train)
    lines += [
        "",
        "## Chronological Holdout",
        "",
        f"Fixed policies scored only after at least {min_train} prior settled player rows.",
        "",
    ]
    lines += md_table(
        ["policy", "n", "delta_vs_current", "brier", "mean_p"],
        [
            [r["policy"], r["n"], f"{r['delta']:.2f}", f"{r['brier']:.4f}", f"{r['mean_p']:.3f}"]
            for r in sorted(holdout, key=lambda x: x["delta"], reverse=True)
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
            f"Trials: {trials}; seed: {seed}. CI is for fixed-policy delta vs current on player rows.",
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
        direct = bootstrap_strategy_diff(
            rows,
            strategies["asym075_cap10"],
            strategies["field"],
            trials=trials,
            seed=seed,
        )
        all_diff = score(rows, strategies["asym075_cap10"])["delta"] - score(rows, strategies["field"])["delta"]
        holdout = chronological_holdout(rows, min_train)
        by_policy = {row["policy"]: row for row in holdout}
        holdout_diff = by_policy["asym075_cap10"]["delta"] - by_policy["field"]["delta"]
        lines += [
            "## Direct Player Policy Comparison",
            "",
            "`asym075_cap10_minus_field` tests whether the researched-player policy beats simply using the field for player props when the field is visible.",
            "",
            "| comparison | full_sample_delta | chronological_holdout_delta | bootstrap_ci_2.5 | bootstrap_median | bootstrap_ci_97.5 | bootstrap_pr_gt_0 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            (
                f"| asym075_cap10_minus_field | {all_diff:.2f} | {holdout_diff:.2f} | "
                f"{direct['ci_2.5']:.2f} | {direct['median']:.2f} | "
                f"{direct['ci_97.5']:.2f} | {direct['pr_gt_0']:.3f} |"
            ),
            "",
        ]
    lines += [
        "## Interpretation",
        "",
        "- Treat this as player-family evidence only; do not apply these results to goal/count markets.",
        "- Field values are unavailable before lock; unresolved player props should be exact-prop/lineup led unless structured player research clears the risk.",
        "- `asym075_cap10` remains the promoted researched-player policy only because its overall/player-family bootstrap is positive; the direct comparison with field is weaker and should be rechecked as new player rows settle.",
        "- The sample is still small, so prefer the simplest robust policy over the in-sample winner.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=30)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_player_rows(sports_predict=args.sports_predict)
    text = render(rows, min_train=args.min_train, trials=args.bootstrap, seed=args.seed)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
