"""Tail-risk diagnostic for leaderboard catch-up strategies.

This is not a submission workflow.  It tests whether variance-seeking
probability transforms are supported by settled SportsPredict history when the
objective is a prize threshold rather than mean RBP.

With `--sports-predict`, SportsPredict is used only for read-only GET
`/events`, `/lobbies`, `/results`, `/matches`, and `/markets`.  The script never
reads `/predictions` and never submits.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import random
import sys
from typing import Callable

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import (  # noqa: E402
    HistoryMarket,
    asymmetric_field_policy,
    brier,
    clip_prob,
    exact_rows,
    load_history,
    official_delta_rbp,
    settle_v1_rows,
    unique_outcomes_by_question,
)
from manual.anchors import fetch_sports_predict_open_markets, sp_get, sports_predict_key  # noqa: E402
from manual.policy import is_count_market, is_odds_goal_market, is_special_market, manual_decision  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


OUT = ROOT / "data" / "logs" / "leaderboard_tail_risk.md"
HISTORY = ROOT / "docs" / "history.md"
DEFAULT_TARGETS = (728.16, 871.09)

Strategy = Callable[[HistoryMarket], float]


@dataclass(frozen=True)
class StrategySpec:
    name: str
    strategy: Strategy


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


def market_category_from_question(question: str) -> str:
    spec = parse(question, teams=ALL_TEAM_NAMES)
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


def row_category(row: HistoryMarket) -> str:
    try:
        return market_category_from_question(row.question)
    except Exception:
        return "other"


def open_market_mix() -> Counter:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --open-mix")
    _matches, markets = fetch_sports_predict_open_markets(key)
    counts = Counter()
    for market in markets:
        try:
            counts[market_category_from_question(market["question"])] += 1
        except Exception:
            counts["other"] += 1
    return counts


def open_special_questions() -> list[str]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --special-proxy statsbomb")
    _matches, markets = fetch_sports_predict_open_markets(key)
    questions = []
    for market in markets:
        try:
            spec = parse(market["question"], teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if is_special_market(spec):
            questions.append(market["question"])
    return questions


def fallback_mix(rows: list[HistoryMarket], sample_size: int) -> Counter:
    observed = Counter(row_category(row) for row in rows)
    total = sum(observed.values())
    if total <= 0:
        raise ValueError("no rows available for fallback mix")
    raw = {category: sample_size * n / total for category, n in observed.items()}
    counts = Counter({category: int(value) for category, value in raw.items()})
    remainder = sample_size - sum(counts.values())
    fractions = sorted(((value - int(value), category) for category, value in raw.items()), reverse=True)
    for _frac, category in fractions[:remainder]:
        counts[category] += 1
    return counts


def combined_manual(row: HistoryMarket) -> float:
    spec = parse(row.question, teams=ALL_TEAM_NAMES)
    return manual_decision(spec, row.you, field_probability=row.crowd).probability / 100.0


def combined_raw_special(row: HistoryMarket) -> float:
    spec = parse(row.question, teams=ALL_TEAM_NAMES)
    if is_special_market(spec):
        return row.you
    return manual_decision(spec, row.you, field_probability=row.crowd).probability / 100.0


def combined_player_field(row: HistoryMarket) -> float:
    spec = parse(row.question, teams=ALL_TEAM_NAMES)
    if spec.player:
        return row.crowd
    return manual_decision(spec, row.you, field_probability=row.crowd).probability / 100.0


def combined_count_field(row: HistoryMarket) -> float:
    spec = parse(row.question, teams=ALL_TEAM_NAMES)
    if is_count_market(spec) and not is_special_market(spec):
        return row.crowd
    return manual_decision(spec, row.you, field_probability=row.crowd).probability / 100.0


def combined_goal_field(row: HistoryMarket) -> float:
    spec = parse(row.question, teams=ALL_TEAM_NAMES)
    if is_odds_goal_market(spec):
        return row.crowd
    return manual_decision(spec, row.you, field_probability=row.crowd).probability / 100.0


def is_penalty_red_market(spec) -> bool:
    return spec.family in {"penalty_awarded", "penalty_or_red", "red_card"}


def combined_penalty_red_field(row: HistoryMarket) -> float:
    spec = parse(row.question, teams=ALL_TEAM_NAMES)
    if is_penalty_red_market(spec):
        return row.crowd
    return manual_decision(spec, row.you, field_probability=row.crowd).probability / 100.0


def expand_from_field(k: float) -> Strategy:
    def _strategy(row: HistoryMarket) -> float:
        return clip_prob(row.crowd + k * (row.you - row.crowd))

    return _strategy


def hard_model_side(row: HistoryMarket) -> float:
    return 0.99 if row.you >= 0.50 else 0.01


def hard_field_deviation_side(row: HistoryMarket) -> float:
    if row.you == row.crowd:
        return 0.99 if row.you >= 0.50 else 0.01
    return 0.99 if row.you > row.crowd else 0.01


def strategy_specs() -> list[StrategySpec]:
    return [
        StrategySpec("current", lambda row: row.you),
        StrategySpec("field", lambda row: row.crowd),
        StrategySpec("combined_manual_policy", combined_manual),
        StrategySpec("combined_player_field", combined_player_field),
        StrategySpec("combined_count_field", combined_count_field),
        StrategySpec("combined_goal_field", combined_goal_field),
        StrategySpec("combined_penalty_red_field", combined_penalty_red_field),
        StrategySpec("combined_raw_special", combined_raw_special),
        StrategySpec("global_asym075_cap12", asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.12)),
        StrategySpec("expand125", expand_from_field(1.25)),
        StrategySpec("expand150", expand_from_field(1.50)),
        StrategySpec("hard_model_side_99_1", hard_model_side),
        StrategySpec("hard_field_deviation_99_1", hard_field_deviation_side),
    ]


def special_outcome(row: dict, question: str) -> bool:
    spec = parse(question, teams=ALL_TEAM_NAMES)
    if spec.family == "goal_before_hydration":
        return bool(row["goal_before_30"])
    if spec.family == "goal_after_first_hydration_1h":
        return bool(row["goal_after_30_1h"])
    if spec.family == "goal_after_second_hydration":
        return bool(row["goal_after_75"])
    if spec.family == "goal_in_stoppage":
        return bool(row["goal_1h_stoppage" if spec.period == "1h" else "goal_2h_stoppage"])
    if spec.family == "offside_before_hydration":
        return bool(row["offside_before_30"])
    if spec.family == "card_after_second_hydration":
        return bool(row["card_after_75"])
    if spec.family == "card_in_stoppage_any_half":
        return bool(row["card_stoppage"])
    if spec.family == "first_card_before_first_goal":
        return bool(row["first_card_before_first_goal"])
    if spec.family == "substitute_scores":
        return bool(row["substitute_goal"])
    if spec.family == "own_goal":
        return bool(row["own_goal"])
    if spec.family == "outside_box_goal":
        return bool(row["outside_box_goal"])
    if spec.family == "header_goal":
        return bool(row["header_goal"])
    if spec.family == "any_player_goals_ge":
        if int(spec.threshold or 0) <= 1:
            return bool(row["any_player_goal"])
        return bool(row["any_player_brace"])
    if spec.family == "any_player_sot_ge":
        return bool(row["any_player_2_sot"])
    if spec.family == "substitution_before_halftime":
        return bool(row["substitution_before_halftime"])
    if spec.family == "total_count_ge" and spec.stat == "shots":
        return int(row["shots"]) >= int(spec.threshold or 0)
    if spec.family == "total_count_ge" and spec.stat == "substitutions":
        return int(row["substitutions"]) >= int(spec.threshold or 0)
    if spec.family == "total_count_before_hydration" and spec.stat == "corners":
        return int(row["corners_before_30"]) >= int(spec.threshold or 0)
    raise ValueError(f"unsupported special proxy question: {question}")


def special_loto_probabilities(data: list[dict], question: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for idx, row in enumerate(data):
        train = [candidate for candidate in data if candidate["tournament"] != row["tournament"]]
        if not train:
            out[idx] = 0.5
        else:
            out[idx] = sum(special_outcome(candidate, question) for candidate in train) / len(train)
    return out


def _moment_alpha(values: list[float]) -> float:
    arr = np.asarray(values, dtype=float)
    mu = float(arr.mean()) if len(arr) else 0.0
    if mu <= 0:
        return 0.0
    var = float(arr.var(ddof=1)) if len(arr) > 1 else mu
    return max(0.0, (var - mu) / (mu * mu))


def _nb_ge(mean: float, alpha: float, threshold: int) -> float:
    if threshold <= 0:
        return 1.0
    if mean <= 0:
        return 0.0
    if alpha < 1e-6:
        return float(stats.poisson.sf(threshold - 1, mean))
    size = 1.0 / alpha
    p = size / (size + mean)
    return float(stats.nbinom.sf(threshold - 1, size, p))


def special_proxy_probability(train: list[dict], question: str) -> float:
    """LOTO model probability for a StatsBomb special proxy row.

    Most special families use the exact-question empirical LOTO rate. Count
    threshold specials use the same negative-binomial family as the live
    `SpecialPricer`, fitted only on the held-in tournaments.
    """

    if not train:
        return 0.5
    spec = parse(question, teams=ALL_TEAM_NAMES)
    if spec.family == "total_count_ge" and spec.stat == "shots":
        values = [float(row["shots"]) for row in train]
        return _nb_ge(sum(values) / len(values), _moment_alpha(values), int(spec.threshold or 0))
    if spec.family == "total_count_before_hydration" and spec.stat == "corners":
        values = [float(row["corners_before_30"]) for row in train]
        return _nb_ge(sum(values) / len(values), _moment_alpha(values), int(spec.threshold or 0))
    return sum(special_outcome(candidate, question) for candidate in train) / len(train)


def special_loto_model_probabilities(data: list[dict], question: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for idx, row in enumerate(data):
        train = [candidate for candidate in data if candidate["tournament"] != row["tournament"]]
        out[idx] = special_proxy_probability(train, question)
    return out


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = z * ((phat * (1 - phat) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _special_confidence(edge_vs_50: float, low: float, high: float) -> str:
    if edge_vs_50 < 0.005:
        return "weak_near_coinflip"
    if low <= 0.5 <= high:
        return "moderate_rate_uncertain"
    return "usable_base_prior"


def special_question_diagnostics(data: list[dict], questions: list[str]) -> list[dict]:
    """Question-level LOTO diagnostics for current StatsBomb special proxies."""

    diagnostics = []
    for question, open_markets in sorted(Counter(questions).items()):
        probabilities = special_loto_model_probabilities(data, question)
        scored = [
            (clip_prob(probabilities[idx]), int(special_outcome(row, question)))
            for idx, row in enumerate(data)
        ]
        if not scored:
            continue
        successes = sum(outcome for _probability, outcome in scored)
        n = len(scored)
        brier_model = sum(brier(probability, outcome) for probability, outcome in scored) / n
        brier_50 = sum(brier(0.5, outcome) for _probability, outcome in scored) / n
        low, high = _wilson_interval(successes, n)
        edge = brier_50 - brier_model
        spec = parse(question, teams=ALL_TEAM_NAMES)
        diagnostics.append(
            {
                "question": question,
                "open_markets": open_markets,
                "family": spec.family if not spec.stat else f"{spec.family}/{spec.stat}",
                "n": n,
                "successes": successes,
                "hit": successes / n,
                "brier_model": brier_model,
                "brier_50": brier_50,
                "edge_vs_50": edge,
                "wilson_low": low,
                "wilson_high": high,
                "confidence": _special_confidence(edge, low, high),
            }
        )
    return sorted(diagnostics, key=lambda row: (row["confidence"], row["family"], row["question"]))


def special_confidence_counts(diagnostics: list[dict]) -> Counter:
    counts = Counter()
    for row in diagnostics:
        counts[str(row["confidence"])] += int(row.get("open_markets", 1))
    return counts


def build_statsbomb_special_proxy_rows(questions: list[str], data: list[dict] | None = None) -> list[HistoryMarket]:
    """Create exact-scoring proxy rows for current special markets.

    Each row treats 50% as the neutral crowd baseline and a leave-one-
    tournament-out StatsBomb probability as the independent model. Count
    threshold specials use a leave-one-tournament-out negative-binomial count
    proxy; other special families use exact-question empirical rates. This is
    not official SportsPredict RBP, but it is a more category-honest tail proxy
    than sampling unrelated settled market families.
    """

    from special_market_backtest import rows as statsbomb_rows

    data = data if data is not None else statsbomb_rows()
    if not data or not questions:
        return []

    proxy_rows: list[HistoryMarket] = []
    order = 10_000
    for question_idx, question in enumerate(questions):
        spec = parse(question, teams=ALL_TEAM_NAMES)
        probabilities = special_loto_model_probabilities(data, question)
        for row_idx, row in enumerate(data):
            outcome = int(special_outcome(row, question))
            p = clip_prob(probabilities[row_idx])
            neutral = 0.5
            proxy_rows.append(
                HistoryMarket(
                    version=99,
                    order=order,
                    match=f"statsbomb_special_proxy_{question_idx}_{row['tournament']}_{row_idx}",
                    question=question,
                    you=p,
                    crowd=neutral,
                    outcome=outcome,
                    official_rbp=100.0 * (brier(neutral, outcome) - brier(p, outcome)),
                    family=spec.family,
                    stat=spec.stat or "",
                    period=spec.period,
                )
            )
            order += 1
    return proxy_rows


def alt_rbp(row: HistoryMarket, probability: float, *, stage_weight: float) -> float:
    if row.official_rbp is None or row.outcome is None:
        raise ValueError("exact settled row required")
    base_alt = row.official_rbp + official_delta_rbp(row, probability)
    return stage_weight * base_alt


def score_rows(rows: list[HistoryMarket], strategy: Strategy, *, stage_weight: float) -> float:
    return sum(alt_rbp(row, clip_prob(strategy(row)), stage_weight=stage_weight) for row in rows)


def sample_rows_by_mix(
    by_category: dict[str, list[HistoryMarket]],
    mix: Counter,
    *,
    rng: random.Random,
) -> tuple[list[HistoryMarket], Counter]:
    rows: list[HistoryMarket] = []
    fallback = [row for part in by_category.values() for row in part]
    fallback_used = Counter()
    for category, n in sorted(mix.items()):
        pool = by_category.get(category) or fallback
        if category not in by_category:
            fallback_used[category] += n
        for _ in range(n):
            rows.append(rng.choice(pool))
    return rows, fallback_used


def quantile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    idx = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return values[idx]


def simulate(
    rows: list[HistoryMarket],
    mix: Counter,
    strategies: list[StrategySpec],
    *,
    trials: int,
    seed: int,
    stage_weight: float,
    targets: tuple[float, ...],
    special_proxy_rows: list[HistoryMarket] | None = None,
) -> tuple[list[dict], Counter]:
    by_category: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        by_category[row_category(row)].append(row)
    if special_proxy_rows:
        by_category["special"] = list(special_proxy_rows)
        score_rows_source = [*rows, *special_proxy_rows]
    else:
        score_rows_source = rows
    score_lookup = {
        spec.name: {
            row: alt_rbp(row, clip_prob(spec.strategy(row)), stage_weight=stage_weight)
            for row in score_rows_source
        }
        for spec in strategies
    }

    rng = random.Random(seed)
    totals = {spec.name: [] for spec in strategies}
    aggregate_fallback = Counter()
    for _ in range(trials):
        slate, fallback_used = sample_rows_by_mix(by_category, mix, rng=rng)
        aggregate_fallback.update(fallback_used)
        for spec in strategies:
            totals[spec.name].append(sum(score_lookup[spec.name][row] for row in slate))

    out = []
    for spec in strategies:
        values = totals[spec.name]
        row = {
            "strategy": spec.name,
            "mean": sum(values) / len(values),
            "sd": _std(values),
            "p05": quantile(values, 0.05),
            "p50": quantile(values, 0.50),
            "p95": quantile(values, 0.95),
            "p99": quantile(values, 0.99),
        }
        for target in targets:
            row[f"pr_ge_{target:.2f}"] = sum(value >= target for value in values) / len(values)
        out.append(row)
    out.sort(key=lambda row: row["mean"], reverse=True)
    return out, aggregate_fallback


def _std(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


def md_table(headers: list[str], rows: list[list]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def render(
    *,
    rows: list[HistoryMarket],
    mix: Counter,
    sim_rows: list[dict],
    fallback_used: Counter,
    targets: tuple[float, ...],
    trials: int,
    seed: int,
    stage_weight: float,
    mix_source: str,
    special_proxy: str,
    special_proxy_rows: int,
    special_diagnostics: list[dict] | None = None,
) -> str:
    run_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    target_headers = [f"Pr >= {target:.2f}" for target in targets]
    caveat = (
        "- caveat: current special markets are scored with leave-one-tournament-out StatsBomb proxy rows, not official SportsPredict special-market history"
        if special_proxy == "statsbomb"
        else "- caveat: unsupported current-open categories use the full safe-history fallback pool and are reported below"
    )
    table_rows = []
    for row in sim_rows:
        table_rows.append(
            [
                row["strategy"],
                f"{row['mean']:.2f}",
                f"{row['sd']:.2f}",
                f"{row['p05']:.2f}",
                f"{row['p50']:.2f}",
                f"{row['p95']:.2f}",
                f"{row['p99']:.2f}",
                *[f"{100 * row[f'pr_ge_{target:.2f}']:.2f}%" for target in targets],
            ]
        )

    lines = [
        "# Leaderboard Tail-Risk Diagnostic",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {run_utc}",
        f"- safe_history_rows: {len(rows)}",
        f"- mix_source: {mix_source}",
        f"- special_proxy: {special_proxy}",
        f"- special_proxy_rows: {special_proxy_rows}",
        *(
            ["- special_proxy_model: LOTO empirical for timing/player-aggregate specials; LOTO negative-binomial for shots and early-corner count thresholds"]
            if special_proxy == "statsbomb"
            else []
        ),
        f"- simulated_markets_per_trial: {sum(mix.values())}",
        f"- trials: {trials}",
        f"- seed: {seed}",
        f"- stage_weight_multiplier: {stage_weight:.2f}",
        "- scoring: empirical official RBP replay from settled SportsPredict rows; stage multiplier scales each sampled historical row",
        caveat,
        "",
        "## Market Mix",
        "",
        *md_table(["category", "markets"], [[category, count] for category, count in sorted(mix.items())]),
        "",
    ]
    if special_diagnostics:
        confidence_counts = special_confidence_counts(special_diagnostics)
        lines.extend(
            [
                "## Special Proxy Diagnostics",
                "",
                "Question-level leave-one-tournament-out diagnostics for the current open special markets. `weak_near_coinflip` rows should be treated as noisy priors, not validated edge.",
                "",
                *md_table(
                    ["confidence", "questions"],
                    [[confidence, count] for confidence, count in sorted(confidence_counts.items())],
                ),
                "",
                "| confidence | open_markets | family | n | hit_rate | wilson_95 | brier_model | brier_50 | edge_vs_50 | question |",
                "| --- | ---: | --- | ---: | ---: | --- | ---: | ---: | ---: | --- |",
            ]
        )
        for row in special_diagnostics:
            lines.append(
                f"| {row['confidence']} | {row['open_markets']} | {row['family']} | {row['n']} | {row['hit']:.3f} | "
                f"{row['wilson_low']:.3f}-{row['wilson_high']:.3f} | "
                f"{row['brier_model']:.4f} | {row['brier_50']:.4f} | {row['edge_vs_50']:.4f} | "
                f"{row['question']} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Strategy Tail Distribution",
            "",
            *md_table(
                ["strategy", "mean", "sd", "p05", "p50", "p95", "p99", *target_headers],
                table_rows,
            ),
            "",
            "## Fallback Categories",
            "",
        ]
    )
    if fallback_used:
        per_trial = [[category, count, f"{count / trials:.2f}"] for category, count in sorted(fallback_used.items())]
        lines.extend(md_table(["category", "sampled_rows_total", "mean_rows_per_trial"], per_trial))
    else:
        lines.append("_None._")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- The calibrated/manual policies should lead on mean RBP if the probability estimates are directionally sound.",
            "- A variance-seeking policy would need materially higher target-hit probability to justify its lower expected score.",
            "- If hard-extreme strategies do not improve the target tail in this replay, the catch-up plan should remain calibrated edge capture: lineups, exact player-prop anchors, current sources, and odds refreshes.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="settle V1 rows and fetch open mix through read-only GET calls")
    ap.add_argument("--open-mix", action="store_true", help="use current open SportsPredict market mix instead of history-proportional mix")
    ap.add_argument(
        "--special-proxy",
        choices=["history", "statsbomb"],
        default="history",
        help="how to sample current special markets in tail simulations",
    )
    ap.add_argument("--sample-size", type=int, default=135, help="history-proportional simulated slate size when --open-mix is not used")
    ap.add_argument("--trials", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260628)
    ap.add_argument("--stage-weight", type=float, default=2.0)
    ap.add_argument("--target", type=float, action="append", help="RBP target to estimate; repeatable")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_rows(sports_predict=args.sports_predict)
    if args.open_mix:
        if not args.sports_predict:
            raise SystemExit("--open-mix requires --sports-predict for read-only market discovery")
        mix = open_market_mix()
        mix_source = "current_open_sports_predict_markets"
    else:
        mix = fallback_mix(rows, args.sample_size)
        mix_source = "history_proportional"
    special_proxy_rows: list[HistoryMarket] = []
    special_diagnostics: list[dict] = []
    if args.special_proxy == "statsbomb":
        if not args.sports_predict:
            raise SystemExit("--special-proxy statsbomb requires --sports-predict to read current special questions")
        from special_market_backtest import rows as statsbomb_rows

        special_questions = open_special_questions()
        statsbomb_data = statsbomb_rows()
        special_proxy_rows = build_statsbomb_special_proxy_rows(special_questions, data=statsbomb_data)
        special_diagnostics = special_question_diagnostics(statsbomb_data, special_questions)
    targets = tuple(args.target or DEFAULT_TARGETS)
    sim_rows, fallback_used = simulate(
        rows,
        mix,
        strategy_specs(),
        trials=args.trials,
        seed=args.seed,
        stage_weight=args.stage_weight,
        targets=targets,
        special_proxy_rows=special_proxy_rows,
    )
    text = render(
        rows=rows,
        mix=mix,
        sim_rows=sim_rows,
        fallback_used=fallback_used,
        targets=targets,
        trials=args.trials,
        seed=args.seed,
        stage_weight=args.stage_weight,
        mix_source=mix_source,
        special_proxy=args.special_proxy,
        special_proxy_rows=len(special_proxy_rows),
        special_diagnostics=special_diagnostics,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
