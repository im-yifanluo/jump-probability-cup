"""Audit live special-market rows against StatsBomb proxy evidence.

This is a read-only manual-entry diagnostic. It reads open SportsPredict
markets, prices special rows through the same manual stack, and attaches the
special backtest evidence that supports each row. It does not read
`/predictions` and never submits.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from full_manual_pricing import (  # noqa: E402
    PlayerPropAnchors,
    MarketRow,
    price_rows,
    read_open_markets,
    selected_markets,
)
from manual.policy import is_special_market  # noqa: E402
from questions.parser import MarketSpec, parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402
from special_market_backtest import (  # noqa: E402
    COUNT_THRESHOLD_PRIORS,
    SPECIAL_FIELDS,
    loto,
    loto_threshold,
    rows as statsbomb_rows,
    tournament_heterogeneity_rows,
)


OUT = ROOT / "data" / "logs" / "special_market_live_audit.md"
COUNT_SOURCE_TEMPLATE = ROOT / "data" / "special_count_sources_template.csv"


@dataclass(frozen=True)
class SpecialEvidence:
    key: str
    evidence_type: str
    confidence: str
    n: int
    hit_rate: float
    wilson_low: float
    wilson_high: float
    brier_loto: float
    edge_vs_50: float
    heterogeneity: str


def special_evidence_key(spec: MarketSpec) -> str | None:
    if spec.family == "goal_before_hydration":
        return "goal_before_30"
    if spec.family == "goal_after_first_hydration_1h":
        return "goal_after_30_1h"
    if spec.family == "goal_after_second_hydration":
        return "goal_after_75"
    if spec.family == "goal_in_stoppage":
        return "goal_1h_stoppage" if spec.period == "1h" else "goal_2h_stoppage"
    if spec.family == "offside_before_hydration":
        return "offside_before_30"
    if spec.family == "card_after_second_hydration":
        return "card_after_75"
    if spec.family == "card_in_stoppage_any_half":
        return "card_stoppage"
    if spec.family == "first_card_before_first_goal":
        return "first_card_before_first_goal"
    if spec.family == "substitution_before_halftime":
        return "substitution_before_halftime"
    if spec.family == "substitute_scores":
        return "substitute_goal"
    if spec.family == "own_goal":
        return "own_goal"
    if spec.family == "outside_box_goal":
        return "outside_box_goal"
    if spec.family == "header_goal":
        return "header_goal"
    if spec.family == "any_player_goals_ge" and spec.threshold == 1:
        return "any_player_goal"
    if spec.family == "any_player_goals_ge" and spec.threshold == 2:
        return "any_player_brace"
    if spec.family == "any_player_sot_ge" and spec.threshold == 2:
        return "any_player_2_sot"
    if spec.family == "total_count_ge" and spec.stat == "shots" and spec.threshold:
        return f"total_shots_ge_{spec.threshold}"
    if spec.family == "total_count_ge" and spec.stat == "substitutions" and spec.threshold:
        return f"total_substitutions_ge_{spec.threshold}"
    if spec.family == "total_count_before_hydration" and spec.stat == "corners" and spec.threshold:
        return f"early_corners_ge_{spec.threshold}"
    return None


def evidence_table(data: list[dict]) -> dict[str, SpecialEvidence]:
    out: dict[str, SpecialEvidence] = {}
    heterogeneity = {str(row["key"]): str(row["heterogeneity"]) for row in tournament_heterogeneity_rows(data)}
    for key in SPECIAL_FIELDS:
        row = loto(data, key)
        out[key] = SpecialEvidence(
            key=key,
            evidence_type="binary_loto_base_prior",
            confidence=str(row["confidence_tier"]),
            n=int(row["n"]),
            hit_rate=float(row["hit"]),
            wilson_low=float(row["wilson_low"]),
            wilson_high=float(row["wilson_high"]),
            brier_loto=float(row["brier_loto"]),
            edge_vs_50=float(row["edge_vs_50"]),
            heterogeneity=heterogeneity.get(key, ""),
        )
    for key, value_field, threshold in COUNT_THRESHOLD_PRIORS:
        row = loto_threshold(data, value_field, threshold)
        out[key] = SpecialEvidence(
            key=key,
            evidence_type="count_threshold_loto_base_prior",
            confidence=str(row["confidence_tier"]),
            n=int(row["n"]),
            hit_rate=float(row["hit"]),
            wilson_low=float(row["wilson_low"]),
            wilson_high=float(row["wilson_high"]),
            brier_loto=float(row["brier_loto"]),
            edge_vs_50=float(row["edge_vs_50"]),
            heterogeneity=heterogeneity.get(key, ""),
        )
    return out


def risk_state(evidence: SpecialEvidence | None, *, model_probability: float) -> str:
    if evidence is None:
        return "UNMAPPED_PROXY"
    scaled_shift = abs(model_probability - evidence.hit_rate)
    if evidence.confidence == "weak_near_coinflip":
        return "WEAK_PRIOR"
    if evidence.evidence_type == "count_threshold_loto_base_prior":
        if scaled_shift >= 0.15:
            return "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE"
        if evidence.heterogeneity == "high":
            return "COUNT_THRESHOLD_HIGH_HETEROGENEITY"
        return "COUNT_THRESHOLD_PROXY"
    if evidence.heterogeneity == "high":
        return "HIGH_HETEROGENEITY_PROXY"
    if scaled_shift >= 0.15:
        return "SCALED_FAR_FROM_BASE"
    return "BASE_PRIOR"


def risk_action(risk_state_: str) -> str:
    if risk_state_ == "WEAK_PRIOR":
        return "near-coinflip family; compare current source before treating as edge"
    if risk_state_ == "COUNT_THRESHOLD_PROXY":
        return "count-threshold proxy; verify current count-volume input if time allows"
    if risk_state_ == "COUNT_THRESHOLD_HIGH_HETEROGENEITY":
        return "high-heterogeneity count threshold; verify current count-volume input before lock"
    if risk_state_ == "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE":
        return "far from no-match-info base; verify count-volume input and current count sources before lock"
    if risk_state_ == "HIGH_HETEROGENEITY_PROXY":
        return "high cross-tournament spread; require direct current evidence for strong confidence"
    if risk_state_ == "SCALED_FAR_FROM_BASE":
        return "far from no-match-info base; recheck match-volume input before lock"
    if risk_state_ == "UNMAPPED_PROXY":
        return "unmapped special proxy; do targeted research before manual entry"
    return "usable base prior; still lower confidence than odds-anchored markets"


def live_rows(
    *,
    local_date: str,
    tz: ZoneInfo,
    all_open: bool,
    include_started: bool,
    matches: set[str] | None,
) -> list[dict]:
    now = datetime.now(timezone.utc)
    open_rows = read_open_markets()
    if all_open:
        grace = timedelta(minutes=10)
        selected: list[MarketRow] = []
        for row in open_rows:
            if matches and row.match_name not in matches:
                continue
            if not include_started and row.kickoff < now.astimezone(timezone.utc) - grace:
                continue
            selected.append(row)
        selected = sorted(selected, key=lambda row: (row.kickoff, row.match_name, row.market_id))
    else:
        selected = selected_markets(
            open_rows,
            local_date=local_date,
            tz=tz,
            remaining_only=not include_started,
            now=now,
            matches=matches,
        )
    special = []
    for row in selected:
        try:
            spec = parse(row.question, teams=ALL_TEAM_NAMES)
        except Exception:
            continue
        if is_special_market(spec):
            special.append(row)
    priced, _diagnostics = price_rows(
        special,
        lineups={},
        field_by_market={},
        player_prop_anchors=PlayerPropAnchors.empty(),
        player_no_field_base=None,
        polymarket_events=[],
    )
    return priced


def audit_rows(priced: list[dict], evidence: dict[str, SpecialEvidence]) -> list[dict]:
    out = []
    for row in priced:
        spec = parse(row["question"], teams=ALL_TEAM_NAMES)
        key = special_evidence_key(spec)
        ev = evidence.get(key or "")
        model_probability = float(row["p_raw"])
        state = risk_state(ev, model_probability=model_probability)
        signed_shift_pp = round((model_probability - ev.hit_rate) * 100) if ev else 0
        out.append(
            {
                **row,
                "family": spec.family,
                "stat": spec.stat or "",
                "threshold": spec.threshold or "",
                "evidence_key": key or "",
                "evidence_type": ev.evidence_type if ev else "",
                "confidence": ev.confidence if ev else "",
                "heterogeneity": ev.heterogeneity if ev else "",
                "n": ev.n if ev else 0,
                "hit_rate": ev.hit_rate if ev else 0.0,
                "wilson": f"{ev.wilson_low:.3f}-{ev.wilson_high:.3f}" if ev else "",
                "brier_loto": ev.brier_loto if ev else 0.0,
                "edge_vs_50": ev.edge_vs_50 if ev else 0.0,
                "scaled_shift_pp": round(abs(model_probability - ev.hit_rate) * 100) if ev else 0,
                "model_minus_base_pp": signed_shift_pp,
                "risk_state": state,
                "risk_action": risk_action(state),
            }
        )
    return sorted(out, key=lambda row: (row["kickoff"], row["match_name"], row["question"]))


def render(rows_: list[dict], *, local_date: str, all_open: bool, include_started: bool, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    risk_counts = Counter(row["risk_state"] for row in rows_)
    confidence_counts = Counter(row["confidence"] or "unmapped" for row in rows_)
    heterogeneity_counts = Counter(row["heterogeneity"] or "unmapped" for row in rows_)
    lines = [
        "# Special Market Live Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- local_date: {'all-open' if all_open else local_date}",
        f"- include_started: {str(include_started).lower()}",
        f"- special_rows: {len(rows_)}",
        "- purpose: attach StatsBomb proxy evidence and uncertainty to live special-market rows",
        "- interpretation: this does not change probabilities; it identifies weak/proxy-only rows needing current-source caution",
        "",
        "## Risk Summary",
        "",
        "| risk_state | rows |",
        "| --- | ---: |",
    ]
    for key, value in sorted(risk_counts.items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "| confidence | rows |", "| --- | ---: |"])
    for key, value in sorted(confidence_counts.items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "| heterogeneity | rows |", "| --- | ---: |"])
    for key, value in sorted(heterogeneity_counts.items()):
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Live Special Rows",
            "",
            "| kickoff_utc | match | market_id | entry | risk_state | confidence | heterogeneity | evidence_key | evidence_type | hit_rate | wilson_95 | scaled_shift_pp | model_minus_base_pp | action | question |",
            "| --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in rows_:
        lines.append(
            f"| {row['kickoff'].astimezone(timezone.utc).isoformat(timespec='minutes')} | "
            f"{row['match_name']} | {row.get('market_id', '')} | {row['manual_entry']} | {row['risk_state']} | "
            f"{row['confidence']} | {row['heterogeneity']} | {row['evidence_key']} | {row['evidence_type']} | "
            f"{row['hit_rate']:.3f} | {row['wilson']} | {row['scaled_shift_pp']} | "
            f"{row['model_minus_base_pp']} | {row['risk_action']} | {row['question']} |"
        )
    lines.extend(
        [
            "",
            "## Policy",
            "",
            "- `WEAK_PRIOR`: near-coinflip family in the StatsBomb leave-one-tournament-out replay; treat large current-source disagreement as a warning.",
            "- `COUNT_THRESHOLD_PROXY`: threshold prior is validated only as a no-match-info base rate; live count-volume scaling remains proxy evidence.",
            "- `COUNT_THRESHOLD_HIGH_HETEROGENEITY`: count-threshold prior also has high cross-tournament rate spread; treat it as weaker transfer evidence.",
            "- `COUNT_THRESHOLD_SCALED_FAR_FROM_BASE`: count-threshold row is proxy-only and at least 15 points from the no-match-info threshold prior; recheck count-volume inputs before lock.",
            "- `HIGH_HETEROGENEITY_PROXY`: usable base prior, but cross-tournament rate spread is high; do not treat as a strong edge without direct current evidence.",
            "- `SCALED_FAR_FROM_BASE`: live odds/count-scaled probability is at least 15 points from the no-match-info base rate; recheck match-volume inputs before lock.",
            "- `BASE_PRIOR`: usable StatsBomb base prior, still lower confidence than official SportsPredict history.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def count_source_template_rows(rows_: list[dict]) -> list[dict]:
    """Special count-threshold rows needing independent current source checks."""

    return [
        row
        for row in rows_
        if row.get("evidence_type") == "count_threshold_loto_base_prior"
        and row.get("risk_state") in {
            "COUNT_THRESHOLD_PROXY",
            "COUNT_THRESHOLD_HIGH_HETEROGENEITY",
            "COUNT_THRESHOLD_SCALED_FAR_FROM_BASE",
        }
    ]


def write_count_source_template(rows_: list[dict], path: Path) -> None:
    """Write a fillable current-source CSV for special count-threshold rows."""

    selected = count_source_template_rows(rows_)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "market_id",
        "match",
        "kickoff_utc",
        "risk_state",
        "evidence_key",
        "model_entry",
        "base_hit_rate",
        "model_minus_base_pp",
        "question",
        "source_1",
        "source_1_probability",
        "source_1_count_mean",
        "source_2",
        "source_2_probability",
        "source_2_count_mean",
        "notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in selected:
            writer.writerow(
                {
                    "market_id": row.get("market_id", ""),
                    "match": row.get("match_name", ""),
                    "kickoff_utc": row["kickoff"].astimezone(timezone.utc).isoformat(timespec="minutes"),
                    "risk_state": row.get("risk_state", ""),
                    "evidence_key": row.get("evidence_key", ""),
                    "model_entry": row.get("manual_entry", ""),
                    "base_hit_rate": f"{float(row.get('hit_rate', 0.0)) * 100:.1f}%",
                    "model_minus_base_pp": row.get("model_minus_base_pp", ""),
                    "question": row.get("question", ""),
                    "source_1": "",
                    "source_1_probability": "",
                    "source_1_count_mean": "",
                    "source_2": "",
                    "source_2_probability": "",
                    "source_2_count_mean": "",
                    "notes": "",
                }
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tz", default="America/Chicago")
    ap.add_argument("--local-date", help="Local date to audit, YYYY-MM-DD. Default: today in --tz.")
    ap.add_argument("--all-open", action="store_true", help="Audit all currently open future special markets.")
    ap.add_argument("--include-started", action="store_true", help="Include matches that have already kicked off.")
    ap.add_argument("--match", action="append", help="Restrict to an exact SportsPredict match name. Repeatable.")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    ap.add_argument(
        "--count-source-template",
        nargs="?",
        const=str(COUNT_SOURCE_TEMPLATE),
        help=f"write current-source CSV template for special count-threshold rows; default path {COUNT_SOURCE_TEMPLATE}",
    )
    args = ap.parse_args()

    tz = ZoneInfo(args.tz)
    now = datetime.now(timezone.utc)
    local_date = args.local_date or now.astimezone(tz).date().isoformat()
    priced = live_rows(
        local_date=local_date,
        tz=tz,
        all_open=args.all_open,
        include_started=args.include_started,
        matches=set(args.match) if args.match else None,
    )
    audited = audit_rows(priced, evidence_table(statsbomb_rows()))
    text = render(audited, local_date=local_date, all_open=args.all_open, include_started=args.include_started, now=now)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")
    if args.count_source_template:
        template_path = Path(args.count_source_template)
        write_count_source_template(audited, template_path)
        print(f"wrote {template_path}")


if __name__ == "__main__":
    main()
