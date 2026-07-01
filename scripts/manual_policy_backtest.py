"""End-to-end manual policy backtest.

This read-only diagnostic applies the same family-specific manual-entry policy
used by `scripts/full_manual_pricing.py` to every settled market in
`docs/history.md`.

It answers the practical question: after the separate goal/count/player/special
analyses, what is the combined RBP effect of the actual operational policy?

With `--sports-predict`, SportsPredict is used only for GET `/events`,
`/lobbies`, and `/results` to settle older V1 history rows. The script never
reads `/predictions` and never submits.

Usage:
    uv run --with requests python scripts/manual_policy_backtest.py --sports-predict --write
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
    official_alt_rbp,
    official_delta_rbp,
    settle_v1_rows,
    shrink_to_crowd,
    unique_outcomes_by_question,
)
from manual.anchors import sp_get, sports_predict_key  # noqa: E402
from manual.policy import is_count_market, is_odds_goal_market, is_special_market, manual_decision  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


OUT = ROOT / "data" / "logs" / "manual_policy_backtest.md"
HISTORY = ROOT / "docs" / "history.md"
GLOBAL_ASYM = asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.12)


def _read_only_outcomes_from_sports_predict() -> dict[str, int]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    results = sp_get(f"/results?lobby_id={lobby['id']}", key)
    return unique_outcomes_by_question(results)


def load_rows(*, sports_predict: bool) -> list[HistoryMarket]:
    rows = load_history(HISTORY)
    if sports_predict:
        rows = settle_v1_rows(rows, _read_only_outcomes_from_sports_predict())
    return exact_rows(rows)


def _spec(row: HistoryMarket):
    return parse(row.question, teams=ALL_TEAM_NAMES)


def category(row: HistoryMarket) -> str:
    try:
        spec = _spec(row)
    except Exception:
        return "unparsed"
    if spec.player:
        return "player"
    if is_odds_goal_market(spec):
        return "odds_goal"
    if is_special_market(spec):
        return "special"
    if is_count_market(spec):
        return f"count_{spec.stat or 'unknown'}"
    if spec.family in {"penalty_awarded", "penalty_or_red", "red_card"}:
        return "penalty_red"
    return "other"


def current_prob(row: HistoryMarket) -> float:
    return row.you


def field_prob(row: HistoryMarket) -> float:
    return row.crowd


def global_asym_prob(row: HistoryMarket) -> float:
    return GLOBAL_ASYM(row)


def combined_policy(row: HistoryMarket) -> tuple[float, str]:
    spec = _spec(row)
    decision = manual_decision(spec, row.you, field_probability=row.crowd)
    return decision.probability / 100.0, decision.policy


def combined_prob(row: HistoryMarket) -> float:
    return combined_policy(row)[0]


def combined_player_field_prob(row: HistoryMarket) -> float:
    spec = _spec(row)
    if spec.player:
        return row.crowd
    return combined_prob(row)


def score(rows: list[HistoryMarket], strategy) -> dict[str, float]:
    if not rows:
        return {"n": 0, "official_rbp": 0.0, "delta": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    preds = [clip_prob(strategy(r)) for r in rows]
    return {
        "n": len(rows),
        "official_rbp": sum(official_alt_rbp(r, p) for r, p in zip(rows, preds)),
        "delta": sum(official_delta_rbp(r, p) for r, p in zip(rows, preds)),
        "brier": sum(brier(p, r.outcome or 0) for r, p in zip(rows, preds)) / len(rows),
        "mean_p": sum(preds) / len(preds),
    }


def policy_counts(rows: list[HistoryMarket]) -> Counter:
    counts = Counter()
    for row in rows:
        try:
            _p, name = combined_policy(row)
        except Exception:
            name = "unparsed_current"
        counts[name] += 1
    return counts


def fixed_policy_table(rows: list[HistoryMarket]) -> list[dict]:
    specs = [
        ("current", current_prob),
        ("field", field_prob),
        ("shrink50", shrink_to_crowd(0.50)),
        ("global_asym075_cap12", global_asym_prob),
        ("combined_player_field", combined_player_field_prob),
        ("combined_manual_policy", combined_prob),
    ]
    return sorted(
        [{"policy": name, **score(rows, strategy)} for name, strategy in specs],
        key=lambda r: r["delta"],
        reverse=True,
    )


def category_table(rows: list[HistoryMarket]) -> list[dict]:
    grouped: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        grouped[category(row)].append(row)
    out = []
    for cat, part in grouped.items():
        cur = score(part, current_prob)
        combined = score(part, combined_prob)
        field = score(part, field_prob)
        out.append(
            {
                "category": cat,
                "n": len(part),
                "current_brier": cur["brier"],
                "field_delta": field["delta"],
                "combined_delta": combined["delta"],
                "combined_brier": combined["brier"],
                "model": sum(r.you for r in part) / len(part),
                "field": sum(r.crowd for r in part) / len(part),
                "hit": sum(r.outcome or 0 for r in part) / len(part),
            }
        )
    return sorted(out, key=lambda r: r["combined_delta"], reverse=True)


def chronological_holdout(rows: list[HistoryMarket], min_train: int) -> list[dict]:
    orders = sorted({r.order for r in rows})
    holdout = []
    for order in orders:
        if sum(1 for r in rows if r.order < order) >= min_train:
            holdout.extend([r for r in rows if r.order == order])
    return fixed_policy_table(holdout)


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


def md_table(headers: list[str], rows: list[list]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def render(rows: list[HistoryMarket], *, min_train: int, trials: int, seed: int) -> str:
    fixed = fixed_policy_table(rows)
    holdout = chronological_holdout(rows, min_train)
    counts = policy_counts(rows)
    lines = [
        "# Manual Policy Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- settled_rows: {len(rows)}",
        f"- matches: {len({r.match for r in rows})}",
        "- scoring: exact official-RBP deltas from displayed market RBP and realized Brier",
        "",
        "## Combined Policy",
        "",
    ]
    lines += md_table(
        ["policy", "n", "official_rbp", "delta_vs_current", "brier", "mean_p"],
        [
            [
                r["policy"],
                r["n"],
                f"{r['official_rbp']:.2f}",
                f"{r['delta']:.2f}",
                f"{r['brier']:.4f}",
                f"{r['mean_p']:.3f}",
            ]
            for r in fixed
        ],
    )
    lines += [
        "",
        "## Policy Usage",
        "",
    ]
    lines += md_table(
        ["policy", "rows"],
        [[name, count] for name, count in counts.most_common()],
    )
    lines += ["", "## Category Contribution", ""]
    lines += md_table(
        ["category", "n", "field_delta", "combined_delta", "current_brier", "combined_brier", "model", "field", "hit"],
        [
            [
                r["category"],
                r["n"],
                f"{r['field_delta']:.2f}",
                f"{r['combined_delta']:.2f}",
                f"{r['current_brier']:.4f}",
                f"{r['combined_brier']:.4f}",
                f"{r['model']:.3f}",
                f"{r['field']:.3f}",
                f"{r['hit']:.3f}",
            ]
            for r in category_table(rows)
        ],
    )
    lines += [
        "",
        "## Chronological Holdout",
        "",
        f"Fixed policies scored only after at least {min_train} prior settled rows.",
        "",
    ]
    lines += md_table(
        ["policy", "n", "official_rbp", "delta_vs_current", "brier", "mean_p"],
        [
            [
                r["policy"],
                r["n"],
                f"{r['official_rbp']:.2f}",
                f"{r['delta']:.2f}",
                f"{r['brier']:.4f}",
                f"{r['mean_p']:.3f}",
            ]
            for r in holdout
        ],
    )
    if trials > 0:
        lo, med, hi = bootstrap(rows, combined_prob, trials=trials, seed=seed)
        lines += [
            "",
            "## Match-Level Bootstrap",
            "",
            f"- trials: {trials}",
            f"- seed: {seed}",
            f"- combined_policy_delta_point: {score(rows, combined_prob)['delta']:.2f}",
            f"- ci_2.5: {lo:.2f}",
            f"- median: {med:.2f}",
            f"- ci_97.5: {hi:.2f}",
            "",
        ]
    lines += [
        "## Interpretation",
        "",
        "- This report validates the operational policy used by `full_manual_pricing.py` when field averages are supplied.",
        "- It assumes historical player props had no unresolved research flags; live unresolved player rows need exact prop, lineup, or structured player research.",
        "- The combined policy is not a substitute for fresh odds/lineup/referee checks; field-dependent branches are historical/diagnostic unless field values are actually visible.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--min-train", type=int, default=120)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=41)
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
