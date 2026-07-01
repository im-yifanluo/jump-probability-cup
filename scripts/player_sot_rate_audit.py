"""Audit player shots-on-target rate shrinkage on StatsBomb tournament data.

This is a proxy research gate for manual player SOT markets. It uses local
StatsBomb open-data events only, never calls SportsPredict, never reads
`/predictions`, and never submits anything.

The live player model currently shrinks player SOT-per-appearance estimates
toward a tournament attacker prior with `SOT_SHRINK_APPS = 1`. This audit tests
that choice on market-like attacking players, using chronological
future-tournament scoring and a leave-one-tournament-out sensitivity check.
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
OUT = LOG_DIR / "player_sot_rate_audit.md"

ATTACK_POSITION_MARKERS = (
    "Forward",
    "Wing",
    "Attacking Midfield",
    "Left Midfield",
    "Right Midfield",
)
METHODS = (
    "global_prior",
    "app_shrink_k1",
    "app_shrink_k4",
    "app_shrink_k8",
    "app_shrink_k16",
    "minutes_shrink_k4",
    "minutes_shrink_k8",
    "minutes_shrink_k16",
)
THRESHOLDS = (1, 2)


@dataclass(frozen=True)
class SotAppearance:
    match_date: date
    tournament: str
    match_id: int
    player: str
    team: str
    role: str
    position: str
    minutes: float
    sot: int

    @property
    def match_key(self) -> str:
        return f"{self.tournament}:{self.match_id}"

    @property
    def player_key(self) -> str:
        return f"{self.team}::{_normalize_name(self.player)}"

    @property
    def market_like(self) -> bool:
        return any(marker in self.position for marker in ATTACK_POSITION_MARKERS)


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
) -> list[SotAppearance]:
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
                "sot": 0,
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
        if event.get("type", {}).get("name") != "Shot":
            continue
        player = event.get("player", {}).get("name")
        if not player:
            continue
        rec = ensure(
            player,
            event.get("team", {}).get("name", ""),
            "event_only",
            0.0,
            event.get("position", {}).get("name", ""),
        )
        if event.get("shot", {}).get("outcome", {}).get("name") in {"Goal", "Saved", "Saved To Post"}:
            rec["sot"] += 1

    rows: list[SotAppearance] = []
    for rec in players.values():
        start = float(rec["start"])
        end = float(rec["end"])
        minutes = max(0.0, end - start)
        if minutes <= 0.0:
            continue
        rows.append(
            SotAppearance(
                match_date=match_date,
                tournament=tournament,
                match_id=match_id,
                player=str(rec["player"]),
                team=str(rec["team"]),
                role=str(rec["role"]),
                position=str(rec.get("position") or ""),
                minutes=minutes,
                sot=int(rec["sot"]),
            )
        )
    return sorted(rows, key=lambda row: (row.team, row.player))


def load_appearances(raw_dir: Path | None = None) -> list[SotAppearance]:
    raw_dir = raw_dir or default_raw_dir()
    dates = _match_dates(raw_dir)
    rows: list[SotAppearance] = []
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


def _poisson_ge(mean: float, threshold: int) -> float:
    mean = max(float(mean), 0.0)
    term = math.exp(-mean)
    cdf = term
    for k in range(1, threshold):
        term *= mean / k
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


@dataclass(frozen=True)
class PlayerHistory:
    apps: int
    minutes: float
    sot: int


@dataclass(frozen=True)
class TrainContext:
    global_rate: float
    player: dict[str, PlayerHistory]


def _train_context(rows: list[SotAppearance]) -> TrainContext:
    total_minutes = sum(row.minutes for row in rows)
    total_sot = sum(row.sot for row in rows)
    global_rate = 90.0 * total_sot / total_minutes if total_minutes > 0 else 0.72
    grouped: dict[str, list[SotAppearance]] = defaultdict(list)
    for row in rows:
        grouped[row.player_key].append(row)
    player = {
        key: PlayerHistory(
            apps=len(part),
            minutes=sum(row.minutes for row in part),
            sot=sum(row.sot for row in part),
        )
        for key, part in grouped.items()
    }
    return TrainContext(global_rate=global_rate, player=player)


def _rate(method: str, row: SotAppearance, ctx: TrainContext) -> float:
    hist = ctx.player.get(row.player_key)
    if method == "global_prior" or hist is None:
        return ctx.global_rate
    if method.startswith("app_shrink_k"):
        k = float(method.removeprefix("app_shrink_k"))
        raw = hist.sot / max(hist.apps, 1)
        return (hist.apps * raw + k * ctx.global_rate) / (hist.apps + k)
    if method.startswith("minutes_shrink_k"):
        k = float(method.removeprefix("minutes_shrink_k"))
        exposure = hist.minutes / 90.0
        return (hist.sot + k * ctx.global_rate) / (exposure + k) if exposure > 0 else ctx.global_rate
    raise KeyError(method)


def _tournament_starts(rows: list[SotAppearance]) -> dict[str, date]:
    out: dict[str, date] = {}
    for row in rows:
        out[row.tournament] = min(out.get(row.tournament, row.match_date), row.match_date)
    return out


def score_rows(
    rows: list[SotAppearance],
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
        ctx = _train_context(train)
        for row in test:
            for threshold in THRESHOLDS:
                scored = {
                    "mode": mode,
                    "tournament": tournament,
                    "match_key": row.match_key,
                    "threshold": threshold,
                    "outcome": int(row.sot >= threshold),
                }
                for method in METHODS:
                    scored[method] = _poisson_ge(_rate(method, row, ctx) * row.minutes / 90.0, threshold)
                out.append(scored)
    return out


def summarize_scores(scored: list[dict[str, float | int | str]]) -> list[dict[str, float | int | str]]:
    out: list[dict[str, float | int | str]] = []
    for mode in sorted({str(row["mode"]) for row in scored}):
        for threshold in THRESHOLDS:
            part = [row for row in scored if row["mode"] == mode and row["threshold"] == threshold]
            if not part:
                continue
            base_briers = [(float(row["app_shrink_k1"]) - int(row["outcome"])) ** 2 for row in part]
            base_total = sum(base_briers)
            for method in METHODS:
                briers = [(float(row[method]) - int(row["outcome"])) ** 2 for row in part]
                out.append(
                    {
                        "mode": mode,
                        "threshold": threshold,
                        "method": method,
                        "n": len(part),
                        "brier": sum(briers) / len(briers),
                        "delta_vs_app_k1_x100": 100.0 * (base_total - sum(briers)),
                        "mean_p": sum(float(row[method]) for row in part) / len(part),
                        "hit": sum(int(row["outcome"]) for row in part) / len(part),
                    }
                )
    return sorted(out, key=lambda row: (str(row["mode"]), int(row["threshold"]), float(row["brier"])))


def bootstrap_comparison(
    scored: list[dict[str, float | int | str]],
    *,
    mode: str,
    threshold: int,
    method: str,
    base: str = "app_shrink_k1",
    trials: int = 1000,
    seed: int = 73,
) -> dict[str, float | int | str]:
    part = [row for row in scored if row["mode"] == mode and row["threshold"] == threshold]
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
        "mode": mode,
        "threshold": threshold,
        "comparison": f"{method}_minus_{base}",
        "matches": len(matches),
        "point_delta_x100": point,
        "ci_2.5": draws[int(0.025 * (trials - 1))],
        "ci_50": draws[int(0.500 * (trials - 1))],
        "ci_97.5": draws[int(0.975 * (trials - 1))],
        "pr_gt_0": sum(value > 0 for value in draws) / len(draws),
    }


def analyze(rows: list[SotAppearance]) -> dict:
    scored = score_rows(rows, mode="chronological") + score_rows(rows, mode="leave_one_tournament_out")
    summary = summarize_scores(scored)
    bootstraps = [
        bootstrap_comparison(scored, mode="chronological", threshold=threshold, method=method)
        for threshold in THRESHOLDS
        for method in ("app_shrink_k8", "minutes_shrink_k8", "global_prior")
    ]
    app8_thr1 = next(
        row for row in bootstraps if row["threshold"] == 1 and row["comparison"] == "app_shrink_k8_minus_app_shrink_k1"
    )
    app8_thr2 = next(
        row for row in bootstraps if row["threshold"] == 2 and row["comparison"] == "app_shrink_k8_minus_app_shrink_k1"
    )
    if float(app8_thr1["ci_2.5"]) > 0 and float(app8_thr2["ci_2.5"]) <= 0:
        decision = "proxy_candidate_app_shrink_k8_positive_for_1plus_only_not_live_promoted"
    elif float(app8_thr1["ci_2.5"]) > 0 and float(app8_thr2["ci_2.5"]) > 0:
        decision = "proxy_candidate_app_shrink_k8_positive_all_sot_thresholds_not_live_promoted"
    else:
        decision = "not_promoted_keep_current_sot_shrinkage"
    market_rows = [row for row in rows if row.market_like]
    chronological_rows = [row for row in scored if row["mode"] == "chronological" and row["threshold"] == 1]
    loto_rows = [row for row in scored if row["mode"] == "leave_one_tournament_out" and row["threshold"] == 1]
    return {
        "decision": decision,
        "appearances": len(rows),
        "matches": len({row.match_key for row in rows}),
        "market_like_rows": len(market_rows),
        "market_like_matches": len({row.match_key for row in market_rows}),
        "chronological_scored_rows": len(chronological_rows),
        "chronological_matches": len({str(row["match_key"]) for row in chronological_rows}),
        "loto_scored_rows": len(loto_rows),
        "loto_matches": len({str(row["match_key"]) for row in loto_rows}),
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
        "# Player SOT Rate Audit",
        "",
        "Read-only proxy analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        f"- raw_data: {raw_dir}",
        "- purpose: test player SOT-rate shrinkage for market-like attacking player props",
        f"- decision: {analysis['decision']}",
        f"- appearances: {analysis['appearances']}",
        f"- matches: {analysis['matches']}",
        f"- market_like_rows: {analysis['market_like_rows']}",
        f"- market_like_matches: {analysis['market_like_matches']}",
        f"- chronological_scored_rows: {analysis['chronological_scored_rows']}",
        f"- chronological_matches: {analysis['chronological_matches']}",
        f"- loto_scored_rows: {analysis['loto_scored_rows']}",
        f"- loto_matches: {analysis['loto_matches']}",
        "",
        "## Method Definitions",
        "",
    ]
    lines += md_table(
        ["method", "definition"],
        [
            ["global_prior", "chronological tournament attacker SOT-per-90 prior only"],
            ["app_shrink_k1", "current live approximation: player SOT/app shrunk by 1 app toward the attacker prior"],
            ["app_shrink_k4/k8/k16", "same deployable app-based shrinkage with stronger prior weight"],
            ["minutes_shrink_k4/k8/k16", "player SOT per 90-minute exposure shrunk toward the attacker prior; proxy candidate requiring a live data-path change"],
        ],
    )
    lines.extend(["", "## Score Summary", ""])
    lines += md_table(
        ["mode", "threshold", "method", "n", "brier", "delta_vs_app_k1_x100", "mean_p", "hit"],
        [
            [
                row["mode"],
                row["threshold"],
                row["method"],
                row["n"],
                row["brier"],
                row["delta_vs_app_k1_x100"],
                row["mean_p"],
                row["hit"],
            ]
            for row in analysis["summary"]
        ],
    )
    lines.extend(["", "## Chronological Candidate Bootstraps", ""])
    lines += md_table(
        ["threshold", "comparison", "matches", "point_delta_x100", "ci_2.5", "ci_50", "ci_97.5", "pr_gt_0"],
        [
            [
                row["threshold"],
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
            "- Stronger app-based shrinkage is positive for 1+ SOT in the chronological proxy replay.",
            "- The same candidate does not clear the bootstrap lower-bound gate for 2+ SOT, so high-threshold player SOT rows remain prop/source/lineup-led.",
            "- Do not change live player SOT probabilities from this audit alone; exact prop odds and confirmed lineup minutes still dominate manual entries.",
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
