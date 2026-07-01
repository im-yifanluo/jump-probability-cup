"""Backtest strategy transforms against docs/history.md.

This is the live-history validator, not the club-data model fitter.  It uses
settled SportsPredict blocks with explicit RESULT/OUTCOME/RBP and scores
alternate predictions by exact Brier deltas from the displayed official RBP.

Usage:
    python3 scripts/history_backtest.py
"""

from __future__ import annotations

import argparse
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import (  # noqa: E402
    asymmetric_field_policy,
    brier,
    cap_deviation,
    clip_prob,
    crowd_strategy,
    evaluate,
    exact_rows,
    flat50_strategy,
    group_by_fam,
    load_history,
    official_alt_rbp,
    settle_v1_rows,
    shrink_to_crowd,
    unique_outcomes_by_question,
)
from manual.anchors import sp_get, sports_predict_key  # noqa: E402


def _fmt(v: float, nd: int = 2) -> str:
    return f"{v:.{nd}f}"


def fixed_policy_specs():
    return [
        ("current", lambda r: r.you),
        ("crowd", crowd_strategy),
        ("flat50", flat50_strategy),
        ("shrink25", shrink_to_crowd(0.25)),
        ("shrink50", shrink_to_crowd(0.50)),
        ("shrink75", shrink_to_crowd(0.75)),
        ("cap12", cap_deviation(0.12)),
        ("cap15", cap_deviation(0.15)),
        (
            "asym_bal",
            asymmetric_field_policy(above_k=0.0, below_k=0.75),
        ),
        (
            "asym_bal12",
            asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.12),
        ),
        (
            "asym_bal15",
            asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.15),
        ),
        (
            "asym_guard",
            asymmetric_field_policy(above_k=0.0, below_k=1.0, max_abs_dev=0.15),
        ),
        (
            "asym_soft",
            asymmetric_field_policy(
                above_k=0.25,
                below_k=1.0,
                max_abs_dev=0.15,
                neutral_width=0.03,
                neutral_k=0.0,
            ),
        ),
        (
            "below_ext",
            asymmetric_field_policy(above_k=0.0, below_k=1.25, max_abs_dev=0.15),
        ),
        ("expand125", shrink_to_crowd(1.25)),
    ]


def policy_grid():
    """Small manual-policy grid for walk-forward selection.

    The grid is intentionally coarse.  Its job is to test whether a family of
    conservative field-relative policies generalizes, not to mine the history
    file for a precise threshold.
    """

    for above_k in (0.0, 0.25, 0.50, 0.75, 1.0):
        for below_k in (0.50, 0.75, 1.0, 1.25):
            for max_abs_dev in (0.10, 0.12, 0.15, 0.18, None):
                yield (
                    f"a{above_k:g}_b{below_k:g}_cap{max_abs_dev or 'none'}",
                    asymmetric_field_policy(
                        above_k=above_k,
                        below_k=below_k,
                        max_abs_dev=max_abs_dev,
                    ),
                )
    for k in (0.0, 0.25, 0.50, 0.75, 1.0):
        yield (f"shrink{k:g}", shrink_to_crowd(k))


def _read_only_outcomes_from_sports_predict() -> dict[str, int]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    results = sp_get(f"/results?lobby_id={lobby['id']}", key)
    return unique_outcomes_by_question(results)


def _print_strategy_table(rows) -> None:
    print("=== Strategy Transforms, Exact Official-RBP Deltas ===")
    print(f"{'strategy':<13} {'n':>4} {'official_rbp':>13} {'delta':>10} {'brier':>8}")
    for name, strategy in fixed_policy_specs():
        e = evaluate(rows, strategy)
        print(f"{name:<13} {int(e['n']):>4} {_fmt(e['official_rbp']):>13} "
              f"{_fmt(e['delta']):>10} {_fmt(e['brier'], 4):>8}")


def _print_family_table(rows) -> None:
    print("\n=== Current Strategy By Family ===")
    print(f"{'family':<34} {'n':>4} {'rbp':>9} {'mean':>8} {'brier':>8} "
          f"{'you':>7} {'crowd':>7} {'hit':>7} {'dev':>7}")
    family_rows = []
    for fam, fam_rows in group_by_fam(exact_rows(rows)).items():
        n = len(fam_rows)
        rbp = sum(r.official_rbp or 0.0 for r in fam_rows)
        br = sum((r.you - (r.outcome or 0)) ** 2 for r in fam_rows) / n
        you = sum(r.you for r in fam_rows) / n
        crowd = sum(r.crowd for r in fam_rows) / n
        hit = sum(r.outcome or 0 for r in fam_rows) / n
        dev = sum(r.you - r.crowd for r in fam_rows) / n
        family_rows.append((rbp, fam, n, rbp, rbp / n, br, you, crowd, hit, dev))
    for _, fam, n, rbp, mean, br, you, crowd, hit, dev in sorted(family_rows):
        print(f"{fam:<34} {n:>4} {_fmt(rbp):>9} {_fmt(mean):>8} {_fmt(br, 4):>8} "
              f"{_fmt(you):>7} {_fmt(crowd):>7} {_fmt(hit):>7} {_fmt(dev):>7}")


