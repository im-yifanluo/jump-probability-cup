"""Full read-only manual pricing run for Probability Cup markets.

This is the manual-entry workflow, not a bot workflow. It:

* reads open markets with read-only SportsPredict GET endpoints,
* optionally pulls posted ESPN lineups for the players in those markets,
* prices every selected market through the full odds/count/player/referee stack,
* appends the exact table and run evidence to data/logs/manual_predictions.md.

It intentionally does not call SportsPredict /predictions and never submits.
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

from manual.anchors import fetch_sports_predict_open_markets, sports_predict_key
from manual.policy import manual_decision
from manual.player_prop_anchors import (
    PlayerPropAnchors,
    anchor_for_market,
    read_player_prop_anchor_csv,
)
from manual.polymarket_direct import (
    DirectMarketAnchor,
    count_prop_anchor_for_market,
    direct_anchor_for_market,
    fetch_polymarket_events,
    player_prop_anchor_for_market,
)
from models.specials import load_special_baselines
from price_match import build_pricer, referee_context
from pull_lineups import pull_lineup, write_lineup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES
from player_no_field_policy_backtest import beta_mean
from player_policy_backtest import load_player_rows


LOG_PATH = ROOT / "data" / "logs" / "manual_predictions.md"
PLAYER_OVERRIDES = ROOT / "data" / "player_overrides.yaml"


@dataclass(frozen=True)
class MarketRow:
    match_name: str
    kickoff: datetime
    market_id: str
    question: str


@dataclass(frozen=True)
class FieldCoverage:
    supplied: bool
    selected: int
    supplied_rows: int
    matched: int
    missing: tuple[MarketRow, ...] = ()
    unused_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlayerNoFieldPolicy:
    base: float | None
    settled_rows: int = 0
    error: str = ""


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _local_date(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).date().isoformat()


def read_open_markets() -> list[MarketRow]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("SportsPredict key missing from environment/.env")
    _matches, markets = fetch_sports_predict_open_markets(key)
    rows = []
    for market in markets:
        match = market.get("match") or {}
        rows.append(
            MarketRow(
                match_name=match.get("name", ""),
                kickoff=_dt(match.get("opening_time")),
                market_id=market.get("id", ""),
                question=market.get("question", ""),
            )
        )
    return rows


def selected_markets(
    rows: list[MarketRow],
    *,
    local_date: str,
    tz: ZoneInfo,
    remaining_only: bool,
    now: datetime,
    matches: set[str] | None,
) -> list[MarketRow]:
    out = []
    grace = timedelta(minutes=10)
    for row in rows:
        if matches and row.match_name not in matches:
            continue
        if _local_date(row.kickoff, tz) != local_date:
            continue
        if remaining_only and row.kickoff < now.astimezone(timezone.utc) - grace:
            continue
        out.append(row)
    return sorted(out, key=lambda r: (r.kickoff, r.match_name, r.market_id))


def market_players(rows: list[MarketRow]) -> dict[str, set[str]]:
    players: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        try:
            spec = parse(row.question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if spec.player:
            players[row.match_name].add(spec.player)
    return players


def model_note(pricer, spec) -> str:
    parts = [spec.family]
    if spec.stat:
        parts.append(spec.stat)
    if spec.period and spec.period != "match":
        parts.append(spec.period)
    note = "/".join(parts)
    if spec.player:
        rates = pricer.players.rates(spec.player)
        note += f" [{rates.source}, {rates.expected_minutes:.0f} min]"
    return note


def _player_override(player: str | None) -> dict:
    if not player or not PLAYER_OVERRIDES.exists():
        return {}
    overrides = yaml.safe_load(PLAYER_OVERRIDES.read_text()) or {}
    override = overrides.get(player) or {}
    return override if isinstance(override, dict) else {}


def _parse_field_probability(raw: str) -> float:
    value = raw.strip().rstrip("%").strip()
    if not value:
        raise ValueError("empty field probability")
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"field probability must be positive: {raw!r}")
    if parsed <= 1:
        return parsed
    if parsed <= 100:
        return parsed
    raise ValueError(f"field probability must be <= 100%: {raw!r}")


def read_field_csv(path: str | None) -> dict[str, float]:
    if not path:
        return {}
    out: dict[str, float] = {}
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = set(reader.fieldnames or [])
        if not ({"market_id", "id"} & headers):
            raise ValueError("field CSV must include `market_id` or `id` column")
        if not ({"field", "field_probability", "crowd"} & headers):
            raise ValueError("field CSV must include `field`, `field_probability`, or `crowd` column")
        for line_no, row in enumerate(reader, start=2):
            market_id = (row.get("market_id") or row.get("id") or "").strip()
            value = (row.get("field") or row.get("field_probability") or row.get("crowd") or "").strip()
            if not market_id and not value:
                continue
            if not market_id or not value:
                raise ValueError(f"field CSV line {line_no} must include both market id and field value")
            if market_id in out:
                raise ValueError(f"duplicate field CSV market id: {market_id}")
            out[market_id] = _parse_field_probability(value)
    return out


def field_coverage(rows: list[MarketRow], field_by_market: dict[str, float]) -> FieldCoverage:
    if not field_by_market:
        return FieldCoverage(False, selected=len(rows), supplied_rows=0, matched=0)
    selected_ids = {row.market_id for row in rows}
    matched_ids = selected_ids & set(field_by_market)
    missing = tuple(row for row in rows if row.market_id not in field_by_market)
    unused = tuple(sorted(set(field_by_market) - selected_ids))
    return FieldCoverage(
        True,
        selected=len(rows),
        supplied_rows=len(field_by_market),
        matched=len(matched_ids),
        missing=missing,
        unused_ids=unused,
    )


def player_flags(spec, pricer, lineup_result: dict) -> list[str]:
    if not spec.player:
        return []
    flags = []
    rates = pricer.players.rates(spec.player)
    if rates.source == "prior":
        flags.append("PRIOR_ONLY")
    elif rates.source == "statsbomb" and pricer.players.is_sparse(spec.player):
        flags.append("SPARSE_HISTORY")
    if pricer._player_side(spec.player, spec.params.get("player_team")) is None:
        flags.append("TEAM_UNKNOWN")
    if rates.source == "override" and rates.expected_minutes not in (90,):
        if spec.player not in (lineup_result.get("minutes") or {}):
            flags.append("GLOBAL_MINUTES_UNCONFIRMED")
    override = _player_override(spec.player)
    if override.get("expected_minutes") not in (None, 90):
        if spec.player not in (lineup_result.get("minutes") or {}):
            if "GLOBAL_MINUTES_UNCONFIRMED" not in flags:
                flags.append("GLOBAL_MINUTES_UNCONFIRMED")
    if spec.player in pricer.scorer_props or "scorer_prop_prob" in override:
        flags.append("SCORER_PROP_RECHECK")

    if lineup_result.get("status") == "pulled":
        minutes = lineup_result.get("minutes") or {}
        if spec.player in (lineup_result.get("unmatched") or []):
            flags.append("LINEUP_UNMATCHED_DEFAULT_90")
        elif spec.player in minutes and minutes[spec.player] < 90:
            flags.append("CONFIRMED_BENCH")
        elif spec.player in minutes:
            flags.append("CONFIRMED_START")
    else:
        flags.append("LINEUP_NOT_POSTED")
    return flags


def _flags_after_current_prop_anchor(flags: list[str]) -> list[str]:
    clearable = {
        "PRIOR_ONLY",
        "TEAM_UNKNOWN",
        "GLOBAL_MINUTES_UNCONFIRMED",
        "SCORER_PROP_RECHECK",
        "SPARSE_HISTORY",
    }
    out = [flag for flag in flags if flag not in clearable]
    out.append("CURRENT_PROP_MARKET_ANCHOR")
    return out


def current_player_no_field_policy() -> PlayerNoFieldPolicy:
    """Current global player base for no-field, no-anchor player props.

    This is intentionally computed from safe settled player rows. If the
    read-only `/results` settlement is unavailable, the daily sheet still runs
    without this fallback and keeps the older policy guidance.
    """

    try:
        rows = load_player_rows(sports_predict=True)
    except Exception as exc:  # pragma: no cover - operational network/API guard
        return PlayerNoFieldPolicy(None, error=f"{exc.__class__.__name__}: {exc}")
    return PlayerNoFieldPolicy(beta_mean(rows, prior=0.5, strength=2.0), settled_rows=len(rows))


def reasoning(spec) -> str:
    if spec.family in {"team_win", "match_drawn"}:
        return "blended ESPN/DraftKings and Polymarket market-implied 1X2 probability"
    if spec.family == "team_advance":
        return "regulation win plus draw-path extra-time/penalty model from odds-implied goal rates"
    if spec.family == "penalty_shootout":
        return "regulation draw plus extra-time draw model from odds-implied goal rates"
    if spec.family == "team_leads_any_time":
        return "odds-anchored scoreline and goal-order model for whether the team ever leads"
    if spec.family in {
        "goal_before_hydration",
        "goal_after_first_hydration_1h",
        "goal_after_second_hydration",
        "goal_in_stoppage",
        "offside_before_hydration",
        "card_after_second_hydration",
        "card_in_stoppage_any_half",
        "substitution_before_halftime",
        "substitute_scores",
        "any_player_goals_ge",
        "any_player_sot_ge",
        "total_count_before_hydration",
    } or spec.stat == "shots":
        return "StatsBomb international-tournament timing/special baseline scaled to this match's goal or count volume"
    if spec.family == "first_goal_other_than_players":
        return "odds-anchored first-goal race minus named players' estimated first-scorer shares from the player goal-rate model"
    if spec.stat == "goals" or spec.family in {
        "total_goals",
        "btts",
        "btts_and_total_ge",
        "team_goals_ge",
        "team_scores_in_half",
        "team_more_goals_2h",
        "team_first_goal_2h",
        "first_goal_game_2h",
        "team_scores_both_halves",
        "team_first_goal_game",
        "either_team_wins_both_halves",
        "match_decided_by_exactly_one_goal",
        "h2_more_goals_than_h1",
        "ht_team_winning",
        "ht_tied",
    }:
        return "odds-anchored scoreline model using current 1X2, total-goals line, and tournament half-goal share"
    if spec.family == "penalty_or_red":
        return "StatsBomb international-tournament penalty/red base rate from cached event data"
    if spec.family in {"penalty_awarded", "red_card"}:
        return "StatsBomb international-tournament event base rate from cached event data"
    if spec.family == "total_cards_more_than_goals":
        return "card-count model compared against the odds-anchored total-goals distribution"
    if spec.player:
        return "player model using StatsBomb/override rates, team tempo from the count model, and ESPN lineup minutes when posted"
    if spec.family in {
        "team_count_ge",
        "total_count_ge",
        "both_teams_count_ge",
        "team_more_corners_and_total_shots",
        "team_more_count_than",
        "total_count_each_half_ge",
        "total_cards_more_than_goals",
    }:
        return "negative-binomial count model driven by odds-implied goal rates, tournament calibration, and referee override if available"
    return "full manual pricing stack"


def _model_note_with_anchor(
    pricer,
    spec,
    anchor,
    direct_anchor: DirectMarketAnchor | None = None,
    polymarket_player_anchor: DirectMarketAnchor | None = None,
    polymarket_count_anchor: DirectMarketAnchor | None = None,
) -> str:
    if direct_anchor is not None:
        return f"current_polymarket_direct [{direct_anchor.note}]"
    if polymarket_player_anchor is not None:
        return f"current_player_prop_anchor [{polymarket_player_anchor.note}]"
    if polymarket_count_anchor is not None:
        return f"current_polymarket_count_direct [{polymarket_count_anchor.note}]"
    if anchor is None:
        return model_note(pricer, spec)
    note = "current_player_prop_anchor"
    if anchor.source:
        note += f" [{anchor.source}]"
    if anchor.as_of:
        note += f" as_of={anchor.as_of}"
    return note


def _reasoning_with_anchor(
    spec,
    anchor,
    direct_anchor: DirectMarketAnchor | None = None,
    polymarket_player_anchor: DirectMarketAnchor | None = None,
    polymarket_count_anchor: DirectMarketAnchor | None = None,
) -> str:
    if direct_anchor is not None:
        return (
            "exact current Polymarket market for this same non-player question; "
            "used only when market-level volume clears the direct-anchor threshold"
        )
    if polymarket_player_anchor is not None:
        return (
            "exact current Polymarket player prop for this same match, player, and threshold; "
            "used only when the separate player-prop volume threshold is cleared"
        )
    if polymarket_count_anchor is not None:
        return (
            "exact current Polymarket corner market for this same match and threshold; "
            "used only when the stricter count-market volume threshold is cleared"
        )
    if anchor is None:
        return reasoning(spec)
    return (
        "current opponent-specific player prop market anchor supplied by CSV; "
        "used only for this exact market"
    )


def price_rows(
    rows: list[MarketRow],
    *,
    lineups: dict[str, dict],
    field_by_market: dict[str, float],
    player_prop_anchors: PlayerPropAnchors | None = None,
    player_no_field_base: float | None = None,
    polymarket_events: list[dict] | None = None,
    polymarket_direct_min_volume: float = 1000.0,
    polymarket_player_min_volume: float = 1000.0,
    polymarket_count_min_volume: float = 5000.0,
) -> tuple[list[dict], dict[str, dict]]:
    player_prop_anchors = player_prop_anchors or PlayerPropAnchors.empty()
    polymarket_events = polymarket_events or []
    priced = []
    diagnostics: dict[str, dict] = {}
    for match_name, match_rows_iter in _group(rows).items():
        match_rows = list(match_rows_iter)
        kickoff = match_rows[0].kickoff
        pricer = build_pricer(
            match_name,
            kickoff,
            include_global_scorer_props=False,
            include_global_expected_minutes=False,
        )
        diagnostics[match_name] = {
            "lam_home": pricer.goals.lam_home,
            "lam_away": pricer.goals.lam_away,
            "home": pricer.home_name,
            "away": pricer.away_name,
            "referee": referee_context(match_name),
        }
        for row in match_rows:
            spec = parse(row.question, teams=ALL_TEAM_NAMES)
            anchor = anchor_for_market(row, player_prop_anchors) if spec.player else None
            polymarket_player_anchor = None
            if spec.player and anchor is None and polymarket_events:
                polymarket_player_anchor = player_prop_anchor_for_market(
                    row.match_name,
                    spec,
                    polymarket_events,
                    min_volume=polymarket_player_min_volume,
                )
            p_model = float(
                anchor.probability
                if anchor
                else polymarket_player_anchor.probability
                if polymarket_player_anchor
                else pricer.price(spec)
            )
            direct_anchor = None
            if anchor is None and polymarket_player_anchor is None and polymarket_events:
                direct_anchor = direct_anchor_for_market(
                    row.match_name,
                    spec,
                    polymarket_events,
                    min_volume=polymarket_direct_min_volume,
                )
            polymarket_count_anchor = None
            if (
                anchor is None
                and polymarket_player_anchor is None
                and direct_anchor is None
                and polymarket_events
            ):
                polymarket_count_anchor = count_prop_anchor_for_market(
                    row.match_name,
                    spec,
                    polymarket_events,
                    min_volume=polymarket_count_min_volume,
                )
            p = float(
                direct_anchor.probability
                if direct_anchor
                else polymarket_count_anchor.probability
                if polymarket_count_anchor
                else p_model
            )
            flags = player_flags(spec, pricer, lineups.get(match_name, {}))
            if anchor or polymarket_player_anchor:
                flags = _flags_after_current_prop_anchor(flags)
            field = field_by_market.get(row.market_id)
            decision = manual_decision(
                spec,
                p,
                field_probability=field,
                flags=flags,
                player_no_field_base=player_no_field_base,
            )
            priced.append(
                {
                    "match_name": row.match_name,
                    "kickoff": row.kickoff,
                    "market_id": row.market_id,
                    "question": row.question,
                    "probability": round(p * 100),
                    "manual_entry": decision.display,
                    "manual_policy": decision.policy,
                    "policy_note": decision.note,
                    "field": decision.field,
                    "flags": ",".join(flags) if flags else "",
                    "p_raw": p,
                    "model": _model_note_with_anchor(
                        pricer, spec, anchor, direct_anchor, polymarket_player_anchor, polymarket_count_anchor
                    ),
                    "reasoning": _reasoning_with_anchor(
                        spec, anchor, direct_anchor, polymarket_player_anchor, polymarket_count_anchor
                    ),
                    "direct_market_anchor": direct_anchor,
                    "polymarket_player_anchor": polymarket_player_anchor,
                    "polymarket_count_anchor": polymarket_count_anchor,
                }
            )
    return priced, diagnostics


def _group(rows: list[MarketRow]) -> dict[str, list[MarketRow]]:
    grouped: dict[str, list[MarketRow]] = defaultdict(list)
    for row in rows:
        grouped[row.match_name].append(row)
    return dict(grouped)


def refresh_lineups(rows: list[MarketRow], *, write: bool) -> dict[str, dict]:
    by_match = _group(rows)
    players = market_players(rows)
    results: dict[str, dict] = {}
    for match_name, match_rows in by_match.items():
        result = pull_lineup(match_name, match_rows[0].kickoff, players=players.get(match_name, set()))
        results[match_name] = result
        if write and result["status"] == "pulled":
            write_lineup(match_name, match_rows[0].kickoff, result)
    return results


def markdown_report(
    rows: list[dict],
    diagnostics: dict[str, dict],
    lineups: dict[str, dict],
    *,
    local_date: str,
    tz: ZoneInfo,
    now: datetime,
    field_info: FieldCoverage,
    player_prop_anchors: PlayerPropAnchors,
    player_no_field_policy: PlayerNoFieldPolicy,
    polymarket_direct_enabled: bool = False,
    polymarket_direct_min_volume: float = 1000.0,
    polymarket_player_enabled: bool = False,
    polymarket_player_min_volume: float = 1000.0,
    polymarket_count_enabled: bool = False,
    polymarket_count_min_volume: float = 5000.0,
) -> str:
    run_id = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    espn_dates = sorted(
        {
            row["kickoff"].strftime("%Y%m%d")
            for row in rows
        }
        | {
            (row["kickoff"] - timedelta(days=1)).strftime("%Y%m%d")
            for row in rows
        }
    )
    lines = [
        f"## {run_id} full_manual_pricing_v2 local_date={local_date}",
        "",
        "- bot_predictions: not_read",
        "- sports_predict: read_only GET /events, /lobbies, /matches, /markets, /results only",
        "- odds_sources: ESPN/DraftKings scoreboard plus Polymarket 1X2 markets where available",
        "- count_sources: data/count_models.json and data/statsbomb_team_halves.parquet",
        "- player_sources: data/statsbomb_players.parquet, data/player_overrides.yaml, data/match_lineups.yaml, ESPN summary lineups",
        f"- special_market_sources: {load_special_baselines().source}, n={load_special_baselines().n_matches}",
        "- field_policy: field averages are unavailable before prediction lock; pre-lock manual entries do not use or wait for field values, so count/card/guardrail rows use no-field current-source or model values with explicit confidence flags",
        _polymarket_direct_line(rows, enabled=polymarket_direct_enabled, min_volume=polymarket_direct_min_volume),
        _polymarket_player_line(rows, enabled=polymarket_player_enabled, min_volume=polymarket_player_min_volume),
        _polymarket_count_line(rows, enabled=polymarket_count_enabled, min_volume=polymarket_count_min_volume),
        _field_coverage_line(field_info),
        _player_prop_anchor_line(rows, player_prop_anchors),
        _player_no_field_policy_line(player_no_field_policy),
        "- source_links:",
        "  - SportsPredict API base: https://api.sportspredict.com/api/v1",
        "  - Polymarket generic soccer events: https://gamma-api.polymarket.com/events?closed=false&tag_slug=soccer&limit=100",
        "  - Polymarket FIFA World Cup series events: https://gamma-api.polymarket.com/events?closed=false&series_slug=soccer-fifwc&limit=100",
        "  - StatsBomb open data: https://github.com/statsbomb/open-data",
    ]
    for espn_date in espn_dates:
        lines.append(
            f"  - ESPN soccer scoreboard {espn_date}: "
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard?dates={espn_date}"
        )
    lines.extend([
        "",
    ])
    for match_name, match_rows in _group_dict(rows).items():
        diag = diagnostics.get(match_name, {})
        kickoff = match_rows[0]["kickoff"].astimezone(tz)
        lines.extend(
            [
                f"### {match_name}",
                "",
                f"- kickoff_local: {kickoff:%Y-%m-%d %H:%M %Z}",
                f"- goal_rates: {diag.get('home', 'home')} {diag.get('lam_home', 0):.2f}, {diag.get('away', 'away')} {diag.get('lam_away', 0):.2f}",
                _referee_line(diag.get("referee") or {}),
                f"- lineup_status: {lineups.get(match_name, {}).get('status', 'unknown')}",
            ]
        )
        for player, espn, role, mins in lineups.get(match_name, {}).get("detail", []):
            lines.append(f"  - {player}: {role}, {mins} min (ESPN: {espn})")
        unmatched = lineups.get(match_name, {}).get("unmatched") or []
        if unmatched:
            lines.append(f"  - unmatched/default_90: {', '.join(unmatched)}")
        lines.extend(
            [
                "",
                "| model_probability | manual_entry | policy | flags | question | model_source | reasoning |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in match_rows:
            field_note = f" field={row['field']}%" if row["field"] is not None else ""
            lines.append(
                f"| {row['probability']}% | {row['manual_entry']} | {row['manual_policy']}{field_note} | "
                f"{row['flags'] or '-'} | {row['question']} | {row['model']} | {row['reasoning']} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _field_coverage_line(info: FieldCoverage) -> str:
    if not info.supplied:
        return (
            "- field_csv: not_supplied; expected because field values are unavailable before prediction lock; "
            "manual_entry uses the no-field player fallback where eligible, otherwise independent current-source/model values"
        )
    parts = [
        "- field_csv: supplied",
        f"selected={info.selected}",
        f"csv_rows={info.supplied_rows}",
        f"matched={info.matched}",
        f"missing={len(info.missing)}",
        f"unused={len(info.unused_ids)}",
    ]
    if info.missing:
        preview = "; ".join(f"{row.match_name} {row.market_id}: {row.question}" for row in info.missing[:5])
        parts.append(f"missing_preview={preview}")
    if info.unused_ids:
        parts.append("unused_preview=" + ", ".join(info.unused_ids[:5]))
    return " ".join(parts)


def _polymarket_direct_line(rows: list[dict], *, enabled: bool, min_volume: float) -> str:
    if not enabled:
        return "- polymarket_direct: disabled"
    applied = [row for row in rows if row.get("direct_market_anchor") is not None]
    return (
        "- polymarket_direct: enabled "
        f"min_market_volume=${min_volume:,.0f} applied={len(applied)}; "
        "direct exact non-player markets replace model-derived probabilities only when exact wording/threshold and volume checks pass"
    )


def _polymarket_player_line(rows: list[dict], *, enabled: bool, min_volume: float) -> str:
    if not enabled:
        return "- polymarket_player_props: disabled"
    applied = [row for row in rows if row.get("polymarket_player_anchor") is not None]
    return (
        "- polymarket_player_props: enabled "
        f"min_market_volume=${min_volume:,.0f} applied={len(applied)}; "
        "exact same-match player prop markets replace player-model probabilities only when player/family/threshold and volume checks pass"
    )


def _polymarket_count_line(rows: list[dict], *, enabled: bool, min_volume: float) -> str:
    if not enabled:
        return "- polymarket_count_direct: disabled"
    applied = [row for row in rows if row.get("polymarket_count_anchor") is not None]
    return (
        "- polymarket_count_direct: enabled "
        f"min_market_volume=${min_volume:,.0f} applied={len(applied)}; "
        "exact same-match corner markets replace count-model probabilities only when corner type/threshold and stricter volume checks pass"
    )


def _player_prop_anchor_line(rows: list[dict], anchors: PlayerPropAnchors) -> str:
    if anchors.row_count == 0:
        return "- player_prop_csv: not_supplied"
    matched_market_ids = set()
    matched_question_keys = set()
    matched = 0
    selected_player_rows = 0
    for row in rows:
        try:
            spec = parse(row["question"], teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if not spec.player:
            continue
        selected_player_rows += 1
        market_id = row.get("market_id") or ""
        key = (row.get("match_name") or "", row.get("question") or "")
        if market_id in anchors.by_market_id:
            matched_market_ids.add(market_id)
            matched += 1
        elif key in anchors.by_match_question:
            matched_question_keys.add(key)
            matched += 1
    unused_market_ids = set(anchors.by_market_id) - matched_market_ids
    unused_question_keys = set(anchors.by_match_question) - matched_question_keys
    return (
        "- player_prop_csv: supplied "
        f"selected_player_rows={selected_player_rows} csv_rows={anchors.row_count} "
        f"matched={matched} unused={len(unused_market_ids) + len(unused_question_keys)}"
    )


def _player_no_field_policy_line(info: PlayerNoFieldPolicy) -> str:
    if info.base is None:
        return (
            "- player_no_field_policy: unavailable; no-field player props use raw model "
            f"with policy guidance only ({info.error or 'not computed'})"
        )
    return (
        "- player_no_field_policy: raw_scores_else_global_base "
        f"base={round(info.base * 100)}% settled_player_rows={info.settled_rows}; "
        "applies only to raw scorer rows, score/assist rows, and 1+ SOT rows; "
        "PRIOR_ONLY/TEAM_UNKNOWN rows stay raw and are source-unvalidated; "
        "confirmed-bench and posted-lineup-unmatched rows do not use the global base; "
        "higher player-SOT thresholds stay raw without exact prop/source/lineup anchors"
    )


def _referee_line(ref: dict) -> str:
    status = ref.get("status", "unknown")
    factor = float(ref.get("card_factor", 1.0))
    if status == "override":
        return (
            f"- referee: {ref.get('referee', 'unknown')} "
            f"(cards_per_game={float(ref.get('cards_per_game', 0)):.2f}, "
            f"card_factor={factor:.2f}, asymmetric_cap=1.00)"
        )
    return f"- referee: {status}; card_factor={factor:.2f}"


def _group_dict(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["match_name"]].append(row)
    return dict(grouped)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local-date", help="Local date to price, YYYY-MM-DD. Default: today in --tz.")
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--include-started", action="store_true", help="Include matches that have already kicked off.")
    ap.add_argument("--match", action="append", help="Restrict to an exact SportsPredict match name. Repeatable.")
    ap.add_argument("--field-csv", help="Historical/diagnostic CSV with market_id,field columns; do not use for pre-lock manual entries.")
    ap.add_argument("--player-prop-csv", help="Optional exact current player-prop anchors CSV.")
    ap.add_argument("--no-polymarket-direct", action="store_true", help="Disable exact current Polymarket anchors for eligible non-player markets.")
    ap.add_argument("--polymarket-direct-min-volume", type=float, default=1000.0, help="Minimum individual Polymarket market volume for direct non-player anchors.")
    ap.add_argument("--no-polymarket-player-props", action="store_true", help="Disable exact current Polymarket anchors for eligible player props.")
    ap.add_argument("--polymarket-player-min-volume", type=float, default=1000.0, help="Minimum individual Polymarket market volume for exact player-prop anchors.")
    ap.add_argument("--no-polymarket-count-direct", action="store_true", help="Disable exact current Polymarket anchors for eligible corner count markets.")
    ap.add_argument("--polymarket-count-min-volume", type=float, default=5000.0, help="Minimum individual Polymarket market volume for exact corner count anchors.")
    ap.add_argument("--no-lineup-write", action="store_true", help="Do not persist pulled ESPN lineup minutes.")
    ap.add_argument("--no-log", action="store_true", help="Print only; do not append data/logs/manual_predictions.md.")
    args = ap.parse_args()

    tz = ZoneInfo(args.tz)
    now = datetime.now(timezone.utc)
    local_date = args.local_date or now.astimezone(tz).date().isoformat()
    rows = selected_markets(
        read_open_markets(),
        local_date=local_date,
        tz=tz,
        remaining_only=not args.include_started,
        now=now,
        matches=set(args.match) if args.match else None,
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
    lineups = refresh_lineups(rows, write=not args.no_lineup_write)
    priced, diagnostics = price_rows(
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
    report = markdown_report(
        priced,
        diagnostics,
        lineups,
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
    print(report)

    if not args.no_log:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write("\n" + report)
        print(f"logged {LOG_PATH}")


if __name__ == "__main__":
    main()
