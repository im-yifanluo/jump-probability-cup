"""Audit exact current Polymarket market coverage for open manual questions.

This script is read-only. It does not submit predictions and does not read
stored bot predictions. Promoted direct anchors are already used by
`full_manual_pricing.py`; audit-only corner anchors are listed here so they can
be evaluated before any count-market promotion.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from full_manual_pricing import read_open_markets, selected_markets  # noqa: E402
from manual.polymarket_direct import (  # noqa: E402
    DirectMarketAnchor,
    count_prop_anchor_for_market,
    direct_anchor_for_market,
    fetch_polymarket_events,
    player_prop_anchor_for_market,
)
from price_match import build_pricer  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402
from zoneinfo import ZoneInfo


OUT = ROOT / "data" / "logs" / "polymarket_direct_coverage_audit.md"
SNAPSHOT_CSV = ROOT / "data" / "logs" / "polymarket_direct_anchor_snapshots.csv"


@dataclass(frozen=True)
class AuditRow:
    status: str
    market_id: str
    match_name: str
    kickoff: datetime
    family: str
    stat: str
    model_probability: int
    anchor_probability: int
    model_probability_raw: float
    anchor_probability_raw: float
    delta_pp: int
    volume: float
    question: str
    event_title: str
    market_question: str
    source: str
    outcome: str
    note: str


def audit_rows(
    rows,
    events: list[dict],
    *,
    direct_min_volume: float,
    player_min_volume: float,
    count_min_volume: float,
) -> list[AuditRow]:
    out: list[AuditRow] = []
    pricers = {}
    for row in rows:
        try:
            spec = parse(row.question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        anchor: DirectMarketAnchor | None = direct_anchor_for_market(
            row.match_name, spec, events, min_volume=direct_min_volume
        )
        status = "promoted_non_player" if anchor else ""
        if anchor is None:
            anchor = player_prop_anchor_for_market(row.match_name, spec, events, min_volume=player_min_volume)
            status = "promoted_player_prop" if anchor else ""
        if anchor is None:
            anchor = count_prop_anchor_for_market(row.match_name, spec, events, min_volume=count_min_volume)
            status = "promoted_count_corner" if anchor else ""
        if anchor is None:
            continue
        pricer = pricers.get(row.match_name)
        if pricer is None:
            pricer = build_pricer(
                row.match_name,
                row.kickoff,
                include_global_scorer_props=False,
                include_global_expected_minutes=False,
            )
            pricers[row.match_name] = pricer
        try:
            model_p = float(pricer.price(spec))
        except Exception:
            model_p = float(anchor.probability)
        model_pct = round(model_p * 100)
        anchor_pct = round(anchor.probability * 100)
        out.append(
            AuditRow(
                status=status,
                market_id=row.market_id,
                match_name=row.match_name,
                kickoff=row.kickoff,
                family=spec.family,
                stat=spec.stat or "",
                model_probability=model_pct,
                anchor_probability=anchor_pct,
                model_probability_raw=model_p,
                anchor_probability_raw=anchor.probability,
                delta_pp=anchor_pct - model_pct,
                volume=anchor.market_volume,
                question=row.question,
                event_title=anchor.event_title,
                market_question=anchor.market_question,
                source=anchor.source,
                outcome=anchor.outcome,
                note=anchor.note,
            )
        )
    return sorted(out, key=lambda item: (item.status, item.kickoff, item.match_name, item.question))


def render(
    rows: list[AuditRow],
    *,
    local_date: str,
    now: datetime,
    direct_min_volume: float,
    player_min_volume: float,
    count_min_volume: float,
) -> str:
    counts = defaultdict(int)
    for row in rows:
        counts[row.status] += 1
    lines = [
        "# Polymarket Direct Coverage Audit",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- local_date: {local_date}",
        "- bot_predictions: not_read",
        "- sports_predict: read_only open-market GETs only through the manual workflow helpers",
        f"- direct_min_market_volume: ${direct_min_volume:,.0f}",
        f"- player_min_market_volume: ${player_min_volume:,.0f}",
        f"- count_min_market_volume: ${count_min_volume:,.0f}",
        "- decision: exact current goal/player/corner anchors are handled by full_manual_pricing.py under their separate volume gates",
        "",
        "## Coverage Summary",
        "",
        "| status | markets |",
        "| --- | ---: |",
    ]
    for status in ("promoted_non_player", "promoted_player_prop", "promoted_count_corner"):
        lines.append(f"| {status} | {counts.get(status, 0)} |")
    lines.extend(
        [
            "",
            "## Matched Markets",
            "",
            "| status | match | kickoff_utc | model | anchor | delta_pp | volume | question | polymarket_question |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row.status} | {row.match_name} | {row.kickoff.astimezone(timezone.utc).isoformat(timespec='minutes')} | "
            f"{row.model_probability}% | {row.anchor_probability}% | {row.delta_pp:+d} | "
            f"${row.volume:,.0f} | {row.question} | {row.market_question} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def snapshot_records(
    rows: list[AuditRow],
    *,
    run_utc: datetime,
    local_date: str,
    direct_min_volume: float,
    player_min_volume: float,
    count_min_volume: float,
) -> list[dict[str, str]]:
    """Replay-ready row-level record of current direct-anchor decisions."""

    run_id = run_utc.astimezone(timezone.utc).isoformat(timespec="seconds")
    records = []
    for row in rows:
        records.append(
            {
                "run_utc": run_id,
                "local_date": local_date,
                "status": row.status,
                "market_id": row.market_id,
                "match": row.match_name,
                "kickoff_utc": row.kickoff.astimezone(timezone.utc).isoformat(timespec="minutes"),
                "family": row.family,
                "stat": row.stat,
                "model_probability": f"{row.model_probability_raw:.6f}",
                "anchor_probability": f"{row.anchor_probability_raw:.6f}",
                "delta_pp": str(row.delta_pp),
                "market_volume": f"{row.volume:.2f}",
                "sports_predict_question": row.question,
                "polymarket_event_title": row.event_title,
                "polymarket_question": row.market_question,
                "source": row.source,
                "outcome": row.outcome,
                "direct_min_volume": f"{direct_min_volume:.2f}",
                "player_min_volume": f"{player_min_volume:.2f}",
                "count_min_volume": f"{count_min_volume:.2f}",
            }
        )
    return records


def write_snapshot_csv(path: Path, records: list[dict[str, str]], *, append: bool = True) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(records[0])
    needs_header = not append or not path.exists() or path.stat().st_size == 0
    mode = "a" if append else "w"
    with path.open(mode, newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if needs_header:
            writer.writeheader()
        writer.writerows(records)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-date", help="Local date to audit, YYYY-MM-DD. Default: today in --tz.")
    ap.add_argument("--all-open", action="store_true", help="Audit all currently open future markets.")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--include-started", action="store_true", help="Include matches that have already kicked off.")
    ap.add_argument("--direct-min-volume", type=float, default=1000.0)
    ap.add_argument("--player-min-volume", type=float, default=1000.0)
    ap.add_argument("--count-min-volume", type=float, default=5000.0)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument(
        "--snapshot-csv",
        nargs="?",
        const=str(SNAPSHOT_CSV),
        help=f"append replay-ready matched-anchor rows to CSV; default path {SNAPSHOT_CSV}",
    )
    ap.add_argument("--snapshot-overwrite", action="store_true", help="overwrite --snapshot-csv instead of appending")
    args = ap.parse_args()

    tz = ZoneInfo(args.tz)
    now = datetime.now(timezone.utc)
    local_date = args.local_date or now.astimezone(tz).date().isoformat()
    all_rows = read_open_markets()
    if args.all_open:
        rows = [row for row in all_rows if args.include_started or row.kickoff >= now]
        local_date = "all-open"
    else:
        rows = selected_markets(
            all_rows,
            local_date=local_date,
            tz=tz,
            remaining_only=not args.include_started,
            now=now,
            matches=None,
        )
    events = fetch_polymarket_events()
    audited = audit_rows(
        rows,
        events,
        direct_min_volume=args.direct_min_volume,
        player_min_volume=args.player_min_volume,
        count_min_volume=args.count_min_volume,
    )
    text = render(
        audited,
        local_date=local_date,
        now=now,
        direct_min_volume=args.direct_min_volume,
        player_min_volume=args.player_min_volume,
        count_min_volume=args.count_min_volume,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")
    if args.snapshot_csv:
        snapshot_path = Path(args.snapshot_csv)
        records = snapshot_records(
            audited,
            run_utc=now,
            local_date=local_date,
            direct_min_volume=args.direct_min_volume,
            player_min_volume=args.player_min_volume,
            count_min_volume=args.count_min_volume,
        )
        write_snapshot_csv(snapshot_path, records, append=not args.snapshot_overwrite)
        mode = "overwrote" if args.snapshot_overwrite else "appended"
        print(f"{mode} {len(records)} rows to {snapshot_path}")


if __name__ == "__main__":
    main()
