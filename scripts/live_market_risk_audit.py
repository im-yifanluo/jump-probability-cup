"""Read-only live market risk audit for manual Probability Cup entries.

This is an execution-control report, not a prediction submission workflow. It
uses the same independent pricing stack as `full_manual_pricing.py`, then
classifies each open market by what still has to be checked before manual lock:
field average, lineup/player research, weak special prior, or odds refresh.

It does not read `/predictions` and never submits.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from full_manual_pricing import (  # noqa: E402
    current_player_no_field_policy,
    field_coverage,
    _player_no_field_policy_line,
    _player_prop_anchor_line,
    price_rows,
    read_field_csv,
    read_open_markets,
    refresh_lineups,
    selected_markets,
)
from manual.policy import (  # noqa: E402
    is_count_market,
    is_odds_goal_market,
    is_special_market,
    is_strong_sot_count,
    unresolved_player_flags,
)
from manual.player_prop_anchors import read_player_prop_anchor_csv  # noqa: E402
from manual.polymarket_direct import fetch_polymarket_events  # noqa: E402
from player_role_research_audit import load_role_summary  # noqa: E402
from questions.parser import MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402
from special_market_live_audit import (  # noqa: E402
    SpecialEvidence,
    evidence_table as special_evidence_table,
    risk_state as special_proxy_risk_state,
    special_evidence_key,
)
from special_market_backtest import rows as statsbomb_special_rows  # noqa: E402


OUT = ROOT / "data" / "logs" / "live_market_risk_audit.md"
FIELD_TEMPLATE = ROOT / "data" / "field_averages_template.csv"

TIER_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
FIELD_TEMPLATE_MODES = {"actionable", "all"}
PLAYER_LINEUP_FLAGS = {
    "CONFIRMED_BENCH",
    "LINEUP_NOT_POSTED",
    "LINEUP_NOT_POSTED_NEAR_KICKOFF",
    "SPARSE_HISTORY",
}
ROLE_RISK_CLASSES = {"bench_or_rotation_risk", "rotation_risk"}


@dataclass(frozen=True)
class MarketRisk:
    tier: str
    action: str
    evidence: str


@dataclass(frozen=True)
class SpecialProxyContext:
    risk_state: str
    evidence_key: str
    confidence: str
    heterogeneity: str
    hit_rate: float
    model_minus_base_pp: int


def _flag_set(value: str | None) -> set[str]:
    if not value or value == "-":
        return set()
    return {part for part in value.split(",") if part}


@lru_cache(maxsize=1)
def _special_evidence() -> dict[str, SpecialEvidence]:
    return special_evidence_table(statsbomb_special_rows())


def _model_probability(priced_row: dict) -> float:
    if priced_row.get("p_raw") is not None:
        return float(priced_row["p_raw"])
    if priced_row.get("probability") is not None:
        return float(priced_row["probability"]) / 100.0
    entry = str(priced_row.get("manual_entry", "")).strip().removesuffix("%")
    if entry:
        try:
            return float(entry) / 100.0
        except ValueError:
            pass
    return 0.5


def special_proxy_context(spec: MarketSpec, priced_row: dict) -> SpecialProxyContext | None:
    key = special_evidence_key(spec)
    evidence = _special_evidence().get(key or "")
    if evidence is None:
        return None
    model_probability = _model_probability(priced_row)
    return SpecialProxyContext(
        risk_state=special_proxy_risk_state(evidence, model_probability=model_probability),
        evidence_key=key or "",
        confidence=evidence.confidence,
        heterogeneity=evidence.heterogeneity,
        hit_rate=evidence.hit_rate,
        model_minus_base_pp=round((model_probability - evidence.hit_rate) * 100),
    )


def _special_risk(spec: MarketSpec, priced_row: dict) -> MarketRisk:
    ctx = special_proxy_context(spec, priced_row)
    if ctx is None:
        return MarketRisk(
            "HIGH",
            "map special question to evidence or do targeted research",
            "special market is not covered by the StatsBomb proxy audit",
        )
    base = f"{ctx.evidence_key} base={ctx.hit_rate:.1%}, model_minus_base={ctx.model_minus_base_pp:+d}pp"
    if ctx.risk_state == "WEAK_PRIOR":
        return MarketRisk(
            "MEDIUM",
            "use raw weak prior; treat large current-source disagreement as a research flag",
            f"{base}; weak_near_coinflip StatsBomb LOTO prior",
        )
    if ctx.risk_state == "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE":
        return MarketRisk(
            "HIGH",
            "verify count-volume inputs and current count sources before trusting raw special model",
            f"{base}; count-threshold proxy is far from no-match-info base and not ex-ante volume-validated",
        )
    if ctx.risk_state == "COUNT_THRESHOLD_HIGH_HETEROGENEITY":
        return MarketRisk(
            "MEDIUM",
            "verify count-volume inputs and current total-shots sources before lock",
            f"{base}; count-threshold proxy has high cross-tournament heterogeneity",
        )
    if ctx.risk_state == "COUNT_THRESHOLD_PROXY":
        return MarketRisk(
            "MEDIUM",
            "verify current count-volume input if time allows",
            f"{base}; threshold prior is a no-match-info StatsBomb proxy",
        )
    if ctx.risk_state == "HIGH_HETEROGENEITY_PROXY":
        return MarketRisk(
            "MEDIUM",
            "use raw special model, but require direct current evidence for strong confidence",
            f"{base}; usable prior with high cross-tournament heterogeneity",
        )
    if ctx.risk_state == "SCALED_FAR_FROM_BASE":
        return MarketRisk(
            "MEDIUM",
            "recheck match-volume inputs before trusting raw special model",
            f"{base}; live probability is far from the no-match-info base prior",
        )
    return MarketRisk(
        "MEDIUM",
        "use raw special model; do not apply generic field transform",
        f"{base}; StatsBomb special-proxy tail replay favors raw special priors over generic field transforms",
    )


def risk_for_market(spec: MarketSpec, priced_row: dict, *, now: datetime) -> MarketRisk:
    """Classify live operational risk from the validated manual policy."""

    flags = _flag_set(priced_row.get("flags"))
    field_missing = priced_row.get("field") is None
    kickoff = priced_row["kickoff"]
    near_kickoff = kickoff - now.astimezone(timezone.utc) <= timedelta(minutes=100)

    unresolved = unresolved_player_flags(flags)
    if spec.player and unresolved:
        return MarketRisk(
            "HIGH",
            "clear current prop/lineup research; field is unavailable before lock",
            "player-prop backtests show this is the largest leak; unresolved flags need exact prop or lineup research",
        )
    if spec.player and "CONFIRMED_BENCH" in flags:
        return MarketRisk(
            "HIGH",
            "verify pulled minutes and avoid model-above-field",
            "bench/not-listed player rows are mandatory research flags",
        )
    if spec.player and near_kickoff and "LINEUP_NOT_POSTED" in flags:
        return MarketRisk(
            "HIGH",
            "wait for lineup if possible; otherwise keep the no-field player policy",
            "lineup is missing inside the final pre-match window",
        )
    role_class = str(priced_row.get("player_role_class") or "")
    if spec.player and role_class in ROLE_RISK_CLASSES:
        recent_roles = priced_row.get("player_recent_roles") or "-"
        return MarketRisk(
            "MEDIUM",
            "use exact prop odds unless final XI confirms starter role",
            f"recent ESPN role audit: {role_class}; recent_roles={recent_roles}",
        )
    if spec.player and (flags & PLAYER_LINEUP_FLAGS):
        return MarketRisk(
            "MEDIUM",
            "recheck lineup and current prop market before lock",
            "researched player props use no-field/current-source policy unless exact prop or lineup evidence clears the flags",
        )

    if is_count_market(spec) and spec.stat == "cards":
        if field_missing:
            return MarketRisk(
                "HIGH",
                "field average unavailable before lock; use no-field card value and carry lower confidence",
                "safe-settlement count backtest: historical card-field branch is not actionable pre-lock",
            )
        return MarketRisk(
            "LOW",
            "field supplied; card-count policy is already applied",
            "card-count backtest favors field over raw model",
        )

    if is_odds_goal_market(spec):
        return MarketRisk(
            "LOW",
            "use raw odds-anchored model; rerun only if public odds move",
            "goal/outcome backtest rejects field transforms and keeps the odds model",
        )

    if is_count_market(spec) and is_strong_sot_count(spec):
        return MarketRisk(
            "LOW",
            "use raw strong-SOT count model; field is unavailable before lock",
            "count-policy backtest promotes raw model for team/head-to-head SOT markets",
        )

    if is_special_market(spec):
        return _special_risk(spec, priced_row)

    if spec.family in {"penalty_awarded", "penalty_or_red", "red_card"}:
        return MarketRisk(
            "MEDIUM",
            "use base-rate model; avoid pushing above base without direct current evidence",
            "penalty/red sample is small and treated as a low-event prior",
        )

    if is_count_market(spec):
        if field_missing:
            return MarketRisk(
                "MEDIUM",
                "field average unavailable before lock; use no-field count value and carry lower confidence",
                "historical count-field branch is not actionable pre-lock",
            )
        return MarketRisk(
            "LOW",
            "field supplied; count field policy is already applied",
            "manual_decision applied the validated global asymmetric field policy",
        )

    return MarketRisk(
        "MEDIUM",
        "review parser/model coverage before manual entry",
        "market does not map to a stronger promoted evidence bucket",
    )


def _with_role_context(row: dict, role_summary: dict[str, dict[str, str]]) -> dict:
    role = role_summary.get(row.get("market_id", ""))
    if not role:
        return row
    return {
        **row,
        "player_role_class": role.get("role_class", ""),
        "player_recent_roles": role.get("recent_roles", ""),
        "player_role_action": role.get("action", ""),
    }


def audit_priced_rows(
    priced_rows: list[dict],
    *,
    now: datetime,
    role_summary: dict[str, dict[str, str]] | None = None,
) -> list[dict]:
    role_summary = role_summary or {}
    out = []
    for row in priced_rows:
        row = _with_role_context(row, role_summary)
        spec = parse(row["question"], teams=ALL_TEAM_NAMES)
        risk = risk_for_market(spec, row, now=now)
        risk_evidence = risk.evidence
        if spec.player and row.get("player_role_class") and "recent ESPN role audit" not in risk_evidence:
            risk_evidence = (
                f"{risk_evidence}; recent_role={row.get('player_role_class')}"
                f" ({row.get('player_recent_roles') or '-'})"
            )
        audited = {
            **row,
            "risk_tier": risk.tier,
            "risk_action": risk.action,
            "risk_evidence": risk_evidence,
        }
        special_ctx = special_proxy_context(spec, row) if is_special_market(spec) else None
        if special_ctx:
            audited.update(
                {
                    "special_risk_state": special_ctx.risk_state,
                    "special_evidence_key": special_ctx.evidence_key,
                    "special_model_minus_base_pp": special_ctx.model_minus_base_pp,
                }
            )
        out.append(audited)
    return sorted(
        out,
        key=lambda r: (
            TIER_ORDER.get(r["risk_tier"], 99),
            r["kickoff"],
            r["match_name"],
            r["question"],
        ),
    )


def render(
    rows: list[dict],
    *,
    local_date: str,
    tz: ZoneInfo,
    now: datetime,
    field_info,
    player_prop_anchors,
    player_no_field_policy,
    polymarket_direct_enabled: bool = False,
    polymarket_direct_min_volume: float = 1000.0,
    polymarket_player_enabled: bool = False,
    polymarket_player_min_volume: float = 1000.0,
    polymarket_count_enabled: bool = False,
    polymarket_count_min_volume: float = 5000.0,
) -> str:
    run_id = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    counts = Counter(row["risk_tier"] for row in rows)
    by_match: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_match[row["match_name"]].append(row)

    lines = [
        "# Live Market Risk Audit",
        "",
        f"- run_utc: {run_id}",
        f"- local_date: {local_date}",
        "- bot_predictions: not_read",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets, /results only",
        "- purpose: identify live execution risks before manual entry, not submit predictions",
        _polymarket_direct_line(rows, enabled=polymarket_direct_enabled, min_volume=polymarket_direct_min_volume),
        _polymarket_player_line(rows, enabled=polymarket_player_enabled, min_volume=polymarket_player_min_volume),
        _polymarket_count_line(rows, enabled=polymarket_count_enabled, min_volume=polymarket_count_min_volume),
        _field_coverage_line(field_info),
        _player_prop_anchor_line(rows, player_prop_anchors),
        _player_no_field_policy_line(player_no_field_policy),
        "",
        "## Risk Summary",
        "",
        "| tier | markets |",
        "| --- | ---: |",
    ]
    for tier in ("HIGH", "MEDIUM", "LOW"):
        lines.append(f"| {tier} | {counts.get(tier, 0)} |")
    lines.extend(["", "## Markets", ""])

    for match_name, match_rows in by_match.items():
        kickoff = match_rows[0]["kickoff"].astimezone(tz)
        lines.extend(
            [
                f"### {match_name}",
                "",
                f"- kickoff_local: {kickoff:%Y-%m-%d %H:%M %Z}",
                "",
                "| risk | model | manual_entry | policy | flags | action | question | evidence |",
                "| --- | ---: | ---: | --- | --- | --- | --- | --- |",
            ]
        )
        for row in match_rows:
            role_note = ""
            if row.get("player_role_class"):
                role_note = f"; role={row.get('player_role_class')} ({row.get('player_recent_roles') or '-'})"
            lines.append(
                f"| {row['risk_tier']} | {row['probability']}% | {row['manual_entry']} | {row['manual_policy']} | "
                f"{(row['flags'] or '-') + role_note} | {row['risk_action']} | {row['question']} | "
                f"{row['risk_evidence']} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_field_template(rows: list[dict], path: Path) -> None:
    """Write a diagnostic exact field-average CSV template.

    Current SportsPredict UI does not expose field averages before prediction
    lock, so this template is not a pre-lock input. It remains useful if the UI
    behavior changes or for post-lock/historical diagnostics.
    """

    write_field_template_rows(rows, path, mode="actionable")


def field_template_rows(rows: list[dict], *, mode: str = "actionable") -> list[dict]:
    """Rows that would appear in a diagnostic field-average CSV.

    `actionable` keeps only markets where a visible SportsPredict field average
    would change the documented manual policy if it were visible before lock.
    It deliberately excludes raw
    odds-goal rows, raw special priors, and strong team/head-to-head SOT count
    rows because the validated policy does not use field values there.
    """

    if mode not in FIELD_TEMPLATE_MODES:
        raise ValueError(f"field template mode must be one of {sorted(FIELD_TEMPLATE_MODES)}")
    if mode == "all":
        return list(rows)

    out = []
    for row in rows:
        spec = parse(row["question"], teams=ALL_TEAM_NAMES)
        if is_odds_goal_market(spec) or is_special_market(spec):
            continue
        if is_count_market(spec) and is_strong_sot_count(spec):
            continue
        if spec.player or is_count_market(spec):
            out.append(row)
            continue
        if spec.family in {"penalty_awarded", "penalty_or_red", "red_card"}:
            out.append(row)
            continue
        if (row.get("manual_policy") or "").endswith("_when_field_visible"):
            out.append(row)
    return out


def write_field_template_rows(rows: list[dict], path: Path, *, mode: str = "actionable") -> None:
    """Write a field-average CSV in either actionable-only or all-row mode."""

    selected_rows = field_template_rows(rows, mode=mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "market_id",
        "match",
        "kickoff_utc",
        "risk_tier",
        "model_probability",
        "manual_entry",
        "manual_policy",
        "question",
        "field",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in selected_rows:
            writer.writerow(
                {
                    "market_id": row["market_id"],
                    "match": row["match_name"],
                    "kickoff_utc": row["kickoff"].astimezone(timezone.utc).isoformat(timespec="minutes"),
                    "risk_tier": row["risk_tier"],
                    "model_probability": f"{row['probability']}%",
                    "manual_entry": row["manual_entry"],
                    "manual_policy": row["manual_policy"],
                    "question": row["question"],
                    "field": "",
                    "notes": "",
                }
            )


def _field_coverage_line(info) -> str:
    if not info.supplied:
        return "- field_csv: not_supplied; field averages are unavailable before lock, so field-led rows are low-confidence no-field entries"
    return (
        "- field_csv: supplied "
        f"selected={info.selected} csv_rows={info.supplied_rows} matched={info.matched} "
        f"missing={len(info.missing)} unused={len(info.unused_ids)}"
    )


def _polymarket_direct_line(rows: list[dict], *, enabled: bool, min_volume: float) -> str:
    if not enabled:
        return "- polymarket_direct: disabled"
    applied = sum(1 for row in rows if row.get("direct_market_anchor") is not None)
    return (
        "- polymarket_direct: enabled "
        f"min_market_volume=${min_volume:,.0f} applied={applied}; "
        "same exact-market gate as full_manual_pricing.py"
    )


def _polymarket_player_line(rows: list[dict], *, enabled: bool, min_volume: float) -> str:
    if not enabled:
        return "- polymarket_player_props: disabled"
    applied = sum(1 for row in rows if row.get("polymarket_player_anchor") is not None)
    return (
        "- polymarket_player_props: enabled "
        f"min_market_volume=${min_volume:,.0f} applied={applied}; "
        "same exact player/family/threshold gate as full_manual_pricing.py"
    )


def _polymarket_count_line(rows: list[dict], *, enabled: bool, min_volume: float) -> str:
    if not enabled:
        return "- polymarket_count_direct: disabled"
    applied = sum(1 for row in rows if row.get("polymarket_count_anchor") is not None)
    return (
        "- polymarket_count_direct: enabled "
        f"min_market_volume=${min_volume:,.0f} applied={applied}; "
        "same exact corner type/threshold gate as full_manual_pricing.py"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-date", help="Local date to audit, YYYY-MM-DD. Default: today in --tz.")
    ap.add_argument("--all-open", action="store_true", help="Audit all currently open future markets.")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--include-started", action="store_true", help="Include matches that have already kicked off.")
    ap.add_argument("--match", action="append", help="Restrict to an exact SportsPredict match name. Repeatable.")
    ap.add_argument("--field-csv", help="Optional CSV with market_id,field columns from visible UI field averages.")
    ap.add_argument("--player-prop-csv", help="Optional exact current player-prop anchors CSV.")
    ap.add_argument("--no-polymarket-direct", action="store_true", help="Disable exact current Polymarket anchors for eligible non-player markets.")
    ap.add_argument("--polymarket-direct-min-volume", type=float, default=1000.0, help="Minimum individual Polymarket market volume for direct non-player anchors.")
    ap.add_argument("--no-polymarket-player-props", action="store_true", help="Disable exact current Polymarket anchors for eligible player props.")
    ap.add_argument("--polymarket-player-min-volume", type=float, default=1000.0, help="Minimum individual Polymarket market volume for exact player-prop anchors.")
    ap.add_argument("--no-polymarket-count-direct", action="store_true", help="Disable exact current Polymarket anchors for eligible corner count markets.")
    ap.add_argument("--polymarket-count-min-volume", type=float, default=5000.0, help="Minimum individual Polymarket market volume for exact corner count anchors.")
    ap.add_argument("--lineup-write", action="store_true", help="Persist pulled ESPN lineup minutes if lineups are posted.")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument(
        "--field-template",
        nargs="?",
        const=str(FIELD_TEMPLATE),
        help=f"write exact field-average CSV template; default path {FIELD_TEMPLATE}",
    )
    ap.add_argument(
        "--field-template-mode",
        choices=sorted(FIELD_TEMPLATE_MODES),
        default="actionable",
        help="which rows to include in --field-template; actionable excludes rows where field is not part of the validated policy",
    )
    args = ap.parse_args()

    tz = ZoneInfo(args.tz)
    now = datetime.now(timezone.utc)
    local_date = args.local_date or now.astimezone(tz).date().isoformat()
    matches = set(args.match) if args.match else None
    all_rows = read_open_markets()
    if args.all_open:
        grace = timedelta(minutes=10)
        rows = [
            row for row in all_rows
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
    text = render(
        audited,
        local_date=local_date,
        tz=tz,
        now=now,
        field_info=field_coverage(rows, field_by_market),
        player_prop_anchors=player_prop_anchors,
        player_no_field_policy=player_no_field_policy,
        polymarket_direct_enabled=not args.no_polymarket_direct,
        polymarket_direct_min_volume=args.polymarket_direct_min_volume,
        polymarket_player_enabled=not args.no_polymarket_player_props,
        polymarket_player_min_volume=args.polymarket_player_min_volume,
        polymarket_count_enabled=not args.no_polymarket_count_direct,
        polymarket_count_min_volume=args.polymarket_count_min_volume,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")
    if args.field_template:
        template_path = Path(args.field_template)
        write_field_template_rows(audited, template_path, mode=args.field_template_mode)
        print(f"wrote {template_path} mode={args.field_template_mode}")


if __name__ == "__main__":
    main()
