"""Replay current-tournament player-stat source as a player-prop strategy.

`current_player_stats_audit.py` attaches ESPN current-tournament player form to
open player props, but that report is intentionally diagnostic. This script
tests whether that source is strong enough to promote. For each settled
historical player prop, it maps the match to an ESPN event and builds the
player aggregate only from earlier completed ESPN events. Same-match stats are
never available to the prediction being scored.

This does not read `/predictions` and never submits. With `--sports-predict`,
SportsPredict is used only through GET `/events`, `/lobbies`, and `/results` to
settle older V1 history rows.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, brier, clip_prob, official_delta_rbp  # noqa: E402
from current_player_stats_audit import (  # noqa: E402
    OpenPlayerMarket,
    PlayerLine,
    aggregate_player_lines,
    canonical_team,
    parse_date,
    fetch_player_lines,
    source_for_market,
)
from player_policy_backtest import load_player_rows  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES, names_match  # noqa: E402


OUT = ROOT / "data" / "logs" / "current_player_stats_replay.md"


@dataclass(frozen=True)
class EventRef:
    event_id: str
    kickoff: datetime
    match: str


@dataclass(frozen=True)
class ReplayRow:
    row: HistoryMarket
    event: EventRef | None
    source_state: str
    stat_key: str
    threshold: int
    player_apps: int
    player_starts: int
    current_probability: float | None
    predictions: dict[str, float]


def event_refs_from_lines(lines: list[PlayerLine]) -> list[EventRef]:
    seen: dict[str, EventRef] = {}
    for line in lines:
        seen.setdefault(line.event_id, EventRef(line.event_id, line.kickoff, line.match))
    return sorted(seen.values(), key=lambda event: event.kickoff)


def _teams(match: str) -> tuple[str, str] | None:
    parts = re.split(r"\s+vs\.?\s+", match, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def same_match(left: str, right: str) -> bool:
    left_teams = _teams(left)
    right_teams = _teams(right)
    if not left_teams or not right_teams:
        return left.strip().lower() == right.strip().lower()
    def team_match(a: str, b: str) -> bool:
        return names_match(a, b) or names_match(canonical_team(a), canonical_team(b))

    return (
        team_match(left_teams[0], right_teams[0])
        and team_match(left_teams[1], right_teams[1])
    ) or (
        team_match(left_teams[0], right_teams[1])
        and team_match(left_teams[1], right_teams[0])
    )


def map_history_events(rows: list[HistoryMarket], events: list[EventRef]) -> dict[HistoryMarket, EventRef | None]:
    out: dict[HistoryMarket, EventRef | None] = {}
    for row in rows:
        matches = [event for event in events if same_match(row.match, event.match)]
        out[row] = matches[0] if len(matches) == 1 else None
    return out


def replay_source_for_row(row: HistoryMarket, event: EventRef, prior_lines: list[PlayerLine]) -> ReplayRow:
    try:
        spec = parse(row.question, teams=ALL_TEAM_NAMES)
    except Exception:
        return ReplayRow(row, event, "UNPARSED_PLAYER_ROW", "", 0, 0, 0, None, {})
    market = OpenPlayerMarket(
        market_id=f"history:{row.order}:{row.question[:40]}",
        match=row.match,
        kickoff=event.kickoff,
        question=row.question,
        spec=spec,
    )
    aggregates, global_means = aggregate_player_lines(prior_lines)
    source = source_for_market(market, aggregates, global_means)
    if source.probability is None:
        return ReplayRow(
            row=row,
            event=event,
            source_state=source.source_state if prior_lines else "NO_PRIOR_PLAYER_STATS",
            stat_key=source.stat_key,
            threshold=source.threshold,
            player_apps=0,
            player_starts=0,
            current_probability=None,
            predictions={},
        )
    agg = source.aggregate
    current = clip_prob(source.probability)
    predictions = {
        "raw_model": row.you,
        "current_player_stats": current,
        "raw75_current25": 0.75 * row.you + 0.25 * current,
        "raw50_current50": 0.50 * row.you + 0.50 * current,
        "raw25_current75": 0.25 * row.you + 0.75 * current,
    }
    return ReplayRow(
        row=row,
        event=event,
        source_state=source.source_state,
        stat_key=source.stat_key,
        threshold=source.threshold,
        player_apps=agg.appearances if agg else 0,
        player_starts=agg.starts if agg else 0,
        current_probability=current,
        predictions={name: clip_prob(value) for name, value in predictions.items()},
    )


def build_replay(rows: list[HistoryMarket], lines: list[PlayerLine]) -> list[ReplayRow]:
    events = event_refs_from_lines(lines)
    event_map = map_history_events(rows, events)
    replay: list[ReplayRow] = []
    for row in rows:
        event = event_map.get(row)
        if event is None:
            replay.append(ReplayRow(row, None, "NO_MATCH_EVENT", "", 0, 0, 0, None, {}))
            continue
        prior_lines = [line for line in lines if line.kickoff < event.kickoff]
        replay.append(replay_source_for_row(row, event, prior_lines))
    return replay


def scored_rows(replay: list[ReplayRow]) -> list[ReplayRow]:
    return [row for row in replay if row.current_probability is not None]


def score_policy(rows: list[ReplayRow], policy: str) -> dict[str, float]:
    if not rows:
        return {"n": 0, "delta_vs_raw": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    delta = br = mean_p = 0.0
    for item in rows:
        p = clip_prob(item.predictions[policy])
        delta += official_delta_rbp(item.row, p) - official_delta_rbp(item.row, item.row.you)
        br += brier(p, item.row.outcome or 0)
        mean_p += p
    return {
        "n": len(rows),
        "delta_vs_raw": delta,
        "brier": br / len(rows),
        "mean_p": mean_p / len(rows),
    }


def policy_summary(rows: list[ReplayRow]) -> list[dict[str, float | str]]:
    if not rows:
        return []
    names = list(rows[0].predictions)
    return sorted(
        [{"policy": name, **score_policy(rows, name)} for name in names],
        key=lambda item: float(item["delta_vs_raw"]),
        reverse=True,
    )


def bootstrap_policy_diff(rows: list[ReplayRow], left: str, right: str, *, trials: int, seed: int) -> dict[str, float]:
    if not rows or not trials:
        return {"ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    by_match: dict[str, list[ReplayRow]] = defaultdict(list)
    for row in rows:
        by_match[row.row.match].append(row)
    matches = sorted(by_match)
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(trials):
        delta = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for item in by_match[match]:
                delta += official_delta_rbp(item.row, clip_prob(item.predictions[left]))
                delta -= official_delta_rbp(item.row, clip_prob(item.predictions[right]))
        draws.append(delta)
    draws.sort()
    return {
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "median": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(value > 0 for value in draws) / len(draws),
    }


def family_diagnostics(rows: list[ReplayRow], policy: str) -> list[dict[str, float | str]]:
    grouped: dict[str, list[ReplayRow]] = defaultdict(list)
    for row in rows:
        grouped[row.row.famkey].append(row)
    out: list[dict[str, float | str]] = []
    for family, part in sorted(grouped.items()):
        summary = score_policy(part, policy)
        out.append(
            {
                "family": family,
                **summary,
                "raw": sum(row.row.you for row in part) / len(part),
                "current": sum(row.current_probability or 0 for row in part) / len(part),
                "hit": sum(row.row.outcome or 0 for row in part) / len(part),
            }
        )
    return out


def source_state_summary(replay: list[ReplayRow]) -> Counter[str]:
    return Counter(row.source_state for row in replay)


def promotion_decision(rows: list[ReplayRow], best_policy: str, boot: dict[str, float]) -> str:
    point = score_policy(rows, best_policy)["delta_vs_raw"] if rows else 0.0
    if len(rows) < 25:
        return "NOT_PROMOTED_TOO_SPARSE"
    if best_policy != "raw_model" and point > 0 and boot["ci_2.5"] > 0 and boot["pr_gt_0"] >= 0.90:
        return "PROMOTION_CANDIDATE_REVIEW"
    return "NOT_PROMOTED_DIAGNOSTIC_ONLY"


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render(
    replay: list[ReplayRow],
    *,
    settled_player_rows: int,
    sports_predict: bool,
    trials: int,
    seed: int,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    rows = scored_rows(replay)
    summaries = policy_summary(rows)
    best_policy = str(summaries[0]["policy"]) if summaries else "raw_model"
    boot = bootstrap_policy_diff(rows, best_policy, "raw_model", trials=trials, seed=seed)
    decision = promotion_decision(rows, best_policy, boot)
    state_counts = source_state_summary(replay)
    status = "COMPLETE" if rows else "NO_MATCHED_REPLAY_ROWS"
    lines = [
        "# Current Player Stats Replay",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- sports_predict: {'GET /events,/lobbies,/results settlement only' if sports_predict else 'not_called'}",
        "- public_source: ESPN scoreboard + ESPN summary rosters/player stats",
        f"- current_player_stats_replay_status: {status}",
        f"- decision: {decision}",
        f"- settled_player_rows: {settled_player_rows}",
        f"- replay_rows: {len(replay)}",
        f"- matched_replay_rows: {len(rows)}",
        f"- best_policy: {best_policy}",
        f"- best_delta_vs_raw: {score_policy(rows, best_policy)['delta_vs_raw']:.2f}",
        f"- best_bootstrap_ci_2.5: {boot['ci_2.5']:.2f}",
        f"- best_bootstrap_pr_gt_0: {boot['pr_gt_0']:.3f}",
        "- leakage_guard: each row uses only ESPN player lines from matches with kickoff earlier than the scored match",
        "",
        "## Source State Summary",
        "",
        "| source_state | rows |",
        "| --- | ---: |",
    ]
    for state, count in sorted(state_counts.items()):
        lines.append(f"| {state} | {count} |")
    lines.extend(["", "## Policy Replay", ""])
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
    if rows:
        lines.extend(["", "## Family Diagnostics", ""])
        lines += md_table(
            ["family", "n", "delta_vs_raw", "raw", "current", best_policy, "hit"],
            [
                [
                    str(row["family"]),
                    f"{int(row['n'])}",
                    f"{float(row['delta_vs_raw']):.2f}",
                    f"{float(row['raw']):.3f}",
                    f"{float(row['current']):.3f}",
                    f"{float(row['mean_p']):.3f}",
                    f"{float(row['hit']):.3f}",
                ]
                for row in family_diagnostics(rows, best_policy)
            ],
        )
    if trials:
        point = score_policy(rows, best_policy)["delta_vs_raw"] if rows else 0.0
        lines.extend(
            [
                "",
                "## Match-Level Bootstrap",
                "",
                f"Trials: {trials}; seed: {seed}. Bootstrap is over fixed replay predictions by historical match.",
                "",
                "| comparison | point_delta | ci_2.5 | median | ci_97.5 | pr_gt_0 |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
                f"| {best_policy}_minus_raw_model | {point:.2f} | {boot['ci_2.5']:.2f} | {boot['median']:.2f} | {boot['ci_97.5']:.2f} | {boot['pr_gt_0']:.3f} |",
            ]
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is a promotion gate for ESPN current-tournament player form, not a live pricing rule by itself.",
            "- Promote only if a predeclared raw/current-form policy beats raw chronologically with a positive match-bootstrap lower bound.",
            "- If the decision is not `PROMOTION_CANDIDATE_REVIEW`, keep current-player stats as research context only and rely on exact prop odds and confirmed lineups for player rows.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start-date", default="2026-06-11")
    ap.add_argument("--end-date", default=datetime.now(timezone.utc).date().isoformat())
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=37)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_player_rows(sports_predict=args.sports_predict)
    lines = fetch_player_lines(parse_date(args.start_date), parse_date(args.end_date))
    replay = build_replay(rows, lines)
    text = render(
        replay,
        settled_player_rows=len(rows),
        sports_predict=args.sports_predict,
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
