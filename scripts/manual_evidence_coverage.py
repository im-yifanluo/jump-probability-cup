"""Per-market evidence coverage audit for manual entries.

This report joins the live manual pricing/risk stack to the strategy evidence
matrix. It answers a narrow operational question: for each currently open
manual market, which evidence branch supports the recommended entry, and what
still has to be checked before lock?

It is read-only. It does not read `/predictions` and never submits.
"""

from __future__ import annotations

import argparse
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
    price_rows,
    read_field_csv,
    read_open_markets,
    refresh_lineups,
    selected_markets,
)
from live_market_risk_audit import audit_priced_rows  # noqa: E402
from manual.player_prop_anchors import read_player_prop_anchor_csv  # noqa: E402
from manual.policy import (  # noqa: E402
    is_count_market,
    is_odds_goal_market,
    is_special_market,
    is_strong_sot_count,
)
from manual.polymarket_direct import fetch_polymarket_events  # noqa: E402
from player_role_research_audit import load_role_summary  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402
from strategy_evidence_matrix import build_matrix  # noqa: E402


OUT = ROOT / "data" / "logs" / "manual_evidence_coverage.md"
DIRECT_ANCHOR_RISK_LOG = ROOT / "data" / "logs" / "polymarket_direct_anchor_risk_audit.md"


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _table_after(text: str, heading: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            start = idx + 1
            break
    if start is None:
        return []
    while start < len(lines) and not lines[start].lstrip().startswith("|"):
        start += 1
    if start >= len(lines):
        return []
    headers = [_clean(part) for part in lines[start].strip().strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        parts = [_clean(part) for part in line.strip().strip("|").split("|")]
        if len(parts) == len(headers):
            rows.append(dict(zip(headers, parts)))
    return rows


def evidence_statuses() -> dict[str, str]:
    return {row.area: row.status for row in build_matrix()}


def load_direct_anchor_risks(path: Path = DIRECT_ANCHOR_RISK_LOG) -> dict[tuple[str, str], dict[str, str]]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    out: dict[tuple[str, str], dict[str, str]] = {}
    for row in _table_after(text, "Current Anchor Recheck Rows"):
        match = row.get("match", "")
        question = row.get("question", "")
        if not match or not question:
            continue
        out[(match, question)] = {
            "risk": row.get("risk", ""),
            "tags": row.get("tags", ""),
            "action": row.get("action", ""),
            "delta_pp": row.get("delta_pp", ""),
        }
    return out


def _flag_set(value: str | None) -> set[str]:
    if not value or value == "-":
        return set()
    return {part for part in value.split(",") if part}


def evidence_area(row: dict) -> str:
    spec = parse(row["question"], teams=ALL_TEAM_NAMES)
    model = row.get("model") or ""
    policy = row.get("manual_policy") or ""

    if (
        "current_polymarket_direct" in model
        or "current_player_prop_anchor" in model
        or "current_polymarket_count_direct" in model
    ):
        return "exact_polymarket_anchors"

    if spec.player:
        if policy.startswith("player_no_field") or policy.startswith("research_required_no_field"):
            return "player_no_field_fallback"
        return "researched_player_props"

    if spec.family in {"penalty_awarded", "penalty_or_red", "red_card"}:
        return "penalty_red"

    if is_special_market(spec):
        return "special_markets"

    if is_odds_goal_market(spec):
        return "odds_goal_rows"

    if is_count_market(spec):
        return "count_field_policy"

    return "combined_manual_policy"


def coverage_state(row: dict, *, status: str) -> tuple[str, str]:
    spec = parse(row["question"], teams=ALL_TEAM_NAMES)
    risk_tier = row.get("risk_tier", "")
    action = row.get("risk_action", "")
    flags = _flag_set(row.get("flags"))

    if spec.player and risk_tier == "HIGH":
        return "PLAYER_RESEARCH_REQUIRED", "clear lineup/role/prop flags; field averages are not available before lock"
    if spec.player and ("recheck lineup" in action or flags):
        return "PLAYER_RECHECK", "recheck lineup and current prop market before lock"
    if "field average" in action:
        return (
            "FIELD_UNAVAILABLE_PRELOCK",
            "field averages are not available before prediction lock; use the no-field/current-source value and carry lower confidence",
        )
    if status == "CURRENT_SOURCE_RULE":
        return (
            "CURRENT_ANCHOR_RECHECK",
            "use only as a current-source anchor; replay gate is not historically promoted yet, so verify exact wording, threshold, period, subject, and volume",
        )
    if status == "PROXY_ONLY":
        special_state = row.get("special_risk_state") or ""
        if special_state == "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE":
            return (
                "SPECIAL_COUNT_RESEARCH_REQUIRED",
                "verify count-volume inputs and current count sources before trusting raw proxy prior",
            )
        if special_state in {"COUNT_THRESHOLD_HIGH_HETEROGENEITY", "COUNT_THRESHOLD_PROXY"}:
            return (
                "SPECIAL_COUNT_RECHECK",
                "recheck current count-volume evidence before lock",
            )
        return "PROXY_ONLY", "use raw proxy prior but treat as lower-confidence than official-history rows"
    if status == "GUARDRAIL_ONLY":
        return "GUARDRAIL_ONLY", "avoid aggressive movement without direct current evidence"
    if status == "PROMOTED_LIMITED":
        return "LIMITED_SUPPORT", "stay inside the documented support set"
    if status in {"NOT_PROMOTED", "REJECTED"}:
        return "NOT_PROMOTED_GUARD", "do not convert this branch into an optimization rule"
    if risk_tier == "MEDIUM":
        return "MONITOR", "monitor before lock; no automatic probability change"
    return "COVERED", "covered by a promoted branch with no unresolved live gate"


def build_coverage(
    rows: list[dict],
    statuses: dict[str, str] | None = None,
    direct_anchor_risks: dict[tuple[str, str], dict[str, str]] | None = None,
) -> list[dict]:
    statuses = statuses or evidence_statuses()
    direct_anchor_risks = load_direct_anchor_risks() if direct_anchor_risks is None else direct_anchor_risks
    out = []
    for row in rows:
        area = evidence_area(row)
        status = statuses.get(area, "UNKNOWN")
        state, gate = coverage_state(row, status=status)
        direct_risk = direct_anchor_risks.get((row["match_name"], row["question"]), {})
        direct_risk_level = direct_risk.get("risk", "") if area == "exact_polymarket_anchors" else ""
        direct_risk_tags = direct_risk.get("tags", "") if area == "exact_polymarket_anchors" else ""
        if direct_risk_level:
            gate = (
                f"{gate}; direct-anchor risk {direct_risk_level}"
                f"{f' ({direct_risk_tags})' if direct_risk_tags else ''}: {direct_risk.get('action', '')}"
            )
        out.append(
            {
                **row,
                "evidence_area": area,
                "evidence_status": status,
                "coverage_state": state,
                "coverage_gate": gate,
                "direct_anchor_risk": direct_risk_level or "-",
            }
        )
    return sorted(
        out,
        key=lambda row: (
            row["kickoff"],
            row["match_name"],
            row["coverage_state"],
            row["question"],
        ),
    )


def render(
    rows: list[dict],
    *,
    local_date: str,
    tz: ZoneInfo,
    now: datetime,
) -> str:
    status_counts = Counter(row["evidence_status"] for row in rows)
    state_counts = Counter(row["coverage_state"] for row in rows)
    area_counts = Counter(row["evidence_area"] for row in rows)
    by_match: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_match[row["match_name"]].append(row)

    lines = [
        "# Manual Evidence Coverage Audit",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- local_date: {local_date}",
        "- bot_predictions: not_read",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets, /results only",
        "- purpose: map every live manual market to its evidence branch and unresolved execution gate",
        "",
        "## Coverage Summary",
        "",
        "| evidence_status | markets |",
        "| --- | ---: |",
    ]
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")
    lines.extend(["", "| coverage_state | markets |", "| --- | ---: |"])
    for state, count in sorted(state_counts.items()):
        lines.append(f"| {state} | {count} |")
    lines.extend(["", "| evidence_area | markets |", "| --- | ---: |"])
    for area, count in sorted(area_counts.items()):
        lines.append(f"| {area} | {count} |")

    lines.extend(
        [
            "",
            "## Markets",
            "",
            "| market_id | kickoff_local | match | entry | risk | direct_anchor_risk | evidence_status | coverage_state | evidence_area | question | gate |",
            "| --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        kickoff = row["kickoff"].astimezone(tz)
        lines.append(
            f"| {row.get('market_id', '')} | {kickoff:%Y-%m-%d %H:%M %Z} | {row['match_name']} | {row['manual_entry']} | "
            f"{row.get('risk_tier', '-')} | {row.get('direct_anchor_risk', '-')} | "
            f"{row['evidence_status']} | {row['coverage_state']} | "
            f"{row['evidence_area']} | {row['question']} | {row['coverage_gate']} |"
        )

    lines.extend(["", "## Match Checklist", ""])
    action_states = {
        "PLAYER_RESEARCH_REQUIRED",
        "PLAYER_RECHECK",
        "FIELD_UNAVAILABLE_PRELOCK",
        "SPECIAL_COUNT_RESEARCH_REQUIRED",
        "SPECIAL_COUNT_RECHECK",
        "CURRENT_ANCHOR_RECHECK",
        "PROXY_ONLY",
        "GUARDRAIL_ONLY",
        "NOT_PROMOTED_GUARD",
    }
    for match_name, match_rows in by_match.items():
        actionable = [row for row in match_rows if row["coverage_state"] in action_states]
        if not actionable:
            continue
        kickoff = match_rows[0]["kickoff"].astimezone(tz)
        lines.extend([f"### {match_name}", "", f"- kickoff_local: {kickoff:%Y-%m-%d %H:%M %Z}"])
        for row in actionable:
            lines.append(
                f"- [{row['coverage_state']}] {row['evidence_area']} ({row['evidence_status']}): "
                f"{row['coverage_gate']} -- {row['question']}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-date", help="Local date to audit, YYYY-MM-DD. Default: today in --tz.")
    ap.add_argument("--all-open", action="store_true", help="Audit all currently open future markets.")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--include-started", action="store_true", help="Include matches that have already kicked off.")
    ap.add_argument("--match", action="append", help="Restrict to an exact SportsPredict match name. Repeatable.")
    ap.add_argument("--field-csv", help="Optional CSV with market_id,field columns from visible UI field averages.")
    ap.add_argument("--player-prop-csv", help="Optional exact current player-prop anchors CSV.")
    ap.add_argument("--no-polymarket-direct", action="store_true")
    ap.add_argument("--polymarket-direct-min-volume", type=float, default=1000.0)
    ap.add_argument("--no-polymarket-player-props", action="store_true")
    ap.add_argument("--polymarket-player-min-volume", type=float, default=1000.0)
    ap.add_argument("--no-polymarket-count-direct", action="store_true")
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
        selected = [
            row
            for row in all_rows
            if (not matches or row.match_name in matches)
            and (args.include_started or row.kickoff >= now.astimezone(timezone.utc) - grace)
        ]
        local_date = "all-open"
    else:
        selected = selected_markets(
            all_rows,
            local_date=local_date,
            tz=tz,
            remaining_only=not args.include_started,
            now=now,
            matches=matches,
        )
    if not selected:
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
    lineups = refresh_lineups(selected, write=args.lineup_write)
    priced, _diagnostics = price_rows(
        selected,
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
    coverage = build_coverage(audited)
    text = render(coverage, local_date=local_date, tz=tz, now=now)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
