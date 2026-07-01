"""Prioritized research queue for manual Probability Cup entries.

This report is read-only. It uses the same independent pricing and live-risk
classification stack as `live_market_risk_audit.py`, then ranks the open rows
where extra manual work can actually change the recommended entry.

It does not read `/predictions` and never submits.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from full_manual_pricing import (  # noqa: E402
    current_player_no_field_policy,
    field_coverage,
    price_rows,
    read_field_csv,
    read_open_markets,
    refresh_lineups,
    selected_markets,
)
from live_market_risk_audit import audit_priced_rows  # noqa: E402
from manual.player_prop_anchors import read_player_prop_anchor_csv  # noqa: E402
from manual.polymarket_direct import fetch_polymarket_events  # noqa: E402
from player_role_research_audit import load_role_summary  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


OUT = ROOT / "data" / "logs" / "manual_research_queue.md"
RESEARCH_RESOLUTION_CSV = ROOT / "data" / "manual_research_resolutions.csv"

TIER_SCORE = {"HIGH": 100, "MEDIUM": 50, "LOW": 0}
UNRESOLVED_AFTER_RESEARCH_STATUSES = {
    "researched_no_usable_current_source",
    "researched_no_usable_exact_anchor",
}


def load_research_resolutions(path: Path = RESEARCH_RESOLUTION_CSV) -> dict[tuple[str, str], dict[str, str]]:
    """Load completed negative research checks.

    These rows are not ready to lock; they are just no longer untouched
    overnight/morning research work. Packet and match-lock audits still carry
    them as near-lock rechecks.
    """

    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            status = (row.get("status") or "").strip()
            if status not in UNRESOLVED_AFTER_RESEARCH_STATUSES:
                continue
            market_id = (row.get("market_id") or "").strip()
            match = (row.get("match") or "").strip()
            question = (row.get("question") or "").strip()
            if market_id:
                out[("id", market_id)] = row
            if match and question:
                out[(match, question)] = row
    return out


def research_resolution_for(
    row: dict[str, object],
    resolutions: dict[tuple[str, str], dict[str, str]],
) -> dict[str, str] | None:
    market_id = str(row.get("market_id") or "")
    if market_id and ("id", market_id) in resolutions:
        return resolutions[("id", market_id)]
    match = str(row.get("match_name") or row.get("match") or "")
    question = str(row.get("question") or "")
    return resolutions.get((match, question))


def research_bucket(row: dict) -> tuple[str, str]:
    """Return an operational bucket plus why it matters."""

    spec = parse(row["question"], teams=ALL_TEAM_NAMES)
    action = row.get("risk_action", "")
    evidence = row.get("risk_evidence", "")
    flags = row.get("flags") or ""
    if spec.player and row.get("risk_tier") == "HIGH":
        return (
            "player_hard_flag",
            "Validated player-policy leak; clear prop/lineup facts because field averages are unavailable before lock.",
        )
    if spec.player and row.get("player_role_class") in {"bench_or_rotation_risk", "rotation_risk"}:
        return (
            "player_role_risk",
            "Recent ESPN roster evidence shows bench/rotation risk; final XI or exact prop odds should resolve it.",
        )
    if spec.player:
        return (
            "player_recheck",
            "Player rows remain lineup/role sensitive; exact prop odds or confirmed lineup can change the entry.",
        )
    if "field average" in action and spec.stat == "cards":
        return (
            "field_unavailable_prelock",
            "Card-count backtest is field-led historically, but field averages are unavailable before lock; carry no-field risk.",
        )
    if "field average" in action:
        return (
            "field_unavailable_prelock",
            "Field-relative count policy is unavailable before lock; carry no-field risk.",
        )
    special_state = row.get("special_risk_state") or ""
    if special_state == "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE":
        return (
            "special_count_far_from_base",
            "Count-threshold special depends on unvalidated volume scaling far from the broad StatsBomb base rate.",
        )
    if special_state == "COUNT_THRESHOLD_HIGH_HETEROGENEITY":
        return (
            "special_count_heterogeneity",
            "Total-shots special has high cross-tournament spread; verify current count-volume evidence before lock.",
        )
    if special_state == "COUNT_THRESHOLD_PROXY":
        return (
            "special_count_proxy",
            "Count-threshold special is backed by a no-match-info StatsBomb prior plus proxy volume scaling.",
        )
    if "weak prior" in action:
        return (
            "weak_special_check",
            "This special family is near coinflip in StatsBomb LOTO; large current-source disagreement should override confidence.",
        )
    if "base-rate" in action or "penalty/red" in evidence:
        return (
            "low_event_base_rate",
            "Penalty/red evidence is sparse; avoid moving above the low-event base without a direct current anchor.",
        )
    if flags:
        return (
            "flagged_review",
            "Model row has execution flags that should be cleared before lock.",
        )
    return (
        "special_or_count_review",
        "Medium-risk row without a stronger exact current anchor.",
    )


def priority_score(row: dict, *, now: datetime) -> int:
    """Rank rows by validated leverage and urgency.

    The score is intentionally coarse. It is an execution ordering, not a new
    probability model.
    """

    score = TIER_SCORE.get(row.get("risk_tier", ""), 0)
    bucket, _reason = research_bucket(row)
    score += {
        "player_hard_flag": 35,
        "special_count_far_from_base": 35,
        "player_role_risk": 25,
        "player_recheck": 20,
        "special_count_heterogeneity": 18,
        "field_unavailable_prelock": 6,
        "weak_special_check": 10,
        "low_event_base_rate": 8,
        "special_count_proxy": 8,
    }.get(bucket, 5)

    hours = (row["kickoff"].astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds() / 3600
    if hours <= 2:
        score += 30
    elif hours <= 12:
        score += 20
    elif hours <= 36:
        score += 10
    return score


def build_queue(
    rows: list[dict],
    *,
    now: datetime,
    include_low: bool = False,
    resolutions: dict[tuple[str, str], dict[str, str]] | None = None,
) -> list[dict]:
    resolutions = resolutions or {}
    queue = []
    for row in rows:
        resolution = research_resolution_for(row, resolutions)
        if resolution and not include_low:
            continue
        if not include_low and row.get("risk_tier") == "LOW":
            continue
        if resolution:
            bucket = "researched_unresolved_recheck"
            reason = (
                "Targeted research found no usable exact source; keep the provisional value "
                "and recheck lineups/current sources near lock."
            )
            score = 45
        else:
            bucket, reason = research_bucket(row)
            score = priority_score(row, now=now)
        if not include_low and bucket == "field_unavailable_prelock":
            continue
        queue.append(
            {
                **row,
                "research_bucket": bucket,
                "research_reason": reason,
                "priority_score": score,
                "research_resolution": (resolution or {}).get("status", ""),
            }
        )
    return sorted(
        queue,
        key=lambda row: (
            -row["priority_score"],
            row["kickoff"],
            row["match_name"],
            row["question"],
        ),
    )


def render(
    queue: list[dict],
    *,
    local_date: str,
    tz: ZoneInfo,
    now: datetime,
    include_low: bool,
) -> str:
    counts = Counter(row["research_bucket"] for row in queue)
    tier_counts = Counter(row["risk_tier"] for row in queue)
    lines = [
        "# Manual Research Queue",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- local_date: {local_date}",
        "- bot_predictions: not_read",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets, /results only",
        "- purpose: rank the manual research work that can change entries before lock",
        f"- include_low: {str(include_low).lower()}",
        "",
        "## Queue Summary",
        "",
        "| tier | rows |",
        "| --- | ---: |",
    ]
    for tier in ("HIGH", "MEDIUM", "LOW"):
        if tier_counts.get(tier, 0) or include_low:
            lines.append(f"| {tier} | {tier_counts.get(tier, 0)} |")
    lines.extend(["", "| bucket | rows |", "| --- | ---: |"])
    for bucket, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {bucket} | {count} |")

    lines.extend(
        [
            "",
            "## Priority Rows",
            "",
            "| rank | score | kickoff_local | tier | bucket | match | model | entry | role | action | question | reason |",
            "| ---: | ---: | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for idx, row in enumerate(queue, 1):
        kickoff = row["kickoff"].astimezone(tz)
        role = row.get("player_role_class") or "-"
        if row.get("player_recent_roles"):
            role = f"{role} ({row['player_recent_roles']})"
        lines.append(
            f"| {idx} | {row['priority_score']} | {kickoff:%Y-%m-%d %H:%M %Z} | "
            f"{row['risk_tier']} | {row['research_bucket']} | {row['match_name']} | "
            f"{row['probability']}% | {row['manual_entry']} | {role} | {row['risk_action']} | "
            f"{row['question']} | {row['research_reason']} |"
        )

    by_match: dict[str, list[dict]] = defaultdict(list)
    for row in queue:
        by_match[row["match_name"]].append(row)
    lines.extend(["", "## Match Checklist", ""])
    for match_name, match_rows in by_match.items():
        kickoff = match_rows[0]["kickoff"].astimezone(tz)
        lines.extend(
            [
                f"### {match_name}",
                "",
                f"- kickoff_local: {kickoff:%Y-%m-%d %H:%M %Z}",
            ]
        )
        for row in match_rows:
            role = ""
            if row.get("player_role_class"):
                role = f" [{row['player_role_class']}: {row.get('player_recent_roles') or '-'}]"
            lines.append(
                f"- [{row['risk_tier']}] {row['research_bucket']}{role}: {row['risk_action']} -- {row['question']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-date", help="Local date to queue, YYYY-MM-DD. Default: today in --tz.")
    ap.add_argument("--all-open", action="store_true", help="Queue all currently open future markets.")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--include-started", action="store_true", help="Include matches that have already kicked off.")
    ap.add_argument("--include-low", action="store_true", help="Include low-risk monitoring rows.")
    ap.add_argument("--match", action="append", help="Restrict to an exact SportsPredict match name. Repeatable.")
    ap.add_argument("--field-csv", help="Optional CSV with market_id,field columns from visible UI field averages.")
    ap.add_argument("--player-prop-csv", help="Optional exact current player-prop anchors CSV.")
    ap.add_argument("--no-polymarket-direct", action="store_true", help="Disable exact current Polymarket anchors for eligible non-player markets.")
    ap.add_argument("--polymarket-direct-min-volume", type=float, default=1000.0)
    ap.add_argument("--no-polymarket-player-props", action="store_true", help="Disable exact current Polymarket anchors for eligible player props.")
    ap.add_argument("--polymarket-player-min-volume", type=float, default=1000.0)
    ap.add_argument("--no-polymarket-count-direct", action="store_true", help="Disable exact current Polymarket anchors for eligible corner count markets.")
    ap.add_argument("--polymarket-count-min-volume", type=float, default=5000.0)
    ap.add_argument("--lineup-write", action="store_true", help="Persist pulled ESPN lineup minutes if lineups are posted.")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    tz = ZoneInfo(args.tz)
    now = datetime.now(timezone.utc)
    local_date = args.local_date or now.astimezone(tz).date().isoformat()
    matches = set(args.match) if args.match else None
    all_rows = read_open_markets()
    if args.all_open:
        grace = timedelta(minutes=10)
        rows = [
            row
            for row in all_rows
            if (not matches or row.match_name in matches)
            and (args.include_started or row.kickoff >= now.astimezone(timezone.utc) - grace)
        ]
        local_date = "all-open"
    else:
        rows = selected_markets(
            all_rows,
            local_date=local_date,
            tz=tz,
            remaining_only=not args.include_started,
            now=now,
            matches=matches,
        )
    if not rows:
        raise SystemExit(f"No selected open markets for local date {local_date}")

    field_by_market = read_field_csv(args.field_csv)
    player_prop_anchors = read_player_prop_anchor_csv(args.player_prop_csv)
    player_no_field_policy = current_player_no_field_policy()
    use_polymarket_events = not (
        args.no_polymarket_direct
        and args.no_polymarket_player_props
        and args.no_polymarket_count_direct
    )
    polymarket_events = fetch_polymarket_events() if use_polymarket_events else []
    lineups = refresh_lineups(rows, write=args.lineup_write)
    priced, _diagnostics = price_rows(
        rows,
        lineups=lineups,
        field_by_market=field_by_market,
        player_prop_anchors=player_prop_anchors,
        player_no_field_base=player_no_field_policy.base,
        polymarket_events=polymarket_events,
        polymarket_direct_min_volume=args.polymarket_direct_min_volume,
        polymarket_player_min_volume=args.polymarket_player_min_volume,
        polymarket_count_min_volume=args.polymarket_count_min_volume,
    )
    audited = audit_priced_rows(priced, now=now, role_summary=load_role_summary())
    queue = build_queue(
        audited,
        now=now,
        include_low=args.include_low,
        resolutions=load_research_resolutions(),
    )
    text = render(queue, local_date=local_date, tz=tz, now=now, include_low=args.include_low)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
