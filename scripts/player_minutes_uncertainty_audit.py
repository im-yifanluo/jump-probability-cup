"""Audit distributional player-minutes models on StatsBomb tournament data.

This is a local proxy research gate. It uses raw StatsBomb open-data events
only, never calls SportsPredict, never reads `/predictions`, and never submits
anything.

The current manual player model uses point minutes: 90 for confirmed starters,
20 for confirmed bench players, and 90 when lineups are not posted. This audit
tests whether replacing those point assumptions with chronological empirical
minutes distributions improves player event probabilities on tournament data.
Because this is a StatsBomb proxy rather than settled SportsPredict replay, a
positive result is not allowed to override field/exact-prop/lineup policy by
itself.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from typing import Callable

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
RAW_CANDIDATES = (ROOT / "data" / "raw" / "statsbomb", ROOT / "data_reference" / "raw" / "statsbomb")
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "player_minutes_uncertainty_audit.md"

FAMILIES = ("sot1", "scores", "score_or_assist")
METHODS = (
    "point90_no_lineup",
    "role_point_live",
    "role_empirical_mean",
    "role_empirical_distribution",
    "player_recent_mean",
    "player_recent_distribution",
    "actual_minutes_oracle",
)
DEFAULT_RATES_PER90 = {
    "sot1": 0.72,
    "scores": 0.10,
    "score_or_assist": 0.15,
}


@dataclass(frozen=True)
class PlayerAppearance:
    match_date: date
    tournament: str
    match_id: int
    player: str
    team: str
    role: str
    start_minute: float
    end_minute: float
    minutes: float
    sot: int
    goals: int
    assists: int

    def events_for(self, family: str) -> int:
        if family == "sot1":
            return self.sot
        if family == "scores":
            return self.goals
        if family == "score_or_assist":
            return self.goals + self.assists
        raise KeyError(family)


@dataclass
class PlayerHistory:
    minutes: list[float]
    events: dict[str, int]


def default_raw_dir() -> Path:
    for path in RAW_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError("No StatsBomb raw directory found under data/raw/statsbomb or data_reference/raw/statsbomb")


def _minute(event: dict) -> float:
    return min(90.0, max(0.0, float(event.get("minute", 0)) + float(event.get("second", 0)) / 60.0))


def _match_dates(raw_dir: Path) -> dict[tuple[str, int], date]:
    out: dict[tuple[str, int], date] = {}
    for tournament_dir in sorted(raw_dir.iterdir()):
        path = tournament_dir / "matches.json"
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            out[(tournament_dir.name, int(row["match_id"]))] = datetime.strptime(row["match_date"], "%Y-%m-%d").date()
    return out


def appearances_from_events(
    *,
    tournament: str,
    match_id: int,
    match_date: date,
    events: list[dict],
) -> list[PlayerAppearance]:
    players: dict[str, dict] = {}

    def ensure(player: str, team: str, role: str, start: float) -> dict:
        return players.setdefault(
            player,
            {
                "player": player,
                "team": team,
                "role": role,
                "start": start,
                "end": 90.0,
                "sot": 0,
                "goals": 0,
                "assists": 0,
            },
        )

    for event in events:
        etype = event.get("type", {}).get("name")
        team = event.get("team", {}).get("name", "")
        if etype == "Starting XI":
            for slot in event.get("tactics", {}).get("lineup", []):
                ensure(slot["player"]["name"], team, "starter", 0.0)
        elif etype == "Substitution":
            minute = _minute(event)
            outgoing = event.get("player", {}).get("name")
            if outgoing:
                rec = ensure(outgoing, team, "starter", 0.0)
                rec["end"] = min(float(rec["end"]), minute)
            replacement = event.get("substitution", {}).get("replacement", {}).get("name")
            if replacement:
                ensure(replacement, team, "substitute", minute)

    for event in events:
        player = event.get("player", {}).get("name")
        team = event.get("team", {}).get("name", "")
        if not player:
            continue
        etype = event.get("type", {}).get("name")
        if etype == "Shot":
            rec = ensure(player, team, "event_only", 0.0)
            outcome = event.get("shot", {}).get("outcome", {}).get("name")
            if outcome in {"Goal", "Saved", "Saved To Post"}:
                rec["sot"] += 1
            if outcome == "Goal":
                rec["goals"] += 1
        elif etype == "Pass" and event.get("pass", {}).get("goal_assist"):
            ensure(player, team, "event_only", 0.0)["assists"] += 1

    rows: list[PlayerAppearance] = []
    for rec in players.values():
        start = float(rec["start"])
        end = float(rec["end"])
        minutes = max(0.0, end - start)
        if minutes <= 0.0:
            continue
        rows.append(
            PlayerAppearance(
                match_date=match_date,
                tournament=tournament,
                match_id=match_id,
                player=str(rec["player"]),
                team=str(rec["team"]),
                role=str(rec["role"]),
                start_minute=start,
                end_minute=end,
                minutes=minutes,
                sot=int(rec["sot"]),
                goals=int(rec["goals"]),
                assists=int(rec["assists"]),
            )
        )
    return sorted(rows, key=lambda row: (row.team, row.player))


def load_appearances(raw_dir: Path | None = None) -> list[PlayerAppearance]:
    raw_dir = raw_dir or default_raw_dir()
    dates = _match_dates(raw_dir)
    rows: list[PlayerAppearance] = []
    for tournament_dir in sorted(raw_dir.iterdir()):
        events_dir = tournament_dir / "events"
        if not events_dir.exists():
            continue
        for path in sorted(events_dir.glob("*.json")):
            match_id = int(path.stem)
            match_date = dates.get((tournament_dir.name, match_id))
            if match_date is None:
                continue
            rows.extend(
                appearances_from_events(
                    tournament=tournament_dir.name,
                    match_id=match_id,
                    match_date=match_date,
                    events=json.loads(path.read_text(encoding="utf-8")),
                )
            )
    return sorted(rows, key=lambda row: (row.match_date, row.tournament, row.match_id, row.player))


def _event_probability(rate_per90: float, minutes: float) -> float:
    return float(1.0 - math.exp(-max(rate_per90, 0.0) * max(minutes, 0.0) / 90.0))


def _distribution_probability(rate_per90: float, minutes_values: list[float], fallback_minutes: float) -> float:
    values = minutes_values or [fallback_minutes]
    return float(np.mean([_event_probability(rate_per90, minutes) for minutes in values]))


def _brier(probability: float, outcome: int) -> float:
    return float((probability - outcome) ** 2)


def _rate_per90(
    player_history: PlayerHistory | None,
    family: str,
    *,
    global_rate: float,
    shrink_minutes: float,
) -> float:
    if player_history is None or not player_history.minutes:
        return global_rate
    minutes = sum(player_history.minutes)
    events = player_history.events.get(family, 0)
    return 90.0 * (events + global_rate * shrink_minutes / 90.0) / max(minutes + shrink_minutes, 1e-9)


def _mean(values: list[float], fallback: float) -> float:
    return float(np.mean(values)) if values else fallback


def replay(
    appearances: list[PlayerAppearance],
    *,
    min_training_rows: int = 500,
    min_player_rows: int = 3,
    shrink_minutes: float = 540.0,
) -> dict[str, list[dict[str, float | int | str]]]:
    global_minutes_by_role: dict[str, list[float]] = defaultdict(list)
    player_histories: dict[str, PlayerHistory] = {}
    global_minutes = 0.0
    global_events = {family: 0 for family in FAMILIES}
    scored: dict[str, list[dict[str, float | int | str]]] = {family: [] for family in FAMILIES}

    for app in appearances:
        if global_minutes > 0 and sum(len(values) for values in global_minutes_by_role.values()) >= min_training_rows:
            player_key = f"{app.team}::{app.player}"
            player_history = player_histories.get(player_key)
            role_values = global_minutes_by_role.get(app.role, [])
            role_point_fallback = 90.0 if app.role == "starter" else 20.0
            role_mean = _mean(role_values, role_point_fallback)
            player_values = player_history.minutes if player_history and len(player_history.minutes) >= min_player_rows else []
            player_mean = _mean(player_values, role_mean)
            for family in FAMILIES:
                global_rate = 90.0 * global_events[family] / global_minutes if global_minutes else DEFAULT_RATES_PER90[family]
                rate = _rate_per90(
                    player_history,
                    family,
                    global_rate=global_rate,
                    shrink_minutes=shrink_minutes,
                )
                outcome = int(app.events_for(family) >= 1)
                predictions = {
                    "point90_no_lineup": _event_probability(rate, 90.0),
                    "role_point_live": _event_probability(rate, role_point_fallback),
                    "role_empirical_mean": _event_probability(rate, role_mean),
                    "role_empirical_distribution": _distribution_probability(rate, role_values, role_mean),
                    "player_recent_mean": _event_probability(rate, player_mean),
                    "player_recent_distribution": _distribution_probability(rate, player_values, player_mean),
                    "actual_minutes_oracle": _event_probability(rate, app.minutes),
                }
                for method, probability in predictions.items():
                    scored[family].append(
                        {
                            "method": method,
                            "match_id": app.match_id,
                            "tournament": app.tournament,
                            "role": app.role,
                            "brier": _brier(probability, outcome),
                            "probability": probability,
                            "outcome": outcome,
                        }
                    )

        player_key = f"{app.team}::{app.player}"
        history = player_histories.setdefault(
            player_key,
            PlayerHistory(minutes=[], events={family: 0 for family in FAMILIES}),
        )
        history.minutes.append(app.minutes)
        for family in FAMILIES:
            history.events[family] += app.events_for(family)
            global_events[family] += app.events_for(family)
        global_minutes += app.minutes
        global_minutes_by_role[app.role].append(app.minutes)

    return scored


def _method_rows(scored_rows: list[dict[str, float | int | str]], *, baseline: str) -> list[dict[str, str]]:
    by_method: dict[str, list[dict[str, float | int | str]]] = defaultdict(list)
    for row in scored_rows:
        by_method[str(row["method"])].append(row)
    base = by_method.get(baseline, [])
    base_scores = [float(row["brier"]) for row in base]
    base_mean = float(np.mean(base_scores)) if base_scores else float("nan")
    out: list[dict[str, str]] = []
    for method in METHODS:
        rows = by_method.get(method, [])
        if not rows:
            continue
        brier = float(np.mean([float(row["brier"]) for row in rows]))
        delta = (base_mean - brier) * len(rows) * 100.0
        out.append(
            {
                "method": method,
                "n": str(len(rows)),
                "brier": f"{brier:.6f}",
                f"delta_vs_{baseline}_x100": f"{delta:.2f}",
                "brier_points_per_1000": f"{(base_mean - brier) * 100000:.3f}",
            }
        )
    return sorted(out, key=lambda row: float(row["brier"]))


def _bootstrap(
    scored_rows: list[dict[str, float | int | str]],
    *,
    challenger: str,
    baseline: str,
    trials: int = 2000,
    seed: int = 20260629,
) -> dict[str, float | int]:
    by_key: dict[tuple[int, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in scored_rows:
        key = (int(row["match_id"]), str(row["tournament"]))
        by_key[key][str(row["method"])] += float(row["brier"])
    deltas_by_match = [
        values[baseline] - values[challenger]
        for values in by_key.values()
        if baseline in values and challenger in values
    ]
    if not deltas_by_match:
        return {"matches": 0, "point_delta_x100": 0.0, "ci_2.5": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    values = np.array(deltas_by_match, dtype=float)
    rng = np.random.default_rng(seed)
    sampled = values[rng.integers(0, len(values), size=(trials, len(values)))].sum(axis=1) * 100.0
    return {
        "matches": len(values),
        "point_delta_x100": float(values.sum() * 100.0),
        "ci_2.5": float(np.quantile(sampled, 0.025)),
        "ci_97.5": float(np.quantile(sampled, 0.975)),
        "pr_gt_0": float((sampled > 0).mean()),
    }


def _role_minutes_summary(appearances: list[PlayerAppearance]) -> list[dict[str, str]]:
    grouped: dict[str, list[PlayerAppearance]] = defaultdict(list)
    for app in appearances:
        grouped[app.role].append(app)
    rows: list[dict[str, str]] = []
    for role, part in sorted(grouped.items()):
        minutes = np.array([app.minutes for app in part], dtype=float)
        rows.append(
            {
                "role": role,
                "n": str(len(part)),
                "mean": f"{float(minutes.mean()):.2f}",
                "p10": f"{float(np.quantile(minutes, 0.10)):.1f}",
                "p50": f"{float(np.quantile(minutes, 0.50)):.1f}",
                "p90": f"{float(np.quantile(minutes, 0.90)):.1f}",
            }
        )
    return rows


def analyze(appearances: list[PlayerAppearance]) -> dict[str, object]:
    scored = replay(appearances)
    family_reports: dict[str, dict[str, object]] = {}
    no_lineup_required = []
    confirmed_required = []
    for family, rows in scored.items():
        no_lineup_rows = _method_rows(rows, baseline="point90_no_lineup")
        role_rows = _method_rows(rows, baseline="role_point_live")
        no_lineup_boot = _bootstrap(rows, challenger="player_recent_distribution", baseline="point90_no_lineup")
        role_boot = _bootstrap(rows, challenger="role_empirical_distribution", baseline="role_point_live")
        family_reports[family] = {
            "rows": len(rows) // len(METHODS),
            "no_lineup_rows": no_lineup_rows,
            "role_rows": role_rows,
            "no_lineup_bootstrap": no_lineup_boot,
            "confirmed_role_bootstrap": role_boot,
        }
        no_lineup_distribution = next(
            (row for row in no_lineup_rows if row.get("method") == "player_recent_distribution"),
            {},
        )
        role_distribution = next(
            (row for row in role_rows if row.get("method") == "role_empirical_distribution"),
            {},
        )
        no_lineup_required.append(
            float(no_lineup_distribution.get("brier_points_per_1000", "0")) > 0.0
            and float(no_lineup_boot["ci_2.5"]) > 0.0
        )
        confirmed_required.append(
            float(role_distribution.get("brier_points_per_1000", "0")) > 0.0
            and float(role_boot["ci_2.5"]) > 0.0
        )

    if all(no_lineup_required) and all(confirmed_required):
        decision = "proxy_candidate_minutes_uncertainty_positive_not_live_promoted"
    elif all(no_lineup_required):
        decision = "proxy_candidate_no_lineup_player_recent_distribution"
    elif all(confirmed_required):
        decision = "proxy_candidate_confirmed_role_distribution"
    else:
        decision = "not_promoted_keep_point_minutes_with_lineup_guardrails"

    return {
        "appearances": len(appearances),
        "matches": len({(app.tournament, app.match_id) for app in appearances}),
        "date_min": min(app.match_date for app in appearances).isoformat() if appearances else "?",
        "date_max": max(app.match_date for app in appearances).isoformat() if appearances else "?",
        "decision": decision,
        "role_minutes_summary": _role_minutes_summary(appearances),
        "families": family_reports,
    }


def _table(headers: list[str], rows: list[dict[str, str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return lines


def render(analysis: dict[str, object], *, now: datetime | None = None, raw_dir: Path | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    raw_dir = raw_dir or default_raw_dir()
    lines = [
        "# Player Minutes Uncertainty Audit",
        "",
        "Read-only proxy analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        f"- raw_data: {raw_dir}",
        "- purpose: test whether empirical minutes distributions beat live-style point minutes for player event props",
        f"- decision: {analysis['decision']}",
        f"- appearances: {analysis['appearances']}",
        f"- matches: {analysis['matches']}",
        f"- date_range: {analysis['date_min']} to {analysis['date_max']}",
        "",
        "## Role Minutes Summary",
        "",
        *_table(["role", "n", "mean", "p10", "p50", "p90"], analysis["role_minutes_summary"]),  # type: ignore[arg-type]
        "",
        "## Method Definitions",
        "",
        "| method | definition |",
        "| --- | --- |",
        "| point90_no_lineup | no-current-lineup point assumption: every player receives 90 minutes |",
        "| role_point_live | current confirmed-lineup point assumption: starters 90 minutes, substitutes 20 minutes |",
        "| role_empirical_mean | chronological global mean minutes for the current role |",
        "| role_empirical_distribution | expectation over chronological global minutes distribution for the current role |",
        "| player_recent_mean | player's own prior mean minutes when at least 3 prior appearances exist, else role empirical mean |",
        "| player_recent_distribution | expectation over player's own prior minutes distribution when at least 3 prior appearances exist, else player_recent_mean |",
        "| actual_minutes_oracle | non-deployable upper-bound using the player's actual played minutes in the match |",
        "",
    ]
    families = analysis["families"]
    assert isinstance(families, dict)
    for family in FAMILIES:
        report = families[family]
        assert isinstance(report, dict)
        lines.extend(
            [
                f"## {family} No-Lineup Comparison",
                "",
                *_table(
                    ["method", "n", "brier", "delta_vs_point90_no_lineup_x100", "brier_points_per_1000"],
                    report["no_lineup_rows"],  # type: ignore[arg-type]
                ),
                "",
                f"## {family} Confirmed-Role Comparison",
                "",
                *_table(
                    ["method", "n", "brier", "delta_vs_role_point_live_x100", "brier_points_per_1000"],
                    report["role_rows"],  # type: ignore[arg-type]
                ),
                "",
            ]
        )
        no_lineup_boot = report["no_lineup_bootstrap"]
        role_boot = report["confirmed_role_bootstrap"]
        assert isinstance(no_lineup_boot, dict)
        assert isinstance(role_boot, dict)
        lines.extend(
            [
                f"## {family} Candidate Bootstraps",
                "",
                "| comparison | matches | point_delta_x100 | ci_2.5 | ci_97.5 | pr_gt_0 |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
                (
                    "| player_recent_distribution_minus_point90_no_lineup | "
                    f"{no_lineup_boot['matches']} | {no_lineup_boot['point_delta_x100']:.2f} | "
                    f"{no_lineup_boot['ci_2.5']:.2f} | {no_lineup_boot['ci_97.5']:.2f} | "
                    f"{no_lineup_boot['pr_gt_0']:.3f} |"
                ),
                (
                    "| role_empirical_distribution_minus_role_point_live | "
                    f"{role_boot['matches']} | {role_boot['point_delta_x100']:.2f} | "
                    f"{role_boot['ci_2.5']:.2f} | {role_boot['ci_97.5']:.2f} | "
                    f"{role_boot['pr_gt_0']:.3f} |"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation",
            "",
            "- This is a proxy player-event replay, not official SportsPredict validation.",
            "- Positive proxy evidence may justify future implementation work, but final lineups and exact prop odds still dominate manual entries.",
            "- Until this clears an official SportsPredict replay gate, keep the current live point-minutes policy and use the existing lineup/role flags to avoid stale minutes assumptions.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=None, help="StatsBomb raw directory")
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    raw_dir = args.raw_dir or default_raw_dir()
    analysis = analyze(load_appearances(raw_dir))
    text = render(analysis, raw_dir=raw_dir)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
