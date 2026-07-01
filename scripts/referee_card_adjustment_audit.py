"""Audit whether referee-specific card history improves card-threshold priors.

This is a local proxy research gate. It uses Football-Data CSVs with referee
and card columns, never calls SportsPredict, never reads `/predictions`, and
never submits anything.

The live manual rule is intentionally conservative: if the SportsPredict field
average is visible for card-count rows, use it; otherwise keep neutral/raw card
probabilities unless a current referee is verified and an independent card-rate
source is archived. This audit tests whether referee-specific historical card
rates are strong enough to justify more work on that branch.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parent.parent
RAW_CANDIDATES = (ROOT / "data" / "raw" / "fduk", ROOT / "data_reference" / "raw" / "fduk")
LOG_DIR = ROOT / "data" / "logs"
OUT = LOG_DIR / "referee_card_adjustment_audit.md"

THRESHOLDS = (3, 4, 5, 6)
METHODS = (
    "global_prior",
    "league_prior",
    "referee_shrink10",
    "referee_shrink20",
    "referee_shrink40",
    "league_poisson",
    "referee_cap1_poisson",
)


@dataclass(frozen=True)
class CardMatch:
    key: str
    match_date: date
    div: str
    season_file: str
    home: str
    away: str
    referee: str
    total_cards: int


@dataclass(frozen=True)
class ScoreRow:
    match_key: str
    threshold: int
    method: str
    probability: float
    outcome: int
    brier: float


def default_raw_dir() -> Path:
    for path in RAW_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError("No Football-Data raw directory found under data/raw/fduk or data_reference/raw/fduk")


def _parse_date(value: str) -> date | None:
    value = value.strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    value = str(value).strip()
    if value in {"", "-"}:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def load_matches(raw_dir: Path | None = None) -> list[CardMatch]:
    raw_dir = raw_dir or default_raw_dir()
    rows: list[CardMatch] = []
    for path in sorted(raw_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "Referee" not in reader.fieldnames:
                continue
            for idx, row in enumerate(reader, start=2):
                ref = (row.get("Referee") or "").strip()
                match_date = _parse_date(row.get("Date", ""))
                cards = [_int(row.get(name)) for name in ("HY", "AY", "HR", "AR")]
                if not ref or match_date is None or any(value is None for value in cards):
                    continue
                home = (row.get("HomeTeam") or "").strip()
                away = (row.get("AwayTeam") or "").strip()
                div = (row.get("Div") or path.stem.split("_", 1)[-1]).strip()
                rows.append(
                    CardMatch(
                        key=f"{path.stem}:{idx}:{home}:{away}",
                        match_date=match_date,
                        div=div,
                        season_file=path.name,
                        home=home,
                        away=away,
                        referee=ref,
                        total_cards=sum(value or 0 for value in cards),
                    )
                )
    return sorted(rows, key=lambda row: (row.match_date, row.season_file, row.key))


def _smooth(successes: int, n: int, base: float, shrink: float) -> float:
    return (successes + base * shrink) / max(n + shrink, 1e-9)


def _poisson_ge(mean: float, threshold: int) -> float:
    mean = max(mean, 1e-9)
    cumulative = 0.0
    term = math.exp(-mean)
    for k in range(threshold):
        if k == 0:
            term = math.exp(-mean)
        elif k > 0:
            term *= mean / k
        cumulative += term
    return min(0.99, max(0.01, 1.0 - cumulative))


def _brier(probability: float, outcome: int) -> float:
    p = min(0.99, max(0.01, probability))
    return (p - outcome) ** 2


def replay(
    matches: list[CardMatch],
    *,
    min_global_train: int = 500,
    min_ref_matches: int = 5,
) -> list[ScoreRow]:
    global_n = 0
    global_total_cards = 0.0
    global_success = {threshold: 0 for threshold in THRESHOLDS}
    league_n: dict[str, int] = defaultdict(int)
    league_total_cards: dict[str, float] = defaultdict(float)
    league_success: dict[str, dict[int, int]] = defaultdict(lambda: {threshold: 0 for threshold in THRESHOLDS})
    ref_n: dict[str, int] = defaultdict(int)
    ref_total_cards: dict[str, float] = defaultdict(float)
    ref_success: dict[str, dict[int, int]] = defaultdict(lambda: {threshold: 0 for threshold in THRESHOLDS})
    scored: list[ScoreRow] = []

    for match in matches:
        if global_n >= min_global_train:
            league_count = league_n[match.div]
            league_mean = (
                (league_total_cards[match.div] + (global_total_cards / global_n) * 100.0)
                / max(league_count + 100.0, 1e-9)
            )
            ref_count = ref_n[match.referee]
            ref_mean_raw = ref_total_cards[match.referee] / ref_count if ref_count else league_mean
            ref_mean = (ref_total_cards[match.referee] + league_mean * 20.0) / max(ref_count + 20.0, 1e-9)
            factor = min(1.0, max(0.6, ref_mean / max(league_mean, 1e-9)))

            for threshold in THRESHOLDS:
                outcome = int(match.total_cards >= threshold)
                global_prior = global_success[threshold] / global_n
                league_prior = _smooth(
                    league_success[match.div][threshold],
                    league_count,
                    global_prior,
                    100.0,
                )
                ref_base = league_prior
                if ref_count < min_ref_matches:
                    ref_shrink10 = league_prior
                    ref_shrink20 = league_prior
                    ref_shrink40 = league_prior
                else:
                    ref_shrink10 = _smooth(ref_success[match.referee][threshold], ref_count, ref_base, 10.0)
                    ref_shrink20 = _smooth(ref_success[match.referee][threshold], ref_count, ref_base, 20.0)
                    ref_shrink40 = _smooth(ref_success[match.referee][threshold], ref_count, ref_base, 40.0)
                predictions = {
                    "global_prior": global_prior,
                    "league_prior": league_prior,
                    "referee_shrink10": ref_shrink10,
                    "referee_shrink20": ref_shrink20,
                    "referee_shrink40": ref_shrink40,
                    "league_poisson": _poisson_ge(league_mean, threshold),
                    "referee_cap1_poisson": _poisson_ge(league_mean * factor, threshold)
                    if ref_count >= min_ref_matches and ref_mean_raw < league_mean
                    else _poisson_ge(league_mean, threshold),
                }
                for method, probability in predictions.items():
                    scored.append(
                        ScoreRow(
                            match_key=match.key,
                            threshold=threshold,
                            method=method,
                            probability=probability,
                            outcome=outcome,
                            brier=_brier(probability, outcome),
                        )
                    )

        global_n += 1
        global_total_cards += match.total_cards
        league_n[match.div] += 1
        league_total_cards[match.div] += match.total_cards
        ref_n[match.referee] += 1
        ref_total_cards[match.referee] += match.total_cards
        for threshold in THRESHOLDS:
            outcome = int(match.total_cards >= threshold)
            global_success[threshold] += outcome
            league_success[match.div][threshold] += outcome
            ref_success[match.referee][threshold] += outcome

    return scored


def _method_table(scored: list[ScoreRow], *, baseline: str = "league_prior") -> list[dict[str, str]]:
    by_method: dict[str, list[ScoreRow]] = defaultdict(list)
    for row in scored:
        by_method[row.method].append(row)
    base = by_method[baseline]
    base_mean = sum(row.brier for row in base) / len(base)
    out = []
    for method in METHODS:
        rows = by_method.get(method, [])
        if not rows:
            continue
        brier = sum(row.brier for row in rows) / len(rows)
        delta = (base_mean - brier) * len(rows) * 100.0
        out.append(
            {
                "method": method,
                "n": str(len(rows)),
                "brier": f"{brier:.6f}",
                f"delta_vs_{baseline}_x100": f"{delta:.2f}",
                "brier_points_per_1000": f"{(base_mean - brier) * 100000:.3f}",
            }
        )
    return sorted(out, key=lambda row: float(row["brier"]))


def _threshold_table(scored: list[ScoreRow], threshold: int) -> list[dict[str, str]]:
    return _method_table([row for row in scored if row.threshold == threshold])


def _bootstrap(
    scored: list[ScoreRow],
    *,
    challenger: str,
    baseline: str = "league_prior",
    trials: int = 2000,
    seed: int = 20260629,
) -> dict[str, float | int]:
    by_match: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in scored:
        by_match[row.match_key][row.method] += row.brier
    deltas = [
        values[baseline] - values[challenger]
        for values in by_match.values()
        if baseline in values and challenger in values
    ]
    if not deltas:
        return {"matches": 0, "point_delta_x100": 0.0, "ci_2.5": 0.0, "ci_97.5": 0.0, "pr_gt_0": 0.0}
    rng = random.Random(seed)
    sampled = []
    for _ in range(trials):
        sampled.append(sum(rng.choice(deltas) for _ in deltas) * 100.0)
    sampled.sort()
    lo = sampled[int(0.025 * (len(sampled) - 1))]
    hi = sampled[int(0.975 * (len(sampled) - 1))]
    return {
        "matches": len(deltas),
        "point_delta_x100": sum(deltas) * 100.0,
        "ci_2.5": lo,
        "ci_97.5": hi,
        "pr_gt_0": sum(value > 0 for value in sampled) / len(sampled),
    }


def _referee_summary(matches: list[CardMatch]) -> list[dict[str, str]]:
    by_ref: dict[str, list[CardMatch]] = defaultdict(list)
    for match in matches:
        by_ref[match.referee].append(match)
    counts = sorted(len(rows) for rows in by_ref.values())
    if not counts:
        return []
    def q(frac: float) -> int:
        return counts[int(frac * (len(counts) - 1))]
    return [
        {
            "referees": str(len(counts)),
            "min_matches": str(counts[0]),
            "p25": str(q(0.25)),
            "p50": str(q(0.50)),
            "p75": str(q(0.75)),
            "max_matches": str(counts[-1]),
        }
    ]


def analyze(matches: list[CardMatch]) -> dict[str, object]:
    scored = replay(matches)
    overall = _method_table(scored)
    boot20 = _bootstrap(scored, challenger="referee_shrink20")
    boot_cap1 = _bootstrap(scored, challenger="referee_cap1_poisson")
    threshold_tables = {threshold: _threshold_table(scored, threshold) for threshold in THRESHOLDS}
    if boot20["ci_2.5"] > 0:
        decision = "proxy_candidate_referee_threshold_positive_not_live_promoted"
    elif boot_cap1["ci_2.5"] > 0:
        decision = "proxy_candidate_lenient_referee_cap1_positive_not_live_promoted"
    else:
        decision = "not_promoted_keep_field_or_neutral_referee_policy"
    return {
        "source_matches": len(matches),
        "scored_rows": len(scored) // len(METHODS),
        "date_min": min(match.match_date for match in matches).isoformat() if matches else "?",
        "date_max": max(match.match_date for match in matches).isoformat() if matches else "?",
        "unique_referees": len({match.referee for match in matches}),
        "thresholds": ",".join(str(value) for value in THRESHOLDS),
        "decision": decision,
        "referee_summary": _referee_summary(matches),
        "overall": overall,
        "threshold_tables": threshold_tables,
        "bootstraps": {
            "referee_shrink20_minus_league_prior": boot20,
            "referee_cap1_poisson_minus_league_prior": boot_cap1,
        },
    }


def _table(headers: list[str], rows: list[dict[str, str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return lines


def render(analysis: dict[str, object], *, raw_dir: Path | None = None, now: datetime | None = None) -> str:
    raw_dir = raw_dir or default_raw_dir()
    now = now or datetime.now(timezone.utc)
    lines = [
        "# Referee Card Adjustment Audit",
        "",
        "Read-only proxy analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        "- sports_predict: not_called",
        f"- raw_data: {raw_dir}",
        "- purpose: test referee-specific historical card rates as a card-threshold probability adjustment",
        f"- decision: {analysis['decision']}",
        f"- source_matches: {analysis['source_matches']}",
        f"- scored_rows: {analysis['scored_rows']}",
        f"- unique_referees: {analysis['unique_referees']}",
        f"- thresholds: {analysis['thresholds']}",
        f"- date_range: {analysis['date_min']} to {analysis['date_max']}",
        "",
        "## Referee Sample Summary",
        "",
        *_table(["referees", "min_matches", "p25", "p50", "p75", "max_matches"], analysis["referee_summary"]),  # type: ignore[arg-type]
        "",
        "## Overall Method Comparison",
        "",
        *_table(["method", "n", "brier", "delta_vs_league_prior_x100", "brier_points_per_1000"], analysis["overall"]),  # type: ignore[arg-type]
        "",
    ]
    threshold_tables = analysis["threshold_tables"]
    assert isinstance(threshold_tables, dict)
    for threshold in THRESHOLDS:
        lines.extend(
            [
                f"## Threshold {threshold}+ Cards",
                "",
                *_table(
                    ["method", "n", "brier", "delta_vs_league_prior_x100", "brier_points_per_1000"],
                    threshold_tables[threshold],  # type: ignore[index,arg-type]
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Candidate Bootstraps",
            "",
            "| comparison | matches | point_delta_x100 | ci_2.5 | ci_97.5 | pr_gt_0 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    boots = analysis["bootstraps"]
    assert isinstance(boots, dict)
    for comparison, boot in boots.items():
        assert isinstance(boot, dict)
        lines.append(
            f"| {comparison} | {boot['matches']} | {boot['point_delta_x100']:.2f} | "
            f"{boot['ci_2.5']:.2f} | {boot['ci_97.5']:.2f} | {boot['pr_gt_0']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is a Football-Data proxy replay over club leagues with named referees, not official SportsPredict validation.",
            "- SportsPredict field averages dominated historical card-count entries when available, but they are unavailable before lock in the current UI.",
            "- A positive proxy result would require verified current referee assignment plus current independent card-rate research before any live use.",
            "- A non-promoted result means no referee-specific card probability transform should be applied without a new evidence gate.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=None, help="Football-Data raw directory")
    parser.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = parser.parse_args()

    raw_dir = args.raw_dir or default_raw_dir()
    analysis = analyze(load_matches(raw_dir))
    text = render(analysis, raw_dir=raw_dir)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