def _print_calibration(rows) -> None:
    print("\n=== Calibration Buckets ===")
    settled = exact_rows(rows)
    buckets = [
        (0.00, 0.20, "0-20"),
        (0.20, 0.35, "20-35"),
        (0.35, 0.50, "35-50"),
        (0.50, 0.65, "50-65"),
        (0.65, 0.80, "65-80"),
        (0.80, 1.01, "80-100"),
    ]
    print(f"{'bucket':<8} {'n':>4} {'you':>7} {'crowd':>7} {'hit':>7} "
          f"{'brier_you':>10} {'brier_crowd':>12}")
    for lo, hi, label in buckets:
        b = [r for r in settled if lo <= r.you < hi]
        if not b:
            continue
        n = len(b)
        hit = sum(r.outcome or 0 for r in b) / n
        you = sum(r.you for r in b) / n
        crowd = sum(r.crowd for r in b) / n
        by = sum((r.you - (r.outcome or 0)) ** 2 for r in b) / n
        bc = sum((r.crowd - (r.outcome or 0)) ** 2 for r in b) / n
        print(f"{label:<8} {n:>4} {_fmt(you):>7} {_fmt(crowd):>7} {_fmt(hit):>7} "
              f"{_fmt(by, 4):>10} {_fmt(bc, 4):>12}")


def _print_deviation_table(rows) -> None:
    print("\n=== Deviation From Field ===")
    settled = exact_rows(rows)
    buckets = [
        (0.00, 0.03, "<=3pt"),
        (0.03, 0.08, "3-8pt"),
        (0.08, 0.15, "8-15pt"),
        (0.15, 1.01, ">15pt"),
    ]
    print(f"{'bucket':<8} {'n':>4} {'skill':>9} {'mean':>8} {'absdev':>8} {'rbp':>9}")
    for lo, hi, label in buckets:
        b = [r for r in settled if lo <= abs(r.you - r.crowd) < hi]
        if not b:
            continue
        skill = sum(100 * (((r.crowd - (r.outcome or 0)) ** 2) - ((r.you - (r.outcome or 0)) ** 2)) for r in b)
        rbp = sum(r.official_rbp or 0.0 for r in b)
        absdev = sum(abs(r.you - r.crowd) for r in b) / len(b)
        print(f"{label:<8} {len(b):>4} {_fmt(skill):>9} {_fmt(skill/len(b)):>8} "
              f"{_fmt(absdev):>8} {_fmt(rbp):>9}")

    print("\n=== Directional Deviation ===")
    print(f"{'direction':<12} {'n':>4} {'skill':>9} {'mean':>8} {'you':>7} {'crowd':>7} {'hit':>7}")
    for label, pred in (
        ("below_field", lambda r: r.you < r.crowd),
        ("near_field", lambda r: abs(r.you - r.crowd) < 0.005),
        ("above_field", lambda r: r.you > r.crowd),
    ):
        b = [r for r in settled if pred(r)]
        if not b:
            continue
        skill = sum(100 * (((r.crowd - (r.outcome or 0)) ** 2) - ((r.you - (r.outcome or 0)) ** 2)) for r in b)
        you = sum(r.you for r in b) / len(b)
        crowd = sum(r.crowd for r in b) / len(b)
        hit = sum(r.outcome or 0 for r in b) / len(b)
        print(f"{label:<12} {len(b):>4} {_fmt(skill):>9} {_fmt(skill/len(b)):>8} "
              f"{_fmt(you):>7} {_fmt(crowd):>7} {_fmt(hit):>7}")


def _best_grid_factor(train_rows) -> float:
    grid = [0.0, 0.25, 0.50, 0.75, 1.0, 1.25]
    best_k, best_score = 1.0, None
    for k in grid:
        score = evaluate(train_rows, shrink_to_crowd(k))["official_rbp"]
        if best_score is None or score > best_score:
            best_k, best_score = k, score
    return best_k


def _best_grid_policy(train_rows):
    best_name, best_strategy, best_score = "current", lambda r: r.you, None
    for name, strategy in policy_grid():
        score = evaluate(train_rows, strategy)["official_rbp"]
        if best_score is None or score > best_score:
            best_name, best_strategy, best_score = name, strategy, score
    return best_name, best_strategy


