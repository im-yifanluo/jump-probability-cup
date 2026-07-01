"""Calibration watchlist for manual Probability Cup entries.

This is a read-only guardrail over existing logs. It reads the calibration
backtest and the latest manual-pricing sheet, then highlights live rows that
fall in historically weak reliability buckets. It does not change recommended
probabilities: the walk-forward calibration audit rejects a global probability
transform, so this report is only an execution/research warning.

Usage:
    uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
        python scripts/manual_calibration_watchlist.py --all-open --write

Fallback over the latest logged daily sheet:
    python3 scripts/manual_calibration_watchlist.py --write
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
CALIBRATION_LOG = LOG_DIR / "manual_calibration_backtest.md"
PREDICTIONS_LOG = LOG_DIR / "manual_predictions.md"
OUT = LOG_DIR / "manual_calibration_watchlist.md"


@dataclass(frozen=True)
class ReliabilityBucket:
    policy: str
    bucket: str
    low: float
    high: float
    n: int
    mean_p: float
    hit: float
    abs_error: float
    brier: float

    @property
    def direction(self) -> str:
        if self.mean_p > self.hit:
            return "historically_overestimated"
        if self.hit > self.mean_p:
            return "historically_underestimated"
        return "historically_matched"


@dataclass(frozen=True)
class SheetRow:
    run_id: str
    match: str
    kickoff_local: str
    model_probability: int
    manual_entry: int
    policy: str
    flags: str
    question: str
    model_source: str


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _pct_to_int(value: str) -> int:
    m = re.search(r"(\d+)\s*%", value)
    if not m:
        raise ValueError(f"no percentage in {value!r}")
    return int(m.group(1))


def _prob_to_int(value: str | int | float) -> int:
    if isinstance(value, str):
        return _pct_to_int(value)
    return int(round(float(value)))


def _bucket_bounds(label: str) -> tuple[float, float]:
    low, high = label.split("-", 1)
    return float(low), float(high)


def _table_after(text: str, heading: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    target = f"## {heading}"
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == target:
            start = idx + 1
            break
    if start is None:
        return []
    while start < len(lines) and not lines[start].lstrip().startswith("|"):
        start += 1
    if start >= len(lines):
        return []
    headers = [_clean(part) for part in lines[start].strip().strip("|").split("|")]
    rows = []
    for line in lines[start + 2 :]:
        if not line.lstrip().startswith("|"):
            break
        parts = [_clean(part) for part in line.strip().strip("|").split("|")]
        if len(parts) == len(headers):
            rows.append(dict(zip(headers, parts)))
    return rows


def load_reliability_buckets(path: Path = CALIBRATION_LOG) -> list[ReliabilityBucket]:
    rows = _table_after(path.read_text(encoding="utf-8"), "Full-Sample Reliability By Bucket")
    out = []
    for row in rows:
        if row.get("policy") != "combined_manual_policy":
            continue
        low, high = _bucket_bounds(row["bucket"])
        out.append(
            ReliabilityBucket(
                policy=row["policy"],
                bucket=row["bucket"],
                low=low,
                high=high,
                n=int(row["n"]),
                mean_p=float(row["mean_p"]),
                hit=float(row["hit"]),
                abs_error=float(row["abs_error"]),
                brier=float(row["brier"]),
            )
        )
    return out


def flagged_buckets(
    buckets: list[ReliabilityBucket],
    *,
    min_rows: int = 10,
    medium_abs_error: float = 0.05,
) -> list[ReliabilityBucket]:
    return [
        bucket
        for bucket in buckets
        if bucket.n >= min_rows and bucket.abs_error >= medium_abs_error
    ]


def severity(bucket: ReliabilityBucket, *, high_abs_error: float = 0.075) -> str:
    if bucket.abs_error >= high_abs_error:
        return "HIGH"
    return "MEDIUM"


def latest_pricing_block(text: str) -> tuple[str, str]:
    matches = list(re.finditer(r"^## ([^\n]+) full_manual_pricing_v2\b.*$", text, flags=re.MULTILINE))
    if not matches:
        raise ValueError("manual_predictions.md has no full_manual_pricing_v2 block")
    start = matches[-1].start()
    end = matches[-1 + 1].start() if False else len(text)
    run_id = matches[-1].group(1).strip()
    return run_id, text[start:end]


def parse_latest_sheet(path: Path = PREDICTIONS_LOG) -> list[SheetRow]:
    run_id, block = latest_pricing_block(path.read_text(encoding="utf-8"))
    rows: list[SheetRow] = []
    current_match = ""
    kickoff_local = ""
    lines = block.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        match = re.match(r"^### (.+)$", line)
        if match:
            current_match = match.group(1).strip()
            kickoff_local = ""
            idx += 1
            continue
        if line.startswith("- kickoff_local:"):
            kickoff_local = line.split(":", 1)[1].strip()
            idx += 1
            continue
        if line.startswith("| model_probability |"):
            idx += 2
            while idx < len(lines) and lines[idx].lstrip().startswith("|"):
                parts = [_clean(part) for part in lines[idx].strip().strip("|").split("|")]
                if len(parts) == 7:
                    rows.append(
                        SheetRow(
                            run_id=run_id,
                            match=current_match,
                            kickoff_local=kickoff_local,
                            model_probability=_pct_to_int(parts[0]),
                            manual_entry=_pct_to_int(parts[1]),
                            policy=parts[2],
                            flags="" if parts[3] == "-" else parts[3],
                            question=parts[4],
                            model_source=parts[5],
                        )
                    )
                idx += 1
            continue
        idx += 1
    return rows


def sheet_rows_from_priced_rows(priced_rows: list[dict], *, run_id: str, tz: ZoneInfo) -> list[SheetRow]:
    rows: list[SheetRow] = []
    for row in priced_rows:
        kickoff = row["kickoff"].astimezone(tz)
        rows.append(
            SheetRow(
                run_id=run_id,
                match=row["match_name"],
                kickoff_local=f"{kickoff:%Y-%m-%d %H:%M %Z}",
                model_probability=_prob_to_int(row["probability"]),
                manual_entry=_prob_to_int(row["manual_entry"]),
                policy=row["manual_policy"],
                flags=row.get("flags", ""),
                question=row["question"],
                model_source=row.get("model", ""),
            )
        )
    return rows


def live_pricing_sheet(
    *,
    all_open: bool,
    local_date: str | None,
    tz_name: str,
    now: datetime | None = None,
) -> tuple[list[SheetRow], dict[str, str | None]]:
    """Build a current read-only pricing sheet for calibration guardrails."""

    import sys

    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "scripts"))

    from full_manual_pricing import (  # noqa: WPS433
        current_player_no_field_policy,
        price_rows,
        read_open_markets,
        refresh_lineups,
        selected_markets,
    )
    from manual.polymarket_direct import fetch_polymarket_events  # noqa: WPS433

    tz = ZoneInfo(tz_name)
    now = now or datetime.now(timezone.utc)
    open_rows = read_open_markets()
    if all_open:
        grace = timedelta(minutes=10)
        selected = [
            row
            for row in open_rows
            if row.kickoff >= now.astimezone(timezone.utc) - grace
        ]
        source_context = "all-open"
    else:
        source_context = local_date or now.astimezone(tz).date().isoformat()
        selected = selected_markets(
            open_rows,
            local_date=source_context,
            tz=tz,
            remaining_only=True,
            now=now,
            matches=None,
        )

    selected = sorted(selected, key=lambda row: (row.kickoff, row.match_name, row.market_id))
    lineups = refresh_lineups(selected, write=False)
    player_no_field_policy = current_player_no_field_policy()
    polymarket_events = fetch_polymarket_events()
    priced, _diagnostics = price_rows(
        selected,
        lineups=lineups,
        field_by_market={},
        player_no_field_base=player_no_field_policy.base,
        polymarket_events=polymarket_events,
    )
    return sheet_rows_from_priced_rows(
        priced,
        run_id=now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        tz=tz,
    ), {
        "source": "live_full_manual_pricing",
        "source_context": source_context,
        "sports_predict": "read_only GET /events, /lobbies, /matches, /markets, /results only",
        "selected_markets": str(len(selected)),
    }


def bucket_for_probability(probability: int, buckets: list[ReliabilityBucket]) -> ReliabilityBucket | None:
    p = probability / 100.0
    for bucket in buckets:
        if bucket.low <= p < bucket.high:
            return bucket
    return None


def build_watchlist(
    sheet_rows: list[SheetRow],
    buckets: list[ReliabilityBucket],
    *,
    include_medium: bool = True,
) -> list[dict]:
    weak_buckets = flagged_buckets(buckets)
    out = []
    for row in sheet_rows:
        bucket = bucket_for_probability(row.manual_entry, weak_buckets)
        if bucket is None:
            continue
        sev = severity(bucket)
        if sev == "MEDIUM" and not include_medium:
            continue
        out.append(
            {
                "severity": sev,
                "bucket": bucket,
                "row": row,
            }
        )
    return sorted(
        out,
        key=lambda item: (
            0 if item["severity"] == "HIGH" else 1,
            item["row"].kickoff_local,
            item["row"].match,
            item["row"].question,
        ),
    )


def recommended_action(item: dict) -> str:
    bucket: ReliabilityBucket = item["bucket"]
    row: SheetRow = item["row"]
    if "current_polymarket_direct" in row.model_source or "current_player_prop_anchor" in row.model_source:
        return "keep exact current anchor; verify wording/volume only"
    if "CONFIRMED_BENCH" in row.flags:
        return "lineup already adjusted; do not move back toward pre-lineup model"
    if bucket.direction == "historically_overestimated":
        return "do not push higher; recheck anchor, prop, lineup, wording, or source quality"
    if bucket.direction == "historically_underestimated":
        return "do not shrink blindly; verify source quality before moving lower"
    return "monitor; no automatic calibration change"


def render(
    watchlist: list[dict],
    buckets: list[ReliabilityBucket],
    *,
    now: datetime,
    include_medium: bool,
    source_label: str = "data/logs/manual_predictions.md latest full_manual_pricing_v2 block",
    source_context: str = "latest-log",
    sports_predict: str = "not_called",
    selected_markets: str | int | None = None,
) -> str:
    counts = Counter(item["severity"] for item in watchlist)
    bucket_counts = Counter(item["bucket"].bucket for item in watchlist)
    lines = [
        "# Manual Calibration Watchlist",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- bot_predictions: not_read",
        f"- sports_predict: {sports_predict}",
        f"- source_predictions: {source_label}",
        f"- source_context: {source_context}",
        "- source_calibration_log: data/logs/manual_calibration_backtest.md",
        "- decision: guardrail_only; do not apply a global calibration transform",
        f"- include_medium: {str(include_medium).lower()}",
    ]
    if selected_markets is not None:
        lines.append(f"- selected_markets: {selected_markets}")
    lines.extend(
        [
            "",
            "## Flagged Historical Buckets",
            "",
            "| bucket | n | mean_p | hit | abs_error | direction | severity |",
            "| --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for bucket in flagged_buckets(buckets):
        lines.append(
            f"| {bucket.bucket} | {bucket.n} | {bucket.mean_p:.3f} | {bucket.hit:.3f} | "
            f"{bucket.abs_error:.3f} | {bucket.direction} | {severity(bucket)} |"
        )
    lines.extend(
        [
            "",
            "## Current Watchlist Summary",
            "",
            "| severity | rows |",
            "| --- | ---: |",
        ]
    )
    for sev in ("HIGH", "MEDIUM"):
        lines.append(f"| {sev} | {counts.get(sev, 0)} |")
    lines.extend(["", "| calibration_bucket | rows |", "| --- | ---: |"])
    for bucket, count in sorted(bucket_counts.items()):
        lines.append(f"| {bucket} | {count} |")

    lines.extend(
        [
            "",
            "## Current Watchlist Rows",
            "",
            "| severity | bucket | historical_error | direction | match | kickoff_local | entry | policy | question | action |",
            "| --- | --- | ---: | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for item in watchlist:
        bucket: ReliabilityBucket = item["bucket"]
        row: SheetRow = item["row"]
        lines.append(
            f"| {item['severity']} | {bucket.bucket} | {bucket.abs_error:.1%} | {bucket.direction} | "
            f"{row.match} | {row.kickoff_local} | {row.manual_entry}% | {row.policy} | "
            f"{row.question} | {recommended_action(item)} |"
        )

    by_match: dict[str, list[dict]] = defaultdict(list)
    for item in watchlist:
        by_match[item["row"].match].append(item)
    if by_match:
        lines.extend(["", "## Match Checklist", ""])
        for match, items in by_match.items():
            lines.extend([f"### {match}", ""])
            for item in items:
                bucket: ReliabilityBucket = item["bucket"]
                row: SheetRow = item["row"]
                lines.append(
                    f"- [{item['severity']}] {row.manual_entry}% in {bucket.bucket} "
                    f"({bucket.direction}, abs_error={bucket.abs_error:.1%}): "
                    f"{recommended_action(item)} -- {row.question}"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument("--high-only", action="store_true", help="Suppress medium-severity bucket warnings.")
    ap.add_argument("--all-open", action="store_true", help="Build the watchlist from all current future open markets.")
    ap.add_argument("--local-date", help="Build the watchlist from current read-only pricing for one local date, YYYY-MM-DD.")
    ap.add_argument("--tz", default="America/Chicago")
    args = ap.parse_args()

    buckets = load_reliability_buckets()
    source_meta: dict[str, str | None] = {
        "source": "manual_predictions_log",
        "source_context": "latest-log",
        "sports_predict": "not_called",
        "selected_markets": None,
    }
    if args.all_open or args.local_date:
        sheet, source_meta = live_pricing_sheet(
            all_open=args.all_open,
            local_date=args.local_date,
            tz_name=args.tz,
        )
    else:
        sheet = parse_latest_sheet()
    watchlist = build_watchlist(sheet, buckets, include_medium=not args.high_only)
    text = render(
        watchlist,
        buckets,
        now=datetime.now(timezone.utc),
        include_medium=not args.high_only,
        source_label=str(source_meta["source"]),
        source_context=str(source_meta["source_context"]),
        sports_predict=str(source_meta["sports_predict"]),
        selected_markets=source_meta.get("selected_markets"),
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
