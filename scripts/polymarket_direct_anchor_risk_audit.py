"""Risk audit for current exact Polymarket direct anchors.

This report is read-only. It does not call SportsPredict, does not read
`/predictions`, and does not submit anything. It combines the current exact
Polymarket coverage report with the archived snapshot scorer so large live
anchor shifts and fragile anchor types are visible before manual entry.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
COVERAGE = LOG_DIR / "polymarket_direct_coverage_audit.md"
SCORE = LOG_DIR / "polymarket_direct_snapshot_score.md"
OUT = LOG_DIR / "polymarket_direct_anchor_risk_audit.md"


@dataclass(frozen=True)
class CurrentAnchor:
    status: str
    match: str
    kickoff_utc: str
    model_probability: int
    anchor_probability: int
    delta_pp: int
    volume: float
    question: str
    polymarket_question: str


@dataclass(frozen=True)
class StatusEvidence:
    status: str
    latest_markets: int
    scored: int
    unscored: int
    scored_delta: float
    anchor_wins: int


@dataclass(frozen=True)
class RiskRow:
    anchor: CurrentAnchor
    risk_level: str
    tags: tuple[str, ...]
    action: str
    status_scored: int
    status_scored_delta: float | None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _clean(value: str) -> str:
    return value.strip().strip("`")


def _key_values(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^- ([A-Za-z0-9_ ./%>=#-]+):\s*(.*)$", line)
        if match:
            out[match.group(1).strip()] = match.group(2).strip()
    return out


def _table_after(text: str, heading: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if re.match(rf"^##+\s+{re.escape(heading)}\s*$", line.strip()):
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


def _int(value: str | None, default: int = 0) -> int:
    if value is None:
        return default
    cleaned = value.strip().replace("%", "").replace("+", "").replace(",", "")
    if cleaned in {"", "-"}:
        return default
    return int(float(cleaned))


def _float(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    cleaned = value.strip().replace("$", "").replace(",", "")
    if cleaned in {"", "-"}:
        return default
    return float(cleaned)


def read_current_anchors(path: Path = COVERAGE) -> list[CurrentAnchor]:
    anchors: list[CurrentAnchor] = []
    for row in _table_after(_read(path), "Matched Markets"):
        anchors.append(
            CurrentAnchor(
                status=row.get("status", ""),
                match=row.get("match", ""),
                kickoff_utc=row.get("kickoff_utc", ""),
                model_probability=_int(row.get("model")),
                anchor_probability=_int(row.get("anchor")),
                delta_pp=_int(row.get("delta_pp")),
                volume=_float(row.get("volume")),
                question=row.get("question", ""),
                polymarket_question=row.get("polymarket_question", ""),
            )
        )
    return anchors


def read_status_evidence(path: Path = SCORE) -> tuple[dict[str, StatusEvidence], dict[str, str]]:
    text = _read(path)
    keys = _key_values(text)
    evidence: dict[str, StatusEvidence] = {}
    for row in _table_after(text, "Latest Market Coverage By Anchor Type"):
        status = row.get("status", "")
        evidence[status] = StatusEvidence(
            status=status,
            latest_markets=_int(row.get("latest_markets")),
            scored=_int(row.get("scored")),
            unscored=_int(row.get("unscored")),
            scored_delta=_float(row.get("scored_delta")),
            anchor_wins=_int(row.get("anchor_wins")),
        )
    return evidence, keys


def _risk_action(tags: tuple[str, ...]) -> str:
    parts: list[str] = []
    if "LARGE_SHIFT_RECHECK" in tags:
        parts.append("verify wording and compare an independent source before lock")
    if "PLAYER_PROP_LINEUP_RECHECK" in tags:
        parts.append("confirm lineup/minutes because prop anchors do not clear lineup risk")
    if "CORNER_COUNT_RECHECK" in tags:
        parts.append("find an alternate exact corner source; do not infer a broad count edge")
    if "LOW_REPLAY_SAMPLE" in tags:
        parts.append("treat as current source only, not a historical promotion")
    if not parts:
        parts.append("keep as exact-anchor current source and continue archived scoring")
    return "; ".join(parts)


def build_risk_rows(
    anchors: list[CurrentAnchor],
    status_evidence: dict[str, StatusEvidence],
    score_keys: dict[str, str],
    *,
    large_shift_pp: int = 5,
) -> list[RiskRow]:
    scored_latest = _int(score_keys.get("scored_latest_markets"))
    promotion_min = _int(score_keys.get("historical_promotion_min_latest_markets"), default=30)
    low_replay_sample = scored_latest < promotion_min
    rows: list[RiskRow] = []
    for anchor in anchors:
        tags: list[str] = []
        evidence = status_evidence.get(anchor.status)
        if abs(anchor.delta_pp) >= large_shift_pp:
            tags.append("LARGE_SHIFT_RECHECK")
        if evidence is None or evidence.scored == 0:
            tags.append("UNSCORED_ANCHOR_TYPE")
            status_scored = 0
            status_delta = None
        else:
            status_scored = evidence.scored
            status_delta = evidence.scored_delta
            if evidence.scored_delta < 0:
                tags.append("EARLY_NEGATIVE_TYPE_SCORE")
        if low_replay_sample:
            tags.append("LOW_REPLAY_SAMPLE")
        if anchor.status == "promoted_player_prop":
            tags.append("PLAYER_PROP_LINEUP_RECHECK")
        if anchor.status == "promoted_count_corner":
            tags.append("CORNER_COUNT_RECHECK")

        if "LARGE_SHIFT_RECHECK" in tags and (
            anchor.status in {"promoted_player_prop", "promoted_count_corner"}
            or "EARLY_NEGATIVE_TYPE_SCORE" in tags
        ):
            risk_level = "HIGH"
        elif (
            "LARGE_SHIFT_RECHECK" in tags
            or "EARLY_NEGATIVE_TYPE_SCORE" in tags
            or anchor.status in {"promoted_player_prop", "promoted_count_corner"}
        ):
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        tag_tuple = tuple(dict.fromkeys(tags))
        rows.append(
            RiskRow(
                anchor=anchor,
                risk_level=risk_level,
                tags=tag_tuple,
                action=_risk_action(tag_tuple),
                status_scored=status_scored,
                status_scored_delta=status_delta,
            )
        )
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(rows, key=lambda row: (order[row.risk_level], row.anchor.kickoff_utc, row.anchor.status, row.anchor.match))


def render(
    risk_rows: list[RiskRow],
    status_evidence: dict[str, StatusEvidence],
    score_keys: dict[str, str],
    *,
    large_shift_pp: int,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(timezone.utc)
    counts = Counter(row.risk_level for row in risk_rows)
    large_shift_rows = sum(1 for row in risk_rows if "LARGE_SHIFT_RECHECK" in row.tags)
    lines = [
        "# Polymarket Direct Anchor Risk Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        "- decision: guardrail_only_no_probability_transform",
        f"- current_anchor_rows: {len(risk_rows)}",
        f"- large_shift_threshold_pp: {large_shift_pp}",
        f"- large_shift_rows: {large_shift_rows}",
        f"- high_risk_rows: {counts.get('HIGH', 0)}",
        f"- medium_risk_rows: {counts.get('MEDIUM', 0)}",
        f"- low_risk_rows: {counts.get('LOW', 0)}",
        f"- scored_latest_markets: {score_keys.get('scored_latest_markets', '?')}",
        f"- unscored_latest_markets: {score_keys.get('unscored_latest_markets', '?')}",
        f"- historical_promotion_decision: {score_keys.get('historical_promotion_decision', '?')}",
    ]
    lines.extend(
        [
            "",
            "## Risk Summary",
            "",
            "| risk_level | markets |",
            "| --- | ---: |",
        ]
    )
    for risk_level in ("HIGH", "MEDIUM", "LOW"):
        lines.append(f"| {risk_level} | {counts.get(risk_level, 0)} |")

    lines.extend(
        [
            "",
            "## Anchor Type Replay Evidence",
            "",
            "| status | latest_markets | scored | unscored | scored_delta | anchor_wins |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for status in sorted(status_evidence):
        evidence = status_evidence[status]
        lines.append(
            f"| {status} | {evidence.latest_markets} | {evidence.scored} | {evidence.unscored} | "
            f"{evidence.scored_delta:+.4f} | {evidence.anchor_wins} |"
        )

    lines.extend(
        [
            "",
            "## Current Anchor Recheck Rows",
            "",
            "| risk | status | match | kickoff_utc | model | anchor | delta_pp | volume | tags | action | question | polymarket_question |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
    )
    for row in risk_rows:
        anchor = row.anchor
        lines.append(
            f"| {row.risk_level} | {anchor.status} | {anchor.match} | {anchor.kickoff_utc} | "
            f"{anchor.model_probability}% | {anchor.anchor_probability}% | {anchor.delta_pp:+d} | "
            f"${anchor.volume:,.0f} | {', '.join(row.tags)} | {row.action} | "
            f"{anchor.question} | {anchor.polymarket_question} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This report does not change any probability by itself.",
            "- Large exact-anchor shifts, player props, and corner-count anchors are manual recheck priorities while replay sample remains small.",
            "- Keep scoring archived latest-per-market rows as results settle before treating exact anchors as historical edge.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--coverage", default=str(COVERAGE))
    ap.add_argument("--score", default=str(SCORE))
    ap.add_argument("--large-shift-pp", type=int, default=5)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    anchors = read_current_anchors(Path(args.coverage))
    status_evidence, score_keys = read_status_evidence(Path(args.score))
    text = render(
        build_risk_rows(anchors, status_evidence, score_keys, large_shift_pp=args.large_shift_pp),
        status_evidence,
        score_keys,
        large_shift_pp=args.large_shift_pp,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
