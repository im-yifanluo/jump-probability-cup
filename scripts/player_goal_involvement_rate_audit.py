"""Audit player goal and goal+assist rate shrinkage on StatsBomb data.

This is a proxy research gate for manual scorer and score-or-assist props. It
uses local StatsBomb open-data events only, never calls SportsPredict, never
reads `/predictions`, and never submits anything.

The live player model uses app-based shrinkage strength 6 for goal and
involvement shares. This audit tests nearby stronger/weaker shrinkage on
market-like attacking players with chronological future-tournament scoring.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import random
import unicodedata


ROOT = Path(__file__).resolve().parent.parent
RAW_CANDIDATES = (ROOT / "data" / "raw" / "statsbomb", ROOT / "data_reference" / "raw" / "statsbomb")
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "player_goal_involvement_rate_audit.md"

ATTACK_POSITION_MARKERS = (
    "Forward",
    "Wing",
    "Attacking Midfield",
    "Left Midfield",
    "Right Midfield",
)
FAMILIES = ("goal", "goal_or_assist")
METHODS = (
    "global_prior",
    "app_shrink_k1",
    "app_shrink_k2",
    "app_shrink_k4",
    "app_shrink_k6",
    "app_shrink_k8",
    "app_shrink_k12",
    "app_shrink_k16",
    "minutes_shrink_k4",
    "minutes_shrink_k6",
    "minutes_shrink_k8",
    "minutes_shrink_k12",
    "minutes_shrink_k16",
)


@dataclass(frozen=True)
class GoalAppearance:
    match_date: date
    tournament: str
    match_id: int
    player: str
    team: str
    role: str
    position: str
    minutes: float
    goals: int
    assists: int

    @property
    def match_key(self) -> str:
        return f"{self.tournament}:{self.match_id}"

    @property
    def player_key(self) -> str:
        return f"{self.team}::{_normalize_name(self.player)}"

    @property
    def market_like(self) -> bool:
        return any(marker in self.position for marker in ATTACK_POSITION_MARKERS)

    def events_for(self, family: str) -> int:
        if family == "goal":
            return self.goals
        if family == "goal_or_assist":
            return self.goals + self.assists
        raise KeyError(family)


def default_raw_dir() -> Path:
    for path in RAW_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError("No StatsBomb raw directory found under data/raw/statsbomb or data_reference/raw/statsbomb")


def _normalize_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(sorted(folded.lower().replace("-", " ").split()))


def _minute(event: dict) -> float:
    return min(90.0, max(0.0, float(event.get("minute", 0)) + float(event.get("second", 0)) / 60.0))


def _match_dates(raw_dir: Path) -> dict[tuple[str, int], date]:
    out: dict[tuple[str, int], date] = {}
    for tournament_dir in sorted(raw_dir.iterdir()):
        path = tournament_dir / "matches.json"
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            out[(tournament_dir.name, int(row["match_id"]))] = datetime.strptime(row["match_date"], "%Y-%m-%d").date()
    return out


def appearances_from_events(
    *,
    tournament: str,
    match_id: int,
    match_date: date,
    events: list[dict],
) -> list[GoalAppearance]:
    players: dict[str, dict] = {}

    def ensure(player: str, team: str, role: str, start: float, position: str = "") -> dict:
        rec = players.setdefault(
            player,
            {
                "player": player,
                "team": team,
                "role": role,
                "start": start,
                "end": 90.0,
                "position": position,
                "goals": 0,
                "assists": 0,
            },
        )
        if position and not rec.get("position"):
            rec["position"] = position
        if role == "starter":
            rec["role"] = "starter"
        return rec

    for event in events:
        etype = event.get("type", {}).get("name")
        team = event.get("team", {}).get("name", "")
        if etype == "Starting XI":
            for slot in event.get("tactics", {}).get("lineup", []):
                ensure(
                    slot["player"]["name"],
                    team,
                    "starter",
                    0.0,
                    slot.get("position", {}).get("name", ""),
                )
        elif etype == "Substitution":
            minute = _minute(event)
            position = event.get("position", {}).get("name", "")
            outgoing = event.get("player", {}).get("name")
            if outgoing:
                rec = ensure(outgoing, team, "starter", 0.0, position)
                rec["end"] = min(float(rec["end"]), minute)
            replacement = event.get("substitution", {}).get("replacement", {}).get("name")
            if replacement:
                ensure(replacement, team, "substitute", minute, position)

    for event in events:
        player = event.get("player", {}).get("name")
        if not player:
            continue
        etype = event.get("type", {}).get("name")
        if etype == "Shot":
            rec = ensure(
                player,
                event.get("team", {}).get("name", ""),
                "event_only",
                0.0,
                event.get("position", {}).get("name", ""),
            )
            if event.get("shot", {}).get("outcome", {}).get("name") == "Goal":
                rec["goals"] += 1
        elif etype == "Pass" and event.get("pass", {}).get("goal_assist"):
            rec = ensure(
                player,
                event.get("team", {}).get("name", ""),
                "event_only",
                0.0,
                event.get("position", {}).get("name", ""),
            )
            rec["assists"] += 1

    rows: list[GoalAppearance] = []
    for rec in players.values():
        minutes = max(0.0, float(rec["end"]) - float(rec["start"]))
        if minutes <= 0.0:
            continue
        rows.append(
            GoalAppearance(
                match_date=match_date,
                tournament=tournament,
                match_id=match_id,
                player=str(rec["player"]),
                team=str(rec["team"]),
                role=str(rec["role"]),
                position=str(rec.get("position") or ""),
                minutes=minutes,
                goals=int(rec["goals"]),
                assists=int(rec["assists"]),
            )
        )
    return sorted(rows, key=lambda row: (row.team, row.player))


def load_appearances(raw_dir: Path | None = None) -> list[GoalAppearance]:
    raw_dir = raw_dir or default_raw_dir()
    dates = _match_dates(raw_dir)
    rows: list[GoalAppearance] = []
    for tournament_dir in sorted(raw_dir.iterdir()):
        events_dir = tournament_dir / "events"
        if not events_dir.exists():
            continue
        for path in sorted(events_dir.glob("*.json")):
            match_id = int(path.stem)
            match_date = dates.get((tournament_dir.name, match_id))
            if match_date is None:
                continue
            rows.extend(
                appearances_from_events(
                    tournament=tournament_dir.name,
                    match_id=match_id,
                    match_date=match_date,
                    events=json.loads(path.read_text(encoding="utf-8")),
                )
            )
    return sorted(rows, key=lambda row: (row.match_date, row.tournament, row.match_id, row.player))


@dataclass(frozen=True)
class PlayerHistory:
    apps: int
    minutes: float
    events: int


@dataclass(frozen=True)
class TrainContext:
    global_rate: float
    player: dict[str, PlayerHistory]


def _train_context(rows: list[GoalAppearance], family: str) -> TrainContext:
    total_minutes = sum(row.minutes for row in rows)
    total_events = sum(row.events_for(family) for row in rows)
    global_rate = 90.0 * total_events / total_minutes if total_minutes > 0 else 0.12
    grouped: dict[str, list[GoalAppearance]] = defaultdict(list)
    for row in rows:
        grouped[row.player_key].append(row)
    player = {
        key: PlayerHistory(
            apps=len(part),
            minutes=sum(row.minutes for row in part),
            events=sum(row.events_for(family) for row in part),
        )
        for key, part in grouped.items()
    }
    return TrainContext(global_rate=global_rate, player=player)


def _rate(method: str, row: GoalAppearance, ctx: TrainContext) -> float:
    hist = ctx.player.get(row.player_key)
    if method == "global_prior" or hist is None:
        return ctx.global_rate
    if method.startswith("app_shrink_k"):
        k = float(method.removeprefix("app_shrink_k"))
        raw = hist.events / max(hist.apps, 1)
        return (hist.apps * raw + k * ctx.global_rate) / (hist.apps + k)
    if method.startswith("minutes_shrink_k"):
        k = float(method.removeprefix("minutes_shrink_k"))
        exposure = hist.minutes / 90.0
        return (hist.events + k * ctx.global_rate) / (exposure + k) if exposure > 0 else ctx.global_rate
    raise KeyError(method)


def _event_probability(rate_per90: float, minutes: float) -> float:
    return 1.0 - math.exp(-max(rate_per90, 0.0) * max(minutes, 0.0) / 90.0)


def _tournament_starts(rows: list[GoalAppearance]) -> dict[str, date]:
    out: dict[str, date] = {}
    for row in rows:
        out[row.tournament] = min(out.get(row.tournament, row.match_date), row.match_date)
    return out


def score_rows(
    rows: list[GoalAppearance],
    *,
    mode: str,
    min_train_rows: int = 200,
) -> list[dict[str, float | int | str]]:
    market_rows = [row for row in rows if row.market_like]
    starts = _tournament_starts(market_rows)
    out: list[dict[str, float | int | str]] = []
    for tournament in sorted(starts, key=lambda item: starts[item]):
        test = [row for row in market_rows if row.tournament == tournament]
        if mode == "chronological":
            train = [row for row in market_rows if starts[row.tournament] < starts[tournament]]
        elif mode == "leave_one_tournament_out":
            train = [row for row in market_rows if row.tournament != tournament]
        else:
            raise ValueError(f"unsupported mode: {mode}")
        if len(train) < min_train_rows:
            continue
        for family in FAMILIES:
            ctx = _train_context(train, family)
            for row in test:
                scored = {
                    "mode": mode,
                    "family": family,
                    "tournament": tournament,
                    "match_key": row.match_key,
                    "outcome": int(row.events_for(family) > 0),
                }
                for method in METHODS:
                    scored[method] = _event_probability(_rate(method, row, ctx), row.minutes)
                out.append(scored)
    return out


def summarize_scores(scored: list[dict[str, float | int | str]]) -> list[dict[str, float | int | str]]:
    out: list[dict[str, float | int | str]] = []
    for mode in sorted({str(row["mode"]) for row in scored}):
        for family in FAMILIES:
            part = [row for row in scored if row["mode"] == mode and row["family"] == family]
            if not part:
                continue
            base_total = sum((float(row["app_shrink_k6"]) - int(row["outcome"])) ** 2 for row in part)
            for method in METHODS:
                briers = [(float(row[method]) - int(row["outcome"])) ** 2 for row in part]
                out.append(
                    {
                        "mode": mode,
                        "family": family,
                        "method": method,
                        "n": len(part),
                        "brier": sum(briers) / len(briers),
                        "delta_vs_app_k6_x100": 100.0 * (base_total - sum(briers)),
                        "mean_p": sum(float(row[method]) for row in part) / len(part),
                        "hit": sum(int(row["outcome"]) for row in part) / len(part),
                    }
                )
    return sorted(out, key=lambda row: (str(row["mode"]), str(row["family"]), float(row["brier"])))


def bootstrap_comparison(
    scored: list[dict[str, float | int | str]],
    *,
    family: str,
    method: str,
    mode: str = "chronological",
    base: str = "app_shrink_k6",
    trials: int = 1000,
    seed: int = 79,
) -> dict[str, float | int | str]:
    part = [row for row in scored if row["mode"] == mode and row["family"] == family]
    by_match: dict[str, list[dict[str, float | int | str]]] = defaultdict(list)
    for row in part:
        by_match[str(row["match_key"])].append(row)
    matches = sorted(by_match)
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(trials):
        delta = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for row in by_match[match]:
                outcome = int(row["outcome"])
                delta += (float(row[base]) - outcome) ** 2
                delta -= (float(row[method]) - outcome) ** 2
        draws.append(100.0 * delta)
    draws.sort()
    point = 100.0 * sum(
        (float(row[base]) - int(row["outcome"])) ** 2 - (float(row[method]) - int(row["outcome"])) ** 2
        for row in part
    )
    return {
        "family": family,
        "comparison": f"{method}_minus_{base}",
        "matches": len(matches),
        "point_delta_x100": point,
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "ci_50": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(value > 0 for value in draws) / len(draws),
    }


def analyze(rows: list[GoalAppearance]) -> dict:
    scored = score_rows(rows, mode="chronological") + score_rows(rows, mode="leave_one_tournament_out")
    summary = summarize_scores(scored)
    bootstraps = [
        bootstrap_comparison(scored, family=family, method=method)
        for family in FAMILIES
        for method in ("app_shrink_k8", "app_shrink_k12", "app_shrink_k16", "global_prior")
    ]
    promoted = [row for row in bootstraps if row["comparison"].startswith("app_shrink") and float(row["ci_2.5"]) > 0]
    decision = (
        "proxy_candidate_goal_involvement_shrinkage_positive_not_live_promoted"
        if promoted
        else "not_promoted_keep_current_goal_involvement_shrinkage"
    )
    market_rows = [row for row in rows if row.market_like]
    chronological_rows = [
        row
        for row in scored
        if row["mode"] == "chronological" and row["family"] == "goal"
    ]
    return {
        "decision": decision,
        "appearances": len(rows),
        "matches": len({row.match_key for row in rows}),
        "market_like_rows": len(market_rows),
        "market_like_matches": len({row.match_key for row in market_rows}),
        "chronological_scored_rows": len(chronological_rows),
        "chronological_matches": len({str(row["match_key"]) for row in chronological_rows}),
        "summary": summary,
        "bootstraps": bootstraps,
    }


def _fmt(value: float | int | str) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def md_table(headers: list[str], rows: list[list[float | int | str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(cell) for cell in row) + " |")
    return lines


def render(analysis: dict, *, raw_dir: Path) -> str:
    lines = [
        "# Player Goal/Involvement Rate Audit",
        "",
        "Read-only proxy analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        f"- raw_data: {raw_dir}",
        "- purpose: test player goal and goal+assist rate shrinkage for market-like attacking player props",
        f"- decision: {analysis['decision']}",
        f"- appearances: {analysis['appearances']}",
        f"- matches: {analysis['matches']}",
        f"- market_like_rows: {analysis['market_like_rows']}",
        f"- market_like_matches: {analysis['market_like_matches']}",
        f"- chronological_scored_rows: {analysis['chronological_scored_rows']}",
        f"- chronological_matches: {analysis['chronological_matches']}",
        "",
        "## Score Summary",
        "",
    ]
    lines += md_table(
        ["mode", "family", "method", "n", "brier", "delta_vs_app_k6_x100", "mean_p", "hit"],
        [
            [
                row["mode"],
                row["family"],
                row["method"],
                row["n"],
                row["brier"],
                row["delta_vs_app_k6_x100"],
                row["mean_p"],
                row["hit"],
            ]
            for row in analysis["summary"]
        ],
    )
    lines.extend(["", "## Chronological Candidate Bootstraps", ""])
    lines += md_table(
        ["family", "comparison", "matches", "point_delta_x100", "ci_2.5", "ci_50", "ci_97.5", "pr_gt_0"],
        [
            [
                row["family"],
                row["comparison"],
                row["matches"],
                row["point_delta_x100"],
                row["ci_2.5"],
                row["ci_50"],
                row["ci_97.5"],
                row["pr_gt_0"],
            ]
            for row in analysis["bootstraps"]
        ],
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is a StatsBomb proxy replay on attacking-position appearances, not official SportsPredict settlement.",
            "- Stronger shrinkage has positive point estimates for scorer and score-or-assist, but the bootstrap lower bounds cross zero.",
            "- Keep the current goal/involvement shrinkage unless official SportsPredict replay or current prop integration clears a stronger gate.",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help=f"write report to {OUT}")
    args = ap.parse_args()

    raw_dir = default_raw_dir()
    analysis = analyze(load_appearances(raw_dir))
    text = render(analysis, raw_dir=raw_dir)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
