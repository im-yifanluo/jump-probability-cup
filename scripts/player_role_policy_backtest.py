"""Backtest whether recent role history should alter player-prop policy.

This is a read-only manual-strategy diagnostic. It does not read
SportsPredict `/predictions` and never submits.

`player_lineup_status_backtest.py` validates what to do once the actual lineup
is known. This script asks the earlier operational question: before the current
match lineup is posted, does recent ESPN starter/bench history support changing
the field-relative player policy?
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import random
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import (  # noqa: E402
    HistoryMarket,
    asymmetric_field_policy,
    brier,
    clip_prob,
    official_delta_rbp,
)
from player_lineup_status_backtest import (  # noqa: E402
    EventContext,
    SCOREBOARD,
    SUMMARY,
    _event_date,
    _event_teams,
    _find_player,
    _records,
    build_contexts,
    classify_rows,
    verify_rosters,
)
from player_policy_backtest import load_player_rows  # noqa: E402
from questions.teams import names_match  # noqa: E402


OUT = ROOT / "data" / "logs" / "player_role_policy_backtest.md"


@dataclass(frozen=True)
class RoleContext:
    role_class: str
    observations: tuple[str, ...]
    team: str


@dataclass(frozen=True)
class RolePlayerRow:
    row: HistoryMarket
    player: str
    actual_status: str
    role: RoleContext


def fetch_events_with_rosters(start: date, end: date, session: requests.Session) -> list[EventContext]:
    events: list[EventContext] = []
    seen: set[str] = set()
    cur = start
    while cur <= end:
        board = session.get(SCOREBOARD, params={"dates": cur.strftime("%Y%m%d")}, timeout=20).json()
        for event in board.get("events", []):
            event_id = str(event.get("id", ""))
            if not event_id or event_id in seen:
                continue
            seen.add(event_id)
            summary = session.get(SUMMARY, params={"event": event_id}, timeout=20).json()
            rosters = verify_rosters(summary)
            if rosters is None:
                continue
            events.append(
                EventContext(
                    match=event.get("name", ""),
                    event_id=event_id,
                    kickoff=_event_date(event),
                    rosters=rosters,
                )
            )
        cur += timedelta(days=1)
    return sorted(events, key=lambda ctx: ctx.kickoff)


def event_involves_team(ctx: EventContext, team: str, raw_event: EventContext | None = None) -> bool:
    # EventContext.match is enough for report labels, but the rosters carry the
    # team display names in a more robust form.
    for roster in ctx.rosters:
        name = (roster.get("team") or {}).get("displayName") or (roster.get("team") or {}).get("name") or ""
        if names_match(team, name):
            return True
    return False


def player_role_in_context(player: str, team: str, ctx: EventContext) -> tuple[str, str]:
    records, matched_team = _records(ctx.rosters, team_hint=team)
    if not matched_team:
        return "team_mismatch", ""
    reason, matches = _find_player(player, records)
    if len(matches) != 1:
        return "not_listed" if not matches else "ambiguous", ""
    rec = matches[0]
    return ("starter" if rec.starter else "bench"), rec.display


def classify_recent_role(observations: list[str]) -> str:
    useful = [role for role in observations if role in {"starter", "bench", "not_listed"}]
    if not useful:
        return "unknown"
    starts = sum(role == "starter" for role in useful)
    last = useful[-1]
    if last == "starter" and starts / len(useful) >= 0.67:
        return "likely_starter"
    if last != "starter":
        return "bench_or_rotation_risk"
    return "rotation_risk"


def role_context_for_row(
    *,
    player: str,
    team: str,
    kickoff: datetime,
    events: list[EventContext],
    lookback_days: int,
) -> RoleContext:
    if not team:
        return RoleContext("unknown", (), "")
    start = kickoff - timedelta(days=lookback_days)
    observations: list[str] = []
    labelled: list[str] = []
    for ctx in events:
        if ctx.kickoff >= kickoff or ctx.kickoff < start:
            continue
        if not event_involves_team(ctx, team):
            continue
        role, matched = player_role_in_context(player, team, ctx)
        observations.append(role)
        label = f"{ctx.kickoff:%m-%d}:{role}"
        if matched:
            label += f"({matched})"
        labelled.append(label)
    return RoleContext(classify_recent_role(observations), tuple(labelled), team)


def build_role_rows(
    rows: list[HistoryMarket],
    match_contexts: dict[str, EventContext],
    events: list[EventContext],
    *,
    lookback_days: int,
) -> list[RolePlayerRow]:
    classified, _skipped = classify_rows(rows, match_contexts)
    out: list[RolePlayerRow] = []
    for item in classified:
        ctx = match_contexts[item.row.match]
        # Prefer the actual ESPN team resolved for the historical match. If the
        # player was not matched, fall back to the question's team hint.
        team = item.status.espn_team or item.team_hint
        role = role_context_for_row(
            player=item.player,
            team=team,
            kickoff=ctx.kickoff,
            events=events,
            lookback_days=lookback_days,
        )
        out.append(
            RolePlayerRow(
                row=item.row,
                player=item.player,
                actual_status=item.status.status,
                role=role,
            )
        )
    return out


def _asym_player(row: RolePlayerRow) -> float:
    return asymmetric_field_policy(above_k=0.0, below_k=0.75, max_abs_dev=0.10)(row.row)


def policy_probability(name: str, row: RolePlayerRow) -> float:
    if name == "submitted":
        return row.row.you
    if name == "field":
        return row.row.crowd
    if name == "player_asym075_cap10":
        return _asym_player(row)
    if name == "role_risk_field_else_asym":
        if row.role.role_class in {"bench_or_rotation_risk", "rotation_risk", "unknown"}:
            return row.row.crowd
        return _asym_player(row)
    if name == "benchrisk_field_else_asym":
        if row.role.role_class == "bench_or_rotation_risk":
            return row.row.crowd
        return _asym_player(row)
    raise KeyError(name)


POLICIES = [
    "submitted",
    "field",
    "player_asym075_cap10",
    "benchrisk_field_else_asym",
    "role_risk_field_else_asym",
]

COMPARISONS = [
    ("benchrisk_field_else_asym", "player_asym075_cap10"),
    ("role_risk_field_else_asym", "player_asym075_cap10"),
]


def score(rows: list[RolePlayerRow], policy: str) -> dict[str, float]:
    if not rows:
        return {"n": 0, "delta": 0.0, "brier": float("nan"), "mean_p": float("nan")}
    preds = [clip_prob(policy_probability(policy, row)) for row in rows]
    return {
        "n": len(rows),
        "delta": sum(official_delta_rbp(row.row, pred) for row, pred in zip(rows, preds)),
        "brier": sum(brier(pred, row.row.outcome or 0) for pred, row in zip(preds, rows)) / len(rows),
        "mean_p": sum(preds) / len(preds),
    }


def policy_delta(rows: list[RolePlayerRow], left: str, right: str) -> float:
    return sum(
        official_delta_rbp(row.row, policy_probability(left, row))
        - official_delta_rbp(row.row, policy_probability(right, row))
        for row in rows
    )


def chronological_comparison(
    rows: list[RolePlayerRow],
    left: str,
    right: str,
    *,
    min_prior: int,
) -> dict[str, float]:
    ordered = sorted(rows, key=lambda row: row.row.order)
    scored = ordered[min_prior:]
    if not scored:
        return {"n": 0, "delta": 0.0}
    return {"n": len(scored), "delta": policy_delta(scored, left, right)}


def match_bootstrap_comparison(
    rows: list[RolePlayerRow],
    left: str,
    right: str,
    *,
    trials: int = 1000,
    seed: int = 29,
) -> dict[str, float]:
    by_match: dict[str, list[RolePlayerRow]] = defaultdict(list)
    for row in rows:
        by_match[row.row.match].append(row)
    matches = sorted(by_match)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(trials):
        total = 0.0
        for _i in matches:
            match = rng.choice(matches)
            total += policy_delta(by_match[match], left, right)
        samples.append(total)
    samples.sort()
    if not samples:
        return {"point": 0.0, "ci_2.5": 0.0, "median": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    return {
        "point": policy_delta(rows, left, right),
        "ci_2.5": samples[int(0.025 * (len(samples) - 1))],
        "median": samples[int(0.50 * (len(samples) - 1))],
        "ci_97.5": samples[int(0.975 * (len(samples) - 1))],
        "pr_gt_0": sum(value > 0 for value in samples) / len(samples),
    }


def grouped_scores(rows: list[RolePlayerRow], key_fn) -> dict[str, list[dict[str, float | str]]]:
    grouped: dict[str, list[RolePlayerRow]] = defaultdict(list)
    for row in rows:
        grouped[key_fn(row)].append(row)
    return {
        key: [{"policy": policy, **score(part, policy)} for policy in POLICIES]
        for key, part in sorted(grouped.items())
    }


def role_summary(rows: list[RolePlayerRow]) -> list[dict[str, float | str]]:
    grouped: dict[str, list[RolePlayerRow]] = defaultdict(list)
    for row in rows:
        grouped[row.role.role_class].append(row)
    out = []
    for role, part in sorted(grouped.items()):
        out.append(
            {
                "role_class": role,
                "n": len(part),
                "skill_vs_field": sum(
                    100 * (brier(row.row.crowd, row.row.outcome or 0) - brier(row.row.you, row.row.outcome or 0))
                    for row in part
                ),
                "brier_model": sum(brier(row.row.you, row.row.outcome or 0) for row in part) / len(part),
                "brier_field": sum(brier(row.row.crowd, row.row.outcome or 0) for row in part) / len(part),
                "model": sum(row.row.you for row in part) / len(part),
                "field": sum(row.row.crowd for row in part) / len(part),
                "hit": sum(row.row.outcome or 0 for row in part) / len(part),
            }
        )
    return out


def _md(headers: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *["| " + " | ".join(row) + " |" for row in rows],
    ]


def render(
    rows: list[HistoryMarket],
    role_rows: list[RolePlayerRow],
    *,
    lookback_days: int,
    now: datetime,
) -> str:
    lines = [
        "# Player Role Policy Backtest",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- settled_player_rows_requested: {len(rows)}",
        f"- classified_role_rows: {len(role_rows)}",
        f"- lookback_days: {lookback_days}",
        "- role_source: ESPN historical summary rosters before each settled match",
        "- scoring: exact official-RBP deltas from displayed market RBP and realized Brier",
        "- limitation: this is a field-policy replay using the historical submitted probability as the model proxy; it does not reconstruct the exact old live model for every row",
        "",
        "## Recent Role Diagnostics",
        "",
    ]
    lines += _md(
        ["role_class", "n", "skill_vs_field", "brier_model", "brier_field", "model", "field", "hit"],
        [
            [
                str(row["role_class"]),
                str(row["n"]),
                f"{float(row['skill_vs_field']):.2f}",
                f"{float(row['brier_model']):.4f}",
                f"{float(row['brier_field']):.4f}",
                f"{float(row['model']):.3f}",
                f"{float(row['field']):.3f}",
                f"{float(row['hit']):.3f}",
            ]
            for row in role_summary(role_rows)
        ],
    )
    lines += ["", "## Overall Policy Replay", ""]
    lines += _md(
        ["policy", "n", "delta_vs_submitted", "brier", "mean_p"],
        [
            [
                str(row["policy"]),
                str(row["n"]),
                f"{float(row['delta']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
            ]
            for row in sorted(
                [{"policy": policy, **score(role_rows, policy)} for policy in POLICIES],
                key=lambda item: float(item["delta"]),
                reverse=True,
            )
        ],
    )
    lines += ["", "## Policy Replay By Recent Role", ""]
    by_role = grouped_scores(role_rows, lambda row: row.role.role_class)
    lines += _md(
        ["role_class", "policy", "n", "delta_vs_submitted", "brier", "mean_p"],
        [
            [
                role,
                str(row["policy"]),
                str(row["n"]),
                f"{float(row['delta']):.2f}",
                f"{float(row['brier']):.4f}",
                f"{float(row['mean_p']):.3f}",
            ]
            for role, part in by_role.items()
            for row in sorted(part, key=lambda item: float(item["delta"]), reverse=True)
        ],
    )

    min_prior = 30
    lines += [
        "",
        "## Direct Policy Comparisons",
        "",
        f"Chronological holdout scores only rows after {min_prior} prior settled player rows.",
        "",
    ]
    lines += _md(
        [
            "comparison",
            "full_sample_delta",
            "holdout_n",
            "chronological_holdout_delta",
            "bootstrap_ci_2.5",
            "bootstrap_median",
            "bootstrap_ci_97.5",
            "bootstrap_pr_gt_0",
        ],
        [
            [
                f"{left}_minus_{right}",
                f"{policy_delta(role_rows, left, right):.2f}",
                str(int(chronological_comparison(role_rows, left, right, min_prior=min_prior)["n"])),
                f"{chronological_comparison(role_rows, left, right, min_prior=min_prior)['delta']:.2f}",
                f"{match_bootstrap_comparison(role_rows, left, right)['ci_2.5']:.2f}",
                f"{match_bootstrap_comparison(role_rows, left, right)['median']:.2f}",
                f"{match_bootstrap_comparison(role_rows, left, right)['ci_97.5']:.2f}",
                f"{match_bootstrap_comparison(role_rows, left, right)['pr_gt_0']:.3f}",
            ]
            for left, right in COMPARISONS
        ],
    )

    examples = [
        row
        for row in role_rows
        if row.role.role_class in {"bench_or_rotation_risk", "rotation_risk"}
    ][:24]
    if examples:
        lines += [
            "",
            "## Role-Risk Examples",
            "",
            "| match | player | role_class | recent_roles | actual_status | model | field | outcome | question |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |",
        ]
        for row in examples:
            recent = ", ".join(row.role.observations) if row.role.observations else "-"
            lines.append(
                f"| {row.row.match} | {row.player} | {row.role.role_class} | {recent} | "
                f"{row.actual_status} | {row.row.you:.0%} | {row.row.crowd:.0%} | "
                f"{row.row.outcome or 0} | {row.row.question.replace('|', '/')} |"
            )

    lines += [
        "",
        "## Interpretation",
        "",
        "- Recent role history is useful as an operational warning, but it is not promoted as a new probability policy unless it beats the existing player policy in this replay.",
        "- The final lineup remains stronger evidence than role history. Exact current prop odds still dominate manual entry.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--start", default="2026-06-11", help="first ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--end", default="2026-06-28", help="last ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--lookback-days", type=int, default=21)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    session = requests.Session()
    rows = load_player_rows(sports_predict=args.sports_predict)
    match_contexts, _missing = build_contexts(
        [row.match for row in rows],
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        session=session,
    )
    events = fetch_events_with_rosters(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        session,
    )
    role_rows = build_role_rows(rows, match_contexts, events, lookback_days=args.lookback_days)
    text = render(rows, role_rows, lookback_days=args.lookback_days, now=datetime.now(timezone.utc))
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