def _print_walk_forward(rows, min_train: int) -> None:
    """Chronological one-match-ahead shrinkage check.

    This is intentionally simple: before each match, choose the global shrink
    factor that would have worked best on prior settled matches, then score that
    factor on the next match.  It is a guard against ex-post "best k" overfit.
    """
    settled = exact_rows(rows)
    orders = sorted({r.order for r in settled})
    scored = []
    for order in orders:
        train = [r for r in settled if r.order < order]
        test = [r for r in settled if r.order == order]
        if len(train) < min_train:
            k = 1.0
        else:
            k = _best_grid_factor(train)
        for row in test:
            p = clip_prob(shrink_to_crowd(k)(row))
            scored.append((row, p, k))
    current = sum((r.official_rbp or 0.0) for r, _, _ in scored)
    alt = sum(official_alt_rbp(r, p) for r, p, _ in scored)
    print("\n=== Walk-Forward Guardrail ===")
    print(f"one-match-ahead global shrink grid, min_train={min_train}")
    print(f"n={len(scored)} current={current:.2f} walk_forward={alt:.2f} delta={alt-current:+.2f}")

    selected = []
    for order in orders:
        train = [r for r in settled if r.order < order]
        test = [r for r in settled if r.order == order]
        if len(train) < min_train:
            name, strategy = "current", lambda r: r.you
        else:
            name, strategy = _best_grid_policy(train)
        for row in test:
            selected.append((row, clip_prob(strategy(row)), name))
    current = sum((r.official_rbp or 0.0) for r, _, _ in selected)
    alt = sum(official_alt_rbp(r, p) for r, p, _ in selected)
    counts: dict[str, int] = {}
    for _, _, name in selected:
        counts[name] = counts.get(name, 0) + 1
    chosen = ", ".join(f"{name}:{count}" for name, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:6])
    print(f"one-match-ahead policy grid, min_train={min_train}")
    print(f"n={len(selected)} current={current:.2f} walk_forward={alt:.2f} delta={alt-current:+.2f}")
    print(f"most selected policies: {chosen}")


def _print_chronological_holdout(rows, min_train: int) -> None:
    settled = exact_rows(rows)
    orders = sorted({r.order for r in settled})
    holdout = []
    for order in orders:
        if len([r for r in settled if r.order < order]) >= min_train:
            holdout.extend([r for r in settled if r.order == order])
    if not holdout:
        return
    print("\n=== Chronological Fixed-Policy Holdout ===")
    print(f"score fixed policies only on matches after {min_train} prior settled rows")
    print(f"{'strategy':<13} {'n':>4} {'delta':>10} {'brier':>8}")
    for name, strategy in fixed_policy_specs():
        if name in {"current", "flat50", "expand125"}:
            continue
        delta = 0.0
        br = 0.0
        for row in holdout:
            p = clip_prob(strategy(row))
            delta += 100 * (((row.you - (row.outcome or 0)) ** 2) - ((p - (row.outcome or 0)) ** 2))
            br += (p - (row.outcome or 0)) ** 2
        print(f"{name:<13} {len(holdout):>4} {_fmt(delta):>10} {_fmt(br / len(holdout), 4):>8}")


def _bootstrap_delta(rows, strategy, trials: int, seed: int) -> tuple[float, float, float]:
    """Match-level bootstrap CI for a fixed strategy's RBP delta vs current."""
    settled = exact_rows(rows)
    by_match: dict[str, list] = {}
    for row in settled:
        by_match.setdefault(row.match, []).append(row)
    matches = sorted(by_match)
    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        delta = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for row in by_match[match]:
                p = clip_prob(strategy(row))
                delta += 100 * (((row.you - (row.outcome or 0)) ** 2) - ((p - (row.outcome or 0)) ** 2))
        draws.append(delta)
    draws.sort()
    lo = draws[int(0.025 * (trials - 1))]
    med = draws[int(0.500 * (trials - 1))]
    hi = draws[int(0.975 * (trials - 1))]
    return lo, med, hi


def _print_bootstrap(rows, trials: int, seed: int) -> None:
    if trials <= 0:
        return
    print("\n=== Match-Level Bootstrap Delta vs Current ===")
    print("Fixed transforms only; this is uncertainty, not permission to pick the in-sample winner.")
    print(f"{'strategy':<13} {'point':>9} {'ci_2.5':>9} {'median':>9} {'ci_97.5':>9}")
    for name, strategy in (
        *[(name, strategy) for name, strategy in fixed_policy_specs() if name != "current"],
    ):
        point = evaluate(rows, strategy)["delta"]
        lo, med, hi = _bootstrap_delta(rows, strategy, trials, seed)
        print(f"{name:<13} {_fmt(point):>9} {_fmt(lo):>9} {_fmt(med):>9} {_fmt(hi):>9}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", default=str(ROOT / "docs" / "history.md"))
    ap.add_argument("--min-train", type=int, default=120)
    ap.add_argument("--bootstrap", type=int, default=1000,
                    help="match-level bootstrap trials for fixed transforms; 0 disables")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sports-predict", action="store_true",
                    help="GET-only API read to settle V1 history rows from /results")
    args = ap.parse_args()

    rows = load_history(args.history)
    if args.sports_predict:
        rows = settle_v1_rows(rows, _read_only_outcomes_from_sports_predict())
    exact = exact_rows(rows)
    print(f"parsed={len(rows)} exact_settled={len(exact)} matches={len(set(r.match for r in exact))}")
    print(f"unsettled_or_no_official={len(rows) - len(exact)}")
    _print_strategy_table(rows)
    _print_family_table(rows)
    _print_calibration(rows)
    _print_deviation_table(rows)
    _print_walk_forward(rows, args.min_train)
    _print_chronological_holdout(rows, args.min_train)
    _print_bootstrap(rows, args.bootstrap, args.seed)


if __name__ == "__main__":
    main()
