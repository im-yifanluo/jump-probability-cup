"""Validate special knockout-market baselines on StatsBomb tournament data.

This is not a bot workflow and does not touch SportsPredict.  It checks the
new manual-pricing families that are not covered by the scoreline/count/player
backtests: hydration timing, stoppage-time goals, substitute goals, any-player
brace/SOT, and early substitutions.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from math import exp, log
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "data" / "logs" / "special_market_backtest.md"


def _shot_from_outside_penalty_area(event: dict) -> bool:
    loc = event.get("location") or []
    if len(loc) < 2:
        return True
    x, y = float(loc[0]), float(loc[1])
    return not (x >= 102 and 18 <= y <= 62)


SCALING_VOLUME = {
    "goal_before_30": "goals",
    "goal_after_75": "goals",
    "goal_1h_stoppage": "goals",
    "goal_2h_stoppage": "goals",
    "substitute_goal": "goals",
    "own_goal": "goals",
    "outside_box_goal": "goals",
    "header_goal": "goals",
    "any_player_goal": "goals",
    "any_player_brace": "goals",
    "offside_before_30": "offsides",
    "card_after_75": "cards",
    "any_player_2_sot": "sot",
}

SPECIAL_FIELDS = [
    "goal_before_30",
    "goal_after_75",
    "goal_1h_stoppage",
    "goal_2h_stoppage",
    "offside_before_30",
    "card_after_75",
    "substitution_before_halftime",
    "substitute_goal",
    "own_goal",
    "outside_box_goal",
    "header_goal",
    "any_player_goal",
    "any_player_brace",
    "any_player_2_sot",
]

COUNT_THRESHOLD_PRIORS = [
    ("total_shots_ge_20", "shots", 20),
    ("total_shots_ge_22", "shots", 22),
    ("early_corners_ge_2", "corners_before_30", 2),
]

ADAPTATION_STRENGTHS = (8, 16, 32, 64, 128)


def event_files() -> list[Path]:
    roots = [
        ROOT / "data" / "raw" / "statsbomb",
        ROOT / "data_reference" / "raw" / "statsbomb",
    ]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.glob("*/events/*.json")))
    return files


def match_metadata() -> dict[tuple[str, int], dict]:
    out = {}
    for root in [
        ROOT / "data" / "raw" / "statsbomb",
        ROOT / "data_reference" / "raw" / "statsbomb",
    ]:
        if not root.exists():
            continue
        for path in sorted(root.glob("*/matches.json")):
            tournament = path.parent.name
            for match in json.loads(path.read_text()):
                out[(tournament, int(match["match_id"]))] = match
    return out


def rows() -> list[dict]:
    out = []
    metadata = match_metadata()
    for path in event_files():
        events = json.loads(path.read_text())
        tournament = path.parent.parent.name
        match_id = int(path.stem)
        meta = metadata.get((tournament, match_id), {})
        stage = (meta.get("competition_stage") or {}).get("name", "unknown")
        starters: set[str] = set()
        subs_in: set[str] = set()
        subs = []
        goals = []
        own_goals = []
        outside_box_goals = []
        header_goals = []
        offsides = []
        cards = []
        player_goals: dict[str, int] = {}
        player_sot: dict[str, int] = {}

        for event in events:
            typ = event.get("type", {}).get("name")
            if typ == "Starting XI":
                for slot in event.get("tactics", {}).get("lineup", []):
                    starters.add(slot["player"]["name"])
            elif typ == "Substitution":
                repl = event.get("substitution", {}).get("replacement", {}).get("name")
                if repl:
                    subs_in.add(repl)
                subs.append(event.get("minute", 0) + event.get("second", 0) / 60)

        shots = sot = corners = corners_before_30 = 0
        for event in events:
            typ = event.get("type", {}).get("name")
            period = event.get("period")
            if period not in (1, 2):
                continue
            minute = event.get("minute", 0) + event.get("second", 0) / 60
            player = event.get("player", {}).get("name")

            if typ == "Shot":
                shots += 1
                outcome = event.get("shot", {}).get("outcome", {}).get("name")
                if outcome in {"Goal", "Saved", "Saved To Post"}:
                    sot += 1
                    if player:
                        player_sot[player] = player_sot.get(player, 0) + 1
                if outcome == "Goal":
                    is_sub = bool(player and player in subs_in and player not in starters)
                    goals.append((minute, period, player, is_sub))
                    if _shot_from_outside_penalty_area(event):
                        outside_box_goals.append(minute)
                    if event.get("shot", {}).get("body_part", {}).get("name") == "Head":
                        header_goals.append(minute)
                    if player:
                        player_goals[player] = player_goals.get(player, 0) + 1
            elif typ == "Own Goal For":
                goals.append((minute, period, None, False))
                own_goals.append(minute)
            elif typ == "Pass":
                pass_data = event.get("pass", {})
                if pass_data.get("type", {}).get("name") == "Corner":
                    corners += 1
                    if minute < 30:
                        corners_before_30 += 1
                if pass_data.get("outcome", {}).get("name") == "Pass Offside":
                    offsides.append(minute)
            elif typ == "Offside":
                offsides.append(minute)
            elif typ == "Foul Committed":
                if event.get("foul_committed", {}).get("card", {}).get("name"):
                    cards.append(minute)
            elif typ == "Bad Behaviour":
                if event.get("bad_behaviour", {}).get("card", {}).get("name"):
                    cards.append(minute)

        out.append(
            {
                "tournament": tournament,
                "match_id": match_id,
                "match_date": meta.get("match_date", "9999-12-31"),
                "kick_off": meta.get("kick_off", "99:99:99.999"),
                "stage": stage,
                "is_knockout": stage != "Group Stage",
                "goals": len(goals),
                "shots": shots,
                "sot": sot,
                "offsides": len(offsides),
                "cards": len(cards),
                "corners": corners,
                "corners_before_30": corners_before_30,
                "goal_before_30": any(p == 1 and m < 30 for m, p, _, _ in goals),
                "goal_after_75": any(p == 2 and m >= 75 for m, p, _, _ in goals),
                "goal_1h_stoppage": any(p == 1 and m >= 45 for m, p, _, _ in goals),
                "goal_2h_stoppage": any(p == 2 and m >= 90 for m, p, _, _ in goals),
                "offside_before_30": any(m < 30 for m in offsides),
                "card_after_75": any(m >= 75 for m in cards),
                "substitution_before_halftime": any(m < 45 for m in subs),
                "substitute_goal": any(is_sub for _, _, _, is_sub in goals),
                "own_goal": bool(own_goals),
                "outside_box_goal": bool(outside_box_goals),
                "header_goal": bool(header_goals),
                "any_player_goal": bool(player_goals),
                "any_player_brace": any(count >= 2 for count in player_goals.values()),
                "any_player_2_sot": any(count >= 2 for count in player_sot.values()),
            }
        )
    return out


def brier(p: float, o: bool) -> float:
    return (p - float(o)) ** 2


def calibration_bins(
    scored: list[tuple[float, bool]],
    *,
    edges: list[float] | None = None,
) -> list[dict[str, float | str]]:
    """Reliability bins for probabilistic special-market diagnostics."""

    if edges is None:
        edges = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    if len(edges) < 2 or edges[0] < 0 or edges[-1] > 1 or any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError("edges must be increasing probabilities between 0 and 1")

    groups: list[list[tuple[float, bool]]] = [[] for _ in range(len(edges) - 1)]
    for p, outcome in scored:
        clipped = min(1.0, max(0.0, float(p)))
        for idx, (low, high) in enumerate(zip(edges, edges[1:])):
            if low <= clipped < high or (idx == len(groups) - 1 and clipped <= high):
                groups[idx].append((clipped, bool(outcome)))
                break

    out: list[dict[str, float | str]] = []
    for idx, part in enumerate(groups):
        if not part:
            continue
        low, high = edges[idx], edges[idx + 1]
        label = f"{low:.1f}-{high:.1f}" if idx < len(groups) - 1 else f"{low:.1f}-1.0"
        mean_pred = sum(p for p, _ in part) / len(part)
        hit_rate = sum(outcome for _, outcome in part) / len(part)
        out.append(
            {
                "bin": label,
                "n": len(part),
                "mean_pred": mean_pred,
                "hit_rate": hit_rate,
                "abs_error": abs(hit_rate - mean_pred),
                "brier": sum(brier(p, outcome) for p, outcome in part) / len(part),
            }
        )
    return out


def calibration_summary(scored: list[tuple[float, bool]]) -> dict[str, float]:
    """Aggregate reliability diagnostics from 5 fixed probability bins."""

    if not scored:
        return {"n": 0, "mean_pred": 0.0, "hit_rate": 0.0, "brier": 0.0, "ece": 0.0}
    bins = calibration_bins(scored)
    n = len(scored)
    return {
        "n": n,
        "mean_pred": sum(p for p, _ in scored) / n,
        "hit_rate": sum(outcome for _, outcome in scored) / n,
        "brier": sum(brier(p, outcome) for p, outcome in scored) / n,
        "ece": sum(int(row["n"]) * float(row["abs_error"]) for row in bins) / n,
    }


def heterogeneity_tier(rate_range: float) -> str:
    if rate_range >= 0.25:
        return "high"
    if rate_range >= 0.15:
        return "moderate"
    return "low"


def _stddev(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


def tournament_heterogeneity_rows(rows_: list[dict]) -> list[dict[str, float | str]]:
    """Cross-tournament rate spread for special priors.

    Leave-one-tournament-out Brier can look good even when tournament rates move
    materially. This table is a fragility check for using one base prior in a
    current tournament, not a probability transform.
    """

    specs: list[tuple[str, str, object]] = [(field, "binary", field) for field in SPECIAL_FIELDS]
    specs.extend((key, "count_threshold", (value_field, threshold)) for key, value_field, threshold in COUNT_THRESHOLD_PRIORS)
    by_tournament: dict[str, list[dict]] = defaultdict(list)
    for row in rows_:
        by_tournament[str(row["tournament"])].append(row)

    out: list[dict[str, float | str]] = []
    for key, typ, selector in specs:
        rates: list[float] = []
        counts: list[int] = []
        for tournament, part in sorted(by_tournament.items()):
            if not part:
                continue
            if typ == "binary":
                successes = sum(bool(row[str(selector)]) for row in part)
            else:
                value_field, threshold = selector  # type: ignore[misc]
                successes = sum(float(row[str(value_field)]) >= int(threshold) for row in part)
            rates.append(successes / len(part))
            counts.append(len(part))
        if not rates:
            continue
        rate_range = max(rates) - min(rates)
        out.append(
            {
                "key": key,
                "type": typ,
                "tournaments": len(rates),
                "min_rate": min(rates),
                "max_rate": max(rates),
                "range": rate_range,
                "sd": _stddev(rates),
                "min_matches": min(counts),
                "max_matches": max(counts),
                "heterogeneity": heterogeneity_tier(rate_range),
            }
        )
    return sorted(out, key=lambda row: (str(row["heterogeneity"]), -float(row["range"]), str(row["key"])))


def _base_rate(train: list[dict], field: str) -> float:
    return sum(bool(row[field]) for row in train) / len(train)


def _chronological_batches(rows_: list[dict]) -> list[list[dict]]:
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
        if not batches:
            batches.append([row])
            continue
        last = batches[-1][0]
        last_key = (last.get("match_date"), last.get("kick_off"))
        if key == last_key:
            batches[-1].append(row)
        else:
            batches.append([row])
    return batches


def chronological_adaptation_predictions(
    rows_: list[dict],
    fields: list[str],
    *,
    strength: int,
    knockout_only: bool = False,
) -> list[dict]:
    """Leave-one-tournament-out in-tournament adaptation replay.

    For each held-out tournament, the base probability is estimated from all
    other tournaments. The adaptive probability then blends that base with
    only earlier matches from the held-out tournament:

        p = (strength * p_global + prior_successes) / (strength + prior_n)

    Matches with the same kickoff are scored as one batch before updating the
    prior, so simultaneous games do not leak into each other.
    """

    predictions = []
    tournaments = sorted({row["tournament"] for row in rows_})
    for field in fields:
        for tournament in tournaments:
            train = [row for row in rows_ if row["tournament"] != tournament]
            test = [row for row in rows_ if row["tournament"] == tournament]
            if not train or not test:
                continue
            base = _base_rate(train, field)
            prior_n = 0
            prior_successes = 0
            for batch in _chronological_batches(test):
                for row in batch:
                    if knockout_only and not row.get("is_knockout", False):
                        continue
                    adaptive = (strength * base + prior_successes) / (strength + prior_n)
                    predictions.append(
                        {
                            "tournament": tournament,
                            "field": field,
                            "base": base,
                            "adaptive": adaptive,
                            "outcome": bool(row[field]),
                            "is_knockout": bool(row.get("is_knockout", False)),
                        }
                    )
                prior_n += len(batch)
                prior_successes += sum(bool(row[field]) for row in batch)
    return predictions


def adaptation_score(predictions: list[dict]) -> dict[str, float]:
    if not predictions:
        return {"n": 0, "base_brier": 0.0, "adaptive_brier": 0.0, "delta": 0.0}
    base_brier = sum(brier(row["base"], row["outcome"]) for row in predictions) / len(predictions)
    adaptive_brier = sum(brier(row["adaptive"], row["outcome"]) for row in predictions) / len(predictions)
    return {
        "n": len(predictions),
        "base_brier": base_brier,
        "adaptive_brier": adaptive_brier,
        "delta": adaptive_brier - base_brier,
    }


def adaptation_family_rows(predictions: list[dict]) -> list[dict]:
    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in predictions:
        by_family[row["field"]].append(row)
    out = []
    for field, part in by_family.items():
        out.append({"family": field, **adaptation_score(part)})
    return sorted(out, key=lambda row: row["delta"])


def adaptation_strength_rows(
    rows_: list[dict],
    fields: list[str],
    *,
    knockout_only: bool,
) -> list[dict]:
    out = []
    scope = "knockout_only" if knockout_only else "all_matches"
    for strength in ADAPTATION_STRENGTHS:
        preds = chronological_adaptation_predictions(
            rows_,
            fields,
            strength=strength,
            knockout_only=knockout_only,
        )
        out.append({"scope": scope, "strength": strength, **adaptation_score(preds)})
    return out


def bootstrap_adaptation_delta(
    predictions: list[dict],
    *,
    trials: int = 5000,
    seed: int = 17,
) -> dict[str, float]:
    by_tournament: dict[str, list[dict]] = defaultdict(list)
    for row in predictions:
        by_tournament[row["tournament"]].append(row)
    tournaments = sorted(by_tournament)
    if not tournaments or trials <= 0:
        delta = adaptation_score(predictions)["delta"]
        return {"point": delta, "ci_2.5": delta, "median": delta, "ci_97.5": delta, "pr_lt_0": float(delta < 0)}
    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        sample = []
        for tournament in (rng.choice(tournaments) for _ in tournaments):
            sample.extend(by_tournament[tournament])
        draws.append(adaptation_score(sample)["delta"])
    draws.sort()
    point = adaptation_score(predictions)["delta"]
    return {
        "point": point,
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "median": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_lt_0": sum(1 for value in draws if value < 0) / len(draws),
    }


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = z * ((phat * (1 - phat) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def confidence_tier(edge_vs_50: float, low: float, high: float) -> str:
    if edge_vs_50 < 0.005:
        return "weak_near_coinflip"
    if low <= 0.5 <= high:
        return "moderate_rate_uncertain"
    return "usable_base_prior"


def loto_scored(rows_: list[dict], field: str) -> list[tuple[float, bool]]:
    tournaments = sorted({row["tournament"] for row in rows_})
    scored = []
    for tournament in tournaments:
        train = [row for row in rows_ if row["tournament"] != tournament]
        test = [row for row in rows_ if row["tournament"] == tournament]
        p = sum(bool(row[field]) for row in train) / len(train)
        scored.extend((p, bool(row[field])) for row in test)
    return scored


def loto(rows_: list[dict], field: str) -> dict:
    scored = loto_scored(rows_, field)
    hit = sum(o for _, o in scored) / len(scored)
    br = sum(brier(p, o) for p, o in scored) / len(scored)
    br50 = sum(brier(0.5, o) for _, o in scored) / len(scored)
    successes = sum(o for _, o in scored)
    low, high = wilson_interval(successes, len(scored))
    edge = br50 - br
    return {
        "n": len(scored),
        "successes": successes,
        "hit": hit,
        "wilson_low": low,
        "wilson_high": high,
        "brier_loto": br,
        "brier_50": br50,
        "edge_vs_50": edge,
        "confidence_tier": confidence_tier(edge, low, high),
    }


def loto_threshold(rows_: list[dict], value_field: str, threshold: int) -> dict:
    """Leave-one-tournament-out base prior for special count thresholds."""

    tournaments = sorted({row["tournament"] for row in rows_})
    scored: list[tuple[float, bool]] = []
    for tournament in tournaments:
        train = [row for row in rows_ if row["tournament"] != tournament]
        test = [row for row in rows_ if row["tournament"] == tournament]
        p = sum(float(row[value_field]) >= threshold for row in train) / len(train)
        scored.extend((p, float(row[value_field]) >= threshold) for row in test)
    hit = sum(o for _, o in scored) / len(scored)
    br = sum(brier(p, o) for p, o in scored) / len(scored)
    br50 = sum(brier(0.5, o) for _, o in scored) / len(scored)
    successes = sum(o for _, o in scored)
    low, high = wilson_interval(successes, len(scored))
    edge = br50 - br
    return {
        "n": len(scored),
        "successes": successes,
        "hit": hit,
        "wilson_low": low,
        "wilson_high": high,
        "brier_loto": br,
        "brier_50": br50,
        "edge_vs_50": edge,
        "confidence_tier": confidence_tier(edge, low, high),
    }


def base_rate_uncertainty(rows_: list[dict], fields: list[str]) -> list[dict]:
    return [loto(rows_, field) | {"family": field} for field in fields]


def _scaled_probability(base_rate: float, train_volume: float, test_volume: float) -> float:
    if base_rate <= 0 or train_volume <= 0 or test_volume <= 0:
        return 0.0
    if base_rate >= 1:
        return 1.0
    hazard = -log(1 - base_rate) / train_volume
    return 1 - exp(-hazard * test_volume)


def loto_scaled_volume_scored(rows_: list[dict], field: str, volume_field: str) -> list[tuple[float, float, bool]]:
    """Leave-one-tournament-out diagnostic for SpecialPricer hazard scaling.

    This uses the *realized* total event volume in the held-out match, so it is
    not an ex-ante forecast score. It tests whether the scaling shape is
    directionally valid: conditional on a match having more goals/cards/etc.,
    should the timing/special event probability be higher?
    """

    tournaments = sorted({row["tournament"] for row in rows_})
    scored = []
    for tournament in tournaments:
        train = [row for row in rows_ if row["tournament"] != tournament]
        test = [row for row in rows_ if row["tournament"] == tournament]
        base = sum(bool(row[field]) for row in train) / len(train)
        volume = sum(float(row[volume_field]) for row in train) / len(train)
        for row in test:
            p_constant = base
            p_scaled = _scaled_probability(base, volume, float(row[volume_field]))
            scored.append((p_constant, p_scaled, bool(row[field])))
    return scored


def loto_scaled_volume(rows_: list[dict], field: str, volume_field: str) -> dict:
    scored = loto_scaled_volume_scored(rows_, field, volume_field)
    br_constant = sum(brier(p, o) for p, _, o in scored) / len(scored)
    br_scaled = sum(brier(p, o) for _, p, o in scored) / len(scored)
    return {
        "n": len(scored),
        "brier_constant": br_constant,
        "brier_scaled_oracle_volume": br_scaled,
        "delta_scaled_minus_constant": br_scaled - br_constant,
    }


def ex_ante_volume_replay_status(rows_: list[dict]) -> dict[str, str | int]:
    """Explain whether this cache can support an ex-ante volume replay.

    The raw StatsBomb open-data events contain realized match events, not
    historical pre-match odds or projected goal/count means. Without a
    match-specific pre-match volume feature, hazard scaling has no ex-ante
    variation and reduces algebraically to the constant prior. Keep this status
    in the report so oracle-volume evidence is not mistaken for deployable edge.
    """

    return {
        "status": "not_available",
        "matches": len(rows_),
        "reason": (
            "StatsBomb event cache has realized goals/cards/offsides/SOT but no "
            "historical pre-match odds or projected count means; a no-match-info "
            "proxy equals the training mean and gives the same probability as the "
            "constant leave-one-tournament-out prior."
        ),
    }


def report() -> str:
    from models.specials import load_special_baselines

    data = rows()
    if not data:
        raise SystemExit("No StatsBomb raw event files found")
    baselines = load_special_baselines()
    fields = SPECIAL_FIELDS
    by_tournament: dict[str, int] = defaultdict(int)
    for row in data:
        by_tournament[row["tournament"]] += 1

    lines = [
        "# Special Market Backtest",
        "",
        f"- source: {baselines.source}",
        f"- matches: {len(data)}",
        "- tournaments: " + ", ".join(f"{k}={v}" for k, v in sorted(by_tournament.items())),
        "",
        "## Leave-One-Tournament-Out Binary Baselines",
        "",
        "| family | n | successes | hit_rate | wilson_95 | brier_loto | brier_50 | edge_vs_50 | confidence |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for field in fields:
        row = loto(data, field)
        lines.append(
            f"| {field} | {row['n']} | {row['successes']} | {row['hit']:.3f} | "
            f"{row['wilson_low']:.3f}-{row['wilson_high']:.3f} | "
            f"{row['brier_loto']:.4f} | {row['brier_50']:.4f} | "
            f"{row['edge_vs_50']:.4f} | {row['confidence_tier']} |"
        )
    lines.extend(
        [
            "",
            "## Leave-One-Tournament-Out Baseline Calibration",
            "",
            "These reliability bins use the no-match-info leave-one-tournament-out base rate for each family. They validate the baseline prior only; they do not validate live odds/count scaling.",
            "",
            "| family | bin | n | mean_pred | hit_rate | abs_error | brier |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for field in fields:
        for row in calibration_bins(loto_scored(data, field)):
            lines.append(
                f"| {field} | {row['bin']} | {int(row['n'])} | "
                f"{float(row['mean_pred']):.3f} | {float(row['hit_rate']):.3f} | "
                f"{float(row['abs_error']):.3f} | {float(row['brier']):.4f} |"
            )
    lines.extend(
        [
            "",
            "## Leave-One-Tournament-Out Count Threshold Priors",
            "",
            "These are no-match-info priors for current special count-threshold questions. They validate the base threshold rate only; live pricing still uses projected count volume through the special pricer.",
            "",
            "| key | value_field | threshold | n | successes | hit_rate | wilson_95 | brier_loto | brier_50 | edge_vs_50 | confidence |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for key, value_field, threshold in COUNT_THRESHOLD_PRIORS:
        row = loto_threshold(data, value_field, threshold)
        lines.append(
            f"| {key} | {value_field} | {threshold} | {row['n']} | {row['successes']} | "
            f"{row['hit']:.3f} | {row['wilson_low']:.3f}-{row['wilson_high']:.3f} | "
            f"{row['brier_loto']:.4f} | {row['brier_50']:.4f} | {row['edge_vs_50']:.4f} | "
            f"{row['confidence_tier']} |"
        )
    heterogeneity = tournament_heterogeneity_rows(data)
    lines.extend(
        [
            "",
            "## Tournament Heterogeneity Diagnostics",
            "",
            "This checks how much each special prior's rate varies across source tournaments. High spread is a fragility warning for current-tournament transfer, not a probability transform.",
            "",
            "| key | type | tournaments | min_rate | max_rate | range | sd | matches_per_tournament | heterogeneity |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in heterogeneity:
        lines.append(
            f"| {row['key']} | {row['type']} | {int(row['tournaments'])} | "
            f"{float(row['min_rate']):.3f} | {float(row['max_rate']):.3f} | "
            f"{float(row['range']):.3f} | {float(row['sd']):.3f} | "
            f"{int(row['min_matches'])}-{int(row['max_matches'])} | {row['heterogeneity']} |"
        )
    adaptation_rows = [
        *adaptation_strength_rows(data, fields, knockout_only=False),
        *adaptation_strength_rows(data, fields, knockout_only=True),
    ]
    best_all = min((row for row in adaptation_rows if row["scope"] == "all_matches"), key=lambda row: row["delta"])
    best_knockout = min((row for row in adaptation_rows if row["scope"] == "knockout_only"), key=lambda row: row["delta"])
    best_all_preds = chronological_adaptation_predictions(data, fields, strength=int(best_all["strength"]))
    best_knockout_preds = chronological_adaptation_predictions(
        data,
        fields,
        strength=int(best_knockout["strength"]),
        knockout_only=True,
    )
    boot_all = bootstrap_adaptation_delta(best_all_preds)
    boot_knockout = bootstrap_adaptation_delta(best_knockout_preds)
    lines.extend(
        [
            "",
            "## Chronological In-Tournament Adaptation Replay",
            "",
            "This is a true ex-ante proxy: for each held-out tournament, the global prior is estimated from all other tournaments, then blended with only earlier matches in the same tournament. Matches with the same kickoff are scored before the prior is updated, so simultaneous games do not leak into each other. Negative delta means the adaptive prior has lower Brier than the constant leave-one-tournament-out prior.",
            "",
            "| scope | strength | n | base_brier | adaptive_brier | delta_adaptive_minus_base |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in adaptation_rows:
        lines.append(
            f"| {row['scope']} | {row['strength']} | {row['n']} | "
            f"{row['base_brier']:.4f} | {row['adaptive_brier']:.4f} | {row['delta']:.4f} |"
        )
    lines.extend(
        [
            "",
            "### Adaptation Bootstrap",
            "",
            "Tournament-level bootstrap uncertainty for the best point-estimate strength in each scope. This is a promotion guardrail, not a tuning license.",
            "",
            "| scope | strength | point_delta | ci_2.5 | median | ci_97.5 | pr_delta_lt_0 | decision |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for scope, best, boot in [
        ("all_matches", best_all, boot_all),
        ("knockout_only", best_knockout, boot_knockout),
    ]:
        decision = "not_promoted" if boot["ci_97.5"] >= 0 else "promoted"
        lines.append(
            f"| {scope} | {best['strength']} | {boot['point']:.4f} | {boot['ci_2.5']:.4f} | "
            f"{boot['median']:.4f} | {boot['ci_97.5']:.4f} | {boot['pr_lt_0']:.3f} | {decision} |"
        )
    lines.extend(
        [
            "",
            "### Best Adaptation Family Deltas",
            "",
            "Family rows for the best all-match strength. Negative delta means in-tournament adaptation improved that family in this proxy replay.",
            "",
            "| family | n | base_brier | adaptive_brier | delta_adaptive_minus_base |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in adaptation_family_rows(best_all_preds):
        lines.append(
            f"| {row['family']} | {row['n']} | {row['base_brier']:.4f} | "
            f"{row['adaptive_brier']:.4f} | {row['delta']:.4f} |"
        )
    lines.extend(
        [
            "",
            "Interpretation: promote in-tournament adaptation only if it survives this chronological replay with bootstrap support. If it is not promoted, current-tournament event trends should remain a research warning rather than an automatic special-market probability adjustment.",
        ]
    )
    lines.extend(
        [
            "",
            "## Leave-One-Tournament-Out Volume Scaling Diagnostic",
            "",
            "This tests the hazard-scaling shape used by `SpecialPricer` with realized match volume.",
            "It is an oracle-volume diagnostic, not an ex-ante betting backtest; negative deltas mean the scaled shape beats a constant held-out tournament rate conditional on realized volume.",
            "",
            "| family | volume | n | brier_constant | brier_scaled_oracle_volume | delta_scaled_minus_constant |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for field in fields:
        volume = SCALING_VOLUME.get(field)
        if not volume:
            continue
        row = loto_scaled_volume(data, field, volume)
        lines.append(
            f"| {field} | {volume} | {row['n']} | {row['brier_constant']:.4f} | "
            f"{row['brier_scaled_oracle_volume']:.4f} | {row['delta_scaled_minus_constant']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Oracle Volume Calibration Summary",
            "",
            "This is still oracle evidence because the scaled model uses realized held-out event volume. Use it to assess hazard-scaling shape, not to claim a deployable pre-match edge.",
            "",
            "| family | model | n | mean_pred | hit_rate | brier | ece_5bin |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for field in fields:
        volume = SCALING_VOLUME.get(field)
        if not volume:
            continue
        scored = loto_scaled_volume_scored(data, field, volume)
        summaries = {
            "constant": calibration_summary([(p_constant, outcome) for p_constant, _, outcome in scored]),
            "oracle_scaled": calibration_summary([(p_scaled, outcome) for _, p_scaled, outcome in scored]),
        }
        for model_name, row in summaries.items():
            lines.append(
                f"| {field} | {model_name} | {int(row['n'])} | {row['mean_pred']:.3f} | "
                f"{row['hit_rate']:.3f} | {row['brier']:.4f} | {row['ece']:.3f} |"
            )
    ex_ante = ex_ante_volume_replay_status(data)
    lines.extend(
        [
            "",
            "## Ex-Ante Volume Replay Availability",
            "",
            "| status | matches | reason |",
            "| --- | ---: | --- |",
            f"| {ex_ante['status']} | {ex_ante['matches']} | {ex_ante['reason']} |",
            "",
            "Interpretation: the oracle-volume table supports the direction of hazard scaling, but it is not sufficient evidence to create a more aggressive special-market policy. The live manual workflow may use odds/count-scaled `raw_special_model` as a calibrated prior, but it should not claim a validated ex-ante edge until archived pre-match odds/count projections or official settled SportsPredict special rows are available.",
        ]
    )
    lines.extend(
        [
            "",
            "## Count Conversion Parameters",
            "",
            f"- shots_per_sot: {baselines.shots_per_sot:.3f}",
            f"- total_shots_alpha: {baselines.total_shots_alpha:.3f}",
            f"- early_corner_share: {baselines.early_corner_share:.3f}",
            f"- early_corners_alpha: {baselines.early_corners_alpha:.3f}",
            f"- extra_time_goal_factor: {baselines.extra_time_goal_factor:.3f}",
            f"- extra_time_card_factor: {baselines.extra_time_card_factor:.3f}",
            "",
            "Interpretation: these are weaker than market-anchored goal/win prices, but they are now explicit, reproducible priors instead of unlogged manual constants.",
            "`weak_near_coinflip` timing markets should not be treated as strong edges without a field-average disagreement or a direct market anchor.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()
    text = report()
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
