"""Score archived exact Polymarket anchor snapshots when results settle.

This is read-only against SportsPredict. It does not read `/predictions` and
never submits. The scorer joins archived snapshot rows by exact `market_id` to
SportsPredict `/results`, infers the binary outcome from the official Brier
score, and reports whether the direct anchor beat the model probability stored
in the snapshot.

The output is intentionally a Brier replay, not an official RBP replay:
`/results` does not expose field average or per-market RBP.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import brier, result_outcome  # noqa: E402
from manual.anchors import sp_get, sports_predict_key  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES, names_match  # noqa: E402


SNAPSHOT_CSV = ROOT / "data" / "logs" / "polymarket_direct_anchor_snapshots.csv"
OUT = ROOT / "data" / "logs" / "polymarket_direct_snapshot_score.md"


@dataclass(frozen=True)
class SnapshotRow:
    run_utc: datetime
    local_date: str
    status: str
    market_id: str
    match: str
    kickoff_utc: datetime
    family: str
    stat: str
    model_probability: float
    anchor_probability: float
    sports_predict_question: str
    polymarket_question: str
    market_volume: float


@dataclass(frozen=True)
class ScoredSnapshot:
    snapshot: SnapshotRow
    outcome: int

    @property
    def brier_model(self) -> float:
        return brier(self.snapshot.model_probability, self.outcome)

    @property
    def brier_anchor(self) -> float:
        return brier(self.snapshot.anchor_probability, self.outcome)

    @property
    def brier_delta(self) -> float:
        """Positive means the Polymarket anchor beat the stored model value."""

        return self.brier_model - self.brier_anchor


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_snapshots(path: Path = SNAPSHOT_CSV) -> list[SnapshotRow]:
    if not path.exists():
        return []
    rows: list[SnapshotRow] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            rows.append(
                SnapshotRow(
                    run_utc=_dt(raw["run_utc"]),
                    local_date=raw.get("local_date", ""),
                    status=raw["status"],
                    market_id=raw["market_id"],
                    match=raw["match"],
                    kickoff_utc=_dt(raw["kickoff_utc"]),
                    family=raw["family"],
                    stat=raw.get("stat", ""),
                    model_probability=float(raw["model_probability"]),
                    anchor_probability=float(raw["anchor_probability"]),
                    sports_predict_question=raw["sports_predict_question"],
                    polymarket_question=raw["polymarket_question"],
                    market_volume=float(raw.get("market_volume") or 0),
                )
            )
    return rows


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace(" vs. ", " vs ")).strip()


def semantic_issue(row: SnapshotRow) -> str | None:
    """Return a known semantic mismatch reason for archived snapshots.

    This protects the replay archive from old rows produced before a stricter
    live matching gate existed.
    """

    if row.family != "team_first_goal_game":
        return None
    q = _norm(row.polymarket_question)
    if "neither team" in q or "no goal" in q:
        return "invalid_first_goal_subject"
    if ":" not in row.polymarket_question or "to score first" not in q:
        return "invalid_first_goal_shape"
    try:
        spec = parse(row.sports_predict_question, teams=ALL_TEAM_NAMES)
    except Exception:
        return "unparseable_sports_predict_question"
    if not spec.team:
        return "missing_target_team"
    after_colon = row.polymarket_question.split(":", 1)[1]
    subject = re.split(r"\bto score first\b", after_colon, maxsplit=1, flags=re.I)[0].strip(" ?")
    if not subject or not names_match(str(spec.team), subject):
        return "invalid_first_goal_subject"
    return None


def filter_semantic_snapshots(rows: list[SnapshotRow]) -> tuple[list[SnapshotRow], dict[str, int]]:
    valid: list[SnapshotRow] = []
    issues: dict[str, int] = defaultdict(int)
    for row in rows:
        issue = semantic_issue(row)
        if issue:
            issues[issue] += 1
            continue
        valid.append(row)
    return valid, dict(sorted(issues.items()))


def read_results() -> list[dict]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    return sp_get(f"/results?lobby_id={lobby['id']}", key)


def outcomes_by_market_id(results: list[dict]) -> tuple[dict[str, int], dict[str, str]]:
    outcomes: dict[str, set[int]] = defaultdict(set)
    issues: dict[str, str] = {}
    for result in results:
        market_id = str(result.get("market_id", "")).strip()
        if not market_id:
            continue
        if result.get("market_status") not in {None, "", "settled"} and result.get("market_status") != "settled":
            continue
        try:
            outcome = result_outcome(result)
        except (KeyError, TypeError, ValueError) as exc:
            issues[market_id] = f"uninferable:{exc.__class__.__name__}"
            continue
        if outcome is None:
            issues[market_id] = "uninferable:brier_tie"
            continue
        outcomes[market_id].add(outcome)
    exact: dict[str, int] = {}
    for market_id, seen in outcomes.items():
        if len(seen) == 1 and market_id not in issues:
            exact[market_id] = next(iter(seen))
        elif len(seen) > 1:
            issues[market_id] = "ambiguous:multiple_outcomes"
    return exact, issues


def score_snapshots(snapshots: list[SnapshotRow], outcomes: dict[str, int]) -> list[ScoredSnapshot]:
    return [
        ScoredSnapshot(snapshot=row, outcome=outcomes[row.market_id])
        for row in snapshots
        if row.market_id in outcomes
    ]


def latest_snapshot_rows(rows: list[SnapshotRow]) -> list[SnapshotRow]:
    latest: dict[str, SnapshotRow] = {}
    for row in rows:
        current = latest.get(row.market_id)
        if current is None or row.run_utc > current.run_utc:
            latest[row.market_id] = row
    return sorted(latest.values(), key=lambda row: (row.kickoff_utc, row.market_id))


def latest_per_market(rows: list[ScoredSnapshot]) -> list[ScoredSnapshot]:
    latest: dict[str, ScoredSnapshot] = {}
    for row in rows:
        current = latest.get(row.snapshot.market_id)
        if current is None or row.snapshot.run_utc > current.snapshot.run_utc:
            latest[row.snapshot.market_id] = row
    return sorted(latest.values(), key=lambda row: (row.snapshot.kickoff_utc, row.snapshot.market_id))


def summary(rows: list[ScoredSnapshot]) -> dict[str, float]:
    if not rows:
        return {"n": 0, "brier_model": 0.0, "brier_anchor": 0.0, "brier_delta": 0.0, "wins": 0}
    return {
        "n": len(rows),
        "brier_model": sum(row.brier_model for row in rows) / len(rows),
        "brier_anchor": sum(row.brier_anchor for row in rows) / len(rows),
        "brier_delta": sum(row.brier_delta for row in rows) / len(rows),
        "wins": sum(1 for row in rows if row.brier_delta > 0),
    }


def grouped_summary(rows: list[ScoredSnapshot], key_fn) -> list[tuple[str, dict[str, float]]]:
    groups: dict[str, list[ScoredSnapshot]] = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    return sorted((key, summary(part)) for key, part in groups.items())


def latest_coverage_by_status(
    snapshots: list[SnapshotRow],
    scored: list[ScoredSnapshot],
) -> list[dict[str, object]]:
    latest_snapshots = latest_snapshot_rows(snapshots)
    latest_scores = latest_per_market(scored)
    score_by_market_id = {row.snapshot.market_id: row for row in latest_scores}
    by_status: dict[str, list[SnapshotRow]] = defaultdict(list)
    for row in latest_snapshots:
        by_status[row.status].append(row)

    out: list[dict[str, object]] = []
    for status, status_snapshots in sorted(by_status.items()):
        status_scored = [score_by_market_id[row.market_id] for row in status_snapshots if row.market_id in score_by_market_id]
        status_summary = summary(status_scored)
        out.append(
            {
                "status": status,
                "latest_markets": len(status_snapshots),
                "scored": len(status_scored),
                "unscored": len(status_snapshots) - len(status_scored),
                "brier_delta": status_summary["brier_delta"],
                "anchor_wins": status_summary["wins"],
            }
        )
    return out


def promotion_gate(latest: list[ScoredSnapshot], *, min_latest_markets: int = 30, min_type_markets: int = 5) -> dict[str, object]:
    """Historical promotion gate for exact anchors.

    Exact current anchors can still be used as a source hierarchy under strict
    semantic and volume gates. This gate is only for deciding whether the
    archived replay is strong enough to call that hierarchy historically
    validated.
    """

    latest_summary = summary(latest)
    negative_types = [
        status
        for status, row in grouped_summary(latest, lambda item: item.snapshot.status)
        if int(row["n"]) >= min_type_markets and float(row["brier_delta"]) < 0
    ]
    if int(latest_summary["n"]) < min_latest_markets:
        decision = "not_promoted_insufficient_sample"
        reason = f"needs >= {min_latest_markets} scored latest markets"
    elif float(latest_summary["brier_delta"]) <= 0:
        decision = "not_promoted_negative_delta"
        reason = "latest-per-market anchor delta is not positive"
    elif negative_types:
        decision = "not_promoted_negative_anchor_type"
        reason = "negative scored anchor types: " + ", ".join(negative_types)
    else:
        decision = "eligible_for_review"
        reason = "sample and sign gates passed; still review semantic failures before promotion"
    return {
        "decision": decision,
        "reason": reason,
        "min_latest_markets": min_latest_markets,
        "min_type_markets": min_type_markets,
        "latest_markets": int(latest_summary["n"]),
        "latest_delta": float(latest_summary["brier_delta"]),
        "latest_anchor_wins": int(latest_summary["wins"]),
    }


def render(
    snapshots: list[SnapshotRow],
    scored: list[ScoredSnapshot],
    issues: dict[str, str],
    *,
    results_rows: int,
    now: datetime,
    raw_snapshot_rows: int | None = None,
    semantic_exclusions: dict[str, int] | None = None,
) -> str:
    raw_snapshot_rows = len(snapshots) if raw_snapshot_rows is None else raw_snapshot_rows
    semantic_exclusions = semantic_exclusions or {}
    latest = latest_per_market(scored)
    latest_snapshots = latest_snapshot_rows(snapshots)
    scored_market_ids = {item.snapshot.market_id for item in scored}
    missing = [row for row in latest_snapshots if row.market_id not in scored_market_ids]
    missing_all_snapshot_rows = [row for row in snapshots if row.market_id not in scored_market_ids]
    overall = summary(scored)
    latest_summary = summary(latest)
    gate = promotion_gate(latest)
    lines = [
        "# Polymarket Direct Snapshot Score",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- raw_snapshot_rows: {raw_snapshot_rows}",
        f"- snapshot_rows: {len(snapshots)}",
        f"- semantically_excluded_snapshot_rows: {sum(semantic_exclusions.values())}",
        f"- unique_snapshot_markets: {len({row.market_id for row in snapshots})}",
        f"- sports_predict_results_rows: {results_rows}",
        f"- scored_snapshot_rows: {len(scored)}",
        f"- scored_latest_markets: {len(latest)}",
        f"- unscored_latest_markets: {len(missing)}",
        f"- unscored_snapshot_rows: {len(missing_all_snapshot_rows)}",
        f"- historical_promotion_min_latest_markets: {gate['min_latest_markets']}",
        f"- historical_promotion_decision: {gate['decision']}",
        f"- historical_promotion_reason: {gate['reason']}",
        "- scoring: positive brier_delta means the archived direct anchor beat the archived model probability",
        "- limitation: `/results` has no field average or per-market RBP, so this is a Brier replay only",
    ]
    for issue, count in semantic_exclusions.items():
        lines.append(f"- semantic_exclusion_{issue}: {count}")
    lines.extend(
        [
            "",
            "## Latest Market Coverage By Anchor Type",
            "",
            "| status | latest_markets | scored | unscored | scored_delta | anchor_wins |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in latest_coverage_by_status(snapshots, scored):
        lines.append(
            f"| {row['status']} | {row['latest_markets']} | {row['scored']} | {row['unscored']} | "
            f"{float(row['brier_delta']):+.4f} | {int(row['anchor_wins'])} |"
        )
    lines += [
        "",
        "## Summary",
        "",
        "| slice | n | brier_model | brier_anchor | delta | anchor_wins |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| all snapshot rows | {int(overall['n'])} | {overall['brier_model']:.4f} | "
            f"{overall['brier_anchor']:.4f} | {overall['brier_delta']:+.4f} | {int(overall['wins'])} |"
        ),
        (
            f"| latest per market | {int(latest_summary['n'])} | {latest_summary['brier_model']:.4f} | "
            f"{latest_summary['brier_anchor']:.4f} | {latest_summary['brier_delta']:+.4f} | {int(latest_summary['wins'])} |"
        ),
        "",
        "## Latest Per Market By Anchor Type",
        "",
        "| status | n | brier_model | brier_anchor | delta | anchor_wins |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for status, row in grouped_summary(latest, lambda item: item.snapshot.status):
        lines.append(
            f"| {status} | {int(row['n'])} | {row['brier_model']:.4f} | "
            f"{row['brier_anchor']:.4f} | {row['brier_delta']:+.4f} | {int(row['wins'])} |"
        )

    lines.extend(
        [
            "",
            "## Scored Latest Rows",
            "",
            "| run_utc | status | match | kickoff_utc | outcome | model | anchor | delta | question | polymarket_question |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in latest:
        snap = row.snapshot
        lines.append(
            f"| {snap.run_utc.isoformat(timespec='seconds')} | {snap.status} | {snap.match} | "
            f"{snap.kickoff_utc.isoformat(timespec='minutes')} | {row.outcome} | "
            f"{snap.model_probability:.3f} | {snap.anchor_probability:.3f} | "
            f"{row.brier_delta:+.4f} | {snap.sports_predict_question} | {snap.polymarket_question} |"
        )

    if missing:
        lines.extend(
            [
                "",
                "## Unscored Snapshot Rows",
                "",
                "| status | match | kickoff_utc | market_id | issue | question |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in missing[:50]:
            lines.append(
                f"| {row.status} | {row.match} | {row.kickoff_utc.isoformat(timespec='minutes')} | "
                f"{row.market_id} | {issues.get(row.market_id, 'not_settled_or_missing')} | {row.sports_predict_question} |"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Do not promote exact Polymarket anchors from this report until enough independent settled markets accumulate.",
            "- Prefer the `latest per market` slice for promotion decisions; repeated snapshots from the same market are operational records, not independent matches.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only /results settlement")
    ap.add_argument("--snapshot-csv", default=str(SNAPSHOT_CSV))
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    raw_snapshots = read_snapshots(Path(args.snapshot_csv))
    snapshots, semantic_exclusions = filter_semantic_snapshots(raw_snapshots)
    if args.sports_predict:
        results = read_results()
        outcomes, issues = outcomes_by_market_id(results)
    else:
        results = []
        outcomes, issues = {}, {}
    scored = score_snapshots(snapshots, outcomes)
    text = render(
        snapshots,
        scored,
        issues,
        results_rows=len(results),
        now=datetime.now(timezone.utc),
        raw_snapshot_rows=len(raw_snapshots),
        semantic_exclusions=semantic_exclusions,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
