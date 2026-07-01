"""Player-prop diagnostics for the manual Probability Cup workflow.

This is read-only. It does not read `/predictions` and does not submit. It
separates:

1. settled-history player-family performance from docs/history.md, and
2. current open player-prop operational risk: parser name, model source,
   team-side mapping, expected minutes, lineup status, and stale override flags.

Usage:
    uv run --with pandas --with numpy --with scipy --with pyarrow --with pyyaml --with requests --with statsmodels \
      python scripts/player_prop_diagnostics.py --sports-predict --write
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import yaml

from backtest.history import brier, exact_rows, load_history, settle_v1_rows, unique_outcomes_by_question
from manual.anchors import fetch_sports_predict_open_markets, sp_get, sports_predict_key
from full_manual_pricing import PlayerNoFieldPolicy, _flags_after_current_prop_anchor, current_player_no_field_policy
from manual.policy import manual_decision, unresolved_player_flags
from manual.player_prop_anchors import PlayerPropAnchorInspection, inspect_player_prop_anchor_csv
from manual.polymarket_direct import (
    DirectMarketAnchor,
    PlayerPropCandidate,
    fetch_polymarket_events,
    inspect_player_prop_candidates,
    player_prop_anchor_for_market,
)
from models.players import PlayerModel
from price_match import build_pricer
from pull_lineups import pull_lineup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES
from wcdata.statsbomb import build_aggregates


OUT = ROOT / "data" / "logs" / "player_prop_diagnostics.md"
ANCHOR_TEMPLATE = ROOT / "data" / "player_prop_anchors_template.csv"
HISTORY = ROOT / "docs" / "history.md"
OVERRIDES = ROOT / "data" / "player_overrides.yaml"
MATCH_LINEUPS = ROOT / "data" / "match_lineups.yaml"

FORCE_RESEARCH_FLAGS = {
    "CONFIRMED_BENCH",
    "LINEUP_UNMATCHED_DEFAULT_90",
}
MEDIUM_RESEARCH_FLAGS = {
    "LINEUP_NOT_POSTED",
    "LINEUP_NOT_POSTED_NEAR_KICKOFF",
    "SPARSE_HISTORY",
}


@dataclass(frozen=True)
class OpenMarket:
    match_name: str
    kickoff: datetime
    market_id: str
    question: str


def _read_only_outcomes_from_sports_predict() -> dict[str, int]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SportsPredict key found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    results = sp_get(f"/results?lobby_id={lobby['id']}", key)
    return unique_outcomes_by_question(results)


def settled_player_rows(*, sports_predict: bool) -> list:
    rows = load_history(HISTORY)
    if sports_predict:
        rows = settle_v1_rows(rows, _read_only_outcomes_from_sports_predict())
    return [row for row in exact_rows(rows) if str(row.family).startswith("player_")]


def family_history_table(rows) -> list[dict]:
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row.famkey].append(row)
    out = []
    for fam, part in sorted(grouped.items()):
        n = len(part)
        skill = sum(
            100 * (((row.crowd - (row.outcome or 0)) ** 2) - ((row.you - (row.outcome or 0)) ** 2))
            for row in part
        )
        out.append(
            {
                "family": fam,
                "n": n,
                "rbp": sum(row.official_rbp or 0.0 for row in part),
                "skill_vs_field": skill,
                "brier_you": sum((row.you - (row.outcome or 0)) ** 2 for row in part) / n,
                "brier_field": sum((row.crowd - (row.outcome or 0)) ** 2 for row in part) / n,
                "you": sum(row.you for row in part) / n,
                "field": sum(row.crowd for row in part) / n,
                "hit": sum(row.outcome or 0 for row in part) / n,
            }
        )
    return sorted(out, key=lambda r: r["skill_vs_field"])


def famkey(spec) -> str:
    out = spec.family
    if spec.stat:
        out += f"/{spec.stat}"
    if spec.period and spec.period != "match":
        out += f"/{spec.period}"
    return out


def _flag_set(value: str | None) -> set[str]:
    if not value or value == "OK":
        return set()
    return {part for part in value.split(",") if part}


def family_skill_per_row(family_rows: list[dict]) -> dict[str, float]:
    out = {}
    for row in family_rows:
        n = int(row.get("n") or 0)
        if n > 0:
            out[str(row["family"])] = float(row["skill_vs_field"]) / n
    return out


def _research_action(priority: str, flags: set[str], family_skill: float | None) -> str:
    if priority == "HIGH":
        if "TEAM_UNKNOWN" in flags:
            return "resolve team/side or find exact current prop odds"
        if "PRIOR_ONLY" in flags:
            return "find exact current prop odds or structured player research"
        if "GLOBAL_MINUTES_UNCONFIRMED" in flags:
            return "wait for lineup/minutes or use exact current prop odds"
        if "SCORER_PROP_RECHECK" in flags:
            return "refresh opponent-specific scorer odds before trusting override"
        if "CONFIRMED_BENCH" in flags:
            return "reprice from confirmed minutes and prefer exact prop odds when available"
        if "CURRENT_PROP_MARKET_ANCHOR" in flags:
            return "lineup risk remains; recheck current prop anchor near lock"
        return "clear player research flag with exact prop, lineup, or role evidence"
    if priority == "MEDIUM":
        if "CURRENT_PROP_MARKET_ANCHOR" in flags:
            return "recheck exact prop anchor and lineup near lock"
        if family_skill is not None and family_skill <= -1.0:
            return "prefer exact current prop odds because this family lagged field historically"
        return "recheck lineup and prop market before lock"
    return "lineup/prop recheck optional unless an exact current source strongly disagrees"


def manual_entry_fields(spec, probability: float, flags: list[str], player_no_field_base: float | None) -> dict:
    """Diagnostic view of the same no-field manual decision used by daily sheets."""

    decision = manual_decision(
        spec,
        probability,
        flags=flags,
        player_no_field_base=player_no_field_base,
    )
    return {
        "manual_entry": decision.probability,
        "manual_policy": decision.policy,
        "manual_note": decision.note,
    }


def annotate_player_research_priority(audit_rows: list[dict], family_rows: list[dict]) -> list[dict]:
    """Rank open player props for manual research.

    This is an execution audit, not a probability model. HIGH rows are exactly
    the flag types that the manual policy treats as unresolved or mandatory
    research. MEDIUM rows are either historically weak versus the field or have
    lineup/sparse-history uncertainty.
    """

    skill_by_family = family_skill_per_row(family_rows)
    out = []
    for row in audit_rows:
        flags = _flag_set(row.get("flags"))
        family = row.get("famkey") or row.get("family") or ""
        family_skill = skill_by_family.get(str(family))
        high_flags = unresolved_player_flags(flags) | (flags & FORCE_RESEARCH_FLAGS)
        if high_flags:
            priority = "HIGH"
            evidence = "unresolved player-policy flag"
        elif (family_skill is not None and family_skill <= -1.0) or (flags & MEDIUM_RESEARCH_FLAGS):
            priority = "MEDIUM"
            evidence = (
                f"family_skill_vs_field_per_row={family_skill:.2f}"
                if family_skill is not None and family_skill <= -1.0
                else "lineup/sparse-history uncertainty"
            )
        else:
            priority = "LOW"
            evidence = "no unresolved player flag"
        out.append(
            {
                **row,
                "research_priority": priority,
                "research_action": _research_action(priority, flags, family_skill),
                "research_evidence": evidence,
                "family_skill_vs_field_per_row": family_skill,
            }
        )
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(
        out,
        key=lambda r: (
            order.get(r["research_priority"], 99),
            r["kickoff"],
            r["match"],
            r["question"],
        ),
    )


def read_open_player_markets() -> list[OpenMarket]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("SportsPredict key missing from environment/.env")
    _matches, markets = fetch_sports_predict_open_markets(key)
    out = []
    for market in markets:
        match = market.get("match") or {}
        question = market.get("question", "")
        try:
            spec = parse(question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if spec.player:
            out.append(
                OpenMarket(
                    match_name=match.get("name", ""),
                    kickoff=datetime.fromisoformat(match.get("opening_time").replace("Z", "+00:00")),
                    market_id=market.get("id", ""),
                    question=question,
                )
            )
    return sorted(out, key=lambda r: (r.kickoff, r.match_name, r.question))


def selected_player_markets(
    rows: list[OpenMarket],
    *,
    now: datetime,
    tz: ZoneInfo,
    local_date: str | None = None,
    all_open: bool = False,
    include_started: bool = False,
    matches: set[str] | None = None,
) -> list[OpenMarket]:
    """Select player-prop markets for the current manual research pass."""

    grace = timedelta(minutes=10)
    selected = []
    for row in rows:
        if matches and row.match_name not in matches:
            continue
        if not all_open and local_date and row.kickoff.astimezone(tz).date().isoformat() != local_date:
            continue
        if not include_started and row.kickoff < now.astimezone(timezone.utc) - grace:
            continue
        selected.append(row)
    return sorted(selected, key=lambda r: (r.kickoff, r.match_name, r.question))


def _safe_load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {} if path.exists() else {}


def _lineup_entry(match_name: str) -> dict:
    return _safe_load_yaml(MATCH_LINEUPS).get(match_name) or {}


def _player_flags(match_name: str, kickoff: datetime, spec, pricer, lineup_result: dict, overrides: dict) -> list[str]:
    flags = []
    rates = pricer.players.rates(spec.player)
    if rates.source == "prior":
        flags.append("PRIOR_ONLY")
    elif rates.source == "statsbomb" and pricer.players.is_sparse(spec.player):
        flags.append("SPARSE_HISTORY")

    side = pricer._player_side(
        spec.player, spec.params.get("player_team"))  # report-only; this is the model side mapping.
    if side is None:
        flags.append("TEAM_UNKNOWN")

    pulled = (_lineup_entry(match_name).get("minutes") or {})
    override = overrides.get(spec.player) or {}
    if isinstance(override, dict):
        if override.get("expected_minutes") not in (None, 90) and spec.player not in pulled:
            flags.append("GLOBAL_MINUTES_UNCONFIRMED")
        if "scorer_prop_prob" in override:
            flags.append("SCORER_PROP_RECHECK")

    if lineup_result.get("status") == "pulled":
        if spec.player in (lineup_result.get("unmatched") or []):
            flags.append("LINEUP_UNMATCHED_DEFAULT_90")
        elif spec.player in (lineup_result.get("minutes") or {}) and lineup_result["minutes"][spec.player] < 90:
            flags.append("CONFIRMED_BENCH")
        elif spec.player in (lineup_result.get("minutes") or {}):
            flags.append("CONFIRMED_START")
    else:
        if kickoff - datetime.now(timezone.utc) <= timedelta(minutes=100):
            flags.append("LINEUP_NOT_POSTED_NEAR_KICKOFF")
        else:
            flags.append("LINEUP_NOT_POSTED")

    return flags or ["OK"]


def current_player_audit(
    rows: list[OpenMarket] | None = None,
    *,
    player_no_field_base: float | None = None,
    polymarket_events: list[dict] | None = None,
    polymarket_player_min_volume: float = 1000.0,
) -> list[dict]:
    rows = read_open_player_markets() if rows is None else rows
    polymarket_events = polymarket_events or []
    overrides = _safe_load_yaml(OVERRIDES)
    by_match: dict[str, list[OpenMarket]] = defaultdict(list)
    for row in rows:
        by_match[row.match_name].append(row)

    audit = []
    for match_name, match_rows in by_match.items():
        kickoff = match_rows[0].kickoff
        specs = []
        players = set()
        for row in match_rows:
            spec = parse(row.question, teams=ALL_TEAM_NAMES)
            specs.append((row, spec))
            players.add(spec.player)

        lineup_result = pull_lineup(match_name, kickoff, players=players)
        pricer = build_pricer(
            match_name,
            kickoff,
            include_global_scorer_props=False,
            include_global_expected_minutes=False,
        )
        for row, spec in specs:
            rates = pricer.players.rates(spec.player)
            side = pricer._player_side(spec.player, spec.params.get("player_team"))
            raw_p = float(pricer.price(spec))
            polymarket_anchor: DirectMarketAnchor | None = None
            polymarket_candidates: list[PlayerPropCandidate] = []
            if polymarket_events:
                polymarket_candidates = inspect_player_prop_candidates(
                    match_name,
                    spec,
                    polymarket_events,
                    min_volume=polymarket_player_min_volume,
                )
                polymarket_anchor = player_prop_anchor_for_market(
                    match_name,
                    spec,
                    polymarket_events,
                    min_volume=polymarket_player_min_volume,
                )
            p = float(polymarket_anchor.probability if polymarket_anchor else raw_p)
            resolved = pricer.players.resolve(spec.player)
            team = pricer.players.team_of(spec.player)
            flags = _player_flags(match_name, kickoff, spec, pricer, lineup_result, overrides)
            if polymarket_anchor:
                flags = _flags_after_current_prop_anchor(flags)
            manual = manual_entry_fields(spec, p, flags, player_no_field_base)
            audit.append(
                {
                    "match": match_name,
                    "kickoff": kickoff,
                    "market_id": row.market_id,
                    "question": row.question,
                    "player": spec.player,
                    "resolved": resolved or "",
                    "family": spec.family,
                    "famkey": famkey(spec),
                    "probability": round(p * 100),
                    **manual,
                    "source": rates.source,
                    "minutes": rates.expected_minutes,
                    "team": team or "",
                    "team_hint": spec.params.get("player_team") or "",
                    "side": side or "",
                    "lineup_status": lineup_result.get("status", ""),
                    "flags": ",".join(flags),
                    "raw_model_probability": round(raw_p * 100),
                    "current_prop_anchor": round(polymarket_anchor.probability * 100) if polymarket_anchor else None,
                    "current_prop_anchor_note": polymarket_anchor.note if polymarket_anchor else "",
                    "polymarket_prop_candidates": polymarket_candidates,
                    "polymarket_prop_candidate_count": len(polymarket_candidates),
                    "polymarket_prop_near_miss_count": sum(
                        1 for candidate in polymarket_candidates if candidate.status != "eligible"
                    ),
                    "polymarket_prop_candidate_statuses": _candidate_status_summary(polymarket_candidates),
                }
            )
    return sorted(audit, key=lambda r: (r["kickoff"], r["match"], r["question"]))


def _candidate_status_summary(candidates: list[PlayerPropCandidate]) -> str:
    if not candidates:
        return ""
    counts: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        counts[candidate.status] += 1
    return ", ".join(f"{status}={counts[status]}" for status in sorted(counts))


def _player_no_field_policy_line(info: PlayerNoFieldPolicy | None) -> str:
    if info is None:
        return "- player_no_field_policy: not_checked"
    if info.base is None:
        return f"- player_no_field_policy: unavailable ({info.error}); diagnostic manual entries use raw no-field guidance"
    return (
        f"- player_no_field_policy: base={round(info.base * 100)}% "
        f"settled_player_rows={info.settled_rows}; diagnostic manual entries apply the same no-field fallback as the daily sheet"
    )


def current_prop_anchor_summary(audit_rows: list[dict]) -> dict[str, int]:
    current_ids = {row["market_id"] for row in audit_rows if row.get("market_id")}
    anchored_ids = {
        row["market_id"]
        for row in audit_rows
        if row.get("market_id") and row.get("current_prop_anchor") is not None
    }
    high_ids = {
        row["market_id"]
        for row in audit_rows
        if row.get("market_id") and row.get("research_priority") == "HIGH"
    }
    medium_ids = {
        row["market_id"]
        for row in audit_rows
        if row.get("market_id") and row.get("research_priority") == "MEDIUM"
    }
    return {
        "current_player_rows": len(current_ids),
        "current_prop_anchor_rows": len(anchored_ids),
        "current_prop_anchor_missing_rows": len(current_ids - anchored_ids),
        "high_priority_rows": len(high_ids),
        "high_priority_anchor_rows": len(high_ids & anchored_ids),
        "high_priority_missing_current_prop_anchor": len(high_ids - anchored_ids),
        "medium_priority_rows": len(medium_ids),
        "medium_priority_anchor_rows": len(medium_ids & anchored_ids),
    }


def current_prop_anchor_status(summary: dict[str, int], *, enabled: bool) -> str:
    if not enabled:
        return "DISABLED"
    if int(summary.get("current_player_rows") or 0) == 0:
        return "NO_CURRENT_PLAYER_PROPS"
    if int(summary.get("current_prop_anchor_rows") or 0) == 0:
        return "NO_CURRENT_PROP_ANCHORS"
    if int(summary.get("current_prop_anchor_missing_rows") or 0) == 0:
        return "COMPLETE"
    if int(summary.get("high_priority_missing_current_prop_anchor") or 0) > 0:
        return "PARTIAL_HIGH_PRIORITY_MISSING"
    return "PARTIAL_HIGH_PRIORITY_COVERED"


def render(
    history_rows: list,
    family_rows: list[dict],
    audit_rows: list[dict],
    tz_name: str,
    *,
    player_no_field_policy: PlayerNoFieldPolicy | None = None,
    selection_label: str = "current selected open player props",
    include_started: bool = False,
    polymarket_player_enabled: bool = False,
    polymarket_player_min_volume: float = 1000.0,
) -> str:
    tz = ZoneInfo(tz_name)
    priority_counts = defaultdict(int)
    for row in audit_rows:
        priority_counts[row.get("research_priority", "UNRANKED")] += 1
    current_anchor_summary = current_prop_anchor_summary(audit_rows)
    current_anchor_status = current_prop_anchor_status(
        current_anchor_summary,
        enabled=polymarket_player_enabled,
    )
    lines = [
        "# Player Prop Diagnostics",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        "## Settled-History Player Layer",
        "",
        f"- settled_player_rows: {len(history_rows)}",
        "- interpretation: negative skill means our player-prop probabilities underperformed the field average.",
        "",
        "| family | n | rbp | skill_vs_field | brier_you | brier_field | you | field | hit |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in family_rows:
        lines.append(
            f"| {row['family']} | {row['n']} | {row['rbp']:.2f} | {row['skill_vs_field']:.2f} | "
            f"{row['brier_you']:.4f} | {row['brier_field']:.4f} | {row['you']:.3f} | "
            f"{row['field']:.3f} | {row['hit']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Current Player Research Queue",
            "",
            f"- selection: {selection_label}",
            f"- include_started: {str(include_started).lower()}",
            "- purpose: rank current player props for targeted lineup/prop-market research before manual lock",
            "- priority_basis: unresolved player-policy flags first, then historical family skill versus field and lineup/sparse-history uncertainty",
            "- this is an execution audit, not a new probability model",
            _player_no_field_policy_line(player_no_field_policy),
            (
                f"- polymarket_player_props: enabled min_market_volume=${polymarket_player_min_volume:,.0f} "
                f"applied={sum(1 for row in audit_rows if row.get('current_prop_anchor') is not None)}; "
                "exact same-match player props clear source/minutes flags but not lineup recheck risk"
                if polymarket_player_enabled
                else "- polymarket_player_props: disabled"
            ),
            f"- current_prop_anchor_status: {current_anchor_status}",
            f"- current_prop_anchor_rows: {current_anchor_summary['current_prop_anchor_rows']}",
            f"- current_prop_anchor_missing_rows: {current_anchor_summary['current_prop_anchor_missing_rows']}",
            f"- high_priority_missing_current_prop_anchor: {current_anchor_summary['high_priority_missing_current_prop_anchor']}",
            f"- medium_priority_anchor_rows: {current_anchor_summary['medium_priority_anchor_rows']}",
            "",
            "| priority | rows |",
            "| --- | ---: |",
        ]
    )
    for priority in ("HIGH", "MEDIUM", "LOW", "UNRANKED"):
        if priority_counts.get(priority):
            lines.append(f"| {priority} | {priority_counts[priority]} |")
    lines.extend(
        [
            "",
            "## Current Open Player Props",
            "",
            "| priority | action | family_skill/row | match | kickoff_local | market_id | raw_model | current_anchor | model | manual | manual_policy | player | family | source | minutes | resolved | team_hint | side | lineup | flags | question |",
            "| --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in audit_rows:
        kickoff = row["kickoff"].astimezone(tz).strftime("%Y-%m-%d %H:%M %Z")
        family_skill = row.get("family_skill_vs_field_per_row")
        family_skill_text = "" if family_skill is None else f"{family_skill:.2f}"
        current_anchor = row.get("current_prop_anchor")
        current_anchor_text = "" if current_anchor is None else f"{current_anchor}%"
        raw_model = row.get("raw_model_probability", row["probability"])
        lines.append(
            f"| {row.get('research_priority', '')} | {row.get('research_action', '')} | {family_skill_text} | "
            f"{row['match']} | {kickoff} | {row['market_id']} | {raw_model}% | "
            f"{current_anchor_text} | {row['probability']}% | {row.get('manual_entry', '')}% | "
            f"{row.get('manual_policy', '')} | "
            f"{row['player']} | {row['family']} | "
            f"{row['source']} | {row['minutes']:.0f} | {row['resolved']} | {row['team_hint']} | "
            f"{row['side']} | {row['lineup_status']} | {row['flags']} | {row['question']} |"
        )
    lines.extend(render_polymarket_candidate_audit(audit_rows))
    lines.extend(
        [
            "",
            "## Policy Implications",
            "",
            "- `PRIOR_ONLY`, `TEAM_UNKNOWN`, or `LINEUP_UNMATCHED_DEFAULT_90`: do targeted research before manual entry.",
            "- `GLOBAL_MINUTES_UNCONFIRMED`: do not trust the global minutes value until today's lineup confirms it.",
            "- `SCORER_PROP_RECHECK`: scorer odds are opponent-specific; re-check current market odds before using the override.",
            "- `CONFIRMED_BENCH`: player probability should be materially reduced by the pulled minutes; when no field or exact prop anchor is available, the manual sheet keeps the minutes-adjusted model and does not apply the global no-field base.",
            "- `LINEUP_UNMATCHED_DEFAULT_90`: lineup was posted but the player could not be matched; verify manually instead of using the global no-field base.",
            "- Player props remain the highest-risk family; field values are unavailable pre-lock, so prefer exact current prop odds, confirmed lineups, or the validated no-field fallback.",
            "",
        ]
    )
    return "\n".join(lines)


def render_polymarket_candidate_audit(audit_rows: list[dict]) -> list[str]:
    candidate_rows: list[tuple[dict, PlayerPropCandidate]] = []
    for row in audit_rows:
        for candidate in row.get("polymarket_prop_candidates") or []:
            candidate_rows.append((row, candidate))
    if not candidate_rows:
        return [
            "",
            "## Polymarket Player Prop Candidate Audit",
            "",
            "- same_player_candidates: 0",
            "- interpretation: no same-player Polymarket prop markets were visible for the selected SportsPredict player rows.",
        ]

    status_counts: dict[str, int] = defaultdict(int)
    for _row, candidate in candidate_rows:
        status_counts[candidate.status] += 1
    status_text = ", ".join(f"{status}={status_counts[status]}" for status in sorted(status_counts))
    exact_candidate_rows = [
        (row, candidate)
        for row, candidate in candidate_rows
        if candidate.status != "prop_mismatch"
    ]
    mismatch_rows = sorted(
        [(row, candidate) for row, candidate in candidate_rows if candidate.status == "prop_mismatch"],
        key=lambda item: (-item[1].market_volume, item[0]["kickoff"], item[0]["match"], item[0]["player"]),
    )
    mismatch_limit = 20
    shown_rows = exact_candidate_rows + mismatch_rows[:mismatch_limit]
    omitted_mismatches = max(0, len(mismatch_rows) - mismatch_limit)
    lines = [
        "",
        "## Polymarket Player Prop Candidate Audit",
        "",
        f"- same_player_candidates: {len(candidate_rows)}",
        f"- status_counts: {status_text}",
        f"- prop_mismatch_rows_shown: {min(len(mismatch_rows), mismatch_limit)} of {len(mismatch_rows)}",
        f"- prop_mismatch_rows_omitted: {omitted_mismatches}",
        "- interpretation: non-eligible candidates are logged for source review only; they do not change manual probabilities.",
        "",
        "| status | match | player | market_id | candidate_prob | volume | expected | actual | reason | polymarket_question |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |",
    ]
    for row, candidate in sorted(
        shown_rows,
        key=lambda item: (
            item[0]["kickoff"],
            item[0]["match"],
            item[0]["player"],
            item[1].status != "eligible",
            -item[1].market_volume,
        ),
    ):
        probability = "" if candidate.probability is None else f"{round(candidate.probability * 100)}%"
        lines.append(
            f"| {candidate.status} | {row['match']} | {row['player']} | {row['market_id']} | "
            f"{probability} | ${candidate.market_volume:,.0f} | {candidate.expected_prop} | "
            f"{candidate.actual_prop} | {candidate.reason} | {candidate.market_question} |"
        )
    return lines


def anchor_csv_audit(audit_rows: list[dict], inspection: PlayerPropAnchorInspection | None) -> dict:
    if inspection is None:
        return {}
    current_ids = {row["market_id"] for row in audit_rows if row.get("market_id")}
    high_ids = {
        row["market_id"]
        for row in audit_rows
        if row.get("market_id") and row.get("research_priority") == "HIGH"
    }
    usable_ids = {row.market_id for row in inspection.usable if row.market_id}
    blank_ids = {row.market_id for row in inspection.blank if row.market_id}
    invalid_ids = {row.market_id for row in inspection.invalid if row.market_id}
    return {
        "current_player_rows": len(current_ids),
        "csv_rows": len(inspection.rows),
        "usable": len(inspection.usable),
        "blank": len(inspection.blank),
        "invalid": len(inspection.invalid),
        "matched_usable": len(current_ids & usable_ids),
        "current_missing_usable": len(current_ids - usable_ids),
        "high_priority_missing_usable": len(high_ids - usable_ids),
        "current_blank": len(current_ids & blank_ids),
        "current_invalid": len(current_ids & invalid_ids),
        "unused_usable": len(usable_ids - current_ids),
    }


def anchor_csv_status(summary: dict) -> str:
    if not summary:
        return "NOT_SUPPLIED"
    if int(summary.get("current_player_rows") or 0) == 0:
        return "NO_CURRENT_PLAYER_PROPS"
    if (
        int(summary.get("high_priority_missing_usable") or 0) > 0
        or int(summary.get("current_blank") or 0) > 0
        or int(summary.get("current_invalid") or 0) > 0
    ):
        return "INCOMPLETE"
    if int(summary.get("current_missing_usable") or 0) > 0:
        return "PARTIAL"
    if int(summary.get("unused_usable") or 0) > 0:
        return "COMPLETE_WITH_UNUSED_ROWS"
    return "COMPLETE"


def render_anchor_csv_audit(audit_rows: list[dict], inspection: PlayerPropAnchorInspection | None) -> str:
    if inspection is None:
        return ""
    summary = anchor_csv_audit(audit_rows, inspection)
    status = anchor_csv_status(summary)
    current_by_id = {row["market_id"]: row for row in audit_rows if row.get("market_id")}
    lines = [
        "",
        "## Current Prop Anchor CSV Audit",
        "",
        f"- player_prop_anchor_status: {status}",
        f"- current_player_rows: {summary['current_player_rows']}",
        f"- csv_rows: {summary['csv_rows']}",
        f"- usable: {summary['usable']}",
        f"- current_missing_usable: {summary['current_missing_usable']}",
        f"- high_priority_missing_usable: {summary['high_priority_missing_usable']}",
        f"- current_blank: {summary['current_blank']}",
        f"- current_invalid: {summary['current_invalid']}",
        f"- unused_usable: {summary['unused_usable']}",
        "",
        "| metric | value |",
        "| --- | ---: |",
    ]
    for key in [
        "current_player_rows",
        "csv_rows",
        "usable",
        "blank",
        "invalid",
        "matched_usable",
        "current_missing_usable",
        "high_priority_missing_usable",
        "current_blank",
        "current_invalid",
        "unused_usable",
    ]:
        lines.append(f"| {key} | {summary[key]} |")
    lines.extend(
        [
            "",
            "| status | priority | market_id | player | match | probability | source | as_of | issue |",
            "| --- | --- | --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in inspection.rows:
        current = current_by_id.get(row.market_id, {})
        probability = f"{row.probability * 100:.1f}%" if row.probability is not None else ""
        lines.append(
            f"| {row.status} | {current.get('research_priority', '')} | {row.market_id} | {current.get('player', '')} | "
            f"{current.get('match', row.match)} | {probability} | {row.source} | "
            f"{row.as_of} | {row.error} |"
        )
    return "\n".join(lines)


def write_anchor_template(audit_rows: list[dict], path: Path) -> None:
    """Write a ready-to-fill exact current prop anchor CSV.

    Fill either `probability` or both `yes_american` and `no_american`, plus
    `prop_source`/`as_of` for auditability. Keep rows exact; do not copy these
    values into global player overrides.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "research_priority",
        "research_action",
        "market_id",
        "match",
        "kickoff_utc",
        "player",
        "family",
        "famkey",
        "family_skill_vs_field_per_row",
        "model_probability",
        "raw_model_probability",
        "current_prop_anchor",
        "current_prop_anchor_note",
        "manual_entry",
        "manual_policy",
        "flags",
        "question",
        "probability",
        "yes_american",
        "no_american",
        "prop_source",
        "as_of",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in audit_rows:
            writer.writerow(
                {
                    "research_priority": row.get("research_priority", ""),
                    "research_action": row.get("research_action", ""),
                    "market_id": row["market_id"],
                    "match": row["match"],
                    "kickoff_utc": row["kickoff"].astimezone(timezone.utc).isoformat(timespec="minutes"),
                    "player": row["player"],
                    "family": row["family"],
                    "famkey": row.get("famkey", ""),
                    "family_skill_vs_field_per_row": (
                        ""
                        if row.get("family_skill_vs_field_per_row") is None
                        else f"{row['family_skill_vs_field_per_row']:.2f}"
                    ),
                    "model_probability": f"{row['probability']}%",
                    "raw_model_probability": f"{row.get('raw_model_probability', row['probability'])}%",
                    "current_prop_anchor": (
                        "" if row.get("current_prop_anchor") is None else f"{row['current_prop_anchor']}%"
                    ),
                    "current_prop_anchor_note": row.get("current_prop_anchor_note", ""),
                    "manual_entry": "" if row.get("manual_entry") is None else f"{row['manual_entry']}%",
                    "manual_policy": row.get("manual_policy", ""),
                    "flags": row["flags"],
                    "question": row["question"],
                    "probability": "",
                    "yes_american": "",
                    "no_american": "",
                    "prop_source": "",
                    "as_of": "",
                    "notes": "",
                }
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only /results settlement for older history rows")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--local-date", help="Local date to audit, YYYY-MM-DD. Default: today in --tz.")
    ap.add_argument("--all-open", action="store_true", help="Audit all currently open future player-prop markets.")
    ap.add_argument("--include-started", action="store_true", help="Include matches that have already kicked off.")
    ap.add_argument("--match", action="append", help="Restrict to an exact SportsPredict match name. Repeatable.")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument("--no-polymarket-player-props", action="store_true", help="Disable exact current Polymarket player-prop anchors in the diagnostic.")
    ap.add_argument("--polymarket-player-min-volume", type=float, default=1000.0, help="Minimum individual Polymarket market volume for exact player-prop anchors.")
    ap.add_argument(
        "--anchor-template",
        nargs="?",
        const=str(ANCHOR_TEMPLATE),
        help=f"write exact player-prop anchor CSV template; default path {ANCHOR_TEMPLATE}",
    )
    ap.add_argument("--player-prop-csv", help="Optional filled/current player-prop anchor CSV to audit.")
    args = ap.parse_args()

    tz = ZoneInfo(args.tz)
    now = datetime.now(timezone.utc)
    local_date = args.local_date or now.astimezone(tz).date().isoformat()
    matches = set(args.match) if args.match else None
    hist = settled_player_rows(sports_predict=args.sports_predict)
    family = family_history_table(hist)
    player_no_field_policy = current_player_no_field_policy()
    selected_rows = selected_player_markets(
        read_open_player_markets(),
        now=now,
        tz=tz,
        local_date=local_date,
        all_open=args.all_open,
        include_started=args.include_started,
        matches=matches,
    )
    polymarket_events = [] if args.no_polymarket_player_props or not selected_rows else fetch_polymarket_events()
    selection_label = "all-open" if args.all_open else local_date
    if matches:
        selection_label += " " + ", ".join(sorted(matches))
    audit = annotate_player_research_priority(
        current_player_audit(
            selected_rows,
            player_no_field_base=player_no_field_policy.base,
            polymarket_events=polymarket_events,
            polymarket_player_min_volume=args.polymarket_player_min_volume,
        ),
        family,
    )
    inspection = inspect_player_prop_anchor_csv(args.player_prop_csv) if args.player_prop_csv else None
    text = render(
        hist,
        family,
        audit,
        args.tz,
        player_no_field_policy=player_no_field_policy,
        selection_label=selection_label,
        include_started=args.include_started,
        polymarket_player_enabled=not args.no_polymarket_player_props,
        polymarket_player_min_volume=args.polymarket_player_min_volume,
    )
    text += render_anchor_csv_audit(audit, inspection)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")
    if args.anchor_template:
        template_path = Path(args.anchor_template)
        write_anchor_template(audit, template_path)
        print(f"wrote {template_path}")


if __name__ == "__main__":
    main()
